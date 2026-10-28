from __future__ import annotations

import pytest

from chrom_platform.core.dto import ActorContext
from chrom_platform.modules.assistant.errors import AssistantError
from chrom_platform.modules.assistant.persistence import InMemoryAssistantRepository
from chrom_platform.modules.assistant.service import AssistantService, _TOOL_PERMISSIONS
from chrom_platform.modules.assistant.tools import TOOL_SPECS, make_toolset
from chrom_platform.task_core.service import OperationContext


_BUILDS = ("full", "universal", "universal-v1", "minimal-structure-v1")


@pytest.mark.parametrize("build", _BUILDS)
def test_legacy_knowledge_toolset_hides_persistent_tools(build: str) -> None:
    names = {spec.name for spec in make_toolset(build=build, mode="knowledge").specs}

    assert "normalize_structure" not in names
    assert "create_registration_draft" not in names
    assert "validate_enantiomer_pair" in names
    if build != "minimal-structure-v1":
        assert {"search_knowledge", "read_citation"} <= names


def test_legacy_business_registration_permission_matches_service_policy() -> None:
    toolset = make_toolset(build="universal-v1", mode="business", permissions={"model:register"})
    draft = toolset.get("create_registration_draft")

    assert draft is not None
    assert draft.permission == "model:register"
    assert make_toolset(build="universal-v1", mode="business", permissions={"model:create"}).get("create_registration_draft") is None


def test_legacy_data_tools_missing_from_service_policy_remain_fail_closed() -> None:
    missing_from_service = set(TOOL_SPECS) - set(_TOOL_PERMISSIONS)
    assert missing_from_service == {
        "list_data_versions",
        "get_data_version",
        "create_data_import_draft",
        "validate_data_import",
        "commit_data_import",
    }

    actor = ActorContext(
        actor_id="actor",
        project_ids=["project-1"],
        auth_method="deployment_default",
        permissions=["*"],
        policy_version="policy-1",
    )
    service = AssistantService(InMemoryAssistantRepository())
    ctx = OperationContext(actor=actor, interaction="assistant")
    for tool_name in missing_from_service:
        with pytest.raises(AssistantError) as error:
            service._authorize_tool(ctx, tool_name)
        assert error.value.payload.code == "PERMISSION_DENIED"
