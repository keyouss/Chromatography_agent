"""Assistant application service without an agent runtime."""

from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timezone
import hashlib
import inspect
import json
import re
import base64
from typing import Any, Mapping
from uuid import uuid4

from chrom_platform.core.dto import ActorContext, ResourceRef
from chrom_platform.core.errors import APIError
from chrom_platform.core.idempotency import IdempotencyScope
from chrom_platform.task_core.service import OperationContext, TargetSnapshot, TaskError, add_job_rows, add_job_attempt_rows, sync_job_lifecycle
from chrom_platform.task_core.state import JobStatus
from chrom_platform.infrastructure.models import AuditEvent, OutboxEvent
from sqlalchemy import select

from .errors import AssistantError, unavailable
from .persistence import LlmConfigurationActiveRow, LlmConfigurationRow
from .schemas import (
    AgentRun, Attempt, CancelAgentRunRequest, Conversation, ConversationArchive,
    ConversationCreate, ConversationDelete, ConversationDeleted, ConversationPatch, Message, MessageAccepted,
    PendingConfirmationBinding, ResumeAgentRunRequest, ToolCall, ToolCallDraft,
    UserMessageCreate, Usage,
)


_ACTIVE_RUNS = frozenset({"QUEUED", "RUNNING", "WAITING_CONFIRMATION", "CANCEL_REQUESTED"})
_TERMINAL_RUNS = frozenset({"SUCCEEDED", "FAILED", "CANCELED", "TIMED_OUT", "INTERRUPTED"})
_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "QUEUED": frozenset({"RUNNING", "WAITING_CONFIRMATION", "CANCEL_REQUESTED", "CANCELED", "FAILED", "INTERRUPTED"}),
    "RUNNING": frozenset({"WAITING_CONFIRMATION", "CANCEL_REQUESTED", "SUCCEEDED", "FAILED", "CANCELED", "TIMED_OUT", "INTERRUPTED"}),
    "WAITING_CONFIRMATION": frozenset({"RUNNING", "CANCEL_REQUESTED", "CANCELED", "TIMED_OUT", "INTERRUPTED"}),
    "CANCEL_REQUESTED": frozenset({"CANCELED", "FAILED", "INTERRUPTED"}),
}
_TOOL_PERMISSIONS = {
    "normalize_structure": "prediction:create",
    "validate_enantiomer_pair": "structure:read",
    "search_knowledge": "knowledge:read",
    "read_citation": "knowledge:read",
    "create_prediction": "prediction:create",
    "get_prediction": "prediction:read",
    "get_prediction_capabilities": "prediction:read",
    "create_batch_draft": "batch:create",
    "update_batch_draft": "batch:write",
    "validate_batch": "batch:read",
    "submit_batch": "batch:submit",
    "get_batch": "batch:read",
    "list_training_templates": "training:read",
    "create_experiment_draft": "training:create",
    "validate_experiment": "training:read",
    "submit_training": "training:submit",
    "get_experiment": "training:read",
    "get_model_card": "model:read",
    "create_registration_draft": "model:register",
    "validate_registration": "model:read",
    "register_candidate": "model:register",
    "get_evaluation": "model:read",
    "prepare_approval_request": "model:read",
    "molecule_lookup": "utilities:read",
    "tlc_reverse_solvent": "prediction:create",
}
_FORBIDDEN_TOOL_NAMES = frozenset({
    "confirmation:create", "approve", "publish", "rollback", "shell", "exec",
    "eval", "python", "sql", "http_fetch", "http.fetch", "file_write",
})
_SENSITIVE_KEYS = frozenset({"password", "secret", "token", "api_key", "apikey", "credential", "credentials", "authorization", "raw_result", "raw_response"})
ASSISTANT_TOOL_PERMISSIONS = frozenset(_TOOL_PERMISSIONS.values())
DISALLOWED_ASSISTANT_TOOLS = _FORBIDDEN_TOOL_NAMES


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _run_job_id(run: AgentRun) -> str | None:
    for ref in run.related_resources:
        if getattr(ref, "type", None) == "job":
            return str(getattr(ref, "id", "")) or None
    return None


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _is_deleted_replay(value: Any) -> bool:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return isinstance(value, Mapping) and isinstance(value.get("_deleted_resource"), Mapping)


def encode_cursor(scope: str, key: Any, *, filters: Mapping[str, Any] | None = None) -> str:
    """Opaque, filter-bound cursor; the sort key is always server generated."""
    raw = _canonical({"v": 1, "scope": scope, "key": key, "filters": dict(filters or {})}).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str | None, scope: str, *, filters: Mapping[str, Any] | None = None) -> Any:
    if cursor is None:
        return None
    try:
        if not isinstance(cursor, str) or not cursor or len(cursor) > 512 or re.fullmatch(r"[A-Za-z0-9_-]+", cursor) is None:
            raise ValueError
        padded = cursor + "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        if (
            not isinstance(value, dict)
            or value.get("v") != 1
            or value.get("scope") != scope
            or not isinstance(value.get("filters"), dict)
            or value["filters"] != dict(filters or {})
            or "key" not in value
        ):
            raise ValueError
        key = value["key"]
        if scope == "messages":
            if type(key) is not int or key < 1:
                raise ValueError
        elif scope in {"conversations", "runs", "tool_calls"}:
            if not isinstance(key, list) or len(key) != 2 or any(not isinstance(part, str) or not part for part in key):
                raise ValueError
        return key
    except (ValueError, KeyError, TypeError, json.JSONDecodeError, UnicodeError) as exc:
        raise AssistantError(400, "INVALID_CURSOR", "分页 cursor 无效或与当前筛选条件不匹配") from exc


def _binding_hash(binding: PendingConfirmationBinding) -> str:
    return _hash(binding.model_dump(mode="json", exclude={"confirmation_id", "expires_at"}))


