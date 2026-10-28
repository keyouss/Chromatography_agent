from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateIndex

from chrom_platform.infrastructure.models import Base, Job, JobAttempt, JobEvent, OutboxEvent, Project
from chrom_platform.task_core.outbox_dispatch import (
    AsyncOutboxDispatcher,
    OutboxDispatchError,
    _mark_assistant_run_failed,
    validate_stable_payload,
)


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class _Result:
    def __init__(self, rows=(), rowcount=1):
        self.rows = list(rows)
        self.rowcount = rowcount

    def all(self):
        return self.rows


class _Session:
    def __init__(self, rows=(), *, rowcount=1):
        self.result = _Result(rows, rowcount)
        self.statements = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def begin(self):
        return _Transaction()

    async def execute(self, statement):
        self.statements.append(statement)
        return self.result


class _Factory:
    def __init__(self, session):
        self.session = session

    def __call__(self):
        return self.session


class _SequenceSession(_Session):
    def __init__(self, results):
        super().__init__()
        self.results = [_Result(rows) for rows in results]

    async def execute(self, statement):
        self.statements.append(statement)
        return self.results.pop(0)


def _event(*, payload, event_type="job.execute", dispatch_id="dispatch-1"):
    return SimpleNamespace(
        payload=payload,
        dispatch_id=dispatch_id,
        aggregate_type="job",
        aggregate_id="job-1",
        event_type=event_type,
        status="PENDING",
        attempts=0,
        lease_token=3,
        lease_owner=None,
        lease_expires_at=None,
    )


def _job():
    return SimpleNamespace(id="job-1", project_id="project-1", kind="prediction", attempt=2, resource_ref={"type": "prediction", "id": "p-1"})


def test_payload_is_exactly_the_three_stable_fields() -> None:
    assert validate_stable_payload(
        {"job_id": "job-1", "attempt": 2, "dispatch_id": "dispatch-1"},
        dispatch_id="dispatch-1",
        aggregate_id="job-1",
        attempt=2,
    ) == ("job-1", 2, "dispatch-1")
    with pytest.raises(OutboxDispatchError, match="INVALID_WORKER_PAYLOAD"):
        validate_stable_payload(
            {"job_id": "job-1", "attempt": 2, "dispatch_id": "dispatch-1", "extra": "blocked"},
            dispatch_id="dispatch-1",
            aggregate_id="job-1",
            attempt=2,
        )


def test_claim_filters_event_type_and_returns_server_metadata() -> None:
    now = datetime(2026, 9, 21, tzinfo=timezone.utc)
    row = _event(payload={"job_id": "job-1", "attempt": 2, "dispatch_id": "dispatch-1"})
    session = _Session([(row, _job())])
    dispatcher = AsyncOutboxDispatcher(_Factory(session), lease_seconds=30)

    claim = asyncio.run(dispatcher.claim_due(owner="worker-a", allowed_event_types={"job.execute"}, allowed_job_kinds={"prediction"}, now=now))

    assert claim is not None
    assert claim.payload == {"job_id": "job-1", "attempt": 2, "dispatch_id": "dispatch-1"}
    assert claim.event_type == "job.execute"
    assert claim.project_id == "project-1"
    assert claim.resource_ref == {"type": "prediction", "id": "p-1"}
    assert claim.lease_token == 4
    assert row.status == "PROCESSING"
    assert row.lease_owner == "worker-a"
    assert row.lease_expires_at == now + timedelta(seconds=30)
    assert "job.kind IN" in str(session.statements[0].compile(dialect=mysql.dialect()))


def test_malformed_candidate_is_not_claimed_and_valid_candidate_can_follow() -> None:
    bad = _event(payload={"job_id": "job-1", "attempt": 2, "dispatch_id": "wrong"})
    good = _event(payload={"job_id": "job-1", "attempt": 2, "dispatch_id": "dispatch-1"}, dispatch_id="dispatch-1")
    session = _Session([(bad, _job()), (good, _job())])

    claim = asyncio.run(AsyncOutboxDispatcher(_Factory(session)).claim_due(
        owner="worker-a", allowed_event_types={"job.execute"}, now=datetime(2026, 9, 21, tzinfo=timezone.utc)
    ))

    assert claim is not None
    assert claim.dispatch_id == "dispatch-1"
    assert bad.status == "FAILED"
    assert bad.last_error == "INVALID_WORKER_PAYLOAD"


