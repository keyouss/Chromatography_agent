"""M4 assistant composition and request/worker transaction binding."""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from types import SimpleNamespace
from typing import Any, Callable, Iterator, Mapping
from uuid import uuid4

from fastapi import APIRouter
from sqlalchemy import MetaData, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from chrom_platform.infrastructure.db import Base
from chrom_platform.infrastructure.models import ExecutionLedger
from chrom_platform.core.dto import ActorContext
from chrom_platform.artifact_core.repository import SQLAlchemyArtifactReader
from chrom_platform.artifact_core.service import ArtifactService
from chrom_platform.infrastructure.repositories import SQLAlchemyIdempotencyStore
from chrom_platform.infrastructure.structure_uow import SQLAlchemyStructureUnitOfWork
from chrom_platform.modules.model_center.persistence import SQLAlchemyModelCenterRepository
from chrom_platform.modules.model_center.service import ModelCenterService
from chrom_platform.modules.model_center.schemas import AuditEvent, new_id as model_new_id, now_utc as model_now_utc
from chrom_platform.modules.data_center.persistence import (
    SQLAlchemyDataRepository,
    SQLAlchemyPredictionRecordRepository,
    SQLAlchemyPredictionRecordWriter,
)
from chrom_platform.modules.data_center.service import DataVersionService, PredictionRecordService
from chrom_platform.modules.prediction.persistence import SQLAlchemyPredictionPersistence
from chrom_platform.modules.prediction.service import PredictionCreateService
from chrom_platform.assembly.prediction_structure import SQLAlchemyPredictionStructurePort
from chrom_platform.task_core.repository import SQLAlchemyTaskRepository
from chrom_platform.task_core.service import ConfirmationService
from chrom_platform.modules.assistant.adapter import PydanticAIAdapter
from chrom_platform.modules.assistant.events import MySQLAgentEventStore
from chrom_platform.modules.assistant.persistence import (
    AgentEventRow,
    AgentAttemptRow,
    AgentRunRow,
    AssistantBase,
    SQLAlchemyAssistantRepository,
)
from chrom_platform.modules.assistant.router import create_router as create_assistant_router
from chrom_platform.modules.assistant.secret_store import default_secret_store
from chrom_platform.modules.assistant.settings_router import create_router as create_settings_router
from chrom_platform.modules.assistant.runtime import AgentRuntime, RuntimeConfigSnapshot
from chrom_platform.modules.assistant.service import AssistantService
from chrom_platform.modules.assistant.trace import ContextInspector
from chrom_platform.modules.assistant.tools import ToolExecution, ToolExecutionLedger
from chrom_platform.modules.assistant.priority import priority_toolset, resolve_runtime_config
from chrom_platform.modules.assistant.worker import AgentWorker, AgentWorkerMessage
from chrom_platform.modules.assistant.errors import AssistantError, unavailable
from chrom_platform.core.dto import ResourceRef
from chrom_platform.modules.assistant.priority import PriorityAssistantService, PriorityAssistantToolService, PriorityOperationContext
from chrom_platform.structure_core.router import _normalize_in_transaction
from chrom_platform.structure_core.schemas import NormalizeStructureRequest
from chrom_platform.structure_core.service import StructureService
from chrom_platform.core.idempotency import IdempotencyScope


class _BoundTraceStore:
    """Adapt the session-first repository port to TraceStore."""

    def __init__(self, repository: SQLAlchemyAssistantRepository, session: Session) -> None:
        self.repository, self.session = repository, session

    def save_snapshot(self, snapshot) -> None:
        self.repository.save_context_snapshot(self.session, snapshot)

    def get_snapshot(self, snapshot_id):
        return self.repository.get_context_snapshot(snapshot_id, session=self.session)

    def save_llm_call(self, call) -> None:
        _save_trace_call(self.repository, self.session, call)

    def get_llm_call(self, call_id):
        return self.repository.get_llm_call(call_id, session=self.session)

    def list_llm_calls(self, *, run_id: str, limit: int = 50, cursor: str | None = None):
        return self.repository.list_llm_calls(self.session, run_id=run_id, limit=limit, cursor=cursor)

    def save_tool_call(self, call, *, execution_token=None, fencing_token=None) -> None:
        _save_trace_tool_call(self.repository, self.session, call, execution_token=execution_token, fencing_token=fencing_token)

    def get_tool_call(self, call_id: str):
        return self.repository.get_tool_call(self.session, call_id)

    def list_tool_calls(self, *, run_id: str, limit: int = 50):
        return self.repository.list_tool_calls(self.session, run_id=run_id, limit=limit)

    def record_tool_call_event(self, call, event_type, data, *, execution_token=None, fencing_token=None) -> None:
        nested = getattr(self.session, "begin_nested", None)
        if callable(nested):
            with nested():
                _save_trace_tool_call_event(self.repository, self.session, call, event_type, data, execution_token=execution_token, fencing_token=fencing_token)
        else:
            _save_trace_tool_call_event(self.repository, self.session, call, event_type, data, execution_token=execution_token, fencing_token=fencing_token)


