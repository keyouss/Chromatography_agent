"""Fixed child-only GC safetensors Loader.

The loader uses the reviewed GC source/runtime pins and fixed preprocessing
code, while keeping the uploaded package hash separate from the historical PTH
identity and its serving receipts.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from chrom_platform.artifact_core.model_package_manifest import (
    FIXED_FORMAT,
    FIXED_LOADER_ID,
    InspectedPackage,
    PackageValidationError,
)
from chrom_platform.adapters.gc.manifest import (
    GC_ARTIFACT_ID,
    GC_AUTHOR_INPUT_SCHEMA_VERSION,
    GC_BUSINESS_SCOPE_ID,
    GC_INPUT_SCHEMA_VERSION,
    GC_MODEL_SHA256,
    GC_MODEL_VERSION,
    GC_OUTPUT_SCHEMA_VERSION,
    GC_PREPROCESSING_PROTOCOL_ID,
    GC_PREPROCESSING_PROTOCOL_QA_APPROVED,
    GC_RDKIT_EMBED_PARAMETERS,
    GC_RDKIT_MMFF_PARAMETERS,
    GC_SOURCE_ROOT,
    GcArtifactManifest,
)
from chrom_platform.adapters.gc.runtime import (
    GC_GOLDEN_ABS_TOLERANCE,
    GcAssetUnavailable,
    GcModelRoot,
    GcRuntime,
    _default_gc_fixture_path,
)
from chrom_platform.adapters.gc.package_model import (
    build_gc_package_model,
    load_gc_package_state_dict,
)
from chrom_platform.adapters.gc.package_source_pins import PACKAGE_SOURCE_SHA256
from chrom_platform.adapters.gc.serving_profile import (
    GC_REFERENCE_FIXTURE_SHA256,
    GC_SERVING_PROFILE_ID,
    GC_SERVING_PROFILE_SHA256,
)
from chrom_platform.modules.prediction.schemas import (
    GcPredictionCreate,
    GcPredictionResult,
    GcSeriesTemperatureProgram,
)


GC_PACKAGE_TASK_TYPE = "gc"
GC_PACKAGE_SCOPE_ID = GC_BUSINESS_SCOPE_ID
GC_PACKAGE_VERSION = GC_MODEL_VERSION
GC_PACKAGE_INPUT_SCHEMA_VERSION = GC_INPUT_SCHEMA_VERSION
GC_PACKAGE_OUTPUT_SCHEMA_VERSION = GC_OUTPUT_SCHEMA_VERSION
GC_PACKAGE_PREPROCESSING_PROTOCOL_ID = GC_PREPROCESSING_PROTOCOL_ID


class _PackageVerificationRuntime(GcRuntime):
    """Fresh child-local runtime which can only predict in verification mode."""

    def __init__(self, *, trusted_root: str | Path, package_model: Any) -> None:
        root = GcModelRoot.from_registered_path(
            registry_root=trusted_root,
            model_root=trusted_root,
        )
        super().__init__(
            model_root=root,
            manifest=GcArtifactManifest(root=root.path),
            business_scope_id=GC_BUSINESS_SCOPE_ID,
        )
        self._package_model = package_model

    def _load_model(self, *, for_verification: bool) -> Any:
        if not for_verification:
            raise GcAssetUnavailable("MODEL_VERIFICATION_REQUIRED")
        return self._package_model


class GcSafetensorsPackageLoader:
    """Only recognizes the one build-registered GC package identity."""

    loader_id = FIXED_LOADER_ID
    format = FIXED_FORMAT

    def __init__(self, *, trusted_root: str | Path = GC_SOURCE_ROOT) -> None:
        self._trusted_root = Path(trusted_root)

    @classmethod
    def validate_manifest(cls, manifest: Mapping[str, Any]) -> None:
        expected = {
            "schema_version": "1.0",
            "version": GC_PACKAGE_VERSION,
            "input_schema_version": GC_PACKAGE_INPUT_SCHEMA_VERSION,
            "output_schema_version": GC_PACKAGE_OUTPUT_SCHEMA_VERSION,
            "task_type": GC_PACKAGE_TASK_TYPE,
            "scope_ids": [GC_PACKAGE_SCOPE_ID],
            "loader_id": cls.loader_id,
            "format": cls.format,
            "preprocessing_protocol_id": GC_PACKAGE_PREPROCESSING_PROTOCOL_ID,
        }
        if any(manifest.get(key) != value for key, value in expected.items()):
            raise PackageValidationError("GC_PACKAGE_CONTRACT_MISMATCH")

    @staticmethod
    def verify_integrity() -> None:
        source_root = Path(__file__).resolve().parents[3]
        try:
            for relative, expected_sha256 in PACKAGE_SOURCE_SHA256.items():
                path = source_root.joinpath(*Path(relative).parts)
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if digest != expected_sha256:
                    raise PackageValidationError("PACKAGE_SOURCE_INTEGRITY_MISMATCH")
        except OSError as exc:
            raise PackageValidationError("PACKAGE_SOURCE_INTEGRITY_UNAVAILABLE") from exc

    def verify(
        self,
        package: InspectedPackage,
        *,
        expected_container_sha256: str,
        expected_size_bytes: int,
    ) -> Mapping[str, Any]:
        """Load data-only state in this child and compare server golden output."""
        self.validate_manifest(package.manifest)
        if (package.container_sha256 != expected_container_sha256
                or package.container_size_bytes != expected_size_bytes):
            raise PackageValidationError("PACKAGE_CONTAINER_HASH_MISMATCH")

        trusted_manifest = GcArtifactManifest(root=self._trusted_root)
        runtime = _PackageVerificationRuntime(trusted_root=self._trusted_root, package_model=None)
        if runtime.status.status != "READY":
            raise PackageValidationError(runtime.status.reason or "GC_PACKAGE_RUNTIME_UNAVAILABLE")
        source_modules = runtime._load_author_modules()
        try:
            import torch
            from safetensors.torch import load as load_safetensors
        except ImportError as exc:
            raise PackageValidationError("MODEL_RUNTIME_DEPENDENCY_UNAVAILABLE") from exc
        try:
            state = load_safetensors(package.weights_bytes)
        except Exception as exc:
            raise PackageValidationError("SAFETENSORS_LOAD_FAILED") from exc
        if not isinstance(state, Mapping) or any(
            not isinstance(value, torch.Tensor) or value.device.type != "cpu"
            for value in state.values()
        ):
            raise PackageValidationError("SAFETENSORS_STATE_INVALID")
        model = build_gc_package_model(source_modules, torch)
        expected_state = model.state_dict()
        allowed_missing = {
            name for name in expected_state if name.endswith(".rbf_list.0.gamma")
        }
        if (set(state) - set(expected_state)
                or set(expected_state) - set(state) != allowed_missing):
            raise PackageValidationError("SAFETENSORS_STATE_KEYS_INVALID")
        for name, tensor in state.items():
            expected_tensor = expected_state[name]
            if tensor.shape != expected_tensor.shape or tensor.dtype != expected_tensor.dtype:
                raise PackageValidationError("SAFETENSORS_STATE_SCHEMA_INVALID")
            if tensor.is_floating_point() and not bool(torch.isfinite(tensor).all().item()):
                raise PackageValidationError("SAFETENSORS_STATE_NONFINITE")
        model = load_gc_package_state_dict(model, state)
        runtime._package_model = model
        fixture, fixture_status = self._load_server_fixture(runtime, trusted_manifest)
        if fixture is None:
            return self._report(
                package, expected_container_sha256,
                dependency_passed=True, schema_passed=False, load_passed=True,
                golden_passed=False, finite_passed=False,
                error_code=fixture_status,
            )
        sample = fixture["input"]
        expected = fixture["expected_raw"]
        smiles = sample["smiles"]
        temperatures = self._temperature_series(sample)
        validated = GcPredictionCreate.model_validate({
            "project_id": "gc-package-verification",
            "chromatography_type": "gc",
            "tool": "single",
            "schema_version": GC_INPUT_SCHEMA_VERSION,
            "model_selection": {
                "mode": "exact", "model_id": GC_ARTIFACT_ID,
                "model_version": GC_MODEL_VERSION,
                "preprocessing_profile_id": GC_SERVING_PROFILE_ID,
                "preprocessing_profile_sha256": GC_SERVING_PROFILE_SHA256,
            },
            "structure_refs": ["gc-package-golden-structure"],
            "input": {"temperature_program": {
                "mode": "series", "temperature_series_c": temperatures,
            }},
        })
        schema_passed = (
            len(validated.structure_refs) == 1
            and len(validated.input.temperature_program.temperature_series_c) == 30
            and package.manifest.get("input_schema_version") == GC_INPUT_SCHEMA_VERSION
            and package.manifest.get("output_schema_version") == GC_OUTPUT_SCHEMA_VERSION
            and self._schema_contract_valid()
        )
        first = runtime._predict(normalized_smiles=smiles, temperature_series_c=temperatures, for_verification=True)
        second = runtime._predict(normalized_smiles=smiles, temperature_series_c=temperatures, for_verification=True)
        finite_passed = GcRuntime._valid_model_output(first, smiles)
        measured_golden = (
            first == second
            and finite_passed
            and first.get("temperature_series_c") == temperatures
            and temperatures == [float(value) for value in expected["temperature_series_c"]]
            and GcRuntime._matches_golden(first, expected)
        )
        golden_passed = measured_golden and GC_PREPROCESSING_PROTOCOL_QA_APPROVED
        error_code = None if all((schema_passed, golden_passed, finite_passed)) else (
            "GC_PREPROCESSING_PROTOCOL_UNAPPROVED" if measured_golden and not GC_PREPROCESSING_PROTOCOL_QA_APPROVED
            else "GC_PACKAGE_GOLDEN_OR_SCHEMA_FAILED"
        )
        return self._report(
            package, expected_container_sha256,
            dependency_passed=runtime.status.dependencies_available,
            schema_passed=schema_passed,
            load_passed=True,
            golden_passed=golden_passed,
            finite_passed=finite_passed,
            error_code=error_code,
        )

    @staticmethod
    def _temperature_series(sample: Mapping[str, Any]) -> list[float]:
        return [
            float(sample["initial_temperature_c"])
            if minute < int(sample["initial_hold_min"])
            else min(
                float(sample["final_temperature_c"]),
                float(sample["initial_temperature_c"])
                + (minute - int(sample["initial_hold_min"])) * float(sample["heating_rate_c_per_min"]),
            )
            for minute in range(30)
        ]

    @staticmethod
    def _schema_contract_valid() -> bool:
        input_schema = GcPredictionCreate.model_json_schema()
        output_schema = GcPredictionResult.model_json_schema()
        definitions = input_schema.get("$defs", {})
        series = definitions.get("GcSeriesTemperatureProgram", {})
        items = series.get("properties", {}).get("temperature_series_c", {}) if isinstance(series, Mapping) else {}
        linear = definitions.get("GcLinearTemperatureProgram", {})
        hold = linear.get("properties", {}).get("initial_hold_min", {}) if isinstance(linear, Mapping) else {}
        narrowed_schema = deepcopy(input_schema)
        narrowed_definitions = narrowed_schema.get("$defs", {})
        narrowed_series = narrowed_definitions.get("GcSeriesTemperatureProgram", {})
        narrowed_items = narrowed_series.get("properties", {}).get("temperature_series_c", {}) if isinstance(narrowed_series, Mapping) else {}
        narrowed_linear = narrowed_definitions.get("GcLinearTemperatureProgram", {})
        narrowed_hold = narrowed_linear.get("properties", {}).get("initial_hold_min", {}) if isinstance(narrowed_linear, Mapping) else {}
        if isinstance(narrowed_items, dict):
            narrowed_items["minItems"] = 30
            narrowed_items["maxItems"] = 30
        if isinstance(narrowed_hold, dict):
            narrowed_hold["multipleOf"] = 1
        return (
            set(definitions) >= {"GcLinearTemperatureProgram", "GcSeriesTemperatureProgram"}
            and isinstance(items, Mapping)
            and items.get("minItems") == 1
            and items.get("maxItems") == 10_000
            and isinstance(hold, Mapping)
            and hold.get("minimum") == 0.0
            and "multipleOf" not in hold
            and isinstance(narrowed_items, Mapping)
            and narrowed_items.get("minItems") == 30
            and narrowed_items.get("maxItems") == 30
            and isinstance(narrowed_hold, Mapping)
            and narrowed_hold.get("multipleOf") == 1
            and GcSeriesTemperatureProgram.model_fields["temperature_series_c"].annotation == list[float]
            and output_schema.get("title") == "GcPredictionResult"
        )

    @staticmethod
    def _load_server_fixture(
        runtime: GcRuntime,
        trusted_manifest: GcArtifactManifest,
    ) -> tuple[Mapping[str, Any] | None, str | None]:
        path = _default_gc_fixture_path().resolve(strict=True)
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != GC_REFERENCE_FIXTURE_SHA256:
            return None, "GC_REFERENCE_FIXTURE_HASH_MISMATCH"
        try:
            fixture = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None, "GC_REFERENCE_FIXTURE_INVALID"
        if not isinstance(fixture, Mapping):
            return None, "GC_REFERENCE_FIXTURE_INVALID"
        model_meta = fixture.get("model")
        protocol = fixture.get("preprocessing_protocol")
        if not isinstance(model_meta, Mapping) or not isinstance(protocol, Mapping):
            return None, "GC_REFERENCE_FIXTURE_INVALID"
        embed = dict(GC_RDKIT_EMBED_PARAMETERS)
        embed["coordMap"] = {}
        if (
            fixture.get("component") != "gc"
            or model_meta.get("model_id") != GC_ARTIFACT_ID
            or model_meta.get("model_version") != GC_MODEL_VERSION
            or model_meta.get("preprocessing_protocol_id") != GC_PREPROCESSING_PROTOCOL_ID
            or model_meta.get("input_schema_version") != GC_AUTHOR_INPUT_SCHEMA_VERSION
            or model_meta.get("output_schema_version") != GC_OUTPUT_SCHEMA_VERSION
            or str(model_meta.get("model_asset_sha256", "")).lower() != GC_MODEL_SHA256
            or str(model_meta.get("registry_sha256", "")).lower() != trusted_manifest.expected_files()["MODEL_SHA256.txt"]
            or str(model_meta.get("source_sha256", "")).lower() != trusted_manifest.expected_files()["predict.py"]
            or protocol.get("id") != GC_PREPROCESSING_PROTOCOL_ID
            or protocol.get("random_seed") != 42
            or protocol.get("embed_multiple_confs_parameters") != embed
            or protocol.get("mmff_optimize_molecule_confs_parameters") != GC_RDKIT_MMFF_PARAMETERS
            or not isinstance(protocol.get("environment"), Mapping)
            or dict(protocol["environment"]) != runtime._current_environment()
        ):
            return None, "GC_REFERENCE_FIXTURE_CONTRACT_INVALID"
        return fixture, None

    @staticmethod
    def _report(
        package: InspectedPackage,
        artifact_sha256: str,
        *,
        dependency_passed: bool,
        schema_passed: bool,
        load_passed: bool,
        golden_passed: bool,
        finite_passed: bool,
        error_code: str | None,
    ) -> Mapping[str, Any]:
        loader_passed = package.manifest.get("loader_id") == FIXED_LOADER_ID and package.manifest.get("format") == FIXED_FORMAT
        manifest_passed = package.container_sha256 == artifact_sha256
        outcomes = {
            "manifest/hash": manifest_passed,
            "allowlisted loader/format": loader_passed,
            "dependency/runtime": dependency_passed,
            "schema": schema_passed,
            "load": load_passed,
            "golden": golden_passed,
            "finite values": finite_passed,
        }
        checks = [
            {"step": name, "status": "PASSED" if passed else "FAILED", **({"message": error_code} if not passed and error_code else {})}
            for name, passed in outcomes.items()
        ]
        return {
            "valid": all(outcomes.values()),
            "artifact_sha256": artifact_sha256,
            "manifest": {**dict(package.manifest), "artifact_sha256": artifact_sha256},
            "checks": checks,
            "member_hashes": dict(package.member_hashes),
            "member_sizes": dict(package.member_sizes),
            "golden_tolerance_abs": GC_GOLDEN_ABS_TOLERANCE,
            "error_code": error_code,
        }


__all__ = [
    "GC_PACKAGE_INPUT_SCHEMA_VERSION", "GC_PACKAGE_OUTPUT_SCHEMA_VERSION",
    "GC_PACKAGE_PREPROCESSING_PROTOCOL_ID",
    "GC_PACKAGE_SCOPE_ID", "GC_PACKAGE_TASK_TYPE", "GC_PACKAGE_VERSION",
    "GcSafetensorsPackageLoader", "build_gc_safetensors_package_loader",
]


def build_gc_safetensors_package_loader() -> GcSafetensorsPackageLoader:
    """Build-injected child factory bound to the fixed trusted GC root."""
    return GcSafetensorsPackageLoader(trusted_root=GC_SOURCE_ROOT)
