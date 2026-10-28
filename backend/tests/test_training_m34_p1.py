from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from chrom_platform.modules.training.schemas import MetricPoint, ExperimentComparisonMetricValue


def test_training_metric_values_reject_non_finite_numbers() -> None:
    with pytest.raises(ValidationError):
        MetricPoint(key="loss", label="Loss", value=math.nan, step=0, split="train", attempt=1, timestamp="2026-01-01T00:00:00Z")
    with pytest.raises(ValidationError):
        ExperimentComparisonMetricValue(resource_id="exp-1", value=math.inf)
