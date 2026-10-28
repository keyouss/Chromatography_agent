from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from chrom_platform.core.dto import ActorContext, ResourceRef
from chrom_platform.infrastructure.models import Base as PlatformBase, Job, JobAttempt, Project
from chrom_platform.modules.assistant.persistence import AgentAttemptRow, AgentRunRow, AssistantBase, ConversationRow, InMemoryAssistantRepository, LlmConfigurationActiveRow, MessageRow, SQLAlchemyAssistantRepository, SessionAssistantUnitOfWork
from chrom_platform.modules.assistant.schemas import ConversationCreate, Message, PendingConfirmationBinding, ResumeAgentRunRequest, ToolCallDraft, UserMessageCreate
from chrom_platform.modules.assistant.service import AssistantService
from chrom_platform.task_core.repository import SQLAlchemyTaskRepository
from chrom_platform.task_core.service import OperationContext, add_job_rows


def ctx(*, permissions: list[str] | None = None) -> OperationContext:
    return OperationContext(ActorContext(actor_id="a", tenant_id=None, project_ids=["p"], auth_method="deployment_default", permissions=permissions or ["*"], policy_version="policy-1"), interaction="assistant")


class Business:
    def __init__(self):
        self.validated = 0
        self.executed = 0

    def validate(self, _ctx, **_kwargs):
        self.validated += 1

    def execute(self, _ctx, **_kwargs):
        self.executed += 1
        return {"job_id": "job-1", "token": "must-not-leak"}


class Confirmation:
    def __init__(self):
        self.ids = []

    def validate_binding(self, _ctx, confirmation_id, _binding):
        self.ids.append(confirmation_id)


def service(*, permissions: list[str] | None = None, business=None, confirmation=None):
    return AssistantService(InMemoryAssistantRepository(), business_services=business or {}, confirmation=confirmation), ctx(permissions=permissions)


def conversation(s: AssistantService, c: OperationContext):
    return s.create_conversation(c, ConversationCreate(project_id="p", mode="business"))


class ConfiguredRepository(InMemoryAssistantRepository):
    """SQL-shaped test repository for the transactional active-config read."""
    is_test_only = False

    def __init__(self, active, configuration):
        super().__init__()
        self.active, self.configuration = active, configuration
        self.statements = []
        self.added = []

    def add(self, row):
        self.added.append(row)

    def scalar(self, statement):
        self.statements.append(statement)
        if len(self.statements) == 1:
            return self.active
        return self.configuration


def test_project_access_precedes_replay_and_same_client_id_replays() -> None:
    s, c = service()
    conv = conversation(s, c)
    request = UserMessageCreate(content="hello", client_message_id="client-1")
    first = s.send_message(c, conv.id, request, idempotency_key="i-1")
    replay = s.send_message(c, conv.id, request, idempotency_key="i-2")
    assert replay.message_id == first.message_id
    with pytest.raises(Exception) as exc:
        s.send_message(c, conv.id, request.model_copy(update={"content": "changed"}), idempotency_key="i-3")
    assert getattr(exc.value, "payload").code == "IDEMPOTENCY_KEY_REUSED"


def test_user_message_text_block_round_trips_with_original_whitespace_and_newlines() -> None:
    s, c = service()
    s.runtime_available = True
    conv = conversation(s, c)
    original = "第一行  保留空格\r\n第二行\n\n末尾不裁剪 "

    accepted = s.send_message(
        c,
        conv.id,
        UserMessageCreate(content=original, client_message_id="message-round-trip"),
        idempotency_key="message-round-trip",
    )

    restored = s.messages(c, conv.id)
    assert len(restored) == 1
    assert restored[0].id == accepted.message_id
    assert len(restored[0].blocks) == 1
    assert restored[0].blocks[0].type == "text"
    assert restored[0].blocks[0].text == original


def test_same_conversation_execution_slot_returns_busy() -> None:
    s, c = service()
    conv = conversation(s, c)
    s.send_message(c, conv.id, UserMessageCreate(content="one", client_message_id="one"), idempotency_key="one")
    with pytest.raises(Exception) as exc:
        s.send_message(c, conv.id, UserMessageCreate(content="two", client_message_id="two"), idempotency_key="two")
    assert getattr(exc.value, "payload").code == "CONVERSATION_BUSY"


