"""Package helper checks without tensors, package parsing or model assets."""

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from chrom_platform.adapters.gc.package_model import build_gc_package_model, load_gc_package_state_dict
from chrom_platform.adapters.gc.package_source_pins import PACKAGE_SOURCE_SHA256
from chrom_platform.adapters.gc.runtime import GcAssetUnavailable


def test_package_source_inventory_matches_fixed_files():
    source = Path(__file__).resolve().parents[1] / "src"
    assert "chrom_platform/adapters/gc/package_model.py" in PACKAGE_SOURCE_SHA256
    assert "chrom_platform/adapters/gc/package_loader.py" in PACKAGE_SOURCE_SHA256
    for relative, digest in PACKAGE_SOURCE_SHA256.items():
        assert hashlib.sha256((source / relative).read_bytes()).hexdigest() == digest


def test_package_architecture_remains_fixed_on_cpu():
    calls = []
    model = SimpleNamespace(to=lambda device: calls.append(device) or model)
    module = SimpleNamespace(MultiSeqModel=lambda *args, **kwargs: calls.append((args, kwargs)) or model)
    gin = SimpleNamespace()
    assert build_gc_package_model({"GIN": gin, "model": module}, SimpleNamespace(device=lambda value: value)) is model
    assert calls == [((
        {"num_tasks": 3, "num_layers": 5, "emb_dim": 128, "drop_ratio": 0.0, "graph_pooling": "sum", "descriptor_dim": 1827},
        {"seq_len": 32, "d_model": 64, "num_layers": 3, "dropout": 0.1},
    ), {"num_tasks": 3}), "cpu"]
    assert gin.device == module.device == "cpu"


@pytest.mark.parametrize("missing,unexpected,valid", [
    (["layer.rbf_list.0.gamma"], [], True),
    ([], [], False),
    (["layer.rbf_list.0.gamma"], ["arbitrary"], False),
])
def test_package_state_policy_retains_exact_allowed_missing_keys(missing, unexpected, valid):
    calls = []

    def load(state, *, strict):
        assert state == {"weight": "fixed-test-value"}
        assert strict is False
        return SimpleNamespace(missing_keys=missing, unexpected_keys=unexpected)

    model = SimpleNamespace(
        load_state_dict=load,
        state_dict=lambda: {"weight": None, "layer.rbf_list.0.gamma": None},
        eval=lambda: calls.append("eval"),
    )
    if valid:
        assert load_gc_package_state_dict(model, {"weight": "fixed-test-value"}) is model
        assert calls == ["eval"]
    else:
        with pytest.raises(GcAssetUnavailable, match="MODEL_STATE_API_MISMATCH"):
            load_gc_package_state_dict(model, {"weight": "fixed-test-value"})
        assert calls == []
