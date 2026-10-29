"""Batch prediction orchestration over caller-owned SQL persistence.

The batch ORM projections live in batch_models; this service delegates
job/outbox creation to task_core.  It never reads a server path from a batch
request; the input is always an Artifact id.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import io
import zipfile
from collections.abc import Collection, Mapping, Sequence
from typing import Any, Callable, Protocol
from uuid import uuid4

from sqlalchemy import JSON, MetaData, String, Text, select
from sqlalchemy.orm import Session
from pydantic import TypeAdapter

from chrom_platform.artifact_core.models import Artifact
from chrom_platform.artifact_core.service import ArtifactServiceError
from chrom_platform.core.dto import ActorContext, ResourceRef
from chrom_platform.core.cursor import CursorError, SignedCursorCodec
from chrom_platform.core.errors import APIError
from chrom_platform.infrastructure.models import AuditEvent, Job, JobAttempt, OutboxEvent
from chrom_platform.infrastructure.repositories import SQLAlchemyStructureRepository
from chrom_platform.structure_core.schemas import NormalizeStructureRequest
from chrom_platform.structure_core.service import InvalidStructure, StructureService
from chrom_platform.task_core.repository import SQLAlchemyTaskRepository
from chrom_platform.task_core.service import OperationContext, TargetSnapshot, TaskError, add_job_attempt_rows, add_job_rows, sync_job_lifecycle
from chrom_platform.task_core.state import JobStatus

from .batch import (
    BatchResolvedInput,
    BatchAccepted,
    BatchCancel,
    BatchCounts,
    BatchDraft,
    BatchDraftCreate,
    BatchDraftPatch,
    BatchExportAccepted,
    BatchExportCreate,
    BatchExportView,
    BatchPrediction,
    BatchRetryFailed,
    BatchRetryPlan,
    BatchRetryPlanCreate,
    BatchResourceBudget,
    BatchRowResult,
    BatchRowsPage,
    BatchTemplate,
    BatchValidation,
    BatchValidationAccepted,
    BatchValidationRowsPage,
    BatchSubmit,
    RowValidation,
    BatchTemplateRegistry,
    BatchTemplate,
    BatchTemplateDefinition,
    TlcBatchModelConstraints,
    TlcBatchRow,
    TlcBatchRowV2,
    TlcBatchResolvedInputV1,
    TlcBatchResolvedInputV2,
    CcBatchResolvedInputV2,
    HplcBatchResolvedInputV2,
    SfcBatchResolvedInputV2,
    GcBatchResolvedInputV1,
    TlcBatchTemplateOptions,
    CcBatchRow,
    CcBatchRowV2,
    HplcBatchRow,
    HplcBatchRowV2,
    SfcBatchRow,
    SfcBatchRowV2,
    GcBatchRow,
)
from chrom_platform.core.dto import CapabilityHealth
from chrom_platform.modules.prediction.schemas import (
    CcPredictionCreate,
    GcPredictionCreate,
    HplcPredictionCreate,
    ModelSnapshot,
    SfcFullColumnPredictionCreate,
    SfcSingleColumnPredictionCreate,
    SfcCandidateScopeProvenance,
    TlcCandidateSystem,
    TlcInput,
    TlcSolventComponent,
    TlcSolventOption,
)
from chrom_platform.modules.prediction.schemas import ModelSelection

from .batch_errors import BatchServiceError
from .batch_models import (
    BatchPredictionRow, BatchValidationRow, BatchValidationItemRow,
    BatchRowResultRow, BatchRetryPlanRow, BatchExportRow,
)
from .row_prepare import (
    MAX_BATCH_ROWS, _IDENTITY_FIELDS, _decode_upload_cell, _validated_rows,
    _parse_csv, _xlsx_cell_value, _parse_xlsx,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _etag(value: Any) -> str:
    return _hash(value)


def _hashable_batch_config(config: dict[str, Any]) -> dict[str, Any]:
    """Copy a batch config and omit only the new null profile-pair defaults."""
    normalized = dict(config)
    selection = normalized.get("model_selection")
    if isinstance(selection, dict) and (
        selection.get("preprocessing_profile_id") is None
        and selection.get("preprocessing_profile_sha256") is None
    ):
        selection = dict(selection)
        selection.pop("preprocessing_profile_id", None)
        selection.pop("preprocessing_profile_sha256", None)
        normalized["model_selection"] = selection
    return normalized


def _batch_config_hash(config: dict[str, Any]) -> str:
    return _hash(_hashable_batch_config(config))


def _batch_config_etag(*, resource_id: str, revision: int, config: dict[str, Any]) -> str:
    return _etag({
        "id": resource_id,
        "revision": revision,
        "config": _hashable_batch_config(config),
    })


def _as_json(value: Any) -> Any:
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def _batch_cursor_scope(
    ctx: OperationContext,
    batch: "BatchPredictionRow",
    validation: "BatchValidationRow | None",
    *,
    status_filter: str | None,
    endpoint: str,
) -> str:
    return _hash({
        "actor_id": ctx.actor.actor_id,
        "project_id": batch.project_id,
        "batch_id": batch.id,
        "batch_revision": batch.revision,
        "validation_id": batch.validation_id,
        "validation_draft_revision": validation.draft_revision if validation is not None else None,
        "validation_status": validation.status if validation is not None else None,
        "status_filter": status_filter,
        "endpoint": endpoint,
        "sort": "source_row_number.asc,row_id.asc",
    })


def _after_row_cursor(
    items: list[Any], cursor: str | None, *, codec: SignedCursorCodec, scope: str
) -> list[Any]:
    if cursor is None:
        return items
    try:
        position = codec.decode(cursor, scope=scope)
    except CursorError as exc:
        raise BatchServiceError(400, "VALIDATION_FAILED", "分页游标无效或与当前筛选不匹配") from exc
    for index, item in enumerate(items):
        if (int(item.source_row_number), item.row_id) == position:
            return items[index + 1:]
    raise BatchServiceError(400, "VALIDATION_FAILED", "分页游标位置不存在")


def _require_batch_cursor_codec(codec: SignedCursorCodec | None) -> SignedCursorCodec:
    if not isinstance(codec, SignedCursorCodec):
        raise BatchServiceError(503, "DEPENDENCY_UNAVAILABLE", "批量分页签名配置未就绪")
    return codec


def _tlc_template_options(capabilities: object | None) -> TlcBatchTemplateOptions | None:
    """Build choices only from the registered TLC single-tool metadata."""
    getter = getattr(capabilities, "capability", None)
    if not callable(getter):
        return None
    try:
        capability = getter("tlc")
        tools = [tool for tool in capability.tools if tool.id == "single"]
        if len(tools) != 1:
            return None
        constraints = tools[0].model_constraints
        solvent_order = constraints.get("solvent_order")
        raw_systems = constraints.get("allowed_systems")
        if (not isinstance(solvent_order, list) or not solvent_order
                or len(solvent_order) != len(set(solvent_order))
                or any(not isinstance(name, str) or not name for name in solvent_order)
                or not isinstance(raw_systems, list) or not raw_systems):
            return None
        allowed_systems: list[TlcCandidateSystem] = []
        for item in raw_systems:
            if (not isinstance(item, (list, tuple)) or len(item) != 2
                    or any(name not in solvent_order for name in item)
                    or item[0] == item[1]):
                return None
            solvent_ids = list(item)
            allowed_systems.append(TlcCandidateSystem(id="/".join(solvent_ids), solvent_ids=solvent_ids))
        if len({item.id for item in allowed_systems}) != len(allowed_systems):
            return None
        return TlcBatchTemplateOptions(
            solvent_options=[TlcSolventOption(id=name) for name in solvent_order],
            allowed_systems=allowed_systems,
        )
    except (AttributeError, TypeError, ValueError):
        return None


def default_batch_template_registry(capabilities: object | None = None) -> BatchTemplateRegistry:
    """Register only the five build-owned batch schemas."""
    registry = BatchTemplateRegistry()
    limits = BatchResourceBudget(max_runtime_seconds=3600, max_evaluations=100_000)
    health = CapabilityHealth(status="UNKNOWN", reason="MODEL_CHECK_DEFERRED", checked_at=_now())
    tlc_options = _tlc_template_options(capabilities)
    registry.register(
        BatchTemplate(id="tlc_batch", chromatography_type="tlc", tool="batch", version="tlc-batch-1",
                      row_schema=TlcBatchRow.__name__, fields=["structure_ref", "solvent_components"],
                      limits=limits, health=CapabilityHealth(
                          status="UNAVAILABLE", reason="LEGACY_BATCH_TEMPLATE_READ_ONLY", checked_at=_now(),
                      )),
        row_model=TlcBatchRow,
    )
    registry.register(
        BatchTemplate(id="tlc_batch", chromatography_type="tlc", tool="batch", version="tlc-batch-2",
                      row_schema=TlcBatchRowV2.__name__,
                      fields=["smiles", "solvent_1", "fraction_1", "solvent_2", "fraction_2"],
                      limits=limits, health=health, tlc_options=tlc_options),
        row_model=TlcBatchRowV2,
    )
    definitions = (
        ("cc", CcBatchRow, ["structure_ref", "eluent_system_id", "loading_solvent_id", "sample_mass_mg", "loading_solvent_volume_ul", "flow_rate_ml_min", "column_spec_id"]),
        ("hplc", HplcBatchRow, ["structure_ref_a", "structure_ref_b", "isopropanol_fraction", "flow_rate_ml_min", "column_ids"]),
        ("sfc", SfcBatchRow, ["structure_ref_a", "structure_ref_b", "modifier_total_fraction", "modifier_components", "candidate_scope_id", "top_k", "column_id"]),
        ("gc", GcBatchRow, ["structure_ref", "temperature_program"]),
    )
    for type_id, row_model, fields in definitions:
        unavailable_reasons = {
            "cc": "CC_BATCH_EXECUTION_UNVERIFIED",
            "hplc": "HPLC_BATCH_RAW_INPUT_UNAVAILABLE",
        }
        template_health = (
            CapabilityHealth(status="UNAVAILABLE", reason=unavailable_reasons[type_id], checked_at=_now())
            if type_id in unavailable_reasons else health
        )
        registry.register(
            BatchTemplate(
                id=f"{type_id}_batch",
                chromatography_type=type_id,
                tool="batch",
                version=f"{type_id}-batch-1",
                row_schema=row_model.__name__,
                fields=fields,
                limits=limits,
                health=template_health,
            ),
            row_model=row_model,
        )
    registry.register(
        BatchTemplate(id="cc_batch", chromatography_type="cc", tool="batch", version="cc-batch-2",
                      row_schema=CcBatchRowV2.__name__,
                      fields=["structure_ref_a", "structure_ref_b", "eluent_system_id", "loading_solvent_id", "sample_mass_mg", "loading_solvent_volume_ul", "flow_rate_ml_min", "column_spec_id"],
                      limits=limits, health=health),
        row_model=CcBatchRowV2,
    )
    registry.register(
        BatchTemplate(id="hplc_batch", chromatography_type="hplc", tool="batch", version="hplc-batch-2",
                      row_schema=HplcBatchRowV2.__name__,
                      fields=["smiles_a", "smiles_b", "isopropanol_fraction", "flow_rate_ml_min", "column_ids"],
                      limits=limits, health=health),
        row_model=HplcBatchRowV2,
    )
    registry.register(
        BatchTemplate(id="sfc_batch", chromatography_type="sfc", tool="batch", version="sfc-batch-2",
                      row_schema=SfcBatchRowV2.__name__,
                      fields=["structure_ref_a", "structure_ref_b", "modifier_total_fraction", "modifier_components", "candidate_scope_id", "top_k", "tool", "column_id"],
                      limits=limits, health=health),
        row_model=SfcBatchRowV2,
    )
    return registry


def build_batch_template_metadata(
    capabilities: object | None = None,
    *,
    chromatography_type: str | None = None,
) -> list[BatchTemplateDefinition]:
    """Return typed template metadata without changing template/runtime health."""
    templates = default_batch_template_registry(capabilities).list(chromatography_type)
    return [BatchTemplateDefinition.model_validate(template.model_dump(mode="python")) for template in templates]


class BatchPredictionService:
    _KNOWN_BATCH_TYPES = frozenset({"tlc", "cc", "hplc", "gc", "sfc"})

    def __init__(self, session: Session | None = None, *, artifact: object | None = None, confirmation: object | None = None, capabilities: object | None = None, prediction_create_service: object | None = None, template_registry: BatchTemplateRegistry | None = None, audit: object | None = None, idempotency: object | None = None, enabled_batch_types: Collection[str] | None = None, tlc_batch_constraints_provider: Callable[[object, Sequence[BatchTemplateDefinition]], TlcBatchModelConstraints] | None = None) -> None:
        self.session = session
        self.artifact = artifact
        self.confirmation = confirmation
        self.capabilities = capabilities
        self.prediction_create_service = prediction_create_service
        self.template_registry = template_registry or default_batch_template_registry()
        selected_types = frozenset(enabled_batch_types or ())
        if not selected_types.issubset(self._KNOWN_BATCH_TYPES):
            raise ValueError("enabled_batch_types contains an unsupported chromatography type")
        self.enabled_batch_types = selected_types
        # Tests may inject a fake binding provider; production leaves this unset
        # and uses the verified registry-backed batch_health helper.
        self._tlc_batch_constraints_provider = tlc_batch_constraints_provider
        self.audit = audit
        self.idempotency = idempotency

    def _db(self) -> Session:
        if self.session is None:
            raise BatchServiceError(503, "DEPENDENCY_UNAVAILABLE", "批量预测持久化未配置", retryable=True)
        return self.session

    @staticmethod
    def _authorize(ctx: OperationContext, project_id: str, permission: str) -> None:
        if not ctx.actor.can_access_project(project_id):
            raise BatchServiceError(404, "RESOURCE_NOT_FOUND", "批量预测不存在或当前主体不可见")
        if not ctx.actor.can(permission) and not ctx.actor.can("*"):
            raise BatchServiceError(403, "PERMISSION_DENIED", "当前主体无权执行该操作")

    def _ensure_type_enabled(self, type_id: str | None) -> None:
        if type_id not in self.enabled_batch_types:
            raise BatchServiceError(503, "BATCH_TYPE_NOT_ENABLED", "该色谱类型未包含批量能力")

    def _get(self, ctx: OperationContext, batch_id: str, *, lock: bool = False) -> BatchPredictionRow:
        query = select(BatchPredictionRow).where(BatchPredictionRow.id == batch_id)
        if lock:
            query = query.with_for_update()
        row = self._db().scalar(query)
        if row is None or not ctx.actor.can_access_project(row.project_id):
            raise BatchServiceError(404, "RESOURCE_NOT_FOUND", "批量预测不存在或当前主体不可见")
        self._ensure_type_enabled(row.chromatography_type)
        return row

    @staticmethod
    def _config(payload: BatchDraftCreate | BatchPredictionRow) -> dict[str, Any]:
        if isinstance(payload, BatchPredictionRow):
            return {"project_id": payload.project_id, "name": payload.name, "chromatography_type": payload.chromatography_type, "tool": payload.tool, "schema_version": payload.schema_version, "model_selection": payload.model_selection, "input_artifact_id": payload.input_artifact_id, "template_version": payload.template_version, "field_mapping": payload.field_mapping, "common_input": payload.common_input, "invalid_row_policy": payload.invalid_row_policy, "resource_budget": payload.resource_budget}
        return payload.model_dump(mode="json")

    @staticmethod
    def _counts(row: BatchPredictionRow) -> BatchCounts:
        defaults = {key: 0 for key in ("total", "queued", "running", "succeeded", "failed", "skipped", "canceled")}
        defaults.update(row.counts or {})
        return BatchCounts(**defaults)

    def _latest_result_rows(self, batch_id: str, validation_id: str | None = None) -> list[BatchRowResultRow]:
        """Return one current projection per logical row, newest attempt first."""
        statement = select(BatchRowResultRow).where(BatchRowResultRow.batch_id == batch_id)
        if validation_id is not None:
            statement = statement.where(BatchRowResultRow.validation_id == validation_id)
        rows = self._db().scalars(
            statement.order_by(BatchRowResultRow.source_row_number, BatchRowResultRow.attempt.desc())
        )
        latest: dict[str, BatchRowResultRow] = {}
        for item in rows:
            current = latest.get(item.row_id)
            if current is None or int(item.attempt) > int(current.attempt):
                latest[item.row_id] = item
        return sorted(latest.values(), key=lambda item: (item.source_row_number, item.row_id))

    def _audit(self, ctx: OperationContext, *, action: str, row: BatchPredictionRow, result: str = "SUCCEEDED", confirmation_id: str | None = None, job_id: str | None = None, before: dict[str, Any] | None = None, after: dict[str, Any] | None = None) -> None:
        """Persist a batch mutation audit in the caller-owned transaction."""
        event = AuditEvent(
            id=_id("audit"), project_id=row.project_id, actor_id=ctx.actor.actor_id,
            auth_method=ctx.actor.auth_method, interaction=ctx.interaction,
            request_id=ctx.request_id, action=action, resource_type="batch_prediction",
            resource_id=row.id, resource_ref={"type": "batch_prediction", "id": row.id},
            before_summary=before or {}, after_summary=after or {},
            confirmation_id=confirmation_id, config_hash=row.config_hash,
            job_id=job_id or row.job_id, agent_run_id=ctx.agent_run_id,
            result=result, timestamp=_now(),
        )
        recorder = getattr(self.audit, "record", None) if self.audit is not None else None
        if callable(recorder):
            try:
                recorder(event)
                return
            except TypeError:
                recorder(actor=ctx.actor, action=action, project_id=row.project_id, resource_id=row.id, result=result, confirmation_id=confirmation_id, before=before, after=after)
                return
        self._db().add(event)

    def _view(self, row: BatchPredictionRow) -> BatchDraft:
        return BatchDraft(
            id=row.id, project_id=row.project_id, chromatography_type=row.chromatography_type, tool="batch",
            config_hash=row.config_hash,
            draft_status=row.draft_status, execution_status=JobStatus(row.execution_status) if row.execution_status else None, job_id=row.job_id,
            attempt=row.attempt, counts=self._counts(row), progress=float(row.progress), revision=row.revision,
            etag=row.etag, name=row.name, schema_version=row.schema_version,
            model_selection=TypeAdapter(ModelSelection).validate_python(row.model_selection), input_artifact_id=row.input_artifact_id,
            template_version=row.template_version, field_mapping=row.field_mapping,
            common_input=row.common_input, invalid_row_policy=row.invalid_row_policy,
            resource_budget=row.resource_budget, validation_id=row.validation_id,
        )

    def _ensure_artifact(self, ctx: OperationContext, payload: BatchDraftCreate) -> None:
        if self.artifact is None:
            raise BatchServiceError(503, "DEPENDENCY_UNAVAILABLE", "Artifact 服务未配置", retryable=True)
        getter = getattr(self.artifact, "get_artifact", None) or getattr(self.artifact, "get", None)
        if not callable(getter):
            raise BatchServiceError(503, "DEPENDENCY_UNAVAILABLE", "Artifact 读取端口未配置", retryable=True)
        try:
            try:
                value = getter(payload.input_artifact_id, ctx.actor)
            except TypeError:
                value = getter(payload.input_artifact_id)
        except Exception as exc:
            # Keep artifact authorization and availability failures inside the
            # batch public error contract.  In particular, do not let the
            # artifact service's internal exception or storage detail escape
            # through the batch router.
            status_code = int(getattr(exc, "status_code", 503) or 503)
            if status_code == 404:
                raise BatchServiceError(404, "RESOURCE_NOT_FOUND", "批量输入制品不存在或当前主体不可见") from exc
            if status_code == 403:
                raise BatchServiceError(403, "PERMISSION_DENIED", "当前主体无权读取批量输入制品") from exc
            if status_code == 409:
                raise BatchServiceError(409, "ARTIFACT_NOT_AVAILABLE", "批量输入制品尚未通过制品校验") from exc
            raise BatchServiceError(503, "DEPENDENCY_UNAVAILABLE", "Artifact 服务暂不可用", retryable=True) from exc
        if value is None:
            raise BatchServiceError(404, "RESOURCE_NOT_FOUND", "批量输入制品不存在或当前主体不可见")
        self._validate_artifact_value(ctx, value, project_id=payload.project_id)

    @staticmethod
    def _validate_artifact_value(ctx: OperationContext, value: object, *, project_id: str | None = None) -> None:
        """Re-check a fetched artifact immediately before any content read.

        The initial draft check and a later storage read may use different
        transactions.  Rechecking here prevents a status/permission change
        between those two points from turning an archived or quarantined
        object into an executable batch input.
        """
        owner = getattr(value, "project_id", None)
        if not isinstance(owner, str) or not owner:
            raise BatchServiceError(404, "RESOURCE_NOT_FOUND", "批量输入制品不存在或当前主体不可见")
        if project_id is not None and owner != project_id:
            raise BatchServiceError(404, "RESOURCE_NOT_FOUND", "批量输入制品不存在或当前主体不可见")
        if not ctx.actor.can_access_project(owner):
            raise BatchServiceError(404, "RESOURCE_NOT_FOUND", "批量输入制品不存在或当前主体不可见")
        if getattr(value, "kind", None) != "batch_input":
            raise BatchServiceError(422, "ARTIFACT_KIND_INVALID", "批量制品必须是 batch_input 类型")
        status = getattr(value, "status", None)
        if status != "AVAILABLE":
            raise BatchServiceError(409, "ARTIFACT_NOT_AVAILABLE", "批量输入制品尚未通过制品校验")
        scan_status = getattr(value, "scan_status", None)
        if scan_status != "PASSED":
            raise BatchServiceError(409, "ARTIFACT_NOT_AVAILABLE", "批量输入制品尚未通过安全扫描")
        if getattr(value, "download_allowed", True) is False:
            raise BatchServiceError(403, "PERMISSION_DENIED", "当前主体无权读取批量输入制品")
        media_type = str(getattr(value, "media_type", "") or "").lower()
        file_name = str(getattr(value, "file_name", "") or "").lower()
        if media_type and not ("csv" in media_type or "spreadsheetml" in media_type or file_name.endswith((".csv", ".xlsx"))):
            raise BatchServiceError(422, "ARTIFACT_PARSE_FAILED", "批量输入制品必须是 CSV 或 XLSX")

    def _ensure_template(self, payload: BatchDraftCreate) -> BatchTemplate:
        if payload.chromatography_type == "tlc" and payload.template_version != "tlc-batch-2":
            raise BatchServiceError(422, "BATCH_TEMPLATE_UNSUPPORTED", "TLC v1 批量模板仅可读取，不能创建新草稿")
        try:
            template = self.template_registry.get(payload.chromatography_type, payload.tool, payload.template_version)
        except KeyError as exc:
            raise BatchServiceError(422, "BATCH_TEMPLATE_UNSUPPORTED", "批量模板版本不受支持") from exc
        expected = set(template.fields)
        identity = expected & _IDENTITY_FIELDS
        if (not set(payload.field_mapping).issubset(expected)
                or not identity.issubset(payload.field_mapping)
                or len(set(payload.field_mapping.values())) != len(payload.field_mapping)):
            raise BatchServiceError(422, "BATCH_FIELDS_INVALID", "字段映射仅可包含模板字段，结构身份列必须来自每一行")
        if (payload.resource_budget.max_runtime_seconds > template.limits.max_runtime_seconds
                or payload.resource_budget.max_evaluations > template.limits.max_evaluations):
            raise BatchServiceError(422, "BATCH_BUDGET_EXCEEDED", "批量资源预算超过模板上限")
        common = payload.common_input.model_dump(mode="json", exclude_unset=True)
        if not set(common).issubset(expected - identity):
            raise BatchServiceError(422, "BATCH_COMMON_INPUT_INVALID", "common_input 只能提供模板允许的非身份字段")
        return template

    def _ensure_batch_available(self, row: BatchPredictionRow) -> None:
        """Keep legacy resolved-input formats out of new execution paths."""
        type_id = getattr(row, "chromatography_type", None)
        supported_templates = {
            "tlc": "tlc-batch-2",
            "cc": "cc-batch-2",
            "hplc": "hplc-batch-2",
            "sfc": "sfc-batch-2",
            "gc": "gc-batch-1",
        }
        if type_id not in supported_templates:
            return
        expected_version = supported_templates[type_id]
        if row.template_version != expected_version:
            raise BatchServiceError(422, "BATCH_TEMPLATE_UNSUPPORTED", "旧批量模板不可执行，请基于当前模板重建草稿")
        try:
            template = self.template_registry.get(row.chromatography_type, row.tool, row.template_version)
        except KeyError as exc:
            raise BatchServiceError(422, "BATCH_TEMPLATE_UNSUPPORTED", "批量模板版本不受支持") from exc

    def _artifact_content(self, ctx: OperationContext, artifact_id: str, *, project_id: str | None = None) -> bytes:
        if self.artifact is None:
            raise BatchServiceError(503, "DEPENDENCY_UNAVAILABLE", "Artifact 服务未配置", retryable=True)
        getter = getattr(self.artifact, "get_artifact", None) or getattr(self.artifact, "get", None)
        if not callable(getter):
            raise BatchServiceError(503, "DEPENDENCY_UNAVAILABLE", "Artifact 元数据读取端口未配置", retryable=True)
        try:
            try:
                value = getter(artifact_id, ctx.actor)
            except TypeError:
                value = getter(artifact_id)
        except Exception as exc:
            status_code = int(getattr(exc, "status_code", 503) or 503)
            if status_code == 404:
                raise BatchServiceError(404, "RESOURCE_NOT_FOUND", "批量输入制品不存在或当前主体不可见") from exc
            if status_code == 403:
                raise BatchServiceError(403, "PERMISSION_DENIED", "当前主体无权读取批量输入制品") from exc
            if status_code == 409:
                raise BatchServiceError(409, "ARTIFACT_NOT_AVAILABLE", "批量输入制品尚未通过制品校验") from exc
            raise BatchServiceError(503, "DEPENDENCY_UNAVAILABLE", "Artifact 服务暂不可用", retryable=True) from exc
        if value is None:
            raise BatchServiceError(404, "RESOURCE_NOT_FOUND", "批量输入制品不存在或当前主体不可见")
        # Validate in this service immediately before every supported read
        # port.  A read_batch/download implementation cannot bypass a status,
        # project, scan or permission change between validation and storage IO.
        self._validate_artifact_value(ctx, value, project_id=project_id)
        reader = getattr(self.artifact, "read_batch", None)
        if callable(reader):
            try:
                try:
                    return bytes(reader(artifact_id, ctx.actor))
                except TypeError:
                    return bytes(reader(artifact_id))
            except Exception as exc:
                if int(getattr(exc, "status_code", 0) or 0) == 409:
                    raise BatchServiceError(409, "ARTIFACT_NOT_AVAILABLE", "批量输入制品尚未通过制品校验") from exc
                if int(getattr(exc, "status_code", 0) or 0) == 403:
                    raise BatchServiceError(403, "PERMISSION_DENIED", "当前主体无权读取批量输入制品") from exc
                raise BatchServiceError(422, "ARTIFACT_PARSE_FAILED", "批量制品不可读取") from exc
        downloader = getattr(self.artifact, "download", None)
        if callable(downloader):
            try:
                try:
                    content = downloader(artifact_id, ctx.actor)
                except TypeError:
                    content = downloader(artifact_id)
                if isinstance(content, tuple) and len(content) == 2:
                    return bytes(content[1])
                if isinstance(content, (bytes, bytearray)):
                    return bytes(content)
            except Exception as exc:
                if int(getattr(exc, "status_code", 0) or 0) == 409:
                    raise BatchServiceError(409, "ARTIFACT_NOT_AVAILABLE", "批量输入制品尚未通过制品校验") from exc
                if int(getattr(exc, "status_code", 0) or 0) == 403:
                    raise BatchServiceError(403, "PERMISSION_DENIED", "当前主体无权读取批量输入制品") from exc
                raise BatchServiceError(422, "ARTIFACT_PARSE_FAILED", "批量制品不可读取") from exc
        store = getattr(self.artifact, "store", None)
        if value is not None and store is not None and callable(getattr(store, "read", None)):
            try:
                return bytes(store.read(value.storage_key))
            except Exception as exc:
                raise BatchServiceError(503, "DEPENDENCY_UNAVAILABLE", "Artifact 内容读取失败", retryable=True) from exc
        content = getattr(value, "content", None)
        if isinstance(content, (bytes, bytearray)):
            self._validate_artifact_value(ctx, value, project_id=project_id)
            return bytes(content)
        raise BatchServiceError(503, "DEPENDENCY_UNAVAILABLE", "Artifact 内容读取端口未配置", retryable=True)

    def _tlc_model_constraints(self, capability_registry: object) -> TlcBatchModelConstraints:
        # Local import keeps non-TLC selected builds independent of the optional
        # TLC adapter package. Production uses the shared verified-binding helper.
        from .batch_health import build_tlc_batch_model_constraints

        templates = [
            BatchTemplateDefinition.model_validate(item.model_dump(mode="python"))
            for item in self.template_registry.list("tlc")
        ]
        provider = self._tlc_batch_constraints_provider or build_tlc_batch_model_constraints
        try:
            constraints = provider(capability_registry, templates)
        except Exception as exc:
            raise BatchServiceError(503, "MODEL_UNAVAILABLE", "TLC 批量模型绑定不可用", retryable=True) from exc
        if not isinstance(constraints, TlcBatchModelConstraints):
            raise BatchServiceError(503, "MODEL_UNAVAILABLE", "TLC 批量模型绑定不可用", retryable=True)
        return constraints

    def _non_tlc_model_constraints(self, type_id: str) -> object:
        from .batch_health import build_non_tlc_batch_model_constraints

        templates = [
            BatchTemplateDefinition.model_validate(item.model_dump(mode="python"))
            for item in self.template_registry.list(type_id)
        ]
        try:
            return build_non_tlc_batch_model_constraints(type_id, self.capabilities, templates)
        except Exception as exc:
            raise BatchServiceError(503, "MODEL_UNAVAILABLE", "批量模型精确绑定不可用", retryable=True) from exc

    def _resolve_batch_model(self, ctx: OperationContext, payload: BatchDraftCreate) -> ModelSnapshot:
        capabilities = self.capabilities
        if capabilities is None:
            raise BatchServiceError(503, "MODEL_UNAVAILABLE", "批量预测模型未配置", retryable=True)
        capability = getattr(capabilities, "capability", lambda _type: None)(payload.chromatography_type)
        if capability is None or getattr(capability.health, "status", None) != "READY":
            raise BatchServiceError(503, "MODEL_UNAVAILABLE", "批量预测模型不可用", retryable=True)
        selection = payload.model_selection
        selection_profile_id = getattr(selection, "preprocessing_profile_id", None)
        selection_profile_sha256 = getattr(selection, "preprocessing_profile_sha256", None)
        if payload.chromatography_type == "tlc":
            if selection.mode != "exact":
                raise BatchServiceError(422, "PREDICTION_INPUT_INVALID", "TLC 批量预测必须使用精确模型版本")
            constraints = self._tlc_model_constraints(capabilities)
            compatible = [
                item for item in constraints.compatible_models
                if item.model_id == selection.model_id
                and item.model_version == selection.model_version
                and item.input_schema_version == payload.schema_version
                and payload.template_version in item.template_versions
                and item.health.status == "READY"
            ]
            if payload.template_version not in constraints.template_versions or len(compatible) != 1:
                raise BatchServiceError(503, "MODEL_UNAVAILABLE", "TLC 模型未通过该模板的精确批量兼容验证", retryable=True)
        if payload.chromatography_type in {"cc", "hplc", "gc", "sfc"} and selection.mode != "exact":
            raise BatchServiceError(422, "PREDICTION_INPUT_INVALID", "该批量预测必须使用精确模型版本")
        if payload.chromatography_type in {"cc", "hplc", "gc"} and (
            selection.mode != "exact"
            or selection_profile_id is None
            or selection_profile_sha256 is None
        ):
            raise BatchServiceError(422, "PREDICTION_INPUT_INVALID", "该批量预测必须使用能力中声明的精确预处理版本")
        if payload.chromatography_type not in {"cc", "hplc", "gc"} and (
            selection_profile_id is not None
            or selection_profile_sha256 is not None
        ):
            raise BatchServiceError(422, "PREDICTION_INPUT_INVALID", "所选模型不支持该预处理版本")
        candidates = [
            model for model in capability.models
            if (
                model.scope == selection.scope_id
                if selection.mode == "champion"
                else model.id == selection.model_id and model.version == selection.model_version
            )
        ]
        if selection.mode == "exact" and candidates and not any(
            model.preprocessing_profile_id == selection_profile_id
            and model.preprocessing_profile_sha256 == selection_profile_sha256
            for model in candidates
        ):
            raise BatchServiceError(422, "PREDICTION_INPUT_INVALID", "模型或预处理版本已变化，请按当前能力重新选择")
        candidates = [
            model for model in candidates
            if model.preprocessing_profile_id == selection_profile_id
            and model.preprocessing_profile_sha256 == selection_profile_sha256
        ]
        candidates = [m for m in candidates if getattr(m.health, "status", None) == "READY" and m.artifact_sha256]
        if len(candidates) != 1:
            raise BatchServiceError(503, "MODEL_UNAVAILABLE", "批量预测模型选择不存在或不唯一", retryable=True)
        model = candidates[0]
        if model.input_schema_version != payload.schema_version:
            raise BatchServiceError(422, "MODEL_SCHEMA_MISMATCH", "批量 schema_version 与所选模型输入 schema 不匹配")
        if payload.chromatography_type in {"cc", "hplc", "sfc", "gc"}:
            from .adapters import CapabilityRegistry

            # Production uses the registry-backed manifest/handle/schema gate.
            # Lightweight port fakes retain the exact model/profile checks above.
            if isinstance(self.capabilities, CapabilityRegistry):
                constraints = self._non_tlc_model_constraints(payload.chromatography_type)
                compatible = [
                    item for item in constraints.compatible_models
                    if item.model_id == model.id
                    and item.model_version == model.version
                    and item.input_schema_version == model.input_schema_version
                    and item.preprocessing_profile_id == model.preprocessing_profile_id
                    and item.preprocessing_profile_sha256 == model.preprocessing_profile_sha256
                    and payload.template_version in item.template_versions
                    and item.health.status == "READY"
                ]
                if payload.template_version not in constraints.template_versions or len(compatible) != 1:
                    raise BatchServiceError(503, "MODEL_UNAVAILABLE", "模型与批量模板的精确能力绑定不可用", retryable=True)
        return ModelSnapshot(
            id=model.id, version=model.version, business_scope_id=model.scope,
            artifact_sha256=model.artifact_sha256, input_schema_version=model.input_schema_version,
            output_schema_version=model.output_schema_version,
            preprocessing_profile_id=model.preprocessing_profile_id,
            preprocessing_profile_sha256=model.preprocessing_profile_sha256,
            score_semantics=model.score_semantics, threshold=model.threshold,
        )

    def create(self, ctx: OperationContext, payload: BatchDraftCreate) -> BatchDraft:
        self._authorize(ctx, payload.project_id, "batch:create")
        self._ensure_type_enabled(payload.chromatography_type)
        self._ensure_template(payload)
        self._ensure_artifact(ctx, payload)
        row_id = _id("batch")
        config = payload.model_dump(mode="json")
        config["common_input"] = payload.common_input.model_dump(mode="json", exclude_unset=True)
        etag = _batch_config_etag(resource_id=row_id, revision=1, config=config)
        row = BatchPredictionRow(id=row_id, project_id=payload.project_id, actor_id=ctx.actor.actor_id,
            chromatography_type=payload.chromatography_type, tool="batch", name=payload.name,
            schema_version=payload.schema_version, model_selection=_as_json(payload.model_selection),
            input_artifact_id=payload.input_artifact_id, template_version=payload.template_version,
            field_mapping=dict(payload.field_mapping), common_input=config["common_input"],
            invalid_row_policy=payload.invalid_row_policy, resource_budget=_as_json(payload.resource_budget),
            revision=1, config_hash=_batch_config_hash(config), etag=etag, draft_status="DRAFT", counts={})
        self._db().add(row)
        self._db().flush()
        self._audit(ctx, action="batch.create", row=row, after={"status": row.draft_status, "revision": row.revision})
        return self._view(row)

    def get(self, ctx: OperationContext, batch_id: str) -> BatchDraft:
        return self._view(self._get(ctx, batch_id))

    def patch(self, ctx: OperationContext, batch_id: str, payload: BatchDraftPatch, *, if_match: str) -> BatchDraft:
        row = self._get(ctx, batch_id, lock=True)
        self._authorize(ctx, row.project_id, "batch:write")
        if row.chromatography_type == "tlc" and row.template_version == "tlc-batch-1":
            raise BatchServiceError(409, "STATE_CONFLICT", "TLC v1 历史批量草稿只读，请新建 v2 草稿")
        if row.etag != if_match:
            raise BatchServiceError(412, "ETAG_MISMATCH", "资源版本已变化")
        if row.submitted_at is not None or row.draft_status not in {"DRAFT", "INVALID", "READY"}:
            raise BatchServiceError(409, "STATE_CONFLICT", "仅未提交草稿可修改")
        before = {"revision": row.revision, "config_hash": row.config_hash, "input_artifact_id": row.input_artifact_id}
        for key, value in payload.model_dump(mode="json", exclude_none=True).items():
            setattr(row, key, value)
        try:
            template = self.template_registry.get(row.chromatography_type, row.tool, row.template_version)
        except KeyError as exc:
            raise BatchServiceError(422, "BATCH_TEMPLATE_UNSUPPORTED", "批量模板版本不受支持") from exc
        try:
            candidate = self._payload_from_row(row)
            self._ensure_template(candidate)
        except Exception as exc:
            if isinstance(exc, BatchServiceError):
                raise
            raise BatchServiceError(422, "VALIDATION_FAILED", "批量草稿字段不符合对应类型 schema") from exc
        self._ensure_artifact(ctx, candidate)
        row.revision += 1
        row.draft_status, row.validation_id, row.model_snapshot = "DRAFT", None, None
        row.job_id, row.attempt, row.execution_status = None, None, None
        config = self._config(row)
        row.config_hash = _batch_config_hash(config)
        row.etag = _batch_config_etag(resource_id=row.id, revision=row.revision, config=config)
        row.updated_at = _now()
        self._db().flush()
        self._audit(ctx, action="batch.patch", row=row, before=before, after={"revision": row.revision, "config_hash": row.config_hash, "input_artifact_id": row.input_artifact_id})
        return self._view(row)

    def validate(self, ctx: OperationContext, batch_id: str, *, if_match: str) -> BatchValidationAccepted:
        row = self._get(ctx, batch_id, lock=True)
        self._authorize(ctx, row.project_id, "batch:validate")
        if row.chromatography_type == "tlc" and row.template_version == "tlc-batch-2":
            self._authorize(ctx, row.project_id, "prediction:create")
        if row.etag != if_match:
            raise BatchServiceError(412, "ETAG_MISMATCH", "资源版本已变化")
        if row.submitted_at is not None or row.draft_status not in {"DRAFT", "INVALID", "READY"}:
            raise BatchServiceError(409, "STATE_CONFLICT", "当前草稿不可校验")
        self._ensure_batch_available(row)
        payload = self._payload_from_row(row)
        model = self._resolve_batch_model(ctx, payload)
        validation_id, job_id = _id("batch_validation"), _id("job")
        task_repo = SQLAlchemyTaskRepository(lambda: self._db())
        job = add_job_rows(task_repo, self._db(), project_id=row.project_id, owner_id=ctx.actor.actor_id,
            kind="batch_prediction", resource_ref=ResourceRef(type="batch_validation", id=validation_id),
            cancelable=True, retryable=True, resume_mode="restart", deadline_at=None, job_id=job_id)
        validation = BatchValidationRow(id=validation_id, project_id=row.project_id, batch_id=row.id,
            draft_revision=row.revision, config_hash=row.config_hash, status="QUEUED",
            template_version=row.template_version, model_snapshot=model.model_dump(mode="json"), resource_estimate=row.resource_budget)
        self._db().add(validation)
        row.draft_status, row.validation_id, row.job_id, row.attempt, row.execution_status = "VALIDATING", validation_id, job.id, job.attempt, job.status
        row.etag = _etag({"id": row.id, "revision": row.revision, "config_hash": row.config_hash, "status": row.draft_status})
        self._db().flush()
        self._audit(ctx, action="batch.validate", row=row, job_id=job.id, after={"validation_id": validation.id, "status": validation.status, "revision": row.revision})
        return BatchValidationAccepted(id=validation_id, status="QUEUED", job_id=job.id, resource_ref={"type": "batch_validation", "id": validation_id})

    def _payload_from_row(self, row: BatchPredictionRow) -> BatchDraftCreate:
        return TypeAdapter(BatchDraftCreate).validate_python(self._config(row))

    def _tlc_allowed_systems(self, model_snapshot: dict[str, Any]) -> set[frozenset[str]]:
        """Read the selected READY TLC tool's declared solvent domain."""
        if self.capabilities is None:
            raise BatchServiceError(503, "MODEL_UNAVAILABLE", "TLC 能力未配置", retryable=True)
        try:
            capability = self.capabilities.capability("tlc")
        except Exception as exc:
            raise BatchServiceError(503, "MODEL_UNAVAILABLE", "TLC 能力不可用", retryable=True) from exc
        if getattr(getattr(capability, "health", None), "status", None) != "READY":
            raise BatchServiceError(503, "MODEL_UNAVAILABLE", "TLC 能力不可用", retryable=True)
        matches = [
            model for model in getattr(capability, "models", [])
            if model.id == model_snapshot.get("id")
            and model.version == model_snapshot.get("version")
            and model.artifact_sha256 == model_snapshot.get("artifact_sha256")
            and getattr(getattr(model, "health", None), "status", None) == "READY"
        ]
        if len(matches) != 1:
            raise BatchServiceError(503, "MODEL_UNAVAILABLE", "TLC 冻结模型能力已变化", retryable=True)
        tools = [tool for tool in getattr(capability, "tools", []) if tool.id == "single"]
        if len(tools) != 1 or getattr(getattr(tools[0], "health", None), "status", None) != "READY":
            raise BatchServiceError(503, "MODEL_UNAVAILABLE", "TLC 单次工具能力不可用", retryable=True)
        raw_systems = tools[0].model_constraints.get("allowed_systems")
        if not isinstance(raw_systems, list) or not raw_systems:
            raise BatchServiceError(503, "MODEL_UNAVAILABLE", "TLC 溶剂条件目录未配置", retryable=True)
        systems: set[frozenset[str]] = set()
        for item in raw_systems:
            if (not isinstance(item, (list, tuple)) or len(item) != 2
                    or any(not isinstance(name, str) or not name for name in item)
                    or item[0] == item[1]):
                raise BatchServiceError(503, "MODEL_UNAVAILABLE", "TLC 溶剂条件目录无效", retryable=True)
            systems.add(frozenset(item))
        return systems

    def _resolve_tlc_v2_row(
        self, ctx: OperationContext, batch: BatchPredictionRow,
        validation: BatchValidationRow, value: dict[str, Any],
    ) -> dict[str, Any]:
        self._authorize(ctx, batch.project_id, "batch:validate")
        allowed_systems = self._tlc_allowed_systems(validation.model_snapshot or {})
        pair = frozenset((value["solvent_1"], value["solvent_2"]))
        if pair not in allowed_systems:
            raise ValueError("TLC solvent pair is not declared by the selected model")
        conditions = TlcInput.model_validate({
            "solvent_components": [
                {"solvent_id": value["solvent_1"], "fraction": value["fraction_1"]},
                {"solvent_id": value["solvent_2"], "fraction": value["fraction_2"]},
            ],
        })
        # Keep public row checks aligned with the single-prediction adapter.
        # This runs before StructureService persists or deduplicates anything.
        from chrom_platform.adapters.tlc.runtime import validate_mobile_phase

        try:
            validate_mobile_phase({item.solvent_id: item.fraction for item in conditions.solvent_components})
        except ValueError as exc:
            raise ValueError("TLC mobile phase is outside the selected model domain") from exc
        try:
            structure = StructureService(None).normalize(
                NormalizeStructureRequest(
                    project_id=batch.project_id, input_type="smiles", value=value["smiles"],
                    preserve_stereochemistry=True,
                ),
                structure_id=_id("structure"),
                repository=SQLAlchemyStructureRepository(self._db()),
            )
        except InvalidStructure as exc:
            raise ValueError("TLC SMILES is invalid") from exc
        return TlcBatchResolvedInputV2(
            template_version="tlc-batch-2",
            project_id=batch.project_id,
            draft_revision=validation.draft_revision,
            validation_id=validation.id,
            structure_ref=structure.id,
            normalized_smiles=structure.canonical_smiles,
            solvent_conditions=conditions,
        ).model_dump(mode="json")

    def _sfc_row_evaluations(
        self, *, project_id: str, model_snapshot: Mapping[str, Any], row: Mapping[str, Any]
    ) -> tuple[int, SfcCandidateScopeProvenance | None]:
        """Freeze the exact tool-scoped candidate count before top-k return."""
        try:
            adapter = self.capabilities.adapter("sfc")
            binding = adapter.binding_for(str(model_snapshot["id"]), str(model_snapshot["version"]))
            catalog = getattr(binding, "candidate_catalog", None)
            entry = getattr(binding, "registry_entry", None)
            allowed_tool = getattr(binding, "allowed_tool", None)
            if not getattr(self.capabilities, "metadata_only", False):
                runtime = getattr(binding, "runtime", None)
                if runtime is not None:
                    allowed_tool = runtime.allowed_tool
                    bound_entry = runtime.registry.entry(str(model_snapshot["id"]), version=str(model_snapshot["version"]))
                    entry = {name: getattr(bound_entry, name) for name in ("allowed_columns", "stationary_phase", "mode")}
            if binding is None or catalog is None or not isinstance(entry, Mapping) or allowed_tool is None:
                raise ValueError("SFC exact candidate binding is unavailable")
            scope_id = row.get("candidate_scope_id")
            if not isinstance(scope_id, str) or not scope_id:
                raise ValueError("SFC candidate scope is missing")
            resolved_scope = catalog.resolve_with_policy(
                scope_id,
                project_id=project_id,
                model_id=str(model_snapshot["id"]),
                model_version=str(model_snapshot["version"]),
            )
            candidates = tuple(resolved_scope["candidates"])
            if allowed_tool == "single_column":
                column = row.get("column_id")
                allowed_columns = entry.get("allowed_columns") or ()
                if not isinstance(column, str) or column not in allowed_columns:
                    raise ValueError("SFC batch column is outside the bound domain")
                candidates = tuple(
                    item for item in candidates
                    if item.column == column
                    and item.stationary_phase == entry.get("stationary_phase")
                    and item.mode == entry.get("mode")
                )
            if not candidates:
                raise ValueError("SFC candidate scope is empty for the batch row")
            policy = resolved_scope.get("policy")
            provenance = None
            if isinstance(policy, Mapping):
                provenance = SfcCandidateScopeProvenance(
                    scope_id=scope_id,
                    policy=policy,
                    candidate_count=int(resolved_scope["candidate_count"]),
                    observed_count=int(resolved_scope["observed_count"]),
                    unobserved_count=int(resolved_scope["unobserved_count"]),
                )
            return len(candidates), provenance
        except Exception as exc:
            raise BatchServiceError(
                422, "BATCH_ROW_INVALID", "SFC 候选范围不可用或与批量行不匹配"
            ) from exc

    def _prediction_request_for_row(
        self, ctx: OperationContext, batch: BatchPredictionRow,
        payload: BatchDraftCreate, row: Mapping[str, Any],
    ) -> CcPredictionCreate | HplcPredictionCreate | SfcSingleColumnPredictionCreate | SfcFullColumnPredictionCreate | GcPredictionCreate:
        common = {
            "project_id": batch.project_id,
            "schema_version": payload.schema_version,
            "model_selection": payload.model_selection.model_dump(mode="json"),
        }
        if batch.chromatography_type == "cc":
            return CcPredictionCreate.model_validate({
                **common,
                "chromatography_type": "cc",
                "tool": "single",
                "structure_refs": [row["structure_ref_a"], row["structure_ref_b"]],
                "input": {key: row[key] for key in (
                    "eluent_system_id", "loading_solvent_id", "sample_mass_mg",
                    "loading_solvent_volume_ul", "flow_rate_ml_min", "column_spec_id",
                )},
            })
        if batch.chromatography_type == "hplc":
            # HPLC's public template intentionally accepts the original raw
            # pair. Persist each pair member through the same structure core
            # used by TLC v2, then let shared prepare bind these ordered refs
            # back to the unchanged raw input and validate their relationship.
            self._authorize(ctx, batch.project_id, "prediction:create")
            structure_service = StructureService(None)
            structure_repository = SQLAlchemyStructureRepository(self._db())
            normalized = []
            for raw_smiles in (row["smiles_a"], row["smiles_b"]):
                try:
                    normalized.append(structure_service.normalize(
                        NormalizeStructureRequest(
                            project_id=batch.project_id,
                            input_type="smiles",
                            value=raw_smiles,
                            preserve_stereochemistry=True,
                        ),
                        structure_id=_id("structure"),
                        repository=structure_repository,
                    ))
                except InvalidStructure as exc:
                    raise ValueError("HPLC SMILES is invalid") from exc
            return HplcPredictionCreate.model_validate({
                **common,
                "chromatography_type": "hplc",
                "tool": "single",
                "schema_version": "1.1",
                "structure_refs": [item.id for item in normalized],
                "input": {
                    "model_input_smiles": [row["smiles_a"], row["smiles_b"]],
                    **{key: row[key] for key in (
                        "isopropanol_fraction", "flow_rate_ml_min", "column_ids",
                    )},
                },
            })
        if batch.chromatography_type == "gc":
            return GcPredictionCreate.model_validate({
                **common,
                "chromatography_type": "gc",
                "tool": "single",
                "schema_version": "1.1",
                "structure_refs": [row["structure_ref"]],
                "input": {"temperature_program": row["temperature_program"]},
            })
        if batch.chromatography_type == "sfc":
            request_type = (
                SfcSingleColumnPredictionCreate
                if row["tool"] == "single_column"
                else SfcFullColumnPredictionCreate
            )
            input_value = {
                "modifier_total_fraction": row["modifier_total_fraction"],
                "modifier_components": row["modifier_components"],
                "candidate_scope_id": row["candidate_scope_id"],
                "top_k": row["top_k"],
            }
            if row["tool"] == "single_column":
                input_value["column_id"] = row["column_id"]
            return request_type.model_validate({
                **common,
                "chromatography_type": "sfc",
                "tool": row["tool"],
                "schema_version": "1.1",
                "structure_refs": [row["structure_ref_a"], row["structure_ref_b"]],
                "input": input_value,
            })
        raise BatchServiceError(422, "BATCH_TEMPLATE_UNSUPPORTED", "该批量类型没有受支持的单次请求契约")

    def _resolve_non_tlc_row(
        self,
        ctx: OperationContext,
        batch: BatchPredictionRow,
        validation: BatchValidationRow,
        payload: BatchDraftCreate,
        row: Mapping[str, Any],
    ) -> tuple[dict[str, Any], int]:
        if self.prediction_create_service is None:
            raise BatchServiceError(503, "DEPENDENCY_UNAVAILABLE", "批量预测共享准备服务未配置", retryable=True)
        request = self._prediction_request_for_row(ctx, batch, payload, row)
        try:
            prepared = self.prediction_create_service.prepare(ctx, request)
        except APIError:
            raise
        except Exception as exc:
            raise BatchServiceError(422, "PREDICTION_INPUT_INVALID", "预测行未通过共享输入校验") from exc
        if prepared.model_snapshot.model_dump(mode="json") != ModelSnapshot.model_validate(
            validation.model_snapshot
        ).model_dump(mode="json"):
            raise BatchServiceError(412, "BATCH_MODEL_BINDING_STALE", "行级共享准备解析出的模型与校验模型不一致")
        evaluations = 1
        resolved_fields: dict[str, Any] = {
            "project_id": batch.project_id,
            "draft_revision": validation.draft_revision,
            "validation_id": validation.id,
            "request": request.model_dump(mode="json"),
            "normalized_structures": prepared.normalized_structures,
        }
        if batch.chromatography_type == "cc":
            resolved = CcBatchResolvedInputV2(
                template_version="cc-batch-2", evaluation_count=2, **resolved_fields,
            )
            evaluations = 2
        elif batch.chromatography_type == "hplc":
            evaluations = len(request.input.column_ids)
            resolved = HplcBatchResolvedInputV2(
                template_version="hplc-batch-2", evaluation_count=evaluations, **resolved_fields,
            )
        elif batch.chromatography_type == "gc":
            resolved = GcBatchResolvedInputV1(
                template_version="gc-batch-1", evaluation_count=1, **resolved_fields,
            )
        else:
            evaluations, provenance = self._sfc_row_evaluations(
                project_id=batch.project_id,
                model_snapshot=validation.model_snapshot or {},
                row=row,
            )
            resolved = SfcBatchResolvedInputV2(
                template_version="sfc-batch-2",
                normalized_modifier_structures=prepared.normalized_modifier_structures,
                scope_provenance=provenance,
                evaluation_count=evaluations,
                **resolved_fields,
            )
        return resolved.model_dump(mode="json"), evaluations

    @staticmethod
    def _row_preview(
        batch: BatchPredictionRow, validation: BatchValidationRow,
        item: BatchValidationItemRow,
    ) -> BatchResolvedInput | None:
        if (item.status != "VALID" or validation.status in {"STALE", "INVALID"}
                or batch.revision != validation.draft_revision
                or batch.config_hash != validation.config_hash
                or batch.validation_id != validation.id
                or not isinstance(item.resolved_input_snapshot, dict)):
            return None
        snapshot = item.resolved_input_snapshot
        try:
            if batch.chromatography_type != "tlc":
                return TypeAdapter(BatchResolvedInput).validate_python(snapshot)
            if validation.template_version == "tlc-batch-2":
                return TlcBatchResolvedInputV2.model_validate(snapshot)
            if validation.template_version == "tlc-batch-1":
                return TlcBatchResolvedInputV1(
                    project_id=batch.project_id,
                    draft_revision=validation.draft_revision,
                    validation_id=validation.id,
                    structure_ref=snapshot["structure_ref"],
                    solvent_conditions=TlcInput.model_validate({"solvent_components": snapshot["solvent_components"]}),
                    template_version="tlc-batch-1",
                )
        except (KeyError, TypeError, ValueError):
            return None
        return None

    def run_validation(self, ctx: OperationContext, validation_id: str) -> BatchValidation:
        """Parse an immutable artifact and materialize durable row states."""
        validation = self._db().scalar(select(BatchValidationRow).where(BatchValidationRow.id == validation_id).with_for_update())
        if validation is None:
            raise BatchServiceError(404, "RESOURCE_NOT_FOUND", "批量校验不存在")
        batch = self._get(ctx, validation.batch_id, lock=True)
        if batch.chromatography_type == "tlc" and batch.template_version == "tlc-batch-2":
            self._authorize(ctx, batch.project_id, "batch:validate")
            self._authorize(ctx, batch.project_id, "prediction:create")
        self._ensure_batch_available(batch)
        if batch.chromatography_type != "tlc":
            self._authorize(ctx, batch.project_id, "prediction:create")
            if self.prediction_create_service is None:
                raise BatchServiceError(503, "DEPENDENCY_UNAVAILABLE", "批量预测共享准备服务未配置", retryable=True)
        if batch.revision != validation.draft_revision or batch.config_hash != validation.config_hash:
            validation.status = "STALE"
            self._db().flush()
            return self._validation_view(validation)
        if self._db().scalar(select(BatchValidationItemRow.id).where(BatchValidationItemRow.validation_id == validation.id)) is not None:
            raise BatchServiceError(409, "STATE_CONFLICT", "该批量校验已经物化")
        payload = self._payload_from_row(batch)
        template = self._ensure_template(payload)
        try:
            content = self._artifact_content(ctx, payload.input_artifact_id, project_id=batch.project_id)
            row_model = self.template_registry.row_model(payload.chromatography_type, payload.template_version)
            parser = _parse_xlsx if zipfile.is_zipfile(io.BytesIO(content)) else _parse_csv
            common_input = payload.common_input.model_dump(mode="json", exclude_unset=True)
            rows = parser(content, field_mapping=payload.field_mapping, row_model=row_model, expected_fields=list(template.fields), common_input=common_input)
        except BatchServiceError:
            validation.status = "INVALID"
            batch.draft_status = "INVALID"
            self._db().flush()
            raise
        except Exception as exc:
            validation.status = "INVALID"
            batch.draft_status = "INVALID"
            self._db().flush()
            raise BatchServiceError(422, "ARTIFACT_PARSE_FAILED", "批量制品解析失败") from exc
        valid_rows = 0
        invalid_rows = 0
        excluded_rows = 0
        aggregate: list[dict[str, Any]] = []
        estimated_evaluations = 0
        for number, snapshot, error in rows:
            if error is None and payload.chromatography_type == "tlc" and payload.template_version == "tlc-batch-2":
                try:
                    snapshot = self._resolve_tlc_v2_row(ctx, batch, validation, snapshot or {})
                except ValueError as exc:
                    error = {"code": "BATCH_ROW_INVALID", "stage": "VALIDATION", "field": "smiles" if "SMILES" in str(exc) else "solvent_1", "message": "TLC 行未通过结构或溶剂条件校验"}
            row_evaluations = 1
            if error is None and payload.chromatography_type != "tlc":
                try:
                    snapshot, row_evaluations = self._resolve_non_tlc_row(
                        ctx, batch, validation, payload, snapshot or {},
                    )
                except Exception as exc:
                    payload_error = getattr(exc, "payload", None)
                    code = getattr(payload_error, "code", None) or getattr(exc, "code", None)
                    message = getattr(payload_error, "message", None) or getattr(exc, "message", None)
                    error = {
                        "code": code if isinstance(code, str) else "PREDICTION_INPUT_INVALID",
                        "stage": "VALIDATION",
                        "field": (
                            "smiles_a" if payload.chromatography_type == "hplc"
                            else "structure_ref_a" if payload.chromatography_type in {"cc", "sfc"}
                            else "structure_ref"
                        ),
                        "message": message if isinstance(message, str) else "预测行未通过共享输入校验",
                    }
            row_id = _id("brow")
            if error is not None:
                invalid_rows += 1
                error.setdefault("stage", "VALIDATION")
                self._db().add(BatchValidationItemRow(id=_id("bval"), project_id=batch.project_id, validation_id=validation.id, row_id=row_id, source_row_number=number, status="INVALID", normalized_input_hash=_hash(error), field_errors=[error], warnings=[]))
                self._db().add(BatchRowResultRow(id=_id("bresult"), project_id=batch.project_id, validation_id=validation.id, batch_id=batch.id, row_id=row_id, source_row_number=number, input_snapshot={}, result=None, error=error, warnings=[], attempt=1, status="SKIPPED"))
                excluded_rows += payload.invalid_row_policy == "exclude_invalid"
                continue
            valid_rows += 1
            resolved = snapshot or {}
            resolved_hash = _hash(resolved)
            estimated_evaluations += row_evaluations
            aggregate.append({"row_id": row_id, "input_hash": resolved_hash})
            self._db().add(BatchValidationItemRow(id=_id("bval"), project_id=batch.project_id, validation_id=validation.id, row_id=row_id, source_row_number=number, status="VALID", normalized_input_hash=resolved_hash, resolved_input_snapshot=resolved, resolved_input_hash=resolved_hash, field_errors=[], warnings=[]))
            self._db().add(BatchRowResultRow(id=_id("bresult"), project_id=batch.project_id, validation_id=validation.id, batch_id=batch.id, row_id=row_id, source_row_number=number, input_snapshot=resolved, result=None, error=None, warnings=[], attempt=1, status="QUEUED"))
        validation.total_rows = len(rows)
        validation.valid_rows = valid_rows
        validation.invalid_rows = invalid_rows
        validation.excluded_rows = excluded_rows
        validation.total_evaluations = estimated_evaluations
        resolved_hash = _hash(aggregate)
        validation.warnings = []
        if estimated_evaluations > payload.resource_budget.max_evaluations:
            validation.warnings.append({"code": "BATCH_BUDGET_EXCEEDED", "message": "预估 evaluations 超过批量预算"})
        if valid_rows == 0:
            validation.warnings.append({"code": "BATCH_NO_VALID_ROWS", "message": "批量校验没有可提交的有效行"})
        ready = (
            valid_rows > 0
            and not (invalid_rows and payload.invalid_row_policy == "block")
            and estimated_evaluations <= payload.resource_budget.max_evaluations
        )
        validation.status = "READY" if ready else "INVALID"
        batch.draft_status = "READY" if ready else "INVALID"
        batch.model_snapshot = validation.model_snapshot
        if not ready:
            self._db().query(BatchRowResultRow).filter(
                BatchRowResultRow.validation_id == validation.id,
                BatchRowResultRow.status == "QUEUED",
            ).update({BatchRowResultRow.status: "SKIPPED"}, synchronize_session=False)
        skipped_rows = len(rows) if not ready else excluded_rows
        batch.counts = {"total": len(rows), "queued": valid_rows if ready else 0, "running": 0, "succeeded": 0, "failed": 0, "skipped": skipped_rows, "canceled": 0}
        batch.etag = _etag({"id": batch.id, "revision": batch.revision, "config_hash": batch.config_hash,
                            "validation_id": validation.id, "model_snapshot_hash": _hash(validation.model_snapshot),
                            "resolved_input_hash": resolved_hash, "resource_budget": batch.resource_budget})
        validation.resolved_input_hash = resolved_hash
        self._db().flush()
        return self.validation(ctx, batch.id)

    def validation(self, ctx: OperationContext, batch_id: str) -> BatchValidation:
        row = self._get(ctx, batch_id)
        validation = self._db().scalar(select(BatchValidationRow).where(BatchValidationRow.id == row.validation_id)) if row.validation_id else None
        if validation is None:
            raise BatchServiceError(404, "RESOURCE_NOT_FOUND", "当前草稿没有校验结果")
        return self._validation_view(validation)

    @staticmethod
    def _validation_view(validation: BatchValidationRow) -> BatchValidation:
        status = validation.status
        warnings = list(validation.warnings or [])
        if status == "CANCELED":
            status = "FAILED"
            if not any(
                isinstance(warning, Mapping)
                and warning.get("code") == "BATCH_VALIDATION_CANCELED"
                for warning in warnings
            ):
                warnings.append({"code": "BATCH_VALIDATION_CANCELED", "message": "校验任务已取消，可重新校验草稿"})
        return BatchValidation(id=validation.id, draft_revision=validation.draft_revision, config_hash=validation.config_hash, status=status,
            template_version=validation.template_version, model_snapshot=validation.model_snapshot,
            total_rows=validation.total_rows, valid_rows=validation.valid_rows, invalid_rows=validation.invalid_rows,
            excluded_rows=validation.excluded_rows, total_evaluations=validation.total_evaluations,
            resource_estimate=validation.resource_estimate, resolved_input_hash=validation.resolved_input_hash,
            errors_report_artifact_id=validation.errors_report_artifact_id,
            warnings=warnings)

    def validation_rows(self, ctx: OperationContext, batch_id: str, *, status_filter: str | None, cursor: str | None, limit: int, cursor_codec: SignedCursorCodec | None = None) -> BatchValidationRowsPage:
        row = self._get(ctx, batch_id)
        if not row.validation_id:
            raise BatchServiceError(404, "RESOURCE_NOT_FOUND", "当前草稿没有校验结果")
        validation = self._db().scalar(select(BatchValidationRow).where(BatchValidationRow.id == row.validation_id))
        if validation is None or validation.batch_id != row.id or validation.project_id != row.project_id:
            raise BatchServiceError(404, "RESOURCE_NOT_FOUND", "当前草稿没有校验结果")
        codec = _require_batch_cursor_codec(cursor_codec)
        scope = _batch_cursor_scope(ctx, row, validation, status_filter=status_filter, endpoint="batch.validation_rows.v1")
        query = select(BatchValidationItemRow).where(BatchValidationItemRow.project_id == row.project_id, BatchValidationItemRow.validation_id == row.validation_id).order_by(BatchValidationItemRow.source_row_number, BatchValidationItemRow.row_id)
        if status_filter:
            query = query.where(BatchValidationItemRow.status == status_filter)
        items = list(self._db().scalars(query))
        items = _after_row_cursor(items, cursor, codec=codec, scope=scope)
        window, more = items[:limit], len(items) > limit
        return BatchValidationRowsPage(rows=[RowValidation(row_id=item.row_id, source_row_number=item.source_row_number,
            status=item.status, normalized_input_hash=item.normalized_input_hash,
            resolved_input_hash=item.resolved_input_hash,
            resolved_input_snapshot=self._row_preview(row, validation, item),
            field_errors=item.field_errors, warnings=item.warnings) for item in window],
            next_cursor=codec.encode(after=(int(window[-1].source_row_number), window[-1].row_id), scope=scope) if more and window else None, has_more=more)

    def _resolved_input_aggregate(self, validation_id: str, row_ids: list[str] | None = None) -> tuple[str, list[str]]:
        statement = select(BatchValidationItemRow).where(
            BatchValidationItemRow.validation_id == validation_id,
            BatchValidationItemRow.status == "VALID",
        ).order_by(BatchValidationItemRow.source_row_number, BatchValidationItemRow.row_id)
        items = list(self._db().scalars(statement))
        if row_ids is not None:
            selected = set(row_ids)
            items = [item for item in items if item.row_id in selected]
            if len(items) != len(selected):
                raise BatchServiceError(412, "CONFIRMATION_STALE", "校验行快照已变化")
        hashes: list[dict[str, str]] = []
        for item in items:
            snapshot = item.resolved_input_snapshot
            digest = item.resolved_input_hash
            if not isinstance(snapshot, dict) or not digest or _hash(snapshot) != digest:
                raise BatchServiceError(412, "CONFIRMATION_STALE", "解析输入快照缺失或哈希不匹配")
            hashes.append({"row_id": item.row_id, "input_hash": digest})
        return _hash(hashes), [item.row_id for item in items]

    def _resolved_evaluation_count(self, validation_id: str, row_ids: Sequence[str]) -> int:
        selected = set(row_ids)
        items = list(self._db().scalars(select(BatchValidationItemRow).where(
            BatchValidationItemRow.validation_id == validation_id,
            BatchValidationItemRow.status == "VALID",
        )))
        items = [item for item in items if item.row_id in selected]
        if len(items) != len(selected):
            raise BatchServiceError(412, "CONFIRMATION_STALE", "校验行快照已变化")
        total = 0
        for item in items:
            snapshot = item.resolved_input_snapshot
            if not isinstance(snapshot, dict):
                raise BatchServiceError(412, "CONFIRMATION_STALE", "解析输入快照缺失")
            count = snapshot.get("evaluation_count", 1)
            if isinstance(count, bool) or not isinstance(count, int) or count < 1:
                raise BatchServiceError(412, "CONFIRMATION_STALE", "解析输入计算量无效")
            total += count
        return total

    def resolve_confirmation_target(self, ctx: OperationContext, resource_ref: ResourceRef, action: str) -> TargetSnapshot:
        """Resolve confirmation from persisted, current batch snapshots only."""
        if resource_ref.type == "batch_prediction" and action == "batch.submit":
            row = self._db().scalar(select(BatchPredictionRow).where(BatchPredictionRow.id == resource_ref.id))
            if row is None or not ctx.actor.can_access_project(row.project_id):
                raise BatchServiceError(404, "RESOURCE_NOT_FOUND", "批量草稿不存在或当前主体不可见")
            self._ensure_type_enabled(row.chromatography_type)
            self._ensure_batch_available(row)
            validation = self._db().scalar(select(BatchValidationRow).where(BatchValidationRow.id == row.validation_id)) if row.validation_id else None
            if (row.submitted_at is not None or validation is None or validation.status != "READY"
                    or row.draft_status != "READY" or validation.draft_revision != row.revision
                    or validation.config_hash != row.config_hash):
                raise BatchServiceError(412, "CONFIRMATION_STALE", "批量校验快照已变化或不可提交")
            self._assert_batch_model_binding_current(ctx, row, validation)
            input_hash, selected_rows = self._resolved_input_aggregate(validation.id)
            model_hash = _hash(validation.model_snapshot)
            expected_hash = _hash({
                "draft_id": row.id, "draft_config": self._config(row),
                "draft_revision": row.revision, "validation_id": validation.id,
                "validation_revision": validation.draft_revision, "validation_status": validation.status,
                "model_snapshot_hash": model_hash, "resolved_input_hash": input_hash,
                "selected_row_ids": selected_rows, "resource_budget": row.resource_budget,
            })
            return TargetSnapshot(
                project_id=row.project_id, resource_ref=resource_ref, revision=row.revision,
                config_hash=expected_hash, etag=row.etag, policy_version="1",
                summary=[
                    {"label": "有效行", "value": len(selected_rows)},
                    {"label": "预计 evaluations", "value": validation.total_evaluations},
                    {"label": "模型", "value": f"{validation.model_snapshot.get('id')}@{validation.model_snapshot.get('version')}"},
                    {"label": "解析输入摘要", "value": input_hash[:12]},
                ],
                impact={"row_count": len(selected_rows), "max_evaluations": row.resource_budget.get("max_evaluations")},
            )
        if resource_ref.type == "batch_retry_plan" and action == "batch.retry_failed":
            plan = self._db().scalar(select(BatchRetryPlanRow).where(BatchRetryPlanRow.id == resource_ref.id))
            batch = self._db().scalar(select(BatchPredictionRow).where(BatchPredictionRow.id == plan.parent_batch_id)) if plan else None
            if plan is None or batch is None or not ctx.actor.can_access_project(plan.project_id):
                raise BatchServiceError(404, "RESOURCE_NOT_FOUND", "批量重试计划不存在或当前主体不可见")
            self._ensure_type_enabled(batch.chromatography_type)
            self._ensure_batch_available(batch)
            validation = self._db().scalar(select(BatchValidationRow).where(BatchValidationRow.id == plan.validation_id)) if plan.validation_id else None
            input_hash, selected_rows = self._resolved_input_aggregate(plan.validation_id or "", plan.row_ids)
            latest = {item.row_id: item for item in self._latest_result_rows(batch.id, validation.id)}
            if (batch.revision != plan.revision or validation is None or validation.status != "READY"
                    or _hash(validation.model_snapshot) != plan.model_snapshot_hash
                    or input_hash != plan.resolved_input_hash
                    or any(latest.get(item) is None or latest[item].status != "FAILED" for item in plan.row_ids)):
                raise BatchServiceError(412, "CONFIRMATION_STALE", "批量重试计划已过期")
            self._assert_batch_model_binding_current(ctx, batch, validation)
            return TargetSnapshot(
                project_id=plan.project_id, resource_ref=resource_ref, revision=plan.revision,
                config_hash=plan.config_hash, etag=plan.etag, policy_version="1",
                summary=[{"label": "失败行", "value": len(plan.row_ids)},
                         {"label": "预计 evaluations", "value": plan.estimated_evaluations},
                         {"label": "解析输入摘要", "value": input_hash[:12]}],
                impact={"retry_row_count": len(plan.row_ids), "source_attempt": plan.expected_attempt},
            )
        raise BatchServiceError(404, "RESOURCE_NOT_FOUND", "该 batch 资源不支持此确认动作")

    def _assert_batch_model_binding_current(
        self,
        ctx: OperationContext,
        row: BatchPredictionRow,
        validation: BatchValidationRow | None,
    ) -> None:
        if (validation is None or validation.batch_id != row.id
                or validation.project_id != row.project_id
                or validation.id != row.validation_id
                or validation.status != "READY"):
            raise BatchServiceError(412, "BATCH_MODEL_BINDING_STALE", "批量模型校验快照已变化")
        try:
            payload = TypeAdapter(BatchDraftCreate).validate_python(self._config(row))
            current = self._resolve_batch_model(ctx, payload).model_dump(mode="json")
            validation_snapshot = ModelSnapshot.model_validate(validation.model_snapshot).model_dump(mode="json")
            row_snapshot = ModelSnapshot.model_validate(row.model_snapshot).model_dump(mode="json")
        except BatchServiceError:
            raise
        except (TypeError, ValueError) as exc:
            raise BatchServiceError(412, "BATCH_MODEL_BINDING_STALE", "批量模型校验快照已变化") from exc
        if current != validation_snapshot or row_snapshot != validation_snapshot:
            raise BatchServiceError(412, "BATCH_MODEL_BINDING_STALE", "批量模型版本或制品已变化，请重新校验")
        if row.chromatography_type != "tlc":
            payload = TypeAdapter(BatchDraftCreate).validate_python(self._config(row))
            for item in self._db().scalars(select(BatchValidationItemRow).where(
                BatchValidationItemRow.validation_id == validation.id,
                BatchValidationItemRow.status == "VALID",
            )):
                request_data = (item.resolved_input_snapshot or {}).get("request")
                if not isinstance(request_data, dict) or request_data.get("model_selection") != payload.model_selection.model_dump(mode="json"):
                    raise BatchServiceError(412, "BATCH_MODEL_BINDING_STALE", "批量行模型选择与确认快照不一致")

    def submit(self, ctx: OperationContext, batch_id: str, payload: BatchSubmit, *, idempotency_key: str | None = None) -> BatchAccepted:
        row = self._get(ctx, batch_id, lock=True)
        self._authorize(ctx, row.project_id, "batch:submit")
        validation = self.validation(ctx, batch_id)
        if validation.status != "READY" or row.draft_status != "READY":
            raise BatchServiceError(409, "BATCH_NOT_READY", "批量草稿尚未通过校验")
        self._ensure_batch_available(row)
        validation_row = self._db().get(BatchValidationRow, row.validation_id) if row.validation_id else None
        self._assert_batch_model_binding_current(ctx, row, validation_row)
        if self.confirmation is None or not callable(getattr(self.confirmation, "consume_for_mutation", None)):
            raise BatchServiceError(503, "DEPENDENCY_UNAVAILABLE", "Confirmation 服务未配置", retryable=True)
        # Confirmation binding is checked by the shared service.  A missing
        # resolver/target is deliberately surfaced as a dependency error.
        target = self.resolve_confirmation_target(ctx, ResourceRef(type="batch_prediction", id=row.id), "batch.submit")
        try:
            self.confirmation.consume_for_mutation(ctx, payload.confirmation_id, target=target, action="batch.submit", result_ref={"batch_id": row.id}, session=self._db())
        except TypeError as exc:
            raise BatchServiceError(503, "DEPENDENCY_UNAVAILABLE", "Confirmation 目标解析器未配置", retryable=True) from exc
        except TaskError as exc:
            if exc.code == "CONFIRMATION_INVALID":
                raise BatchServiceError(412, "CONFIRMATION_STALE", "批量草稿或校验快照已变化") from exc
            raise BatchServiceError(exc.status_code, exc.code, exc.message, retryable=exc.retryable) from exc
        task_repo = SQLAlchemyTaskRepository(lambda: self._db())
        job = add_job_rows(task_repo, self._db(), project_id=row.project_id, owner_id=ctx.actor.actor_id,
            kind="batch_prediction", resource_ref=ResourceRef(type="batch_prediction", id=row.id),
            cancelable=True, retryable=True, resume_mode="restart", deadline_at=None)
        row.job_id, row.attempt, row.execution_status = job.id, job.attempt, job.status
        row.submitted_at = _now()
        row.counts = {"total": validation.total_rows, "queued": validation.valid_rows, "running": 0, "succeeded": 0, "failed": 0, "skipped": validation.excluded_rows, "canceled": 0}
        self._db().flush()
        self._audit(ctx, action="batch.submit", row=row, confirmation_id=payload.confirmation_id, job_id=job.id, after={"status": row.execution_status, "counts": row.counts})
        accepted = BatchAccepted(id=row.id, job_id=job.id, attempt=job.attempt, execution_status=JobStatus(job.status), resource_ref={"type": "batch_prediction", "id": row.id})
        return accepted

    def rows(self, ctx: OperationContext, batch_id: str, *, status_filter: str | None, cursor: str | None, limit: int, cursor_codec: SignedCursorCodec | None = None) -> BatchRowsPage:
        row = self._get(ctx, batch_id)
        validation = self._db().scalar(select(BatchValidationRow).where(BatchValidationRow.id == row.validation_id)) if row.validation_id else None
        if row.validation_id and (validation is None or validation.batch_id != row.id or validation.project_id != row.project_id):
            raise BatchServiceError(404, "RESOURCE_NOT_FOUND", "当前草稿没有校验结果")
        codec = _require_batch_cursor_codec(cursor_codec)
        scope = _batch_cursor_scope(ctx, row, validation, status_filter=status_filter, endpoint="batch.rows.v1")
        query = select(BatchRowResultRow).where(BatchRowResultRow.project_id == row.project_id, BatchRowResultRow.batch_id == row.id)
        if row.validation_id:
            query = query.where(BatchRowResultRow.validation_id == row.validation_id)
        query = query.order_by(BatchRowResultRow.source_row_number, BatchRowResultRow.attempt.desc())
        # A retry appends a new projection for the same logical row_id.  Keep
        # the failed attempt queryable in SQL while exposing only the latest
        # state through the public rows endpoint.
        latest: dict[str, BatchRowResultRow] = {}
        for item in self._db().scalars(query):
            current = latest.get(item.row_id)
            if current is None or int(item.attempt) > int(current.attempt):
                latest[item.row_id] = item
        items = sorted(latest.values(), key=lambda item: (item.source_row_number, item.row_id))
        if status_filter:
            items = [item for item in items if item.status == status_filter]
        items = _after_row_cursor(items, cursor, codec=codec, scope=scope)
        window, more = items[:limit], len(items) > limit
        return BatchRowsPage(rows=[BatchRowResult(row_id=item.row_id, source_row_number=item.source_row_number, input_snapshot=item.input_snapshot, result=item.result, error=item.error, warnings=item.warnings, attempt=item.attempt, status=item.status) for item in window], next_cursor=codec.encode(after=(int(window[-1].source_row_number), window[-1].row_id), scope=scope) if more and window else None, has_more=more)

    def cancel(self, ctx: OperationContext, batch_id: str, payload: BatchCancel, *, if_match: str) -> BatchDraft:
        row = self._get(ctx, batch_id, lock=True)
        self._authorize(ctx, row.project_id, "batch:cancel")
        if row.etag != if_match:
            raise BatchServiceError(412, "ETAG_MISMATCH", "资源版本已变化")
        before = {"status": row.execution_status, "counts": dict(row.counts or {})}
        job = self._db().get(Job, row.job_id, with_for_update=True) if row.job_id else None
        if job is None:
            raise BatchServiceError(409, "STATE_CONFLICT", "批量任务尚未受理")
        attempt = self._db().scalar(select(JobAttempt).where(JobAttempt.job_id == job.id, JobAttempt.attempt == job.attempt).with_for_update())
        if attempt is None:
            raise BatchServiceError(409, "STATE_CONFLICT", "批量任务 attempt 不存在")
        # Route the state change through the shared CAS lifecycle.  Queued
        # work may be finalized immediately; running work remains
        # CANCEL_REQUESTED until a worker presents the current fencing token.
        old_status = job.status
        target = None
        if old_status == JobStatus.QUEUED.value:
            target = JobStatus.CANCELED
        elif old_status == JobStatus.RUNNING.value:
            target = JobStatus.CANCEL_REQUESTED
        if target is not None:
            transitioned = sync_job_lifecycle(
                SQLAlchemyTaskRepository(lambda: self._db()), self._db(),
                job_id=job.id, status=target, expected_attempt=job.attempt,
                fencing_token=attempt.fencing_token, resource_ref=job.resource_ref,
                finished_at=_now() if target is JobStatus.CANCELED else None,
            )
            if transitioned is None:
                raise BatchServiceError(409, "STATE_CONFLICT", "批量任务状态已由其他执行者改变")
            job = transitioned
            row.execution_status = job.status
            dispatch_id = _id("dispatch")
            self._db().add(OutboxEvent(
                dispatch_id=dispatch_id, aggregate_type="job", aggregate_id=job.id,
                event_type="job.cancel", payload={"job_id": job.id, "attempt": job.attempt,
                "dispatch_id": dispatch_id, "fencing_token": int(attempt.fencing_token or 0)},
                status="PENDING", attempts=0, available_at=_now(), created_at=_now(),
            ))
            if target is JobStatus.CANCELED:
                for result in self._db().scalars(select(BatchRowResultRow).where(BatchRowResultRow.batch_id == row.id, BatchRowResultRow.status.in_(["QUEUED", "RUNNING"])).with_for_update()):
                    result.status = "CANCELED"
                    result.attempt = job.attempt
        counts = {key: 0 for key in ("total", "queued", "running", "succeeded", "failed", "skipped", "canceled")}
        for result in self._latest_result_rows(row.id):
            counts["total"] += 1
            counts[result.status.lower()] = counts.get(result.status.lower(), 0) + 1
        row.counts = counts
        if job.status != old_status:
            row.revision += 1
            row.etag = _etag({"id": row.id, "revision": row.revision, "status": row.execution_status, "reason": payload.reason})
        self._db().flush()
        self._audit(ctx, action="batch.cancel", row=row, job_id=job.id, before=before, after={"status": row.execution_status, "counts": counts, "reason": payload.reason})
        return self._view(row)

    def create_retry_plan(self, ctx: OperationContext, batch_id: str, payload: BatchRetryPlanCreate, *, if_match: str) -> BatchRetryPlan:
        row = self._get(ctx, batch_id, lock=True)
        self._authorize(ctx, row.project_id, "batch:write")
        if row.etag != if_match:
            raise BatchServiceError(412, "ETAG_MISMATCH", "资源版本已变化")
        self._ensure_batch_available(row)
        if row.job_id is None or row.validation_id is None or row.submitted_at is None:
            raise BatchServiceError(409, "STATE_CONFLICT", "批量任务尚未受理")
        job = self._db().get(Job, row.job_id, with_for_update=True)
        if job is None or row.attempt != job.attempt or job.status not in {
            JobStatus.FAILED.value, JobStatus.PARTIAL.value, JobStatus.CANCELED.value,
            JobStatus.TIMED_OUT.value, JobStatus.INTERRUPTED.value,
        }:
            raise BatchServiceError(409, "STATE_CONFLICT", "批量任务 attempt 投影不一致")
        if len(payload.row_ids) != len(set(payload.row_ids)):
            raise BatchServiceError(422, "INVALID_RETRY_ROWS", "retry plan 行 id 不能重复")
        latest = {item.row_id: item for item in self._latest_result_rows(row.id, row.validation_id)}
        if any(latest.get(item) is None or latest[item].status != "FAILED" for item in payload.row_ids):
            raise BatchServiceError(409, "INVALID_RETRY_ROWS", "retry plan 只能包含失败行")
        validation = self._db().get(BatchValidationRow, row.validation_id)
        if validation is None or validation.status != "READY":
            raise BatchServiceError(409, "STATE_CONFLICT", "批量校验快照不可重试")
        input_hash, row_ids = self._resolved_input_aggregate(validation.id, payload.row_ids)
        estimated_evaluations = self._resolved_evaluation_count(validation.id, row_ids)
        if estimated_evaluations > row.resource_budget.get("max_evaluations", 0):
            raise BatchServiceError(422, "BATCH_BUDGET_EXCEEDED", "失败行重试计算量超过批量预算")
        model_hash = _hash(validation.model_snapshot)
        plan_id = _id("batch_retry")
        plan_revision = row.revision
        expected_attempt = job.attempt + 1
        plan_config_hash = _hash({
            "draft_config_hash": row.config_hash, "batch_revision": plan_revision,
            "validation_id": validation.id, "model_snapshot_hash": model_hash,
            "resolved_input_hash": input_hash, "row_ids": row_ids,
            "expected_attempt": expected_attempt,
        })
        plan_etag = _etag({"id": plan_id, "row_ids": row_ids, "config_hash": plan_config_hash,
                           "revision": plan_revision, "validation_id": validation.id,
                           "model_snapshot_hash": model_hash, "resolved_input_hash": input_hash})
        plan = BatchRetryPlanRow(
            id=plan_id, project_id=row.project_id, parent_batch_id=row.id,
            revision=plan_revision, config_hash=plan_config_hash, etag=plan_etag,
            row_ids=row_ids, expected_attempt=expected_attempt, estimated_evaluations=estimated_evaluations,
            validation_id=validation.id, model_snapshot_hash=model_hash, resolved_input_hash=input_hash,
        )
        self._db().add(plan)
        self._db().flush()
        self._audit(ctx, action="batch.retry_plan", row=row, after={"retry_plan_id": plan.id, "row_ids": plan.row_ids, "revision": plan.revision, "config_hash": plan.config_hash})
        return BatchRetryPlan(id=plan.id, revision=plan.revision, config_hash=plan.config_hash, etag=plan.etag,
            row_ids=plan.row_ids, parent_batch_id=plan.parent_batch_id, expected_attempt=plan.expected_attempt,
            estimated_evaluations=plan.estimated_evaluations, validation_id=plan.validation_id,
            model_snapshot_hash=plan.model_snapshot_hash, resolved_input_hash=plan.resolved_input_hash)

    def retry_failed(self, ctx: OperationContext, batch_id: str, payload: BatchRetryFailed, *, if_match: str) -> BatchAccepted:
        row = self._get(ctx, batch_id, lock=True)
        self._authorize(ctx, row.project_id, "batch:submit")
        if row.etag != if_match:
            raise BatchServiceError(412, "ETAG_MISMATCH", "资源版本已变化")
        plan = self._db().scalar(select(BatchRetryPlanRow).where(BatchRetryPlanRow.id == payload.retry_plan_id, BatchRetryPlanRow.parent_batch_id == row.id))
        if plan is None:
            raise BatchServiceError(404, "RESOURCE_NOT_FOUND", "重试计划不存在或不可见")
        self._ensure_batch_available(row)
        if row.validation_id != plan.validation_id:
            raise BatchServiceError(412, "BATCH_MODEL_BINDING_STALE", "重试计划校验快照已变化")
        validation_row = self._db().get(BatchValidationRow, plan.validation_id) if plan.validation_id else None
        self._assert_batch_model_binding_current(ctx, row, validation_row)
        target = self.resolve_confirmation_target(ctx, ResourceRef(type="batch_retry_plan", id=plan.id), "batch.retry_failed")
        if plan.revision != row.revision or target.config_hash != plan.config_hash or target.etag != plan.etag:
            raise BatchServiceError(412, "CONFIRMATION_STALE", "重试计划已因批量快照变化失效")
        if self.confirmation is None or not callable(getattr(self.confirmation, "consume_for_mutation", None)):
            raise BatchServiceError(503, "DEPENDENCY_UNAVAILABLE", "Confirmation 服务未配置", retryable=True)
        try:
            self.confirmation.consume_for_mutation(ctx, payload.confirmation_id, target=target, action="batch.retry_failed", result_ref={"batch_id": row.id, "retry_plan_id": plan.id, "row_ids": plan.row_ids, "plan_revision": plan.revision, "config_hash": plan.config_hash}, session=self._db())
        except TypeError as exc:
            raise BatchServiceError(503, "DEPENDENCY_UNAVAILABLE", "Confirmation 目标解析器未配置", retryable=True) from exc
        except TaskError as exc:
            if exc.code == "CONFIRMATION_INVALID":
                raise BatchServiceError(412, "CONFIRMATION_STALE", "批量重试计划已过期") from exc
            raise BatchServiceError(exc.status_code, exc.code, exc.message, retryable=exc.retryable) from exc
        task_repo = SQLAlchemyTaskRepository(lambda: self._db())
        job = self._db().get(Job, row.job_id, with_for_update=True) if row.job_id else None
        if job is None or job.attempt != row.attempt:
            raise BatchServiceError(409, "STATE_CONFLICT", "批量任务 attempt 投影不一致")
        if job.status not in {JobStatus.FAILED.value, JobStatus.PARTIAL.value, JobStatus.CANCELED.value, JobStatus.TIMED_OUT.value, JobStatus.INTERRUPTED.value}:
            raise BatchServiceError(409, "STATE_CONFLICT", "批量任务当前不可重试")
        all_selected = list(self._db().scalars(select(BatchRowResultRow).where(
            BatchRowResultRow.batch_id == row.id, BatchRowResultRow.validation_id == plan.validation_id,
            BatchRowResultRow.row_id.in_(plan.row_ids),
        ).with_for_update()))
        latest: dict[str, BatchRowResultRow] = {}
        for item in all_selected:
            current = latest.get(item.row_id)
            if current is None or int(item.attempt) > int(current.attempt):
                latest[item.row_id] = item
        selected = [latest[item] for item in plan.row_ids if item in latest]
        if len(selected) != len(plan.row_ids) or any(item.status != "FAILED" for item in selected):
            raise BatchServiceError(409, "INVALID_RETRY_ROWS", "retry 执行前选中行必须仍为 FAILED")
        job, _dispatch = add_job_attempt_rows(task_repo, self._db(), job_id=job.id, attempt=plan.expected_attempt, status=JobStatus.QUEUED, resume_mode=job.resume_mode)
        for item in selected:
            self._db().add(BatchRowResultRow(id=_id("bresult"), project_id=item.project_id, batch_id=item.batch_id, validation_id=item.validation_id, row_id=item.row_id, source_row_number=item.source_row_number, input_snapshot=dict(item.input_snapshot or {}), result=None, error=None, warnings=list(item.warnings or []) + [{"code": "PREVIOUS_ATTEMPT", "message": f"历史失败 attempt={item.attempt}"}], attempt=job.attempt, status="QUEUED"))
        row.job_id, row.attempt, row.execution_status = job.id, job.attempt, job.status
        row.revision += 1
        row.etag = _etag({"id": row.id, "revision": row.revision, "status": row.execution_status, "attempt": row.attempt})
        row.counts = {**(row.counts or {}), "queued": len(selected), "running": 0, "failed": max(0, (row.counts or {}).get("failed", 0) - len(selected))}
        self._db().flush()
        self._audit(ctx, action="batch.retry_failed", row=row, confirmation_id=payload.confirmation_id, job_id=job.id, after={"status": row.execution_status, "retry_plan_id": plan.id, "row_ids": plan.row_ids})
        return BatchAccepted(id=row.id, job_id=job.id, attempt=job.attempt, execution_status=JobStatus(job.status), resource_ref={"type": "batch_prediction", "id": row.id})

    def export(self, ctx: OperationContext, batch_id: str, payload: BatchExportCreate, *, if_match: str) -> BatchExportAccepted:
        row = self._get(ctx, batch_id, lock=True)
        self._authorize(ctx, row.project_id, "batch:read")
        if row.etag != if_match:
            raise BatchServiceError(412, "ETAG_MISMATCH", "资源版本已变化")
        if row.validation_id is None or row.submitted_at is None or row.execution_status not in {
            JobStatus.SUCCEEDED.value, JobStatus.PARTIAL.value, JobStatus.FAILED.value,
            JobStatus.CANCELED.value, JobStatus.TIMED_OUT.value, JobStatus.INTERRUPTED.value,
        }:
            raise BatchServiceError(409, "STATE_CONFLICT", "仅终态批量任务可导出")
        validation = self._db().get(BatchValidationRow, row.validation_id)
        if validation is None or validation.status != "READY":
            raise BatchServiceError(409, "STATE_CONFLICT", "批量校验快照不可导出")
        current_rows = self._latest_result_rows(row.id, row.validation_id)
        generated_at = _now()
        records: list[dict[str, Any]] = []
        model_version = (validation.model_snapshot or {}).get("version")
        for item in current_rows:
            record: dict[str, Any] = {
                "record_id": item.row_id, "created_at": item.created_at.isoformat(),
                "status": item.status, "source": "batch_prediction",
                "chromatography_type": row.chromatography_type,
            }
            if "input_snapshot" in payload.include:
                record["input_snapshot"] = dict(item.input_snapshot or {})
            if "result" in payload.include:
                record["result"] = dict(item.result or {}) if item.result is not None else None
            if "model_version" in payload.include:
                record["model_version"] = model_version
            if "warnings" in payload.include:
                from .production_worker import _merge_record_warnings

                warnings = list(item.warnings or [])
                warnings.append({
                    "code": "BATCH_SOURCE_ROW",
                    "message": f"源数据第 {item.source_row_number} 行",
                })
                if item.status == "SKIPPED" and isinstance(item.error, dict):
                    code, message = item.error.get("code"), item.error.get("message")
                    if isinstance(code, str) and isinstance(message, str):
                        warnings.append({"code": code, "message": message})
                record["warnings"] = _merge_record_warnings([], warnings)
            records.append(record)
        model_hash = _hash(validation.model_snapshot)
        resolved_hash, selected_rows = self._resolved_input_aggregate(validation.id)
        source = {
            "batch_id": row.id, "draft_revision": validation.draft_revision,
            "validation_id": validation.id, "validation_revision": validation.draft_revision,
            "model_snapshot": validation.model_snapshot, "model_snapshot_hash": model_hash,
            "resolved_input_hash": resolved_hash, "attempt": int(row.attempt or 1),
            "row_ids": selected_rows,
        }
        snapshot = {
            "format_version": "1.0", "generated_at": generated_at.isoformat(),
            "include": list(payload.include), "source": source, "records": records,
        }
        snapshot_hash = _hash(snapshot)
        export_id = _id("batch_export")
        task_repo = SQLAlchemyTaskRepository(lambda: self._db())
        job = add_job_rows(task_repo, self._db(), project_id=row.project_id, owner_id=ctx.actor.actor_id,
            kind="batch_prediction", resource_ref=ResourceRef(type="batch_export", id=export_id),
            cancelable=True, retryable=True, resume_mode="restart", deadline_at=None)
        export = BatchExportRow(id=export_id, project_id=row.project_id, batch_id=row.id, job_id=job.id,
            format=payload.format, include=list(payload.include), source_snapshot=snapshot,
            source_snapshot_hash=snapshot_hash, status=JobStatus.QUEUED.value)
        self._db().add(export)
        self._db().flush()
        self._audit(ctx, action="batch.export", row=row, job_id=job.id, after={"export_id": export_id,
            "format": payload.format, "include": list(payload.include), "source_snapshot_hash": snapshot_hash})
        return BatchExportAccepted(id=export_id, job_id=job.id, status="QUEUED", resource_ref={"type": "batch_export", "id": export_id})

    def get_export(self, ctx: OperationContext, export_id: str) -> BatchExportView:
        export = self._db().get(BatchExportRow, export_id)
        if export is None or not ctx.actor.can_access_project(export.project_id):
            raise BatchServiceError(404, "RESOURCE_NOT_FOUND", "批量导出不存在或当前主体不可见")
        self._authorize(ctx, export.project_id, "batch:read")
        batch = self._db().get(BatchPredictionRow, export.batch_id)
        if batch is None or batch.project_id != export.project_id:
            raise BatchServiceError(404, "RESOURCE_NOT_FOUND", "批量导出不存在或当前主体不可见")
        self._ensure_type_enabled(batch.chromatography_type)
        snapshot = export.source_snapshot or {}
        source = snapshot.get("source") if isinstance(snapshot, dict) else {}
        return BatchExportView(id=export.id, batch_id=export.batch_id, job_id=export.job_id,
            format=export.format, include=export.include, status=export.status,
            source_revision=int(source.get("draft_revision", 1)), validation_id=str(source.get("validation_id", "missing")),
            source_attempt=int(source.get("attempt", 1)), source_snapshot_hash=export.source_snapshot_hash or ("0" * 64),
            artifact_id=export.artifact_id, error=export.error, created_at=export.created_at, completed_at=export.completed_at)

    def download_export(self, ctx: OperationContext, export_id: str) -> tuple[bytes, str, str]:
        view = self.get_export(ctx, export_id)
        if view.status != "SUCCEEDED" or not view.artifact_id:
            raise BatchServiceError(409, "ARTIFACT_NOT_AVAILABLE", "批量导出制品尚未生成")
        artifact = self._db().scalar(select(Artifact).where(
            Artifact.id == view.artifact_id, Artifact.project_id == self._db().get(BatchExportRow, export_id).project_id,
            Artifact.kind == "batch_export", Artifact.status == "AVAILABLE", Artifact.scan_status == "PASSED",
        ))
        if artifact is None or not isinstance(artifact.metadata_json, dict) or artifact.metadata_json.get("source_ref") != {"type": "batch_export", "id": export_id}:
            raise BatchServiceError(404, "RESOURCE_NOT_FOUND", "批量导出制品不存在或不可见")
        internal_downloader = getattr(self.artifact, "download_content", None)
        downloader = internal_downloader or getattr(self.artifact, "download", None)
        if not callable(downloader):
            raise BatchServiceError(503, "DEPENDENCY_UNAVAILABLE", "Artifact 下载端口未配置", retryable=True)
        try:
            view_artifact, content = downloader(view.artifact_id, ctx.actor)
        except ArtifactServiceError as exc:
            raise BatchServiceError(
                exc.status_code, exc.code, exc.message, retryable=exc.retryable,
            ) from exc
        except Exception as exc:
            raise BatchServiceError(503, "DEPENDENCY_UNAVAILABLE", "批量导出制品暂不可下载", retryable=True) from exc
        extensions = {"json": ("application/json", "json"), "csv": ("text/csv; charset=utf-8", "csv"),
                      "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx")}
        media_type, extension = extensions[view.format]
        if (getattr(view_artifact, "id", None) != artifact.id
                or getattr(view_artifact, "project_id", None) != artifact.project_id
                or getattr(view_artifact, "kind", None) != "batch_export"
                or getattr(view_artifact, "status", None) != "AVAILABLE"
                or getattr(view_artifact, "scan_status", None) != "PASSED"
                or getattr(view_artifact, "size_bytes", None) != artifact.size_bytes
                or getattr(view_artifact, "sha256", None) != artifact.sha256):
            raise BatchServiceError(404, "RESOURCE_NOT_FOUND", "批量导出制品类型不匹配")
        if (not artifact.sha256 or len(content) != artifact.size_bytes
                or hashlib.sha256(content).hexdigest() != artifact.sha256):
            raise BatchServiceError(404, "RESOURCE_NOT_FOUND", "批量导出制品内容与持久化摘要不匹配")
        return bytes(content), media_type, f"batch-{export_id}.{extension}"


__all__ = ["BatchPredictionRow", "BatchValidationRow", "BatchValidationItemRow", "BatchRowResultRow", "BatchRetryPlanRow", "BatchExportRow", "BatchPredictionService", "BatchServiceError", "build_batch_template_metadata", "default_batch_template_registry"]
