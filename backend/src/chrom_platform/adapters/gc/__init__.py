"""Controlled GC temperature-program retention-time adapter."""

from .adapter import GcPredictionAdapter, build_gc_adapter
from .manifest import (
    GC_ARTIFACT_ID,
    GC_SCOPE_ID,
    GC_BUSINESS_SCOPE_ID,
    GC_INPUT_SCHEMA_VERSION,
    GC_MODEL_SHA256,
    GC_MODEL_VERSION,
    GC_OUTPUT_SCHEMA_VERSION,
)

__all__ = [
    "GC_ARTIFACT_ID",
    "GC_SCOPE_ID",
    "GC_BUSINESS_SCOPE_ID",
    "GC_INPUT_SCHEMA_VERSION",
    "GC_MODEL_SHA256",
    "GC_MODEL_VERSION",
    "GC_OUTPUT_SCHEMA_VERSION",
    "GcPredictionAdapter",
    "build_gc_adapter",
]
