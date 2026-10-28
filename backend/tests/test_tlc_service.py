from __future__ import annotations

import os
from pathlib import Path

import pytest

from chrom_platform.adapters.tlc.manifest import (
    TLC_ARTIFACT_ID,
    TLC_ARTIFACT_MANIFEST_SHA256,
    TLC_MODEL_VERSION,
    TLC_SCOPE_ID,
)
from chrom_platform.adapters.tlc.runtime import TlcRuntime
from chrom_platform.adapters.tlc.service import TlcBatchRow, TlcPredictionService
from chrom_platform.modules.prediction.adapters import (
    AdapterManifest,
    ModelVerificationEvidence,
    issue_verified_model_handle,
)


def test_tlc_code_does_not_reference_legacy_8010() -> None:
    root = Path(__file__).parents[1] / "src" / "chrom_platform" / "adapters" / "tlc"
    assert "8010" not in "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.py"))


def test_batch_keeps_row_failures_explicitly() -> None:
    runtime = object.__new__(TlcRuntime)
    runtime.status = type("Status", (), {"status": "READY", "reason": None})()

    def predict(*, smiles, mobile_phase):
        if smiles == "bad":
            raise ValueError("invalid SMILES")
        return {"rf": 0.5, "component_predictions": {"lgb": 0.5}, "ensemble_weights": {"lgb": 1.0}}

    runtime.predict = predict
    service = TlcPredictionService(runtime)
    rows = service.predict_batch(
        [
            TlcBatchRow("CCO", {"hexane": 0.8, "ethyl_acetate": 0.2}),
            TlcBatchRow("bad", {"hexane": 0.8, "ethyl_acetate": 0.2}),
        ]
    )
    assert rows[0].predicted_rf == 0.5
    assert rows[1].predicted_rf is None
    assert rows[1].error == "invalid SMILES"


@pytest.mark.skipif(
    os.environ.get("RUN_TLC_REAL_MODEL_SMOKE") != "1",
    reason="set RUN_TLC_REAL_MODEL_SMOKE=1 for real-model reverse checks",
)
def test_real_reverse_reports_unreachable_and_partial_failures() -> None:
    root = Path(
        r"D:\Jupyter\Chromatography_agent\deliverables\chromatography_tools_windows_cc_hplc_gc_tlc\tlc_rf"
    )
    runtime = TlcRuntime.from_registered_artifact(registry_root=root.parent, model_root=root)
    manifest = AdapterManifest(
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
    handle = issue_verified_model_handle(
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
            schema_by_tool={"single": {"input_schema": {}, "output_schema": {}}},
        ),
    )
    runtime.bind_verified_handle(handle)
    service = TlcPredictionService(runtime)
    unreachable = service.reverse_solvent(
        smiles="O=C(C1=CC=CC=C1)CCCl",
        target_rf=(0.0, 0.01),
        candidate_system_ids=["hexane/ethyl_acetate"],
        ratio_step=0.5,
        max_candidates=2,
    )
    assert not unreachable.target_reachable
    assert any("TARGET_UNREACHABLE" in warning for warning in unreachable.warnings)
    partial = service.reverse_solvent(
        smiles="not-a-smiles",
        target_rf=(0.0, 1.0),
        candidate_system_ids=["hexane/ethyl_acetate"],
        ratio_step=0.5,
        max_candidates=2,
    )
    assert partial.failed == 1
    assert any("PARTIAL_FAILURE" in warning for warning in partial.warnings)
