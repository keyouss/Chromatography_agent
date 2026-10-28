from __future__ import annotations

import csv
import hashlib
import math
from pathlib import Path
from copy import deepcopy
import importlib.util
import sys
from types import SimpleNamespace

import pytest

from chrom_platform.adapters.cc.adapter import (
    CC_INPUT_SCHEMA_VERSION,
    CC_MODEL_VERSION,
    CC_OUTPUT_SCHEMA_VERSION,
    CcPredictionAdapter,
    _tool_schemas,
    _verify_cc_evidence,
    build_cc_adapter,
)
from chrom_platform.adapters.cc.condition_catalog import (
    CC_CONDITION_CATALOG,
    CC_NUMERIC_INPUT_RANGES,
    CcCondition,
    CcConditionCatalog,
)
from chrom_platform.adapters.cc.golden_4g import CC_GOLDEN_4G, verify_golden_digest
from chrom_platform.adapters.cc.canonical_golden_4g import (
    CC_CANONICAL_GOLDEN_4G,
    verify_cc_canonical_golden,
)
from chrom_platform.adapters.cc.preprocessing_profiles import (
    _PROFILE_DOCUMENT,
    CC_4G_CANONICAL_PROFILE_ID,
    CC_4G_CANONICAL_PROFILE_SHA256,
    verify_cc_preprocessing_environment,
    verify_cc_profile_document,
)
from chrom_platform.adapters.cc.runtime import CcAssetUnavailable, CcModelRegistry, CcRuntime, _condition, derive_time_seconds
from chrom_platform.modules.prediction.adapters import AdapterManifest, CapabilityRegistry, ModelUnavailableError, ModelVerificationEvidence, PredictionAdapterProtocol, PredictionContractError, issue_verified_model_handle
from chrom_platform.modules.prediction.schemas import CcPredictionCreate


SOURCE_ROOT = Path(
    r"D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\assets\chromatography\cc_column_chromatography"
)


def _request() -> CcPredictionCreate:
    return CcPredictionCreate.model_validate(
        {
            "project_id": "p1",
            "schema_version": "1.0",
            "model_selection": {
                "mode": "exact", "model_id": "qgeognn_silica_4g", "model_version": CC_MODEL_VERSION,
                "preprocessing_profile_id": CC_4G_CANONICAL_PROFILE_ID,
                "preprocessing_profile_sha256": CC_4G_CANONICAL_PROFILE_SHA256,
            },
            "structure_refs": ["structure-a", "structure-b"],
            "chromatography_type": "cc",
            "tool": "single",
            "input": {
                "eluent_system_id": "cc4g-pe-ea-000-100-v1",
                "loading_solvent_id": "cc4g-loading-dcm-v1",
                "sample_mass_mg": 100.0,
                "loading_solvent_volume_ul": 300.0,
                "flow_rate_ml_min": 10.0,
                "column_spec_id": "silica-4g",
            },
        }
    )


def _adapter(fake_runtime: object, *, catalog: CcConditionCatalog | None = None) -> CcPredictionAdapter:
    catalog = catalog or CcConditionCatalog()
    input_schema, output_schema = _tool_schemas(catalog)
    manifest = AdapterManifest(
        adapter_id="cc", model_id="qgeognn_silica_4g", model_version=CC_MODEL_VERSION,
        business_scope_id="cc-default",
        manifest_version=CC_OUTPUT_SCHEMA_VERSION, input_schema_version=CC_INPUT_SCHEMA_VERSION,
        output_schema_version=CC_OUTPUT_SCHEMA_VERSION,
        artifact_sha256="d6c4c113b2d02b4275b7d1cf3a572ac202d0bd8a1927d0158d0e7ab188ee4c99",
        preprocessing_profile_id=CC_4G_CANONICAL_PROFILE_ID,
        preprocessing_profile_sha256=CC_4G_CANONICAL_PROFILE_SHA256,
        schema_available=True, assets_available=True, data_origin="production",
    )
    evidence = ModelVerificationEvidence(
        adapter_id="cc", model_id=manifest.model_id, model_version=manifest.model_version,
        business_scope_id=manifest.business_scope_id,
        input_schema_version=manifest.input_schema_version, output_schema_version=manifest.output_schema_version,
        artifact_sha256=manifest.artifact_sha256, manifest_verified=True, schema_verified=True,
        preprocessing_profile_id=manifest.preprocessing_profile_id,
        preprocessing_profile_sha256=manifest.preprocessing_profile_sha256,
        artifact_sha_verified=True, environment_verified=True, golden_sample_verified=True,
        anomaly_checks_verified=True,
        schema_by_tool={"single": {"input_schema": input_schema, "output_schema": output_schema}},
    )
    return CcPredictionAdapter(
        runtime=fake_runtime,
        structure_resolver=lambda ref: {"structure-a": "CC", "structure-b": "CCC"}[ref],
        manifest=manifest,
        verified_handle=issue_verified_model_handle(manifest, evidence),
        artifact_sha256=manifest.artifact_sha256,
        condition_catalog=catalog,
    )


