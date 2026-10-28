from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from chrom_platform.adapters.gc.adapter import GcPredictionAdapter, _gc_input_schema, _temperature_series
from chrom_platform.adapters.gc.adapter import build_gc_adapter
from chrom_platform.adapters.gc.manifest import (
    GC_ARTIFACT_ID,
    GC_BUSINESS_SCOPE_ID,
    GC_INPUT_SCHEMA_VERSION,
    GC_MODEL_SHA256,
    GC_MODEL_VERSION,
    GC_OUTPUT_SCHEMA_VERSION,
    GC_PREPROCESSING_PROTOCOL_ID,
    GC_PREPROCESSING_PROTOCOL_QA_APPROVED,
)
from chrom_platform.adapters.gc.serving_profile import GC_SERVING_PROFILE_ID, GC_SERVING_PROFILE_SHA256
from chrom_platform.adapters.gc.runtime import GcAssetUnavailable, GcRuntime, GcVerificationReport
from chrom_platform.modules.prediction.adapters import (
    AdapterManifest,
    ModelVerificationEvidence,
    PredictionContractError,
    issue_verified_model_handle,
)
from chrom_platform.modules.prediction.schemas import (
    GcPredictionCreate, GcPredictionReadV10, GcPredictionResult,
    ModelSnapshot, PredictionExecutionInputSnapshot, PredictionStoredRead,
    validate_gc_execution_binding,
)


def _manifest() -> AdapterManifest:
    return AdapterManifest(
        adapter_id="gc", model_id=GC_ARTIFACT_ID, model_version=GC_MODEL_VERSION,
        business_scope_id=GC_BUSINESS_SCOPE_ID, manifest_version=GC_OUTPUT_SCHEMA_VERSION,
        input_schema_version=GC_INPUT_SCHEMA_VERSION, output_schema_version=GC_OUTPUT_SCHEMA_VERSION,
        artifact_sha256=GC_MODEL_SHA256,
        preprocessing_profile_id=GC_SERVING_PROFILE_ID,
        preprocessing_profile_sha256=GC_SERVING_PROFILE_SHA256,
        schema_available=True, assets_available=True,
        data_origin="production",
    )


def _handle():
    return issue_verified_model_handle(
        _manifest(),
        ModelVerificationEvidence(
            adapter_id="gc", model_id=GC_ARTIFACT_ID, model_version=GC_MODEL_VERSION,
            business_scope_id=GC_BUSINESS_SCOPE_ID, input_schema_version=GC_INPUT_SCHEMA_VERSION,
            output_schema_version=GC_OUTPUT_SCHEMA_VERSION, artifact_sha256=GC_MODEL_SHA256,
            preprocessing_profile_id=GC_SERVING_PROFILE_ID,
            preprocessing_profile_sha256=GC_SERVING_PROFILE_SHA256,
            manifest_verified=True, schema_verified=True, artifact_sha_verified=True,
            environment_verified=True, golden_sample_verified=True, anomaly_checks_verified=True,
            schema_by_tool={"single": {
                "input_schema": _gc_input_schema(),
                "output_schema": GcPredictionResult.model_json_schema(),
            }},
        ),
    )


def _request(**changes: object) -> GcPredictionCreate:
    payload = {
        "project_id": "project-a", "chromatography_type": "gc", "tool": "single",
        "schema_version": GC_INPUT_SCHEMA_VERSION,
        "model_selection": {
            "mode": "exact", "model_id": GC_ARTIFACT_ID, "model_version": GC_MODEL_VERSION,
            "preprocessing_profile_id": GC_SERVING_PROFILE_ID,
            "preprocessing_profile_sha256": GC_SERVING_PROFILE_SHA256,
        },
        "structure_refs": ["structure-a"],
        "input": {"temperature_program": {"mode": "linear", "initial_temperature_c": 40.0,
                                            "final_temperature_c": 200.0, "heating_rate_c_per_min": 20.0,
                                            "initial_hold_min": 2.0}},
    }
    payload.update(changes)
    return GcPredictionCreate.model_validate(payload)


