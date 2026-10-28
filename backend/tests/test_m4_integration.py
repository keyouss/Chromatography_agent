from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from chrom_platform.core.dto import ActorContext, ResourceRef
from chrom_platform.core.errors import APIError
from chrom_platform.modules.assistant.errors import AssistantError, public_error, public_event_data
from chrom_platform.modules.assistant.events import AgentEvent, InMemoryAgentEventStore
from chrom_platform.modules.assistant.persistence import InMemoryAssistantRepository
from chrom_platform.modules.assistant.persistence import (
    AgentAttemptRow,
    AgentRunRow,
    AssistantBase,
    ConversationRow,
    ContextSnapshotRow,
    LlmCallRow,
    SQLAlchemyAssistantRepository,
    _run,
    _tool,
)
from chrom_platform.infrastructure.models import Base as PlatformBase, Project
from chrom_platform.modules.assistant.router import create_router
from chrom_platform.modules.assistant.schemas import (
    ConfirmationResume,
    ConversationCreate,
    ContextDiff,
    ContextSnapshotContent,
    ContextSnapshotMetadata,
    ContextSnapshotView,
    EventPage,
    LlmCallDetail,
    LlmCallSummary,
    RecoveryResume,
    ResumeAgentRunPayload,
    ToolCallDraft,
    UserMessageCreate,
)
from chrom_platform.modules.assistant.service import AssistantService
from chrom_platform.modules.assistant.runtime import AgentRuntime, RuntimeConfigSnapshot
from chrom_platform.modules.assistant.tools import ToolExecutionLedger
from chrom_platform.modules.assistant.sse import encode_sse, stream_events
from chrom_platform.modules.assistant.trace import ContextInspector, InMemoryTraceStore, LlmCallDetail as TraceCall, Usage
from chrom_platform.task_core.service import OperationContext


def _ctx(*permissions: str) -> OperationContext:
    return OperationContext(
        ActorContext(
            actor_id="actor-a",
            tenant_id=None,
            project_ids=["project-a"],
            auth_method="deployment_default",
            permissions=list(permissions or ("*",)),
            policy_version="policy-1",
        ),
        interaction="human",
    )


def _app(service: AssistantService, *, trace: ContextInspector | None = None, events: InMemoryAgentEventStore | None = None) -> TestClient:
    app = FastAPI()
    app.state.actor_context = _ctx("*").actor
    app.state.assistant_service = service
    if trace is not None:
        app.state.assistant_trace_inspector = trace
    if events is not None:
        app.state.assistant_event_store = events
    app.add_exception_handler(APIError, lambda request, exc: JSONResponse(status_code=exc.status_code, content={"error": exc.payload.model_dump(mode="json")}))
    app.include_router(create_router(trace_enabled=trace is not None), prefix="/api/v1")
    return TestClient(app)


class _BusinessPort:
    def __init__(self) -> None:
        self.executed: list[dict] = []

    def validate(self, _ctx, **_kwargs):
        return None

    def execute(self, _ctx, **kwargs):
        self.executed.append(kwargs)
        return {"job_id": "job-training-1", "status": "QUEUED", "secret": "should-not-leak"}