@pytest.mark.parametrize("payload", [None, 1, "bad", ["job-1", 2]])
def test_json_scalar_or_list_payload_is_quarantined(payload) -> None:
    row = _event(payload=payload)
    session = _Session([(row, _job())])
    assert asyncio.run(AsyncOutboxDispatcher(_Factory(session)).claim_due(
        owner="worker-a", allowed_event_types={"job.execute"}, now=datetime(2026, 9, 21, tzinfo=timezone.utc)
    )) is None
    assert row.status == "FAILED"
    assert row.last_error == "INVALID_WORKER_PAYLOAD"


def test_thirty_two_bad_candidates_are_quarantined_for_the_isolation_window() -> None:
    now = datetime(2026, 9, 21, tzinfo=timezone.utc)
    bad_rows = [
        (_event(payload={"job_id": "job-1", "attempt": 2, "dispatch_id": f"bad-{index}"}, dispatch_id=f"row-{index}"), _job())
        for index in range(32)
    ]
    good = _event(payload={"job_id": "job-1", "attempt": 2, "dispatch_id": "dispatch-1"})
    session = _SequenceSession([bad_rows, [(good, _job())]])
    dispatcher = AsyncOutboxDispatcher(_Factory(session), quarantine_seconds=86400)

    assert asyncio.run(dispatcher.claim_due(owner="worker-a", allowed_event_types={"job.execute"}, now=now)) is None
    assert all(row.status == "FAILED" for row, _ in bad_rows)
    assert all(row.last_error == "INVALID_WORKER_PAYLOAD" for row, _ in bad_rows)
    assert all(row.available_at == now + timedelta(hours=24) for row, _ in bad_rows)
    assert all(row.lease_owner is None and row.lease_expires_at is None for row, _ in bad_rows)
    claim = asyncio.run(dispatcher.claim_due(owner="worker-a", allowed_event_types={"job.execute"}, now=now))
    assert claim is not None and claim.dispatch_id == "dispatch-1"


def test_ack_and_retry_are_owner_token_cas_operations() -> None:
    now = datetime(2026, 9, 21, tzinfo=timezone.utc)
    row = _event(payload={"job_id": "job-1", "attempt": 2, "dispatch_id": "dispatch-1"})
    session = _Session([(row, _job())])
    claim = asyncio.run(AsyncOutboxDispatcher(_Factory(session), lease_seconds=30).claim_due(
        owner="worker-a", allowed_event_types={"job.execute"}, now=now
    ))
    assert claim is not None

    session.result.rowcount = 0
    assert asyncio.run(AsyncOutboxDispatcher(_Factory(session)).ack(claim, now=now)) is False
    session.result.rowcount = 1
    assert asyncio.run(AsyncOutboxDispatcher(_Factory(session)).retry(claim, error_code="BROKER_DOWN", delay_seconds=2, now=now)) is True


def test_max_attempts_dead_letters_and_failed_rows_are_not_claimed_again() -> None:
    now = datetime(2026, 9, 21, tzinfo=timezone.utc)
    row = _event(payload={"job_id": "job-1", "attempt": 2, "dispatch_id": "dispatch-1"})
    row.attempts = 1
    session = _Session([(row, _job())])
    dispatcher = AsyncOutboxDispatcher(_Factory(session), max_attempts=1)

    assert asyncio.run(dispatcher.claim_due(owner="worker-a", allowed_event_types={"job.execute"}, now=now)) is None
    assert row.status == "FAILED"
    assert row.last_error == "MAX_ATTEMPTS_EXCEEDED"