def _fake_runtime(*, malformed_temperature_series: object = None, quantiles: tuple[float, float, float] = (1.0, 2.0, 3.0)) -> GcRuntime:
    runtime = object.__new__(GcRuntime)
    runtime.business_scope_id = GC_BUSINESS_SCOPE_ID
    runtime.status = type("Status", (), {"status": "READY"})()
    runtime._verified_handle = None

    def predict(*, normalized_smiles, temperature_series_c):
        assert normalized_smiles == ["CCCCCCC"]
        assert len(temperature_series_c) == 30
        return {
            "artifact_sha256": GC_MODEL_SHA256,
            "preprocessing_protocol_id": GC_PREPROCESSING_PROTOCOL_ID,
            "temperature_series_c": (
                temperature_series_c if malformed_temperature_series is None else malformed_temperature_series
            ),
            "failed_smiles": [],
            "predictions": [{"smiles": "CCCCCCC", "rt_q10_min": quantiles[0], "rt_q50_min": quantiles[1], "rt_q90_min": quantiles[2]}],
        }

    runtime.predict = predict
    return runtime


def test_gc_requires_exactly_30_series_points() -> None:
    payload = _request(input={"temperature_program": {"mode": "series", "temperature_series_c": [40.0] * 29}}).model_dump()
    adapter = GcPredictionAdapter(_fake_runtime(), structure_resolver={"structure-a": "CC"}, manifest=_manifest(), verified_handle=_handle())
    with pytest.raises(Exception, match="30"):
        adapter.validate_request(GcPredictionCreate.model_validate(payload))
    valid = _request(input={"temperature_program": {"mode": "series", "temperature_series_c": [40.0] * 30}})
    assert len(adapter.validate_request(valid).input.temperature_program.temperature_series_c) == 30
    schema = adapter.verified_handle.schema_for("single")["input_schema"]
    assert schema["$defs"]["GcSeriesTemperatureProgram"]["properties"]["temperature_series_c"]["minItems"] == 30
    assert schema["$defs"]["GcSeriesTemperatureProgram"]["properties"]["temperature_series_c"]["maxItems"] == 30


def test_gc_model_schema_requires_integral_linear_hold_without_narrowing_shared_dto() -> None:
    schema = _gc_input_schema()
    linear = schema["$defs"]["GcLinearTemperatureProgram"]["properties"]
    hold = linear["initial_hold_min"]
    assert hold["minimum"] == 0
    assert hold["multipleOf"] == 1
    assert linear["heating_rate_c_per_min"]["exclusiveMinimum"] == 0
    assert "multipleOf" not in GcPredictionCreate.model_json_schema()["$defs"]["GcLinearTemperatureProgram"]["properties"]["initial_hold_min"]

    series = schema["$defs"]["GcSeriesTemperatureProgram"]["properties"]["temperature_series_c"]
    assert (series["minItems"], series["maxItems"]) == (30, 30)
    for hold_min in (0.0, 2.0):
        program = _request(input={"temperature_program": {
            "mode": "linear", "initial_temperature_c": 40.0, "final_temperature_c": 200.0,
            "heating_rate_c_per_min": 20.0, "initial_hold_min": hold_min,
        }}).input.temperature_program
        assert len(_temperature_series(program)) == 30

    fractional = _request(input={"temperature_program": {
        "mode": "linear", "initial_temperature_c": 40.0, "final_temperature_c": 200.0,
        "heating_rate_c_per_min": 20.0, "initial_hold_min": 0.5,
    }}).input.temperature_program
    with pytest.raises(PredictionContractError, match="integer number of minutes"):
        _temperature_series(fractional)

    for invalid in (
        {"heating_rate_c_per_min": 0.0},
        {"initial_hold_min": -1.0},
        {"final_temperature_c": 39.0},
    ):
        payload = {
            "mode": "linear", "initial_temperature_c": 40.0, "final_temperature_c": 200.0,
            "heating_rate_c_per_min": 20.0, "initial_hold_min": 0.0,
        }
        payload.update(invalid)
        with pytest.raises(ValueError):
            _request(input={"temperature_program": payload})


