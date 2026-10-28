from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest

import chrom_platform.modules.assistant.adapter as adapter_module
from chrom_platform.modules.assistant.adapter import PydanticAIAdapter, ProviderResponseError, ProviderTransportError, _OpenAICompatibleClient
from chrom_platform.modules.assistant.errors import AssistantError
from chrom_platform.modules.assistant.runtime import AgentRuntime, RuntimeConfigSnapshot, RuntimeResult, _summary
from chrom_platform.modules.assistant.security import trusted_provider_url
from chrom_platform.modules.assistant.events import InMemoryAgentEventStore, event_stream_status, events_page
from chrom_platform.modules.assistant.sse import stream_events
from chrom_platform.modules.assistant.persistence import _run
from chrom_platform.modules.assistant.production_worker import AsyncAssistantExecutionStore, AssistantLlmConfigurationTestWorker, AssistantProductionWorker, AssistantWorkerMessage, _assistant_text_blocks, _runtime_result_json
from chrom_platform.modules.assistant.worker import AgentWorker, AgentWorkerMessage
from chrom_platform.modules.assistant.tools import BoundTool, ToolSpec
from chrom_platform.modules.assistant.priority import priority_toolset
from chrom_platform.task_core.service import _attempt_view, _job_view


class Provider:
    def __init__(self, response=None, error: Exception | None = None):
        self.response = response or {"text": "ok"}
        self.error = error
        self.messages = []

    def complete(self, **kwargs):
        self.messages.append(kwargs["messages"])
        if self.error:
            raise self.error
        return self.response


class SequenceProvider:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.messages = []

    def complete(self, **kwargs):
        self.messages.append(kwargs["messages"])
        index = min(len(self.messages) - 1, len(self.responses) - 1)
        return self.responses[index]


class _RuntimeToolService:
    def __init__(self, values):
        self.values = values

    def validate(self, _ctx, **_kwargs):
        return None

    def execute(self, _ctx, *, tool_name, **_kwargs):
        value = self.values[tool_name]
        return value() if callable(value) else value


def _runtime_tools(values):
    service = _RuntimeToolService(values)
    return {
        name: BoundTool(ToolSpec(name, name, permission="assistant:use"), service)
        for name in values
    }


def test_worker_scope_loads_immutable_run_mode_for_toolset_selection():
    class Result:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    class Session:
        def __init__(self):
            self.values = [
                SimpleNamespace(
                    id="run-knowledge", conversation_id="conv-knowledge", project_id="project-a",
                    input_message_id="message-knowledge", mode="knowledge", policy_version="p1",
                    pending_actions=[],
                ),
                SimpleNamespace(id="conv-knowledge", project_id="project-a", owner_id="actor-a"),
                SimpleNamespace(id="message-knowledge", agent_run_id="run-knowledge", attachment_ids=[]),
            ]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def execute(self, _query):
            return Result(self.values.pop(0))

    scope = asyncio.run(AsyncAssistantExecutionStore(lambda: Session()).load_scope("run-knowledge"))
    tools = {spec.name for spec in priority_toolset(mode=scope.mode).specs}

    assert scope.mode == "knowledge"
    assert "create_prediction" not in tools
    assert "normalize_structure" not in tools
    assert "create_registration_draft" not in tools
    assert "get_prediction" in tools


class Store:
    def __init__(self, *, dispatch="d1", cancel=False):
        self.run = {"id": "run-1", "status": "QUEUED", "attempt": 1, "dispatch_id": dispatch}
        self.cancel = cancel
        self.claims = 0
        self.finished = []

    def load_run(self, _run_id):
        return self.run

    def claim(self, **kwargs):
        self.claims += 1
        if kwargs["dispatch_id"] != self.run["dispatch_id"]:
            return None
        return {"fencing_token": 7}

    def is_cancel_requested(self, _run_id):
        return self.cancel

    def finish(self, **kwargs):
        self.finished.append(kwargs)
        return kwargs


def config():
    return RuntimeConfigSnapshot("https://api.openai.com/v1", "model-1", "secret-ref", "rev-1")


def test_runtime_uses_trusted_history_and_provider_errors_are_safe():
    provider = Provider(error=RuntimeError("https://provider?token=do-not-leak"))
    runtime = AgentRuntime(provider, config(), history=[{"role": "user", "content": "hello"}], max_retries=0)
    result = runtime.legacy_run_for_tests(run_id="run-1")
    assert result.status == "FAILED"
    assert result.error == {"code": "PROVIDER_ERROR", "message": "模型服务调用失败", "retryable": True}
    assert "token" not in str(result.error)
    assert provider.messages[0][0]["role"] == "system"
    assert provider.messages[0][1] == {"role": "user", "content": "hello"}


def test_runtime_leaves_terminal_event_to_fenced_execution_store():
    events = InMemoryAgentEventStore()
    runtime = AgentRuntime(Provider(), config(), history=[{"role": "user", "content": "hello"}], event_store=events)
    result = runtime.legacy_run_for_tests(run_id="run-1", attempt=1)
    assert result.status == "SUCCEEDED"
    assert "run.completed" not in [event.type for event in events.list_after("agent_run", "run-1", 0)]
    assert "run.failed" not in [event.type for event in events.list_after("agent_run", "run-1", 0)]


def test_runtime_allows_clarification_or_ordinary_answer_without_model_effect_directive():
    provider = SequenceProvider({"text": "缺少流速和固定相，请补充后再提交。"})
    result = AgentRuntime(provider, config(), history=[{"role": "user", "content": "submit a prediction"}]).legacy_run_for_tests(run_id="run-clarify")
    assert result.status == "SUCCEEDED"
    assert result.text == "缺少流速和固定相，请补充后再提交。"


def test_runtime_keeps_submitted_and_result_as_tool_evidence_without_synthesizing_text():
    provider = SequenceProvider(
        {"tool_calls": [{"id": "create", "name": "create_prediction", "arguments": {}}]},
        {"tool_calls": [{"id": "get", "name": "get_prediction", "arguments": {"prediction_id": "pred-1"}}]},
        {"text": "已提交，结果仍在处理中。"},
    )
    tools = _runtime_tools({
        "create_prediction": {"resource_ref": {"type": "prediction", "id": "pred-1"}, "job_id": "job-1", "status": "QUEUED"},
        "get_prediction": {"id": "pred-1", "status": "SUCCEEDED", "result": {"value": 1}},
    })
    result = AgentRuntime(provider, config(), toolset=tools, history=[{"role": "user", "content": "run prediction"}]).legacy_run_for_tests(run_id="run-prediction")
    assert result.status == "SUCCEEDED"
    assert result.tool_calls == 2
    assert len(provider.messages) == 3
    assert result.text == "已提交，结果仍在处理中。"
    assert result.tool_results[0]["status"] == "QUEUED"
    assert result.tool_results[1]["status"] == "SUCCEEDED"


def test_runtime_rejects_invalid_prediction_submission_without_success_projection():
    provider = SequenceProvider({"tool_calls": [{"id": "create", "name": "create_prediction", "arguments": {}}]})
    tools = _runtime_tools({"create_prediction": {"error": {"code": "VALIDATION_FAILED", "message": "invalid", "retryable": False}}})
    result = AgentRuntime(provider, config(), toolset=tools, history=[{"role": "user", "content": "run prediction"}]).legacy_run_for_tests(run_id="run-invalid-prediction")
    assert result.status == "FAILED"
    assert result.error["code"] == "VALIDATION_FAILED"


def test_runtime_stops_same_tool_args_and_result_without_progress():
    response = {"tool_calls": [{"id": "summary", "name": "get_data_version_summary", "arguments": {}}]}
    provider = SequenceProvider(response, response, response, response)
    tools = _runtime_tools({"get_data_version_summary": {"source_ids": ["v1"]}})
    result = AgentRuntime(provider, config(), toolset=tools, history=[{"role": "user", "content": "summarize data"}]).legacy_run_for_tests(run_id="run-no-progress")
    assert result.status == "FAILED"
    assert result.error["code"] == "RESOURCE_LIMIT_EXCEEDED"
    assert "without resource or status progress" in result.error["message"]
    assert result.tool_calls == 3
    assert provider.messages[2][-1]["role"] == "system"
    assert "不要再次调用该工具" in provider.messages[2][-1]["content"]


def test_runtime_stops_nonconsecutive_no_progress_cycle():
    provider = SequenceProvider(
        {"tool_calls": [{"id": "cap-1", "name": "get_prediction_capabilities", "arguments": {}}]},
        {"tool_calls": [{"id": "models-1", "name": "list_models", "arguments": {}}]},
        {"tool_calls": [{"id": "cap-2", "name": "get_prediction_capabilities", "arguments": {}}]},
        {"tool_calls": [{"id": "models-2", "name": "list_models", "arguments": {}}]},
        {"tool_calls": [{"id": "cap-3", "name": "get_prediction_capabilities", "arguments": {}}]},
    )
    tools = _runtime_tools({"get_prediction_capabilities": {"capabilities": ["tlc"]}, "list_models": {"models": ["m1"]}})
    result = AgentRuntime(provider, config(), toolset=tools, history=[{"role": "user", "content": "inspect"}]).legacy_run_for_tests(run_id="run-cycle")
    assert result.status == "FAILED"
    assert result.error["code"] == "RESOURCE_LIMIT_EXCEEDED"
    assert result.tool_calls == 5


