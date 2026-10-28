"""Immutable facts for the audited GC v1.1 scientific bundle."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

GC_SOURCE_ROOT = r"D:\Jupyter\SFC\deployments\sfc-predictor-v1.1\assets\chromatography\gc_retention_time"
GC_PREDICT_SOURCE_PATH = GC_SOURCE_ROOT + r"\predict.py"
GC_MODEL_PATH = GC_SOURCE_ROOT + r"\Outcome\GNN_seq240408_250c\model_save_600.pth"
GC_ASSET_MANIFEST_PATH = GC_SOURCE_ROOT + r"\MODEL_SHA256.txt"
GC_ARTIFACT_ID = "gc-gnn-seq240408-250c"
GC_BUSINESS_SCOPE_ID = "gc-default"
GC_SCOPE_ID = GC_BUSINESS_SCOPE_ID
GC_TOOL_ID = "single"
GC_AUTHOR_INPUT_SCHEMA_VERSION = "1.0"
GC_INPUT_SCHEMA_VERSION = "1.1"
GC_OUTPUT_SCHEMA_VERSION = "gc-retention-time-1"
GC_MODEL_VERSION = "gc-gnn-seq240408-250c-600-prep-rdkit-seed42-v1"
GC_MODEL_SHA256 = "0433756eab33689981c6d881e3b76c34a8043eb2f47a6adf6ed4a65dd9d5758c"
GC_PREPROCESSING_PROTOCOL_ID = "gc-preprocessing-rdkit-seed42-v1"
GC_PREPROCESSING_PROTOCOL_QA_APPROVED = False
GC_RDKIT_EMBED_PARAMETERS = {
    "numConfs": 10,
    "maxAttempts": 0,
    "randomSeed": 42,
    "clearConfs": True,
    "useRandomCoords": False,
    "boxSizeMult": 2.0,
    "randNegEig": True,
    "numZeroFail": 1,
    "pruneRmsThresh": -1.0,
    "coordMap": {},
    "forceTol": 0.001,
    "ignoreSmoothingFailures": False,
    "enforceChirality": True,
    "numThreads": 1,
    "useExpTorsionAnglePrefs": True,
    "useBasicKnowledge": True,
    "printExpTorsionAngles": False,
    "useSmallRingTorsions": False,
    "useMacrocycleTorsions": True,
    "ETversion": 2,
    "useMacrocycle14config": True,
}
GC_RDKIT_MMFF_PARAMETERS = {
    "numThreads": 1,
    "maxIters": 200,
    "mmffVariant": "MMFF94",
    "nonBondedThresh": 100.0,
    "ignoreInterfragInteractions": True,
}

# Hash every source file imported by the runtime.  This inventory is checked
# before importing author code or loading the weights.
GC_TRUSTED_FILE_SHA256 = {
    "MODEL_SHA256.txt": "ee2bdf82e71d7444f4466e7567a10c7cef47f672b64e4bec36dbc2553ea72395",
    "predict.py": "96725a4c2b1010caceae2ac89229a3f816fe59dc0b7c5c6f63bdc1baef12cc4e",
    "Model/compound_tools.py": "50e62add29877733a8871967471149f3d7401e0712e3dfdd1a1879de04463af3",
    "Model/config.py": "7e22519f7fc62fa125b5821c00cad7316f0cbfd54e3573ec9245dc8ba072480d",
    "Model/data_process.py": "2af7dca63f5b4ddb4cf324fc62fb53e738c27db8733d00e59169fc3d831c06c5",
    "Model/GIN.py": "412381895c0db9568faa78e12945d426b8f32e7e1aca3b31788e871fd1d368a6",
    "Model/model.py": "642d9a3376cced4e615b0eb07f81e7cf2c157cb401cd1f873b2e32ef8ab3bbd2",
    "Outcome/GNN_seq240408_250c/model_save_600.pth": GC_MODEL_SHA256,
}


@dataclass(frozen=True)
class GcArtifactManifest:
    root: Path | None = None
    artifact_id: str = GC_ARTIFACT_ID
    model_version: str = GC_MODEL_VERSION
    model_sha256: str = GC_MODEL_SHA256

    def expected_files(self) -> dict[str, str]:
        # The digest inventory is an adapter constant.  Callers cannot widen
        # the trust root by injecting a replacement hash map.
        return dict(GC_TRUSTED_FILE_SHA256)


__all__ = [
    "GC_AUTHOR_INPUT_SCHEMA_VERSION",
    "GC_ARTIFACT_ID",
    "GC_ASSET_MANIFEST_PATH",
    "GC_BUSINESS_SCOPE_ID",
    "GC_INPUT_SCHEMA_VERSION",
    "GC_MODEL_PATH",
    "GC_MODEL_SHA256",
    "GC_MODEL_VERSION",
    "GC_PREPROCESSING_PROTOCOL_ID",
    "GC_PREPROCESSING_PROTOCOL_QA_APPROVED",
    "GC_RDKIT_EMBED_PARAMETERS",
    "GC_RDKIT_MMFF_PARAMETERS",
    "GC_OUTPUT_SCHEMA_VERSION",
    "GC_PREDICT_SOURCE_PATH",
    "GC_SCOPE_ID",
    "GC_SOURCE_ROOT",
    "GC_TOOL_ID",
    "GC_TRUSTED_FILE_SHA256",
    "GcArtifactManifest",
]
