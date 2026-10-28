"""Async production handler for prediction Job deliveries.

The broker carries only ``job_id``, ``attempt`` and ``dispatch_id``.  This
module reloads all business data from the caller-owned ``AsyncSession`` and
keeps scientific execution outside database transactions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import inspect
import json
from typing import Any, Mapping, Protocol
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chrom_platform.core.dto import ResourceRef
from chrom_platform.core.errors import APIError
from chrom_platform.infrastructure.models import Job, JobAttempt, JobEvent, OutboxEvent
from chrom_platform.modules.data_center.persistence import PredictionRecordRow
from chrom_platform.task_core.state import JobStatus

from .adapters import CapabilityRegistry
from .persistence import PredictionRow, PredictionExecutionSnapshot, _finish_prediction_progress
from .schemas import (
    ModelSnapshot, PredictionExecutionInputSnapshot,
    PublicErrorSummary, Warning as PredictionWarning, build_prediction_execution_request,
    validate_hplc_execution_binding, validate_gc_execution_binding,
)


_TERMINAL = frozenset({
    JobStatus.SUCCEEDED, JobStatus.PARTIAL, JobStatus.FAILED,
    JobStatus.CANCELED, JobStatus.TIMED_OUT, JobStatus.INTERRUPTED,
})
_EXECUTE_EVENTS = frozenset({"prediction.execute", "job.execute"})
_EVENTS = _EXECUTE_EVENTS | {"job.cancel"}


class PredictionWorkerError(RuntimeError):
    """Safe routing/fencing error; no scientific side effect is attempted."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class PredictionWorkerMessage:
    job_id: str
    attempt: int
    dispatch_id: str

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "PredictionWorkerMessage":
        if not isinstance(payload, Mapping) or set(payload) != {"job_id", "attempt", "dispatch_id"}:
            raise PredictionWorkerError("INVALID_WORKER_PAYLOAD")
        job_id, attempt, dispatch_id = payload["job_id"], payload["attempt"], payload["dispatch_id"]
        if (not isinstance(job_id, str) or not job_id or not isinstance(dispatch_id, str) or not dispatch_id
                or isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1):
            raise PredictionWorkerError("INVALID_WORKER_PAYLOAD")
        return cls(job_id, attempt, dispatch_id)


@dataclass(frozen=True)
class ProductionWorkerResult:
    status: JobStatus
    result: object | None = None
    error: PublicErrorSummary | None = None
    replayed: bool = False


@dataclass(frozen=True)
class _Claim:
    snapshot: PredictionExecutionSnapshot | None
    event_type: str
    replayed: bool = False
    status: JobStatus = JobStatus.QUEUED


