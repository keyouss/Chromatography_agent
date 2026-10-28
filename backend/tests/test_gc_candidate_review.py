"""Isolated, non-scientific GC v1.1 review and snapshot boundary tests.

All runtime receipts below are synthetic unit fixtures. They are not production
verification evidence and do not load assets, preprocess molecules, or forward a model.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest
from pydantic import TypeAdapter

import chrom_platform.adapters.gc.adapter as gc_adapter_module
import chrom_platform.adapters.gc.runtime as gc_runtime_module
from chrom_platform.adapters.gc.adapter import GcPredictionAdapter, build_gc_profile_candidate_adapter
from chrom_platform.adapters.gc.runtime import (
    GcProfileReviewApproval,
    GcProfileVerificationReceipt,
    GcRuntime,
)
from chrom_platform.modules.prediction.adapters import PredictionContractError
from chrom_platform.modules.prediction.schemas import (
    GcPredictionCreate,
    GcPredictionReadV10,
    ModelSnapshot,
    PredictionExecutionInputSnapshot,
    PredictionStoredRead,
    validate_gc_execution_binding,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
GC_FIXTURE = REPO_ROOT / "testing" / "fixtures" / "priority-launch" / "gc-reference-preprocessing-v2.json"


def _request(**changes: object) -> GcPredictionCreate:
    payload: dict[str, object] = {
        "project_id": "project-a",
        "chromatography_type": "gc",
        "tool": "single",
        "schema_version": "1.1",
        "model_selection": {
            "mode": "exact",
            "model_id": "gc-model",
            "model_version": "gc-version",
            "preprocessing_profile_id": "gc-profile",
            "preprocessing_profile_sha256": "a" * 64,
        },
        "structure_refs": ["structure-a"],
        "input": {"temperature_program": {"mode": "series", "temperature_series_c": [40.0] * 30}},
    }
    payload.update(changes)
    return GcPredictionCreate.model_validate(payload)


def _snapshots() -> tuple[GcPredictionCreate, ModelSnapshot, PredictionExecutionInputSnapshot]:
    request = _request()
    model = ModelSnapshot(
        id="gc-model", version="gc-version", business_scope_id="gc-scope",
        artifact_sha256="b" * 64, input_schema_version="1.1", output_schema_version="1.0",
        preprocessing_profile_id="gc-profile", preprocessing_profile_sha256="a" * 64,
    )
    snapshot = PredictionExecutionInputSnapshot(
        structure_refs=["structure-a"], normalized_structures=["CCCCCCC"], input=request.input,
    )
    return request, model, snapshot


def test_gc_v11_selection_profile_pair_is_required_and_nonnullable() -> None:
    schema = GcPredictionCreate.model_json_schema()
    selection = schema["$defs"]["GcExactProfileModelSelection"]
    assert set(selection["required"]) >= {"preprocessing_profile_id", "preprocessing_profile_sha256"}
    for name in ("preprocessing_profile_id", "preprocessing_profile_sha256"):
        field = selection["properties"][name]
        assert field["type"] == "string"
        assert "anyOf" not in field
        assert "default" not in field

    payload = _request().model_dump(mode="python")
    payload["model_selection"] = {"mode": "exact", "model_id": "gc-model", "model_version": "gc-version"}
    with pytest.raises(ValueError):
        GcPredictionCreate.model_validate(payload)


def test_gc_v11_single_structure_cardinality_matches_schema_and_preserves_other_contracts() -> None:
    from chrom_platform.modules.prediction.schemas import HplcPredictionCreate, PredictionCreateBase

    gc_refs = GcPredictionCreate.model_json_schema()["properties"]["structure_refs"]
    assert (gc_refs["minItems"], gc_refs["maxItems"]) == (1, 1)
    request = _request()
    assert request.structure_refs == ["structure-a"]
    for refs in ([], ["structure-a", "structure-b"]):
        payload = request.model_dump(mode="python")
        payload["structure_refs"] = refs
        with pytest.raises(ValueError):
            GcPredictionCreate.model_validate(payload)
    for model, bounds in ((GcPredictionReadV10, (1, 1)), (HplcPredictionCreate, (2, 2)), (PredictionCreateBase, (1, 2))):
        refs_schema = model.model_json_schema()["properties"]["structure_refs"]
        assert (refs_schema["minItems"], refs_schema["maxItems"]) == bounds
    legacy = request.model_dump(mode="python")
    legacy["schema_version"] = "1.0"
    assert GcPredictionReadV10.model_validate(legacy).structure_refs == ["structure-a"]


def test_gc_stored_v10_remains_readable_but_cannot_enter_create() -> None:
    old = {
        "project_id": "project-a", "chromatography_type": "gc", "tool": "single",
        "schema_version": "1.0", "model_selection": {"mode": "champion", "scope_id": "gc-scope"},
        "structure_refs": ["structure-a"],
        "input": {"temperature_program": {"mode": "series", "temperature_series_c": [40.0] * 30}},
    }
    stored = TypeAdapter(PredictionStoredRead).validate_python(old)
    assert isinstance(stored, GcPredictionReadV10)
    with pytest.raises(ValueError):
        GcPredictionCreate.model_validate(old)


def test_gc_execution_binding_rejects_schema_ref_and_normalized_container_mismatch() -> None:
    request, model, snapshot = _snapshots()
    validate_gc_execution_binding(request=request, model_snapshot=model, input_snapshot=snapshot)
    bad_models = [
        model.model_copy(update={"input_schema_version": "1.0"}),
        model.model_copy(update={"id": "other-model"}),
        model.model_copy(update={"version": "other-version"}),
        model.model_copy(update={"preprocessing_profile_id": "other-profile"}),
        model.model_copy(update={"preprocessing_profile_sha256": "c" * 64}),
    ]
    bad_snapshots = [snapshot.model_copy(update={"structure_refs": ["other-ref"]})]
    changed_input = _request(input={
        "temperature_program": {"mode": "series", "temperature_series_c": [41.0] * 30},
    }).input
    bad_snapshots.append(snapshot.model_copy(update={"input": changed_input}))
    bad_snapshots.extend(
        snapshot.model_copy(update={"normalized_structures": value})
        for value in ("CCCCCCC", b"CCCCCCC", [" "])
    )
    for bad_model in bad_models:
        with pytest.raises(ValueError, match="snapshots"):
            validate_gc_execution_binding(request=request, model_snapshot=bad_model, input_snapshot=snapshot)
    for bad_snapshot in bad_snapshots:
        with pytest.raises(ValueError, match="snapshots"):
            validate_gc_execution_binding(request=request, model_snapshot=model, input_snapshot=bad_snapshot)


def _synthetic_receipt() -> GcProfileVerificationReceipt:
    return GcProfileVerificationReceipt(
        runtime_nonce="synthetic-runtime-nonce", generation=7,
        profile_id=gc_runtime_module.GC_SERVING_PROFILE_ID,
        profile_sha256=gc_runtime_module.GC_SERVING_PROFILE_SHA256,
        model_id=gc_runtime_module.GC_ARTIFACT_ID, model_version=gc_runtime_module.GC_MODEL_VERSION,
        artifact_sha256=gc_runtime_module.GC_MODEL_SHA256,
        author_input_schema_version=gc_runtime_module.GC_AUTHOR_INPUT_SCHEMA_VERSION,
        platform_input_schema_version=gc_runtime_module.GC_INPUT_SCHEMA_VERSION,
        input_schema_id="GcPredictionCreate", input_schema_sha256="c" * 64,
        output_schema_id="GcPredictionResult", output_schema_sha256="d" * 64,
        fixture_id="synthetic-fixture", fixture_sha256="e" * 64,
        environment_sha256="f" * 64, asset_inventory_sha256="1" * 64,
        adapter_source_sha256="2" * 64, runtime_source_sha256="3" * 64,
        verification_predict_attempts=2, verification_predict_successes=2,
        verification_forward_attempts=2, verification_forward_successes=2,
        output_sha256s=("4" * 64, "5" * 64), receipt_sha256="6" * 64,
    )


def _synthetic_runtime(receipt: GcProfileVerificationReceipt) -> tuple[GcRuntime, GcProfileReviewApproval]:
    """Build process-local synthetic state; it cannot verify or load a model."""
    runtime = object.__new__(GcRuntime)
    runtime._profile_lock = threading.RLock()
    runtime._runtime_nonce = "synthetic-runtime-nonce"
    runtime._review_generation = 7
    runtime._profile_receipt = receipt
    runtime._review_consumed = False
    runtime._verification_output_snapshots = None
    approval = GcProfileReviewApproval(
        runtime_nonce="synthetic-runtime-nonce", generation=7, receipt_sha256=receipt.receipt_sha256,
        profile_id=receipt.profile_id, profile_sha256=receipt.profile_sha256,
        qa_review_sha256="7" * 64, approval_nonce="8" * 64,
    )
    runtime._issued_review_approval = approval
    runtime._recheck_consumed_receipt = lambda _: None
    return runtime, approval


def test_synthetic_gc_approval_identity_consumes_once_and_rejects_clone_or_wrong_generation() -> None:
    receipt = _synthetic_receipt()
    runtime, approval = _synthetic_runtime(receipt)
    consumed = runtime._consume_profile_review_approval(approval)
    assert consumed is receipt
    assert runtime._review_consumed is True
    with pytest.raises(Exception, match="APPROVAL_INVALID"):
        runtime._consume_profile_review_approval(approval)

    receipt = _synthetic_receipt()
    runtime, approval = _synthetic_runtime(receipt)
    clone = GcProfileReviewApproval(**approval.__dict__)
    with pytest.raises(Exception, match="APPROVAL_INVALID"):
        runtime._consume_profile_review_approval(clone)
    assert runtime._profile_receipt is None and runtime._issued_review_approval is None
    assert runtime._review_consumed is True

    receipt = _synthetic_receipt()
    runtime, _ = _synthetic_runtime(receipt)
    wrong_generation = GcProfileReviewApproval(
        runtime_nonce="synthetic-runtime-nonce", generation=6, receipt_sha256=receipt.receipt_sha256,
        profile_id=receipt.profile_id, profile_sha256=receipt.profile_sha256,
        qa_review_sha256="7" * 64, approval_nonce="9" * 64,
    )
    runtime._issued_review_approval = wrong_generation
    with pytest.raises(Exception, match="APPROVAL_INVALID"):
        runtime._consume_profile_review_approval(wrong_generation)
    assert runtime._review_consumed is True


def test_gc_review_reset_clears_handle_receipt_and_generation_state() -> None:
    runtime, approval = _synthetic_runtime(_synthetic_receipt())
    runtime._verified_handle = object()
    runtime._bound_model_id = "old-model"
    runtime._bound_model_version = "old-version"
    runtime._bound_artifact_sha256 = "b" * 64
    runtime._verification_predict_attempts = 1
    runtime._verification_predict_successes = 1
    runtime._verification_forward_attempts = 1
    runtime._verification_forward_successes = 1
    runtime._verification_output_snapshots = ({"predictions": [{"rt_q50_min": 1.0}]}, {"predictions": [{"rt_q50_min": 1.0}]})
    runtime.reset_profile_review_state()
    assert runtime._verified_handle is None
    assert runtime._bound_model_id is None and runtime._bound_model_version is None
    assert runtime._bound_artifact_sha256 is None
    assert runtime._profile_receipt is None and runtime._issued_review_approval is None
    assert runtime._review_generation == 8
    assert runtime._verification_predict_attempts == runtime._verification_forward_attempts == 0
    assert runtime.verification_output_snapshots is None


def test_gc_candidate_consumes_review_before_handle_issue(monkeypatch: pytest.MonkeyPatch) -> None:
    receipt = _synthetic_receipt()
    runtime, approval = _synthetic_runtime(receipt)
    issued = False

    def stop_at_issue(*_args: object, **_kwargs: object) -> object:
        nonlocal issued
        issued = True
        assert runtime._review_consumed is True
        assert runtime._profile_receipt is None
        raise RuntimeError("synthetic stop before handle issue")

    monkeypatch.setattr(gc_adapter_module, "issue_verified_model_handle", stop_at_issue)
    with pytest.raises(RuntimeError, match="stop before handle"):
        build_gc_profile_candidate_adapter(runtime=runtime, structure_resolver={}, approval=approval)
    assert issued is True
    assert runtime._review_consumed is True


def _verification_runtime(fixture: dict[str, object], model_root: Path) -> GcRuntime:
    """Create a fake-ready runtime whose prediction function is replaced by tests."""
    runtime = object.__new__(GcRuntime)
    runtime._profile_lock = threading.RLock()
    runtime._runtime_nonce = "synthetic-verification-runtime"
    runtime._review_generation = 0
    runtime._verified_handle = object()
    runtime._bound_model_id = "old-model"
    runtime._bound_model_version = "old-version"
    runtime._bound_artifact_sha256 = "b" * 64
    runtime._profile_receipt = None
    runtime._issued_review_approval = None
    runtime._review_consumed = False
    runtime._verification_predict_attempts = 0
    runtime._verification_predict_successes = 0
    runtime._verification_forward_attempts = 0
    runtime._verification_forward_successes = 0
    runtime._verification_output_snapshots = None
    runtime.status = SimpleNamespace(
        checked_assets=True, dependencies_available=True, schema_verified=True,
    )
    runtime.manifest = SimpleNamespace(expected_files=lambda: {
        "MODEL_SHA256.txt": fixture["model"]["registry_sha256"],
        "predict.py": fixture["model"]["source_sha256"],
    })
    runtime.model_root = SimpleNamespace(path=model_root)
    return runtime


def test_gc_fixture_mapping_or_wrong_bytes_rejected_before_fake_prediction(tmp_path: Path) -> None:
    original_bytes = GC_FIXTURE.read_bytes()
    original = json.loads(original_bytes.decode("utf-8"))

    for tampered_mapping, fixture_path in (
        ({**copy.deepcopy(original), "acceptance_state": "tampered"}, GC_FIXTURE),
        (original, tmp_path / "wrong-fixture.json"),
    ):
        if fixture_path != GC_FIXTURE:
            fixture_path.write_bytes(original_bytes.replace(b"candidate", b"changed--"))
        runtime = _verification_runtime(original, tmp_path / "model")
        calls: list[int] = []
        runtime._predict = lambda **_: calls.append(1)
        report = runtime.verify_golden(tampered_mapping, fixture_path=fixture_path)
        assert report.fixture_verified is False
        assert calls == []


def test_gc_verification_uses_private_fixture_snapshot_if_caller_mutates_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    caller_fixture = json.loads(GC_FIXTURE.read_text(encoding="utf-8"))
    expected_original = copy.deepcopy(caller_fixture["expected_raw"])
    model_dir = tmp_path / "model"
    (model_dir / "Outcome" / "GNN_seq240408_250c").mkdir(parents=True)
    manifest_path = model_dir / "MODEL_SHA256.txt"
    manifest_path.write_text(caller_fixture["model"]["model_asset_sha256"], encoding="utf-8")
    runtime = _verification_runtime(caller_fixture, model_dir)
    module_hashes = {
        "MODEL_SHA256.txt": caller_fixture["model"]["registry_sha256"],
        "model_save_600.pth": caller_fixture["model"]["model_asset_sha256"],
    }
    monkeypatch.setattr(gc_runtime_module, "_sha256", lambda path: module_hashes[Path(path).name])
    environment = copy.deepcopy(caller_fixture["preprocessing_protocol"]["environment"])
    runtime._current_environment = lambda: environment
    runtime._platform_source_hashes = lambda: (
        gc_runtime_module.GC_ADAPTER_SOURCE_SHA256,
        gc_runtime_module.GC_RUNTIME_SOURCE_SHA256,
    )
    runtime._valid_model_output = lambda *_: True
    seen_expected: list[dict[str, object]] = []

    def matches(_actual: object, expected: dict[str, object]) -> bool:
        seen_expected.append(copy.deepcopy(expected))
        return expected == expected_original

    runtime._matches_golden = matches
    runtime._make_profile_receipt = lambda **_: None
    output = {
        "predictions": copy.deepcopy(expected_original["predictions"]),
        "failed_smiles": [],
        "temperature_series_c": copy.deepcopy(expected_original["temperature_series_c"]),
    }
    predict_calls = 0

    predict_inputs: list[dict[str, object]] = []

    def fake_predict(**kwargs: object) -> dict[str, object]:
        nonlocal predict_calls
        predict_calls += 1
        predict_inputs.append(copy.deepcopy(kwargs))
        if predict_calls == 1:
            caller_fixture["expected_raw"]["predictions"][0]["rt_q50_min"] = 9999.0
        return copy.deepcopy(output)

    runtime._predict = fake_predict
    report = runtime.verify_golden(caller_fixture, fixture_path=GC_FIXTURE)
    assert report.fixture_verified is True
    assert report.golden_sample_verified is True
    assert predict_calls == 2
    assert seen_expected == [expected_original]
    assert len(predict_inputs) == 2
    assert predict_inputs[0] == predict_inputs[1]
    assert predict_inputs[0]["normalized_smiles"] == ["CCCCCCC"]
    assert caller_fixture["expected_raw"]["predictions"][0]["rt_q50_min"] == 9999.0
    snapshots = runtime.verification_output_snapshots
    assert snapshots is not None and snapshots[0] == output and snapshots[1] == output
    snapshots[0]["predictions"][0]["rt_q50_min"] = -1.0
    assert runtime.verification_output_snapshots[0]["predictions"][0]["rt_q50_min"] == 10.781655


def test_gc_frozen_adapter_passes_copied_snapshot_and_never_reads_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = object.__new__(GcPredictionAdapter)
    request = _request()
    adapter.structure_resolver = lambda *_: pytest.fail("frozen path must not call live resolver")
    adapter.validate_request = lambda value: value
    adapter._predict_normalized = lambda _request, normalized: normalized
    monkeypatch.setattr(gc_adapter_module, "_canonical_isomeric_smiles", lambda value: value)
    frozen = ["CCCCCCC"]
    assert adapter.predict_with_normalized_structures(request, frozen) == "CCCCCCC"
    assert frozen == ["CCCCCCC"]
    for invalid in ("CCCCCCC", b"CCCCCCC", ["", "extra"]):
        with pytest.raises(PredictionContractError, match="execution snapshot"):
            adapter.predict_with_normalized_structures(request, invalid)
