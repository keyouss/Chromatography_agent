"""Fail-closed runner for one server-bound model-package validation dispatch."""

from __future__ import annotations

import asyncio
import concurrent.futures
import os
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chrom_platform.artifact_core.models import Artifact as ArtifactRow
from chrom_platform.artifact_core.validation_worker import ArtifactValidationWorker
from chrom_platform.core.dto import ActorContext
from chrom_platform.infrastructure.models import ExecutionLedger, Job, JobAttempt, OutboxEvent
from chrom_platform.task_core.outbox_dispatch import AsyncOutboxDispatcher, LeaseGuard, OutboxClaim, _utc


class SingleJobError(RuntimeError):
    """A single-job admission or execution failed closed."""


@dataclass(frozen=True)
class SingleJobBinding:
    schema: str
    queue: str
    store_root: str
    project_id: str
    job_id: str
    attempt: int
    dispatch_id: str
    artifact_id: str
    enabled: bool = False


@dataclass(frozen=True)
class RuntimeIdentity:
    schema: str
    queue: str
    store_root: str


class _BoundedScanner:
    def __init__(self, scanner: object, guard: LeaseGuard, session_factory, binding: SingleJobBinding,
                 *, attempt_id: str, fencing_token: int, execution_token: str, store_identity: tuple[int, int]) -> None:
        self._scanner = scanner
        self._guard = guard
        self._session_factory = session_factory
        self._binding = binding
        self._attempt_id = attempt_id
        self._fencing_token = fencing_token
        self._execution_token = execution_token
        self._store_identity = store_identity
        self._store_root = Path(binding.store_root)
        self._loop = asyncio.get_running_loop()
        self.calls = 0

    async def _check_current(self) -> None:
        self._guard.check()
        async with self._session_factory() as session:
            job = await session.get(Job, self._binding.job_id)
            attempt = await session.scalar(select(JobAttempt).where(
                JobAttempt.job_id == self._binding.job_id,
                JobAttempt.attempt == self._binding.attempt,
            ))
            event = await session.get(OutboxEvent, self._binding.dispatch_id)
            now = datetime.now(timezone.utc)
            if (job is None or job.kind != "artifact_validation"
                    or job.project_id != self._binding.project_id
                    or job.resource_ref != {"type": "artifact", "id": self._binding.artifact_id}
                    or job.status != "RUNNING" or job.attempt != self._binding.attempt
                    or attempt is None or attempt.id != self._attempt_id
                    or attempt.status != "RUNNING"
                    or int(attempt.fencing_token or 0) != self._fencing_token
                    or attempt.execution_token != self._execution_token
                    or event is None or event.aggregate_type != "job"
                    or event.aggregate_id != self._binding.job_id
                    or event.event_type != "job.execute"
                    or dict(event.payload or {}) != dict(self._guard.claim.payload)
                    or event.status != "PROCESSING"
                    or event.lease_owner != self._guard.claim.lease_owner
                    or event.lease_token != self._guard.claim.lease_token
                    or event.lease_expires_at is None or _utc(event.lease_expires_at) <= now):
                raise SingleJobError("SINGLE_JOB_CANCELED_OR_FENCED")
        if _store_root_identity(self._store_root) != (self._store_root, self._store_identity):
            raise SingleJobError("SINGLE_JOB_STORE_IDENTITY_CHANGED")

    def _check_from_scanner_thread(self) -> None:
        future = asyncio.run_coroutine_threadsafe(self._check_current(), self._loop)
        try:
            future.result(timeout=5)
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise SingleJobError("SINGLE_JOB_FENCE_CHECK_TIMEOUT") from exc

    def scan(self, **kwargs: Any) -> object:
        self._check_from_scanner_thread()
        if self.calls >= 4:
            raise SingleJobError("SCANNER_CALL_BUDGET_EXCEEDED")
        self.calls += 1
        self._check_from_scanner_thread()
        result = self._scanner.scan(**kwargs)
        self._check_from_scanner_thread()
        return result


