"""Controlled CC QGeoGNN runtime.

Scientific graph construction and bundle loading are adapted from the reviewed
SFC Predictor v1.1 CC offline entry point and its ``cc_qgeognn_runtime``
bundle/legacy modules.  The new runtime has no HTTP wrapper, checkpoint,
work-directory or client supplied path.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib
import importlib.util
import io
import json
import math
import pickle
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .condition_catalog import CcCondition, CcConditionCatalog
from .preprocessing_profiles import (
    CC_4G_CANONICAL_PROFILE,
    CC_4G_CANONICAL_PROFILE_ID,
    CC_4G_CANONICAL_PROFILE_SHA256,
    resolve_cc_preprocessing_profile,
    verify_cc_preprocessing_environment,
)


SUPPORTED_SPECS = ("4g", "8g", "25g", "40g")
BUNDLE_NAMES = {
    "4g": "qgeognn_silica_4g",
    "8g": "qgeognn_silica_8g_ft",
    "25g": "qgeognn_silica_25g_ft",
    "40g": "qgeognn_silica_40g_ft",
}
CC_MODEL_SHA256 = {
    "4g": "d6c4c113b2d02b4275b7d1cf3a572ac202d0bd8a1927d0158d0e7ab188ee4c99",
    "8g": "c8a410a9dcbf55c4d986f27d4849aa07fb4d35a3f2005324f88398cf49333105",
    "25g": "86f648fcb39ba47c70f52279b111712e45aee23203ba68bd6e812bd0cb38c0b3",
    "40g": "2afead03ebedbe0e1efd2be6664d669469d1360d2a267c1b8c02dc90a45db9b6",
}
SOLVENT_CODES = {"PE": 0.0, "EA": 1.0, "DCM": 2.0}
ELUENT_SMILES = ("CCCCCC", "CC(OCC)=O")
CONDITION_FEATURES = (
    "eluent_0", "eluent_1", "eluent_2", "eluent_3", "eluent_4", "eluent_5",
    "loading_solvent", "sample_mass", "loading_volume",
)
MORDRED_INDICES = (153, 278, 884, 885, 1273, 1594, 431, 1768, 1769, 1288, 1521)
SCHEMA_VERSION = "cc-qgeognn-1"
CC_SCOPE_ID = "cc-default"

# These are the files loaded by this adapter from the v1.1 deployment.  The
# deployment's MODEL_SHA256.txt only covers weights; keeping the trusted-root
# inventory here also protects the executable author model and preprocessing
# metadata that determine how those weights are interpreted.
TRUSTED_FILE_SHA256 = {
    "MODEL_SHA256.txt": "921ea94276a0e6b289eefe13c4694f90cbf3f2d44f00a10612776819b01ad0e5",
    "predict.py": "b27d578f964f0c29afebee907d0377a37e982bfe0cfe2b9c889e255d48cbaabd",
    "cc_qgeognn_runtime/schema.json": "18ced088c3677d5c4fba603981c77759a52386c8bf9cb54dc44f9d854b29a2db",
    "application/QGeoGNN.py": "d77638e4ad615e4b27dc3435d7f1b175ba8c4f3dc139c5043bb2f9d0d37ba896",
    "cc_qgeognn_artifacts/qgeognn_silica_4g/checksums.json": "a3e48d69733db219f53793e501065c6fd8b9e641dbf7c64974a95a79eddd0272",
    "cc_qgeognn_artifacts/qgeognn_silica_4g/metadata.json": "330bb668ba9c7934439f5e1bfe23bcf892180c269f788082ccc6ef3c6004f0af",
    "cc_qgeognn_artifacts/qgeognn_silica_4g/preprocessing.json": "017a98b926d80f4a3d9cacc6e23a3e1ae32206903d4082c39be7049db06a898d",
    "cc_qgeognn_artifacts/qgeognn_silica_4g/model_state.pt": "d6c4c113b2d02b4275b7d1cf3a572ac202d0bd8a1927d0158d0e7ab188ee4c99",
    "cc_qgeognn_artifacts/qgeognn_silica_8g_ft/checksums.json": "337bd92344b82cf6bae6ec6c0083dd2b684802428d428e26a1a5e94b11452ab1",
    "cc_qgeognn_artifacts/qgeognn_silica_8g_ft/metadata.json": "1088d2d4a5d683d18915468733bcac42f4eec5c526e377b5b19cbe97b5f5d659",
    "cc_qgeognn_artifacts/qgeognn_silica_8g_ft/preprocessing.json": "da560b112f6b771132589ffbbf145815bb1db2b028b9e5eb6409e940a189b3fd",
    "cc_qgeognn_artifacts/qgeognn_silica_8g_ft/model_state.pt": "c8a410a9dcbf55c4d986f27d4849aa07fb4d35a3f2005324f88398cf49333105",
    "cc_qgeognn_artifacts/qgeognn_silica_25g_ft/checksums.json": "369e2ea6608bd2ec9defe92303d02fd8bda4f39a1879f3aaa2b61fd6a0d7c874",
    "cc_qgeognn_artifacts/qgeognn_silica_25g_ft/metadata.json": "926eaafe022d55603338ed12c787c1e77264d0bc92d8afdaa34abb1d7ea59173",
    "cc_qgeognn_artifacts/qgeognn_silica_25g_ft/preprocessing.json": "4587f11ddb11d034faf8280ef120ba458849a3e8a5b46a53f6ad9d50c6454c35",
    "cc_qgeognn_artifacts/qgeognn_silica_25g_ft/model_state.pt": "86f648fcb39ba47c70f52279b111712e45aee23203ba68bd6e812bd0cb38c0b3",
    "cc_qgeognn_artifacts/qgeognn_silica_40g_ft/checksums.json": "d007104d35f0caf979fa50dc97a34a9d29a924e4d727474614df48bb4eb66965",
    "cc_qgeognn_artifacts/qgeognn_silica_40g_ft/metadata.json": "394ef48548a2f3dc35e214889351046b623b6932fcb4dd6b6add1249ed92e372",
    "cc_qgeognn_artifacts/qgeognn_silica_40g_ft/preprocessing.json": "1317cb3a138a55927484041ef59c6046bec2a2c295f917f6f344cca1a6dca5f0",
    "cc_qgeognn_artifacts/qgeognn_silica_40g_ft/model_state.pt": "2afead03ebedbe0e1efd2be6664d669469d1360d2a267c1b8c02dc90a45db9b6",
}


class CcAssetUnavailable(RuntimeError):
    """A CC asset cannot be executed under the trusted model contract."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class CcBundle:
    model_id: str
    spec: str
    path: Path
    metadata: dict[str, Any]
    preprocessing: dict[str, Any]
    artifact_sha256: str


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify_trusted_file(root: Path, relative: str) -> None:
    """Verify one file from the immutable deployment inventory."""
    target = (root / relative).resolve()
    if not _inside(target, root) or not target.is_file():
        raise CcAssetUnavailable("MODEL_TRUSTED_FILE_MISSING")
    expected = TRUSTED_FILE_SHA256.get(relative)
    if expected is None or _sha256(target) != expected:
        raise CcAssetUnavailable("MODEL_TRUSTED_FILE_HASH_MISMATCH")


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CcAssetUnavailable("MODEL_METADATA_INVALID") from exc
    if not isinstance(value, dict):
        raise CcAssetUnavailable("MODEL_METADATA_INVALID")
    return value