def test_m4_openapi_has_21_typed_operations_and_resume_discriminator() -> None:
    app = FastAPI()
    app.include_router(create_router(trace_enabled=True), prefix="/api/v1")
    spec = app.openapi()
    paths = spec["paths"]
    expected = {
        ("GET", "/api/v1/conversations"),
        ("POST", "/api/v1/conversations"),
        ("GET", "/api/v1/conversations/{conversation_id}"),
        ("PATCH", "/api/v1/conversations/{conversation_id}"),
        ("POST", "/api/v1/conversations/{conversation_id}/archive"),
        ("GET", "/api/v1/conversations/{conversation_id}/messages"),
        ("POST", "/api/v1/conversations/{conversation_id}/messages"),
        ("GET", "/api/v1/conversations/{conversation_id}/runs"),
        ("GET", "/api/v1/conversations/{conversation_id}/tool-calls"),
        ("GET", "/api/v1/agent-runs/{run_id}"),
        ("POST", "/api/v1/agent-runs/{run_id}/cancel"),
        ("POST", "/api/v1/agent-runs/{run_id}/resume"),
        ("GET", "/api/v1/agent-runs/{run_id}/events"),
        ("GET", "/api/v1/agent-runs/{run_id}/events/stream"),
        ("GET", "/api/v1/agent-runs/{run_id}/tool-calls"),
        ("GET", "/api/v1/agent-runs/{run_id}/llm-calls"),
        ("GET", "/api/v1/llm-calls/{call_id}"),
        ("GET", "/api/v1/llm-calls/{call_id}/context"),
        ("GET", "/api/v1/context-snapshots/{snapshot_id}"),
        ("GET", "/api/v1/context-snapshots/{snapshot_id}/content"),
        ("POST", "/api/v1/context-snapshots/compare"),
    }
    assert {(method.upper(), path) for path, item in paths.items() for method in item if method in {"get", "post", "patch"}} >= expected
    resume_schema = paths["/api/v1/agent-runs/{run_id}/resume"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    assert resume_schema["discriminator"]["propertyName"] == "reason"
    assert len(resume_schema["oneOf"]) == 2
    for path in ("/api/v1/agent-runs/{run_id}/llm-calls", "/api/v1/conversations"):
        schema = paths[path]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        assert "ListEnvelope" in schema["$ref"]
    for path in (
        "/api/v1/llm-calls/{call_id}",
        "/api/v1/llm-calls/{call_id}/context",
        "/api/v1/context-snapshots/{snapshot_id}",
        "/api/v1/context-snapshots/{snapshot_id}/content",
        "/api/v1/context-snapshots/compare",
    ):
        assert "dict" not in str(paths[path][next(iter(paths[path]))]["responses"]["200"])


def test_cursor_page_filter_sort_and_create_replay() -> None:
    service = AssistantService(InMemoryAssistantRepository())
    ctx = _ctx("*")
    created = [service.create_conversation(ctx, __import__("chrom_platform.modules.assistant.schemas", fromlist=["ConversationCreate"]).ConversationCreate(project_id="project-a", mode="knowledge"), idempotency_key=f"create-{i}") for i in range(3)]
    replay = service.create_conversation(ctx, __import__("chrom_platform.modules.assistant.schemas", fromlist=["ConversationCreate"]).ConversationCreate(project_id="project-a", mode="knowledge"), idempotency_key="create-0")
    assert replay.id == created[0].id
    page1 = service.list_conversations(ctx, project_id="project-a", limit=2)
    from chrom_platform.modules.assistant.service import encode_cursor
    cursor = encode_cursor("conversations", (page1[-1].updated_at.isoformat(), page1[-1].id), filters={"project_id": "project-a", "q": None, "archived": None})
    page2 = service.list_conversations(ctx, project_id="project-a", limit=2, cursor=cursor)
    assert len(page1) == 2 and len(page2) == 1
    assert not {x.id for x in page1}.intersection(x.id for x in page2)
    with pytest.raises(AssistantError) as invalid:
        service.list_conversations(ctx, project_id="other", cursor=cursor)
    assert invalid.value.payload.code == "INVALID_CURSOR"


def test_client_message_replay_variant_and_busy() -> None:
    service = AssistantService(InMemoryAssistantRepository())
    ctx = _ctx("*")
    from chrom_platform.modules.assistant.schemas import ConversationCreate
    conv = service.create_conversation(ctx, ConversationCreate(project_id="project-a", mode="business"), idempotency_key="c")
    first = service.send_message(ctx, conv.id, UserMessageCreate(content="one", client_message_id="m"), idempotency_key="m")
    assert service.send_message(ctx, conv.id, UserMessageCreate(content="one", client_message_id="m"), idempotency_key="different").message_id == first.message_id
    with pytest.raises(AssistantError) as variant:
        service.send_message(ctx, conv.id, UserMessageCreate(content="two", client_message_id="m"), idempotency_key="variant")
    assert variant.value.payload.code == "IDEMPOTENCY_KEY_REUSED"
    with pytest.raises(AssistantError) as busy:
        service.send_message(ctx, conv.id, UserMessageCreate(content="three", client_message_id="m2"), idempotency_key="m2")
    assert busy.value.payload.code == "CONVERSATION_BUSY"


def test_owned_conversation_messages_and_run_refresh_use_one_scope() -> None:
    service = AssistantService(InMemoryAssistantRepository())
    client = _app(service)
    created = client.post(
        "/api/v1/conversations",
        headers={"Idempotency-Key": "scope-conversation"},
        json={"project_id": "project-a", "mode": "business"},
    )
    assert created.status_code == 201
    conversation_id = created.json()["data"]["id"]
    assert client.get(f"/api/v1/conversations/{conversation_id}").status_code == 200
    accepted = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers={"Idempotency-Key": "scope-message"},
        json={"content": "refresh this conversation", "client_message_id": "scope-message"},
    )
    assert accepted.status_code == 202
    run_id = accepted.json()["data"]["agent_run_id"]
    assert client.get(f"/api/v1/conversations/{conversation_id}/messages").status_code == 200
    assert client.get(f"/api/v1/conversations/{conversation_id}/runs").status_code == 200
    assert client.get(f"/api/v1/agent-runs/{run_id}").status_code == 200
    assert client.get(f"/api/v1/conversations/{conversation_id}").status_code == 200