def test_permanent_assistant_dispatch_projects_failed_run_once() -> None:
    from chrom_platform.modules.assistant.persistence import AgentAttemptRow, AgentEventRow, AgentRunRow

    now = datetime(2026, 9, 21, tzinfo=timezone.utc)
    run = SimpleNamespace(id="run-1", attempt=1, status="QUEUED", error=None, finished_at=None, updated_at=None,
                          revision=1, etag='"1"', last_event_seq=0)
    attempt = SimpleNamespace(agent_run_id="run-1", attempt=1, status="QUEUED", error=None,
                              finished_at=None, lease_until=None)
    events = []

    class Result:
        def scalars(self):
            return self
        def all(self):
            return list(events)

    class Session:
        def scalar(self, statement):
            name = str(statement)
            if AgentAttemptRow.__tablename__ in name:
                return attempt
            if AgentRunRow.__tablename__ in name:
                return run
            return None
        async def execute(self, _statement):
            return Result()
        def add(self, value):
            events.append(value)

    job = SimpleNamespace(kind="assistant", resource_ref={"type": "agent_run", "id": "run-1"})
    session = Session()
    asyncio.run(_mark_assistant_run_failed(
        session, job=job, attempt=1,
        error={"code": "MAX_ATTEMPTS_EXCEEDED", "message": "dispatch failed", "retryable": False}, now=now,
    ))
    first_revision = run.revision
    assert run.status == "FAILED" and attempt.status == "FAILED"
    assert len(events) == 1 and events[0].type == "run.failed"
    assert run.last_event_seq == 1
    asyncio.run(_mark_assistant_run_failed(
        session, job=job, attempt=1,
        error={"code": "OTHER_ERROR", "message": "dispatch failed", "retryable": False}, now=now,
    ))
    assert run.revision == first_revision
    assert len(events) == 1
    run.status = "QUEUED"
    run.attempt = 2
    attempt.attempt = 2
    asyncio.run(_mark_assistant_run_failed(
        session, job=job, attempt=2,
        error={"code": "OTHER_ERROR", "message": "dispatch failed", "retryable": False}, now=now,
    ))
    assert len(events) == 2
    assert {int(event.data["attempt"]) for event in events} == {1, 2}


def test_old_outbox_failure_cannot_overwrite_recovered_new_attempt() -> None:
    from chrom_platform.modules.assistant.persistence import AgentAttemptRow, AgentEventRow, AgentRunRow

    now = datetime(2026, 9, 21, tzinfo=timezone.utc)
    run = SimpleNamespace(id="run-recovered", attempt=2, status="RUNNING", error=None, finished_at=None, updated_at=None, revision=3, etag='"3"', last_event_seq=1)
    old_attempt = SimpleNamespace(agent_run_id="run-recovered", attempt=1, status="FAILED", error={"code": "OLD"}, finished_at=now, lease_until=None)
    events = []

    class Result:
        def scalars(self): return self
        def all(self): return list(events)

    class Session:
        def scalar(self, statement):
            text = str(statement)
            if AgentRunRow.__tablename__ in text: return run
            if AgentAttemptRow.__tablename__ in text: return old_attempt
            return None
        async def execute(self, _statement): return Result()
        def add(self, value): events.append(value)

    job = SimpleNamespace(kind="assistant", resource_ref={"type": "agent_run", "id": "run-recovered"})
    asyncio.run(_mark_assistant_run_failed(session=Session(), job=job, attempt=1, error={"code": "MAX_ATTEMPTS_EXCEEDED"}, now=now))
    assert run.attempt == 2 and run.status == "RUNNING" and run.error is None
    assert old_attempt.status == "FAILED" and events == []


def test_lease_columns_and_mysql_index_are_declared_without_sqlite() -> None:
    columns = OutboxEvent.__table__.c
    assert {"lease_owner", "lease_token", "lease_expires_at"} <= set(columns.keys())
    index = next(index for index in OutboxEvent.__table__.indexes if index.name == "ix_outbox_lease")
    compiled = str(CreateIndex(index).compile(dialect=mysql.dialect()))
    assert "ix_outbox_lease" in compiled


class _SQLiteAsyncTransaction:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        self.transaction = self.session.sync.begin()
        self.transaction.__enter__()
        return self

    async def __aexit__(self, *args):
        return self.transaction.__exit__(*args)


