"""The existing platform confirmation service remains the only executor."""
from types import SimpleNamespace

import pytest

from chrom_platform.core.dto import ResourceRef
from chrom_platform.modules.assistant import adapter as adapter_module
from chrom_platform.modules.assistant.persistence import AssistantFrameworkStateRow, MessageRow
from chrom_platform.modules.assistant.schemas import ResumeAgentRunRequest, ToolCallDraft, UserMessageCreate
from test_assistant_service import Business, Confirmation, conversation, service


def setup_pending(monkeypatch):
    from pydantic_ai import __version__
    from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelResponse, ToolCallPart
    business = Business()
    platform, context = service(business={"create_prediction": business}, confirmation=Confirmation())
    conv = conversation(platform, context)
    accepted = platform.send_message(context, conv.id, UserMessageCreate(content="run", client_message_id="native-confirm"))
    draft = ToolCallDraft(provider_tool_call_id="native-provider-call", tool_name="create_prediction",
        schema_version="1.0", normalized_args={"x": 1}, resource_ref=ResourceRef(type="prediction", id="p1"),
        action="prediction:create", revision=1, config_hash="b" * 64, requires_confirmation=True)
    call = platform.propose_tool_call(context, accepted.agent_run_id, draft)
    repo = platform.repository
    run = repo.runs[accepted.agent_run_id]
    repo.runs[run.id] = run.model_copy(update={"model_id": "native-model", "provider_configuration_id": "cfg-native",
        "provider_configuration_revision": "rev-native", "provider_configuration_hash": "a" * 64})
    pending = repo.runs[run.id].pending_actions[0]
    private_pending = {**pending.model_dump(mode="json"), "confirmation_id": "confirm-native",
                       "provider_tool_call_id": call.provider_tool_call_id, "tool_name": call.tool_name}
    native_history = {
        "framework": "pydantic-ai", "framework_version": __version__, "serialization": "ModelMessagesTypeAdapter",
        "config_binding": {"model_id": "native-model", "config_revision": "rev-native", "endpoint_sha256": "d" * 64,
                           "provider_configuration_hash": "a" * 64},
        "messages": ModelMessagesTypeAdapter.dump_python([
            ModelResponse(parts=[ToolCallPart(tool_name=call.tool_name, args={"x": 1}, tool_call_id=call.provider_tool_call_id)]),
        ], mode="json"),
        "pending_actions": [{"tool_call_id": call.provider_tool_call_id, "name": call.tool_name}],
    }
    framework = SimpleNamespace(engine="pydantic-ai", project_id="p", conversation_id=conv.id,
        config_hash="a" * 64, stream_message_id="stream-message",
        state={"engine": "pydantic-ai", "config_hash": "a" * 64,
               "history_state": native_history, "pending_actions": [private_pending]})
    streamed = SimpleNamespace(id="stream-message", agent_run_id=run.id, status="PARTIAL")
    repo.is_test_only = False

    def scalar(statement):
        entity = statement.column_descriptions[0]["entity"]
        if entity is AssistantFrameworkStateRow:
            return framework
        if entity is MessageRow:
            return streamed
        raise AssertionError("unexpected database query")

    repo.scalar = scalar

    async def forbidden_agent(*_args, **_kwargs):
        pytest.fail("confirmation must not call the Agent")

    monkeypatch.setattr(adapter_module.PydanticAIAdapter, "run_agent_async", forbidden_agent)
    return platform, context, business, run.id, framework, streamed


def test_confirmation_executes_once_and_pairs_trusted_result_without_agent(monkeypatch):
    platform, context, business, run_id, framework, streamed = setup_pending(monkeypatch)
    result = platform.resume_run(context, run_id, ResumeAgentRunRequest(confirmation_id="confirm-native"))
    assert result.status == "SUCCEEDED"
    assert business.executed == 1
    assert framework.state["pending_actions"] == []
    assert streamed.status == "FINAL"
    serialized = framework.state["history_state"]
    assert serialized["pending_actions"] == []
    returns = [part for message in serialized["messages"] for part in message["parts"] if part["part_kind"] == "tool-return"]
    assert len(returns) == 1
    assert returns[0]["tool_call_id"] == "native-provider-call"
    assert "must-not-leak" not in str(serialized)
    replay = platform.resume_run(context, run_id, ResumeAgentRunRequest(confirmation_id="confirm-native"))
    assert replay.status == "SUCCEEDED"
    assert business.executed == 1


@pytest.mark.parametrize("mutation", ["version", "binding", "duplicate", "inner-multiple"])
def test_incompatible_private_history_is_rejected_before_business(monkeypatch, mutation):
    platform, context, business, run_id, framework, _streamed = setup_pending(monkeypatch)
    if mutation == "version":
        framework.state["history_state"]["framework_version"] = "0.0.0"
    elif mutation == "binding":
        framework.state["history_state"]["config_binding"]["provider_configuration_hash"] = "f" * 64
    elif mutation == "duplicate":
        framework.state["pending_actions"].append(dict(framework.state["pending_actions"][0]))
    else:
        framework.state["history_state"]["pending_actions"].append({"tool_call_id": "another-provider-call"})
    with pytest.raises(Exception) as error:
        platform.resume_run(context, run_id, ResumeAgentRunRequest(confirmation_id="confirm-native"))
    assert getattr(error.value, "status_code", 409) == 409
    assert business.executed == 0
