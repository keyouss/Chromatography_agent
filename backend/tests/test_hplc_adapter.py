from __future__ import annotations

import time
from pathlib import Path

import pytest

from chrom_platform.adapters.hplc.adapter import HplcPredictionAdapter
from chrom_platform.adapters.hplc.adapter import build_hplc_adapter
from chrom_platform.adapters.hplc.manifest import (
    HPLC_ARTIFACT_ID,
    HPLC_BUSINESS_SCOPE_ID,
    HPLC_INPUT_SCHEMA_VERSION,
    HPLC_MODEL_SHA256,
    HPLC_MODEL_VERSION,
    HPLC_OUTPUT_SCHEMA_VERSION,
)
from chrom_platform.adapters.hplc.runtime import HplcAssetUnavailable, HplcRuntime
from chrom_platform.adapters.hplc.serving_profile import HPLC_SERVING_PROFILE_ID, HPLC_SERVING_PROFILE_SHA256
from chrom_platform.modules.prediction.adapters import ModelUnavailableError
from chrom_platform.modules.prediction.service import PredictionCreateService, PredictionServiceError
from chrom_platform.core.dto import ActorContext
from chrom_platform.task_core.service import OperationContext
from chrom_platform.modules.prediction.adapters import (
    AdapterManifest,
    CapabilityRegistry,
    ModelVerificationEvidence,
    VerifiedModelHandle,
    issue_verified_model_handle,
)
from chrom_platform.modules.prediction.schemas import HplcPredictionCreate


SMILES = ["N[C@@H](C)C(O)=O", "N[C@H](C)C(O)=O"]


def _canonical_pair() -> list[str]:
    from rdkit import Chem

    return [Chem.MolToSmiles(Chem.MolFromSmiles(item), canonical=True, isomericSmiles=True) for item in SMILES]


def _manifest() -> AdapterManifest:
    return AdapterManifest(
        adapter_id="hplc", model_id=HPLC_ARTIFACT_ID, model_version=HPLC_MODEL_VERSION,
        business_scope_id=HPLC_BUSINESS_SCOPE_ID,
        manifest_version=HPLC_OUTPUT_SCHEMA_VERSION,
        input_schema_version=HPLC_INPUT_SCHEMA_VERSION,
        output_schema_version=HPLC_OUTPUT_SCHEMA_VERSION,
        artifact_sha256=HPLC_MODEL_SHA256, schema_available=True,
        preprocessing_profile_id=HPLC_SERVING_PROFILE_ID,
        preprocessing_profile_sha256=HPLC_SERVING_PROFILE_SHA256,
        assets_available=True, data_origin="production",
    )


def _handle():
    manifest = _manifest()
    return issue_verified_model_handle(
        manifest,
        ModelVerificationEvidence(
            adapter_id="hplc", model_id=HPLC_ARTIFACT_ID, model_version=HPLC_MODEL_VERSION,
            business_scope_id=HPLC_BUSINESS_SCOPE_ID,
            input_schema_version=HPLC_INPUT_SCHEMA_VERSION,
            output_schema_version=HPLC_OUTPUT_SCHEMA_VERSION,
            artifact_sha256=HPLC_MODEL_SHA256,
            preprocessing_profile_id=HPLC_SERVING_PROFILE_ID,
            preprocessing_profile_sha256=HPLC_SERVING_PROFILE_SHA256,
            manifest_verified=True, schema_verified=True, artifact_sha_verified=True,
            environment_verified=True, golden_sample_verified=True,
            anomaly_checks_verified=True,
            schema_by_tool={"single": {"input_schema": {}, "output_schema": {}}},
        ),
    )


def _request(**changes: object) -> HplcPredictionCreate:
    payload = {
        "project_id": "project-a", "chromatography_type": "hplc", "tool": "single",
        "schema_version": HPLC_INPUT_SCHEMA_VERSION,
        "model_selection": {
            "mode": "exact", "model_id": HPLC_ARTIFACT_ID, "model_version": HPLC_MODEL_VERSION,
            "preprocessing_profile_id": HPLC_SERVING_PROFILE_ID,
            "preprocessing_profile_sha256": HPLC_SERVING_PROFILE_SHA256,
        },
        "structure_refs": ["structure-a", "structure-b"],
        "input": {"model_input_smiles": SMILES, "isopropanol_fraction": 0.1, "flow_rate_ml_min": 1.0, "column_ids": ["ADH", "IA"]},
    }
    payload.update(changes)
    return HplcPredictionCreate.model_validate(payload)


