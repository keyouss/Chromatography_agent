from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from chrom_platform.core.dto import ActorContext
from chrom_platform.infrastructure.models import (
    AuditEvent,
    Base as PlatformBase,
    IdempotencyRecord,
    Job,
    JobAttempt,
    JobEvent,
    OutboxEvent,
    Project,
)
from chrom_platform.infrastructure.repositories import SQLAlchemyIdempotencyStore
from chrom_platform.modules.assistant.persistence import (
    AgentAttemptRow,
    AgentRunRow,
    AssistantBase,
    ConversationRow,
    LlmConfigurationActiveRow,
    LlmConfigurationRow,
    MessageRow,
    SQLAlchemyAssistantRepository,
)
from chrom_platform.modules.assistant.schemas import UserMessageCreate
from chrom_platform.modules.assistant.service import AssistantService
from chrom_platform.task_core.repository import SQLAlchemyTaskRepository
from chrom_platform.task_core.service import OperationContext


def _database(*, session_class=Session, interaction="human"):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    @event.listens_for(engine, "connect")
    def _sqlite_functions(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")
        if dbapi_connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise RuntimeError("SQLite foreign-key enforcement is unavailable")
        dbapi_connection.create_function("CHAR_LENGTH", 1, lambda value: len(value) if value is not None else None)
        dbapi_connection.isolation_level = None
    @event.listens_for(engine, "begin")
    def _sqlite_begin(connection):
        connection.exec_driver_sql("BEGIN")
    PlatformBase.metadata.create_all(engine)
    AssistantBase.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, class_=session_class, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    digest = "a" * 64
    with factory.begin() as session:
        session.add(Project(id="audit-project", slug="audit-project", name="Audit test"))
        session.add(ConversationRow(
            id="audit-conversation", project_id="audit-project", owner_id="audit-actor",
            title="Test conversation", mode="knowledge", status="ACTIVE", revision=1,
            etag='"conversation-1"', created_at=now, updated_at=now,
        ))
        session.add(LlmConfigurationRow(
            id="llmcfg_test_active", project_id="audit-project", revision=f"sha256:{digest}",
            url="https://provider.invalid/v1", model_id="test-model", key_reference="test-key-reference",
            status="ACTIVE", last_test_id=None, settings={"config_hash": digest}, created_at=now,
        ))
        session.flush()
        session.add(LlmConfigurationActiveRow(
            project_id="audit-project", configuration_id="llmcfg_test_active",
            configuration_revision=f"sha256:{digest}", revision=1, etag='"active-1"', updated_at=now,
        ))
    actor = ActorContext(
        actor_id="audit-actor", tenant_id=None, project_ids=["audit-project"],
        auth_method="deployment_default", permissions=["assistant:use"], policy_version="audit-test-v1",
    )
    context = OperationContext(actor, interaction=interaction, request_id="request-audit-message-1")
    return engine, factory, context


def _service_for(session):
    repository = SQLAlchemyAssistantRepository(session)
    tasks = SQLAlchemyTaskRepository(lambda: session)
    return AssistantService(repository, idempotency_store=SQLAlchemyIdempotencyStore(session),
                            task_repository=tasks, runtime_available=True)


@pytest.mark.parametrize(("interaction", "stored_interaction"), [("human", "human"), ("assistant", "agent_tool")])
def test_first_message_audit_is_bound_to_actor_request_run_and_job_and_replay_is_single(interaction, stored_interaction):
    engine, factory, ctx = _database(interaction=interaction)
    try:
        request = UserMessageCreate(content="PRIVATE_MESSAGE_SENTINEL", client_message_id="client-message-1")
        with factory.begin() as session:
            service = _service_for(session)
            first = service.send_message(ctx, "audit-conversation", request, idempotency_key="message-key-1")
        with factory.begin() as session:
            service = _service_for(session)
            by_key = service.send_message(ctx, "audit-conversation", request, idempotency_key="message-key-1")
        with factory.begin() as session:
            service = _service_for(session)
            by_client_id = service.send_message(ctx, "audit-conversation", request, idempotency_key="message-key-replay")
        with factory.begin() as session:
            service = _service_for(session)
            by_client_id_again = service.send_message(ctx, "audit-conversation", request, idempotency_key="message-key-replay")

        assert by_key.agent_run_id == first.agent_run_id
        assert by_client_id.agent_run_id == first.agent_run_id
        assert by_client_id.message_id == first.message_id
        assert by_client_id_again.agent_run_id == first.agent_run_id
        assert by_client_id_again.message_id == first.message_id
        with factory() as session:
            audits = list(session.scalars(select(AuditEvent)))
            assert len(audits) == 1
            audit = audits[0]
            run = session.get(AgentRunRow, first.agent_run_id)
            message = session.get(MessageRow, first.message_id)
            assert run is not None and message is not None
            job_id = next(ref["id"] for ref in run.related_resources if ref["type"] == "job")
            job = session.get(Job, job_id)
            assert job is not None
            assert audit.project_id == "audit-project"
            assert audit.actor_id == ctx.actor.actor_id
            assert audit.auth_method == ctx.actor.auth_method
            assert audit.interaction == stored_interaction
            assert audit.request_id == ctx.request_id
            assert audit.action == "assistant.message.accepted"
            assert audit.resource_type == "agent_run"
            assert audit.resource_id == run.id == first.agent_run_id
            assert audit.resource_ref == {"type": "agent_run", "id": run.id}
            assert audit.job_id == job.id == job_id
            assert audit.agent_run_id == run.id
            assert audit.result == "SUCCEEDED"
            assert audit.after_summary == {"status": "QUEUED", "message_id": message.id, "job_id": job.id}
            assert "PRIVATE_MESSAGE_SENTINEL" not in repr(audit.after_summary)
            replay_idem = session.scalar(select(IdempotencyRecord).where(IdempotencyRecord.key == "message-key-replay"))
            assert replay_idem is not None and replay_idem.status == "COMPLETED"
            assert replay_idem.result_resource_ref["agent_run_id"] == first.agent_run_id
            first_idem = session.scalar(select(IdempotencyRecord).where(IdempotencyRecord.key == "message-key-1"))
            assert first_idem is not None and first_idem.status == "COMPLETED"
    finally:
        engine.dispose()


def test_audit_flush_failure_rolls_back_message_run_job_attempt_outbox_and_audit():
    class FailAuditSession(Session):
        fail_on_audit = False

    def reject_audit_flush(session, _flush_context, _instances):
        if session.fail_on_audit and any(isinstance(row, AuditEvent) for row in session.new):
            raise RuntimeError("injected audit persistence failure")

    event.listen(FailAuditSession, "before_flush", reject_audit_flush)
    engine = None
    try:
        engine, factory, ctx = _database(session_class=FailAuditSession)
        FailAuditSession.fail_on_audit = True
        with pytest.raises(RuntimeError, match="injected audit persistence failure"):
            with factory.begin() as session:
                _service_for(session).send_message(
                    ctx, "audit-conversation",
                    UserMessageCreate(content="PRIVATE_ROLLBACK_SENTINEL", client_message_id="rollback-message"),
                    idempotency_key="rollback-key",
                )
        FailAuditSession.fail_on_audit = False
        with factory() as session:
            for model in (MessageRow, AgentRunRow, AgentAttemptRow, Job, JobAttempt, JobEvent, OutboxEvent, AuditEvent, IdempotencyRecord):
                assert session.scalar(select(func.count()).select_from(model)) == 0, model.__name__
            assert session.get(ConversationRow, "audit-conversation") is not None
    finally:
        event.remove(FailAuditSession, "before_flush", reject_audit_flush)
        if engine is not None:
            engine.dispose()