def test_cc_registry_verifies_all_real_bundles() -> None:
    if not SOURCE_ROOT.is_dir():
        pytest.skip("审计来源资产不在当前机器")
    registry = CcModelRegistry(SOURCE_ROOT)
    assert {bundle.spec for bundle in registry.bundles} == {"4g", "8g", "25g", "40g"}
    assert all(len(bundle.artifact_sha256) == 64 for bundle in registry.bundles)


def test_cc_versioned_catalog_exposes_only_verified_ids() -> None:
    catalog = CcConditionCatalog()
    assert catalog.document["namespace"] == "cc-author-4g"
    assert catalog.document["revision"] == "v1"
    assert catalog.resolve_eluent_system("cc4g-pe-ea-000-100-v1") == CcCondition(0.0, 1.0)
    assert catalog.resolve_loading_solvent("cc4g-loading-dcm-v1") == "DCM"
    with pytest.raises(KeyError):
        catalog.resolve_eluent_system("hexane-ea-0-1")
    with pytest.raises(KeyError):
        catalog.resolve_loading_solvent("DCM")
    tool = _adapter(_FakeRuntime()).capability.tools[0]
    assert tool.model_constraints["numeric_input_ranges"] == CC_NUMERIC_INPUT_RANGES
    assert "not verified joint support or model generalization" in tool.model_constraints["numeric_input_range_semantics"]
    input_properties = tool.input_schema["properties"]["input"]["properties"]
    for name, bounds in CC_NUMERIC_INPUT_RANGES.items():
        assert input_properties[name]["minimum"] == bounds["minimum"]
        assert input_properties[name]["maximum"] == bounds["maximum"]


def test_cc_sample_mass_feature_is_unit_equivalent_and_loading_volume_stays_separate() -> None:
    request = _request()
    # 1 g/mL * 100 uL has numeric value 100 mg after unit conversion.
    assert 1.0 * 100.0 == request.input.sample_mass_mg
    vector = _condition(request, CcCondition(0.0, 1.0), "DCM")
    assert list(vector[-3:]) == [2.0, 100.0, 300.0]
    invalid = SimpleNamespace(input=SimpleNamespace(
        loading_solvent_id="cc4g-loading-dcm-v1", sample_mass_mg=float("nan"),
        loading_solvent_volume_ul=300.0,
    ))
    with pytest.raises(ValueError, match="sample_mass_mg"):
        _condition(invalid, CcCondition(0.0, 1.0), "DCM")


