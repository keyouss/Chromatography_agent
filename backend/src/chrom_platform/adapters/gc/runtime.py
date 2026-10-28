"""Trusted CPU runtime for the audited GC temperature-program model.

The source bundle is loaded only after the complete executable-source and
weight inventory has been verified.  The runtime deliberately does not call
the source ``predict.py`` because that entry point accepts arbitrary paths and
has an unsafe ``torch.load`` fallback.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import copy
import json
import math
import pickle
import platform
import re
import secrets
import sys
import tempfile
import threading
import types
from dataclasses import dataclass, fields
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

from chrom_platform.modules.prediction.adapters import VerifiedModelHandle

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
    GcArtifactManifest,
)
from .serving_profile import (
    GC_REFERENCE_FIXTURE_SHA256,
    GC_SERVING_PROFILE_ID,
    GC_SERVING_PROFILE_SHA256,
    gc_serving_profile_descriptor,
)
from .source_pins import GC_ADAPTER_SOURCE_SHA256, GC_RUNTIME_SOURCE_SHA256
from chrom_platform.modules.prediction.schemas import (
    GcPredictionCreate,
    GcPredictionResult,
    GcSeriesTemperatureProgram,
)


class GcAssetUnavailable(RuntimeError):
    """Raised when the verified GC asset cannot be executed."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class GcRuntimeStatus:
    status: str
    reason: str | None = None
    checked_assets: bool = False
    dependencies_available: bool = False
    schema_verified: bool = False


@dataclass(frozen=True)
class GcVerificationReport:
    manifest_verified: bool
    schema_verified: bool
    artifact_sha_verified: bool
    environment_verified: bool
    golden_sample_verified: bool
    anomaly_checks_verified: bool
    fixture_verified: bool = False
    receipt_sha256: str | None = None
    verification_predict_attempts: int = 0
    verification_predict_successes: int = 0
    verification_forward_attempts: int = 0
    verification_forward_successes: int = 0


@dataclass(frozen=True)
class GcProfileVerificationReceipt:
    """Runtime-issued proof bound to this process-local GC runtime generation."""

    runtime_nonce: str
    generation: int
    profile_id: str
    profile_sha256: str
    model_id: str
    model_version: str
    artifact_sha256: str
    author_input_schema_version: str
    platform_input_schema_version: str
    input_schema_id: str
    input_schema_sha256: str
    output_schema_id: str
    output_schema_sha256: str
    fixture_id: str
    fixture_sha256: str
    environment_sha256: str
    asset_inventory_sha256: str
    adapter_source_sha256: str
    runtime_source_sha256: str
    verification_predict_attempts: int
    verification_predict_successes: int
    verification_forward_attempts: int
    verification_forward_successes: int
    output_sha256s: tuple[str, str]
    receipt_sha256: str


@dataclass(frozen=True)
class GcProfileReviewApproval:
    """Typed review anchor; accepted only by identity from its issuing runtime."""

    runtime_nonce: str
    generation: int
    receipt_sha256: str
    profile_id: str
    profile_sha256: str
    qa_review_sha256: str
    approval_nonce: str


GC_GOLDEN_ABS_TOLERANCE = 1e-5


@dataclass(frozen=True)
class GcModelRoot:
    path: Path
    registry_root: Path

    @classmethod
    def from_registered_path(
        cls, *, registry_root: str | Path, model_root: str | Path
    ) -> "GcModelRoot":
        registry = Path(registry_root).expanduser().resolve(strict=True)
        root = Path(model_root).expanduser().resolve(strict=True)
        try:
            root.relative_to(registry)
        except ValueError as exc:
            raise ValueError("GC model root must be inside the trusted registry root") from exc
        if not root.is_dir():
            raise ValueError("GC model root must be a directory")
        return cls(path=root, registry_root=registry)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _default_gc_fixture_path() -> Path:
    return (
        Path(__file__).resolve().parents[5]
        / "testing" / "fixtures" / "priority-launch" / "gc-reference-preprocessing-v2.json"
    )


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _dependencies() -> tuple[bool, str | None]:
    required = (
        "numpy", "pandas", "openpyxl", "torch", "torch_geometric", "rdkit",
        "mordred", "sklearn", "joblib", "xgboost", "matplotlib",
    )
    missing: list[str] = []
    for name in required:
        try:
            if importlib.util.find_spec(name) is None:
                missing.append(name)
        except (ImportError, ModuleNotFoundError):
            missing.append(name)
    return (not missing, None if not missing else "missing dependencies: " + ", ".join(missing))


def _finite_series(values: Sequence[float]) -> list[float]:
    try:
        result = [float(value) for value in values]
    except (TypeError, ValueError) as exc:
        raise GcAssetUnavailable("GC_TEMPERATURE_PROGRAM_INVALID") from exc
    if len(result) != 30 or not all(math.isfinite(value) for value in result):
        raise GcAssetUnavailable("GC_TEMPERATURE_PROGRAM_INVALID")
    return result


