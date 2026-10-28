"""Build the API prediction facade from the currently attested Worker snapshot."""

from __future__ import annotations

from typing import Any

from chrom_platform.modules.prediction.api_registry import ApiPredictionRegistry


def build_prediction_api_registry(*, manifest: Any, session_factory: Any = None) -> ApiPredictionRegistry:
    """Return a model-light registry; no scientific runtime or artifact is loaded."""
    return ApiPredictionRegistry(manifest=manifest, session_factory=session_factory)


__all__ = ["build_prediction_api_registry"]