def test_cc_numeric_ranges_match_source_csv_subset_and_unit_conversion() -> None:
    dataset_path = Path(__file__).resolve().parents[2] / "Services/column_chromatography_prediction-main/dataset/dataset_4g.csv"
    dataset_source = next(item for item in CC_CONDITION_CATALOG["sources"] if item["path"].endswith("dataset_4g.csv"))
    assert hashlib.sha256(dataset_path.read_bytes()).hexdigest() == dataset_source["sha256"]

    selected = []
    with dataset_path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        for row in reader:
            if (row["PE/EA"], row["loading solvent"], row["column_specs"]) == (
                "0/1", "DCM", "Silica-CS 4g",
            ):
                selected.append(row)

    assert len(selected) == 462
    sample_masses = [float(row["Density g/ml"]) * float(row["V/ul"]) for row in selected]
    loading_volumes = [float(row["Volume of loading solvent/ul"]) for row in selected]
    flows = [float(row["Flow mL/min"]) for row in selected]
    assert all(value > 0 and math.isfinite(value) for value in sample_masses + loading_volumes + flows)
    assert CC_CONDITION_CATALOG["units"]["sample_mass_mg"] == "mg"
    assert CC_CONDITION_CATALOG["units"]["loading_solvent_volume_ul"] == "uL"
    assert CC_CONDITION_CATALOG["units"]["flow_rate_ml_min"] == "mL/min"
    assert {
        "sample_mass_mg": {"minimum": min(sample_masses), "maximum": max(sample_masses), "unit": "mg"},
        "loading_solvent_volume_ul": {"minimum": min(loading_volumes), "maximum": max(loading_volumes), "unit": "uL"},
        "flow_rate_ml_min": {"minimum": min(flows), "maximum": max(flows), "unit": "mL/min"},
    } == CC_NUMERIC_INPUT_RANGES


@pytest.mark.parametrize(
    ("field", "minimum", "maximum"),
    [
        ("sample_mass_mg", 30.0, 327.3),
        ("loading_solvent_volume_ul", 200.0, 2000.0),
        ("flow_rate_ml_min", 5.0, 10.0),
    ],
)
def test_cc_numeric_range_endpoints_are_accepted(field: str, minimum: float, maximum: float) -> None:
    runtime = _FakeRuntime()
    adapter = _adapter(runtime)
    for value in (minimum, maximum):
        payload = _request().model_dump()
        payload["input"][field] = value
        adapter.predict(CcPredictionCreate.model_validate(payload))
    assert runtime.calls == 2


@pytest.mark.parametrize(
    ("field", "minimum", "maximum", "epsilon"),
    [
        ("sample_mass_mg", 30.0, 327.3, 0.1),
        ("loading_solvent_volume_ul", 200.0, 2000.0, 1.0),
        ("flow_rate_ml_min", 5.0, 10.0, 0.1),
    ],
)
@pytest.mark.parametrize("edge", ["below", "above"])
def test_cc_numeric_range_rejects_outside_before_runtime(
    field: str, minimum: float, maximum: float, epsilon: float, edge: str,
) -> None:
    runtime = _FakeRuntime()
    adapter = _adapter(runtime)
    payload = _request().model_dump()
    payload["input"][field] = minimum - epsilon if edge == "below" else maximum + epsilon
    with pytest.raises(ValueError, match=field):
        adapter.predict(CcPredictionCreate.model_validate(payload))
    assert runtime.calls == 0


def test_cc_runtime_rejects_out_of_range_before_model_loading() -> None:
    payload = _request().model_dump()
    payload["input"]["flow_rate_ml_min"] = 10.1
    runtime = CcRuntime(registry=object(), condition_catalog=CcConditionCatalog())
    with pytest.raises(ValueError, match="flow_rate_ml_min"):
        runtime.predict(CcPredictionCreate.model_validate(payload), ["CC", "CCC"])


def _verification_manifest() -> AdapterManifest:
    return AdapterManifest(
        adapter_id="cc", model_id="qgeognn_silica_4g", model_version=CC_MODEL_VERSION,
        business_scope_id="cc-default", manifest_version=CC_OUTPUT_SCHEMA_VERSION,
        input_schema_version=CC_INPUT_SCHEMA_VERSION, output_schema_version=CC_OUTPUT_SCHEMA_VERSION,
        artifact_sha256="d6c4c113b2d02b4275b7d1cf3a572ac202d0bd8a1927d0158d0e7ab188ee4c99",
        preprocessing_profile_id=CC_4G_CANONICAL_PROFILE_ID,
        preprocessing_profile_sha256=CC_4G_CANONICAL_PROFILE_SHA256,
        schema_available=True, assets_available=True, data_origin="production",
    )


