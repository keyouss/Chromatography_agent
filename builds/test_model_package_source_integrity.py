from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil

import pytest

from chrom_platform.assembly import profile as profile_builder
from chrom_platform.assembly.manifest import load_manifest


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "backend" / "src"
INVENTORY_PATH = Path(__file__).with_name("model_package_source_inventory.json")
HELPER_PATH = Path(__file__).with_name("model_package_source_integrity.py")


def _integrity():
    spec = importlib.util.spec_from_file_location("_test_model_package_source_integrity", HELPER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _copy_inventory_tree(destination: Path, *, inventory_path: Path = INVENTORY_PATH) -> None:
    for relative in _integrity().load_inventory(inventory_path):
        source = SOURCE_ROOT / Path(*Path(relative).parts)
        target = destination / Path(*Path(relative).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def test_valid_inventory_and_gc_source_copy_closure_pass(tmp_path: Path) -> None:
    integrity = _integrity()
    entries = integrity.load_inventory()
    assert len(entries) == 12
    integrity.verify_source_tree(SOURCE_ROOT)

    target = tmp_path / "copied"
    _copy_inventory_tree(target)
    integrity.verify_gc_profile_copy(SOURCE_ROOT, target, model_center_selected=True)


@pytest.mark.parametrize("bad_path", [
    "C:/outside.py",
    "chrom_platform/../outside.py",
    "chrom_platform/adapters/gc/package_loader.py",
    "chrom_platform/unknown/helper.py",
])
def test_inventory_rejects_absolute_traversal_duplicate_and_unknown_paths(
    tmp_path: Path, bad_path: str,
) -> None:
    raw = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
    if bad_path == "chrom_platform/adapters/gc/package_loader.py":
        raw["files"][0]["path"] = bad_path
    else:
        raw["files"].append({"path": bad_path, "sha256": "0" * 64})
    candidate = tmp_path / "inventory.json"
    candidate.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError):
        _integrity().load_inventory(candidate)


def test_changed_runtime_pin_inventory_is_rejected(tmp_path: Path) -> None:
    integrity = _integrity()
    source = tmp_path / "source"
    _copy_inventory_tree(source)
    pin_path = source / "chrom_platform/adapters/gc/package_source_pins.py"
    pin_path.write_text(pin_path.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="MODEL_PACKAGE_SOURCE_HASH_MISMATCH"):
        integrity.verify_source_tree(source)


@pytest.mark.parametrize("relative", [
    "chrom_platform/adapters/gc/validation.py",
    "chrom_platform/core/model_load_observation.py",
])
def test_missing_runtime_guard_source_fails_closed(tmp_path: Path, relative: str) -> None:
    integrity = _integrity()
    source = tmp_path / "source"
    target = tmp_path / "target"
    _copy_inventory_tree(source)
    _copy_inventory_tree(target)

    (source / relative).unlink()
    with pytest.raises(ValueError):
        integrity.verify_source_tree(source)

    (target / relative).unlink()
    with pytest.raises(ValueError):
        integrity.verify_gc_profile_copy(SOURCE_ROOT, target, model_center_selected=True)


@pytest.mark.parametrize("relative", [
    "chrom_platform/adapters/gc/validation.py",
    "chrom_platform/core/model_load_observation.py",
])
def test_changed_runtime_guard_source_fails_closed(tmp_path: Path, relative: str) -> None:
    integrity = _integrity()
    source = tmp_path / "source"
    _copy_inventory_tree(source)
    path = source / relative
    path.write_text(path.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="MODEL_PACKAGE_SOURCE_HASH_MISMATCH"):
        integrity.verify_source_tree(source)


def test_gc_profile_copy_hook_runs_and_keeps_fixed_closure(tmp_path: Path) -> None:
    raw = json.loads((ROOT / "builds/universal-v1.json").read_text(encoding="utf-8"))
    raw.update({
        "build_id": "gc-integrity-positive",
        "modules": ["prediction"],
        "chromatography_types": ["gc"],
        "prediction_tools": {"gc": ["single"]},
        "utilities": [],
    })
    raw.pop("assistant_features", None)
    manifest_path = tmp_path / "gc-integrity-positive.json"
    manifest_path.write_text(json.dumps(raw), encoding="utf-8")

    target = tmp_path / "package"
    profile_builder._copy_tree(SOURCE_ROOT, target, load_manifest(manifest_path))
    for relative in (
        "chrom_platform/adapters/gc/package_source_pins.py",
        "chrom_platform/adapters/gc/package_loader.py",
        "chrom_platform/adapters/gc/package_model.py",
        "chrom_platform/artifact_core/model_package_manifest.py",
        "chrom_platform/adapters/gc/validation.py",
        "chrom_platform/core/model_load_observation.py",
        "chrom_platform/modules/prediction/schemas.py",
    ):
        assert (target / relative).is_file(), relative
    assert not (target / "chrom_platform/modules/model_center").exists()
    assert not (target / "builds").exists()


def test_gc_source_preflight_runs_before_any_profile_copy(tmp_path: Path, monkeypatch) -> None:
    raw = json.loads((ROOT / "builds/universal-v1.json").read_text(encoding="utf-8"))
    raw.update({
        "build_id": "gc-integrity-preflight-order",
        "modules": ["prediction"],
        "chromatography_types": ["gc"],
        "prediction_tools": {"gc": ["single"]},
        "utilities": [],
    })
    raw.pop("assistant_features", None)
    manifest_path = tmp_path / "gc-integrity-preflight-order.json"
    manifest_path.write_text(json.dumps(raw), encoding="utf-8")

    events: list[str] = []

    class Integrity:
        def verify_source_tree(self, source_root: Path) -> None:
            events.append("source-preflight")

    monkeypatch.setattr(profile_builder, "_load_gc_profile_source_integrity", lambda: Integrity())
    original_copy = profile_builder._copy_package_tree

    def tracked_copy(source_root: Path, target_root: Path, relative_files) -> None:
        events.append("copy")
        original_copy(source_root, target_root, relative_files)

    monkeypatch.setattr(profile_builder, "_copy_package_tree", tracked_copy)
    monkeypatch.setattr(
        profile_builder,
        "_verify_gc_profile_sources",
        lambda *args, **kwargs: events.append("copied-closure-check"),
    )

    profile_builder._copy_tree(
        SOURCE_ROOT,
        tmp_path / "package",
        load_manifest(manifest_path),
    )
    assert events == ["source-preflight", "copy", "copied-closure-check"]


def test_non_gc_profile_does_not_load_source_integrity_helper(tmp_path: Path, monkeypatch) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("non-GC profile must not import or call GC source checker")

    monkeypatch.setattr(profile_builder, "_verify_gc_profile_sources", forbidden)
    target = tmp_path / "non-gc"
    profile_builder._copy_tree(
        SOURCE_ROOT,
        target,
        load_manifest(ROOT / "builds/sfc-v1.json"),
    )
    assert not (target / "chrom_platform/adapters/gc").exists()
    assert not (target / "builds").exists()