def test_gc_adapter_preserves_quantiles_and_scope() -> None:
    adapter = GcPredictionAdapter(_fake_runtime(), structure_resolver={"structure-a": "CCCCCCC"}, manifest=_manifest(), verified_handle=_handle())
    result = adapter.predict(_request())
    assert result.rows[0].retention_time_min == 2.0
    assert result.rows[0].quantiles == {"q10": 1.0, "q50": 2.0, "q90": 3.0}
    assert any(warning.code == "CARRIER_GAS_EFFECT_UNSUPPORTED" for warning in result.applicability.warnings)


def test_gc_rejects_unbound_exact_model() -> None:
    adapter = GcPredictionAdapter(_fake_runtime(), structure_resolver={"structure-a": "CC"}, manifest=_manifest(), verified_handle=_handle())
    with pytest.raises(Exception, match="unbound"):
        adapter.predict(_request(model_selection={
            "mode": "exact", "model_id": "other", "model_version": GC_MODEL_VERSION,
            "preprocessing_profile_id": GC_SERVING_PROFILE_ID,
            "preprocessing_profile_sha256": GC_SERVING_PROFILE_SHA256,
        }))


def test_gc_v11_create_exact_profile_and_v10_stored_read_are_separate() -> None:
    current = _request()
    assert current.schema_version == "1.1"
    with pytest.raises(ValueError):
        GcPredictionCreate.model_validate({**current.model_dump(mode="python"), "schema_version": "1.0"})
    no_profile = current.model_dump(mode="python")
    no_profile["model_selection"] = {
        "mode": "exact", "model_id": GC_ARTIFACT_ID, "model_version": GC_MODEL_VERSION,
    }
    with pytest.raises(ValueError, match="profile"):
        GcPredictionCreate.model_validate(no_profile)
    old = {
        "project_id": "project-a", "chromatography_type": "gc", "tool": "single",
        "schema_version": "1.0", "model_selection": {"mode": "champion", "scope_id": GC_BUSINESS_SCOPE_ID},
        "structure_refs": ["structure-a"], "input": current.input.model_dump(mode="python"),
    }
    decoded = TypeAdapter(PredictionStoredRead).validate_python(old)
    assert isinstance(decoded, GcPredictionReadV10)
    assert decoded.schema_version == "1.0"
    with pytest.raises(ValueError):
        GcPredictionCreate.model_validate(old)


def test_gc_execution_binding_checks_frozen_request_and_profile() -> None:
    request = _request()
    model = ModelSnapshot(
        id=GC_ARTIFACT_ID, version=GC_MODEL_VERSION, business_scope_id=GC_BUSINESS_SCOPE_ID,
        artifact_sha256=GC_MODEL_SHA256, input_schema_version=GC_INPUT_SCHEMA_VERSION,
        output_schema_version=GC_OUTPUT_SCHEMA_VERSION,
        preprocessing_profile_id=GC_SERVING_PROFILE_ID,
        preprocessing_profile_sha256=GC_SERVING_PROFILE_SHA256,
    )
    snapshot = PredictionExecutionInputSnapshot(
        structure_refs=["structure-a"], normalized_structures=["CCCCCCC"], input=request.input,
    )
    validate_gc_execution_binding(request=request, model_snapshot=model, input_snapshot=snapshot)
    with pytest.raises(ValueError, match="snapshots"):
        validate_gc_execution_binding(
            request=request, model_snapshot=model,
            input_snapshot=snapshot.model_copy(update={"normalized_structures": []}),
        )
    with pytest.raises(ValueError, match="snapshots"):
        validate_gc_execution_binding(
            request=request, model_snapshot=model.model_copy(update={"preprocessing_profile_sha256": "0" * 64}),
            input_snapshot=snapshot,
        )