def test_new_run_binds_locked_active_pointer_revision_hash_and_reference() -> None:
    from types import SimpleNamespace

    active = SimpleNamespace(project_id="p", configuration_id="cfg-2", configuration_revision="rev-2")
    config = SimpleNamespace(
        id="cfg-2", project_id="p", revision="rev-2", status="ACTIVE", model_id="model-2",
        key_reference="dpapi:v1:" + "a" * 32, settings={"config_hash": "b" * 64},
    )
    repo = ConfiguredRepository(active, config)
    s, c = AssistantService(repo), ctx()
    conv = conversation(s, c)

    accepted = s.send_message(c, conv.id, UserMessageCreate(content="hello", client_message_id="configured"))

    run = repo.runs[accepted.agent_run_id]
    assert (run.provider_configuration_id, run.provider_configuration_revision, run.provider_configuration_hash, run.model_id) == ("cfg-2", "rev-2", "b" * 64, "model-2")
    assert len(repo.statements) == 2
    assert repo.statements[0].column_descriptions[0]["entity"] is LlmConfigurationActiveRow
    assert repo.statements[0]._for_update_arg is not None
    assert len(repo.added) == 1
    assert repo.added[0].engine == "pydantic-ai"
    assert repo.added[0].agent_run_id == accepted.agent_run_id
    assert repo.added[0].config_hash == "b" * 64


@pytest.mark.parametrize("active,configuration", [
    (None, None),
    (type("Pointer", (), {"project_id": "p", "configuration_id": "cfg", "configuration_revision": "rev-2"})(), type("Config", (), {"id": "cfg", "project_id": "p", "revision": "rev-1", "status": "ACTIVE", "model_id": "model", "key_reference": "KEY", "settings": {"config_hash": "a" * 64}})()),
    (type("Pointer", (), {"project_id": "p", "configuration_id": "cfg", "configuration_revision": "rev-1"})(), type("Config", (), {"id": "cfg", "project_id": "p", "revision": "rev-1", "status": "ACTIVE", "model_id": "model", "key_reference": "KEY", "settings": {"config_hash": "not-a-hash"}})()),
])
def test_production_run_creation_fails_closed_for_missing_or_mismatched_active_binding(active, configuration) -> None:
    repo = ConfiguredRepository(active, configuration)
    s, c = AssistantService(repo), ctx()
    conv = conversation(s, c)

    with pytest.raises(Exception) as exc:
        s.send_message(c, conv.id, UserMessageCreate(content="hello", client_message_id="unbound"))

    assert getattr(exc.value, "payload").code == "DEPENDENCY_UNAVAILABLE"
    assert not repo.messages and not repo.runs


def test_pending_tool_call_revalidates_confirmation_and_redacts_result() -> None:
    business, confirm = Business(), Confirmation()
    s, c = service(business={"create_prediction": business}, confirmation=confirm)
    conv = conversation(s, c)
    accepted = s.send_message(c, conv.id, UserMessageCreate(content="run", client_message_id="run"), idempotency_key="run")
    draft = ToolCallDraft(provider_tool_call_id="provider-1", tool_name="create_prediction", schema_version="1.0", normalized_args={"x": 1}, resource_ref=ResourceRef(type="prediction", id="p1"), action="prediction:create", revision=1, config_hash="a" * 64, requires_confirmation=True)
    pending = s.propose_tool_call(c, accepted.agent_run_id, draft)
    assert pending.status == "WAITING_CONFIRMATION"
    done = s.resume_run(c, accepted.agent_run_id, ResumeAgentRunRequest(confirmation_id="confirm-1"))
    assert done.status == "SUCCEEDED"
    assert confirm.ids == ["confirm-1"]
    assert business.validated == 2
    assert business.executed == 1
    assert "token" not in (s.repository.tool_calls[pending.id].result_summary or {})
    assert all(attempt.status in {"WAITING_CONFIRMATION", "SUCCEEDED"} for attempt in s.repository.attempts.values())


