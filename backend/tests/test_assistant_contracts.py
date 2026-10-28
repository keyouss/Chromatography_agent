from __future__ import annotations

import pytest
from pydantic import ValidationError

from chrom_platform.modules.assistant.schemas import (
    ConversationCreate,
    ResumeAgentRunRequest,
    UserMessageCreate,
)


def test_client_message_is_strict_and_cannot_supply_server_history_or_identity() -> None:
    for field, value in {
        "role": "user", "history": [], "tool_result": {}, "actor_id": "a",
        "tenant_id": "t", "permissions": ["*"], "credentials": {},
        "recovery": {}, "raw_message_history": [],
    }.items():
        with pytest.raises(ValidationError):
            UserMessageCreate.model_validate({"content": "hi", "client_message_id": "m", field: value})


def test_resume_accepts_only_confirmation_id() -> None:
    assert ResumeAgentRunRequest(confirmation_id="confirm-1").confirmation_id == "confirm-1"
    with pytest.raises(ValidationError):
        ResumeAgentRunRequest.model_validate({"confirmation_id": "confirm-1", "tool_result": {}})
    with pytest.raises(ValidationError):
        ResumeAgentRunRequest.model_validate({"reason": "recovery"})


def test_conversation_dto_forbids_tenant_and_credentials() -> None:
    with pytest.raises(ValidationError):
        ConversationCreate.model_validate({"project_id": "p", "mode": "business", "tenant_id": "t"})
    with pytest.raises(ValidationError):
        ConversationCreate.model_validate({"project_id": "p", "mode": "business", "credentials": {}})
