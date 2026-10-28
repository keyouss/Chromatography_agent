from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from chrom_platform.artifact_core.models import Artifact
from chrom_platform.artifact_core.single_job import (
    RuntimeIdentity,
    SingleJobBinding,
    SingleJobError,
    _BoundedScanner,
    _validate_mapping,
)
from chrom_platform.artifact_core.storage import LocalArtifactStore
from chrom_platform.artifact_core.validation_worker import ArtifactValidationWorker
from chrom_platform.core.dto import ActorContext
from chrom_platform.infrastructure.db import Base
from chrom_platform.infrastructure.models import ExecutionLedger, Job, JobAttempt, JobEvent, OutboxEvent, Project
from chrom_platform.task_core.service import _job_etag
from chrom_platform.task_core.outbox_dispatch import AsyncOutboxDispatcher, OutboxDispatchError


class _Tx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class _Result:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def first(self):
        return self.rows[0] if self.rows else None


class _Session:
    def __init__(self, rows=(), *, artifact=None):
        self.rows = list(rows)
        self.statements = []
        self.job = self.rows[0][1] if self.rows else None
        self.artifact = artifact

    def _scalar(self, statement):
        text = str(statement)
        if "job_attempt" in text:
            return SimpleNamespace(status="QUEUED")
        if "artifact" in text:
            return self.artifact
        if "job.id" in text:
            return self.job
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def begin(self):
        return _Tx()

    async def execute(self, statement):
        self.statements.append(statement)
        return _Result(self.rows)

    async def scalar(self, statement):
        return self._scalar(statement)

    async def run_sync(self, callback):
        return callback(SimpleNamespace(scalar=self._scalar))


class _Factory:
    def __init__(self, session):
        self.session = session

    def __call__(self):
        return self.session


def _binding(root: Path, **changes):
    values = dict(
        schema="chrom_pkg_accept_20261016_01", queue="chrom-pkg-accept-20261016-01",
        store_root=str(root), project_id="project-1", job_id="job-1", attempt=1,
        dispatch_id="dispatch-1", artifact_id="artifact-1", enabled=True,
    )
    values.update(changes)
    return SingleJobBinding(**values)


def _event():
    return SimpleNamespace(
        dispatch_id="dispatch-1", aggregate_type="job", aggregate_id="job-1",
        event_type="job.execute", payload={"job_id": "job-1", "attempt": 1, "dispatch_id": "dispatch-1"},
        status="PENDING", attempts=0, available_at=datetime.now(timezone.utc),
        lease_token=2, lease_owner=None, lease_expires_at=None,
    )


def test_claim_dispatch_only_selects_exact_fresh_artifact_job(tmp_path):
    event = _event()
    job = SimpleNamespace(
        id="job-1", project_id="project-1", kind="artifact_validation", attempt=1,
        status="QUEUED", resource_ref={"type": "artifact", "id": "artifact-1"},
    )
    session = _Session([(event, job)], artifact=SimpleNamespace(
        id="artifact-1", project_id="project-1", kind="model_package",
        status="VALIDATING", scan_status="PENDING",
    ))
    now = datetime.now(timezone.utc)
    claim = asyncio.run(AsyncOutboxDispatcher(_Factory(session), lease_seconds=30).claim_dispatch(
        owner="single-job-owner", dispatch_id="dispatch-1", job_id="job-1",
        project_id="project-1", attempt=1, artifact_id="artifact-1", now=now,
        preclaim_check=lambda sync_session: sync_session.scalar("artifact") is not None,
    ))
    sql = str(session.statements[0].compile(dialect=mysql.dialect()))
    assert "outbox_event.dispatch_id =" in sql
    assert "outbox_event.attempts =" in sql
    assert "LIMIT 32" not in sql
    assert claim.dispatch_id == "dispatch-1" and claim.attempts == 1
    assert event.status == "PROCESSING" and event.lease_owner == "single-job-owner"
    assert event.lease_expires_at == now + timedelta(seconds=30)


def test_claim_dispatch_rejects_wrong_binding_without_mutating_event():
    event = _event()
    job = SimpleNamespace(
        id="job-1", project_id="project-1", kind="artifact_validation", attempt=1,
        status="QUEUED", resource_ref={"type": "artifact", "id": "someone-elses-artifact"},
    )
    with pytest.raises(OutboxDispatchError, match="SINGLE_DISPATCH_BINDING_INVALID"):
        asyncio.run(AsyncOutboxDispatcher(_Factory(_Session([(event, job)], artifact=SimpleNamespace(
            id="artifact-1", project_id="project-1", kind="model_package",
            status="VALIDATING", scan_status="PENDING",
        )))).claim_dispatch(
            owner="single-job-owner", dispatch_id="dispatch-1", job_id="job-1",
            project_id="project-1", attempt=1, artifact_id="artifact-1",
        ))
    assert event.status == "PENDING" and event.attempts == 0


