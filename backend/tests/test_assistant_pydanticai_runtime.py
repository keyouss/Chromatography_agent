from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from chrom_platform.modules.assistant.events import InMemoryAgentEventStore
from chrom_platform.modules.assistant.runtime import AgentRuntime, RuntimeConfigSnapshot, RuntimeResult
from chrom_platform.modules.assistant.tools import BoundTool, ToolPending, ToolSpec
from chrom_platform.core.dto import ResourceRef
from chrom_platform.modules.assistant.schemas import PendingConfirmationBinding


_CONFIG_HASH = "c" * 64


def _config() -> RuntimeConfigSnapshot:
    return RuntimeConfigSnapshot(
        "https://api.openai.com/v1",
        "test-model",
        "key-reference",
        "revision-1",
        provider_configuration_hash=_CONFIG_HASH,
    )


def _native_state() -> dict:
    return {
        "framework": "pydantic-ai",
        "framework_version": "2.56.0",
        "serialization_version": "ModelMessagesTypeAdapter",
        "config_binding": {"model_id": "test-model", "config_revision": "revision-1"},
        "messages": [],
    }


class _StreamSink:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []
        self.text = ""

    async def __call__(self, _ctx, op: str, payload: dict) -> dict | bool:
        if op == "ensure":
            return {"message_id": "msg-assistant", "block_index": 0, "offset": len(self.text), "text": self.text}
        if op == "append":
            assert payload["offset"] == len(self.text)
            self.events.append(("message.delta", dict(payload)))
            self.text += payload["delta"]
            return {"message_id": "msg-assistant", "block_index": 0, "offset": len(self.text), "text": self.text}
        if op == "replace":
            self.text = payload["text"]
            self.events.append(("snapshot", {"text": self.text}))
            self.events.append(("warning", {"code": "MESSAGE_SNAPSHOT_REPLACED"}))
            return {"message_id": "msg-assistant", "block_index": 0, "offset": len(self.text), "text": self.text}
        raise AssertionError(op)


class _CheckpointSink:
    def __init__(self) -> None:
        self.states: list[dict] = []

    async def __call__(self, _ctx, state: dict) -> bool:
        self.states.append(state)
        return True


class _TextAdapter:
    def __init__(self, stream):
        self.stream = stream
        self.flushed_during_wait = False

    async def run_agent_async(self, *, on_event, **_kwargs):
        await on_event({"type": "model_request", "request_index": 1, "transport_attempt": 1, "model_id": "test-model", "message_count": 2, "tool_names": [], "request_content_sha256": "a" * 64})
        await on_event({"type": "text_start", "text": "你"})
        # The runtime's 100 ms timer must persist this chunk while the adapter
        # remains inside the same model turn.
        await asyncio.sleep(0.12)
        self.flushed_during_wait = self.stream.text == "你"
        await on_event({"type": "text_delta", "text": "好🙂"})
        await on_event({"type": "text_end"})
        await on_event({"type": "model_response", "request_index": 1, "transport_attempt": 1, "status": "SUCCEEDED", "usage": {"input_tokens": 3, "output_tokens": 3}, "finish_reason": "stop"})
        return {"text": "你好🙂", "usage": {"input_tokens": 3, "output_tokens": 3}, "provider_requests": 1, "tool_calls": 0, "history_state": _native_state(), "pending_actions": []}


class _BlockingStreamSink(_StreamSink):
    def __init__(self):
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.append_finished = asyncio.Event()

    async def __call__(self, _ctx, op: str, payload: dict) -> dict | bool:
        if op == "append":
            self.entered.set()
            await self.release.wait()
            result = await super().__call__(_ctx, op, payload)
            self.append_finished.set()
            return result
        return await super().__call__(_ctx, op, payload)


