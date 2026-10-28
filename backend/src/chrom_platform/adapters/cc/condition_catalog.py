"""Versioned, server-owned CC conditions supported by the 4 g author model."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping


CC_CONDITION_CATALOG: dict[str, Any] = {
    "namespace": "cc-author-4g",
    "revision": "v1",
    "model": {
        "id": "qgeognn_silica_4g",
        "version": "cc-qgeognn-1",
        "artifact_sha256": "d6c4c113b2d02b4275b7d1cf3a572ac202d0bd8a1927d0158d0e7ab188ee4c99",
    },
    "sources": [
        {
            "path": "Services/column_chromatography_prediction-main/predict.py",
            "sha256": "b27d578f964f0c29afebee907d0377a37e982bfe0cfe2b9c889e255d48cbaabd",
        },
        {
            "path": "Services/column_chromatography_prediction-main/dataset/dataset_4g.csv",
            "sha256": "d485d3d46a96458d1baac11b2a21cd33374b1be42ba2303e7ae5823cc8ee553a",
            "rows": [2288, 2535],
        },
        {
            "path": "Services/column_chromatography_prediction-main/MODEL_SHA256.txt",
            "sha256": "921ea94276a0e6b289eefe13c4694f90cbf3f2d44f00a10612776819b01ad0e5",
        },
    ],
    "feature_mapping": {
        "eluent_system_id": "normalized petroleum_ether and ethyl_acetate relative parts",
        "loading_solvent_id": "author SOLVENT_CODES model category",
        "sample_mass_mg": "author density_g_ml * sample_volume_ul numeric feature (equivalent mg)",
        "loading_solvent_volume_ul": "author loading_solvent_volume_ul feature",
        "flow_rate_ml_min": "derived time only; not a model input",
        "column_spec_id": "selects the bound 4 g checkpoint",
    },
    "units": {
        "eluent_parts": "relative_parts",
        "sample_mass_mg": "mg",
        "loading_solvent_volume_ul": "uL",
        "flow_rate_ml_min": "mL/min",
    },
    "eluent_systems": [
        {
            "id": "cc4g-pe-ea-000-100-v1",
            "petroleum_ether": 0.0,
            "ethyl_acetate": 1.0,
        }
    ],
    "loading_solvents": [{"id": "cc4g-loading-dcm-v1", "model_code": "DCM"}],
    "column_spec_ids": ["silica-4g"],
}

CC_CONDITION_CATALOG_SHA256 = "d1ed7efc8ef8eed32fb556e9bf7cd8512f68ce08ff672ddc1db7f4d05f05c044"

# Observed numeric domain for the exact 4 g / PE:EA 0:1 / DCM subset of the
# source CSV. These bounds reject extrapolation; they do not validate every
# combination inside the rectangular range or establish model generalization.
CC_NUMERIC_INPUT_RANGES: dict[str, dict[str, float | str]] = {
    "sample_mass_mg": {"minimum": 30.0, "maximum": 327.3, "unit": "mg"},
    "loading_solvent_volume_ul": {"minimum": 200.0, "maximum": 2000.0, "unit": "uL"},
    "flow_rate_ml_min": {"minimum": 5.0, "maximum": 10.0, "unit": "mL/min"},
}


@dataclass(frozen=True)
class CcCondition:
    petroleum_ether: float
    ethyl_acetate: float


class CcConditionCatalog:
    def __init__(self, document: Mapping[str, Any] = CC_CONDITION_CATALOG) -> None:
        encoded = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if hashlib.sha256(encoded).hexdigest() != CC_CONDITION_CATALOG_SHA256:
            raise ValueError("CC_CONDITION_CATALOG_HASH_MISMATCH")
        if document.get("namespace") != "cc-author-4g" or document.get("revision") != "v1":
            raise ValueError("CC_CONDITION_CATALOG_VERSION_INVALID")
        model = document.get("model")
        if not isinstance(model, Mapping) or model.get("id") != "qgeognn_silica_4g" or model.get("version") != "cc-qgeognn-1":
            raise ValueError("CC_CONDITION_CATALOG_MODEL_INVALID")
        systems = document.get("eluent_systems")
        solvents = document.get("loading_solvents")
        if not isinstance(systems, list) or not systems or not isinstance(solvents, list) or not solvents:
            raise ValueError("CC_CONDITION_CATALOG_INVALID")
        self.document = document
        self.eluent_systems = {
            str(item["id"]): CcCondition(float(item["petroleum_ether"]), float(item["ethyl_acetate"]))
            for item in systems
        }
        self.loading_solvents = {str(item["id"]): str(item["model_code"]) for item in solvents}

    def resolve_eluent_system(self, key: str) -> CcCondition:
        try:
            return self.eluent_systems[key]
        except KeyError as exc:
            raise KeyError("CC_CONDITION_UNAVAILABLE") from exc

    def resolve_loading_solvent(self, key: str) -> str:
        try:
            return self.loading_solvents[key]
        except KeyError as exc:
            raise KeyError("CC_LOADING_SOLVENT_UNAVAILABLE") from exc

    @property
    def numeric_input_ranges(self) -> dict[str, dict[str, float | str]]:
        return {key: dict(value) for key, value in CC_NUMERIC_INPUT_RANGES.items()}

    def validate_numeric_inputs(self, value: Any) -> None:
        import math

        for name, bounds in CC_NUMERIC_INPUT_RANGES.items():
            try:
                number = float(getattr(value, name))
            except (AttributeError, TypeError, ValueError) as exc:
                raise ValueError(f"{name} must be within the verified CC range") from exc
            if not math.isfinite(number) or not bounds["minimum"] <= number <= bounds["maximum"]:
                raise ValueError(
                    f"{name} must be between {bounds['minimum']} and {bounds['maximum']} {bounds['unit']}"
                )

    @property
    def column_spec_ids(self) -> tuple[str, ...]:
        return tuple(str(item) for item in self.document["column_spec_ids"])
