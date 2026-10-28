from __future__ import annotations

import ast
from pathlib import Path

import pytest

from chrom_platform.assembly.profile import (
    _trim_prediction_api_registry,
    _trim_prediction_assembly,
)


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "backend" / "src" / "chrom_platform"


def _dto_types(source: str) -> set[str]:
    tree = ast.parse(source)
    for node in tree.body:
        value = None
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "_DTO_CLASS_NAMES"
            for target in node.targets
        ):
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "_DTO_CLASS_NAMES":
            value = node.value
        if value is not None:
            assert isinstance(value, ast.Dict)
            return {
                key.value for key in value.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            }
    raise AssertionError("_DTO_CLASS_NAMES missing")


@pytest.mark.parametrize(
    ("selected", "expected_validator"),
    [
        ({"tlc"}, "adapters.tlc.validation"),
        ({"cc"}, "adapters.cc.validation"),
        ({"hplc"}, "adapters.hplc.validation"),
        ({"gc"}, "adapters.gc.validation"),
        ({"sfc"}, "adapters.sfc.validation"),
    ],
)
def test_api_registry_keeps_only_selected_dto_and_validation_branches(
    selected: set[str], expected_validator: str,
) -> None:
    source = (SOURCE / "modules" / "prediction" / "api_registry.py").read_text(encoding="utf-8")
    trimmed = _trim_prediction_api_registry(source, selected)
    tree = ast.parse(trimmed)

    assert _dto_types(trimmed) == selected & {"tlc", "cc", "hplc", "gc", "sfc"}
    assert expected_validator in trimmed
    for chromatography_type, validator in (
        ("tlc", "adapters.tlc.validation"),
        ("cc", "adapters.cc.validation"),
        ("hplc", "adapters.hplc.validation"),
        ("gc", "adapters.gc.validation"),
        ("sfc", "adapters.sfc.validation"),
    ):
        if chromatography_type not in selected:
            assert validator not in trimmed
    assert tree.body

    if "sfc" not in selected:
        for token in (
            "SfcCandidateCatalog", "SfcModelDomain", "sfc_domain_data",
            "allowed_tool", "registry_entry", "catalog_data", "catalog_source_proof",
            "_catalog=", "_load_sfc_catalog", "catalog_for_scope", "SimpleNamespace",
            "_sha256_file",
        ):
            assert token not in trimmed


@pytest.mark.parametrize(
    ("selected", "retained_import"),
    [
        ({"cc"}, "from chrom_platform.adapters.cc.adapter import build_cc_adapter"),
        ({"hplc"}, "from chrom_platform.adapters.hplc.adapter import build_hplc_adapter"),
        ({"gc"}, "from chrom_platform.adapters.gc.adapter import build_gc_adapter"),
    ],
)
def test_prediction_assembly_retains_selected_optional_registration_only(
    selected: set[str], retained_import: str,
) -> None:
    source = (SOURCE / "assembly" / "prediction.py").read_text(encoding="utf-8")
    trimmed = _trim_prediction_assembly(source, selected)
    ast.parse(trimmed)
    assert retained_import in trimmed
    for optional_type, adapter_import in (
        ("cc", "from chrom_platform.adapters.cc.adapter import build_cc_adapter"),
        ("hplc", "from chrom_platform.adapters.hplc.adapter import build_hplc_adapter"),
        ("gc", "from chrom_platform.adapters.gc.adapter import build_gc_adapter"),
    ):
        if optional_type not in selected:
            assert adapter_import not in trimmed
    assert "_register_optional_adapters(registry, session_factory=session_factory)" in trimmed
    assert 'capability.id == "sfc"' not in trimmed
    assert "catalog_source_proof" not in trimmed
    assert "SFC_FULL_REFERENCE_SHA256" not in trimmed


def test_sfc_only_assembly_has_no_optional_adapter_registrations() -> None:
    source = (SOURCE / "assembly" / "prediction.py").read_text(encoding="utf-8")
    trimmed = _trim_prediction_assembly(source, {"sfc"})
    ast.parse(trimmed)
    for token in (
        "_optional_asset_root", "_register_optional_adapters",
        "build_cc_adapter", "build_hplc_adapter", "build_gc_adapter",
    ):
        assert token not in trimmed


def test_cc_runtime_bytes_are_not_rewritten_by_profile_source() -> None:
    profile_source = (SOURCE / "assembly" / "profile.py").read_text(encoding="utf-8")
    assert "cc_runtime.write_text" not in profile_source
    assert "SFC Predictor v1.1 CC offline entry point" not in profile_source
