from __future__ import annotations

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from chrom_platform.infrastructure.models import Base, Job, JobAttempt, JobEvent, OutboxEvent, Project
from chrom_platform.modules.knowledge.persistence import SQLAlchemyKnowledgeJobQueue
from chrom_platform.modules.model_center.persistence import SQLAlchemyModelCenterJobPort


def _db():
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _sqlite_mysql_functions(dbapi_connection, _connection_record):
        dbapi_connection.create_function("CHAR_LENGTH", 1, lambda value: len(value) if value is not None else None)

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Project(id="project-a", slug="project-a", name="Project A", status="ACTIVE", revision=1))
        session.commit()
    return engine


def _assert_platform_job(session: Session, *, job_id: str, kind: str, resource_ref: dict[str, str], owner_id: str):
    job = session.get(Job, job_id)
    assert job is not None
    assert (job.kind, job.project_id, job.owner_id, job.resource_ref) == (kind, "project-a", owner_id, resource_ref)
    assert session.scalar(select(JobAttempt).where(JobAttempt.job_id == job.id, JobAttempt.attempt == 1)) is not None
    assert session.scalar(select(JobEvent).where(JobEvent.job_id == job.id, JobEvent.type == "job.queued")) is not None
    outbox = session.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_type == "job", OutboxEvent.aggregate_id == job.id, OutboxEvent.event_type == "job.execute"))
    assert outbox is not None
    assert set(outbox.payload) == {"job_id", "attempt", "dispatch_id"}
    assert outbox.payload == {"job_id": job.id, "attempt": 1, "dispatch_id": outbox.dispatch_id}


def test_model_center_sql_job_port_writes_platform_job_and_three_field_outbox():
    engine = _db()
    with Session(engine) as session:
        submission = SQLAlchemyModelCenterJobPort(session).submit(
            kind="model_evaluation",
            project_id="project-a",
            owner_id="actor-a",
            resource_ref={"type": "model_evaluation", "id": "evaluation-1"},
        )
        _assert_platform_job(session, job_id=submission["job_id"], kind="model_evaluation", resource_ref={"type": "model_evaluation", "id": "evaluation-1"}, owner_id="actor-a")
        assert submission["attempt"] == 1
        assert submission["dispatch_id"]


def test_knowledge_sql_job_queue_writes_platform_job_and_three_field_outbox():
    engine = _db()
    with Session(engine) as session:
        submission = SQLAlchemyKnowledgeJobQueue(session).enqueue_knowledge_index(
            project_id="project-a",
            owner_id="actor-a",
            document_id="document-1",
            index_run_id="index-1",
        )
        _assert_platform_job(session, job_id=submission.job_id, kind="knowledge_index", resource_ref={"type": "knowledge_document", "id": "document-1"}, owner_id="actor-a")
        assert submission.attempt == 1 and submission.dispatch_id
