from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from chrom_platform.core.dto import ActorContext
from chrom_platform.infrastructure.models import (
    AuditEvent, Base as PlatformBase, Confirmation, IdempotencyRecord, Job,
    JobAttempt, JobEvent, Project,
)
from chrom_platform.infrastructure.repositories import SQLAlchemyIdempotencyStore
from chrom_platform.modules.assistant.persistence import (
    AgentAttemptRow, AgentEventRow, AgentRunRow, AssistantBase,
    AssistantFrameworkStateRow, ContextSnapshotRow, ConversationRow,
    InMemoryAssistantRepository, LlmCallRow, MessageRow, PendingConfirmationRow, SQLAlchemyAssistantRepository,
    ToolCallRow,
)
from chrom_platform.modules.assistant.errors import AssistantError
from chrom_platform.modules.assistant.schemas import ConversationCreate, ConversationDelete
from chrom_platform.modules.assistant.service import AssistantService, _hash
from chrom_platform.task_core.service import OperationContext


def _seed_delete_graph(session: Session, *, conversation_id: str, project_id: str, actor_id: str, marker: str) -> dict[str, str]:
    """Seed one terminal assistant graph and an unrelated business Job.

    Insert the nullable ends of the migration's cyclic references first. The
    MySQL integration runner can call this helper against the migrated schema.
    """
    now = datetime.now(timezone.utc)
    id_token = marker[:8]
    ids = {name: f"qa-delete-{id_token}-{name}" for name in (
        "message", "run", "attempt", "tool", "llm", "snapshot", "confirm",
        "assistant-job", "assistant-job-attempt", "assistant-job-event",
        "business-job", "business-job-attempt", "business-job-event",
        "conversation-create-idem", "confirmation-create-idem", "delete-idem",
        "framework", "event", "pending",
    )}
    if session.get(Project, project_id) is None:
        session.add(Project(id=project_id, slug=project_id, name="Delete test project"))
        session.flush()
    session.add(ConversationRow(
        id=conversation_id, project_id=project_id, owner_id=actor_id,
        title=f"{marker} private title", mode="business", status="ACTIVE",
        current_run_id=None, revision=1, etag=f"{marker}-etag", created_at=now,
        updated_at=now,
    ))
    session.flush()
    session.add(MessageRow(
        id=ids["message"], conversation_id=conversation_id, seq=1, role="user",
        agent_run_id=None, status="FINAL", blocks=[{"type": "text", "text": f"{marker} private message"}],
        attachment_ids=[], client_message_id=f"{marker}-client", payload_hash="a" * 64,
        created_at=now,
    ))
    session.flush()
    session.add(AgentRunRow(
        id=ids["run"], conversation_id=conversation_id, project_id=project_id,
        input_message_id=ids["message"], mode="business", status="SUCCEEDED", attempt=1,
        provider_configuration_id=None, provider_configuration_revision=None,
        provider_configuration_hash="b" * 64, model_id="test-model", prompt_version="p1",
        toolset_version="t1", policy_version="policy-1", pending_actions=[],
        related_resources=[{"type": "job", "id": ids["assistant-job"]}], usage={},
        last_event_seq=1, error=None, started_at=now, finished_at=now, created_at=now,
        updated_at=now, revision=1, etag=f"{marker}-run-etag", fencing_token=0,
        lease_until=None, cancel_requested=False,
    ))
    session.flush()
    # These updates are valid with the two frozen 0006 cycle FKs enabled.
    session.execute(
        ConversationRow.__table__.update().where(ConversationRow.id == conversation_id)
        .values(current_run_id=ids["run"])
    )
    session.execute(
        MessageRow.__table__.update().where(MessageRow.id == ids["message"])
        .values(agent_run_id=ids["run"])
    )
    session.add_all([
        AgentAttemptRow(
            id=ids["attempt"], agent_run_id=ids["run"], attempt=1, status="SUCCEEDED",
            dispatch_id=None, execution_token=f"{marker}-private-execution-token",
            fencing_token=1, lease_until=None, created_at=now, started_at=now,
            finished_at=now, error={"detail": f"{marker} private attempt error"},
            result_ref={"text": f"{marker} private attempt result"},
        ),
        ToolCallRow(
            id=ids["tool"], agent_run_id=ids["run"], agent_attempt_id=ids["attempt"],
            provider_tool_call_id=f"{marker}-provider-call", tool_name="create_prediction",
            tool_schema_version="1", arguments_hash="c" * 64, binding_hash=None,
            status="SUCCEEDED", arguments_summary={"text": f"{marker} private tool args"},
            resource_ref={"type": "conversation", "id": conversation_id},
            confirmation_id=ids["confirm"], pending_confirmation=None,
            result_summary={"text": f"{marker} private tool result"}, job_id=None,
            error=None, started_at=now, finished_at=now,
        ),
        AssistantFrameworkStateRow(
            agent_run_id=ids["run"], project_id=project_id,
            conversation_id=conversation_id, engine="pydantic-ai",
            framework_version="test", serialization_version="test",
            config_hash="b" * 64,
            state={"history_state": {"messages": [f"{marker} private framework history"]}},
            stream_message_id=ids["message"], created_at=now, updated_at=now,
        ),
        AgentEventRow(
            id=ids["event"], stream_type="agent_run", stream_id=ids["run"], seq=1,
            type="run.completed", schema_version="1", data={"text": f"{marker} private event"},
            timestamp=now, agent_run_id=ids["run"],
        ),
        LlmCallRow(
            id=ids["llm"], agent_run_id=ids["run"], agent_attempt_id=ids["attempt"],
            request_index=1, transport_attempt=1, provider_configuration_id=None,
            provider_configuration_revision=None, model_id="test-model", status="SUCCEEDED",
            model_parameters_summary={"prompt": f"{marker} private llm request"}, usage={},
            estimates={}, latency_ms=1, response_summary={"text": f"{marker} private llm response"},
            error=None, logical_context_snapshot_id=ids["snapshot"],
            provider_request_snapshot_id=None, created_at=now, finished_at=now,
        ),
    ])
    session.flush()
    session.add(PendingConfirmationRow(
        id=ids["pending"], agent_run_id=ids["run"], tool_call_id=ids["tool"],
        project_id=project_id, confirmation_id=ids["confirm"],
        resource_ref={"type": "conversation", "id": conversation_id}, action="conversation.delete",
        revision=1, config_hash="e" * 64, binding_hash="f" * 64,
        schema_version="1", normalized_args={"text": f"{marker} private pending args"},
        expires_at=now + timedelta(days=1), status="CONSUMED", created_at=now,
        consumed_at=now,
    ))
    session.flush()
    session.add(ContextSnapshotRow(
        id=ids["snapshot"], llm_call_id=ids["llm"], project_id=project_id,
        view="logical_context", capture_state="AVAILABLE", capture_mode="full",
        payload_schema_version="1", payload={"text": f"{marker} private context"},
        sources=[], redactions=[], truncated=False, hash_scope="test",
        sha256="d" * 64, created_at=now, expires_at=now + timedelta(days=1),
    ))
    session.flush()

    session.add(Confirmation(
        id=ids["confirm"], project_id=project_id, actor_id=actor_id,
        resource_ref={"type": "conversation", "id": conversation_id},
        action="conversation.delete", revision=1, config_hash="1" * 64,
        binding_hash="2" * 64, policy_version="policy-1",
        summary_snapshot={"summary": [{"value": f"{marker} private title"}]},
        status="CONFIRMED", created_at=now, expires_at=now + timedelta(minutes=10),
        consumed_at=None, consumed_result_ref=None, consumed_request_hash=None, revoked_at=None,
    ))
    for name, kind, ref, response, payload_hash in (
        ("conversation-create-idem", "POST", "/api/v1/conversations", {
            "id": conversation_id, "title": f"{marker} private title",
        }, _hash({"project_id": project_id, "mode": "business", "title": f"{marker} private title"})),
        ("confirmation-create-idem", "POST", "/api/v1/confirmations", {
            "id": ids["confirm"], "summary_snapshot": f"{marker} private title",
        }, "3" * 64),
    ):
        session.add(IdempotencyRecord(
            id=ids[name], actor_id=actor_id, project_id=project_id, method=kind,
            route_identity=ref, key=name, payload_hash=payload_hash, status="COMPLETED",
            error_code=None, result_resource_ref=response, result_status_code=201,
            expires_at=now + timedelta(days=1), created_at=now, completed_at=now,
        ))
    session.add_all([
        Job(
            id=ids["assistant-job"], project_id=project_id, owner_id=actor_id,
            kind="assistant", resource_ref={"type": "agent_run", "id": ids["run"]},
            status="SUCCEEDED", attempt=1, progress={"stage": "done"}, cancelable=False,
            retryable=True, resume_mode="restart", created_at=now, updated_at=now,
            started_at=now, finished_at=now, deadline_at=None,
            result_ref={"text": f"{marker} private job result"}, error={"detail": marker},
            revision=1, etag=f"{marker}-job-etag",
        ),
        JobAttempt(
            id=ids["assistant-job-attempt"], job_id=ids["assistant-job"], attempt=1,
            status="SUCCEEDED", resume_mode="restart", execution_token=f"{marker}-private-job-token",
            fencing_token=1, lease_until=None, checkpoint_ref={"text": marker},
            started_at=now, finished_at=now, error={"detail": marker},
            result_ref={"text": f"{marker} private attempt result"},
        ),
        JobEvent(
            id=ids["assistant-job-event"], job_id=ids["assistant-job"],
            stream_id=ids["assistant-job"], seq=1, type="job.succeeded",
            schema_version="1", data={"status": "SUCCEEDED", "attempt": 1, "message": f"{marker} private event"}, timestamp=now,
        ),
        Job(
            id=ids["business-job"], project_id=project_id, owner_id=actor_id,
            kind="prediction", resource_ref={"type": "prediction", "id": f"{marker}-prediction"},
            status="SUCCEEDED", attempt=1, progress={}, cancelable=False, retryable=False,
            resume_mode="none", created_at=now, updated_at=now, started_at=now,
            finished_at=now, deadline_at=None, result_ref={"prediction_id": f"{marker}-prediction"},
            error=None, revision=1, etag=f"{marker}-business-job-etag",
        ),
        JobAttempt(
            id=ids["business-job-attempt"], job_id=ids["business-job"], attempt=1,
            status="SUCCEEDED", resume_mode="none", execution_token=None, fencing_token=1,
            lease_until=None, checkpoint_ref=None, started_at=now, finished_at=now,
            error=None, result_ref={"prediction_id": f"{marker}-prediction"},
        ),
        JobEvent(
            id=ids["business-job-event"], job_id=ids["business-job"],
            stream_id=ids["business-job"], seq=1, type="job.succeeded",
            schema_version="1", data={"status": "SUCCEEDED"}, timestamp=now,
        ),
    ])
    session.flush()
    return ids


