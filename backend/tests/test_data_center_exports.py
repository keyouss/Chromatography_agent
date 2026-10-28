from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from io import BytesIO
from zipfile import ZipFile, ZipInfo

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from chrom_platform.assembly.m2 import assemble_m2_metadata
from chrom_platform.artifact_core.models import Artifact
from chrom_platform.artifact_core.repository import SQLAlchemyArtifactRepository
from chrom_platform.artifact_core.schemas import CompleteUploadRequest, UploadCreate
from chrom_platform.artifact_core.service import ArtifactService, ArtifactServiceError
from chrom_platform.core.dto import ActorContext, ResourceRef
from chrom_platform.core.errors import APIError
from chrom_platform.core.idempotency import BeginResult, IdempotencyRecord
from chrom_platform.modules.data_center.exports import render_export, execute_export_job, SQLAlchemyExportArtifactReadPolicy, _public_export_error, _public_export_snapshots
from chrom_platform.modules.data_center.errors import DataNotFoundError, DataUnavailableError, DataValidationError
from chrom_platform.modules.data_center.persistence import ExportRow, PredictionRecordRow
from chrom_platform.modules.data_center.exports import SQLAlchemyExportRepository
from chrom_platform.modules.data_center.router import create_router
from chrom_platform.modules.data_center.schemas import ExportAccepted, ExportCreate
from chrom_platform.modules.prediction.persistence import PredictionRow
from chrom_platform.infrastructure.models import AuditEvent, Job, OutboxEvent, Project
from chrom_platform.task_core.repository import SQLAlchemyTaskRepository
from chrom_platform.task_core.service import JobService, OperationContext, add_job_rows
from chrom_platform.task_core.state import JobStatus
from chrom_platform.task_core.outbox_dispatch import LeaseLostError


class Store:
    def __init__(self):
        self.rows = {}

    def get(self, scope):
        return self.rows.get(scope)

    def begin(self, scope, payload_hash):
        if scope in self.rows:
            return BeginResult(self.rows[scope], False)
        self.rows[scope] = IdempotencyRecord(payload_hash, "IN_PROGRESS")
        return BeginResult(self.rows[scope], True)

    def complete(self, scope, payload_hash, status_code, response):
        self.rows[scope] = IdempotencyRecord(payload_hash, "COMPLETED", status_code, response)

    def fail(self, scope, payload_hash, error_code):
        self.rows[scope] = IdempotencyRecord(payload_hash, "FAILED")


class Exports:
    def __init__(self):
        self.created = 0

    def create(self, request, *, actor, request_id):
        self.created += 1
        return ExportAccepted(id="exp-1", job_id="job-1", status="QUEUED", resource_ref=ResourceRef(type="export", id="exp-1"))

    def get(self, export_id, *, actor):
        raise AssertionError("not used by this test")


def _database_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    event.listen(engine, "connect", lambda dbapi, _: dbapi.create_function("CHAR_LENGTH", 1, len))
    assemble_m2_metadata().create_all(engine)
    session = Session(engine)
    session.add(Project(id="project-a", slug="project-a", name="Project A", status="ACTIVE", revision=1))
    session.flush()
    return session


def test_export_submission_is_project_scoped_idempotent_and_async():
    app = FastAPI()
    app.state.actor_context = ActorContext(
        actor_id="actor-1", tenant_id=None, project_ids=["project-a"],
        auth_method="deployment_default", permissions=["prediction:read"], policy_version="test",
    )
    app.state.idempotency_store = Store()
    service = Exports()
    app.add_exception_handler(APIError, lambda request, exc: JSONResponse(status_code=exc.status_code, content={"error": exc.payload.model_dump(mode="json")}))
    app.include_router(create_router(export_service=service, idempotency_store=app.state.idempotency_store), prefix="/api/v1")
    client = TestClient(app)
    payload = {"project_id": "project-a", "resource_refs": [{"type": "prediction_record", "id": "pr-1"}], "format": "json", "include": ["result"]}

    first = client.post("/api/v1/exports", json=payload, headers={"Idempotency-Key": "same-request"})
    replay = client.post("/api/v1/exports", json=payload, headers={"Idempotency-Key": "same-request"})
    forbidden = client.post("/api/v1/exports", json={**payload, "project_id": "project-b"}, headers={"Idempotency-Key": "other"})

    assert first.status_code == 202 and first.headers["location"] == "/api/v1/exports/exp-1"
    assert replay.status_code == 202 and replay.headers["idempotency-replayed"] == "true"
    assert service.created == 1
    assert forbidden.status_code == 404


