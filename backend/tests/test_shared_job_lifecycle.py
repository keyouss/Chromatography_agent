from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from chrom_platform.core.dto import ResourceRef
from chrom_platform.infrastructure.models import Base, Job, JobAttempt, JobEvent, OutboxEvent, Project
from chrom_platform.task_core.repository import SQLAlchemyTaskRepository
from chrom_platform.task_core.service import add_job_rows, sync_job_lifecycle
from chrom_platform.task_core.state import JobStatus


def test_shared_lifecycle_fences_retry_and_deduplicates_terminal_events():
    engine = create_engine("sqlite:///:memory:")
    event.listen(engine, "connect", lambda dbapi, _: dbapi.create_function("CHAR_LENGTH", 1, lambda value: len(value) if value is not None else None))
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Project(id="p1", slug="p1", name="P1"))
        repo = SQLAlchemyTaskRepository(lambda: session)
        job = add_job_rows(repo, session, project_id="p1", owner_id="u1", kind="knowledge_index", resource_ref=ResourceRef(type="knowledge_document", id="d1"), cancelable=True, retryable=True, resume_mode="restart", deadline_at=None)
        session.flush()
        assert sync_job_lifecycle(repo, session, job_id=job.id, status=JobStatus.RUNNING, attempt=1) is not None
        assert sync_job_lifecycle(repo, session, job_id=job.id, status=JobStatus.CANCELED, attempt=1) is not None
        assert sync_job_lifecycle(repo, session, job_id=job.id, status=JobStatus.CANCELED, attempt=1) is not None
        assert len(session.scalars(select(JobEvent).where(JobEvent.job_id == job.id, JobEvent.type == "job.canceled")).all()) == 1
        job, _ = __import__("chrom_platform.task_core.service", fromlist=["add_job_attempt_rows"]).add_job_attempt_rows(repo, session, job_id=job.id, attempt=2, status=JobStatus.QUEUED)
        assert sync_job_lifecycle(repo, session, job_id=job.id, status=JobStatus.SUCCEEDED, attempt=1) is None
        assert session.get(Job, job.id).status == "QUEUED"
        assert session.get(JobAttempt, (next(a.id for a in session.scalars(select(JobAttempt).where(JobAttempt.job_id == job.id, JobAttempt.attempt == 2))),)).status == "QUEUED"


def test_existing_attempt_repairs_missing_execute_outbox_without_duplicate_event():
    engine = create_engine("sqlite:///:memory:")
    event.listen(engine, "connect", lambda dbapi, _: dbapi.create_function("CHAR_LENGTH", 1, lambda value: len(value) if value is not None else None))
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Project(id="p1", slug="p1", name="P1"))
        repo = SQLAlchemyTaskRepository(lambda: session)
        job = add_job_rows(repo, session, project_id="p1", owner_id="u1", kind="assistant", resource_ref=ResourceRef(type="agent_run", id="r1"), cancelable=True, retryable=True, resume_mode="restart", deadline_at=None)
        session.flush()
        add_job_attempt_rows = __import__("chrom_platform.task_core.service", fromlist=["add_job_attempt_rows"]).add_job_attempt_rows
        add_job_attempt_rows(repo, session, job_id=job.id, attempt=2, status=JobStatus.QUEUED)
        outbox = session.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_id == job.id, OutboxEvent.event_type == "job.execute", OutboxEvent.payload["attempt"].as_integer() == 2))
        session.delete(outbox)
        session.flush()
        add_job_attempt_rows(repo, session, job_id=job.id, attempt=2, status=JobStatus.QUEUED)
        repaired = session.scalars(select(OutboxEvent).where(OutboxEvent.aggregate_id == job.id, OutboxEvent.event_type == "job.execute", OutboxEvent.payload["attempt"].as_integer() == 2)).all()
        assert len(repaired) == 1
        assert len(session.scalars(select(JobAttempt).where(JobAttempt.job_id == job.id, JobAttempt.attempt == 2)).all()) == 1
        assert len(session.scalars(select(JobEvent).where(JobEvent.job_id == job.id, JobEvent.data["attempt"].as_integer() == 2)).all()) == 1
