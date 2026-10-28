"""Prediction-owned persistence ports and SQLAlchemy adapters.

The module owns its two projection tables.  Job, outbox and idempotency rows
remain the M1 platform rows; callers own the transaction and this adapter never
commits or closes a session.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from contextlib import nullcontext
import hashlib
import json
from typing import Any, Callable
from uuid import uuid4

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, JSON, Integer, MetaData, String, Text, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from chrom_platform.core.dto import ResourceRef
from chrom_platform.core.errors import APIError
from chrom_platform.infrastructure.models import (
    AuditEvent,
    IdempotencyRecord,
    Job,
    JobAttempt,
    JobEvent,
    OutboxEvent,
)
from chrom_platform.infrastructure.db import Base, MYSQL_TABLE_KWARGS
from chrom_platform.artifact_core.models import Artifact  # noqa: F401 - register M1 metadata targets

_PROJECT_ID = Base.metadata.tables["project"].c.id
_PREDICTION_RECORD_ID = Base.metadata.tables.get("prediction_record")
_JOB_ID = Base.metadata.tables["job"].c.id
from chrom_platform.modules.data_center.ports import (
    PredictionRecordDraft,
    PredictionRecordStagingPort,
    PredictionReplaySourceData,
    default_prediction_record_staging_port,
)
from chrom_platform.modules.data_center.errors import DataUnavailableError
from chrom_platform.task_core.service import OperationContext
from chrom_platform.task_core.state import JobStatus

from .ports import (
    AtomicPredictionCreatePort,
    AtomicPredictionCreateResult,
    IdempotencyLookup,
    PredictionAccessPort,
    PredictionIdempotencyPort,
    PredictionModelPort,
    PredictionReadPort,
    PredictionStructurePort,
    StructureSnapshot,
)
from .schemas import (
    ModelSnapshot,
    Prediction,
    PredictionAccepted,
    PredictionCreate,
    PredictionExecutionRequest,
    PredictionExecutionInputSnapshot,
    build_prediction_execution_request,
    to_public_prediction_input_snapshot,
    PublicErrorSummary,
)


class PredictionBase(DeclarativeBase):
    """Separate metadata so M1 migrations remain unchanged in this slice."""

    metadata = MetaData()


def _json() -> Mapped[dict[str, Any]]:
    return mapped_column(JSON, nullable=False, default=dict)


class PredictionRow(PredictionBase):
    __tablename__ = "prediction"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(96), nullable=False)
    prediction_record_id: Mapped[str] = mapped_column(String(96), nullable=False)
    job_id: Mapped[str] = mapped_column(String(96), nullable=False, unique=True)
    actor_id: Mapped[str] = mapped_column(String(96), nullable=False)
    chromatography_type: Mapped[str] = mapped_column(String(32), nullable=False)
    tool: Mapped[str] = mapped_column(String(96), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=JobStatus.QUEUED.value)
    model_snapshot: Mapped[dict[str, Any]] = _json()
    input_snapshot: Mapped[dict[str, Any]] = _json()
    request_snapshot: Mapped[dict[str, Any]] = _json()
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    warnings: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    conversation_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    agent_run_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    data_origin: Mapped[str] = mapped_column(String(16), nullable=False, default="production")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    etag: Mapped[str] = mapped_column(String(128), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(("project_id",), (_PROJECT_ID,), name="fk_prediction_project_id_project", ondelete="RESTRICT"),
        ForeignKeyConstraint(("job_id",), (_JOB_ID,), name="fk_prediction_job_id_job", ondelete="RESTRICT"),
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint("status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELED','TIMED_OUT','INTERRUPTED')", name="status"),
        CheckConstraint("data_origin IN ('production','fixture')", name="data_origin"),
        Index("ix_prediction_project_status_created", "project_id", "status", "created_at"),
        MYSQL_TABLE_KWARGS,
    )


class PredictionPersistenceUnavailable(APIError):
    def __init__(self, message: str = "预测持久化 Session 未配置") -> None:
        super().__init__(503, "PREDICTION_DEPENDENCY_UNAVAILABLE", message, retryable=True)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware_dt(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _etag(*, prediction_id: str, revision: int, status: str, result: Any, error: Any) -> str:
    return _hash({"id": prediction_id, "revision": revision, "status": status, "result": result, "error": error})


def _job_etag(job: Job) -> str:
    """Keep worker-side job versions identical to task-core versions."""

    return _hash(
        {
            "id": job.id,
            "project_id": job.project_id,
            "status": JobStatus(job.status),
            "attempt": job.attempt,
            "revision": job.revision,
            "progress": job.progress,
            "result_ref": job.result_ref,
            "error": job.error,
        }
    )


def _finish_prediction_progress(job: Job) -> None:
    progress = dict(job.progress or {})
    total = progress.get("total", 1)
    progress.update(completed=total, total=total, unit=progress.get("unit", "prediction"), stage="finished")
    job.progress = progress


def _expired(row: IdempotencyRecord) -> bool:
    expires_at = getattr(row, "expires_at", None)
    if expires_at is None:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= _now()


def _next_event_seq(db: Session, job_id: str) -> int:
    value = db.scalar(select(func.max(JobEvent.seq)).where(JobEvent.job_id == job_id))
    return int(value if value is not None else -1) + 1


def _json_result(result: object | None) -> Any:
    if hasattr(result, "model_dump"):
        try:
            return result.model_dump(mode="json")
        except TypeError:
            return result.model_dump()
    return result


def _as_accepted(value: Any) -> PredictionAccepted:
    if isinstance(value, PredictionAccepted):
        return value
    payload = dict(value)
    if isinstance(payload.get("status"), str):
        payload["status"] = JobStatus(payload["status"])
    return PredictionAccepted.model_validate(payload)


class SQLAlchemyPredictionPersistence(
    AtomicPredictionCreatePort,
    PredictionIdempotencyPort,
    PredictionReadPort,
):
    """Caller-owned-session adapter for prediction creation and reads."""

    def __init__(
        self,
        session: Session | None = None,
        *,
        session_factory: Callable[[], Session] | None = None,
        record_writer: PredictionRecordStagingPort | None = None,
    ) -> None:
        self.session = session
        self.session_factory = session_factory
        self.record_writer = record_writer

    def _session(self, session: Session | None = None) -> Session:
        value = session if session is not None else self.session
        if value is None:
            raise PredictionPersistenceUnavailable()
        return value

    def _record_writer(self) -> PredictionRecordStagingPort:
        return self.record_writer or default_prediction_record_staging_port()

    @staticmethod
    def _scope(ctx: OperationContext, project_id: str, key: str):
        from chrom_platform.core.idempotency import IdempotencyScope

        return IdempotencyScope(ctx.actor.actor_id, project_id, "POST", "/api/v1/predictions", key)

    def ensure_project_access(self, ctx: OperationContext, project_id: str, *, permission: str) -> None:
        if not ctx.actor.can_access_project(project_id):
            raise APIError(404, "RESOURCE_NOT_FOUND", "项目不存在或当前主体不可见")
        if not ctx.actor.can(permission):
            raise APIError(403, "PERMISSION_DENIED", "当前主体无权执行该操作")

    def get_replay_source(self, project_id: str, record: Any) -> PredictionReplaySourceData | None:
        """Resolve a replay only from validated persisted request and project structures."""
        from pydantic import TypeAdapter, ValidationError
        from chrom_platform.artifact_core.models import Structure
        from chrom_platform.structure_core.schemas import StructureView
        from .schemas import (
            GcPredictionCreate, GcPredictionReadV10, HplcPredictionCreate, HplcPredictionReadV10, ModelSnapshot,
            PredictionExecutionInputSnapshot, PredictionStoredRead,
        )
        from chrom_platform.modules.data_center.schemas import PredictionRecord

        try:
            record = PredictionRecord.model_validate(record)
        except (TypeError, ValueError, ValidationError):
            raise DataUnavailableError("预测记录快照不可验证") from None
        if record.project_id != project_id or not record.prediction_id:
            raise DataUnavailableError("预测记录项目或来源绑定不一致")

        db = self._session()
        row = db.scalar(select(PredictionRow).where(
            PredictionRow.id == record.prediction_id, PredictionRow.project_id == project_id,
        ))
        if row is None:
            return None
        try:
            request = TypeAdapter(PredictionStoredRead).validate_python(row.request_snapshot)
            model = ModelSnapshot.model_validate(row.model_snapshot)
            record_model = ModelSnapshot.model_validate(record.model_snapshot)
        except (TypeError, ValueError, ValidationError):
            raise DataUnavailableError("原始预测请求或模型快照不可验证") from None
        if (
            row.prediction_record_id != record.id
            or record_model.model_dump(mode="json") != model.model_dump(mode="json")
            or request.project_id != project_id
            or request.chromatography_type != row.chromatography_type
            or request.tool != row.tool
            or request.schema_version != row.schema_version
            or model.input_schema_version != request.schema_version
            or model.input_schema_version != row.schema_version
        ):
            raise DataUnavailableError("原始预测请求、模型与预测记录绑定不一致")
        if isinstance(request, HplcPredictionReadV10):
            raise DataUnavailableError("历史 HPLC schema 1.0 缺少本次原始输入和 profile，不能重放；请重新创建预测。")
        if isinstance(request, GcPredictionReadV10):
            raise DataUnavailableError("历史 GC schema 1.0 缺少 profile 绑定，不能重放；请重新创建预测。")
        if isinstance(request, GcPredictionCreate):
            try:
                frozen_input = PredictionExecutionInputSnapshot.model_validate(row.input_snapshot)
            except (TypeError, ValueError, ValidationError):
                raise DataUnavailableError("GC 输入快照不完整，不能重放。") from None
            if (
                frozen_input.structure_refs != request.structure_refs
                or frozen_input.input.model_dump(mode="json") != request.input.model_dump(mode="json")
                or request.model_selection.mode != "exact"
                or model.preprocessing_profile_id is None
                or model.preprocessing_profile_sha256 is None
                or model.preprocessing_profile_id != request.model_selection.preprocessing_profile_id
                or model.preprocessing_profile_sha256 != request.model_selection.preprocessing_profile_sha256
            ):
                raise DataUnavailableError("GC 输入、结构顺序或 profile 快照不一致，不能重放。")
        if isinstance(request, HplcPredictionCreate):
            try:
                frozen_input = PredictionExecutionInputSnapshot.model_validate(row.input_snapshot)
            except (TypeError, ValueError, ValidationError):
                raise DataUnavailableError("HPLC 原始输入快照不完整，不能重放。") from None
            if (
                frozen_input.structure_refs != request.structure_refs
                or frozen_input.input.model_dump(mode="json") != request.input.model_dump(mode="json")
                or request.model_selection.mode != "exact"
                or model.preprocessing_profile_id is None
                or model.preprocessing_profile_sha256 is None
                or model.preprocessing_profile_id != request.model_selection.preprocessing_profile_id
                or model.preprocessing_profile_sha256 != request.model_selection.preprocessing_profile_sha256
            ):
                raise DataUnavailableError("HPLC 原始输入、结构顺序或 profile 快照不一致，不能重放。")
        selection = request.model_selection
        if row.chromatography_type == "cc" and (
            model.preprocessing_profile_id is None
            or model.preprocessing_profile_sha256 is None
        ):
            raise DataUnavailableError(
                "历史 CC 预测没有预处理版本绑定，不能重放；请重新创建预测。"
            )
        if selection.mode == "exact":
            if (
                selection.model_id != model.id
                or selection.model_version != model.version
                or selection.preprocessing_profile_id != model.preprocessing_profile_id
                or selection.preprocessing_profile_sha256 != model.preprocessing_profile_sha256
            ):
                raise DataUnavailableError("原始精确模型选择与执行模型不一致")
        elif selection.mode == "champion":
            if selection.scope_id != model.business_scope_id:
                raise DataUnavailableError("原始 Champion 范围与执行模型不一致")
        else:  # pragma: no cover - strict request schema already rejects this
            raise DataUnavailableError("原始模型选择不可验证")

        structure_refs = list(request.structure_refs)
        modifier_refs = [item.structure_ref for item in getattr(request.input, "modifier_components", ())]
        all_refs = structure_refs + modifier_refs
        if not structure_refs:
            raise DataUnavailableError("原始结构引用缺失")
        structure_rows = db.scalars(select(Structure).where(
            Structure.project_id == project_id,
            Structure.id.in_(all_refs),
            Structure.is_valid.is_(True),
        )).all()
        by_id = {item.id: item for item in structure_rows}
        if set(by_id) != set(all_refs):
            raise DataUnavailableError("原始结构不存在、无效或不属于当前项目")

        def public_structure(ref: str) -> dict[str, Any]:
            item = by_id[ref]
            try:
                view = StructureView.model_validate({
                    "id": item.id, "project_id": item.project_id,
                    "schema_version": item.schema_version, "original_input": item.original_input,
                    "canonical_smiles": item.canonical_smiles, "is_valid": item.is_valid,
                    "has_stereochemistry": item.has_stereochemistry,
                    "stereochemistry_status": item.stereochemistry_status,
                    "stereochemistry_warnings": item.stereochemistry_warnings,
                    "snapshot_hash": item.snapshot_hash, "created_at": item.created_at,
                })
            except (TypeError, ValueError, ValidationError):
                raise DataUnavailableError("原始结构快照不可验证") from None
            return {
                "id": view.id,
                "project_id": view.project_id,
                "canonical_smiles": view.canonical_smiles,
                "stereochemistry_status": view.stereochemistry_status,
            }

        return PredictionReplaySourceData(
            prediction_id=row.id, project_id=row.project_id,
            chromatography_type=row.chromatography_type, tool=row.tool,
            schema_version=row.schema_version, structure_refs=structure_refs,
            structure_snapshots=[public_structure(ref) for ref in structure_refs],
            modifier_structure_snapshots=[public_structure(ref) for ref in modifier_refs],
            input=request.input.model_dump(mode="json"),
            model_selection={
                "mode": "exact",
                "model_id": model.id,
                "model_version": model.version,
                "preprocessing_profile_id": model.preprocessing_profile_id,
                "preprocessing_profile_sha256": model.preprocessing_profile_sha256,
            },
            model_snapshot=model.model_dump(mode="json"),
        )

    def lookup(
        self,
        ctx: OperationContext,
        project_id: str,
        *,
        key: str,
        payload_hash: str,
    ) -> IdempotencyLookup | None:
        session = self._session()
        row = session.scalar(select(IdempotencyRecord).where(
            IdempotencyRecord.actor_id == ctx.actor.actor_id,
            IdempotencyRecord.project_id == project_id,
            IdempotencyRecord.method == "POST",
            IdempotencyRecord.route_identity == "/api/v1/predictions",
            IdempotencyRecord.key == key,
        ))
        if row is None or _expired(row) or row.status != "COMPLETED" or not row.result_resource_ref:
            return None
        return IdempotencyLookup(_as_accepted(row.result_resource_ref), row.payload_hash)

    def create(
        self,
        ctx: OperationContext,
        request: PredictionCreate,
        *,
        model_snapshot: ModelSnapshot,
        input_snapshot: PredictionExecutionInputSnapshot,
        source: str,
        idempotency_key: str,
        payload_hash: str,
        session: Session | None = None,
    ) -> AtomicPredictionCreateResult:
        db = self._session(session)
        conversation_id = getattr(ctx, "conversation_id", None)
        agent_run_id = getattr(ctx, "agent_run_id", None)
        if (source == "assistant" or agent_run_id is not None) and not conversation_id:
            raise APIError(422, "VALIDATION_FAILED", "助手预测必须绑定服务端 conversation_id")
        if conversation_id is not None:
            # Assistant supplied conversation/run scope is server-bound.  A
            # direct caller may omit it because the public prediction schema
            # allows a standalone prediction, but a supplied scope must point
            # to the same project and run; never trust a client field here.
            from chrom_platform.modules.assistant.persistence import AgentRunRow, ConversationRow

            conversation = db.scalar(select(ConversationRow).where(ConversationRow.id == conversation_id))
            if conversation is None or conversation.project_id != request.project_id:
                raise APIError(404, "RESOURCE_NOT_FOUND", "助手会话不存在或不属于当前项目")
            if agent_run_id is not None:
                run = db.scalar(select(AgentRunRow).where(AgentRunRow.id == agent_run_id))
                if run is None or run.project_id != request.project_id or run.conversation_id != conversation_id:
                    raise APIError(404, "RESOURCE_NOT_FOUND", "助手运行不存在或不属于当前会话项目")
        scope = self._scope(ctx, request.project_id, idempotency_key)
        idem = db.scalar(select(IdempotencyRecord).where(
            IdempotencyRecord.actor_id == scope.actor_id,
            IdempotencyRecord.project_id == scope.project_id,
            IdempotencyRecord.method == scope.method,
            IdempotencyRecord.route_identity == scope.route_identity,
            IdempotencyRecord.key == scope.key,
        ).with_for_update())
        created_idem = False
        if idem is None:
            idem = IdempotencyRecord(
                id=_id("idem"), actor_id=scope.actor_id, project_id=scope.project_id,
                method=scope.method, route_identity=scope.route_identity, key=scope.key,
                payload_hash=payload_hash, status="IN_PROGRESS",
                expires_at=_now() + timedelta(hours=24),
            )
            try:
                bind = getattr(db, "bind", None)
                is_sqlite = getattr(getattr(bind, "dialect", None), "name", None) == "sqlite"
                # SQLite starts its physical transaction at SAVEPOINT when a
                # read-only caller transaction has not written yet; for this
                # single-writer test/runtime dialect, a plain flush preserves
                # the caller transaction and avoids an implicit savepoint
                # commit. MySQL retains the concurrency-safe savepoint.
                scope_writer = db.begin_nested() if not is_sqlite else nullcontext()
                with scope_writer:
                    db.add(idem)
                    db.flush()
                created_idem = True
            except IntegrityError:
                idem = db.scalar(select(IdempotencyRecord).where(
                    IdempotencyRecord.actor_id == scope.actor_id,
                    IdempotencyRecord.project_id == scope.project_id,
                    IdempotencyRecord.method == scope.method,
                    IdempotencyRecord.route_identity == scope.route_identity,
                    IdempotencyRecord.key == scope.key,
                ).with_for_update())
                if idem is None:
                    raise PredictionPersistenceUnavailable("预测幂等并发写入未能确定") from None
        if _expired(idem):
            # M1 idempotency records are reusable after their retention window.
            # Reclaiming the row keeps the unique scope key and does not need a
            # second INSERT (or an outer-transaction rollback).
            idem.payload_hash = payload_hash
            idem.status = "IN_PROGRESS"
            idem.error_code = None
            idem.result_resource_ref = None
            idem.result_status_code = None
            idem.completed_at = None
            idem.expires_at = _now() + timedelta(hours=24)
            created_idem = True
        if idem.payload_hash != payload_hash:
            raise APIError(409, "IDEMPOTENCY_KEY_REUSED", "幂等键已用于其他请求")
        if idem.status == "IN_PROGRESS" and not created_idem:
            raise APIError(409, "OPERATION_IN_PROGRESS", "相同幂等请求正在处理中", retryable=True)
        if idem.status == "COMPLETED" and idem.result_resource_ref:
            return AtomicPredictionCreateResult(_as_accepted(idem.result_resource_ref), replayed=True)
        if idem.status == "FAILED":
            idem.status = "IN_PROGRESS"
            idem.error_code = None
            created_idem = True
        if idem.status != "IN_PROGRESS":
            raise APIError(409, "OPERATION_IN_PROGRESS", "相同幂等请求状态不可重建", retryable=True)

        prediction_id, record_id, job_id = _id("pred"), _id("record"), _id("job")
        created_at = _now()
        accepted = PredictionAccepted(
            id=prediction_id,
            prediction_record_id=record_id,
            job_id=job_id,
            status=JobStatus.QUEUED,
            resource_ref=ResourceRef(type="prediction", id=prediction_id),
        )
        model_json = model_snapshot.model_dump(mode="json")
        input_json = input_snapshot.model_dump(mode="json")
        request_json = request.model_dump(mode="json")
        record = PredictionRecordDraft(
            id=record_id, prediction_id=prediction_id, batch_id=None, project_id=request.project_id,
            actor_id=ctx.actor.actor_id, source=source,
            chromatography_type=request.chromatography_type,
            model_snapshot=model_json, input_snapshot=input_json,
            result_ref=None, warnings=[], status=JobStatus.QUEUED.value,
            created_at=created_at, artifact_refs=[], revision=1,
            etag=_hash({"id": record_id, "revision": 1, "status": JobStatus.QUEUED.value, "result_ref": None}),
        )
        prediction = PredictionRow(
            id=prediction_id, project_id=request.project_id, prediction_record_id=record_id,
            job_id=job_id, actor_id=ctx.actor.actor_id, chromatography_type=request.chromatography_type,
            tool=request.tool, schema_version=request.schema_version, status=JobStatus.QUEUED.value,
            model_snapshot=model_json, input_snapshot=input_json, request_snapshot=request_json,
            warnings=[], source=source, conversation_id=conversation_id, agent_run_id=agent_run_id,
            created_at=created_at, updated_at=created_at, data_origin="production", revision=1,
            etag=_etag(prediction_id=prediction_id, revision=1, status=JobStatus.QUEUED.value, result=None, error=None),
        )
        job = Job(
            id=job_id, project_id=request.project_id, owner_id=ctx.actor.actor_id, kind="prediction",
            resource_ref=accepted.resource_ref.model_dump(mode="json"), status=JobStatus.QUEUED.value,
            attempt=1, progress={"completed": 0, "total": 1, "unit": "prediction", "stage": "queued"},
            cancelable=True, retryable=False, resume_mode="none", created_at=created_at,
            updated_at=created_at, revision=1,
            etag="pending",
        )
        job.etag = _job_etag(job)
        attempt = JobAttempt(
            id=_id("attempt"), job_id=job_id, attempt=1, status=JobStatus.QUEUED.value,
            resume_mode="none",
        )
        dispatch_id = _id("dispatch")
        outbox = OutboxEvent(
            dispatch_id=dispatch_id, aggregate_type="job", aggregate_id=job_id,
            event_type="prediction.execute", payload={"job_id": job_id, "attempt": 1, "dispatch_id": dispatch_id},
            status="PENDING", attempts=0, available_at=created_at, created_at=created_at,
        )
        event = JobEvent(
            id=_id("event"), job_id=job_id, stream_id=job_id, seq=0,
            type="job.queued", schema_version="1.0",
            data={"status": JobStatus.QUEUED.value, "attempt": 1}, timestamp=created_at,
        )
        # MySQL enforces both sides of this legacy projection cycle.  Insert
        # the nullable record->prediction edge first, then the prediction row,
        # and close the edge in the same caller-owned transaction.
        self._record_writer().stage_prediction_record(replace(record, prediction_id=None), session=db)
        db.flush()
        db.add_all([prediction, job, attempt, event, outbox])
        db.flush()
        from chrom_platform.modules.data_center.persistence import PredictionRecordRow
        record_row = db.scalar(select(PredictionRecordRow).where(PredictionRecordRow.id == record_id).with_for_update())
        if record_row is None and isinstance(db, Session):
            raise RuntimeError("PREDICTION_RECORD_ROW_NOT_FOUND")
        if record_row is not None:
            record_row.prediction_id = prediction.id
            if record_row.prediction_id != prediction.id:
                raise RuntimeError("PREDICTION_RECORD_LINK_INVALID")
        # Assistant predictions carry the server-resolved conversation/run
        # scope into the audit projection.  The audit is staged in the same
        # caller-owned transaction as prediction, job, attempt and outbox;
        # no request/input payload is copied into it.
        db.add(AuditEvent(
            id=_id("audit"), project_id=request.project_id,
            actor_id=ctx.actor.actor_id, auth_method=ctx.actor.auth_method,
            interaction="agent_tool" if ctx.interaction in {"assistant", "agent"} else ctx.interaction if ctx.interaction in {"human", "agent_tool", "worker", "service"} else "service",
            request_id=ctx.request_id, action="prediction.create",
            resource_type="prediction", resource_id=prediction_id,
            resource_ref=accepted.resource_ref.model_dump(mode="json"),
            before_summary={}, after_summary={
                "status": JobStatus.QUEUED.value, "source": source,
                "conversation_id": conversation_id, "job_id": job_id,
            }, job_id=job_id, agent_run_id=agent_run_id,
            result="SUCCEEDED", timestamp=created_at,
        ))
        db.flush()
        idem.status = "COMPLETED"
        idem.result_resource_ref = accepted.model_dump(mode="json")
        idem.result_status_code = 202
        idem.completed_at = _now()
        db.flush()
        return AtomicPredictionCreateResult(accepted, replayed=False)

    def get(self, ctx: OperationContext, prediction_id: str, *, session: Session | None = None) -> Prediction:
        db = self._session(session)
        row = db.scalar(select(PredictionRow).where(PredictionRow.id == prediction_id))
        if row is None or not ctx.actor.can_access_project(row.project_id) or not ctx.actor.can("prediction:read") and "*" not in ctx.actor.permissions:
            raise APIError(404, "RESOURCE_NOT_FOUND", "预测不存在或当前主体不可见")
        return _view(row)

    def load_execution(self, job_id: str, *, session: Session | None = None) -> "PredictionExecutionSnapshot | None":
        if session is None and self.session_factory is not None:
            db = self.session_factory()
            try:
                return self.load_execution(job_id, session=db)
            finally:
                db.close()
        db = self._session(session)
        # Loading is deliberately lock-free.  The claim below is the only
        # short transaction allowed to acquire the execution row lock.
        row = db.scalar(select(PredictionRow).where(PredictionRow.job_id == job_id))
        if row is None:
            return None
        job = db.scalar(select(Job).where(Job.id == job_id))
        if job is None:
            return None
        attempt_row = db.scalar(select(JobAttempt).where(JobAttempt.job_id == job_id, JobAttempt.attempt == job.attempt))
        dispatch = db.scalar(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_type == "job",
                OutboxEvent.aggregate_id == job_id,
                OutboxEvent.event_type == "prediction.execute",
            ).order_by(OutboxEvent.created_at.desc()).limit(1)
        )
        payload = dispatch.payload if dispatch is not None else {}
        return PredictionExecutionSnapshot(
            prediction_id=row.id, project_id=row.project_id, job_id=row.job_id,
            attempt=int(job.attempt), status=JobStatus(job.status), type_id=row.chromatography_type,
            tool=row.tool, schema_version=row.schema_version,
            request=_request_from_row(row),
            model_snapshot=ModelSnapshot.model_validate(row.model_snapshot),
            input_snapshot=PredictionExecutionInputSnapshot.model_validate(row.input_snapshot),
            dispatch_id=payload.get("dispatch_id") if isinstance(payload, dict) else None,
            execution_token=attempt_row.execution_token if attempt_row is not None else None,
            fencing_token=attempt_row.fencing_token if attempt_row is not None else None,
            lease_until=attempt_row.lease_until if attempt_row is not None else None,
        )

    def mark_running(self, item: "PredictionExecutionSnapshot", *, expected_attempt: int | None = None, expected_dispatch_id: str | None = None, session: Session | None = None) -> bool:
        if session is None and self.session_factory is not None:
            db = self.session_factory()
            try:
                with db.begin():
                    return self.mark_running(item, expected_attempt=expected_attempt, expected_dispatch_id=expected_dispatch_id, session=db)
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()
        db = self._session(session)
        expected_attempt = item.attempt if expected_attempt is None else expected_attempt
        expected_dispatch_id = item.dispatch_id if expected_dispatch_id is None else expected_dispatch_id
        job = db.scalar(select(Job).where(Job.id == item.job_id).with_for_update())
        if job is None or int(job.attempt) != int(expected_attempt) or JobStatus(job.status) is not JobStatus.QUEUED:
            return False
        attempt = db.scalar(select(JobAttempt).where(JobAttempt.job_id == item.job_id, JobAttempt.attempt == item.attempt).with_for_update())
        if attempt is None or attempt.status != JobStatus.QUEUED.value:
            return False
        dispatch_row = db.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_type == "job", OutboxEvent.aggregate_id == item.job_id, OutboxEvent.event_type == "prediction.execute").order_by(OutboxEvent.created_at.desc()).limit(1).with_for_update())
        if expected_dispatch_id and (dispatch_row is None or dispatch_row.dispatch_id != expected_dispatch_id):
            return False
        if expected_dispatch_id and db.scalar(select(OutboxEvent).where(OutboxEvent.dispatch_id == expected_dispatch_id, OutboxEvent.aggregate_id == item.job_id, OutboxEvent.status.in_(("PENDING", "PROCESSING")))) is None:
            return False
        # Complete every stale-message/CAS check before mutating any row.  A
        # false claim must commit as a no-op when called through the short
        # worker transaction; otherwise an old dispatch could leave the job in
        # RUNNING even though no worker owns its lease.
        row = db.scalar(select(PredictionRow).where(PredictionRow.id == item.prediction_id).with_for_update())
        if row is None:
            return False
        now = _now()
        row.status = JobStatus.RUNNING.value
        row.updated_at = now
        row.revision += 1
        row.etag = _etag(prediction_id=row.id, revision=row.revision, status=row.status, result=row.result, error=row.error)
        job.status = JobStatus.RUNNING.value
        job.started_at = job.started_at or now
        job.updated_at = now
        job.revision += 1
        job.etag = _job_etag(job)
        if attempt is not None:
            attempt.execution_token = _id("exec")
            attempt.fencing_token = int(attempt.fencing_token or 0) + 1
            lease_until = now + timedelta(seconds=60)
            attempt.lease_until = lease_until
            try:
                object.__setattr__(item, "execution_token", attempt.execution_token)
                object.__setattr__(item, "fencing_token", attempt.fencing_token)
                object.__setattr__(item, "lease_until", lease_until)
            except Exception:
                pass
            attempt.status = JobStatus.RUNNING.value
            attempt.started_at = attempt.started_at or now
        if item.dispatch_id:
            dispatch_row = db.scalar(select(OutboxEvent).where(OutboxEvent.dispatch_id == item.dispatch_id).with_for_update())
            if dispatch_row is not None:
                dispatch_row.status = "PROCESSING"
        self._record_writer().update_prediction_record(
            row.prediction_record_id,
            status=JobStatus.RUNNING.value,
            session=db,
        )
        db.add(JobEvent(
            id=_id("event"), job_id=job.id, stream_id=job.id, seq=_next_event_seq(db, job.id),
            type="job.running", schema_version="1.0",
            data={"status": JobStatus.RUNNING.value, "attempt": item.attempt}, timestamp=now,
        ))
        db.flush()
        return True

    def mark_succeeded(self, item: "PredictionExecutionSnapshot", result: object, *, session: Session | None = None) -> None:
        self._finish(item, result=result, error=None, session=session)

    def mark_failed(self, item: "PredictionExecutionSnapshot", error: PublicErrorSummary, *, session: Session | None = None) -> None:
        self._finish(item, result=None, error=error.model_dump(mode="json"), session=session)

    def heartbeat(self, item: "PredictionExecutionSnapshot", *, session: Session | None = None) -> bool:
        """Extend a lease only for the current attempt/fencing token."""
        if session is None and self.session_factory is not None:
            db = self.session_factory()
            try:
                with db.begin():
                    return self.heartbeat(item, session=db)
            finally:
                db.close()
        db = self._session(session)
        attempt = db.scalar(select(JobAttempt).where(JobAttempt.job_id == item.job_id, JobAttempt.attempt == item.attempt).with_for_update())
        if attempt is None or attempt.status != JobStatus.RUNNING.value or attempt.fencing_token != item.fencing_token:
            return False
        if attempt.lease_until is not None and _aware_dt(attempt.lease_until) <= _now():
            return False
        attempt.lease_until = _now() + timedelta(seconds=60)
        db.flush()
        return True

    def recover_expired(self, job_id: str, *, session: Session | None = None) -> bool:
        """Mark an expired running attempt interrupted for coordinator recovery."""
        if session is None and self.session_factory is not None:
            db = self.session_factory()
            try:
                with db.begin():
                    return self.recover_expired(job_id, session=db)
            finally:
                db.close()
        db = self._session(session)
        job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if job is None or JobStatus(job.status) is not JobStatus.RUNNING:
            return False
        attempt = db.scalar(select(JobAttempt).where(JobAttempt.job_id == job_id, JobAttempt.attempt == job.attempt).with_for_update())
        if attempt is None or attempt.lease_until is None or _aware_dt(attempt.lease_until) > _now():
            return False
        job.status = JobStatus.INTERRUPTED.value
        attempt.status = JobStatus.INTERRUPTED.value
        attempt.lease_until = None
        db.flush()
        return True

    def _finish(self, item: "PredictionExecutionSnapshot", *, result: object | None, error: dict[str, Any] | None, session: Session | None) -> None:
        if session is None and self.session_factory is not None:
            db = self.session_factory()
            try:
                with db.begin():
                    self._finish(item, result=result, error=error, session=db)
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()
            return
        db = self._session(session)
        job = db.scalar(select(Job).where(Job.id == item.job_id).with_for_update())
        if job is None or int(job.attempt) != int(item.attempt) or JobStatus(job.status) is not JobStatus.RUNNING:
            return
        attempt = db.scalar(select(JobAttempt).where(JobAttempt.job_id == item.job_id, JobAttempt.attempt == item.attempt).with_for_update())
        if attempt is None or attempt.status != JobStatus.RUNNING.value:
            return
        if item.execution_token is None or attempt.execution_token != item.execution_token or item.fencing_token is None or attempt.fencing_token != item.fencing_token or (attempt.lease_until is not None and _aware_dt(attempt.lease_until) <= _now()):
            return
        row = db.scalar(select(PredictionRow).where(PredictionRow.id == item.prediction_id).with_for_update())
        if row is None:
            return
        status = JobStatus.SUCCEEDED.value if error is None else JobStatus.FAILED.value
        result_json = _json_result(result)
        now = _now()
        row.status, row.result, row.error, row.completed_at, row.updated_at = status, result_json, error, now, now
        row.revision += 1
        row.etag = _etag(prediction_id=row.id, revision=row.revision, status=status, result=result_json, error=error)
        job.status, job.result_ref, job.error, job.finished_at, job.updated_at = status, {"type": "prediction", "id": row.id} if error is None else None, error, now, now
        _finish_prediction_progress(job)
        job.revision += 1
        job.etag = _job_etag(job)
        attempt.status, attempt.error, attempt.result_ref, attempt.finished_at, attempt.lease_until = status, error, {"type": "prediction", "id": row.id} if error is None else None, now, None
        self._record_writer().update_prediction_record(
            row.prediction_record_id,
            status=status,
            result_ref={"type": "prediction", "id": row.id} if error is None else None,
            session=db,
        )
        db.add(JobEvent(
            id=_id("event"), job_id=job.id, stream_id=job.id, seq=_next_event_seq(db, job.id),
            type="job.succeeded" if error is None else "job.failed", schema_version="1.0",
            data={"status": status, "attempt": item.attempt, "error": error}, timestamp=now,
        ))
        db.flush()


@dataclass(frozen=True)
class PredictionExecutionSnapshot:
    prediction_id: str
    project_id: str
    job_id: str
    attempt: int
    status: JobStatus
    type_id: str
    tool: str
    schema_version: str
    request: PredictionExecutionRequest
    model_snapshot: ModelSnapshot
    input_snapshot: PredictionExecutionInputSnapshot
    dispatch_id: str | None = None
    execution_token: str | None = None
    fencing_token: int | None = None
    lease_until: datetime | None = None


def _request_from_row(row: PredictionRow) -> PredictionExecutionRequest:
    model = ModelSnapshot.model_validate(row.model_snapshot)
    inputs = PredictionExecutionInputSnapshot.model_validate(row.input_snapshot)
    return build_prediction_execution_request(
        row.request_snapshot,
        project_id=row.project_id,
        expected_type=row.chromatography_type,
        expected_tool=row.tool,
        expected_schema_version=row.schema_version,
        model_snapshot=model,
        input_snapshot=inputs,
    )


def _view(row: PredictionRow) -> Prediction:
    return Prediction.model_validate({
        "id": row.id, "project_id": row.project_id, "prediction_record_id": row.prediction_record_id,
        "job_id": row.job_id, "chromatography_type": row.chromatography_type, "tool": row.tool,
        "schema_version": row.schema_version, "status": JobStatus(row.status),
        "model": row.model_snapshot,
        "input_snapshot": to_public_prediction_input_snapshot(row.input_snapshot),
        "result": row.result,
        "warnings": row.warnings or [], "error": row.error, "source": row.source,
        "conversation_id": row.conversation_id, "agent_run_id": row.agent_run_id,
        "created_at": row.created_at, "completed_at": row.completed_at, "data_origin": row.data_origin,
        "revision": row.revision, "etag": row.etag, "updated_at": row.updated_at,
    })


__all__ = [
    "PredictionBase", "PredictionRow", "PredictionRecordRow", "PredictionExecutionSnapshot",
    "PredictionPersistenceUnavailable", "SQLAlchemyPredictionPersistence",
    "PredictionAtomicAdapter", "SQLAlchemyPredictionRepository",
]


def __getattr__(name: str):
    """Compatibility export; record ORM ownership remains in data-center."""

    if name == "PredictionRecordRow":
        from chrom_platform.modules.data_center.persistence import PredictionRecordRow

        return PredictionRecordRow
    raise AttributeError(name)

# Descriptive aliases used by assemblies that call the port an atomic adapter
# or repository.  They intentionally point to the same caller-owned adapter.
PredictionAtomicAdapter = SQLAlchemyPredictionPersistence
SQLAlchemyPredictionRepository = SQLAlchemyPredictionPersistence
