"""Package-only copy of fixed GC construction and state-key policy.

Copied from the reviewed ``runtime.py`` at SHA-256
``d63f1f523d355b1e5a13a5dfb4d938cdc237e66f2ce7454a21f3b308bc54eea2``
for model version ``gc-gnn-seq240408-250c-600-prep-rdkit-seed42-v1``. Keep
this child-only copy under the separate package source inventory; do not move
or alter the legacy serving runtime/profile contract.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from chrom_platform.adapters.gc.runtime import GcAssetUnavailable


def build_gc_package_model(author: Mapping[str, Any], torch: Any) -> Any:
    gin = author["GIN"]
    model_module = author["model"]
    cpu = torch.device("cpu")
    gin.device = cpu
    model_module.device = cpu
    return model_module.MultiSeqModel(
        {"num_tasks": 3, "num_layers": 5, "emb_dim": 128,
         "drop_ratio": 0.0, "graph_pooling": "sum", "descriptor_dim": 1827},
        {"seq_len": 32, "d_model": 64, "num_layers": 3, "dropout": 0.1},
        num_tasks=3,
    ).to(cpu)


def load_gc_package_state_dict(model: Any, state: object) -> Any:
    if not isinstance(state, Mapping):
        raise GcAssetUnavailable("MODEL_STATE_INVALID")
    try:
        incompatible = model.load_state_dict(state, strict=False)
    except (RuntimeError, TypeError, ValueError) as exc:
        raise GcAssetUnavailable("MODEL_STATE_API_MISMATCH") from exc
    allowed_missing = {name for name in model.state_dict() if name.endswith(".rbf_list.0.gamma")}
    if set(incompatible.unexpected_keys) or set(incompatible.missing_keys) != allowed_missing:
        raise GcAssetUnavailable("MODEL_STATE_API_MISMATCH")
    model.eval()
    return model


__all__ = ["build_gc_package_model", "load_gc_package_state_dict"]
