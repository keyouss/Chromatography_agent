from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select, update
from sqlalchemy.orm import sessionmaker

from chrom_platform.core.dto import ActorContext, ResourceRef
from chrom_platform.infrastructure.db import Base
from chrom_platform.infrastructure.metadata import compose_metadata
from chrom_platform.infrastructure.models import Confirmation, Job, JobAttempt, OutboxEvent, Project
from chrom_platform.modules.data_center.errors import DataConflictError
from chrom_platform.modules.data_center.import_workflow import SQLAlchemyImportWorkflow
from chrom_platform.modules.data_center.persistence import (
    DataCenterBase,
    DataDomainRow,
    DataImportRow,
    DataVersionRow,
)
from chrom_platform.task_core.policy import DeploymentTaskPolicy
from chrom_platform.task_core.repository import SQLAlchemyTaskRepository
from chrom_platform.task_core.service import ConfirmationService, OperationContext, TargetSnapshot, TaskError


def _failed_import_fixture(
    *,
    job_status: str = "FAILED",
    attempt_status: str = "FAILED",
    retryable: bool = False,
    error_retryable: bool = False,
    job_kind: str = "data_import_commit",
    job_project_id: str = "project-fixture",
    resource_ref: dict[str, str] | None = None,
    import_job_id: str = "job-failed",
    published_version: bool = False,
):
    from chrom_platform.artifact_core.models import Artifact

    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as connection:
        connection.connection.create_function("CHAR_LENGTH", 1, len)
    compose_metadata(Base.metadata, DataCenterBase.metadata).create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    with sessions.begin() as session:
        session.add_all([
            Project(id="project-fixture", slug="failed-revalidation", name="failed revalidation"),
            Project(id="project-other", slug="other", name="other project"),
            Artifact(
                id="artifact-fixture", project_id="project-fixture", kind="dataset_package",
                title="fixture.csv", media_type="text/csv", size_bytes=1,
                sha256="a" * 64, storage_key="source/fixture", status="AVAILABLE",
                scan_status="PASSED", metadata_json={}, created_by="actor-fixture",
            ),
            DataDomainRow(
                id="domain-fixture", version="1", chromatography_type="tlc",
                record_schema={"measurement_id": {"type": "string", "required": True}},
                unit_definitions={}, identity_fields=["measurement_id"],
                deduplication_policy="reject", supported_import_modes=["append"],
            ),
        ])
        ref = resource_ref or {"type": "data_import", "id": "import-fixture"}
        session.add(Job(
            id="job-failed", project_id=job_project_id, owner_id="actor-fixture",
            kind=job_kind, resource_ref=ref, status=job_status, attempt=1,
            progress={}, cancelable=True, retryable=retryable, resume_mode="restart",
            created_at=now, updated_at=now, finished_at=now if job_status == "FAILED" else None,
            error={"code": "DATA_IMPORT_FAILED", "retryable": error_retryable},
            revision=1, etag="j" * 64,
        ))
        session.add(JobAttempt(
            id="attempt-failed", job_id="job-failed", attempt=1,
            status=attempt_status, resume_mode="restart",
            finished_at=now if attempt_status == "FAILED" else None,
        ))
        if import_job_id != "job-failed":
            session.add(Job(
                id="job-current", project_id="project-fixture", owner_id="actor-fixture",
                kind="prediction", resource_ref=ref, status="FAILED", attempt=1,
                progress={}, cancelable=True, retryable=False, resume_mode="restart",
                created_at=now, updated_at=now, finished_at=now,
                error={"code": "DATA_IMPORT_FAILED", "retryable": False},
                revision=1, etag="k" * 64,
            ))
            session.add(JobAttempt(
                id="attempt-current", job_id="job-current", attempt=1,
                status="FAILED", resume_mode="restart", finished_at=now,
            ))
        session.add(DataImportRow(
            id="import-fixture", project_id="project-fixture", domain_id="domain-fixture",
            artifact_id="artifact-fixture", schema_version="1", import_mode="append",
            field_mapping={"measurement_id": "measurement_id"},
            source_description="synthetic failure recovery fixture", revision=2,
            config_hash="c" * 64, etag='"failed-import-etag"', status="FAILED",
            job_id=import_job_id, validation_ref="validation-old",
            new_data_version_id="version-published" if published_version else None,
            request_snapshot={
                "source_marker": "preserve",
                "validation_parent_version_id": "old-parent",
                "validation_parent_version_sha256": "d" * 64,
                "validation_binding": {"source_sha256": "b" * 64, "report_sha256": "e" * 64},
                "commit_binding": {"job_id": import_job_id, "binding_hash": "f" * 64},
            }, created_at=now, updated_at=now,
        ))
        session.add(Confirmation(
            id="confirmation-old-commit", project_id="project-fixture", actor_id="actor-fixture",
            resource_ref={"type": "data_import", "id": "import-fixture"},
            action="data_import.commit", revision=2, config_hash="c" * 64,
            binding_hash="1" * 64, policy_version="fixture-policy-v1",
            summary_snapshot={}, status="CONSUMED", created_at=now,
            expires_at=now + timedelta(minutes=20), consumed_at=now,
            consumed_result_ref={"type": "data_import", "id": "import-fixture"},
            consumed_request_hash="2" * 64,
        ))
    return engine, sessions, now


