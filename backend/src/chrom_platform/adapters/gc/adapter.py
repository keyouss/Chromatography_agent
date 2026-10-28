"""Prediction adapter for the audited GC temperature-program model."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any

from chrom_platform.core.dto import CapabilityHealth
from chrom_platform.modules.prediction.adapters import (
    AdapterCapability,
    AdapterManifest,
    ModelOption,
    ModelVerificationEvidence,
    PredictionAdapterProtocol,
    PredictionContractError,
    PredictionLimits,
    StructureCount,
    ToolCapability,
    VerifiedModelHandle,
    issue_verified_model_handle,
)
from chrom_platform.modules.prediction.schemas import (
    Applicability,
    GcLinearTemperatureProgram,
    GcPredictionCreate,
    GcPredictionResult,
    GcResultRow,
    Metric,
    Warning,
)

from .manifest import (
    GC_ARTIFACT_ID,
    GC_BUSINESS_SCOPE_ID,
    GC_INPUT_SCHEMA_VERSION,
    GC_MODEL_SHA256,
    GC_MODEL_VERSION,
    GC_OUTPUT_SCHEMA_VERSION,
    GC_PREPROCESSING_PROTOCOL_ID,
    GC_PREPROCESSING_PROTOCOL_QA_APPROVED,
)
from .runtime import GcAssetUnavailable, GcProfileReviewApproval, GcRuntime
from .serving_profile import GC_SERVING_PROFILE_ID, GC_SERVING_PROFILE_SHA256


def _health(reason: str) -> CapabilityHealth:
    return CapabilityHealth(status="UNAVAILABLE", reason=reason, checked_at=datetime.now(timezone.utc))


def _resolve_structure(
    resolver: Mapping[str, str] | Callable[[str], str], ref: str
) -> str:
    try:
        value = resolver(ref) if callable(resolver) else resolver[ref]
    except (KeyError, LookupError, TypeError, ValueError) as exc:
        raise ValueError("GC structure resolver has no normalized structure") from exc
    if not isinstance(value, str) or not value.strip():
        raise ValueError("GC structure resolver returned no normalized structure")
    value = value.strip()
    return _canonical_isomeric_smiles(value)


def _canonical_isomeric_smiles(value: str) -> str:
    try:
        from rdkit import Chem
    except ImportError as exc:
        raise GcAssetUnavailable("SCIENTIFIC_DEPENDENCIES_UNAVAILABLE") from exc
    mol = Chem.MolFromSmiles(value)
    if mol is None or mol.GetNumAtoms() == 0:
        raise ValueError("GC structure resolver returned invalid SMILES")
    return Chem.MolToSmiles(mol, canonical=True, isomericSmiles=True)


def _temperature_series(program: Any) -> list[float]:
    if getattr(program, "mode", None) == "series":
        try:
            values = [float(value) for value in program.temperature_series_c]
        except (AttributeError, TypeError, ValueError) as exc:
            raise PredictionContractError("GC requires exactly 30 finite temperature points") from exc
        if len(values) != 30 or not all(math.isfinite(value) for value in values):
            raise PredictionContractError("GC requires exactly 30 finite temperature points")
        return values
    if not isinstance(program, GcLinearTemperatureProgram):
        raise PredictionContractError("GC temperature program is unsupported")
    try:
        initial, final, rate, hold = (
            float(program.initial_temperature_c),
            float(program.final_temperature_c),
            float(program.heating_rate_c_per_min),
            float(program.initial_hold_min),
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise PredictionContractError("GC temperature program must contain numeric fields") from exc
    if not all(math.isfinite(value) for value in (initial, final, rate, hold)):
        raise PredictionContractError("GC temperature program must be finite")
    if rate <= 0 or hold < 0:
        raise PredictionContractError("GC heating rate must be > 0 and initial hold must be >= 0")
    if final < initial:
        raise PredictionContractError("GC final temperature must be >= initial temperature")
    if not hold.is_integer():
        raise PredictionContractError("GC initial hold must be an integer number of minutes")
    result: list[float] = []
    for minute in range(30):
        elapsed = minute - int(program.initial_hold_min)
        if elapsed < 0:
            result.append(initial)
        else:
            result.append(min(final, initial + elapsed * rate))
    return result


def _gc_input_schema() -> dict[str, Any]:
    """Return the wire DTO schema narrowed to this model's accepted program."""
    schema = GcPredictionCreate.model_json_schema()
    linear_schema = schema.get("$defs", {}).get("GcLinearTemperatureProgram")
    if isinstance(linear_schema, dict):
        hold = linear_schema.get("properties", {}).get("initial_hold_min")
        if isinstance(hold, dict):
            hold["multipleOf"] = 1
    series_schema = schema.get("$defs", {}).get("GcSeriesTemperatureProgram")
    if isinstance(series_schema, dict):
        items = series_schema.get("properties", {}).get("temperature_series_c")
        if isinstance(items, dict):
            items["minItems"] = 30
            items["maxItems"] = 30
    return schema


