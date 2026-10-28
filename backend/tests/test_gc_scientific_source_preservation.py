from __future__ import annotations

import ast
import hashlib
import importlib
import json
import sys
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "backend" / "src"
PLATFORM = SRC / "chrom_platform"
GC = PLATFORM / "adapters" / "gc"


def _assigned_dict(path: Path, name: str) -> dict[str, str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        value = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            value = node.value
        elif isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            value = node.value
        if isinstance(value, ast.Dict):
            return {
                key.value: item.value
                for key, item in zip(value.keys, value.values)
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
                and isinstance(item, ast.Constant) and isinstance(item.value, str)
            }
    raise AssertionError(f"{name} dictionary missing from {path}")


def test_gc_scientific_sources_descriptor_and_policy_remain_bound() -> None:
    expected_adapter = "6a66fd42e98291cb082f385fb251cd62768e1151b573517290a26e468c4ae839"
    expected_runtime = "d63f1f523d355b1e5a13a5dfb4d938cdc237e66f2ce7454a21f3b308bc54eea2"
    expected_profile = "a3c1dc6d576973682357818e2424e0640617ec4a959d40d71fbbb40a733b8799"
    hashes = {
        "adapter.py": expected_adapter,
        "runtime.py": expected_runtime,
    }
    for filename, expected in hashes.items():
        assert hashlib.sha256((GC / filename).read_bytes()).hexdigest() == expected

    pin_tree = ast.parse((GC / "source_pins.py").read_text(encoding="utf-8"))
    pins = {
        node.targets[0].id: node.value.value
        for node in pin_tree.body
        if isinstance(node, ast.Assign) and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name) and isinstance(node.value, ast.Constant)
    }
    assert pins["GC_ADAPTER_SOURCE_SHA256"] == expected_adapter
    assert pins["GC_RUNTIME_SOURCE_SHA256"] == expected_runtime

    # Load only the GC namespace and metadata modules; do not import the GC adapter or runtime.
    package_name = "chrom_platform.adapters.gc"
    prior_modules = {name: value for name, value in sys.modules.items()
                     if name == package_name or name.startswith(package_name + ".")}
    for name in prior_modules:
        sys.modules.pop(name, None)
    namespace = ModuleType(package_name)
    namespace.__path__ = [str(GC)]
    sys.modules[package_name] = namespace
    try:
        profile = importlib.import_module("chrom_platform.adapters.gc.serving_profile")
        from chrom_platform.assembly.gc_candidate_policy import _POLICY

        descriptor = profile.gc_serving_profile_descriptor()
        assert profile.gc_serving_profile_sha256() == expected_profile
        assert _POLICY.profile_sha256 == expected_profile
        assert _POLICY.adapter_source_sha256 == expected_adapter
        assert _POLICY.runtime_source_sha256 == expected_runtime
        assert descriptor["preprocessing"]["adapter_source_sha256"] == expected_adapter
        assert descriptor["preprocessing"]["runtime_source_sha256"] == expected_runtime
        assert descriptor["model_id"] == _POLICY.model_id
        assert descriptor["model_version"] == _POLICY.model_version
    finally:
        for name in tuple(sys.modules):
            if name.startswith(package_name + "."):
                sys.modules.pop(name, None)
        sys.modules.pop(package_name, None)
        sys.modules.update(prior_modules)


def test_gc_package_source_pins_and_build_inventory_match_exact_files() -> None:
    package_pins = _assigned_dict(GC / "package_source_pins.py", "PACKAGE_SOURCE_SHA256")
    inventory = json.loads((ROOT / "builds" / "model_package_source_inventory.json").read_text(encoding="utf-8"))
    inventory_pins = {entry["path"]: entry["sha256"] for entry in inventory["files"]}

    assert len(package_pins) == 11
    assert len(inventory_pins) == 12
    assert inventory_pins["chrom_platform/adapters/gc/package_source_pins.py"] == hashlib.sha256(
        (GC / "package_source_pins.py").read_bytes()
    ).hexdigest()
    assert set(package_pins).issubset(inventory_pins)
    for relative, digest in package_pins.items():
        path = SRC / relative
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == actual
        assert inventory_pins[relative] == actual
    for relative in (
        "chrom_platform/adapters/gc/validation.py",
        "chrom_platform/core/model_load_observation.py",
    ):
        assert relative in package_pins
