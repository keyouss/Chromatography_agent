from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from chrom_platform.modules.assistant import adapter as adapter_module
from chrom_platform.modules.assistant.adapter import PydanticAIAdapter, ProviderRequestBudget, complete_deferred_history
from chrom_platform.modules.assistant.runtime import RuntimeConfigSnapshot
from chrom_platform.modules.assistant.tools import ToolPending


def _config() -> RuntimeConfigSnapshot:
    return RuntimeConfigSnapshot(
        url="https://api.openai.com/v1",
        model_id="offline-test-model",
        key_reference="dpapi:v1:test",
        config_revision="cfg-1",
    )


class _TestTransport:
    def activate(self, *_args: Any) -> object:
        return object()

    def deactivate(self, _token: object) -> None:
        return None


class _TestSDKClient:
    async def close(self) -> None:
        return None


def _patch_function_model(monkeypatch: pytest.MonkeyPatch, model_fn: Any) -> None:
    from openai import AsyncOpenAI
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel
    from pydantic_ai.messages import TextPart, ToolCallPart
    import pydantic_ai.models.openai as openai_models

    async def stream_model(messages: list[Any], info: Any):
        response = model_fn(messages, info)
        tool_index = 0
        for part in response.parts:
            if isinstance(part, TextPart):
                yield part.content
            elif isinstance(part, ToolCallPart):
                args = part.args
                if not isinstance(args, str):
                    import json
                    args = json.dumps(args, ensure_ascii=False)
                yield {tool_index: DeltaToolCall(name=part.tool_name, json_args=args, tool_call_id=part.tool_call_id)}
                tool_index += 1

    # Keep Agent, OpenAIProvider and FunctionModel real; replace only the provider model selection.
    class _FunctionModelFactory:
        def __new__(cls, _name: str, *, provider: object) -> FunctionModel:
            assert provider is not None
            return FunctionModel(stream_function=stream_model, model_name="offline-function-model")

    monkeypatch.setattr(openai_models, "OpenAIChatModel", _FunctionModelFactory)
    sdk_client = AsyncOpenAI(api_key="offline-placeholder", base_url="https://api.openai.com/v1")
    monkeypatch.setattr(adapter_module, "_test_sdk_client", sdk_client, raising=False)
    monkeypatch.setattr(adapter_module, "_native_openai_client", lambda *_args: (_TestTransport(), sdk_client))


def test_native_agent_executes_schema_tool_and_emits_native_text_events(monkeypatch: pytest.MonkeyPatch) -> None:
    asyncio.run(_test_native_agent_executes_schema_tool_and_emits_native_text_events(monkeypatch))