class GcRuntime:
    """One fixed model/scope runtime; no request-supplied paths are accepted."""

    def __init__(
        self,
        *,
        model_root: GcModelRoot,
        manifest: GcArtifactManifest | None = None,
        bound_model_id: str = GC_ARTIFACT_ID,
        bound_model_version: str = GC_MODEL_VERSION,
        business_scope_id: str,
    ) -> None:
        if not business_scope_id:
            raise ValueError("business_scope_id is required")
        if bound_model_id != GC_ARTIFACT_ID or bound_model_version != GC_MODEL_VERSION:
            raise ValueError("GC runtime is bound to the audited model")
        self.model_root = model_root
        self.manifest = manifest or GcArtifactManifest(root=model_root.path)
        if type(self.manifest) is not GcArtifactManifest:
            raise TypeError("GC runtime requires the audited GcArtifactManifest implementation")
        if self.manifest.root is not None and self.manifest.root.resolve() != model_root.path:
            raise ValueError("GC artifact manifest root does not match trusted model root")
        if (
            self.manifest.artifact_id != GC_ARTIFACT_ID
            or self.manifest.model_version != GC_MODEL_VERSION
            or self.manifest.model_sha256 != GC_MODEL_SHA256
        ):
            raise ValueError("GC artifact manifest does not bind the audited model")
        self.business_scope_id = business_scope_id
        self._bound_model_id: str | None = None
        self._bound_model_version: str | None = None
        self._bound_artifact_sha256: str | None = None
        self._verified_handle: VerifiedModelHandle | None = None
        self._model: Any | None = None
        self._author: dict[str, Any] | None = None
        self._runtime_nonce = secrets.token_hex(32)
        self._review_generation = 0
        self._profile_lock = threading.RLock()
        self._profile_receipt: GcProfileVerificationReceipt | None = None
        self._issued_review_approval: GcProfileReviewApproval | None = None
        self._review_consumed = False
        self._verification_predict_attempts = 0
        self._verification_predict_successes = 0
        self._verification_forward_attempts = 0
        self._verification_forward_successes = 0
        self._verification_output_snapshots: tuple[dict[str, Any], dict[str, Any]] | None = None
        self._verified_fixture_path: Path | None = None
        self.status = self._inspect()

    def _inspect(self) -> GcRuntimeStatus:
        try:
            for relative, expected in self.manifest.expected_files().items():
                target = (self.model_root.path / relative).resolve()
                if not _inside(target, self.model_root.path) or not target.is_file():
                    return GcRuntimeStatus("UNAVAILABLE", "MODEL_TRUSTED_FILE_MISSING")
                if _sha256(target) != expected:
                    return GcRuntimeStatus("UNAVAILABLE", "MODEL_TRUSTED_FILE_HASH_MISMATCH")
            adapter_source, runtime_source = self._platform_source_hashes()
            if (
                not _valid_sha256(GC_ADAPTER_SOURCE_SHA256)
                or not _valid_sha256(GC_RUNTIME_SOURCE_SHA256)
                or adapter_source != GC_ADAPTER_SOURCE_SHA256
                or runtime_source != GC_RUNTIME_SOURCE_SHA256
            ):
                return GcRuntimeStatus("UNAVAILABLE", "GC_PLATFORM_SOURCE_HASH_MISMATCH", True, False, True)
        except (OSError, ValueError):
            return GcRuntimeStatus("UNAVAILABLE", "MODEL_TRUSTED_FILE_UNREADABLE")
        dependencies, reason = _dependencies()
        if not dependencies:
            return GcRuntimeStatus("UNAVAILABLE", "SCIENTIFIC_DEPENDENCIES_UNAVAILABLE", True, False, True)
        return GcRuntimeStatus("READY", None, True, True, True)

    @classmethod
    def from_registered_artifact(
        cls,
        *,
        registry_root: str | Path,
        model_root: str | Path,
        business_scope_id: str,
    ) -> "GcRuntime":
        trusted_root = GcModelRoot.from_registered_path(
            registry_root=registry_root, model_root=model_root
        )
        return cls(model_root=trusted_root, business_scope_id=business_scope_id)

    @property
    def verified_handle(self) -> VerifiedModelHandle | None:
        return self._verified_handle

    @property
    def verification_output_snapshots(self) -> tuple[dict[str, Any], dict[str, Any]] | None:
        """Return isolated copies of the two latest golden outputs, when available."""
        with self._profile_lock:
            if self._verification_output_snapshots is None:
                return None
            return copy.deepcopy(self._verification_output_snapshots)

    def bind_verified_handle(self, handle: VerifiedModelHandle) -> None:
        """Bind central verification facts before any model load."""
        if (
            not isinstance(handle, VerifiedModelHandle)
            or handle.adapter_id != "gc"
            or handle.model_id != GC_ARTIFACT_ID
            or handle.model_version != GC_MODEL_VERSION
            or handle.business_scope_id != self.business_scope_id
            or handle.input_schema_version != GC_INPUT_SCHEMA_VERSION
            or handle.output_schema_version != GC_OUTPUT_SCHEMA_VERSION
            or handle.artifact_sha256 != GC_MODEL_SHA256
            or handle.preprocessing_profile_id != GC_SERVING_PROFILE_ID
            or handle.preprocessing_profile_sha256 != GC_SERVING_PROFILE_SHA256
            or handle.schema_for("single") is None
        ):
            raise GcAssetUnavailable("MODEL_VERIFICATION_MISMATCH")
        self._bound_model_id = handle.model_id
        self._bound_model_version = handle.model_version
        self._bound_artifact_sha256 = handle.artifact_sha256
        self._verified_handle = handle

    @staticmethod
    def _platform_source_hashes() -> tuple[str, str]:
        directory = Path(__file__).resolve().parent
        return _sha256(directory / "adapter.py"), _sha256(directory / "runtime.py")

    def _begin_profile_verification(self) -> None:
        with self._profile_lock:
            self._verified_handle = None
            self._bound_model_id = None
            self._bound_model_version = None
            self._bound_artifact_sha256 = None
            self._profile_receipt = None
            self._issued_review_approval = None
            self._review_consumed = False
            self._review_generation += 1
            self._verification_predict_attempts = 0
            self._verification_predict_successes = 0
            self._verification_forward_attempts = 0
            self._verification_forward_successes = 0
            self._verification_output_snapshots = None
            self._verified_fixture_path = None

    def reset_profile_review_state(self) -> None:
        """Invalidate any proof/approval before an explicit offline retry."""
        self._begin_profile_verification()

    def _issue_profile_review_approval(self, receipt: GcProfileVerificationReceipt, qa_review_sha256: str) -> GcProfileReviewApproval:
        """Offline operator hook; a review digest alone never creates a receipt."""
        with self._profile_lock:
            if (
                self._profile_receipt is not receipt
                or receipt.runtime_nonce != self._runtime_nonce
                or receipt.generation != self._review_generation
                or receipt.profile_id != GC_SERVING_PROFILE_ID
                or receipt.profile_sha256 != GC_SERVING_PROFILE_SHA256
                or not _valid_sha256(qa_review_sha256)
                or self._issued_review_approval is not None
                or self._review_consumed
            ):
                raise GcAssetUnavailable("GC_PROFILE_REVIEW_BINDING_INVALID")
            approval = GcProfileReviewApproval(
                runtime_nonce=self._runtime_nonce,
                generation=self._review_generation,
                receipt_sha256=receipt.receipt_sha256,
                profile_id=receipt.profile_id,
                profile_sha256=receipt.profile_sha256,
                qa_review_sha256=qa_review_sha256,
                approval_nonce=secrets.token_hex(32),
            )
            self._issued_review_approval = approval
            return approval

    def _consume_profile_review_approval(self, approval: GcProfileReviewApproval) -> GcProfileVerificationReceipt:
        """Consume identity-bound approval before any handle is issued; failures stay consumed."""
        with self._profile_lock:
            receipt = self._profile_receipt
            accepted = (
                approval is self._issued_review_approval
                and type(approval) is GcProfileReviewApproval
                and receipt is not None
                and approval.runtime_nonce == self._runtime_nonce
                and approval.generation == self._review_generation
                and approval.receipt_sha256 == receipt.receipt_sha256
                and approval.profile_id == GC_SERVING_PROFILE_ID
                and approval.profile_sha256 == GC_SERVING_PROFILE_SHA256
                and not self._review_consumed
            )
            # Clear state first so every later validation or issue failure consumes it.
            self._issued_review_approval = None
            self._profile_receipt = None
            self._review_consumed = True
            if not accepted or receipt is None:
                raise GcAssetUnavailable("GC_PROFILE_REVIEW_APPROVAL_INVALID")
            self._recheck_consumed_receipt(receipt)
            return receipt

    def _recheck_consumed_receipt(self, receipt: GcProfileVerificationReceipt) -> None:
        # Re-read the same fixed file before issuing a handle; the receipt alone
        # cannot prove it stayed unchanged during the independent review.
        fixture_path = self._verified_fixture_path
        try:
            fixture_matches = fixture_path is not None and _sha256(fixture_path) == receipt.fixture_sha256
        except OSError:
            fixture_matches = False
        if not fixture_matches:
            raise GcAssetUnavailable("GC_PROFILE_REVIEW_FIXTURE_STALE")
        current_profile = gc_serving_profile_descriptor()
        current_adapter, current_runtime = self._platform_source_hashes()
        receipt_payload = {
            item.name: getattr(receipt, item.name)
            for item in fields(GcProfileVerificationReceipt)
            if item.name != "receipt_sha256"
        }
        refreshed = self._inspect()
        if (
            receipt.runtime_nonce != self._runtime_nonce
            or receipt.generation != self._review_generation
            or receipt.profile_id != GC_SERVING_PROFILE_ID
            or receipt.profile_sha256 != GC_SERVING_PROFILE_SHA256
            or receipt.model_id != GC_ARTIFACT_ID
            or receipt.model_version != GC_MODEL_VERSION
            or receipt.artifact_sha256 != GC_MODEL_SHA256
            or receipt.author_input_schema_version != GC_AUTHOR_INPUT_SCHEMA_VERSION
            or receipt.platform_input_schema_version != GC_INPUT_SCHEMA_VERSION
            or receipt.input_schema_id != current_profile["input_schema_id"]
            or receipt.adapter_source_sha256 != current_adapter
            or receipt.runtime_source_sha256 != current_runtime
            or current_adapter != GC_ADAPTER_SOURCE_SHA256
            or current_runtime != GC_RUNTIME_SOURCE_SHA256
            or receipt.input_schema_sha256 != current_profile["input_schema_sha256"]
            or receipt.output_schema_sha256 != current_profile["output_schema_sha256"]
            or receipt.output_schema_id != current_profile["output_schema_id"]
            or receipt.fixture_id != current_profile["reference_fixture_id"]
            or not _valid_sha256(receipt.fixture_sha256)
            or receipt.fixture_sha256 != GC_REFERENCE_FIXTURE_SHA256
            or not _valid_sha256(receipt.environment_sha256)
            or not _valid_sha256(receipt.asset_inventory_sha256)
            or receipt.verification_predict_attempts != receipt.verification_predict_successes
            or receipt.verification_forward_attempts != receipt.verification_forward_successes
            or len(receipt.output_sha256s) != 2
            or not all(_valid_sha256(item) for item in receipt.output_sha256s)
            or receipt.receipt_sha256 != _canonical_sha256(receipt_payload)
            or receipt.asset_inventory_sha256 != self._verified_asset_inventory_sha256()
            or receipt.environment_sha256 != _canonical_sha256(self._current_environment())
            or refreshed.status != "READY"
        ):
            raise GcAssetUnavailable("GC_PROFILE_REVIEW_RECEIPT_STALE")
        self.status = refreshed

    @staticmethod
    def _current_environment() -> dict[str, str]:
        import numpy as np
        import torch
        from mordred import __version__ as mordred_version
        from rdkit import rdBase

        return {
            "python": platform.python_version(),
            "rdkit": rdBase.rdkitVersion,
            "torch": str(torch.__version__),
            "numpy": str(np.__version__),
            "mordred": str(mordred_version),
        }

    def _verified_asset_inventory_sha256(self) -> str:
        inventory: dict[str, str] = {}
        for relative, expected in sorted(self.manifest.expected_files().items()):
            path = (self.model_root.path / relative).resolve()
            if not _inside(path, self.model_root.path) or not path.is_file():
                raise GcAssetUnavailable("MODEL_TRUSTED_FILE_MISSING")
            actual = _sha256(path)
            if actual != expected:
                raise GcAssetUnavailable("MODEL_TRUSTED_FILE_HASH_MISMATCH")
            inventory[relative] = actual
        return _canonical_sha256(inventory)

    def _make_profile_receipt(
        self,
        *,
        fixture_sha256: str,
        environment: Mapping[str, Any],
        first: Mapping[str, Any],
        second: Mapping[str, Any],
    ) -> GcProfileVerificationReceipt | None:
        with self._profile_lock:
            counts = (
                self._verification_predict_attempts,
                self._verification_predict_successes,
                self._verification_forward_attempts,
                self._verification_forward_successes,
            )
            # This sole golden is one molecule and one sequence batch. The
            # measured function and forward counters must match its observed path.
            if counts != (2, 2, 2, 2):
                return None
            descriptor = gc_serving_profile_descriptor()
            adapter_source, runtime_source = self._platform_source_hashes()
            if (
                adapter_source != GC_ADAPTER_SOURCE_SHA256
                or runtime_source != GC_RUNTIME_SOURCE_SHA256
                or fixture_sha256 != GC_REFERENCE_FIXTURE_SHA256
            ):
                return None
            asset_inventory_sha256 = self._verified_asset_inventory_sha256()
            payload: dict[str, Any] = {
                "runtime_nonce": self._runtime_nonce,
                "generation": self._review_generation,
                "profile_id": GC_SERVING_PROFILE_ID,
                "profile_sha256": GC_SERVING_PROFILE_SHA256,
                "model_id": GC_ARTIFACT_ID,
                "model_version": GC_MODEL_VERSION,
                "artifact_sha256": GC_MODEL_SHA256,
                "author_input_schema_version": GC_AUTHOR_INPUT_SCHEMA_VERSION,
                "platform_input_schema_version": GC_INPUT_SCHEMA_VERSION,
                "input_schema_id": descriptor["input_schema_id"],
                "input_schema_sha256": descriptor["input_schema_sha256"],
                "output_schema_id": descriptor["output_schema_id"],
                "output_schema_sha256": descriptor["output_schema_sha256"],
                "fixture_id": descriptor["reference_fixture_id"],
                "fixture_sha256": fixture_sha256,
                "environment_sha256": _canonical_sha256(environment),
                "asset_inventory_sha256": asset_inventory_sha256,
                "adapter_source_sha256": adapter_source,
                "runtime_source_sha256": runtime_source,
                "verification_predict_attempts": counts[0],
                "verification_predict_successes": counts[1],
                "verification_forward_attempts": counts[2],
                "verification_forward_successes": counts[3],
                "output_sha256s": (_canonical_sha256(first), _canonical_sha256(second)),
            }
            receipt = GcProfileVerificationReceipt(
                **payload, receipt_sha256=_canonical_sha256(payload),
            )
            self._profile_receipt = receipt
            self._issued_review_approval = None
            self._review_consumed = False
            return receipt

    def _load_author_modules(self) -> dict[str, Any]:
        if self._author is not None:
            return self._author
        if self.status.status != "READY":
            raise GcAssetUnavailable(self.status.reason or "MODEL_RUNTIME_UNAVAILABLE")
        root = self.model_root.path
        previous = {name: value for name, value in sys.modules.items() if name == "Model" or name.startswith("Model.")}
        loaded_ok = False
        try:
            package = types.ModuleType("Model")
            package.__path__ = [str(root / "Model")]
            sys.modules["Model"] = package
            loaded: dict[str, Any] = {}
            for name in ("compound_tools", "config", "GIN", "data_process", "model"):
                path = (root / "Model" / f"{name}.py").resolve()
                module_name = f"Model.{name}"
                spec = importlib.util.spec_from_file_location(module_name, path)
                if spec is None or spec.loader is None:
                    raise GcAssetUnavailable("MODEL_AUTHOR_RUNTIME_INVALID")
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
                loaded[name] = module
            self._author = loaded
            loaded_ok = True
            return loaded
        except GcAssetUnavailable:
            raise
        except Exception as exc:
            raise GcAssetUnavailable("MODEL_AUTHOR_RUNTIME_UNAVAILABLE") from exc
        finally:
            # torch-geometric's Inspector resolves annotations through
            # ``sys.modules`` while constructing the model, so the audited
            # modules stay registered for the lifetime of the cached model.
            # On a failed import, restore any caller modules and leave no
            # partially imported author package behind.
            if not loaded_ok:
                for name in list(sys.modules):
                    if name == "Model" or name.startswith("Model."):
                        sys.modules.pop(name, None)
                sys.modules.update(previous)

    def load(self) -> Any:
        if self._bound_model_id is None:
            raise GcAssetUnavailable("MODEL_VERIFICATION_REQUIRED")
        return self._load_model(for_verification=False)

    def _load_model(self, *, for_verification: bool) -> Any:
        if self._model is not None:
            return self._model
        if self._bound_model_id is None and not for_verification:
            raise GcAssetUnavailable("MODEL_VERIFICATION_REQUIRED")
        if self.status.status != "READY":
            raise GcAssetUnavailable(self.status.reason or "MODEL_RUNTIME_UNAVAILABLE")
        author = self._load_author_modules()
        try:
            import torch
        except ImportError as exc:
            raise GcAssetUnavailable("TORCH_UNAVAILABLE") from exc
        gin = author["GIN"]
        model_module = author["model"]
        cpu = torch.device("cpu")
        gin.device = cpu
        model_module.device = cpu
        model = model_module.MultiSeqModel(
            {"num_tasks": 3, "num_layers": 5, "emb_dim": 128,
             "drop_ratio": 0.0, "graph_pooling": "sum", "descriptor_dim": 1827},
            {"seq_len": 32, "d_model": 64, "num_layers": 3, "dropout": 0.1},
            num_tasks=3,
        ).to(cpu)
        checkpoint = (self.model_root.path / "Outcome/GNN_seq240408_250c/model_save_600.pth").resolve()
        try:
            state = torch.load(checkpoint, map_location=cpu, weights_only=True)
        except (TypeError, RuntimeError, ValueError, EOFError, pickle.UnpicklingError) as exc:
            raise GcAssetUnavailable("WEIGHTS_ONLY_UNSUPPORTED") from exc
        if not isinstance(state, Mapping):
            raise GcAssetUnavailable("MODEL_STATE_INVALID")
        try:
            incompatible = model.load_state_dict(state, strict=False)
        except (RuntimeError, TypeError, ValueError) as exc:
            raise GcAssetUnavailable("MODEL_STATE_API_MISMATCH") from exc
        allowed_missing = {name for name in model.state_dict() if name.endswith(".rbf_list.0.gamma")}
        if set(incompatible.unexpected_keys) or set(incompatible.missing_keys) != allowed_missing:
            raise GcAssetUnavailable("MODEL_STATE_API_MISMATCH")
        model.eval()
        self._model = model
        return model

    @staticmethod
    def _config(work_dir: Path) -> SimpleNamespace:
        return SimpleNamespace(
            Whether_predict=True, shuffle_seed=520, known_data_path="", unknown_data_path="",
            known_descriptor_path="", unknown_descriptor_path="", ML_test_ratio=0.1,
            ML_features=[], ML_label=["True_RT"], known_3D_path="", unknown_3D_path=str(work_dir),
            gnn_train_ratio=0.8, gnn_valid_ratio=0.1, gnn_test_ratio=0.1, gnn_batch_size=64,
            num_workers=0, clean_data_path="", Enbedding_mode="Post", Whether_sequence=True,
            Whether_30min=True, Fig2c_Similarity=False, similarity_threshold=0.8, Fig2c_number=False,
            Fig2c_noise=False, noise_level=0.8, Re_smiles_A="", Re_smiles_B="",
            recommend_3D_path="", GNN_mode="Pre",
        )

    @staticmethod
    def _write_protocol_mol(smiles: str, name: Path, *, compound_tools: Any) -> None:
        """Run the author 3D path with the versioned deterministic RDKit seed."""
        AllChem = compound_tools.AllChem
        Chem = compound_tools.Chem
        molecule = AllChem.MolFromSmiles(smiles)
        if molecule is None:
            raise ValueError("invalid GC SMILES")
        molecule = Chem.AddHs(molecule)
        embed_parameters = dict(GC_RDKIT_EMBED_PARAMETERS)
        embed_parameters["coordMap"] = {}
        conformer_ids = list(AllChem.EmbedMultipleConfs(molecule, **embed_parameters))
        if not conformer_ids:
            raise ValueError("RDKit did not generate a GC conformer")
        AllChem.MMFFOptimizeMoleculeConfs(molecule, **GC_RDKIT_MMFF_PARAMETERS)
        molecule = Chem.RemoveHs(molecule)
        # Preserve the author helper's MolToMolFile default conformer selection.
        Chem.MolToMolFile(molecule, str(name) + ".mol")

    def predict(self, *, normalized_smiles: Sequence[str], temperature_series_c: Sequence[float]) -> dict[str, Any]:
        return self._predict(
            normalized_smiles=normalized_smiles,
            temperature_series_c=temperature_series_c,
            for_verification=False,
        )

    def _predict(
        self,
        *,
        normalized_smiles: Sequence[str],
        temperature_series_c: Sequence[float],
        for_verification: bool,
    ) -> dict[str, Any]:
        if for_verification:
            with self._profile_lock:
                self._verification_predict_attempts += 1
        if (
            not for_verification
            and (
                self._verified_handle is None
                or self._bound_model_id is None
                or self._bound_artifact_sha256 != GC_MODEL_SHA256
            )
        ):
            raise GcAssetUnavailable("MODEL_VERIFICATION_REQUIRED")
        if isinstance(normalized_smiles, (str, bytes)) or not normalized_smiles:
            raise ValueError("GC requires at least one normalized SMILES")
        if not all(isinstance(smiles, str) and smiles.strip() for smiles in normalized_smiles):
            raise ValueError("GC normalized SMILES must be non-empty strings")
        temperatures = _finite_series(temperature_series_c)
        model = self._load_model(for_verification=for_verification)
        author = self._load_author_modules()
        try:
            import numpy as np
            import torch
            from rdkit import Chem
            from torch_geometric.data import DataLoader
        except ImportError as exc:
            raise GcAssetUnavailable("SCIENTIFIC_DEPENDENCIES_UNAVAILABLE") from exc
        with tempfile.TemporaryDirectory(prefix="chrom_gc_", dir=None) as temporary:
            work_dir = Path(temporary)
            descriptor_rows: list[Any] = []
            graphs: list[Any] = []
            valid_smiles: list[str] = []
            failed_smiles: list[str] = []
            for index, smiles in enumerate(normalized_smiles):
                mol = Chem.MolFromSmiles(smiles)
                if mol is None:
                    failed_smiles.append(smiles)
                    continue
                try:
                    # Keep the author's 3D/optimization/serialization path and
                    # bind this runtime version to its explicit RDKit seed.
                    mol_path = work_dir / f"3D_mol_{index}"
                    self._write_protocol_mol(smiles, mol_path, compound_tools=author["compound_tools"])
                    mol = Chem.MolFromMolFile(str(mol_path) + ".mol")
                except Exception:
                    failed_smiles.append(smiles)
                    continue
                if mol is None:
                    failed_smiles.append(smiles)
                    continue
                graph = author["compound_tools"].mol_to_geognn_graph_data_MMFF3d(mol)
                descriptor = np.asarray(author["compound_tools"].mord(mol), dtype=np.float32)
                if descriptor.ndim != 1 or descriptor.size < 1569 or not np.isfinite(descriptor[:1569]).all():
                    failed_smiles.append(smiles)
                    continue
                graphs.append(graph)
                descriptor_rows.append(descriptor)
                valid_smiles.append(smiles)
            if not valid_smiles:
                if for_verification:
                    with self._profile_lock:
                        self._verification_predict_successes += 1
                return {
                    "predictions": [],
                    "failed_smiles": failed_smiles,
                    "temperature_series_c": temperatures,
                    "preprocessing_protocol_id": GC_PREPROCESSING_PROTOCOL_ID,
                }
            np.save(work_dir / "Descriptors.npy", np.asarray(descriptor_rows, dtype=np.float32))
            data = author["data_process"].Dataset_process(self._config(work_dir))
            zero = np.zeros(len(graphs), dtype=np.float32)
            atom_graphs, angle_graphs, _ = data.Construct_dataset(
                graphs, np.arange(len(graphs)), zero, str(work_dir), zero, zero, zero, zero
            )
            sequences = []
            for _ in atom_graphs:
                sequence = list(temperatures) + [temperatures[-1], temperatures[-1]]
                sequences.append(torch.tensor(sequence[:32], dtype=torch.float32).view(32, 1))
            atom_loader = DataLoader(atom_graphs, batch_size=64, shuffle=False, num_workers=0)
            angle_loader = DataLoader(angle_graphs, batch_size=64, shuffle=False, num_workers=0)
            sequence_loader = DataLoader(sequences, batch_size=64, shuffle=False, num_workers=0)
            chunks: list[np.ndarray] = []
            with torch.no_grad():
                for atom_batch, angle_batch, sequence_batch in zip(atom_loader, angle_loader, sequence_loader):
                    if for_verification:
                        with self._profile_lock:
                            self._verification_forward_attempts += 1
                    output = model(atom_batch.to("cpu"), angle_batch.to("cpu"), sequence_batch.to("cpu"))
                    if for_verification:
                        with self._profile_lock:
                            self._verification_forward_successes += 1
                    array = output.detach().cpu().numpy()
                    if array.ndim != 2 or array.shape[1] != 3 or not np.isfinite(array).all():
                        raise GcAssetUnavailable("MODEL_OUTPUT_SHAPE_INVALID")
                    chunks.append(array)
            predictions = np.concatenate(chunks, axis=0)
            if predictions.shape != (len(valid_smiles), 3) or not np.isfinite(predictions).all():
                raise GcAssetUnavailable("MODEL_OUTPUT_SHAPE_INVALID")
            rows = []
            for smiles, values in zip(valid_smiles, predictions):
                q10, q50, q90 = (float(value) for value in values)
                if min(q10, q50, q90) < 0 or max(q10, q50, q90) > 10_000 or not (q10 <= q50 <= q90):
                    raise GcAssetUnavailable("MODEL_OUTPUT_RANGE_INVALID")
                rows.append({"smiles": smiles, "rt_q10_min": q10, "rt_q50_min": q50, "rt_q90_min": q90})
            result = {
                "predictions": rows,
                "failed_smiles": failed_smiles,
                "temperature_series_c": temperatures,
                "model_name": "author_GNN_model_save_600",
                "artifact_sha256": GC_MODEL_SHA256,
                "preprocessing_protocol_id": GC_PREPROCESSING_PROTOCOL_ID,
            }
            if for_verification:
                with self._profile_lock:
                    self._verification_predict_successes += 1
            return result

    def verify_golden(
        self, fixture: Mapping[str, Any], *, fixture_path: str | Path | None = None,
    ) -> GcVerificationReport:
        """Verify the fixed author checkpoint against the checked-in software-parity sample."""
        self._begin_profile_verification()
        checks = {
            "manifest_verified": False,
            "schema_verified": False,
            "artifact_sha_verified": False,
            "environment_verified": False,
            "golden_sample_verified": False,
            "anomaly_checks_verified": False,
            "fixture_verified": False,
        }
        try:
            if not (self.status.checked_assets and self.status.dependencies_available and self.status.schema_verified):
                return GcVerificationReport(**checks)
            authoritative_fixture_path = Path(fixture_path).resolve(strict=True) if fixture_path is not None else _default_gc_fixture_path()
            fixture_bytes = authoritative_fixture_path.read_bytes()
            fixture_sha256 = hashlib.sha256(fixture_bytes).hexdigest()
            authoritative_fixture = json.loads(fixture_bytes.decode("utf-8"))
            fixture_snapshot = copy.deepcopy(authoritative_fixture)
            if (
                fixture_sha256 != GC_REFERENCE_FIXTURE_SHA256
                or not isinstance(fixture_snapshot, Mapping)
                or not isinstance(fixture, Mapping)
                or dict(fixture_snapshot) != dict(fixture)
            ):
                return GcVerificationReport(**checks)
            checks["fixture_verified"] = True
            self._verified_fixture_path = authoritative_fixture_path
            model_meta = fixture_snapshot.get("model")
            if not isinstance(model_meta, Mapping):
                return GcVerificationReport(**checks)
            if (
                fixture_snapshot.get("component") != "gc"
                or model_meta.get("model_id") != GC_ARTIFACT_ID
                or model_meta.get("model_version") != GC_MODEL_VERSION
                or model_meta.get("preprocessing_protocol_id") != GC_PREPROCESSING_PROTOCOL_ID
                or model_meta.get("input_schema_version") != GC_AUTHOR_INPUT_SCHEMA_VERSION
                or model_meta.get("output_schema_version") != GC_OUTPUT_SCHEMA_VERSION
                or model_meta.get("model_asset_sha256", "").lower() != GC_MODEL_SHA256
                or model_meta.get("registry_sha256", "").lower() != self.manifest.expected_files()["MODEL_SHA256.txt"]
                or model_meta.get("source_sha256", "").lower() != self.manifest.expected_files()["predict.py"]
            ):
                return GcVerificationReport(**checks)
            protocol_meta = fixture_snapshot.get("preprocessing_protocol")
            if not isinstance(protocol_meta, Mapping):
                return GcVerificationReport(**checks)
            embed_parameters = dict(GC_RDKIT_EMBED_PARAMETERS)
            embed_parameters["coordMap"] = {}
            if (
                protocol_meta.get("id") != GC_PREPROCESSING_PROTOCOL_ID
                or protocol_meta.get("random_seed") != 42
                or protocol_meta.get("embed_multiple_confs_parameters") != embed_parameters
                or protocol_meta.get("mmff_optimize_molecule_confs_parameters") != GC_RDKIT_MMFF_PARAMETERS
            ):
                return GcVerificationReport(**checks)
            environment = protocol_meta.get("environment")
            if not isinstance(environment, Mapping) or dict(environment) != self._current_environment():
                return GcVerificationReport(**checks)
            manifest_path = self.model_root.path / "MODEL_SHA256.txt"
            manifest_text = manifest_path.read_text(encoding="utf-8").lower()
            checks["manifest_verified"] = (
                _sha256(manifest_path) == self.manifest.expected_files()["MODEL_SHA256.txt"]
                and GC_MODEL_SHA256 in manifest_text
            )
            checks["artifact_sha_verified"] = (
                _sha256(self.model_root.path / "Outcome/GNN_seq240408_250c/model_save_600.pth") == GC_MODEL_SHA256
            )

            input_schema = GcPredictionCreate.model_json_schema()
            output_schema = GcPredictionResult.model_json_schema()
            # The production adapter narrows the shared DTO's broad array bound
            # to the audited model's actual 30-point program contract.
            definitions = input_schema.get("$defs", {})
            linear_schema = definitions.get("GcLinearTemperatureProgram", {})
            hold_schema = linear_schema.get("properties", {}).get("initial_hold_min", {})
            if isinstance(hold_schema, dict):
                hold_schema["multipleOf"] = 1
            series_schema = definitions.get("GcSeriesTemperatureProgram", {})
            if not isinstance(series_schema, dict):
                return GcVerificationReport(**checks)
            series_items = series_schema.get("properties", {}).get("temperature_series_c", {})
            if not isinstance(series_items, dict):
                return GcVerificationReport(**checks)
            series_items["minItems"] = 30
            series_items["maxItems"] = 30
            checks["schema_verified"] = (
                set(definitions) >= {"GcLinearTemperatureProgram", "GcSeriesTemperatureProgram"}
                and series_items.get("minItems") == 30
                and series_items.get("maxItems") == 30
                and GcSeriesTemperatureProgram.model_fields["temperature_series_c"].annotation == list[float]
                and output_schema.get("title") == "GcPredictionResult"
            )
            if not (checks["manifest_verified"] and checks["artifact_sha_verified"] and checks["schema_verified"]):
                return GcVerificationReport(**checks)

            sample = fixture_snapshot["input"]
            expected = fixture_snapshot["expected_raw"]
            smiles = sample["smiles"]
            temperatures = [
                float(sample["initial_temperature_c"])
                if minute < int(sample["initial_hold_min"])
                else min(
                    float(sample["final_temperature_c"]),
                    float(sample["initial_temperature_c"])
                    + (minute - int(sample["initial_hold_min"])) * float(sample["heating_rate_c_per_min"]),
                )
                for minute in range(30)
            ]
            if temperatures != [float(value) for value in expected["temperature_series_c"]]:
                return GcVerificationReport(**checks)
            validated = GcPredictionCreate.model_validate({
                "project_id": "gc-verification",
                "chromatography_type": "gc",
                "tool": "single",
                "schema_version": GC_INPUT_SCHEMA_VERSION,
                "model_selection": {
                    "mode": "exact", "model_id": GC_ARTIFACT_ID, "model_version": GC_MODEL_VERSION,
                    "preprocessing_profile_id": GC_SERVING_PROFILE_ID,
                    "preprocessing_profile_sha256": GC_SERVING_PROFILE_SHA256,
                },
                "structure_refs": ["gc-golden-structure"],
                "input": {"temperature_program": {
                    "mode": "series", "temperature_series_c": temperatures,
                }},
            })
            if len(validated.structure_refs) != 1 or len(validated.input.temperature_program.temperature_series_c) != 30:
                return GcVerificationReport(**checks)
            first = self._predict(normalized_smiles=smiles, temperature_series_c=temperatures, for_verification=True)
            second = self._predict(normalized_smiles=smiles, temperature_series_c=temperatures, for_verification=True)
            with self._profile_lock:
                self._verification_output_snapshots = (
                    copy.deepcopy(dict(first)),
                    copy.deepcopy(dict(second)),
                )
            checks["environment_verified"] = self.status.dependencies_available
            checks["anomaly_checks_verified"] = self._valid_model_output(first, smiles)
            checks["golden_sample_verified"] = (
                first == second
                and checks["anomaly_checks_verified"]
                and first.get("temperature_series_c") == temperatures
                and self._matches_golden(first, expected)
            )
        except Exception:
            return self._verification_report(checks)
        receipt = None
        if all(checks.values()):
            receipt = self._make_profile_receipt(
                fixture_sha256=fixture_sha256,
                environment=environment,
                first=first,
                second=second,
            )
        return self._verification_report(checks, receipt=receipt)

    def _verification_report(
        self,
        checks: Mapping[str, bool],
        *,
        receipt: GcProfileVerificationReceipt | None = None,
    ) -> GcVerificationReport:
        return GcVerificationReport(
            **checks,
            receipt_sha256=receipt.receipt_sha256 if receipt is not None else None,
            verification_predict_attempts=self._verification_predict_attempts,
            verification_predict_successes=self._verification_predict_successes,
            verification_forward_attempts=self._verification_forward_attempts,
            verification_forward_successes=self._verification_forward_successes,
        )

    @staticmethod
    def _valid_model_output(output: Mapping[str, Any], expected_smiles: Sequence[str]) -> bool:
        try:
            predictions = output["predictions"]
            failed = output["failed_smiles"]
            if not isinstance(predictions, list) or not isinstance(failed, list) or failed:
                return False
            if len(predictions) != len(expected_smiles):
                return False
            for row, smiles in zip(predictions, expected_smiles):
                values = [float(row[key]) for key in ("rt_q10_min", "rt_q50_min", "rt_q90_min")]
                if row.get("smiles") != smiles or not all(math.isfinite(value) and 0 <= value <= 10_000 for value in values):
                    return False
                if values != sorted(values):
                    return False
            temperatures = output["temperature_series_c"]
            return len(temperatures) == 30 and all(math.isfinite(float(value)) for value in temperatures)
        except (KeyError, TypeError, ValueError):
            return False

    @staticmethod
    def _matches_golden(output: Mapping[str, Any], expected: Mapping[str, Any]) -> bool:
        try:
            rows = output["predictions"]
            expected_rows = expected["predictions"]
            if len(rows) != len(expected_rows):
                return False
            keys = ("rt_q10_min", "rt_q50_min", "rt_q90_min")
            return all(
                actual["smiles"] == wanted["smiles"]
                and all(abs(float(actual[key]) - float(wanted[key])) <= GC_GOLDEN_ABS_TOLERANCE for key in keys)
                for actual, wanted in zip(rows, expected_rows)
            )
        except (KeyError, TypeError, ValueError):
            return False


__all__ = ["GcAssetUnavailable", "GcModelRoot", "GcRuntime", "GcRuntimeStatus", "GcVerificationReport"]
