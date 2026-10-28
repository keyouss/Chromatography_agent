"""Controlled TLC model runtime.

Scientific portions are adapted from the audited v1.1 offline entrypoint:
``TLC_SOURCE_PATH`` / ``TLC_SOURCE_SHA256`` in :mod:`manifest`.

The adapter deliberately does not import the old entrypoint.  It preserves the
audited 167 MACCS + 6 solvent descriptors + 6 molecule descriptors feature
order and the LGB/XGB/RF/ANN ensemble weights while replacing source-relative
paths with a trusted, registered artifact root.
"""

from __future__ import annotations

import hashlib
import importlib.util
import gzip
import math
import pickle
import re
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from chrom_platform.modules.prediction.adapters import VerifiedModelHandle

from .manifest import (
    TLC_ARTIFACT_ID,
    TLC_ARTIFACT_MANIFEST_SHA256,
    TLC_MANIFEST_FILENAME,
    TLC_MODEL_ASSET_SHA256,
    TLC_MODEL_VERSION,
    TLC_SCOPE_ID,
    TlcArtifactManifest,
)

SOLVENT_ORDER = (
    "hexane",
    "ethyl_acetate",
    "dichloromethane",
    "methanol",
    "diethyl_ether",
)
# The model's legacy five-position vector is a named mapping, never a
# positional interpretation of arbitrary client input.
SOLVENT_TO_INDEX = {name: index for index, name in enumerate(SOLVENT_ORDER)}
# Explicitly no petroleum_ether alias: the audited model declares hexane.
SOLVENT_SMILES = ("CCCCCC", "CC(OCC)=O", "C(Cl)Cl", "CO", "CCOCC")
ALLOWED_SYSTEMS = (
    frozenset(("hexane", "ethyl_acetate")),
    frozenset(("dichloromethane", "methanol")),
    frozenset(("hexane", "diethyl_ether")),
)
ENSEMBLE_WEIGHTS = {"lgb": 0.2, "xgb": 0.2, "random_forest": 0.2, "ann": 0.4}
MODEL_FILENAMES = {
    "ann": Path("model_LGB") / "model_ANN (3).pkl.gz",
    "lgb": Path("model_LGB") / "model_LGB (3).pkl.gz",
    "xgb": Path("model_LGB") / "model_XGB (3).pkl.gz",
    "random_forest": Path("model_LGB") / "model_RF (3).pkl.gz",
}


class TlcArtifactUnavailable(RuntimeError):
    """Raised when a controlled model cannot be proven executable."""


@dataclass(frozen=True)
class TlcRuntimeStatus:
    status: str
    reason: str | None = None
    checked_assets: bool = False
    dependencies_available: bool = False
    feature_schema_verified: bool = False


@dataclass(frozen=True)
class TlcModelRoot:
    """A model root that has been proven to be inside a registry root."""

    path: Path
    registry_root: Path

    @classmethod
    def from_registered_path(cls, *, registry_root: str | Path, model_root: str | Path) -> "TlcModelRoot":
        registry = Path(registry_root).expanduser().resolve(strict=True)
        root = Path(model_root).expanduser().resolve(strict=True)
        if not registry.is_dir():
            raise ValueError("TLC registry root must be a directory")
        try:
            root.relative_to(registry)
        except ValueError as exc:
            raise ValueError("TLC model root must be inside the trusted registry root") from exc
        if not root.is_dir():
            raise ValueError("TLC model root must be a directory")
        return cls(path=root, registry_root=registry)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_manifest_assets(path: Path) -> dict[str, str]:
    """Parse the signed manifest without accepting ambiguous or duplicate rows."""

    declared: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise TlcArtifactUnavailable("TLC model manifest cannot be read") from exc
    for line_number, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        fields = stripped.split(None, 1)
        if len(fields) != 2 or not re.fullmatch(r"[0-9a-fA-F]{64}", fields[0]):
            raise TlcArtifactUnavailable(f"invalid TLC model manifest row: {line_number}")
        digest, relative_name = fields
        relative_name = relative_name.replace("\\", "/")
        if relative_name in declared:
            raise TlcArtifactUnavailable(f"duplicate TLC model manifest row: {relative_name}")
        declared[relative_name] = digest.lower()
    return declared