def _spec(value: str) -> str:
    raw = str(value).strip().lower()
    for key, bundle_name in BUNDLE_NAMES.items():
        if raw == bundle_name:
            return key
    normalized = raw.replace("silica-cs", "").replace("silica", "")
    normalized = normalized.replace("+", "").replace(" ", "").replace("-", "")
    if normalized == "4g4g":
        return "8g"
    for item in SUPPORTED_SPECS:
        if normalized == item:
            return item
    raise CcAssetUnavailable("COLUMN_SPEC_UNSUPPORTED")


def derive_time_seconds(*, volume_ml: float, flow_rate_ml_min: float) -> float:
    if volume_ml <= 0 or flow_rate_ml_min <= 0:
        raise ValueError("volume_ml and flow_rate_ml_min must be > 0")
    return float(volume_ml) * 60.0 / float(flow_rate_ml_min)


class CcModelRegistry:
    """Read-only registry over a trusted CC deployment root."""

    def __init__(self, model_root: str | Path) -> None:
        from chrom_platform.structure_core import service as structure_service

        if not verify_cc_preprocessing_environment(
            runtime_source=Path(__file__),
            canonicalizer_source=Path(structure_service.__file__),
        ):
            raise CcAssetUnavailable("MODEL_PREPROCESSING_PROFILE_UNAVAILABLE")
        self.model_root = Path(model_root).resolve()
        if not self.model_root.is_dir():
            raise CcAssetUnavailable("MODEL_ROOT_MISSING")
        self.artifacts_root = (self.model_root / "cc_qgeognn_artifacts").resolve()
        self.source_runtime_root = (self.model_root / "cc_qgeognn_runtime").resolve()
        self.application_root = (self.model_root / "application").resolve()
        if not _inside(self.artifacts_root, self.model_root) or not self.artifacts_root.is_dir():
            raise CcAssetUnavailable("MODEL_ARTIFACT_ROOT_MISSING")
        if not _inside(self.source_runtime_root, self.model_root) or not self.source_runtime_root.is_dir():
            raise CcAssetUnavailable("MODEL_RUNTIME_ROOT_MISSING")
        if not _inside(self.application_root, self.model_root) or not self.application_root.is_dir():
            raise CcAssetUnavailable("MODEL_APPLICATION_ROOT_MISSING")
        self.schema_path = self.source_runtime_root / "schema.json"
        self.checksum_path = self.model_root / "MODEL_SHA256.txt"
        for relative in TRUSTED_FILE_SHA256:
            _verify_trusted_file(self.model_root, relative)
        self.schema = _json(self.schema_path)
        self._checksums = self._read_checksums()
        self._validate_schema()

    def _read_checksums(self) -> dict[str, str]:
        try:
            lines = self.checksum_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise CcAssetUnavailable("MODEL_CHECKSUMS_MISSING") from exc
        result: dict[str, str] = {}
        for line in lines:
            parts = line.split()
            if len(parts) != 2 or len(parts[0]) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in parts[0]):
                raise CcAssetUnavailable("MODEL_CHECKSUMS_INVALID")
            digest, relative = parts[0].lower(), Path(parts[1])
            target = (self.model_root / relative).resolve()
            if not _inside(target, self.model_root):
                raise CcAssetUnavailable("MODEL_CHECKSUM_PATH_OUTSIDE_ROOT")
            normalized = str(relative).replace("\\", "/")
            if normalized in result:
                raise CcAssetUnavailable("MODEL_CHECKSUMS_INVALID")
            result[normalized] = digest
        if set(result) != {
            f"cc_qgeognn_artifacts/{BUNDLE_NAMES[item]}/model_state.pt" for item in SUPPORTED_SPECS
        }:
            raise CcAssetUnavailable("MODEL_CHECKSUMS_INCOMPLETE")
        if any(result[key] != TRUSTED_FILE_SHA256[key] for key in result):
            raise CcAssetUnavailable("MODEL_CHECKSUMS_UNTRUSTED")
        return result

    def _validate_schema(self) -> None:
        if self.schema.get("schema_version") != SCHEMA_VERSION:
            raise CcAssetUnavailable("MODEL_SCHEMA_VERSION_MISMATCH")
        target = self.schema.get("target")
        if not isinstance(target, dict) or target.get("outputs") != ["V1", "V2"] or target.get("quantiles") != [0.1, 0.5, 0.9]:
            raise CcAssetUnavailable("MODEL_SCHEMA_TARGET_INVALID")
        if self.schema.get("condition_features") != list(CONDITION_FEATURES):
            raise CcAssetUnavailable("MODEL_SCHEMA_CONDITION_INVALID")
        if self.schema.get("model_name") != "QGeoGNN":
            raise CcAssetUnavailable("MODEL_SCHEMA_MODEL_INVALID")

    @property
    def bundles(self) -> tuple[CcBundle, ...]:
        return tuple(self.bundle(item) for item in SUPPORTED_SPECS)

    def bundle(self, model_id_or_spec: str) -> CcBundle:
        # Recheck on every resolution to avoid a post-startup replacement of a
        # file that will be loaded by the worker.
        for relative in TRUSTED_FILE_SHA256:
            _verify_trusted_file(self.model_root, relative)
        spec = str(model_id_or_spec).lower()
        if spec in BUNDLE_NAMES:
            model_id = BUNDLE_NAMES[spec]
        else:
            model_id = str(model_id_or_spec)
            matching = [key for key, value in BUNDLE_NAMES.items() if value == model_id]
            if not matching:
                raise CcAssetUnavailable("MODEL_BUNDLE_UNSUPPORTED")
            spec = matching[0]
        path = (self.artifacts_root / model_id).resolve()
        if not _inside(path, self.artifacts_root) or not path.is_dir():
            raise CcAssetUnavailable("MODEL_BUNDLE_MISSING")
        metadata = _json(path / "metadata.json")
        preprocessing = _json(path / "preprocessing.json")
        preprocessing_sha256 = _sha256(path / "preprocessing.json")
        checksums = _json(path / "checksums.json")
        state = (path / "model_state.pt").resolve()
        if not state.is_file() or not _inside(state, self.model_root):
            raise CcAssetUnavailable("MODEL_WEIGHTS_MISSING")
        digest = _sha256(state)
        relative = f"cc_qgeognn_artifacts/{model_id}/model_state.pt"
        if digest != self._checksums.get(relative) or digest != str(checksums.get("model_state.pt", "")).lower():
            raise CcAssetUnavailable("MODEL_ARTIFACT_HASH_MISMATCH")
        if metadata.get("name") != model_id or metadata.get("schema_version") != SCHEMA_VERSION:
            raise CcAssetUnavailable("MODEL_METADATA_MISMATCH")
        if _spec(str(metadata.get("column_spec", ""))) != spec:
            raise CcAssetUnavailable("MODEL_COLUMN_SPEC_METADATA_MISMATCH")
        if preprocessing.get("schema_version") != SCHEMA_VERSION or preprocessing.get("condition_features") != list(CONDITION_FEATURES):
            raise CcAssetUnavailable("MODEL_PREPROCESSING_MISMATCH")
        if model_id == BUNDLE_NAMES["4g"] and preprocessing_sha256 != str(
            CC_4G_CANONICAL_PROFILE["model"]["scaler_file_sha256"]
        ):
            raise CcAssetUnavailable("MODEL_PREPROCESSING_PROFILE_MISMATCH")
        indices = preprocessing.get("mordred_indices")
        if indices != list(MORDRED_INDICES):
            raise CcAssetUnavailable("MODEL_PREPROCESSING_INDICES_MISMATCH")
        if not isinstance(metadata.get("model_config"), dict):
            raise CcAssetUnavailable("MODEL_CONFIG_MISSING")
        self._validate_preprocessing(preprocessing)
        return CcBundle(model_id, spec, path, metadata, preprocessing, digest)

    @staticmethod
    def _validate_preprocessing(preprocessing: Mapping[str, Any]) -> None:
        """Reject shape-changing or non-finite preprocessing metadata."""
        for key, expected_size in (("condition_scaler", len(CONDITION_FEATURES)), ("descriptor_scaler", 5 + len(MORDRED_INDICES))):
            scaler = preprocessing.get(key)
            if not isinstance(scaler, Mapping):
                raise CcAssetUnavailable("MODEL_PREPROCESSING_SCALER_INVALID")
            minimum, maximum = scaler.get("minimum"), scaler.get("maximum")
            if not isinstance(minimum, list) or not isinstance(maximum, list) or len(minimum) != expected_size or len(maximum) != expected_size:
                raise CcAssetUnavailable("MODEL_PREPROCESSING_SCALER_SHAPE_INVALID")
            for low, high in zip(minimum, maximum):
                try:
                    low_number, high_number = float(low), float(high)
                except (TypeError, ValueError) as exc:
                    raise CcAssetUnavailable("MODEL_PREPROCESSING_SCALER_INVALID") from exc
                if not math.isfinite(low_number) or not math.isfinite(high_number) or high_number < low_number:
                    raise CcAssetUnavailable("MODEL_PREPROCESSING_SCALER_INVALID")


