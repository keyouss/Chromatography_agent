"""Canonical descriptor for the sole supported GC serving profile."""

from __future__ import annotations

import hashlib
import json

from .manifest import (
    GC_ARTIFACT_ID,
    GC_AUTHOR_INPUT_SCHEMA_VERSION,
    GC_INPUT_SCHEMA_VERSION,
    GC_MODEL_SHA256,
    GC_MODEL_VERSION,
    GC_OUTPUT_SCHEMA_VERSION,
    GC_PREPROCESSING_PROTOCOL_ID,
    GC_RDKIT_EMBED_PARAMETERS,
    GC_RDKIT_MMFF_PARAMETERS,
    GC_TRUSTED_FILE_SHA256,
)
from .source_pins import GC_ADAPTER_SOURCE_SHA256, GC_RUNTIME_SOURCE_SHA256
from chrom_platform.modules.prediction.schemas import GcPredictionCreate, GcPredictionResult

GC_SERVING_PROFILE_ID = "gc-gnn-seq240408-250c-rdkit-seed42-v1"
GC_REFERENCE_FIXTURE_ID = "gc-reference-preprocessing-v2.json"
GC_REFERENCE_FIXTURE_SHA256 = "06035bb25618b14a3b2547bb238167b19d3b593e7a2e0e4c3d40b08409d3e597"
GC_REFERENCE_ENVIRONMENT = {
    "python": "3.11.15",
    "rdkit": "2026.03.3",
    "torch": "2.6.0+cu124",
    "numpy": "1.26.4",
    "mordred": "1.2.0",
}


def gc_serving_profile_descriptor() -> dict[str, object]:
    """Return public, path-free, immutable serving facts for the GC model."""
    input_schema = GcPredictionCreate.model_json_schema()
    definitions = input_schema.get("$defs", {})
    linear = definitions.get("GcLinearTemperatureProgram", {})
    hold = linear.get("properties", {}).get("initial_hold_min", {})
    if isinstance(hold, dict):
        hold["multipleOf"] = 1
    series = definitions.get("GcSeriesTemperatureProgram", {})
    points = series.get("properties", {}).get("temperature_series_c", {})
    if isinstance(points, dict):
        points["minItems"] = 30
        points["maxItems"] = 30
    output_schema = GcPredictionResult.model_json_schema()
    return {
        "profile_id": GC_SERVING_PROFILE_ID,
        "model_id": GC_ARTIFACT_ID,
        "model_version": GC_MODEL_VERSION,
        "artifact_sha256": GC_MODEL_SHA256,
        "platform_input_schema_version": GC_INPUT_SCHEMA_VERSION,
        "author_input_schema_version": GC_AUTHOR_INPUT_SCHEMA_VERSION,
        "output_schema_version": GC_OUTPUT_SCHEMA_VERSION,
        "input_schema_id": "GcPredictionCreate",
        "input_schema_sha256": _canonical_sha256(input_schema),
        "output_schema_id": "GcPredictionResult",
        "output_schema_sha256": _canonical_sha256(output_schema),
        "reference_fixture_id": GC_REFERENCE_FIXTURE_ID,
        "reference_fixture_sha256": GC_REFERENCE_FIXTURE_SHA256,
        "preprocessing_protocol_id": GC_PREPROCESSING_PROTOCOL_ID,
        "preprocessing": {
            "rdkit_embed_multiple_confs": dict(GC_RDKIT_EMBED_PARAMETERS),
            "rdkit_mmff_optimize_molecule_confs": dict(GC_RDKIT_MMFF_PARAMETERS),
            "molecule_selection": "author runtime uses RDKit default conformer serialization; no platform conformer ranking or pruning",
            "identity_normalization": "RDKit MolFromSmiles followed by MolToSmiles(canonical=True, isomericSmiles=True)",
            "3d_serialization": "AddHs; EmbedMultipleConfs with pinned ETversion=2/seed=42 parameters; MMFFOptimizeMoleculeConfs; RemoveHs; MolToMolFile(confId=-1 default)",
            "feature_source_sha256": dict(GC_TRUSTED_FILE_SHA256),
            "runtime_source_sha256": GC_RUNTIME_SOURCE_SHA256,
            "adapter_source_sha256": GC_ADAPTER_SOURCE_SHA256,
            "environment_reference": dict(GC_REFERENCE_ENVIRONMENT),
        },
        "input_contract": {
            "structure_refs": 1,
            "temperature_series_points": 30,
            "temperature_series_unit": "degC",
            "temperature_program_modes": ["linear", "series"],
            "linear_initial_hold_min": "integer >= 0",
            "linear_final_temperature_c": ">= initial_temperature_c",
            "heating_rate_c_per_min": "> 0",
        },
        "output_contract": {
            "schema_version": GC_OUTPUT_SCHEMA_VERSION,
            "retention_time_unit": "min",
            "quantiles": ["q10", "q50", "q90"],
        },
    }


def gc_serving_profile_sha256() -> str:
    return _canonical_sha256(gc_serving_profile_descriptor())


def _canonical_sha256(value: object) -> str:
    canonical = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


GC_SERVING_PROFILE_SHA256 = gc_serving_profile_sha256()

__all__ = [
    "GC_REFERENCE_ENVIRONMENT",
    "GC_REFERENCE_FIXTURE_ID",
    "GC_REFERENCE_FIXTURE_SHA256",
    "GC_SERVING_PROFILE_ID",
    "GC_SERVING_PROFILE_SHA256",
    "gc_serving_profile_descriptor",
    "gc_serving_profile_sha256",
]
