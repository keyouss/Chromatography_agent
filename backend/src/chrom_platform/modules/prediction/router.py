"""Unregistered prediction router factory."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Callable, Literal, Mapping
from fastapi import APIRouter, Depends, Header, Request, Response, status
from pydantic import ConfigDict, Field
from pydantic import BaseModel, ValidationError, field_validator

from chrom_platform.core.dto import ActorContext, CapabilityHealth, SuccessEnvelope
from chrom_platform.core.errors import APIError, InputValidationError
from chrom_platform.core.idempotency import BeginResult, IdempotencyRecord, IdempotencyScope
from chrom_platform.core.projects import ConfiguredProjectReader, ProjectSelectionError, resolve_project
from chrom_platform.task_core.service import OperationContext
from chrom_platform.adapters.tlc.schema import (
    TlcConditionComparisonCreate,
    TlcReverseCreate,
)

from .adapters import ModelOption, SfcAllColumnDomain, SfcSingleColumnDomain, ToolCapability, TypeCapability
from .batch import (
    BatchPrediction,
    BatchResourceBudget,
    BatchTemplateDefinition,
    CcBatchDraftCreate,
    GcBatchDraftCreate,
    HplcBatchDraftCreate,
    NonTlcBatchModelConstraints,
    SfcBatchDraftCreate,
    TlcBatchDraftCreate,
    TlcBatchModelConstraints,
)
from .batch_health import (
    build_non_tlc_batch_model_constraints,
    build_tlc_batch_model_constraints,
    project_non_tlc_batch_template_health,
    project_tlc_batch_template_health,
)
from .ports import PredictionReadPort
from .schemas import (
    ExactModelSelection, Id, Prediction, PredictionAccepted, PredictionCreate, SfcCandidateScopePreview, SfcCandidateScopeView,
    SfcConditionsOptions, SfcCandidateScopeCreate, TlcCandidateSystem,
    TlcConditionOptions, TlcRange, TlcReverseLimits, TlcSolventOption,
)
from .service import PredictionCreateService, PredictionCreateResult, PredictionServiceError


_IDEMPOTENCY_PARAMETER = {
    "in": "header", "name": "Idempotency-Key", "required": True,
    "schema": {"type": "string", "minLength": 1, "maxLength": 128},
}
_ASYNC_RESPONSE = {
    "202": {
        "description": "预测已接受",
        "headers": {"Location": {"required": True, "schema": {"type": "string", "format": "uri-reference"}}},
    }
}


class ChromatographyTypeSummary(BaseModel):
    """Small capability listing required by the public API contract."""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    tools: list[str]
    health: CapabilityHealth


class TlcBatchToolCapability(ToolCapability):
    """Typed TLC batch capability; template metadata stays in the public contract."""

    id: Literal["batch"]
    chromatography_type: Literal["tlc"]
    limits: BatchResourceBudget
    model_constraints: TlcBatchModelConstraints
    templates: list[BatchTemplateDefinition]


class NonTlcBatchToolCapability(ToolCapability):
    id: Literal["batch"]
    chromatography_type: Literal["cc", "hplc", "sfc", "gc"]
    limits: BatchResourceBudget
    model_constraints: NonTlcBatchModelConstraints
    templates: list[BatchTemplateDefinition]


class NonBatchToolCapability(ToolCapability):
    """A generic tool branch that cannot impersonate typed TLC batch metadata."""

    id: Id = Field(json_schema_extra={"not": {"const": "batch"}})

    @field_validator("id")
    @classmethod
    def reject_batch_id(cls, value: str) -> str:
        if value == "batch":
            raise ValueError("batch capability requires typed metadata")
        return value


BatchCapabilityUnion = Annotated[
    TlcBatchToolCapability | NonTlcBatchToolCapability | NonBatchToolCapability,
    Field(json_schema_extra={
        "oneOf": [
            {"$ref": "#/components/schemas/TlcBatchToolCapability"},
            {"$ref": "#/components/schemas/NonTlcBatchToolCapability"},
            {"$ref": "#/components/schemas/NonBatchToolCapability"},
        ]
    }),
]


class BatchAwareTypeCapability(TypeCapability):
    tools: list[BatchCapabilityUnion]


def _ctx(request: Request) -> OperationContext:
    actor: ActorContext | None = getattr(request.app.state, "actor_context", None)
    if actor is None:
        raise APIError(503, "DEPENDENCY_UNAVAILABLE", "ActorContext 未由服务端配置", retryable=True)
    return OperationContext(
        actor=actor,
        interaction=getattr(request.app.state, "interaction", "human"),
        request_id=getattr(request.state, "request_id", "service-request"),
        conversation_id=getattr(request.state, "conversation_id", None),
        agent_run_id=getattr(request.state, "agent_run_id", None),
    )


def _idempotency(value: str | None = Header(default=None, alias="Idempotency-Key")) -> str:
    if value is None:
        raise APIError(400, "IDEMPOTENCY_KEY_REQUIRED", "缺少 Idempotency-Key")
    if value == "" or len(value) > 128:
        raise APIError(422, "VALIDATION_FAILED", "Idempotency-Key 长度无效")
    return value


def _raise(exc: PredictionServiceError) -> None:
    raise APIError(
        exc.status_code,
        exc.payload.code,
        exc.payload.message,
        retryable=exc.payload.retryable,
        details=exc.payload.details,
        field_errors=exc.payload.field_errors,
    )


def _idempotency_digest(route: str, payload: object) -> str:
    raw = json.dumps({"route": route, "payload": payload}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _reserve_idempotency(request: Request, explicit: object | None, *, actor: ActorContext, project_id: str, route: str, key: str, payload: object) -> tuple[object, IdempotencyScope, str, IdempotencyRecord | None]:
    store = explicit or getattr(request.state, "idempotency_store", None) or getattr(request.app.state, "idempotency_store", None)
    if store is None:
        raise APIError(503, "DEPENDENCY_UNAVAILABLE", "幂等记录存储未配置", retryable=True)
    digest = _idempotency_digest(route, payload)
    scope = IdempotencyScope(actor_id=actor.actor_id, project_id=project_id, method="POST", route_identity=route, key=key)
    try:
        old = store.get(scope)
        if old is not None:
            if old.payload_hash != digest:
                raise APIError(409, "IDEMPOTENCY_KEY_REUSED", "幂等键已用于其他请求")
            if old.status == "IN_PROGRESS":
                raise APIError(409, "OPERATION_IN_PROGRESS", "相同幂等请求正在处理中", retryable=True)
            return store, scope, digest, old
        begin = store.begin(scope, digest)
    except APIError:
        raise
    except Exception as exc:
        raise APIError(503, "DEPENDENCY_UNAVAILABLE", "幂等存储暂不可用", retryable=True) from exc
    if not isinstance(begin, BeginResult) or not begin.acquired:
        record = begin.record if isinstance(begin, BeginResult) else None
        if record is None:
            raise APIError(503, "DEPENDENCY_UNAVAILABLE", "幂等存储返回无效结果", retryable=True)
        if record.payload_hash != digest:
            raise APIError(409, "IDEMPOTENCY_KEY_REUSED", "幂等键已用于其他请求")
        if record.status == "IN_PROGRESS":
            raise APIError(409, "OPERATION_IN_PROGRESS", "相同幂等请求正在处理中", retryable=True)
        return store, scope, digest, record
    return store, scope, digest, None


def _complete_idempotency(store: object, scope: IdempotencyScope, digest: str, value: object) -> None:
    try:
        store.complete(scope, digest, 200, value.model_dump(mode="json"))
    except Exception as exc:
        raise APIError(503, "DEPENDENCY_UNAVAILABLE", "幂等记录暂不可用", retryable=True) from exc


def _fail_idempotency(store: object, scope: IdempotencyScope, digest: str, code: str) -> None:
    try:
        store.fail(scope, digest, code)
    except Exception:
        # Preserve the original business/dependency error; a failed cleanup
        # must not turn it into a success or leak storage details.
        return


def _replay_idempotency(record: IdempotencyRecord, model: type[SfcCandidateScopeView]) -> SfcCandidateScopeView:
    if not record.response:
        raise APIError(409, "STATE_CONFLICT", "幂等记录缺少响应")
    return model.model_validate_json(json.dumps(record.response, ensure_ascii=False))


def create_router(
    *,
    prediction_service: PredictionCreateService | None = None,
    read_service: PredictionReadPort | None = None,
    registry: object | None = None,
    enabled_types: set[str] | frozenset[str] | None = None,
    idempotency_store: object | None = None,
    batch_metadata_provider: Callable[[str], list[BatchTemplateDefinition]] | None = None,
) -> APIRouter:
    """Build routes without registering them on an application.

    Dependencies are explicit assembly inputs or server-owned app state.  A
    missing dependency is a controlled 503; no in-memory prediction is made.
    """

    router = APIRouter(tags=["prediction"])
    enabled_type_ids = frozenset(enabled_types or ())
    profile_refs = {
        "tlc": ("#/components/schemas/TlcPredictionCreate",),
        "cc": ("#/components/schemas/CcPredictionCreate",),
        "hplc": ("#/components/schemas/HplcPredictionCreate",),
        "chiral_lc": ("#/components/schemas/ChiralLcPredictionCreate",),
        "sfc": (
            "#/components/schemas/SfcSingleColumnPredictionCreate",
            "#/components/schemas/SfcFullColumnPredictionCreate",
        ),
        "gc": ("#/components/schemas/GcPredictionCreate",),
    }
    profile_union = [
        {"$ref": ref}
        for type_id in sorted(enabled_type_ids)
        for ref in profile_refs.get(type_id, ())
    ]

    def service(request: Request) -> PredictionCreateService:
        bound = getattr(request.state, "m2_assembly", None)
        value = prediction_service or getattr(bound, "prediction_service", None) or getattr(request.app.state, "prediction_service", None)
        if value is None:
            raise APIError(503, "PREDICTION_DEPENDENCY_UNAVAILABLE", "预测服务未配置", retryable=True)
        return value

    def reader(request: Request) -> PredictionReadPort:
        bound = getattr(request.state, "m2_assembly", None)
        value = read_service or getattr(bound, "prediction_persistence", None) or getattr(request.app.state, "prediction_read_service", None)
        if value is None:
            value = prediction_service or getattr(bound, "prediction_service", None) or getattr(request.app.state, "prediction_service", None)
        if value is None or not callable(getattr(value, "get", None)):
            raise APIError(503, "PREDICTION_DEPENDENCY_UNAVAILABLE", "预测读取服务未配置", retryable=True)
        return value

    def capabilities(request: Request):
        bound = getattr(request.state, "m2_assembly", None)
        value = registry or getattr(bound, "capability_registry", None) or getattr(request.app.state, "prediction_registry", None)
        if value is None:
            raise APIError(503, "PREDICTION_DEPENDENCY_UNAVAILABLE", "预测能力注册表未配置", retryable=True)
        return value

    def batch_selected(request: Request, type_id: str) -> bool:
        if type_id not in {"tlc", "cc", "hplc", "sfc", "gc"}:
            return False
        manifest = getattr(request.app.state, "manifest", None)
        prediction_tools = getattr(manifest, "prediction_tools", None)
        if prediction_tools is None:
            return False
        tools = prediction_tools.get(type_id, ()) if hasattr(prediction_tools, "get") else ()
        return "batch" in tools

    def batch_tool(request: Request, capability_registry: object, type_id: str) -> TlcBatchToolCapability | NonTlcBatchToolCapability:
        if batch_metadata_provider is None:
            raise APIError(503, "PREDICTION_DEPENDENCY_UNAVAILABLE", "批量能力元数据未配置", retryable=True)
        try:
            executable_versions = {
                "tlc": {"tlc-batch-2"},
                "cc": {"cc-batch-2"},
                "hplc": {"hplc-batch-2"},
                "sfc": {"sfc-batch-2"},
                "gc": {"gc-batch-1"},
            }.get(type_id, set())
            templates = [item for item in batch_metadata_provider(type_id) if item.version in executable_versions]
            if not templates:
                raise APIError(503, "PREDICTION_DEPENDENCY_UNAVAILABLE", "批量模板元数据不可用", retryable=True)
            capability = capability_registry.capability(type_id)
            latest = templates[-1]
            if type_id == "tlc":
                constraints = build_tlc_batch_model_constraints(capability_registry, templates)
                projected_templates, batch_health = project_tlc_batch_template_health(templates, constraints, capability_registry)
                draft_schema = TlcBatchDraftCreate.model_json_schema()
                return TlcBatchToolCapability(
                    id="batch", chromatography_type="tlc", schema_version="1.0",
                    input_schema=draft_schema, output_schema=BatchPrediction.model_json_schema(),
                    structure_count={"min": 1, "max": 1, "semantics": "batch"},
                    model_constraints=constraints, units={}, supported_visualizations=[],
                    limits=latest.limits, template_version=latest.version, health=batch_health,
                    templates=projected_templates,
                )
            constraints = build_non_tlc_batch_model_constraints(type_id, capability_registry, templates)
            projected_templates, batch_health = project_non_tlc_batch_template_health(type_id, templates, constraints, capability_registry)
            draft_models = {"cc": CcBatchDraftCreate, "hplc": HplcBatchDraftCreate, "sfc": SfcBatchDraftCreate, "gc": GcBatchDraftCreate}
            return NonTlcBatchToolCapability(
                id="batch", chromatography_type=type_id, schema_version="1.0",
                input_schema=draft_models[type_id].model_json_schema(), output_schema=BatchPrediction.model_json_schema(),
                structure_count={"min": 1, "max": 2, "semantics": "batch"}, model_constraints=constraints,
                units={}, supported_visualizations=[], limits=latest.limits, template_version=latest.version,
                health=batch_health, templates=projected_templates,
            )
        except APIError:
            raise
        except Exception:
            raise APIError(503, "PREDICTION_DEPENDENCY_UNAVAILABLE", "批量能力元数据不可用", retryable=True) from None

    def envelope(request: Request, data):
        return SuccessEnvelope(data=data, warnings=[], request_id=getattr(request.state, "request_id", "service-request"))

    def sfc_binding(request: Request, model_id: str, model_version: str):
        adapter = capabilities(request).adapter("sfc")
        resolver = getattr(adapter, "binding_for", None)
        if callable(resolver):
            return resolver(model_id, model_version)
        runtime = getattr(adapter, "runtime", None)
        if (getattr(runtime, "bound_model_id", None), getattr(runtime, "bound_model_version", None)) == (model_id, model_version):
            return adapter
        return None

    def sfc_catalog(request: Request, *, model_id: str | None = None, model_version: str | None = None, scope_id: str | None = None):
        try:
            adapter = capabilities(request).adapter("sfc")
            if scope_id is not None:
                by_scope = getattr(adapter, "catalog_for_scope", None)
                if callable(by_scope):
                    _binding, catalog, _stored = by_scope(scope_id)
                else:
                    catalog = getattr(adapter, "candidate_catalog", None)
            elif model_id is not None and model_version is not None:
                binding = sfc_binding(request, model_id, model_version)
                catalog = getattr(binding, "candidate_catalog", None)
            else:
                catalog = getattr(adapter, "candidate_catalog", None)
            if catalog is None:
                raise KeyError("sfc candidate catalog")
            return catalog
        except KeyError:
            raise APIError(503, "PREDICTION_DEPENDENCY_UNAVAILABLE", "SFC 候选资产未配置", retryable=True) from None

    def sfc_selected_model(request: Request, selection: ExactModelSelection, *, require_ready: bool):
        if selection.mode != "exact" or selection.preprocessing_profile_id is not None or selection.preprocessing_profile_sha256 is not None:
            raise APIError(422, "VALIDATION_FAILED", "SFC 必须精确选择不带 preprocessing profile 的模型")
        registry = capabilities(request)
        capability = registry.capability("sfc")
        matches = [item for item in capability.models if item.id == selection.model_id and item.version == selection.model_version]
        if len(matches) != 1 or matches[0].sfc_domain is None:
            raise APIError(422, "VALIDATION_FAILED", "SFC 模型或模型域不存在或不唯一")
        model = matches[0]
        if require_ready and (capability.health.status != "READY" or model.health.status != "READY" or not model.artifact_sha256):
            raise APIError(503, "MODEL_UNAVAILABLE", "所选 SFC 模型尚未通过验证", retryable=True)
        domain = model.sfc_domain
        tool_id = "single_column" if isinstance(domain, SfcSingleColumnDomain) else "full_column"
        tools = [tool for tool in capability.tools if tool.id == tool_id]
        if require_ready and (len(tools) != 1 or tools[0].health.status != "READY"):
            raise APIError(503, "MODEL_UNAVAILABLE", "所选 SFC 工具不可用", retryable=True)
        if require_ready:
            binding = sfc_binding(request, model.id, model.version)
            attestation = getattr(binding, "attestation", None)
            schemas = getattr(binding, "tool_schemas", {})
            allowed_tool = getattr(binding, "allowed_tool", None)
            bound_domain = getattr(binding, "sfc_domain", None)
            if not getattr(registry, "metadata_only", False):
                handle = getattr(binding, "verified_handle", None)
                runtime = getattr(binding, "runtime", None)
                if handle is not None and runtime is not None:
                    attestation = {name: getattr(handle, name) for name in (
                        "adapter_id", "model_id", "model_version", "business_scope_id",
                        "artifact_sha256", "input_schema_version", "output_schema_version",
                    )}
                    schemas = handle.schema_by_tool
                    allowed_tool = runtime.allowed_tool
                    bound_domain = domain
            scope = getattr(model, "business_scope_id", getattr(model, "scope", None))
            if (
                not isinstance(attestation, Mapping)
                or attestation.get("adapter_id") != "sfc"
                or attestation.get("model_id") != model.id
                or attestation.get("model_version") != model.version
                or attestation.get("business_scope_id") != scope
                or attestation.get("artifact_sha256") != model.artifact_sha256
                or attestation.get("input_schema_version") != model.input_schema_version
                or attestation.get("output_schema_version") != model.output_schema_version
                or tools[0].schema_version != model.input_schema_version
                or model.input_schema_version != "1.1"
                or allowed_tool != tool_id
                or tool_id not in schemas
                or getattr(binding, "candidate_catalog", None) is None
                or bound_domain is None
            ):
                raise APIError(503, "MODEL_UNAVAILABLE", "SFC Worker 元数据与能力快照不匹配", retryable=True)
        return model, tools[0] if len(tools) == 1 else None, domain

    def sfc_project(request: Request, requested_project_id: str | None):
        actor = _ctx(request).actor
        project_reader = getattr(request.app.state, "project_reader", None)
        if project_reader is None and actor.project_ids:
            project_reader = ConfiguredProjectReader(actor.project_ids)
        if project_reader is None:
            raise APIError(503, "DEPENDENCY_UNAVAILABLE", "项目存储未配置", retryable=True)
        try:
            selected = resolve_project(actor, project_reader, requested_project_id, default_project_id=actor.default_project_id)
        except ProjectSelectionError as exc:
            raise APIError(exc.status_code, exc.code, exc.message, details=exc.details) from None
        return selected.project_id

    def sfc_candidate_scope_context(request: Request, payload: SfcCandidateScopeCreate):
        actor = _ctx(request).actor
        if not actor.can("prediction:create") and not actor.can("*"):
            raise APIError(403, "PERMISSION_DENIED", "缺少预测创建权限")
        project_id = sfc_project(request, payload.project_id)
        model_option, tool, _domain = sfc_selected_model(request, payload.model_selection, require_ready=True)
        model_id, model_version = model_option.id, model_option.version
        binding_adapter = sfc_binding(request, model_id, model_version)
        bound_tool = getattr(binding_adapter, "allowed_tool", None)
        if not getattr(capabilities(request), "metadata_only", False):
            bound_tool = getattr(getattr(binding_adapter, "runtime", None), "allowed_tool", None)
        if (
            binding_adapter is None
            or getattr(binding_adapter, "candidate_catalog", None) is None
            or bound_tool is None
        ):
            raise APIError(503, "MODEL_UNAVAILABLE", "所选 SFC 模型未绑定到 Worker 元数据", retryable=True)
        catalog = sfc_catalog(request, model_id=model_id, model_version=model_version)
        if tool.id != bound_tool:
            raise APIError(422, "VALIDATION_FAILED", "所选 SFC 模型与工具范围不一致")
        return actor, project_id, model_id, model_version, catalog

    def tlc_service(request: Request):
        try:
            adapter = capabilities(request).adapter("tlc")
            if capabilities(request).capability("tlc").health.status != "READY":
                raise APIError(503, "MODEL_UNAVAILABLE", "TLC 真实模型不可用", retryable=True)
            if getattr(capabilities(request), "metadata_only", False):
                return adapter, None
            value = getattr(adapter, "utility_service", None)
            if value is None:
                raise APIError(503, "MODEL_UNAVAILABLE", "TLC 真实模型不可用", retryable=True)
            return adapter, value
        except APIError:
            raise
        except KeyError:
            raise APIError(503, "MODEL_UNAVAILABLE", "TLC 真实模型不可用", retryable=True) from None

    def _tlc_number_bound(schema: dict[str, object], field: str, key: str) -> float | bool | None:
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            return None
        value = properties.get(field)
        if not isinstance(value, dict):
            return None
        if key == "min_inclusive":
            return "exclusiveMinimum" not in value
        if key == "max_inclusive":
            return "exclusiveMaximum" not in value
        raw = value.get("minimum" if key == "min" else "maximum")
        if raw is None and key == "min":
            raw = value.get("exclusiveMinimum")
        if raw is None and key == "max":
            raw = value.get("exclusiveMaximum")
        return raw if isinstance(raw, (int, float)) and not isinstance(raw, bool) else None

    def _tlc_options(request: Request, adapter: object) -> TlcConditionOptions:
        try:
            capability = capabilities(request).capability("tlc")
            single = next(tool for tool in capability.tools if tool.id == "single")
            reverse = next(tool for tool in capability.tools if tool.id == "reverse_solvent")
            constraints = single.model_constraints
            order = constraints.get("solvent_order")
            systems = constraints.get("allowed_systems")
            if not isinstance(order, list) or not order or any(not isinstance(item, str) or not item.strip() for item in order):
                raise ValueError("TLC solvent_order is missing")
            if not isinstance(systems, list) or not systems:
                raise ValueError("TLC allowed_systems is missing")
            candidate_systems: list[TlcCandidateSystem] = []
            for raw in systems:
                if not isinstance(raw, list) or len(raw) < 2 or any(not isinstance(item, str) for item in raw):
                    raise ValueError("TLC allowed_systems is invalid")
                ids = list(dict.fromkeys(raw))
                candidate_systems.append(TlcCandidateSystem(id="/".join(ids), solvent_ids=ids))
            input_schema = reverse.input_schema
            target_rf = TlcRange(
                min=float(_tlc_number_bound(input_schema, "target_rf_min", "min") or 0.0),
                max=float(_tlc_number_bound(input_schema, "target_rf_min", "max") or 1.0),
                min_inclusive=_tlc_number_bound(input_schema, "target_rf_min", "min_inclusive") is not False,
                max_inclusive=_tlc_number_bound(input_schema, "target_rf_min", "max_inclusive") is not False,
            )
            target_rf_max = _tlc_number_bound(input_schema, "target_rf_max", "max")
            if target_rf_max is not None:
                target_rf = target_rf.model_copy(update={"max": float(target_rf_max)})
            ratio_step = TlcRange(
                min=float(_tlc_number_bound(input_schema, "ratio_step", "min") or 0.0),
                max=float(_tlc_number_bound(input_schema, "ratio_step", "max") or 1.0),
                min_inclusive=_tlc_number_bound(input_schema, "ratio_step", "min_inclusive") is not False,
                max_inclusive=_tlc_number_bound(input_schema, "ratio_step", "max_inclusive") is not False,
            )
            max_candidates = reverse.limits.max_candidates
            max_candidates_schema = _tlc_number_bound(input_schema, "max_candidates", "max")
            if max_candidates_schema is not None:
                max_candidates = min(max_candidates, int(max_candidates_schema))
            ratio = TlcRange(min=0.0, max=1.0)
            models = [item for item in capability.models if item.health.status == "READY"]
            binding_for = getattr(adapter, "binding_for", None)
            if len(models) != 1 or (callable(binding_for) and binding_for(models[0].id, models[0].version) is None):
                raise ValueError("TLC Worker binding is unavailable")
            model = models[0]
            return TlcConditionOptions(
                schema_version=single.schema_version,
                model_id=model.id,
                model_version=model.version,
                artifact_sha256=model.artifact_sha256,
                solvent_options=[TlcSolventOption(id=item) for item in order],
                candidate_systems=candidate_systems,
                ratio=ratio,
                reverse=TlcReverseLimits(
                    target_rf=target_rf,
                    ratio_step=ratio_step,
                    max_candidates=max_candidates,
                    max_evaluations=reverse.limits.max_evaluations,
                ),
            )
        except (AttributeError, KeyError, TypeError, ValueError):
            raise APIError(503, "MODEL_CONTRACT_MISSING", "TLC 条件能力契约不可用", retryable=True) from None

    @router.get("/tlc/conditions/options", response_model=SuccessEnvelope[TlcConditionOptions], operation_id="get_tlc_condition_options")
    def get_tlc_condition_options(request: Request):
        adapter, _ = tlc_service(request)
        return envelope(request, _tlc_options(request, adapter))

    @router.post(
        "/tlc/condition-comparisons",
        response_model=SuccessEnvelope[PredictionAccepted],
        status_code=status.HTTP_202_ACCEPTED,
        operation_id="create_tlc_condition_comparison",
        openapi_extra={"parameters": [_IDEMPOTENCY_PARAMETER], "responses": _ASYNC_RESPONSE},
    )
    def create_tlc_condition_comparison(
        payload: TlcConditionComparisonCreate,
        request: Request,
        response: Response,
        idempotency_key: str = Depends(_idempotency),
    ):
        return create_prediction(payload, request, response, idempotency_key)

    @router.post(
        "/tlc/reverse-solvent",
        response_model=SuccessEnvelope[PredictionAccepted],
        status_code=status.HTTP_202_ACCEPTED,
        operation_id="create_tlc_reverse_solvent",
        openapi_extra={"parameters": [_IDEMPOTENCY_PARAMETER], "responses": _ASYNC_RESPONSE},
    )
    def create_tlc_reverse_solvent(
        payload: TlcReverseCreate,
        request: Request,
        response: Response,
        idempotency_key: str = Depends(_idempotency),
    ):
        return create_prediction(payload, request, response, idempotency_key)

    @router.get(
        "/sfc/conditions/options",
        response_model=SuccessEnvelope[SfcConditionsOptions],
        operation_id="get_sfc_condition_options",
    )
    def get_sfc_condition_options(
        request: Request,
        scope_id: str | None = None,
        model_id: str | None = None,
        model_version: str | None = None,
        project_id: str | None = None,
    ):
        scope_mode = scope_id is not None
        model_mode = model_id is not None and model_version is not None
        if scope_mode == model_mode or ((model_id is None) != (model_version is None)):
            raise APIError(422, "VALIDATION_FAILED", "必须且只能提供 scope_id 或完整的 model_id 与 model_version")
        try:
            selected_project_id = sfc_project(request, project_id)
            if scope_mode:
                catalog = sfc_catalog(request, scope_id=scope_id)
                binding = catalog.scope_binding(scope_id)
                if binding is None or binding["project_id"] != selected_project_id:
                    raise KeyError(scope_id)
                selection = ExactModelSelection(mode="exact", model_id=binding["model_id"], model_version=binding["model_version"])
                model, _tool, domain = sfc_selected_model(request, selection, require_ready=False)
                raw = catalog.options(
                    scope_id,
                    project_id=selected_project_id,
                    model_id=model.id,
                    model_version=model.version,
                )
            else:
                selection = ExactModelSelection(mode="exact", model_id=model_id, model_version=model_version)
                model, _tool, domain_dto = sfc_selected_model(request, selection, require_ready=False)
                binding = sfc_binding(request, model.id, model.version)
                domain = getattr(binding, "sfc_domain", None)
                entry = getattr(binding, "registry_entry", None)
                if not getattr(capabilities(request), "metadata_only", False):
                    runtime = getattr(binding, "runtime", None)
                    entry_reader = getattr(getattr(runtime, "registry", None), "entry", None)
                    if callable(entry_reader):
                        runtime_entry = entry_reader(model.id, version=model.version)
                        from chrom_platform.adapters.sfc.runtime import SfcModelDomain
                        domain = SfcModelDomain.from_registry_entry(runtime_entry)
                        entry = {name: getattr(runtime_entry, name) for name in (
                            "scope", "single_column_slug", "single_column_group",
                            "stationary_phase", "mode", "allowed_columns",
                        )}
                if domain is None or entry is None:
                    raise APIError(503, "PREDICTION_DEPENDENCY_UNAVAILABLE", "SFC 模型目录不可用", retryable=True)
                catalog = sfc_catalog(request, model_id=model.id, model_version=model.version)
                if domain_dto.model_dump(mode="json") != (
                    {
                        "model_scope": "single_column", "group_id": entry.get("single_column_slug"),
                        "group_label": entry.get("single_column_group"), "stationary_phase": entry.get("stationary_phase"),
                        "mode": entry.get("mode"), "allowed_columns": list(entry.get("allowed_columns") or ()),
                    } if entry.get("scope") == "single_column" else {"model_scope": "all_column"}
                ):
                    raise APIError(422, "VALIDATION_FAILED", "SFC 模型目录与能力域不一致")
                raw = catalog.options_for_model(domain)
            raw.update(
                project_id=selected_project_id,
                model_id=model.id,
                model_version=model.version,
                threshold=model.threshold,
                sfc_domain=domain_dto.model_dump(mode="json") if not scope_mode else domain.model_dump(mode="json"),
            )
            data = SfcConditionsOptions.model_validate(raw)
        except KeyError:
            raise APIError(404, "RESOURCE_NOT_FOUND", "SFC 候选范围不存在") from None
        except APIError:
            raise
        except Exception:
            raise APIError(503, "PREDICTION_DEPENDENCY_UNAVAILABLE", "SFC 候选资产不可用", retryable=True) from None
        return envelope(request, data)

    @router.post(
        "/sfc/candidate-scopes/preview",
        response_model=SuccessEnvelope[SfcCandidateScopePreview],
        operation_id="preview_sfc_candidate_scope",
    )
    def preview_sfc_candidate_scope(payload: SfcCandidateScopeCreate, request: Request):
        try:
            _actor, project_id, model_id, model_version, catalog = sfc_candidate_scope_context(request, payload)
            raw = catalog.preview_scope(
                payload.axes,
                project_id=project_id,
                model_id=model_id,
                model_version=model_version,
                max_candidates=payload.max_candidates,
                expansion_mode=payload.expansion_mode,
            )
            status_map = {"ready": "READY", "empty": "EMPTY", "over_limit": "OVER_LIMIT"}
            data = SfcCandidateScopePreview.model_validate({
                "project_id": raw["project_id"],
                "model_id": raw["model_id"],
                "model_version": raw["model_version"],
                "matched_count": raw["candidate_count"],
                "max_candidates": raw["max_candidates"],
                "expansion_mode": raw["expansion_mode"],
                "policy": raw.get("policy"),
                "observed_count": raw.get("observed_count"),
                "unobserved_count": raw.get("unobserved_count"),
                "status": status_map[raw["status"]],
            })
        except APIError:
            raise
        except ValueError as exc:
            raise APIError(422, "VALIDATION_FAILED", str(exc)) from None
        except Exception:
            raise APIError(503, "PREDICTION_DEPENDENCY_UNAVAILABLE", "SFC 候选资产不可用", retryable=True) from None
        return envelope(request, data)

    @router.post(
        "/sfc/candidate-scopes",
        response_model=SuccessEnvelope[SfcCandidateScopeView],
        operation_id="create_sfc_candidate_scope",
        openapi_extra={"parameters": [_IDEMPOTENCY_PARAMETER]},
    )
    def list_sfc_candidate_scopes(payload: SfcCandidateScopeCreate, request: Request, response: Response, idempotency_key: str = Depends(_idempotency)):
        from chrom_platform.adapters.sfc.runtime import SfcCandidateScopeSelectionError

        store = scope = digest = None
        try:
            actor, project_id, model_id, model_version, catalog = sfc_candidate_scope_context(request, payload)
            store, scope, digest, replay = _reserve_idempotency(
                request,
                idempotency_store,
                actor=actor,
                project_id=project_id,
                route="/api/v1/sfc/candidate-scopes",
                key=idempotency_key,
                payload=payload.model_dump(mode="json"),
            )
            if replay is not None:
                data = _replay_idempotency(replay, SfcCandidateScopeView)
                response.headers["Idempotency-Replayed"] = "true"
                return envelope(request, data)
            data = SfcCandidateScopeView.model_validate(
                catalog.create_scope(
                    payload.axes,
                    project_id=project_id,
                    model_id=model_id,
                    model_version=model_version,
                    max_candidates=payload.max_candidates,
                    expansion_mode=payload.expansion_mode,
                )
            )
            _complete_idempotency(store, scope, digest, data)
        except SfcCandidateScopeSelectionError as exc:
            code = "SFC_CANDIDATE_SCOPE_EMPTY" if exc.reason == "empty" else "SFC_CANDIDATE_SCOPE_LIMIT_EXCEEDED"
            if store is not None and scope is not None and digest is not None:
                _fail_idempotency(store, scope, digest, "VALIDATION_FAILED")
            raise APIError(422, code, str(exc), details={
                "matched_count": exc.matched_count,
                "max_candidates": exc.max_candidates,
                "expansion_mode": exc.expansion_mode,
                "observed_count": exc.observed_count,
                "unobserved_count": exc.unobserved_count,
            }) from None
        except ValueError as exc:
            if store is not None and scope is not None and digest is not None:
                _fail_idempotency(store, scope, digest, "VALIDATION_FAILED")
            raise APIError(422, "VALIDATION_FAILED", str(exc)) from None
        except APIError:
            if store is not None and scope is not None and digest is not None:
                _fail_idempotency(store, scope, digest, "API_ERROR")
            raise
        except Exception:
            if store is not None and scope is not None and digest is not None:
                _fail_idempotency(store, scope, digest, "PREDICTION_DEPENDENCY_UNAVAILABLE")
            raise APIError(503, "PREDICTION_DEPENDENCY_UNAVAILABLE", "SFC 候选资产不可用", retryable=True) from None
        return envelope(request, data)

    @router.get("/prediction/types", response_model=SuccessEnvelope[list[ChromatographyTypeSummary]], operation_id="list_prediction_types")
    def list_prediction_types(request: Request, value=Depends(capabilities)):
        try:
            include_batch = any(batch_selected(request, type_id) for type_id in ("tlc", "cc", "hplc", "sfc", "gc"))
            if include_batch:
                for type_id in ("tlc", "cc", "hplc", "sfc", "gc"):
                    if batch_selected(request, type_id):
                        batch_tool(request, value, type_id)
            data = [
                ChromatographyTypeSummary(
                    id=item.id,
                    display_name=item.display_name,
                    tools=[tool.id for tool in item.tools] + (["batch"] if batch_selected(request, item.id) else []),
                    health=item.health,
                )
                for item in value.capabilities()
            ]
        except Exception as exc:  # noqa: BLE001 - registry errors are dependency failures
            if isinstance(exc, APIError):
                raise
            raise APIError(503, "PREDICTION_DEPENDENCY_UNAVAILABLE", "预测能力暂不可用", retryable=True) from None
        return envelope(request, data)

    @router.get("/prediction/types/{type}", response_model=SuccessEnvelope[BatchAwareTypeCapability], operation_id="get_prediction_type")
    def get_prediction_type(type: str, request: Request, value=Depends(capabilities)):
        try:
            data = value.capability(type)
        except KeyError:
            raise APIError(404, "RESOURCE_NOT_FOUND", "预测类型不存在或未构建") from None
        if batch_selected(request, type):
            batch = batch_tool(request, value, type)
            try:
                generic_tools = [
                    NonBatchToolCapability.model_validate(tool.model_dump(mode="python")).model_dump(mode="python")
                    for tool in data.tools
                ]
                data = BatchAwareTypeCapability.model_validate({
                    **data.model_dump(mode="python"),
                    "tools": [*generic_tools, batch.model_dump(mode="python")],
                })
            except ValidationError:
                raise APIError(503, "PREDICTION_DEPENDENCY_UNAVAILABLE", "批量能力元数据不可用", retryable=True) from None
        return envelope(request, data)

    @router.get("/prediction/types/{type}/tools/{tool}/schema", response_model=SuccessEnvelope[BatchCapabilityUnion], operation_id="get_prediction_tool_schema")
    def get_prediction_tool_schema(type: str, tool: str, request: Request, schema_version: str = "1.0", value=Depends(capabilities)):
        if tool == "batch":
            if not batch_selected(request, type):
                raise APIError(404, "RESOURCE_NOT_FOUND", "预测工具不存在或未构建")
            if schema_version != "1.0":
                raise APIError(422, "VALIDATION_FAILED", "预测工具或 schema 版本不受支持")
            return envelope(request, batch_tool(request, value, type))
        try:
            data = value.get_tool(type, tool, schema_version)
        except InputValidationError as exc:
            raise APIError(422, "VALIDATION_FAILED", "预测工具或 schema 版本不受支持") from exc
        except KeyError:
            raise APIError(404, "RESOURCE_NOT_FOUND", "预测工具不存在或未构建") from None
        if batch_selected(request, type):
            try:
                data = NonBatchToolCapability.model_validate(data.model_dump(mode="python"))
            except ValidationError:
                raise APIError(503, "PREDICTION_DEPENDENCY_UNAVAILABLE", "预测工具能力元数据不可用", retryable=True) from None
        return envelope(request, data)

    @router.get("/prediction/types/{type}/models", response_model=SuccessEnvelope[list[ModelOption]], operation_id="list_prediction_models")
    def list_prediction_models(type: str, request: Request, value=Depends(capabilities)):
        try:
            data = value.capability(type).models
        except KeyError:
            raise APIError(404, "RESOURCE_NOT_FOUND", "预测类型不存在或未构建") from None
        return envelope(request, data)

    @router.post(
        "/predictions",
        response_model=SuccessEnvelope[PredictionAccepted],
        status_code=status.HTTP_202_ACCEPTED,
        operation_id="create_prediction",
        openapi_extra={
            "parameters": [_IDEMPOTENCY_PARAMETER],
            "responses": _ASYNC_RESPONSE,
            **(
                {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {"schema": {"oneOf": profile_union}}
                        },
                    }
                }
                if profile_union
                else {}
            ),
        },
    )
    def create_prediction(
        payload: PredictionCreate,
        request: Request,
        response: Response,
        idempotency_key: str = Depends(_idempotency),
    ):
        if enabled_type_ids and payload.chromatography_type not in enabled_type_ids:
            raise APIError(422, "PREDICTION_DISCRIMINATOR_INVALID", "当前构建未包含该预测类型")
        actor = _ctx(request).actor
        project_reader = getattr(request.app.state, "project_reader", None)
        if project_reader is None and actor.project_ids:
            project_reader = ConfiguredProjectReader(actor.project_ids)
        if project_reader is None:
            raise APIError(503, "DEPENDENCY_UNAVAILABLE", "项目存储未配置", retryable=True)
        try:
            selected = resolve_project(
                actor,
                project_reader,
                getattr(payload, "project_id", None),
                default_project_id=actor.default_project_id,
            )
        except ProjectSelectionError as exc:
            raise APIError(exc.status_code, exc.code, exc.message, details=exc.details) from None
        payload = payload.model_copy(update={"project_id": selected.project_id})
        value = service(request)
        try:
            result = value.create(_ctx(request), payload, idempotency_key=idempotency_key)
        except PredictionServiceError as exc:
            _raise(exc)
        if isinstance(result, PredictionCreateResult):
            accepted = result.data
            replayed = result.replayed
        elif isinstance(result, PredictionAccepted):
            accepted, replayed = result, False
        else:
            raise APIError(503, "PREDICTION_DEPENDENCY_UNAVAILABLE", "预测服务未返回有效受理结果", retryable=True)
        response.headers["Location"] = f"/api/v1/predictions/{accepted.id}"
        if replayed:
            response.headers["Idempotency-Replayed"] = "true"
        return envelope(request, accepted)

    @router.get("/predictions/{prediction_id}", response_model=SuccessEnvelope[Prediction], operation_id="get_prediction")
    def get_prediction(prediction_id: str, request: Request, value: PredictionReadPort = Depends(reader)):
        try:
            data = value.get(_ctx(request), prediction_id)
        except APIError:
            raise
        except Exception:
            raise APIError(503, "PREDICTION_DEPENDENCY_UNAVAILABLE", "预测读取暂不可用", retryable=True) from None
        if enabled_type_ids and getattr(data, "chromatography_type", None) not in enabled_type_ids:
            raise APIError(503, "PREDICTION_TYPE_UNAVAILABLE", "当前构建未包含该预测类型", retryable=True)
        return envelope(request, data)

    if enabled_types is not None and "tlc" not in enabled_type_ids:
        router.routes[:] = [route for route in router.routes if not route.path.startswith("/tlc/")]
    if enabled_types is not None and "sfc" not in enabled_type_ids:
        router.routes[:] = [route for route in router.routes if not route.path.startswith("/sfc/")]
    return router


create_prediction_router = create_router

__all__ = ["create_router", "create_prediction_router"]