def _save_trace_call(repository: SQLAlchemyAssistantRepository, session: Session, call):
    row = repository.save_llm_call(session, call)
    usage = getattr(call, "usage", None)
    if usage is not None:
        if isinstance(usage, Mapping):
            public_usage = {
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
                "cached_input_tokens": usage.get("cached_input_tokens"),
            }
        else:
            public_usage = {
                "input_tokens": getattr(usage, "input_tokens", None),
                "output_tokens": getattr(usage, "output_tokens", None),
                "cached_input_tokens": getattr(usage, "cached_input_tokens", None),
            }
        row.usage = public_usage
        repository._flush(session)
    return row


def _save_trace_tool_call(repository: SQLAlchemyAssistantRepository, session: Session, call, *, execution_token=None, fencing_token=None):
    """Persist runtime tool lifecycle with the same run/attempt fence."""
    attempt_row = repository._one(session, select(AgentAttemptRow).where(AgentAttemptRow.id == call.agent_attempt_id), for_update=True)
    run_row = repository._row(session, AgentRunRow, call.agent_run_id, for_update=True)
    if attempt_row is None or run_row is None or attempt_row.agent_run_id != call.agent_run_id:
        raise AssistantError(409, "STALE_FENCING_TOKEN", "工具调用租约已失效", retryable=True)
    expected_execution = getattr(attempt_row, "execution_token", None)
    expected_fence = getattr(attempt_row, "fencing_token", None)
    run_fence = getattr(run_row, "fencing_token", None)
    if expected_execution is not None and execution_token != expected_execution:
        raise AssistantError(409, "STALE_FENCING_TOKEN", "工具调用租约已失效", retryable=True)
    if expected_fence is not None and (fencing_token is None or int(fencing_token) != int(expected_fence)):
        raise AssistantError(409, "STALE_FENCING_TOKEN", "工具调用租约已失效", retryable=True)
    if run_fence is not None and int(run_fence or 0) > 0 and (fencing_token is None or int(fencing_token) != int(run_fence)):
        raise AssistantError(409, "STALE_FENCING_TOKEN", "工具调用租约已失效", retryable=True)
    existing = repository.get_tool_call(session, call.id)
    if existing is not None:
        if existing.agent_run_id != call.agent_run_id or existing.agent_attempt_id != call.agent_attempt_id:
            raise AssistantError(409, "FENCE_REJECTED", "工具调用不属于当前 Run Attempt")
        terminal = {"SUCCEEDED", "FAILED", "WAITING_CONFIRMATION", "REJECTED", "INTERRUPTED"}
        if existing.status in terminal:
            if call.status == existing.status:
                return existing
            if call.status in terminal:
                raise AssistantError(409, "STATE_CONFLICT", "工具调用终态已确定", retryable=False)
            return existing
    return repository.save_tool_call(session, call)


def _save_trace_tool_call_event(repository: SQLAlchemyAssistantRepository, session: Session, call, event_type: str, data: Mapping[str, Any], *, execution_token=None, fencing_token=None):
    """Atomically persist one ToolCall transition and its public event."""
    _save_trace_tool_call(repository, session, call, execution_token=execution_token, fencing_token=fencing_token)
    event_data = dict(data)
    event_data.setdefault("tool_call_id", call.id)
    event_data.setdefault("status", call.status)
    event_data.setdefault("attempt", int(repository._one(session, select(AgentRunRow).where(AgentRunRow.id == call.agent_run_id)).attempt))
    event_rows = session.scalars(select(AgentEventRow).where(AgentEventRow.stream_type == "agent_run", AgentEventRow.stream_id == call.agent_run_id)).all()
    event_key = (str(event_data.get("tool_call_id")), str(event_data.get("status")), int(event_data.get("attempt", 0) or 0))
    for row in event_rows:
        existing = row.data or {}
        if row.type in {"tool.proposed", "tool.started", "tool.completed", "tool.failed", "confirmation.required"} and (str(existing.get("tool_call_id")), str(existing.get("status")), int(existing.get("attempt", 0) or 0)) == event_key:
            return
    MySQLAgentEventStore(session, AgentEventRow).append("agent_run", call.agent_run_id, event_type, event_data, agent_run_id=call.agent_run_id)


