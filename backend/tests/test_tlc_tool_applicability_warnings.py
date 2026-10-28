from __future__ import annotations

from types import SimpleNamespace

# Load prediction contracts before the TLC package because the module package
# initializer wires the router back to the TLC adapter.
import chrom_platform.modules.prediction  # noqa: F401

from chrom_platform.adapters.tlc.adapter import TlcPredictionAdapter
from chrom_platform.adapters.tlc.manifest import (
    TLC_ARTIFACT_ID,
    TLC_ARTIFACT_MANIFEST_SHA256,
    TLC_MODEL_VERSION,
    TLC_SCOPE_ID,
)
from chrom_platform.adapters.tlc.runtime import TlcRuntime
from chrom_platform.modules.prediction.adapters import (
    AdapterManifest,
    ModelVerificationEvidence,
    issue_verified_model_handle,
)
from chrom_platform.modules.prediction.schemas import (
    TlcConditionComparisonCreate,
    TlcConditionComparisonResult,
    TlcPredictionCreate,
    TlcPredictionResult,
    TlcReverseCreate,
    TlcReverseResult,
)


_WARNING_CODES = {
    "EXPERIMENTAL_CONFIRMATION_REQUIRED",
    "NO_INDEPENDENT_GENERALIZATION_EVIDENCE",
}


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
    manifest = _central_manifest()
    return issue_verified_model_handle(
        manifest,
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


def _adapter(monkeypatch) -> TlcPredictionAdapter:
    # The adapter still enforces the trusted runtime type and verified handle;
    # this minimal ready runtime avoids loading scientific assets in this test.
    runtime = object.__new__(TlcRuntime)
    runtime.status = SimpleNamespace(status="READY")
    runtime._verified_handle = None
    runtime.bind_verified_handle = lambda handle: setattr(runtime, "_verified_handle", handle)
    adapter = TlcPredictionAdapter(
        runtime,
        structure_resolver=lambda _ref: "CCO",
        manifest=_central_manifest(),
        verified_handle=_central_handle(),
    )
    return adapter


def _model_selection() -> dict[str, str]:
    return {"mode": "champion", "scope_id": TLC_SCOPE_ID}


def _condition_request() -> TlcConditionComparisonCreate:
    return TlcConditionComparisonCreate.model_validate(
        {
            "schema_version": "1.0",
            "model_selection": _model_selection(),
            "structure_ref": "structure-a",
            "conditions": [
                {
                    "solvent_components": [
                        {"solvent_id": "hexane", "fraction": 0.8},
                        {"solvent_id": "ethyl_acetate", "fraction": 0.2},
                    ]
                }
            ],
        }
    )


def _reverse_request() -> TlcReverseCreate:
    return TlcReverseCreate.model_validate(
        {
            "schema_version": "1.0",
            "model_selection": _model_selection(),
            "structure_ref": "structure-a",
            "target_rf_min": 0.4,
            "target_rf_max": 0.6,
            "candidate_system_ids": ["hexane/ethyl_acetate"],
            "ratio_step": 0.5,
            "max_candidates": 2,
        }
    )


def test_existing_condition_comparison_payload_without_warnings_remains_valid() -> None:
    result = TlcConditionComparisonResult.model_validate(
        {
            "task_type": "tlc_condition_comparison",
            "schema_version": "1.0",
            "structure_ref": "structure-a",
            "model_id": TLC_ARTIFACT_ID,
            "model_version": TLC_MODEL_VERSION,
            "artifact_sha256": TLC_ARTIFACT_MANIFEST_SHA256,
            "results": [],
        }
    )

    assert result.warnings == []


def test_compare_and_reverse_publish_common_warnings_and_keep_reverse_warnings(monkeypatch) -> None:
    class FakeService:
        def __init__(self, _runtime):
            pass

        def compare_conditions(self, *, smiles, conditions):
            assert smiles == "CCO"
            return [
                SimpleNamespace(
                    condition_index=0,
                    mobile_phase=conditions[0],
                    predicted_rf=0.5,
                    component_predictions={"ensemble": 0.5},
                )
            ]

        def reverse_solvent(self, **_kwargs):
            return SimpleNamespace(
                candidates=[],
                evaluated=2,
                failed=1,
                target_reachable=False,
                warnings=[
                    "PARTIAL_FAILURE: one or more real model evaluations failed",
                    "TARGET_UNREACHABLE: no directly predicted solvent point reaches target Rf",
                ],
            )

    monkeypatch.setattr("chrom_platform.adapters.tlc.service.TlcPredictionService", FakeService)
    adapter = _adapter(monkeypatch)

    compared = adapter.predict(_condition_request())
    reversed_result = adapter.predict(_reverse_request())

    assert isinstance(compared.warnings, list)
    assert {item.split(":", 1)[0] for item in compared.warnings} == _WARNING_CODES
    assert all(item and ":" in item for item in compared.warnings)
    assert {item.split(":", 1)[0] for item in reversed_result.warnings} >= _WARNING_CODES
    assert "PARTIAL_FAILURE: one or more real model evaluations failed" in reversed_result.warnings
    assert "TARGET_UNREACHABLE: no directly predicted solvent point reaches target Rf" in reversed_result.warnings
    assert reversed_result.evaluated == 2
    assert reversed_result.failed == 1


def test_single_tool_keeps_applicability_warning_objects(monkeypatch) -> None:
    adapter = _adapter(monkeypatch)
    adapter.runtime.predict = lambda **_kwargs: {
        "rf": 0.5,
        "component_predictions": {"ensemble": 0.5},
        "ensemble_weights": {"ensemble": 1.0},
    }
    request = TlcPredictionCreate.model_validate(
        {
            "chromatography_type": "tlc",
            "tool": "single",
            "schema_version": "1.0",
            "model_selection": _model_selection(),
            "structure_refs": ["structure-a"],
            "input": {
                "solvent_components": [
                    {"solvent_id": "hexane", "fraction": 0.8},
                    {"solvent_id": "ethyl_acetate", "fraction": 0.2},
                ]
            },
        }
    )

    result = adapter.predict(request)

    assert [warning.code for warning in result.applicability.warnings] == [
        "RATIO_NORMALIZED",
        "EXPERIMENTAL_CONFIRMATION_REQUIRED",
        "NO_INDEPENDENT_GENERALIZATION_EVIDENCE",
    ]