class _LockOrderingTrace:
    def __init__(self, sink):
        self.sink = sink
        self.called_before_stream_commit = False

    def capture_pair(self, **_kwargs):
        self.called_before_stream_commit = not self.sink.append_finished.is_set()
        return SimpleNamespace(id="logical"), SimpleNamespace(id="provider")

    def save_llm_call(self, _call):
        pass


class _LockOrderingAdapter:
    async def run_agent_async(self, *, on_event, **_kwargs):
        await on_event({"type": "text_start", "text": "first"})
        await asyncio.sleep(0.12)
        await on_event({"type": "model_request", "request_index": 1, "transport_attempt": 1})
        await on_event({"type": "model_response", "request_index": 1, "transport_attempt": 1, "status": "SUCCEEDED"})
        await on_event({"type": "text_end"})
        return {"text": "first", "history_state": _native_state(), "pending_actions": []}


def test_stream_transaction_finishes_before_sync_trace_capture() -> None:
    async def scenario():
        stream = _BlockingStreamSink()
        trace = _LockOrderingTrace(stream)
        runtime = AgentRuntime(
            _LockOrderingAdapter(), _config(),
            history=[{"role": "user", "content": "test"}],
            stream_sink=stream,
            checkpoint_loader=lambda _ctx: None,
            checkpoint_sink=_CheckpointSink(),
            context_inspector=trace,
        )
        task = asyncio.create_task(runtime.run_async(run_id="run-lock-order"))
        await asyncio.wait_for(stream.entered.wait(), timeout=1)
        await asyncio.sleep(0.02)
        assert trace.called_before_stream_commit is False
        stream.release.set()
        result = await task
        assert result.status == "SUCCEEDED"
        assert stream.append_finished.is_set()
        assert trace.called_before_stream_commit is False

    asyncio.run(scenario())


def test_run_returns_native_coroutine_and_persists_timer_flushed_unicode_deltas() -> None:
    stream, checkpoints = _StreamSink(), _CheckpointSink()
    adapter = _TextAdapter(stream)
    runtime = AgentRuntime(
        adapter,
        _config(),
        history=[{"role": "user", "content": "你好"}],
        stream_sink=stream,
        checkpoint_loader=lambda _ctx: None,
        checkpoint_sink=checkpoints,
    )

    pending = runtime.run(run_id="run-native")
    assert asyncio.iscoroutine(pending)
    result = asyncio.run(pending)

    assert isinstance(result, RuntimeResult)
    assert result.status == "SUCCEEDED"
    assert result.text == "你好🙂"
    assert result.provider_requests == 1
    assert adapter.flushed_during_wait
    assert stream.text == "你好🙂"
    assert [event[0] for event in stream.events] == ["message.delta", "message.delta"]
    first, second = [event[1] for event in stream.events]
    assert (first["offset"], first["delta"]) == (0, "你")
    assert (second["offset"], second["delta"]) == (1, "好🙂")
    checkpoint = checkpoints.states[-1]
    assert checkpoint["config_hash"] == _CONFIG_HASH
    assert checkpoint["stream_message_id"] == "msg-assistant"
    assert checkpoint["history_state"] == _native_state()
    assert not hasattr(result, "history_state")


