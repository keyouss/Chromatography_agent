from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from types import SimpleNamespace

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from chrom_platform.assembly.m4 import _BoundTraceStore, _FreshTraceStore
from chrom_platform.modules.assistant.events import MySQLAgentEventStore
from chrom_platform.infrastructure.models import Base as PlatformBase, Project
from chrom_platform.modules.assistant.persistence import AgentAttemptRow, AgentEventRow, AgentRunRow, AssistantBase, ConversationRow, SQLAlchemyAssistantRepository, ToolCallRow
from chrom_platform.modules.assistant.events import InMemoryAgentEventStore
from chrom_platform.modules.assistant.errors import AssistantError
from chrom_platform.modules.assistant.runtime import AgentRuntime, RuntimeConfigSnapshot, _namespaced_tool_call_id, _tool_result_summary
from chrom_platform.modules.assistant.worker import AgentWorker, AgentWorkerMessage
from chrom_platform.modules.assistant.trace import ContextInspector, InMemoryTraceStore
from chrom_platform.modules.assistant.tools import BoundTool, ToolPending, ToolSpec
from chrom_platform.core.dto import ResourceRef
from chrom_platform.core.errors import APIError
from chrom_platform.modules.assistant.priority import PredictionRecordSummary


class _Provider:
    def __init__(self, responses):
        self.responses = iter(responses)

    def complete(self, **_kwargs):
        return next(self.responses)


class _Service:
    def validate(self, _ctx, **_kwargs):
        return None

    def execute(self, _ctx, **kwargs):
        return {"resource_ref": {"type": "prediction", "id": "pred-safe"}, "job_id": "job-safe", "secret": "do-not-persist", "value": kwargs["arguments"].get("value")}


def _runtime(responses, store=None, events=None, toolset=None):
    store = store or InMemoryTraceStore()
    return AgentRuntime(
        _Provider(responses),
        RuntimeConfigSnapshot("https://api.openai.com/v1", "model", "KEY_REF", "rev"),
        history=[{"role": "user", "content": "synthetic"}],
        toolset=toolset or {"read": BoundTool(ToolSpec("read", "read", permission="prediction:read", argument_schema={"type": "object"}), _Service())},
        context_inspector=ContextInspector(store),
        event_store=events,
        max_retries=0,
    ), store


def _ctx():
    return SimpleNamespace(project_id="priority-local", actor_id="actor-a", agent_attempt_id="attempt-a", execution_token="exec-a", fencing_token=3)


def _call_id(provider_id: str, run_id: str, ctx=None) -> str:
    return _namespaced_tool_call_id(run_id, 1, ctx or _ctx(), provider_id)


def test_runtime_persists_tool_lifecycle_with_event_ids_and_structured_metadata_summary():
    events = InMemoryAgentEventStore()
    runtime, store = _runtime([
        {"tool_calls": [{"id": "call-read", "name": "read", "arguments": {"value": "private-value"}}]},
        {"text": "done"},
        ], events=events)

    result = runtime.legacy_run_for_tests(run_id="run-tools", ctx=_ctx())

    assert result.status == "SUCCEEDED"
    call = store.get_tool_call(_call_id("call-read", "run-tools"))
    assert call is not None and call.status == "SUCCEEDED"
    assert call.agent_attempt_id == "attempt-a"
    assert call.arguments_summary["metadata_only"] is True
    assert "private-value" not in str(call.arguments_summary)
    assert call.result_summary["metadata_only"] is True
    assert call.result_summary["count"] == 3
    assert len(call.result_summary["result_hash"]) == 64
    assert call.result_summary["resource_ref"] == {"type": "prediction", "id": "pred-safe"}
    assert call.result_summary["job_id"] == "job-safe"
    assert call.resource_ref.id == "pred-safe"
    assert call.job_id == "job-safe"
    lifecycle = [e for e in events.list_after("agent_run", "run-tools", 0) if e.type.startswith("tool.")]
    assert [e.type for e in lifecycle] == ["tool.proposed", "tool.started", "tool.completed"]
    assert {e.data["tool_call_id"] for e in lifecycle} == {call.id}