def test_missing_gc_assets_are_unavailable(tmp_path: Path) -> None:
    runtime = GcRuntime.from_registered_artifact(registry_root=tmp_path, model_root=tmp_path, business_scope_id=GC_BUSINESS_SCOPE_ID)
    assert runtime.status.status == "UNAVAILABLE"
    with pytest.raises(GcAssetUnavailable):
        runtime.load()
    adapter = build_gc_adapter(
        registry_root=str(tmp_path), model_root=str(tmp_path), structure_resolver={},
        golden_fixture=Path(r"D:\Jupyter\Chromatography_agent\testing\fixtures\priority-launch\gc-reference-preprocessing-v2.json"),
    )
    assert adapter.verified_handle is None
    assert adapter.capability.health.reason == "MODEL_VERIFICATION_REQUIRED"


def test_gc_rejects_duck_typed_runtime() -> None:
    class DuckRuntime:
        status = type("Status", (), {"status": "READY"})()

    with pytest.raises(TypeError, match="GcRuntime"):
        GcPredictionAdapter(DuckRuntime(), manifest=_manifest(), verified_handle=_handle())


def test_gc_rejects_malformed_runtime_temperature_series() -> None:
    adapter = GcPredictionAdapter(
        _fake_runtime(malformed_temperature_series=[40.0] * 29),
        structure_resolver={"structure-a": "CCCCCCC"},
        manifest=_manifest(),
        verified_handle=_handle(),
    )
    with pytest.raises(GcAssetUnavailable, match="TEMPERATURE_PROGRAM"):
        adapter.predict(_request())


@pytest.mark.parametrize("quantiles", [(float("nan"), 2.0, 3.0), (3.0, 2.0, 4.0), (-1.0, 2.0, 3.0)])
def test_gc_rejects_nonfinite_or_out_of_order_predictions(quantiles: tuple[float, float, float]) -> None:
    adapter = GcPredictionAdapter(
        _fake_runtime(quantiles=quantiles), structure_resolver={"structure-a": "CCCCCCC"},
        manifest=_manifest(), verified_handle=_handle(),
    )
    with pytest.raises(GcAssetUnavailable, match="MODEL_OUTPUT_RANGE_INVALID"):
        adapter.predict(_request())


def _real_gc_root() -> tuple[Path, Path]:
    source_root = Path(r"D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\assets\chromatography")
    return source_root, source_root / "gc_retention_time"


def _fixture() -> dict[str, object]:
    return json.loads(Path(r"D:\Jupyter\Chromatography_agent\testing\fixtures\priority-launch\gc-reference-preprocessing-v2.json").read_text(encoding="utf-8"))


def test_gc_v2_fixture_has_distinct_version_and_preserves_legacy_evidence() -> None:
    legacy_path = Path(r"D:\Jupyter\Chromatography_agent\testing\fixtures\priority-launch\gc-reference.json")
    legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
    candidate = _fixture()
    assert hashlib.sha256(legacy_path.read_bytes()).hexdigest() == "f0a511080f05086304cf6872650a437fe9bc0e6822bea18999d6ead5c8915674"
    assert legacy["model"]["model_version"] == "gc-gnn-seq240408-250c-600"
    assert candidate["model"]["model_version"] == GC_MODEL_VERSION
    assert candidate["acceptance_state"] == "candidate"
    assert candidate["preprocessing_protocol"]["id"] == GC_PREPROCESSING_PROTOCOL_ID


def _parity_output(fixture: dict[str, object]) -> dict[str, object]:
    raw = copy.deepcopy(fixture["expected_raw"])
    raw["failed_smiles"] = []
    raw["artifact_sha256"] = GC_MODEL_SHA256
    raw["preprocessing_protocol_id"] = GC_PREPROCESSING_PROTOCOL_ID
    return raw