@pytest.mark.parametrize(
    ("end_kind", "receive_complete_at", "expected_timestamp"),
    [
        ("EOF", "2026-10-15T08:09:10.123456Z", "2026-10-15T08:09:10.123456Z"),
        ("CLOSED_EARLY", None, None),
        ("EOF", "2026-10-15T08:09:10Z", None),
    ],
)
def test_native_trace_preserves_valid_receive_eof_time_across_delayed_text_flush(
    end_kind: str, receive_complete_at: str | None, expected_timestamp: str | None,
) -> None:
    class Trace:
        def __init__(self):
            self.saved = []

        def capture_pair(self, **_kwargs):
            return SimpleNamespace(id="logical"), SimpleNamespace(id="provider")

        def save_llm_call(self, call):
            self.saved.append(call)

    class Adapter:
        def __init__(self, stream):
            self.stream = stream
            self.flushed_before_response = False

        async def run_agent_async(self, *, on_event, **_kwargs):
            await on_event({"type": "text_start", "text": "first"})
            # Preserve the adapter's EOF timestamp across the runtime's real
            # 100 ms persistence timer and the subsequent synchronous trace IO.
            await asyncio.sleep(0.12)
            self.flushed_before_response = self.stream.text == "first"
            await on_event({"type": "model_request", "request_index": 1,
                            "transport_attempt": 1, "request_content_sha256": "e" * 64})
            await on_event({"type": "model_response", "request_index": 1,
                            "transport_attempt": 1, "status": "SUCCEEDED",
                            "response_end_kind": end_kind,
                            "receive_complete_at": receive_complete_at})
            await on_event({"type": "text_end"})
            return {"text": "first", "usage": {}, "provider_requests": 1,
                    "tool_calls": 0, "history_state": _native_state(), "pending_actions": []}

    async def scenario():
        stream, trace = _StreamSink(), Trace()
        adapter = Adapter(stream)
        runtime = AgentRuntime(
            adapter, _config(), history=[{"role": "user", "content": "test"}],
            stream_sink=stream, checkpoint_loader=lambda _ctx: None,
            checkpoint_sink=_CheckpointSink(), context_inspector=trace,
        )
        result = await runtime.run_async(run_id="run-response-eof")
        assert result.status == "SUCCEEDED"
        assert adapter.flushed_before_response
        assert len(trace.saved) == 1
        summary = trace.saved[0].model_parameters_summary
        assert summary["provider_response_end_kind"] == end_kind
        assert summary.get("provider_response_receive_complete_at") == expected_timestamp

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("exception_kind", "exception_class", "reason_category"),
    [
        ("type", "TypeError", "INTERNAL_TYPE_ERROR"),
        ("protocol", "RemoteProtocolError", "PROTOCOL"),
        ("transport", "ProviderTransportError", "TLS"),
        ("unknown", "Unknown", "UNKNOWN"),
        ("known_transport_then_generic", "ProviderTransportError", "CONNECT"),
        ("category_then_type", "TypeError", "CONNECT"),
    ],
)
def test_native_error_diagnostic_is_allowlisted_and_only_updates_latest_failed_call(
    exception_kind: str, exception_class: str, reason_category: str,
) -> None:
    from chrom_platform.modules.assistant.adapter import ProviderTransportError
    from chrom_platform.modules.assistant.runtime import _native_error_diagnostic

    def make_exception():
        if exception_kind in {"type", "category_then_type"}:
            return TypeError("secret://provider?token=hidden")
        if exception_kind == "protocol":
            return type("RemoteProtocolError", (Exception,), {})("secret://provider?token=hidden")
        if exception_kind == "transport":
            return ProviderTransportError("TLS", message="secret://provider?token=hidden")
        return RuntimeError("secret://provider?token=hidden")

    expected_diagnostic = {"exception_class": exception_class, "reason_category": reason_category}
    expected_exception_diagnostic = (
        {"exception_class": "Unknown", "reason_category": "UNKNOWN"}
        if exception_kind == "known_transport_then_generic" else
        {"exception_class": "TypeError", "reason_category": "INTERNAL_TYPE_ERROR"}
        if exception_kind == "category_then_type" else expected_diagnostic
    )
    assert _native_error_diagnostic(make_exception()) == expected_exception_diagnostic
    if exception_kind == "type":
        mapped_diagnostic = {"exception_class": "Unknown", "reason_category": "UNKNOWN"}
        mapping = {"message": "secret://provider?token=hidden"}
    elif exception_kind == "transport":
        mapped_diagnostic = {"exception_class": "Unknown", "reason_category": "TLS"}
        mapping = {"category": "TLS", "message": "secret://provider?token=hidden"}
    elif exception_kind == "category_then_type":
        mapped_diagnostic = {"exception_class": "Unknown", "reason_category": "CONNECT"}
        mapping = {"category": "CONNECT", "message": "secret://provider?token=hidden"}
    elif exception_kind == "known_transport_then_generic":
        mapped_diagnostic = expected_diagnostic
        mapping = {"exception_class": "ProviderTransportError", "category": "CONNECT", "message": "secret://provider?token=hidden"}
    elif exception_kind == "unknown":
        mapped_diagnostic = expected_diagnostic
        mapping = {"exception_class": "UntrustedException", "message": "secret://provider?token=hidden"}
    else:
        mapped_diagnostic = expected_diagnostic
        mapping = {"exception_class": exception_class, "message": "secret://provider?token=hidden"}
    assert _native_error_diagnostic(mapping) == mapped_diagnostic
    assert _native_error_diagnostic(FileNotFoundError("C:\\sensitive\\model.joblib")) == {
        "exception_class": "Unknown", "reason_category": "UNKNOWN",
    }

    class Trace:
        def __init__(self):
            self.saved = []

        def capture_pair(self, *, llm_call, **_kwargs):
            return SimpleNamespace(id=f"logical-{llm_call.request_index}"), SimpleNamespace(id=f"provider-{llm_call.request_index}")

        def save_llm_call(self, call):
            self.saved.append(call)

    class Adapter:
        async def run_agent_async(self, *, on_event, **_kwargs):
            for index, status in ((1, "SUCCEEDED"), (2, "FAILED"), (3, "FAILED")):
                await on_event({"type": "model_request", "request_index": index, "transport_attempt": 1})
                if status == "SUCCEEDED":
                    await on_event({"type": "model_response", "request_index": index, "transport_attempt": 1, "status": status, "usage": {"input_tokens": 2}})
                    continue
                error = {
                    "code": "PROVIDER_ERROR",
                    "message": "safe public failure",
                    "retryable": True,
                }
                if index == 2:
                    error.update({"exception_class": "ProviderResponseError", "category": "RESPONSE_SCHEMA"})
                elif exception_kind == "transport":
                    error["category"] = "TLS"
                elif exception_kind == "category_then_type":
                    error["category"] = "CONNECT"
                elif exception_kind == "known_transport_then_generic":
                    error.update({"exception_class": "ProviderTransportError", "category": "CONNECT"})
                elif exception_kind not in {"type", "unknown"}:
                    error["exception_class"] = exception_class
                await on_event({"type": "model_response", "request_index": index, "transport_attempt": 1, "status": status, "error": error, "usage": {"input_tokens": index}, "response_end_kind": "ERROR"})
            raise make_exception()

    async def scenario():
        trace = Trace()
        runtime = AgentRuntime(
            Adapter(), _config(), history=[{"role": "user", "content": "test"}],
            stream_sink=_StreamSink(), checkpoint_loader=lambda _ctx: None,
            checkpoint_sink=_CheckpointSink(), context_inspector=trace,
        )
        result = await runtime.run_async(run_id="run-native-error-diagnostic")
        assert result.status == "FAILED"
        assert result.error == {"code": "PROVIDER_ERROR", "message": "模型服务调用失败", "retryable": True}
        latest_by_request = {call.request_index: call for call in trace.saved}
        assert latest_by_request[1].status == "SUCCEEDED"
        assert "provider_error_diagnostic" not in latest_by_request[1].model_parameters_summary
        earlier_failure = latest_by_request[2]
        assert earlier_failure.status == "FAILED"
        assert earlier_failure.model_parameters_summary["provider_error_diagnostic"] == {
            "exception_class": "ProviderResponseError", "reason_category": "RESPONSE_SCHEMA",
        }
        latest_failure = latest_by_request[3]
        terminal_versions = [call for call in trace.saved if call.request_index == 3]
        assert len(terminal_versions) >= 2
        original_terminal = terminal_versions[0]
        assert latest_failure.status == "FAILED"
        assert latest_failure.error["message"] == "safe public failure"
        assert latest_failure.usage.input_tokens == 3
        assert latest_failure.model_parameters_summary["provider_response_end_kind"] == "ERROR"
        expected_terminal_diagnostic = (
            {"exception_class": "ProviderTransportError", "reason_category": "TLS"}
            if exception_kind == "transport" else expected_diagnostic
        )
        assert latest_failure.model_parameters_summary["provider_error_diagnostic"] == expected_terminal_diagnostic
        assert "secret://provider" not in repr(latest_failure.model_parameters_summary)
        assert latest_failure.finished_at == original_terminal.finished_at

    asyncio.run(scenario())