def test_runtime_persists_failed_tool_without_ghost_running_row():
    class FailingService(_Service):
        def execute(self, _ctx, **_kwargs):
            raise RuntimeError("secret provider detail")

    store = InMemoryTraceStore()
    events = InMemoryAgentEventStore()
    toolset = {"read": BoundTool(ToolSpec("read", "read", argument_schema={"type": "object"}), FailingService())}
    runtime, store = _runtime([{"tool_calls": [{"id": "call-fail", "name": "read", "arguments": {}}]}], store=store, events=events, toolset=toolset)

    result = runtime.legacy_run_for_tests(run_id="run-fail", ctx=_ctx())

    assert result.status == "FAILED"
    call = store.get_tool_call(_call_id("call-fail", "run-fail"))
    assert call is not None and call.status == "FAILED"
    assert call.error["code"] == "TOOL_EXECUTION_FAILED"
    assert "secret provider detail" not in str(call.error)
    assert [e.type for e in events.list_after("agent_run", "run-fail", 0) if e.type.startswith("tool.")] == ["tool.proposed", "tool.started", "tool.failed"]


def test_tool_result_summary_keeps_allowlisted_status_error_schema_metadata_only():
    summary = _tool_result_summary({
        "resource_ref": {"type": "prediction", "id": "p1"},
        "job_id": "j1",
        "status": "FAILED",
        "error": {"code": "MODEL_UNAVAILABLE", "message": "safe"},
        "schema_version": "prediction.v1",
        "secret": "must-not-persist",
    })
    assert summary["metadata_only"] is True
    assert summary["resource_ref"] == {"type": "prediction", "id": "p1"}
    assert summary["job_id"] == "j1"
    assert summary["status"] == "FAILED"
    assert summary["error"]["code"] == "MODEL_UNAVAILABLE"
    assert summary["schema_version"] == "prediction.v1"
    assert "secret" not in str(summary)


def test_bound_tool_preserves_formal_application_error_code():
    class Service:
        def validate(self, _ctx, **_kwargs):
            return None

        def execute(self, _ctx, **_kwargs):
            raise APIError(503, "MODEL_UNAVAILABLE", "预测模型不可用", retryable=True)

    bound = BoundTool(ToolSpec("create_prediction", "create", argument_schema={"type": "object"}), Service())
    try:
        bound(_ctx(), {})
    except AssistantError as exc:
        assert exc.status_code == 503
        assert exc.payload.code == "MODEL_UNAVAILABLE"
        assert exc.payload.retryable is True
    else:
        raise AssertionError("formal application error was wrapped as TOOL_EXECUTION_FAILED")


def test_runtime_rejects_top_level_error_result_but_accepts_error_status_summary():
    class ErrorService(_Service):
        def execute(self, _ctx, **_kwargs):
            return {"error": {"code": "INTERNAL_ERROR", "message": "controlled failure", "retryable": False}}

    runtime, store = _runtime([{"tool_calls": [{"id": "call-error-result", "name": "read", "arguments": {}}]}], toolset={"read": BoundTool(ToolSpec("read", "read", argument_schema={"type": "object"}), ErrorService())})
    result = runtime.legacy_run_for_tests(run_id="run-error-result", ctx=_ctx())
    assert result.status == "FAILED"
    assert result.error["code"] == "INTERNAL_ERROR"
    assert store.get_tool_call(_call_id("call-error-result", "run-error-result")).status == "FAILED"

    class SummaryService(_Service):
        def execute(self, _ctx, **_kwargs):
            return PredictionRecordSummary(
                project_id="priority-local", count=1,
                status_counts={"INTERNAL_ERROR": 1},
                source={"source_type": "prediction_record", "source_ids": ["record-1"], "version": "1"},
            )

    runtime, store = _runtime(
        [{"tool_calls": [{"id": "call-summary", "name": "read", "arguments": {}}]}, {"text": "summary read"}],
        toolset={"read": BoundTool(ToolSpec("read", "read", argument_schema={"type": "object"}), SummaryService())},
    )
    result = runtime.legacy_run_for_tests(run_id="run-summary", ctx=_ctx())
    assert result.status == "SUCCEEDED"
    assert store.get_tool_call(_call_id("call-summary", "run-summary")).status == "SUCCEEDED"


def test_runtime_persists_four_tool_sequence_and_reuses_provider_ids():
    calls = [{"tool_calls": [{"id": f"call-{index}", "name": "read", "arguments": {"value": index}}]} for index in range(4)]
    calls.append({"text": "done"})
    runtime, store = _runtime(calls)

    result = runtime.legacy_run_for_tests(run_id="run-four", ctx=_ctx())

    assert result.status == "SUCCEEDED" and result.tool_calls == 4
    rows = store.list_tool_calls(run_id="run-four")
    assert {row.id for row in rows} == {_call_id(f"call-{index}", "run-four") for index in range(4)}
    assert all(row.status == "SUCCEEDED" for row in rows)