class GcPredictionAdapter(PredictionAdapterProtocol):
    """Explicit ``gc/single`` adapter with one fixed production bundle."""

    type_id = "gc"
    request_types = (GcPredictionCreate,)
    manifest: AdapterManifest | None
    verified_handle: VerifiedModelHandle | None = None

    def __init__(
        self,
        runtime: GcRuntime | None = None,
        *,
        structure_resolver: Mapping[str, str] | Callable[[str], str] | None = None,
        model_id: str = GC_ARTIFACT_ID,
        model_version: str = GC_MODEL_VERSION,
        artifact_sha256: str = GC_MODEL_SHA256,
        business_scope_id: str = GC_BUSINESS_SCOPE_ID,
        manifest: AdapterManifest | None = None,
        verified_handle: VerifiedModelHandle | None = None,
    ) -> None:
        if runtime is not None and type(runtime) is not GcRuntime:
            raise TypeError("GC production adapter requires the trusted GcRuntime implementation")
        if model_id != GC_ARTIFACT_ID or model_version != GC_MODEL_VERSION:
            raise ValueError("GC adapter is bound to the audited GNN model")
        if business_scope_id != GC_BUSINESS_SCOPE_ID:
            raise ValueError("GC adapter business scope is fixed")
        if artifact_sha256.lower() != GC_MODEL_SHA256:
            raise ValueError("GC adapter artifact hash does not match the audited model")
        if verified_handle is not None and not isinstance(verified_handle, VerifiedModelHandle):
            raise TypeError("GC adapter requires a central VerifiedModelHandle")
        if manifest is not None and not isinstance(manifest, AdapterManifest):
            raise TypeError("GC adapter requires a central AdapterManifest")
        if verified_handle is not None and manifest is None:
            raise ValueError("GC verified handle requires its central adapter manifest")
        if verified_handle is not None and (
            verified_handle.adapter_id != "gc"
            or verified_handle.model_id != GC_ARTIFACT_ID
            or verified_handle.model_version != GC_MODEL_VERSION
            or verified_handle.business_scope_id != GC_BUSINESS_SCOPE_ID
            or verified_handle.input_schema_version != GC_INPUT_SCHEMA_VERSION
            or verified_handle.output_schema_version != GC_OUTPUT_SCHEMA_VERSION
            or verified_handle.artifact_sha256 != GC_MODEL_SHA256
            or verified_handle.preprocessing_profile_id != GC_SERVING_PROFILE_ID
            or verified_handle.preprocessing_profile_sha256 != GC_SERVING_PROFILE_SHA256
            or verified_handle.schema_for("single") is None
        ):
            raise ValueError("GC verified handle does not bind the audited scope and tool")
        if manifest is not None and (
            manifest.adapter_id != "gc"
            or manifest.model_id != GC_ARTIFACT_ID
            or manifest.model_version != GC_MODEL_VERSION
            or manifest.business_scope_id != GC_BUSINESS_SCOPE_ID
            or manifest.input_schema_version != GC_INPUT_SCHEMA_VERSION
            or manifest.output_schema_version != GC_OUTPUT_SCHEMA_VERSION
            or manifest.artifact_sha256 != GC_MODEL_SHA256
            or manifest.preprocessing_profile_id != GC_SERVING_PROFILE_ID
            or manifest.preprocessing_profile_sha256 != GC_SERVING_PROFILE_SHA256
            or manifest.data_origin != "production"
        ):
            raise ValueError("GC adapter manifest does not bind the audited production artifact")
        self.runtime = runtime
        self.structure_resolver = structure_resolver
        self.manifest = manifest
        self.verified_handle = verified_handle
        self.business_scope_id = business_scope_id
        if runtime is not None and verified_handle is not None:
            runtime.bind_verified_handle(verified_handle)

        health = _health("MODEL_VERIFICATION_REQUIRED")
        limits = PredictionLimits(max_rows=1, max_candidates=1, max_evaluations=1)
        tool = ToolCapability(
            id="single",
            schema_version=GC_INPUT_SCHEMA_VERSION,
            input_schema={
                "title": "GcPredictionCreate",
                "type": "object",
                "temperature_program": {
                    "modes": ["linear", "series"],
                    "series_length": 30,
                    "linear_fields": ["initial_temperature_c", "final_temperature_c", "heating_rate_c_per_min", "initial_hold_min"],
                },
            },
            output_schema={
                "title": "GcPredictionResult",
                "type": "object",
                "retention_time_unit": "min",
                "quantiles": ["q10", "q50", "q90"],
            },
            structure_count=StructureCount(min=1, max=1, semantics="single"),
            model_constraints={
                "business_scope_id": GC_BUSINESS_SCOPE_ID,
                "model_id": GC_ARTIFACT_ID,
                "fixed_stationary_phase": "5% phenyl/95% dimethylpolysiloxane",
                "temperature_points": 30,
                "quantiles": ["q10", "q50", "q90"],
                "device": "cpu",
                "carrier_gas_effect": "unsupported",
            },
            units={
                "initial_temperature_c": "C",
                "final_temperature_c": "C",
                "heating_rate_c_per_min": "C/min",
                "initial_hold_min": "min",
                "retention_time_min": "min",
            },
            supported_visualizations=[], limits=limits, health=health,
        )
        model = ModelOption(
            id=GC_ARTIFACT_ID, version=GC_MODEL_VERSION, task_type="gc",
            scope=GC_BUSINESS_SCOPE_ID, artifact_sha256=GC_MODEL_SHA256,
            preprocessing_profile_id=GC_SERVING_PROFILE_ID,
            preprocessing_profile_sha256=GC_SERVING_PROFILE_SHA256,
            input_schema_version=GC_INPUT_SCHEMA_VERSION,
            output_schema_version=GC_OUTPUT_SCHEMA_VERSION, health=health,
        )
        self.capability = AdapterCapability(
            id="gc", display_name="GC temperature-program retention time",
            tools=[tool], models=[model], limits=limits, health=health,
        )

    def _validate_selection(self, request: GcPredictionCreate) -> None:
        selection = request.model_selection
        if selection.mode != "exact" or (
            selection.model_id != GC_ARTIFACT_ID
            or selection.model_version != GC_MODEL_VERSION
            or selection.preprocessing_profile_id != GC_SERVING_PROFILE_ID
            or selection.preprocessing_profile_sha256 != GC_SERVING_PROFILE_SHA256
        ):
            raise PredictionContractError("GC request selects an unbound model")

    def validate_request(self, request: Any) -> GcPredictionCreate:
        if not isinstance(request, GcPredictionCreate):
            raise PredictionContractError("GC adapter requires GcPredictionCreate")
        if request.chromatography_type != "gc" or request.tool != "single" or request.schema_version != GC_INPUT_SCHEMA_VERSION:
            raise PredictionContractError("GC request is outside the bound tool contract")
        if len(request.structure_refs) != 1:
            raise PredictionContractError("GC requires exactly one structure ref")
        self._validate_selection(request)
        _temperature_series(request.input.temperature_program)
        return request

    def _normalized_structure(self, request: GcPredictionCreate) -> str:
        if self.structure_resolver is None:
            raise GcAssetUnavailable("STRUCTURE_RESOLVER_REQUIRED")
        return _resolve_structure(self.structure_resolver, request.structure_refs[0])

    def predict(self, request: GcPredictionCreate) -> GcPredictionResult:
        validated = self.validate_request(request)
        normalized = self._normalized_structure(validated)
        return self._predict_normalized(validated, normalized)

    def predict_with_normalized_structures(
        self, request: GcPredictionCreate, normalized_structures: Sequence[str],
    ) -> GcPredictionResult:
        """Execute with the project-authorized canonical structure frozen at create time."""
        validated = self.validate_request(request)
        if (
            isinstance(normalized_structures, (str, bytes))
            or not isinstance(normalized_structures, list)
        ):
            raise PredictionContractError("GC execution snapshot requires one canonical structure")
        frozen_structures = tuple(normalized_structures)
        if len(frozen_structures) != 1 or not isinstance(frozen_structures[0], str):
            raise PredictionContractError("GC execution snapshot requires one canonical structure")
        normalized = _canonical_isomeric_smiles(frozen_structures[0])
        if normalized != frozen_structures[0]:
            raise PredictionContractError("GC execution snapshot structure is not canonical isomeric SMILES")
        return self._predict_normalized(validated, normalized)

    def _predict_normalized(self, validated: GcPredictionCreate, normalized: str) -> GcPredictionResult:
        if self.verified_handle is None:
            raise GcAssetUnavailable("MODEL_VERIFICATION_REQUIRED")
        if self.verified_handle.business_scope_id != self.business_scope_id:
            raise GcAssetUnavailable("MODEL_VERIFICATION_SCOPE_MISMATCH")
        if self.manifest is None:
            raise GcAssetUnavailable("MODEL_CONTRACT_MISSING")
        if self.runtime is None:
            raise GcAssetUnavailable("MODEL_RUNTIME_UNAVAILABLE")
        if type(self.runtime) is not GcRuntime or self.runtime.status.status != "READY":
            raise GcAssetUnavailable("MODEL_RUNTIME_UNAVAILABLE")
        if self.runtime.verified_handle is not self.verified_handle:
            raise GcAssetUnavailable("MODEL_RUNTIME_VERIFICATION_MISMATCH")
        temperatures = _temperature_series(validated.input.temperature_program)
        raw = self.runtime.predict(normalized_smiles=[normalized], temperature_series_c=temperatures)
        if (
            not isinstance(raw, Mapping)
            or raw.get("artifact_sha256") != self.verified_handle.artifact_sha256
            or raw.get("preprocessing_protocol_id") != GC_PREPROCESSING_PROTOCOL_ID
        ):
            raise GcAssetUnavailable("MODEL_ARTIFACT_BINDING_INVALID")
        raw_temps = raw.get("temperature_series_c")
        try:
            returned_temperatures = [float(value) for value in raw_temps]
        except (TypeError, ValueError) as exc:
            raise GcAssetUnavailable("MODEL_TEMPERATURE_PROGRAM_INVALID") from exc
        if (
            not isinstance(raw_temps, list)
            or len(returned_temperatures) != 30
            or not all(math.isfinite(value) for value in returned_temperatures)
            or returned_temperatures != temperatures
        ):
            raise GcAssetUnavailable("MODEL_TEMPERATURE_PROGRAM_INVALID")
        failed = raw.get("failed_smiles", [])
        predictions = raw.get("predictions")
        if not isinstance(failed, list) or not all(isinstance(value, str) for value in failed):
            raise GcAssetUnavailable("MODEL_OUTPUT_INVALID")
        if not isinstance(predictions, list) or len(predictions) + len(failed) != 1:
            raise GcAssetUnavailable("MODEL_OUTPUT_SHAPE_INVALID")
        rows: list[GcResultRow] = []
        metrics: list[Metric] = []
        for item in predictions:
            if not isinstance(item, Mapping) or item.get("smiles") != normalized:
                raise GcAssetUnavailable("MODEL_OUTPUT_ORDER_INVALID")
            try:
                quantiles = {key: float(item[f"rt_{key}_min"]) for key in ("q10", "q50", "q90")}
            except (KeyError, TypeError, ValueError) as exc:
                raise GcAssetUnavailable("MODEL_OUTPUT_INVALID") from exc
            values = [quantiles[key] for key in ("q10", "q50", "q90")]
            if not all(math.isfinite(value) and 0.0 <= value <= 10_000 for value in values) or values != sorted(values):
                raise GcAssetUnavailable("MODEL_OUTPUT_RANGE_INVALID")
            rows.append(GcResultRow(retention_time_min=quantiles["q50"], quantiles=quantiles))
            for key, value in quantiles.items():
                metrics.append(Metric(key=f"retention_time_{key}_min", label=f"Retention time {key}", value=value, unit="min", origin="model"))
        warnings = [
            Warning(code="EXPERIMENTAL_CONFIRMATION_REQUIRED", message="结果仅用于实验规划，须通过实验确认。"),
            Warning(code="FIXED_STATIONARY_PHASE_SCOPE", message="模型仅在 5% phenyl/95% dimethylpolysiloxane 固定相范围内验证。"),
            Warning(code="CARRIER_GAS_EFFECT_UNSUPPORTED", message="当前模型未建模载气种类、流速或压力效应。"),
            Warning(code="SEEDED_3D_REPRESENTATION_SCOPE", message="预处理采用固定 RDKit 参数与随机种子 42，并保留作者的默认构象序列化路径；这是一项工程表示约定，不证明实验构象或跨环境等价性。"),
            Warning(code="NO_INDEPENDENT_GENERALIZATION_EVIDENCE", message="当前没有独立标签集泛化验收证据。"),
        ]
        if failed:
            warnings.append(Warning(code="MODEL_FAILED_STRUCTURE", message="部分结构未能生成有效 3D 构象。", field="structure_refs"))
        return GcPredictionResult(
            task_type="gc", schema_version=GC_OUTPUT_SCHEMA_VERSION,
            rows=rows, metrics=metrics, visualizations=[],
            applicability=Applicability(
                supported_scope="作者 GC GNN 程序升温模型；固定 30 点温度程序、单结构输入和固定相范围受契约约束。",
                warnings=warnings,
            ),
        )