class PredictionExecutionStore(Protocol):
    async def claim(self, message: PredictionWorkerMessage) -> _Claim: ...
    async def finish(self, snapshot: PredictionExecutionSnapshot, *, result: object | None, error: PublicErrorSummary | None) -> bool | JobStatus: ...
    async def cancel(self, message: PredictionWorkerMessage) -> ProductionWorkerResult: ...


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json(value: object) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _etag(row: PredictionRow) -> str:
    return hashlib.sha256(json.dumps(
        {"id": row.id, "revision": row.revision, "status": row.status, "result": row.result, "error": row.error},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _job_etag(job: Job) -> str:
    return hashlib.sha256(json.dumps(
        {"id": job.id, "project_id": job.project_id, "status": job.status, "attempt": job.attempt,
         "revision": job.revision, "progress": job.progress, "result_ref": job.result_ref, "error": job.error},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _record_etag(row: PredictionRecordRow) -> str:
    return hashlib.sha256(json.dumps(
        {"id": row.id, "revision": row.revision, "status": row.status, "result_ref": row.result_ref},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


async def _update_record_projection(
    session: AsyncSession,
    record_id: str,
    *,
    status: str,
    result_ref: dict[str, Any] | None,
    warnings: list[dict[str, Any]] | None = None,
) -> None:
    record = (await session.execute(select(PredictionRecordRow).where(PredictionRecordRow.id == record_id).with_for_update())).scalar_one_or_none()
    if record is None:
        raise PredictionWorkerError("PREDICTION_RECORD_NOT_FOUND")
    record.status = status
    record.result_ref = result_ref
    if warnings is not None:
        record.warnings = _merge_record_warnings(getattr(record, "warnings", []), warnings)
    record.revision = int(record.revision or 1) + 1
    record.etag = _record_etag(record)


def _normalise_warning(value: object) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        try:
            warning = PredictionWarning.model_validate(value)
        except ValidationError:
            return None
        return warning.model_dump(mode="json", exclude_none=True)
    if isinstance(value, str) and value.strip():
        code, separator, message = value.partition(":")
        if separator and code.strip() and message.strip():
            return {"code": code.strip(), "message": message.strip()}
        return {"code": "MODEL_WARNING", "message": value}
    return None


def _warning_key(warning: Mapping[str, Any]) -> tuple[str, str, object]:
    return warning["code"], warning["message"], warning.get("field")


def _warning_items(values: object) -> list[dict[str, Any]]:
    if not isinstance(values, (list, tuple)):
        return []
    warnings: list[dict[str, Any]] = []
    seen: set[tuple[str, str, object]] = set()
    for value in values:
        warning = _normalise_warning(value)
        if warning is None:
            continue
        key = _warning_key(warning)
        if key not in seen:
            seen.add(key)
            warnings.append(warning)
    return warnings


def _result_warnings(result: object | None) -> list[dict[str, Any]]:
    public_result = _json(result)
    if not isinstance(public_result, Mapping):
        return []
    applicability = public_result.get("applicability")
    applicable_warnings = applicability.get("warnings") if isinstance(applicability, Mapping) else None
    return _warning_items([*_warning_items(applicable_warnings), *_warning_items(public_result.get("warnings"))])


def _merge_prediction_warnings(existing: object, additions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = _warning_items(existing)
    seen = {_warning_key(item) for item in merged}
    for warning in additions:
        key = _warning_key(warning)
        if key not in seen:
            seen.add(key)
            merged.append(dict(warning))
    return merged


def _merge_record_warnings(existing: object, additions: list[dict[str, Any]]) -> list[str]:
    values = list(existing) if isinstance(existing, (list, tuple)) else []
    seen = set()
    for value in values:
        warning = _normalise_warning(value)
        if warning is not None:
            seen.add(_warning_key(warning))
    for warning in additions:
        key = _warning_key(warning)
        if key not in seen:
            values.append(f"{warning['code']}: {warning['message']}")
            seen.add(key)
    return values


def _public_error(exc: BaseException) -> PublicErrorSummary:
    if isinstance(exc, APIError):
        p = exc.payload
        return PublicErrorSummary(code=p.code, message=p.message, retryable=p.retryable)
    return PublicErrorSummary(code="PREDICTION_EXECUTION_FAILED", message="预测执行失败", retryable=True)


class AsyncPredictionExecutionStore:
    """Short AsyncSession transactions for prediction state and fences."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], *, lease_seconds: int = 60) -> None:
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        self.session_factory = session_factory
        self.lease_seconds = lease_seconds

    @staticmethod
    def _validate_job(job: Job, message: PredictionWorkerMessage) -> ResourceRef:
        if job.kind != "prediction" or int(job.attempt) != message.attempt:
            raise PredictionWorkerError("WORKER_JOB_FENCE_INVALID")
        try:
            ref = ResourceRef.model_validate(job.resource_ref)
        except ValidationError as exc:
            raise PredictionWorkerError("WORKER_JOB_RESOURCE_INVALID") from exc
        if ref.type != "prediction":
            raise PredictionWorkerError("WORKER_JOB_RESOURCE_INVALID")
        return ref

    async def _rows(self, session: AsyncSession, message: PredictionWorkerMessage) -> tuple[Job, JobAttempt, PredictionRow, OutboxEvent]:
        job = (await session.execute(select(Job).where(Job.id == message.job_id).with_for_update())).scalar_one_or_none()
        if job is None:
            raise PredictionWorkerError("WORKER_JOB_NOT_FOUND")
        ref = self._validate_job(job, message)
        dispatch = (await session.execute(select(OutboxEvent).where(
            OutboxEvent.dispatch_id == message.dispatch_id,
            OutboxEvent.aggregate_type == "job", OutboxEvent.aggregate_id == job.id,
        ).with_for_update())).scalar_one_or_none()
        if dispatch is None or dispatch.event_type not in _EVENTS or dispatch.status not in {"PENDING", "PROCESSING"}:
            raise PredictionWorkerError("WORKER_DISPATCH_FENCE_INVALID")
        if dict(dispatch.payload or {}) != {"job_id": job.id, "attempt": message.attempt, "dispatch_id": message.dispatch_id}:
            raise PredictionWorkerError("WORKER_DISPATCH_FENCE_INVALID")
        attempt = (await session.execute(select(JobAttempt).where(
            JobAttempt.job_id == job.id, JobAttempt.attempt == message.attempt,
        ).with_for_update())).scalar_one_or_none()
        row = (await session.execute(select(PredictionRow).where(
            PredictionRow.job_id == job.id,
        ).with_for_update())).scalar_one_or_none()
        if attempt is None or row is None or row.id != ref.id:
            raise PredictionWorkerError("WORKER_JOB_RESOURCE_INVALID")
        return job, attempt, row, dispatch

    @staticmethod
    def _snapshot(job: Job, row: PredictionRow, dispatch: OutboxEvent, attempt: JobAttempt) -> PredictionExecutionSnapshot:
        try:
            model = ModelSnapshot.model_validate(row.model_snapshot)
            inputs = PredictionExecutionInputSnapshot.model_validate(row.input_snapshot)
            request = build_prediction_execution_request(
                row.request_snapshot,
                project_id=row.project_id,
                expected_type=row.chromatography_type,
                expected_tool=row.tool,
                expected_schema_version=row.schema_version,
                model_snapshot=model,
                input_snapshot=inputs,
            )
            validate_hplc_execution_binding(
                request=request, model_snapshot=model, input_snapshot=inputs,
            )
            validate_gc_execution_binding(
                request=request, model_snapshot=model, input_snapshot=inputs,
            )
        except (ValidationError, TypeError, ValueError) as exc:
            raise PredictionWorkerError("PREDICTION_SNAPSHOT_INVALID") from exc
        return PredictionExecutionSnapshot(
            prediction_id=row.id, project_id=row.project_id, job_id=job.id, attempt=int(job.attempt),
            status=JobStatus(job.status), type_id=row.chromatography_type, tool=row.tool,
            schema_version=row.schema_version, request=request, model_snapshot=model, input_snapshot=inputs,
            dispatch_id=dispatch.dispatch_id, execution_token=attempt.execution_token,
            fencing_token=attempt.fencing_token, lease_until=attempt.lease_until,
        )

    async def claim(self, message: PredictionWorkerMessage) -> _Claim:
        async with self.session_factory() as session:
            async with session.begin():
                job, attempt, row, dispatch = await self._rows(session, message)
                status = JobStatus(job.status)
                if status in _TERMINAL:
                    return _Claim(None, dispatch.event_type, replayed=True, status=status)
                if dispatch.event_type == "job.cancel":
                    return _Claim(None, dispatch.event_type, status=status)
                if status is not JobStatus.QUEUED or attempt.status != JobStatus.QUEUED.value:
                    return _Claim(None, dispatch.event_type, replayed=True, status=status)
                now = _now()
                job.status = JobStatus.RUNNING.value
                job.started_at = job.started_at or now
                job.updated_at = now
                job.revision += 1
                job.etag = _job_etag(job)
                attempt.status = JobStatus.RUNNING.value
                attempt.started_at = attempt.started_at or now
                attempt.execution_token = f"exec_{uuid4().hex}"
                attempt.fencing_token = int(attempt.fencing_token or 0) + 1
                attempt.lease_until = now + timedelta(seconds=self.lease_seconds)
                row.status = JobStatus.RUNNING.value
                row.updated_at = now
                row.revision += 1
                row.etag = _etag(row)
                dispatch.status = "PROCESSING"
                session.add(JobEvent(
                    id=f"event_{uuid4().hex}", job_id=job.id, stream_id=job.id,
                    seq=(await self._next_seq(session, job.id)), type="job.running", schema_version="1.0",
                    data={"status": JobStatus.RUNNING.value, "attempt": message.attempt}, timestamp=now,
                ))
                return _Claim(self._snapshot(job, row, dispatch, attempt), dispatch.event_type, status=JobStatus.RUNNING)

    @staticmethod
    async def _next_seq(session: AsyncSession, job_id: str) -> int:
        rows = (await session.execute(select(JobEvent.seq).where(JobEvent.job_id == job_id).order_by(JobEvent.seq.desc()).limit(1))).first()
        return int(rows[0]) + 1 if rows else 0

    async def cancel(self, message: PredictionWorkerMessage) -> ProductionWorkerResult:
        async with self.session_factory() as session:
            async with session.begin():
                job, attempt, row, dispatch = await self._rows(session, message)
                status = JobStatus(job.status)
                if status in _TERMINAL:
                    return ProductionWorkerResult(status=status, replayed=True)
                if dispatch.event_type != "job.cancel":
                    raise PredictionWorkerError("WORKER_EVENT_TYPE_INVALID")
                if status is JobStatus.QUEUED and attempt.status == JobStatus.QUEUED.value:
                    now = _now()
                    target = JobStatus.CANCELED.value
                    job.status = target; attempt.status = target; row.status = target
                    job.finished_at = now; job.updated_at = now; job.revision += 1; job.etag = _job_etag(job)
                    row.completed_at = now; row.updated_at = now; row.revision += 1; row.etag = _etag(row)
                    await _update_record_projection(session, row.prediction_record_id, status=target, result_ref=None)
                    dispatch.status = "PROCESSING"
                    session.add(JobEvent(id=f"event_{uuid4().hex}", job_id=job.id, stream_id=job.id,
                        seq=(await self._next_seq(session, job.id)), type="job.canceled", schema_version="1.0",
                        data={"status": target, "attempt": message.attempt}, timestamp=now))
                    return ProductionWorkerResult(status=JobStatus.CANCELED)
                if status is JobStatus.RUNNING:
                    job.status = JobStatus.CANCEL_REQUESTED.value; attempt.status = JobStatus.CANCEL_REQUESTED.value
                    job.updated_at = _now(); job.revision += 1; job.etag = _job_etag(job)
                    session.add(JobEvent(id=f"event_{uuid4().hex}", job_id=job.id, stream_id=job.id,
                        seq=(await self._next_seq(session, job.id)), type="job.cancel_requested", schema_version="1.0",
                        data={"status": JobStatus.CANCEL_REQUESTED.value, "attempt": message.attempt}, timestamp=_now()))
                    return ProductionWorkerResult(status=JobStatus.CANCEL_REQUESTED)
                return ProductionWorkerResult(status=status, replayed=True)

    async def finish(self, snapshot: PredictionExecutionSnapshot, *, result: object | None, error: PublicErrorSummary | None) -> bool | JobStatus:
        async with self.session_factory() as session:
            async with session.begin():
                job = (await session.execute(select(Job).where(Job.id == snapshot.job_id).with_for_update())).scalar_one_or_none()
                if job is None or int(job.attempt) != snapshot.attempt or job.status not in {JobStatus.RUNNING.value, JobStatus.CANCEL_REQUESTED.value}:
                    return False
                attempt = (await session.execute(select(JobAttempt).where(JobAttempt.job_id == snapshot.job_id, JobAttempt.attempt == snapshot.attempt).with_for_update())).scalar_one_or_none()
                if attempt is None or attempt.execution_token != snapshot.execution_token or attempt.fencing_token != snapshot.fencing_token or attempt.status not in {JobStatus.RUNNING.value, JobStatus.CANCEL_REQUESTED.value}:
                    return False
                if attempt.lease_until is not None and attempt.lease_until <= _now():
                    return False
                row = (await session.execute(select(PredictionRow).where(PredictionRow.id == snapshot.prediction_id).with_for_update())).scalar_one_or_none()
                if row is None:
                    return False
                now = _now()
                # A cancellation wins the race.  A late model result must not
                # resurrect a canceled Job or publish a result.
                canceled = job.status == JobStatus.CANCEL_REQUESTED.value
                status = JobStatus.CANCELED.value if canceled else (JobStatus.FAILED.value if error is not None else JobStatus.SUCCEEDED.value)
                result_json = None if canceled else _json(result)
                final_error = None if canceled else _json(error)
                result_warnings = _result_warnings(result) if status == JobStatus.SUCCEEDED.value else []
                if result_warnings:
                    row.warnings = _merge_prediction_warnings(getattr(row, "warnings", []), result_warnings)
                row.status = status; row.result = result_json; row.error = final_error; row.completed_at = now; row.updated_at = now; row.revision += 1; row.etag = _etag(row)
                await _update_record_projection(
                    session,
                    row.prediction_record_id,
                    status=status,
                    result_ref={"type": "prediction", "id": row.id} if status == JobStatus.SUCCEEDED.value else None,
                    warnings=result_warnings if status == JobStatus.SUCCEEDED.value else None,
                )
                job.status = status; job.result_ref = {"type": "prediction", "id": row.id} if status == JobStatus.SUCCEEDED.value else None; job.error = final_error; job.finished_at = now; job.updated_at = now; job.revision += 1
                if status in {JobStatus.SUCCEEDED.value, JobStatus.FAILED.value}:
                    _finish_prediction_progress(job)
                job.etag = _job_etag(job)
                attempt.status = status; attempt.result_ref = job.result_ref; attempt.error = _json(error); attempt.finished_at = now; attempt.lease_until = None
                session.add(JobEvent(id=f"event_{uuid4().hex}", job_id=job.id, stream_id=job.id,
                    seq=(await self._next_seq(session, job.id)), type="job.canceled" if canceled else ("job.succeeded" if error is None else "job.failed"), schema_version="1.0",
                    data={"status": status, "attempt": snapshot.attempt, "error": final_error}, timestamp=now))
                attempt.error = final_error
                return JobStatus(status) if canceled else True


class PredictionProductionWorker:
    """Execute registered chromatography adapters through the verified registry."""

    def __init__(self, *, registry: CapabilityRegistry, store: PredictionExecutionStore) -> None:
        self.registry, self.store = registry, store

    async def handle(self, message: PredictionWorkerMessage | Mapping[str, Any], *, event_type: str | None = None) -> ProductionWorkerResult:
        message = message if isinstance(message, PredictionWorkerMessage) else PredictionWorkerMessage.from_payload(message)
        try:
            claim = await self.store.claim(message)
            if event_type is not None and claim.event_type != event_type:
                raise PredictionWorkerError("WORKER_EVENT_TYPE_INVALID")
            if claim.event_type == "job.cancel":
                return await self.store.cancel(message)
            if event_type is not None and event_type not in _EXECUTE_EVENTS:
                raise PredictionWorkerError("WORKER_EVENT_TYPE_INVALID")
            if claim.replayed or claim.snapshot is None:
                return ProductionWorkerResult(status=claim.status, replayed=True)
            snapshot = claim.snapshot
            try:
                execute_snapshot = self.registry.execute_snapshot
                execution_kwargs = {"model_snapshot": snapshot.model_snapshot}
                if "normalized_structures" in inspect.signature(execute_snapshot).parameters:
                    execution_kwargs["normalized_structures"] = snapshot.input_snapshot.normalized_structures
                if "normalized_modifier_structures" in inspect.signature(execute_snapshot).parameters:
                    execution_kwargs["normalized_modifier_structures"] = {
                        value.structure_ref: value.normalized_smiles
                        for value in snapshot.input_snapshot.modifier_structures
                    }
                result = execute_snapshot(snapshot.type_id, snapshot.request, **execution_kwargs)
            except Exception as exc:  # safe public failure; never fabricate a result
                error = _public_error(exc)
                await self.store.finish(snapshot, result=None, error=error)
                return ProductionWorkerResult(status=JobStatus.FAILED, error=error)
            finished = await self.store.finish(snapshot, result=result, error=None)
            if finished is JobStatus.CANCELED:
                return ProductionWorkerResult(status=JobStatus.CANCELED, replayed=True)
            if not finished:
                return ProductionWorkerResult(status=JobStatus.RUNNING, replayed=True)
            return ProductionWorkerResult(status=JobStatus.SUCCEEDED, result=result)
        except PredictionWorkerError:
            raise

    async def execute(self, message: PredictionWorkerMessage | Mapping[str, Any]) -> ProductionWorkerResult:
        """Stable execute entrypoint; event type is reloaded from the DB row."""
        return await self.handle(message)

    async def cancel(self, message: PredictionWorkerMessage | Mapping[str, Any]) -> ProductionWorkerResult:
        """Stable cancel entrypoint; only the persisted cancel event is accepted."""
        return await self.handle(message, event_type="job.cancel")

    async def retry(self, message: PredictionWorkerMessage | Mapping[str, Any]) -> ProductionWorkerResult:
        """Retry uses the same execute path with the new attempt fence."""
        return await self.handle(message, event_type="job.execute")


AsyncPredictionWorker = PredictionProductionWorker


def build_prediction_worker_handler(*, session_factory: async_sessionmaker[AsyncSession], registry: CapabilityRegistry) -> PredictionProductionWorker:
    """Factory imported by worker assembly; it does not create or ack outbox rows."""
    return PredictionProductionWorker(registry=registry, store=AsyncPredictionExecutionStore(session_factory))


__all__ = [
    "AsyncPredictionExecutionStore", "AsyncPredictionWorker", "PredictionExecutionStore",
    "PredictionProductionWorker", "PredictionWorkerError", "PredictionWorkerMessage",
    "ProductionWorkerResult", "build_prediction_worker_handler",
]
