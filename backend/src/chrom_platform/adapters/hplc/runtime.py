"""Controlled runtime for the audited v1.1 HPLC GeoGNN bundle.

Only the reviewed ``runtime.py`` scientific implementation is imported, and
only after every source and binary hash has been checked inside the trusted
artifact root.  The old HTTP entrypoint and its path-based request options are
not used.
"""

from __future__ import annotations

import hashlib
import importlib.util
import importlib
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from chrom_platform.modules.prediction.adapters import VerifiedModelHandle

from .manifest import (
    HPLC_ARTIFACT_ID,
    HPLC_ASSET_MANIFEST_SHA256,
    HPLC_BUSINESS_SCOPE_ID,
    HPLC_COLUMN_NAMES,
    HPLC_COLUMN_DESCRIPTOR_SHA256,
    HPLC_MODEL_SHA256,
    HPLC_MODEL_VERSION,
    HPLC_MULTI_COLUMN_SOURCE_SHA256,
    HPLC_GOLDEN_COLUMNS,
    HPLC_GOLDEN_EXPECTED,
    HPLC_GOLDEN_FLOW_RATE_ML_MIN,
    HPLC_GOLDEN_IPA_FRACTION,
    HPLC_GOLDEN_RECOMMENDED_COLUMN,
    HPLC_GOLDEN_SMILES,
    HPLC_PREDICT_SOURCE_SHA256,
    HPLC_RUNTIME_SOURCE_SHA256,
    HplcArtifactManifest,
)
from .serving_profile import HPLC_SERVING_PROFILE_ID, HPLC_SERVING_PROFILE_SHA256