def build_gc_adapter(
    *,
    registry_root: str,
    model_root: str,
    structure_resolver: Mapping[str, str] | Callable[[str], str],
    golden_fixture: str | Path | None = None,
) -> GcPredictionAdapter:
    runtime = GcRuntime.from_registered_artifact(
        registry_root=registry_root,
        model_root=model_root,
        business_scope_id=GC_BUSINESS_SCOPE_ID,
    )
    manifest = AdapterManifest(
        adapter_id="gc", model_id=GC_ARTIFACT_ID, model_version=GC_MODEL_VERSION,
        business_scope_id=GC_BUSINESS_SCOPE_ID,
        manifest_version=GC_OUTPUT_SCHEMA_VERSION,
        input_schema_version=GC_INPUT_SCHEMA_VERSION,
        output_schema_version=GC_OUTPUT_SCHEMA_VERSION,
        artifact_sha256=GC_MODEL_SHA256,
        preprocessing_profile_id=GC_SERVING_PROFILE_ID,
        preprocessing_profile_sha256=GC_SERVING_PROFILE_SHA256,
        schema_available=runtime.status.schema_verified,
        assets_available=runtime.status.checked_assets,
        data_origin="production",
    )
    fixture_path = Path(golden_fixture) if golden_fixture is not None else (
        Path(__file__).resolve().parents[5] / "testing" / "fixtures" / "priority-launch"
        / "gc-reference-preprocessing-v2.json"
    )
    proof = None
    try:
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        proof = runtime.verify_golden(fixture, fixture_path=fixture_path)
    except (OSError, ValueError, TypeError):
        pass
    evidence = None
    if GC_PREPROCESSING_PROTOCOL_QA_APPROVED and proof is not None and all((
        proof.manifest_verified,
        proof.schema_verified,
        proof.artifact_sha_verified,
        proof.environment_verified,
        proof.golden_sample_verified,
        proof.anomaly_checks_verified,
    )):
        evidence = ModelVerificationEvidence(
            adapter_id="gc", model_id=GC_ARTIFACT_ID, model_version=GC_MODEL_VERSION,
            business_scope_id=GC_BUSINESS_SCOPE_ID,
            input_schema_version=GC_INPUT_SCHEMA_VERSION,
            output_schema_version=GC_OUTPUT_SCHEMA_VERSION,
            artifact_sha256=GC_MODEL_SHA256,
            preprocessing_profile_id=GC_SERVING_PROFILE_ID,
            preprocessing_profile_sha256=GC_SERVING_PROFILE_SHA256,
            manifest_verified=proof.manifest_verified,
            schema_verified=proof.schema_verified,
            artifact_sha_verified=proof.artifact_sha_verified,
            environment_verified=proof.environment_verified,
            golden_sample_verified=proof.golden_sample_verified,
            anomaly_checks_verified=proof.anomaly_checks_verified,
            schema_by_tool={"single": {
                "input_schema": _gc_input_schema(),
                "output_schema": GcPredictionResult.model_json_schema(),
            }},
        )
    handle = issue_verified_model_handle(manifest, evidence) if evidence is not None else None
    return GcPredictionAdapter(runtime, structure_resolver=structure_resolver, manifest=manifest, verified_handle=handle)