def test_exports_generate_json_csv_and_formula_safe_xlsx():
    snapshots = {
        "format_version": "1.0", "generated_at": "2026-10-03T00:00:00+00:00", "include": ["input_snapshot"],
        "records": [{"record_id": "pr-1", "source": "=HYPERLINK(1)"}],
    }
    json_body, _, _ = render_export("json", snapshots)
    csv_body, _, _ = render_export("csv", snapshots)
    xlsx_body, _, _ = render_export("xlsx", snapshots)

    assert json.loads(json_body)["format_version"] == "1.0"
    assert b"generated_at" in csv_body and b"'=HYPERLINK(1)" in csv_body
    with ZipFile(BytesIO(xlsx_body)) as archive:
        sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
    assert "generated_at" in sheet and "&#x27;=HYPERLINK(1)" in sheet
    assert "<f>" not in sheet


def test_repository_snapshots_only_selected_authorized_prediction_records():
    session = _database_session()
    actor = ActorContext(
        actor_id="actor-1", tenant_id=None, project_ids=["project-a"],
        auth_method="deployment_default", permissions=["prediction:read"], policy_version="test",
    )
    at = datetime.now(timezone.utc)
    prediction_job = add_job_rows(
        SQLAlchemyTaskRepository(lambda: session), session, project_id="project-a", owner_id="actor-1",
        kind="prediction", resource_ref=ResourceRef(type="prediction", id="prediction-1"),
        cancelable=True, retryable=True, resume_mode="restart", deadline_at=None,
    )
    session.add(PredictionRecordRow(
        id="record-1", project_id="project-a", prediction_id=None, batch_id=None,
        source="web", actor_id="actor-1", chromatography_type="tlc",
        model_snapshot={"version": "model-v1"}, input_snapshot={"smiles": "CC"},
        result_ref={"type": "prediction", "id": "prediction-1"}, warnings=["fixture-warning"],
        status="SUCCEEDED", archived_at=None, created_at=at, artifact_refs=[], revision=1, etag='"record-v1"',
    ))
    session.flush()
    session.add(PredictionRow(
        id="prediction-1", project_id="project-a", prediction_record_id="record-1", job_id=prediction_job.id,
        actor_id="actor-1", chromatography_type="tlc", tool="single", schema_version="1.0",
        status="SUCCEEDED", model_snapshot={"version": "model-v1"}, input_snapshot={"smiles": "CC"},
        request_snapshot={}, result={"rf": 0.42}, warnings=[], error=None, source="web",
        conversation_id=None, agent_run_id=None, created_at=at, updated_at=at, completed_at=at,
        data_origin="production", revision=1, etag="prediction-etag",
    ))
    session.flush()

    accepted = SQLAlchemyExportRepository(session).create(ExportCreate(
        project_id="project-a", resource_refs=[ResourceRef(type="prediction_record", id="record-1")],
        format="json", include=["input_snapshot", "result", "model_version", "warnings"],
    ), actor=actor, request_id="req-1")
    row = session.scalar(select(ExportRow).where(ExportRow.id == accepted.id))
    audit = session.scalars(select(AuditEvent).where(AuditEvent.action == "records.export")).all()
    assert accepted.status == "QUEUED" and accepted.job_id
    assert row is not None
    assert row.source_snapshots["records"] == [{
        "record_id": "record-1", "created_at": at.isoformat(), "status": "SUCCEEDED", "source": "web",
        "chromatography_type": "tlc", "input_snapshot": {"smiles": "CC"}, "model_version": "model-v1",
        "warnings": ["fixture-warning"], "result": {"rf": 0.42},
    }]
    assert row.source_snapshots["generated_at"] and row.source_snapshots["format_version"] == "1.0"
    assert len(audit) == 1
    with pytest.raises(DataNotFoundError):
        SQLAlchemyExportRepository(session).create(ExportCreate(
            project_id="project-a", resource_refs=[ResourceRef(type="prediction_record", id="record-1")],
            format="json", include=["result"],
        ), actor=actor.model_copy(update={"project_ids": ["project-b"]}), request_id="req-2")
    with pytest.raises(DataValidationError):
        SQLAlchemyExportRepository(session).create(ExportCreate(
            project_id="project-a", resource_refs=[ResourceRef(type="prediction", id="prediction-1")],
            format="json", include=["result"],
        ), actor=actor, request_id="req-3")
    session.rollback()
    session.close()


