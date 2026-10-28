from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from chrom_platform.adapters.tlc.manifest import (
    TLC_ARTIFACT_ID,
    TLC_ARTIFACT_MANIFEST_SHA256,
    TLC_MODEL_ASSET_SHA256,
    TLC_MODEL_VERSION,
    TLC_SCOPE_ID,
)
from chrom_platform.adapters.tlc.runtime import (
    ALLOWED_SYSTEMS,
    TlcArtifactUnavailable,
    TlcModelRoot,
    TlcRuntime,
    build_tlc_features,
    mobile_phase_vector,
    validate_mobile_phase,
)
from chrom_platform.adapters.tlc.adapter import TlcPredictionAdapter
from chrom_platform.modules.prediction.adapters import (
    AdapterManifest,
    CapabilityRegistry,
    ModelVerificationEvidence,
    PredictionAdapterProtocol,
    ModelUnavailableError,
    issue_verified_model_handle,
)
from chrom_platform.modules.prediction.schemas import (
    TlcConditionComparisonCreate,
    TlcConditionComparisonResult,
    TlcPredictionCreate,
    TlcReverseCreate,
    TlcReverseResult,
    TlcPredictionResult,
)


def test_tlc_feature_order_is_179_dimensions() -> None:
    features = build_tlc_features("O=C(C1=CC=CC=C1)CCCl", {"hexane": 0.8, "ethyl_acetate": 0.2})
    assert features.shape == (1, 179)


def test_solvent_ids_are_explicit_and_ratios_are_normalized_without_aliasing() -> None:
    phase = validate_mobile_phase({"hexane": 0.8, "ethyl_acetate": 0.2})
    assert phase == {"hexane": 0.8, "ethyl_acetate": 0.2, "dichloromethane": 0.0, "methanol": 0.0, "diethyl_ether": 0.0}
    assert validate_mobile_phase({"hexane": 8.0, "ethyl_acetate": 2.0}) == phase
    assert validate_mobile_phase({"hexane": 80.0, "ethyl_acetate": 20.0}) == phase
    with pytest.raises(ValueError, match="unknown solvent"):
        validate_mobile_phase({"petroleum_ether": 0.8, "ethyl_acetate": 0.2})
    assert mobile_phase_vector({"hexane": 0.8, "ethyl_acetate": 0.2}) == (0.8, 0.2, 0.0, 0.0, 0.0)
    # Named mappings are order independent; the model still receives the same
    # normalized fractions and never maps petroleum ether to hexane.
    assert validate_mobile_phase({"ethyl_acetate": 0.2, "hexane": 0.8}) == phase


@pytest.mark.parametrize("system", ALLOWED_SYSTEMS)
def test_only_model_declared_binary_systems_are_accepted(system: frozenset[str]) -> None:
    phase = {solvent: 0.5 for solvent in system}
    assert set(validate_mobile_phase(phase)) == {"hexane", "ethyl_acetate", "dichloromethane", "methanol", "diethyl_ether"}


def test_model_root_must_be_inside_registered_root(tmp_path: Path) -> None:
    registry = tmp_path / "registry"
    model_root = registry / "tlc"
    model_root.mkdir(parents=True)
    trusted = TlcModelRoot.from_registered_path(registry_root=registry, model_root=model_root)
    assert trusted.path == model_root.resolve()
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(ValueError, match="inside"):
        TlcModelRoot.from_registered_path(registry_root=registry, model_root=outside)


def test_missing_assets_are_unavailable_and_cannot_load(tmp_path: Path) -> None:
    registry = tmp_path / "registry"
    model_root = registry / "tlc"
    model_root.mkdir(parents=True)
    runtime = TlcRuntime.from_registered_artifact(registry_root=registry, model_root=model_root)
    assert runtime.status.status == "UNAVAILABLE"
    with pytest.raises(TlcArtifactUnavailable):
        runtime.load()


def test_verified_runtime_has_no_ad_hoc_boolean_evidence_api(tmp_path: Path) -> None:
    registry = tmp_path / "registry"
    model_root = registry / "tlc"
    model_root.mkdir(parents=True)
    runtime = TlcRuntime.from_registered_artifact(registry_root=registry, model_root=model_root)
    assert not hasattr(runtime, "mark_verified")


def test_audit_records_four_distinct_model_hashes() -> None:
    assert set(TLC_MODEL_ASSET_SHA256) == {"ann", "lgb", "xgb", "random_forest"}
    assert len(set(TLC_MODEL_ASSET_SHA256.values())) == 4


def test_adapter_matches_prediction_protocol_and_result_dto() -> None:
    request = TlcPredictionCreate.model_validate(
        {
            "project_id": "project-a",
            "chromatography_type": "tlc",
            "tool": "single",
            "schema_version": "1.0",
            "model_selection": {"mode": "champion", "scope_id": "scope_tlc_default"},
            "structure_refs": ["structure-a"],
            "input": {
                "solvent_components": [
                    {"solvent_id": "hexane", "fraction": 0.8},
                    {"solvent_id": "ethyl_acetate", "fraction": 0.2},
                ]
            },
        }
    )
    adapter = TlcPredictionAdapter(None, structure_resolver=lambda _ref: "CCO")
    assert isinstance(adapter, PredictionAdapterProtocol)
    with pytest.raises(RuntimeError, match="central verification handle"):
        adapter.predict(request)