def test_gc_builder_keeps_candidate_protocol_blocked_before_qa_approval(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    runtime = GcRuntime.from_registered_artifact(
        registry_root=tmp_path, model_root=tmp_path, business_scope_id=GC_BUSINESS_SCOPE_ID,
    )
    monkeypatch.setattr(GcRuntime, "from_registered_artifact", classmethod(lambda cls, **_: runtime))
    monkeypatch.setattr(runtime, "verify_golden", lambda _: GcVerificationReport(True, True, True, True, True, True))
    monkeypatch.setattr(runtime, "bind_verified_handle", lambda handle: setattr(runtime, "_verified_handle", handle))
    fixture_path = tmp_path / "gc-reference-preprocessing-v2.json"
    fixture_path.write_text(json.dumps(_fixture()), encoding="utf-8")
    adapter = build_gc_adapter(
        registry_root=str(tmp_path), model_root=str(tmp_path), structure_resolver={}, golden_fixture=fixture_path,
    )
    assert GC_PREPROCESSING_PROTOCOL_QA_APPROVED is False
    assert adapter.verified_handle is None
    assert adapter.capability.health.reason == "MODEL_VERIFICATION_REQUIRED"


def test_gc_real_checkpoint_remains_blocked_until_protocol_qa_approval() -> None:
    source_root, model_root = _real_gc_root()
    if not model_root.is_dir():
        pytest.skip("audited GC assets are unavailable")
    runtime = GcRuntime.from_registered_artifact(registry_root=source_root, model_root=model_root, business_scope_id=GC_BUSINESS_SCOPE_ID)
    if runtime.status.status != "READY":
        pytest.skip(f"audited GC runtime unavailable: {runtime.status.reason}")
    adapter = build_gc_adapter(
        registry_root=str(source_root), model_root=str(model_root),
        structure_resolver={"structure-a": "CCCCCCC"},
    )
    assert adapter.verified_handle is None
    assert adapter.capability.health.reason == "MODEL_VERIFICATION_REQUIRED"


@pytest.mark.parametrize(
    ("path", "value", "failed_check"),
    [
        (("model", "registry_sha256"), "0" * 64, "manifest_verified"),
        (("model", "source_sha256"), "0" * 64, "manifest_verified"),
        (("model", "model_asset_sha256"), "0" * 64, "manifest_verified"),
        (("model", "model_version"), "wrong-version", "manifest_verified"),
        (("model", "preprocessing_protocol_id"), "wrong-protocol", "manifest_verified"),
        (("model", "input_schema_version"), "wrong-schema", "manifest_verified"),
        (("expected_raw", "predictions", 0, "rt_q50_min"), 99.0, "golden_sample_verified"),
    ],
)
def test_gc_verification_fail_closes_hash_schema_and_golden_errors(
    monkeypatch: pytest.MonkeyPatch, path: tuple[object, ...], value: object, failed_check: str,
) -> None:
    source_root, model_root = _real_gc_root()
    if not model_root.is_dir():
        pytest.skip("audited GC assets are unavailable")
    runtime = GcRuntime.from_registered_artifact(registry_root=source_root, model_root=model_root, business_scope_id=GC_BUSINESS_SCOPE_ID)
    if runtime.status.status != "READY":
        pytest.skip(f"audited GC runtime unavailable: {runtime.status.reason}")
    fixture = _fixture()
    valid_output = _parity_output(fixture)
    current = fixture
    for key in path[:-1]:
        current = current[key]
    current[path[-1]] = value
    monkeypatch.setattr(runtime, "_predict", lambda **_: copy.deepcopy(valid_output))
    report = runtime.verify_golden(fixture)
    assert not getattr(report, failed_check)


def test_gc_verification_rejects_anomaly_and_nonfinite_output(monkeypatch: pytest.MonkeyPatch) -> None:
    source_root, model_root = _real_gc_root()
    if not model_root.is_dir():
        pytest.skip("audited GC assets are unavailable")
    runtime = GcRuntime.from_registered_artifact(registry_root=source_root, model_root=model_root, business_scope_id=GC_BUSINESS_SCOPE_ID)
    if runtime.status.status != "READY":
        pytest.skip(f"audited GC runtime unavailable: {runtime.status.reason}")
    fixture = _fixture()
    bad = _parity_output(fixture)
    bad["predictions"][0]["rt_q50_min"] = float("nan")
    monkeypatch.setattr(runtime, "_predict", lambda **_: copy.deepcopy(bad))
    report = runtime.verify_golden(fixture)
    assert not report.anomaly_checks_verified
    assert not report.golden_sample_verified


@pytest.mark.parametrize("binding", [
    {"model_id": "other-model"},
    {"model_version": "other-version"},
    {"artifact_sha256": "0" * 64},
])
def test_gc_adapter_rejects_handles_bound_to_other_model_facts(binding: dict[str, str]) -> None:
    fields = {
        "adapter_id": "gc", "model_id": GC_ARTIFACT_ID, "model_version": GC_MODEL_VERSION,
        "business_scope_id": GC_BUSINESS_SCOPE_ID, "manifest_version": GC_OUTPUT_SCHEMA_VERSION,
        "input_schema_version": GC_INPUT_SCHEMA_VERSION, "output_schema_version": GC_OUTPUT_SCHEMA_VERSION,
        "artifact_sha256": GC_MODEL_SHA256, "schema_available": True, "assets_available": True,
        "data_origin": "production",
    }
    fields.update(binding)
    from chrom_platform.modules.prediction.adapters import AdapterManifest, ModelVerificationEvidence, issue_verified_model_handle

    manifest = AdapterManifest(**fields)
    evidence = ModelVerificationEvidence(
        adapter_id=manifest.adapter_id, model_id=manifest.model_id, model_version=manifest.model_version,
        business_scope_id=manifest.business_scope_id, input_schema_version=manifest.input_schema_version,
        output_schema_version=manifest.output_schema_version, artifact_sha256=manifest.artifact_sha256,
        manifest_verified=True, schema_verified=True, artifact_sha_verified=True,
        environment_verified=True, golden_sample_verified=True, anomaly_checks_verified=True,
        schema_by_tool={"single": {"input_schema": {}, "output_schema": {}}},
    )
    handle = issue_verified_model_handle(manifest, evidence)
    with pytest.raises(ValueError, match="does not bind"):
        GcPredictionAdapter(manifest=manifest, verified_handle=handle)


def test_real_gc_seeded_preprocessing_is_repeatable_without_issuing_a_handle() -> None:
    source_root = Path(r"D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\assets\chromatography")
    model_root = source_root / "gc_retention_time"
    if not model_root.is_dir():
        pytest.skip("audited GC assets are unavailable")
    runtime = GcRuntime.from_registered_artifact(registry_root=source_root, model_root=model_root, business_scope_id=GC_BUSINESS_SCOPE_ID)
    if runtime.status.status != "READY":
        pytest.skip(f"audited GC runtime unavailable: {runtime.status.reason}")
    series = [40.0] * 2 + [min(200.0, 40.0 + (minute - 2) * 20.0) for minute in range(2, 30)]
    first = runtime._predict(normalized_smiles=["CCCCCCC"], temperature_series_c=series, for_verification=True)
    second = runtime._predict(normalized_smiles=["CCCCCCC"], temperature_series_c=series, for_verification=True)
    assert len(first["predictions"]) == 1
    assert GcRuntime._valid_model_output(first, ["CCCCCCC"])
    assert GcRuntime._valid_model_output(second, ["CCCCCCC"])
    assert first == second
    assert first["preprocessing_protocol_id"] == GC_PREPROCESSING_PROTOCOL_ID
    assert runtime.verified_handle is None