def test_get_export_status_matches_its_terminal_job_view(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'export-job-view.db'}")
    event.listen(engine, "connect", lambda dbapi, _: dbapi.create_function("CHAR_LENGTH", 1, len))
    assemble_m2_metadata().create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    actor = ActorContext(
        actor_id="actor-a", tenant_id=None, project_ids=["project-a"],
        auth_method="deployment_default", permissions=["prediction:read"], policy_version="test",
    )

    class ReadPolicy:
        def required_permission(self, operation, *, kind, resource_ref, action=None):
            return "prediction:read"

    with sessions() as session:
        session.add(Project(id="project-a", slug="project-a", name="Project A", status="ACTIVE", revision=1))
        session.flush()
        at = datetime.now(timezone.utc)
        job = add_job_rows(
            SQLAlchemyTaskRepository(lambda: session), session, project_id="project-a", owner_id="actor-a",
            kind="export", resource_ref=ResourceRef(type="export", id="export-a"),
            cancelable=True, retryable=True, resume_mode="restart", deadline_at=None,
        )
        job.status = JobStatus.SUCCEEDED.value
        session.add(PredictionRecordRow(
            id="record-a", project_id="project-a", prediction_id=None, batch_id=None,
            source="web", actor_id="actor-a", chromatography_type="tlc",
            model_snapshot={"version": "model-v1"}, input_snapshot={"smiles": "CC"},
            result_ref={"type": "prediction", "id": "prediction-a"}, warnings=[],
            status="SUCCEEDED", archived_at=None, created_at=at, artifact_refs=[], revision=1, etag='"record-v1"',
        ))
        session.add(ExportRow(
            id="export-a", project_id="project-a", job_id=job.id, format="json", format_version="1.0",
            source_snapshots={"records": [{"record_id": "record-a"}]}, status=JobStatus.SUCCEEDED.value, artifact_id="artifact-a",
            error=None, created_at=at, completed_at=at,
        ))
        session.commit()

        job_view = JobService(
            SQLAlchemyTaskRepository(sessions), policy=ReadPolicy()
        ).get(OperationContext(actor), job.id)
        export_view = SQLAlchemyExportRepository(session).get("export-a", actor=actor)

        assert job_view.status is JobStatus.SUCCEEDED
        assert job_view.cancelable is False and job_view.retryable is False
        assert export_view.status == job_view.status.value

    engine.dispose()


def test_export_public_projection_keeps_provenance_and_strips_legacy_secrets():
    poisoned = {
        "format_version": "1.0", "generated_at": "2026-10-04T00:00:00+00:00",
        "include": ["model_version", "warnings", "result"],
        "connection_string": "mysql://user:password@host/db",
        "records": [{
            "record_id": "record-a", "created_at": "2026-10-04T00:00:00+00:00",
            "status": "SUCCEEDED", "source": "web", "chromatography_type": "sfc",
            "model_version": "sfc-model-v7", "warnings": ["historical warning"],
            "result": {"rf": 0.42, "private_token": "do-not-return", "debug": {"absolute_path": "C:\\private\\model.pt", "label": "safe"}},
            "stack_trace": "Traceback (most recent call last): hidden",
        }],
    }
    projected = _public_export_snapshots(poisoned)
    encoded, _, _ = render_export("json", projected)
    public = encoded.decode("utf-8")
    assert projected["records"][0]["model_version"] == "sfc-model-v7"
    assert projected["records"][0]["warnings"] == ["historical warning"]
    assert projected["records"][0]["result"]["rf"] == 0.42
    assert projected["records"][0]["result"]["debug"] == {"label": "safe"}
    assert all(secret not in public for secret in ("password", "private_token", "private\\model.pt", "Traceback", "stack_trace"))
    assert _public_export_error({"code": "INTERNAL_DB_ERROR", "message": "mysql://user:password@host/db", "traceback": "private"}) == {
        "code": "EXPORT_GENERATION_FAILED", "message": "导出生成失败", "retryable": True,
    }


@pytest.mark.parametrize("include", [[None], [{}], [[]], [1], None, {"nested": "result"}])
def test_export_public_projection_rejects_polluted_include_values(include):
    with pytest.raises(DataUnavailableError):
        _public_export_snapshots({"include": include, "records": []})