def test_conversation_and_run_are_hidden_from_another_actor() -> None:
    service = AssistantService(InMemoryAssistantRepository())
    owner = _ctx("*")
    from chrom_platform.modules.assistant.schemas import ConversationCreate
    conversation = service.create_conversation(owner, ConversationCreate(project_id="project-a", mode="knowledge"), idempotency_key="owner-conversation")
    other = OperationContext(
        ActorContext(
            actor_id="actor-b",
            tenant_id=None,
            project_ids=["project-a"],
            auth_method="deployment_default",
            permissions=["*"],
            policy_version="policy-1",
        ),
        interaction="human",
    )
    with pytest.raises(AssistantError) as conversation_error:
        service.get_conversation(other, conversation.id)
    assert conversation_error.value.status_code == 404
    assert conversation_error.value.payload.code == "RESOURCE_NOT_FOUND"


def test_tool_call_public_projection_does_not_invent_success_error() -> None:
    from types import SimpleNamespace

    base = dict(
        id="tool-projection",
        agent_run_id="run-projection",
        agent_attempt_id="attempt-projection",
        provider_tool_call_id="provider-tool",
        tool_name="get_prediction_capabilities",
        tool_schema_version="1.0",
        arguments_hash=None,
        binding_hash=None,
        arguments_summary={},
        resource_ref=None,
        confirmation_id=None,
        pending_confirmation=None,
        result_summary={"metadata_only": True},
        job_id=None,
        started_at=None,
        finished_at=None,
    )
    succeeded = _tool(SimpleNamespace(**base, status="SUCCEEDED", error=None))
    assert succeeded.error is None
    failed = _tool(SimpleNamespace(**base, status="FAILED", error={"code": "VALIDATION_FAILED", "message": "bad input", "retryable": False}))
    assert failed.error is not None and failed.error.code == "VALIDATION_FAILED"
    waiting = _tool(SimpleNamespace(**base, status="WAITING_CONFIRMATION", error=None))
    assert waiting.error is None


def test_idempotency_key_replays_send_payload_independently_of_client_message_id() -> None:
    service = AssistantService(InMemoryAssistantRepository())
    ctx = _ctx("*")
    from chrom_platform.modules.assistant.schemas import ConversationCreate
    conv = service.create_conversation(ctx, ConversationCreate(project_id="project-a", mode="business"), idempotency_key="c")
    request = UserMessageCreate(content="same", client_message_id="client-a")
    first = service.send_message(ctx, conv.id, request, idempotency_key="operation-key")
    assert service.send_message(ctx, conv.id, request, idempotency_key="operation-key").message_id == first.message_id
    with pytest.raises(AssistantError) as changed:
        service.send_message(ctx, conv.id, request.model_copy(update={"content": "changed", "client_message_id": "client-b"}), idempotency_key="operation-key")
    assert changed.value.payload.code == "IDEMPOTENCY_KEY_REUSED"


def test_idempotency_ledger_is_shared_by_service_adapters_on_one_repository() -> None:
    repo = InMemoryAssistantRepository()
    first_service = AssistantService(repo)
    second_service = AssistantService(repo)
    ctx = _ctx("*")
    from chrom_platform.modules.assistant.schemas import ConversationCreate
    conv = first_service.create_conversation(ctx, ConversationCreate(project_id="project-a", mode="business"), idempotency_key="c")
    request = UserMessageCreate(content="same", client_message_id="m")
    first = first_service.send_message(ctx, conv.id, request, idempotency_key="k")
    replay = second_service.send_message(ctx, conv.id, request, idempotency_key="k")
    assert replay.message_id == first.message_id


