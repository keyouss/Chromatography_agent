from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from chrom_platform.infrastructure.db import Base
from chrom_platform.infrastructure.models import Job, JobAttempt, OutboxEvent, Project
from chrom_platform.modules.data_center.import_worker import DataImportWorker
from chrom_platform.modules.data_center.persistence import DataCenterBase, DataImportRow, DataVersionRow
from chrom_platform.task_core.outbox_dispatch import LeaseLostError


PROJECT_ID = "project-lease-fixture"
JOB_ID = "job-lease-fixture"
IMPORT_ID = "import-lease-fixture"
DISPATCH_ID = "dispatch-lease-fixture"
RESOURCE_REF = {"type": "data_import", "id": IMPORT_ID}
PAYLOAD = {"job_id": JOB_ID, "attempt": 1, "dispatch_id": DISPATCH_ID}


@pytest.fixture
def lease_db():
    engine = create_engine("sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    event.listen(engine, "connect", lambda connection, _record: connection.create_function("CHAR_LENGTH", 1, len))
    Base.metadata.create_all(engine)
    DataCenterBase.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    with sessions.begin() as session:
        session.add(Project(id=PROJECT_ID, slug="lease-fixture", name="Lease fixture", status="ACTIVE", revision=1))
        session.add(Job(
            id=JOB_ID, project_id=PROJECT_ID, owner_id="owner-fixture", kind="data_import_commit",
            resource_ref=RESOURCE_REF, status="QUEUED", attempt=1, progress={}, cancelable=True,
            retryable=False, resume_mode="none", revision=1, etag="a" * 64,
        ))
        session.add(JobAttempt(
            id="attempt-lease-fixture", job_id=JOB_ID, attempt=1, status="QUEUED", resume_mode="none",
        ))
        session.add(DataImportRow(
            id=IMPORT_ID, project_id=PROJECT_ID, domain_id="domain-fixture", artifact_id="source-artifact-fixture",
            schema_version="1", import_mode="append", field_mapping={}, source_description="synthetic fixture",
            revision=1, config_hash="b" * 64, etag="import-etag-fixture", status="COMMITTING", job_id=JOB_ID,
            validation_ref=None, new_data_version_id=None,
            request_snapshot={"commit_binding": {"job_id": JOB_ID, "revision": 1, "config_hash": "b" * 64, "parent_version_id": None}},
            created_at=now, updated_at=now,
        ))
        session.add(OutboxEvent(
            dispatch_id=DISPATCH_ID, aggregate_type="job", aggregate_id=JOB_ID, event_type="job.execute",
            payload=PAYLOAD, status="PROCESSING", attempts=1, available_at=now, created_at=now,
            lease_owner="worker-fixture", lease_token=7, lease_expires_at=now + timedelta(minutes=2),
        ))
    yield sessions
    engine.dispose()


class _Guard:
    def __init__(self, *, owner="worker-fixture", token=7, claim_expiry=None):
        self.claim = SimpleNamespace(
            dispatch_id=DISPATCH_ID, payload=PAYLOAD, event_type="job.execute", aggregate_type="job",
            aggregate_id=JOB_ID, project_id=PROJECT_ID, resource_ref=RESOURCE_REF, attempts=1,
            lease_owner=owner, lease_token=token,
            lease_expires_at=claim_expiry or datetime.now(timezone.utc) + timedelta(minutes=1),
        )

    def check(self):
        return None


def _version(*, session, data_import, job, payload, lease_guard):
    now = datetime.now(timezone.utc)
    session.add(DataVersionRow(
        id="version-lease-fixture", project_id=PROJECT_ID, domain_id=data_import.domain_id, version=1,
        sha256="c" * 64, status="READY", record_count=1, source_import_id=IMPORT_ID,
        snapshot_artifact_id="snapshot-artifact-fixture", schema_version="1", quality_report_ref=None,
        created_at=now, referenced_by_count=0,
    ))
    data_import.status = "COMMITTED"
    data_import.new_data_version_id = "version-lease-fixture"
    data_import.updated_at = now
    return {"data_version_id": "version-lease-fixture"}


def _worker(sessions, commit_handler):
    return DataImportWorker(
        sessions, object(), profile_resolver=lambda *_: None, commit_handler=commit_handler,
    )


def test_live_lease_allows_commit_projection(lease_db):
    worker = _worker(lease_db, _version)

    result = worker.handle(PAYLOAD, lease_guard=_Guard())

    assert result == {"status": "SUCCEEDED", "data_version_id": "version-lease-fixture"}
    with lease_db() as session:
        assert session.scalar(select(Job).where(Job.id == JOB_ID)).status == "SUCCEEDED"
        assert session.scalar(select(DataImportRow).where(DataImportRow.id == IMPORT_ID)).status == "COMMITTED"
        assert session.scalar(select(func.count()).select_from(DataVersionRow)) == 1


@pytest.mark.parametrize("stale_mode", ["expired", "taken_over"])
def test_stale_lease_rolls_back_commit_and_version(lease_db, stale_mode):
    now = datetime.now(timezone.utc)
    if stale_mode == "expired":
        outbox_owner, outbox_token, outbox_expiry = "worker-fixture", 7, now - timedelta(seconds=1)
    else:
        outbox_owner, outbox_token, outbox_expiry = "replacement-worker", 8, now + timedelta(minutes=2)
    with lease_db.begin() as session:
        outbox = session.scalar(select(OutboxEvent).where(OutboxEvent.dispatch_id == DISPATCH_ID))
        outbox.lease_owner, outbox.lease_token, outbox.lease_expires_at = outbox_owner, outbox_token, outbox_expiry

    snapshot_side_effects = []

    def handler(**kwargs):
        result = _version(**kwargs)
        snapshot_side_effects.append("snapshot-written-before-lease-fence")
        return result

    worker = _worker(lease_db, handler)
    with pytest.raises(LeaseLostError):
        worker.handle(PAYLOAD, lease_guard=_Guard())

    assert snapshot_side_effects == ["snapshot-written-before-lease-fence"]
    with lease_db() as session:
        assert session.scalar(select(Job).where(Job.id == JOB_ID)).status == "QUEUED"
        assert session.scalar(select(DataImportRow).where(DataImportRow.id == IMPORT_ID)).status == "COMMITTING"
        assert session.scalar(select(func.count()).select_from(DataVersionRow)) == 0


def test_lost_lease_during_failure_projection_preserves_original_error(lease_db):
    def broken_commit(**_kwargs):
        raise OSError("synthetic snapshot failure")

    worker = _worker(lease_db, broken_commit)
    with lease_db.begin() as session:
        outbox = session.scalar(select(OutboxEvent).where(OutboxEvent.dispatch_id == DISPATCH_ID))
        outbox.lease_owner = "replacement-worker"
        outbox.lease_token = 8

    with pytest.raises(OSError, match="synthetic snapshot failure"):
        worker.handle(PAYLOAD, lease_guard=_Guard())

    with lease_db() as session:
        assert session.scalar(select(Job).where(Job.id == JOB_ID)).status == "QUEUED"
        assert session.scalar(select(DataImportRow).where(DataImportRow.id == IMPORT_ID)).status == "COMMITTING"
        assert session.scalar(select(func.count()).select_from(DataVersionRow)) == 0


def test_worker_requires_lease_guard_outside_explicit_fixture_mode(lease_db):
    worker = _worker(lease_db, _version)

    with pytest.raises(LeaseLostError, match="OUTBOX_LEASE_REQUIRED"):
        worker.handle(PAYLOAD)