class _FakeRuntime:
    status = type("Status", (), {"status": "READY"})()

    def __init__(self) -> None:
        self.predict_calls = 0

    def bind_verified_handle(self, _handle: object) -> None:
        return None

    def predict(self, *, model_input_smiles, ipa_fraction, flow_rate_ml_min, columns):
        self.predict_calls += 1
        assert model_input_smiles == SMILES
        assert columns == ["ADH", "IA"]
        assert ipa_fraction == 0.1
        assert flow_rate_ml_min == 1.0
        return {
            "columns": [
                {"column": "ADH", "separation_probability": 0.6, "compounds": [
                    {"smiles": SMILES[0], "rt_q10_min": 1.0, "rt_q50_min": 2.0, "rt_q90_min": 3.0},
                    {"smiles": SMILES[1], "rt_q10_min": 4.0, "rt_q50_min": 5.0, "rt_q90_min": 6.0},
                ], "median_rt_min": [2.0, 5.0]},
                {"column": "IA", "separation_probability": 0.8, "compounds": [
                    {"smiles": SMILES[0], "rt_q10_min": 2.0, "rt_q50_min": 3.0, "rt_q90_min": 4.0},
                    {"smiles": SMILES[1], "rt_q10_min": 5.0, "rt_q50_min": 6.0, "rt_q90_min": 7.0},
                ], "median_rt_min": [3.0, 6.0]},
            ],
            "recommended_column": "IA",
            "warning": "verify experimentally",
        }


def test_hplc_requires_two_distinct_structure_refs() -> None:
    payload = _request().model_dump(mode="python")
    payload["structure_refs"] = ["structure-a", "structure-a"]
    with pytest.raises(ValueError):
        HplcPredictionCreate.model_validate(payload)


def test_hplc_adapter_preserves_order_and_returns_quantiles() -> None:
    adapter = HplcPredictionAdapter(
        _FakeRuntime(), structure_resolver=dict(zip(("structure-a", "structure-b"), _canonical_pair())),
        manifest=_manifest(), verified_handle=_handle(),
        _test_runtime=True,
    )
    result = adapter.predict(_request())
    assert [row.candidate_id for row in result.rows] == ["ADH", "IA"]
    assert [row.separation for row in result.rows] == [0.6, 0.8]
    assert any(metric.key == "ADH.compound_1.rt_q10_min" and metric.value == 1.0 for metric in result.metrics)
    assert any(metric.key == "IA.compound_2.rt_q90_min" and metric.value == 7.0 for metric in result.metrics)
    assert any(metric.key == "recommended_column" and metric.value == "IA" for metric in result.metrics)


def test_hplc_adapter_rejects_raw_snapshot_identity_drift_before_runtime() -> None:
    runtime = _FakeRuntime()
    adapter = HplcPredictionAdapter(
        runtime, manifest=_manifest(), verified_handle=_handle(), _test_runtime=True,
    )
    with pytest.raises(Exception, match="raw input does not match"):
        adapter.predict_with_normalized_structures(_request(), ["CC", "CCC"])
    assert runtime.predict_calls == 0


def test_hplc_rejects_same_connectivity_without_stereochemistry_before_inference() -> None:
    runtime = _FakeRuntime()
    adapter = HplcPredictionAdapter(
        runtime, structure_resolver={"structure-a": "C[C@H](N)C(=O)O", "structure-b": "C[C@H](N)C(=O)O"},
        manifest=_manifest(), verified_handle=_handle(),
        _test_runtime=True,
    )
    with pytest.raises(Exception, match="raw input does not match") as exc_info:
        adapter.predict(_request())
    assert runtime.predict_calls == 0


def test_hplc_rejects_different_connectivity_before_inference() -> None:
    runtime = _FakeRuntime()
    adapter = HplcPredictionAdapter(
        runtime, structure_resolver={"structure-a": "CC", "structure-b": "CCC"},
        manifest=_manifest(), verified_handle=_handle(),
        _test_runtime=True,
    )
    with pytest.raises(Exception, match="raw input does not match") as exc_info:
        adapter.predict(_request())
    assert runtime.predict_calls == 0


def test_hplc_scope_and_exact_model_are_fixed() -> None:
    adapter = HplcPredictionAdapter(
        _FakeRuntime(), structure_resolver=dict(zip(("structure-a", "structure-b"), _canonical_pair())),
        manifest=_manifest(), verified_handle=_handle(),
        _test_runtime=True,
    )
    request = _request(model_selection={
        "mode": "exact", "model_id": "other", "model_version": HPLC_MODEL_VERSION,
        "preprocessing_profile_id": HPLC_SERVING_PROFILE_ID,
        "preprocessing_profile_sha256": HPLC_SERVING_PROFILE_SHA256,
    })
    with pytest.raises(Exception, match="stale or unsupported"):
        adapter.predict(request)


def test_production_adapter_rejects_duck_typed_runtime() -> None:
    with pytest.raises(TypeError, match="requires HplcRuntime"):
        HplcPredictionAdapter(
            _FakeRuntime(), structure_resolver=dict(zip(("structure-a", "structure-b"), _canonical_pair())),
            manifest=_manifest(), verified_handle=_handle(),
        )


def test_runtime_rejects_duck_typed_handle_and_scope_override(tmp_path: Path) -> None:
    runtime = HplcRuntime.from_registered_artifact(registry_root=tmp_path, model_root=tmp_path)
    with pytest.raises(TypeError, match="VerifiedModelHandle"):
        runtime.bind_verified_handle(object())
    with pytest.raises(ValueError, match="business scope"):
        HplcRuntime.from_registered_artifact(
            registry_root=tmp_path, model_root=tmp_path, business_scope_id="other-scope"
        )