async def _test_native_agent_executes_schema_tool_and_emits_native_text_events(monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart

    calls = 0

    def model_fn(messages: list[Any], _info: Any) -> ModelResponse:
        nonlocal calls
        calls += 1
        if any(isinstance(part, ToolReturnPart) for message in messages for part in getattr(message, "parts", [])):
            return ModelResponse(parts=[TextPart("工具结果已读取")])
        return ModelResponse(parts=[ToolCallPart("lookup", {"query": "SFC"}, tool_call_id="provider-call-1")])

    _patch_function_model(monkeypatch, model_fn)
    events: list[dict[str, Any]] = []
    validated: list[dict[str, Any]] = []
    executed: list[tuple[str, dict[str, Any], str]] = []

    async def validate(name: str, args: dict[str, Any], call_id: str) -> dict[str, Any]:
        validated.append({"name": name, **args, "call_id": call_id})
        return args

    async def execute(name: str, args: dict[str, Any], call_id: str) -> dict[str, Any]:
        executed.append((name, args, call_id))
        return {"matches": 1}

    result = await PydanticAIAdapter().run_agent_async(
        config=_config(),
        messages=[{"role": "system", "content": "只读助手"}, {"role": "user", "content": "查SFC"}],
        tools=[{"name": "lookup", "description": "lookup", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"], "additionalProperties": False}}],
        validate_tool=validate,
        execute_tool=execute,
        on_event=events.append,
        history_state=None,
        request_budget=ProviderRequestBudget(deadline_monotonic=10**12, max_requests=5),
    )

    assert calls == 2
    assert validated == [{"name": "lookup", "query": "SFC", "call_id": "provider-call-1"}]
    assert executed == [("lookup", {"query": "SFC"}, "provider-call-1")]
    assert result["text"] == "工具结果已读取"
    assert result["framework_history"] == "pydantic-ai"
    assert result["history_state"]["serialization"] == "ModelMessagesTypeAdapter"
    assert [event["type"] for event in events if event["type"] in {"tool_call", "tool_result"}] == ["tool_call", "tool_result"]
    assert any(event["type"] in {"text_start", "text_delta"} and event["text"] == "工具结果已读取" for event in events)
    await result_client_cleanup(monkeypatch)


def test_deferred_tool_stops_agent_and_history_completion_is_memory_only(monkeypatch: pytest.MonkeyPatch) -> None:
    asyncio.run(_test_deferred_tool_stops_agent_and_history_completion_is_memory_only(monkeypatch))


async def _test_deferred_tool_stops_agent_and_history_completion_is_memory_only(monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart

    calls = 0

    def model_fn(_messages: list[Any], _info: Any) -> ModelResponse:
        nonlocal calls
        calls += 1
        return ModelResponse(parts=[ToolCallPart("submit", {"draft_id": "draft-1"}, tool_call_id="provider-pending-1")])

    _patch_function_model(monkeypatch, model_fn)

    async def execute(_name: str, _args: dict[str, Any], _call_id: str) -> ToolPending:
        return ToolPending("submit", "batch.submit", {"draft_id": "draft-1"}, "a" * 64, revision=2, config_hash="b" * 64)

    result = await PydanticAIAdapter().run_agent_async(
        config=_config(),
        messages=[{"role": "user", "content": "提交草稿"}],
        tools=[{"name": "submit", "description": "submit", "parameters": {"type": "object", "properties": {"draft_id": {"type": "string"}}, "required": ["draft_id"], "additionalProperties": False}}],
        validate_tool=lambda *_args: None,
        execute_tool=execute,
        on_event=None,
        history_state=None,
        request_budget=ProviderRequestBudget(deadline_monotonic=10**12, max_requests=5),
    )

    assert calls == 1
    assert result["pending"] is True
    assert result["pending_actions"][0]["tool_call_id"] == "provider-pending-1"
    updated = complete_deferred_history(result["history_state"], "provider-pending-1", {"status": "SUCCEEDED"})
    assert updated["pending_actions"] == []
    native = adapter_module.importlib.import_module("pydantic_ai.messages").ModelMessagesTypeAdapter.validate_python(updated["messages"])
    returns = [part for message in native for part in getattr(message, "parts", []) if isinstance(part, ToolReturnPart)]
    assert len(returns) == 1
    assert returns[0].tool_call_id == "provider-pending-1"
    assert "SUCCEEDED" in returns[0].content
    with pytest.raises(Exception, match="完成或不匹配"):
        complete_deferred_history(updated, "provider-pending-1", {"status": "SUCCEEDED"})
    unsupported_version = dict(result["history_state"], framework_version="0.0.0")
    with pytest.raises(Exception, match="框架版本不支持"):
        adapter_module.validate_native_history_state(unsupported_version)
    multiple_pending = dict(result["history_state"], pending_actions=[
        result["history_state"]["pending_actions"][0],
        {"tool_call_id": "provider-pending-2", "action": "batch.submit"},
    ])
    with pytest.raises(adapter_module.AssistantError, match="待确认状态无效"):
        adapter_module.validate_native_history_state(multiple_pending)
    with pytest.raises(adapter_module.AssistantError, match="待确认状态无效"):
        complete_deferred_history(multiple_pending, "provider-pending-1", {"status": "SUCCEEDED"})
    await result_client_cleanup(monkeypatch)


def test_multiple_deferred_calls_fail_without_dispatching_a_second_pending_action(monkeypatch: pytest.MonkeyPatch) -> None:
    asyncio.run(_test_multiple_deferred_calls_fail_without_dispatching_a_second_pending_action(monkeypatch))


async def _test_multiple_deferred_calls_fail_without_dispatching_a_second_pending_action(monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic_ai.messages import ModelResponse, ToolCallPart

    def model_fn(_messages: list[Any], _info: Any) -> ModelResponse:
        return ModelResponse(parts=[
            ToolCallPart("submit", {"draft_id": "draft-1"}, tool_call_id="provider-pending-1"),
            ToolCallPart("submit", {"draft_id": "draft-2"}, tool_call_id="provider-pending-2"),
        ])

    _patch_function_model(monkeypatch, model_fn)
    executed: list[str] = []

    async def execute(_name: str, _args: dict[str, Any], call_id: str) -> ToolPending:
        executed.append(call_id)
        return ToolPending("submit", "batch.submit", {"draft_id": "draft-1"}, "a" * 64, revision=2, config_hash="b" * 64)

    events: list[dict[str, Any]] = []
    with pytest.raises(adapter_module.AssistantError) as error:
        await PydanticAIAdapter().run_agent_async(
            config=_config(),
            messages=[{"role": "user", "content": "提交两个草稿"}],
            tools=[{"name": "submit", "description": "submit", "parameters": {"type": "object", "properties": {"draft_id": {"type": "string"}}, "required": ["draft_id"], "additionalProperties": False}}],
            validate_tool=lambda *_args: None,
            execute_tool=execute,
            on_event=events.append,
            history_state=None,
            request_budget=ProviderRequestBudget(deadline_monotonic=10**12, max_requests=5),
        )
    assert error.value.payload.code == "UNSUPPORTED_MULTIPLE_PENDING"
    # The first callback only prepares a draft and returns ToolPending; the
    # second request is rejected before its business callback can run.
    assert executed == ["provider-pending-1"]
    assert [event["tool_call_id"] for event in events if event["type"] == "tool_call"] == [
        "provider-pending-1", "provider-pending-2",
    ]
    await result_client_cleanup(monkeypatch)


async def result_client_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    sdk = getattr(adapter_module, "_test_sdk_client", None)
    if sdk is not None:
        await sdk.close()


@pytest.mark.parametrize("case", [
    "same_response_replace_after_final_signal",
    "next_response_reuses_index_zero",
    "next_response_repeats_prior_prefix",
])
def test_typed_text_parts_replace_only_within_their_model_response(monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    asyncio.run(_test_typed_text_parts_replace_only_within_their_model_response(monkeypatch, case))


async def _test_typed_text_parts_replace_only_within_their_model_response(
    monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    from types import SimpleNamespace

    import pydantic_ai
    from pydantic_ai.messages import (
        FinalResultEvent,
        FunctionToolCallEvent,
        FunctionToolResultEvent,
        PartEndEvent,
        PartStartEvent,
        TextPart,
        ToolCallPart,
        ToolReturnPart,
    )
    from pydantic_ai.run import AgentRunResultEvent

    old_part, new_part = TextPart(content="old"), TextPart(content="new")
    if case == "same_response_replace_after_final_signal":
        # FinalResultEvent and PartEndEvent do not end the model response.
        scripted_events = [
            PartStartEvent(index=0, part=old_part),
            FinalResultEvent(tool_name=None, tool_call_id=None),
            PartEndEvent(index=0, part=old_part),
            PartStartEvent(index=0, part=new_part),
        ]
        expected_text_events = [
            ("text_start", "old"), ("text_end", None), ("replacement", "new"), ("text_end", None),
        ]
    else:
        next_response_text = "oldnew" if case == "next_response_repeats_prior_prefix" else "new"
        scripted_events = [
            PartStartEvent(index=0, part=old_part),
            PartEndEvent(index=0, part=old_part),
            FunctionToolCallEvent(part=ToolCallPart("lookup", {}, tool_call_id="call-1")),
            FunctionToolResultEvent(part=ToolReturnPart("lookup", "call-1", "done")),
            PartStartEvent(index=0, part=TextPart(content=next_response_text)),
        ]
        suffix = "oldnew" if case == "next_response_repeats_prior_prefix" else "new"
        expected_text_events = [("text_start", "old"), ("text_end", None), ("text_start", suffix), ("text_end", None)]

    class _RunResult:
        output = "oldnew" if case == "next_response_repeats_prior_prefix" else "new"
        usage = SimpleNamespace(requests=2, input_tokens=1, output_tokens=1, total_tokens=2)
        finish_reason = "stop"

        def all_messages(self) -> list[Any]:
            return []

    class _EventHandle:
        def __init__(self) -> None:
            self.index = 0

        async def __aenter__(self) -> "_EventHandle":
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        def __aiter__(self) -> "_EventHandle":
            return self

        async def __anext__(self) -> Any:
            if self.index < len(scripted_events):
                event = scripted_events[self.index]
                self.index += 1
                return event
            if self.index == len(scripted_events):
                self.index += 1
                return AgentRunResultEvent(result=_RunResult())
            raise StopAsyncIteration

    class _EventAgent:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def run_stream_events(self, **_kwargs: Any) -> _EventHandle:
            return _EventHandle()

    _patch_function_model(monkeypatch, lambda *_args: None)
    monkeypatch.setattr(pydantic_ai, "Agent", _EventAgent)
    captured: list[dict[str, Any]] = []
    result = await PydanticAIAdapter().run_agent_async(
        config=_config(),
        messages=[{"role": "user", "content": "typed part test"}],
        tools=[],
        execute_tool=lambda *_args: None,
        validate_tool=None,
        on_event=lambda event: captured.append(dict(event)),
        history_state=None,
        request_budget=ProviderRequestBudget(deadline_monotonic=10**12, max_requests=4),
    )

    actual_text_events = [
        (event["type"], event.get("text")) for event in captured
        if event.get("type") in {"text_start", "text_delta", "text_end", "replacement"}
    ]
    assert actual_text_events == expected_text_events
    expected_result = {
        "same_response_replace_after_final_signal": "new",
        "next_response_reuses_index_zero": "oldnew",
        "next_response_repeats_prior_prefix": "oldoldnew",
    }[case]
    assert result["text"] == expected_result
    await result_client_cleanup(monkeypatch)


def test_native_transport_pins_ip_preserves_tls_hostname_and_bounds_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    asyncio.run(_test_native_transport_pins_ip_preserves_tls_hostname_and_bounds_retry(monkeypatch))


async def _test_native_transport_pins_ip_preserves_tls_hostname_and_bounds_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    import ssl

    import httpcore

    responses = [
        b'{"error":{"code":"server_error"}}',
        b'data: {"choices":[{"index":0,"delta":{"role":"assistant","content":"ok"}}]}\n\n'
        b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
        b'data: {"choices":[],"usage":{"prompt_tokens":2,"completion_tokens":1,"total_tokens":3}}\n\n'
        b"data: [DONE]\n\n",
        b"data: [DONE]\n\n",
        b'data: {"choices":[{"index":0,"delta":{"role":"assistant","content":"later"}}]}\n\n'
        b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
        b'data: [DONE]\n\n',
        b'data: {"choices":[{"index":0,"delta":{"role":"assistant","content":"closed"}}]}\n\n'
        b'data: [DONE]\n\n',
        b'data: {"choices":[{"index":0,"delta":{"role":"assistant","content":"cancel"}}]}\n\n'
        b'data: [DONE]\n\n',
        b"data: [DONE]\n\n",
    ]
    connections: list[str] = []
    tls_hosts: list[str | None] = []
    tls_contexts: list[ssl.SSLContext] = []
    dispatched: list[bytes] = []
    response_bodies: list[bytes] = []
    connection_close_calls: list[bool] = []
    cancel_eof_waiting, release_cancel_eof = asyncio.Event(), asyncio.Event()

    class _MemoryStream:
        def __init__(self, body: bytes) -> None:
            content_type = b"application/json" if b'"error"' in body else b"text/event-stream"
            self.response = (
                b"HTTP/1.1 " + (b"503 Service Unavailable" if b'"error"' in body else b"200 OK") + b"\r\n"
                b"content-type: " + content_type + b"\r\n"
                b"content-length: " + str(len(body)).encode() + b"\r\n"
                b"connection: close\r\n\r\n" + body
            )
            self.offset = 0
            self.block_eof = b'"content":"cancel"' in body
            self.block_boundary = len(self.response) - len(b"data: [DONE]\n\n")

        async def write(self, buffer: bytes, timeout: float | None = None) -> None:
            del timeout
            if buffer.startswith((b"POST ", b"GET ")):
                dispatched.append(buffer)

        async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
            del timeout
            if self.block_eof and self.offset >= self.block_boundary:
                self.block_eof = False
                cancel_eof_waiting.set()
                await release_cancel_eof.wait()
            end = min(self.offset + max_bytes, self.block_boundary) if self.block_eof else self.offset + max_bytes
            chunk = self.response[self.offset:end]
            self.offset += len(chunk)
            return chunk

        async def start_tls(self, context: ssl.SSLContext, server_hostname: str | None = None,
                            timeout: float | None = None) -> "_MemoryStream":
            del timeout
            tls_contexts.append(context)
            tls_hosts.append(server_hostname)
            return self

        def get_extra_info(self, info: str) -> Any:
            del info
            return None

        async def aclose(self) -> None:
            connection_close_calls.append(True)
            return None

    class _OfflineBackend:
        async def connect_tcp(self, host: str, port: int, **kwargs: Any) -> _MemoryStream:
            del port, kwargs
            connections.append(host)
            body = responses.pop(0)
            response_bodies.append(body)
            return _MemoryStream(body)

        async def connect_unix_socket(self, *_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("unix socket is not permitted")

        async def sleep(self, seconds: float) -> None:
            await asyncio.sleep(seconds)

    monkeypatch.setattr(httpcore, "AnyIOBackend", _OfflineBackend)
    monkeypatch.setattr(
        adapter_module._OpenAICompatibleClient, "_resolved_public_ips",
        staticmethod(lambda _host, _port: ["203.0.113.19"]),
    )
    adapter = PydanticAIAdapter(secret_resolver=lambda _ref: "offline-secret")
    transport, client = adapter_module._native_openai_client(adapter, _config())
    events: list[dict[str, Any]] = []
    clock_calls: list[str] = []

    def capture_utc() -> str:
        clock_calls.append("captured")
        return f"2026-10-15T12:00:00.{len(clock_calls):06d}Z"

    monkeypatch.setattr(adapter_module, "_utc_iso_now", capture_utc)
    budget = ProviderRequestBudget(deadline_monotonic=10**12, max_requests=2)
    token = transport.activate(budget, events.append, 5.0, 1)
    try:
        answer = await client.chat.completions.create(
            model="offline-test-model", messages=[{"role": "user", "content": "test"}], stream=True,
        )
        chunks = [chunk async for chunk in answer]
        assert chunks[0].choices[0].delta.content == "ok"
    finally:
        transport.deactivate(token)
        await adapter_module.close_native_clients()

    assert connections == ["203.0.113.19", "203.0.113.19"]
    assert tls_hosts == ["api.openai.com", "api.openai.com"]
    assert all(context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname for context in tls_contexts)
    assert len(dispatched) == 2
    assert budget.requests == 2
    request_events = [event for event in events if event["type"] == "model_request"]
    response_events = [event for event in events if event["type"] == "model_response"]
    assert [(event["request_index"], event["transport_attempt"]) for event in request_events] == [(1, 1), (1, 2)]
    assert [(event["request_index"], event["transport_attempt"]) for event in response_events] == [(1, 1), (1, 2)]
    assert response_events[0]["status"] == 503 and response_events[0]["error"]["category"] == "HTTP_5XX"
    assert response_events[1]["status"] == 200 and response_events[1]["finish_reason"] == "stop"
    assert response_events[1]["receive_complete_at"] == "2026-10-15T12:00:00.000001Z"
    assert response_events[1]["response_end_kind"] == "EOF"

    transport, client = adapter_module._native_openai_client(adapter, _config())
    blocked_budget = ProviderRequestBudget(deadline_monotonic=10**12, max_requests=1)
    callback_entered, release_callback = asyncio.Event(), asyncio.Event()
    blocked_events: list[dict[str, Any]] = []

    async def block_model_request(event: dict[str, Any]) -> None:
        blocked_events.append(event)
        if event["type"] == "model_request":
            callback_entered.set()
            await release_callback.wait()

    token = transport.activate(blocked_budget, block_model_request, 5.0, 0)
    try:
        task = asyncio.create_task(client.chat.completions.create(
            model="offline-test-model", messages=[{"role": "user", "content": "sealed"}], stream=True,
        ))
        await callback_entered.wait()
        blocked_budget.seal()
        release_callback.set()
        with pytest.raises(Exception):
            await task
    finally:
        transport.deactivate(token)
        await adapter_module.close_native_clients()

    assert blocked_budget.requests == 0
    assert len(dispatched) == 2
    assert any(event["type"] == "model_response" and event.get("error", {}).get("category") == "TIMEOUT" for event in blocked_events)

    transport, client = adapter_module._native_openai_client(adapter, _config())
    delayed_budget = ProviderRequestBudget(deadline_monotonic=10**12, max_requests=1)
    callback_entered, release_callback = asyncio.Event(), asyncio.Event()
    delayed_events: list[dict[str, Any]] = []
    clock_count_at_callback: list[int] = []

    async def delayed_response_callback(event: dict[str, Any]) -> None:
        if event["type"] == "model_response":
            delayed_events.append(dict(event))
            clock_count_at_callback.append(len(clock_calls))
            callback_entered.set()
            await release_callback.wait()

    token = transport.activate(delayed_budget, delayed_response_callback, 5.0, 0)
    try:
        async def consume_delayed_stream() -> list[Any]:
            stream = await client.chat.completions.create(
                model="offline-test-model", messages=[{"role": "user", "content": "delayed callback"}], stream=True,
            )
            return [chunk async for chunk in stream]

        delayed_task = asyncio.create_task(consume_delayed_stream())
        await asyncio.wait_for(callback_entered.wait(), timeout=2)
        assert delayed_events[0]["response_end_kind"] == "EOF"
        assert delayed_events[0]["receive_complete_at"] == "2026-10-15T12:00:00.000002Z"
        assert clock_count_at_callback == [2]
        release_callback.set()
        delayed_chunks = await asyncio.wait_for(delayed_task, timeout=2)
        assert delayed_chunks[0].choices[0].delta.content == "later"
    finally:
        release_callback.set()
        transport.deactivate(token)

    transport, client = adapter_module._native_openai_client(adapter, _config())
    early_budget = ProviderRequestBudget(deadline_monotonic=10**12, max_requests=1)
    close_error_events: list[dict[str, Any]] = []

    async def fail_early_report(event: dict[str, Any]) -> None:
        close_error_events.append(dict(event))
        if event["type"] == "model_response":
            raise RuntimeError("offline callback failure")

    token = transport.activate(early_budget, fail_early_report, 5.0, 0)
    close_count_before = len(connection_close_calls)
    try:
        early_stream = await client.chat.completions.create(
            model="offline-test-model", messages=[{"role": "user", "content": "early close"}], stream=True,
        )
        with pytest.raises(RuntimeError, match="offline callback failure"):
            await early_stream.aclose()
    finally:
        transport.deactivate(token)

    early_response = next(event for event in close_error_events if event["type"] == "model_response")
    assert early_response["receive_complete_at"] is None
    assert early_response["response_end_kind"] == "CLOSED_EARLY"
    assert early_response["error"]["category"] == "STREAM_INTERRUPTED"
    assert len(connection_close_calls) > close_count_before
    assert early_budget.active_provider_calls == 0

    transport, client = adapter_module._native_openai_client(adapter, _config())
    cancel_budget = ProviderRequestBudget(deadline_monotonic=10**12, max_requests=1)
    cancel_events: list[dict[str, Any]] = []
    token = transport.activate(cancel_budget, cancel_events.append, 5.0, 0)
    try:
        cancel_stream = await client.chat.completions.create(
            model="offline-test-model", messages=[{"role": "user", "content": "cancel"}], stream=True,
        )
        first_cancel_chunk = await cancel_stream.__anext__()
        assert first_cancel_chunk.choices[0].delta.content == "cancel"
        next_chunk = asyncio.create_task(cancel_stream.__anext__())
        try:
            await asyncio.wait_for(cancel_eof_waiting.wait(), timeout=2)
        except asyncio.TimeoutError:
            raise AssertionError([body.decode("utf-8", errors="replace") for body in response_bodies])
        next_chunk.cancel()
        with pytest.raises(asyncio.CancelledError):
            await next_chunk
        await cancel_stream.aclose()
    finally:
        release_cancel_eof.set()
        transport.deactivate(token)

    cancel_response = next(event for event in cancel_events if event["type"] == "model_response")
    assert cancel_response["receive_complete_at"] is None
    assert cancel_response["response_end_kind"] in {"ERROR", "CLOSED_EARLY"}

    # Many small, legal SSE comments may make the wire body larger than the
    # decoded choice budget without making the actual answer large.
    responses[:] = [
        (b":" + b"x" * 1024 + b"\n\n") * 300
        + b'data: {"choices":[{"index":0,"delta":{"role":"assistant","content":"ok"}}]}\n\n'
        + b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
        + b'data: {"choices":[],"usage":{"prompt_tokens":2,"completion_tokens":1,"total_tokens":3}}\n\n'
        + b"data: [DONE]\n\n"
    ]
    transport, client = adapter_module._native_openai_client(adapter, _config())
    large_wire_budget = ProviderRequestBudget(deadline_monotonic=10**12, max_requests=1)
    large_wire_events: list[dict[str, Any]] = []
    token = transport.activate(large_wire_budget, large_wire_events.append, 5.0, 0)
    try:
        large_wire_stream = await client.chat.completions.create(
            model="offline-test-model", messages=[{"role": "user", "content": "large wire"}], stream=True,
        )
        large_wire_chunks = [chunk async for chunk in large_wire_stream]
    finally:
        transport.deactivate(token)
        await adapter_module.close_native_clients()
    assert large_wire_chunks[0].choices[0].delta.content == "ok"
    large_wire_response = next(event for event in large_wire_events if event["type"] == "model_response")
    assert large_wire_response["status"] == 200
    assert large_wire_response["usage"] == {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3}
    assert large_wire_response["finish_reason"] == "stop"
    assert large_wire_response["response_end_kind"] == "EOF"
    assert large_wire_response["receive_complete_at"] == "2026-10-15T12:00:00.000003Z"

    async def assert_response_limit(body: bytes) -> None:
        responses[:] = [body]
        limited_transport, limited_client = adapter_module._native_openai_client(adapter, _config())
        limited_budget = ProviderRequestBudget(deadline_monotonic=10**12, max_requests=1)
        limited_events: list[dict[str, Any]] = []
        limited_token = limited_transport.activate(limited_budget, limited_events.append, 5.0, 0)
        try:
            limited_stream = await limited_client.chat.completions.create(
                model="offline-test-model", messages=[{"role": "user", "content": "bounded"}], stream=True,
            )
            with pytest.raises(adapter_module.ProviderResponseError) as raised:
                _ = [chunk async for chunk in limited_stream]
            assert raised.value.category == "RESPONSE_LIMIT"
        finally:
            limited_transport.deactivate(limited_token)
            await adapter_module.close_native_clients()
        limited_response = next(event for event in limited_events if event["type"] == "model_response")
        assert limited_response["error"] == {"category": "RESPONSE_LIMIT"}
        assert limited_response["response_end_kind"] == "ERROR"
        assert limited_response["receive_complete_at"] is None
        assert limited_budget.active_provider_calls == 0
        assert limited_budget.requests == 1

    await assert_response_limit(b":" + b"x" * (256 * 1024) + b"\n\n")
    await assert_response_limit(b":" + b"x" * (256 * 1024 - 4) + b"\r\n\r\n")
    await assert_response_limit(
        (b'data: {"choices":[{"delta":{"content":"' + b"x" * 100_000 + b'"}}]}\n\n') * 3
        + b"data: [DONE]\n\n"
    )
    await assert_response_limit(
        (b'data: {"choices":[{"delta":{"reasoning_content":"' + b"x" * 100_000 + b'"}}]}\n\n') * 3
        + b"data: [DONE]\n\n"
    )
    await assert_response_limit(
        (b'data: {"choices":[{"delta":{"tool_calls":[{"function":{"arguments":"'
         + b"x" * 100_000 + b'"}}]}}]}\n\n') * 3
        + b"data: [DONE]\n\n"
    )
    await assert_response_limit((b":" + b"x" * 1024 + b"\n\n") * 2100 + b"data: [DONE]\n\n")
    await adapter_module.close_native_clients()


@pytest.mark.parametrize("split_crlf", [False, True])
@pytest.mark.parametrize("excess_bytes", [0, 1])
def test_sse_event_limit_counts_crlf_wire_bytes(split_crlf: bool, excess_bytes: int) -> None:
    prefix, suffix = b'data: {"x":"', b'"}\r\n\r\n'
    limit = adapter_module._SSEStreamLimits.event_limit
    body = prefix + b"a" * (limit - len(prefix) - len(suffix) + excess_bytes) + suffix
    assert len(body) == limit + excess_bytes
    # Exercise both a single read and reads ending between every CR/LF pair.
    chunks = [body] if not split_crlf else [body[:-3], body[-3:-1], body[-1:]]
    limits = adapter_module._SSEStreamLimits()

    def consume() -> None:
        for chunk in chunks:
            limits.feed(chunk)
        limits.finish()

    if excess_bytes:
        with pytest.raises(adapter_module.ProviderResponseError) as raised:
            consume()
        assert raised.value.category == "RESPONSE_LIMIT"
    else:
        consume()
        assert limits.wire_size == limit
        assert limits.event_size == 0


def test_transport_error_summary_keeps_unknown_exceptions_unclassified() -> None:
    assert adapter_module._transport_error_summary(TypeError("private body")) == {"category": "UNKNOWN"}
    assert adapter_module._transport_error_summary(
        FileNotFoundError(r"C:\private\tenant\secret.txt")
    ) == {"category": "UNKNOWN"}


@pytest.mark.parametrize("case", ["cumulative_snapshots", "no_usage", "partial_fields"])
def test_native_usage_snapshots_replace_per_response_and_keep_history(
    monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    asyncio.run(_test_native_usage_snapshots_replace_per_response_and_keep_history(monkeypatch, case))


async def _test_native_usage_snapshots_replace_per_response_and_keep_history(
    monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    import httpcore
    from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelResponse

    def sse(choices: list[dict[str, Any]], usage: dict[str, Any] | None = None) -> bytes:
        event: dict[str, Any] = {"id": "offline", "object": "chat.completion.chunk", "created": 1,
                                 "model": "offline-test-model", "choices": choices}
        if usage is not None:
            event["usage"] = usage
        return b"data: " + json.dumps(event, separators=(",", ":")).encode() + b"\n\n"

    def usage(prompt: int, completion: int, *, cached: int | None = None) -> dict[str, Any]:
        result: dict[str, Any] = {"prompt_tokens": prompt, "completion_tokens": completion,
                                  "total_tokens": prompt + completion}
        if cached is not None:
            result["prompt_tokens_details"] = {"cached_tokens": cached}
        return result

    first_response = (
        sse([{"index": 0, "delta": {"role": "assistant", "tool_calls": [{
            "index": 0, "id": "call-1", "type": "function",
            "function": {"name": "echo", "arguments": "{\"value\":\"read\"}"},
        }]}, "finish_reason": None}], usage(3, 1))
        + sse([{"index": 0, "delta": {}, "finish_reason": "tool_calls"}], usage(5, 2))
        + sse([], usage(7, 3, cached=2))
        + b"data: [DONE]\n\n"
    )
    second_response = (
        sse([{"index": 0, "delta": {"role": "assistant", "content": "done"}, "finish_reason": None}])
        + sse([{"index": 0, "delta": {}, "finish_reason": "stop"}], usage(11, 4, cached=3))
        + sse([])  # A trailing no-usage chunk must not clear the final snapshot.
        + b"data: [DONE]\n\n"
    )
    responses = ([first_response, second_response] if case == "cumulative_snapshots" else [
        sse([{"index": 0, "delta": {"role": "assistant", "content": "done"}, "finish_reason": None}])
        + sse([{"index": 0, "delta": {}, "finish_reason": "stop"}],
              {"prompt_tokens": 5} if case == "partial_fields" else None)
        + b"data: [DONE]\n\n"
    ])
    streams: list[Any] = []
    class _MemoryStream:
        def __init__(self, body: bytes) -> None:
            self.response = (
                b"HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\ncontent-length: "
                + str(len(body)).encode() + b"\r\nconnection: close\r\n\r\n" + body
            )
            self.offset = 0
            self.writes: list[bytes] = []

        async def write(self, buffer: bytes, timeout: float | None = None) -> None:
            del timeout
            self.writes.append(buffer)

        async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
            del timeout
            chunk = self.response[self.offset:self.offset + max_bytes]
            self.offset += len(chunk)
            return chunk

        async def start_tls(self, context: Any, server_hostname: str | None = None,
                            timeout: float | None = None) -> "_MemoryStream":
            del context, server_hostname, timeout
            return self

        def get_extra_info(self, info: str) -> Any:
            del info
            return None

        async def aclose(self) -> None:
            return None

    class _OfflineBackend:
        async def connect_tcp(self, host: str, port: int, **kwargs: Any) -> _MemoryStream:
            del host, port, kwargs
            stream = _MemoryStream(responses.pop(0))
            streams.append(stream)
            return stream

        async def connect_unix_socket(self, *_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("unix socket is not permitted")

        async def sleep(self, seconds: float) -> None:
            await asyncio.sleep(seconds)

    monkeypatch.setattr(httpcore, "AnyIOBackend", _OfflineBackend)
    monkeypatch.setattr(adapter_module._OpenAICompatibleClient, "_resolved_public_ips",
                        staticmethod(lambda _host, _port: ["203.0.113.19"]))
    adapter = PydanticAIAdapter(secret_resolver=lambda _ref: "offline-secret")
    captured: list[dict[str, Any]] = []

    async def execute(_name: str, args: dict[str, Any], _call_id: str) -> dict[str, Any]:
        return args

    try:
        run = adapter.run_agent_async(
            config=_config(), messages=[{"role": "user", "content": "read"}],
            tools=([{"name": "echo", "description": "echo", "parameters": {
                "type": "object", "properties": {"value": {"type": "string"}},
                "required": ["value"], "additionalProperties": False,
            }}] if case == "cumulative_snapshots" else []),
            validate_tool=lambda *_args: None, execute_tool=execute, on_event=captured.append,
            history_state=None, request_budget=ProviderRequestBudget(deadline_monotonic=10**12, max_requests=4),
        )
        if case == "partial_fields":
            with pytest.warns(Warning, match=r"Missing value at `usage\.completion_tokens`"):
                result = await run
        else:
            result = await run
    finally:
        await adapter_module.close_native_clients()

    assert result["text"] == "done"
    if case == "no_usage":
        assert result["usage"]["requests"] == 1
        assert result["usage"]["input_tokens"] is None
        assert result["usage"]["output_tokens"] is None
        assert result["usage"]["total_tokens"] is None
        from chrom_platform.modules.assistant.runtime import _usage
        from chrom_platform.modules.assistant.schemas import Usage as PublicUsage

        normalized_usage = _usage(result["usage"])
        public_usage = PublicUsage(
            input_tokens=normalized_usage.get("input_tokens"),
            output_tokens=normalized_usage.get("output_tokens"),
            model_requests=result["usage"]["requests"],
        ).model_dump(mode="json")
        assert public_usage["input_tokens"] is None and public_usage["output_tokens"] is None
    elif case == "partial_fields":
        assert result["usage"]["requests"] == 1
        assert result["usage"]["input_tokens"] == 5
        assert result["usage"]["output_tokens"] is None
        assert result["usage"]["total_tokens"] is None
        from chrom_platform.modules.assistant.runtime import _usage
        from chrom_platform.modules.assistant.schemas import Usage as PublicUsage

        normalized_usage = _usage(result["usage"])
        public_usage = PublicUsage(
            input_tokens=normalized_usage.get("input_tokens"),
            output_tokens=normalized_usage.get("output_tokens"),
            model_requests=result["usage"]["requests"],
        ).model_dump(mode="json")
        assert public_usage["input_tokens"] == 5 and public_usage["output_tokens"] is None
    else:
        assert result["usage"]["requests"] == 2
        assert result["usage"]["input_tokens"] == 18
        assert result["usage"]["output_tokens"] == 7
        assert result["usage"]["total_tokens"] == 25
        assert result["usage"]["cached_input_tokens"] == 5
    history = ModelMessagesTypeAdapter.validate_python(result["history_state"]["messages"])
    response_usage = [message.usage for message in history if isinstance(message, ModelResponse)]
    if case == "cumulative_snapshots":
        assert [(item.input_tokens, item.output_tokens) for item in response_usage] == [(7, 3), (11, 4)]
        assert [item.cache_read_tokens for item in response_usage] == [2, 3]
        response_events = [event for event in captured if event.get("type") == "model_response"]
        assert [event["usage"]["cached_input_tokens"] for event in response_events] == [2, 3]
    else:
        assert len(response_usage) == 1
        if case == "partial_fields":
                # PydanticAI falls back to an empty RequestUsage if extraction is partial;
                # the transport observer remains authoritative for known public fields.
                assert response_usage[0].input_tokens == 0
    assert len(streams) == result["usage"]["requests"] and responses == []
    request_payloads = [json.loads(b"".join(stream.writes).split(b"\r\n\r\n", 1)[1]) for stream in streams]
    assert all(payload.get("max_tokens") == 512 or payload.get("max_completion_tokens") == 512
               for payload in request_payloads)
    assert all("continuous_usage_stats" not in json.dumps(payload) for payload in request_payloads)


def test_sse_usage_summary_preserves_missing_fields_as_unknown() -> None:
    usage, finish_reason = adapter_module._sse_usage_summary(
        b'data: {"choices":[{"finish_reason":"stop"}],"usage":{"prompt_tokens":5}}\n\n'
    )
    assert usage == {"input_tokens": 5, "output_tokens": None, "total_tokens": None}
    assert finish_reason == "stop"