@pytest.mark.parametrize(
    ("binding_change", "actual_change", "error"),
    [
        ({"enabled": False}, {}, "SINGLE_JOB_DISABLED"),
        ({"enabled": 1}, {}, "SINGLE_JOB_DISABLED"),
        ({"schema": "chrom_assistant_accept_20261006"}, {}, "SINGLE_JOB_LEGACY_SCHEMA_BLOCKED"),
        ({"attempt": True}, {}, "SINGLE_JOB_ATTEMPT_UNSUPPORTED"),
        ({}, {"queue": "other-queue"}, "SINGLE_JOB_RUNTIME_IDENTITY_MISMATCH"),
        ({}, {"schema": "other-schema"}, "SINGLE_JOB_RUNTIME_IDENTITY_MISMATCH"),
    ],
)
def test_mapping_fails_closed_for_disabled_legacy_or_wrong_runtime(tmp_path, binding_change, actual_change, error):
    root = tmp_path / "store"
    root.mkdir()
    store = SimpleNamespace(_root=root.resolve(), configured=True)
    expected = dict(schema="chrom_pkg_accept_20261016_01", queue="chrom-pkg-accept-20261016-01", store_root=str(root))
    expected.update(actual_change)
    with pytest.raises(SingleJobError, match=error):
        _validate_mapping(
            _binding(root, **binding_change), RuntimeIdentity(**expected), store,
        )


def test_mapping_rejects_store_root_mismatch_and_missing_root(tmp_path):
    store_root = tmp_path / "store"
    other_root = tmp_path / "other-store"
    store_root.mkdir()
    other_root.mkdir()
    store = SimpleNamespace(_root=store_root.resolve(), configured=True)
    binding = _binding(store_root)
    actual = RuntimeIdentity(binding.schema, binding.queue, str(other_root))
    with pytest.raises(SingleJobError, match="SINGLE_JOB_RUNTIME_IDENTITY_MISMATCH"):
        _validate_mapping(binding, actual, store)
    with pytest.raises(SingleJobError, match="SINGLE_JOB_STORE_ROOT_UNAVAILABLE"):
        _validate_mapping(_binding(tmp_path / "missing"), RuntimeIdentity(
            binding.schema, binding.queue, str(tmp_path / "missing"),
        ), store)


class _NoopGuard:
    claim = SimpleNamespace(
        lease_owner="owner", lease_token=1,
        payload={"job_id": "job-1", "attempt": 1, "dispatch_id": "dispatch-1"},
    )

    def check(self):
        return None


class _SessionContext:
    job_status = "RUNNING"
    fencing_token = 1

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def get(self, model, key):
        if model.__name__ == "Job":
            return SimpleNamespace(
                id=key, status=self.job_status, attempt=1, kind="artifact_validation",
                project_id="project-1", resource_ref={"type": "artifact", "id": "artifact-1"},
            )
        return SimpleNamespace(
            dispatch_id=key, status="PROCESSING", aggregate_type="job", aggregate_id="job-1",
            event_type="job.execute", payload={"job_id": "job-1", "attempt": 1, "dispatch_id": "dispatch-1"},
            lease_owner="owner", lease_token=1,
            lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        )

    async def scalar(self, _statement):
        return SimpleNamespace(
            id="attempt-1", status="RUNNING", fencing_token=self.fencing_token,
            execution_token="artifact-validation:dispatch-1:1",
        )


class _Scanner:
    def __init__(self, on_scan=None):
        self.calls = 0
        self.on_scan = on_scan

    def scan(self, **_kwargs):
        self.calls += 1
        if self.on_scan is not None:
            self.on_scan()
        return object()


def test_scanner_budget_is_four_and_checks_current_fence_each_time(tmp_path):
    _SessionContext.job_status = "RUNNING"
    _SessionContext.fencing_token = 1
    root = tmp_path / "store"
    root.mkdir()
    identity = (root.stat().st_dev, root.stat().st_ino)
    scanner = _Scanner()

    async def run():
        bounded = _BoundedScanner(
            scanner, _NoopGuard(), _SessionContext,
            _binding(root), attempt_id="attempt-1", fencing_token=1,
            execution_token="artifact-validation:dispatch-1:1", store_identity=identity,
        )
        for _ in range(4):
            await asyncio.to_thread(bounded.scan)
        with pytest.raises(SingleJobError, match="SCANNER_CALL_BUDGET_EXCEEDED"):
            await asyncio.to_thread(bounded.scan)

    asyncio.run(run())
    assert scanner.calls == 4