def test_resume_rejects_tampered_pending_binding_before_business_execution() -> None:
    business, confirm = Business(), Confirmation()
    s, c = service(business={"create_prediction": business}, confirmation=confirm)
    conv = conversation(s, c)
    accepted = s.send_message(c, conv.id, UserMessageCreate(content="run", client_message_id="run"), idempotency_key="run")
    draft = ToolCallDraft(provider_tool_call_id="provider-1", tool_name="create_prediction", schema_version="1.0", normalized_args={"x": 1}, resource_ref=ResourceRef(type="prediction", id="p1"), action="prediction:create", revision=1, config_hash="a" * 64, requires_confirmation=True)
    pending = s.propose_tool_call(c, accepted.agent_run_id, draft)
    run = s.repository.runs[accepted.agent_run_id]
    run.pending_actions[0].action = "prediction:other"
    with pytest.raises(Exception) as exc:
        s.resume_run(c, accepted.agent_run_id, ResumeAgentRunRequest(confirmation_id="confirm-1"))
    assert getattr(exc.value, "payload").code == "CONFIRMATION_INVALID"
    assert business.executed == 0
    assert pending.id in s.repository.tool_calls


def test_redaction_is_recursive_and_forbidden_tool_aliases_are_rejected() -> None:
    from chrom_platform.modules.assistant.service import _safe

    assert _safe({"nested": {"accessToken": "secret", "safe": 1}}) == {"nested": {"safe": 1}}
    s, c = service()
    conv = conversation(s, c)
    accepted = s.send_message(c, conv.id, UserMessageCreate(content="run", client_message_id="run"), idempotency_key="run")
    for tool_name in ("http fetch", "confirmation:create", "model.rollback"):
        with pytest.raises(Exception) as exc:
            s.propose_tool_call(c, accepted.agent_run_id, ToolCallDraft(provider_tool_call_id=tool_name, tool_name=tool_name, schema_version="1.0"))
        assert getattr(exc.value, "payload").code == "PERMISSION_DENIED"


def test_forbidden_tool_and_missing_dependencies_are_not_success() -> None:
    s, c = service()
    conv = conversation(s, c)
    accepted = s.send_message(c, conv.id, UserMessageCreate(content="run", client_message_id="run"), idempotency_key="run")
    with pytest.raises(Exception) as exc:
        s.propose_tool_call(c, accepted.agent_run_id, ToolCallDraft(provider_tool_call_id="x", tool_name="shell", schema_version="1.0"))
    assert getattr(exc.value, "payload").code == "PERMISSION_DENIED"
    with pytest.raises(Exception) as missing:
        AssistantService().create_conversation(c, ConversationCreate(project_id="p", mode="business"))
    assert getattr(missing.value, "payload").code == "DEPENDENCY_UNAVAILABLE"


def test_cancel_agent_run_does_not_cancel_submitted_job() -> None:
    business = Business()
    s, c = service(business={"create_prediction": business})
    conv = conversation(s, c)
    accepted = s.send_message(c, conv.id, UserMessageCreate(content="run", client_message_id="run"), idempotency_key="run")
    # Direct business execution completes the AgentRun while its returned job remains an external resource.
    s.propose_tool_call(c, accepted.agent_run_id, ToolCallDraft(provider_tool_call_id="x", tool_name="create_prediction", schema_version="1.0"))
    run = s.get_run(c, accepted.agent_run_id)
    assert run.status == "SUCCEEDED"
    assert business.executed == 1


def test_cancel_running_agent_run_persists_request_and_replays_once() -> None:
    s, c = service()
    conv = conversation(s, c)
    accepted = s.send_message(c, conv.id, UserMessageCreate(content="run", client_message_id="cancel-run"), idempotency_key="send-cancel-run")
    run = s.repository.get_run(None, accepted.agent_run_id)
    attempt = s.repository.get_attempt(None, run.id, run.attempt)
    s.repository.save_run(None, run.model_copy(update={"status": "RUNNING"}))
    s.repository.save_attempt(None, attempt.model_copy(update={"status": "RUNNING"}))

    canceled = s.cancel_run(c, run.id, idempotency_key="cancel-once", expected_etag=run.etag)
    replay = s.cancel_run(c, run.id, idempotency_key="cancel-once", expected_etag=run.etag)

    assert canceled.status == replay.status == "CANCEL_REQUESTED"
    assert s.repository.get_attempt(None, run.id, run.attempt).status == "CANCEL_REQUESTED"
    assert s.repository.cancel_requested[run.id] is True
    assert [event["type"] for event in s.repository.events if event["stream_id"] == run.id] == ["run.cancel_requested"]


