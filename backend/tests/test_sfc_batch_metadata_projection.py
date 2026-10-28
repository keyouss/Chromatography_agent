"""Synthetic catalogue projection parity; no model or structure execution."""
from types import SimpleNamespace

import pytest

from chrom_platform.modules.prediction.batch_service import BatchPredictionService, BatchServiceError


@pytest.mark.parametrize("metadata_only", [False, True])
def test_sfc_batch_counts_same_domain_for_worker_and_metadata_registry(metadata_only):
    entry = SimpleNamespace(allowed_columns=("c",), stationary_phase="p", mode="normal")
    candidates = [SimpleNamespace(column=column, stationary_phase="p", mode="normal") for column in ("c", "other")]
    catalog = SimpleNamespace(resolve_with_policy=lambda *_args, **_kwargs: {"candidates": candidates})
    binding = SimpleNamespace(candidate_catalog=catalog)
    if metadata_only:
        binding.allowed_tool = "single_column"
        binding.registry_entry = vars(entry)
    else:
        binding.runtime = SimpleNamespace(allowed_tool="single_column", registry=SimpleNamespace(entry=lambda *_args, **_kwargs: entry))
    registry = SimpleNamespace(metadata_only=metadata_only, adapter=lambda *_: SimpleNamespace(binding_for=lambda *_: binding))
    service = BatchPredictionService(capabilities=registry)
    assert service._sfc_row_evaluations(project_id="p", model_snapshot={"id": "m", "version": "1"}, row={"candidate_scope_id": "s", "column_id": "c"}) == (1, None)
    with pytest.raises(BatchServiceError):
        service._sfc_row_evaluations(project_id="p", model_snapshot={"id": "m", "version": "1"}, row={"candidate_scope_id": "s", "column_id": "outside"})