def _database():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _sqlite_functions(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.create_function("CHAR_LENGTH", 1, lambda value: len(value) if value is not None else None)
        connection.isolation_level = None

    @event.listens_for(engine, "begin")
    def _sqlite_begin(connection):
        connection.exec_driver_sql("BEGIN")

    PlatformBase.metadata.create_all(engine)
    AssistantBase.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


class _Confirmation:
    def consume_for_mutation(self, _ctx, confirmation_id, *, target, action, result_ref, session):
        assert confirmation_id.startswith("qa-delete-")
        assert target.resource_ref.model_dump(mode="json")["type"] == "conversation"
        assert action == "conversation.delete"
        assert result_ref["deleted"] is True


def _context(actor_id: str, project_id: str) -> OperationContext:
    actor = ActorContext(
        actor_id=actor_id, tenant_id=None, project_ids=[project_id],
        auth_method="deployment_default", permissions=["assistant:use"],
        policy_version="policy-1",
    )
    return OperationContext(actor, interaction="human", request_id="qa-delete-request")


def test_delete_erases_private_graph_and_keeps_safe_job_and_business_history() -> None:
    engine, factory = _database()
    marker = "PRIVATE-SENTINEL-graph-1"
    id_token = marker[:8]
    project_id = f"qa-delete-{id_token}-project"
    actor_id = f"qa-delete-{id_token}-actor"
    conversation_id = f"qa-delete-{id_token}-conversation"
    try:
        with factory.begin() as session:
            ids = _seed_delete_graph(
                session, conversation_id=conversation_id, project_id=project_id,
                actor_id=actor_id, marker=marker,
            )
        with factory.begin() as session:
            service = AssistantService(
                SQLAlchemyAssistantRepository(session),
                idempotency_store=SQLAlchemyIdempotencyStore(session),
                confirmation=_Confirmation(),
            )
            deleted = service.delete_conversation(
                _context(actor_id, project_id), conversation_id,
                ConversationDelete(confirmation_id=ids["confirm"]),
                expected_etag=f"{marker}-etag", idempotency_key="qa-delete-key",
            )
            assert deleted.id == conversation_id and deleted.deleted is True

        with factory.begin() as session:
            service = AssistantService(
                SQLAlchemyAssistantRepository(session),
                idempotency_store=SQLAlchemyIdempotencyStore(session),
                confirmation=_Confirmation(),
            )
            replay = service.delete_conversation(
                _context(actor_id, project_id), conversation_id,
                ConversationDelete(confirmation_id=ids["confirm"]),
                expected_etag=f"{marker}-etag", idempotency_key="qa-delete-key",
            )
            assert replay.id == conversation_id and replay.deleted is True
            with pytest.raises(AssistantError) as old_create:
                service.create_conversation(
                    _context(actor_id, project_id),
                    ConversationCreate(project_id=project_id, mode="business", title=f"{marker} private title"),
                    idempotency_key="conversation-create-idem",
                )
            assert old_create.value.status_code == 404

        with pytest.raises(AssistantError) as missing:
            with factory.begin() as session:
                service = AssistantService(
                    SQLAlchemyAssistantRepository(session),
                    idempotency_store=SQLAlchemyIdempotencyStore(session),
                    confirmation=_Confirmation(),
                )
                service.delete_conversation(
                    _context(actor_id, project_id), conversation_id,
                    ConversationDelete(confirmation_id=ids["confirm"]),
                    expected_etag=f"{marker}-etag", idempotency_key="qa-delete-new-key",
                )
        assert missing.value.status_code == 404

        with factory() as session:
            assert session.get(ConversationRow, conversation_id) is None
            assert session.get(MessageRow, ids["message"]) is None
            assert session.get(AgentRunRow, ids["run"]) is None
            for row_type, ident in (
                (AgentAttemptRow, ids["attempt"]), (ToolCallRow, ids["tool"]),
                (AssistantFrameworkStateRow, ids["run"]), (AgentEventRow, ids["event"]),
                (LlmCallRow, ids["llm"]), (ContextSnapshotRow, ids["snapshot"]),
                (PendingConfirmationRow, ids["pending"]), (Confirmation, ids["confirm"]),
            ):
                assert session.get(row_type, ident) is None
            assistant_job = session.get(Job, ids["assistant-job"])
            assert assistant_job is not None
            assert assistant_job.resource_ref == {"type": "agent_run", "id": ids["run"]}
            assert assistant_job.retryable is False and assistant_job.resume_mode == "none"
            assert assistant_job.result_ref is None and assistant_job.error is None
            assert session.get(JobAttempt, ids["assistant-job-attempt"]).result_ref is None
            safe_job_event = session.get(JobEvent, ids["assistant-job-event"])
            assert safe_job_event is not None and safe_job_event.data == {"status": "SUCCEEDED", "attempt": 1}
            assert session.get(Job, ids["business-job"]).result_ref == {"prediction_id": f"{marker}-prediction"}
            assert session.get(JobAttempt, ids["business-job-attempt"]) is not None
            assert session.get(JobEvent, ids["business-job-event"]) is not None

            old_replays = list(session.scalars(select(IdempotencyRecord).where(
                IdempotencyRecord.id.in_([ids["conversation-create-idem"], ids["confirmation-create-idem"]]),
            )))
            assert len(old_replays) == 2
            assert all(row.result_resource_ref == {"_deleted_resource": {"type": "conversation", "id": conversation_id}} for row in old_replays)
            assert all(marker not in repr(row.result_resource_ref) for row in old_replays)
            deletion_audit = session.scalar(select(AuditEvent).where(AuditEvent.resource_id == conversation_id))
            assert deletion_audit is not None
            assert deletion_audit.action == "assistant.conversation.deleted"
            assert deletion_audit.after_summary == {"deleted": True}
            assert marker not in repr(deletion_audit.before_summary)
            delete_idem = session.scalar(select(IdempotencyRecord).where(IdempotencyRecord.key == "qa-delete-key"))
            assert delete_idem is not None and delete_idem.method == "DELETE"
            assert delete_idem.result_resource_ref == {"id": conversation_id, "deleted": True}
            assert session.scalar(select(IdempotencyRecord).where(IdempotencyRecord.key == "qa-delete-new-key")) is None
    finally:
        engine.dispose()


def test_in_memory_delete_tombstones_old_create_replay_and_replays_delete_key() -> None:
    project_id = "qa-delete-memory-project"
    actor_id = "qa-delete-memory-actor"
    ctx = _context(actor_id, project_id)
    repository = InMemoryAssistantRepository()
    service = AssistantService(repository, confirmation=_Confirmation())
    original = ConversationCreate(project_id=project_id, mode="business", title="PRIVATE-SENTINEL-memory")
    conversation = service.create_conversation(ctx, original, idempotency_key="qa-delete-create-key")
    deleted = service.delete_conversation(
        ctx, conversation.id, ConversationDelete(confirmation_id="qa-delete-confirm"),
        expected_etag=conversation.etag, idempotency_key="qa-delete-memory-key",
    )
    assert deleted.deleted is True
    replay = service.delete_conversation(
        ctx, conversation.id, ConversationDelete(confirmation_id="qa-delete-confirm"),
        expected_etag=conversation.etag, idempotency_key="qa-delete-memory-key",
    )
    assert replay.id == conversation.id
    with pytest.raises(AssistantError) as old_create:
        service.create_conversation(ctx, original, idempotency_key="qa-delete-create-key")
    assert old_create.value.status_code == 404