def _context() -> OperationContext:
    actor = ActorContext(
        actor_id="actor-fixture", tenant_id=None, project_ids=["project-fixture"],
        auth_method="service", permissions=["data:write", "task:confirm"], policy_version="fixture-policy-v1",
    )
    return OperationContext(actor=actor)


class _UnusedConfirmationService:
    def consume_for_mutation(self, *_args, **_kwargs):
        raise AssertionError("revalidation must not consume the old commit confirmation")


def _workflow(session):
    return SQLAlchemyImportWorkflow(
        session, artifact_store=None, confirmation_service=_UnusedConfirmationService(),
        profile_resolver=lambda domain_id, version: (
            SimpleNamespace(profile_id="fixture-profile")
            if (domain_id, version) == ("domain-fixture", "1") else None
        ),
        id_factory=lambda prefix: f"{prefix}-revalidation",
    )


def test_nonretryable_failed_import_revalidation_clears_old_commit_binding_and_keeps_consumed_confirmation():
    engine, sessions, _now = _failed_import_fixture()
    try:
        with sessions.begin() as session:
            accepted = _workflow(session).submit_validation(
                _context(), "project-fixture", "import-fixture", if_match='"failed-import-etag"',
            )
            imported = session.scalar(select(DataImportRow).where(DataImportRow.id == "import-fixture"))
            old_confirmation = session.scalar(select(Confirmation).where(Confirmation.id == "confirmation-old-commit"))
            assert accepted.job_id == "job-revalidation"
            assert accepted.resource_ref.model_dump(mode="json") == {"type": "data_import", "id": "import-fixture"}
            assert imported.status == "VALIDATING" and imported.job_id == accepted.job_id
            assert imported.validation_ref is None and imported.new_data_version_id is None
            assert imported.request_snapshot["source_marker"] == "preserve"
            assert imported.request_snapshot["validation_parent_version_id"] is None
            assert imported.request_snapshot["validation_parent_version_sha256"] is None
            assert "commit_binding" not in imported.request_snapshot
            assert "validation_binding" not in imported.request_snapshot
            assert old_confirmation.status == "CONSUMED"
            confirmation_service = ConfirmationService(
                SQLAlchemyTaskRepository(lambda: session), policy=DeploymentTaskPolicy(),
            )
            retry_target = TargetSnapshot(
                project_id="project-fixture",
                resource_ref=ResourceRef(type="data_import", id="import-fixture"),
                revision=2, config_hash="3" * 64, etag='"failed-import-etag"',
                policy_version="fixture-policy-v1",
            )
            with pytest.raises(TaskError) as rejected_old_confirmation:
                confirmation_service.validate(
                    _context(), "confirmation-old-commit", target=retry_target,
                    action="data_import.commit", session=session,
                )
            assert rejected_old_confirmation.value.code == "CONFIRMATION_INVALID"
            assert session.scalar(select(func.count()).select_from(Confirmation)) == 1
            assert session.scalar(select(func.count()).select_from(DataVersionRow)) == 0
            assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 1
    finally:
        engine.dispose()