def _number(value: Any, name: str, *, positive: bool = True) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    import math
    if not math.isfinite(number) or (positive and number <= 0):
        raise ValueError(f"{name} must be {'greater than zero' if positive else 'finite'}")
    return number


def _condition(request: Any, condition: CcCondition, loading_solvent: str) -> Any:
    try:
        import numpy as np
        from rdkit import Chem
        from rdkit.Chem import Descriptors
    except ImportError as exc:
        raise CcAssetUnavailable("SCIENTIFIC_DEPENDENCIES_UNAVAILABLE") from exc
    pe = _number(condition.petroleum_ether, "petroleum_ether", positive=False)
    ea = _number(condition.ethyl_acetate, "ethyl_acetate", positive=False)
    if pe < 0 or ea < 0 or pe + ea <= 0:
        raise ValueError("PE/EA proportions must be non-negative and sum to more than zero")
    if loading_solvent not in SOLVENT_CODES:
        raise ValueError("loading_solvent_id resolves to an unsupported model code")
    sample_mass = _number(request.input.sample_mass_mg, "sample_mass_mg")
    loading_volume = _number(request.input.loading_solvent_volume_ul, "loading_solvent_volume_ul")
    desc = np.zeros(6, dtype=np.float32)
    for fraction, smiles in zip((pe / (pe + ea), ea / (pe + ea)), ELUENT_SMILES):
        mol = Chem.MolFromSmiles(smiles)
        desc += fraction * np.asarray(
            [Descriptors.ExactMolWt(mol), Chem.rdMolDescriptors.CalcTPSA(mol),
             Descriptors.NumRotatableBonds(mol), Descriptors.NumHDonors(mol),
             Descriptors.NumHAcceptors(mol), Descriptors.MolLogP(mol)],
            dtype=np.float32,
        )
    return np.concatenate((desc, [SOLVENT_CODES[loading_solvent], sample_mass, loading_volume])).astype(np.float32)


