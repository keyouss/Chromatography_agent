"""HTTP DTOs for TLC utilities; prediction create/query stays in M2 schemas."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, model_validator

from chrom_platform.modules.prediction.schemas import (
    Id,
    ModelSelection,
    PredictionStrictModel,
    StructureRefId,
    TlcInput,
    TlcConditionComparisonCreate,
    TlcConditionComparisonResult,
    TlcReverseCreate,
    TlcReverseResult,
)


class TlcTargetRf(PredictionStrictModel):
    min: float = Field(ge=0.0, le=1.0)
    max: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_order(self) -> "TlcTargetRf":
        if self.min > self.max:
            raise ValueError("target_rf.min must be <= target_rf.max")
        return self


class TlcConditionComparisonView(PredictionStrictModel):
    condition_index: int = Field(ge=0)
    mobile_phase: dict[str, float]
    predicted_rf: float = Field(ge=0.0, le=1.0)
    component_predictions: dict[str, float]


class TlcReverseCandidateView(PredictionStrictModel):
    system_id: Id
    mobile_phase: dict[str, float]
    predicted_rf: float = Field(ge=0.0, le=1.0)
    source: str = "model"


__all__ = [
    "TlcConditionComparisonCreate",
    "TlcConditionComparisonResult",
    "TlcConditionComparisonView",
    "TlcReverseCandidateView",
    "TlcReverseCreate",
    "TlcReverseResult",
    "TlcTargetRf",
]