def build_gc_profile_candidate_adapter(
    *,
    runtime: GcRuntime,
    structure_resolver: Mapping[str, str] | Callable[[str], str],
    approval: GcProfileReviewApproval,
) -> GcPredictionAdapter:
    """Issue one offline candidate adapter from a runtime-local reviewed receipt."""
    if type(runtime) is not GcRuntime:
        raise TypeError("GC profile candidate requires the verified GcRuntime instance")
    receipt = runtime._consume_profile_review_approval(approval)
    # Approval is consumed before manifest/evidence/handle creation. Any error
    # below leaves this runtime generation permanently unable to retry.
    manifest = AdapterManifest(
        adapter_id="gc", model_id=GC_ARTIFACT_ID, model_version=GC_MODEL_VERSION,
        business_scope_id=GC_BUSINESS_SCOPE_ID,
        manifest_version=GC_OUTPUT_SCHEMA_VERSION,
        input_schema_version=GC_INPUT_SCHEMA_VERSION,
        output_schema_version=GC_OUTPUT_SCHEMA_VERSION,
        artifact_sha256=GC_MODEL_SHA256,
        preprocessing_profile_id=GC_SERVING_PROFILE_ID,
        preprocessing_profile_sha256=GC_SERVING_PROFILE_SHA256,
        schema_available=True,
        assets_available=True,
        data_origin="production",
    )
    evidence = ModelVerificationEvidence(
        adapter_id="gc", model_id=GC_ARTIFACT_ID, model_version=GC_MODEL_VERSION,
        business_scope_id=GC_BUSINESS_SCOPE_ID,
        input_schema_version=GC_INPUT_SCHEMA_VERSION,
        output_schema_version=GC_OUTPUT_SCHEMA_VERSION,
        artifact_sha256=GC_MODEL_SHA256,
        preprocessing_profile_id=GC_SERVING_PROFILE_ID,
        preprocessing_profile_sha256=GC_SERVING_PROFILE_SHA256,
        manifest_verified=True,
        schema_verified=True,
        artifact_sha_verified=True,
        environment_verified=True,
        golden_sample_verified=True,
        anomaly_checks_verified=True,
        schema_by_tool={"single": {
            "input_schema": _gc_input_schema(),
            "output_schema": GcPredictionResult.model_json_schema(),
        }},
    )
    if (
        receipt.profile_id != GC_SERVING_PROFILE_ID
        or receipt.profile_sha256 != GC_SERVING_PROFILE_SHA256
        or receipt.model_id != GC_ARTIFACT_ID
        or receipt.model_version != GC_MODEL_VERSION
        or receipt.artifact_sha256 != GC_MODEL_SHA256
    ):
        raise GcAssetUnavailable("GC_PROFILE_REVIEW_RECEIPT_STALE")
    handle = issue_verified_model_handle(manifest, evidence)
    return GcPredictionAdapter(
        runtime, structure_resolver=structure_resolver, manifest=manifest, verified_handle=handle,
    )


__all__ = ["GcPredictionAdapter", "build_gc_adapter", "build_gc_profile_candidate_adapter"]