class _ReadService:
    def validate(self, _ctx, *, tool_name, arguments, **_kwargs):
        assert tool_name == "read"
        assert arguments == {"query": "known"}

    def execute(self, _ctx, *, tool_name, arguments, **_kwargs):
        assert tool_name == "read"
        return {"answer": arguments["query"], "api_token": "must-not-reach-model"}


class _ToolAdapter:
    def __init__(self) -> None:
        self.model_tool_result = None

    async def run_agent_async(self, *, on_event, validate_tool, execute_tool, **_kwargs):
        await on_event({"type": "model_request", "request_index": 1, "transport_attempt": 1, "model_id": "test-model", "message_count": 2, "tool_names": ["read"], "request_content_sha256": "b" * 64})
        call = {"type": "tool_call", "tool_call_id": "call-native-1", "name": "read", "arguments": {"query": "known"}}
        await on_event(call)
        arguments = validate_tool("read", call["arguments"], call["tool_call_id"])
        if asyncio.iscoroutine(arguments):
            arguments = await arguments
        self.model_tool_result = execute_tool("read", arguments, call["tool_call_id"])
        if asyncio.iscoroutine(self.model_tool_result):
            self.model_tool_result = await self.model_tool_result
        await on_event({"type": "tool_result", "tool_call_id": call["tool_call_id"], "name": "read", "result": self.model_tool_result})
        await on_event({"type": "model_response", "request_index": 1, "transport_attempt": 1, "status": "SUCCEEDED", "usage": {"input_tokens": 5, "output_tokens": 2}, "finish_reason": "stop"})
        return {"text": "done", "usage": {"input_tokens": 5, "output_tokens": 2}, "provider_requests": 1, "tool_calls": 1, "history_state": _native_state(), "pending_actions": []}