@pytest.mark.parametrize("initial_status", ["QUEUED", "WAITING_CONFIRMATION"])
def test_cancel_without_active_provider_converges_run_attempt_and_event(initial_status: str) -> None:
    s, c = service()
    conv = conversation(s, c)
    accepted = s.send_message(c, conv.id, UserMessageCreate(content="run", client_message_id=f"cancel-{initial_status}"), idempotency_key=f"send-{initial_status}")
    run = s.repository.get_run(None, accepted.agent_run_id)
    attempt = s.repository.get_attempt(None, run.id, run.attempt)
    s.repository.save_run(None, run.model_copy(update={"status": initial_status}))
    s.repository.save_attempt(None, attempt.model_copy(update={"status": initial_status}))

    canceled = s.cancel_run(c, run.id, idempotency_key=f"cancel-{initial_status}", expected_etag=run.etag)

    assert canceled.status == "CANCELED"
    assert canceled.finished_at is not None
    assert s.repository.get_attempt(None, run.id, run.attempt).status == "CANCELED"
    assert s.repository.get_attempt(None, run.id, run.attempt).finished_at is not None
    assert s.repository.cancel_requested[run.id] is True
    assert [event["type"] for event in s.repository.events if event["stream_id"] == run.id] == ["run.canceled"]


def test_cancel_waiting_run_clears_pending_binding_and_fails_partial_message() -> None:
    s, c = service()
    conv = conversation(s, c)
    accepted = s.send_message(c, conv.id, UserMessageCreate(content="confirm", client_message_id="cancel-waiting-partial"), idempotency_key="send-waiting-partial")
    run = s.repository.get_run(None, accepted.agent_run_id)
    attempt = s.repository.get_attempt(None, run.id, run.attempt)
    pending = PendingConfirmationBinding(
        tool_call_id="call-pending", confirmation_id="confirm-pending",
        resource_ref=ResourceRef(type="prediction", id="prediction-draft"),
        action="prediction.create", revision=1, config_hash="a" * 64,
        schema_version="1.0", normalized_args={"smiles": "C"},
    )
    waiting = run.model_copy(update={"status": "WAITING_CONFIRMATION", "pending_actions": [pending]})
    s.repository.save_run(None, waiting)
    s.repository.save_attempt(None, attempt.model_copy(update={"status": "WAITING_CONFIRMATION"}))
    s.repository.save_message(None, Message(
        id="assistant-partial", conversation_id=conv.id, seq="2", role="assistant",
        agent_run_id=run.id, status="PARTIAL", blocks=[{"type": "text", "text": "draft text"}],
        attachment_ids=[], created_at=run.created_at,
    ))

    canceled = s.cancel_run(c, run.id, idempotency_key="cancel-waiting-partial", expected_etag=waiting.etag)

    message = s.repository.messages["assistant-partial"]
    assert canceled.status == "CANCELED" and canceled.pending_actions == []
    assert message.status == "FAILED"
    assert message.blocks[0].type == "text" and message.blocks[0].text == "draft text"


def test_cancel_of_terminal_run_completes_idempotency_without_event() -> None:
    s, c = service()
    conv = conversation(s, c)
    accepted = s.send_message(c, conv.id, UserMessageCreate(content="run", client_message_id="cancel-terminal"), idempotency_key="send-terminal")
    run = s.repository.get_run(None, accepted.agent_run_id)
    terminal = run.model_copy(update={"status": "SUCCEEDED"})
    s.repository.save_run(None, terminal)

    first = s.cancel_run(c, run.id, idempotency_key="cancel-terminal", expected_etag=run.etag)
    replay = s.cancel_run(c, run.id, idempotency_key="cancel-terminal", expected_etag=run.etag)

    assert first.status == replay.status == "SUCCEEDED"
    assert not any(event["stream_id"] == run.id for event in s.repository.events)


def test_cancel_completion_race_does_not_overwrite_fresh_terminal_run() -> None:
    s, c = service()
    conv = conversation(s, c)
    accepted = s.send_message(c, conv.id, UserMessageCreate(content="run", client_message_id="cancel-race"), idempotency_key="send-race")
    original_get_run = s.repository.get_run
    initial = original_get_run(None, accepted.agent_run_id)
    calls = 0

    def finish_between_reads(session, run_id):
        nonlocal calls
        value = original_get_run(session, run_id)
        calls += 1
        if calls == 1:
            completed = value.model_copy(update={"status": "SUCCEEDED", "revision": value.revision + 1, "etag": '"completed"'})
            s.repository.save_run(session, completed)
        return value

    s.repository.get_run = finish_between_reads
    with pytest.raises(Exception) as exc:
        s.cancel_run(c, initial.id, idempotency_key="cancel-race", expected_etag=initial.etag)

    assert getattr(exc.value, "payload").code == "VERSION_CONFLICT"
    assert original_get_run(None, initial.id).status == "SUCCEEDED"