def test_missing_assets_are_unavailable_and_cannot_load(tmp_path: Path) -> None:
    runtime = HplcRuntime.from_registered_artifact(registry_root=tmp_path, model_root=tmp_path)
    assert runtime.status.status == "UNAVAILABLE"
    with pytest.raises(HplcAssetUnavailable):
        runtime.load()


def test_real_hplc_model_deterministic_smoke() -> None:
    source_root = Path(r"D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\assets\chromatography")
    if not (source_root / "hplc_enantioseparation").is_dir():
        pytest.skip("audited HPLC assets are unavailable")
    runtime = HplcRuntime.from_registered_artifact(
        registry_root=source_root, model_root=source_root / "hplc_enantioseparation",
    )
    if not runtime.status.dependencies_available:
        pytest.skip(runtime.status.reason or "scientific dependencies are unavailable")
    runtime.bind_verified_handle(_handle())
    started = time.perf_counter()
    first = runtime.predict(model_input_smiles=SMILES, ipa_fraction=0.1, flow_rate_ml_min=1.0, columns=["ADH", "IA"])
    second = runtime.predict(model_input_smiles=SMILES, ipa_fraction=0.1, flow_rate_ml_min=1.0, columns=["ADH", "IA"])
    elapsed = time.perf_counter() - started
    assert first == second
    assert [item["column"] for item in first["columns"]] == ["ADH", "IA"]
    assert all(0.0 <= item["separation_probability"] <= 1.0 for item in first["columns"])
    assert all(
        all(compound["rt_q10_min"] <= compound["rt_q50_min"] <= compound["rt_q90_min"] for compound in item["compounds"])
        for item in first["columns"]
    )
    print(f"HPLC real smoke: elapsed_seconds={elapsed:.3f}, result={first}")
    assert first["columns"][0]["compounds"][0]["rt_q50_min"] == pytest.approx(14.676679, abs=1e-5)
    assert runtime.load_audit is not None
    assert runtime.load_audit.checkpoint_key_count == 255
    assert len(runtime.load_audit.loaded_keys) == 253
    assert runtime.load_audit.shape_mappings == (
        ("gnn_node.atom_encoder.atom_embedding_list.1.weight", (9, 128), (14, 128), 9),
        ("gnn_node.atom_encoder.atom_embedding_list.5.weight", (13, 128), (14, 128), 13),
    )
    assert len(runtime.load_audit.missing_keys) == 50
    assert runtime.load_audit.unexpected_keys == ()


def test_hplc_assembly_fails_closed_when_raw_golden_parity_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    from chrom_platform.assembly import prediction as assembly

    model_root = Path(r"D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\assets\chromatography\hplc_enantioseparation")
    if not model_root.is_dir():
        pytest.skip("audited HPLC assets are unavailable")
    monkeypatch.setattr(
        assembly, "_optional_asset_root",
        lambda env_name, _folder: str(model_root) if env_name == "CHROM_HPLC_MODEL_ROOT" else None,
    )
    registry = CapabilityRegistry()
    assembly._register_optional_adapters(registry, session_factory=None)

    adapter = registry.adapter("hplc")
    capability = registry.capability("hplc")
    assert adapter.verified_handle is None
    assert adapter.runtime.status.status == "UNAVAILABLE"
    assert capability.health.status == "UNAVAILABLE"
    assert capability.health.reason == "MODEL_VERIFICATION_REQUIRED"
    assert all(tool.health.reason == "MODEL_VERIFICATION_REQUIRED" for tool in capability.tools)
    assert all(model.health.reason == "MODEL_VERIFICATION_REQUIRED" for model in capability.models)
    assert adapter.runtime.load_audit is not None
    assert len(adapter.runtime.load_audit.loaded_keys) == 253
    tool = registry.get_tool("hplc", "single", HPLC_INPUT_SCHEMA_VERSION)
    with pytest.raises(ModelUnavailableError) as exc_info:
        registry.resolve_model(None, _request(), tool=tool)
    assert exc_info.value.payload.code == "MODEL_UNAVAILABLE"
    class _Access:
        def ensure_project_access(self, *_args, **_kwargs) -> None:
            return None

    class _Idempotency:
        def lookup(self, *_args, **_kwargs):
            return None

    service = PredictionCreateService(
        access=_Access(), structures=None, capabilities=registry, models=registry,
        idempotency=_Idempotency(), atomic=object(),
    )
    context = OperationContext(ActorContext(
        actor_id="actor-a", project_ids=["project-a"], auth_method="deployment_default",
        permissions=["prediction:create"], policy_version="policy-1",
    ))
    with pytest.raises(PredictionServiceError) as create_error:
        service.create(context, _request(), idempotency_key="hplc-unavailable-test")
    assert create_error.value.payload.code == "MODEL_UNAVAILABLE"
    adapter.structure_resolver = dict(zip(("structure-a", "structure-b"), _canonical_pair()))
    with pytest.raises(HplcAssetUnavailable, match="MODEL_VERIFICATION_REQUIRED"):
        adapter.predict(_request())
