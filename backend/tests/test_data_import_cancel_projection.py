from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from chrom_platform.artifact_core.models import Artifact
from chrom_platform.infrastructure.db import Base
from chrom_platform.infrastructure.metadata import compose_metadata
from chrom_platform.infrastructure.models import Job, JobAttempt, JobEvent, OutboxEvent, Project
from chrom_platform.modules.data_center.persistence import DataCenterBase, DataImportRow
from chrom_platform.worker_cli import _cancel_data_import_job


_PROJECT = "project-cancel-fixture"
_IMPORT = "import-cancel-fixture"
_JOB = "job-cancel-fixture"
_EXEC_DISPATCH = "dispatch-execute-fixture"
_CANCEL_DISPATCH = "dispatch-cancel-fixture"


def _fixture(*, job_status="CANCELED", attempt=1, kind="data_import_validation", resource_ref=None,
             import_status="VALIDATING", import_job_id=_JOB, attempt_status="QUEUED"):
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as connection:
        connection.connection.create_function("CHAR_LENGTH", 1, len)
    compose_metadata(Base.metadata, DataCenterBase.metadata).create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    resource_ref = resource_ref or {"type": "data_import", "id": _IMPORT}
    payload = {"job_id": _JOB, "attempt": 1, "dispatch_id": _CANCEL_DISPATCH}
    with sessions.begin() as session:
        session.add(Project(id=_PROJECT, slug="data-import-cancel", name="cancel fixture"))
        session.add(Artifact(
            id="artifact-cancel-source", project_id=_PROJECT, kind="dataset_package", title="input.csv",
            media_type="text/csv", size_bytes=1, sha256="a" * 64, storage_key="source/cancel",
            status="AVAILABLE", scan_status="PASSED", metadata_json={}, created_by="fixture-actor",
        ))
        session.add(Job(
            id=_JOB, project_id=_PROJECT, owner_id="fixture-actor", kind=kind,
            resource_ref=resource_ref, status=job_status, attempt=attempt,
            progress={"completed": 0, "total": None, "unit": "items", "stage": None},
            cancelable=True, retryable=False, resume_mode="restart", created_at=now, updated_at=now,
            started_at=None, finished_at=now if job_status == "CANCELED" else None,
            deadline_at=None, revision=1, etag="e" * 64,
        ))
        session.add(JobAttempt(
            id="attempt-cancel-fixture", job_id=_JOB, attempt=attempt, status=attempt_status,
            resume_mode="restart", execution_token=None, fencing_token=None, lease_until=None,
            checkpoint_ref=None, started_at=None, finished_at=None, error=None, result_ref=None,
        ))
        session.add(JobEvent(
            id="event-cancel-fixture", job_id=_JOB, stream_id=_JOB, seq=1,
            type="job.canceled" if job_status == "CANCELED" else "job.cancel_requested",
            schema_version="1.0", data={"status": job_status, "attempt": attempt, "reason": "fixture cancel"},
            timestamp=now,
        ))
        session.add_all([
            OutboxEvent(
                dispatch_id=_EXEC_DISPATCH, aggregate_type="job", aggregate_id=_JOB,
                event_type="job.execute", payload={"job_id": _JOB, "attempt": attempt, "dispatch_id": _EXEC_DISPATCH},
                status="PENDING", attempts=0, available_at=now, created_at=now,
            ),
            OutboxEvent(
                dispatch_id=_CANCEL_DISPATCH, aggregate_type="job", aggregate_id=_JOB,
                event_type="job.cancel", payload=payload, status="PROCESSING", attempts=1,
                available_at=now, created_at=now,
            ),
        ])
        session.add(DataImportRow(
            id=_IMPORT, project_id=_PROJECT, domain_id="fixture-domain",
            artifact_id="artifact-cancel-source", schema_version="fixture-v1", import_mode="append",
            field_mapping={"sample_id": "sample_id"}, source_description="cancel fixture",
            revision=1, config_hash="b" * 64, etag='"fixture-import-etag"',
            status=import_status, job_id=import_job_id, validation_ref="validation-fixture",
            new_data_version_id="data-version-fixture" if import_status == "COMMITTED" else None,
            request_snapshot={"validation_parent_version_id": None}, created_at=now, updated_at=now,
        ))
    return engine, sessions, payload


def test_queued_job_cancel_projects_import_failure_and_is_idempotent():
    engine, sessions, payload = _fixture()
    try:
        assert _cancel_data_import_job(sessions, payload) == {"status": "CANCELED", "job_id": _JOB}
        with sessions() as session:
            job = session.scalar(select(Job).where(Job.id == _JOB))
            attempt = session.scalar(select(JobAttempt).where(JobAttempt.job_id == _JOB))
            imported = session.scalar(select(DataImportRow).where(DataImportRow.id == _IMPORT))
            assert job.status == "CANCELED"
            assert attempt.status == "CANCELED"
            assert imported.status == "FAILED"
            assert imported.request_snapshot["terminal_projection"] == {
                "terminal_status": "CANCELED",
                "job_id": _JOB,
                "attempt": 1,
                "dispatch_id": _CANCEL_DISPATCH,
                "reason_code": "JOB_CANCELED",
            }
            terminal_projection = dict(imported.request_snapshot["terminal_projection"])

        assert _cancel_data_import_job(sessions, payload) == {"status": "CANCELED", "job_id": _JOB}
        with sessions() as session:
            imported = session.scalar(select(DataImportRow).where(DataImportRow.id == _IMPORT))
            assert imported.status == "FAILED"
            assert imported.request_snapshot["terminal_projection"] == terminal_projection
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "overrides",
    [
        {"job_status": "QUEUED", "attempt": 2},
        {"resource_ref": {"type": "batch_prediction", "id": _IMPORT}},
        {"import_job_id": "other-job"},
        {"resource_ref": {"type": "data_import", "id": "missing-import"}},
    ],
)
def test_stale_attempt_or_wrong_resource_import_binding_never_projects(overrides):
    engine, sessions, payload = _fixture(**overrides)
    try:
        result = _cancel_data_import_job(sessions, payload)
        assert result == {"status": "STALE"}
        with sessions() as session:
            imported = session.scalar(select(DataImportRow).where(DataImportRow.id == _IMPORT))
            if imported is not None:
                assert imported.status == "VALIDATING"
                assert "terminal_projection" not in (imported.request_snapshot or {})
    finally:
        engine.dispose()


def test_successful_job_and_committed_import_are_not_regressed_by_cancel_delivery():
    engine, sessions, payload = _fixture(
        job_status="SUCCEEDED", kind="data_import_commit", import_status="COMMITTED",
        attempt_status="SUCCEEDED",
    )
    try:
        assert _cancel_data_import_job(sessions, payload) == {"status": "SUCCEEDED", "job_id": _JOB}
        with sessions() as session:
            imported = session.scalar(select(DataImportRow).where(DataImportRow.id == _IMPORT))
            assert imported.status == "COMMITTED"
            assert imported.new_data_version_id == "data-version-fixture"
            assert "terminal_projection" not in (imported.request_snapshot or {})
    finally:
        engine.dispose()