class _GoldenRuntime:
    def __init__(self, *, artifact_sha256: str | None = None, altered: bool = False):
        self.registry = SimpleNamespace(bundle=lambda _model: SimpleNamespace(
            artifact_sha256=artifact_sha256 or CC_GOLDEN_4G["artifact_sha256"]
        ))
        self.altered = altered

    def predict(self, _request, smiles):
        cases_by_smiles = {
            case["smiles"]: case for case in CC_GOLDEN_4G["cases"]
        } | {
            case["smiles"]: case for case in CC_CANONICAL_GOLDEN_4G["cases"]
        }
        output = [
            {"smiles": smiles_item, "predicted_elution_windows": deepcopy(cases_by_smiles[smiles_item]["expected"])}
            for smiles_item in smiles
        ]
        if self.altered:
            output[0]["predicted_elution_windows"][0]["volume_ml"]["q50"] += 0.01
        return {
            "artifact_sha256": self.registry.bundle("4g").artifact_sha256,
            "model": {"name": "qgeognn_silica_4g", "column_spec": "Silica-CS 4g"},
            "predicted_elution_windows": output,
        }


def test_cc_verification_rejects_asset_schema_and_golden_mismatches(monkeypatch) -> None:
    manifest = _verification_manifest()
    catalog = CcConditionCatalog()
    with pytest.raises(CcAssetUnavailable, match="MODEL_ARTIFACT_BINDING_INVALID"):
        _verify_cc_evidence(
            manifest=manifest, runtime=_GoldenRuntime(artifact_sha256="0" * 64), catalog=catalog,
        )

    monkeypatch.setattr("chrom_platform.adapters.cc.adapter.verify_golden_digest", lambda _doc: True)
    wrong_schema = deepcopy(CC_GOLDEN_4G)
    wrong_schema["input_schema_version"] = "9.9"
    with pytest.raises(CcAssetUnavailable, match="MODEL_GOLDEN_BINDING_INVALID"):
        _verify_cc_evidence(
            manifest=manifest, runtime=_GoldenRuntime(), catalog=catalog, golden=wrong_schema,
        )
    with pytest.raises(CcAssetUnavailable, match="MODEL_GOLDEN_MISMATCH"):
        _verify_cc_evidence(
            manifest=manifest, runtime=_GoldenRuntime(altered=True), catalog=catalog,
        )


def test_cc_bad_golden_digest_cannot_issue_handle() -> None:
    assert verify_golden_digest()
    changed = deepcopy(CC_GOLDEN_4G)
    changed["cases"][0]["expected"][0]["volume_ml"]["q50"] += 0.01
    assert not verify_golden_digest(changed)


