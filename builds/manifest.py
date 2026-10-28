"""Validate V1 build intent without installing dependencies or importing business modules."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

MODULES = {"prediction", "records", "datasets", "knowledge", "training", "model_center", "utilities", "assistant", "settings"}
TOOLS = {
    "tlc": {"single", "batch", "condition_compare", "reverse_solvent"},
    "cc": {"single", "batch"}, "hplc": {"single", "batch"},
    "sfc": {"single_column", "full_column", "batch"},
    "gc": {"single", "batch"}, "chiral_lc": {"single", "batch"},
}
REQUIRED = {"manifest_version", "build_id", "modules", "chromatography_types", "prediction_tools", "utilities", "export_formats", "auth_provider"}


def unique_strings(value: object, label: str, allowed: set[str]) -> set[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{label}: expected string array")
    selected = set(value)
    if len(selected) != len(value) or selected - allowed:
        raise ValueError(f"{label}: duplicate or unsupported value")
    return selected


def validate(manifest: dict) -> dict:
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be an object")
    if REQUIRED - manifest.keys() or manifest.keys() - REQUIRED - {"assistant_features"}:
        raise ValueError("missing or unknown manifest fields")
    if manifest["manifest_version"] != "1.0" or manifest["auth_provider"] != "deployment_default":
        raise ValueError("unsupported version/auth provider")
    if not isinstance(manifest["build_id"], str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", manifest["build_id"]):
        raise ValueError("invalid build_id")
    modules = unique_strings(manifest["modules"], "modules", MODULES)
    types = unique_strings(manifest["chromatography_types"], "chromatography_types", set(TOOLS))
    utilities = unique_strings(manifest["utilities"], "utilities", {"structure_converter", "molecule_lookup", "tlc_reverse_solvent"})
    unique_strings(manifest["export_formats"], "export_formats", {"json", "csv", "xlsx", "pdf", "png", "zip"})
    tools = manifest["prediction_tools"]
    if not isinstance(tools, dict) or set(tools) != types:
        raise ValueError("prediction_tools must exactly match selected adapters")
    for kind, entries in tools.items():
        if not unique_strings(entries, f"prediction_tools.{kind}", TOOLS[kind]):
            raise ValueError("selected adapter must have a tool")
    if "prediction" in modules and not types:
        raise ValueError("prediction requires at least one adapter")
    if types and "prediction" not in modules and "tlc_reverse_solvent" not in utilities:
        raise ValueError("adapters require a consuming capability")
    if "prediction" not in modules and types and (
        types != {"tlc"} or tools["tlc"] != ["reverse_solvent"]
    ):
        raise ValueError("without prediction only the TLC reverse adapter may be selected")
    if "tlc_reverse_solvent" in utilities and ("tlc" not in types or "reverse_solvent" not in tools["tlc"]):
        raise ValueError("TLC reverse utility requires TLC reverse service")
    if "utilities" not in modules and utilities - {"structure_converter"}:
        raise ValueError("optional utilities require utilities module")
    if "assistant" not in modules and "assistant_features" in manifest:
        raise ValueError("assistant_features must be absent without assistant")
    if "assistant" in modules:
        features = unique_strings(manifest.get("assistant_features"), "assistant_features", {"knowledge", "business", "trace"})
        if not features & {"knowledge", "business"}:
            raise ValueError("assistant requires an executable mode")
        if "knowledge" in features and "knowledge" not in modules:
            raise ValueError("knowledge mode requires knowledge module")

    # Dependencies bring services, never implicitly add management pages.
    services = {"core", "structure_core", "artifact_core", "task_core"}
    if types or modules & {"training", "model_center"}:
        services.add("model_runtime")
    if "training" in modules:
        services.update({"data_version_service", "model_registration_service"})
    return {"build_id": manifest["build_id"], "modules": sorted(modules),
            "adapters": sorted(types), "required_services": sorted(services),
            "structure_converter_required": True}


def validate_file(path: Path) -> dict:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    result = validate(manifest)
    if path.stem != result["build_id"]:
        raise ValueError("file stem must match build_id")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifests", type=Path, nargs="+")
    args = parser.parse_args()
    for path in args.manifests:
        try:
            result = validate_file(path)
        except (ValueError, OSError) as error:
            parser.exit(1, f"{path.name}: {error}\n")
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