def _safe(value: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        return "[omitted]"
    if isinstance(value, Mapping):
        return {str(k): _safe(v, depth=depth + 1) for k, v in value.items() if not _sensitive_key(str(k))}
    if isinstance(value, (list, tuple)):
        return [_safe(v, depth=depth + 1) for v in value[:100]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _sensitive_key(key: str) -> bool:
    """Recognize common secret spellings at every nesting level."""
    lowered = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key).lower()
    normalized = re.sub(r"[^a-z0-9]+", "_", lowered).strip("_")
    if normalized in _SENSITIVE_KEYS:
        return True
    parts = set(normalized.split("_"))
    return bool(parts & {"password", "secret", "token", "credential", "credentials", "authorization"}) or normalized.endswith(("apikey", "api_key", "raw_result", "raw_response"))


def transition_run(current: str, target: str) -> str:
    """Validate and return a Run transition for callers/workers."""
    AssistantService._next_status(current, target)
    return target


def transition_attempt(current: str, target: str) -> str:
    allowed = {
        "QUEUED": {"RUNNING", "WAITING_CONFIRMATION", "CANCEL_REQUESTED", "CANCELED", "FAILED", "INTERRUPTED"},
        "WAITING_CONFIRMATION": {"RUNNING", "CANCEL_REQUESTED", "CANCELED", "TIMED_OUT", "INTERRUPTED"},
        "RUNNING": {"CANCEL_REQUESTED", "SUCCEEDED", "FAILED", "CANCELED", "TIMED_OUT", "INTERRUPTED"},
        "CANCEL_REQUESTED": {"CANCELED", "FAILED", "INTERRUPTED"},
    }
    if target not in allowed.get(current, set()):
        raise AssistantError(409, "STATE_CONFLICT", f"不允许的 Attempt 状态迁移: {current}->{target}")
    return target


def _ctx_actor(ctx: OperationContext | ActorContext) -> OperationContext:
    if isinstance(ctx, OperationContext):
        return ctx
    return OperationContext(ctx, interaction="assistant")


class AssistantService:
    """Conversation/run service.  Dependencies are mandatory at execution time."""

    def __init__(
        self,
        repository: object | None = None,
        *,
        repo: object | None = None,
        unit_of_work: object | None = None,
        uow: object | None = None,
        access: object | None = None,
        business_services: Mapping[str, object] | None = None,
        confirmation: object | None = None,
        ledger: object | None = None,
        tool_authorization: object | None = None,
        max_concurrent_runs: int = 1,
        runtime_available: bool = True,
        idempotency_store: object | None = None,
        task_repository: object | None = None,
    ) -> None:
        self.repository = repository if repository is not None else repo
        self.unit_of_work = unit_of_work if unit_of_work is not None else uow
        self.access = access
        self.business_services = dict(business_services or {})
        self.confirmation = confirmation
        self.ledger = ledger
        self.tool_authorization = tool_authorization
        self.max_concurrent_runs = max(1, max_concurrent_runs)
        self.runtime_available = runtime_available
        self.idempotency_store = idempotency_store
        # M4 constructs this service per HTTP request; transport metadata stays out of DTOs.
        self.idempotency_replayed = False
        # M4 injects the platform TaskRepository.  Assistant does not own
        # queue claiming or completion; the platform outbox dispatcher does.
        self.task_repository = task_repository
        # The process-local cache remains the explicit fallback for test and
        # trimmed in-memory assemblies. Production M4 passes the shared
        # SQLAlchemy idempotency port below.
        persisted_replays = getattr(self.repository, "_assistant_write_replays", None) if self.repository is not None else None
        if persisted_replays is None:
            persisted_replays = {}
            if self.repository is not None:
                try:
                    setattr(self.repository, "_assistant_write_replays", persisted_replays)
                except Exception:
                    pass
        self._write_replays = persisted_replays

    @staticmethod
    def _idempotency_route(operation: str) -> str:
        return {
            "conversation.create": "/api/v1/conversations",
            "conversation.patch": "/api/v1/conversations/{conversation_id}",
            "conversation.archive": "/api/v1/conversations/{conversation_id}/archive",
            "conversation.delete": "/api/v1/conversations/{conversation_id}",
            "message.send": "/api/v1/conversations/{conversation_id}/messages",
            "run.cancel": "/api/v1/agent-runs/{run_id}/cancel",
            "run.resume": "/api/v1/agent-runs/{run_id}/resume",
        }.get(operation, f"/api/v1/assistant/{operation}")

    @staticmethod
    def _idempotency_method(operation: str) -> str:
        return "DELETE" if operation == "conversation.delete" else "POST"

    @staticmethod
    def _replay_model(operation: str):
        return {
            "conversation.create": Conversation,
            "conversation.patch": Conversation,
            "conversation.archive": Conversation,
            "conversation.delete": ConversationDeleted,
            "message.send": MessageAccepted,
            "run.cancel": AgentRun,
            "run.resume": AgentRun,
        }.get(operation)

    def _replay(
        self,
        ctx: OperationContext,
        operation: str,
        target: str | None,
        key: str | None,
        payload: Any,
        *,
        project_id: str | None = None,
        session: object | None = None,
    ) -> object | None:
        self.idempotency_replayed = False
        if not key:
            return None
        digest = _hash(payload)
        if self.idempotency_store is not None:
            if not project_id and isinstance(payload, Mapping):
                project_id = payload.get("project_id")
            if not project_id:
                raise unavailable("幂等请求缺少项目范围")
            scope = IdempotencyScope(
                actor_id=ctx.actor.actor_id,
                project_id=str(project_id),
                method=self._idempotency_method(operation),
                route_identity=self._idempotency_route(operation),
                key=key,
            )
            begin = getattr(self.idempotency_store, "begin", None)
            if not callable(begin):
                raise unavailable("Assistant 持久幂等端口未配置")
            result = begin(scope, digest)
            record = getattr(result, "record", result)
            if not getattr(result, "acquired", False):
                if getattr(record, "payload_hash", None) != digest:
                    raise AssistantError(409, "IDEMPOTENCY_KEY_REUSED", "Idempotency-Key 已用于其他内容")
                if getattr(record, "status", None) == "COMPLETED" and getattr(record, "response", None) is not None:
                    if _is_deleted_replay(record.response):
                        raise AssistantError(404, "RESOURCE_NOT_FOUND", "会话不存在或当前主体不可见")
                    model = self._replay_model(operation)
                    if model is None:
                        raise AssistantError(409, "STATE_CONFLICT", "幂等结果不可重放")
                    replay = model.model_validate_json(_canonical(record.response))
                    self.idempotency_replayed = True
                    return replay
                raise AssistantError(409, "OPERATION_IN_PROGRESS", "相同幂等请求正在处理中", retryable=True)
            return None
        identity = (ctx.actor.actor_id, operation, target, key)
        previous = self._write_replays.get(identity)
        if previous is not None:
            if previous[0] != digest:
                raise AssistantError(409, "IDEMPOTENCY_KEY_REUSED", "Idempotency-Key 已用于其他内容")
            if _is_deleted_replay(previous[1]):
                raise AssistantError(404, "RESOURCE_NOT_FOUND", "会话不存在或当前主体不可见")
            self.idempotency_replayed = True
            return previous[1]
        return None

    def _remember(
        self,
        ctx: OperationContext,
        operation: str,
        target: str | None,
        key: str | None,
        payload: Any,
        result: object,
        *,
        project_id: str | None = None,
        session: object | None = None,
    ) -> None:
        if key:
            if self.idempotency_store is not None:
                if not project_id:
                    raise unavailable("幂等结果缺少项目范围")
                scope = IdempotencyScope(
                    actor_id=ctx.actor.actor_id,
                    project_id=project_id,
                    method=self._idempotency_method(operation),
                    route_identity=self._idempotency_route(operation),
                    key=key,
                )
                complete = getattr(self.idempotency_store, "complete", None)
                if not callable(complete):
                    raise unavailable("Assistant 持久幂等端口未配置")
                response = result.model_dump(mode="json") if hasattr(result, "model_dump") else _safe(result)
                status_code = 201 if operation == "conversation.create" else 202 if operation in {"message.send", "run.cancel", "run.resume"} else 200
                complete(scope, _hash(payload), status_code, response)
                return
            self._write_replays[(ctx.actor.actor_id, operation, target, key)] = (_hash(payload), result)

    def _require_repo(self) -> object:
        repo = self.repository
        if repo is None:
            raise unavailable("Assistant repository 未配置")
        if not callable(getattr(repo, "transaction", None)):
            raise unavailable("Assistant UOW 未配置")
        return repo

    def _tx(self, session: object | None = None):
        repo = self._require_repo()
        transaction = getattr(repo, "transaction", None)
        if callable(transaction):
            return transaction(session)
        if self.unit_of_work is not None and callable(getattr(self.unit_of_work, "transaction", None)):
            return self.unit_of_work.transaction(session)
        raise unavailable("Assistant UOW 未配置")

    @staticmethod
    def _access(ctx: OperationContext, project_id: str, access: object | None, *, permission: str = "assistant:use") -> None:
        if access is not None:
            method = getattr(access, "ensure_project_access", None)
            if not callable(method):
                raise unavailable("Assistant 访问端口未配置")
            method(ctx, project_id, permission=permission)
        elif not ctx.actor.can_access_project(project_id):
            # Deliberately returns 404 to avoid project enumeration.
            raise AssistantError(404, "RESOURCE_NOT_FOUND", "项目不存在或当前主体不可见")
        if not ctx.actor.can("assistant:use") and not ctx.actor.can(permission):
            raise AssistantError(403, "PERMISSION_DENIED", "当前主体无权使用助手")

    @staticmethod
    def _project_from_run(run: AgentRun, ctx: OperationContext, access: object | None) -> None:
        AssistantService._access(ctx, run.project_id, access)

    @staticmethod
    def _etag(value: object) -> str:
        if isinstance(value, Conversation):
            return _hash({"id": value.id, "revision": value.revision, "status": value.status, "updated_at": value.updated_at.isoformat()})
        if isinstance(value, AgentRun):
            return _hash({"id": value.id, "revision": value.revision, "status": value.status, "attempt": value.attempt, "pending": [p.model_dump(mode="json") for p in value.pending_actions]})
        return _hash(value)

    @staticmethod
    def _next_status(current: str, target: str) -> None:
        if current in _TERMINAL_RUNS:
            raise AssistantError(409, "STATE_CONFLICT", f"Run 已处于终态 {current}")
        if target not in _ALLOWED_TRANSITIONS.get(current, frozenset()):
            raise AssistantError(409, "STATE_CONFLICT", f"不允许的 Run 状态迁移: {current}->{target}")

    @staticmethod
    def _attempt(repo: object, db: object, run: AgentRun) -> Attempt | None:
        getter = getattr(repo, "get_attempt", None)
        if not callable(getter):
            return None
        return getter(db, run.id, run.attempt)

    @staticmethod
    def _set_attempt(repo: object, db: object, attempt: Attempt | None, target: str, *, now: datetime) -> Attempt | None:
        if attempt is None:
            return None
        transition_attempt(attempt.status, target)
        updates: dict[str, Any] = {"status": target}
        if target == "RUNNING":
            updates["started_at"] = attempt.started_at or now
        if target in _TERMINAL_RUNS:
            updates["finished_at"] = now
        updated = attempt.model_copy(update=updates)
        repo.save_attempt(db, updated)
        return updated

    def create_conversation(self, ctx: OperationContext, request: ConversationCreate, *, idempotency_key: str | None = None, session: object | None = None) -> Conversation:
        ctx = _ctx_actor(ctx)
        self._access(ctx, request.project_id, self.access)
        payload = request.model_dump(mode="json")
        replay = self._replay(ctx, "conversation.create", None, idempotency_key, payload, project_id=request.project_id, session=session)
        if replay is not None:
            return replay
        now = _now()
        title = (request.title or "").strip() or "新对话"
        value = Conversation(id=_id("conv"), project_id=request.project_id, owner_id=ctx.actor.actor_id, title=title, mode=request.mode, status="ACTIVE", created_at=now, updated_at=now, revision=1, etag="pending")
        value = value.model_copy(update={"etag": self._etag(value)})
        with self._tx(session) as db:
            self.repository.save_conversation(db, value)
        self._remember(ctx, "conversation.create", None, idempotency_key, payload, value, project_id=request.project_id, session=session)
        return value

    def list_conversations(self, ctx: OperationContext, *, project_id: str | None = None, q: str | None = None, archived: bool | None = None, limit: int = 50, cursor: str | None = None, session: object | None = None) -> list[Conversation]:
        ctx = _ctx_actor(ctx)
        with self._tx(session) as db:
            requested_limit = min(max(limit, 1), 200)
            filters = {"project_id": project_id, "q": q, "archived": archived}
            marker = decode_cursor(cursor, "conversations", filters=filters)
            if project_id is not None:
                self._access(ctx, project_id, self.access)
                values = self.repository.list_conversations(db, project_id=project_id, owner_id=ctx.actor.actor_id, archived=archived, q=q, limit=200)
                values = sorted(values, key=lambda item: (item.updated_at, item.id), reverse=True)
                if marker is not None:
                    values = [v for v in values if (v.updated_at.isoformat(), v.id) < tuple(marker)]
                return values[:requested_limit]

            # A project-less list is still scoped to the server-issued actor
            # project set.  Passing NULL to the repository would otherwise
            # return same-owner rows from projects the actor cannot access.
            values: list[Conversation] = []
            for accessible_project in ctx.actor.project_ids:
                self._access(ctx, accessible_project, self.access)
                values.extend(self.repository.list_conversations(db, project_id=accessible_project, owner_id=ctx.actor.actor_id, archived=archived, q=q, limit=requested_limit))
            values = sorted(values, key=lambda item: (item.updated_at, item.id), reverse=True)
            if marker is not None:
                values = [v for v in values if (v.updated_at.isoformat(), v.id) < tuple(marker)]
            return values[:requested_limit]

    def get_conversation(self, ctx: OperationContext, conversation_id: str, *, session: object | None = None) -> Conversation:
        ctx = _ctx_actor(ctx)
        with self._tx(session) as db:
            value = self.repository.get_conversation(db, conversation_id)
            if value is None:
                raise AssistantError(404, "RESOURCE_NOT_FOUND", "会话不存在或当前主体不可见")
            self._access(ctx, value.project_id, self.access)
            if value.owner_id != ctx.actor.actor_id:
                raise AssistantError(404, "RESOURCE_NOT_FOUND", "会话不存在或当前主体不可见")
            return value

    def patch_conversation(self, ctx: OperationContext, conversation_id: str, request: ConversationPatch, *, expected_etag: str | None = None, idempotency_key: str | None = None, session: object | None = None) -> Conversation:
        ctx = _ctx_actor(ctx)
        repo = self._require_repo()
        with repo.transaction(session) as db:
            value = repo.lock_conversation(db, conversation_id)
            if value is None or value.owner_id != ctx.actor.actor_id:
                raise AssistantError(404, "RESOURCE_NOT_FOUND", "会话不存在或当前主体不可见")
            self._access(ctx, value.project_id, self.access)
            payload = {"request": request.model_dump(mode="json"), "expected_etag": expected_etag}
            current_session = session if session is not None else db
            replay = self._replay(ctx, "conversation.patch", conversation_id, idempotency_key, payload, project_id=value.project_id, session=current_session)
            if replay is not None:
                return replay
            if value.status == "ARCHIVED":
                raise AssistantError(409, "STATE_CONFLICT", "归档会话不可修改")
            if expected_etag is not None and expected_etag != value.etag:
                raise AssistantError(412, "VERSION_CONFLICT", "会话版本已变化")
            title = (request.title or "").strip() or "新对话"
            updated = value.model_copy(update={"title": title, "revision": value.revision + 1, "updated_at": _now()})
            updated = updated.model_copy(update={"etag": self._etag(updated)})
            repo.save_conversation(db, updated)
            if not getattr(repo, "is_test_only", False):
                db.add(AuditEvent(
                    id=_id("audit"), project_id=value.project_id,
                    actor_id=ctx.actor.actor_id, auth_method=ctx.actor.auth_method,
                    interaction=ctx.interaction, request_id=ctx.request_id,
                    action="assistant.conversation.renamed", resource_type="conversation",
                    resource_id=value.id, resource_ref={"type": "conversation", "id": value.id},
                    before_summary={"revision": value.revision},
                    after_summary={"revision": updated.revision},
                    confirmation_id=None, config_hash=None, job_id=None,
                    agent_run_id=None, result="SUCCEEDED", timestamp=updated.updated_at,
                ))
            self._remember(ctx, "conversation.patch", conversation_id, idempotency_key, payload, updated, project_id=value.project_id, session=current_session)
            return updated

    def archive_conversation(self, ctx: OperationContext, conversation_id: str, request: ConversationArchive, *, idempotency_key: str | None = None, session: object | None = None) -> Conversation:
        ctx = _ctx_actor(ctx)
        repo = self._require_repo()
        with repo.transaction(session) as db:
            value = repo.lock_conversation(db, conversation_id)
            if value is None or value.owner_id != ctx.actor.actor_id:
                raise AssistantError(404, "RESOURCE_NOT_FOUND", "会话不存在或当前主体不可见")
            self._access(ctx, value.project_id, self.access)
            payload = request.model_dump(mode="json")
            current_session = session if session is not None else db
            replay = self._replay(ctx, "conversation.archive", conversation_id, idempotency_key, payload, project_id=value.project_id, session=current_session)
            if replay is not None:
                return replay
            if value.status != "ACTIVE":
                raise AssistantError(409, "STATE_CONFLICT", "归档会话当前状态不可变更")
            if self.confirmation is None:
                raise unavailable("Conversation archive confirmation 端口未配置")
            consume = getattr(self.confirmation, "consume_for_mutation", None)
            if not callable(consume):
                raise unavailable("Conversation archive Confirmation 消费端口未配置")
            target = self.conversation_archive_target(ctx, value)
            now = _now()
            updated = value.model_copy(update={"status": "ARCHIVED", "revision": value.revision + 1, "updated_at": now})
            updated = updated.model_copy(update={"etag": self._etag(updated)})
            try:
                consume(
                    ctx, request.confirmation_id, target=target, action="resource.archive",
                    result_ref={"resource_ref": {"type": "conversation", "id": value.id}, "status": "ARCHIVED", "revision": updated.revision},
                    session=current_session,
                )
            except TaskError as exc:
                raise AssistantError(exc.status_code, exc.code, exc.message, retryable=exc.retryable, details=exc.details) from exc
            repo.save_conversation(db, updated)
            if not getattr(repo, "is_test_only", False):
                db.add(AuditEvent(
                    id=_id("audit"), project_id=value.project_id,
                    actor_id=ctx.actor.actor_id, auth_method=ctx.actor.auth_method,
                    interaction=ctx.interaction, request_id=ctx.request_id,
                    action="assistant.conversation.archived", resource_type="conversation",
                    resource_id=value.id, resource_ref={"type": "conversation", "id": value.id},
                    before_summary={"status": value.status, "revision": value.revision},
                    after_summary={"status": updated.status, "revision": updated.revision},
                    confirmation_id=request.confirmation_id, config_hash=target.config_hash,
                    job_id=None, agent_run_id=None, result="SUCCEEDED", timestamp=now,
                ))
            self._remember(ctx, "conversation.archive", conversation_id, idempotency_key, payload, updated, project_id=value.project_id, session=current_session)
            return updated

    def delete_conversation(self, ctx: OperationContext, conversation_id: str, request: ConversationDelete, *, expected_etag: str | None = None, idempotency_key: str | None = None, session: object | None = None) -> ConversationDeleted:
        ctx = _ctx_actor(ctx)
        repo = self._require_repo()
        payload = {
            "conversation_id": conversation_id,
            "request": request.model_dump(mode="json"),
            "expected_etag": expected_etag,
        }
        with repo.transaction(session) as db:
            value = repo.lock_conversation(db, conversation_id)
            if value is None:
                audit = repo.get_conversation_deletion(db, conversation_id)
                if audit is None or audit.actor_id != ctx.actor.actor_id:
                    raise AssistantError(404, "RESOURCE_NOT_FOUND", "会话不存在或当前主体不可见")
                self._access(ctx, audit.project_id, self.access)
                replay = self._replay(ctx, "conversation.delete", conversation_id, idempotency_key, payload, project_id=audit.project_id, session=db)
                if replay is not None:
                    return replay
                raise AssistantError(404, "RESOURCE_NOT_FOUND", "会话不存在或当前主体不可见")

            if value.owner_id != ctx.actor.actor_id:
                raise AssistantError(404, "RESOURCE_NOT_FOUND", "会话不存在或当前主体不可见")
            self._access(ctx, value.project_id, self.access)
            replay = self._replay(ctx, "conversation.delete", conversation_id, idempotency_key, payload, project_id=value.project_id, session=db)
            if replay is not None:
                return replay
            if expected_etag is not None and expected_etag != value.etag:
                raise AssistantError(412, "ETAG_MISMATCH", "会话版本已变化")

            busy = bool(repo.conversation_delete_busy(db, conversation_id))
            if busy:
                raise AssistantError(409, "CONVERSATION_BUSY", "请先停止回答并等待运行结束")
            runs = repo.lock_conversation_runs(db, conversation_id)
            run_ids = [run.id for run in runs]
            message_ids = repo.conversation_message_ids(db, conversation_id)
            target = self.conversation_delete_target(ctx, value, busy=False)
            if self.confirmation is None:
                raise unavailable("Conversation delete confirmation 端口未配置")
            consume = getattr(self.confirmation, "consume_for_mutation", None)
            if not callable(consume):
                raise unavailable("Conversation delete Confirmation 消费端口未配置")
            try:
                consume(
                    ctx, request.confirmation_id, target=target, action="conversation.delete",
                    result_ref={"resource_ref": {"type": "conversation", "id": value.id}, "deleted": True},
                    session=db,
                )
            except TaskError as exc:
                code = "CONFIRMATION_INVALIDATED" if exc.code == "CONFIRMATION_INVALID" else exc.code
                raise AssistantError(exc.status_code, code, exc.message, retryable=exc.retryable, details=exc.details) from exc

            now = _now()
            audit = AuditEvent(
                id=_id("audit"), project_id=value.project_id,
                actor_id=ctx.actor.actor_id, auth_method=ctx.actor.auth_method,
                interaction=ctx.interaction, request_id=ctx.request_id,
                action="assistant.conversation.deleted", resource_type="conversation",
                resource_id=value.id, resource_ref={"type": "conversation", "id": value.id},
                before_summary={"status": value.status, "revision": value.revision},
                after_summary={"deleted": True}, confirmation_id=request.confirmation_id,
                config_hash=target.config_hash, job_id=None, agent_run_id=None,
                result="SUCCEEDED", timestamp=now,
            )
            if getattr(repo, "is_test_only", False):
                repo.record_conversation_deletion(db, audit)
            else:
                db.add(audit)
            repo.delete_conversation_data(
                db, conversation_id, run_ids=run_ids, message_ids=message_ids,
                actor_id=ctx.actor.actor_id, project_id=value.project_id,
            )
            result = ConversationDeleted(id=conversation_id)
            self._remember(ctx, "conversation.delete", conversation_id, idempotency_key, payload, result, project_id=value.project_id, session=db)
            return result

    @staticmethod
    def conversation_delete_target(ctx: OperationContext, conversation: Conversation, *, busy: bool = False) -> TargetSnapshot:
        binding = {
            "conversation_id": conversation.id,
            "project_id": conversation.project_id,
            "owner_id": conversation.owner_id,
            "revision": conversation.revision,
            "etag": conversation.etag,
            "status": conversation.status,
            "busy": bool(busy),
        }
        return TargetSnapshot(
            project_id=conversation.project_id,
            resource_ref=ResourceRef(type="conversation", id=conversation.id),
            revision=conversation.revision,
            config_hash=_hash(binding),
            etag=conversation.etag,
            policy_version=ctx.actor.policy_version,
            summary=[{"label": "删除会话", "value": conversation.id}, {"label": "版本", "value": str(conversation.revision)}],
            can_confirm=not busy,
            blockers=[] if not busy else [{"code": "CONVERSATION_BUSY", "message": "请先停止回答并等待运行结束"}],
        )

    @staticmethod
    def conversation_archive_target(ctx: OperationContext, conversation: Conversation) -> TargetSnapshot:
        binding = {
            "conversation_id": conversation.id,
            "project_id": conversation.project_id,
            "owner_id": conversation.owner_id,
            "revision": conversation.revision,
            "etag": conversation.etag,
            "status": conversation.status,
        }
        return TargetSnapshot(
            project_id=conversation.project_id,
            resource_ref=ResourceRef(type="conversation", id=conversation.id),
            revision=conversation.revision,
            config_hash=_hash(binding),
            etag=conversation.etag,
            policy_version=ctx.actor.policy_version,
            summary=[{"label": "会话归档", "value": conversation.id}, {"label": "版本", "value": str(conversation.revision)}],
            can_confirm=conversation.status == "ACTIVE",
            blockers=[] if conversation.status == "ACTIVE" else [{"code": "ALREADY_ARCHIVED", "message": "会话已归档"}],
        )

    def send_message(self, ctx: OperationContext, conversation_id: str, request: UserMessageCreate, *, idempotency_key: str | None = None, session: object | None = None) -> MessageAccepted:
        ctx = _ctx_actor(ctx)
        audit_interaction = "agent_tool" if ctx.interaction == "assistant" else ctx.interaction
        if audit_interaction not in {"human", "agent_tool", "worker", "service"}:
            raise unavailable("Assistant 操作的 interaction 类型无效")
        if not self.runtime_available:
            raise unavailable("PydanticAI adapter 未安装或未配置，助手运行不可用")
        repo = self._require_repo()
        with repo.transaction(session) as db:
            conversation = repo.lock_conversation(db, conversation_id)
            if conversation is None or conversation.owner_id != ctx.actor.actor_id:
                raise AssistantError(404, "RESOURCE_NOT_FOUND", "会话不存在或当前主体不可见")
            # Access is deliberately before replay lookup.
            self._access(ctx, conversation.project_id, self.access)
            if request.mode is not None and request.mode != conversation.mode:
                raise AssistantError(409, "STATE_CONFLICT", "消息 mode 必须与会话一致")
            payload = request.model_dump(mode="json", exclude={"client_message_id"})
            # Hash the effective mode so omitted mode and the fixed session
            # mode are the same replayable request.
            payload["mode"] = request.mode or conversation.mode
            key_payload = {"conversation_id": conversation_id, "request": request.model_dump(mode="json"), "effective_mode": payload["mode"]}
            replay = self._replay(ctx, "message.send", conversation_id, idempotency_key, key_payload, project_id=conversation.project_id, session=session)
            if replay is not None:
                return replay
            payload_hash = _hash(payload)
            old = repo.get_message_by_client_id(db, conversation_id, request.client_message_id)
            if old is not None:
                get_hash = getattr(repo, "get_message_payload_hash", None)
                old_hash = get_hash(db, old.id) if callable(get_hash) else None
                fallback = _hash({"content": next((b.text for b in old.blocks if getattr(b, "type", None) == "text"), ""), "attachment_ids": old.attachment_ids, "context_refs": [], "mode": request.mode or conversation.mode})
                if old_hash is None:
                    old_hash = fallback
                    if old_hash != payload_hash:
                        raise AssistantError(409, "IDEMPOTENCY_KEY_REUSED", "client_message_id 已用于其他内容")
                elif old_hash != payload_hash:
                    raise AssistantError(409, "IDEMPOTENCY_KEY_REUSED", "client_message_id 已用于其他内容")
                accepted = MessageAccepted(message_id=old.id, agent_run_id=old.agent_run_id or conversation.current_run_id or "unknown", status="QUEUED", events_url=f"/api/v1/agent-runs/{old.agent_run_id}/events", stream_url=f"/api/v1/agent-runs/{old.agent_run_id}/events/stream")
                self._remember(ctx, "message.send", conversation_id, idempotency_key, key_payload, accepted, project_id=conversation.project_id, session=session)
                return accepted
            if conversation.status != "ACTIVE":
                raise AssistantError(409, "STATE_CONFLICT", "归档会话不可发送新消息")
            if conversation.current_run_id:
                current = repo.get_run(db, conversation.current_run_id)
                if current is not None and current.status in _ACTIVE_RUNS:
                    raise AssistantError(409, "CONVERSATION_BUSY", "会话已有运行中的 Agent Run", details={"current_run_id": current.id})
            message_id, run_id, attempt_id = _id("msg"), _id("arun"), _id("attempt")
            now = _now()
            provider_configuration = None
            provider_configuration_hash = None
            scalar = getattr(db, "scalar", None)
            if callable(scalar):
                active_pointer = scalar(select(LlmConfigurationActiveRow).where(
                    LlmConfigurationActiveRow.project_id == conversation.project_id,
                ).with_for_update())
                if active_pointer is not None:
                    provider_configuration = scalar(select(LlmConfigurationRow).where(
                        LlmConfigurationRow.id == active_pointer.configuration_id,
                        LlmConfigurationRow.project_id == conversation.project_id,
                    ).with_for_update())
                    settings = getattr(provider_configuration, "settings", None)
                    candidate_hash = settings.get("config_hash") if isinstance(settings, Mapping) else None
                    if (
                        provider_configuration is None
                        or getattr(provider_configuration, "status", None) != "ACTIVE"
                        or getattr(provider_configuration, "revision", None) != active_pointer.configuration_revision
                        or not isinstance(candidate_hash, str)
                        or not re.fullmatch(r"[0-9a-f]{64}", candidate_hash)
                        or not isinstance(getattr(provider_configuration, "key_reference", None), str)
                        or not getattr(provider_configuration, "key_reference", "").strip()
                    ):
                        raise unavailable("活动 LLM 配置指针或不可变配置快照无效")
                    provider_configuration_hash = candidate_hash
                elif not getattr(self.repository, "is_test_only", False):
                    raise unavailable("项目尚未配置活动 LLM 配置")
            elif not getattr(self.repository, "is_test_only", False):
                raise unavailable("Assistant 活动配置读取端口未配置")
            if provider_configuration is None and not getattr(self.repository, "is_test_only", False):
                raise unavailable("项目尚未配置活动 LLM 配置")
            existing = repo.list_messages(db, conversation_id, limit=1_000_000)
            seq = str(max((int(m.seq) for m in existing), default=0) + 1)
            # MySQL has a real cycle: agent_run.input_message_id references
            # message, while message.agent_run_id references agent_run.  The
            # nullable side must be inserted first, then linked in-place.
            message = Message(id=message_id, conversation_id=conversation_id, seq=seq, role="user", agent_run_id=None, status="FINAL", blocks=[{"type": "text", "text": request.content}], attachment_ids=list(request.attachment_ids), created_at=now)
            run = AgentRun(id=run_id, conversation_id=conversation_id, project_id=conversation.project_id, input_message_id=message_id, mode=request.mode or conversation.mode, status="QUEUED", attempt=1, provider_configuration_id=getattr(provider_configuration, "id", None), provider_configuration_revision=getattr(provider_configuration, "revision", None), provider_configuration_hash=provider_configuration_hash, model_id=getattr(provider_configuration, "model_id", "server-configured"), prompt_version="1.0", toolset_version="1.0", policy_version=ctx.actor.policy_version, created_at=now, updated_at=now, revision=1, etag="pending")
            run = run.model_copy(update={"etag": self._etag(run)})
            attempt = Attempt(id=attempt_id, agent_run_id=run_id, attempt=1, status="QUEUED", created_at=now)
            repo.save_message(db, message)
            repo.save_run(db, run)
            repo.save_attempt(db, attempt)
            message = message.model_copy(update={"agent_run_id": run_id})
            repo.save_message(db, message)
            if not getattr(repo, "is_test_only", False):
                from .persistence import AssistantFrameworkStateRow
                # Engine selection is server-owned and committed with the Run.
                # Older nonterminal Runs deliberately have no such stamp.
                db.add(AssistantFrameworkStateRow(
                    agent_run_id=run_id, project_id=conversation.project_id,
                    conversation_id=conversation_id, engine="pydantic-ai",
                    framework_version="uninitialized", serialization_version="1",
                    config_hash=provider_configuration_hash, state={},
                    stream_message_id=None, created_at=now, updated_at=now,
                ))
            save_client_id = getattr(repo, "save_client_message_id", None)
            if callable(save_client_id):
                save_client_id(db, conversation_id, request.client_message_id, message.id)
            save_hash = getattr(repo, "save_message_payload_hash", None)
            if callable(save_hash):
                save_hash(db, message.id, payload_hash)
            updated_conversation = conversation.model_copy(update={"current_run_id": run_id, "last_message_at": now, "updated_at": now, "revision": conversation.revision + 1})
            repo.save_conversation(db, updated_conversation.model_copy(update={"etag": self._etag(updated_conversation)}))
            job = None
            if self.task_repository is not None:
                job = add_job_rows(
                    self.task_repository,
                    db,
                    project_id=conversation.project_id,
                    owner_id=conversation.owner_id,
                    kind="assistant",
                    resource_ref=ResourceRef(type="agent_run", id=run_id),
                    cancelable=True,
                    retryable=True,
                    resume_mode="restart",
                    deadline_at=None,
                )
                flush = getattr(self.task_repository, "flush", None)
                if callable(flush):
                    flush(db)
                # Keep the Assistant Run's server-owned relation visible while
                # the platform Job remains the queue/lifecycle authority.
                run = run.model_copy(update={"related_resources": [ResourceRef(type="job", id=job.id)]})
                repo.save_run(db, run)
                dispatch = db.scalar(
                    select(OutboxEvent).where(
                        OutboxEvent.aggregate_type == "job",
                        OutboxEvent.aggregate_id == job.id,
                        OutboxEvent.event_type == "job.execute",
                    )
                )
                if dispatch is None or set(dispatch.payload or {}) != {"job_id", "attempt", "dispatch_id"}:
                    raise unavailable("平台 Job dispatch 记录未创建")
                db.add(AuditEvent(
                    id=_id("audit"), project_id=conversation.project_id,
                    actor_id=ctx.actor.actor_id, auth_method=ctx.actor.auth_method,
                    interaction=audit_interaction, request_id=ctx.request_id,
                    action="assistant.message.accepted", resource_type="agent_run",
                    resource_id=run.id, resource_ref={"type": "agent_run", "id": run.id},
                    before_summary={}, after_summary={"status": "QUEUED", "message_id": message.id, "job_id": job.id},
                    confirmation_id=None, config_hash=None, job_id=job.id,
                    agent_run_id=run.id, result="SUCCEEDED", timestamp=now,
                ))
            accepted = MessageAccepted(message_id=message_id, agent_run_id=run_id, status="QUEUED", events_url=f"/api/v1/agent-runs/{run_id}/events", stream_url=f"/api/v1/agent-runs/{run_id}/events/stream")
            self._remember(ctx, "message.send", conversation_id, idempotency_key, key_payload, accepted, project_id=conversation.project_id, session=session)
            return accepted

    def messages(self, ctx: OperationContext, conversation_id: str, *, limit: int = 50, before: str | None = None, cursor: str | None = None, session: object | None = None) -> list[Message]:
        conversation = self.get_conversation(ctx, conversation_id, session=session)
        with self._tx(session) as db:
            if before is not None and cursor is not None:
                raise AssistantError(400, "INVALID_CURSOR", "before 与 cursor 不能同时使用")
            requested = min(max(limit, 1), 201)
            marker = decode_cursor(cursor, "messages", filters={"conversation_id": conversation_id})
            boundary = str(marker) if marker is not None else before
            values = self.repository.list_messages(db, conversation.id, limit=requested, before=boundary)
            return values[:requested]

    def runs(self, ctx: OperationContext, conversation_id: str, *, limit: int = 50, cursor: str | None = None, session: object | None = None) -> list[AgentRun]:
        conversation = self.get_conversation(ctx, conversation_id, session=session)
        with self._tx(session) as db:
            requested = min(max(limit, 1), 200)
            marker = decode_cursor(cursor, "runs", filters={"conversation_id": conversation_id})
            values = self.repository.list_runs(db, conversation.id, limit=200)
            values = sorted(values, key=lambda v: (v.created_at, v.id), reverse=True)
            if marker is not None:
                values = [v for v in values if (v.created_at.isoformat(), v.id) < tuple(marker)]
            return values[:requested]

    def get_run(self, ctx: OperationContext, run_id: str, *, session: object | None = None) -> AgentRun:
        ctx = _ctx_actor(ctx)
        with self._tx(session) as db:
            run = self.repository.get_run(db, run_id)
            if run is None:
                raise AssistantError(404, "RESOURCE_NOT_FOUND", "Agent Run 不存在或当前主体不可见")
            self._project_from_run(run, ctx, self.access)
            conversation = self.repository.get_conversation(db, run.conversation_id)
            if conversation is None or conversation.owner_id != ctx.actor.actor_id:
                raise AssistantError(404, "RESOURCE_NOT_FOUND", "Agent Run 不存在或当前主体不可见")
            return run

    def scope_run(self, ctx: OperationContext, run_id: str, *, session: object | None = None) -> AgentRun:
        """Validate trace resource ownership without requiring assistant:use."""
        ctx = _ctx_actor(ctx)
        with self._tx(session) as db:
            run = self.repository.get_run(db, run_id)
            if run is None or not ctx.actor.can_access_project(run.project_id):
                raise AssistantError(404, "RESOURCE_NOT_FOUND", "Agent Run 不存在或当前主体不可见")
            conversation = self.repository.get_conversation(db, run.conversation_id)
            if conversation is None or conversation.owner_id != ctx.actor.actor_id:
                raise AssistantError(404, "RESOURCE_NOT_FOUND", "Agent Run 不存在或当前主体不可见")
            return run

    def cancel_run(self, ctx: OperationContext, run_id: str, request: CancelAgentRunRequest | None = None, *, idempotency_key: str | None = None, expected_etag: str | None = None, session: object | None = None) -> AgentRun:
        ctx = _ctx_actor(ctx)
        payload = {"request": (request or CancelAgentRunRequest()).model_dump(mode="json"), "expected_etag": expected_etag}
        repo = self._require_repo()
        with repo.transaction(session) as db:
            initial = repo.get_run(db, run_id)
            if initial is None:
                raise AssistantError(404, "RESOURCE_NOT_FOUND", "Agent Run 不存在或当前主体不可见")
            conversation = repo.lock_conversation(db, initial.conversation_id)
            if conversation is None or conversation.owner_id != ctx.actor.actor_id:
                raise AssistantError(404, "RESOURCE_NOT_FOUND", "Agent Run 不存在或当前主体不可见")
            self._access(ctx, conversation.project_id, self.access)
            current = repo.get_run(db, run_id)
            if current is None or current.conversation_id != conversation.id:
                raise AssistantError(404, "RESOURCE_NOT_FOUND", "Agent Run 不存在或当前主体不可见")
            replay = self._replay(ctx, "run.cancel", run_id, idempotency_key, payload, project_id=current.project_id, session=db)
            if replay is not None:
                return replay
            if expected_etag is not None and expected_etag != current.etag:
                raise AssistantError(412, "VERSION_CONFLICT", "Agent Run 版本已变化")
            lock_for_cancel = getattr(repo, "lock_run_for_cancel", None)
            run = lock_for_cancel(db, run_id) if callable(lock_for_cancel) else repo.get_run(db, run_id)
            if run is None:
                raise AssistantError(404, "RESOURCE_NOT_FOUND", "Agent Run 不存在或当前主体不可见")
            self._project_from_run(run, ctx, self.access)
            if expected_etag is not None and expected_etag != run.etag:
                raise AssistantError(412, "VERSION_CONFLICT", "Agent Run 版本已变化")
            if run.status in _TERMINAL_RUNS:
                self._remember(ctx, "run.cancel", run_id, idempotency_key, payload, run, project_id=run.project_id, session=db)
                return run
            target = "CANCELED" if run.status in {"QUEUED", "WAITING_CONFIRMATION"} else "CANCEL_REQUESTED"
            self._next_status(run.status, target)
            now = _now()
            attempt = self._attempt(repo, db, run)
            self._set_attempt(repo, db, attempt, target, now=now)
            updated = run.model_copy(update={
                "status": target,
                "pending_actions": [] if target == "CANCELED" else run.pending_actions,
                "revision": run.revision + 1,
                "updated_at": now,
                "finished_at": now if target in _TERMINAL_RUNS else run.finished_at,
            })
            updated = updated.model_copy(update={"etag": self._etag(updated)})
            repo.save_run(db, updated)
            if target == "CANCELED":
                fail_partial = getattr(repo, "fail_partial_messages_for_run", None)
                if callable(fail_partial):
                    fail_partial(db, run_id)
            request_cancel = getattr(repo, "set_cancel_requested", None)
            if target in {"CANCEL_REQUESTED", "CANCELED"} and callable(request_cancel):
                request_cancel(db, run_id, requested=True)
            append_event = getattr(repo, "append_event", None)
            if callable(append_event):
                event_type = "run.cancel_requested" if target == "CANCEL_REQUESTED" else "run.canceled"
                event = append_event(
                    "agent_run", run_id, event_type,
                    {"status": target, "attempt": run.attempt},
                    agent_run_id=run_id, session=db,
                )
                event_seq = getattr(event, "seq", None)
                if event_seq is not None and int(event_seq) > int(updated.last_event_seq):
                    updated = updated.model_copy(update={"last_event_seq": str(event_seq)})
                    repo.save_run(db, updated)
            # Deliberately no JobService.cancel call: submitted Jobs outlive AgentRun.
            self._remember(ctx, "run.cancel", run_id, idempotency_key, payload, updated, project_id=run.project_id, session=db)
            return updated

    def _business_port(self, tool_name: str) -> object:
        port = self.business_services.get(tool_name) or self.business_services.get("*")
        if port is None:
            raise unavailable("业务 ApplicationService 端口未配置")
        if not callable(getattr(port, "validate", None)) or not callable(getattr(port, "execute", None)):
            raise unavailable("业务 ApplicationService 端口缺少 validate/execute")
        return port

    def _execute_ledger(self, *, run_id: str, tool_name: str, arguments: Mapping[str, Any], target_revision: Mapping[str, Any], fn):
        if self.ledger is None or not callable(getattr(self.ledger, "execute", None)):
            return fn(), False
        execution = self.ledger.execute(tool_name=tool_name, normalized_args=arguments, target_revision=target_revision, run_id=run_id, fn=fn)
        if getattr(execution, "status", None) == "UNKNOWN":
            raise AssistantError(503, "SIDE_EFFECT_UNKNOWN", "业务动作结果未知，禁止自动重投", retryable=False)
        return execution.result, bool(getattr(execution, "replayed", False))

    def _ledger_existing(self, *, run_id: str, tool_name: str, arguments: Mapping[str, Any], target_revision: Mapping[str, Any]):
        if self.ledger is None or not callable(getattr(self.ledger, "get", None)):
            return None
        key_builder = getattr(self.ledger, "operation_key", None)
        if not callable(key_builder):
            return None
        key = key_builder(tool_name=tool_name, normalized_args=arguments, target_revision=target_revision, run_id=run_id)
        return self.ledger.get(key)

    def _authorize_tool(self, ctx: OperationContext, tool_name: str) -> str:
        lowered = tool_name.lower()
        forbidden_key = re.sub(r"[\s._-]+", "_", lowered)
        if lowered in _FORBIDDEN_TOOL_NAMES or forbidden_key in {name.replace(".", "_") for name in _FORBIDDEN_TOOL_NAMES} or any(part in forbidden_key for part in ("shell", "exec", "eval", "python", "sql", "http_fetch", "confirmation", "approve", "publish", "rollback")):
            raise AssistantError(403, "PERMISSION_DENIED", "该工具被助手策略禁止")
        permission = _TOOL_PERMISSIONS.get(tool_name)
        if permission is None:
            raise AssistantError(403, "PERMISSION_DENIED", "该工具不在受控工具清单中")
        if self.tool_authorization is not None:
            method = getattr(self.tool_authorization, "authorize", None)
            if not callable(method):
                raise unavailable("工具授权端口未配置")
            method(ctx, tool_name=tool_name, permission=permission)
        elif not ctx.actor.can(permission) and not ctx.actor.can("*"):
            raise AssistantError(403, "PERMISSION_DENIED", "当前主体无权调用该业务工具")
        return permission

    def propose_tool_call(self, ctx: OperationContext, run_id: str, draft: ToolCallDraft, *, session: object | None = None) -> ToolCall:
        ctx = _ctx_actor(ctx)
        repo = self._require_repo()
        with repo.transaction(session) as db:
            run = repo.get_run(db, run_id)
            if run is None:
                raise AssistantError(404, "RESOURCE_NOT_FOUND", "Agent Run 不存在或当前主体不可见")
            self._project_from_run(run, ctx, self.access)
            if run.status in _TERMINAL_RUNS or run.status == "CANCEL_REQUESTED":
                raise AssistantError(409, "STATE_CONFLICT", "Run 当前不允许工具调用")
            self._authorize_tool(ctx, draft.tool_name)
            port = self._business_port(draft.tool_name)
            attempt = self._attempt(repo, db, run)
            try:
                port.validate(ctx, tool_name=draft.tool_name, arguments=draft.normalized_args, resource_ref=draft.resource_ref)
            except APIError:
                raise
            except Exception as exc:
                raise AssistantError(422, "VALIDATION_FAILED", "业务工具参数校验失败") from exc
            now = _now()
            call_id = _id("tool")
            pending = None
            if draft.requires_confirmation:
                pending = PendingConfirmationBinding(tool_call_id=call_id, resource_ref=draft.resource_ref, action=draft.action or "", revision=draft.revision or 1, config_hash=draft.config_hash or ("0" * 64), schema_version=draft.schema_version, normalized_args=_safe(draft.normalized_args))
            persisted_args = pending.normalized_args if pending is not None else draft.normalized_args
            call = ToolCall(id=call_id, agent_run_id=run.id, agent_attempt_id=attempt.id if attempt is not None else _id("attempt-ref"), provider_tool_call_id=draft.provider_tool_call_id, tool_name=draft.tool_name, tool_schema_version=draft.schema_version, arguments_hash=_hash(persisted_args), binding_hash=_binding_hash(pending) if pending is not None else None, status="WAITING_CONFIRMATION" if pending else "RUNNING", arguments_summary=_safe(draft.normalized_args), resource_ref=draft.resource_ref, pending_confirmation=pending, started_at=None if pending else now)
            if pending:
                self._set_attempt(repo, db, attempt, "WAITING_CONFIRMATION", now=now)
                updated_run = run.model_copy(update={"status": "WAITING_CONFIRMATION", "pending_actions": list(run.pending_actions) + [pending], "revision": run.revision + 1, "updated_at": now})
            else:
                target_revision = {"revision": draft.revision, "config_hash": draft.config_hash, "resource_ref": draft.resource_ref.model_dump(mode="json") if draft.resource_ref else None}
                existing_execution = self._ledger_existing(run_id=run.id, tool_name=draft.tool_name, arguments=draft.normalized_args, target_revision=target_revision)
                if existing_execution is not None and getattr(existing_execution, "status", None) == "UNKNOWN":
                    raise AssistantError(503, "SIDE_EFFECT_UNKNOWN", "业务动作结果未知，禁止自动重投", retryable=False)
                attempt = self._set_attempt(repo, db, attempt, "RUNNING", now=now)
                try:
                    result, replayed = self._execute_ledger(run_id=run.id, tool_name=draft.tool_name, arguments=draft.normalized_args, target_revision=target_revision, fn=lambda: port.execute(ctx, tool_name=draft.tool_name, arguments=draft.normalized_args, resource_ref=draft.resource_ref))
                except APIError:
                    self._set_attempt(repo, db, attempt, "FAILED", now=_now())
                    raise
                except Exception as exc:
                    self._set_attempt(repo, db, attempt, "FAILED", now=_now())
                    raise AssistantError(503, "BUSINESS_SERVICE_FAILED", "业务服务执行失败", retryable=True) from exc
                call = call.model_copy(update={"status": "SUCCEEDED", "result_summary": _safe(result), "finished_at": _now(), "job_id": _safe(result).get("job_id") if isinstance(_safe(result), dict) else None})
                self._set_attempt(repo, db, attempt, "SUCCEEDED", now=_now())
                updated_run = run.model_copy(update={"status": "SUCCEEDED", "revision": run.revision + 1, "updated_at": _now()})
            updated_run = updated_run.model_copy(update={"etag": self._etag(updated_run)})
            repo.save_tool_call(db, call)
            repo.save_run(db, updated_run)
            return call

    def resume_run(self, ctx: OperationContext, run_id: str, request: ResumeAgentRunRequest, *, idempotency_key: str | None = None, expected_etag: str | None = None, session: object | None = None) -> AgentRun:
        ctx = _ctx_actor(ctx)
        request_data = request.model_dump(mode="json") if hasattr(request, "model_dump") else getattr(request, "root", request).model_dump(mode="json")
        request_data["expected_etag"] = expected_etag
        repo = self._require_repo()
        with repo.transaction(session) as db:
            initial = repo.get_run(db, run_id)
            if initial is None:
                raise AssistantError(404, "RESOURCE_NOT_FOUND", "Agent Run 不存在或当前主体不可见")
            conversation = repo.lock_conversation(db, initial.conversation_id)
            if conversation is None or conversation.owner_id != ctx.actor.actor_id:
                raise AssistantError(404, "RESOURCE_NOT_FOUND", "Agent Run 不存在或当前主体不可见")
            self._access(ctx, conversation.project_id, self.access)
            run = repo.get_run(db, run_id)
            if run is None or run.conversation_id != conversation.id:
                raise AssistantError(404, "RESOURCE_NOT_FOUND", "Agent Run 不存在或当前主体不可见")
            replay = self._replay(ctx, "run.resume", run_id, idempotency_key, request_data, project_id=run.project_id, session=db)
            if replay is not None:
                return replay
            if expected_etag is not None and expected_etag != run.etag:
                raise AssistantError(412, "VERSION_CONFLICT", "Agent Run 版本已变化")
            self._project_from_run(run, ctx, self.access)
            reason = request_data.get("reason")
            if reason == "recovery":
                if run.status != "INTERRUPTED":
                    raise AssistantError(409, "RECOVERY_NOT_ALLOWED", "只有 INTERRUPTED Run 可恢复")
                # Recovery uses trusted server state and creates no duplicate
                # user message. The worker owns the new Attempt.
                recovery_attempt = Attempt(id=_id("attempt"), agent_run_id=run.id, attempt=run.attempt + 1, status="QUEUED", created_at=_now())
                repo.save_attempt(db, recovery_attempt)
                job_id = _run_job_id(run)
                if self.task_repository is not None and job_id is not None:
                    add_job_attempt_rows(self.task_repository, db, job_id=job_id, attempt=recovery_attempt.attempt, status=JobStatus.QUEUED)
                updated = run.model_copy(update={"status": "QUEUED", "attempt": run.attempt + 1, "revision": run.revision + 1, "updated_at": _now()})
                updated = updated.model_copy(update={"etag": self._etag(updated)})
                repo.save_run(db, updated)
                self._remember(ctx, "run.resume", run_id, idempotency_key, request_data, updated, project_id=run.project_id, session=db)
                return updated
            if reason != "confirmation":
                raise AssistantError(409, "STATE_CONFLICT", "Run 当前不在等待确认状态")
            confirmation_id = request_data.get("confirmation_id")
            if not confirmation_id:
                raise AssistantError(422, "VALIDATION_FAILED", "confirmation resume 缺少 confirmation_id")
            # Once the first request has committed, a concurrent retry reads a
            # terminal Run and must replay its durable result.  The repository
            # owns the lock and confirmation binding check for this path.
            if run.status != "WAITING_CONFIRMATION" or len(run.pending_actions) != 1:
                atomic_resume = getattr(repo, "resume_confirmation_atomic", None)
                if callable(atomic_resume):
                    try:
                        _, replayed_run, replayed_resume = atomic_resume(run.id, confirmation_id, now=_now(), session=db)
                    except (ValueError, AssistantError):
                        replayed_resume = False
                    if replayed_resume:
                        return replayed_run
                raise AssistantError(409, "STATE_CONFLICT", "Run 当前不在等待确认状态")
            pending = run.pending_actions[0]
            if pending.confirmation_id is not None and pending.confirmation_id != confirmation_id:
                raise AssistantError(409, "CONFIRMATION_INVALID", "确认未绑定到待执行工具")
            call = repo.get_tool_call(db, pending.tool_call_id)
            if call is None:
                raise AssistantError(409, "STATE_CONFLICT", "待执行工具调用不存在")
            if call.agent_run_id != run.id or call.status != "WAITING_CONFIRMATION" or call.pending_confirmation != pending:
                raise AssistantError(409, "CONFIRMATION_INVALID", "待执行工具绑定已变化")
            if call.arguments_hash is not None and call.arguments_hash != _hash(pending.normalized_args):
                raise AssistantError(409, "CONFIRMATION_INVALID", "待执行工具参数已变化")
            if call.binding_hash is not None and call.binding_hash != _binding_hash(pending):
                raise AssistantError(409, "CONFIRMATION_INVALID", "待执行工具绑定已变化")
            self._authorize_tool(ctx, call.tool_name)
            if self.confirmation is None:
                raise unavailable("Confirmation 校验端口未配置")
            validator = getattr(self.confirmation, "validate_binding", None) or getattr(self.confirmation, "validate", None)
            if not callable(validator):
                raise unavailable("Confirmation 校验端口未配置")
            try:
                validator(ctx, confirmation_id, pending)
            except TypeError:
                validator(ctx, confirmation_id, binding=pending)
            framework_row = None
            native_history = None
            native_tool_id = None
            if not getattr(repo, "is_test_only", False):
                from .persistence import AssistantFrameworkStateRow
                from .adapter import complete_deferred_history
                framework_row = db.scalar(select(AssistantFrameworkStateRow).where(
                    AssistantFrameworkStateRow.agent_run_id == run.id,
                ).with_for_update())
                state = framework_row.state if framework_row is not None else {}
                private_pending = state.get("pending_actions", []) if isinstance(state, dict) else []
                if (framework_row is None or framework_row.engine != "pydantic-ai"
                        or framework_row.project_id != run.project_id
                        or framework_row.conversation_id != run.conversation_id
                        or framework_row.config_hash != run.provider_configuration_hash
                        or not isinstance(state, dict)
                        or state.get("config_hash") != run.provider_configuration_hash
                        or not isinstance(private_pending, list)
                        or len(private_pending) != 1 or not isinstance(private_pending[0], dict)):
                    raise AssistantError(409, "RECOVERY_STATE_INVALID", "待确认的原生助手历史不可用")
                private_binding = private_pending[0]
                if (private_binding.get("tool_call_id") != call.id
                        or private_binding.get("confirmation_id") != confirmation_id
                        or private_binding.get("provider_tool_call_id") != call.provider_tool_call_id
                        or private_binding.get("normalized_args") != pending.normalized_args
                        or private_binding.get("revision") != pending.revision
                        or private_binding.get("config_hash") != pending.config_hash):
                    raise AssistantError(409, "CONFIRMATION_INVALID", "私有助手历史与确认绑定不匹配")
                native_tool_id = call.provider_tool_call_id
                native_history = state.get("history_state")
                native_binding = native_history.get("config_binding") if isinstance(native_history, dict) else None
                if (not isinstance(native_binding, dict)
                        or native_binding.get("model_id") != run.model_id
                        or native_binding.get("config_revision") != run.provider_configuration_revision
                        or native_binding.get("provider_configuration_hash") != run.provider_configuration_hash):
                    raise AssistantError(409, "RECOVERY_STATE_INVALID", "原生助手历史与运行配置不匹配")
                # Pure serialization preflight before the existing business
                # execution point; this discarded copy is never persisted.
                complete_deferred_history(native_history, native_tool_id, None)
            port = self._business_port(call.tool_name)
            # Re-run business validation against the current draft; no client history/results are accepted.
            try:
                port.validate(ctx, tool_name=call.tool_name, arguments=pending.normalized_args, resource_ref=pending.resource_ref)
            except APIError:
                raise
            except Exception as exc:
                raise AssistantError(422, "VALIDATION_FAILED", "业务工具参数校验失败") from exc
            now = _now()
            job_id = _run_job_id(run)
            atomic_resume = getattr(repo, "resume_confirmation_atomic", None)
            if not callable(atomic_resume):
                raise unavailable("Assistant resume 原子仓储端口未配置")
            try:
                resumed_attempt, resumed_run, replayed_resume = atomic_resume(run.id, confirmation_id, now=now, session=db)
            except ValueError as exc:
                raise AssistantError(409, "CONFIRMATION_INVALID", "确认绑定已变化") from exc
            if replayed_resume:
                return resumed_run
            next_attempt = int(resumed_attempt.attempt)
            resumed_attempt = resumed_attempt.model_copy(update={"status": "RUNNING", "started_at": now})
            repo.save_attempt(db, resumed_attempt)
            run = resumed_run
            try:
                result, replayed = self._execute_ledger(run_id=run.id, tool_name=call.tool_name, arguments=pending.normalized_args, target_revision={"revision": pending.revision, "config_hash": pending.config_hash, "resource_ref": pending.resource_ref.model_dump(mode="json")}, fn=lambda: port.execute(ctx, tool_name=call.tool_name, arguments=pending.normalized_args, resource_ref=pending.resource_ref, confirmation_id=confirmation_id))
            except APIError:
                repo.save_attempt(db, resumed_attempt.model_copy(update={"status": "FAILED", "finished_at": _now()}))
                raise
            except Exception as exc:
                repo.save_attempt(db, resumed_attempt.model_copy(update={"status": "FAILED", "finished_at": _now()}))
                raise AssistantError(503, "BUSINESS_SERVICE_FAILED", "业务服务执行失败", retryable=True) from exc
            resumed_attempt = resumed_attempt.model_copy(update={"status": "SUCCEEDED", "finished_at": _now()})
            if framework_row is not None:
                framework_row.state = {
                    **framework_row.state,
                    "history_state": complete_deferred_history(native_history, native_tool_id, _safe(result)),
                    "pending_actions": [],
                }
                framework_row.updated_at = _now()
                from .persistence import MessageRow
                if framework_row.stream_message_id:
                    streamed_message = db.scalar(select(MessageRow).where(
                        MessageRow.id == framework_row.stream_message_id,
                        MessageRow.agent_run_id == run.id,
                    ).with_for_update())
                    if streamed_message is not None:
                        streamed_message.status = "FINAL"
            completed_call = call.model_copy(update={"status": "SUCCEEDED", "confirmation_id": confirmation_id, "pending_confirmation": pending.model_copy(update={"confirmation_id": confirmation_id}), "result_summary": _safe(result), "started_at": now, "finished_at": now, "job_id": _safe(result).get("job_id") if isinstance(_safe(result), dict) else None})
            updated = run.model_copy(update={"status": "SUCCEEDED", "attempt": next_attempt, "pending_actions": [], "revision": run.revision + 1, "updated_at": now, "finished_at": now})
            updated = updated.model_copy(update={"etag": self._etag(updated)})
            repo.save_tool_call(db, completed_call)
            repo.save_attempt(db, resumed_attempt)
            repo.save_run(db, updated)
            if self.task_repository is not None and job_id is not None:
                sync_job_lifecycle(self.task_repository, db, job_id=job_id, status="SUCCEEDED", attempt=next_attempt, result_ref=_safe(result) if isinstance(_safe(result), dict) else None, finished_at=now)
            self._remember(ctx, "run.resume", run_id, idempotency_key, request_data, updated, project_id=run.project_id, session=db)
            return updated

    def tool_calls(self, ctx: OperationContext, *, run_id: str | None = None, conversation_id: str | None = None, limit: int = 50, cursor: str | None = None, session: object | None = None) -> list[ToolCall]:
        if run_id is None and conversation_id is None:
            raise AssistantError(400, "VALIDATION_FAILED", "必须指定 run_id 或 conversation_id")
        if run_id is not None:
            self.get_run(ctx, run_id, session=session)
        if conversation_id is not None:
            # Keep the direct service port subject to the same actor/project
            # checks as the HTTP adapter; callers must not rely on the router
            # having performed this lookup first.
            conversation = self.get_conversation(ctx, conversation_id, session=session)
            if run_id is not None:
                run = self.get_run(ctx, run_id, session=session)
                if run.conversation_id != conversation.id:
                    raise AssistantError(404, "RESOURCE_NOT_FOUND", "工具调用不存在或当前主体不可见")
        with self._tx(session) as db:
            requested = min(max(limit, 1), 200)
            marker = decode_cursor(cursor, "tool_calls", filters={"run_id": run_id, "conversation_id": conversation_id})
            values = self.repository.list_tool_calls(db, run_id=run_id, conversation_id=conversation_id, limit=200)
            values = sorted(values, key=lambda v: ((v.started_at or datetime.min.replace(tzinfo=timezone.utc)), v.id), reverse=True)
            if marker is not None:
                values = [v for v in values if ((v.started_at or datetime.min.replace(tzinfo=timezone.utc)).isoformat(), v.id) < tuple(marker)]
            return values[:requested]


__all__ = ["ASSISTANT_TOOL_PERMISSIONS", "DISALLOWED_ASSISTANT_TOOLS", "AssistantService", "transition_attempt", "transition_run"]