def test_cc_platform_condition_vector_and_pair_golden_match_author_entry() -> None:
    if not SOURCE_ROOT.is_dir():
        pytest.skip("审计来源资产不在当前机器")
    dataset_path = Path(__file__).resolve().parents[2] / "Services/column_chromatography_prediction-main/dataset/dataset_4g.csv"
    dataset_source = next(item for item in CC_CONDITION_CATALOG["sources"] if item["path"].endswith("dataset_4g.csv"))
    assert hashlib.sha256(dataset_path.read_bytes()).hexdigest() == dataset_source["sha256"]
    source_columns = {
        "CAS": "CAS", "name": "name", "Purity": "purity", "Density g/ml": "density_g_ml",
        "V/ul": "sample_volume_ul", "loading solvent": "loading_solvent",
        "Volume of loading solvent/ul": "loading_solvent_volume_ul", "PE/EA": "PE_EA",
        "Flow mL/min": "flow_rate_ml_min", "Ending time": "ending_time",
        "column_specs": "column_specs", "t1": "t1", "t2": "t2", "smiles": "smiles",
        "verified t1": "verified_t1", "verified t2": "verified_t2",
    }
    source_rows = {}
    with dataset_path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        for row in reader:
            # Golden source_row uses the CSV's physical line number, including its header.
            if reader.line_num in {case["source_row"] for case in CC_GOLDEN_4G["cases"]}:
                source_rows[reader.line_num] = row
    assert set(source_rows) == {case["source_row"] for case in CC_GOLDEN_4G["cases"]}
    for case in CC_GOLDEN_4G["cases"]:
        source_record = case["source_record"]
        for column, key in source_columns.items():
            actual = source_rows[case["source_row"]][column]
            expected = source_record[key]
            if isinstance(expected, (int, float)):
                assert float(actual) == float(expected), (case["source_row"], column)
            else:
                assert actual == expected, (case["source_row"], column)

    module_path = SOURCE_ROOT / "predict.py"
    spec = importlib.util.spec_from_file_location("cc_author_predict_for_verification", module_path)
    assert spec is not None and spec.loader is not None
    author = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SOURCE_ROOT))
    try:
        spec.loader.exec_module(author)
    finally:
        sys.path.remove(str(SOURCE_ROOT))

    catalog = CcConditionCatalog()
    condition = catalog.resolve_eluent_system("cc4g-pe-ea-000-100-v1")
    platform_vector = _condition(_request(), condition, "DCM")
    author_vector = author._condition(CC_GOLDEN_4G["cases"][0]["author_input"])
    assert list(platform_vector) == list(author_vector)

    for case in CC_GOLDEN_4G["cases"]:
        author_request = dict(case["author_input"])
        author_request.update(smiles=case["smiles"], column_spec="4g")
        outputs = [author.predict(author_request) for _ in range(2)]
        assert outputs[0] == outputs[1]
        for output in outputs:
            assert output["model"]["name"] == CC_GOLDEN_4G["model_id"]
            assert output["predicted_elution_windows"] == case["expected"]


def test_cc_real_author_golden_issues_only_bound_4g_handle() -> None:
    if not SOURCE_ROOT.is_dir():
        pytest.skip("审计来源资产不在当前机器")
    refs = {"golden-0": CC_GOLDEN_4G["cases"][0]["smiles"], "golden-1": CC_GOLDEN_4G["cases"][1]["smiles"]}
    adapter = build_cc_adapter(model_root=str(SOURCE_ROOT), structure_resolver=refs.__getitem__)
    assert adapter.verified_handle is not None
    assert adapter.verified_handle.model_id == "qgeognn_silica_4g"
    assert adapter.verified_handle.model_version == CC_MODEL_VERSION
    assert adapter.verified_handle.artifact_sha256 == CC_GOLDEN_4G["artifact_sha256"]
    assert adapter.verified_handle.preprocessing_profile_id == CC_4G_CANONICAL_PROFILE_ID
    assert adapter.verified_handle.preprocessing_profile_sha256 == CC_4G_CANONICAL_PROFILE_SHA256
    tool = adapter.capability.tools[0]
    assert tool.model_constraints["eluent_system_ids"] == ["cc4g-pe-ea-000-100-v1"]
    assert tool.model_constraints["loading_solvent_ids"] == ["cc4g-loading-dcm-v1"]
    payload = _request().model_dump()
    payload["structure_refs"] = ["golden-0", "golden-1"]
    registry = CapabilityRegistry()
    registry.register(adapter)
    assert registry.capability("cc").health.status == "READY"
    assert registry.get_tool("cc", "single", CC_INPUT_SCHEMA_VERSION).health.status == "READY"
    result = registry.execute("cc", CcPredictionCreate.model_validate(payload))
    assert [row.volume_ml for row in result.rows] == pytest.approx([
        CC_GOLDEN_4G["cases"][0]["expected"][0]["volume_ml"]["q50"],
        CC_GOLDEN_4G["cases"][0]["expected"][1]["volume_ml"]["q50"],
        CC_GOLDEN_4G["cases"][1]["expected"][0]["volume_ml"]["q50"],
        CC_GOLDEN_4G["cases"][1]["expected"][1]["volume_ml"]["q50"],
    ], abs=0.0, rel=0.0)
    for index, case in enumerate(CC_GOLDEN_4G["cases"], 1):
        for window in case["expected"]:
            for quantile in ("q10", "q50", "q90"):
                volume_key = f"structure_{index}_{window['name']}_volume_{quantile}"
                time_key = f"structure_{index}_{window['name']}_time_{quantile}"
                volume_metric = next(metric for metric in result.metrics if metric.key == volume_key)
                time_metric = next(metric for metric in result.metrics if metric.key == time_key)
                expected_volume = window["volume_ml"][quantile]
                assert volume_metric.value == expected_volume
                assert time_metric.value == derive_time_seconds(volume_ml=expected_volume, flow_rate_ml_min=10.0)