class _SQLExecutionLedger:
    """Durable execution fence backed by the platform execution_ledger table."""

    def __init__(self, session_factory: Callable[[], Session] | None, session: Session | None = None) -> None:
        self.session_factory, self.session = session_factory, session

    @contextmanager
    def _transaction(self) -> Iterator[Session]:
        if self.session_factory is None:
            if self.session is None:
                raise RuntimeError("execution ledger requires a session")
            yield self.session
            return
        db = self.session_factory()
        try:
            with db.begin():
                yield db
        finally:
            db.close()

    @staticmethod
    def operation_key(*, tool_name: str, normalized_args: Mapping[str, Any], target_revision: Any, run_id: str | None = None) -> str:
        return ToolExecutionLedger.operation_key(tool_name=tool_name, normalized_args=normalized_args, target_revision=target_revision, run_id=run_id)

    @staticmethod
    def _value(row: ExecutionLedger) -> ToolExecution:
        return ToolExecution(str(row.operation_type), str(row.operation_key), row.result_ref, False, str(row.status))

    def get(self, operation_key: str) -> ToolExecution | None:
        with self._transaction() as db:
            row = db.scalar(select(ExecutionLedger).where(ExecutionLedger.operation_key == operation_key))
            return self._value(row) if row is not None else None

    def execute(self, *, tool_name: str, normalized_args: Mapping[str, Any], target_revision: Any, fn, run_id: str | None = None) -> ToolExecution:
        key = self.operation_key(tool_name=tool_name, normalized_args=normalized_args, target_revision=target_revision, run_id=run_id)
        with self._transaction() as db:
            row = db.scalar(select(ExecutionLedger).where(ExecutionLedger.operation_key == key).with_for_update())
            if row is None:
                row = ExecutionLedger(id=f"exec_{uuid4().hex}", operation_key=key, operation_type=tool_name, status="STARTED", started_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
                db.add(row)
                db.flush()
            elif row.status == "SUCCEEDED":
                return self._value(row)
            elif row.status == "UNKNOWN":
                raise AssistantError(503, "SIDE_EFFECT_UNKNOWN", "业务动作结果未知，禁止自动重投", retryable=False)
            elif row.status == "FAILED":
                raise AssistantError(503, "BUSINESS_SERVICE_FAILED", "业务动作已有失败记录，禁止隐式重投", retryable=False)
            elif row.status == "STARTED":
                raise AssistantError(409, "OPERATION_IN_PROGRESS", "相同业务动作正在执行，禁止并发重投", retryable=True)
        try:
            result = fn()
        except BaseException as exc:
            with self._transaction() as db:
                row = db.scalar(select(ExecutionLedger).where(ExecutionLedger.operation_key == key).with_for_update())
                if row is not None:
                    row.status = "UNKNOWN"
                    row.error = {"code": "SIDE_EFFECT_UNKNOWN", "message": str(exc)[:500]}
                    row.updated_at = datetime.now(timezone.utc)
                    row.finished_at = datetime.now(timezone.utc)
            raise
        with self._transaction() as db:
            row = db.scalar(select(ExecutionLedger).where(ExecutionLedger.operation_key == key).with_for_update())
            if row is not None:
                row.status = "SUCCEEDED"
                row.result_ref = result if isinstance(result, dict) else {"value": str(result)}
                row.updated_at = datetime.now(timezone.utc)
                row.finished_at = datetime.now(timezone.utc)
        return ToolExecution(tool_name, key, result, False, "SUCCEEDED")


class _WorkerHistory:
    def __init__(self, repository: SQLAlchemyAssistantRepository) -> None:
        self.repository = repository

    def load_history(self, run_id: str, *, attempt: int = 1) -> list[dict[str, Any]]:
        with self.repository.transaction() as db:
            messages = self.repository.list_messages(db, self.repository.get_run(db, run_id).conversation_id, limit=200)
        result: list[dict[str, Any]] = []
        for message in messages:
            text_parts: list[str] = []
            for block in message.blocks:
                if isinstance(block, Mapping):
                    block_type, block_text = block.get("type"), block.get("text")
                else:
                    block_type, block_text = getattr(block, "type", None), getattr(block, "text", None)
                if block_type == "text" and isinstance(block_text, str):
                    text_parts.append(block_text)
            text = "\n".join(text_parts)
            if text:
                result.append({"role": message.role, "content": text})
        return result


class _FreshEventStore:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self.session_factory = session_factory

    def append(self, *args, **kwargs):
        db = self.session_factory()
        try:
            with db.begin():
                return MySQLAgentEventStore(db, AgentEventRow).append(*args, **kwargs)
        finally:
            db.close()


class _FreshTraceStore:
    """TraceStore adapter that gives each worker write its own short UOW."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self.session_factory = session_factory

    def _call(self, callback):
        db = self.session_factory()
        try:
            with db.begin():
                repository = SQLAlchemyAssistantRepository(db)
                return callback(repository, db)
        finally:
            db.close()

    def save_snapshot(self, snapshot) -> None:
        self._call(lambda repository, db: repository.save_context_snapshot(db, snapshot))

    def get_snapshot(self, snapshot_id):
        return self._call(lambda repository, db: repository.get_context_snapshot(snapshot_id, session=db))

    def save_llm_call(self, call) -> None:
        self._call(lambda repository, db: _save_trace_call(repository, db, call))

    def get_llm_call(self, call_id):
        return self._call(lambda repository, db: repository.get_llm_call(call_id, session=db))

    def list_llm_calls(self, *, run_id: str, limit: int = 50, cursor: str | None = None):
        return self._call(lambda repository, db: repository.list_llm_calls(db, run_id=run_id, limit=limit, cursor=cursor))

    def save_tool_call(self, call, *, execution_token=None, fencing_token=None) -> None:
        self._call(lambda repository, db: _save_trace_tool_call(repository, db, call, execution_token=execution_token, fencing_token=fencing_token))

    def get_tool_call(self, call_id: str):
        return self._call(lambda repository, db: repository.get_tool_call(db, call_id))

    def list_tool_calls(self, *, run_id: str, limit: int = 50):
        return self._call(lambda repository, db: repository.list_tool_calls(db, run_id=run_id, limit=limit))

    def record_tool_call_event(self, call, event_type, data, *, execution_token=None, fencing_token=None) -> None:
        self._call(lambda repository, db: _save_trace_tool_call_event(repository, db, call, event_type, data, execution_token=execution_token, fencing_token=fencing_token))

    def resolve_project_id(self, call_id: str) -> str | None:
        call = self.get_llm_call(call_id)
        return getattr(call, "project_id", None) if call is not None else None


class _WorkerModelCenterPort:
    """Fresh-session model-center port used by one assistant tool call."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self.session_factory = session_factory

    def _call(self, method: str, *args, **kwargs):
        db = self.session_factory()
        try:
            with db.begin():
                service = ModelCenterService(SQLAlchemyModelCenterRepository(db))
                return getattr(service, method)(*args, **kwargs)
        finally:
            db.close()

    def create_registration(self, request, *, idempotency_key=None, actor=None):
        return self._call("create_registration", request, idempotency_key=idempotency_key, actor=actor)

    def get_registration_replay(self, request, *, idempotency_key=None, actor=None):
        if not idempotency_key:
            return None
        from chrom_platform.modules.model_center.service import _idempotency_scope, config_hash
        db = self.session_factory()
        try:
            with db.begin():
                repo = SQLAlchemyModelCenterRepository(db)
                return repo.idempotent_get(
                    _idempotency_scope(request.project_id, actor, "POST", "/models/registrations", idempotency_key),
                    config_hash(request.model_dump(mode="json")),
                )
        finally:
            db.close()

    def get_registration(self, project_id, registration_id):
        return self._call("get_registration", project_id, registration_id)

    def list_models(self, project_id, *, task_type=None, lifecycle=None, scope_id=None):
        return self._call("list_models", project_id, task_type=task_type, lifecycle=lifecycle, scope_id=scope_id)

    def get_model(self, project_id, model_id):
        return self._call("get_model", project_id, model_id)


class _WorkerAuditPort:
    """Persist assistant audit events without retaining a worker Session."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self.session_factory = session_factory

    def record(self, *, actor, interaction, action, project_id, resource_id, result, confirmation_id=None, after=None, before=None):
        db = self.session_factory()
        try:
            with db.begin():
                SQLAlchemyModelCenterRepository(db).record_audit(AuditEvent(
                    id=model_new_id("audit"), project_id=project_id, actor_id=actor.actor_id,
                    interaction=interaction, action=action,
                    resource_ref=ResourceRef(type="model_registration", id=resource_id),
                    result=result, confirmation_id=confirmation_id,
                    after_summary=dict(after or {}), before_summary=dict(before or {}),
                    timestamp=model_now_utc(),
                ))
        finally:
            db.close()


class _WorkerPredictionPort:
    """Fresh-session adapter over the normal M2 prediction service."""

    def __init__(self, session_factory: Callable[[], Session], registry: object) -> None:
        self.session_factory, self.registry = session_factory, registry

    def capabilities(self):
        return self.registry.capabilities()

    def _service(self, session: Session) -> PredictionCreateService:
        persistence = SQLAlchemyPredictionPersistence(session, record_writer=SQLAlchemyPredictionRecordWriter())
        return PredictionCreateService(
            access=persistence,
            structures=SQLAlchemyPredictionStructurePort(session),
            capabilities=self.registry,
            models=self.registry,
            idempotency=persistence,
            atomic=persistence,
        )

    def create(self, ctx, request, *, idempotency_key):
        session = self.session_factory()
        try:
            with session.begin():
                return self._service(session).create(ctx, request, idempotency_key=idempotency_key)
        finally:
            session.close()

    def get(self, ctx, prediction_id):
        session = self.session_factory()
        try:
            try:
                return SQLAlchemyPredictionPersistence(session).get(ctx, prediction_id)
            except SQLAlchemyError as exc:
                raise unavailable("预测读取服务未配置") from exc
        finally:
            session.close()


class _WorkerDataVersionPort:
    """Fresh-session adapter over the formal M2 data-version service."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self.session_factory = session_factory

    def list(self, project_id: str, domain_id: str):
        session = self.session_factory()
        try:
            try:
                return DataVersionService(SQLAlchemyDataRepository(session)).list(
                    project_id, domain_id, limit=200
                )
            except SQLAlchemyError as exc:
                raise unavailable("数据版本读取服务未配置") from exc
        finally:
            session.close()


class _WorkerPredictionRecordPort:
    """Fresh-session adapter over the formal M2 prediction-record service."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self.session_factory = session_factory

    def list(self, project_id: str, *, chromatography_type: str | None = None, limit: int = 200):
        session = self.session_factory()
        try:
            try:
                return PredictionRecordService(
                    SQLAlchemyPredictionRecordRepository(session)
                ).list(project_id, chromatography_type=chromatography_type, limit=limit)
            except SQLAlchemyError as exc:
                raise unavailable("预测记录读取服务未配置") from exc
        finally:
            session.close()


class _WorkerStructurePort:
    """Fresh-session adapter over the formal persisted structure service."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self.session_factory = session_factory

    def normalize(self, ctx, request: NormalizeStructureRequest, *, idempotency_key: str):
        actor = getattr(ctx, "actor", None)
        actor_id = getattr(actor, "actor_id", None)
        project_id = request.project_id or getattr(ctx, "server_project_id", None)
        if not actor_id or not project_id:
            raise unavailable("结构规范化缺少服务端访问上下文")
        if not isinstance(idempotency_key, str) or not idempotency_key or len(idempotency_key) > 128:
            raise APIError(422, "VALIDATION_FAILED", "结构规范化幂等键无效")
        payload = request.model_copy(update={"project_id": project_id})
        payload_hash = hashlib.sha256(
            json.dumps(
                {"path": "/structures/normalize", "payload": payload.model_dump(mode="json")},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        scope = IdempotencyScope(
            actor_id=actor_id,
            project_id=project_id,
            method="POST",
            route_identity="/api/v1/structures/normalize",
            key=idempotency_key,
        )
        structure_id = "str_" + hashlib.sha256(
            f"{actor_id}|{project_id}|{idempotency_key}|{payload_hash}".encode("utf-8")
        ).hexdigest()[:32]
        request_id = f"worker:{getattr(ctx, 'agent_run_id', None) or 'assistant'}:{getattr(ctx, 'agent_attempt_id', None) or '1'}"
        return _normalize_in_transaction(
            service=StructureService(None),
            uow=SQLAlchemyStructureUnitOfWork(self.session_factory),
            payload=payload,
            scope=scope,
            payload_hash=payload_hash,
            structure_id=structure_id,
            request_id=request_id[:96],
            agent_run_id=getattr(ctx, "agent_run_id", None),
            attempt=getattr(ctx, "tool_call_attempt", None),
        )[0]


def _worker_priority_service(
    session_factory: Callable[[], Session], *, model_center_enabled: bool,
    prediction_registry: object | None = None,
    build_prediction_registry_if_missing: bool = True,
) -> PriorityAssistantService | None:
    """Build formal per-operation ports over the boot-owned prediction registry."""
    if prediction_registry is None and build_prediction_registry_if_missing:
        try:
            from chrom_platform.assembly.prediction_worker_registry import build_prediction_worker_registry

            prediction_registry = build_prediction_worker_registry(session_factory=session_factory)
        except Exception:
            prediction_registry = None
    prediction = _WorkerPredictionPort(session_factory, prediction_registry) if prediction_registry is not None else None
    if not model_center_enabled and prediction is None:
        return None
    try:
        artifacts = ArtifactService(SQLAlchemyArtifactReader(session_factory), None)
        confirmation = ConfirmationService(SQLAlchemyTaskRepository(session_factory))
        models = _WorkerModelCenterPort(session_factory)
        audit = _WorkerAuditPort(session_factory)
        return PriorityAssistantService(
            prediction=prediction,
            capabilities=prediction,
            structures=_WorkerStructurePort(session_factory),
            data_versions=_WorkerDataVersionPort(session_factory),
            records=_WorkerPredictionRecordPort(session_factory),
            models=models,
            artifacts=artifacts,
            confirmation=confirmation,
            audit=audit,
        )
    except Exception:
        # Assembly must remain loadable when an optional module or storage
        # table is absent; the bound tool will fail closed at runtime.
        return None

class _ProjectAwareWorkerRuntime:
    """Pass the persisted Run project scope into the shared runtime."""

    def __init__(self, runtime: AgentRuntime, repository: SQLAlchemyAssistantRepository, actor_context: ActorContext | None = None) -> None:
        self.runtime, self.repository, self.actor_context = runtime, repository, actor_context

    @property
    def provider(self):
        return self.runtime.provider

    @provider.setter
    def provider(self, value) -> None:
        self.runtime.provider = value

    def run(self, *, run_id: str, attempt: int = 1, **kwargs):
        execution_token = kwargs.pop("execution_token", None)
        fencing_token = kwargs.pop("fencing_token", None)
        with self.repository.transaction() as db:
            run = self.repository.get_run(db, run_id)
            conversation = self.repository.get_conversation(db, run.conversation_id) if run is not None else None
            messages = self.repository.list_messages(db, run.conversation_id, limit=200) if run is not None else []
            attempt_row = self.repository._one(db, select(AgentAttemptRow).where(AgentAttemptRow.agent_run_id == run_id, AgentAttemptRow.attempt == attempt)) if run is not None else None
            persisted_attempt_id = getattr(attempt_row, "id", None)
            persisted_execution_token = getattr(attempt_row, "execution_token", None)
            persisted_fencing_token = getattr(run, "fencing_token", None)
        project_id = getattr(run, "project_id", None)
        if not project_id or conversation is None:
            raise AssistantError(503, "TRACE_SCOPE_UNAVAILABLE", "Agent Run 缺少项目范围", retryable=True)
        if self.actor_context is not None and (conversation.owner_id != self.actor_context.actor_id or not self.actor_context.can_access_project(project_id)):
            raise AssistantError(404, "RESOURCE_NOT_FOUND", "Agent Run 不属于当前服务端主体", retryable=False)
        user_messages = [item for item in messages if getattr(item, "role", None) == "user"]
        attachments = list(getattr(user_messages[-1], "attachment_ids", []) if user_messages else [])
        artifact_id = attachments[0] if len(attachments) == 1 else None
        actor = self.actor_context.model_copy(update={"project_ids": [project_id], "default_project_id": project_id}) if self.actor_context is not None else None
        pending = next(iter(getattr(run, "pending_actions", []) or []), None)
        agent_attempt_id = persisted_attempt_id
        kwargs.pop("ctx", None)
        if actor is None:
            context = SimpleNamespace(
                server_project_id=project_id, server_artifact_id=artifact_id,
                project_id=project_id, agent_run_id=run_id, agent_attempt_id=agent_attempt_id,
                execution_token=execution_token or persisted_execution_token, fencing_token=fencing_token if fencing_token is not None else persisted_fencing_token,
            )
        else:
            context = PriorityOperationContext(
                actor=actor, interaction="assistant", request_id=f"worker:{run_id}",
                conversation_id=conversation.id, agent_run_id=run_id,
                server_project_id=project_id, server_artifact_id=artifact_id,
                idempotency_key=f"assistant-run:{run_id}:{attempt}",
                confirmation_id=getattr(pending, "confirmation_id", None) if pending is not None else None,
                agent_attempt_id=agent_attempt_id,
                execution_token=execution_token or persisted_execution_token, fencing_token=fencing_token if fencing_token is not None else persisted_fencing_token,
            )
        return self.runtime.run(run_id=run_id, attempt=attempt, ctx=context, **kwargs)


def agent_worker_message_from_job(job: object, payload: Mapping[str, Any]) -> AgentWorkerMessage:
    """Convert a dispatcher-validated platform Job delivery to Agent input.

    The dispatcher owns outbox leasing and completion.  This adapter only
    validates the trusted Job identity and resolves the run from its persisted
    ``resource_ref``; it never claims or mutates an OutboxEvent.
    """
    if not isinstance(payload, Mapping) or set(payload) != {"job_id", "attempt", "dispatch_id"}:
        raise ValueError("job.execute payload must contain exactly job_id, attempt and dispatch_id")
    job_id = getattr(job, "id", None)
    if isinstance(job, Mapping):
        job_id = job.get("id", job_id)
    if type(job_id) is not str or payload["job_id"] != job_id:
        raise ValueError("job.execute payload job_id does not match Job")
    kind = getattr(job, "kind", None)
    resource_ref = getattr(job, "resource_ref", None)
    if isinstance(job, Mapping):
        kind = job.get("kind", kind)
        resource_ref = job.get("resource_ref", resource_ref)
    if kind != "assistant":
        raise ValueError("Job kind is not assistant")
    ref = ResourceRef.model_validate(resource_ref)
    if ref.type != "agent_run":
        raise ValueError("assistant Job resource_ref must target agent_run")
    attempt = getattr(job, "attempt", None)
    if isinstance(job, Mapping):
        attempt = job.get("attempt", attempt)
    if type(attempt) is not int or payload["attempt"] != attempt:
        raise ValueError("job.execute payload attempt does not match Job")
    return AgentWorkerMessage(run_id=ref.id, attempt=attempt, dispatch_id=payload["dispatch_id"])


class AssistantWorkerEntrypoint:
    """Assistant handler port called by the platform Job dispatcher."""

    def __init__(self, worker: AgentWorker, *, job_loader: Callable[[str], object | None] | None = None) -> None:
        self.worker, self.job_loader = worker, job_loader

    def handle_job(self, job: object, payload: Mapping[str, Any]):
        return self.worker.handle(agent_worker_message_from_job(job, payload))

    def handle(self, message: Mapping[str, Any] | object):
        """Handle a stable worker message after loading its trusted Job row."""
        if isinstance(message, Mapping):
            payload = dict(message)
        else:
            as_dict = getattr(message, "as_dict", None)
            if not callable(as_dict):
                raise ValueError("platform worker message must expose as_dict")
            payload = dict(as_dict())
        if set(payload) != {"job_id", "attempt", "dispatch_id"}:
            raise ValueError("platform worker message must contain exactly job_id, attempt and dispatch_id")
        if self.job_loader is None:
            raise RuntimeError("platform Job loader is not configured")
        job = self.job_loader(payload["job_id"])
        if job is None:
            raise ValueError("platform Job does not exist")
        return self.handle_job(job, payload)


def _copy_metadata(*registries: MetaData) -> MetaData:
    result = MetaData(naming_convention=Base.metadata.naming_convention)
    seen: set[str] = set()
    for registry in registries:
        for name, table in registry.tables.items():
            if name in seen:
                raise ValueError(f"duplicate table ownership: {name}")
            table.to_metadata(result)
            seen.add(name)
    return result


def m4_metadata(
    *,
    m3_modules: set[str] | frozenset[str] | None = None,
    include_m2: bool = True,
) -> MetaData:
    """Compose M1, selected M2/M3 registries and AssistantBase metadata."""

    selected_m3 = set(m3_modules or ())
    if selected_m3:
        # Lazy import keeps an assistant-only trimmed profile free of M3 source.
        from chrom_platform.assembly.m3 import m3_metadata

        base = m3_metadata(selected_m3, include_m2=include_m2)
    elif include_m2:
        try:
            from chrom_platform.infrastructure.metadata import m2_metadata

            base = m2_metadata()
        except ModuleNotFoundError:
            base = Base.metadata
    else:
        base = Base.metadata
    return _copy_metadata(base, AssistantBase.metadata)


@dataclass(frozen=True)
class M4Assembly:
    """Factories only; no Session is retained between requests."""

    modules: frozenset[str] = frozenset({"assistant"})
    features: frozenset[str] = frozenset({"knowledge", "business", "trace"})
    m3_modules: frozenset[str] = frozenset()
    include_m2: bool = True
    session_factory: Callable[[], Session] | None = None
    confirmation_service: object | None = None
    router_factory: Callable[..., APIRouter] = create_assistant_router
    router: APIRouter | None = None
    transport_retries: int = 2

    @property
    def metadata(self) -> MetaData:
        return m4_metadata(m3_modules=self.m3_modules, include_m2=self.include_m2)


@dataclass(frozen=True)
class M4RequestPorts:
    """Ports bound to a caller-owned request or worker Session."""

    session: Session
    repository: SQLAlchemyAssistantRepository
    service: AssistantService
    event_store: MySQLAgentEventStore
    trace_inspector: ContextInspector | None = None
    idempotency_store: SQLAlchemyIdempotencyStore | None = None
    execution_ledger: _SQLExecutionLedger | None = None
    worker: AssistantWorkerEntrypoint | None = None


def assemble_m4(
    *,
    modules: set[str] | frozenset[str] | None = None,
    assistant_features: set[str] | frozenset[str] | None = None,
    m3_modules: set[str] | frozenset[str] | None = None,
    include_m2: bool = True,
    session_factory: Callable[[], Session] | None = None,
    confirmation_service: object | None = None,
    transport_retries: int = 2,
) -> M4Assembly:
    try:
        configured_retries = min(max(int(transport_retries), 0), 5)
    except (TypeError, ValueError) as exc:
        raise ValueError("transport_retries must be an integer between 0 and 5") from exc
    selected_modules = frozenset(modules or {"assistant"})
    if "assistant" not in selected_modules:
        return M4Assembly(modules=selected_modules, features=frozenset(), m3_modules=frozenset(m3_modules or ()), include_m2=include_m2, session_factory=session_factory, confirmation_service=confirmation_service, router=None, transport_retries=configured_retries)
    features = frozenset(assistant_features or {"knowledge", "business", "trace"})
    router = create_assistant_router(trace_enabled="trace" in features)
    router.include_router(create_settings_router(secret_store=default_secret_store()))
    if confirmation_service is not None and session_factory is not None:
        from chrom_platform.modules.assistant.service import AssistantService
        from chrom_platform.task_core.confirmation_target_resolver import register_confirmation_target_resolver
        from chrom_platform.task_core.service import OperationContext, TaskError

        def resolve_conversation_archive(ctx, resource_ref, action):
            if action not in {"resource.archive", "conversation.delete"} or resource_ref.type != "conversation":
                raise TaskError(400, "VALIDATION_FAILED", "会话仅支持归档或删除确认")
            repository = SQLAlchemyAssistantRepository(session_factory=session_factory)
            with repository.transaction() as db:
                conversation = repository.get_conversation(db, resource_ref.id)
                busy = repository.conversation_delete_busy(db, resource_ref.id) if action == "conversation.delete" and conversation is not None else False
            if conversation is None or conversation.owner_id != ctx.actor.actor_id or not ctx.actor.can_access_project(conversation.project_id):
                raise TaskError(404, "RESOURCE_NOT_FOUND", "会话不存在或当前主体不可见")
            if not ctx.actor.can("assistant:use"):
                raise TaskError(403, "PERMISSION_DENIED", "当前主体无权使用助手")
            operation = OperationContext(
                actor=ctx.actor, interaction=ctx.interaction, request_id=ctx.request_id,
                conversation_id=conversation.id,
            )
            if action == "conversation.delete":
                return AssistantService.conversation_delete_target(operation, conversation, busy=busy)
            return AssistantService.conversation_archive_target(operation, conversation)

        register_confirmation_target_resolver(confirmation_service, "conversation", resolve_conversation_archive)
    return M4Assembly(modules=selected_modules, features=features, m3_modules=frozenset(m3_modules or ()), include_m2=include_m2, session_factory=session_factory, confirmation_service=confirmation_service, router=router, transport_retries=configured_retries)


def _trace_scope(repository: SQLAlchemyAssistantRepository, session: Session, actor: object, run_id: str) -> None:
    actor_context = getattr(actor, "actor", actor)
    with repository.transaction(session) as db:
        run = repository.get_run(db, run_id)
        if run is None or not actor_context.can_access_project(run.project_id):
            raise AssistantError(404, "RESOURCE_NOT_FOUND", "Trace 资源不存在或当前主体不可见")
        conversation = repository.get_conversation(db, run.conversation_id)
        if conversation is None or conversation.owner_id != actor_context.actor_id:
            raise AssistantError(404, "RESOURCE_NOT_FOUND", "Trace 资源不存在或当前主体不可见")


def _worker_for(
    assembly: M4Assembly,
    repository: SQLAlchemyAssistantRepository,
    task_repository: SQLAlchemyTaskRepository,
    event_store: MySQLAgentEventStore,
    session: Session,
    *,
    actor_context: ActorContext | None = None,
) -> AssistantWorkerEntrypoint:
    factory = assembly.session_factory
    if factory is None:
        worker_repository = repository
        worker_events = event_store
    else:
        worker_repository = SQLAlchemyAssistantRepository(session_factory=factory)
        worker_events = _FreshEventStore(factory)
    provider = PydanticAIAdapter()
    config = resolve_runtime_config()
    trace_inspector = None
    if "trace" in assembly.features:
        trace_store = _FreshTraceStore(factory) if factory is not None else _BoundTraceStore(repository, session)
        trace_inspector = ContextInspector(trace_store)
    priority_service = _worker_priority_service(factory or (lambda: session), model_center_enabled="model_center" in assembly.m3_modules)
    services = {}
    if priority_service is not None:
        services["priority"] = PriorityAssistantToolService(priority_service)
    runtime = AgentRuntime(
        provider,
        config,
        # Priority launch keeps the runtime schema bounded to the actual
        # launch surface. Request-scoped HTTP adapters bind the services;
        # workers still fail closed when those ports are unavailable.
        toolset=priority_toolset(),
        services=services,
        ledger=_SQLExecutionLedger(factory, session) if factory is None else None,
        history_loader=_WorkerHistory(worker_repository),
        context_inspector=trace_inspector,
        event_store=worker_events,
        max_retries=assembly.transport_retries,
    )
    runtime = _ProjectAwareWorkerRuntime(runtime, worker_repository, actor_context=actor_context)
    def load_job(job_id: str):
        if factory is None:
            return task_repository.get_job(session, job_id)
        with task_repository.transaction() as db:
            return task_repository.get_job(db, job_id)

    return AssistantWorkerEntrypoint(AgentWorker(runtime=runtime, store=worker_repository, event_store=worker_events), job_loader=load_job)


def bind_m4(
    assembly: M4Assembly,
    session: Session,
    *,
    idempotency_store: SQLAlchemyIdempotencyStore | None = None,
    actor_context: ActorContext | None = None,
    build_worker: bool = True,
) -> M4RequestPorts:
    if not isinstance(session, Session):
        raise TypeError("bind_m4 requires a caller-owned SQLAlchemy Session")
    repository = SQLAlchemyAssistantRepository(session)
    event_store = MySQLAgentEventStore(session, AgentEventRow)
    persistent_idempotency = idempotency_store or SQLAlchemyIdempotencyStore(session)
    task_repository = SQLAlchemyTaskRepository(assembly.session_factory or (lambda: session))
    execution_ledger = _SQLExecutionLedger(assembly.session_factory, session)
    # ContextInspector is intentionally enabled only when the build selected
    # trace.  It stores no provider client and never fabricates snapshots.
    trace = None
    if "trace" in assembly.features:
        trace = ContextInspector(_BoundTraceStore(repository, session), scope_checker=lambda actor, run_id: _trace_scope(repository, session, actor, run_id))
    # Queueing a Run is valid even when the optional provider is unavailable;
    # the formal Worker then records a real 503 failure instead of fabricating
    # a model response.
    service = AssistantService(
        repository=repository,
        runtime_available=True,
        confirmation=assembly.confirmation_service,
        idempotency_store=persistent_idempotency,
        task_repository=task_repository,
        ledger=execution_ledger,
    )
    # HTTP requests only need the request-scoped service/event/trace ports.
    # The assistant worker runs in the dedicated Worker process; constructing
    # it here would reload every verified prediction registry for every HTTP
    # request and can exhaust memory under concurrent health checks.
    worker = (
        _worker_for(assembly, repository, task_repository, event_store, session, actor_context=actor_context)
        if build_worker
        else None
    )
    return M4RequestPorts(
        session=session,
        repository=repository,
        service=service,
        event_store=event_store,
        trace_inspector=trace,
        idempotency_store=persistent_idempotency,
        execution_ledger=execution_ledger,
        worker=worker,
    )


assemble_m4_metadata = m4_metadata

__all__ = ["AssistantWorkerEntrypoint", "M4Assembly", "M4RequestPorts", "agent_worker_message_from_job", "assemble_m4", "assemble_m4_metadata", "bind_m4", "m4_metadata"]
