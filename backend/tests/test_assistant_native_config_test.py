"""Offline configuration-test orchestration, without a Provider connection."""
import asyncio
import time
from types import SimpleNamespace

import pytest

from chrom_platform.modules.assistant import adapter as adapter_module
from chrom_platform.modules.assistant.production_worker import AssistantLlmConfigurationTestWorker


ENDPOINT = "https://api.siliconflow.cn/v1"


def configuration():
    return SimpleNamespace(id="native-config", revision="native-revision", url=ENDPOINT,
        model_id="native-model", key_reference="dpapi:v1:" + "a" * 32,
        settings={"external_send_policy": "approved_endpoint_only", "config_hash": "a" * 64})


@pytest.mark.parametrize("auth_stage", [None, "connectivity", "tool_calling", "streaming"])
def test_configuration_uses_native_agent_and_stops_after_auth(monkeypatch, auth_stage):
    calls = []
    budget = adapter_module.ProviderRequestBudget(deadline_monotonic=time.monotonic() + 90, max_requests=5)

    class NativeAdapter:
        def __init__(self, **_kwargs):
            pass

        async def run_agent_async(self, **kwargs):
            assert kwargs["request_budget"] is budget
            assert kwargs["config"].provider_configuration_hash == "a" * 64
            stage = ("connectivity", "tool_calling", "streaming")[len(calls)]
            calls.append(stage)
            budget.reserve_request()
            if stage == auth_stage:
                raise adapter_module.ProviderTransportError("HTTP_4XX", http_status=401,
                    message="PRIVATE_PROVIDER_BODY")
            if stage == "tool_calling":
                args = kwargs["validate_tool"]("assistant_test_echo", {"value": "chrom-platform-test"}, "call-echo")
                result = kwargs["execute_tool"]("assistant_test_echo", args, "call-echo")
                assert result == {"echo": "chrom-platform-test"}
                budget.reserve_request()  # Logical Agent continuation, same physical budget.
                return {"text": "echo checked", "tool_calls": 1, "tool_results": [result]}
            assert kwargs["tools"] == []
            await kwargs["on_event"]({"type": "text_delta", "delta": "connected"})
            return {"text": "connected", "tool_calls": 0, "tool_results": []}

    monkeypatch.setattr(adapter_module, "PydanticAIAdapter", NativeAdapter)
    outcome = asyncio.run(AssistantLlmConfigurationTestWorker._test_adapter(
        configuration(), request_budget=budget, approved_endpoints_resolver=lambda: ENDPOINT))
    assert budget.sealed
    assert budget.requests <= 5
    assert "PRIVATE_PROVIDER_BODY" not in str(outcome)
    if auth_stage is None:
        assert outcome["status"] == "SUCCEEDED"
        assert calls == ["connectivity", "tool_calling", "streaming"]
        assert budget.requests == 4
    else:
        assert outcome["status"] == "FAILED"
        assert outcome["error"]["code"] == "PROVIDER_AUTHENTICATION_FAILED"
        assert calls[-1] == auth_stage
        assert len(calls) == ("connectivity", "tool_calling", "streaming").index(auth_stage) + 1
        if auth_stage == "connectivity":
            assert outcome["checks"]["tool_calling"] == "SKIPPED"
            assert outcome["checks"]["streaming"] == "SKIPPED"
        elif auth_stage == "tool_calling":
            assert outcome["checks"]["streaming"] == "SKIPPED"


def test_configuration_echo_rejects_unknown_fields(monkeypatch):
    class NativeAdapter:
        def __init__(self, **_kwargs):
            pass

        async def run_agent_async(self, **kwargs):
            if kwargs["tools"]:
                with pytest.raises(ValueError):
                    kwargs["validate_tool"]("assistant_test_echo", {"value": "x", "script": "x"}, "bad-call")
                with pytest.raises(ValueError):
                    kwargs["execute_tool"]("shell", {"value": "x"}, "bad-call")
                return {"text": "", "tool_calls": 0}
            await kwargs["on_event"]({"type": "text_delta", "delta": "ok"})
            return {"text": "ok"}

    monkeypatch.setattr(adapter_module, "PydanticAIAdapter", NativeAdapter)
    outcome = asyncio.run(AssistantLlmConfigurationTestWorker._test_adapter(
        configuration(), approved_endpoints_resolver=lambda: ENDPOINT))
    assert outcome["checks"]["tool_calling"] == "FAILED"


def test_shared_absolute_deadline_cancels_an_open_native_stream(monkeypatch):
    observed = []

    class NativeAdapter:
        def __init__(self, **_kwargs):
            pass

        async def run_agent_async(self, **_kwargs):
            observed.append("started")
            try:
                await asyncio.sleep(30)
            finally:
                observed.append("closed")

    monkeypatch.setattr(adapter_module, "PydanticAIAdapter", NativeAdapter)
    budget = adapter_module.ProviderRequestBudget(deadline_monotonic=time.monotonic() + 0.05, max_requests=5)
    outcome = asyncio.run(AssistantLlmConfigurationTestWorker._test_adapter(
        configuration(), request_budget=budget, approved_endpoints_resolver=lambda: ENDPOINT))
    assert observed == ["started", "closed"]
    assert outcome["status"] == "FAILED"
    assert budget.sealed
    assert budget.provider_call_state[2] == 0
