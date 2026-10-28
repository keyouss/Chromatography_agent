from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from chrom_platform.modules.assistant import adapter as adapter_module
from chrom_platform.modules.assistant.adapter import PydanticAIAdapter, ProviderRequestBudget
from chrom_platform.modules.assistant.runtime import AgentRuntime, RuntimeConfigSnapshot
from test_assistant_pydanticai_runtime import _CheckpointSink, _StreamSink


@pytest.mark.parametrize("status", [401, 403])
def test_native_agent_auth_failure_is_a_single_terminal_dispatch(
    monkeypatch: pytest.MonkeyPatch, status: int,
) -> None:
    asyncio.run(_test_native_agent_auth_failure_is_a_single_terminal_dispatch(monkeypatch, status))


async def _test_native_agent_auth_failure_is_a_single_terminal_dispatch(
    monkeypatch: pytest.MonkeyPatch, status: int,
) -> None:
    """Exercise the production Agent entry with local 401/403 responses and no network."""
    import httpcore

    class _ResponseStream:
        def __init__(self) -> None:
            body = b'{"error":{"code":"authentication_error"}}'
            self.data = (
                f"HTTP/1.1 {status} Auth Failure\r\n".encode("ascii") +
                b"content-type: application/json\r\n"
                b"content-length: " + str(len(body)).encode("ascii") + b"\r\n"
                b"connection: close\r\n\r\n" + body
            )
            self.offset = 0

        async def write(self, buffer: bytes, timeout: float | None = None) -> None:
            del timeout
            if buffer.startswith(b"POST "):
                dispatches.append(buffer)

        async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
            del timeout
            chunk = self.data[self.offset:self.offset + max_bytes]
            self.offset += len(chunk)
            return chunk

        async def start_tls(self, context: Any, server_hostname: str | None = None,
                            timeout: float | None = None) -> "_ResponseStream":
            del context, server_hostname, timeout
            return self

        def get_extra_info(self, info: str) -> None:
            del info
            return None

        async def aclose(self) -> None:
            return None

    class _OfflineBackend:
        async def connect_tcp(self, host: str, port: int, **kwargs: Any) -> _ResponseStream:
            del host, port, kwargs
            connections.append("connected")
            return _ResponseStream()

        async def connect_unix_socket(self, *_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("Unix sockets must remain unavailable")

        async def sleep(self, seconds: float) -> None:
            await asyncio.sleep(seconds)

    connections: list[str] = []
    dispatches: list[bytes] = []
    monkeypatch.setattr(httpcore, "AnyIOBackend", _OfflineBackend)
    monkeypatch.setattr(
        adapter_module._OpenAICompatibleClient,
        "_resolved_public_ips",
        staticmethod(lambda _host, _port: ["203.0.113.19"]),
    )

    adapter = PydanticAIAdapter(secret_resolver=lambda _ref: "offline-secret")
    config = RuntimeConfigSnapshot(
        url="https://api.openai.com/v1",
        model_id="offline-test-model",
        key_reference="dpapi:v1:test",
        config_revision="cfg-401",
    )
    budget = ProviderRequestBudget(deadline_monotonic=10**12, max_requests=6)
    events: list[dict[str, Any]] = []

    try:
        with pytest.raises(Exception) as raised:
            await adapter.run_agent_async(
                config=config,
                messages=[{"role": "user", "content": "offline auth probe"}],
                tools=[],
                execute_tool=lambda *_args: None,
                validate_tool=None,
                on_event=events.append,
                history_state=None,
                request_budget=budget,
                max_retries=2,
            )
    finally:
        await adapter.close_async_clients()

    # The adapter budget is consumed immediately before the only socket write;
    # neither SDK retries nor the adapter retry policy may replay a 401/403.
    assert len(connections) == 1, f"transport was not reached: {type(raised.value).__name__}: {raised.value}"
    assert len(dispatches) == 1
    assert budget.requests == 1
    request_events = [event for event in events if event.get("type") == "model_request"]
    response_events = [event for event in events if event.get("type") == "model_response"]
    assert len(request_events) == 1
    assert [(event.get("status"), event.get("error", {}).get("http_status")) for event in response_events] == [(status, status)]
    assert budget.auth_failed


def test_runtime_streams_real_native_agent_partial_unicode_before_run_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asyncio.run(_test_runtime_streams_real_native_agent_partial_unicode_before_run_finishes(monkeypatch))


async def _test_runtime_streams_real_native_agent_partial_unicode_before_run_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise Runtime -> native Agent -> FunctionModel -> persisted stream sink."""
    import pydantic_ai.models.openai as openai_models
    from pydantic_ai.models.function import FunctionModel

    model_waiting = asyncio.Event()
    release_model = asyncio.Event()
    stream = _StreamSink()
    checkpoints = _CheckpointSink()
    adapter = PydanticAIAdapter(secret_resolver=lambda _ref: "offline-secret")
    config = RuntimeConfigSnapshot(
        url="https://api.openai.com/v1",
        model_id="offline-test-model",
        key_reference="dpapi:v1:test",
        config_revision="cfg-native-qa",
        provider_configuration_hash="c" * 64,
    )

    async def _stream_function(_messages: list[Any], _info: Any):
        yield "你"
        model_waiting.set()
        await release_model.wait()
        yield "好🙂"

    def _make_model(_name: str, *, provider: object) -> FunctionModel:
        assert provider is not None
        return FunctionModel(stream_function=_stream_function, model_name="offline-function-model")

    monkeypatch.setattr(openai_models, "OpenAIChatModel", _make_model)
    monkeypatch.setattr(
        adapter_module._OpenAICompatibleClient,
        "_resolved_public_ips",
        staticmethod(lambda _host, _port: ["203.0.113.19"]),
    )
    runtime = AgentRuntime(
        adapter,
        config,
        history=[{"role": "user", "content": "用中文回复"}],
        stream_sink=stream,
        checkpoint_loader=lambda _ctx: None,
        checkpoint_sink=checkpoints,
    )

    run_task = asyncio.create_task(runtime.run_async(run_id="run-native-qa"))
    try:
        await asyncio.wait_for(model_waiting.wait(), timeout=2)
        # Keep the real FunctionModel turn open long enough for Runtime's
        # periodic flush task to persist a prefix before Agent completion.
        await asyncio.sleep(0.15)
        assert not run_task.done()
        assert stream.text == "你"
        release_model.set()
        result = await asyncio.wait_for(run_task, timeout=3)
    finally:
        release_model.set()
        if not run_task.done():
            await run_task
        await adapter.close_async_clients()

    assert result.status == "SUCCEEDED", result.error
    assert result.text == "你好🙂"
    assert stream.text == "你好🙂"
    assert [event[1]["delta"] for event in stream.events if event[0] == "message.delta"] == ["你", "好🙂"]
    assert [event[1]["offset"] for event in stream.events if event[0] == "message.delta"] == [0, 1]
    assert checkpoints.states[-1]["history_state"]["messages"]


@pytest.mark.parametrize(
    ("case", "expected_before_run_result", "expected_result_text"),
    [
        (
            "same_response_same_index_replaces",
            [("text_start", "old"), ("replacement", "new")],
            "new",
        ),
        (
            "next_response_same_index_is_independent",
            [("text_start", "old"), ("text_end", None), ("text_start", "new")],
            "oldnew",
        ),
        (
            "next_response_common_prefix_keeps_both_responses",
            [
                ("text_start", "旧"), ("text_end", None),
                ("text_start", "旧"), ("text_delta", "新"), ("text_end", None),
            ],
            "旧旧新",
        ),
    ],
)
def test_native_part_start_uses_index_and_response_boundary_before_final_result(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    expected_before_run_result: list[tuple[str, str | None]],
    expected_result_text: str,
) -> None:
    asyncio.run(_test_native_part_start_uses_index_and_response_boundary_before_final_result(
        monkeypatch, case, expected_before_run_result, expected_result_text,
    ))


async def _test_native_part_start_uses_index_and_response_boundary_before_final_result(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    expected_before_run_result: list[tuple[str, str | None]],
    expected_result_text: str,
) -> None:
    """A bounded typed-SDK event probe, separate from real Agent/FunctionModel coverage."""
    import pydantic_ai
    from pydantic_ai.messages import (
        FunctionToolCallEvent,
        FunctionToolResultEvent,
        PartEndEvent,
            PartDeltaEvent,
        PartStartEvent,
            TextPartDelta,
        TextPart,
        ToolCallPart,
        ToolReturnPart,
    )
    from pydantic_ai.run import AgentRunResultEvent
    from types import SimpleNamespace

    release_final = asyncio.Event()
    two_text_starts = asyncio.Event()
    captured: list[dict[str, Any]] = []

    if case == "next_response_common_prefix_keeps_both_responses":
        old_part = TextPart(content="旧")
        next_part = TextPart(content="旧")
        delta = TextPartDelta(content_delta="新")
    else:
        old_part = TextPart(content="old")
        next_part = TextPart(content="new")
        delta = None
    new_part = TextPart(content="new")
    if case == "same_response_same_index_replaces":
        scripted_events = [
            PartStartEvent(index=0, part=old_part),
            PartStartEvent(index=0, part=new_part),
        ]
        final_output = "new"
    else:
        # A typed tool round separates two model responses. Part indexes are
        # local to their response: each response legitimately starts at 0.
        call_part = ToolCallPart("lookup", {}, tool_call_id="call-1")
        return_part = ToolReturnPart("lookup", "call-1", "done")
        scripted_events = [
            PartStartEvent(index=0, part=old_part),
            PartEndEvent(index=0, part=old_part),
            FunctionToolCallEvent(part=call_part),
            FunctionToolResultEvent(part=return_part),
        ]
        if delta is not None:
            scripted_events.extend([
                PartStartEvent(index=0, part=next_part),
                PartDeltaEvent(index=0, delta=delta),
                PartEndEvent(index=0, part=TextPart(content="旧新")),
            ])
            final_output = "旧新"
        else:
            scripted_events.append(PartStartEvent(index=0, part=next_part))
            final_output = "new"

    class _RunResult:
        output = final_output
        usage = SimpleNamespace(requests=2, input_tokens=1, output_tokens=1, total_tokens=2)
        finish_reason = "stop"

        def all_messages(self) -> list[Any]:
            return []

    class _EventHandle:
        def __init__(self) -> None:
            self.index = 0
            self.result_emitted = False

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
            if not self.result_emitted:
                await release_final.wait()
                self.result_emitted = True
                return AgentRunResultEvent(result=_RunResult())
            raise StopAsyncIteration

    class _EventAgent:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def run_stream_events(self, **_kwargs: Any) -> _EventHandle:
            return _EventHandle()

    monkeypatch.setattr(pydantic_ai, "Agent", _EventAgent)
    monkeypatch.setattr(
        adapter_module._OpenAICompatibleClient,
        "_resolved_public_ips",
        staticmethod(lambda _host, _port: ["203.0.113.19"]),
    )
    adapter = PydanticAIAdapter(secret_resolver=lambda _ref: "offline-secret")
    config = RuntimeConfigSnapshot(
        url="https://api.openai.com/v1",
        model_id="offline-test-model",
        key_reference="dpapi:v1:test",
        config_revision=f"cfg-{case}",
    )

    async def on_event(event: dict[str, Any]) -> None:
        captured.append(dict(event))
        if sum(item.get("type") in {"text_start", "replacement"} for item in captured) == 2:
            two_text_starts.set()

    run_task = asyncio.create_task(adapter.run_agent_async(
        config=config,
        messages=[{"role": "user", "content": "probe part indexing"}],
        tools=[],
        execute_tool=lambda *_args: None,
        validate_tool=None,
        on_event=on_event,
        history_state=None,
        request_budget=ProviderRequestBudget(deadline_monotonic=10**12, max_requests=6),
    ))
    try:
        await asyncio.wait_for(two_text_starts.wait(), timeout=2)
        before_final = [
            (event["type"], event.get("text"))
            for event in captured
            if event.get("type") in {"text_start", "text_delta", "text_end", "replacement"}
        ]
        release_final.set()
        result = await asyncio.wait_for(run_task, timeout=2)
    finally:
        release_final.set()
        if not run_task.done():
            await run_task
        await adapter.close_async_clients()

    assert result["text"] == expected_result_text
    assert before_final == expected_before_run_result
    if case == "same_response_same_index_replaces":
        replacement_events = [event for event in captured if event.get("type") == "replacement"]
        assert len(replacement_events) == 1
        assert replacement_events[0].get("replace") is True
        assert replacement_events[0].get("text") == "new"
        assert replacement_events[0].get("warning") == "same_response_text_part_replaced"
    public_text = ""
    for event in captured:
        if event.get("type") in {"text_start", "text_delta"}:
            public_text += event.get("text", "")
        elif event.get("type") == "replacement":
            public_text = event.get("text", "")
    assert public_text == result["text"]
