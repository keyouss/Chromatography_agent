"""PredictionAdapter implementation for the controlled TLC runtime."""

from __future__ import annotations

from datetime import datetime, timezone
from collections.abc import Callable
from contextvars import ContextVar
from typing import Any

from pydantic import BaseModel

from chrom_platform.core.dto import CapabilityHealth
from chrom_platform.core.errors import InputValidationError
from chrom_platform.modules.prediction.adapters import (
    AdapterCapability,
    AdapterManifest,
    ModelOption,
    PredictionAdapterProtocol,
    PredictionLimits,
    StructureCount,
    ToolCapability,
    VerifiedModelHandle,
)
from chrom_platform.modules.prediction.schemas import (
    Applicability,
    Metric,
    TlcPredictionCreate,
    TlcConditionComparisonCreate,
    TlcConditionComparisonResult,
    TlcReverseCreate,
    TlcReverseResult,
    TlcPredictionResult,
    TlcResultRow,
    Warning,
)

from .manifest import (
    TLC_ARTIFACT_ID,
    TLC_ARTIFACT_MANIFEST_SHA256,
    TLC_INPUT_SCHEMA_VERSION,
    TLC_MODEL_VERSION,
    TLC_SCOPE_ID,
    TLC_OUTPUT_SCHEMA_VERSION,
)
from .runtime import TlcRuntime, validate_mobile_phase
from .validation import TLC_REQUEST_TYPES, validate_tlc_request


def _unavailable_health(reason: str) -> CapabilityHealth:
    return CapabilityHealth(status="UNAVAILABLE", reason=reason, checked_at=datetime.now(timezone.utc))


_WORKER_NORMALIZED_SMILES: ContextVar[str | None] = ContextVar(
    "tlc_worker_normalized_smiles", default=None
)

_SCIENTIFIC_WARNING_MESSAGES = (
    "EXPERIMENTAL_CONFIRMATION_REQUIRED: TLC results are initial condition suggestions and require experimental confirmation",
    "NO_INDEPENDENT_GENERALIZATION_EVIDENCE: no independent labeled-set evidence is available; results do not establish independent scientific generalization",
)