@pytest.mark.parametrize("change", ["fence", "cancel"])
def test_scanner_stops_after_current_attempt_is_fenced_or_canceled(tmp_path, change):
    root = tmp_path / "store"
    root.mkdir()
    _SessionContext.job_status = "RUNNING"
    _SessionContext.fencing_token = 1

    def change_state():
        if change == "fence":
            _SessionContext.fencing_token += 1
        else:
            _SessionContext.job_status = "CANCEL_REQUESTED"

    scanner = _Scanner(on_scan=change_state)

    async def run():
        bounded = _BoundedScanner(
            scanner, _NoopGuard(), _SessionContext, _binding(root),
            attempt_id="attempt-1", fencing_token=1,
            execution_token="artifact-validation:dispatch-1:1",
            store_identity=(root.stat().st_dev, root.stat().st_ino),
        )
        with pytest.raises(SingleJobError, match="SINGLE_JOB_CANCELED_OR_FENCED"):
            await asyncio.to_thread(bounded.scan)

    asyncio.run(run())
    assert scanner.calls == 1


class _AsyncTransaction:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        self.transaction = self.session.sync.begin()
        self.transaction.__enter__()
        return self

    async def __aexit__(self, *args):
        return self.transaction.__exit__(*args)


class _AsyncSQLiteSession:
    def __init__(self, sync_session):
        self.sync = sync_session

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self.sync.close()

    def begin(self):
        return _AsyncTransaction(self)

    async def run_sync(self, callback):
        return callback(self.sync)

    async def scalar(self, statement):
        return self.sync.scalar(statement)

    async def execute(self, statement):
        return self.sync.execute(statement)

    async def get(self, model, key):
        return self.sync.get(model, key)

    def add(self, row):
        self.sync.add(row)


class _AsyncSQLiteFactory:
    def __init__(self, sync_factory):
        self.sync_factory = sync_factory
        self.kw = {}

    def __call__(self):
        return _AsyncSQLiteSession(self.sync_factory())


def _single_job_database():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def register_char_length(connection, _record):
        connection.create_function("CHAR_LENGTH", 1, lambda value: len(value) if value is not None else None)

    Base.metadata.create_all(engine)
    sync_factory = sessionmaker(bind=engine, expire_on_commit=False)
    job = Job(
        id="job-1", project_id="project-1", owner_id="actor-1", kind="artifact_validation",
        resource_ref={"type": "artifact", "id": "artifact-1"}, status="QUEUED", attempt=1,
        progress={}, cancelable=False, retryable=False, resume_mode="restart", revision=1, etag="pending",
    )
    job.etag = _job_etag(job)
    now = datetime.now(timezone.utc)
    with sync_factory.begin() as session:
        session.add(Project(id="project-1", slug="project-1", name="Project 1"))
        session.add(Artifact(
            id="artifact-1", project_id="project-1", kind="model_package", media_type="application/zip",
            size_bytes=1, sha256="a" * 64, storage_key="package.zip", status="VALIDATING",
            scan_status="PENDING", metadata_json={}, created_by="actor-1",
        ))
        session.add(job)
        session.add(JobAttempt(
            id="attempt-1", job_id="job-1", attempt=1, status="QUEUED", resume_mode="restart",
            fencing_token=0,
        ))
        session.add(JobEvent(
            id="queued-1", job_id="job-1", stream_id="job-1", seq=0,
            type="task.queued", data={"status": "QUEUED"}, timestamp=now,
        ))
        session.add(OutboxEvent(
            dispatch_id="dispatch-1", aggregate_type="job", aggregate_id="job-1",
            event_type="job.execute", payload={"job_id": "job-1", "attempt": 1, "dispatch_id": "dispatch-1"},
            status="PENDING", attempts=0, available_at=now, lease_token=0,
        ))
    return engine, sync_factory, _AsyncSQLiteFactory(sync_factory)