def _store_root_identity(path_value: str | os.PathLike[str]) -> tuple[Path, tuple[int, int]]:
    path = Path(path_value)
    if not path.is_absolute():
        raise SingleJobError("SINGLE_JOB_STORE_ROOT_NOT_ABSOLUTE")
    current = Path(path.anchor)
    components = path.parts[1:]
    for component in components:
        current = current / component
        try:
            info = current.lstat()
        except OSError as exc:
            raise SingleJobError("SINGLE_JOB_STORE_ROOT_UNAVAILABLE") from exc
        attributes = getattr(info, "st_file_attributes", 0)
        if stat.S_ISLNK(info.st_mode) or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise SingleJobError("SINGLE_JOB_STORE_REPARSE_POINT")
    if not path.is_dir():
        raise SingleJobError("SINGLE_JOB_STORE_ROOT_UNAVAILABLE")
    try:
        resolved = path.resolve(strict=True)
        before = resolved.stat()
        after = resolved.stat()
    except OSError as exc:
        raise SingleJobError("SINGLE_JOB_STORE_ROOT_UNAVAILABLE") from exc
    identity_before = (int(before.st_dev), int(before.st_ino))
    identity_after = (int(after.st_dev), int(after.st_ino))
    if identity_before != identity_after:
        raise SingleJobError("SINGLE_JOB_STORE_IDENTITY_CHANGED")
    return resolved, identity_before


def _validate_mapping(binding: SingleJobBinding, actual: RuntimeIdentity, store: object) -> tuple[Path, tuple[int, int]]:
    if binding.enabled is not True:
        raise SingleJobError("SINGLE_JOB_DISABLED")
    values = (binding.schema, binding.queue, binding.store_root, binding.project_id,
              binding.job_id, binding.dispatch_id, binding.artifact_id)
    if any(not isinstance(value, str) or not value for value in values):
        raise SingleJobError("SINGLE_JOB_BINDING_INCOMPLETE")
    if binding.schema == "chrom_assistant_accept_20261006":
        raise SingleJobError("SINGLE_JOB_LEGACY_SCHEMA_BLOCKED")
    if isinstance(binding.attempt, bool) or type(binding.attempt) is not int or binding.attempt != 1:
        raise SingleJobError("SINGLE_JOB_ATTEMPT_UNSUPPORTED")
    store_root = getattr(store, "_root", None)
    expected_root, identity = _store_root_identity(binding.store_root)
    if (actual.schema != binding.schema or actual.queue != binding.queue or store_root is None
            or _store_root_identity(actual.store_root) != (expected_root, identity)
            or _store_root_identity(store_root) != (expected_root, identity)
            or not bool(getattr(store, "configured", False))):
        raise SingleJobError("SINGLE_JOB_RUNTIME_IDENTITY_MISMATCH")
    return expected_root, identity


async def _probe_database_schema(session_factory) -> str:
    bind = getattr(session_factory, "kw", {}).get("bind")
    if bind is None or getattr(getattr(bind, "dialect", None), "name", None) != "mysql":
        raise SingleJobError("SINGLE_JOB_DATABASE_ENGINE_INVALID")
    async with bind.connect() as connection:
        value = await connection.scalar(text("SELECT DATABASE()"))
    if not isinstance(value, str) or not value:
        raise SingleJobError("SINGLE_JOB_DATABASE_SCHEMA_UNKNOWN")
    return value


async def _ledger_reserve(session_factory: async_sessionmaker[AsyncSession], binding: SingleJobBinding) -> tuple[str, str]:
    key = f"artifact-single-validation:{binding.job_id}:{binding.attempt}"
    row_id = f"exec_{uuid4().hex}"
    async with session_factory() as session:
        async with session.begin():
            row = await session.scalar(select(ExecutionLedger).where(
                ExecutionLedger.operation_key == key,
            ).with_for_update())
            if row is not None:
                raise SingleJobError("SINGLE_JOB_ALREADY_ENTERED")
            session.add(ExecutionLedger(
                id=row_id, operation_key=key,
                operation_type="artifact_single_validation", status="STARTED", job_id=binding.job_id,
                error={"dispatch_id": binding.dispatch_id},
            ))
    return key, row_id


async def _ledger_finish(session_factory, binding: SingleJobBinding, key: str, row_id: str, status: str) -> None:
    async with session_factory() as session:
        async with session.begin():
            result = await session.execute(
                update(ExecutionLedger).where(
                    ExecutionLedger.operation_key == key,
                    ExecutionLedger.id == row_id,
                    ExecutionLedger.operation_type == "artifact_single_validation",
                    ExecutionLedger.job_id == binding.job_id,
                    ExecutionLedger.status == "STARTED",
                ).values(status=status, error={"dispatch_id": binding.dispatch_id})
            )
            if result.rowcount != 1:
                raise SingleJobError("SINGLE_JOB_LEDGER_FENCE_INVALID")