def test_export_report_is_server_artifact_kind_only():
    assert "export_report" in __import__("chrom_platform.artifact_core.schemas", fromlist=["ArtifactKind"]).ArtifactKind.__args__
    assert "export_report" not in UploadCreate.model_json_schema()["properties"]["kind"]["enum"]
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    active = json.loads((root / "contracts" / "universal-v1" / "openapi.json").read_text(encoding="utf-8"))
    assert "/api/v1/exports" in active["paths"]
    assert "export_report" in active["components"]["schemas"]["ArtifactView"]["properties"]["kind"]["enum"]
    assert "export_report" not in active["components"]["schemas"]["UploadCreate"]["properties"]["kind"]["enum"]
    with pytest.raises(ValidationError):
        UploadCreate.model_validate({
            "project_id": "project-a", "kind": "export_report", "file_name": "x.json",
            "media_type": "application/json", "size_bytes": 1,
        })
    with pytest.raises(ValidationError):
        CompleteUploadRequest.model_validate({"kind": "export_report"})


def test_export_candidate_is_an_additive_strict_contract_diff():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    active = json.loads((root / "contracts" / "universal-v1" / "rollback-snapshots" / "revision-20261003-02-baseline" / "openapi.json").read_text(encoding="utf-8"))
    candidate = json.loads((root / "contracts" / "candidates" / "m2-export" / "universal-v1.openapi.json").read_text(encoding="utf-8"))
    added_paths = set(candidate["paths"]) - set(active["paths"])
    assert added_paths == {"/api/v1/exports", "/api/v1/exports/{id}"}
    assert all(active["paths"][path] == candidate["paths"][path] for path in active["paths"])

    old_schemas = active["components"]["schemas"]
    new_schemas = candidate["components"]["schemas"]
    assert set(new_schemas) - set(old_schemas) == {
        "ExportCreate", "ExportAccepted", "ExportView",
        "SuccessEnvelope_ExportAccepted_", "SuccessEnvelope_ExportView_",
    }
    assert set(old_schemas) == set(new_schemas) - (set(new_schemas) - set(old_schemas))
    for name in old_schemas:
        if name != "ArtifactView":
            assert old_schemas[name] == new_schemas[name], name
    before, after = old_schemas["ArtifactView"], new_schemas["ArtifactView"]
    before_kind = before["properties"]["kind"]
    after_kind = after["properties"]["kind"]
    assert after_kind["enum"] == before_kind["enum"] + ["export_report"]
    assert {**after, "properties": {**after["properties"], "kind": before_kind}} == before

    post = candidate["paths"]["/api/v1/exports"]["post"]
    assert post["responses"]["202"]["headers"]["Location"]["required"] is True
    assert any(p["name"] == "Idempotency-Key" and p["required"] for p in post["parameters"])
    assert "get" in candidate["paths"]["/api/v1/exports/{id}"]


class _ReadStore:
    configured = True

    def __init__(self, content):
        self.content = content

    def read(self, _storage_key):
        return self.content


