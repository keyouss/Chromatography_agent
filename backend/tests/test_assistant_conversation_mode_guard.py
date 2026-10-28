"""Existing conversation mode is authoritative; no production policy change."""
import pytest

from chrom_platform.modules.assistant.schemas import ConversationCreate, UserMessageCreate
from test_assistant_service import service


@pytest.mark.parametrize("mode", ["knowledge", "business"])
def test_omitted_message_mode_inherits_server_conversation(mode):
    platform, ctx = service()
    conversation = platform.create_conversation(ctx, ConversationCreate(project_id="p", mode=mode))
    accepted = platform.send_message(ctx, conversation.id,
        UserMessageCreate(content="plain text", client_message_id="mode-inherit"))
    assert platform.repository.runs[accepted.agent_run_id].mode == mode


@pytest.mark.parametrize("mode,override", [("knowledge", "business"), ("business", "knowledge")])
def test_explicit_mismatched_message_mode_is_rejected_before_resource_creation(mode, override):
    platform, ctx = service()
    conversation = platform.create_conversation(ctx, ConversationCreate(project_id="p", mode=mode))
    with pytest.raises(Exception) as error:
        platform.send_message(ctx, conversation.id,
            UserMessageCreate(content="plain text", client_message_id="mode-mismatch", mode=override))
    assert (error.value.status_code, error.value.payload.code) == (409, "STATE_CONFLICT")
    assert not platform.repository.messages
    assert not platform.repository.runs
    assert not platform.repository.attempts