def test_cancel_resume_headers_location_and_recovery_union() -> None:
    repo = InMemoryAssistantRepository()
    service = AssistantService(repo)
    client = _app(service)
    created = client.post("/api/v1/conversations", headers={"Idempotency-Key": "c"}, json={"project_id": "project-a", "mode": "business"})
    conv = created.json()["data"]
    accepted = client.post(f"/api/v1/conversations/{conv['id']}/messages", headers={"Idempotency-Key": "m"}, json={"content": "run", "client_message_id": "m"})
    run_id = accepted.json()["data"]["agent_run_id"]
    run = repo.runs[run_id]
    response = client.post(
        f"/api/v1/agent-runs/{run_id}/cancel",
        headers={"Idempotency-Key": "cancel", "If-Match": run.etag},
        json={"reason": "stop"},
    )
    assert response.status_code == 202
    assert response.headers["Location"].endswith(run_id)
    assert response.json()["data"]["status"] == "CANCELED"
    assert client.post(f"/api/v1/agent-runs/{run_id}/cancel", headers={"Idempotency-Key": "cancel-2"}, json={}).status_code == 428
    assert TypeAdapter(ResumeAgentRunPayload).validate_python({"reason": "confirmation", "confirmation_id": "c1"}).reason == "confirmation"
    assert TypeAdapter(ResumeAgentRunPayload).validate_python({"reason": "recovery"}).reason == "recovery"
    with pytest.raises(ValidationError):
        TypeAdapter(ResumeAgentRunPayload).validate_python({"reason": "recovery", "confirmation_id": "c1"})


def test_forbidden_agent_tools_and_external_job_survives_cancel() -> None:
    port = _BusinessPort()
    service = AssistantService(InMemoryAssistantRepository(), business_services={"create_prediction": port})
    ctx = _ctx("*")
    from chrom_platform.modules.assistant.schemas import ConversationCreate
    conv = service.create_conversation(ctx, ConversationCreate(project_id="project-a", mode="business"), idempotency_key="c")
    accepted = service.send_message(ctx, conv.id, UserMessageCreate(content="run", client_message_id="m"), idempotency_key="m")
    for tool in ("approve", "deploy", "human-confirmation", "shell"):
        with pytest.raises(AssistantError) as denied:
            service.propose_tool_call(ctx, accepted.agent_run_id, ToolCallDraft(provider_tool_call_id=tool, tool_name=tool, schema_version="1"))
        assert denied.value.payload.code == "PERMISSION_DENIED"
    call = service.propose_tool_call(ctx, accepted.agent_run_id, ToolCallDraft(provider_tool_call_id="prediction", tool_name="create_prediction", schema_version="1"))
    assert call.job_id == "job-training-1"
    assert service.cancel_run(ctx, accepted.agent_run_id).status == "SUCCEEDED"
    assert port.executed and port.executed[0]["tool_name"] == "create_prediction"


def test_trace_dtos_permissions_usage_null_redaction_and_transport_pair() -> None:
    store = InMemoryTraceStore()
    audit: list[str] = []
    inspector = ContextInspector(store, mode="redacted", audit=lambda **kwargs: audit.append(kwargs["action"]))
    call = TraceCall(id="llm-1", agent_run_id="run-1", agent_attempt_id="attempt-1", request_index=0, transport_attempt=1, model_id="m", usage=Usage())
    outbound = {"model": "m", "messages": [{"role": "user", "content": "hello"}], "api_key": "secret", "thinking": "hidden"}
    logical, provider = inspector.capture_pair(llm_call=call, logical_context=outbound, provider_request=outbound, project_id="project-a")
    detail = inspector.llm_call(call.id, actor=_ctx("trace:read"))
    assert detail["usage"] == {"input_tokens": None, "output_tokens": None, "cached_input_tokens": None}
    assert inspector.content(provider.id, actor=_ctx("trace:content:read"))["payload"]["model"] == "m"
    rendered = str(inspector.content(provider.id, actor=_ctx("trace:content:read")))
    assert "api_key': 'secret" not in rendered and "thinking': 'hidden" not in rendered
    with pytest.raises(AssistantError) as denied:
        inspector.content(provider.id, actor=_ctx("trace:read"))
    assert denied.value.status_code == 403
    assert audit.count("trace.content.read") >= 1
    assert logical.llm_call_id == provider.llm_call_id == call.id