def test_cancel_projects_only_its_own_job_when_unrelated_job_precedes_it() -> None:
    engine = create_engine("sqlite://")
    @event.listens_for(engine, "connect")
    def _sqlite_char_length(connection, _record):
        connection.create_function("CHAR_LENGTH", 1, lambda value: len(value) if value is not None else None)
    PlatformBase.metadata.create_all(engine)
    AssistantBase.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    task_repo = SQLAlchemyTaskRepository(factory)
    run_id = "run-cancel-own-job"
    with factory.begin() as db:
        db.add(Project(id="p", slug="p", name="Project"))
        db.add(ConversationRow(
            id="conversation-cancel", project_id="p", owner_id="a", title="Cancel",
            mode="business", status="ACTIVE", revision=1, etag='"conversation"',
            created_at=now, updated_at=now,
        ))
        unrelated = add_job_rows(
            task_repo, db, project_id="p", owner_id="a", kind="prediction",
            resource_ref=ResourceRef(type="prediction", id="prediction-1"),
            cancelable=True, retryable=True, resume_mode="restart", deadline_at=None,
            job_id="job-unrelated-prediction",
        )
        own = add_job_rows(
            task_repo, db, project_id="p", owner_id="a", kind="assistant",
            resource_ref=ResourceRef(type="agent_run", id=run_id),
            cancelable=True, retryable=True, resume_mode="restart", deadline_at=None,
            job_id="job-assistant-run",
        )
        unrelated.status = "RUNNING"
        unrelated_attempt = db.scalar(select(JobAttempt).where(JobAttempt.job_id == unrelated.id))
        unrelated_attempt.status = "RUNNING"
        own.status = "RUNNING"
        own_attempt = db.scalar(select(JobAttempt).where(JobAttempt.job_id == own.id))
        own_attempt.status = "RUNNING"
        db.add(AgentRunRow(
            id=run_id, conversation_id="conversation-cancel", project_id="p",
            input_message_id="user-message", mode="business", status="RUNNING", attempt=1,
            model_id="model", prompt_version="prompt", toolset_version="tools", policy_version="policy",
            pending_actions=[], related_resources=[
                {"type": "job", "id": unrelated.id}, {"type": "job", "id": own.id},
            ], usage={}, last_event_seq=0, created_at=now, updated_at=now, revision=1, etag='"run"',
        ))
        db.add(AgentAttemptRow(
            id="agent-attempt-cancel", agent_run_id=run_id, attempt=1, status="RUNNING",
            created_at=now,
        ))

    service = AssistantService(
        SQLAlchemyAssistantRepository(session_factory=factory),
        task_repository=task_repo,
    )
    canceled = service.cancel_run(ctx(), run_id, idempotency_key="cancel-own-job", expected_etag='"run"')

    assert canceled.status == "CANCEL_REQUESTED"
    with factory() as db:
        own_job = db.get(Job, "job-assistant-run")
        unrelated_job = db.get(Job, "job-unrelated-prediction")
        run = db.get(AgentRunRow, run_id)
        attempt = db.scalar(select(AgentAttemptRow).where(AgentAttemptRow.agent_run_id == run_id))
        assert own_job.status == "CANCEL_REQUESTED"
        assert unrelated_job.status == "RUNNING"
        assert run.cancel_requested is True
        assert run.status == attempt.status == "CANCEL_REQUESTED"
    engine.dispose()


