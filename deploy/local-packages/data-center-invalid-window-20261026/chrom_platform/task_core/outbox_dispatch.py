"""Async MySQL outbox leasing for stable worker messages.

The dispatcher validates the server-side association before taking a lease.
It never executes business code or reshapes the broker payload. Ordinary
events use ``job_id``, ``attempt``, and ``dispatch_id``; batch cancellation
may additionally carry its database-bound ``fencing_token``.
"""

from __future__ import annotations

import asyncio
import inspect
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping
from uuid import uuid4

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chrom_platform.infrastructure.models import Job, JobAttempt, JobEvent, OutboxEvent
from chrom_platform.task_core.service import _job_etag
from chrom_platform.task_core.state import OutboxStatus


class OutboxDispatchError(ValueError):
    """The outbox row is not a valid stable job dispatch."""


class LeaseLostError(OutboxDispatchError):
    """The worker lost its outbox lease while business code was running."""


_STABLE_PAYLOAD_FIELDS = frozenset({"job_id", "attempt", "dispatch_id"})
_BATCH_CANCEL_PAYLOAD_FIELDS = _STABLE_PAYLOAD_FIELDS | {"fencing_token"}
_BATCH_CANCEL_RESOURCE_TYPES = frozenset({"batch_prediction", "batch_validation", "batch_export"})


class LeaseGuard:
    """Thread-safe view of a claim lease for long-running handlers.

    The heartbeat task owns no business session.  Every renewal opens its own
    short-lived session through the dispatcher's session factory.
    """

    def __init__(self, dispatcher: "AsyncOutboxDispatcher", claim: "OutboxClaim") -> None:
        self.dispatcher = dispatcher
        self.claim = claim
        self._lost = threading.Event()
        self._stopped = threading.Event()
        self._task: asyncio.Task[None] | None = None

    @property
    def lease_lost(self) -> bool:
        return self._lost.is_set()

    def check(self) -> None:
        if self._lost.is_set():
            raise LeaseLostError("OUTBOX_LEASE_LOST")

    def _mark_lost(self) -> None:
        self._lost.set()

    async def stop(self) -> None:
        self._stopped.set()
        task = self._task
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _run(self, interval: float) -> None:
        try:
            while not self._stopped.is_set():
                await asyncio.sleep(interval)
                if self._stopped.is_set():
                    return
                try:
                    renewed = await self.dispatcher.heartbeat(self.claim)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    renewed = False
                if not renewed:
                    self._mark_lost()
                    return
        except asyncio.CancelledError:
            raise

    def _start(self, interval: float) -> None:
        self._task = asyncio.create_task(self._run(interval))


@dataclass(frozen=True)
class OutboxClaim:
    """A leased row and trusted server-side routing metadata."""

    dispatch_id: str
    payload: Mapping[str, Any]
    event_type: str
    aggregate_type: str
    aggregate_id: str
    project_id: str
    resource_ref: Mapping[str, Any]
    attempts: int
    lease_owner: str
    lease_token: int
    lease_expires_at: datetime


def _utc(value: datetime | None) -> datetime:
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def validate_stable_payload(
    payload: Mapping[str, Any], *, dispatch_id: str, aggregate_id: str, attempt: int
) -> tuple[str, int, str]:
    """Validate the immutable broker payload and its DB associations."""

    if not isinstance(payload, Mapping) or set(payload) != {"job_id", "attempt", "dispatch_id"}:
        raise OutboxDispatchError("INVALID_WORKER_PAYLOAD")
    job_id = payload["job_id"]
    payload_attempt = payload["attempt"]
    payload_dispatch_id = payload["dispatch_id"]
    if (
        not isinstance(job_id, str)
        or not job_id
        or job_id != aggregate_id
        or not isinstance(payload_dispatch_id, str)
        or not payload_dispatch_id
        or payload_dispatch_id != dispatch_id
        or isinstance(payload_attempt, bool)
        or not isinstance(payload_attempt, int)
        or payload_attempt < 1
        or payload_attempt != attempt
    ):
        raise OutboxDispatchError("INVALID_WORKER_PAYLOAD")
    return job_id, payload_attempt, payload_dispatch_id


