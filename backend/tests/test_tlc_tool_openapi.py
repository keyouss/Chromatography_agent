from pathlib import Path
import ast

import pytest

from chrom_platform.app import create_app
from chrom_platform.assembly.profile import _trim_app_source


ROOT = Path(__file__).resolve().parents[2]
TLC_TASKS = {"tlc", "tlc_condition_comparison", "tlc_reverse_solvent"}


@pytest.mark.parametrize("profile", ["universal-v1", "cc-v1", "sfc-v1", "minimal-structure-v1"])
def test_profile_openapi_preserves_supported_tlc_tool_results(profile):
    document = create_app(
        manifest_path=ROOT / "builds" / f"{profile}.json", contract_generation=True,
    ).openapi()
    schemas = document["components"]["schemas"]
    if profile == "universal-v1":
        result = schemas["Prediction"]["properties"]["result"]
        union = next(item for item in result["anyOf"] if "oneOf" in item)
        mapping = union["discriminator"]["mapping"]
        assert TLC_TASKS <= set(mapping)
        assert {mapping[task] for task in TLC_TASKS} <= {item["$ref"] for item in union["oneOf"]}
        comparison = schemas["TlcConditionComparisonResult"]
        assert comparison["properties"]["warnings"]["items"]["type"] == "string"
        assert "warnings" not in comparison["required"]
    else:
        assert "TlcConditionComparisonResult" not in schemas
        assert "TlcReverseResult" not in schemas


@pytest.mark.parametrize("selected", [{"tlc"}, {"cc"}, {"sfc"}])
def test_trimmed_app_keeps_only_selected_result_contracts(selected):
    source = (ROOT / "backend/src/chrom_platform/app.py").read_text(encoding="utf-8")
    tree = ast.parse(_trim_app_source(source, selected, batch_enabled=False))
    assignments = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == "result_refs" for target in node.targets)]
    refs = ast.literal_eval(assignments[0].value)
    assert set(refs) == selected
    if "tlc" in selected:
        assert {task for task, _ in refs["tlc"]} == TLC_TASKS
