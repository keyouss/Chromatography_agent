from __future__ import annotations

import json
import hashlib
from pathlib import Path
import sys
from types import ModuleType
import tomllib

from chrom_platform.assembly.profile import _copy_tree, assemble_profile
from chrom_platform.core.dto import BuildManifest


ROOT = Path(__file__).resolve().parents[2]
RESOURCE_FILES = {
    "gc": {"gc-reference-preprocessing-v2.json"},
    "sfc": {
        "sfc-full-reference.json",
        "sfc-rs12-full-column-reference-coupled-20261020-v2.json",
        "sfc-single-reference-2p5-warning-v3.json",
        "sfc-single-reference.json",
    },
    "tlc": {"tlc.smoke.json"},
}


def _manifest(kind: str, tools: list[str]) -> BuildManifest:
    return BuildManifest(
        manifest_version="1.0",
        build_id=f"resource-profile-{kind}",
        modules=["prediction"],
        chromatography_types=[kind],
        prediction_tools={kind: tools},
        utilities=[],
        export_formats=[],
        auth_provider="deployment_default",
    )


def test_prediction_profiles_copy_only_selected_reference_resources(tmp_path: Path) -> None:
    source = ROOT / "backend" / "src"
    for kind, expected in RESOURCE_FILES.items():
        package_root = tmp_path / kind
        tools = ["single_column"] if kind == "sfc" else ["single"]
        _copy_tree(source, package_root, _manifest(kind, tools))
        resources = package_root / "chrom_platform" / "resources"
        assert {path.name for path in resources.glob("*.json")} == expected
        for name in expected:
            assert (resources / name).read_bytes() == (source / "chrom_platform" / "resources" / name).read_bytes()

    batch_root = tmp_path / "tlc-batch"
    _copy_tree(source, batch_root, _manifest("tlc", ["single", "batch"]))
    batch_resources = batch_root / "chrom_platform" / "resources"
    assert {path.name for path in batch_resources.glob("*.json")} == {"tlc.smoke.json", "batch-scan-manifest.json"}


def test_assembled_profile_manifest_hashes_packaged_resources(tmp_path: Path) -> None:
    manifest_path = tmp_path / "tlc-manifest.json"
    manifest_path.write_text(
        json.dumps(_manifest("tlc", ["single"]).model_dump(mode="json")), encoding="utf-8",
    )
    package_root = assemble_profile(manifest_path, tmp_path / "profiles")
    source_manifest = json.loads((package_root / "SOURCE_MANIFEST.json").read_text(encoding="utf-8"))
    resources = {
        row["path"]: row["sha256"] for row in source_manifest["files"]
        if row["path"].startswith("chrom_platform/resources/")
    }
    assert set(resources) == {"chrom_platform/resources/tlc.smoke.json"}
    assert resources["chrom_platform/resources/tlc.smoke.json"] == hashlib.sha256(
        (package_root / "chrom_platform/resources/tlc.smoke.json").read_bytes()
    ).hexdigest()


def test_data_window_code_stays_in_its_exact_datasets_only_profile(tmp_path: Path) -> None:
    source = ROOT / "backend" / "src"
    for build_id, selected in (
        ("ordinary-datasets", False),
        ("data-center-invalid-window-20261026", True),
    ):
        manifest = BuildManifest(
            manifest_version="1.0",
            build_id=build_id,
            modules=["datasets"],
            chromatography_types=[],
            prediction_tools={},
            utilities=[],
            export_formats=[],
            auth_provider="deployment_default",
        )
        package_root = tmp_path / build_id
        _copy_tree(source, package_root, manifest)
        artifact_core = package_root / "chrom_platform" / "artifact_core"
        assert (artifact_core / "data_csv_window.py").is_file() is selected
        assert (artifact_core / "dataset_csv_elevated_runner.py").is_file() is selected


def test_prediction_assembly_passes_the_packaged_gc_fixture(monkeypatch) -> None:
    import chrom_platform.assembly.prediction as prediction

    captured: dict[str, object] = {}
    adapter_module = ModuleType("chrom_platform.adapters.gc.adapter")
    policy_module = ModuleType("chrom_platform.assembly.gc_candidate_policy")
    adapter_module.build_gc_adapter = lambda **kwargs: captured.update(kwargs) or "gc-adapter"
    policy_module.maybe_build_reviewed_gc_candidate = lambda adapter: adapter
    monkeypatch.setitem(sys.modules, "chrom_platform.adapters.gc.adapter", adapter_module)
    monkeypatch.setitem(sys.modules, "chrom_platform.assembly.gc_candidate_policy", policy_module)
    monkeypatch.setattr(
        prediction, "_optional_asset_root",
        lambda env_name, _folder: "model-root" if env_name == "CHROM_GC_MODEL_ROOT" else None,
    )

    class Registry:
        def __init__(self) -> None:
            self.adapters: list[object] = []

        def register(self, adapter: object) -> None:
            self.adapters.append(adapter)

    registry = Registry()
    prediction._register_optional_adapters(registry, session_factory=None)

    fixture = Path(captured["golden_fixture"])
    expected = ROOT / "backend" / "src" / "chrom_platform" / "resources" / "gc-reference-preprocessing-v2.json"
    assert fixture.resolve() == expected.resolve()
    assert fixture.is_file()
    assert registry.adapters == ["gc-adapter"]
    assert "testing" not in str(fixture).lower()


def test_wheel_package_data_includes_json_resources() -> None:
    config = tomllib.loads((ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8"))
    assert config["tool"]["setuptools"]["package-data"]["chrom_platform"] == ["resources/*.json"]