def _central_manifest() -> AdapterManifest:
    return AdapterManifest(
        adapter_id="tlc",
        model_id=TLC_ARTIFACT_ID,
        model_version=TLC_MODEL_VERSION,
        business_scope_id=TLC_SCOPE_ID,
        manifest_version="1",
        input_schema_version="1.0",
        output_schema_version="1.0",
        artifact_sha256=TLC_ARTIFACT_MANIFEST_SHA256,
        schema_available=True,
        assets_available=True,
        data_origin="production",
    )


def _central_handle():
    return issue_verified_model_handle(
        _central_manifest(),
        ModelVerificationEvidence(
            adapter_id="tlc",
            model_id=TLC_ARTIFACT_ID,
            model_version=TLC_MODEL_VERSION,
            business_scope_id=TLC_SCOPE_ID,
            input_schema_version="1.0",
            output_schema_version="1.0",
            artifact_sha256=TLC_ARTIFACT_MANIFEST_SHA256,
            manifest_verified=True,
            schema_verified=True,
            artifact_sha_verified=True,
            environment_verified=True,
            golden_sample_verified=True,
            anomaly_checks_verified=True,
            schema_by_tool={
                "single": {
                    "input_schema": TlcPredictionCreate.model_json_schema(),
                    "output_schema": TlcPredictionResult.model_json_schema(),
                },
                "condition_compare": {
                    "input_schema": TlcConditionComparisonCreate.model_json_schema(),
                    "output_schema": TlcConditionComparisonResult.model_json_schema(),
                },
                "reverse_solvent": {
                    "input_schema": TlcReverseCreate.model_json_schema(),
                    "output_schema": TlcReverseResult.model_json_schema(),
                },
            },
        ),
    )


def test_adapter_rejects_missing_structure_resolver_and_wrong_handle() -> None:
    adapter = TlcPredictionAdapter(manifest=_central_manifest(), verified_handle=_central_handle())
    assert adapter.runtime is None

    class Runtime:
        status = type("Status", (), {"status": "UNAVAILABLE"})()

        def bind_verified_handle(self, _handle: object) -> None:
            raise TlcArtifactUnavailable("wrong artifact")

    with pytest.raises(TypeError, match="trusted TlcRuntime"):
        TlcPredictionAdapter(Runtime(), manifest=_central_manifest(), verified_handle=_central_handle())

    class ReadyRuntime:
        status = type("Status", (), {"status": "READY"})()

        def bind_verified_handle(self, _handle: object) -> None:
            return None

    with pytest.raises(TypeError, match="trusted TlcRuntime"):
        TlcPredictionAdapter(ReadyRuntime(), manifest=_central_manifest(), verified_handle=_central_handle())


def test_unverified_adapter_cannot_become_ready_in_capability_registry() -> None:
    adapter = TlcPredictionAdapter()
    registry = CapabilityRegistry()
    registry.register(adapter)
    with pytest.raises(ModelUnavailableError):
        registry.execute("tlc", object())


@pytest.mark.skipif(
    os.environ.get("RUN_TLC_REAL_MODEL_SMOKE") != "1",
    reason="set RUN_TLC_REAL_MODEL_SMOKE=1 for external four-asset model execution",
)
def test_capability_registry_real_execution_with_verified_artifact() -> None:
    source_root = Path(
        r"D:\Jupyter\Chromatography_agent\deliverables\chromatography_tools_windows_cc_hplc_gc_tlc"
    )
    model_root = source_root / "tlc_rf"
    runtime = TlcRuntime.from_registered_artifact(
        registry_root=source_root, model_root=model_root
    )
    manifest = _central_manifest()
    handle = _central_handle()
    adapter = TlcPredictionAdapter(
        runtime,
        structure_resolver=lambda _ref: "O=C(C1=CC=CC=C1)CCCl",
        manifest=manifest,
        verified_handle=handle,
    )
    registry = CapabilityRegistry()
    registry.register(adapter)
    request = TlcPredictionCreate.model_validate(
        {
            "project_id": "project-a",
            "chromatography_type": "tlc",
            "tool": "single",
            "schema_version": "1.0",
            "model_selection": {"mode": "champion", "scope_id": "tlc-default"},
            "structure_refs": ["structure-a"],
            "input": {"solvent_components": [
                {"solvent_id": "hexane", "fraction": 0.8},
                {"solvent_id": "ethyl_acetate", "fraction": 0.2},
            ]},
        }
    )
    result = registry.execute("tlc", request)
    assert result.rows[0].rf == pytest.approx(0.657982)
    assert result.model_id == TLC_ARTIFACT_ID
    assert result.model_version == TLC_MODEL_VERSION
    assert result.artifact_sha256 == TLC_ARTIFACT_MANIFEST_SHA256
    assert result.ensemble_weights == {"lgb": 0.2, "xgb": 0.2, "random_forest": 0.2, "ann": 0.4}
    assert result.mobile_phase_original == {"hexane": 0.8, "ethyl_acetate": 0.2}
    assert result.mobile_phase_normalized == {"hexane": 0.8, "ethyl_acetate": 0.2, "dichloromethane": 0.0, "methanol": 0.0, "diethyl_ether": 0.0}
    assert any(item.code == "RATIO_NORMALIZED" for item in result.applicability.warnings)
    with pytest.raises(ValidationError):
        TlcPredictionCreate.model_validate(
            request.model_dump(mode="python")
            | {"input": {"solvent_components": [
                {"solvent_id": "hexane", "fraction": 8.0},
                {"solvent_id": "ethyl_acetate", "fraction": 2.0},
            ]}}
        )
    assert result.visualizations == []