class TlcPredictionAdapter(PredictionAdapterProtocol):
    """Explicit ``tlc/single`` adapter.

    The normalized structure is supplied by the trusted structure service at
    execution time.  It is intentionally not accepted as a client path or
    model argument.
    """

    type_id = "tlc"
    request_types = (TlcPredictionCreate, TlcConditionComparisonCreate, TlcReverseCreate)
    manifest: AdapterManifest | None
    verified_handle: VerifiedModelHandle | None = None

    def __init__(self, runtime: TlcRuntime | None = None, *, structure_resolver: Callable[[str], str] | None = None, manifest: AdapterManifest | None = None, verified_handle: VerifiedModelHandle | None = None) -> None:
        self.runtime = runtime
        self.structure_resolver = structure_resolver
        self.manifest = manifest
        self.verified_handle = verified_handle
        self.utility_service = None
        if runtime is not None and type(runtime) is not TlcRuntime:
            raise TypeError("TLC adapter requires the trusted TlcRuntime implementation")
        if verified_handle is not None and manifest is None:
            raise ValueError("TLC verified handle requires its central adapter manifest")
        if manifest is not None and (
            manifest.adapter_id != "tlc"
                or manifest.model_id != TLC_ARTIFACT_ID
                or manifest.model_version != TLC_MODEL_VERSION
                or manifest.business_scope_id != TLC_SCOPE_ID
            or manifest.input_schema_version != TLC_INPUT_SCHEMA_VERSION
            or manifest.output_schema_version != TLC_OUTPUT_SCHEMA_VERSION
            or manifest.artifact_sha256 != TLC_ARTIFACT_MANIFEST_SHA256
            or manifest.data_origin != "production"
            or not manifest.schema_available
            or not manifest.assets_available
        ):
            raise ValueError("TLC adapter manifest does not bind the audited artifact")
        if verified_handle is not None:
            if (
                not isinstance(verified_handle, VerifiedModelHandle)
                or verified_handle.adapter_id != "tlc"
                or verified_handle.model_id != TLC_ARTIFACT_ID
                or verified_handle.model_version != TLC_MODEL_VERSION
                or verified_handle.business_scope_id != TLC_SCOPE_ID
                or verified_handle.input_schema_version != TLC_INPUT_SCHEMA_VERSION
                or verified_handle.output_schema_version != TLC_OUTPUT_SCHEMA_VERSION
                or verified_handle.artifact_sha256 != TLC_ARTIFACT_MANIFEST_SHA256
                or verified_handle.schema_for("single") is None
            ):
                raise ValueError("TLC verified handle does not bind the audited scope and tool")
        if runtime is not None and verified_handle is not None:
            runtime.bind_verified_handle(verified_handle)
        health = _unavailable_health("MODEL_VERIFICATION_REQUIRED")
        limits = PredictionLimits(max_rows=1, max_candidates=1, max_evaluations=1)
        tool = ToolCapability(
            id="single",
            schema_version=TLC_INPUT_SCHEMA_VERSION,
            input_schema={"title": "TlcPredictionCreate", "type": "object"},
            output_schema={"title": "TlcPredictionResult", "type": "object"},
            structure_count=StructureCount(min=1, max=1, semantics="single"),
            model_constraints={
                "feature_count": 179,
                "solvent_order": ["hexane", "ethyl_acetate", "dichloromethane", "methanol", "diethyl_ether"],
                "allowed_systems": [
                    ["hexane", "ethyl_acetate"],
                    ["dichloromethane", "methanol"],
                    ["hexane", "diethyl_ether"],
                ],
                "ratios_normalized": True,
            },
            units={"rf": "unitless", "fraction": "unitless"},
            supported_visualizations=[],
            limits=limits,
            health=health,
        )
        utility_tools = [
            ToolCapability(
                id="condition_compare",
                schema_version=TLC_INPUT_SCHEMA_VERSION,
                input_schema=TlcConditionComparisonCreate.model_json_schema(),
                output_schema=TlcConditionComparisonResult.model_json_schema(),
                structure_count=StructureCount(min=1, max=1, semantics="single"),
                model_constraints={"real_model_evaluations": True, "max_conditions": 200},
                units={"rf": "unitless", "fraction": "unitless"},
                supported_visualizations=[],
                limits=PredictionLimits(max_rows=200, max_candidates=200, max_evaluations=200),
                health=health,
            ),
            ToolCapability(
                id="reverse_solvent",
                schema_version=TLC_INPUT_SCHEMA_VERSION,
                input_schema=TlcReverseCreate.model_json_schema(),
                output_schema=TlcReverseResult.model_json_schema(),
                structure_count=StructureCount(min=1, max=1, semantics="single"),
                model_constraints={"real_model_evaluations": True, "max_candidates": 200},
                units={"rf": "unitless", "fraction": "unitless"},
                supported_visualizations=[],
                limits=PredictionLimits(max_rows=200, max_candidates=200, max_evaluations=200),
                health=health,
            ),
        ]
        model = ModelOption(
            id=TLC_ARTIFACT_ID,
            version=TLC_MODEL_VERSION,
            task_type="tlc",
            scope=TLC_SCOPE_ID,
            artifact_sha256=TLC_ARTIFACT_MANIFEST_SHA256,
            input_schema_version=TLC_INPUT_SCHEMA_VERSION,
            output_schema_version=TLC_OUTPUT_SCHEMA_VERSION,
            health=health,
        )
        self.capability = AdapterCapability(
            id="tlc",
            display_name="TLC Rf",
            tools=[tool, *utility_tools],
            models=[model],
            limits=limits,
            health=health,
        )

    def validate_request(self, request: BaseModel) -> TlcPredictionCreate | TlcConditionComparisonCreate | TlcReverseCreate:
        return validate_tlc_request(request)  # type: ignore[return-value]

    def predict(self, request: BaseModel) -> TlcPredictionResult | TlcConditionComparisonResult | TlcReverseResult:
        if self.verified_handle is None:
            raise RuntimeError("TLC central verification handle is required")
        if self.manifest is None:
            raise RuntimeError("TLC central adapter manifest is required")
        validated = self.validate_request(request)
        if self.runtime is None or self.runtime.status.status != "READY":
            raise RuntimeError("TLC model runtime is unavailable")
        if not isinstance(self.runtime, TlcRuntime):
            raise RuntimeError("TLC model runtime is not a trusted registered runtime")
        if getattr(self.runtime, "_verified_handle", None) is not self.verified_handle:
            raise RuntimeError("TLC runtime is not bound to the central verification handle")
        if self.runtime.status.status != "READY":
            raise RuntimeError("TLC model runtime is unavailable")
        worker_smiles = _WORKER_NORMALIZED_SMILES.get()
        if worker_smiles is not None:
            normalized_smiles = worker_smiles
        else:
            if self.structure_resolver is None:
                raise RuntimeError("TLC structure resolver is required")
            normalized_smiles = self.structure_resolver(validated.structure_refs[0])
        if not isinstance(normalized_smiles, str) or not normalized_smiles.strip():
            raise ValueError("structure resolver returned no normalized SMILES")
        if validated.tool == "condition_compare":
            from .service import TlcPredictionService
            service = TlcPredictionService(self.runtime)
            rows = service.compare_conditions(
                smiles=normalized_smiles,
                conditions=[{item.solvent_id: item.fraction for item in condition.solvent_components} for condition in validated.conditions],
            )
            return TlcConditionComparisonResult(
                task_type="tlc_condition_comparison", schema_version=TLC_OUTPUT_SCHEMA_VERSION,
                structure_ref=validated.structure_ref, model_id=TLC_ARTIFACT_ID,
                model_version=TLC_MODEL_VERSION, artifact_sha256=TLC_ARTIFACT_MANIFEST_SHA256,
                results=[{
                    "condition_index": row.condition_index,
                    "mobile_phase": dict(row.mobile_phase),
                    "predicted_rf": row.predicted_rf,
                    "component_predictions": dict(row.component_predictions),
                } for row in rows],
                warnings=list(_SCIENTIFIC_WARNING_MESSAGES),
            )
        if validated.tool == "reverse_solvent":
            from .service import TlcPredictionService
            service = TlcPredictionService(self.runtime)
            result = service.reverse_solvent(
                smiles=normalized_smiles,
                target_rf=(validated.target_rf_min, validated.target_rf_max),
                candidate_system_ids=validated.candidate_system_ids,
                ratio_step=validated.ratio_step,
                max_candidates=validated.max_candidates,
            )
            return TlcReverseResult(
                task_type="tlc_reverse_solvent", schema_version=TLC_OUTPUT_SCHEMA_VERSION,
                structure_ref=validated.structure_ref, model_id=TLC_ARTIFACT_ID,
                model_version=TLC_MODEL_VERSION, artifact_sha256=TLC_ARTIFACT_MANIFEST_SHA256,
                candidates=[{
                    "system_id": item.system_id, "mobile_phase": dict(item.mobile_phase),
                    "predicted_rf": item.predicted_rf, "source": item.source,
                } for item in result.candidates],
                evaluated=result.evaluated, failed=result.failed,
                target_reachable=result.target_reachable,
                warnings=list(_SCIENTIFIC_WARNING_MESSAGES) + list(result.warnings),
            )
        original_phase = {item.solvent_id: item.fraction for item in validated.input.solvent_components}
        normalized_phase = validate_mobile_phase(original_phase)
        raw = self.runtime.predict(smiles=normalized_smiles, mobile_phase=original_phase)
        metrics = [
            Metric(key="ensemble_rf", label="Ensemble Rf", value=raw["rf"], unit="unitless", origin="model")
        ] + [
            Metric(key=f"component_{name}", label=f"{name} component", value=value, unit="unitless", origin="model")
            for name, value in raw["component_predictions"].items()
        ]
        return TlcPredictionResult(
            task_type="tlc",
            schema_version=TLC_OUTPUT_SCHEMA_VERSION,
            model_id=TLC_ARTIFACT_ID,
            model_version=TLC_MODEL_VERSION,
            artifact_sha256=TLC_ARTIFACT_MANIFEST_SHA256,
            ensemble_weights=dict(raw["ensemble_weights"]),
            mobile_phase_original=original_phase,
            mobile_phase_normalized=normalized_phase,
            rows=[TlcResultRow(rf=raw["rf"])],
            metrics=metrics,
            visualizations=[],
            applicability=Applicability(
                supported_scope="作者 v1.1 TLC 三种二元展开剂体系；不同板材、展开缸、温湿度需实验确认。",
                warnings=[
                    Warning(
                        code="RATIO_NORMALIZED",
                        message=f"原始溶剂比例 {original_phase} 已归一化为 {normalized_phase}；未替换溶剂语义。",
                        field="input.solvent_components",
                    ),
                    Warning(code="EXPERIMENTAL_CONFIRMATION_REQUIRED", message="该模型结果仅用于初始展开剂条件建议，需实验确认。"),
                    Warning(code="NO_INDEPENDENT_GENERALIZATION_EVIDENCE", message="当前没有独立标签集证据，结果不代表独立科学泛化验收。"),
                ],
            ),
        )

    def predict_with_normalized_structures(
        self, request: BaseModel, normalized_structures: list[str]
    ) -> TlcPredictionResult | TlcConditionComparisonResult | TlcReverseResult:
        """Worker entrypoint using the server-owned execution snapshot.

        The worker has already reloaded and validated normalized structures in
        ``PredictionInputSnapshot``.  A context variable keeps concurrent
        jobs isolated and avoids a second synchronous database dependency.
        """

        if len(normalized_structures) != 1 or not isinstance(normalized_structures[0], str):
            raise ValueError("TLC execution snapshot must contain one normalized structure")
        token = _WORKER_NORMALIZED_SMILES.set(normalized_structures[0])
        try:
            return self.predict(request)
        finally:
            _WORKER_NORMALIZED_SMILES.reset(token)


__all__ = ["TlcPredictionAdapter"]
