"""Pure SFC request-shape validation shared by the API and Worker adapter."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel

from chrom_platform.modules.prediction.adapters import PredictionContractError
from chrom_platform.modules.prediction.schemas import SfcFullColumnExecutionV10, SfcFullColumnPredictionCreate, SfcSingleColumnExecutionV10, SfcSingleColumnPredictionCreate

from .runtime import SfcAssetUnavailable, SfcCandidate, SfcRequestValidationError, _ModelEntry


SFC_REQUEST_TYPES = (
    SfcSingleColumnPredictionCreate,
    SfcFullColumnPredictionCreate,
    SfcSingleColumnExecutionV10,
    SfcFullColumnExecutionV10,
)


def validate_sfc_request(request: BaseModel, *, allowed_tool: str) -> BaseModel:
    expected = (SfcSingleColumnPredictionCreate, SfcSingleColumnExecutionV10) if allowed_tool == "single_column" else (SfcFullColumnPredictionCreate, SfcFullColumnExecutionV10)
    if not isinstance(request, expected):
        raise PredictionContractError(f"SFC request must use {allowed_tool} DTO")
    if request.chromatography_type != "sfc":
        raise PredictionContractError("SFC adapter received another chromatography type")
    if request.tool != allowed_tool:
        raise PredictionContractError("SFC request tool is outside the bound model scope")
    return request


def candidate_from_value(value: SfcCandidate | Mapping[str, Any], index: int) -> SfcCandidate:
    """Normalize one catalog candidate without constructing model features."""
    if isinstance(value, SfcCandidate):
        if any(not math.isfinite(float(item)) for item in (
            value.length_mm, value.inner_diameter_mm, value.particle_size_um,
            value.column_temperature, value.flow_rate,
        )):
            raise ValueError("candidate contains a non-finite condition")
        return value
    if not isinstance(value, Mapping):
        raise ValueError("candidate resolver returned an invalid candidate")
    required = (
        "column", "stationary_phase", "mode", "length_mm", "inner_diameter_mm",
        "particle_size_um", "column_temperature", "flow_rate",
    )
    if any(key not in value for key in required):
        raise ValueError("candidate resolver omitted a required condition")
    numeric = {key: float(value[key]) for key in required[3:]}
    if any(not math.isfinite(number) for number in numeric.values()):
        raise ValueError("candidate contains a non-finite condition")
    return SfcCandidate(
        candidate_id=str(value.get("candidate_id", f"candidate-{index + 1}")),
        column=str(value["column"]), stationary_phase=str(value["stationary_phase"]),
        mode=str(value["mode"]), **numeric,
        observed_in_source=value.get("observed_in_source", True),
    )


def validate_sfc_domain(
    request: Any,
    *,
    entry: _ModelEntry,
    candidates: Sequence[SfcCandidate],
    modifiers: Sequence[tuple[str, float]],
    invalid_error: type[Exception] = SfcAssetUnavailable,
) -> None:
    """Pure preflight checks shared by API and Worker; never builds features."""
    total_fraction = float(request.input.modifier_total_fraction)
    if not math.isfinite(total_fraction):
        raise invalid_error("SFC_INPUT_NONFINITE")
    if any(not math.isfinite(float(fraction)) for _, fraction in modifiers):
        raise invalid_error("SFC_INPUT_NONFINITE")


def validate_sfc_preflight(
    request: Any,
    *,
    entry: _ModelEntry,
    candidates: Sequence[SfcCandidate | Mapping[str, Any]],
    allowed_tool: str,
    authorized_modifier_structures: Mapping[str, str] | None,
) -> tuple[SfcCandidate, ...]:
    """Validate a resolved, project/model-bound candidate scope and modifiers."""
    if authorized_modifier_structures is None:
        raise SfcAssetUnavailable("SFC_MODIFIER_SNAPSHOT_MISSING")
    normalized = tuple(candidate_from_value(item, index) for index, item in enumerate(candidates))
    if allowed_tool == "single_column":
        if request.input.column_id not in entry.allowed_columns:
            raise SfcRequestValidationError("SFC column is outside the bound model domain")
        normalized = tuple(
            item for item in normalized
            if item.column == request.input.column_id
            and item.stationary_phase == entry.stationary_phase
            and item.mode == entry.mode
        )
    if not normalized:
        raise SfcRequestValidationError("SFC candidate scope is empty for this model")
    modifiers: list[tuple[str, float]] = []
    for item in request.input.modifier_components:
        try:
            smiles = authorized_modifier_structures[item.structure_ref]
        except KeyError as exc:
            raise SfcAssetUnavailable("SFC_MODIFIER_SNAPSHOT_MISSING") from exc
        if smiles is None:
            raise SfcAssetUnavailable("SFC_MODIFIER_SNAPSHOT_MISSING")
        modifiers.append((str(smiles), float(item.fraction)))
    validate_sfc_domain(
        request, entry=entry, candidates=normalized, modifiers=modifiers,
        invalid_error=SfcRequestValidationError,
    )
    return normalized