def _has_dependencies() -> tuple[bool, str | None]:
    names = ("numpy", "torch", "rdkit", "lightgbm", "xgboost", "sklearn", "joblib")
    missing = [name for name in names if importlib.util.find_spec(name) is None]
    return (not missing, None if not missing else "missing dependencies: " + ", ".join(missing))


def _molecule(smiles: str) -> Any:
    from rdkit import Chem

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ValueError("invalid SMILES")
    return mol


def _basic_descriptors(smiles: str):
    import numpy as np
    from rdkit.Chem import Descriptors, rdMolDescriptors

    mol = _molecule(smiles)
    return np.asarray(
        [
            Descriptors.ExactMolWt(mol),
            rdMolDescriptors.CalcTPSA(mol),
            Descriptors.NumRotatableBonds(mol),
            Descriptors.NumHDonors(mol),
            Descriptors.NumHAcceptors(mol),
            Descriptors.MolLogP(mol),
        ],
        dtype=np.float32,
    )


def _maccs(smiles: str):
    import numpy as np
    from rdkit.Chem import MACCSkeys

    fingerprint = MACCSkeys.GenMACCSKeys(_molecule(smiles))
    return np.asarray([int(bit) for bit in fingerprint], dtype=np.float32)


def build_tlc_features(smiles: str, mobile_phase: Mapping[str, float]):
    """Build the audited 179-dimensional feature vector in fixed order."""

    import numpy as np

    if not isinstance(smiles, str) or not smiles.strip():
        raise ValueError("smiles must be a non-empty string")
    ratios = validate_mobile_phase(mobile_phase)
    solvent_desc = np.zeros(6, dtype=np.float32)
    for solvent, solvent_smiles in zip(SOLVENT_ORDER, SOLVENT_SMILES):
        ratio = ratios[solvent]
        if ratio:
            solvent_desc += _basic_descriptors(solvent_smiles) * ratio
    features = np.concatenate([_maccs(smiles), solvent_desc, _basic_descriptors(smiles)]).astype(np.float32)
    if features.size != 179:
        raise RuntimeError(f"TLC feature dimension is {features.size}, expected 179")
    return features.reshape(1, -1)


def mobile_phase_vector(mobile_phase: Mapping[str, float]) -> tuple[float, ...]:
    """Map named solvent IDs to the audited legacy five-position vector."""

    values = validate_mobile_phase(mobile_phase)
    return tuple(values[name] for name in SOLVENT_ORDER)


def validate_mobile_phase(mobile_phase: Mapping[str, float]) -> dict[str, float]:
    """Validate and normalize an internal model solvent ratio.

    This helper is an internal feature-construction boundary.  Public HTTP
    DTOs validate exact fractions and a unit total before they reach it; this
    function does not normalize malformed public requests.
    """

    if not isinstance(mobile_phase, Mapping):
        raise ValueError("mobile_phase must be a solvent-to-fraction mapping")
    unknown = sorted(set(mobile_phase) - set(SOLVENT_ORDER))
    if unknown:
        raise ValueError(f"unknown solvent IDs: {unknown}")
    values: dict[str, float] = {}
    for solvent in SOLVENT_ORDER:
        raw = mobile_phase.get(solvent, 0.0)
        try:
            fraction = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{solvent} fraction must be numeric") from exc
        if not math.isfinite(fraction) or fraction < 0:
            raise ValueError(f"{solvent} fraction must be finite and non-negative")
        values[solvent] = fraction
    total = sum(values.values())
    if total <= 0:
        raise ValueError("at least one solvent fraction must be positive")
    active = frozenset(solvent for solvent, fraction in values.items() if fraction > 0)
    if not any(active == system for system in ALLOWED_SYSTEMS):
        raise ValueError("only the three model-declared binary solvent systems are supported")
    return {solvent: fraction / total for solvent, fraction in values.items()}


def _sigmoid(value: float) -> float:
    value = max(-60.0, min(60.0, float(value)))
    return 1.0 / (1.0 + math.exp(-value))