def test_public_error_and_trace_projection_drop_internal_provider_diagnostics() -> None:
    internal = {"code": "PROVIDER_REQUEST_REJECTED", "message": "拒绝", "retryable": False, "provider": {"category": "HTTP_4XX", "http_status": 400, "provider_error_code": "20015"}, "unknown": "drop"}
    assert public_error(internal) == {"code": "PROVIDER_REQUEST_REJECTED", "message": "拒绝", "retryable": False}
    assert public_error("legacy-corrupt-error") == {"code": "INTERNAL_ERROR", "message": "内部错误", "retryable": False}
    malformed = public_error({"code": 7, "message": object(), "retryable": "yes"})
    assert malformed == {"code": "INTERNAL_ERROR", "message": "内部错误", "retryable": False}
    assert public_event_data({"error": internal, "nested": {"provider": "drop", "ok": 1}}, event_type="llm.failed") == {"error": {"code": "PROVIDER_REQUEST_REJECTED", "message": "拒绝", "retryable": False}, "nested": {"ok": 1}}
    business = {"category": "prediction_result", "provider": "business-provider", "result": {"category": "keep", "provider": "keep"}}
    assert public_event_data(business, event_type="tool.completed") == business
    business_diagnostic = {"diagnostic": {"category": "business", "provider": "business-provider"}}
    assert public_event_data(business_diagnostic, event_type="tool.completed") == business_diagnostic
    assert public_event_data({"diagnostic": {"category": "HTTP_4XX", "provider_error_code": "20015", "code": "SAFE", "message": "诊断", "retryable": False}}, event_type="provider.diagnostic") == {"diagnostic": {"code": "SAFE", "message": "诊断", "retryable": False}}
    inspector = ContextInspector(InMemoryTraceStore(), mode="off")
    call = TraceCall(id="llm-public", agent_run_id="run-public", agent_attempt_id="attempt-1", request_index=1, transport_attempt=1, model_id="m", usage=Usage(), error=internal)
    inspector.save_llm_call(call)
    rendered = inspector.llm_call(call.id, actor=_ctx("trace:read"))
    assert rendered["error"] == {"code": "PROVIDER_REQUEST_REJECTED", "message": "拒绝", "retryable": False}
    sse = encode_sse(AgentEvent("agent_run", "run-public", 1, "llm.failed", {"error": internal}))
    assert '"provider"' not in sse and '"http_status"' not in sse and '"provider_error_code"' not in sse
    assert LlmCallDetail.model_fields["usage"].annotation is not object
    assert ContextSnapshotMetadata.model_config["extra"] == "forbid"
    assert ContextSnapshotContent.model_config["extra"] == "forbid"
    assert ContextSnapshotView.model_config["extra"] == "forbid"
    assert ContextDiff.model_config["extra"] == "forbid"


def test_trace_scope_checker_is_applied_from_llm_call_to_run() -> None:
    store = InMemoryTraceStore()
    seen: list[tuple[str, str]] = []
    inspector = ContextInspector(store, mode="redacted", scope_checker=lambda actor, run_id: seen.append((actor.actor.actor_id, run_id)))
    call = TraceCall(id="scope-call", agent_run_id="scope-run", agent_attempt_id="a", request_index=0, transport_attempt=1, model_id="m", usage=Usage())
    inspector.save_llm_call(call)
    inspector.llm_call(call.id, actor=_ctx("trace:read"))
    assert seen == [("actor-a", "scope-run")]


def test_event_page_sse_replay_410_and_json_serialization() -> None:
    now = datetime.now(timezone.utc)
    store = InMemoryAgentEventStore(clock=lambda: now, retention_days=1)
    event = store.append("agent_run", "run-1", "run.started", {"status": "RUNNING", "attempt": 1})
    page = {"events": [{**event.as_dict(), "timestamp": event.timestamp}], "next_seq": "1", "has_more": False, "stream_status": "OPEN"}
    assert EventPage.model_validate(page).model_dump(mode="json")["events"][0]["seq"] == "1"
    assert '"status":"RUNNING"' in encode_sse(event)
    old = replace(event, timestamp=now.replace(year=now.year - 1))
    store._streams[("agent_run", "run-1")] = [old]
    with pytest.raises(AssistantError) as expired:
        list(stream_events(store, "agent_run", "run-1", after_seq="0"))
    assert expired.value.status_code == 410


def test_polling_events_route_returns_410_for_expired_cursor() -> None:
    class RunLookup:
        def get_run(self, *_args):
            return object()

    now = datetime.now(timezone.utc)
    events = InMemoryAgentEventStore(clock=lambda: now, retention_days=1)
    event = events.append("agent_run", "run-expired", "run.started", {"status": "RUNNING", "attempt": 1})
    events._streams[("agent_run", "run-expired")] = [replace(event, timestamp=now.replace(year=now.year - 1))]
    client = _app(RunLookup(), events=events)
    response = client.get("/api/v1/agent-runs/run-expired/events?after_seq=0")
    assert response.status_code == 410


