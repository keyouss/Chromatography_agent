from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy.dialects import mysql

from test_training_vertical import build_service, request

from chrom_platform.modules.training.persistence import (
    InMemoryTrainingRepository,
    SQLAlchemyTrainingRepository,
    TrainingOutboxDispatcher,
)
from chrom_platform.modules.training.schemas import TrainingJob, TrainingOutbox, WorkerMessage
from chrom_platform.modules.training.worker import TrainingWorker


def _repo() -> tuple[InMemoryTrainingRepository, WorkerMessage]:
    repo = InMemoryTrainingRepository()
    message = WorkerMessage(job_id="job-1", attempt=1, dispatch_id="dispatch-1")
    now = datetime.now(timezone.utc)
    repo.jobs[message.job_id] = TrainingJob(
        id=message.job_id,
        project_id="project-1",
        experiment_id="experiment-1",
        status="QUEUED",
        attempt=message.attempt,
        dispatch_id=message.dispatch_id,
        created_at=now,
    )
    repo.outbox[message.dispatch_id] = TrainingOutbox(
        dispatch_id=message.dispatch_id,
        job_id=message.job_id,
        status="PENDING",
        attempts=0,
        payload=message,
        created_at=now,
    )
    return repo, message


def test_dispatcher_claims_exact_three_fields_and_fences_old_token() -> None:
    repo, message = _repo()
    first = TrainingOutboxDispatcher(repo, owner="dispatcher-a")
    second = TrainingOutboxDispatcher(repo, owner="dispatcher-b")

    lease = first.claim(message)
    assert lease is not None and lease.token == 1
    assert repo.outbox[message.dispatch_id].status == "PROCESSING"
    assert repo.outbox[message.dispatch_id].payload.model_dump() == {
        "job_id": "job-1", "attempt": 1, "dispatch_id": "dispatch-1",
    }
    assert second.claim(message) is None

    recovered = second.recover(message)
    assert recovered is not None and recovered.token == 2
    assert first.ack(lease) is False
    assert first.retry(lease) is False
    assert second.ack(recovered) is True
    assert repo.outbox[message.dispatch_id].status == "SENT"


def test_dispatcher_claim_next_polls_one_training_message() -> None:
    repo, message = _repo()
    dispatcher = TrainingOutboxDispatcher(repo, owner="dispatcher")

    lease = dispatcher.claim_next()

    assert lease is not None
    assert lease.message == message
    assert lease.token == 1
    assert repo.outbox[message.dispatch_id].status == "PROCESSING"


def test_sql_claim_next_compiles_mysql_skip_locked_without_sqlite_execution() -> None:
    class SessionProbe:
        def __init__(self):
            self.statement = None

        def add(self, _row): pass
        def add_all(self, _rows): pass
        def flush(self): pass
        def get(self, *_args): return None
        def scalars(self, _statement): return []

        def scalar(self, statement):
            self.statement = statement
            return None

    session = SessionProbe()
    assert SQLAlchemyTrainingRepository(session).claim_next_outbox(owner="dispatcher") is None
    compiled = str(session.statement.compile(dialect=mysql.dialect()))
    assert "FOR UPDATE SKIP LOCKED" in compiled
    assert "training_outbox.status IN" in compiled


def test_recover_requires_processing_and_does_not_touch_other_dispatch() -> None:
    repo, message = _repo()
    other = WorkerMessage(job_id="job-2", attempt=1, dispatch_id="dispatch-2")
    repo.jobs[other.job_id] = repo.jobs[message.job_id].model_copy(update={"id": other.job_id, "dispatch_id": other.dispatch_id})
    repo.outbox[other.dispatch_id] = repo.outbox[message.dispatch_id].model_copy(update={"dispatch_id": other.dispatch_id, "job_id": other.job_id, "payload": other})
    dispatcher = TrainingOutboxDispatcher(repo, owner="dispatcher")

    assert dispatcher.recover(message) is None
    assert repo.outbox[message.dispatch_id].status == "PENDING"
    assert repo.outbox[other.dispatch_id].status == "PENDING"
    assert dispatcher.claim(message) is not None
    assert dispatcher.recover(other) is None
    assert repo.outbox[other.dispatch_id].status == "PENDING"


def test_dispatcher_retry_then_redelivery_ack_is_single_transport_owner() -> None:
    repo, message = _repo()
    dispatcher = TrainingOutboxDispatcher(repo, owner="dispatcher")

    lease = dispatcher.claim(message)
    assert lease is not None
    assert dispatcher.retry(lease) is True
    assert repo.outbox[message.dispatch_id].status == "FAILED"
    redelivery = dispatcher.claim(message)
    assert redelivery is not None and redelivery.token == 2
    assert dispatcher.ack(redelivery) is True
    assert repo.outbox[message.dispatch_id].status == "SENT"
    assert dispatcher.claim(message) is None


def test_worker_does_not_ack_transport_or_increment_transport_attempts() -> None:
    calls = 0

    def handler(spec, *, attempt):
        nonlocal calls
        calls += 1
        return {"artifact_id": "artifact-1", "manifest": {}}

    repo, confirmation, registry, service, candidate = build_service(handler=handler)
    experiment = service.create(request())
    service.validate("p1", experiment.id)
    confirmation.add(
        "confirm-1", project_id="p1", resource_ref={"type": "experiment", "id": experiment.id},
        action="training.submit", revision=experiment.revision, config_hash=experiment.config_hash,
    )
    accepted = service.submit("p1", experiment.id, "confirm-1", if_match=experiment.etag)
    job = repo.get_job(accepted.job_id)
    result = TrainingWorker(repo, registry, candidate_registration=candidate).process(
        WorkerMessage(job_id=job.id, attempt=job.attempt, dispatch_id=job.dispatch_id),
    )

    outbox = repo.get_outbox(job.dispatch_id)
    assert result.status == "SUCCEEDED"
    assert calls == 1
    assert outbox is not None and outbox.status == "PENDING" and outbox.attempts == 0


def test_cancelled_job_is_not_executed_as_execute() -> None:
    calls = 0

    def handler(spec, *, attempt):
        nonlocal calls
        calls += 1
        return {"artifact_id": "artifact-1", "manifest": {}}

    repo, confirmation, registry, service, candidate = build_service(handler=handler)
    experiment = service.create(request())
    service.validate("p1", experiment.id)
    confirmation.add(
        "confirm-2", project_id="p1", resource_ref={"type": "experiment", "id": experiment.id},
        action="training.submit", revision=experiment.revision, config_hash=experiment.config_hash,
    )
    accepted = service.submit("p1", experiment.id, "confirm-2", if_match=experiment.etag)
    job = repo.get_job(accepted.job_id)
    repo.save_job(job.model_copy(update={"status": "CANCEL_REQUESTED"}))

    result = TrainingWorker(repo, registry, candidate_registration=candidate).process(
        WorkerMessage(job_id=job.id, attempt=job.attempt, dispatch_id=job.dispatch_id),
    )
    assert result.status == "CANCELED"
    assert calls == 0