@pytest.mark.parametrize("format", ["json", "csv", "xlsx"])
def test_export_artifact_reads_recheck_project_permission_and_source_visibility(tmp_path, format):
    engine = create_engine(f"sqlite:///{tmp_path / 'export-access.db'}")
    event.listen(engine, "connect", lambda dbapi, _: dbapi.create_function("CHAR_LENGTH", 1, len))
    assemble_m2_metadata().create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions() as session:
        session.add_all([
            Project(id="project-a", slug="project-a", name="Project A", status="ACTIVE", revision=1),
            Project(id="project-b", slug="project-b", name="Project B", status="ACTIVE", revision=1),
        ])
        session.flush()
        at = datetime.now(timezone.utc)
        session.add(PredictionRecordRow(
            id="record-a", project_id="project-a", prediction_id=None, batch_id=None,
            source="web", actor_id="actor-a", chromatography_type="tlc", model_snapshot={},
            input_snapshot={}, result_ref=None, warnings=[], status="SUCCEEDED", archived_at=None,
            created_at=at, artifact_refs=[], revision=1, etag="record-a",
        ))
        job = add_job_rows(
            SQLAlchemyTaskRepository(lambda: session), session, project_id="project-a", owner_id="actor-a",
            kind="export", resource_ref=ResourceRef(type="export", id="export-a"),
            cancelable=True, retryable=True, resume_mode="restart", deadline_at=None,
        )
        snapshots = {
            "format_version": "1.0", "generated_at": at.isoformat(), "include": ["model_version", "warnings", "result"],
            "connection_string": "mysql://user:password@host/db", "records": [{"record_id": "record-a", "created_at": at.isoformat(),
                "status": "SUCCEEDED", "source": "web", "chromatography_type": "tlc", "model_version": "model-v7",
                "warnings": ["legacy warning"], "result": {"rf": 0.5, "private_token": "hidden", "debug": {"absolute_path": "C:\\private\\x.pt"}},
                "traceback": "Traceback (most recent call last): hidden"}],
        }
        from chrom_platform.modules.data_center.exports import render_export
        body, media_type, extension = render_export(format, snapshots)
        session.add(ExportRow(
            id="export-a", project_id="project-a", job_id=job.id, format=format, format_version="1.0",
            source_snapshots=snapshots, status="SUCCEEDED",
            artifact_id="artifact-a", error={"code": "DB_SECRET", "message": "mysql://user:password@host/db", "traceback": "hidden"}, created_at=at, completed_at=at,
        ))
        session.add(Artifact(
            id="artifact-a", project_id="project-a", kind="export_report", title=f"records.{extension}",
            media_type=media_type, size_bytes=len(body), sha256=hashlib.sha256(body).hexdigest(), storage_key="export-a",
            status="AVAILABLE", scan_status="PASSED", metadata_json={"source_ref": {"type": "export", "id": "export-a"}},
            created_by="actor-a",
        ))
        session.commit()

        policy = SQLAlchemyExportArtifactReadPolicy(sessions)
        store = _ReadStore(body)
        service = ArtifactService(SQLAlchemyArtifactRepository(session), store, export_read_policy=policy)
        artifact_reader = ActorContext(
            actor_id="actor-a", tenant_id=None, project_ids=["project-a"], auth_method="deployment_default",
            permissions=["artifact:read", "prediction:read"], policy_version="test",
        )
        revoked_source = artifact_reader.model_copy(update={"permissions": ["artifact:read"]})
        other_project = artifact_reader.model_copy(update={"actor_id": "actor-b", "project_ids": ["project-b"]})

        assert service.download("artifact-a", artifact_reader)[1] == body
        if format == "xlsx":
            rewritten = BytesIO()
            with ZipFile(BytesIO(body)) as source, ZipFile(rewritten, "w") as target:
                for name in source.namelist():
                    info = ZipInfo(name, date_time=(2001, 1, 1, 0, 0, 0))
                    info.compress_type = 8
                    target.writestr(info, source.read(name))
            store.content = rewritten.getvalue()
            with sessions.begin() as metadata_session:
                artifact = metadata_session.get(Artifact, "artifact-a")
                artifact.size_bytes = len(store.content)
                artifact.sha256 = hashlib.sha256(store.content).hexdigest()
            session.expire_all()
            assert service.download("artifact-a", artifact_reader)[1] == store.content
            original = store.content
            eocd = original.rfind(b"PK\x05\x06")
            central = int.from_bytes(original[eocd + 16:eocd + 20], "little")
            name_len = int.from_bytes(original[26:28], "little")
            extra_len = int.from_bytes(original[28:30], "little")
            compressed_size = int.from_bytes(original[18:22], "little")
            compressed_start = 30 + name_len + extra_len
            compressed_end = compressed_start + compressed_size
            marker = b"hidden-deflate-payload"
            tainted = bytearray(original[:compressed_end] + marker + original[compressed_end:])
            tainted[18:22] = (compressed_size + len(marker)).to_bytes(4, "little")
            central_shifted = central + len(marker)
            tainted[central_shifted + 20:central_shifted + 24] = (compressed_size + len(marker)).to_bytes(4, "little")
            eocd_shifted = eocd + len(marker)
            tainted[eocd_shifted + 16:eocd_shifted + 20] = central_shifted.to_bytes(4, "little")
            store.content = bytes(tainted)
            with sessions.begin() as metadata_session:
                artifact = metadata_session.get(Artifact, "artifact-a")
                artifact.size_bytes = len(store.content)
                artifact.sha256 = hashlib.sha256(store.content).hexdigest()
            session.expire_all()
            with pytest.raises(ArtifactServiceError) as error:
                service.download("artifact-a", artifact_reader)
            assert error.value.status_code == 404
            hidden = b"private=hidden-prefix" + body
            store.content = hidden
            with sessions.begin() as metadata_session:
                artifact = metadata_session.get(Artifact, "artifact-a")
                artifact.size_bytes = len(hidden)
                artifact.sha256 = hashlib.sha256(hidden).hexdigest()
            session.expire_all()
            with pytest.raises(ArtifactServiceError) as error:
                service.download("artifact-a", artifact_reader)
            assert error.value.status_code == 404
            store.content = body
            with sessions.begin() as metadata_session:
                artifact = metadata_session.get(Artifact, "artifact-a")
                artifact.size_bytes = len(body)
                artifact.sha256 = hashlib.sha256(body).hexdigest()
            session.expire_all()
        export_view = SQLAlchemyExportRepository(session).get("export-a", actor=artifact_reader)
        serialized_view = json.dumps(export_view.model_dump(mode="json"), ensure_ascii=False)
        assert export_view.source_snapshots["records"][0]["model_version"] == "model-v7"
        assert export_view.source_snapshots["records"][0]["warnings"] == ["legacy warning"]
        assert export_view.error == {"code": "EXPORT_GENERATION_FAILED", "message": "导出生成失败", "retryable": True}
        assert all(secret not in serialized_view for secret in ("password", "private_token", "private\\x.pt", "Traceback", "hidden"))
        poisoned = b'{"records":[{"secret":"password=hidden"}]}'
        store.content = poisoned
        with sessions.begin() as tamper_session:
            artifact = tamper_session.get(Artifact, "artifact-a")
            artifact.size_bytes = len(poisoned)
            artifact.sha256 = hashlib.sha256(poisoned).hexdigest()
        session.expire_all()
        with pytest.raises(ArtifactServiceError) as error:
            service.download("artifact-a", artifact_reader)
        assert error.value.status_code == 404
        store.content = body
        for actor in (revoked_source, other_project):
            with pytest.raises(ArtifactServiceError) as error:
                service.download("artifact-a", actor)
            assert error.value.status_code == 404

        session.delete(session.get(PredictionRecordRow, "record-a"))
        session.commit()
        with pytest.raises(ArtifactServiceError) as error:
            service.get_artifact("artifact-a", artifact_reader)
        assert error.value.status_code == 404
        with pytest.raises(DataNotFoundError):
            SQLAlchemyExportRepository(session).get("export-a", actor=artifact_reader)
    engine.dispose()