def test_native_tool_callback_preserves_platform_audit_and_sanitizes_framework_result() -> None:
    adapter = _ToolAdapter()
    events = InMemoryAgentEventStore()
    tool = BoundTool(
        ToolSpec("read", "Read the authorized record", permission="prediction:read", argument_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        }),
        _ReadService(),
    )
    runtime = AgentRuntime(
        adapter,
        _config(),
        toolset={"read": tool},
        history=[{"role": "user", "content": "read known"}],
        event_store=events,
        stream_sink=_StreamSink(),
        checkpoint_loader=lambda _ctx: None,
        checkpoint_sink=_CheckpointSink(),
    )
    actor = SimpleNamespace(can=lambda _permission: True)
    result = asyncio.run(runtime.run_async(run_id="run-tool", ctx=SimpleNamespace(actor=actor, actor_id="actor-a", agent_attempt_id="attempt-a")))

    lifecycle = [item.type for item in events.list_after("agent_run", "run-tool", 0) if item.type.startswith("tool.")]
    assert result.status == "SUCCEEDED"
    assert result.tool_calls == 1
    assert lifecycle == ["tool.proposed", "tool.started", "tool.completed"]
    assert "must-not-reach-model" not in str(adapter.model_tool_result)
    assert "api_token" not in str(result.tool_results)