class _SQLiteAsyncSession:
    def __init__(self, sync_session):
        self.sync = sync_session

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self.sync.close()

    def begin(self):
        return _SQLiteAsyncTransaction(self)

    async def execute(self, statement):
        return self.sync.execute(statement)

    async def scalar(self, statement):
        return self.sync.scalar(statement)

    def add(self, row):
        self.sync.add(row)


def _sqlite_dispatcher_database(
    *, event_type="job.cancel", job_kind="batch_prediction", resource_type="batch_validation",
    payload=None, fencing_token=7, job_status="CANCEL_REQUESTED", include_attempt=True,
):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[
        Project.__table__, Job.__table__, JobAttempt.__table__, JobEvent.__table__, OutboxEvent.__table__,
    ])
    sync_factory = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime(2026, 10, 22, tzinfo=timezone.utc)
    if payload is None:
        payload = {"job_id": "job-batch", "attempt": 1, "dispatch_id": "cancel-1", "fencing_token": 7}
    with sync_factory.begin() as session:
        session.add(Project(id="project-1", slug="project-1", name="Project 1"))
        session.add(Job(
            id="job-batch", project_id="project-1", owner_id="actor-1", kind=job_kind,
            resource_ref={"type": resource_type, "id": "resource-1"}, status=job_status,
            attempt=1, progress={}, cancelable=True, retryable=True, resume_mode="restart",
            revision=1, etag='"1"',
        ))
        if include_attempt:
            session.add(JobAttempt(
                id="attempt-1", job_id="job-batch", attempt=1, status=job_status,
                resume_mode="restart", fencing_token=fencing_token,
            ))
        session.add(OutboxEvent(
            dispatch_id="cancel-1", aggregate_type="job", aggregate_id="job-batch",
            event_type=event_type, payload=payload, status="PENDING", attempts=0,
            available_at=now, created_at=now, lease_token=0,
        ))

    def session_factory():
        return _SQLiteAsyncSession(sync_factory())

    return engine, sync_factory, AsyncOutboxDispatcher(session_factory)


def test_real_dispatcher_claim_load_ack_for_fenced_batch_cancel() -> None:
    engine, sync_factory, dispatcher = _sqlite_dispatcher_database()
    now = datetime(2026, 10, 22, tzinfo=timezone.utc)
    claim = asyncio.run(dispatcher.claim_cancel(owner="worker-a", allowed_job_kinds={"batch_prediction"}, now=now))
    assert claim is not None
    assert claim.payload["fencing_token"] == 7
    loaded = asyncio.run(dispatcher.resolve_active(
        claim.payload,
        owner="worker-a",
        allowed_event_types={"job.cancel"},
        allowed_job_kinds={"batch_prediction"},
        now=now,
    ))
    assert loaded.lease_token == claim.lease_token
    assert loaded.resource_ref == {"type": "batch_validation", "id": "resource-1"}
    assert asyncio.run(dispatcher.ack(loaded, now=now)) is True
    with sync_factory() as session:
        assert session.get(OutboxEvent, "cancel-1").status == "SENT"
        assert session.get(Job, "job-batch").status == "CANCEL_REQUESTED"
        assert session.get(JobAttempt, "attempt-1").status == "CANCEL_REQUESTED"
    engine.dispose()