def _load_rf_with_sklearn_compatibility(path: Path) -> Any:
    """Load only an already hash-verified author RF artifact."""

    import numpy as np
    import joblib.numpy_pickle as numpy_pickle
    from sklearn.tree import _tree
    from sklearn.tree._classes import BaseDecisionTree

    class CompatibleNumpyUnpickler(numpy_pickle.NumpyUnpickler):
        def load_build(self) -> None:
            stack = self.stack
            state = stack.pop()
            instance = stack[-1]
            if isinstance(instance, _tree.Tree) and isinstance(state, dict):
                nodes = state.get("nodes")
                if isinstance(nodes, np.ndarray) and "missing_go_to_left" not in nodes.dtype.names:
                    upgraded = np.zeros(nodes.shape, dtype=_tree.NODE_DTYPE)
                    for field in nodes.dtype.names:
                        upgraded[field] = nodes[field]
                    state = dict(state)
                    state["nodes"] = upgraded
            setstate = getattr(instance, "__setstate__", None)
            if setstate is not None:
                setstate(state)
            else:
                if state:
                    instance.__dict__.update(state)
            if isinstance(instance, BaseDecisionTree) and not hasattr(instance, "monotonic_cst"):
                instance.monotonic_cst = None
            if isinstance(self.stack[-1], (numpy_pickle.NDArrayWrapper, numpy_pickle.NumpyArrayWrapper)):
                wrapper = self.stack.pop()
                if isinstance(wrapper, numpy_pickle.NDArrayWrapper):
                    self.compat_mode = True
                    payload = wrapper.read(self)
                else:
                    payload = wrapper.read(self, self.ensure_native_byte_order)
                self.stack.append(payload)

    CompatibleNumpyUnpickler.dispatch = numpy_pickle.NumpyUnpickler.dispatch.copy()
    CompatibleNumpyUnpickler.dispatch[pickle.BUILD[0]] = CompatibleNumpyUnpickler.load_build
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rb") as stream:
        return CompatibleNumpyUnpickler(str(path), stream, True, mmap_mode=None).load()


