"""Pure request checks shared by the API and CC worker adapter."""

from __future__ import annotations

from typing import Any

from chrom_platform.core.errors import InputValidationError
from chrom_platform.modules.prediction.schemas import CcPredictionCreate

from .condition_catalog import CC_NUMERIC_INPUT_RANGES, CcConditionCatalog

CC_INPUT_SCHEMA_VERSION = "1.0"


def validate_cc_request(request: Any, catalog: CcConditionCatalog) -> CcPredictionCreate:
    if not isinstance(request, CcPredictionCreate):
        raise InputValidationError("CC adapter requires CcPredictionCreate")
    if request.schema_version != CC_INPUT_SCHEMA_VERSION or request.tool != "single" or request.chromatography_type != "cc":
        raise InputValidationError("CC request protocol binding is invalid")
    if len(request.structure_refs) != 2 or request.structure_refs[0] == request.structure_refs[1]:
        raise InputValidationError("CC requires exactly two distinct structures")
    try:
        catalog.resolve_eluent_system(request.input.eluent_system_id)
    except KeyError as exc:
        if exc.args == ("CC_CONDITION_UNAVAILABLE",):
            raise InputValidationError("CC eluent system is not supported") from exc
        raise
    try:
        catalog.resolve_loading_solvent(request.input.loading_solvent_id)
    except KeyError as exc:
        if exc.args == ("CC_LOADING_SOLVENT_UNAVAILABLE",):
            raise InputValidationError("CC loading solvent is not supported") from exc
        raise
    if request.input.column_spec_id not in catalog.column_spec_ids:
        raise InputValidationError("CC column_spec_id is outside the verified catalog")
    try:
        catalog.validate_numeric_inputs(request.input)
    except ValueError as exc:
        if any(str(exc).startswith(f"{name} must be") for name in CC_NUMERIC_INPUT_RANGES):
            raise InputValidationError(str(exc)) from exc
        raise
    return request