def test_runtime_allows_repeated_pending_prediction_poll_until_request_budget():
    poll = {"tool_calls": [{"id": "poll", "name": "get_prediction", "arguments": {"prediction_id": "pred-1"}}]}
    provider = SequenceProvider(poll, poll, poll, poll)
    tools = _runtime_tools({"get_prediction": {"id": "pred-1", "status": "RUNNING"}})
    result = AgentRuntime(provider, config(), toolset=tools, history=[{"role": "user", "content": "wait"}], max_requests=4).legacy_run_for_tests(run_id="run-poll")
    assert result.status == "FAILED"
    assert result.error["code"] == "RESOURCE_LIMIT_EXCEEDED"
    assert result.tool_calls == 4


def test_provider_tool_result_has_bounded_valid_json_with_key_fields_on_large_output():
    wire = _summary({"resource_ref": {"type": "structure", "id": "s1"}, "status": "SUCCEEDED", "payload": "x" * 30_000})
    payload = json.loads(wire)
    assert len(wire.encode("utf-8")) <= 20_000
    assert payload["truncated"] is True
    assert payload["resource_ref"] == {"type": "structure", "id": "s1"}
    assert len(payload["result_hash"]) == 64


def test_normalize_result_is_sent_as_complete_safe_json_to_next_model_turn():
    provider = SequenceProvider(
        {"tool_calls": [{"id": "normalize", "name": "normalize_structure", "arguments": {"smiles": "CCO"}}]},
        {"text": "结构已规范化。"},
    )
    value = {"structure_ref": {"type": "structure", "id": "structure-1"}, "resource_ref": {"type": "structure", "id": "structure-1"}, "canonical_smiles": "CCO", "snapshot_hash": "a" * 64}
    result = AgentRuntime(provider, config(), toolset=_runtime_tools({"normalize_structure": value}), history=[{"role": "user", "content": "normalize"}]).legacy_run_for_tests(run_id="run-normalize")
    assert result.status == "SUCCEEDED"
    tool_message = next(message for message in provider.messages[1] if message.get("role") == "tool")
    payload = json.loads(tool_message["content"])
    assert payload["structure_ref"] == {"type": "structure", "id": "structure-1"}
    assert payload["resource_ref"] == {"type": "structure", "id": "structure-1"}


def test_priority_prediction_schema_and_description_require_normalized_refs_and_conditions():
    spec = next(item for item in priority_toolset().specs if item.name == "create_prediction")
    encoded = json.dumps(spec.argument_schema, ensure_ascii=False)
    assert "structure_refs" in encoded
    assert "normalize_structure" in spec.description
    assert "TLC" in spec.description
    assert "get_prediction" in spec.description


@pytest.mark.parametrize("status", ["SUCCEEDED", "FAILED", "CANCELED"])
def test_event_page_closes_for_terminal_run_status_and_preserves_cursor(status):
    events = InMemoryAgentEventStore()
    events.append("agent_run", "run-stream", "run.started", {"status": "RUNNING"})
    events.append("agent_run", "run-stream", "run.completed" if status == "SUCCEEDED" else "run.failed", {"status": status})
    page = events_page(events, "agent_run", "run-stream", authoritative_status=status)
    resumed = events_page(events, "agent_run", "run-stream", after_seq=1, authoritative_status=status)
    assert event_stream_status(status) == "TERMINAL"
    assert page["stream_status"] == resumed["stream_status"] == "TERMINAL"
    assert resumed["next_seq"] == "2"


def test_event_page_keeps_running_stream_open_and_last_event_cursor():
    events = InMemoryAgentEventStore()
    events.append("agent_run", "run-stream-open", "run.started", {"status": "RUNNING"})
    page = events_page(events, "agent_run", "run-stream-open", after_seq=0, authoritative_status="RUNNING")
    assert event_stream_status("RUNNING") == "OPEN"
    assert page["stream_status"] == "OPEN"
    assert page["next_seq"] == "1"


def test_async_execution_store_deduplicates_runtime_first_terminal_event_after_restart():
    run = SimpleNamespace(last_event_seq=0)
    events = []

    class Result:
        def __init__(self, *, scalar=None, rows=()):
            self.scalar, self.rows = scalar, list(rows)

        def scalar_one_or_none(self):
            return self.scalar

        def scalars(self):
            return self

        def all(self):
            return list(self.rows)

    class Session:
        async def execute(self, statement):
            text = str(statement)
            if "agent_event" in text:
                return Result(rows=events)
            return Result(scalar=run)

        def add(self, value):
            events.append(value)

    session = Session()
    asyncio.run(AsyncAssistantExecutionStore._agent_event(session, "run-1", "run.started", {"attempt": 1}))
    asyncio.run(AsyncAssistantExecutionStore._agent_event(session, "run-1", "run.failed", {"attempt": 1, "error": {"code": "A"}}))
    restarted_store = AsyncAssistantExecutionStore(lambda: None)
    asyncio.run(restarted_store._agent_event(session, "run-1", "run.failed", {"attempt": 1, "error": {"code": "B"}}))
    assert [event.type for event in events] == ["run.started", "run.failed"]
    assert run.last_event_seq == 1


def test_runtime_rejects_client_history_and_optional_dependency_is_503():
    runtime = AgentRuntime(Provider(), config())
    with pytest.raises(AssistantError) as history_error:
        runtime.legacy_run_for_tests(run_id="run-1", messages=[{"role": "user", "content": "client supplied"}])
    assert history_error.value.payload.code == "DEPENDENCY_UNAVAILABLE"

    with pytest.raises(AssistantError) as exc_info:
        PydanticAIAdapter(import_name="module_that_is_not_installed").complete(config=config(), messages=[])
    assert exc_info.value.status_code == 503
    assert "module_that_is_not_installed" in exc_info.value.payload.message


def test_worker_stale_dispatch_and_cancel_do_not_call_provider_or_business_job():
    provider = Provider()
    runtime = AgentRuntime(provider, config(), history=[{"role": "user", "content": "hello"}])
    stale_store = Store(dispatch="d2")
    stale = AgentWorker(runtime=runtime, store=stale_store).handle(AgentWorkerMessage("run-1", 1, "d1"))
    assert stale.replayed and stale_store.claims == 0 and not provider.messages

    cancel_store = Store(cancel=True)
    canceled = AgentWorker(runtime=runtime, store=cancel_store).handle(AgentWorkerMessage("run-1", 1, "d1"))
    assert canceled.status == "CANCELED"
    assert cancel_store.finished[0]["status"] == "CANCELED"
    assert not provider.messages


def test_runtime_config_public_shape_has_no_secret():
    public = config().public_dict()
    assert set(public) == {"url", "model_id", "key_reference", "config_revision"}
    assert "secret-ref" in public["key_reference"]  # reference is safe; secret value is never accepted
    with pytest.raises(ValueError):
        RuntimeConfigSnapshot("u", "m", "token=raw-secret", "r")


def test_default_adapter_sends_openai_compatible_request_and_normalizes_tool_call(monkeypatch):
    captured = {}
    def fake_request_once(**kwargs):
        captured.update(kwargs)
        return json.dumps({
            "choices": [{"finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [{
                "id": "call-1", "function": {"name": "get_prediction_capabilities", "arguments": "{}"},
            }]}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        }).encode("utf-8")

    monkeypatch.setenv("API_KEY", "process-only-secret")
    monkeypatch.setattr(adapter_module.importlib, "import_module", lambda *_args, **_kwargs: pytest.fail("default adapter must not import optional backend"))
    monkeypatch.setattr(_OpenAICompatibleClient, "_resolved_public_ips", staticmethod(lambda *_args: ["198.51.100.10"]))
    monkeypatch.setattr(_OpenAICompatibleClient, "_request_once", staticmethod(fake_request_once))
    result = PydanticAIAdapter().complete(
            config=RuntimeConfigSnapshot("https://api.openai.com/v1", "model-1", "API_KEY", "rev-1"),
        messages=[{"role": "user", "content": "call the capability tool"}],
        tools=[{"name": "get_prediction_capabilities", "description": "read", "parameters": {"type": "object", "properties": {}}}],
        timeout_seconds=4,
    )
    assert result["tool_calls"] == [{"id": "call-1", "name": "get_prediction_capabilities", "arguments": {}}]
    assert result["usage"] == {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}
    assert result["finish_reason"] == "tool_calls"
    assert captured["hostname"] == "api.openai.com"
    assert captured["address"] == "198.51.100.10"
    assert captured["path"] == "/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer process-only-secret"
    assert json.loads(captured["body"].decode("utf-8"))["tools"][0]["function"]["name"] == "get_prediction_capabilities"
    assert json.loads(captured["body"].decode("utf-8"))["max_tokens"] == 512


@pytest.mark.parametrize("operation", ["complete", "stream"])
@pytest.mark.parametrize("status", [401, 403])
def test_default_adapter_auth_http_failure_does_not_try_another_ip(monkeypatch, operation, status):
    monkeypatch.setenv("API_KEY", "process-only-secret")
    addresses = ["198.51.100.10", "198.51.100.11"]
    monkeypatch.setattr(_OpenAICompatibleClient, "_resolved_public_ips", staticmethod(lambda *_args: addresses))
    attempted = []

    def fake_request_once(**kwargs):
        attempted.append(kwargs["address"])
        raise ProviderTransportError("HTTP_4XX", http_status=status)

    monkeypatch.setattr(_OpenAICompatibleClient, "_request_once", staticmethod(fake_request_once))
    client = _OpenAICompatibleClient()
    request = {
        "config": RuntimeConfigSnapshot("https://api.openai.com/v1", "model-1", "API_KEY", "rev-1"),
        "messages": [{"role": "user", "content": "hello"}], "tools": [], "timeout_seconds": 4,
    }

    with pytest.raises(ProviderTransportError) as failure:
        if operation == "complete":
            client.complete(**request)
        else:
            list(client.stream(**request))

    assert failure.value.http_status == status
    assert failure.value.category == "HTTP_4XX"
    assert "process-only-secret" not in str(failure.value)
    assert attempted == [addresses[0]]


