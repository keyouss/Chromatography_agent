"""Pure HPLC DTO and serving-profile validation shared with the API."""

from __future__ import annotations

from typing import Any

from chrom_platform.modules.prediction.adapters import PredictionContractError
from chrom_platform.modules.prediction.schemas import HplcPredictionCreate

from .manifest import (
    HPLC_ARTIFACT_ID,
    HPLC_COLUMN_NAMES,
    HPLC_INPUT_SCHEMA_VERSION,
    HPLC_MODEL_VERSION,
    HPLC_TOOL_ID,
)
from .serving_profile import HPLC_SERVING_PROFILE_ID, HPLC_SERVING_PROFILE_SHA256


def validate_hplc_request(request: Any) -> HplcPredictionCreate:
    if not isinstance(request, HplcPredictionCreate):
        raise PredictionContractError("HPLC adapter requires HplcPredictionCreate")
    if request.chromatography_type != "hplc" or request.tool != HPLC_TOOL_ID or request.schema_version != HPLC_INPUT_SCHEMA_VERSION:
        raise PredictionContractError("HPLC request is outside the bound tool contract")
    if len(request.structure_refs) != 2 or request.structure_refs[0] == request.structure_refs[1]:
        raise PredictionContractError("HPLC requires exactly two distinct ordered structure refs")
    selection = request.model_selection
    if selection.mode != "exact":
        raise PredictionContractError("HPLC requires an exact model and serving profile selection")
    if (
        selection.model_id != HPLC_ARTIFACT_ID
        or selection.model_version != HPLC_MODEL_VERSION
        or selection.preprocessing_profile_id != HPLC_SERVING_PROFILE_ID
        or selection.preprocessing_profile_sha256 != HPLC_SERVING_PROFILE_SHA256
    ):
        raise PredictionContractError("HPLC exact model/profile binding is stale or unsupported")
    if not 0.0 < request.input.isopropanol_fraction < 1.0:
        raise PredictionContractError("HPLC isopropanol fraction must be strictly between 0 and 1")
    if not request.input.column_ids or any(column not in HPLC_COLUMN_NAMES for column in request.input.column_ids):
        raise PredictionContractError("HPLC request contains an unsupported column")
    return request