def test_cancel_lock_refreshes_cached_run_after_worker_finishes() -> None:
    engine = create_engine("sqlite://")
    @event.listens_for(engine, "connect")
    def _sqlite_char_length(connection, _record):
        connection.create_function("CHAR_LENGTH", 1, lambda value: len(value) if value is not None else None)
    PlatformBase.metadata.create_all(engine)
    AssistantBase.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    run_id = "run-cancel-refresh"
    with factory.begin() as db:
        db.add(Project(id="p", slug="p", name="Project"))
        db.add(ConversationRow(
            id="conversation-refresh", project_id="p", owner_id="a", title="Refresh",
            mode="business", status="ACTIVE", revision=1, etag='"conversation"',
            created_at=now, updated_at=now,
        ))
        db.add(AgentRunRow(
            id=run_id, conversation_id="conversation-refresh", project_id="p",
            input_message_id="user-message", mode="business", status="RUNNING", attempt=1,
            model_id="model", prompt_version="prompt", toolset_version="tools", policy_version="policy",
            pending_actions=[], related_resources=[], usage={}, last_event_seq=0,
            created_at=now, updated_at=now, revision=1, etag='"running"',
        ))
    repository = SQLAlchemyAssistantRepository(session_factory=factory)
    with factory() as cached_session:
        assert repository.get_run(cached_session, run_id).status == "RUNNING"
        cached_session.commit()
        with factory.begin() as worker_session:
            completed = worker_session.get(AgentRunRow, run_id)
            completed.status = "SUCCEEDED"
            completed.revision = 2
            completed.etag = '"completed"'
        with cached_session.begin():
            locked = repository.lock_run_for_cancel(cached_session, run_id)
        assert locked.status == "SUCCEEDED"
        assert locked.etag == '"completed"'
    engine.dispose()


def test_waiting_cancel_partial_message_and_pending_bindings_roll_back_together() -> None:
    engine = create_engine("sqlite://")
    @event.listens_for(engine, "connect")
    def _sqlite_char_length(connection, _record):
        connection.create_function("CHAR_LENGTH", 1, lambda value: len(value) if value is not None else None)
    PlatformBase.metadata.create_all(engine)
    AssistantBase.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    run_id = "run-waiting-cancel-rollback"
    pending = PendingConfirmationBinding(
        tool_call_id="call-rollback", confirmation_id="confirm-rollback",
        resource_ref=ResourceRef(type="prediction", id="prediction-draft"),
        action="prediction.create", revision=1, config_hash="b" * 64,
        schema_version="1.0", normalized_args={"smiles": "C"},
    )
    with factory.begin() as db:
        db.add(Project(id="p", slug="p", name="Project"))
        db.add(ConversationRow(
            id="conversation-waiting-rollback", project_id="p", owner_id="a", title="Waiting",
            mode="business", status="ACTIVE", revision=1, etag='"conversation"',
            created_at=now, updated_at=now,
        ))
        db.add(AgentRunRow(
            id=run_id, conversation_id="conversation-waiting-rollback", project_id="p",
            input_message_id="user-message", mode="business", status="WAITING_CONFIRMATION", attempt=1,
            model_id="model", prompt_version="prompt", toolset_version="tools", policy_version="policy",
            pending_actions=[pending.model_dump(mode="json")], related_resources=[], usage={}, last_event_seq=0,
            created_at=now, updated_at=now, revision=1, etag='"waiting"',
        ))
        db.add(AgentAttemptRow(
            id="agent-attempt-waiting-rollback", agent_run_id=run_id, attempt=1,
            status="WAITING_CONFIRMATION", created_at=now,
        ))
        db.add(MessageRow(
            id="assistant-partial-rollback", conversation_id="conversation-waiting-rollback", seq=2,
            role="assistant", agent_run_id=run_id, status="PARTIAL",
            blocks=[{"type": "text", "text": "draft text"}], attachment_ids=[], created_at=now,
        ))
    repository = SQLAlchemyAssistantRepository(session_factory=factory)
    original_append_event = repository.append_event
    def fail_event(*args, **kwargs):
        raise RuntimeError("injected event failure")
    repository.append_event = fail_event
    service = AssistantService(repository)

    with pytest.raises(RuntimeError, match="injected event failure"):
        with factory.begin() as db:
            service.cancel_run(ctx(), run_id, idempotency_key="cancel-rollback", expected_etag='"waiting"', session=db)

    repository.append_event = original_append_event
    with factory() as db:
        run = db.get(AgentRunRow, run_id)
        message = db.get(MessageRow, "assistant-partial-rollback")
        assert run.status == "WAITING_CONFIRMATION"
        assert run.pending_actions == [pending.model_dump(mode="json")]
        assert message.status == "PARTIAL"
        assert message.blocks == [{"type": "text", "text": "draft text"}]
    engine.dispose()


def test_caller_session_uow_never_commits_or_closes() -> None:
    class Session:
        def __init__(self):
            self.commits = 0
            self.closes = 0

        @contextmanager
        def begin_nested(self):
            yield self

        def commit(self):
            self.commits += 1

        def close(self):
            self.closes += 1

    session = Session()
    with SessionAssistantUnitOfWork(session).transaction() as current:
        assert current is session
    assert session.commits == 0
    assert session.closes == 0