@pytest.mark.parametrize("operation", ["complete", "stream"])
def test_default_adapter_connection_failure_falls_back_to_another_ip(monkeypatch, operation):
    monkeypatch.setenv("API_KEY", "process-only-secret")
    addresses = ["198.51.100.10", "198.51.100.11"]
    monkeypatch.setattr(_OpenAICompatibleClient, "_resolved_public_ips", staticmethod(lambda *_args: addresses))
    attempted = []

    def fake_request_once(**kwargs):
        attempted.append(kwargs["address"])
        if len(attempted) == 1:
            raise ProviderTransportError("CONNECT")
        if operation == "complete":
            return b'{"choices":[{"message":{"content":"ok","tool_calls":[]}}]}'
        return b'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'

    monkeypatch.setattr(_OpenAICompatibleClient, "_request_once", staticmethod(fake_request_once))
    client = _OpenAICompatibleClient()
    request = {
        "config": RuntimeConfigSnapshot("https://api.openai.com/v1", "model-1", "API_KEY", "rev-1"),
        "messages": [{"role": "user", "content": "hello"}], "tools": [], "timeout_seconds": 4,
    }

    if operation == "complete":
        assert client.complete(**request)["text"] == "ok"
    else:
        assert any(event.get("text") == "ok" for event in client.stream(**request))
    assert attempted == addresses


