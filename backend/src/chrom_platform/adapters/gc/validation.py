"""Pure GC request and temperature-program validation shared with the API."""

from __future__ import annotations

import math
from typing import Any

from chrom_platform.modules.prediction.adapters import PredictionContractError
from chrom_platform.modules.prediction.schemas import GcLinearTemperatureProgram, GcPredictionCreate

from .manifest import GC_ARTIFACT_ID, GC_INPUT_SCHEMA_VERSION, GC_MODEL_VERSION
from .serving_profile import GC_SERVING_PROFILE_ID, GC_SERVING_PROFILE_SHA256


def temperature_series(program: Any) -> list[float]:
    if getattr(program, "mode", None) == "series":
        try:
            values = [float(value) for value in program.temperature_series_c]
        except (AttributeError, TypeError, ValueError) as exc:
            raise PredictionContractError("GC requires exactly 30 finite temperature points") from exc
        if len(values) != 30 or not all(math.isfinite(value) for value in values):
            raise PredictionContractError("GC requires exactly 30 finite temperature points")
        return values
    if not isinstance(program, GcLinearTemperatureProgram):
        raise PredictionContractError("GC temperature program is unsupported")
    try:
        initial, final, rate, hold = (
            float(program.initial_temperature_c), float(program.final_temperature_c),
            float(program.heating_rate_c_per_min), float(program.initial_hold_min),
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise PredictionContractError("GC temperature program must contain numeric fields") from exc
    if not all(math.isfinite(value) for value in (initial, final, rate, hold)):
        raise PredictionContractError("GC temperature program must be finite")
    if rate <= 0 or hold < 0:
        raise PredictionContractError("GC heating rate must be > 0 and initial hold must be >= 0")
    if final < initial:
        raise PredictionContractError("GC final temperature must be >= initial temperature")
    if not hold.is_integer():
        raise PredictionContractError("GC initial hold must be an integer number of minutes")
    result: list[float] = []
    for minute in range(30):
        elapsed = minute - int(program.initial_hold_min)
        result.append(initial if elapsed < 0 else min(final, initial + elapsed * rate))
    return result


def validate_gc_request(request: Any) -> GcPredictionCreate:
    if not isinstance(request, GcPredictionCreate):
        raise PredictionContractError("GC adapter requires GcPredictionCreate")
    if request.chromatography_type != "gc" or request.tool != "single" or request.schema_version != GC_INPUT_SCHEMA_VERSION:
        raise PredictionContractError("GC request is outside the bound tool contract")
    if len(request.structure_refs) != 1:
        raise PredictionContractError("GC requires exactly one structure ref")
    selection = request.model_selection
    if selection.mode != "exact" or (
        selection.model_id != GC_ARTIFACT_ID or selection.model_version != GC_MODEL_VERSION
        or selection.preprocessing_profile_id != GC_SERVING_PROFILE_ID
        or selection.preprocessing_profile_sha256 != GC_SERVING_PROFILE_SHA256
    ):
        raise PredictionContractError("GC request selects an unbound model")
    temperature_series(request.input.temperature_program)
    return request