class HplcAssetUnavailable(RuntimeError):
    """Raised when a verified HPLC artifact cannot be executed."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class HplcRuntimeStatus:
    status: str
    reason: str | None = None
    checked_assets: bool = False
    dependencies_available: bool = False
    schema_verified: bool = False


@dataclass(frozen=True)
class HplcLoadAudit:
    checkpoint_key_count: int
    loaded_keys: tuple[str, ...]
    shape_mappings: tuple[tuple[str, tuple[int, ...], tuple[int, ...], int], ...]
    missing_keys: tuple[str, ...]
    unexpected_keys: tuple[str, ...]


@dataclass(frozen=True)
class HplcVerificationReport:
    manifest_verified: bool
    schema_verified: bool
    artifact_sha_verified: bool
    environment_verified: bool
    golden_sample_verified: bool
    anomaly_checks_verified: bool


@dataclass(frozen=True)
class HplcModelRoot:
    path: Path
    registry_root: Path

    @classmethod
    def from_registered_path(
        cls, *, registry_root: str | Path, model_root: str | Path
    ) -> "HplcModelRoot":
        registry = Path(registry_root).expanduser().resolve(strict=True)
        root = Path(model_root).expanduser().resolve(strict=True)
        if not registry.is_dir():
            raise ValueError("HPLC registry root must be a directory")
        try:
            root.relative_to(registry)
        except ValueError as exc:
            raise ValueError("HPLC model root must be inside the trusted registry root") from exc
        if not root.is_dir():
            raise ValueError("HPLC model root must be a directory")
        return cls(path=root, registry_root=registry)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _has_dependencies() -> tuple[bool, str | None]:
    names = ("numpy", "torch", "torch_geometric", "rdkit", "mordred")
    missing: list[str] = []
    for name in names:
        try:
            if importlib.util.find_spec(name) is None:
                missing.append(name)
        except (ImportError, ModuleNotFoundError):
            missing.append(name)
    return (not missing, None if not missing else "missing dependencies: " + ", ".join(missing))


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


class HplcRuntime:
    """A runtime fixed to one model, one scope, and CPU execution."""

    def __init__(
        self,
        *,
        model_root: HplcModelRoot,
        manifest: HplcArtifactManifest | None = None,
        bound_model_id: str = HPLC_ARTIFACT_ID,
        bound_model_version: str = HPLC_MODEL_VERSION,
        business_scope_id: str = HPLC_BUSINESS_SCOPE_ID,
    ) -> None:
        self.model_root = model_root
        self.manifest = manifest or HplcArtifactManifest(root=model_root.path)
        if self.manifest.root is not None and Path(self.manifest.root).resolve() != model_root.path:
            raise ValueError("HPLC artifact manifest root does not match trusted model root")
        if (
            self.manifest.artifact_id != HPLC_ARTIFACT_ID
            or self.manifest.model_version != HPLC_MODEL_VERSION
            or self.manifest.model_sha256.lower() != HPLC_MODEL_SHA256
            or self.manifest.column_descriptor_sha256.lower() != HPLC_COLUMN_DESCRIPTOR_SHA256
            or self.manifest.asset_manifest_sha256.lower() != HPLC_ASSET_MANIFEST_SHA256
            or self.manifest.runtime_source_sha256.lower() != HPLC_RUNTIME_SOURCE_SHA256
            or self.manifest.predict_source_sha256.lower() != HPLC_PREDICT_SOURCE_SHA256
            or self.manifest.multi_column_source_sha256.lower() != HPLC_MULTI_COLUMN_SOURCE_SHA256
        ):
            raise ValueError("HPLC artifact manifest does not bind the audited source and model")
        if bound_model_id != self.manifest.artifact_id or bound_model_version != self.manifest.model_version:
            raise ValueError("HPLC runtime model binding does not match manifest")
        if business_scope_id != HPLC_BUSINESS_SCOPE_ID:
            raise ValueError("HPLC runtime business scope is fixed")
        self.bound_model_id = bound_model_id
        self.bound_model_version = bound_model_version
        self.business_scope_id = business_scope_id
        self.bound_artifact_sha256 = self.manifest.model_sha256.lower()
        self._author_module: Any | None = None
        self._model: Any | None = None
        self._column_descriptor: Any | None = None
        self._verified_handle: VerifiedModelHandle | None = None
        self.load_audit: HplcLoadAudit | None = None
        self.status = self._check_artifact()

    @classmethod
    def from_registered_artifact(
        cls,
        *,
        registry_root: str | Path,
        model_root: str | Path,
        artifact_id: str = HPLC_ARTIFACT_ID,
        model_version: str = HPLC_MODEL_VERSION,
        business_scope_id: str = HPLC_BUSINESS_SCOPE_ID,
    ) -> "HplcRuntime":
        if artifact_id != HPLC_ARTIFACT_ID or model_version != HPLC_MODEL_VERSION:
            raise ValueError("unknown HPLC artifact binding")
        trusted = HplcModelRoot.from_registered_path(registry_root=registry_root, model_root=model_root)
        return cls(
            model_root=trusted,
            manifest=HplcArtifactManifest(root=trusted.path),
            bound_model_id=artifact_id,
            bound_model_version=model_version,
            business_scope_id=business_scope_id,
        )

    def _path(self, relative: str) -> Path:
        path = (self.model_root.path / relative).resolve()
        if not _inside(path, self.model_root.path):
            raise HplcAssetUnavailable("HPLC_ASSET_OUTSIDE_ROOT")
        return path

    def _check_artifact(self) -> HplcRuntimeStatus:
        dependencies_ok, dependency_reason = _has_dependencies()
        paths = {
            "model": self._path(r"application\GeoGNN_model.pth"),
            "column_descriptor": self._path(r"application\column_descriptor.npy"),
            "asset_manifest": self._path("MODEL_SHA256.txt"),
            "predict_source": self._path("predict.py"),
            "runtime_source": self._path("runtime.py"),
            "multi_column_source": self._path(r"application\Multi_column_prediction.py"),
        }
        expected = self.manifest.expected_assets()
        for key, path in paths.items():
            if not path.is_file():
                return HplcRuntimeStatus("UNAVAILABLE", f"missing HPLC asset: {path.name}", dependencies_available=dependencies_ok)
            if _sha256(path) != expected[key]:
                return HplcRuntimeStatus("UNAVAILABLE", f"HPLC asset SHA-256 mismatch: {path.name}", dependencies_available=dependencies_ok)
        if not dependencies_ok:
            return HplcRuntimeStatus("UNAVAILABLE", dependency_reason, checked_assets=True, dependencies_available=False)

        # The manifest is an auditable record, not a source of trust.  Its
        # digest and both binary records were checked independently above.
        text = paths["asset_manifest"].read_text(encoding="utf-8").lower()
        if HPLC_MODEL_SHA256 not in text or HPLC_COLUMN_DESCRIPTOR_SHA256 not in text:
            return HplcRuntimeStatus("UNAVAILABLE", "HPLC asset manifest contents mismatch", checked_assets=True, dependencies_available=True)
        try:
            import numpy as np

            descriptor = np.load(paths["column_descriptor"], allow_pickle=False)
            if tuple(descriptor.shape) != (25, 1826) or not np.isfinite(descriptor).all():
                return HplcRuntimeStatus("UNAVAILABLE", "HPLC column descriptor schema mismatch", checked_assets=True, dependencies_available=True)
        except Exception as exc:
            return HplcRuntimeStatus("UNAVAILABLE", "HPLC column descriptor cannot be read", checked_assets=True, dependencies_available=True)
        return HplcRuntimeStatus(
            "UNAVAILABLE",
            "verification evidence required before execution",
            checked_assets=True,
            dependencies_available=True,
            schema_verified=True,
        )

    def bind_verified_handle(self, handle: VerifiedModelHandle) -> None:
        """Bind the central verification-port handle; no local bypass exists."""

        if not isinstance(handle, VerifiedModelHandle):
            raise TypeError("HPLC runtime requires a central VerifiedModelHandle")

        expected = (
            (handle.adapter_id, "hplc"),
            (handle.model_id, self.bound_model_id),
            (handle.model_version, self.bound_model_version),
            (handle.business_scope_id, self.business_scope_id),
            (handle.input_schema_version, "1.1"),
            (handle.output_schema_version, "hplc-geognn-1"),
            (handle.artifact_sha256, self.bound_artifact_sha256),
        )
        expected = (*expected, (handle.preprocessing_profile_id, HPLC_SERVING_PROFILE_ID),
                    (handle.preprocessing_profile_sha256, HPLC_SERVING_PROFILE_SHA256))
        if any(left != right for left, right in expected) or handle.schema_for("single") is None:
            raise HplcAssetUnavailable("central HPLC verification handle does not bind this artifact")
        if not (self.status.checked_assets and self.status.dependencies_available and self.status.schema_verified):
            raise HplcAssetUnavailable(self.status.reason or "HPLC artifact unavailable")
        self.status = HplcRuntimeStatus("READY", checked_assets=True, dependencies_available=True, schema_verified=True)
        self._verified_handle = handle

    def _require_ready(self) -> None:
        if self.status.status != "READY":
            raise HplcAssetUnavailable(self.status.reason or "HPLC model unavailable")

    def _load_author_module(self, *, for_verification: bool = False) -> Any:
        if self._author_module is not None:
            return self._author_module
        if for_verification:
            self._require_verifiable_assets()
        else:
            self._require_ready()
        source = self._path("runtime.py")
        name = f"_chrom_platform_hplc_author_runtime_{id(self)}"
        spec = importlib.util.spec_from_file_location(name, source)
        if spec is None or spec.loader is None:
            raise HplcAssetUnavailable("HPLC_AUTHOR_RUNTIME_INVALID")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            sys.modules.pop(name, None)
            raise HplcAssetUnavailable("HPLC_AUTHOR_RUNTIME_IMPORT_FAILED") from exc
        if not callable(getattr(module, "GINGraphPooling", None)) or not callable(getattr(module, "hplc_predict_with_geognn", None)):
            raise HplcAssetUnavailable("HPLC_AUTHOR_RUNTIME_API_UNSUPPORTED")
        self._author_module = module
        return module

    def _require_verifiable_assets(self) -> None:
        if not (self.status.checked_assets and self.status.dependencies_available and self.status.schema_verified):
            raise HplcAssetUnavailable(self.status.reason or "HPLC artifact unavailable")

    def _load_assets(self, *, for_verification: bool = False) -> tuple[Any, Any]:
        if for_verification:
            self._require_verifiable_assets()
        else:
            self._require_ready()
        if self._model is not None and self._column_descriptor is not None:
            return self._model, self._column_descriptor
        try:
            import numpy as np
            import torch
        except ImportError as exc:
            raise HplcAssetUnavailable("SCIENTIFIC_DEPENDENCIES_UNAVAILABLE") from exc
        author = self._load_author_module(for_verification=for_verification)
        model = author.GINGraphPooling(
            num_tasks=3, num_layers=5, emb_dim=128,
            drop_ratio=0, graph_pooling="sum", descriptor_dim=1827,
        ).to(torch.device("cpu"))
        model_path = self._path(r"application\GeoGNN_model.pth")
        try:
            from chrom_platform.core.model_load_observation import record_model_load

            record_model_load("hplc", self.bound_model_id)
            state_dict = torch.load(model_path, map_location="cpu", weights_only=True)
        except TypeError as exc:
            raise HplcAssetUnavailable("WEIGHTS_ONLY_UNSUPPORTED") from exc
        if not isinstance(state_dict, Mapping):
            raise HplcAssetUnavailable("HPLC_MODEL_STATE_INVALID")
        state_dict = dict(state_dict)
        checkpoint_key_count = len(state_dict)
        model_state = model.state_dict()
        expected_shape_mappings = {
            "gnn_node.atom_encoder.atom_embedding_list.1.weight": ((9, 128), (14, 128)),
            "gnn_node.atom_encoder.atom_embedding_list.5.weight": ((13, 128), (14, 128)),
        }
        shape_mappings: list[tuple[str, tuple[int, ...], tuple[int, ...], int]] = []
        for name in list(state_dict):
            checkpoint_value = state_dict[name]
            model_value = model_state.get(name)
            if model_value is not None and tuple(model_value.shape) != tuple(checkpoint_value.shape):
                source_shape = tuple(checkpoint_value.shape)
                target_shape = tuple(model_value.shape)
                if expected_shape_mappings.get(name) != (source_shape, target_shape):
                    raise HplcAssetUnavailable("HPLC_MODEL_STATE_SHAPE_MISMATCH")
                slices = tuple(slice(0, min(model_size, checkpoint_size)) for model_size, checkpoint_size in zip(model_value.shape, checkpoint_value.shape))
                model_value[slices] = checkpoint_value[slices]
                shape_mappings.append((name, source_shape, target_shape, min(source_shape[0], target_shape[0])))
                del state_dict[name]
        if {item[0] for item in shape_mappings} != set(expected_shape_mappings):
            raise HplcAssetUnavailable("HPLC_MODEL_STATE_SHAPE_MISMATCH")
        allowed_rbf_missing = {
            name for name in model_state
            if ".rbf_list." in name and name.rsplit(".", 1)[-1] in {"centers", "gamma"}
        }
        # The audited author runtime registers these deterministic RBF tensors
        # as buffers; they are absent from the checkpoint state_dict.
        if len(allowed_rbf_missing) != 48:
            raise HplcAssetUnavailable("HPLC_MODEL_STATE_API_MISMATCH")
        allowed_missing = allowed_rbf_missing | set(expected_shape_mappings)
        loaded_keys = tuple(sorted(state_dict))
        incompatible = model.load_state_dict(state_dict, strict=False)
        missing = set(incompatible.missing_keys)
        unexpected = set(incompatible.unexpected_keys)
        if unexpected or missing != allowed_missing:
            raise HplcAssetUnavailable("HPLC_MODEL_STATE_API_MISMATCH")
        self.load_audit = HplcLoadAudit(
            checkpoint_key_count=checkpoint_key_count,
            loaded_keys=loaded_keys,
            shape_mappings=tuple(sorted(shape_mappings)),
            missing_keys=tuple(sorted(missing)),
            unexpected_keys=tuple(sorted(unexpected)),
        )
        model.eval()
        descriptor = np.load(self._path(r"application\column_descriptor.npy"), allow_pickle=False)
        if tuple(descriptor.shape) != (25, 1826) or not np.isfinite(descriptor).all():
            raise HplcAssetUnavailable("HPLC_COLUMN_DESCRIPTOR_SCHEMA_MISMATCH")
        self._model, self._column_descriptor = model, descriptor
        return model, descriptor

    def load(self) -> tuple[Any, Any]:
        """Load the model only after a central handle has been bound."""

        return self._load_assets()

    def verify_author_demo(self) -> HplcVerificationReport:
        """Run the bundle-owned demo twice before the central handle is issued."""

        manifest_ok = self.status.checked_assets
        schema_ok = self.status.schema_verified
        artifact_ok = manifest_ok and self.bound_artifact_sha256 == HPLC_MODEL_SHA256
        if not (manifest_ok and schema_ok and self.status.dependencies_available):
            return HplcVerificationReport(manifest_ok, schema_ok, artifact_ok, False, False, False)
        try:
            import torch

            environment_ok = torch.__version__ == "2.6.0+cu124"
            if not environment_ok:
                return HplcVerificationReport(manifest_ok, schema_ok, artifact_ok, False, False, False)
            # Keep the original author raw-input golden pair. Canonicalizing
            # it would test a different, traversal-sensitive model input.
            golden_smiles = HPLC_GOLDEN_SMILES
            model, descriptor = self._load_assets(for_verification=True)
            author = self._load_author_module(for_verification=True)
            outputs = []
            for _ in range(2):
                raw = author.hplc_predict_with_geognn(
                    model, descriptor, ",".join(golden_smiles),
                    HPLC_GOLDEN_IPA_FRACTION, HPLC_GOLDEN_FLOW_RATE_ML_MIN,
                    list(HPLC_GOLDEN_COLUMNS), device="cpu", return_quantiles=True,
                )
                self._validate_output(HPLC_GOLDEN_COLUMNS, *raw)
                outputs.append(raw)
            first, second = outputs
            anomaly_ok = (
                len(first[0]) == len(HPLC_GOLDEN_COLUMNS)
                and all(0.0 <= float(value) <= 1.0 for value in first[0])
                and all(float(value) >= 0.0 for pair in first[2] for quantiles in pair for value in quantiles)
                and all(float(value) >= 0.0 for pair in first[1] for value in pair)
                and self.load_audit is not None
                and self.load_audit.checkpoint_key_count == 255
                and len(self.load_audit.loaded_keys) == 253
                and len(self.load_audit.shape_mappings) == 2
                and len(self.load_audit.missing_keys) == 50
                and not self.load_audit.unexpected_keys
            )

            def numeric_values(value: Any):
                if isinstance(value, (list, tuple)):
                    for item in value:
                        yield from numeric_values(item)
                else:
                    yield float(value)

            first_values = tuple(numeric_values(first))
            second_values = tuple(numeric_values(second))
            repeat_ok = all(
                abs(left - right) <= 1e-12
                for left, right in zip(first_values, second_values)
            )
            golden_ok = repeat_ok and len(first_values) == len(second_values) and len(first[0]) == len(HPLC_GOLDEN_EXPECTED)
            if golden_ok:
                for index, (column, expected_sp, expected_pairs) in enumerate(HPLC_GOLDEN_EXPECTED):
                    golden_ok = golden_ok and all((
                        column == HPLC_GOLDEN_COLUMNS[index],
                        abs(float(first[0][index]) - expected_sp) <= 5e-6,
                        all(
                            abs(float(first[2][index][compound][quantile]) - expected_pairs[compound][quantile]) <= 5e-6
                            for compound in range(2) for quantile in range(3)
                        ),
                        all(
                            abs(float(first[1][index][compound]) - expected_pairs[compound][1]) <= 5e-6
                            for compound in range(2)
                        ),
                    ))
                recommended_column = HPLC_GOLDEN_COLUMNS[
                    max(range(len(first[0])), key=lambda index: float(first[0][index]))
                ]
                golden_ok = golden_ok and recommended_column == HPLC_GOLDEN_RECOMMENDED_COLUMN
            return HplcVerificationReport(
                manifest_verified=manifest_ok,
                schema_verified=schema_ok,
                artifact_sha_verified=artifact_ok,
                environment_verified=environment_ok,
                golden_sample_verified=golden_ok,
                anomaly_checks_verified=anomaly_ok,
            )
        except Exception:
            return HplcVerificationReport(manifest_ok, schema_ok, artifact_ok, False, False, False)

    @staticmethod
    def _validate_output(columns: Sequence[str], sp_list: Any, rt_list: Any, quantiles: Any) -> None:
        try:
            n = len(columns)
            if len(sp_list) != n or len(rt_list) != n or len(quantiles) != n:
                raise ValueError
            for index in range(n):
                sp = float(sp_list[index])
                if not _finite(sp) or not 0.0 <= sp <= 1.0:
                    raise ValueError
                pair = rt_list[index]
                q = quantiles[index]
                if len(pair) != 2 or len(q) != 2:
                    raise ValueError
                for compound in range(2):
                    if len(q[compound]) != 3:
                        raise ValueError
                    values = [float(value) for value in q[compound]]
                    if not all(_finite(value) and value >= 0.0 for value in values):
                        raise ValueError
                    if values != sorted(values):
                        raise ValueError
                    if not _finite(pair[compound]) or abs(float(pair[compound]) - values[1]) > 1e-5:
                        raise ValueError
        except (TypeError, ValueError, IndexError, AttributeError) as exc:
            raise HplcAssetUnavailable("HPLC_MODEL_OUTPUT_INVALID") from exc

    def predict(
        self,
        *,
        model_input_smiles: Sequence[str],
        ipa_fraction: float,
        flow_rate_ml_min: float,
        columns: Sequence[str],
    ) -> dict[str, Any]:
        """Run the real GeoGNN and return ordered quantiles for each column."""

        self._require_ready()
        if len(model_input_smiles) != 2 or any(not isinstance(value, str) or not value.strip() for value in model_input_smiles):
            raise ValueError("HPLC requires exactly two nonblank raw SMILES")
        if len(columns) == 0 or len(set(columns)) != len(columns) or any(column not in HPLC_COLUMN_NAMES for column in columns):
            raise ValueError("HPLC columns are unsupported or duplicated")
        try:
            ipa = float(ipa_fraction)
            flow = float(flow_rate_ml_min)
        except (TypeError, ValueError) as exc:
            raise ValueError("HPLC conditions must be numeric") from exc
        if not _finite(ipa) or not 0.0 < ipa < 1.0:
            raise ValueError("ipa_fraction must be finite and strictly between 0 and 1")
        if not _finite(flow) or flow <= 0.0:
            raise ValueError("flow_rate_ml_min must be finite and positive")
        model, descriptor = self.load()
        author = self._load_author_module()
        try:
            sp_list, rt_list, quantiles = author.hplc_predict_with_geognn(
                model, descriptor, ",".join(model_input_smiles), ipa, flow,
                list(columns), device="cpu", return_quantiles=True,
            )
        except (ImportError, RuntimeError, ValueError) as exc:
            raise HplcAssetUnavailable("HPLC_INFERENCE_FAILED") from exc
        self._validate_output(columns, sp_list, rt_list, quantiles)
        result_columns = []
        for column, sp, median_pair, pair_quantiles in zip(columns, sp_list, rt_list, quantiles):
            result_columns.append({
                "column": column,
                "separation_probability": float(sp),
                "separable_at_sp_0_38": bool(float(sp) > 0.38),
                "compounds": [
                    {
                        "smiles": str(model_input_smiles[index]),
                        "rt_q10_min": float(pair_quantiles[index][0]),
                        "rt_q50_min": float(pair_quantiles[index][1]),
                        "rt_q90_min": float(pair_quantiles[index][2]),
                    }
                    for index in range(2)
                ],
                "median_rt_min": [float(median_pair[0]), float(median_pair[1])],
            })
        best_index = max(range(len(result_columns)), key=lambda index: result_columns[index]["separation_probability"])
        return {
            "success": True,
            "smiles": [str(value) for value in model_input_smiles],
            "ipa_fraction": ipa,
            "flow_rate_ml_min": flow,
            "columns": result_columns,
            "recommended_column": result_columns[best_index]["column"],
            "model_name": "QGeoGNN GeoGNN_model.pth",
            "artifact_sha256": self.bound_artifact_sha256,
            "warning": "该模型训练于论文 CMRT 条件；陌生分子、溶剂或实验系统的结果需实验确认。",
        }


__all__ = [
    "HplcAssetUnavailable", "HplcLoadAudit", "HplcModelRoot", "HplcRuntime",
    "HplcRuntimeStatus", "HplcVerificationReport",
]