class _DeferredAdapter:
    def __init__(self) -> None:
        self.received = None

    def complete_deferred_history(self, *, history_state, tool_call_id, trusted_result):
        self.received = (tool_call_id, trusted_result)
        return {**history_state, "confirmed_tool_call_id": tool_call_id, "confirmed_result": trusted_result}


def test_confirmed_tool_result_updates_only_private_history_without_provider_call() -> None:
    adapter, checkpoints = _DeferredAdapter(), _CheckpointSink()
    stored = {
        "engine": "pydantic-ai",
        "framework_version": "2.56.0",
        "serialization_version": "ModelMessagesTypeAdapter",
        "history_state": _native_state(),
        "usage": {"input_tokens": 4, "output_tokens": 1},
        "pending_actions": [{"tool_call_id": "tool-platform-1", "provider_tool_call_id": "call-wire-1", "action": "draft.submit"}],
        "config_hash": _CONFIG_HASH,
        "stream_message_id": "msg-assistant",
    }
    runtime = AgentRuntime(
        adapter,
        _config(),
        checkpoint_loader=lambda _ctx: stored,
        checkpoint_sink=checkpoints,
    )
    result = asyncio.run(runtime.complete_deferred_history(
        ctx=SimpleNamespace(),
        tool_call_id="tool-platform-1",
        trusted_result={"status": "SUCCEEDED", "job_id": "job-1"},
    ))

    assert adapter.received == ("call-wire-1", {"status": "SUCCEEDED", "job_id": "job-1"})
    assert result["pending_actions"] == []
    assert result["history_state"]["confirmed_tool_call_id"] == "call-wire-1"
    assert checkpoints.states == [result]


class _MustNotRunAdapter:
    def __init__(self):
        self.calls = 0

    async def run_agent_async(self, **_kwargs):
        self.calls += 1
        raise AssertionError("pending confirmation must not resume the Agent")


def test_pending_checkpoint_confirmation_dispatch_fails_closed_before_agent_call() -> None:
    adapter = _MustNotRunAdapter()
    stored = {
        "engine": "pydantic-ai",
        "framework_version": "2.56.0",
        "serialization_version": "ModelMessagesTypeAdapter",
        "history_state": _native_state(),
        "usage": {"input_tokens": 4, "output_tokens": 1},
        "pending_actions": [{
            "tool_call_id": "tool-platform-1",
            "provider_tool_call_id": "call-wire-1",
            "tool_name": "submit",
            "confirmation_id": "confirm-1",
            "resource_ref": {"type": "draft", "id": "draft-1"},
            "action": "draft.submit",
            "revision": 1,
            "config_hash": "a" * 64,
            "target_revision_hash": "a" * 64,
            "schema_version": "1.0",
            "normalized_args": {"draft_id": "draft-1"},
        }],
        "config_hash": _CONFIG_HASH,
        "stream_message_id": "msg-assistant",
    }
    runtime = AgentRuntime(
        adapter,
        _config(),
        history=[{"role": "user", "content": "confirm draft-1"}],
        checkpoint_loader=lambda _ctx: stored,
        checkpoint_sink=_CheckpointSink(),
    )

    result = asyncio.run(runtime.run_async(
        run_id="run-pending",
        ctx=SimpleNamespace(confirmation_id="confirm-1"),
    ))

    assert result.status == "FAILED"
    assert result.error["code"] == "CONFIRMATION_STATE_INVALID"
    assert adapter.calls == 0


class _ConfirmationService:
    def validate(self, _ctx, *, tool_name, arguments, **_kwargs):
        assert tool_name == "submit"
        assert arguments == {"draft_id": "draft-1"}

    def execute(self, *_args, **_kwargs):
        raise AssertionError("confirmation must not execute the business operation")


class _StrictToolPersistence:
    def __init__(self):
        self.tool_calls = []

    def record_tool_call_event(self, call, _event_type, _data, **_kwargs):
        if call.pending_confirmation is not None:
            checked = PendingConfirmationBinding.model_validate(call.pending_confirmation)
            assert checked.tool_call_id == call.id
        self.tool_calls.append(call)
        return True

    def capture_pair(self, **_kwargs):
        return SimpleNamespace(id="logical"), SimpleNamespace(id="provider")

    def save_llm_call(self, _call):
        pass