def test_tool_trace_write_failure_fails_run_and_does_not_leave_running_row():
    class BrokenStore(InMemoryTraceStore):
        def save_tool_call(self, call, **kwargs):
            if call.status == "RUNNING":
                raise RuntimeError("trace unavailable")
            return super().save_tool_call(call, **kwargs)

    store = BrokenStore()
    runtime, store = _runtime([{"tool_calls": [{"id": "call-trace", "name": "read", "arguments": {}}]}], store=store)

    result = runtime.legacy_run_for_tests(run_id="run-trace-tool", ctx=_ctx())

    assert result.status == "FAILED" and result.error["code"] == "TRACE_PERSISTENCE_FAILED"
    assert store.get_tool_call(_call_id("call-trace", "run-trace-tool")).status == "FAILED"


def test_tool_trace_is_idempotent_and_fenced_by_run_attempt():
    store = InMemoryTraceStore()
    runtime, store = _runtime([{"text": "done"}], store=store)
    call = runtime._new_tool_call("run-idempotent", 1, "call-one", "read", {"value": 1}, _ctx())
    store.save_tool_call(call)
    store.save_tool_call(call.model_copy(update={"status": "RUNNING"}))
    store.save_tool_call(call.model_copy(update={"status": "SUCCEEDED"}))
    store.save_tool_call(call.model_copy(update={"status": "PROPOSED"}))
    assert store.get_tool_call("call-one").status == "SUCCEEDED"
    try:
        store.save_tool_call(call.model_copy(update={"agent_attempt_id": "attempt-other"}))
    except Exception as exc:
        assert getattr(exc, "payload", None).code == "FENCE_REJECTED"
    else:
        raise AssertionError("attempt fence was not enforced")


def test_tool_trace_rejects_cross_terminal_statuses_but_allows_same_terminal_retry():
    store = InMemoryTraceStore()
    runtime, _ = _runtime([{"text": "done"}], store=store)
    call = runtime._new_tool_call("run-terminal", 1, "call-terminal", "read", {}, _ctx())
    store.save_tool_call(call.model_copy(update={"status": "SUCCEEDED"}))
    store.save_tool_call(call.model_copy(update={"status": "SUCCEEDED"}))
    try:
        store.save_tool_call(call.model_copy(update={"status": "FAILED"}))
    except Exception as exc:
        assert getattr(exc, "payload", None).code == "STATE_CONFLICT"
    else:
        raise AssertionError("cross-terminal overwrite was accepted")


def test_runtime_waiting_confirmation_returns_bound_pending_action():
    class PendingTool:
        spec = ToolSpec("read", "read", argument_schema={"type": "object"})

        def __call__(self, _ctx, _args):
            return ToolPending("read", "read.confirm", {}, "a" * 64, resource_ref=ResourceRef(type="prediction", id="pred-pending"), revision=2, config_hash="b" * 64)

    runtime, store = _runtime([{"tool_calls": [{"id": "call-pending", "name": "read", "arguments": {}}]}], toolset={"read": PendingTool()})
    result = runtime.legacy_run_for_tests(run_id="run-pending", ctx=_ctx())

    assert result.status == "WAITING_CONFIRMATION"
    assert len(result.pending_actions) == 1
    pending = result.pending_actions[0]
    pending_id = _call_id("call-pending", "run-pending")
    assert pending["tool_call_id"] == pending_id
    assert pending["confirmation_id"] == store.get_tool_call(pending_id).confirmation_id
    assert pending["revision"] == 2
    assert store.get_tool_call(pending_id).status == "WAITING_CONFIRMATION"


def test_stale_trace_fence_rejects_before_any_tool_event():
    class StaleStore(InMemoryTraceStore):
        def save_tool_call(self, _call, **_kwargs):
            raise AssistantError(409, "STALE_FENCING_TOKEN", "工具调用租约已失效", retryable=True)

    events = InMemoryAgentEventStore()
    runtime, _ = _runtime([{"tool_calls": [{"id": "call-stale", "name": "read", "arguments": {}}]}], store=StaleStore(), events=events)
    result = runtime.legacy_run_for_tests(run_id="run-stale", ctx=_ctx())

    assert result.status == "FAILED" and result.error["code"] == "STALE_FENCING_TOKEN"
    assert [event for event in events.list_after("agent_run", "run-stale", 0) if event.type.startswith("tool.")] == []