def _author_module(application_root: Path, device: Any) -> Any:
    """Load only the audited author model definitions from a trusted root."""
    try:
        import torch
    except ImportError as exc:
        raise CcAssetUnavailable("TORCH_UNAVAILABLE") from exc
    qgeo_path = (application_root / "QGeoGNN.py").resolve()
    if not _inside(qgeo_path, application_root) or not qgeo_path.is_file():
        raise CcAssetUnavailable("MODEL_AUTHOR_RUNTIME_MISSING")
    # QGeoGNN imports unrelated ``utils`` helpers; the reviewed legacy shim
    # intentionally supplies an empty module because graph/model primitives do
    # not use those helpers during inference.
    qgeo_digest = _sha256(qgeo_path)
    if qgeo_digest != TRUSTED_FILE_SHA256["application/QGeoGNN.py"]:
        raise CcAssetUnavailable("MODEL_AUTHOR_RUNTIME_HASH_MISMATCH")
    # The author file contains ``from utils import *`` although inference does
    # not use that legacy helper.  Never reuse a process-global module supplied
    # by a caller; temporarily install the audited empty shim instead.
    previous_utils = sys.modules.get("utils")
    sys.modules["utils"] = types.ModuleType("utils")
    module_name = f"_chrom_platform_cc_qgeognn_{qgeo_digest[:16]}"
    try:
        module = sys.modules.get(module_name)
        if module is None:
            spec = importlib.util.spec_from_file_location(module_name, qgeo_path)
            if spec is None or spec.loader is None:
                raise CcAssetUnavailable("MODEL_AUTHOR_RUNTIME_INVALID")
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                spec.loader.exec_module(module)
    finally:
        if previous_utils is None:
            sys.modules.pop("utils", None)
        else:
            sys.modules["utils"] = previous_utils
    module.device = device
    module.Use_geometry_enhanced = True
    module.Use_column_info = False
    module.bond_float_names = ["bond_length", "prop", "e", "m", "V_e"]
    return module