def _is_batch_cancel_job(event_type: str, job_kind: str) -> bool:
    return event_type == "job.cancel" and job_kind == "batch_prediction"


def _allows_batch_cancel_fencing(event_type: str, job_kind: str, resource_ref: Any) -> bool:
    return bool(
        _is_batch_cancel_job(event_type, job_kind)
        and isinstance(resource_ref, Mapping)
        and set(resource_ref) == {"type", "id"}
        and resource_ref.get("type") in _BATCH_CANCEL_RESOURCE_TYPES
        and isinstance(resource_ref.get("id"), str)
        and resource_ref.get("id")
    )


async def _validate_server_payload(
    session: Any,
    payload: Mapping[str, Any],
    *,
    dispatch_id: str,
    aggregate_id: str,
    attempt: int,
    event_type: str,
    job_kind: str,
    resource_ref: Any,
) -> tuple[str, int, str]:
    """Validate the standard payload, with a DB-bound fence for batch cancel only."""

    if not _allows_batch_cancel_fencing(event_type, job_kind, resource_ref):
        return validate_stable_payload(
            payload, dispatch_id=dispatch_id, aggregate_id=aggregate_id, attempt=attempt,
        )
    if not isinstance(payload, Mapping) or set(payload) != _BATCH_CANCEL_PAYLOAD_FIELDS:
        raise OutboxDispatchError("INVALID_WORKER_PAYLOAD")
    stable_payload = {key: payload[key] for key in _STABLE_PAYLOAD_FIELDS}
    values = validate_stable_payload(
        stable_payload, dispatch_id=dispatch_id, aggregate_id=aggregate_id, attempt=attempt,
    )
    fencing_token = payload["fencing_token"]
    if isinstance(fencing_token, bool) or not isinstance(fencing_token, int) or fencing_token < 0:
        raise OutboxDispatchError("INVALID_WORKER_PAYLOAD")
    attempt_row = await session.scalar(
        select(JobAttempt).where(JobAttempt.job_id == aggregate_id, JobAttempt.attempt == attempt).with_for_update()
    )
    if attempt_row is None:
        raise OutboxDispatchError("OUTBOX_ASSOCIATION_INVALID")
    current_token = attempt_row.fencing_token
    if current_token is None:
        current_token = 0
    if (
        isinstance(current_token, bool)
        or not isinstance(current_token, int)
        or current_token < 0
        or fencing_token != current_token
    ):
        raise OutboxDispatchError("INVALID_WORKER_PAYLOAD")
    return values


async def _mark_shared_job_failed(session: Any, *, job_id: str, attempt: int, error_code: str, now: datetime,
                                  expected_fencing_token: int | None = None) -> None:
    """Project a durable dispatch failure while retaining attempt fencing."""

    if not hasattr(session, "scalar"):
        return
    async def scalar(statement: Any) -> Any:
        value = session.scalar(statement)
        return await value if inspect.isawaitable(value) else value

    job = await scalar(select(Job).where(Job.id == job_id).with_for_update())
    if job is None or int(job.attempt) != int(attempt):
        return
    terminal = {"SUCCEEDED", "PARTIAL", "FAILED", "CANCELED", "TIMED_OUT", "INTERRUPTED"}
    attempt_row = await scalar(
        select(JobAttempt).where(JobAttempt.job_id == job.id, JobAttempt.attempt == attempt).with_for_update()
    )
    if attempt_row is None:
        return
    if expected_fencing_token is not None and (
        isinstance(expected_fencing_token, bool)
        or (attempt_row.fencing_token or 0) != expected_fencing_token
    ):
        return
    error = {"code": error_code[:96], "message": "Worker dispatch failed", "retryable": False}
    if job.status not in terminal:
        job.status = "FAILED"
        job.error = error
        job.finished_at = now
        job.updated_at = now
        job.revision += 1
        job.etag = _job_etag(job)
        attempt_row.status = "FAILED"
        attempt_row.error = error
        attempt_row.finished_at = now
        seq = await scalar(select(func.max(JobEvent.seq)).where(JobEvent.job_id == job.id))
        session.add(
            JobEvent(
                id=f"event_{uuid4().hex}",
                job_id=job.id,
                stream_id=job.id,
                seq=(int(seq) + 1) if seq is not None else 0,
                type="job.failed",
                schema_version="1.0",
                data={"status": "FAILED", "attempt": int(attempt), "error": {"code": error_code[:96]}},
                timestamp=now,
            )
        )
    if job.kind == "assistant" and job.status == "FAILED":
        await _mark_assistant_run_failed(session, job=job, attempt=attempt, error=error, now=now)


