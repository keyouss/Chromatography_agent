from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm import sessionmaker

from chrom_platform.modules.data_center.errors import DataUnavailableError, DataValidationError
from chrom_platform.modules.data_center.import_artifacts import SQLAlchemyImportArtifactPort
from chrom_platform.modules.data_center.import_worker import DataImportWorker


class _Session:
    def __init__(self, row=None):
        self.row = row
        self.added = []
        self.flush_count = 0

    def scalar(self, _statement):
        return self.row

    def add(self, row):
        self.added.append(row)

    def flush(self):
        self.flush_count += 1


class _Store:
    def __init__(self, content=b"fixture bytes"):
        self.content = content
        self.put_args = None

    def read(self, _key):
        return self.content

    def put(self, artifact_id, content, *, expected_size, expected_sha256):
        self.put_args = (artifact_id, content, expected_size, expected_sha256)
        digest = hashlib.sha256(content).hexdigest()
        return f"objects/{artifact_id}", len(content), digest


def _artifact(**updates):
    values = {
        "id": "artifact-fixture", "project_id": "project-fixture", "kind": "dataset_package",
        "title": "fixture.csv", "media_type": "text/csv", "size_bytes": len(b"fixture bytes"),
        "sha256": hashlib.sha256(b"fixture bytes").hexdigest(), "storage_key": "objects/source",
        "status": "AVAILABLE", "scan_status": "PASSED", "metadata_json": {}, "created_by": "owner",
    }
    values.update(updates)
    return SimpleNamespace(**values)


def test_input_artifact_requires_project_available_scan_and_exact_bytes():
    session = _Session(_artifact())
    result = SQLAlchemyImportArtifactPort(session, _Store()).read_input("project-fixture", "artifact-fixture")
    assert result.content == b"fixture bytes"
    assert result.sha256 == hashlib.sha256(result.content).hexdigest()

    port = SQLAlchemyImportArtifactPort(_Session(_artifact(scan_status="NOT_CONFIGURED")), _Store())
    with pytest.raises(DataUnavailableError):
        port.read_input("project-fixture", "artifact-fixture")

    port = SQLAlchemyImportArtifactPort(_Session(_artifact(project_id="other-project")), _Store())
    with pytest.raises(DataUnavailableError):
        port.read_input("project-fixture", "artifact-fixture")

    port = SQLAlchemyImportArtifactPort(_Session(_artifact()), _Store(b"tampered"))
    with pytest.raises(DataValidationError):
        port.read_input("project-fixture", "artifact-fixture")


def test_generated_artifact_is_hash_checked_and_not_marked_scanned():
    session = _Session()
    port = SQLAlchemyImportArtifactPort(session, _Store())
    artifact_id, _, size, digest = port.publish_generated(
        project_id="project-fixture", owner_id="owner-fixture", kind="dataset_package",
        title="fixture.json", media_type="application/json", content=b'{"fixture":true}',
        source_ref={"type": "data_version", "id": "dv-fixture"}, generation="fixture-v1",
    )
    assert artifact_id.startswith("artifact_")
    assert size == len(b'{"fixture":true}')
    assert digest == hashlib.sha256(b'{"fixture":true}').hexdigest()
    assert session.flush_count == 1
    assert session.added[0].status == "AVAILABLE"
    assert session.added[0].scan_status == "NOT_CONFIGURED"


def test_worker_rejects_unconfigured_cancel_and_invalid_dispatch_payload():
    worker = DataImportWorker(lambda: None, _Store(), profile_resolver=lambda *_: None, allow_unleased_fixture=True)
    with pytest.raises(ValueError):
        worker.handle({"job_id": "j", "attempt": 1}, event_type="job.execute")
    with pytest.raises(DataUnavailableError):
        worker.handle({"job_id": "j", "attempt": 1, "dispatch_id": "d"}, event_type="job.cancel")