def test_cc_builder_stays_unavailable_when_any_verification_step_fails(monkeypatch) -> None:
    if not SOURCE_ROOT.is_dir():
        pytest.skip("审计来源资产不在当前机器")

    def failed_verification(**_kwargs):
        raise CcAssetUnavailable("MODEL_GOLDEN_MISMATCH")

    monkeypatch.setattr("chrom_platform.adapters.cc.adapter._verify_cc_evidence", failed_verification)
    adapter = build_cc_adapter(model_root=str(SOURCE_ROOT))
    assert adapter.verified_handle is None
    registry = CapabilityRegistry()
    registry.register(adapter)
    assert registry.capability("cc").health.status == "UNAVAILABLE"
    assert registry.capability("cc").health.reason == "MODEL_VERIFICATION_REQUIRED"


def test_cc_build_binding_rejects_unverified_model_version_and_artifact() -> None:
    with pytest.raises(ValueError, match="launch binding"):
        build_cc_adapter(model_root=str(SOURCE_ROOT), model_id="qgeognn_silica_8g_ft")
    with pytest.raises(ValueError, match="launch binding"):
        build_cc_adapter(model_root=str(SOURCE_ROOT), model_version="other")
    with pytest.raises(ValueError, match="artifact hash"):
        CcPredictionAdapter(_FakeRuntime(), artifact_sha256="0" * 64)


def test_cc_time_formula_preserves_volume() -> None:
    assert derive_time_seconds(volume_ml=12.5, flow_rate_ml_min=2.5) == pytest.approx(300.0)


def test_cc_requires_distinct_pair() -> None:
    payload = _request().model_dump()
    payload["structure_refs"] = ["structure-a", "structure-a"]
    with pytest.raises(ValueError):
        CcPredictionCreate.model_validate(payload)


class _FakeRuntime:
    def __init__(self):
        self.calls = 0

    def predict(self, request, normalized_smiles):
        self.calls += 1
        assert normalized_smiles == ["CC", "CCC"]
        return {
            "artifact_sha256": "d6c4c113b2d02b4275b7d1cf3a572ac202d0bd8a1927d0158d0e7ab188ee4c99",
            "model": {"column_spec": "Silica-CS 4g"},
            "predicted_elution_windows": [
                {"smiles": "CC", "predicted_elution_windows": [
                    {"name": "V1", "volume_ml": {"q10": 1.0, "q50": 2.0, "q90": 3.0}, "time_seconds": {"q10": 6.0, "q50": 12.0, "q90": 18.0}},
                    {"name": "V2", "volume_ml": {"q10": 4.0, "q50": 5.0, "q90": 6.0}, "time_seconds": {"q10": 24.0, "q50": 30.0, "q90": 36.0}},
                ]},
                {"smiles": "CCC", "predicted_elution_windows": [
                    {"name": "V1", "volume_ml": {"q10": 7.0, "q50": 8.0, "q90": 9.0}, "time_seconds": {"q10": 42.0, "q50": 48.0, "q90": 54.0}},
                    {"name": "V2", "volume_ml": {"q10": 10.0, "q50": 11.0, "q90": 12.0}, "time_seconds": {"q10": 60.0, "q50": 66.0, "q90": 72.0}},
                ]},
            ],
        }