def test_sync_worker_passes_pending_actions_to_fenced_finish():
    class Store:
        def __init__(self):
            self.finished = []

        def load_run(self, _run_id):
            return {"id": "run-wait", "status": "QUEUED", "attempt": 1, "dispatch_id": "dispatch-wait"}

        def claim(self, **_kwargs):
            return {"fencing_token": 4, "execution_token": "exec-wait"}

        def is_cancel_requested(self, _run_id):
            return False

        def finish(self, **kwargs):
            self.finished.append(kwargs)
            return {"accepted": True, "status": kwargs["status"]}

    pending = {"tool_call_id": "call-wait", "confirmation_id": "confirm-wait", "revision": 2}
    class WaitingRuntime:
        def run(self, **_kwargs):
            from chrom_platform.modules.assistant.runtime import RuntimeResult
            return RuntimeResult("WAITING_CONFIRMATION", pending_actions=(pending,))

    store = Store()
    result = AgentWorker(runtime=WaitingRuntime(), store=store).handle(AgentWorkerMessage("run-wait", 1, "dispatch-wait"))
    assert result.status == "WAITING_CONFIRMATION"
    assert store.finished[0]["result"].pending_actions == (pending,)


def test_runtime_persists_tool_call_through_sqlalchemy_trace_store():
    engine = create_engine("sqlite:///:memory:")
    @event.listens_for(engine, "connect")
    def _sqlite_mysql_functions(dbapi_connection, _connection_record):
        dbapi_connection.create_function("CHAR_LENGTH", 1, lambda value: len(value) if value is not None else None)
    PlatformBase.metadata.create_all(engine)
    AssistantBase.metadata.create_all(engine)
    with Session(engine) as session:
        now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        session.add(Project(id="project-sql", slug="project-sql", name="Project SQL", status="ACTIVE", revision=1))
        session.add(ConversationRow(id="conv-sql", project_id="project-sql", owner_id="actor-a", title="SQL", mode="knowledge", status="ACTIVE", revision=1, etag="e", created_at=now, updated_at=now))
        session.add(AgentRunRow(id="run-sql", conversation_id="conv-sql", project_id="project-sql", input_message_id="msg-sql", mode="knowledge", status="RUNNING", attempt=1, model_id="model", prompt_version="p", toolset_version="t", policy_version="policy", fencing_token=3, created_at=now, updated_at=now, revision=1, etag="e"))
        session.flush()
        session.add(AgentAttemptRow(id="attempt-sql", agent_run_id="run-sql", attempt=1, status="RUNNING", execution_token="exec-sql", fencing_token=3, created_at=now))
        session.flush()
        inspector = ContextInspector(_BoundTraceStore(SQLAlchemyAssistantRepository(session), session))
        runtime, _ = _runtime([{"tool_calls": [{"id": "call-sql", "name": "read", "arguments": {"value": "private"}}]}, {"text": "done"}], store=InMemoryTraceStore())
        runtime.context_inspector = inspector
        result = runtime.legacy_run_for_tests(run_id="run-sql", ctx=SimpleNamespace(project_id="project-sql", agent_attempt_id="attempt-sql", execution_token="exec-sql", fencing_token=3))
        sql_call_id = _call_id("call-sql", "run-sql", SimpleNamespace(agent_attempt_id="attempt-sql"))
        row = session.scalar(select(ToolCallRow).where(ToolCallRow.id == sql_call_id))
        assert result.status == "SUCCEEDED"
        assert row is not None and row.status == "SUCCEEDED" and row.agent_attempt_id == "attempt-sql"
        assert "private" not in str(row.arguments_summary)
        events = list(session.scalars(select(AgentEventRow).where(AgentEventRow.agent_run_id == "run-sql").order_by(AgentEventRow.seq)))
        assert [event.type for event in events] == ["tool.proposed", "tool.started", "tool.completed"]
        assert {event.data["tool_call_id"] for event in events} == {sql_call_id}
        stored = inspector.get_tool_call(sql_call_id)
        inspector.record_tool_call_event(stored, "tool.completed", {"tool_call_id": sql_call_id, "status": "SUCCEEDED"}, execution_token="exec-sql", fencing_token=3)
        assert len(list(session.scalars(select(AgentEventRow).where(AgentEventRow.agent_run_id == "run-sql")))) == 3
        stale = runtime._new_tool_call("run-sql", 1, "call-stale-sql", "read", {}, SimpleNamespace(project_id="project-sql", agent_attempt_id="attempt-sql"))
        try:
            inspector.save_tool_call(stale, execution_token="wrong", fencing_token=3)
        except Exception as exc:
            assert getattr(exc, "payload", None).code == "STALE_FENCING_TOKEN"
        else:
            raise AssertionError("stale execution token was accepted")


