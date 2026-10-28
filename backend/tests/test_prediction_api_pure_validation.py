"""Focused pure-validation fixtures; these tests never import model adapters."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


_ADAPTER_ROOT = Path(__file__).resolve().parents[1] / "src" / "chrom_platform" / "adapters"


def _install_lightweight_adapter_packages() -> None:
    """Skip adapter package __init__ files, which intentionally expose runtimes."""
    for name in ("tlc", "cc", "hplc", "gc", "sfc"):
        module_name = f"chrom_platform.adapters.{name}"
        if module_name not in sys.modules:
            package = ModuleType(module_name)
            package.__package__ = module_name
            package.__path__ = [str(_ADAPTER_ROOT / name)]
            sys.modules[module_name] = package


@pytest.fixture(scope="module", autouse=True)
def lightweight_adapter_packages():
    original = {name: value for name, value in sys.modules.items() if name.startswith("chrom_platform.adapters.")}
    _install_lightweight_adapter_packages()
    schemas = importlib.import_module("chrom_platform.modules.prediction.schemas")
    for name in ("CcPredictionCreate", "GcPredictionCreate", "HplcPredictionCreate", "SfcSingleColumnPredictionCreate", "TlcPredictionCreate"):
        globals()[name] = getattr(schemas, name)
    globals()["PredictionContractError"] = importlib.import_module("chrom_platform.modules.prediction.adapters").PredictionContractError
    yield
    for name in list(sys.modules):
        if name.startswith("chrom_platform.adapters.") and name not in original:
            sys.modules.pop(name, None)
    sys.modules.update(original)

from chrom_platform.core.errors import InputValidationError  # noqa: E402


def _exact(model_id: str, version: str, profile_id: str | None = None, profile_sha: str | None = None) -> dict[str, object]:
    return {
        "mode": "exact",
        "model_id": model_id,
        "model_version": version,
        "preprocessing_profile_id": profile_id,
        "preprocessing_profile_sha256": profile_sha,
    }


def test_tlc_mobile_phase_and_scope_are_checked_without_model_runtime() -> None:
    validation = importlib.import_module("chrom_platform.adapters.tlc.validation")
    from chrom_platform.adapters.tlc.manifest import TLC_SCOPE_ID

    request = TlcPredictionCreate.model_validate({
        "schema_version": "1.0",
        "model_selection": {"mode": "champion", "scope_id": TLC_SCOPE_ID},
        "chromatography_type": "tlc",
        "tool": "single",
        "structure_refs": ["structure-a"],
        "input": {"solvent_components": [
            {"solvent_id": "hexane", "fraction": 0.7},
            {"solvent_id": "ethyl_acetate", "fraction": 0.3},
        ]},
    })
    assert validation.validate_tlc_request(request) is request

    stale_scope = request.model_copy(update={
        "model_selection": request.model_selection.model_copy(update={"scope_id": "other-scope"}),
    })
    with pytest.raises(InputValidationError, match="scope"):
        validation.validate_tlc_request(stale_scope)


class _FakeCcCatalog:
    column_spec_ids = ("silica-4g",)

    def resolve_eluent_system(self, key: str) -> str:
        if key != "eluent-ok":
            raise KeyError("CC_CONDITION_UNAVAILABLE")
        return key

    def resolve_loading_solvent(self, key: str) -> str:
        if key != "solvent-ok":
            raise KeyError("CC_LOADING_SOLVENT_UNAVAILABLE")
        return key

    def validate_numeric_inputs(self, value: object) -> None:
        if not 30.0 <= value.sample_mass_mg <= 327.3:
            raise ValueError("sample_mass_mg must be inside the catalog range")


def test_cc_solvent_column_and_numeric_catalog_checks_are_pure() -> None:
    validation = importlib.import_module("chrom_platform.adapters.cc.validation")
    request = CcPredictionCreate.model_validate({
        "schema_version": "1.0",
        "model_selection": _exact("cc-model", "v1"),
        "chromatography_type": "cc",
        "tool": "single",
        "structure_refs": ["structure-a", "structure-b"],
        "input": {
            "eluent_system_id": "eluent-ok", "loading_solvent_id": "solvent-ok",
            "sample_mass_mg": 100.0, "loading_solvent_volume_ul": 500.0,
            "flow_rate_ml_min": 7.0, "column_spec_id": "silica-4g",
        },
    })
    catalog = _FakeCcCatalog()
    assert validation.validate_cc_request(request, catalog) is request

    bad_column = request.model_copy(update={"input": request.input.model_copy(update={"column_spec_id": "unknown"})})
    with pytest.raises(InputValidationError, match="column_spec_id"):
        validation.validate_cc_request(bad_column, catalog)
    bad_solvent = request.model_copy(update={"input": request.input.model_copy(update={"loading_solvent_id": "unknown"})})
    with pytest.raises(InputValidationError, match="loading solvent"):
        validation.validate_cc_request(bad_solvent, catalog)


def test_hplc_requires_exact_profile_preserves_order_and_rejects_boundary_fraction() -> None:
    validation = importlib.import_module("chrom_platform.adapters.hplc.validation")
    from chrom_platform.adapters.hplc.manifest import HPLC_ARTIFACT_ID, HPLC_COLUMN_NAMES, HPLC_MODEL_VERSION
    from chrom_platform.adapters.hplc.serving_profile import HPLC_SERVING_PROFILE_ID, HPLC_SERVING_PROFILE_SHA256

    def make_request(fraction: float = 0.5, profile_id: str = HPLC_SERVING_PROFILE_ID):
        return HplcPredictionCreate.model_validate({
            "schema_version": "1.1",
            "model_selection": _exact(HPLC_ARTIFACT_ID, HPLC_MODEL_VERSION, profile_id, HPLC_SERVING_PROFILE_SHA256),
            "chromatography_type": "hplc", "tool": "single",
            "structure_refs": ["left-ref", "right-ref"],
            "input": {
                "model_input_smiles": ["N[C@@H](C)O", "N[C@H](C)O"],
                "isopropanol_fraction": fraction, "flow_rate_ml_min": 1.0,
                "column_ids": [HPLC_COLUMN_NAMES[0]],
            },
        })

    request = make_request()
    assert validation.validate_hplc_request(request) is request
    assert request.structure_refs == ["left-ref", "right-ref"]
    assert request.input.model_input_smiles == ["N[C@@H](C)O", "N[C@H](C)O"]
    with pytest.raises(PredictionContractError, match="model/profile"):
        validation.validate_hplc_request(make_request(profile_id="stale-profile"))
    with pytest.raises(PredictionContractError, match="strictly between"):
        validation.validate_hplc_request(make_request(fraction=0.0))


def test_gc_temperature_series_and_exact_profile_are_checked_without_descriptors() -> None:
    validation = importlib.import_module("chrom_platform.adapters.gc.validation")
    from chrom_platform.adapters.gc.manifest import GC_ARTIFACT_ID, GC_MODEL_VERSION
    from chrom_platform.adapters.gc.serving_profile import GC_SERVING_PROFILE_ID, GC_SERVING_PROFILE_SHA256

    series = [float(40 + index) for index in range(30)]
    request = GcPredictionCreate.model_validate({
        "schema_version": "1.1",
        "model_selection": _exact(GC_ARTIFACT_ID, GC_MODEL_VERSION, GC_SERVING_PROFILE_ID, GC_SERVING_PROFILE_SHA256),
        "chromatography_type": "gc", "tool": "single", "structure_refs": ["structure-a"],
        "input": {"temperature_program": {"mode": "series", "temperature_series_c": series}},
    })
    assert validation.validate_gc_request(request) is request
    assert validation.temperature_series(request.input.temperature_program) == series
    with pytest.raises(PredictionContractError, match="30 finite"):
        validation.temperature_series(SimpleNamespace(mode="series", temperature_series_c=series[:-1]))
    with pytest.raises(PredictionContractError, match="unbound model"):
        validation.validate_gc_request(request.model_copy(update={
            "model_selection": request.model_selection.model_copy(update={"preprocessing_profile_id": "stale"}),
        }))


def test_sfc_preflight_checks_column_candidates_and_authorized_modifier_snapshot() -> None:
    validation = importlib.import_module("chrom_platform.adapters.sfc.validation")
    request = SfcSingleColumnPredictionCreate.model_validate({
        "project_id": "fixture-project", "schema_version": "1.1",
        "model_selection": _exact("sfc-model", "v1"),
        "chromatography_type": "sfc", "tool": "single_column",
        "structure_refs": ["analyte-a", "analyte-b"],
        "input": {
            "modifier_total_fraction": 0.2,
            "modifier_components": [{"structure_ref": "modifier-a", "fraction": 1.0}],
            "candidate_scope_id": "fixture-scope", "top_k": 5, "column_id": "column-a",
        },
    })
    assert validation.validate_sfc_request(request, allowed_tool="single_column") is request
    entry = SimpleNamespace(allowed_columns=("column-a",), stationary_phase="silica", mode="normal")
    candidate = {
        "candidate_id": "candidate-a", "column": "column-a", "stationary_phase": "silica", "mode": "normal",
        "length_mm": 150.0, "inner_diameter_mm": 4.6, "particle_size_um": 5.0,
        "column_temperature": 35.0, "flow_rate": 2.0,
    }
    resolved = validation.validate_sfc_preflight(
        request, entry=entry, candidates=[candidate], allowed_tool="single_column",
        authorized_modifier_structures={"modifier-a": "CCO"},
    )
    assert [item.candidate_id for item in resolved] == ["candidate-a"]
    with pytest.raises(validation.SfcRequestValidationError, match="outside the bound model domain"):
        validation.validate_sfc_preflight(
            request.model_copy(update={"input": request.input.model_copy(update={"column_id": "column-b"})}),
            entry=entry, candidates=[candidate], allowed_tool="single_column",
            authorized_modifier_structures={"modifier-a": "CCO"},
        )
    with pytest.raises(validation.SfcAssetUnavailable, match="MODIFIER_SNAPSHOT_MISSING"):
        validation.validate_sfc_preflight(
            request, entry=entry, candidates=[candidate], allowed_tool="single_column",
            authorized_modifier_structures={},
        )


def test_fixtures_do_not_import_adapters_or_scientific_libraries() -> None:
    assert all(
        f"chrom_platform.adapters.{kind}.adapter" not in sys.modules
        for kind in ("tlc", "cc", "hplc", "gc", "sfc")
    )
    assert "torch" not in sys.modules
    assert "rdkit" not in sys.modules