async def _mark_assistant_run_failed(session: Any, *, job: Any, attempt: int, error: Mapping[str, Any], now: datetime) -> None:
    """Synchronize an assistant run after a permanent shared-job failure.

    This is idempotent: a retry/restart can call it again without adding a
    second ``run.failed`` event or changing terminal success/cancel facts.
    """
    resource = job.resource_ref if isinstance(job.resource_ref, Mapping) else {}
    if resource.get("type") != "agent_run" or not isinstance(resource.get("id"), str):
        return
    # Lazy imports keep task-core independent from the optional Assistant module.
    from chrom_platform.modules.assistant.persistence import AgentAttemptRow, AgentEventRow, AgentRunRow

    async def scalar(statement: Any) -> Any:
        value = session.scalar(statement)
        return await value if inspect.isawaitable(value) else value

    run = await scalar(select(AgentRunRow).where(AgentRunRow.id == resource["id"]).with_for_update())
    if run is None or run.status in {"SUCCEEDED", "CANCELED", "TIMED_OUT", "INTERRUPTED"}:
        return
    # An old outbox row can arrive after recovery has advanced the AgentRun.
    # It may never fail the newer attempt or append a terminal event for it.
    if int(getattr(run, "attempt", 0) or 0) != int(attempt):
        return
    if run.status != "FAILED":
        run.status = "FAILED"
        run.error = dict(error)
        run.finished_at = now
        run.updated_at = now
        run.revision = int(run.revision or 1) + 1
        run.etag = f'"{run.revision}"'
    agent_attempt = await scalar(
        select(AgentAttemptRow).where(AgentAttemptRow.agent_run_id == run.id, AgentAttemptRow.attempt == attempt).with_for_update()
    )
    if agent_attempt is not None and agent_attempt.status not in {"SUCCEEDED", "CANCELED", "TIMED_OUT", "INTERRUPTED"}:
        agent_attempt.status = "FAILED"
        agent_attempt.error = dict(error)
        agent_attempt.finished_at = now
        agent_attempt.lease_until = None
    result = await session.execute(select(AgentEventRow).where(AgentEventRow.stream_type == "agent_run", AgentEventRow.stream_id == run.id))
    events = list(result.scalars().all())
    existing = next((item for item in events if item.type == "run.failed" and int((item.data or {}).get("attempt", 0)) == int(attempt)), None)
    if existing is not None:
        run.last_event_seq = max(int(run.last_event_seq or 0), int(existing.seq))
        return
    seq = max([int(item.seq) for item in events] + [int(run.last_event_seq or 0)]) + 1
    session.add(AgentEventRow(
        id=f"assistant_dispatch_event_{uuid4().hex}", stream_type="agent_run", stream_id=run.id,
        seq=seq, type="run.failed", schema_version="1.0",
        data={"status": "FAILED", "attempt": int(attempt), "error": {"code": str(error.get("code", "DISPATCH_FAILED"))[:96], "message": "Worker dispatch failed"}},
        timestamp=now, agent_run_id=run.id,
    ))
    run.last_event_seq = seq


