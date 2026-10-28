"""TLC source and artifact facts from the scientific reuse audit.

The four model hashes are intentionally kept separately.  A single digest is
not allowed to stand in for one of the four serialized assets.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

TLC_SOURCE_PATH = (
    r"D:\Jupyter\Chromatography_agent\deliverables\chromatography_tools_windows_cc_hplc_gc_tlc\tlc_rf\predict.py"
)
TLC_SOURCE_SHA256 = "978c690b4ddc3bc12087269af0b81b24c7103080a0e66678041757b2d7d6c3ff"
TLC_MODEL_MANIFEST_PATH = (
    r"D:\Jupyter\Chromatography_agent\deliverables\chromatography_tools_windows_cc_hplc_gc_tlc\tlc_rf\MODEL_SHA256.txt"
)
TLC_ARTIFACT_MANIFEST_SHA256 = "19ebc03913693e882d3d7b37ab52c86a56a2bae6a733a272236641631b93e379"
TLC_MANIFEST_FILENAME = "MODEL_SHA256.txt"

TLC_ARTIFACT_ID = "tlc-author-ensemble-lgb-xgb-rf-ann"
TLC_MODEL_VERSION = "v1.1-author-ensemble"
TLC_SCOPE_ID = "tlc-default"
TLC_INPUT_SCHEMA_VERSION = "1.0"
TLC_OUTPUT_SCHEMA_VERSION = "1.0"

TLC_MODEL_ASSET_SHA256 = {
    "ann": "05119d440f0c224671beb4faec470700598214dbd3fdc56119214efa0da3d234",
    "lgb": "4174408259cbb1605a4b57c0cacc0026c265427f22224d887f9e17164c24b69d",
    "xgb": "ca24624473fd617c462d1ad687a9b3c082aa2110796cc5c244b265078877d396",
    "random_forest": "10dbad5457e8961418f28b42dd5c4350e55dc367574d38dba421b16a481d7559",
}


@dataclass(frozen=True)
class TlcArtifactManifest:
    """Expected files for one trusted artifact root.

    ``root`` is supplied by the trusted build/artifact registry.  It is not a
    request field and is never accepted by the prediction DTO.
    """

    artifact_id: str = TLC_ARTIFACT_ID
    model_version: str = TLC_MODEL_VERSION
    root: Path | None = None
    asset_sha256: dict[str, str] | None = None

    def expected_assets(self) -> dict[str, str]:
        return dict(self.asset_sha256 or TLC_MODEL_ASSET_SHA256)