async def _admit_after_claim(session_factory, binding: SingleJobBinding, claim: OutboxClaim) -> tuple[str, int, str]:
    async with session_factory() as session:
        async with session.begin():
            def check(sync_session) -> None:
                job = sync_session.scalar(select(Job).where(Job.id == binding.job_id).with_for_update())
                attempt = sync_session.scalar(select(JobAttempt).where(
                    JobAttempt.job_id == binding.job_id,
                    JobAttempt.attempt == binding.attempt,
                ).with_for_update())
                event = sync_session.scalar(select(OutboxEvent).where(
                    OutboxEvent.dispatch_id == binding.dispatch_id,
                ).with_for_update())
                artifact = sync_session.scalar(select(ArtifactRow).where(
                    ArtifactRow.id == binding.artifact_id,
                    ArtifactRow.project_id == binding.project_id,
                ).with_for_update())
                if (job is None or job.kind != "artifact_validation"
                        or job.project_id != binding.project_id or job.attempt != binding.attempt
                        or job.status != "QUEUED"
                        or job.resource_ref != {"type": "artifact", "id": binding.artifact_id}
                        or attempt is None or attempt.status != "QUEUED"
                        or event is None or event.status != "PROCESSING"
                        or event.aggregate_type != "job" or event.aggregate_id != binding.job_id
                        or event.event_type != "job.execute"
                        or event.attempts != 1 or event.lease_owner != claim.lease_owner
                        or event.lease_token != claim.lease_token
                        or event.lease_expires_at is None
                        or _utc(event.lease_expires_at) <= datetime.now(timezone.utc)
                        or dict(event.payload or {}) != dict(claim.payload)
                        or artifact is None or artifact.kind != "model_package"
                        or artifact.status != "VALIDATING" or artifact.scan_status != "PENDING"):
                    raise SingleJobError("SINGLE_JOB_BINDING_CHANGED")
                return attempt.id, int(attempt.fencing_token or 0), f"artifact-validation:{binding.dispatch_id}:{binding.attempt}"
            return await session.run_sync(check)


async def run_single_job(
    *, binding: SingleJobBinding, actual: RuntimeIdentity,
    dispatcher: AsyncOutboxDispatcher, session_factory: async_sessionmaker[AsyncSession],
    store: object, scanner: object, actor: ActorContext, owner: str,
) -> Mapping[str, Any]:
    """Run exactly one fresh artifact-validation dispatch; every uncertain exit is non-reentrant."""

    store_root, store_identity = _validate_mapping(binding, actual, store)
    if await _probe_database_schema(session_factory) != binding.schema:
        raise SingleJobError("SINGLE_JOB_DATABASE_SCHEMA_MISMATCH")
    key, ledger_id = await _ledger_reserve(session_factory, binding)
    guard: LeaseGuard | None = None
    claim: OutboxClaim | None = None
    try:
        def validate_artifact(sync_session) -> bool:
            artifact = sync_session.scalar(select(ArtifactRow).where(
                ArtifactRow.id == binding.artifact_id,
                ArtifactRow.project_id == binding.project_id,
            ).with_for_update())
            return bool(
                artifact is not None and artifact.kind == "model_package"
                and artifact.status == "VALIDATING" and artifact.scan_status == "PENDING"
            )

        claim = await dispatcher.claim_dispatch(
            owner=owner, dispatch_id=binding.dispatch_id, job_id=binding.job_id,
            project_id=binding.project_id, attempt=binding.attempt,
            artifact_id=binding.artifact_id, preclaim_check=validate_artifact,
        )
        guard = await dispatcher.start_heartbeat(claim)
        attempt_id, fencing_token, execution_token = await _admit_after_claim(session_factory, binding, claim)
        bounded_scanner = _BoundedScanner(
            scanner, guard, session_factory, binding, attempt_id=attempt_id,
            fencing_token=fencing_token + 1, execution_token=execution_token,
            store_identity=store_identity,
        )
        result = await ArtifactValidationWorker(
            session_factory=session_factory, store=store, scanner=bounded_scanner, actor=actor,
        ).handle(claim.payload, event_type="job.execute", lease_guard=guard)
        guard.check()
        if not await dispatcher.ack(claim):
            raise SingleJobError("SINGLE_JOB_OUTBOX_ACK_UNKNOWN")
        status = str(result.get("status", "FAILED")) if isinstance(result, Mapping) else "FAILED"
        await _ledger_finish(
            session_factory, binding, key, ledger_id,
            "SUCCEEDED" if status == "SUCCEEDED" else "FAILED",
        )
        return result if isinstance(result, Mapping) else {"status": "FAILED"}
    except BaseException:
        try:
            await _ledger_finish(session_factory, binding, key, ledger_id, "UNKNOWN")
        except Exception:
            # STARTED is also a durable deny-on-reentry state.
            pass
        raise
    finally:
        if guard is not None:
            await dispatcher.stop_heartbeat(guard)


__all__ = ["RuntimeIdentity", "SingleJobBinding", "SingleJobError", "run_single_job"]
