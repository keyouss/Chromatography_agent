"""Application services for durable jobs and server-bound confirmations."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import json
from typing import Any, Callable, Generic, Mapping, Protocol, TypeVar
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select

from chrom_platform.core.dto import ActorContext, ResourceRef
from chrom_platform.core.idempotency import IdempotencyScope
from chrom_platform.infrastructure.models import (
    AuditEvent,
    Confirmation,
    IdempotencyRecord,
    Job,
    JobAttempt,
    JobEvent,
    OutboxEvent,
)

from .repository import SQLAlchemyTaskRepository, TaskRepository
from .schemas import (
    ConfirmationCreateRequest,
    ConfirmationPreview,
    ConfirmationPreviewRequest,
    ConfirmationView,
    JobAttemptView,
    JobCreateRequest,
    JobEventView,
    JobView,
)
from .state import ConfirmationStatus, InvalidTransition, JobStatus, idempotency_scope, job_action_flags, transition_job


class TaskError(Exception):
    """Safe service error which can be mapped by an HTTP adapter."""

    def __init__(self, status_code: int, code: str, message: str, *, retryable: bool = False, details: Mapping[str, Any] | None = None) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = dict(details or {})
        super().__init__(message)


class TaskPolicy(Protocol):
    """Server-owned policy port; clients never select these permissions."""

    def required_permission(self, operation: str, *, kind: str, resource_ref: Mapping[str, Any], action: str | None = None) -> str | None: ...

    def authorize_resource(self, ctx: OperationContext, target: "TargetSnapshot", *, action: str) -> bool: ...

    def retry_requires_confirmation(self, *, kind: str, resource_ref: Mapping[str, Any]) -> bool: ...

    def resolve_target(self, ctx: OperationContext, resource_ref: ResourceRef, action: str) -> "TargetSnapshot": ...

    def filter_visible_jobs(self, ctx: OperationContext, rows: list[Job]) -> list[Job]: ...


@dataclass(frozen=True)
class OperationContext:
    actor: ActorContext
    interaction: str = "human"
    request_id: str = "service-request"
    conversation_id: str | None = None
    agent_run_id: str | None = None
    tool_call_id: str | None = None


@dataclass(frozen=True)
class TargetSnapshot:
    """Server-resolved current state used by preview/create/consume."""

    project_id: str
    resource_ref: ResourceRef
    revision: int
    config_hash: str
    etag: str
    policy_version: str
    summary: list[dict[str, Any]] = field(default_factory=list)
    dependencies: list[dict[str, Any]] = field(default_factory=list)
    impact: dict[str, Any] = field(default_factory=dict)
    can_confirm: bool = True
    blockers: list[dict[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class ConfirmedJobSpec:
    project_id: str
    kind: str
    resource_ref: ResourceRef
    action: str
    cancelable: bool = True
    retryable: bool = False
    resume_mode: str = "none"
    deadline_at: datetime | None = None


T = TypeVar("T")


@dataclass(frozen=True)
class ServiceResult(Generic[T]):
    data: T
    replayed: bool = False
    status_code: int = 200
    execution_available: bool | None = None

    def __getattr__(self, name: str):
        return getattr(self.data, name)


@dataclass(frozen=True)
class EventPageResult:
    events: list[JobEventView]
    has_more: bool
    next_seq: str | None


@dataclass(frozen=True)
class JobEventStreamSnapshot:
    events: list[JobEventView]
    has_more: bool
    terminal: bool
    oldest_event: JobEventView | None


@dataclass(frozen=True)
class JobPageResult:
    jobs: list[JobView]
    next_cursor: str | None
    has_more: bool


@dataclass(frozen=True)
class AttemptPageResult:
    attempts: list[JobAttemptView]
    next_cursor: str | None
    has_more: bool


_DEFAULT_KINDS = frozenset(
    {
        "prediction",
        "batch_prediction",
        "training",
        "model_registration",
        "model_deployment",
        "data_import",
        "artifact_delete",
        "export",
        "validation",
        "agent_run",
    }
)
_CONFIRMATION_ACTIONS = frozenset(
    {
        "batch.submit",
        "batch.retry_failed",
        "data_import.commit",
        "training.submit",
        "job.retry",
        "model_registration.register",
        "model_deployment.publish",
        "resource.archive",
        "conversation.delete",
        "artifact.delete",
    }
)
_AUDITED_ACTIONS = _CONFIRMATION_ACTIONS | {
    "job.cancel",
    "job.retry",
    "settings.change",
    "config.change",
    "model.rollback",
    "resource.delete",
    "confirmation.revoke",
}
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _encode_cursor(value: Mapping[str, Any]) -> str:
    return base64.urlsafe_b64encode(_canonical(dict(value)).encode("utf-8")).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str | None, *, fields: set[str]) -> dict[str, Any] | None:
    if cursor is None:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except Exception as exc:
        raise TaskError(400, "VALIDATION_FAILED", "cursor 无效") from exc
    if not isinstance(value, dict) or set(value) != fields:
        raise TaskError(400, "VALIDATION_FAILED", "cursor 与分页资源不匹配")
    return value


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _check_access(ctx: OperationContext, project_id: str, permission: str | None = None) -> None:
    if not ctx.actor.can_access_project(project_id):
        raise TaskError(404, "RESOURCE_NOT_FOUND", "项目不存在或当前主体不可见")
    if permission and not ctx.actor.can(permission):
        raise TaskError(403, "PERMISSION_DENIED", "当前主体无权执行该操作")


def _policy_permission(policy: TaskPolicy | None, ctx: OperationContext, *, operation: str, kind: str, resource_ref: Mapping[str, Any], action: str | None = None) -> None:
    if policy is None:
        raise TaskError(503, "POLICY_UNAVAILABLE", "任务权限策略未配置", retryable=True)
    permission = policy.required_permission(operation, kind=kind, resource_ref=resource_ref, action=action)
    if not permission or not ctx.actor.can(permission):
        raise TaskError(403, "PERMISSION_DENIED", "当前主体无权执行该操作")


def _policy_target(policy: TaskPolicy | None, ctx: OperationContext, target: TargetSnapshot, *, action: str) -> None:
    if policy is None:
        raise TaskError(503, "POLICY_UNAVAILABLE", "资源权限策略未配置", retryable=True)
    if not policy.authorize_resource(ctx, target, action=action):
        raise TaskError(404, "RESOURCE_NOT_FOUND", "资源不存在或当前主体不可见")


def _policy_job(policy: TaskPolicy | None, ctx: OperationContext, job: Job) -> None:
    if policy is None:
        raise TaskError(503, "POLICY_UNAVAILABLE", "任务权限策略未配置", retryable=True)
    authorize_job = getattr(policy, "authorize_job", None)
    if authorize_job is not None:
        if authorize_job(ctx, job):
            return
        if not ctx.actor.can_access_project(job.project_id):
            raise TaskError(404, "RESOURCE_NOT_FOUND", "任务不存在或当前主体不可见")
        raise TaskError(403, "PERMISSION_DENIED", "当前主体无权读取该任务")
    _policy_permission(policy, ctx, operation="read", kind=job.kind, resource_ref=job.resource_ref)


def _require_if_match(expected: str | None, current: str) -> None:
    if expected is None:
        raise TaskError(428, "IF_MATCH_REQUIRED", "该操作需要 If-Match")
    if expected != current:
        raise TaskError(412, "VERSION_CONFLICT", "资源版本已变化")


def _scope(ctx: OperationContext, project_id: str, route: str, key: str | None) -> IdempotencyScope:
    if not key:
        raise TaskError(400, "IDEMPOTENCY_KEY_REQUIRED", "缺少 Idempotency-Key")
    try:
        values = idempotency_scope(
            actor_id=ctx.actor.actor_id,
            project_id=project_id,
            method="POST",
            route_identity=route,
            key=key,
        )
    except ValueError as exc:
        raise TaskError(400, "VALIDATION_FAILED", str(exc)) from exc
    return IdempotencyScope(*values)


def _job_etag(job: Job) -> str:
    public_result_ref = _public_job_result_ref(job)
    return _hash(
        {
            "id": job.id,
            "project_id": job.project_id,
            "status": JobStatus(job.status),
            "attempt": job.attempt,
            "revision": job.revision,
            "progress": job.progress,
            "result_ref": public_result_ref.model_dump(mode="json") if public_result_ref is not None else None,
            "error": _public_job_error(job.status, job.error),
        }
    )


def _public_job_result_ref(job: Job) -> ResourceRef | None:
    """Keep the strict public ResourceRef shape after old assistant writes.

    Older assistant workers stored the runtime result object in the shared job
    result_ref column.  The durable assistant message is the result payload;
    the shared Job contract exposes only its resource reference.
    """
    if job.result_ref is None:
        return None
    try:
        return ResourceRef.model_validate(job.result_ref)
    except ValidationError:
        if str(job.kind) == "assistant":
            try:
                return ResourceRef.model_validate(job.resource_ref)
            except ValidationError:
                return None
        return None


def _public_job_error(status: str, error: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    return None if JobStatus(status) is JobStatus.SUCCEEDED else error


def _job_view(job: Job) -> JobView:
    status = JobStatus(job.status)
    cancelable, retryable = job_action_flags(
        status, cancelable=job.cancelable, retryable=job.retryable
    )
    return JobView.model_validate(
        {
            "id": job.id,
            "project_id": job.project_id,
            "owner_id": job.owner_id,
            "kind": job.kind,
            "resource_ref": job.resource_ref,
            "status": status,
            "attempt": job.attempt,
            "progress": job.progress,
            "cancelable": cancelable,
            "retryable": retryable,
            "resume_mode": job.resume_mode,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "deadline_at": job.deadline_at,
            "result_ref": _public_job_result_ref(job),
            "error": _public_job_error(status, job.error),
            "revision": job.revision,
            "etag": job.etag,
        }
    )


def _attempt_view(row: JobAttempt) -> JobAttemptView:
    status = JobStatus(row.status)
    result_ref = None
    if row.result_ref is not None:
        try:
            result_ref = ResourceRef.model_validate(row.result_ref)
        except ValidationError:
            result_ref = None
    return JobAttemptView.model_validate(
        {
            "id": row.id,
            "job_id": row.job_id,
            "attempt": row.attempt,
            "status": status,
            "resume_mode": row.resume_mode,
            "checkpoint_ref": row.checkpoint_ref,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
            "error": _public_job_error(status, row.error),
            "result_ref": result_ref,
        }
    )


def _event_view(row: JobEvent) -> JobEventView:
    data = row.data
    if row.type == "model_package.termination":
        # Physical process identities stay in the trusted server-side receipt.
        # Both paged events and SSE use this projection.
        receipt = data if isinstance(data, dict) else {}
        termination = receipt.get("termination")
        termination = termination if isinstance(termination, dict) else {}
        data = {
            "stop_confirmed": (
                termination.get("confirmed") is True
                and termination.get("cleanup_completed") is True
                and termination.get("output_drained") is True
            )
        }
        attempt = receipt.get("attempt")
        if type(attempt) is int and attempt > 0:
            data["attempt"] = attempt
    return JobEventView.model_validate(
        {
            "id": row.id,
            "job_id": row.job_id,
            "stream_id": row.stream_id,
            "seq": str(row.seq),
            "type": row.type,
            "schema_version": row.schema_version,
            "data": data,
            "timestamp": row.timestamp,
        }
    )


def _confirmation_view(row: Confirmation) -> ConfirmationView:
    return ConfirmationView.model_validate(
        {
            "id": row.id,
            "project_id": row.project_id,
            "actor_id": row.actor_id,
            "resource_ref": row.resource_ref,
            "action": row.action,
            "revision": row.revision,
            "config_hash": row.config_hash,
            "policy_version": row.policy_version,
            "summary_snapshot": row.summary_snapshot,
            "status": ConfirmationStatus(row.status),
            "created_at": row.created_at,
            "expires_at": row.expires_at,
            "consumed_at": row.consumed_at,
            "consumed_result_ref": row.consumed_result_ref,
        }
    )


def _job_view_from_data(value: Mapping[str, Any]) -> JobView:
    # JSON validation performs the same RFC3339/enum decoding as FastAPI,
    # while the public models remain strict for Python-side callers.
    view = JobView.model_validate_json(_canonical(dict(value)))
    cancelable, retryable = job_action_flags(
        view.status, cancelable=view.cancelable, retryable=view.retryable
    )
    return view.model_copy(update={"cancelable": cancelable, "retryable": retryable})


def _confirmation_view_from_data(value: Mapping[str, Any]) -> ConfirmationView:
    return ConfirmationView.model_validate_json(_canonical(dict(value)))


def _confirmation_binding_hash(row: Confirmation) -> str:
    return _hash({
        "project_id": row.project_id,
        "actor_id": row.actor_id,
        "resource_ref": row.resource_ref,
        "action": row.action,
        "revision": row.revision,
        "config_hash": row.config_hash,
        "policy_version": row.policy_version,
    })


def _validate_target_binding(row: Confirmation, target: TargetSnapshot, ctx: OperationContext, action: str) -> None:
    if row.project_id != target.project_id or row.actor_id != ctx.actor.actor_id or row.action != action or row.resource_ref != target.resource_ref.model_dump(mode="json") or row.revision != target.revision or row.config_hash != target.config_hash or row.policy_version != target.policy_version:
        raise TaskError(409, "CONFIRMATION_INVALID", "确认绑定已失效")
    if row.binding_hash != _confirmation_binding_hash(row):
        raise TaskError(409, "CONFIRMATION_INVALID", "确认绑定摘要无效")


def _idempotency_get(repo: TaskRepository, session, scope: IdempotencyScope, payload_hash: str) -> tuple[IdempotencyRecord | None, bool]:
    row = repo.get_idempotency(session, scope, for_update=True)
    if row is None:
        row = IdempotencyRecord(
            id=_id("idem"),
            actor_id=scope.actor_id,
            project_id=scope.project_id,
            method=scope.method,
            route_identity=scope.route_identity,
            key=scope.key,
            payload_hash=payload_hash,
            status="IN_PROGRESS",
            expires_at=_now() + timedelta(hours=24),
        )
        try:
            # Flush inside a savepoint so a duplicate-key race never rolls
            # back a caller-owned outer transaction.
            nested_factory = getattr(session, "begin_nested", None)
            if nested_factory is None:
                repo.add(session, row)
                repo.flush(session)
            else:
                with nested_factory():
                    repo.add(session, row)
                    repo.flush(session)
            return row, False
        except IntegrityError:
            # The savepoint has already been rolled back by its context
            # manager.  Never call session.rollback() here: the caller may be
            # composing Job + Outbox + audit in an outer transaction.
            row = repo.get_idempotency(session, scope, for_update=True)
            if row is None:
                raise TaskError(503, "DEPENDENCY_UNAVAILABLE", "幂等记录竞争后无法重读", retryable=True)
    now = _now()
    expires_at = _as_utc(row.expires_at) if row.expires_at is not None else now
    if now >= expires_at:
        row.payload_hash = payload_hash
        row.status = "IN_PROGRESS"
        row.result_resource_ref = None
        row.result_status_code = None
        row.error_code = None
        row.completed_at = None
        row.expires_at = now + timedelta(hours=24)
        return row, False
    if row.payload_hash != payload_hash:
        raise TaskError(409, "IDEMPOTENCY_KEY_REUSED", "幂等键已用于其他请求")
    if row.status == "FAILED":
        # A failed attempt is retryable with the same semantic request.  A
        # different payload was rejected above while the retention window is
        # still active.
        row.status = "IN_PROGRESS"
        row.result_resource_ref = None
        row.result_status_code = None
        row.error_code = None
        row.completed_at = None
        return row, False
    if row.status == "IN_PROGRESS":
        raise TaskError(409, "OPERATION_IN_PROGRESS", "相同幂等请求正在处理中", retryable=True)
    if row.status == "COMPLETED" and row.result_resource_ref is not None:
        return row, True
    raise TaskError(409, "STATE_CONFLICT", "幂等记录不可重放")


def _idempotency_complete(repo: TaskRepository, row: IdempotencyRecord, response: Mapping[str, Any], status_code: int) -> None:
    repo.complete_idempotency(row, status="COMPLETED", result=dict(response), status_code=status_code, completed_at=_now())


def _add_job_rows(repo: TaskRepository, session, *, project_id: str, owner_id: str, kind: str, resource_ref: ResourceRef, cancelable: bool, retryable: bool, resume_mode: str, deadline_at: datetime | None, job_id: str | None = None, dispatch_id: str | None = None) -> Job:
    now = _now()
    job = Job(
        id=job_id or _id("job"),
        project_id=project_id,
        owner_id=owner_id,
        kind=kind,
        resource_ref=resource_ref.model_dump(mode="json"),
        status=JobStatus.QUEUED.value,
        attempt=1,
        progress={"completed": 0, "total": None, "unit": "items", "stage": None},
        cancelable=cancelable,
        retryable=retryable,
        resume_mode=resume_mode,
        created_at=now,
        updated_at=now,
        deadline_at=deadline_at,
        revision=1,
        etag="pending",
    )
    job.etag = _job_etag(job)
    attempt = JobAttempt(id=_id("attempt"), job_id=job.id, attempt=1, status=JobStatus.QUEUED.value, resume_mode=resume_mode)
    event = JobEvent(
        id=_id("event"), job_id=job.id, stream_id=job.id, seq=0, type="job.queued", schema_version="1.0",
        data={"status": JobStatus.QUEUED.value, "attempt": 1}, timestamp=now,
    )
    dispatch_id = dispatch_id or _id("dispatch")
    outbox = OutboxEvent(
        dispatch_id=dispatch_id, aggregate_type="job", aggregate_id=job.id, event_type="job.execute",
        payload={"job_id": job.id, "attempt": 1, "dispatch_id": dispatch_id}, status="PENDING", attempts=0,
        available_at=now, created_at=now,
    )
    for row in (job, attempt, event, outbox):
        repo.add(session, row)
    return job


def add_job_rows(repo: TaskRepository, session, *, project_id: str, owner_id: str, kind: str, resource_ref: ResourceRef, cancelable: bool, retryable: bool, resume_mode: str, deadline_at: datetime | None, job_id: str | None = None, dispatch_id: str | None = None) -> Job:
    """Stable internal port for modules that create a platform Job inline.

    The caller owns the surrounding transaction.  This intentionally delegates
    to the original helper so existing JobService/ConfirmationService calls
    retain exactly the same row and outbox semantics.
    """
    return _add_job_rows(
        repo,
        session,
        project_id=project_id,
        owner_id=owner_id,
        kind=kind,
        resource_ref=resource_ref,
        cancelable=cancelable,
        retryable=retryable,
        resume_mode=resume_mode,
        deadline_at=deadline_at,
        job_id=job_id,
        dispatch_id=dispatch_id,
    )


def add_job_attempt_rows(
    repo: TaskRepository,
    session,
    *,
    job_id: str,
    attempt: int,
    status: JobStatus = JobStatus.QUEUED,
    resume_mode: str | None = None,
    dispatch_id: str | None = None,
) -> tuple[Job, str]:
    """Append one canonical attempt, event and execute outbox atomically."""
    if attempt < 1:
        raise TaskError(400, "VALIDATION_FAILED", "attempt 必须为正整数")
    job = repo.get_job(session, job_id, for_update=True)
    if job is None:
        raise TaskError(404, "RESOURCE_NOT_FOUND", "任务不存在")
    existing = session.scalar(select(JobAttempt).where(JobAttempt.job_id == job_id, JobAttempt.attempt == attempt))
    if existing is not None:
        outbox = next((row for row in session.scalars(select(OutboxEvent).where(OutboxEvent.aggregate_type == "job", OutboxEvent.aggregate_id == job_id, OutboxEvent.event_type == "job.execute")) if (row.payload or {}).get("attempt") == attempt), None)
        if outbox is None:
            # The Job row lock above serializes recovery/retry callers.  An
            # existing attempt is otherwise immutable, but its execute
            # outbox may have been lost during an interrupted transaction or
            # an older migration.  Repair exactly that missing transport row;
            # never append another attempt/event or replace a known dispatch.
            now = _now()
            dispatch = dispatch_id or _id("dispatch")
            session.add(OutboxEvent(
                dispatch_id=dispatch,
                aggregate_type="job",
                aggregate_id=job_id,
                event_type="job.execute",
                payload={"job_id": job_id, "attempt": attempt, "dispatch_id": dispatch},
                status="PENDING",
                attempts=0,
                available_at=now,
                created_at=now,
            ))
            outbox = dispatch
        return job, outbox.dispatch_id if hasattr(outbox, "dispatch_id") else outbox
    now = _now()
    dispatch = dispatch_id or _id("dispatch")
    job.attempt = attempt
    job.status = status.value
    job.resume_mode = resume_mode or job.resume_mode
    job.updated_at = now
    job.revision += 1
    job.etag = _job_etag(job)
    event_type = "job.queued" if status is JobStatus.QUEUED else f"job.{status.value.lower()}"
    seq = repo.next_event_seq(session, job_id)
    repo.add(session, JobAttempt(id=_id("attempt"), job_id=job_id, attempt=attempt, status=status.value, resume_mode=job.resume_mode))
    repo.add(session, JobEvent(id=_id("event"), job_id=job_id, stream_id=job_id, seq=seq, type=event_type, schema_version="1.0", data={"status": status.value, "attempt": attempt}, timestamp=now))
    repo.add(session, OutboxEvent(dispatch_id=dispatch, aggregate_type="job", aggregate_id=job_id, event_type="job.execute", payload={"job_id": job_id, "attempt": attempt, "dispatch_id": dispatch}, status="PENDING", attempts=0, available_at=now, created_at=now))
    return job, dispatch


def sync_job_lifecycle(
    repo: TaskRepository,
    session,
    *,
    job_id: str,
    status: JobStatus | str,
    attempt: int | None = None,
    result_ref: Mapping[str, Any] | None = None,
    error: Mapping[str, Any] | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    expected_attempt: int | None = None,
    fencing_token: int | None = None,
    dispatch_id: str | None = None,
    expected_dispatch_id: str | None = None,
    resource_ref: Mapping[str, Any] | None = None,
) -> Job | None:
    """CAS-style projection used by module workers for shared task queries.

    ``attempt`` is an ownership fence.  A late delivery from an older attempt
    is a no-op, including its terminal event, so a retry can never be
    overwritten by the old worker.  The caller owns the transaction; Job,
    JobAttempt and JobEvent are changed together.
    """
    job = repo.get_job(session, job_id, for_update=True)
    if job is None:
        return None
    target = status.value if isinstance(status, JobStatus) else str(status)
    current_attempt = int(job.attempt)
    expected = expected_attempt if expected_attempt is not None else attempt
    if expected is not None and int(expected) != current_attempt:
        return None
    attempt_row = session.scalar(
        select(JobAttempt).where(
            JobAttempt.job_id == job_id, JobAttempt.attempt == current_attempt
        ).with_for_update().execution_options(populate_existing=True)
    )
    if attempt_row is None:
        # A platform Job without its canonical attempt is not executable.
        return None
    if fencing_token is not None and attempt_row.fencing_token != fencing_token:
        return None
    expected_dispatch = expected_dispatch_id if expected_dispatch_id is not None else dispatch_id
    if expected_dispatch is not None:
        dispatches = session.scalars(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_type == "job",
                OutboxEvent.aggregate_id == job_id,
                OutboxEvent.event_type == "job.execute",
            )
        ).all()
        if not any(
            row.dispatch_id == expected_dispatch
            and int((row.payload or {}).get("attempt", -1)) == current_attempt
            for row in dispatches
        ):
            return None
    if resource_ref is not None and dict(job.resource_ref or {}) != dict(resource_ref):
        return None
    old = job.status
    terminal = {
        JobStatus.SUCCEEDED.value,
        JobStatus.PARTIAL.value,
        JobStatus.FAILED.value,
        JobStatus.CANCELED.value,
        JobStatus.TIMED_OUT.value,
        JobStatus.INTERRUPTED.value,
    }
    if old == target:
        # JobService may have committed a queued cancellation before the
        # model-center cancel consumer projects it to the current attempt.
        # Repair only that exact, fenced, never-started case; do not append a
        # duplicate JobEvent or touch a later attempt.
        if target == JobStatus.CANCELED.value and attempt_row.status == JobStatus.QUEUED.value:
            attempt_row.status = JobStatus.CANCELED.value
            attempt_row.error = dict(error) if error is not None else attempt_row.error
            attempt_row.result_ref = dict(result_ref) if result_ref is not None else attempt_row.result_ref
            attempt_row.finished_at = finished_at or job.finished_at or _now()
            session.flush()
        return job
    if old in terminal:
        return None
    if attempt is not None and int(attempt) != current_attempt:
        return None
    try:
        transition_job(
            old,
            target,
            actual_finished=bool(result_ref is not None),
        )
    except InvalidTransition:
        return None
    job.status = target
    job.result_ref = dict(result_ref) if result_ref is not None else job.result_ref
    job.error = dict(error) if error is not None else job.error
    if started_at is not None:
        job.started_at = started_at
    if finished_at is not None:
        job.finished_at = finished_at
    job.updated_at = _now()
    if old != target:
        job.revision += 1
        job.etag = _job_etag(job)
        attempt_row.status = target
        attempt_row.error = dict(error) if error is not None else attempt_row.error
        attempt_row.result_ref = dict(result_ref) if result_ref is not None else attempt_row.result_ref
        attempt_row.started_at = started_at or attempt_row.started_at
        attempt_row.finished_at = finished_at or attempt_row.finished_at
        seq = repo.next_event_seq(session, job_id)
        if seq == 0:
            current_seq = session.scalar(select(JobEvent.seq).where(JobEvent.job_id == job_id).order_by(JobEvent.seq.desc()).limit(1))
            if current_seq is not None:
                seq = int(current_seq) + 1
        repo.add(session, JobEvent(id=_id("event"), job_id=job_id, stream_id=job_id, seq=seq, type=f"job.{target.lower()}", schema_version="1.0", data={"status": target, "attempt": job.attempt}, timestamp=_now()))
        session.flush()
    return job


def _add_audit(repo: TaskRepository, session, *, ctx: OperationContext, action: str, project_id: str, resource_ref: Mapping[str, Any], confirmation_id: str | None, config_hash: str | None, job_id: str | None, result: str = "SUCCEEDED", before_summary: Mapping[str, Any] | None = None, after_summary: Mapping[str, Any] | None = None) -> None:
    if action not in _AUDITED_ACTIONS:
        raise TaskError(400, "VALIDATION_FAILED", "审计动作不在白名单中")
    resource_type = resource_ref.get("type") if isinstance(resource_ref, Mapping) else None
    resource_id = resource_ref.get("id") if isinstance(resource_ref, Mapping) else None
    repo.add(
        session,
        AuditEvent(
            id=_id("audit"), project_id=project_id, actor_id=ctx.actor.actor_id,
            auth_method=ctx.actor.auth_method, interaction=ctx.interaction, request_id=ctx.request_id,
            action=action, resource_type=resource_type, resource_id=resource_id,
            resource_ref=dict(resource_ref), before_summary=dict(before_summary or {}), after_summary=dict(after_summary or {}), confirmation_id=confirmation_id,
            config_hash=config_hash, job_id=job_id, result=result, timestamp=_now(),
        ),
    )


class JobService:
    def __init__(self, repository: TaskRepository, *, policy: TaskPolicy | None = None, confirmation_service: "ConfirmationService | None" = None, allowed_kinds: set[str] | frozenset[str] | None = None, queue_available: bool = False, lifecycle_coordinator: object | None = None) -> None:
        self.repository = repository
        self.policy = policy
        self.confirmation_service = confirmation_service
        self.allowed_kinds = frozenset(allowed_kinds or _DEFAULT_KINDS)
        self.queue_available = queue_available
        self.lifecycle_coordinator = lifecycle_coordinator

    def _validate_kind(self, kind: str) -> None:
        if kind not in self.allowed_kinds:
            raise TaskError(503, "CAPABILITY_UNAVAILABLE", "该任务类型未注册固定执行能力")

    def create(self, ctx: OperationContext, project_id: str, request: JobCreateRequest, *, idempotency_key: str | None, session=None) -> ServiceResult[JobView]:
        # Creation is an internal application-service operation.  The
        # The business module authorizes its own action; task_core does not
        # invent a separate public task-creation permission.
        _check_access(ctx, project_id)
        self._validate_kind(request.kind)
        scope = _scope(ctx, project_id, "/api/v1/jobs", idempotency_key)
        payload_hash = _hash({"project_id": project_id, "request": request.model_dump(mode="json")})
        with self.repository.transaction(session) as db:
            idem, replayed = _idempotency_get(self.repository, db, scope, payload_hash)
            if replayed:
                return ServiceResult(_job_view_from_data(idem.result_resource_ref), replayed=True, status_code=idem.result_status_code or 202, execution_available=self.queue_available)
            job = _add_job_rows(self.repository, db, project_id=project_id, owner_id=ctx.actor.actor_id, kind=request.kind, resource_ref=request.resource_ref, cancelable=request.cancelable, retryable=request.retryable, resume_mode=request.resume_mode, deadline_at=request.deadline_at)
            view = _job_view(job)
            _idempotency_complete(self.repository, idem, view.model_dump(mode="json"), 202)
            return ServiceResult(view, status_code=202, execution_available=self.queue_available)

    def get(self, ctx: OperationContext, job_id: str, *, session=None) -> JobView:
        with self.repository.transaction(session) as db:
            job = self.repository.get_job(db, job_id)
            if job is None:
                raise TaskError(404, "RESOURCE_NOT_FOUND", "任务不存在或当前主体不可见")
            _check_access(ctx, job.project_id)
            _policy_job(self.policy, ctx, job)
            return _job_view(job)

    def list_page(self, ctx: OperationContext, project_id: str, *, status: str | None = None, kind: str | None = None, limit: int = 50, cursor: str | None = None, session=None) -> JobPageResult:
        if limit < 1 or limit > 200:
            raise TaskError(400, "VALIDATION_FAILED", "limit 必须在 1 到 200 之间")
        _check_access(ctx, project_id)
        if self.policy is None:
            raise TaskError(503, "POLICY_UNAVAILABLE", "任务权限策略未配置", retryable=True)
        decoded = _decode_cursor(cursor, fields={"project_id", "status", "kind", "created_at", "id"})
        if decoded is not None and (decoded["project_id"], decoded["status"], decoded["kind"]) != (project_id, status or "", kind or ""):
            raise TaskError(400, "VALIDATION_FAILED", "cursor 与筛选条件不匹配")
        after_created_at = datetime.fromisoformat(decoded["created_at"]) if decoded else None
        after_id = decoded["id"] if decoded else None
        with self.repository.transaction(session) as db:
            filter_visible = getattr(self.policy, "filter_visible_jobs", None)
            visible_rows = []
            chunk_limit = limit + 1
            exhausted = False
            while len(visible_rows) < limit + 1 and not exhausted:
                rows = self.repository.list_jobs(db, project_id, status=status, kind=kind, limit=chunk_limit, after_created_at=after_created_at, after_id=after_id)
                if not rows:
                    break
                raw_last = rows[-1]
                exhausted = len(rows) < chunk_limit
                if filter_visible is not None:
                    visible_rows.extend(filter_visible(ctx, rows))
                else:
                    for row in rows:
                        try:
                            _policy_permission(self.policy, ctx, operation="read", kind=row.kind, resource_ref=row.resource_ref)
                        except TaskError as exc:
                            if exc.status_code == 403:
                                continue
                            raise
                        visible_rows.append(row)
                if not exhausted:
                    new_after_created_at, new_after_id = raw_last.created_at, raw_last.id
                    if (new_after_created_at, new_after_id) == (after_created_at, after_id):
                        raise TaskError(503, "DEPENDENCY_UNAVAILABLE", "任务分页游标未推进", retryable=True)
                    after_created_at, after_id = new_after_created_at, new_after_id
            has_more = len(visible_rows) > limit
            rows = visible_rows[:limit]
            views = [_job_view(row) for row in rows]
            next_cursor = _encode_cursor({"project_id": project_id, "status": status or "", "kind": kind or "", "created_at": _as_utc(rows[-1].created_at).isoformat(), "id": rows[-1].id}) if has_more and rows else None
            return JobPageResult(jobs=views, next_cursor=next_cursor, has_more=has_more)

    def list(self, ctx: OperationContext, project_id: str, *, status: str | None = None, kind: str | None = None, session=None) -> list[JobView]:
        return self.list_page(ctx, project_id, status=status, kind=kind, limit=200, session=session).jobs

    def attempts_page(self, ctx: OperationContext, job_id: str, *, limit: int = 50, cursor: str | None = None, session=None) -> AttemptPageResult:
        if limit < 1 or limit > 200:
            raise TaskError(400, "VALIDATION_FAILED", "limit 必须在 1 到 200 之间")
        job = self.get(ctx, job_id, session=session)
        decoded = _decode_cursor(cursor, fields={"job_id", "attempt", "id"})
        if decoded is not None and decoded["job_id"] != job.id:
            raise TaskError(400, "VALIDATION_FAILED", "cursor 与任务不匹配")
        with self.repository.transaction(session) as db:
            rows = self.repository.get_attempts(db, job.id, limit=limit + 1, after_attempt=int(decoded["attempt"]) if decoded else None, after_id=decoded["id"] if decoded else None)
            has_more = len(rows) > limit
            rows = rows[:limit]
            views = [_attempt_view(row) for row in rows]
            next_cursor = _encode_cursor({"job_id": job.id, "attempt": rows[-1].attempt, "id": rows[-1].id}) if has_more and rows else None
            return AttemptPageResult(attempts=views, next_cursor=next_cursor, has_more=has_more)

    def attempts(self, ctx: OperationContext, job_id: str, *, session=None) -> list[JobAttemptView]:
        return self.attempts_page(ctx, job_id, limit=200, session=session).attempts

    def events(self, ctx: OperationContext, job_id: str, *, after_seq: int = -1, limit: int = 100, session=None) -> list[JobEventView]:
        if limit < 1 or limit > 200:
            raise TaskError(400, "VALIDATION_FAILED", "limit 必须在 1 到 200 之间")
        job = self.get(ctx, job_id, session=session)
        with self.repository.transaction(session) as db:
            return [_event_view(row) for row in self.repository.get_events(db, job.id, after_seq, limit)]

    def events_page(self, ctx: OperationContext, job_id: str, *, after_seq: int = -1, limit: int = 100, session=None) -> EventPageResult:
        if limit < 1 or limit > 200:
            raise TaskError(400, "VALIDATION_FAILED", "limit 必须在 1 到 200 之间")
        job = self.get(ctx, job_id, session=session)
        with self.repository.transaction(session) as db:
            rows = self.repository.get_events(db, job.id, after_seq, limit + 1)
            has_more = len(rows) > limit
            events = [_event_view(row) for row in rows[:limit]]
            return EventPageResult(events=events, has_more=has_more, next_seq=events[-1].seq if events else None)

    def event_stream_snapshot(self, ctx: OperationContext, job_id: str, *, after_seq: int, limit: int = 100, session=None) -> JobEventStreamSnapshot:
        """Read an authorized, short-lived job/event snapshot for SSE polling.

        Job status and its current event page share one database transaction so
        an SSE client cannot observe a terminal job while the corresponding
        committed tail event is still missing from that response.
        """
        if after_seq < -1 or after_seq > (1 << 63) - 1 or limit < 1 or limit > 200:
            raise TaskError(400, "VALIDATION_FAILED", "任务事件游标或limit无效")
        with self.repository.transaction(session) as db:
            job = self.repository.get_job(db, job_id)
            if job is None:
                raise TaskError(404, "RESOURCE_NOT_FOUND", "任务不存在或当前主体不可见")
            _check_access(ctx, job.project_id)
            _policy_job(self.policy, ctx, job)
            rows = self.repository.get_events(db, job.id, after_seq, limit + 1)
            oldest_rows = self.repository.get_events(db, job.id, -1, 1)
            has_more = len(rows) > limit
            events = [_event_view(row) for row in rows[:limit]]
            oldest = _event_view(oldest_rows[0]) if oldest_rows else None
            terminal = JobStatus(job.status) in {
                JobStatus.SUCCEEDED, JobStatus.PARTIAL, JobStatus.FAILED,
                JobStatus.CANCELED, JobStatus.TIMED_OUT, JobStatus.INTERRUPTED,
            }
            return JobEventStreamSnapshot(events=events, has_more=has_more, terminal=terminal, oldest_event=oldest)

    def _mutate(self, ctx: OperationContext, job_id: str, *, action: str, if_match: str | None, idempotency_key: str | None, reason: str | None = None, retry: bool = False, confirmation_id: str | None = None, session=None) -> ServiceResult[JobView]:
        with self.repository.transaction(session) as db:
            job = self.repository.get_job(db, job_id, for_update=True)
            if job is None:
                raise TaskError(404, "RESOURCE_NOT_FOUND", "任务不存在或当前主体不可见")
            _check_access(ctx, job.project_id)
            operation = "cancel" if action == "job.cancel" else "retry"
            _policy_permission(self.policy, ctx, operation=operation, kind=job.kind, resource_ref=job.resource_ref)
            scope = _scope(ctx, job.project_id, f"/api/v1/jobs/{job_id}/{action.rsplit('.', 1)[1]}", idempotency_key)
            payload_hash = _hash({"job_id": job_id, "action": action, "reason": reason, "if_match": if_match, "confirmation_id": confirmation_id})
            idem, replayed = _idempotency_get(self.repository, db, scope, payload_hash)
            if replayed:
                return ServiceResult(_job_view_from_data(idem.result_resource_ref), replayed=True, status_code=idem.result_status_code or 202)
            requires_confirmation = bool(retry and self.policy.retry_requires_confirmation(kind=job.kind, resource_ref=job.resource_ref)) if self.policy is not None else False
            if retry and self.policy is None:
                raise TaskError(503, "POLICY_UNAVAILABLE", "任务权限策略未配置", retryable=True)
            if requires_confirmation:
                if not confirmation_id:
                    raise TaskError(409, "CONFIRMATION_REQUIRED", "该重试操作需要有效 Confirmation")
                if self.confirmation_service is None:
                    raise TaskError(503, "POLICY_UNAVAILABLE", "Confirmation 策略未配置", retryable=True)
                target = self.policy.resolve_target(ctx, ResourceRef.model_validate(job.resource_ref), "job.retry")
                self.confirmation_service.validate(ctx, confirmation_id, target=target, action="job.retry", session=db)
            _require_if_match(if_match, job.etag)
            old_status = JobStatus(job.status)
            try:
                if retry:
                    if not job.retryable:
                        raise TaskError(409, "STATE_CONFLICT", "任务当前不可重试")
                    transition_job(old_status, JobStatus.QUEUED, retry=True)
                    job.attempt += 1
                    attempt_no = job.attempt
                    job.status = JobStatus.QUEUED.value
                    job.updated_at = _now()
                    self.repository.add(db, JobAttempt(id=_id("attempt"), job_id=job.id, attempt=attempt_no, status=JobStatus.QUEUED.value, resume_mode=job.resume_mode))
                    event_type = "job.retry_queued"
                else:
                    if not job.cancelable:
                        raise TaskError(409, "STATE_CONFLICT", "任务当前不可取消")
                    target = JobStatus.CANCELED if old_status is JobStatus.QUEUED else JobStatus.CANCEL_REQUESTED
                    transition_job(old_status, target)
                    job.status = target.value
                    job.updated_at = _now()
                    attempt_no = job.attempt
                    event_type = "job.cancel_requested" if target is JobStatus.CANCEL_REQUESTED else "job.canceled"
                job.revision += 1
                job.etag = _job_etag(job)
                seq = 1
                if hasattr(self.repository, "next_event_seq"):
                    seq = self.repository.next_event_seq(db, job.id)
                self.repository.add(db, JobEvent(id=_id("event"), job_id=job.id, stream_id=job.id, seq=seq, type=event_type, schema_version="1.0", data={"status": job.status, "attempt": attempt_no, "reason": reason}, timestamp=_now()))
                dispatch_id = _id("dispatch")
                self.repository.add(db, OutboxEvent(dispatch_id=dispatch_id, aggregate_type="job", aggregate_id=job.id, event_type="job.cancel" if not retry else "job.execute", payload={"job_id": job.id, "attempt": attempt_no, "dispatch_id": dispatch_id}, status="PENDING", attempts=0, available_at=_now(), created_at=_now()))
                coordinator = self.lifecycle_coordinator
                if coordinator is not None:
                    callback = getattr(coordinator, "on_job_retry" if retry else "on_job_cancel", None)
                    if callback is None:
                        callback = getattr(coordinator, "retry" if retry else "cancel", None)
                    if callback is not None:
                        callback(job, attempt=attempt_no, dispatch_id=dispatch_id, session=db)
                view = _job_view(job)
                if retry and confirmation_id and requires_confirmation:
                    self.confirmation_service.consume_for_mutation(ctx, confirmation_id, target=target, action="job.retry", result_ref={"job_id": job.id, "status": job.status}, session=db)
                _idempotency_complete(self.repository, idem, view.model_dump(mode="json"), 202)
                _add_audit(self.repository, db, ctx=ctx, action=action, project_id=job.project_id, resource_ref=job.resource_ref, confirmation_id=confirmation_id, config_hash=None, job_id=job.id, before_summary={"status": old_status.value, "attempt": attempt_no - (1 if retry else 0), "revision": job.revision - 1}, after_summary={"status": job.status, "attempt": attempt_no, "revision": job.revision})
                return ServiceResult(view, status_code=202, execution_available=self.queue_available)
            except InvalidTransition as exc:
                raise TaskError(409, "STATE_CONFLICT", str(exc)) from exc

    def cancel(self, ctx: OperationContext, job_id: str, *, if_match: str | None, idempotency_key: str | None, reason: str | None = None, session=None) -> ServiceResult[JobView]:
        return self._mutate(ctx, job_id, action="job.cancel", if_match=if_match, idempotency_key=idempotency_key, reason=reason, session=session)

    def retry(self, ctx: OperationContext, job_id: str, *, if_match: str | None, idempotency_key: str | None, confirmation_id: str | None = None, session=None) -> ServiceResult[JobView]:
        return self._mutate(ctx, job_id, action="job.retry", if_match=if_match, idempotency_key=idempotency_key, retry=True, confirmation_id=confirmation_id, session=session)


class ConfirmationService:
    def __init__(self, repository: TaskRepository, *, policy: TaskPolicy | None = None, job_service: JobService | None = None, resolver: Callable[[OperationContext, ResourceRef, str], TargetSnapshot] | None = None, ttl_seconds: int = 1_800) -> None:
        if ttl_seconds < 1 or ttl_seconds > 1_800:
            raise ValueError("confirmation TTL must be between 1 and 1800 seconds")
        self.repository = repository
        self.policy = policy
        self.job_service = job_service
        self.resolver = resolver
        self.ttl_seconds = ttl_seconds

    def _validate_action(self, ctx: OperationContext, action: str, *, resource_ref: Mapping[str, Any] | None = None) -> None:
        if action not in _CONFIRMATION_ACTIONS:
            raise TaskError(400, "VALIDATION_FAILED", "确认动作不在白名单中")
        if ctx.interaction != "human":
            raise TaskError(403, "PERMISSION_DENIED", "该确认必须来自 human 通道")
        _policy_permission(self.policy, ctx, operation="confirm", kind="confirmation", resource_ref=resource_ref or {}, action=action)

    def _resolve_target(self, ctx: OperationContext, resource_ref: ResourceRef, action: str) -> TargetSnapshot:
        if self.resolver is not None:
            return self.resolver(ctx, resource_ref, action)
        if self.policy is not None:
            return self.policy.resolve_target(ctx, resource_ref, action)
        raise TaskError(503, "POLICY_UNAVAILABLE", "确认目标策略未配置", retryable=True)

    def preview(self, ctx: OperationContext, target: TargetSnapshot, request: ConfirmationPreviewRequest | None = None) -> ConfirmationPreview:
        _check_access(ctx, target.project_id)
        action = request.action if request else ""
        resource_ref = request.resource_ref if request else target.resource_ref
        if request and resource_ref != target.resource_ref:
            raise TaskError(409, "CONFIRMATION_INVALID", "目标资源已变化")
        self._validate_action(ctx, action, resource_ref=resource_ref.model_dump(mode="json"))
        _policy_target(self.policy, ctx, target, action=action)
        can_confirm = target.can_confirm and not target.blockers and target.revision >= 1 and len(target.config_hash) == 64 and bool(target.etag)
        return ConfirmationPreview.model_validate({
            "resource_ref": target.resource_ref,
            "action": action,
            "revision": target.revision,
            "config_hash": target.config_hash,
            "etag": target.etag,
            "summary": target.summary,
            "dependencies": target.dependencies,
            "impact": target.impact,
            "can_confirm": can_confirm,
            "blockers": target.blockers,
            "policy_version": target.policy_version,
        })

    def preview_from_request(self, ctx: OperationContext, request: ConfirmationPreviewRequest) -> ConfirmationPreview:
        if self.resolver is None:
            raise TaskError(503, "DEPENDENCY_UNAVAILABLE", "确认目标解析器未配置", retryable=True)
        return self.preview(ctx, self._resolve_target(ctx, request.resource_ref, request.action), request)

    def create(self, ctx: OperationContext, target: TargetSnapshot, request: ConfirmationCreateRequest, *, if_match: str | None, idempotency_key: str | None, session=None) -> ServiceResult[ConfirmationView]:
        _check_access(ctx, target.project_id)
        self._validate_action(ctx, request.action, resource_ref=target.resource_ref.model_dump(mode="json"))
        _policy_target(self.policy, ctx, target, action=request.action)
        if not target.can_confirm or target.blockers:
            raise TaskError(409, "CONFIRMATION_INVALID", "当前资源状态不可确认")
        if request.resource_ref != target.resource_ref or request.revision != target.revision or request.config_hash != target.config_hash or request.policy_version != target.policy_version:
            raise TaskError(409, "CONFIRMATION_INVALID", "确认绑定与当前资源不一致")
        scope = _scope(ctx, target.project_id, "/api/v1/confirmations", idempotency_key)
        payload_hash = _hash({"resource_ref": request.resource_ref.model_dump(mode="json"), "action": request.action, "revision": request.revision, "config_hash": request.config_hash, "policy_version": request.policy_version, "if_match": if_match})
        with self.repository.transaction(session) as db:
            idem, replayed = _idempotency_get(self.repository, db, scope, payload_hash)
            if replayed:
                return ServiceResult(_confirmation_view_from_data(idem.result_resource_ref), replayed=True, status_code=idem.result_status_code or 201)
            _require_if_match(if_match, target.etag)
            now = _now()
            row = Confirmation(
                id=_id("confirm"), project_id=target.project_id, actor_id=ctx.actor.actor_id,
                resource_ref=request.resource_ref.model_dump(mode="json"), action=request.action, revision=request.revision,
                config_hash=request.config_hash, binding_hash=_hash({"project_id": target.project_id, "actor_id": ctx.actor.actor_id, "resource_ref": request.resource_ref.model_dump(mode="json"), "action": request.action, "revision": request.revision, "config_hash": request.config_hash, "policy_version": request.policy_version}), policy_version=request.policy_version,
                summary_snapshot={"summary": target.summary, "dependencies": target.dependencies, "impact": target.impact}, status=ConfirmationStatus.CONFIRMED.value,
                created_at=now, expires_at=now + timedelta(seconds=self.ttl_seconds),
            )
            self.repository.add(db, row)
            view = _confirmation_view(row)
            _idempotency_complete(self.repository, idem, view.model_dump(mode="json"), 201)
            return ServiceResult(view, status_code=201)

    def create_from_request(self, ctx: OperationContext, request: ConfirmationCreateRequest, *, if_match: str | None, idempotency_key: str | None, session=None) -> ServiceResult[ConfirmationView]:
        if self.resolver is None:
            raise TaskError(503, "DEPENDENCY_UNAVAILABLE", "确认目标解析器未配置", retryable=True)
        return self.create(ctx, self._resolve_target(ctx, request.resource_ref, request.action), request, if_match=if_match, idempotency_key=idempotency_key, session=session)

    def get(self, ctx: OperationContext, confirmation_id: str, *, session=None) -> ConfirmationView:
        with self.repository.transaction(session) as db:
            row = self.repository.get_confirmation(db, confirmation_id)
            if row is None or row.actor_id != ctx.actor.actor_id or not ctx.actor.can_access_project(row.project_id):
                raise TaskError(404, "RESOURCE_NOT_FOUND", "确认不存在或当前主体不可见")
            if row.status == ConfirmationStatus.CONSUMED.value:
                self._validate_action(ctx, row.action, resource_ref=row.resource_ref)
                if row.binding_hash != _confirmation_binding_hash(row):
                    raise TaskError(409, "CONFIRMATION_INVALID", "确认绑定摘要无效")
                return _confirmation_view(row)
            target = self._resolve_target(ctx, ResourceRef.model_validate(row.resource_ref), row.action)
            _check_access(ctx, target.project_id)
            self._validate_action(ctx, row.action, resource_ref=row.resource_ref)
            _policy_target(self.policy, ctx, target, action=row.action)
            _validate_target_binding(row, target, ctx, row.action)
            return _confirmation_view(row)

    def revoke(self, ctx: OperationContext, confirmation_id: str, *, reason: str, idempotency_key: str | None, session=None) -> ServiceResult[ConfirmationView]:
        with self.repository.transaction(session) as db:
            row = self.repository.get_confirmation(db, confirmation_id, for_update=True)
            if row is None or row.actor_id != ctx.actor.actor_id or not ctx.actor.can_access_project(row.project_id):
                raise TaskError(404, "RESOURCE_NOT_FOUND", "确认不存在或当前主体不可见")
            target = self._resolve_target(ctx, ResourceRef.model_validate(row.resource_ref), row.action)
            _check_access(ctx, target.project_id)
            _policy_target(self.policy, ctx, target, action=row.action)
            _validate_target_binding(row, target, ctx, row.action)
            self._validate_action(ctx, row.action, resource_ref=row.resource_ref)
            scope = _scope(ctx, row.project_id, f"/api/v1/confirmations/{confirmation_id}/revoke", idempotency_key)
            payload_hash = _hash({"confirmation_id": confirmation_id, "action": "revoke", "reason": reason})
            idem, replayed = _idempotency_get(self.repository, db, scope, payload_hash)
            if replayed:
                return ServiceResult(_confirmation_view_from_data(idem.result_resource_ref), replayed=True, status_code=idem.result_status_code or 200)
            if row.status != ConfirmationStatus.CONFIRMED.value:
                raise TaskError(409, "CONFIRMATION_INVALID", f"确认当前状态为 {row.status}")
            row.status = ConfirmationStatus.REVOKED.value
            row.revoked_at = _now()
            view = _confirmation_view(row)
            _idempotency_complete(self.repository, idem, view.model_dump(mode="json"), 200)
            _add_audit(self.repository, db, ctx=ctx, action="confirmation.revoke", project_id=row.project_id, resource_ref=row.resource_ref, confirmation_id=row.id, config_hash=row.config_hash, job_id=None, before_summary={"status": ConfirmationStatus.CONFIRMED.value}, after_summary={"status": ConfirmationStatus.REVOKED.value, "reason": reason})
            return ServiceResult(view, status_code=200)

    def validate(self, ctx: OperationContext, confirmation_id: str, *, target: TargetSnapshot, action: str, session=None) -> None:
        """Validate a confirmation for a same-transaction mutation."""
        _check_access(ctx, target.project_id)
        self._validate_action(ctx, action, resource_ref=target.resource_ref.model_dump(mode="json"))
        _policy_target(self.policy, ctx, target, action=action)
        terminal_error: TaskError | None = None
        with self.repository.transaction(session) as db:
            row = self.repository.get_confirmation(db, confirmation_id, for_update=True)
            if row is None or row.actor_id != ctx.actor.actor_id or row.project_id != target.project_id:
                raise TaskError(404, "RESOURCE_NOT_FOUND", "确认不存在或当前主体不可见")
            if row.status != ConfirmationStatus.CONFIRMED.value:
                raise TaskError(409, "CONFIRMATION_INVALID", f"确认当前状态为 {row.status}")
            if _now() >= _as_utc(row.expires_at):
                row.status = ConfirmationStatus.EXPIRED.value
                terminal_error = TaskError(409, "CONFIRMATION_EXPIRED", "确认已过期")
            elif row.revoked_at is not None or row.project_id != target.project_id or row.actor_id != ctx.actor.actor_id or row.action != action or row.resource_ref != target.resource_ref.model_dump(mode="json") or row.revision != target.revision or row.config_hash != target.config_hash or row.policy_version != target.policy_version or row.binding_hash != _confirmation_binding_hash(row):
                row.status = ConfirmationStatus.INVALIDATED.value
                terminal_error = TaskError(409, "CONFIRMATION_INVALID", "确认绑定已失效")
        if terminal_error is not None:
            raise terminal_error

    def consume_for_mutation(self, ctx: OperationContext, confirmation_id: str, *, target: TargetSnapshot, action: str, result_ref: Mapping[str, Any], session=None) -> None:
        self.validate(ctx, confirmation_id, target=target, action=action, session=session)
        with self.repository.transaction(session) as db:
            row = self.repository.get_confirmation(db, confirmation_id, for_update=True)
            if row is None:
                raise TaskError(404, "RESOURCE_NOT_FOUND", "确认不存在或当前主体不可见")
            row.status = ConfirmationStatus.CONSUMED.value
            row.consumed_at = _now()
            row.consumed_result_ref = dict(result_ref)
            row.consumed_request_hash = _hash({"confirmation_id": confirmation_id, "action": action, "target": target.resource_ref.model_dump(mode="json"), "revision": target.revision, "config_hash": target.config_hash, "result": dict(result_ref)})

    def consume(self, ctx: OperationContext, confirmation_id: str, *, target: TargetSnapshot, job: ConfirmedJobSpec, idempotency_key: str | None, session=None) -> ServiceResult[JobView]:
        _check_access(ctx, target.project_id)
        self._validate_action(ctx, job.action, resource_ref=job.resource_ref.model_dump(mode="json"))
        _policy_target(self.policy, ctx, target, action=job.action)
        if self.job_service is None:
            raise TaskError(503, "DEPENDENCY_UNAVAILABLE", "Job 服务未配置", retryable=True)
        self.job_service._validate_kind(job.kind)
        request_hash = _hash({"confirmation_id": confirmation_id, "project_id": job.project_id, "kind": job.kind, "resource_ref": job.resource_ref.model_dump(mode="json"), "action": job.action, "revision": target.revision, "config_hash": target.config_hash, "policy_version": target.policy_version})
        terminal_error: TaskError | None = None
        with self.repository.transaction(session) as db:
            row = self.repository.get_confirmation(db, confirmation_id, for_update=True)
            if row is None or row.actor_id != ctx.actor.actor_id or row.project_id != target.project_id:
                raise TaskError(404, "RESOURCE_NOT_FOUND", "确认不存在或当前主体不可见")
            scope = _scope(ctx, target.project_id, f"/api/v1/confirmations/{confirmation_id}/consume", idempotency_key)
            idem, replayed = _idempotency_get(self.repository, db, scope, request_hash)
            if replayed:
                stored = idem.result_resource_ref or {}
                return ServiceResult(_job_view_from_data(stored.get("job", stored)), replayed=True, status_code=idem.result_status_code or 202)
            if row.status == ConfirmationStatus.CONSUMED.value:
                if row.consumed_request_hash != request_hash:
                    raise TaskError(409, "CONFIRMATION_INVALID", "确认已被其他语义的请求消费")
                stored = row.consumed_result_ref or {}
                if "job" not in stored:
                    raise TaskError(409, "STATE_CONFLICT", "确认消费结果缺失")
                return ServiceResult(_job_view_from_data(stored["job"]), replayed=True, status_code=202)
            if row.status != ConfirmationStatus.CONFIRMED.value:
                raise TaskError(409, "CONFIRMATION_INVALID", f"确认当前状态为 {row.status}")
            if _now() >= _as_utc(row.expires_at):
                # Persist the terminal state before returning the conflict;
                # otherwise a transaction rollback would make expiry appear
                # as CONFIRMED on the next request.
                row.status = ConfirmationStatus.EXPIRED.value
                terminal_error = TaskError(409, "CONFIRMATION_EXPIRED", "确认已过期")
            else:
                candidate_hash = _hash({"project_id": target.project_id, "actor_id": ctx.actor.actor_id, "resource_ref": target.resource_ref.model_dump(mode="json"), "action": job.action, "revision": target.revision, "config_hash": target.config_hash, "policy_version": target.policy_version})
                if row.binding_hash != candidate_hash or target.resource_ref.model_dump(mode="json") != job.resource_ref.model_dump(mode="json") or target.revision != row.revision or target.config_hash != row.config_hash or target.policy_version != row.policy_version:
                    row.status = ConfirmationStatus.INVALIDATED.value
                    terminal_error = TaskError(409, "CONFIRMATION_INVALID", "确认绑定已失效")
                else:
                    new_job = _add_job_rows(self.repository, db, project_id=job.project_id, owner_id=ctx.actor.actor_id, kind=job.kind, resource_ref=job.resource_ref, cancelable=job.cancelable, retryable=job.retryable, resume_mode=job.resume_mode, deadline_at=job.deadline_at)
                    view = _job_view(new_job)
                    result = {"job": view.model_dump(mode="json"), "confirmation_id": confirmation_id}
                    row.status = ConfirmationStatus.CONSUMED.value
                    row.consumed_at = _now()
                    row.consumed_result_ref = result
                    row.consumed_request_hash = request_hash
                    _idempotency_complete(self.repository, idem, result, 202)
                    _add_audit(self.repository, db, ctx=ctx, action=job.action, project_id=job.project_id, resource_ref=job.resource_ref.model_dump(mode="json"), confirmation_id=confirmation_id, config_hash=row.config_hash, job_id=new_job.id)
                    return ServiceResult(view, status_code=202, execution_available=self.job_service.queue_available)
        if terminal_error is not None:
            raise terminal_error
        raise TaskError(500, "STATE_CONFLICT", "确认消费未产生结果")


__all__ = [
    "ConfirmedJobSpec",
    "ConfirmationService",
    "EventPageResult",
    "JobService",
    "JobEventStreamSnapshot",
    "add_job_attempt_rows",
    "add_job_rows",
    "sync_job_lifecycle",
    "OperationContext",
    "ServiceResult",
    "TargetSnapshot",
    "TaskError",
    "TaskPolicy",
]