@pytest.mark.parametrize(
    "event_type,job_kind,resource_type,payload_updates,fencing_token,expected_job_status",
    [
        ("job.execute", "batch_prediction", "batch_prediction", {"fencing_token": 7}, 7, "FAILED"),
        ("job.cancel", "prediction", "prediction", {"fencing_token": 7}, 7, "FAILED"),
        ("job.cancel", "batch_prediction", "batch_prediction", {"fencing_token": 7, "unexpected": 1}, 7, "CANCEL_REQUESTED"),
        ("job.cancel", "batch_prediction", "batch_prediction", {"fencing_token": True}, 7, "CANCEL_REQUESTED"),
        ("job.cancel", "batch_prediction", "batch_prediction", {"fencing_token": -1}, 7, "CANCEL_REQUESTED"),
        ("job.cancel", "batch_prediction", "prediction", {"fencing_token": 7}, 7, "CANCEL_REQUESTED"),
        ("job.cancel", "batch_prediction", "batch_prediction", {"fencing_token": 6}, 7, "RUNNING"),
    ],
)
def test_invalid_fencing_payload_is_quarantined_without_staling_batch_owner(
    event_type, job_kind, resource_type, payload_updates, fencing_token, expected_job_status,
) -> None:
    payload = {"job_id": "job-batch", "attempt": 1, "dispatch_id": "cancel-1", **payload_updates}
    engine, sync_factory, dispatcher = _sqlite_dispatcher_database(
        event_type=event_type,
        job_kind=job_kind,
        resource_type=resource_type,
        payload=payload,
        fencing_token=fencing_token,
        job_status=expected_job_status,
    )
    now = datetime(2026, 10, 22, tzinfo=timezone.utc)
    allowed_event = "job.cancel" if event_type == "job.cancel" else "job.execute"
    claim = asyncio.run(dispatcher.claim_due(owner="worker-a", allowed_event_types={allowed_event}, now=now))
    assert claim is None
    with sync_factory() as session:
        assert session.get(OutboxEvent, "cancel-1").status == "FAILED"
        assert session.get(OutboxEvent, "cancel-1").last_error == "INVALID_WORKER_PAYLOAD"
        assert session.get(Job, "job-batch").status == expected_job_status
        if expected_job_status in {"CANCEL_REQUESTED", "RUNNING"}:
            assert session.get(JobAttempt, "attempt-1").status == expected_job_status
            assert session.scalars(select(JobEvent).where(JobEvent.job_id == "job-batch")).all() == []
    engine.dispose()


@pytest.mark.parametrize("failure", ["permanent", "retry_exhausted"])
def test_cancel_failure_after_resolve_cannot_fail_a_new_fencing_owner(failure) -> None:
    engine, sync_factory, dispatcher = _sqlite_dispatcher_database()
    dispatcher.max_attempts = 1
    now = datetime(2026, 10, 22, tzinfo=timezone.utc)
    claim = asyncio.run(dispatcher.claim_cancel(owner="worker-a", allowed_job_kinds={"batch_prediction"}, now=now))
    assert claim is not None
    claim = asyncio.run(dispatcher.resolve_active(claim.payload, owner="worker-a", allowed_event_types={"job.cancel"}, now=now))
    with sync_factory.begin() as session:
        session.get(JobAttempt, "attempt-1").fencing_token = 8
        session.get(JobAttempt, "attempt-1").status = "RUNNING"
        session.get(Job, "job-batch").status = "RUNNING"
    if failure == "permanent":
        assert asyncio.run(dispatcher.fail_permanent(claim, error_code="STALE_CANCEL", now=now)) is True
    else:
        assert asyncio.run(dispatcher.retry(claim, error_code="STALE_CANCEL", delay_seconds=0, now=now)) is True
    with sync_factory() as session:
        assert session.get(OutboxEvent, "cancel-1").status == "FAILED"
        assert session.get(Job, "job-batch").status == "RUNNING"
        assert session.get(JobAttempt, "attempt-1").status == "RUNNING"
        assert session.get(JobAttempt, "attempt-1").fencing_token == 8
        assert session.scalars(select(JobEvent).where(JobEvent.job_id == "job-batch")).all() == []
    engine.dispose()


def test_batch_cancel_requires_matching_database_attempt_for_fencing() -> None:
    engine, sync_factory, dispatcher = _sqlite_dispatcher_database(include_attempt=False)
    now = datetime(2026, 10, 22, tzinfo=timezone.utc)
    claim = asyncio.run(dispatcher.claim_cancel(owner="worker-a", allowed_job_kinds={"batch_prediction"}, now=now))
    assert claim is None
    with sync_factory() as session:
        assert session.get(OutboxEvent, "cancel-1").status == "FAILED"
        assert session.get(OutboxEvent, "cancel-1").last_error == "OUTBOX_ASSOCIATION_INVALID"
        assert session.get(Job, "job-batch").status == "CANCEL_REQUESTED"
        assert session.scalars(select(JobEvent).where(JobEvent.job_id == "job-batch")).all() == []
    engine.dispose()