def test_entry_reentry_and_claim_commit_unknown_do_not_rescan(tmp_path, monkeypatch):
    import chrom_platform.artifact_core.single_job as single_job

    engine, sync_factory, async_factory = _single_job_database()
    root = tmp_path / "store"
    root.mkdir()
    store = LocalArtifactStore(root)
    binding = _binding(root)
    actual = RuntimeIdentity(binding.schema, binding.queue, str(root))
    monkeypatch.setattr(single_job, "_probe_database_schema", lambda _factory: _async_schema(binding.schema))
    scanner = _Scanner()

    class UnknownClaimDispatcher:
        claims = 0

        async def claim_dispatch(self, **_kwargs):
            self.claims += 1
            raise RuntimeError("claim commit outcome unknown")

    dispatcher = UnknownClaimDispatcher()
    actor = ActorContext(
        actor_id="actor-1", tenant_id=None, project_ids=["project-1"],
        auth_method="deployment_default", permissions=["artifact:read"], policy_version="test",
    )

    async def run():
        with pytest.raises(RuntimeError, match="claim commit outcome unknown"):
            await single_job.run_single_job(
                binding=binding, actual=actual, dispatcher=dispatcher,
                session_factory=async_factory, store=store, scanner=scanner,
                actor=actor, owner="single-job-owner",
            )
        with pytest.raises(SingleJobError, match="SINGLE_JOB_ALREADY_ENTERED"):
            await single_job.run_single_job(
                binding=binding, actual=actual, dispatcher=dispatcher,
                session_factory=async_factory, store=store, scanner=scanner,
                actor=actor, owner="single-job-owner",
            )

    try:
        asyncio.run(run())
        with sync_factory() as session:
            ledger = session.scalar(select(ExecutionLedger))
            assert ledger.status == "UNKNOWN"
            assert ledger.operation_key == "artifact-single-validation:job-1:1"
        assert dispatcher.claims == 1 and scanner.calls == 0
    finally:
        engine.dispose()


def test_entry_runs_one_bounded_attempt_and_acks_only_after_worker_finish(tmp_path, monkeypatch):
    import chrom_platform.artifact_core.single_job as single_job

    engine, sync_factory, async_factory = _single_job_database()
    root = tmp_path / "store"
    root.mkdir()
    store = LocalArtifactStore(root)
    binding = _binding(root)
    actual = RuntimeIdentity(binding.schema, binding.queue, str(root))
    monkeypatch.setattr(single_job, "_probe_database_schema", lambda _factory: _async_schema(binding.schema))
    scanner = _Scanner()

    def controlled_component_scan(worker, _claim, _guard):
        for _ in range(4):
            worker.scanner.scan(artifact_id="artifact-1")
        return "FAILED", "FAILED", "TEST_COMPONENT_COMPLETE", "scan-1", None, None

    monkeypatch.setattr(ArtifactValidationWorker, "_validate_model_package_and_scan", controlled_component_scan)
    actor = ActorContext(
        actor_id="actor-1", tenant_id=None, project_ids=["project-1"],
        auth_method="deployment_default", permissions=["artifact:read"], policy_version="test",
    )
    dispatcher = AsyncOutboxDispatcher(async_factory, lease_seconds=30, heartbeat_seconds=10)

    try:
        result = asyncio.run(single_job.run_single_job(
            binding=binding, actual=actual, dispatcher=dispatcher,
            session_factory=async_factory, store=store, scanner=scanner,
            actor=actor, owner="single-job-owner",
        ))
        with sync_factory() as session:
            event = session.get(OutboxEvent, "dispatch-1")
            attempt = session.scalar(select(JobAttempt).where(JobAttempt.job_id == "job-1"))
            ledger = session.scalar(select(ExecutionLedger))
            assert event.status == "SENT" and event.attempts == 1
            assert attempt.status == "FAILED"
            assert ledger.status == "FAILED"
        assert result["status"] == "FAILED"
        assert scanner.calls == 4
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "binding_change",
    [
        {"project_id": "other-project"},
        {"job_id": "other-job"},
        {"attempt": 2},
        {"dispatch_id": "other-dispatch"},
    ],
)
def test_claim_dispatch_mismatch_leaves_only_bound_event_unclaimed(tmp_path, binding_change):
    engine, sync_factory, async_factory = _single_job_database()
    values = dict(
        owner="single-job-owner", dispatch_id="dispatch-1", job_id="job-1",
        project_id="project-1", attempt=1, artifact_id="artifact-1",
    )
    values.update(binding_change)
    try:
        with pytest.raises(OutboxDispatchError):
            asyncio.run(AsyncOutboxDispatcher(async_factory).claim_dispatch(**values))
        with sync_factory() as session:
            event_row = session.get(OutboxEvent, "dispatch-1")
            assert event_row.status == "PENDING" and event_row.attempts == 0
    finally:
        engine.dispose()


async def _async_schema(schema: str) -> str:
    return schema
