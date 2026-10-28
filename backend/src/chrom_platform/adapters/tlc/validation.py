"""Pure request checks shared by the API and TLC worker adapter."""

from __future__ import annotations

from pydantic import BaseModel

from chrom_platform.core.errors import InputValidationError
from chrom_platform.modules.prediction.schemas import (
    TlcConditionComparisonCreate,
    TlcPredictionCreate,
    TlcReverseCreate,
)

from .manifest import TLC_ARTIFACT_ID, TLC_INPUT_SCHEMA_VERSION, TLC_MODEL_VERSION, TLC_SCOPE_ID
from .runtime import validate_mobile_phase


TLC_REQUEST_TYPES = (TlcPredictionCreate, TlcConditionComparisonCreate, TlcReverseCreate)


def validate_tlc_request(request: BaseModel) -> BaseModel:
    if not isinstance(request, TLC_REQUEST_TYPES):
        raise InputValidationError("TLC adapter requires a TLC request DTO")
    if request.chromatography_type != "tlc" or request.tool not in {"single", "condition_compare", "reverse_solvent"}:
        raise InputValidationError("TLC request is outside the bound tool")
    if request.schema_version != TLC_INPUT_SCHEMA_VERSION:
        raise InputValidationError("TLC request schema is outside the bound model")
    selection = request.model_selection
    if selection.mode == "champion" and selection.scope_id != TLC_SCOPE_ID:
        raise InputValidationError("TLC request scope is outside the bound champion model")
    if selection.mode == "exact" and (selection.model_id != TLC_ARTIFACT_ID or selection.model_version != TLC_MODEL_VERSION):
        raise InputValidationError("TLC request model is outside the bound artifact")
    phases = [request.input] if request.tool == "single" else request.conditions if request.tool == "condition_compare" else []
    for phase in phases:
        try:
            validate_mobile_phase({item.solvent_id: item.fraction for item in phase.solvent_components})
        except ValueError as exc:
            raise InputValidationError(str(exc)) from exc
    return request