def test_runtime_history_object_trace_pair_and_public_event_types() -> None:
    class History:
        def load_history(self, run_id, *, attempt=1):
            assert run_id == "run-runtime" and attempt == 2
            return [{"role": "user", "content": "hello"}]

    class Provider:
        def __init__(self):
            self.outbound = []

        def complete(self, *, config, messages, tools, **_kwargs):
            self.outbound.append((config.public_dict(), messages, tools))
            return {"text": "done", "usage": {}}

    provider = Provider()
    trace = ContextInspector(InMemoryTraceStore(), mode="redacted")
    events = InMemoryAgentEventStore()
    runtime = AgentRuntime(
        provider,
            RuntimeConfigSnapshot("https://api.openai.com/v1", "model", "secret-ref", "r1"),
        history_loader=History(),
        context_inspector=trace,
        event_store=events,
        max_retries=0,
        project_id="project-a",
    )
    result = runtime.legacy_run_for_tests(run_id="run-runtime", attempt=2)
    assert result.status == "SUCCEEDED" and provider.outbound
    calls = list(trace.store.calls.values())
    assert len(calls) == 1
    assert trace.store.get_snapshot(calls[0].logical_context_snapshot_id).payload["messages"] == provider.outbound[0][1]
    assert trace.store.get_snapshot(calls[0].provider_request_snapshot_id).payload["messages"] == provider.outbound[0][1]
    allowed = {"run.queued", "run.started", "run.completed", "run.failed", "run.canceled", "run.interrupted", "message.delta", "message.completed", "llm.started", "llm.completed", "llm.failed", "tool.proposed", "tool.started", "tool.completed", "tool.failed", "confirmation.required", "confirmation.resolved", "job.linked", "job.status_changed", "job.progress", "artifact.created", "warning"}
    assert {event.type for event in events._streams[("agent_run", "run-runtime")]} <= allowed


def test_runtime_transport_retries_create_distinct_calls_and_update_lifecycle() -> None:
    class RetryingProvider:
        def __init__(self):
            self.count = 0

        def complete(self, **_kwargs):
            self.count += 1
            if self.count == 1:
                raise ConnectionError("temporary")
            return {"text": "ok", "usage": {"input_tokens": 4, "output_tokens": 2}}

    trace = ContextInspector(InMemoryTraceStore(), mode="redacted")
    runtime = AgentRuntime(
        RetryingProvider(),
            RuntimeConfigSnapshot("https://api.openai.com/v1", "model", "secret-ref", "r1"),
        history=[{"role": "user", "content": "hello"}],
        context_inspector=trace,
        project_id="project-a",
        max_retries=1,
    )
    result = runtime.legacy_run_for_tests(run_id="run-retry", attempt=1)
    assert result.status == "SUCCEEDED"
    calls = sorted(trace.store.calls.values(), key=lambda value: value.transport_attempt)
    assert [call.transport_attempt for call in calls] == [1, 2]
    assert [call.status for call in calls] == ["FAILED", "SUCCEEDED"]
    assert all(call.finished_at is not None and call.latency_ms is not None for call in calls)
    assert calls[0].usage.input_tokens is None
    assert calls[1].usage.input_tokens == 4
    assert calls[0].estimates != calls[1].estimates or calls[0].usage != calls[1].usage
    assert all(trace.store.get_snapshot(call.logical_context_snapshot_id).project_id == "project-a" for call in calls)


def test_runtime_trace_persistence_failure_is_not_reported_as_provider_success() -> None:
    class BrokenTraceStore(InMemoryTraceStore):
        def save_snapshot(self, snapshot):
            raise RuntimeError("trace write failed")

    class Provider:
        def complete(self, **_kwargs):
            return {"text": "would be success", "usage": {}}

    runtime = AgentRuntime(
        Provider(),
            RuntimeConfigSnapshot("https://api.openai.com/v1", "model", "secret-ref", "r1"),
        history=[{"role": "user", "content": "hello"}],
        context_inspector=ContextInspector(BrokenTraceStore(), mode="redacted"),
        project_id="project-a",
        max_retries=0,
    )
    result = runtime.legacy_run_for_tests(run_id="run-trace-failure")
    assert result.status == "FAILED"
    assert result.error and result.error["code"] == "TRACE_PERSISTENCE_FAILED"


