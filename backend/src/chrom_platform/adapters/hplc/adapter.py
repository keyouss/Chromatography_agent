"""Prediction adapter for the two-structure HPLC enantioseparation model."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
import math
from typing import Any

from chrom_platform.core.dto import CapabilityHealth
from chrom_platform.modules.prediction.adapters import (
    AdapterCapability,
    AdapterManifest,
    ModelOption,
    ModelVerificationEvidence,
    PredictionAdapterProtocol,
    PredictionLimits,
    PredictionContractError,
    StructureCount,
    ToolCapability,
    VerifiedModelHandle,
    issue_verified_model_handle,
)
from chrom_platform.modules.prediction.schemas import (
    Applicability,
    HplcPredictionCreate,
    HplcPredictionResult,
    HplcResultRow,
    Metric,
    Warning,
)

from .manifest import (
    HPLC_ARTIFACT_ID,
    HPLC_BUSINESS_SCOPE_ID,
    HPLC_COLUMN_NAMES,
    HPLC_INPUT_SCHEMA_VERSION,
    HPLC_MODEL_SHA256,
    HPLC_MODEL_VERSION,
    HPLC_OUTPUT_SCHEMA_VERSION,
    HPLC_TOOL_ID,
)
from .runtime import HplcAssetUnavailable, HplcRuntime
from .serving_profile import HPLC_SERVING_PROFILE_ID, HPLC_SERVING_PROFILE_SHA256
from .validation import validate_hplc_request


def _health(reason: str) -> CapabilityHealth:
    return CapabilityHealth(status="UNAVAILABLE", reason=reason, checked_at=datetime.now(timezone.utc))


def _resolve(resolver: Mapping[str, str] | Callable[[str], str], ref: str) -> str:
    try:
        value = resolver(ref) if callable(resolver) else resolver[ref]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"structure resolver has no structure: {ref}") from exc
    if not isinstance(value, str) or not value.strip():
        raise ValueError("structure resolver returned no normalized SMILES")
    return value.strip()


def _require_stereospecified_pair(smiles: list[str]) -> None:
    """Require identical connectivity and inverted CIP labels."""

    try:
        from rdkit import Chem
    except ImportError as exc:
        raise ValueError("stereochemistry validation is unavailable") from exc
    if len(smiles) != 2:
        raise ValueError("HPLC requires two structures")
    molecules = []
    for value in smiles:
        mol = Chem.MolFromSmiles(value)
        if mol is None:
            raise ValueError("structure resolver returned invalid SMILES")
        molecules.append(mol)
    if Chem.MolToSmiles(molecules[0], isomericSmiles=False) != Chem.MolToSmiles(molecules[1], isomericSmiles=False):
        raise ValueError("HPLC requires an enantiomer pair with identical connectivity")
    labels: list[dict[int, str]] = []
    for molecule in molecules:
        Chem.AssignStereochemistry(molecule, cleanIt=True, force=True)
        ranks = Chem.CanonicalRankAtoms(molecule, breakTies=False)
        centers = Chem.FindMolChiralCenters(molecule, includeUnassigned=True, includeCIP=True)
        by_rank = {ranks[index]: label for index, label in centers}
        if not by_rank or any(label not in {"R", "S"} for label in by_rank.values()):
            raise ValueError("HPLC requires two stereospecified enantiomer structures")
        labels.append(by_rank)
    if labels[0].keys() != labels[1].keys() or any(labels[0][key] == labels[1][key] for key in labels[0]):
        raise ValueError("HPLC requires two distinct enantiomer structures")


class HplcPredictionAdapter(PredictionAdapterProtocol):
    """Explicit ``hplc/single`` adapter with one fixed production bundle."""

    type_id = "hplc"
    request_types = (HplcPredictionCreate,)
    manifest: AdapterManifest | None
    verified_handle: VerifiedModelHandle | None = None

    def __init__(
        self,
        runtime: HplcRuntime | None = None,
        *,
        structure_resolver: Mapping[str, str] | Callable[[str], str] | None = None,
        model_id: str = HPLC_ARTIFACT_ID,
        model_version: str = HPLC_MODEL_VERSION,
        artifact_sha256: str = HPLC_MODEL_SHA256,
        business_scope_id: str = HPLC_BUSINESS_SCOPE_ID,
        manifest: AdapterManifest | None = None,
        verified_handle: VerifiedModelHandle | None = None,
        _test_runtime: bool = False,
    ) -> None:
        if runtime is not None and type(runtime) is not HplcRuntime and not _test_runtime:
            raise TypeError("HPLC production adapter requires HplcRuntime")
        if model_id != HPLC_ARTIFACT_ID or model_version != HPLC_MODEL_VERSION:
            raise ValueError("HPLC adapter is bound to the audited GeoGNN model")
        if business_scope_id != HPLC_BUSINESS_SCOPE_ID:
            raise ValueError("HPLC adapter business scope is fixed")
        if artifact_sha256.lower() != HPLC_MODEL_SHA256:
            raise ValueError("HPLC adapter artifact hash does not match the audited model")
        if verified_handle is not None and not isinstance(verified_handle, VerifiedModelHandle):
            raise TypeError("HPLC adapter requires a central VerifiedModelHandle")
        if verified_handle is not None and manifest is None:
            raise ValueError("HPLC verified handle requires its central adapter manifest")
        if verified_handle is not None and verified_handle.schema_for(HPLC_TOOL_ID) is None:
            raise ValueError("HPLC verified handle must bind the single tool schema")
        if manifest is not None and (
            manifest.adapter_id != "hplc"
            or manifest.model_id != HPLC_ARTIFACT_ID
            or manifest.model_version != HPLC_MODEL_VERSION
            or manifest.business_scope_id != HPLC_BUSINESS_SCOPE_ID
            or manifest.input_schema_version != HPLC_INPUT_SCHEMA_VERSION
            or manifest.output_schema_version != HPLC_OUTPUT_SCHEMA_VERSION
            or manifest.artifact_sha256 != HPLC_MODEL_SHA256
            or manifest.preprocessing_profile_id != HPLC_SERVING_PROFILE_ID
            or manifest.preprocessing_profile_sha256 != HPLC_SERVING_PROFILE_SHA256
            or manifest.data_origin != "production"
        ):
            raise ValueError("HPLC adapter manifest does not bind the audited production artifact")
        self.runtime = runtime
        self.structure_resolver = structure_resolver
        self.manifest = manifest
        self.verified_handle = verified_handle
        self.business_scope_id = business_scope_id
        if runtime is not None and verified_handle is not None:
            runtime.bind_verified_handle(verified_handle)

        unavailable = _health("MODEL_VERIFICATION_REQUIRED")
        limits = PredictionLimits(max_rows=25, max_candidates=25, max_evaluations=25)
        tool = ToolCapability(
            id=HPLC_TOOL_ID,
            schema_version=HPLC_INPUT_SCHEMA_VERSION,
            input_schema={
                "title": "HplcPredictionCreate",
                "type": "object",
                "structure_refs": {"minItems": 2, "maxItems": 2, "semantics": "ordered_enantiomer_pair"},
                "input": {
                    "type": "object",
                    "properties": {
                        "model_input_smiles": {
                            "type": "array", "minItems": 2, "maxItems": 2,
                            "items": {"type": "string", "minLength": 1, "maxLength": 20_000},
                        },
                        "isopropanol_fraction": {"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 1},
                        "flow_rate_ml_min": {"type": "number", "exclusiveMinimum": 0},
                        "column_ids": {"type": "array", "items": {"enum": list(HPLC_COLUMN_NAMES)}, "minItems": 1},
                    },
                    "required": ["model_input_smiles", "isopropanol_fraction", "flow_rate_ml_min", "column_ids"],
                    "additionalProperties": False,
                },
            },
            output_schema={
                "title": "HplcPredictionResult",
                "type": "object",
                "rows": {"candidate_id": "column", "separation": "probability"},
                "metrics": {"retention_time_quantiles": ["q10", "q50", "q90"]},
            },
            structure_count=StructureCount(min=2, max=2, semantics="pair"),
            model_constraints={
                "business_scope_id": HPLC_BUSINESS_SCOPE_ID,
                "model_id": HPLC_ARTIFACT_ID,
                "preprocessing_profile_id": HPLC_SERVING_PROFILE_ID,
                "preprocessing_profile_sha256": HPLC_SERVING_PROFILE_SHA256,
                "columns": list(HPLC_COLUMN_NAMES),
                "ipa_fraction": {"min_exclusive": 0.0, "max_exclusive": 1.0},
                "separation_threshold": 0.38,
                "quantiles": ["q10", "q50", "q90"],
                "device": "cpu",
            },
            units={"isopropanol_fraction": "fraction", "flow_rate_ml_min": "ml/min", "retention_time": "min", "separation": "probability"},
            supported_visualizations=[], limits=limits, health=unavailable,
        )
        model = ModelOption(
            id=HPLC_ARTIFACT_ID, version=HPLC_MODEL_VERSION, task_type="hplc",
            scope=HPLC_BUSINESS_SCOPE_ID, artifact_sha256=HPLC_MODEL_SHA256,
            input_schema_version=HPLC_INPUT_SCHEMA_VERSION,
            output_schema_version=HPLC_OUTPUT_SCHEMA_VERSION, health=unavailable,
            preprocessing_profile_id=HPLC_SERVING_PROFILE_ID,
            preprocessing_profile_sha256=HPLC_SERVING_PROFILE_SHA256,
        )
        self.capability = AdapterCapability(
            id="hplc", display_name="HPLC enantioseparation",
            tools=[tool], models=[model], limits=limits, health=unavailable,
        )

    def _validate_model_selection(self, request: HplcPredictionCreate) -> None:
        selection = request.model_selection
        if selection.mode != "exact":
            raise PredictionContractError("HPLC requires an exact model and serving profile selection")
        if (
            selection.model_id != HPLC_ARTIFACT_ID
            or selection.model_version != HPLC_MODEL_VERSION
            or selection.preprocessing_profile_id != HPLC_SERVING_PROFILE_ID
            or selection.preprocessing_profile_sha256 != HPLC_SERVING_PROFILE_SHA256
        ):
            raise PredictionContractError("HPLC exact model/profile binding is stale or unsupported")

    def validate_request(self, request: Any) -> HplcPredictionCreate:
        return validate_hplc_request(request)

    @staticmethod
    def _validate_raw_binding(request: HplcPredictionCreate, canonical: list[str]) -> list[str]:
        raw = list(request.input.model_input_smiles)
        if len(raw) != 2 or len(canonical) != 2:
            raise PredictionContractError("HPLC requires two ordered raw inputs and two identities")
        try:
            from rdkit import Chem
        except ImportError as exc:
            raise HplcAssetUnavailable("HPLC_STRUCTURE_VALIDATION_UNAVAILABLE") from exc
        raw_identity: list[str] = []
        for raw_smiles, canonical_smiles in zip(raw, canonical):
            molecule = Chem.MolFromSmiles(raw_smiles)
            if molecule is None:
                raise PredictionContractError("HPLC model input SMILES is invalid")
            identity = Chem.MolToSmiles(molecule, canonical=True, isomericSmiles=True)
            if identity != canonical_smiles:
                raise PredictionContractError("HPLC raw input does not match its ordered structure reference")
            raw_identity.append(identity)
        _require_stereospecified_pair(raw_identity)
        return raw

    def _normalized_structures(self, request: HplcPredictionCreate) -> list[str]:
        if self.structure_resolver is None:
            raise ValueError("HPLC structure resolver is required")
        return [_resolve(self.structure_resolver, ref) for ref in request.structure_refs]

    def predict(self, request: HplcPredictionCreate) -> HplcPredictionResult:
        validated = self.validate_request(request)
        if self.verified_handle is None:
            raise HplcAssetUnavailable("MODEL_VERIFICATION_REQUIRED")
        if self.verified_handle.business_scope_id != self.business_scope_id:
            raise HplcAssetUnavailable("MODEL_VERIFICATION_SCOPE_MISMATCH")
        if (
            self.verified_handle.preprocessing_profile_id != HPLC_SERVING_PROFILE_ID
            or self.verified_handle.preprocessing_profile_sha256 != HPLC_SERVING_PROFILE_SHA256
        ):
            raise HplcAssetUnavailable("HPLC_PROFILE_BINDING_MISMATCH")
        if self.manifest is None:
            raise HplcAssetUnavailable("MODEL_CONTRACT_MISSING")
        if self.runtime is None:
            raise HplcAssetUnavailable("MODEL_RUNTIME_UNAVAILABLE")
        if self.runtime.status.status != "READY":
            raise HplcAssetUnavailable("MODEL_RUNTIME_UNAVAILABLE")
        canonical = self._normalized_structures(validated)
        return self.predict_with_normalized_structures(validated, canonical)

    def predict_with_normalized_structures(
        self, request: HplcPredictionCreate, normalized_structures: list[str],
    ) -> HplcPredictionResult:
        validated = self.validate_request(request)
        if self.verified_handle is None:
            raise HplcAssetUnavailable("MODEL_VERIFICATION_REQUIRED")
        if self.manifest is None or self.runtime is None or self.runtime.status.status != "READY":
            raise HplcAssetUnavailable("MODEL_RUNTIME_UNAVAILABLE")
        if (
            self.verified_handle is None
            or self.verified_handle.business_scope_id != self.business_scope_id
            or self.verified_handle.preprocessing_profile_id != HPLC_SERVING_PROFILE_ID
            or self.verified_handle.preprocessing_profile_sha256 != HPLC_SERVING_PROFILE_SHA256
        ):
            raise HplcAssetUnavailable("HPLC_PROFILE_BINDING_MISMATCH")
        raw_smiles = self._validate_raw_binding(validated, list(normalized_structures))
        raw = self.runtime.predict(
            model_input_smiles=raw_smiles,
            ipa_fraction=validated.input.isopropanol_fraction,
            flow_rate_ml_min=validated.input.flow_rate_ml_min,
            columns=validated.input.column_ids,
        )
        raw_columns = raw.get("columns") if isinstance(raw, Mapping) else None
        if not isinstance(raw_columns, list) or len(raw_columns) != len(validated.input.column_ids):
            raise HplcAssetUnavailable("HPLC_MODEL_OUTPUT_INVALID")
        rows: list[HplcResultRow] = []
        metrics: list[Metric] = []
        for expected_column, item in zip(validated.input.column_ids, raw_columns):
            if not isinstance(item, Mapping) or item.get("column") != expected_column:
                raise HplcAssetUnavailable("HPLC_MODEL_OUTPUT_ORDER_INVALID")
            try:
                separation = float(item["separation_probability"])
                compounds = item["compounds"]
                median = item["median_rt_min"]
                if not 0.0 <= separation <= 1.0 or len(compounds) != 2 or len(median) != 2:
                    raise ValueError
                rows.append(HplcResultRow(candidate_id=expected_column, separation=separation))
                metrics.append(Metric(key=f"{expected_column}.separation_probability", label=f"{expected_column} separation probability", value=separation, unit="probability", origin="model"))
                for index, compound in enumerate(compounds, 1):
                    quantile_values: list[float] = []
                    for quantile in ("q10", "q50", "q90"):
                        value = float(compound[f"rt_{quantile}_min"])
                        if not math.isfinite(value) or value < 0.0:
                            raise ValueError
                        quantile_values.append(value)
                        metrics.append(Metric(key=f"{expected_column}.compound_{index}.rt_{quantile}_min", label=f"{expected_column} compound {index} {quantile} retention time", value=value, unit="min", origin="model"))
                    if quantile_values != sorted(quantile_values):
                        raise ValueError
                    if str(compound["smiles"]) != raw_smiles[index - 1]:
                        raise ValueError
                for index, value in enumerate(median, 1):
                    value = float(value)
                    if not math.isfinite(value) or value < 0.0:
                        raise ValueError
                    compound_q50 = float(compounds[index - 1]["rt_q50_min"])
                    if abs(value - compound_q50) > 1e-5:
                        raise ValueError
                    metrics.append(Metric(key=f"{expected_column}.compound_{index}.median_rt_min", label=f"{expected_column} compound {index} median retention time", value=value, unit="min", origin="model"))
            except (KeyError, TypeError, ValueError, IndexError) as exc:
                raise HplcAssetUnavailable("HPLC_MODEL_OUTPUT_INVALID") from exc
        recommended = raw.get("recommended_column")
        if recommended not in validated.input.column_ids:
            raise HplcAssetUnavailable("HPLC_MODEL_RECOMMENDATION_INVALID")
        metrics.append(Metric(key="recommended_column", label="Recommended column", value=str(recommended), unit=None, origin="derived"))
        warning = raw.get("warning")
        warnings = [Warning(code="EXPERIMENTAL_CONFIRMATION_REQUIRED", message=str(warning or "HPLC 结果需实验确认。"))]
        warnings.append(Warning(code="NO_INDEPENDENT_GENERALIZATION_EVIDENCE", message="当前没有独立标签集泛化验收证据。"))
        warnings.append(Warning(code="HPLC_INPUT_REPRESENTATION_SENSITIVE", message="作者模型的分子构象输入对 SMILES 表示可能敏感；本次实际输入已按原字符串保留。"))
        warnings.append(Warning(code="HPLC_TRAINING_INPUT_PROVENANCE_GAP", message="训练端原始结构表示及缓存来源尚未追溯确认；部署预处理固定为 ETKDGv3 seed 324 单构象与 MMFF。"))
        return HplcPredictionResult(
            task_type="hplc", schema_version=HPLC_OUTPUT_SCHEMA_VERSION,
            rows=rows, metrics=metrics, visualizations=[],
            applicability=Applicability(
                supported_scope="作者 v1.1 HPLC GeoGNN 双对映体模型；25 个登记柱型、IPA 比例和流速字段受模型契约约束。",
                warnings=warnings,
            ),
        )


def build_hplc_adapter(
    *,
    registry_root: str,
    model_root: str,
    structure_resolver: Mapping[str, str] | Callable[[str], str],
) -> HplcPredictionAdapter:
    """Build HPLC and issue a central handle only after real offline proof."""

    runtime = HplcRuntime.from_registered_artifact(
        registry_root=registry_root,
        model_root=model_root,
        artifact_id=HPLC_ARTIFACT_ID,
        model_version=HPLC_MODEL_VERSION,
        business_scope_id=HPLC_BUSINESS_SCOPE_ID,
    )
    manifest = AdapterManifest(
        adapter_id="hplc", model_id=HPLC_ARTIFACT_ID, model_version=HPLC_MODEL_VERSION,
        business_scope_id=HPLC_BUSINESS_SCOPE_ID,
        manifest_version=HPLC_OUTPUT_SCHEMA_VERSION,
        input_schema_version=HPLC_INPUT_SCHEMA_VERSION,
        output_schema_version=HPLC_OUTPUT_SCHEMA_VERSION,
        artifact_sha256=HPLC_MODEL_SHA256,
        preprocessing_profile_id=HPLC_SERVING_PROFILE_ID,
        preprocessing_profile_sha256=HPLC_SERVING_PROFILE_SHA256,
        schema_available=True, assets_available=runtime.status.checked_assets,
        data_origin="production",
    )
    proof = runtime.verify_author_demo()
    evidence = None
    if all((
        proof.manifest_verified,
        proof.schema_verified,
        proof.artifact_sha_verified,
        proof.environment_verified,
        proof.golden_sample_verified,
        proof.anomaly_checks_verified,
    )):
        evidence = ModelVerificationEvidence(
            adapter_id="hplc", model_id=HPLC_ARTIFACT_ID,
            model_version=HPLC_MODEL_VERSION, business_scope_id=HPLC_BUSINESS_SCOPE_ID,
            input_schema_version=HPLC_INPUT_SCHEMA_VERSION,
            output_schema_version=HPLC_OUTPUT_SCHEMA_VERSION,
            artifact_sha256=HPLC_MODEL_SHA256,
            preprocessing_profile_id=HPLC_SERVING_PROFILE_ID,
            preprocessing_profile_sha256=HPLC_SERVING_PROFILE_SHA256,
            manifest_verified=proof.manifest_verified,
            schema_verified=proof.schema_verified,
            artifact_sha_verified=proof.artifact_sha_verified,
            environment_verified=proof.environment_verified,
            golden_sample_verified=proof.golden_sample_verified,
            anomaly_checks_verified=proof.anomaly_checks_verified,
            schema_by_tool={HPLC_TOOL_ID: {
                "input_schema": HplcPredictionCreate.model_json_schema(),
                "output_schema": HplcPredictionResult.model_json_schema(),
            }},
        )
    handle = issue_verified_model_handle(manifest, evidence) if evidence is not None else None
    return HplcPredictionAdapter(
        runtime, structure_resolver=structure_resolver, manifest=manifest,
        verified_handle=handle,
    )


__all__ = ["HplcPredictionAdapter", "build_hplc_adapter"]