@pytest.mark.parametrize("failure", [KeyError("catalog backend unavailable"), ValueError("catalog backend unavailable")])
def test_cc_preflight_preserves_injected_catalog_failures(failure: Exception) -> None:
    class FaultingCatalog(CcConditionCatalog):
        def resolve_eluent_system(self, _key):
            raise failure

    registry = CapabilityRegistry()
    registry.register(_adapter(_FakeRuntime(), catalog=FaultingCatalog()))
    request = _request()
    snapshot = registry.resolve_model(None, request, tool=registry.get_tool("cc", "single", "1.0"))

    with pytest.raises(type(failure)) as exc_info:
        registry.validate_request("cc", request, model_snapshot=snapshot)

    assert exc_info.value is failure


@pytest.mark.parametrize(
    ("field", "value"),
    [("eluent_system_id", "missing-system"), ("loading_solvent_id", "missing-solvent")],
)
def test_cc_unknown_catalog_ids_remain_explicit_input_errors(field: str, value: str) -> None:
    registry = CapabilityRegistry()
    registry.register(_adapter(_FakeRuntime()))
    payload = _request().model_dump()
    payload["input"][field] = value
    request = CcPredictionCreate.model_validate(payload)
    snapshot = registry.resolve_model(None, _request(), tool=registry.get_tool("cc", "single", "1.0"))

    with pytest.raises(PredictionContractError) as exc_info:
        registry.validate_request("cc", request, model_snapshot=snapshot)

    assert exc_info.value.status_code == 422
    assert exc_info.value.payload.code == "PREDICTION_INPUT_INVALID"


def test_cc_result_keeps_quantiles_as_metrics_without_confidence() -> None:
    adapter = _adapter(_FakeRuntime())
    assert isinstance(adapter, PredictionAdapterProtocol)
    result = adapter.predict(_request())
    assert len(result.rows) == 4
    assert {row.volume_ml for row in result.rows} == {2.0, 5.0, 8.0, 11.0}
    assert any(metric.key == "structure_1_V1_volume_q10" and metric.origin == "model" for metric in result.metrics)
    assert any(metric.key == "structure_2_V2_time_q90" and metric.origin == "derived" for metric in result.metrics)
    assert all("confidence" not in metric.key for metric in result.metrics)
    warnings = {warning.code: warning.message for warning in result.applicability.warnings}
    assert warnings["TRAINING_PREPROCESSING_EQUIVALENCE_UNVERIFIED"] == (
        "当前 canonical 预处理仅验证软件集成基线，训练端预处理等价性尚未验证。"
    )


def test_cc_model_selection_is_bound_to_verified_handle() -> None:
    adapter = _adapter(_FakeRuntime())
    payload = _request().model_dump()
    payload["model_selection"] = {"mode": "exact", "model_id": "qgeognn_silica_8g_ft", "model_version": CC_MODEL_VERSION}
    request = CcPredictionCreate.model_validate(payload)
    with pytest.raises(CcAssetUnavailable, match="MODEL_SELECTION_NOT_VERIFIED"):
        adapter.predict(request)


def test_cc_result_rejects_non_monotonic_quantiles() -> None:
    class InvalidRuntime(_FakeRuntime):
        def predict(self, request, normalized_smiles):
            raw = super().predict(request, normalized_smiles)
            raw["predicted_elution_windows"][0]["predicted_elution_windows"][0]["volume_ml"]["q50"] = 0.5
            return raw

    with pytest.raises(CcAssetUnavailable, match="MODEL_OUTPUT_QUANTILES_INVALID"):
        _adapter(InvalidRuntime()).predict(_request())


def test_cc_runs_through_central_registry_and_rejects_wrong_scope_before_runtime() -> None:
    runtime = _FakeRuntime()
    adapter = _adapter(runtime)
    registry = CapabilityRegistry()
    registry.register(adapter)
    result = registry.execute("cc", _request())
    assert result.task_type == "cc"
    assert runtime.calls == 1
    snapshot = registry.resolve_model(None, _request(), tool=registry.get_tool("cc", "single", "1.0"))
    registry.execute_snapshot("cc", _request(), model_snapshot=snapshot)
    assert runtime.calls == 2
    payload = _request().model_dump(mode="python")
    payload["model_selection"] = {"mode": "champion", "scope_id": "other-scope"}
    wrong_scope = CcPredictionCreate.model_validate(payload)
    with pytest.raises(PredictionContractError):
        registry.execute("cc", wrong_scope)
    with pytest.raises(PredictionContractError):
        registry.execute_snapshot("cc", wrong_scope, model_snapshot=snapshot)
    assert runtime.calls == 2