def _run_sqlite_import_lifecycle(*, preview_only: bool = False, audit_mode: str | None = None):
    """Shared SQLite setup for the full lifecycle and confirmation preview."""
    from chrom_platform.artifact_core.models import Artifact, Structure
    from chrom_platform.core.dto import ActorContext, ResourceRef
    from chrom_platform.infrastructure.db import Base
    from chrom_platform.infrastructure.metadata import compose_metadata
    from chrom_platform.infrastructure.models import AuditEvent, Confirmation, Job, OutboxEvent, Project
    from chrom_platform.modules.data_center.import_workflow import SQLAlchemyImportWorkflow
    from chrom_platform.modules.data_center.persistence import (
        DataCenterBase,
        DataDomainRow,
        DataImportRow,
        DataImportValidationRow,
        DataVersionRow,
        ExperimentRecordRow,
    )
    from chrom_platform.modules.data_center.schemas import DataDomainSchema, DomainField
    from chrom_platform.modules.data_center.csv_import import TrustedCsvProfile, NumericBounds
    from chrom_platform.task_core.repository import SQLAlchemyTaskRepository
    from chrom_platform.task_core.service import ConfirmationService, JobService, OperationContext, _hash
    from chrom_platform.task_core.policy import DeploymentTaskPolicy

    source = b"measurement_id,smiles,rf_observed\nm-1,C,0.5\n"
    source_sha = hashlib.sha256(source).hexdigest()
    from sqlalchemy.pool import StaticPool
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.connect() as connection:
        connection.connection.create_function("CHAR_LENGTH", 1, len)
    compose_metadata(Base.metadata, DataCenterBase.metadata).create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)

    class _BlobStore:
        configured = True

        def __init__(self):
            self.objects = {"source-key": source}
            self.fail_snapshot_once = True

        def read(self, key):
            return self.objects[key]

        def put(self, artifact_id, content, *, expected_size, expected_sha256):
            digest = hashlib.sha256(content).hexdigest()
            assert len(content) == expected_size
            assert digest == expected_sha256
            if self.fail_snapshot_once and content.startswith(b'{"snapshot_schema_version"'):
                self.fail_snapshot_once = False
                # Object storage is outside SQL's transaction: model a
                # successful write whose response is lost afterwards.
                self.objects[f"server/{artifact_id}"] = content
                raise OSError("synthetic transient object-store interruption")
            key = f"server/{artifact_id}"
            self.objects[key] = content
            return key, len(content), digest

    store = _BlobStore()
    domain = DataDomainSchema(
        id="fixture-tlc", version="1", chromatography_type="tlc",
        record_schema={
            "measurement_id": DomainField(type="string", required=True),
            "smiles": DomainField(type="string", required=True),
            "rf_observed": DomainField(type="number", required=True),
        },
        unit_definitions={}, identity_fields=["measurement_id"],
        deduplication_policy="reject", supported_import_modes=["append"],
    )
    profile = TrustedCsvProfile(
        profile_id="fixture-tlc-profile-v1", domain_id=domain.id, schema_version=domain.version,
        encoding="utf-8", field_bounds={"rf_observed": NumericBounds(minimum=Decimal("0"), maximum=Decimal("1"))},
        measurement_id_field="measurement_id", smiles_field="smiles",
    )
    actor = ActorContext(
        actor_id="actor-fixture", tenant_id=None, project_ids=["project-fixture"],
        auth_method="service",
        permissions=["data:read", "data:write", "dataset:write", "artifact:read", "task:retry", "task:confirm", "task:read"],
        policy_version="fixture-policy-v1",
    )
    ctx = OperationContext(actor=actor)
    now = datetime.now(timezone.utc)
    with sessions.begin() as session:
        session.add(Project(id="project-fixture", slug="data-import-fixture", name="data import fixture"))
        session.add(Artifact(
            id="artifact-source", project_id="project-fixture", kind="dataset_package",
            title="fixture.csv", media_type="text/csv", size_bytes=len(source), sha256=source_sha,
            storage_key="source-key", status="AVAILABLE", scan_status="PASSED",
            metadata_json={}, created_by="actor-fixture",
        ))
        session.add(DataDomainRow(
            id=domain.id, version=domain.version, chromatography_type=domain.chromatography_type,
            record_schema={key: value.model_dump(mode="json") for key, value in domain.record_schema.items()},
            unit_definitions={}, identity_fields=domain.identity_fields,
            deduplication_policy="reject", supported_import_modes=["append"],
        ))
        session.add(DataImportRow(
            id="import-fixture", project_id="project-fixture", domain_id=domain.id,
            artifact_id="artifact-source", schema_version=domain.version, import_mode="append",
            field_mapping={"measurement_id": "measurement_id", "smiles": "smiles", "rf_observed": "rf_observed"},
            source_description="synthetic SQLite fixture", revision=1,
            config_hash="a" * 64, etag='"fixture-etag"', status="DRAFT", job_id=None,
            validation_ref=None, new_data_version_id=None,
            request_snapshot={
                "project_id": "project-fixture", "domain_id": domain.id,
                "artifact_id": "artifact-source", "schema_version": domain.version,
                "import_mode": "append", "field_mapping": {
                    "measurement_id": "measurement_id", "smiles": "smiles", "rf_observed": "rf_observed",
                }, "source_description": "synthetic SQLite fixture",
            }, created_at=now, updated_at=now,
        ))

    class _TaskPolicy:
        def required_permission(self, operation, *, kind, resource_ref, action=None):
            return "data:write"

        def authorize_resource(self, ctx, target, *, action):
            return ctx.actor.can_access_project(target.project_id)

        def retry_requires_confirmation(self, *, kind, resource_ref):
            return False

        def resolve_target(self, ctx, resource_ref, action):
            return workflow.resolve_target(ctx, resource_ref, action)

        def filter_visible_jobs(self, ctx, rows):
            return rows

    def normalize_structure(smiles):
        return {"structure_id": "structure-fixture", "canonical_smiles": smiles, "inchikey": "FIXTURE"}

    def ensure_structure(*, session, project_id, normalized_structure, source_artifact_id):
        existing = session.scalar(select(Structure).where(
            Structure.project_id == project_id, Structure.id == normalized_structure["structure_id"],
        ))
        if existing is None:
            session.add(Structure(
                id=normalized_structure["structure_id"], project_id=project_id, schema_version="fixture-1",
                original_input={"smiles": "fixture"}, canonical_smiles=normalized_structure["canonical_smiles"],
                is_valid=True, has_stereochemistry=False, stereochemistry_status="unspecified",
                stereochemistry_warnings=[], snapshot_hash="c" * 64, source_artifact_id=source_artifact_id,
                created_at=now,
            ))
            session.flush()
        return normalized_structure

    generated_ids = iter(range(1, 100))

    def make_workflow(session):
        workflow = SQLAlchemyImportWorkflow(
            session, store, None,
            profile_resolver=lambda domain_id, version: profile if (domain_id, version) == (domain.id, domain.version) else None,
            id_factory=lambda prefix: f"{prefix}-fixture-{next(generated_ids)}",
        )
        confirmation = ConfirmationService(
            SQLAlchemyTaskRepository(lambda: session), policy=_TaskPolicy(), resolver=workflow.resolve_target,
        )
        workflow.confirmation_service = confirmation
        return workflow

    def delivery(session, job_id, attempt=1):
        outbox = next((row for row in session.scalars(select(OutboxEvent).where(
            OutboxEvent.aggregate_id == job_id, OutboxEvent.event_type == "job.execute",
        )) if (row.payload or {}).get("attempt") == attempt), None)
        assert outbox is not None
        return dict(outbox.payload)

    worker = DataImportWorker(
        sessions, store,
        profile_resolver=lambda domain_id, version: profile if (domain_id, version) == (domain.id, domain.version) else None,
        structure_normalizer=normalize_structure,
        ensure_structure=ensure_structure,
        allow_unleased_fixture=True,
    )
    with sessions() as session:
        session.begin()
        accepted_validation = make_workflow(session).submit_validation(ctx, "project-fixture", "import-fixture", if_match='"fixture-etag"')
        validate_payload = delivery(session, accepted_validation.job_id)
        session.commit()
    assert accepted_validation.id == "import-fixture"
    assert accepted_validation.resource_ref.model_dump(mode="json") == {"type": "data_import", "id": "import-fixture"}
    wrong_project_worker = DataImportWorker(
        sessions, store, profile_resolver=lambda domain_id, version: None,
        allowed_project_id="project-other", allow_unleased_fixture=True,
    )
    assert wrong_project_worker.handle(validate_payload) == {"status": "STALE"}
    wrong_project_worker._project_failure(
        validate_payload, lease_guard=None, cause=RuntimeError("synthetic scope guard"),
    )
    from chrom_platform.worker_cli import _cancel_data_import_job
    assert _cancel_data_import_job(
        sessions, validate_payload, allowed_project_id="project-other",
    ) == {"status": "STALE"}
    with sessions() as session:
        assert session.scalar(select(Job).where(Job.id == accepted_validation.job_id)).status == "QUEUED"
        assert session.scalar(select(DataImportRow).where(DataImportRow.id == "import-fixture")).status == "VALIDATING"
    validation_result = worker.handle(validate_payload)
    assert validation_result["status"] == "SUCCEEDED"
    with sessions() as session:
        imported = session.scalar(select(DataImportRow).where(DataImportRow.id == "import-fixture"))
        validation = session.scalar(select(DataImportValidationRow).where(DataImportValidationRow.id == imported.validation_ref))
        report_artifact = session.scalar(select(Artifact).where(Artifact.id == validation.report_artifact_id))
        assert imported.status == "READY" and validation.status == "VALID"
        assert report_artifact.status == "AVAILABLE" and report_artifact.scan_status == "NOT_CONFIGURED"
        report_bytes = store.read(report_artifact.storage_key)
        report_digest = hashlib.sha256(report_bytes).hexdigest()
        assert report_digest == report_artifact.sha256
        workflow = make_workflow(session)
        if preview_only:
            from chrom_platform.task_core.schemas import ConfirmationCreateRequest, ConfirmationPreviewRequest

            real_confirmation = ConfirmationService(
                SQLAlchemyTaskRepository(lambda: session),
                policy=DeploymentTaskPolicy(),
                resolver=workflow.resolve_target,
            )
            resource_ref = ResourceRef(type="data_import", id="import-fixture")
            preview = real_confirmation.preview_from_request(
                ctx, ConfirmationPreviewRequest(resource_ref=resource_ref, action="data_import.commit"),
            )
            draft_hash = session.scalar(select(DataImportRow.config_hash).where(DataImportRow.id == "import-fixture"))
            assert preview.config_hash != draft_hash
            assert preview.can_confirm is True
            assert all(item.label and item.value is not None for item in preview.summary)
            assert [item.model_dump(mode="json") for item in preview.dependencies] == [{
                "type": "data_import_validation",
                "id": validation.id,
                "version": str(validation.revision),
                "sha256": report_artifact.sha256,
            }]
            created = real_confirmation.create_from_request(
                ctx,
                ConfirmationCreateRequest(
                    resource_ref=preview.resource_ref,
                    action=preview.action,
                    revision=preview.revision,
                    config_hash=preview.config_hash,
                    policy_version=preview.policy_version,
                ),
                if_match=preview.etag,
                idempotency_key="confirmation-preview-fixture",
                session=session,
            )
            session.flush()
            confirmation_row = session.scalar(select(Confirmation).where(Confirmation.id == created.data.id))
            assert confirmation_row.status == "CONFIRMED"
            preview_dump = preview.model_dump(mode="json", exclude_none=True)
            assert confirmation_row.summary_snapshot == {
                "summary": preview_dump["summary"],
                "dependencies": preview_dump["dependencies"],
                "impact": preview_dump["impact"],
            }
            assert session.scalar(select(func.count()).select_from(Job).where(
                Job.project_id == "project-fixture", Job.kind == "data_import_validation",
            )) == 1
            assert session.scalar(select(func.count()).select_from(Job).where(
                Job.project_id == "project-fixture", Job.kind == "data_import_commit",
            )) == 0
            assert session.scalar(select(func.count()).select_from(DataVersionRow)) == 0
            assert session.scalar(select(func.count()).select_from(ExperimentRecordRow)) == 0
            assert session.scalar(select(DataImportRow.status).where(DataImportRow.id == "import-fixture")) == "READY"
            session.commit()
            engine.dispose()
            return
        if audit_mode == "agent_tool_denied":
            from chrom_platform.task_core.schemas import ConfirmationPreviewRequest
            from chrom_platform.task_core.service import TaskError

            real_confirmation = ConfirmationService(
                SQLAlchemyTaskRepository(lambda: session), policy=DeploymentTaskPolicy(),
                resolver=workflow.resolve_target,
            )
            agent_ctx = OperationContext(actor=actor, interaction="agent_tool")
            with pytest.raises(TaskError):
                real_confirmation.preview_from_request(
                    agent_ctx, ConfirmationPreviewRequest(
                        resource_ref=ResourceRef(type="data_import", id="import-fixture"),
                        action="data_import.commit",
                    ),
                )
            assert session.scalar(select(func.count()).select_from(Job).where(Job.kind == "data_import_commit")) == 0
            assert session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "data_import.commit")) == 0
            assert session.scalar(select(DataImportRow.status).where(DataImportRow.id == "import-fixture")) == "READY"
            session.commit()
            engine.dispose()
            return
        target = workflow.resolve_target(ctx, ResourceRef(type="data_import", id="import-fixture"), "data_import.commit")
        confirmation_id = "confirmation-fixture"
        session.add(Confirmation(
            id=confirmation_id, project_id=target.project_id, actor_id=actor.actor_id,
            resource_ref=target.resource_ref.model_dump(mode="json"), action="data_import.commit",
            revision=target.revision, config_hash=target.config_hash,
            binding_hash=_hash({
                "project_id": target.project_id, "actor_id": actor.actor_id,
                "resource_ref": target.resource_ref.model_dump(mode="json"), "action": "data_import.commit",
                "revision": target.revision, "config_hash": target.config_hash,
                "policy_version": target.policy_version,
            }), policy_version=target.policy_version,
            summary_snapshot={"summary": target.summary, "dependencies": target.dependencies, "impact": target.impact},
            status="CONFIRMED", created_at=now, expires_at=now + timedelta(minutes=20),
        ))
        session.commit()

    if audit_mode == "accepted_audit_failure":
        def reject_accepted_audit(session, _flush_context, _instances):
            if any(
                isinstance(row, AuditEvent)
                and (row.after_summary or {}).get("phase") == "accepted"
                for row in session.new
            ):
                raise RuntimeError("synthetic accepted audit persistence failure")

        event.listen(Session, "before_flush", reject_accepted_audit)
        try:
            with pytest.raises(RuntimeError, match="synthetic accepted audit persistence failure"):
                with sessions.begin() as session:
                    make_workflow(session).submit_commit(
                        ctx, "project-fixture", "import-fixture", confirmation_id,
                    )
        finally:
            event.remove(Session, "before_flush", reject_accepted_audit)
        with sessions() as session:
            confirmation = session.scalar(select(Confirmation).where(Confirmation.id == confirmation_id))
            imported = session.scalar(select(DataImportRow).where(DataImportRow.id == "import-fixture"))
            assert confirmation.status == "CONFIRMED"
            assert imported.status == "READY" and imported.job_id.startswith("job-")
            assert session.scalar(select(func.count()).select_from(Job).where(Job.kind == "data_import_commit")) == 0
            assert session.scalar(select(func.count()).select_from(OutboxEvent).where(
                OutboxEvent.event_type == "job.execute",
            )) == 1
            assert session.scalar(select(func.count()).select_from(AuditEvent).where(
                AuditEvent.action == "data_import.commit",
            )) == 0
        engine.dispose()
        return

    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient
    from chrom_platform.core.errors import APIError
    from chrom_platform.infrastructure.models import IdempotencyRecord
    from chrom_platform.infrastructure.repositories import SQLAlchemyIdempotencyStore
    from chrom_platform.modules.data_center.router import create_router
    from chrom_platform.modules.data_center.schemas import AsyncAccepted

    app = FastAPI()
    app.state.actor_context = actor
    app.add_exception_handler(APIError, lambda _request, exc: JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.payload.model_dump(mode="json"), "request_id": "fixture"},
    ))

    @app.middleware("http")
    async def bind_request_uow(request, call_next):
        with sessions() as request_session:
            with request_session.begin():
                request.state.data_import_workflow = make_workflow(request_session)
                request.state.idempotency_store = SQLAlchemyIdempotencyStore(request_session)
                return await call_next(request)

    app.include_router(create_router(), prefix="/api/v1")
    commit_path = "/api/v1/data-imports/import-fixture/commit"
    commit_headers = {"Idempotency-Key": "commit-route-fixture"}
    commit_body = {"confirmation_id": confirmation_id}
    direct_acceptance = audit_mode in {"published_audit_failure", "post_publish_fence_failure", "published_success"}
    if direct_acceptance:
        with sessions.begin() as request_session:
            accepted_commit = make_workflow(request_session).submit_commit(
                ctx, "project-fixture", "import-fixture", confirmation_id,
            )
            commit_payload = delivery(request_session, accepted_commit.job_id)
    else:
        with TestClient(app) as client:
            first_commit_response = client.post(commit_path, headers=commit_headers, json=commit_body)
            replay_commit_response = client.post(commit_path, headers=commit_headers, json=commit_body)
        assert first_commit_response.status_code == replay_commit_response.status_code == 202
        assert first_commit_response.headers["Location"] == replay_commit_response.headers["Location"] == "/api/v1/data-imports/import-fixture"
        assert replay_commit_response.headers["Idempotency-Replayed"] == "true"
        assert first_commit_response.json()["data"] == replay_commit_response.json()["data"]
        accepted_commit = AsyncAccepted.model_validate(first_commit_response.json()["data"])
    with sessions() as session:
        frozen_import = session.scalar(select(DataImportRow).where(DataImportRow.id == "import-fixture"))
        frozen_report = session.scalar(select(Artifact).where(Artifact.id == validation.report_artifact_id))
        assert frozen_import.request_snapshot["commit_binding"]["report_sha256"] == frozen_report.sha256
        if not direct_acceptance:
            commit_payload = delivery(session, accepted_commit.job_id)
        assert session.scalar(select(func.count()).select_from(Job).where(Job.id == accepted_commit.job_id)) == 1
        assert session.scalar(select(func.count()).select_from(Job).where(
            Job.project_id == "project-fixture", Job.kind == "data_import_commit",
        )) == 1
        assert session.scalar(select(func.count()).select_from(OutboxEvent).join(
            Job, OutboxEvent.aggregate_id == Job.id,
        ).where(Job.project_id == "project-fixture", Job.kind == "data_import_commit",
                OutboxEvent.event_type == "job.execute")) == 1
        assert session.scalar(select(func.count()).select_from(OutboxEvent).where(
            OutboxEvent.aggregate_id == accepted_commit.job_id,
            OutboxEvent.event_type == "job.execute",
        )) == 1
        if not direct_acceptance:
            idem = session.scalar(select(IdempotencyRecord).where(IdempotencyRecord.key == "commit-route-fixture"))
            assert idem is not None and idem.status == "COMPLETED" and idem.result_status_code == 202
        commit_audits = list(session.scalars(select(AuditEvent).where(
            AuditEvent.action == "data_import.commit", AuditEvent.job_id == accepted_commit.job_id,
        )))
        accepted_audits = [row for row in commit_audits if (row.after_summary or {}).get("phase") == "accepted"]
        assert len(accepted_audits) == 1
        assert accepted_audits[0].actor_id == actor.actor_id
        assert accepted_audits[0].auth_method == actor.auth_method
        assert accepted_audits[0].interaction == "human"
        assert accepted_audits[0].after_summary["status"] == "COMMITTING"
        accepted_audit = accepted_audits[0]
        commit_binding = frozen_import.request_snapshot["commit_binding"]
        assert accepted_audit.project_id == frozen_import.project_id
        assert accepted_audit.resource_ref == {"type": "data_import", "id": frozen_import.id}
        assert accepted_audit.confirmation_id == confirmation_id
        assert accepted_audit.config_hash == commit_binding["binding_hash"]
        assert accepted_audit.job_id == accepted_commit.job_id and accepted_audit.request_id
        assert set(accepted_audit.after_summary) == {
            "phase", "status", "revision", "config_hash", "binding_hash", "validation_id",
            "report_artifact_id", "report_sha256", "source_artifact_id", "source_sha256", "parent_version_id",
        }
        assert not {"smiles", "rf_observed", "source_description"} & set(accepted_audit.after_summary)
    assert accepted_commit.id == "import-fixture"
    assert accepted_commit.resource_ref.model_dump(mode="json") == {"type": "data_import", "id": "import-fixture"}
    if audit_mode == "accepted_audit":
        engine.dispose()
        return
    if audit_mode in {"published_audit_failure", "post_publish_fence_failure", "published_success"}:
        store.fail_snapshot_once = False
        if audit_mode == "published_audit_failure":
            def reject_published_audit(session, _flush_context, _instances):
                if any(
                    isinstance(row, AuditEvent)
                    and (row.after_summary or {}).get("phase") == "published"
                    for row in session.new
                ):
                    raise RuntimeError("synthetic published audit persistence failure")

            event.listen(Session, "before_flush", reject_published_audit)
            try:
                with pytest.raises(RuntimeError, match="synthetic published audit persistence failure"):
                    worker.handle(commit_payload)
            finally:
                event.remove(Session, "before_flush", reject_published_audit)
            expected_import_status, expected_job_status = "FAILED", "FAILED"
        elif audit_mode == "post_publish_fence_failure":
            from chrom_platform.task_core.outbox_dispatch import LeaseLostError

            original_fence = worker._fence_delivery
            fence_calls = 0

            def reject_final_fence(*args, **kwargs):
                nonlocal fence_calls
                fence_calls += 1
                if fence_calls == 2:
                    raise LeaseLostError("synthetic post-audit lease loss")
                return original_fence(*args, **kwargs)

            worker._fence_delivery = reject_final_fence
            with pytest.raises(LeaseLostError, match="synthetic post-audit lease loss"):
                worker.handle(commit_payload)
            assert fence_calls == 2
            expected_import_status, expected_job_status = "COMMITTING", "QUEUED"
        else:
            assert worker.handle(commit_payload)["status"] == "SUCCEEDED"
            expected_import_status, expected_job_status = "COMMITTED", "SUCCEEDED"
        if audit_mode != "published_success":
            with sessions() as session:
                imported = session.scalar(select(DataImportRow).where(DataImportRow.id == "import-fixture"))
                job = session.scalar(select(Job).where(Job.id == accepted_commit.job_id))
                assert imported.status == expected_import_status
                assert job.status == expected_job_status
                assert session.scalar(select(func.count()).select_from(DataVersionRow)) == 0
                assert session.scalar(select(func.count()).select_from(ExperimentRecordRow)) == 0
                assert session.scalar(select(func.count()).select_from(Artifact).where(
                    Artifact.metadata_json["generation"].as_string() == "data_import_snapshot_v1",
                )) == 0
                commit_audits = list(session.scalars(select(AuditEvent).where(
                    AuditEvent.action == "data_import.commit", AuditEvent.job_id == accepted_commit.job_id,
                )))
                assert [row for row in commit_audits if (row.after_summary or {}).get("phase") == "published"] == []
                assert len([row for row in commit_audits if (row.after_summary or {}).get("phase") == "accepted"]) == 1
        else:
            with sessions() as session:
                version = session.scalar(select(DataVersionRow).where(DataVersionRow.source_import_id == "import-fixture"))
                snapshot_artifact = session.scalar(select(Artifact).where(Artifact.id == version.snapshot_artifact_id))
                commit_audits = list(session.scalars(select(AuditEvent).where(
                    AuditEvent.action == "data_import.commit", AuditEvent.job_id == accepted_commit.job_id,
                )))
                published_audits = [row for row in commit_audits if (row.after_summary or {}).get("phase") == "published"]
                assert len(published_audits) == 1
                audit = published_audits[0]
                assert audit.actor_id == actor.actor_id and audit.auth_method == "service"
                assert audit.interaction == "worker" and audit.request_id == f"worker:{accepted_commit.job_id}"
                assert audit.project_id == version.project_id
                assert audit.resource_ref == {"type": "data_version", "id": version.id}
                assert audit.confirmation_id == confirmation_id
                assert audit.config_hash == frozen_import.request_snapshot["commit_binding"]["binding_hash"]
                assert audit.job_id == accepted_commit.job_id
                assert audit.after_summary["data_version_number"] == version.version
                assert audit.after_summary["snapshot_sha256"] == version.sha256 == snapshot_artifact.sha256
                assert audit.after_summary["source_sha256"] == hashlib.sha256(source).hexdigest()
                assert audit.after_summary["record_count"] == version.record_count == 1
                assert audit.after_summary["snapshot_artifact_id"] == version.snapshot_artifact_id
                assert audit.after_summary["schema_version"] == version.schema_version
                assert set(audit.after_summary) == {
                    "phase", "status", "data_import_id", "data_version_id", "data_version_number",
                    "snapshot_artifact_id", "snapshot_sha256", "record_count", "source_artifact_id",
                    "source_sha256", "domain_id", "schema_version", "parent_version_id", "binding_hash",
                    "origin_actor_id", "origin_auth_method", "origin_request_id",
                }
                assert not {"smiles", "rf_observed", "source_description"} & set(audit.after_summary)
            assert worker.handle(commit_payload) == {"status": "STALE"}
            with sessions() as session:
                commit_audits = list(session.scalars(select(AuditEvent).where(
                    AuditEvent.action == "data_import.commit", AuditEvent.job_id == accepted_commit.job_id,
                )))
                assert len([row for row in commit_audits if (row.after_summary or {}).get("phase") == "published"]) == 1
        engine.dispose()
        return
    with pytest.raises(DataUnavailableError):
        worker.handle(commit_payload)
    with sessions() as session:
        failed_job = session.scalar(select(Job).where(Job.id == accepted_commit.job_id))
        failed_import = session.scalar(select(DataImportRow).where(DataImportRow.id == "import-fixture"))
        assert failed_job.status == "FAILED" and failed_job.retryable is True
        assert failed_job.error["retryable"] is True
        assert failed_import.status == "FAILED" and failed_import.job_id == accepted_commit.job_id
        assert failed_import.new_data_version_id is None
        assert session.scalar(select(func.count()).select_from(DataVersionRow)) == 0
        assert session.scalar(select(func.count()).select_from(ExperimentRecordRow)) == 0
        assert session.scalar(select(func.count()).select_from(Artifact).where(
            Artifact.metadata_json["generation"].as_string() == "data_import_snapshot_v1",
        )) == 0
        retry_job_id, retry_etag = failed_job.id, failed_job.etag
    assert any(
        key.startswith("server/") and value.startswith(b'{"snapshot_schema_version"')
        for key, value in store.objects.items()
    ), "the failed transaction should leave only an unreferenced object-store orphan"
    with sessions() as session:
        retry_workflow = make_workflow(session)
        retry_target = retry_workflow.resolve_target(
            ctx, ResourceRef(type="data_import", id="import-fixture"), "job.retry",
        )
        retry_confirmation_id = "confirmation-retry-fixture"
        session.add(Confirmation(
            id=retry_confirmation_id, project_id=retry_target.project_id, actor_id=actor.actor_id,
            resource_ref=retry_target.resource_ref.model_dump(mode="json"), action="job.retry",
            revision=retry_target.revision, config_hash=retry_target.config_hash,
            binding_hash=_hash({
                "project_id": retry_target.project_id, "actor_id": actor.actor_id,
                "resource_ref": retry_target.resource_ref.model_dump(mode="json"), "action": "job.retry",
                "revision": retry_target.revision, "config_hash": retry_target.config_hash,
                "policy_version": retry_target.policy_version,
            }), policy_version=retry_target.policy_version,
            summary_snapshot={"summary": retry_target.summary, "dependencies": retry_target.dependencies, "impact": retry_target.impact},
            status="CONFIRMED", created_at=now, expires_at=now + timedelta(minutes=20),
        ))
        session.commit()

    class _BoundDeploymentTaskPolicy(DeploymentTaskPolicy):
        def __init__(self, bound_workflow):
            self.bound_workflow = bound_workflow

        def resolve_target(self, ctx, resource_ref, action):
            return self.bound_workflow.resolve_target(ctx, resource_ref, action)

    retry_policy = _BoundDeploymentTaskPolicy(retry_workflow)
    retry_confirmation = ConfirmationService(
        SQLAlchemyTaskRepository(sessions), policy=retry_policy,
        resolver=retry_policy.resolve_target,
    )
    retry_service = JobService(
        SQLAlchemyTaskRepository(sessions), policy=retry_policy,
        confirmation_service=retry_confirmation,
    )
    retried = retry_service.retry(
        ctx, retry_job_id, if_match=retry_etag, idempotency_key="retry-import-fixture",
        confirmation_id=retry_confirmation_id,
    )
    with sessions() as session:
        retry_payload = delivery(session, retry_job_id, attempt=2)
    assert retry_payload["attempt"] == 2 and retried.id == retry_job_id
    commit_result = worker.handle(retry_payload)
    assert commit_result["status"] == "SUCCEEDED"
    with sessions() as session:
        data_import = session.scalar(select(DataImportRow).where(DataImportRow.id == "import-fixture"))
        version = session.scalar(select(DataVersionRow).where(DataVersionRow.source_import_id == "import-fixture"))
        records = list(session.scalars(select(ExperimentRecordRow).where(ExperimentRecordRow.data_version_id == version.id)))
        confirmation = session.scalar(select(Confirmation).where(Confirmation.id == confirmation_id))
        retry_confirmation = session.scalar(select(Confirmation).where(Confirmation.id == retry_confirmation_id))
        snapshot_artifact = session.scalar(select(Artifact).where(Artifact.id == version.snapshot_artifact_id))
        assert data_import.status == "COMMITTED" and data_import.new_data_version_id == version.id
        assert version.record_count == len(records) == 1
        assert records[0].values["measurement_id"] == "m-1"
        assert records[0].values["rf_observed"] == "0.5"
        assert confirmation.status == "CONSUMED"
        assert retry_confirmation.status == "CONSUMED"
        snapshot_bytes = store.read(snapshot_artifact.storage_key)
        assert hashlib.sha256(snapshot_bytes).hexdigest() == version.sha256 == snapshot_artifact.sha256
        assert snapshot_artifact.status == "AVAILABLE" and snapshot_artifact.scan_status == "NOT_CONFIGURED"

    first_version_id, first_version_sha = version.id, version.sha256
    first_snapshot_bytes = snapshot_bytes

    # A second import appends to the immutable first version. Verify the
    # snapshot artifact, version metadata, report count, and experiment rows
    # all describe the same two-record set.
    second_source = b"measurement_id,smiles,rf_observed\nm-2,C,0.7\n"
    second_source_sha = hashlib.sha256(second_source).hexdigest()
    store.objects["source-key-2"] = second_source
    with sessions.begin() as session:
        session.add(Artifact(
            id="artifact-source-2", project_id="project-fixture", kind="dataset_package",
            title="fixture-2.csv", media_type="text/csv", size_bytes=len(second_source),
            sha256=second_source_sha, storage_key="source-key-2", status="AVAILABLE",
            scan_status="PASSED", metadata_json={}, created_by="actor-fixture",
        ))
        session.add(DataImportRow(
            id="import-fixture-2", project_id="project-fixture", domain_id=domain.id,
            artifact_id="artifact-source-2", schema_version=domain.version, import_mode="append",
            field_mapping={"measurement_id": "measurement_id", "smiles": "smiles", "rf_observed": "rf_observed"},
            source_description="second synthetic append", revision=1,
            config_hash="e" * 64, etag='"fixture-etag-2"', status="DRAFT", job_id=None,
            validation_ref=None, new_data_version_id=None,
            request_snapshot={
                "project_id": "project-fixture", "domain_id": domain.id,
                "artifact_id": "artifact-source-2", "schema_version": domain.version,
                "import_mode": "append", "field_mapping": {
                    "measurement_id": "measurement_id", "smiles": "smiles", "rf_observed": "rf_observed",
                }, "source_description": "second synthetic append",
            }, created_at=now, updated_at=now,
        ))
    with sessions() as session:
        with session.begin():
            accepted_validation_2 = make_workflow(session).submit_validation(
                ctx, "project-fixture", "import-fixture-2", if_match='"fixture-etag-2"',
            )
            validation_payload_2 = delivery(session, accepted_validation_2.job_id)
    assert worker.handle(validation_payload_2)["status"] == "SUCCEEDED"
    with sessions.begin() as session:
        workflow_2 = make_workflow(session)
        target_2 = workflow_2.resolve_target(
            ctx, ResourceRef(type="data_import", id="import-fixture-2"), "data_import.commit",
        )
        confirmation_id_2 = "confirmation-fixture-2"
        session.add(Confirmation(
            id=confirmation_id_2, project_id=target_2.project_id, actor_id=actor.actor_id,
            resource_ref=target_2.resource_ref.model_dump(mode="json"), action="data_import.commit",
            revision=target_2.revision, config_hash=target_2.config_hash,
            binding_hash=_hash({
                "project_id": target_2.project_id, "actor_id": actor.actor_id,
                "resource_ref": target_2.resource_ref.model_dump(mode="json"), "action": "data_import.commit",
                "revision": target_2.revision, "config_hash": target_2.config_hash,
                "policy_version": target_2.policy_version,
            }), policy_version=target_2.policy_version,
            summary_snapshot={"summary": target_2.summary, "dependencies": target_2.dependencies, "impact": target_2.impact},
            status="CONFIRMED", created_at=now, expires_at=now + timedelta(minutes=20),
        ))
    with sessions() as session:
        with session.begin():
            accepted_commit_2 = make_workflow(session).submit_commit(
                ctx, "project-fixture", "import-fixture-2", confirmation_id_2,
            )
            commit_payload_2 = delivery(session, accepted_commit_2.job_id)
    assert worker.handle(commit_payload_2)["status"] == "SUCCEEDED"

    def assert_snapshot_matches_rows(session, data_version):
        artifact = session.scalar(select(Artifact).where(Artifact.id == data_version.snapshot_artifact_id))
        content = store.read(artifact.storage_key)
        snapshot = json.loads(content)
        experiment_rows = list(session.scalars(select(ExperimentRecordRow).where(
            ExperimentRecordRow.data_version_id == data_version.id,
        )))
        snapshot_members = {
            (row["molecule_id"], row["identity_hash"], json.dumps(row["values"], sort_keys=True, separators=(",", ":")))
            for row in snapshot["records"]
        }
        experiment_members = {
            (row.molecule_id, row.identity_hash, json.dumps(row.values, sort_keys=True, separators=(",", ":")))
            for row in experiment_rows
        }
        validation_report = session.scalar(select(Artifact).where(
            Artifact.id == data_version.quality_report_ref["id"],
        ))
        report = json.loads(store.read(validation_report.storage_key))
        assert snapshot["snapshot_schema_version"] == "data-import-snapshot-1"
        assert snapshot["project_id"] == data_version.project_id
        assert snapshot["domain_id"] == data_version.domain_id
        assert snapshot["schema_version"] == data_version.schema_version
        assert snapshot["source_import_id"] == data_version.source_import_id
        assert snapshot_members == experiment_members
        assert data_version.record_count == len(snapshot["records"]) == len(experiment_rows)
        assert report["counts"]["new"] == 1
        assert data_version.sha256 == hashlib.sha256(content).hexdigest() == artifact.sha256
        return snapshot, content, experiment_members

    with sessions() as session:
        first_version_after_append = session.scalar(select(DataVersionRow).where(DataVersionRow.id == first_version_id))
        second_version = session.scalar(select(DataVersionRow).where(DataVersionRow.source_import_id == "import-fixture-2"))
        second_import = session.scalar(select(DataImportRow).where(DataImportRow.id == "import-fixture-2"))
        first_artifact_after_append = session.scalar(select(Artifact).where(Artifact.id == first_version_after_append.snapshot_artifact_id))
        first_snapshot_after_append = store.read(first_artifact_after_append.storage_key)
        first_snapshot, _, first_members = assert_snapshot_matches_rows(session, first_version_after_append)
        second_snapshot, second_snapshot_bytes, second_members = assert_snapshot_matches_rows(session, second_version)
        assert first_version_after_append.version == 1
        assert first_version_after_append.sha256 == first_version_sha
        assert first_snapshot_after_append == first_snapshot_bytes
        assert first_snapshot["parent_version_id"] is None
        assert second_version.version == 2
        assert second_import.status == "COMMITTED" and second_import.new_data_version_id == second_version.id
        assert second_snapshot["parent_version_id"] == first_version_id
        assert second_snapshot["source_artifact_id"] == "artifact-source-2"
        assert second_snapshot["source_sha256"] == second_source_sha
        assert first_members <= second_members and len(first_members) == 1 and len(second_members) == 2
        assert first_snapshot["records"] != second_snapshot["records"]
        assert session.scalar(select(func.count()).select_from(DataVersionRow)) == 2

    assert worker.handle(commit_payload_2) == {"status": "STALE"}
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(DataVersionRow)) == 2

    # A terminal duplicate delivery is fenced before the committer and cannot
    # add another version or consume the already-consumed authorization again.
    assert worker.handle(commit_payload) == {"status": "STALE"}
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(DataVersionRow).where(DataVersionRow.source_import_id == "import-fixture")) == 1
        confirmation = session.scalar(select(Confirmation).where(Confirmation.id == confirmation_id))
        assert confirmation.status == "CONSUMED"
        report_artifact = session.scalar(select(Artifact).where(Artifact.id == validation.report_artifact_id))
        report_artifact_id = report_artifact.id
        report_bytes = store.read(report_artifact.storage_key)
        source_artifact = session.scalar(select(Artifact).where(Artifact.id == "artifact-source"))
        original_source_sha = source_artifact.sha256
        imported = session.scalar(select(DataImportRow).where(DataImportRow.id == "import-fixture"))
        imported.revision += 1
        imported.config_hash = "b" * 64
        imported.request_snapshot = {"later_revision": True}
        session.commit()

    from chrom_platform.modules.data_center.import_artifacts import DataImportArtifactReadPolicy
    policy = DataImportArtifactReadPolicy(sessions)
    with sessions() as session:
        report_artifact = session.scalar(select(Artifact).where(Artifact.id == report_artifact_id))
        assert policy.can_read_export(report_artifact, actor)
        assert policy.validate_export_body(report_artifact, actor, report_bytes)
        forged_metadata = dict(report_artifact.metadata_json)
        forged_metadata["validation_binding"] = {**forged_metadata["validation_binding"], "source_sha256": "0" * 64}
        report_artifact.metadata_json = forged_metadata
        assert not policy.can_read_export(report_artifact, actor)
        report_artifact.metadata_json = {
            **forged_metadata,
            "validation_binding": {**forged_metadata["validation_binding"], "source_sha256": original_source_sha},
        }
        session.commit()
    with sessions.begin() as session:
        source_artifact = session.scalar(select(Artifact).where(Artifact.id == "artifact-source"))
        source_artifact.sha256 = "d" * 64
    with sessions() as session:
        report_artifact = session.scalar(select(Artifact).where(Artifact.id == report_artifact_id))
        assert not policy.validate_export_body(report_artifact, actor, report_bytes)


def test_sqlite_import_validation_confirmation_commit_and_terminal_replay_are_atomic():
    _run_sqlite_import_lifecycle()


def test_real_import_confirmation_preview_and_create_use_strict_bound_metadata():
    _run_sqlite_import_lifecycle(preview_only=True)


def test_import_acceptance_audit_failure_rolls_back_confirmation_import_job_and_outbox():
    _run_sqlite_import_lifecycle(audit_mode="accepted_audit_failure")


def test_agent_tool_commit_confirmation_remains_rejected_without_audit_or_job():
    _run_sqlite_import_lifecycle(audit_mode="agent_tool_denied")


def test_idempotent_commit_replay_writes_one_accepted_audit():
    _run_sqlite_import_lifecycle(audit_mode="accepted_audit")


@pytest.mark.parametrize("audit_mode", ["published_audit_failure", "post_publish_fence_failure", "published_success"])
def test_publish_audit_is_atomic_with_version_fence_and_terminal_replay(audit_mode):
    _run_sqlite_import_lifecycle(audit_mode=audit_mode)