@pytest.mark.parametrize("case", [
    "retryable",
    "queued",
    "running",
    "canceled",
    "wrong_project",
    "wrong_kind",
    "wrong_resource_ref",
    "not_current_job",
    "published_version",
])
def test_failed_import_revalidation_rejects_nonterminal_retryable_or_wrong_scope_jobs(case: str):
    options = {
        "retryable": {"retryable": True, "error_retryable": True},
        "queued": {"job_status": "QUEUED", "attempt_status": "QUEUED"},
        "running": {"job_status": "RUNNING", "attempt_status": "RUNNING"},
        "canceled": {"job_status": "CANCELED", "attempt_status": "CANCELED"},
        "wrong_project": {"job_project_id": "project-other"},
        "wrong_kind": {"job_kind": "prediction"},
        "wrong_resource_ref": {"resource_ref": {"type": "data_import", "id": "other-import"}},
        "not_current_job": {"import_job_id": "job-current"},
        "published_version": {"published_version": True},
    }[case]
    engine, sessions, _now = _failed_import_fixture(**options)
    try:
        with pytest.raises(DataConflictError):
            with sessions.begin() as session:
                _workflow(session).submit_validation(
                    _context(), "project-fixture", "import-fixture", if_match='"failed-import-etag"',
                )
        with sessions() as session:
            imported = session.scalar(select(DataImportRow).where(DataImportRow.id == "import-fixture"))
            assert imported.status == "FAILED"
            assert session.scalar(select(func.count()).select_from(Job)) == (2 if case == "not_current_job" else 1)
            assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 0
            assert session.scalar(select(func.count()).select_from(DataVersionRow)) == 0
    finally:
        engine.dispose()


def test_failed_job_with_active_attempt_cannot_revalidate():
    engine, sessions, _now = _failed_import_fixture(attempt_status="RUNNING")
    try:
        with pytest.raises(DataConflictError), sessions.begin() as session:
            _workflow(session).submit_validation(
                _context(), "project-fixture", "import-fixture", if_match='"failed-import-etag"',
            )
        with sessions() as session:
            assert session.scalar(select(func.count()).select_from(Job)) == 1
            assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 0
    finally:
        engine.dispose()


def test_existing_version_blocks_revalidation_even_without_import_version_pointer():
    engine, sessions, now = _failed_import_fixture()
    try:
        with sessions.begin() as session:
            session.add(DataVersionRow(
                id="version-existing", project_id="project-fixture", domain_id="domain-fixture",
                version=1, sha256="d" * 64, status="READY", record_count=1,
                source_import_id="import-fixture", snapshot_artifact_id="artifact-fixture",
                schema_version="1", created_at=now, referenced_by_count=0,
            ))
        with pytest.raises(DataConflictError), sessions.begin() as session:
            _workflow(session).submit_validation(
                _context(), "project-fixture", "import-fixture", if_match='"failed-import-etag"',
            )
        with sessions() as session:
            assert session.scalar(select(func.count()).select_from(DataVersionRow)) == 1
            assert session.scalar(select(func.count()).select_from(Job)) == 1
            assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 0
    finally:
        engine.dispose()


def test_revalidation_compares_locked_row_to_observed_scalar_values(monkeypatch):
    """Exercise identity-map refresh, without claiming a MySQL concurrency test."""
    engine, sessions, _now = _failed_import_fixture()
    try:
        with pytest.raises(DataConflictError), sessions.begin() as session:
            original_scalar = session.scalar
            import_reads = 0

            def scalar_with_refresh(statement, *args, **kwargs):
                nonlocal import_reads
                descriptions = getattr(statement, "column_descriptions", [])
                if descriptions and descriptions[0].get("entity") is DataImportRow:
                    import_reads += 1
                    if import_reads == 2:
                        session.execute(update(DataImportRow).where(
                            DataImportRow.id == "import-fixture",
                        ).values(status="VALIDATING"))
                return original_scalar(statement, *args, **kwargs)

            monkeypatch.setattr(session, "scalar", scalar_with_refresh)
            _workflow(session).submit_validation(
                _context(), "project-fixture", "import-fixture", if_match='"failed-import-etag"',
            )
        with sessions() as session:
            assert session.scalar(select(func.count()).select_from(Job)) == 1
            assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 0
            assert session.scalar(select(DataImportRow.status)) == "FAILED"
    finally:
        engine.dispose()
