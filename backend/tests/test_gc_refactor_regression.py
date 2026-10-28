"""Bounded old-checkpoint regression; does not issue a serving approval."""

import hashlib
import json
from pathlib import Path

from chrom_platform.adapters.gc.runtime import GcRuntime
from chrom_platform.adapters.gc.serving_profile import GC_REFERENCE_FIXTURE_SHA256


def test_fixed_checkpoint_refactor_retains_server_golden_without_receipt(monkeypatch):
    from test_gc_adapter import test_real_gc_seeded_preprocessing_is_repeatable_without_issuing_a_handle

    fixture_path = Path(__file__).resolve().parents[2] / "testing/fixtures/priority-launch/gc-reference-preprocessing-v2.json"
    raw = fixture_path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == GC_REFERENCE_FIXTURE_SHA256
    fixture = json.loads(raw)
    outputs = []
    original = GcRuntime._predict

    def capture(runtime, **kwargs):
        assert kwargs["for_verification"] is True
        assert kwargs["normalized_smiles"] == fixture["input"]["smiles"]
        assert kwargs["temperature_series_c"] == fixture["expected_raw"]["temperature_series_c"]
        output = original(runtime, **kwargs)
        outputs.append(output)
        assert runtime.verified_handle is None
        assert runtime._profile_receipt is None
        return output

    monkeypatch.setattr(GcRuntime, "_predict", capture)
    test_real_gc_seeded_preprocessing_is_repeatable_without_issuing_a_handle()
    assert len(outputs) == 2
    assert all(GcRuntime._matches_golden(output, fixture["expected_raw"]) for output in outputs)