def test_execution_ledger_marks_unknown_and_blocks_external_job_replay() -> None:
    class FailingPort(_BusinessPort):
        def execute(self, _ctx, **kwargs):
            self.executed.append(kwargs)
            raise RuntimeError("provider lost after submit")

    port = FailingPort()
    service = AssistantService(InMemoryAssistantRepository(), business_services={"create_prediction": port}, ledger=ToolExecutionLedger())
    ctx = _ctx("*")
    from chrom_platform.modules.assistant.schemas import ConversationCreate
    conv = service.create_conversation(ctx, ConversationCreate(project_id="project-a", mode="business"), idempotency_key="c")
    accepted = service.send_message(ctx, conv.id, UserMessageCreate(content="run", client_message_id="m"), idempotency_key="m")
    draft = ToolCallDraft(provider_tool_call_id="p", tool_name="create_prediction", schema_version="1")
    with pytest.raises(AssistantError) as first:
        service.propose_tool_call(ctx, accepted.agent_run_id, draft)
    assert first.value.payload.code == "BUSINESS_SERVICE_FAILED"
    with pytest.raises(AssistantError) as retry:
        service.propose_tool_call(ctx, accepted.agent_run_id, draft)
    assert retry.value.payload.code == "SIDE_EFFECT_UNKNOWN"
    assert len(port.executed) == 1


def test_strict_dto_json_roundtrip_for_trace_operations() -> None:
    usage = {"input_tokens": None, "output_tokens": 3, "cached_input_tokens": None}
    summary = LlmCallSummary(id="l", agent_run_id="r", request_index=0, transport_attempt=1, status="SUCCEEDED", model_id="m", usage=usage, created_at=datetime.now(timezone.utc))
    detail = LlmCallDetail(id="l", agent_run_id="r", agent_attempt_id="a", request_index=0, transport_attempt=1, status="SUCCEEDED", model_id="m", usage=usage, created_at=summary.created_at)
    assert LlmCallSummary.model_validate_json(summary.model_dump_json()).id == "l"
    assert LlmCallDetail.model_validate_json(detail.model_dump_json()).usage.output_tokens == 3


def test_bound_trace_store_cursor_reaches_sql_repository_port() -> None:
    call = TraceCall(
        id="llm-bound",
        agent_run_id="run-bound",
        agent_attempt_id="attempt-1",
        request_index=0,
        transport_attempt=1,
        model_id="model",
        created_at=datetime.now(timezone.utc),
    )

    class Repository:
        def __init__(self) -> None:
            self.cursor = None

        def list_llm_calls(self, session, *, run_id, limit, cursor=None):
            self.cursor = (session, run_id, limit, cursor)
            return [call]

    class BoundStore:
        def __init__(self, repository):
            self.repository, self.session = repository, "session-1"

        # Mirrors the current assembly adapter's pre-cursor signature.
        def list_llm_calls(self, *, run_id, limit=50):
            return self.repository.list_llm_calls(self.session, run_id=run_id, limit=limit)

    repository = Repository()
    inspector = ContextInspector(BoundStore(repository), mode="redacted")
    result = inspector.list_llm_calls("run-bound", actor=_ctx("trace:read"), limit=1, cursor="cursor-2")
    assert result[0]["id"] == "llm-bound"
    assert repository.cursor == ("session-1", "run-bound", 1, "cursor-2")