class _ExportStore:
    configured = True

    def __init__(self, *, fail=False):
        self.fail = fail
        self.put_calls = 0
        self.remove_calls = 0

    def key_for(self, artifact_id):
        return artifact_id

    def put(self, artifact_id, body, *, expected_size):
        self.put_calls += 1
        if self.fail:
            raise RuntimeError("STORAGE_WRITE_FAILED")
        assert expected_size == len(body)
        return artifact_id, len(body), hashlib.sha256(body).hexdigest()

    def remove(self, _storage_key):
        self.remove_calls += 1


class _LeaseLostOnCheck:
    def __init__(self, lost_on):
        self.lost_on = lost_on
        self.checks = 0

    def check(self):
        self.checks += 1
        if self.checks == self.lost_on:
            raise LeaseLostError("OUTBOX_LEASE_LOST")


@pytest.mark.parametrize(
    "lost_on, dispatch_override, expected_error, expected_puts",
    [(1, None, "OUTBOX_LEASE_LOST", 0), (3, None, "OUTBOX_LEASE_LOST", 1), (None, "stale-dispatch", "EXPORT_JOB_FENCE_INVALID", 0)],
)
def test_export_worker_lease_loss_or_stale_fence_never_commits_terminal_state(tmp_path, lost_on, dispatch_override, expected_error, expected_puts):
    engine = create_engine(f"sqlite:///{tmp_path / f'export-lease-{lost_on}.db'}")
    event.listen(engine, "connect", lambda dbapi, _: dbapi.create_function("CHAR_LENGTH", 1, len))
    assemble_m2_metadata().create_all(engine)
    with Session(engine) as session:
        session.add(Project(id="project-a", slug="project-a", name="Project A", status="ACTIVE", revision=1))
        session.flush()
        at = datetime.now(timezone.utc)
        job = add_job_rows(
            SQLAlchemyTaskRepository(lambda: session), session, project_id="project-a", owner_id="actor-a",
            kind="export", resource_ref=ResourceRef(type="export", id="export-a"),
            cancelable=True, retryable=True, resume_mode="restart", deadline_at=None,
        )
        session.add(ExportRow(
            id="export-a", project_id="project-a", job_id=job.id, format="json", format_version="1.0",
            source_snapshots={"format_version": "1.0", "generated_at": at.isoformat(), "include": [], "records": []},
            status="QUEUED", artifact_id=None, error=None, created_at=at, completed_at=None,
        ))
        session.commit()
        dispatch_id = session.scalar(select(OutboxEvent.dispatch_id).where(
            OutboxEvent.aggregate_id == job.id, OutboxEvent.event_type == "job.execute",
        ))
        session.commit()
        store = _ExportStore()
        guard = _LeaseLostOnCheck(lost_on) if lost_on is not None else None
        dispatch = dispatch_override or dispatch_id
        with pytest.raises((LeaseLostError, RuntimeError), match=expected_error):
            with session.begin():
                current_job = session.get(Job, job.id)
                execute_export_job(session, job=current_job, store=store, dispatch_id=dispatch, lease_guard=guard)

        assert store.put_calls == expected_puts
        assert store.remove_calls == expected_puts
        assert session.get(ExportRow, "export-a").status == "QUEUED"
        assert session.scalar(select(Artifact.id).where(Artifact.project_id == "project-a")) is None
        assert session.scalar(select(AuditEvent.id).where(AuditEvent.action == "export.generate")) is None
    engine.dispose()