class TlcRuntime:
    """Runtime bound to one trusted artifact root."""

    def __init__(self, model_root: TlcModelRoot, *, manifest: TlcArtifactManifest | None = None) -> None:
        self.model_root = model_root
        self.manifest = manifest or TlcArtifactManifest(root=model_root.path)
        if self.manifest.root is not None and Path(self.manifest.root).resolve() != model_root.path:
            raise ValueError("TLC artifact manifest root does not match trusted model root")
        self._models: dict[str, Any] | None = None
        self._verified_handle: Any | None = None
        self.status = self._check_artifact()

    @classmethod
    def from_registered_artifact(
        cls,
        *,
        registry_root: str | Path,
        model_root: str | Path,
        artifact_id: str = TLC_ARTIFACT_ID,
    ) -> "TlcRuntime":
        if artifact_id != TLC_ARTIFACT_ID:
            raise ValueError("unknown TLC artifact ID")
        trusted = TlcModelRoot.from_registered_path(registry_root=registry_root, model_root=model_root)
        return cls(trusted, manifest=TlcArtifactManifest(artifact_id=artifact_id, root=trusted.path))

    def _check_artifact(self) -> TlcRuntimeStatus:
        dependencies_ok, dependency_reason = _has_dependencies()
        if not dependencies_ok:
            return TlcRuntimeStatus("UNAVAILABLE", dependency_reason, dependencies_available=False)
        expected = self.manifest.expected_assets()
        expected_names = {relative.as_posix() for relative in MODEL_FILENAMES.values()}
        if set(expected) != set(MODEL_FILENAMES) or any(
            not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value.lower())
            for value in expected.values()
        ):
            return TlcRuntimeStatus("UNAVAILABLE", "TLC artifact asset digest binding is invalid", dependencies_available=True)
        expected = {name: digest.lower() for name, digest in expected.items()}
        manifest_path = self.model_root.path / TLC_MANIFEST_FILENAME
        if not manifest_path.is_file() or _sha256(manifest_path) != TLC_ARTIFACT_MANIFEST_SHA256:
            return TlcRuntimeStatus("UNAVAILABLE", "TLC model manifest SHA-256 mismatch", dependencies_available=True)
        try:
            declared = _read_manifest_assets(manifest_path)
        except TlcArtifactUnavailable as exc:
            return TlcRuntimeStatus("UNAVAILABLE", str(exc), dependencies_available=True)
        expected_manifest = {
            relative.as_posix(): expected[name].lower()
            for name, relative in MODEL_FILENAMES.items()
        }
        if set(declared) != expected_names or declared != expected_manifest:
            return TlcRuntimeStatus("UNAVAILABLE", "TLC model manifest does not bind the expected assets", dependencies_available=True)
        for name, relative in MODEL_FILENAMES.items():
            path = self.model_root.path / relative
            if not path.is_file():
                return TlcRuntimeStatus("UNAVAILABLE", f"missing TLC asset: {relative}", dependencies_available=True)
            if _sha256(path) != expected.get(name):
                return TlcRuntimeStatus("UNAVAILABLE", f"TLC asset SHA-256 mismatch: {relative}", dependencies_available=True)
        return TlcRuntimeStatus(
            "UNAVAILABLE",
            "verification evidence required before execution",
            checked_assets=True,
            dependencies_available=True,
            feature_schema_verified=True,
        )

    def bind_verified_handle(self, handle: Any) -> None:
        """Bind the central verification-port handle; never accept ad-hoc flags."""

        if (
            not isinstance(handle, VerifiedModelHandle)
            or getattr(handle, "adapter_id", None) != "tlc"
            or getattr(handle, "model_id", None) != self.manifest.artifact_id
            or getattr(handle, "model_version", None) != self.manifest.model_version
            or getattr(handle, "input_schema_version", None) != "1.0"
            or getattr(handle, "output_schema_version", None) != "1.0"
            or getattr(handle, "artifact_sha256", None) != TLC_ARTIFACT_MANIFEST_SHA256
            or handle.business_scope_id != TLC_SCOPE_ID
            or handle.schema_for("single") is None
        ):
            raise TlcArtifactUnavailable("central TLC verification handle does not bind this artifact")
        if not (self.status.checked_assets and self.status.dependencies_available and self.status.feature_schema_verified):
            raise TlcArtifactUnavailable(self.status.reason or "TLC artifact unavailable")
        self.status = TlcRuntimeStatus("READY", checked_assets=True, dependencies_available=True, feature_schema_verified=True)
        self._verified_handle = handle

    def _require_ready(self) -> None:
        if self.status.status != "READY" or self._verified_handle is None:
            raise TlcArtifactUnavailable(self.status.reason or "TLC model unavailable")

    def load(self) -> dict[str, Any]:
        self._require_ready()
        return self._load_models()

    def _load_models(self) -> dict[str, Any]:
        if self._models is not None:
            return self._models
        import torch
        import joblib

        paths = {name: self.model_root.path / relative for name, relative in MODEL_FILENAMES.items()}
        class TLCANN(torch.nn.Module):
            def __init__(self) -> None:
                super().__init__()
                self.input_layer = torch.nn.Linear(179, 128)
                self.hidden_layer = torch.nn.Linear(128, 128)
                self.output_layer = torch.nn.Linear(128, 1)

            def forward(self, x):
                x = torch.nn.functional.leaky_relu(self.input_layer(x))
                x = torch.nn.functional.leaky_relu(self.hidden_layer(x))
                x = torch.nn.functional.leaky_relu(self.hidden_layer(x))
                x = torch.nn.functional.leaky_relu(self.hidden_layer(x))
                return torch.sigmoid(self.output_layer(x))

        ann_model = TLCANN()
        try:
            from chrom_platform.core.model_load_observation import record_model_load

            record_model_load("tlc", "ann")
            opener = gzip.open if paths["ann"].suffix == ".gz" else open
            with opener(paths["ann"], "rb") as stream:
                state_dict = torch.load(stream, map_location="cpu", weights_only=True)
        except TypeError as exc:
            raise TlcArtifactUnavailable("torch without weights_only=True is unsupported") from exc
        ann_model.load_state_dict(state_dict, strict=True)
        ann_model.eval()
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message=".*serialized model.*")
            opener = gzip.open if paths["lgb"].suffix == ".gz" else open
            with opener(paths["lgb"], "rb") as stream:
                record_model_load("tlc", "lgb")
                lgb = joblib.load(stream)
            opener = gzip.open if paths["xgb"].suffix == ".gz" else open
            with opener(paths["xgb"], "rb") as stream:
                record_model_load("tlc", "xgb")
                xgb = joblib.load(stream)
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="Trying to unpickle estimator.*")
            record_model_load("tlc", "random_forest")
            rf = _load_rf_with_sklearn_compatibility(paths["random_forest"])
        models = {"ann": ann_model, "lgb": lgb, "xgb": xgb, "random_forest": rf}
        for name, model in models.items():
            if name != "ann" and getattr(model, "n_features_in_", 179) != 179:
                raise TlcArtifactUnavailable(f"{name} model API does not declare 179 features")
        self._models = models
        return models

    def verify_golden(self, *, smiles: str, mobile_phase: Mapping[str, float]) -> dict[str, Any]:
        """Run a fixed golden input before a verification handle exists.

        Only the constructor's asset/dependency/schema checks permit this
        method. It never changes runtime health or creates an executable
        binding; assembly must still issue the central handle afterwards.
        """

        if not (self.status.checked_assets and self.status.dependencies_available and self.status.feature_schema_verified):
            raise TlcArtifactUnavailable(self.status.reason or "TLC artifact unavailable")
        models = self._load_models()
        import torch

        phase = validate_mobile_phase(mobile_phase)
        features = build_tlc_features(smiles, phase)
        with torch.no_grad():
            ann_value = float(models["ann"](torch.from_numpy(features)).cpu().numpy()[0, 0])
        lgb_booster = getattr(models["lgb"], "booster_", None)
        if lgb_booster is None:
            raise TlcArtifactUnavailable("LGB model API does not expose booster_")
        lgb_value = _sigmoid(float(lgb_booster.predict(features)[0]))
        import xgboost as xgb

        xgb_value = _sigmoid(float(models["xgb"].get_booster().predict(xgb.DMatrix(features))[0]))
        rf_value = _sigmoid(float(models["random_forest"].predict(features)[0]))
        components = {
            "lgb": max(0.0, min(1.0, lgb_value)),
            "xgb": max(0.0, min(1.0, xgb_value)),
            "random_forest": max(0.0, min(1.0, rf_value)),
            "ann": max(0.0, min(1.0, ann_value)),
        }
        predicted_rf = sum(ENSEMBLE_WEIGHTS[name] * components[name] for name in ENSEMBLE_WEIGHTS)
        return {
            "rf": round(max(0.0, min(1.0, predicted_rf)), 6),
            "component_predictions": {name: round(value, 6) for name, value in components.items()},
            "ensemble_weights": dict(ENSEMBLE_WEIGHTS),
            "mobile_phase": dict(phase),
        }

    def predict(self, *, smiles: str, mobile_phase: Mapping[str, float]) -> dict[str, Any]:
        """Return the real Rf and four model component predictions."""

        import torch

        models = self.load()
        phase = validate_mobile_phase(mobile_phase)
        features = build_tlc_features(smiles, phase)
        with torch.no_grad():
            ann_value = float(models["ann"](torch.from_numpy(features)).cpu().numpy()[0, 0])
        lgb_booster = getattr(models["lgb"], "booster_", None)
        if lgb_booster is None:
            raise TlcArtifactUnavailable("LGB model API does not expose booster_")
        lgb_value = _sigmoid(float(lgb_booster.predict(features)[0]))
        import xgboost as xgb

        xgb_value = _sigmoid(float(models["xgb"].get_booster().predict(xgb.DMatrix(features))[0]))
        rf_value = _sigmoid(float(models["random_forest"].predict(features)[0]))
        components = {
            "lgb": max(0.0, min(1.0, lgb_value)),
            "xgb": max(0.0, min(1.0, xgb_value)),
            "random_forest": max(0.0, min(1.0, rf_value)),
            "ann": max(0.0, min(1.0, ann_value)),
        }
        predicted_rf = sum(ENSEMBLE_WEIGHTS[name] * components[name] for name in ENSEMBLE_WEIGHTS)
        return {
            "rf": round(max(0.0, min(1.0, predicted_rf)), 6),
            "component_predictions": {name: round(value, 6) for name, value in components.items()},
            "ensemble_weights": dict(ENSEMBLE_WEIGHTS),
            "mobile_phase": dict(phase),
        }


__all__ = [
    "ALLOWED_SYSTEMS",
    "ENSEMBLE_WEIGHTS",
    "MODEL_FILENAMES",
    "SOLVENT_ORDER",
    "SOLVENT_TO_INDEX",
    "TlcArtifactUnavailable",
    "TlcModelRoot",
    "TlcRuntime",
    "TlcRuntimeStatus",
    "build_tlc_features",
    "mobile_phase_vector",
    "validate_mobile_phase",
]