class AsyncOutboxDispatcher:
    """Lease and complete outbox rows using short AsyncSession transactions."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        lease_seconds: int = 60,
        heartbeat_seconds: float = 15,
        quarantine_seconds: int = 24 * 60 * 60,
        max_attempts: int = 5,
    ) -> None:
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        if quarantine_seconds < 1:
            raise ValueError("quarantine_seconds must be positive")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if heartbeat_seconds <= 0 or heartbeat_seconds >= lease_seconds:
            raise ValueError("heartbeat_seconds must be positive and shorter than lease_seconds")
        self.session_factory = session_factory
        self.lease_seconds = lease_seconds
        self.heartbeat_seconds = float(heartbeat_seconds)
        self.quarantine_seconds = quarantine_seconds
        self.max_attempts = max_attempts

    async def heartbeat(
        self,
        claim: OutboxClaim,
        *,
        now: datetime | None = None,
        lease_seconds: int | None = None,
    ) -> bool:
        """Renew a claim only while its exact owner/token still owns it."""

        duration = self.lease_seconds if lease_seconds is None else lease_seconds
        if duration < 1:
            raise ValueError("lease_seconds must be positive")
        now = _utc(now)
        expires = now + timedelta(seconds=duration)
        async with self.session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    update(OutboxEvent)
                    .where(
                        OutboxEvent.dispatch_id == claim.dispatch_id,
                        OutboxEvent.lease_owner == claim.lease_owner,
                        OutboxEvent.lease_token == claim.lease_token,
                        OutboxEvent.status == OutboxStatus.PROCESSING.value,
                        OutboxEvent.lease_expires_at > now,
                    )
                    .values(lease_expires_at=expires)
                )
                return result.rowcount == 1

    async def start_heartbeat(
        self,
        claim: OutboxClaim,
        *,
        heartbeat_seconds: float | None = None,
    ) -> LeaseGuard:
        """Start a renewal task on the caller's event loop."""

        interval = self.heartbeat_seconds if heartbeat_seconds is None else float(heartbeat_seconds)
        if interval <= 0 or interval >= self.lease_seconds:
            raise ValueError("heartbeat_seconds must be positive and shorter than lease_seconds")
        guard = LeaseGuard(self, claim)
        guard._start(interval)
        return guard

    async def stop_heartbeat(self, guard: LeaseGuard | None) -> None:
        if guard is not None:
            await guard.stop()

    async def claim_due(
        self,
        *,
        owner: str,
        allowed_event_types: Iterable[str],
        allowed_job_kinds: Iterable[str] | None = None,
        now: datetime | None = None,
        lease_seconds: int | None = None,
    ) -> OutboxClaim | None:
        """Claim one explicitly allowed, due job event with a DB lease."""

        if not isinstance(owner, str) or not owner or len(owner) > 128:
            raise ValueError("owner must be a non-empty string of at most 128 characters")
        allowed = tuple(dict.fromkeys(allowed_event_types))
        if not allowed or any(not isinstance(value, str) or not value for value in allowed):
            raise ValueError("allowed_event_types must contain explicit event types")
        kinds = None if allowed_job_kinds is None else tuple(dict.fromkeys(allowed_job_kinds))
        if kinds is not None and (not kinds or any(not isinstance(value, str) or not value for value in kinds)):
            raise ValueError("allowed_job_kinds must contain explicit job kinds")
        duration = self.lease_seconds if lease_seconds is None else lease_seconds
        if duration < 1:
            raise ValueError("lease_seconds must be positive")
        now = _utc(now)
        expires = now + timedelta(seconds=duration)
        due = or_(
            and_(
                OutboxEvent.status == OutboxStatus.PENDING.value,
                OutboxEvent.available_at <= now,
            ),
            and_(
                OutboxEvent.status == OutboxStatus.PROCESSING.value,
                OutboxEvent.lease_expires_at.is_not(None),
                OutboxEvent.lease_expires_at <= now,
            ),
        )
        statement = (
            select(OutboxEvent, Job)
            .join(Job, and_(Job.id == OutboxEvent.aggregate_id, OutboxEvent.aggregate_type == "job"))
            .where(OutboxEvent.event_type.in_(allowed), due)
            .order_by(OutboxEvent.available_at, OutboxEvent.created_at, OutboxEvent.dispatch_id)
            .limit(32)
            .with_for_update(skip_locked=True)
        )
        if kinds is not None:
            statement = statement.where(Job.kind.in_(kinds))
        async with self.session_factory() as session:
            async with session.begin():
                result = await session.execute(statement)
                for event, job in result.all():
                    batch_cancel_job = _is_batch_cancel_job(event.event_type, job.kind)
                    try:
                        # JSON columns accept scalar/list/null values at the
                        # database boundary.  Treat them as malformed rows so
                        # one bad event is quarantined instead of aborting the
                        # whole poll transaction.
                        if not isinstance(event.payload, Mapping):
                            raise OutboxDispatchError("INVALID_WORKER_PAYLOAD")
                        payload = dict(event.payload)
                        await _validate_server_payload(
                            session,
                            payload,
                            dispatch_id=event.dispatch_id,
                            aggregate_id=event.aggregate_id,
                            attempt=job.attempt,
                            event_type=event.event_type,
                            job_kind=job.kind,
                            resource_ref=job.resource_ref,
                        )
                        if not isinstance(job.project_id, str) or not job.project_id:
                            raise OutboxDispatchError("JOB_ASSOCIATION_INVALID")
                        if not isinstance(job.resource_ref, Mapping):
                            raise OutboxDispatchError("JOB_ASSOCIATION_INVALID")
                        if int(event.attempts or 0) >= self.max_attempts:
                            event.status = OutboxStatus.FAILED.value
                            event.last_error = "MAX_ATTEMPTS_EXCEEDED"
                            event.lease_owner = None
                            event.lease_expires_at = None
                            await _mark_shared_job_failed(session, job_id=job.id, attempt=int(job.attempt), error_code="MAX_ATTEMPTS_EXCEEDED", now=now,
                                                          expected_fencing_token=payload.get("fencing_token") if batch_cancel_job else None)
                            continue
                    except OutboxDispatchError as exc:
                        # Quarantine malformed rows so the next poll can move
                        # past a full batch without repeatedly locking it.
                        event.status = OutboxStatus.FAILED.value
                        event.last_error = str(exc)[:96]
                        event.available_at = now + timedelta(seconds=self.quarantine_seconds)
                        event.lease_owner = None
                        event.lease_expires_at = None
                        payload = event.payload if isinstance(event.payload, Mapping) else {}
                        if (
                            not batch_cancel_job
                            and payload.get("job_id") == job.id
                            and payload.get("attempt") == int(job.attempt)
                        ):
                            await _mark_shared_job_failed(session, job_id=job.id, attempt=int(job.attempt), error_code=str(exc), now=now)
                        continue
                    token = int(event.lease_token or 0) + 1
                    attempts = int(event.attempts or 0) + 1
                    event.status = OutboxStatus.PROCESSING.value
                    event.attempts = attempts
                    event.lease_owner = owner
                    event.lease_token = token
                    event.lease_expires_at = expires
                    return OutboxClaim(
                        dispatch_id=event.dispatch_id,
                        payload=payload,
                        event_type=event.event_type,
                        aggregate_type=event.aggregate_type,
                        aggregate_id=event.aggregate_id,
                        project_id=job.project_id,
                        resource_ref=dict(job.resource_ref),
                        attempts=attempts,
                        lease_owner=owner,
                        lease_token=token,
                        lease_expires_at=expires,
                    )
        return None

    async def claim_execute(self, *, owner: str, allowed_job_kinds: Iterable[str] | None = None, now: datetime | None = None) -> OutboxClaim | None:
        """Claim only ``job.execute``; cancellation has a separate path."""

        return await self.claim_due(owner=owner, allowed_event_types=("job.execute",), allowed_job_kinds=allowed_job_kinds, now=now)

    async def claim_dispatch(
        self, *, owner: str, dispatch_id: str, job_id: str, project_id: str,
        attempt: int, artifact_id: str, now: datetime | None = None,
        lease_seconds: int | None = None, preclaim_check: Callable[[Any], bool] | None = None,
    ) -> OutboxClaim:
        """Claim one fresh artifact-validation dispatch without polling other rows."""

        if not owner or len(owner) > 128:
            raise ValueError("owner must be a non-empty string of at most 128 characters")
        if any(not isinstance(value, str) or not value for value in (dispatch_id, job_id, project_id, artifact_id)):
            raise ValueError("single dispatch binding is incomplete")
        if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
            raise ValueError("attempt must be positive")
        duration = self.lease_seconds if lease_seconds is None else lease_seconds
        if duration < 1:
            raise ValueError("lease_seconds must be positive")
        now = _utc(now)
        expires = now + timedelta(seconds=duration)
        async with self.session_factory() as session:
            async with session.begin():
                statement = (
                    select(OutboxEvent, Job)
                    .join(Job, and_(Job.id == OutboxEvent.aggregate_id, OutboxEvent.aggregate_type == "job"))
                    .where(
                        OutboxEvent.dispatch_id == dispatch_id,
                        OutboxEvent.status == OutboxStatus.PENDING.value,
                        OutboxEvent.attempts == 0,
                        OutboxEvent.available_at <= now,
                        OutboxEvent.event_type == "job.execute",
                        Job.id == job_id,
                        Job.kind == "artifact_validation",
                        Job.project_id == project_id,
                        Job.attempt == attempt,
                    )
                    .with_for_update(skip_locked=True)
                )
                event, job = (await session.execute(statement)).first() or (None, None)
                if event is None or job is None:
                    raise OutboxDispatchError("SINGLE_DISPATCH_NOT_FRESH")
                try:
                    payload = dict(event.payload) if isinstance(event.payload, Mapping) else {}
                    validate_stable_payload(
                        payload, dispatch_id=dispatch_id, aggregate_id=job_id, attempt=attempt,
                    )
                except OutboxDispatchError as exc:
                    raise OutboxDispatchError("SINGLE_DISPATCH_BINDING_INVALID") from exc
                ref = job.resource_ref if isinstance(job.resource_ref, Mapping) else {}
                if ref != {"type": "artifact", "id": artifact_id}:
                    raise OutboxDispatchError("SINGLE_DISPATCH_BINDING_INVALID")
                job_row = await session.scalar(select(Job).where(Job.id == job_id).with_for_update())
                attempt_row = await session.scalar(select(JobAttempt).where(
                    JobAttempt.job_id == job_id, JobAttempt.attempt == attempt,
                ).with_for_update())
                if (job_row is None or job_row.status != "QUEUED"
                        or attempt_row is None or attempt_row.status != "QUEUED"):
                    raise OutboxDispatchError("SINGLE_DISPATCH_STATE_INVALID")
                if preclaim_check is not None:
                    valid = await session.run_sync(preclaim_check)
                    if valid is not True:
                        raise OutboxDispatchError("SINGLE_DISPATCH_RESOURCE_INVALID")
                token = int(event.lease_token or 0) + 1
                event.status = OutboxStatus.PROCESSING.value
                event.attempts = 1
                event.lease_owner = owner
                event.lease_token = token
                event.lease_expires_at = expires
                return OutboxClaim(
                    dispatch_id=dispatch_id, payload=payload, event_type=event.event_type,
                    aggregate_type=event.aggregate_type, aggregate_id=job_id,
                    project_id=project_id, resource_ref=dict(ref), attempts=1,
                    lease_owner=owner, lease_token=token, lease_expires_at=expires,
                )

    async def claim_cancel(self, *, owner: str, allowed_job_kinds: Iterable[str] | None = None, now: datetime | None = None) -> OutboxClaim | None:
        """Claim only ``job.cancel``; execution has a separate path."""

        return await self.claim_due(owner=owner, allowed_event_types=("job.cancel",), allowed_job_kinds=allowed_job_kinds, now=now)

    async def resolve_active(
        self,
        payload: Mapping[str, Any],
        *,
        owner: str,
        allowed_event_types: Iterable[str],
        allowed_job_kinds: Iterable[str] | None = None,
        now: datetime | None = None,
    ) -> OutboxClaim:
        """Reload and validate a live lease before invoking business code."""

        if not isinstance(owner, str) or not owner or len(owner) > 128:
            raise ValueError("owner must be a non-empty string of at most 128 characters")
        allowed = tuple(dict.fromkeys(allowed_event_types))
        if not allowed:
            raise ValueError("allowed_event_types must contain explicit event types")
        kinds = None if allowed_job_kinds is None else tuple(dict.fromkeys(allowed_job_kinds))
        now = _utc(now)
        if not isinstance(payload, Mapping) or not isinstance(payload.get("dispatch_id"), str):
            raise OutboxDispatchError("INVALID_WORKER_PAYLOAD")
        statement = (
            select(OutboxEvent, Job)
            .join(Job, and_(Job.id == OutboxEvent.aggregate_id, OutboxEvent.aggregate_type == "job"))
            .where(
                OutboxEvent.dispatch_id == payload["dispatch_id"],
                OutboxEvent.status == OutboxStatus.PROCESSING.value,
                OutboxEvent.lease_owner == owner,
                OutboxEvent.lease_expires_at > now,
                OutboxEvent.event_type.in_(allowed),
            )
            .with_for_update()
        )
        if kinds is not None:
            statement = statement.where(Job.kind.in_(kinds))
        async with self.session_factory() as session:
            async with session.begin():
                event, job = (await session.execute(statement)).first() or (None, None)
                if event is None or job is None:
                    raise OutboxDispatchError("OUTBOX_LEASE_INVALID")
                if not isinstance(event.payload, Mapping):
                    raise OutboxDispatchError("INVALID_WORKER_PAYLOAD")
                event_payload = dict(event.payload)
                await _validate_server_payload(
                    session,
                    event_payload,
                    dispatch_id=event.dispatch_id,
                    aggregate_id=event.aggregate_id,
                    attempt=job.attempt,
                    event_type=event.event_type,
                    job_kind=job.kind,
                    resource_ref=job.resource_ref,
                )
                if event_payload != dict(payload) or not isinstance(job.project_id, str) or not job.project_id or not isinstance(job.resource_ref, Mapping):
                    raise OutboxDispatchError("OUTBOX_ASSOCIATION_INVALID")
                return OutboxClaim(
                    dispatch_id=event.dispatch_id,
                    payload=event_payload,
                    event_type=event.event_type,
                    aggregate_type=event.aggregate_type,
                    aggregate_id=event.aggregate_id,
                    project_id=job.project_id,
                    resource_ref=dict(job.resource_ref),
                    attempts=int(event.attempts or 0),
                    lease_owner=owner,
                    lease_token=int(event.lease_token or 0),
                    lease_expires_at=_utc(event.lease_expires_at),
                )

    async def ack(self, claim: OutboxClaim, *, now: datetime | None = None) -> bool:
        """Mark a live lease sent; stale owners fail the CAS and change nothing."""

        now = _utc(now)
        async with self.session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    update(OutboxEvent)
                    .where(
                        OutboxEvent.dispatch_id == claim.dispatch_id,
                        OutboxEvent.status == OutboxStatus.PROCESSING.value,
                        OutboxEvent.lease_owner == claim.lease_owner,
                        OutboxEvent.lease_token == claim.lease_token,
                        OutboxEvent.lease_expires_at > now,
                    )
                    .values(
                        status=OutboxStatus.SENT.value,
                        dispatched_at=now,
                        last_error=None,
                        lease_owner=None,
                        lease_expires_at=None,
                    )
                )
                return result.rowcount == 1

    async def retry(
        self,
        claim: OutboxClaim,
        *,
        error_code: str,
        delay_seconds: float,
        now: datetime | None = None,
    ) -> bool:
        """Return a live lease to PENDING with owner/token CAS protection."""

        if delay_seconds < 0:
            raise ValueError("delay_seconds must be non-negative")
        if not isinstance(error_code, str) or not error_code:
            raise ValueError("error_code must be a non-empty string")
        now = _utc(now)
        async with self.session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    update(OutboxEvent)
                    .where(
                        OutboxEvent.dispatch_id == claim.dispatch_id,
                        OutboxEvent.status == OutboxStatus.PROCESSING.value,
                        OutboxEvent.lease_owner == claim.lease_owner,
                        OutboxEvent.lease_token == claim.lease_token,
                        OutboxEvent.lease_expires_at > now,
                    )
                    .values(
                        status=(OutboxStatus.FAILED.value if int(claim.attempts) >= self.max_attempts else OutboxStatus.PENDING.value),
                        available_at=now + timedelta(seconds=delay_seconds),
                        last_error=("MAX_ATTEMPTS_EXCEEDED" if int(claim.attempts) >= self.max_attempts else error_code[:96]),
                        lease_owner=None,
                        lease_expires_at=None,
                    )
                )
                applied = result.rowcount == 1
                if applied and int(claim.attempts) >= self.max_attempts:
                    await _mark_shared_job_failed(session, job_id=claim.aggregate_id, attempt=int(claim.payload["attempt"]), error_code="MAX_ATTEMPTS_EXCEEDED", now=now,
                                                  expected_fencing_token=claim.payload.get("fencing_token") if claim.event_type == "job.cancel" else None)
                return applied

    async def fail_permanent(
        self,
        claim: OutboxClaim,
        *,
        error_code: str,
        now: datetime | None = None,
    ) -> bool:
        """Dead-letter a deterministic dispatch failure under lease CAS."""

        if not isinstance(error_code, str) or not error_code:
            raise ValueError("error_code must be a non-empty string")
        now = _utc(now)
        async with self.session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    update(OutboxEvent)
                    .where(
                        OutboxEvent.dispatch_id == claim.dispatch_id,
                        OutboxEvent.status == OutboxStatus.PROCESSING.value,
                        OutboxEvent.lease_owner == claim.lease_owner,
                        OutboxEvent.lease_token == claim.lease_token,
                        OutboxEvent.lease_expires_at > now,
                    )
                    .values(
                        status=OutboxStatus.FAILED.value,
                        available_at=now,
                        last_error=error_code[:96],
                        lease_owner=None,
                        lease_expires_at=None,
                    )
                )
                applied = result.rowcount == 1
                if applied:
                    await _mark_shared_job_failed(session, job_id=claim.aggregate_id, attempt=int(claim.payload["attempt"]), error_code=error_code, now=now,
                                                  expected_fencing_token=claim.payload.get("fencing_token") if claim.event_type == "job.cancel" else None)
                return applied


__all__ = [
    "AsyncOutboxDispatcher",
    "LeaseGuard",
    "LeaseLostError",
    "OutboxClaim",
    "OutboxDispatchError",
    "validate_stable_payload",
]