def test_sql_trace_tool_event_failure_rolls_back_tool_row(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    @event.listens_for(engine, "connect")
    def _sqlite_mysql_functions(dbapi_connection, _connection_record):
        dbapi_connection.create_function("CHAR_LENGTH", 1, lambda value: len(value) if value is not None else None)
    PlatformBase.metadata.create_all(engine)
    AssistantBase.metadata.create_all(engine)
    factory = lambda: Session(engine)
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    with factory() as session:
        session.add(Project(id="project-rollback", slug="project-rollback", name="Project Rollback", status="ACTIVE", revision=1))
        session.add(ConversationRow(id="conv-rollback", project_id="project-rollback", owner_id="actor-a", title="Rollback", mode="knowledge", status="ACTIVE", revision=1, etag="e", created_at=now, updated_at=now))
        session.add(AgentRunRow(id="run-rollback", conversation_id="conv-rollback", project_id="project-rollback", input_message_id="msg-rollback", mode="knowledge", status="RUNNING", attempt=1, model_id="model", prompt_version="p", toolset_version="t", policy_version="policy", fencing_token=3, created_at=now, updated_at=now, revision=1, etag="e"))
        session.add(AgentAttemptRow(id="attempt-rollback", agent_run_id="run-rollback", attempt=1, status="RUNNING", execution_token="exec-rollback", fencing_token=3, created_at=now))
        session.commit()
    original_append = MySQLAgentEventStore.append
    def fail_append(self, *args, **kwargs):
        raise RuntimeError("event unavailable")
    monkeypatch.setattr(MySQLAgentEventStore, "append", fail_append)
    with factory() as session:
        inspector = ContextInspector(_FreshTraceStore(factory))
        runtime, _ = _runtime([{"tool_calls": [{"id": "call-rollback", "name": "read", "arguments": {}}]}], store=InMemoryTraceStore())
        runtime.context_inspector = inspector
        result = runtime.legacy_run_for_tests(run_id="run-rollback", ctx=SimpleNamespace(project_id="project-rollback", agent_attempt_id="attempt-rollback", execution_token="exec-rollback", fencing_token=3))
        assert result.status == "FAILED"
    with factory() as session:
        assert session.scalar(select(ToolCallRow).where(ToolCallRow.id == "call-rollback")) is None
        assert list(session.scalars(select(AgentEventRow).where(AgentEventRow.agent_run_id == "run-rollback"))) == []
    monkeypatch.setattr(MySQLAgentEventStore, "append", original_append)


def test_sql_trace_tool_row_failure_does_not_append_event(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    @event.listens_for(engine, "connect")
    def _sqlite_mysql_functions(dbapi_connection, _connection_record):
        dbapi_connection.create_function("CHAR_LENGTH", 1, lambda value: len(value) if value is not None else None)
    PlatformBase.metadata.create_all(engine)
    AssistantBase.metadata.create_all(engine)
    factory = lambda: Session(engine)
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    with factory() as session:
        session.add(Project(id="project-row-failure", slug="project-row-failure", name="Project Row Failure", status="ACTIVE", revision=1))
        session.add(ConversationRow(id="conv-row-failure", project_id="project-row-failure", owner_id="actor-a", title="Row Failure", mode="knowledge", status="ACTIVE", revision=1, etag="e", created_at=now, updated_at=now))
        session.add(AgentRunRow(id="run-row-failure", conversation_id="conv-row-failure", project_id="project-row-failure", input_message_id="msg-row-failure", mode="knowledge", status="RUNNING", attempt=1, model_id="model", prompt_version="p", toolset_version="t", policy_version="policy", fencing_token=3, created_at=now, updated_at=now, revision=1, etag="e"))
        session.add(AgentAttemptRow(id="attempt-row-failure", agent_run_id="run-row-failure", attempt=1, status="RUNNING", execution_token="exec-row-failure", fencing_token=3, created_at=now))
        session.commit()
    def fail_save(self, _session, _call):
        raise RuntimeError("tool row unavailable")
    monkeypatch.setattr(SQLAlchemyAssistantRepository, "save_tool_call", fail_save)
    inspector = ContextInspector(_FreshTraceStore(factory))
    runtime, _ = _runtime([{"tool_calls": [{"id": "call-row-failure", "name": "read", "arguments": {}}]}], store=InMemoryTraceStore())
    runtime.context_inspector = inspector
    result = runtime.legacy_run_for_tests(run_id="run-row-failure", ctx=SimpleNamespace(project_id="project-row-failure", agent_attempt_id="attempt-row-failure", execution_token="exec-row-failure", fencing_token=3))
    assert result.status == "FAILED"
    with factory() as session:
        assert session.scalar(select(ToolCallRow).where(ToolCallRow.id == "call-row-failure")) is None
        assert list(session.scalars(select(AgentEventRow).where(AgentEventRow.agent_run_id == "run-row-failure"))) == []


def test_sql_event_payload_is_json_safe_for_resource_ref_pending_binding_and_other_events():
    class Outcome(Enum):
        WAITING = "WAITING_CONFIRMATION"

    @dataclass(frozen=True)
    class PendingMeta:
        resource_ref: ResourceRef
        status: Outcome
        expires_at: datetime

    engine = create_engine("sqlite:///:memory:")
    @event.listens_for(engine, "connect")
    def _sqlite_mysql_functions(dbapi_connection, _connection_record):
        dbapi_connection.create_function("CHAR_LENGTH", 1, lambda value: len(value) if value is not None else None)
    PlatformBase.metadata.create_all(engine)
    AssistantBase.metadata.create_all(engine)
    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        session.add(Project(id="project-safe-event", slug="project-safe-event", name="Project Safe Event", status="ACTIVE", revision=1))
        session.add(ConversationRow(id="conv-safe-event", project_id="project-safe-event", owner_id="actor-a", title="Safe Event", mode="knowledge", status="ACTIVE", revision=1, etag="e", created_at=now, updated_at=now))
        session.add(AgentRunRow(id="run-safe-event", conversation_id="conv-safe-event", project_id="project-safe-event", input_message_id="msg-safe-event", mode="knowledge", status="RUNNING", attempt=1, model_id="model", prompt_version="p", toolset_version="t", policy_version="policy", fencing_token=3, created_at=now, updated_at=now, revision=1, etag="e"))
        session.flush()
        session.add(AgentAttemptRow(id="attempt-safe-event", agent_run_id="run-safe-event", attempt=1, status="RUNNING", execution_token="exec-safe-event", fencing_token=3, created_at=now))
        session.flush()
        payload = {
            "resource_ref": ResourceRef(type="prediction", id="pred-safe-event"),
            "pending": PendingMeta(ResourceRef(type="job", id="job-safe-event"), Outcome.WAITING, now),
            "api_key": "should-not-persist",
            "provider_token": "should-not-persist",
            "business_provider": "internal-model",
        }
        event_store = MySQLAgentEventStore(session, AgentEventRow)
        event_store.append("agent_run", "run-safe-event", "confirmation.required", payload, agent_run_id="run-safe-event")
        event_store.append("agent_run", "run-safe-event", "tool.completed", {"resource_ref": ResourceRef(type="prediction", id="pred-safe-event"), "category": "business"}, agent_run_id="run-safe-event")
        session.flush()
        rows = list(session.scalars(select(AgentEventRow).where(AgentEventRow.agent_run_id == "run-safe-event").order_by(AgentEventRow.seq)))
        assert len(rows) == 2
        assert rows[0].data["resource_ref"] == {"type": "prediction", "id": "pred-safe-event"}
        assert rows[0].data["pending"]["status"] == "WAITING_CONFIRMATION"
        assert rows[0].data["pending"]["expires_at"].endswith("+00:00")
        assert rows[0].data["api_key"] == "[REDACTED]"
        assert rows[0].data["provider_token"] == "[REDACTED]"
        assert rows[0].data["business_provider"] == "internal-model"
        assert rows[1].data["category"] == "business"
