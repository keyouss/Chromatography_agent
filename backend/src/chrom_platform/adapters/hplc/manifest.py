"""Audited facts for the v1.1 HPLC enantioseparation bundle.

The model accepts two ordered, stereospecified structures.  The hashes below
are build facts; a runtime must verify them before importing scientific code or
loading either binary asset.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


HPLC_SOURCE_ROOT = (
    r"D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\assets\chromatography\hplc_enantioseparation"
)
HPLC_PREDICT_SOURCE_PATH = HPLC_SOURCE_ROOT + r"\predict.py"
HPLC_RUNTIME_SOURCE_PATH = HPLC_SOURCE_ROOT + r"\runtime.py"
HPLC_MULTI_COLUMN_SOURCE_PATH = HPLC_SOURCE_ROOT + r"\application\Multi_column_prediction.py"
HPLC_MODEL_PATH = HPLC_SOURCE_ROOT + r"\application\GeoGNN_model.pth"
HPLC_COLUMN_DESCRIPTOR_PATH = HPLC_SOURCE_ROOT + r"\application\column_descriptor.npy"
HPLC_ASSET_MANIFEST_PATH = HPLC_SOURCE_ROOT + r"\MODEL_SHA256.txt"

HPLC_PREDICT_SOURCE_SHA256 = "5b1b17a7265313136750e80ded2d5f8db43d2c40445e47476991e2b58b36083c"
HPLC_RUNTIME_SOURCE_SHA256 = "b7cce7ab5c3901be03b8c2cd2aab487bd11d7fa68f39234a2de8e7e0f4d8b4f9"
HPLC_MULTI_COLUMN_SOURCE_SHA256 = "66f238b036b009afcbfbdada3b08bff199726b18754af67fb21073c8fa380b93"
HPLC_MODEL_SHA256 = "3ff5277fd1a9269b3166882941438f3f68d43b2a8eefedf1d4b61b2801a66bf8"
HPLC_COLUMN_DESCRIPTOR_SHA256 = "b263dd8713acc0b863b76ba295f5d5828b350323f4dc304115d196b9cc1fa969"
HPLC_ASSET_MANIFEST_SHA256 = "952ba3ed4b3e9c80e50b65366edd46d5147e64d6a8e9145f2eccb8be4bc0fdff"

HPLC_ARTIFACT_ID = "hplc-author-geognn-enantioseparation"
HPLC_MODEL_VERSION = "v1.1-author-geognn"
HPLC_LEGACY_INPUT_SCHEMA_VERSION = "1.0"
HPLC_INPUT_SCHEMA_VERSION = "1.1"
HPLC_OUTPUT_SCHEMA_VERSION = "hplc-geognn-1"
HPLC_BUSINESS_SCOPE_ID = "hplc-default"
HPLC_TOOL_ID = "single"

# Software-parity golden from the bundle's existing ``predict.py --demo``.
# This checks the audited loader/runtime wiring; it is not a measured-result
# accuracy claim or independent generalization evidence.
HPLC_GOLDEN_SMILES = ("N[C@@H](C)C(O)=O", "N[C@H](C)C(O)=O")
HPLC_GOLDEN_IPA_FRACTION = 0.1
HPLC_GOLDEN_FLOW_RATE_ML_MIN = 1.0
HPLC_GOLDEN_COLUMNS = ("ADH", "IA")
HPLC_GOLDEN_RECOMMENDED_COLUMN = "ADH"
HPLC_GOLDEN_EXPECTED = (
    ("ADH", 1.0, ((13.950490, 14.676679, 15.395338), (12.003219, 12.734612, 13.438482))),
    ("IA", 1.0, ((8.201467, 8.771506, 9.361368), (12.195879, 12.897688, 13.600186))),
)

HPLC_COLUMN_NAMES = (
    "ADH", "ODH", "IC", "IA", "OJH", "ASH", "IC3", "IE", "ID", "OD3",
    "IB", "AD", "AD3", "IF", "OD", "AS", "OJ3", "IG", "AZ", "IAH",
    "OJ", "ICH", "OZ3", "IF3", "IAU",
)

@dataclass(frozen=True)
class HplcArtifactManifest:
    """Expected files for one trusted HPLC artifact root."""

    artifact_id: str = HPLC_ARTIFACT_ID
    model_version: str = HPLC_MODEL_VERSION
    root: Path | None = None
    model_sha256: str = HPLC_MODEL_SHA256
    column_descriptor_sha256: str = HPLC_COLUMN_DESCRIPTOR_SHA256
    asset_manifest_sha256: str = HPLC_ASSET_MANIFEST_SHA256
    predict_source_sha256: str = HPLC_PREDICT_SOURCE_SHA256
    runtime_source_sha256: str = HPLC_RUNTIME_SOURCE_SHA256
    multi_column_source_sha256: str = HPLC_MULTI_COLUMN_SOURCE_SHA256

    def expected_assets(self) -> dict[str, str]:
        return {
            "model": self.model_sha256.lower(),
            "column_descriptor": self.column_descriptor_sha256.lower(),
            "asset_manifest": self.asset_manifest_sha256.lower(),
            "predict_source": self.predict_source_sha256.lower(),
            "runtime_source": self.runtime_source_sha256.lower(),
            "multi_column_source": self.multi_column_source_sha256.lower(),
        }


__all__ = [
    "HPLC_ARTIFACT_ID", "HPLC_ASSET_MANIFEST_PATH", "HPLC_ASSET_MANIFEST_SHA256",
    "HPLC_BUSINESS_SCOPE_ID", "HPLC_COLUMN_DESCRIPTOR_PATH", "HPLC_COLUMN_DESCRIPTOR_SHA256",
    "HPLC_COLUMN_NAMES", "HPLC_INPUT_SCHEMA_VERSION", "HPLC_MODEL_PATH", "HPLC_MODEL_SHA256",
    "HPLC_GOLDEN_COLUMNS", "HPLC_GOLDEN_EXPECTED", "HPLC_GOLDEN_FLOW_RATE_ML_MIN",
    "HPLC_GOLDEN_IPA_FRACTION", "HPLC_GOLDEN_RECOMMENDED_COLUMN", "HPLC_GOLDEN_SMILES",
    "HPLC_MODEL_VERSION", "HPLC_MULTI_COLUMN_SOURCE_PATH", "HPLC_MULTI_COLUMN_SOURCE_SHA256",
    "HPLC_OUTPUT_SCHEMA_VERSION", "HPLC_PREDICT_SOURCE_PATH", "HPLC_PREDICT_SOURCE_SHA256",
    "HPLC_RUNTIME_SOURCE_PATH", "HPLC_RUNTIME_SOURCE_SHA256", "HPLC_SOURCE_ROOT", "HPLC_TOOL_ID",
    "HplcArtifactManifest",
]
