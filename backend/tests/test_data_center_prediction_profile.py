from __future__ import annotations

import pytest
from pydantic import ValidationError

from chrom_platform.modules.data_center.schemas import (
    PredictionDraftExactModelSelection,
    PredictionDraftSourceModelSnapshot,
)


def test_prediction_draft_profile_pair_accepts_legacy_none_and_complete_binding() -> None:
    legacy_selection = PredictionDraftExactModelSelection.model_validate({
        "mode": "exact", "model_id": "model-a", "model_version": "1",
    })
    legacy_snapshot = PredictionDraftSourceModelSnapshot.model_validate({
        "id": "model-a", "version": "1", "business_scope_id": "scope-a",
        "artifact_sha256": "a" * 64, "input_schema_version": "1.0", "output_schema_version": "1.0",
    })
    assert legacy_selection.preprocessing_profile_id is None
    assert legacy_snapshot.preprocessing_profile_sha256 is None

    selection = PredictionDraftExactModelSelection.model_validate({
        "mode": "exact", "model_id": "model-a", "model_version": "1",
        "preprocessing_profile_id": "profile-v1", "preprocessing_profile_sha256": "b" * 64,
    })
    snapshot = PredictionDraftSourceModelSnapshot.model_validate({
        "id": "model-a", "version": "1", "business_scope_id": "scope-a",
        "artifact_sha256": "a" * 64, "input_schema_version": "1.0", "output_schema_version": "1.0",
        "preprocessing_profile_id": "profile-v1", "preprocessing_profile_sha256": "b" * 64,
    })
    assert selection.preprocessing_profile_sha256 == snapshot.preprocessing_profile_sha256


@pytest.mark.parametrize(
    "partial",
    [
        {"preprocessing_profile_id": "profile-v1"},
        {"preprocessing_profile_sha256": "b" * 64},
        {"preprocessing_profile_id": "profile-v1", "preprocessing_profile_sha256": "B" * 64},
    ],
)
def test_prediction_draft_profile_pair_rejects_partial_or_invalid_hash(partial) -> None:
    selection_data = {"mode": "exact", "model_id": "model-a", "model_version": "1", **partial}
    snapshot_data = {
        "id": "model-a", "version": "1", "business_scope_id": "scope-a",
        "artifact_sha256": "a" * 64, "input_schema_version": "1.0", "output_schema_version": "1.0",
        **partial,
    }
    with pytest.raises(ValidationError):
        PredictionDraftExactModelSelection.model_validate(selection_data)
    with pytest.raises(ValidationError):
        PredictionDraftSourceModelSnapshot.model_validate(snapshot_data)