def test_default_adapter_preserves_nonstream_truncation_reason_and_usage(monkeypatch):
    def fake_request_once(**_kwargs):
        return json.dumps({
            "choices": [{"finish_reason": "length", "message": {"content": "partial", "tool_calls": [
                {"id": "partial-call", "function": {"name": "read", "arguments": "{}"}},
            ]}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 512, "total_tokens": 523},
        }).encode("utf-8")

    monkeypatch.setenv("API_KEY", "process-only-secret")
    monkeypatch.setattr(_OpenAICompatibleClient, "_resolved_public_ips", staticmethod(lambda *_args: ["198.51.100.10"]))
    monkeypatch.setattr(_OpenAICompatibleClient, "_request_once", staticmethod(fake_request_once))
    result = PydanticAIAdapter().complete(
        config=RuntimeConfigSnapshot("https://api.openai.com/v1", "model-1", "API_KEY", "rev-1"),
        messages=[{"role": "user", "content": "read"}],
        tools=[{"name": "read", "parameters": {"type": "object", "properties": {}}}],
    )

    assert result["finish_reason"] == "length"
    assert result["usage"] == {"input_tokens": 11, "output_tokens": 512, "total_tokens": 523}
    assert result["tool_calls"] == [{"id": "partial-call", "name": "read", "arguments": {}}]


def test_runtime_rejects_provider_length_termination_without_final_answer():
    class Inspector:
        def __init__(self):
            self.calls = []
        def capture_pair(self, **_kwargs):
            return SimpleNamespace(id="logical"), SimpleNamespace(id="provider")
        def save_llm_call(self, call):
            self.calls.append(call)

    inspector = Inspector()
    events = InMemoryAgentEventStore()
    result = AgentRuntime(
        Provider(response={"text": "unfinished answer", "finish_reason": "length", "usage": {"input_tokens": 21, "output_tokens": 512, "total_tokens": 533}}),
        config(), history=[{"role": "user", "content": "summarize"}],
        context_inspector=inspector, event_store=events, max_retries=0, trace_metadata_only=True,
    ).legacy_run_for_tests(run_id="run-truncated-provider-output")

    assert result.status == "FAILED"
    assert result.text is None
    assert result.error == {
        "code": "PROVIDER_OUTPUT_TRUNCATED",
        "message": "模型服务输出达到长度上限，结果未提交",
        "retryable": False,
    }
    assert result.usage == {"input_tokens": 21, "output_tokens": 512, "total_tokens": 533}
    assert inspector.calls[-1].status == "FAILED"
    assert inspector.calls[-1].usage.input_tokens == 21
    assert inspector.calls[-1].usage.output_tokens == 512
    assert inspector.calls[-1].error["code"] == "PROVIDER_OUTPUT_TRUNCATED"
    assert inspector.calls[-1].response_summary == {
        "text_excerpt": None, "tool_names": [], "finish_reason": "length",
    }
    recorded = events.list_after("agent_run", "run-truncated-provider-output", 0)
    assert [event.type for event in recorded] == ["llm.started", "llm.failed"]
    assert recorded[-1].data["error"]["code"] == "PROVIDER_OUTPUT_TRUNCATED"
    assert recorded[-1].data["usage"] == result.usage
    page = events_page(events, "agent_run", "run-truncated-provider-output")
    assert page["events"][-1]["data"]["usage"] == result.usage
    sse = "".join(stream_events(events, "agent_run", "run-truncated-provider-output"))
    assert '"total_tokens":533' in sse
    assert "unfinished answer" not in sse
    assert AssistantProductionWorker._runtime_usage(result) == {
        "model_requests": 1, "tool_calls": 0, "transport_attempts": 1,
        "input_tokens": 21, "output_tokens": 512,
    }


def test_stream_aggregation_keeps_final_nonempty_truncation_reason_and_blocks_tool_calls():
    executions = []

    class StreamingProvider:
        def stream(self, **_kwargs):
            return iter((
                {"text": "partial", "finish_reason": "stop", "usage": {"input_tokens": 17}},
                {"text": " answer", "tool_calls": [{"id": "call-1", "name": "read", "arguments": {}}],
                 "finish_reason": "MAX_TOKENS", "usage": {"output_tokens": 512, "total_tokens": 529}},
                {"text": "", "finish_reason": "", "usage": {}},
            ))

    class Inspector:
        def __init__(self):
            self.saved = []
        def capture_pair(self, **_kwargs):
            return SimpleNamespace(id="logical"), SimpleNamespace(id="provider")
        def save_llm_call(self, call):
            self.saved.append(call)

    inspector = Inspector()
    events = InMemoryAgentEventStore()
    result = AgentRuntime(
        StreamingProvider(), config(), history=[{"role": "user", "content": "read"}],
        toolset=_runtime_tools({"read": lambda: executions.append("read") or {"ok": True}}),
        context_inspector=inspector, event_store=events, stream=True, max_retries=0,
        trace_metadata_only=True,
    ).legacy_run_for_tests(run_id="run-truncated-stream")

    assert result.status == "FAILED"
    assert result.text is None
    assert result.tool_calls == 0
    assert result.usage == {"input_tokens": 17, "output_tokens": 512, "total_tokens": 529}
    assert executions == []
    assert inspector.saved[-1].status == "FAILED"
    assert inspector.saved[-1].error["code"] == "PROVIDER_OUTPUT_TRUNCATED"
    assert inspector.saved[-1].response_summary == {
        "text_excerpt": None, "tool_names": ["read"], "finish_reason": "MAX_TOKENS",
    }
    recorded = events.list_after("agent_run", "run-truncated-stream", 0)
    assert [event.type for event in recorded] == ["llm.started", "llm.failed"]
    assert recorded[-1].data["usage"] == result.usage


@pytest.mark.parametrize("finish_reason", ["stop", "tool_calls"])
def test_runtime_accepts_nontruncated_finish_reasons(finish_reason):
    calls = []
    if finish_reason == "tool_calls":
        provider = SequenceProvider(
            {"tool_calls": [{"id": "read-1", "name": "read", "arguments": {}}], "finish_reason": "tool_calls"},
            {"text": "done", "finish_reason": "stop"},
        )
        toolset = _runtime_tools({"read": lambda: calls.append("read") or {"ok": True}})
    else:
        provider = Provider(response={"text": "done", "finish_reason": finish_reason})
        toolset = None

    result = AgentRuntime(
        provider, config(), history=[{"role": "user", "content": "hello"}],
        toolset=toolset, max_retries=0,
    ).legacy_run_for_tests(run_id=f"run-finish-{finish_reason}")

    assert result.status == "SUCCEEDED"
    assert result.text == "done"
    assert calls == (["read"] if finish_reason == "tool_calls" else [])


def test_default_adapter_keeps_json_assistant_content_as_text_without_private_completion_directive(monkeypatch):
    captured = {}

    def fake_request_once(**kwargs):
        captured.update(kwargs)
        return json.dumps({
            "choices": [{"message": {"content": json.dumps({"assistant_outcome": {"required_effect": "summary"}})}}],
        }).encode("utf-8")

    monkeypatch.setenv("API_KEY", "process-only-secret")
    monkeypatch.setattr(_OpenAICompatibleClient, "_resolved_public_ips", staticmethod(lambda *_args: ["198.51.100.10"]))
    monkeypatch.setattr(_OpenAICompatibleClient, "_request_once", staticmethod(fake_request_once))
    result = PydanticAIAdapter().complete(
        config=RuntimeConfigSnapshot("https://api.openai.com/v1", "model-1", "API_KEY", "rev-1"),
        messages=[{"role": "user", "content": "summarize"}],
    )
    assert result["structured"] is None
    assert result["text"] == json.dumps({"assistant_outcome": {"required_effect": "summary"}})


def test_default_adapter_classifies_http_status_and_allowlisted_provider_code(monkeypatch):
    class FakeContext:
        def wrap_socket(self, raw, server_hostname):
            return raw
    class FakeResponse:
        status = 400
        read_count = 0
        def read(self, _limit):
            self.read_count += 1
            return b'{"code":20015,"message":"secret provider body must not persist"}' if self.read_count == 1 else b""
        def getheaders(self):
            return []
    class FakeConnection:
        def __init__(self, *_args, **_kwargs):
            pass
        def request(self, *_args, **_kwargs):
            pass
        def getresponse(self):
            return FakeResponse()
        def close(self):
            pass
    monkeypatch.setattr(adapter_module.ssl, "create_default_context", lambda: FakeContext())
    monkeypatch.setattr(adapter_module.socket, "create_connection", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(adapter_module.http.client, "HTTPSConnection", FakeConnection)
    with pytest.raises(ProviderTransportError) as failure:
        _OpenAICompatibleClient._request_once(
            hostname="api.openai.com", address="198.51.100.10", port=443,
            path="/v1/chat/completions", body=b"{}", headers={}, timeout=1,
        )
    exc = failure.value
    assert exc.category == "HTTP_4XX"
    assert exc.http_status == 400
    assert exc.provider_error_code == "20015"
    assert "secret provider body" not in str(exc)
    assert exc.safe_details() == {"category": "HTTP_4XX", "http_status": 400, "provider_error_code": "20015"}


def test_runtime_persists_only_safe_provider_diagnostics():
    provider = Provider(error=ProviderTransportError("HTTP_5XX", http_status=503, provider_error_code="server_error"))
    class Inspector:
        def __init__(self):
            self.calls = []
        def capture_pair(self, **_kwargs):
            return SimpleNamespace(id="logical"), SimpleNamespace(id="provider")
        def save_llm_call(self, call):
            self.calls.append(call)
    inspector = Inspector()
    result = AgentRuntime(provider, config(), history=[{"role": "user", "content": "hello"}], max_retries=0, context_inspector=inspector).legacy_run_for_tests(run_id="run-safe-diagnostics")
    assert result.error == {
        "code": "PROVIDER_UNAVAILABLE", "message": "模型服务暂不可用", "retryable": True,
    }
    assert inspector.calls[-1].error == {"code": "PROVIDER_UNAVAILABLE", "message": "模型服务暂不可用", "retryable": True, "provider": {"category": "HTTP_5XX", "http_status": 503, "provider_error_code": "server_error"}}


def test_metadata_only_trace_persists_shape_hashes_without_prompt_or_wire_body():
    class Inspector:
        def __init__(self):
            self.capture = None
            self.calls = []
        def capture_pair(self, **kwargs):
            self.capture = kwargs
            return SimpleNamespace(id="logical"), SimpleNamespace(id="provider")
        def save_llm_call(self, call):
            self.calls.append(call)

    inspector = Inspector()
    provider = Provider(error=ProviderTransportError("HTTP_4XX", http_status=400, provider_error_code="invalid_request_error"))
    runtime = AgentRuntime(
        provider,
        RuntimeConfigSnapshot("https://api.openai.com/v1", "model-1", "API_KEY", "rev-1", provider_configuration_id="cfg-1"),
        history=[{"role": "user", "content": "private prompt should not be persisted"}],
        context_inspector=inspector,
        trace_metadata_only=True,
        max_retries=0,
    )
    result = runtime.legacy_run_for_tests(run_id="run-metadata-only")
    assert result.status == "FAILED"
    assert result.provider_requests == 1 and result.transport_attempts == 1
    payload = inspector.capture["provider_request"]
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "private prompt" not in serialized
    assert "messages" in payload and payload["messages"]["items"][0]["content_length"] > 0
    assert len(payload["messages"]["items"][0]["content_sha256"]) == 64
    assert payload["configuration"] == {
        "provider_configuration_id": "cfg-1",
        "config_revision": "rev-1",
        "model_id": "model-1",
        "key_reference": "API_KEY",
    }
    assert all(secret not in serialized.lower() for secret in ("authorization", "bearer", "api_key_value", "chat/completions"))
    assert inspector.calls[-1].status == "FAILED"

    success_inspector = Inspector()
    success = AgentRuntime(
        Provider(response={"text": "provider response must not be persisted", "usage": {}}),
        config(),
        history=[{"role": "user", "content": "hello"}],
        context_inspector=success_inspector,
        trace_metadata_only=True,
        max_retries=0,
    ).legacy_run_for_tests(run_id="run-metadata-success")
    assert success.status == "SUCCEEDED"
    assert success_inspector.calls[-1].response_summary == {"text_excerpt": None, "tool_names": [], "finish_reason": None}


def test_metadata_trace_uses_server_bound_project_scope_in_worker_context():
    class Inspector:
        def __init__(self):
            self.capture = None

        def capture_pair(self, **kwargs):
            self.capture = kwargs
            return SimpleNamespace(id="logical"), SimpleNamespace(id="provider")

        def save_llm_call(self, _call):
            return None

    inspector = Inspector()
    result = AgentRuntime(
        Provider(response={"text": "ok", "usage": {}}),
        config(),
        history=[{"role": "user", "content": "hello"}],
        context_inspector=inspector,
        trace_metadata_only=True,
        max_retries=0,
    ).legacy_run_for_tests(run_id="run-server-project", ctx=SimpleNamespace(server_project_id="project-a", agent_attempt_id="attempt-persisted"))
    assert result.status == "SUCCEEDED"
    assert inspector.capture["project_id"] == "project-a"
    assert inspector.capture["llm_call"].agent_attempt_id == "attempt-persisted"


def test_production_runtime_usage_projects_failed_and_retry_counters():
    value = SimpleNamespace(
        provider_requests=1,
        tool_calls=0,
        transport_attempts=3,
        usage={"input_tokens": 7, "output_tokens": 2},
    )
    assert AssistantProductionWorker._runtime_usage(value) == {
        "model_requests": 1,
        "tool_calls": 0,
        "transport_attempts": 3,
        "input_tokens": 7,
        "output_tokens": 2,
    }


def test_trace_capture_failure_closes_llm_call_and_emits_failed_event():
    class BrokenInspector:
        def __init__(self):
            self.saved = []
        def capture_pair(self, **_kwargs):
            raise RuntimeError("trace store unavailable")
        def save_llm_call(self, call):
            self.saved.append(call)

    inspector = BrokenInspector()
    events = InMemoryAgentEventStore()
    result = AgentRuntime(
        Provider(), config(), history=[{"role": "user", "content": "hello"}],
        context_inspector=inspector, event_store=events, max_retries=0,
    ).legacy_run_for_tests(run_id="run-trace-failure")
    assert result.status == "FAILED"
    assert result.error["code"] == "TRACE_PERSISTENCE_FAILED"
    assert inspector.saved and inspector.saved[-1].status == "FAILED"
    assert [event.type for event in events.list_after("agent_run", "run-trace-failure", 0)] == ["llm.started", "llm.failed"]


def test_llm_call_terminal_write_failure_attempts_safe_failed_upsert():
    class FailOnceInspector:
        def __init__(self):
            self.saved = []
            self.calls = 0
        def capture_pair(self, **_kwargs):
            return SimpleNamespace(id="logical"), SimpleNamespace(id="provider")
        def save_llm_call(self, call):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("trace write failed")
            self.saved.append(call)

    inspector = FailOnceInspector()
    result = AgentRuntime(
        Provider(response={"text": "private provider response", "usage": {}}),
        config(), history=[{"role": "user", "content": "hello"}],
        context_inspector=inspector, max_retries=0,
    ).legacy_run_for_tests(run_id="run-trace-terminal-write-failure")
    assert result.status == "FAILED"
    assert result.error["code"] == "TRACE_PERSISTENCE_FAILED"
    assert inspector.calls == 2
    assert inspector.saved[-1].status == "FAILED"
    assert inspector.saved[-1].response_summary is None


def test_production_initialization_failures_finish_fenced_claim():
    class FakeStore:
        def __init__(self, *, scope_error=None, fail_finish=False):
            self.scope_error = scope_error
            self.fail_finish = fail_finish
            self.finished = []
        async def claim(self, message):
            claim = SimpleNamespace(
                message=message, run_id="run-init", provider_config=SimpleNamespace(status="ACTIVE"),
                execution_token="exec-1", fencing_token=3,
            )
            return claim, "RUNNING"
        async def load_scope(self, _run_id):
            if self.scope_error:
                raise self.scope_error
            return SimpleNamespace(project_id="project-a", actor_id="actor-a", artifact_ids=(), confirmation_id=None)
        async def finish(self, claim, **kwargs):
            if self.fail_finish:
                raise RuntimeError("finish unavailable")
            self.finished.append((claim, kwargs))
            return True

    class Actor:
        actor_id = "actor-a"
        def can_access_project(self, project_id):
            return project_id == "project-a"
        def model_copy(self, **_kwargs):
            return self

    message = AssistantWorkerMessage("job-init", 1, "dispatch-init")
    cases = [
        (RuntimeError("scope"), None, None),
        (None, RuntimeError("runtime factory"), None),
        (None, None, RuntimeError("services factory")),
    ]
    for scope_error, runtime_error, services_error in cases:
        store = FakeStore(scope_error=scope_error)
        def runtime_factory(**_kwargs):
            if runtime_error:
                raise runtime_error
            return SimpleNamespace(
                provider=SimpleNamespace(complete=lambda **_kwargs: {"text": "ok"}),
                config=SimpleNamespace(url="https://provider.example/v1", model_id="model", key_reference="API_KEY"),
                toolset=None,
            )
        def services_factory(**_kwargs):
            if services_error:
                raise services_error
            return {}
        worker = AssistantProductionWorker(
            session_factory=lambda: None, runtime_factory=runtime_factory,
            services_factory=services_factory, actor_context=Actor(),
        )
        worker.store = store
        result = asyncio.run(worker.execute(message))
        assert result.status.value == "FAILED"
        assert len(store.finished) == 1
        _, finished = store.finished[0]
        assert finished["status"] == "FAILED"
        assert finished["usage"] == {"model_requests": 0, "tool_calls": 0, "transport_attempts": 0}

    store = FakeStore(scope_error=RuntimeError("scope"), fail_finish=True)
    worker = AssistantProductionWorker(session_factory=lambda: None, actor_context=Actor())
    worker.store = store
    with pytest.raises(RuntimeError, match="finish unavailable"):
        asyncio.run(worker.execute(message))


@pytest.mark.parametrize("configuration_status", ["ACTIVE", "RETIRED"])
def test_production_worker_serializes_runtime_result_before_json_finish(configuration_status):
    class FakeStore:
        lease_seconds = 60

        def __init__(self):
            self.finished = []

        async def claim(self, message):
            return SimpleNamespace(
                message=message,
                run_id="run-result-json",
                provider_config=SimpleNamespace(status=configuration_status),
                execution_token="exec-1",
                fencing_token=1,
                agent_attempt_id="attempt-result-json",
            ), "RUNNING"

        async def load_scope(self, _run_id):
            return SimpleNamespace(project_id="project-a", actor_id="actor-a", artifact_ids=(), confirmation_id=None)

        async def finish(self, _claim, **kwargs):
            self.finished.append(kwargs)
            return True

    class Actor:
        actor_id = "actor-a"

        def can_access_project(self, project_id):
            return project_id == "project-a"

        def model_copy(self, **_kwargs):
            return self

    async def run(**_kwargs):
        return RuntimeResult("SUCCEEDED", text="ok", usage={"input_tokens": 1}, provider_requests=1)

    async def run_agent_async(**_kwargs):
        raise AssertionError("readiness probe must not dispatch a provider request")

    runtime = SimpleNamespace(
        provider=SimpleNamespace(run_agent_async=run_agent_async),
        config=SimpleNamespace(url="https://provider.example/v1", model_id="model", key_reference="API_KEY"),
        run=run,
    )
    store = FakeStore()
    worker = AssistantProductionWorker(session_factory=lambda: None, runtime=runtime, actor_context=Actor())
    worker.store = store
    result = asyncio.run(worker.execute(AssistantWorkerMessage("job-result-json", 1, "dispatch-result-json")))
    assert result.status.value == "SUCCEEDED"
    assert store.finished and store.finished[0]["result"] == {
        "status": "SUCCEEDED", "text": "ok", "structured": None,
        "tool_results": [], "usage": {"input_tokens": 1},
        "provider_requests": 1, "tool_calls": 0, "transport_attempts": 0, "error": None,
    }


def test_dpapi_secret_reference_uses_only_injected_resolver(monkeypatch):
    reference = "dpapi:v1:" + "a" * 32
    snapshot = RuntimeConfigSnapshot(
        "https://api.openai.com/v1", "model", reference, "rev-1",
        provider_configuration_id="cfg-1",
    )
    monkeypatch.setenv(reference, "must-not-be-used")
    with pytest.raises(AssistantError):
        _OpenAICompatibleClient()._key(snapshot)

    calls = []
    client = _OpenAICompatibleClient(secret_resolver=lambda value: calls.append(value) or "resolved-secret")
    assert client._key(snapshot) == "resolved-secret"
    assert calls == [reference]


def test_protected_provider_endpoint_policy_uses_exact_normalized_base_endpoint():
    trusted = trusted_provider_url(
        "https://API.SILICONFLOW.CN:443/v1/",
        external_send_policy="approved_endpoint_only",
        approved_endpoints="https://api.siliconflow.cn/v1",
    )
    assert trusted == "https://api.siliconflow.cn/v1"
    for value in (
        "https://api.siliconflow.cn/v1/other",
        "https://api.siliconflow.cn:444/v1",
        "https://api.siliconflow.cn:0/v1",
        "https://api.siliconflow.cn:65536/v1",
        "https://api.siliconflow.cn:invalid/v1",
        "https://api.siliconflow.cn/v2",
    ):
        with pytest.raises(ValueError):
            trusted_provider_url(value, external_send_policy="approved_endpoint_only", approved_endpoints="https://api.siliconflow.cn/v1")
    with pytest.raises(ValueError):
        trusted_provider_url("https://api.siliconflow.cn/v1", external_send_policy="approved_endpoint_only", approved_endpoints=None)
    with pytest.raises(ValueError):
        trusted_provider_url("https://api.siliconflow.cn/v1", external_send_policy="deny_external", approved_endpoints="https://api.siliconflow.cn/v1")


def test_default_adapter_streams_bounded_sse_using_protected_endpoint(monkeypatch):
    reference = "dpapi:v1:" + "a" * 32
    config = RuntimeConfigSnapshot(
        "https://api.siliconflow.cn/v1", "model", reference, "rev",
        external_send_policy="approved_endpoint_only",
        approved_endpoints="https://api.siliconflow.cn/v1",
    )
    calls = []
    monkeypatch.setattr(_OpenAICompatibleClient, "_resolved_public_ips", staticmethod(lambda host, port: ["203.0.113.8"]))

    def fake_request(**kwargs):
        calls.append(kwargs)
        return (
            b'data: {"choices":[{"delta":{"content":"hello"},"finish_reason":null}]}\n\n'
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
            b'data: [DONE]\n\n'
        )

    monkeypatch.setattr(_OpenAICompatibleClient, "_request_once", staticmethod(fake_request))
    client = _OpenAICompatibleClient(
        secret_resolver=lambda _reference: "test-secret",
        approved_endpoints_resolver=lambda: "https://api.siliconflow.cn/v1",
    )
    chunks = list(client.stream(config=config, messages=[{"role": "user", "content": "hello"}], tools=[]))
    assert chunks[0]["text"] == "hello"
    assert chunks[-1]["stream_complete"] is True
    assert calls[0]["path"] == "/v1/chat/completions"


def test_provider_allowlist_revocation_blocks_next_request_before_network(monkeypatch):
    reference = "dpapi:v1:" + "b" * 32
    endpoint = "https://api.siliconflow.cn/v1"
    approved = {"value": endpoint}
    config = RuntimeConfigSnapshot(
        endpoint, "model", reference, "rev",
        external_send_policy="approved_endpoint_only", approved_endpoints=endpoint,
    )
    requests = []
    monkeypatch.setattr(_OpenAICompatibleClient, "_resolved_public_ips", staticmethod(lambda _host, _port: ["203.0.113.8"]))
    monkeypatch.setattr(_OpenAICompatibleClient, "_request_once", staticmethod(lambda **kwargs: requests.append(kwargs) or b'{"choices":[{"message":{"content":"ok"},"finish_reason":"stop"}]}'))
    client = _OpenAICompatibleClient(
        secret_resolver=lambda _reference: "test-key",
        approved_endpoints_resolver=lambda: approved["value"],
    )
    assert client.complete(config=config, messages=[{"role": "user", "content": "hello"}], tools=[])["text"] == "ok"
    approved["value"] = None
    with pytest.raises(ValueError):
        client.complete(config=config, messages=[{"role": "user", "content": "hello"}], tools=[])
    assert len(requests) == 1


def test_llm_configuration_test_probe_runs_text_stream_and_in_memory_tool_only(monkeypatch):
    from chrom_platform.modules.assistant import adapter as adapter_module

    class FakeAdapter:
        def __init__(self, **_kwargs):
            self.tool_request_count = 0

        async def run_agent_async(self, *, messages, tools, validate_tool, execute_tool,
                                  on_event, request_budget, **_kwargs):
            # Offline orchestration fake: one native dispatch per stage, no HTTP.
            request_budget.reserve_request()
            prompt = messages[-1]["content"]
            if tools:
                self.tool_request_count += 1
                arguments = {"value": "chrom-platform-test"}
                await on_event({"type": "tool_call", "tool_call_id": "echo-1",
                                "name": "assistant_test_echo", "arguments": arguments})
                validated = validate_tool("assistant_test_echo", arguments, "echo-1")
                if asyncio.iscoroutine(validated):
                    validated = await validated
                result = execute_tool("assistant_test_echo", validated or arguments, "echo-1")
                if asyncio.iscoroutine(result):
                    result = await result
                return {"text": "echo checked", "tool_results": [result], "tool_calls": 1}
            text = "streamed" if "streaming" in prompt else "connected"
            await on_event({"type": "text_start", "text": text})
            await on_event({"type": "text_end"})
            return {"text": text, "tool_results": [], "tool_calls": 0}

    monkeypatch.setattr(adapter_module, "PydanticAIAdapter", FakeAdapter)
    config = SimpleNamespace(
        id="cfg-test", revision="rev-test", url="https://api.siliconflow.cn/v1",
        model_id="model-test", key_reference="dpapi:v1:" + "c" * 32,
        settings={"external_send_policy": "approved_endpoint_only", "timeout_seconds": 2, "max_retries": 0},
    )
    result = asyncio.run(AssistantLlmConfigurationTestWorker._test_adapter(
        config, secret_resolver=lambda _reference: "test-secret",
        approved_endpoints_resolver=lambda: "https://api.siliconflow.cn/v1",
    ))
    assert result == {
        "status": "SUCCEEDED",
        "checks": {"connectivity": "PASSED", "streaming": "PASSED", "tool_calling": "PASSED", "structured_output": "NOT_SUPPORTED"},
        "error": None,
    }


def test_llm_configuration_test_diagnostic_is_bounded_and_keeps_public_error_generic(monkeypatch, caplog):
    import logging
    from chrom_platform.modules.assistant import adapter as adapter_module
    from chrom_platform.modules.assistant import production_worker as worker_module

    class FakeAdapter:
        def __init__(self, **_kwargs):
            pass

        async def run_agent_async(self, **_kwargs):
            raise RuntimeError("SECRET_SENTINEL provider body and credentials")

    monkeypatch.setattr(adapter_module, "PydanticAIAdapter", FakeAdapter)
    caplog.set_level(logging.WARNING, logger=worker_module.__name__)
    result = asyncio.run(AssistantLlmConfigurationTestWorker._test_adapter(
        SimpleNamespace(
            id="cfg-diagnostic", revision="rev-diagnostic",
            url="https://api.siliconflow.cn/v1", model_id="model",
            key_reference="dpapi:v1:" + "d" * 32,
            settings={"external_send_policy": "approved_endpoint_only", "timeout_seconds": 2, "max_retries": 0},
        ),
        secret_resolver=lambda _reference: "not-used",
        approved_endpoints_resolver=lambda: "https://api.siliconflow.cn/v1",
        job_id="job-diagnostic", test_id="test-diagnostic",
    ))

    assert result["status"] == "FAILED"
    assert result["error"]["code"] == "PROVIDER_TEST_FAILED"
    assert "job_id=job-diagnostic test_id=test-diagnostic stage=connectivity category=UNEXPECTED_EXCEPTION" in caplog.text
    assert "SECRET_SENTINEL" not in caplog.text
    assert "RuntimeError" not in caplog.text


def test_llm_configuration_test_diagnostic_keeps_provider_status_whitelisted(monkeypatch, caplog):
    import logging
    from chrom_platform.modules.assistant import adapter as adapter_module
    from chrom_platform.modules.assistant import production_worker as worker_module

    class FakeAdapter:
        def __init__(self, **_kwargs):
            pass

        async def run_agent_async(self, **_kwargs):
            raise adapter_module.ProviderTransportError("HTTP_4XX", http_status=429, message="SECRET_SENTINEL raw body")

    monkeypatch.setattr(adapter_module, "PydanticAIAdapter", FakeAdapter)
    caplog.set_level(logging.WARNING, logger=worker_module.__name__)
    result = asyncio.run(AssistantLlmConfigurationTestWorker._test_adapter(
        SimpleNamespace(
            id="cfg-status", revision="rev-status",
            url="https://api.siliconflow.cn/v1", model_id="model",
            key_reference="dpapi:v1:" + "e" * 32,
            settings={"external_send_policy": "approved_endpoint_only", "timeout_seconds": 2, "max_retries": 0},
        ),
        secret_resolver=lambda _reference: "not-used",
        approved_endpoints_resolver=lambda: "https://api.siliconflow.cn/v1",
        job_id="job-status", test_id="test-status",
    ))

    assert result["error"]["code"] == "PROVIDER_TEST_FAILED"
    assert "stage=connectivity category=PROVIDER_HTTP_4XX http_status=429" in caplog.text
    assert "SECRET_SENTINEL" not in caplog.text


def test_llm_configuration_test_endpoint_rejection_logs_without_adapter_call(monkeypatch, caplog):
    import logging
    from chrom_platform.modules.assistant import adapter as adapter_module
    from chrom_platform.modules.assistant import production_worker as worker_module

    calls = []

    class FakeAdapter:
        def __init__(self, **_kwargs):
            calls.append("constructed")

        async def run_agent_async(self, **_kwargs):
            raise AssertionError("endpoint policy rejection must happen before adapter dispatch")

    monkeypatch.setattr(adapter_module, "PydanticAIAdapter", FakeAdapter)
    caplog.set_level(logging.WARNING, logger=worker_module.__name__)
    result = asyncio.run(AssistantLlmConfigurationTestWorker._test_adapter(
        SimpleNamespace(
            id="cfg-policy", revision="rev-policy",
            url="https://api.siliconflow.cn/v1", model_id="model",
            key_reference="dpapi:v1:" + "f" * 32,
            settings={"external_send_policy": "approved_endpoint_only", "timeout_seconds": 2, "max_retries": 0},
        ),
        approved_endpoints_resolver=lambda: "https://api.siliconflow.cn/v2",
        job_id="job-policy", test_id="test-policy",
    ))

    assert result["error"]["code"] == "PROVIDER_TEST_FAILED"
    assert calls == []
    assert "stage=endpoint_policy category=ENDPOINT_POLICY_REJECTED http_status=none" in caplog.text


def test_llm_configuration_test_outer_exception_logs_only_safe_category(monkeypatch, caplog):
    import logging
    from chrom_platform.modules.assistant import production_worker as worker_module

    claim = SimpleNamespace(
        message=SimpleNamespace(job_id="job-outer", attempt=1, dispatch_id="dispatch-outer"),
        test_id="test-outer", config=SimpleNamespace(id="cfg-outer"), token="token", fence=1,
    )

    async def claim_job(_message):
        return claim, "RUNNING"

    async def finish_job(_claim, outcome):
        assert outcome["error"]["code"] == "PROVIDER_TEST_FAILED"
        return True

    async def fail_runner(**_kwargs):
        raise RuntimeError("SECRET_SENTINEL raw provider detail")

    worker = AssistantLlmConfigurationTestWorker(session_factory=lambda: None, test_runner=fail_runner)
    worker._claim = claim_job
    worker._finish = finish_job
    caplog.set_level(logging.WARNING, logger=worker_module.__name__)
    result = asyncio.run(worker.execute({"job_id": "job-outer", "attempt": 1, "dispatch_id": "dispatch-outer"}))

    assert result.status.value == "FAILED"
    assert result.error["code"] == "PROVIDER_TEST_FAILED"
    assert "job_id=job-outer test_id=test-outer stage=worker_execution category=UNEXPECTED_EXCEPTION" in caplog.text
    assert "SECRET_SENTINEL" not in caplog.text
    assert "RuntimeError" not in caplog.text


def test_production_worker_persists_truncated_output_as_failed_run_with_usage():
    class FakeStore:
        lease_seconds = 60

        def __init__(self):
            self.finished = []
        async def framework_state(self, _claim, state=None):
            return None if state is None else True
        async def claim(self, message):
            return SimpleNamespace(
                message=message, run_id="run-truncated-worker",
                provider_config=SimpleNamespace(status="ACTIVE"),
                execution_token="exec-truncated", fencing_token=1,
                agent_attempt_id="attempt-truncated",
            ), "RUNNING"
        async def load_scope(self, _run_id):
            return SimpleNamespace(
                project_id="project-a", actor_id="actor-a", artifact_ids=(),
                confirmation_id=None, conversation_id="conversation-a",
            )
        async def finish(self, _claim, **kwargs):
            self.finished.append(kwargs)
            return True

    class Actor:
        actor_id = "actor-a"
        def can_access_project(self, project_id):
            return project_id == "project-a"
        def model_copy(self, **_kwargs):
            return self

    inspector = type("Inspector", (), {
        "capture_pair": lambda self, **_kwargs: (SimpleNamespace(id="logical"), SimpleNamespace(id="provider")),
        "save_llm_call": lambda self, _call: None,
    })()
    runtime_events = InMemoryAgentEventStore()
    class NativeTruncatedAdapter:
        async def run_agent_async(self, *, on_event, **_kwargs):
            # Offline native-adapter protocol fake: trace a failed transport
            # response with usage, then return the provider truncation marker.
            await on_event({"type": "model_request", "request_index": 1,
                            "transport_attempt": 1, "request_sha256": "a" * 64,
                            "model_id": "model-1", "message_count": 2, "tool_names": []})
            await on_event({"type": "model_response", "request_index": 1,
                            "transport_attempt": 1, "status": "FAILED",
                            "error": {"code": "PROVIDER_OUTPUT_TRUNCATED"},
                            "finish_reason": "max_tokens", "usage": {
                                "input_tokens": 13, "output_tokens": 512, "total_tokens": 525,
                            }})
            return {"text": "private unfinished content", "finish_reason": "max_tokens",
                    "usage": {"input_tokens": 13, "output_tokens": 512, "total_tokens": 525}}

    runtime_config = RuntimeConfigSnapshot(
        config().url, config().model_id, config().key_reference, config().config_revision,
        provider_configuration_hash="a" * 64,
    )
    runtime = AgentRuntime(
        NativeTruncatedAdapter(), runtime_config, history=[{"role": "user", "content": "summarize"}],
        context_inspector=inspector, event_store=runtime_events, trace_metadata_only=True,
        max_retries=0,
    )
    store = FakeStore()
    worker = AssistantProductionWorker(session_factory=lambda: None, runtime=runtime, actor_context=Actor())
    worker.store = store

    result = asyncio.run(worker.execute(AssistantWorkerMessage("job-truncated", 1, "dispatch-truncated")))

    assert result.status.value == "FAILED"
    assert store.finished == [{
        "status": "FAILED", "result": None,
        "error": {"code": "PROVIDER_OUTPUT_TRUNCATED", "message": "模型服务输出达到长度上限，结果未提交", "retryable": False},
        "usage": {
            "model_requests": 1, "tool_calls": 0, "transport_attempts": 1,
            "input_tokens": 13, "output_tokens": 512,
        },
    }]
    assert [event.type for event in runtime_events.list_after("agent_run", "run-truncated-worker", 0)] == ["llm.started", "llm.failed"]


def test_runtime_result_projection_is_allowlisted_and_redacted():
    value = RuntimeResult(
        "SUCCEEDED",
        text="safe output",
        structured={"content": "must not persist", "value": 1},
        tool_results=({"result": 1, "token": "secret"},),
        provider_requests=1,
    )
    projected = _runtime_result_json(value)
    assert projected["status"] == "SUCCEEDED"
    assert projected["text"] == "safe output"
    assert projected["structured"] == {"value": 1}
    assert projected["tool_results"] == [{"result": 1}]
    assert _assistant_text_blocks(projected) == [{"type": "text", "text": "safe output"}]
    output_text = _runtime_result_json({"status": "SUCCEEDED", "output_text": "safe output"})
    assert _assistant_text_blocks(output_text) == [{"type": "text", "text": "safe output"}]
    assert "provider" not in projected


def test_finish_persists_one_safe_assistant_final_message_in_same_transaction():
    class Result:
        def scalars(self):
            return self

        def first(self):
            return None

    class Begin:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    class Session:
        def __init__(self):
            self.added = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def begin(self):
            return Begin()

        async def execute(self, _statement):
            return Result()

        async def scalar(self, _statement):
            return None

        def add(self, value):
            self.added.append(value)

    now = datetime.now(timezone.utc)
    job = SimpleNamespace(id="job-finish", project_id="project-finish", owner_id="actor-finish", kind="assistant", resource_ref={"type": "agent_run", "id": "run-finish"}, status="RUNNING", attempt=1, progress={}, cancelable=True, retryable=False, resume_mode="none", created_at=now, started_at=now, deadline_at=None, result_ref=None, error=None, finished_at=None, updated_at=now, revision=1, etag="1", cancel_requested=False)
    attempt = SimpleNamespace(id="attempt-finish", status="RUNNING", execution_token="exec-finish", fencing_token=3, result_ref=None, error=None, finished_at=None, lease_until=now)
    run = SimpleNamespace(id="run-finish", conversation_id="conversation-finish", status="RUNNING", cancel_requested=False, usage={}, error=None, finished_at=None, updated_at=now, lease_until=now, last_event_seq=0)
    agent_attempt = SimpleNamespace(id="agent-attempt-finish", status="RUNNING", error=None, finished_at=None, lease_until=now)
    dispatch = SimpleNamespace()
    session = Session()
    store = AsyncAssistantExecutionStore(lambda: session)

    async def rows(_session, _message):
        return job, attempt, run, agent_attempt, None, dispatch

    async def no_event(*_args):
        return None

    store._rows = rows
    store._job_event = no_event
    store._agent_event = no_event
    claim = SimpleNamespace(
        message=AssistantWorkerMessage("job-finish", 1, "dispatch-finish"),
        run_id=run.id, execution_token="exec-finish", fencing_token=3,
    )
    assert asyncio.run(store.finish(
        claim, status="SUCCEEDED",
        result={"status": "SUCCEEDED", "text": "safe final", "structured": {"content": "secret"}},
        usage={"model_requests": 1},
    )) is True
    messages = [value for value in session.added if getattr(value, "role", None) == "assistant"]
    assert len(messages) == 1
    assert messages[0].status == "FINAL"
    assert messages[0].agent_run_id == run.id
    assert messages[0].blocks == [{"type": "text", "text": "safe final"}]
    assert job.result_ref == {"type": "agent_run", "id": run.id}
    assert attempt.result_ref == job.result_ref
    assert job.error is None and attempt.error is None and run.error is None
    job.result_ref = {"status": "SUCCEEDED", "text": "legacy internal result"}
    job.error = {"code": "INTERNAL_ERROR", "message": "stale"}
    public_job = _job_view(job)
    assert public_job.status.value == "SUCCEEDED"
    assert public_job.result_ref.model_dump() == {"type": "agent_run", "id": run.id}
    assert public_job.error is None
    public_attempt = _attempt_view(SimpleNamespace(
        id=attempt.id, job_id=job.id, attempt=1, status="SUCCEEDED", resume_mode="none",
        checkpoint_ref=None, started_at=now, finished_at=now,
        error={"code": "INTERNAL_ERROR"}, result_ref={"status": "SUCCEEDED", "text": "legacy"},
    ))
    assert public_attempt.error is None and public_attempt.result_ref is None


def test_succeeded_agent_run_projection_clears_stale_internal_error_and_filters_usage():
    now = datetime.now(timezone.utc)
    public_run = _run(SimpleNamespace(
        id="run-projection", conversation_id="conversation-projection", project_id="project-projection",
        input_message_id="message-projection", mode="knowledge", status="SUCCEEDED", attempt=1,
        provider_configuration_id="config-projection", provider_configuration_revision="rev-1",
        provider_configuration_hash=None,
        model_id="model", prompt_version="1", toolset_version="1", policy_version="1",
        pending_actions=[], related_resources=[], usage={"model_requests": 1, "transport_attempts": 1},
        last_event_seq=3, error={"code": "INTERNAL_ERROR"}, started_at=now, finished_at=now,
        created_at=now, updated_at=now, revision=2, etag="2",
    ))
    assert public_run.error is None
    assert public_run.usage.model_requests == 1
    assert not hasattr(public_run.usage, "transport_attempts")


def test_running_agent_run_projection_preserves_absent_error():
    now = datetime.now(timezone.utc)
    public_run = _run(SimpleNamespace(
        id="run-running-projection", conversation_id="conversation-projection", project_id="project-projection",
        input_message_id="message-projection", mode="business", status="RUNNING", attempt=1,
        provider_configuration_id="config-projection", provider_configuration_revision="rev-1",
        provider_configuration_hash=None,
        model_id="model", prompt_version="1", toolset_version="1", policy_version="1",
        pending_actions=[], related_resources=[], usage={}, last_event_seq=3, error=None,
        started_at=now, finished_at=None, created_at=now, updated_at=now, revision=2, etag="2",
    ))
    assert public_run.status == "RUNNING"
    assert public_run.error is None


def test_failed_agent_run_without_error_keeps_safe_fallback():
    now = datetime.now(timezone.utc)
    public_run = _run(SimpleNamespace(
        id="run-failed-projection", conversation_id="conversation-projection", project_id="project-projection",
        input_message_id="message-projection", mode="business", status="FAILED", attempt=1,
        provider_configuration_id="config-projection", provider_configuration_revision="rev-1",
        provider_configuration_hash=None,
        model_id="model", prompt_version="1", toolset_version="1", policy_version="1",
        pending_actions=[], related_resources=[], usage={}, last_event_seq=3, error=None,
        started_at=now, finished_at=now, created_at=now, updated_at=now, revision=2, etag="2",
    ))
    assert public_run.status == "FAILED"
    assert public_run.error.code == "INTERNAL_ERROR"


def test_finalization_reconcile_requires_exact_fence_and_preserves_llm_usage():
    class Result:
        def __init__(self, value=None, values=None, first=None):
            self.value, self.values, self.first_value = value, values or [], first

        def scalar_one_or_none(self):
            return self.value

        def scalars(self):
            return self

        def all(self):
            return list(self.values)

        def first(self):
            return self.first_value

    class Begin:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    class Session:
        def __init__(self, values):
            self.values, self.added = list(values), []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def begin(self):
            return Begin()

        async def execute(self, _statement):
            value = self.values.pop(0)
            return value

        def add(self, value):
            self.added.append(value)

    now = datetime.now(timezone.utc)
    job = SimpleNamespace(id="job-reconcile", project_id="project-reconcile", status="RUNNING", attempt=1, resource_ref={"type": "agent_run", "id": "run-reconcile"}, error=None, result_ref=None, finished_at=None, updated_at=now, revision=2, etag="2")
    job_attempt = SimpleNamespace(job_id=job.id, attempt=1, status="RUNNING", execution_token="exec-reconcile", fencing_token=7, error=None, result_ref=None, finished_at=None, lease_until=now)
    run = SimpleNamespace(id="run-reconcile", project_id="project-reconcile", conversation_id="conversation-reconcile", provider_configuration_id="config-reconcile", provider_configuration_revision="rev-1", fencing_token=7, status="RUNNING", attempt=1, usage={}, error=None, finished_at=None, updated_at=now, lease_until=now, last_event_seq=1)
    agent_attempt = SimpleNamespace(id="agent-attempt-reconcile", agent_run_id=run.id, attempt=1, execution_token="exec-reconcile", fencing_token=7, status="RUNNING", error=None, finished_at=None, lease_until=now)
    call = SimpleNamespace(id="llm-reconcile", agent_run_id=run.id, agent_attempt_id=agent_attempt.id, provider_configuration_id=run.provider_configuration_id, provider_configuration_revision=run.provider_configuration_revision, status="SUCCEEDED", transport_attempt=1, usage={"input_tokens": 7175, "output_tokens": 31})
    conversation = SimpleNamespace(id=run.conversation_id, project_id=run.project_id, owner_id="actor-reconcile")
    session = Session([
        Result(value=job), Result(value=job_attempt), Result(value=run),
        Result(value=agent_attempt), Result(value=call), Result(value=conversation), Result(first=None),
        Result(value=run), Result(values=[]),
    ])
    dry_session = Session([
        Result(value=job), Result(value=job_attempt), Result(value=run),
        Result(value=agent_attempt), Result(value=call), Result(value=conversation),
    ])
    dry_run = asyncio.run(AsyncAssistantExecutionStore(lambda: dry_session).reconcile_finalization_failure(
        run_id=run.id, job_id=job.id, attempt=1,
        agent_attempt_id=agent_attempt.id, llm_call_id=call.id,
        execution_token="exec-reconcile", fencing_token=7,
        project_id="project-reconcile", actor_id="actor-reconcile",
    ))
    assert dry_run["matched"] is True and dry_run["applied"] is False
    assert run.status == job.status == agent_attempt.status == job_attempt.status == "RUNNING"
    assert dry_session.added == []
    store = AsyncAssistantExecutionStore(lambda: session)
    reconciled = asyncio.run(store.reconcile_finalization_failure(
        run_id=run.id, job_id=job.id, attempt=1,
        agent_attempt_id=agent_attempt.id, llm_call_id=call.id,
        execution_token="exec-reconcile", fencing_token=7,
        project_id="project-reconcile", actor_id="actor-reconcile", apply=True,
    ))
    assert reconciled["applied"] is True
    assert run.status == job.status == agent_attempt.status == job_attempt.status == "FAILED"
    assert run.error["code"] == "FINALIZATION_PERSISTENCE_FAILED"
    assert run.usage["model_requests"] == 1
    assert run.usage["input_tokens"] == 7175 and run.usage["output_tokens"] == 31
    assert call.status == "SUCCEEDED"
    audits = [item for item in session.added if item.__class__.__name__ == "AuditEvent"]
    assert len(audits) == 1
    assert audits[0].project_id == "project-reconcile"
    assert audits[0].actor_id == "actor-reconcile"
    assert audits[0].after_summary["error_code"] == "FINALIZATION_PERSISTENCE_FAILED"
    assert any(getattr(item, "type", None) == "job.failed" for item in session.added)
    assert any(getattr(item, "type", None) == "run.failed" for item in session.added)
    repeat_session = Session([
        Result(value=job), Result(value=job_attempt), Result(value=run),
        Result(value=agent_attempt), Result(value=call), Result(value=conversation),
    ])
    repeated = asyncio.run(AsyncAssistantExecutionStore(lambda: repeat_session).reconcile_finalization_failure(
        run_id=run.id, job_id=job.id, attempt=1,
        agent_attempt_id=agent_attempt.id, llm_call_id=call.id,
        execution_token="exec-reconcile", fencing_token=7,
        project_id="project-reconcile", actor_id="actor-reconcile", apply=True,
    ))
    assert repeated["idempotent"] is True
    assert repeat_session.added == []


def test_finalization_reconcile_rejects_scope_or_fence_mismatch_without_write():
    class Result:
        def __init__(self, value=None):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    class Begin:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    class Session:
        def __init__(self, rows):
            self.rows, self.added = list(rows), []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def begin(self):
            return Begin()

        async def execute(self, _statement):
            return Result(self.rows.pop(0))

        def add(self, value):
            self.added.append(value)

    def make_rows():
        now = datetime.now(timezone.utc)
        job = SimpleNamespace(id="job-guard", project_id="project-guard", status="RUNNING", attempt=1, resource_ref={"type": "agent_run", "id": "run-guard"})
        attempt = SimpleNamespace(job_id=job.id, attempt=1, status="RUNNING", execution_token="exec-guard", fencing_token=9)
        run = SimpleNamespace(id="run-guard", project_id="project-guard", conversation_id="conversation-guard", provider_configuration_id=None, provider_configuration_revision=None, fencing_token=9, status="RUNNING", attempt=1, usage={}, lease_until=now)
        agent_attempt = SimpleNamespace(id="agent-guard", agent_run_id=run.id, attempt=1, execution_token="exec-guard", fencing_token=9, status="RUNNING")
        call = SimpleNamespace(id="llm-guard", agent_run_id=run.id, agent_attempt_id=agent_attempt.id, provider_configuration_id=None, provider_configuration_revision=None, status="SUCCEEDED", transport_attempt=1, usage={})
        conversation = SimpleNamespace(id=run.conversation_id, project_id=run.project_id, owner_id="actor-guard")
        return [job, attempt, run, agent_attempt, call, conversation]

    async def invoke(rows, **overrides):
        session = Session(rows)
        store = AsyncAssistantExecutionStore(lambda: session)
        values = {
            "run_id": "run-guard", "job_id": "job-guard", "attempt": 1,
            "agent_attempt_id": "agent-guard", "llm_call_id": "llm-guard",
            "execution_token": "exec-guard", "fencing_token": 9,
            "project_id": "project-guard", "actor_id": "actor-guard", "apply": True,
        }
        values.update(overrides)
        return await store.reconcile_finalization_failure(**values), session

    wrong_token, token_session = asyncio.run(invoke(make_rows(), execution_token="wrong"))
    wrong_project, project_session = asyncio.run(invoke(make_rows(), project_id="wrong-project"))
    wrong_id, id_session = asyncio.run(invoke(make_rows(), llm_call_id="wrong-llm"))
    for result, session in ((wrong_token, token_session), (wrong_project, project_session), (wrong_id, id_session)):
        assert result["matched"] is False
        assert result["applied"] is False
        assert session.added == []


@pytest.mark.parametrize(
    ("status", "code", "expected_code", "expected_attempts"),
    [(400, "invalid_request_error", "PROVIDER_REQUEST_REJECTED", 1),
     (429, "rate_limit_exceeded", "PROVIDER_RATE_LIMITED", 3),
     (500, "server_error", "PROVIDER_UNAVAILABLE", 3)],
)
def test_provider_http_retry_policy_is_bounded(status, code, expected_code, expected_attempts):
    provider = Provider(error=ProviderTransportError("HTTP_4XX" if status < 500 else "HTTP_5XX", http_status=status, provider_error_code=code))
    result = AgentRuntime(provider, config(), history=[{"role": "user", "content": "hello"}], max_retries=2).legacy_run_for_tests(run_id="run-http-policy")
    assert result.error["code"] == expected_code
    assert len(provider.messages) == expected_attempts
    assert "provider" not in result.error


def test_default_adapter_reports_response_schema_without_body(monkeypatch):
    monkeypatch.setenv("API_KEY", "process-only-secret")
    monkeypatch.setattr(_OpenAICompatibleClient, "_resolved_public_ips", staticmethod(lambda *_args: ["198.51.100.10"]))
    monkeypatch.setattr(_OpenAICompatibleClient, "_request_once", staticmethod(lambda **_kwargs: b'{"unexpected":"shape"}'))
    with pytest.raises(ProviderResponseError) as failure:
        PydanticAIAdapter().complete(
            config=RuntimeConfigSnapshot("https://api.openai.com/v1", "model-1", "API_KEY", "rev-1"),
            messages=[{"role": "user", "content": "hello"}], tools=[], timeout_seconds=4,
        )
    assert failure.value.category == "RESPONSE_SCHEMA"
    assert "unexpected" not in str(failure.value)


def test_default_adapter_rejects_private_dns_answer_and_redirect(monkeypatch):
    monkeypatch.setenv("API_KEY", "process-only-secret")
    monkeypatch.setattr(adapter_module.socket, "getaddrinfo", lambda *_args, **_kwargs: [
        (adapter_module.socket.AF_INET, adapter_module.socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
    ])
    with pytest.raises(ConnectionError):
        _OpenAICompatibleClient()._resolved_public_ips("api.openai.com", 443)

    class FakeContext:
        def wrap_socket(self, raw, server_hostname):
            return raw
    class FakeResponse:
        status = 302
    class FakeConnection:
        def __init__(self, *_args, **_kwargs):
            pass
        def request(self, *_args, **_kwargs):
            pass
        def getresponse(self):
            return FakeResponse()
        def close(self):
            pass
    monkeypatch.setattr(adapter_module.ssl, "create_default_context", lambda: FakeContext())
    monkeypatch.setattr(adapter_module.socket, "create_connection", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(adapter_module.http.client, "HTTPSConnection", FakeConnection)
    with pytest.raises(ConnectionError, match="禁止重定向"):
        _OpenAICompatibleClient._request_once(
            hostname="api.openai.com", address="198.51.100.10", port=443,
            path="/v1/chat/completions", body=b"{}", headers={}, timeout=1,
        )


@pytest.mark.parametrize(
    ("tls_error", "retryable"),
    [(adapter_module.ssl.SSLCertVerificationError("hostname mismatch"), False),
     (adapter_module.ssl.SSLError("UNEXPECTED_EOF_WHILE_READING"), True)],
)
def test_tls_certificate_failure_is_not_retried_but_transient_eof_is(tls_error, retryable, monkeypatch):
    class FakeRaw:
        def close(self):
            pass
    class FakeContext:
        def wrap_socket(self, _raw, server_hostname):
            raise tls_error
    monkeypatch.setattr(adapter_module.ssl, "create_default_context", lambda: FakeContext())
    monkeypatch.setattr(adapter_module.socket, "create_connection", lambda *_args, **_kwargs: FakeRaw())
    with pytest.raises(ProviderTransportError) as failure:
        _OpenAICompatibleClient._request_once(
            hostname="api.openai.com", address="198.51.100.10", port=443,
            path="/v1/chat/completions", body=b"{}", headers={}, timeout=1,
        )
    assert failure.value.category == "TLS"
    assert failure.value.retryable is retryable


def test_runtime_clamps_provider_retries_to_safe_bound():
    assert AgentRuntime(Provider(), config(), history=[{"role": "user", "content": "hello"}], max_retries=100).max_retries == 5
    assert AgentRuntime(Provider(), config(), history=[{"role": "user", "content": "hello"}], max_retries=-1).max_retries == 0