def test_cc_profile_is_required_exact_and_stale_pairs_fail_closed() -> None:
    runtime = _FakeRuntime()
    registry = CapabilityRegistry()
    registry.register(_adapter(runtime))
    tool = registry.get_tool("cc", "single", "1.0")
    base = _request().model_dump(mode="python")
    del base["model_selection"]["preprocessing_profile_id"]
    del base["model_selection"]["preprocessing_profile_sha256"]
    with pytest.raises(PredictionContractError, match="预处理版本"):
        registry.execute("cc", CcPredictionCreate.model_validate(base))

    stale = _request().model_dump(mode="python")
    stale["model_selection"]["preprocessing_profile_sha256"] = "0" * 64
    with pytest.raises(PredictionContractError, match="预处理版本"):
        registry.resolve_model(None, CcPredictionCreate.model_validate(stale), tool=tool)
    assert runtime.calls == 0


def test_profileless_historical_cc_snapshot_is_readable_but_not_executable() -> None:
    from chrom_platform.modules.prediction.schemas import ModelSnapshot

    legacy = ModelSnapshot(
        id="qgeognn_silica_4g", version=CC_MODEL_VERSION, business_scope_id="cc-default",
        artifact_sha256=CC_GOLDEN_4G["artifact_sha256"], input_schema_version="1.0",
        output_schema_version=CC_OUTPUT_SCHEMA_VERSION,
    )
    assert legacy.preprocessing_profile_id is None
    runtime = _FakeRuntime()
    registry = CapabilityRegistry()
    registry.register(_adapter(runtime))
    with pytest.raises(ModelUnavailableError, match="重新创建预测"):
        registry.execute_snapshot("cc", _request(), model_snapshot=legacy)
    assert runtime.calls == 0


def test_canonical_integration_golden_and_profile_are_hash_pinned() -> None:
    assert verify_cc_profile_document()
    assert verify_cc_canonical_golden()
    assert verify_cc_preprocessing_environment(
        runtime_source=Path(__file__).resolve().parents[1] / "src/chrom_platform/adapters/cc/runtime.py",
        canonicalizer_source=Path(__file__).resolve().parents[1] / "src/chrom_platform/structure_core/service.py",
    )
    changed_profile_golden = deepcopy(CC_CANONICAL_GOLDEN_4G)
    changed_profile_golden["cases"][0]["expected"][0]["volume_ml"]["q50"] += 0.01
    assert not verify_cc_canonical_golden(changed_profile_golden)
    with pytest.raises(TypeError):
        from chrom_platform.adapters.cc.preprocessing_profiles import CC_4G_CANONICAL_PROFILE

        CC_4G_CANONICAL_PROFILE["graph_preprocessing"]["torch"] = "tampered"
    original = _PROFILE_DOCUMENT["purpose"]
    _PROFILE_DOCUMENT["purpose"] = "tampered"
    try:
        assert not verify_cc_profile_document()
    finally:
        _PROFILE_DOCUMENT["purpose"] = original
    assert verify_cc_profile_document()


def test_verified_handle_requires_manifest_evidence_profile_binding() -> None:
    manifest = _verification_manifest()
    evidence = _verify_cc_evidence(
        manifest=manifest, runtime=_GoldenRuntime(), catalog=CcConditionCatalog(),
    )
    assert evidence.preprocessing_profile_id == manifest.preprocessing_profile_id
    assert evidence.preprocessing_profile_sha256 == manifest.preprocessing_profile_sha256
    mismatched = evidence.model_copy(update={"preprocessing_profile_sha256": "c" * 64})
    with pytest.raises(ValueError, match="not bound"):
        issue_verified_model_handle(manifest, mismatched)