class CcRuntime:
    """Execute a verified bundle using trusted structures and conditions."""

    def __init__(
        self,
        *,
        registry: CcModelRegistry,
        condition_catalog: CcConditionCatalog,
    ) -> None:
        self.registry = registry
        self.condition_catalog = condition_catalog
        self._models: dict[str, Any] = {}
        self._bound_model_id: str | None = None
        self._bound_model_version: str | None = None
        self._bound_artifact_sha256: str | None = None
        self.business_scope_id = CC_SCOPE_ID

    def bind_verified_handle(self, handle: Any) -> None:
        """Bind this runtime to the one model authorized by central assembly."""
        if (
            getattr(handle, "adapter_id", None) != "cc"
            or getattr(handle, "input_schema_version", None) != "1.0"
            or getattr(handle, "output_schema_version", None) != SCHEMA_VERSION
            or handle.business_scope_id != self.business_scope_id
            or getattr(handle, "model_id", None) != BUNDLE_NAMES["4g"]
            or getattr(handle, "artifact_sha256", None) != CC_MODEL_SHA256["4g"]
            or getattr(handle, "model_version", None) != SCHEMA_VERSION
            or getattr(handle, "preprocessing_profile_id", None) != CC_4G_CANONICAL_PROFILE_ID
            or getattr(handle, "preprocessing_profile_sha256", None) != CC_4G_CANONICAL_PROFILE_SHA256
            or resolve_cc_preprocessing_profile(
                getattr(handle, "preprocessing_profile_id", None),
                getattr(handle, "preprocessing_profile_sha256", None),
            ) is None
        ):
            raise CcAssetUnavailable("MODEL_VERIFICATION_MISMATCH")
        self._bound_model_id = handle.model_id
        self._bound_model_version = handle.model_version
        self._bound_artifact_sha256 = handle.artifact_sha256

    @classmethod
    def from_registered_artifact(
        cls,
        *,
        model_root: str | Path,
        condition_catalog: CcConditionCatalog,
    ) -> "CcRuntime":
        return cls(registry=CcModelRegistry(model_root), condition_catalog=condition_catalog)

    def _resolve_condition(self, key: str) -> CcCondition:
        try:
            return self.condition_catalog.resolve_eluent_system(key)
        except KeyError as exc:
            raise CcAssetUnavailable("CC_CONDITION_UNAVAILABLE") from exc

    def load_model(self, model_id_or_spec: str) -> tuple[Any, CcBundle]:
        bundle = self.registry.bundle(model_id_or_spec)
        if self._bound_model_id is not None and bundle.model_id != self._bound_model_id:
            raise CcAssetUnavailable("MODEL_SELECTION_NOT_VERIFIED")
        if bundle.model_id in self._models:
            return self._models[bundle.model_id], bundle
        try:
            import numpy  # noqa: F401
            import torch
            from torch_geometric.data import Batch, Data  # noqa: F401
        except ImportError as exc:
            raise CcAssetUnavailable("SCIENTIFIC_DEPENDENCIES_UNAVAILABLE") from exc
        author = _author_module(self.registry.application_root, torch.device("cpu"))
        config = {
            "num_tasks": 6, "num_layers": 5, "emb_dim": 128,
            "drop_ratio": 0.0, "graph_pooling": "sum", "descriptor_dim": 17,
        }
        config.update(bundle.metadata["model_config"])
        model = author.GINGraphPooling(**config).to(torch.device("cpu"))
        try:
            state = torch.load(bundle.path / "model_state.pt", map_location="cpu", weights_only=True)
        except (TypeError, RuntimeError, ValueError, EOFError, pickle.UnpicklingError) as exc:
            raise CcAssetUnavailable("WEIGHTS_ONLY_UNSUPPORTED") from exc
        if not isinstance(state, Mapping):
            raise CcAssetUnavailable("MODEL_STATE_INVALID")
        try:
            incompatible = model.load_state_dict(state, strict=False)
        except (RuntimeError, TypeError, ValueError) as exc:
            raise CcAssetUnavailable("MODEL_STATE_API_MISMATCH") from exc
        illegal_missing = [key for key in incompatible.missing_keys if not key.endswith(".gamma")]
        if illegal_missing or incompatible.unexpected_keys:
            raise CcAssetUnavailable("MODEL_STATE_API_MISMATCH")
        if not callable(getattr(model, "forward", None)):
            raise CcAssetUnavailable("MODEL_API_UNSUPPORTED")
        model.eval()
        self._models[bundle.model_id] = (model, author)
        return self._models[bundle.model_id], bundle

    @staticmethod
    def _graph(smiles: str, author: Any, indices: Sequence[int]) -> tuple[dict[str, Any], Any]:
        try:
            import numpy as np
            from rdkit import Chem
            from rdkit.Chem import AllChem, Descriptors
        except ImportError as exc:
            raise CcAssetUnavailable("SCIENTIFIC_DEPENDENCIES_UNAVAILABLE") from exc
        if not isinstance(smiles, str) or not smiles.strip():
            raise ValueError("normalized SMILES must be a non-empty string")
        mol = Chem.MolFromSmiles(smiles.strip())
        if mol is None:
            raise ValueError("Invalid normalized SMILES")
        mol = Chem.AddHs(mol)
        if AllChem.EmbedMolecule(
            mol,
            maxAttempts=0,
            randomSeed=525,
            clearConfs=True,
            useRandomCoords=False,
            boxSizeMult=2.0,
            randNegEig=True,
            numZeroFail=1,
            forceTol=0.001,
            ignoreSmoothingFailures=False,
            enforceChirality=True,
            useExpTorsionAnglePrefs=True,
            useBasicKnowledge=True,
            printExpTorsionAngles=False,
            useSmallRingTorsions=False,
            useMacrocycleTorsions=True,
            ETversion=2,
            useMacrocycle14config=True,
        ) != 0:
            raise ValueError("Unable to generate a 3D conformer for this SMILES")
        # Preserve legacy behavior while naming every RDKit default in the profile.
        AllChem.MMFFOptimizeMolecule(
            mol,
            mmffVariant="MMFF94",
            maxIters=200,
            nonBondedThresh=100.0,
            confId=-1,
            ignoreInterfragInteractions=True,
        )
        mol = Chem.RemoveHs(mol)
        graph = author.mol_to_geognn_graph_data_MMFF3d(mol)
        mordred = np.asarray(author.mord(mol), dtype=np.float32)
        if mordred.ndim != 1 or len(indices) != len(MORDRED_INDICES) or any(index < 0 or index >= mordred.size for index in indices):
            raise CcAssetUnavailable("MODEL_DESCRIPTOR_SCHEMA_INVALID")
        descriptor = np.concatenate(
            (np.asarray([Descriptors.ExactMolWt(mol), Descriptors.NumRotatableBonds(mol),
                         Descriptors.NumHDonors(mol), Descriptors.NumHAcceptors(mol),
                         Descriptors.MolLogP(mol)], dtype=np.float32), mordred[list(indices)])
        ).astype(np.float32)
        if not np.isfinite(descriptor).all():
            raise ValueError("Molecular descriptors contain non-finite values")
        if descriptor.shape != (5 + len(MORDRED_INDICES),):
            raise CcAssetUnavailable("MODEL_DESCRIPTOR_SHAPE_INVALID")
        return graph, descriptor

    @staticmethod
    def _scale(values: Any, scaler: Mapping[str, Any]) -> Any:
        import numpy as np
        lo = np.asarray(scaler["minimum"], dtype=np.float32)
        hi = np.asarray(scaler["maximum"], dtype=np.float32)
        array = np.asarray(values, dtype=np.float32)
        if array.ndim != 1 or lo.ndim != 1 or hi.ndim != 1 or array.shape != lo.shape or lo.shape != hi.shape:
            raise CcAssetUnavailable("MODEL_PREPROCESSING_SHAPE_MISMATCH")
        if not np.isfinite(array).all() or not np.isfinite(lo).all() or not np.isfinite(hi).all() or (hi < lo).any():
            raise CcAssetUnavailable("MODEL_PREPROCESSING_VALUES_INVALID")
        scaled = (array - lo) / np.maximum(hi - lo, 1e-8)
        if not np.isfinite(scaled).all():
            raise CcAssetUnavailable("MODEL_PREPROCESSING_OUTPUT_INVALID")
        return scaled

    def _predict_one(self, model: Any, author: Any, bundle: CcBundle, smiles: str, condition: Any, flow: float) -> dict[str, Any]:
        try:
            import numpy as np
            import torch
            from torch_geometric.data import Batch, Data
        except ImportError as exc:
            raise CcAssetUnavailable("SCIENTIFIC_DEPENDENCIES_UNAVAILABLE") from exc
        graph, molecule = self._graph(smiles, author, bundle.preprocessing["mordred_indices"])
        condition_scaled = self._scale(condition, bundle.preprocessing["condition_scaler"])
        molecule_scaled = self._scale(molecule, bundle.preprocessing["descriptor_scaler"])
        atom = torch.as_tensor(np.stack([graph[name] for name in author.atom_id_names], axis=1), dtype=torch.long)
        bond = np.stack([graph[name] for name in author.bond_id_names], axis=1)
        floats = np.concatenate((graph["bond_length"].reshape(-1, 1), np.repeat(condition_scaled[None, :], len(bond), axis=0)), axis=1)
        atom_graph = Data(
            x=atom,
            edge_index=torch.as_tensor(graph["edges"].T, dtype=torch.long),
            edge_attr=torch.as_tensor(np.concatenate((bond, floats), axis=1), dtype=torch.float32),
        )
        angle = np.concatenate((graph["bond_angle"].reshape(-1, 1), np.repeat(molecule_scaled[None, :], len(graph["bond_angle"]), axis=0)), axis=1)
        angle_graph = Data(
            edge_index=torch.as_tensor(graph["BondAngleGraph_edges"].T, dtype=torch.long),
            edge_attr=torch.as_tensor(angle, dtype=torch.float32),
        )
        with torch.no_grad():
            output = model(Batch.from_data_list([atom_graph]), Batch.from_data_list([angle_graph]))
        if not isinstance(output, (tuple, list)) or len(output) < 1:
            raise CcAssetUnavailable("MODEL_OUTPUT_SHAPE_INVALID")
        tensor = output[0]
        if not hasattr(tensor, "detach") or tuple(tensor.shape) != (1, 6):
            raise CcAssetUnavailable("MODEL_OUTPUT_SHAPE_INVALID")
        values = np.asarray(tensor.detach().cpu().numpy(), dtype=np.float64)
        if values.shape != (1, 6) or not np.isfinite(values).all():
            raise CcAssetUnavailable("MODEL_OUTPUT_INVALID")
        values = values.reshape(2, 3)
        if (values <= 0).any() or (np.diff(values, axis=1) < 0).any():
            raise CcAssetUnavailable("MODEL_OUTPUT_INVALID")
        windows = []
        for label, triplet in zip(("V1", "V2"), values):
            windows.append({
                "name": label,
                "volume_ml": {"q10": float(triplet[0]), "q50": float(triplet[1]), "q90": float(triplet[2])},
                "time_seconds": {"q10": derive_time_seconds(volume_ml=float(triplet[0]), flow_rate_ml_min=flow),
                                  "q50": derive_time_seconds(volume_ml=float(triplet[1]), flow_rate_ml_min=flow),
                                  "q90": derive_time_seconds(volume_ml=float(triplet[2]), flow_rate_ml_min=flow)},
            })
        return {"smiles": smiles, "predicted_elution_windows": windows}

    def predict(self, request: Any, normalized_smiles: Sequence[str]) -> dict[str, Any]:
        if len(normalized_smiles) != 2 or any(not isinstance(item, str) or not item.strip() for item in normalized_smiles):
            raise ValueError("CC requires exactly two normalized SMILES")
        self.condition_catalog.validate_numeric_inputs(request.input)
        selection = request.model_selection
        if selection.mode == "exact":
            model_id = selection.model_id
            if (
                model_id != BUNDLE_NAMES["4g"]
                or selection.model_version != SCHEMA_VERSION
                or resolve_cc_preprocessing_profile(
                    selection.preprocessing_profile_id,
                    selection.preprocessing_profile_sha256,
                ) is None
                or (self._bound_model_id is not None and model_id != self._bound_model_id)
                or (self._bound_model_version is not None and selection.model_version != self._bound_model_version)
            ):
                raise CcAssetUnavailable("MODEL_SELECTION_NOT_VERIFIED")
        else:
            raise CcAssetUnavailable("MODEL_SELECTION_NOT_VERIFIED")
        requested_spec = _spec(request.input.column_spec_id)
        if requested_spec != "4g":
            raise CcAssetUnavailable("MODEL_COLUMN_SPEC_NOT_VERIFIED")
        bundle_spec = _spec(model_id)
        if requested_spec != bundle_spec:
            raise CcAssetUnavailable("MODEL_COLUMN_SPEC_MISMATCH")
        (model, author), bundle = self.load_model(model_id)
        if self._bound_artifact_sha256 is not None and bundle.artifact_sha256 != self._bound_artifact_sha256:
            raise CcAssetUnavailable("MODEL_ARTIFACT_BINDING_INVALID")
        condition = self._resolve_condition(request.input.eluent_system_id)
        try:
            loading_solvent = self.condition_catalog.resolve_loading_solvent(request.input.loading_solvent_id)
        except KeyError as exc:
            raise CcAssetUnavailable("CC_LOADING_SOLVENT_UNAVAILABLE") from exc
        flow = _number(request.input.flow_rate_ml_min, "flow_rate_ml_min")
        feature_condition = _condition(request, condition, loading_solvent)
        per_structure = [self._predict_one(model, author, bundle, smiles, feature_condition, flow) for smiles in normalized_smiles]
        return {
            "success": True,
            "model": {"name": bundle.metadata["name"], "column_spec": bundle.metadata["column_spec"], "device": "cpu"},
            "normalized_input": {
                "smiles": list(normalized_smiles), "column_spec": requested_spec,
                "flow_rate_ml_min": flow, "mobile_phase": {
                    "petroleum_ether": float(condition.petroleum_ether),
                    "ethyl_acetate": float(condition.ethyl_acetate),
                }, "loading_solvent": loading_solvent,
                "loading_solvent_id": request.input.loading_solvent_id,
                "sample_mass_mg": float(request.input.sample_mass_mg),
                "loading_solvent_volume_ul": float(request.input.loading_solvent_volume_ul),
            },
            "predicted_elution_windows": per_structure,
            "artifact_sha256": bundle.artifact_sha256,
            "warning": "该模型结果仅用于实验规划，须使用匹配柱规格并通过实验确认；当前没有独立标签集泛化验收证据。",
        }


__all__ = [
    "BUNDLE_NAMES", "CC_MODEL_SHA256", "CcAssetUnavailable", "CcBundle", "CcCondition",
    "CcModelRegistry", "CcRuntime", "CONDITION_FEATURES", "derive_time_seconds",
]