class _ConfirmationAdapter:
    async def run_agent_async(self, *, on_event, validate_tool, execute_tool, **_kwargs):
        call = {"type": "tool_call", "tool_call_id": "call-wire-confirm", "name": "submit", "arguments": {"draft_id": "draft-1"}}
        await on_event({"type": "model_request", "request_index": 1, "transport_attempt": 1, "request_content_sha256": "d" * 64})
        await on_event(call)
        arguments = validate_tool("submit", call["arguments"], call["tool_call_id"])
        if asyncio.iscoroutine(arguments):
            arguments = await arguments
        pending = execute_tool("submit", arguments, call["tool_call_id"])
        if asyncio.iscoroutine(pending):
            pending = await pending
        assert pending["status"] == "WAITING_CONFIRMATION"
        await on_event({"type": "model_response", "request_index": 1, "transport_attempt": 1, "status": "SUCCEEDED"})
        return {
            "text": None,
            "usage": {},
            "provider_requests": 1,
            "tool_calls": 1,
            "history_state": _native_state(),
            "pending": True,
            "pending_actions": [{"tool_call_id": call["tool_call_id"], "action": "draft.submit"}],
        }


def test_confirmation_tool_stops_run_and_persists_only_platform_binding() -> None:
    checkpoints = _CheckpointSink()
    persistence = _StrictToolPersistence()
    tool = BoundTool(
        ToolSpec(
            "submit",
            "Submit the draft",
            permission="draft:submit",
            requires_confirmation=True,
            action="draft.submit",
            argument_schema={
                "type": "object",
                "properties": {"draft_id": {"type": "string"}},
                "required": ["draft_id"],
                "additionalProperties": False,
            },
        ),
        _ConfirmationService(),
    )
    runtime = AgentRuntime(
        _ConfirmationAdapter(),
        _config(),
        toolset={"submit": tool},
        history=[{"role": "user", "content": "submit draft-1"}],
        stream_sink=_StreamSink(),
        checkpoint_loader=lambda _ctx: None,
        checkpoint_sink=checkpoints,
        context_inspector=persistence,
    )
    runtime._execute_tool = lambda *_args, **_kwargs: ToolPending(
        tool_name="submit", action="draft.submit", normalized_args={"draft_id": "draft-1"},
        target_revision_hash="a" * 64, resource_ref=ResourceRef(type="draft", id="draft-1"),
        revision=3, config_hash="b" * 64,
    )

    result = asyncio.run(runtime.run_async(
        run_id="run-confirm",
        ctx=SimpleNamespace(actor=SimpleNamespace(can=lambda _permission: True), actor_id="actor-a"),
    ))

    assert result.status == "WAITING_CONFIRMATION"
    assert len(result.pending_actions) == 1
    assert "provider_tool_call_id" not in result.pending_actions[0]
    persisted = checkpoints.states[-1]["pending_actions"][0]
    assert persisted["provider_tool_call_id"] == "call-wire-confirm"
    assert persisted["tool_call_id"] != "call-wire-confirm"
    saved = next(call for call in persistence.tool_calls if call.status == "WAITING_CONFIRMATION")
    assert saved.provider_tool_call_id == "call-wire-confirm"
    assert set(saved.pending_confirmation.model_dump()) == {
        "tool_call_id", "confirmation_id", "resource_ref", "action", "revision",
        "config_hash", "schema_version", "normalized_args", "expires_at",
    }
    from chrom_platform.modules.assistant.service import _binding_hash, _hash
    assert saved.arguments_hash == _hash(saved.pending_confirmation.normalized_args)
    assert saved.binding_hash == _binding_hash(saved.pending_confirmation)