def test_sql_snapshot_project_scope_and_non_null_flush() -> None:
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _sqlite_functions(dbapi_connection, _connection_record):
        dbapi_connection.create_function("CHAR_LENGTH", 1, lambda value: len(value) if value is not None else None)

    PlatformBase.metadata.create_all(engine)
    AssistantBase.metadata.create_all(engine)
    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        session.add(Project(id="project-a", slug="project-a", name="A"))
        session.add(ConversationRow(id="conv-sql", project_id="project-a", owner_id="actor-a", title="SQL", mode="knowledge", status="ACTIVE", revision=1, etag="e", created_at=now, updated_at=now))
        session.add(AgentRunRow(id="run-sql", conversation_id="conv-sql", project_id="project-a", input_message_id="msg-sql", mode="knowledge", status="RUNNING", attempt=1, model_id="m", prompt_version="p", toolset_version="t", policy_version="policy", created_at=now, updated_at=now, revision=1, etag="e"))
        session.add(AgentAttemptRow(id="attempt-sql", agent_run_id="run-sql", attempt=1, status="RUNNING", created_at=now))
        session.flush()
        repository = SQLAlchemyAssistantRepository(session)
        store = type("BoundStore", (), {
            "repository": repository,
            "session": session,
            "save_llm_call": lambda self, call: repository.save_llm_call(session, call),
            "get_llm_call": lambda self, call_id: repository.get_llm_call(call_id, session=session),
            "save_snapshot": lambda self, snapshot: repository.save_context_snapshot(session, snapshot),
            "get_snapshot": lambda self, snapshot_id: repository.get_context_snapshot(snapshot_id, session=session),
        })()
        inspector = ContextInspector(store, mode="redacted")
        call = TraceCall(id="llm-sql", agent_run_id="run-sql", agent_attempt_id="attempt-sql", request_index=1, transport_attempt=1, model_id="m", project_id="project-a", usage=Usage())
        logical, provider = inspector.capture_pair(llm_call=call, logical_context={"messages": []}, provider_request={"messages": []}, project_id="project-a")
        session.flush()
        row = session.get(ContextSnapshotRow, logical.id)
        assert row is not None and row.project_id == "project-a"
        assert row.project_id is not None
        loaded = repository.get_context_snapshot(provider.id, session=session)
        assert loaded is not None and loaded.project_id == "project-a"
        loaded_call = repository.get_llm_call("llm-sql", session=session)
        assert loaded_call is not None and loaded_call.project_id == "project-a"
        assert session.get(LlmCallRow, "llm-sql").status == "CREATED"


def test_failed_agent_run_public_projection_filters_internal_usage_fields() -> None:
    now = datetime.now(timezone.utc)
    row = AgentRunRow(
        id="run-failed-projection",
        conversation_id="conv-sql",
        project_id="project-a",
        input_message_id="msg-sql",
        mode="knowledge",
        status="FAILED",
        attempt=1,
        model_id="m",
        prompt_version="p",
        toolset_version="t",
        policy_version="policy",
        usage={"model_requests": 1, "tool_calls": 0, "transport_attempts": 1},
        error={"code": "TRACE_SCOPE_UNAVAILABLE", "message": "trace scope unavailable", "retryable": True},
        last_event_seq=3,
        created_at=now,
        updated_at=now,
        revision=2,
        etag="e",
    )
    projected = _run(row)
    assert projected.status == "FAILED"
    assert projected.error.code == "TRACE_SCOPE_UNAVAILABLE"
    assert projected.usage.model_requests == 1
    assert projected.usage.tool_calls == 0
    assert "transport_attempts" not in projected.usage.model_dump()


def test_snapshot_http_probe_enforces_project_scope_for_metadata_content_and_compare() -> None:
    repository = InMemoryAssistantRepository()
    service = AssistantService(repository, runtime_available=True)
    conversation = service.create_conversation(_ctx("assistant:use"), ConversationCreate(project_id="project-a", mode="knowledge"), idempotency_key="scope-conv")
    accepted = service.send_message(_ctx("assistant:use"), conversation.id, UserMessageCreate(content="hello", client_message_id="scope-msg"), idempotency_key="scope-msg")
    store = InMemoryTraceStore()
    inspector = ContextInspector(store, mode="redacted")
    call = TraceCall(id="scope-http-call", agent_run_id=accepted.agent_run_id, agent_attempt_id="attempt-1", request_index=1, transport_attempt=1, model_id="m", project_id="project-a", usage=Usage())
    left, right = inspector.capture_pair(llm_call=call, logical_context={"a": 1}, provider_request={"a": 2}, project_id="project-a")
    client = _app(service, trace=inspector)
    client.app.state.actor_context = ActorContext(actor_id="actor-a", tenant_id=None, project_ids=["project-b"], auth_method="deployment_default", permissions=["trace:read", "trace:content:read"], policy_version="policy-1")
    assert client.get(f"/api/v1/context-snapshots/{left.id}").status_code == 404
    assert client.get(f"/api/v1/context-snapshots/{left.id}/content").status_code == 404
    assert client.post("/api/v1/context-snapshots/compare", json={"left_id": left.id, "right_id": right.id}).status_code == 404
    client.app.state.actor_context = ActorContext(actor_id="actor-a", tenant_id=None, project_ids=["project-a"], auth_method="deployment_default", permissions=["trace:read", "trace:content:read"], policy_version="policy-1")
    assert client.get(f"/api/v1/context-snapshots/{left.id}").status_code == 200
    assert client.get(f"/api/v1/context-snapshots/{left.id}/content").status_code == 200
    assert client.post("/api/v1/context-snapshots/compare", json={"left_id": left.id, "right_id": right.id}).status_code == 200