def test_export_storage_failure_commits_only_failed_lifecycle_and_audit(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'export-failure.db'}")
    event.listen(engine, "connect", lambda dbapi, _: dbapi.create_function("CHAR_LENGTH", 1, len))
    assemble_m2_metadata().create_all(engine)
    with Session(engine) as session:
        session.add(Project(id="project-a", slug="project-a", name="Project A", status="ACTIVE", revision=1))
        session.flush()
        at = datetime.now(timezone.utc)
        job = add_job_rows(
            SQLAlchemyTaskRepository(lambda: session), session, project_id="project-a", owner_id="actor-a",
            kind="export", resource_ref=ResourceRef(type="export", id="export-a"),
            cancelable=True, retryable=True, resume_mode="restart", deadline_at=None,
        )
        session.add(ExportRow(
            id="export-a", project_id="project-a", job_id=job.id, format="json", format_version="1.0",
            source_snapshots={"format_version": "1.0", "generated_at": at.isoformat(), "include": [], "records": []},
            status="QUEUED", artifact_id=None, error=None, created_at=at, completed_at=None,
        ))
        session.commit()
        dispatch_id = session.scalar(select(OutboxEvent.dispatch_id).where(
            OutboxEvent.aggregate_id == job.id, OutboxEvent.event_type == "job.execute",
        ))
        session.commit()
        with session.begin():
            current_job = session.get(Job, job.id)
            result = execute_export_job(session, job=current_job, store=_ExportStore(fail=True), dispatch_id=dispatch_id)
        assert result["status"] == "FAILED"
        assert session.get(ExportRow, "export-a").status == "FAILED"
        assert session.scalar(select(Artifact.id).where(Artifact.project_id == "project-a")) is None
        assert session.scalar(select(AuditEvent.id).where(AuditEvent.action == "export.generate")) is not None
    engine.dispose()


def test_prediction_input_export_projection_keeps_hplc_raw_and_legacy_v10_has_none():
    from chrom_platform.modules.data_center.persistence import _public_prediction_input_snapshot

    current = _public_prediction_input_snapshot({
        "structure_refs": ["structure-a", "structure-b"],
        "normalized_structures": ["C[C@@H](N)C(=O)O", "C[C@H](N)C(=O)O"],
        "input": {
            "model_input_smiles": [" N[C@@H](C)C(O)=O", "N[C@H](C)C(O)=O\n"],
            "isopropanol_fraction": 0.1, "flow_rate_ml_min": 1.0, "column_ids": ["ADH"],
        },
    })
    assert current["input"]["model_input_smiles"] == [" N[C@@H](C)C(O)=O", "N[C@H](C)C(O)=O\n"]
    legacy = _public_prediction_input_snapshot({
        "structure_refs": ["structure-a", "structure-b"],
        "normalized_structures": ["C[C@@H](N)C(=O)O", "C[C@H](N)C(=O)O"],
        "input": {"isopropanol_fraction": 0.1, "flow_rate_ml_min": 1.0, "column_ids": ["ADH"]},
    })
    assert "model_input_smiles" not in legacy["input"]
