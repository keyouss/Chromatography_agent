"""Async production handler for canonical Training Job deliveries.

The queue payload is deliberately limited to ``job_id``, ``attempt`` and
``dispatch_id``.  Claims and finishes use short AsyncSession transactions;
the reviewed template and candidate ports run after the claim commits.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import inspect
from typing import Any, Callable, Mapping
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chrom_platform.core.dto import ResourceRef
from chrom_platform.infrastructure.models import Job, JobAttempt, JobEvent, OutboxEvent
from chrom_platform.task_core.state import JobStatus

from .persistence import TrainingExperimentRow, TrainingJobRow
from .schemas import CandidateResult, Experiment, WorkerMessage


_TERMINAL = frozenset({"SUCCEEDED", "PARTIAL", "FAILED", "CANCELED", "TIMED_OUT", "INTERRUPTED"})
_EVENTS = frozenset({"job.execute", "job.cancel"})


class TrainingWorkerError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class TrainingWorkerResult:
    status: JobStatus
    result: object | None = None
    error: Mapping[str, Any] | None = None
    replayed: bool = False


@dataclass(frozen=True)
class _Claim:
    message: WorkerMessage
    experiment: Experiment
    execution_token: str
    fencing_token: int


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json(value: object) -> Any:
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def _safe_error(code: str, message: str = "训练执行失败") -> dict[str, Any]:
    return {"code": code, "message": message, "retryable": True}


async def _call(value: Any, *args: Any, **kwargs: Any) -> Any:
    if not callable(value):
        return value
    result = value(*args, **kwargs)
    return await result if inspect.isawaitable(result) else result


class AsyncTrainingExecutionStore:
    """Canonical Job/Attempt/Outbox fence plus the Training projection."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], *, lease_seconds: int = 60):
        self.session_factory = session_factory
        self.lease_seconds = max(1, int(lease_seconds))

    @staticmethod
    def _validate_job(job: Job, message: WorkerMessage) -> ResourceRef:
        if job.kind != "training" or int(job.attempt) != message.attempt:
            raise TrainingWorkerError("WORKER_JOB_FENCE_INVALID")
        try:
            ref = ResourceRef.model_validate(job.resource_ref)
        except ValidationError as exc:
            raise TrainingWorkerError("WORKER_JOB_RESOURCE_INVALID") from exc
        if ref.type != "experiment":
            raise TrainingWorkerError("WORKER_JOB_RESOURCE_INVALID")
        return ref

    async def _rows(self, session: AsyncSession, message: WorkerMessage):
        job = (await session.execute(select(Job).where(Job.id == message.job_id).with_for_update())).scalar_one_or_none()
        if job is None:
            raise TrainingWorkerError("WORKER_JOB_NOT_FOUND")
        ref = self._validate_job(job, message)
        dispatch = (await session.execute(select(OutboxEvent).where(
            OutboxEvent.dispatch_id == message.dispatch_id,
            OutboxEvent.aggregate_type == "job",
            OutboxEvent.aggregate_id == job.id,
        ).with_for_update())).scalar_one_or_none()
        if dispatch is None or dispatch.event_type not in _EVENTS or dispatch.status not in {"PENDING", "PROCESSING"}:
            raise TrainingWorkerError("WORKER_DISPATCH_FENCE_INVALID")
        if dict(dispatch.payload or {}) != message.model_dump(mode="json"):
            raise TrainingWorkerError("WORKER_DISPATCH_FENCE_INVALID")
        attempt = (await session.execute(select(JobAttempt).where(
            JobAttempt.job_id == job.id, JobAttempt.attempt == message.attempt,
        ).with_for_update())).scalar_one_or_none()
        module_job = (await session.execute(select(TrainingJobRow).where(TrainingJobRow.id == job.id).with_for_update())).scalar_one_or_none()
        experiment = (await session.execute(select(TrainingExperimentRow).where(
            TrainingExperimentRow.id == ref.id,
            TrainingExperimentRow.project_id == job.project_id,
        ).with_for_update())).scalar_one_or_none()
        if attempt is None or module_job is None or experiment is None or module_job.dispatch_id != message.dispatch_id:
            raise TrainingWorkerError("WORKER_JOB_RESOURCE_INVALID")
        if module_job.attempt != message.attempt:
            raise TrainingWorkerError("WORKER_JOB_FENCE_INVALID")
        try:
            value = Experiment.model_validate({
                "id": experiment.id, "revision": experiment.revision,
                "config_hash": experiment.config_hash, "etag": experiment.etag,
                "spec": experiment.spec, "status": experiment.status,
                "validation_report": experiment.validation_report, "job_id": experiment.job_id,
                "current_attempt": experiment.current_attempt, "candidate_status": experiment.candidate_status,
                "candidate_model_id": experiment.candidate_model_id, "spec_locked": experiment.spec_locked,
                "created_at": experiment.created_at, "started_at": experiment.started_at,
                "finished_at": experiment.finished_at, "error": experiment.error,
            })
        except (ValidationError, TypeError, ValueError) as exc:
            raise TrainingWorkerError("TRAINING_SNAPSHOT_INVALID") from exc
        return job, attempt, module_job, experiment, dispatch, value

    async def claim(self, message: WorkerMessage) -> tuple[_Claim | None, str]:
        async with self.session_factory() as session:
            async with session.begin():
                job, attempt, module_job, row, dispatch, experiment = await self._rows(session, message)
                status = str(job.status)
                if status in _TERMINAL:
                    return None, status
                if dispatch.event_type == "job.cancel":
                    return None, status
                if status != "QUEUED" or attempt.status != "QUEUED" or module_job.status != "QUEUED":
                    return None, status
                now = _now()
                token, fence = f"exec_{uuid4().hex}", int(attempt.fencing_token or 0) + 1
                job.status = attempt.status = module_job.status = row.status = "RUNNING"
                job.started_at = job.started_at or now
                job.updated_at = now
                job.revision = int(job.revision or 1) + 1
                job.etag = str(job.revision)
                attempt.started_at = attempt.started_at or now
                attempt.execution_token, attempt.fencing_token = token, fence
                attempt.lease_until = now + timedelta(seconds=self.lease_seconds)
                module_job.started_at = job.started_at
                module_job.execution_token, module_job.fencing_token = token, fence
                module_job.lease_until = attempt.lease_until
                row.started_at = job.started_at
                row.current_attempt = message.attempt
                await self._event(session, job.id, "job.running", {"status": "RUNNING", "attempt": message.attempt})
                return _Claim(message, experiment, token, fence), "RUNNING"

    async def cancel(self, message: WorkerMessage) -> TrainingWorkerResult:
        async with self.session_factory() as session:
            async with session.begin():
                job, attempt, module_job, row, dispatch, _ = await self._rows(session, message)
                status = JobStatus(job.status)
                if status in _TERMINAL:
                    return TrainingWorkerResult(status, replayed=True)
                if dispatch.event_type != "job.cancel":
                    raise TrainingWorkerError("WORKER_EVENT_TYPE_INVALID")
                now = _now()
                if status is JobStatus.QUEUED:
                    for value in (job, attempt, module_job, row):
                        value.status = "CANCELED"
                    job.finished_at = module_job.finished_at = row.finished_at = now
                    job.updated_at = now; job.revision = int(job.revision or 1) + 1; job.etag = str(job.revision)
                    await self._event(session, job.id, "job.canceled", {"status": "CANCELED", "attempt": message.attempt})
                    return TrainingWorkerResult(JobStatus.CANCELED)
                if status is JobStatus.RUNNING:
                    job.status = attempt.status = module_job.status = row.status = "CANCEL_REQUESTED"
                    job.updated_at = now; job.revision = int(job.revision or 1) + 1; job.etag = str(job.revision)
                    await self._event(session, job.id, "job.cancel_requested", {"status": "CANCEL_REQUESTED", "attempt": message.attempt})
                    return TrainingWorkerResult(JobStatus.CANCEL_REQUESTED)
                return TrainingWorkerResult(status, replayed=True)

    async def finish(self, claim: _Claim, *, status: str, result: CandidateResult | None = None, error: Mapping[str, Any] | None = None, candidate_id: str | None = None, candidate_status: str | None = None) -> bool | JobStatus:
        async with self.session_factory() as session:
            async with session.begin():
                job, attempt, module_job, row, _, _ = await self._rows(session, claim.message)
                if int(job.attempt) != claim.message.attempt or attempt.execution_token != claim.execution_token or int(attempt.fencing_token or 0) != claim.fencing_token or attempt.status not in {"RUNNING", "CANCEL_REQUESTED"}:
                    return False
                canceled = str(job.status) == "CANCEL_REQUESTED"
                final = "CANCELED" if canceled else status
                safe_result = None if canceled else result
                safe_error = None if canceled else _json(error)
                now = _now()
                job.status = attempt.status = module_job.status = row.status = final
                job.finished_at = module_job.finished_at = row.finished_at = now
                job.updated_at = now; job.result_ref = {"type": "artifact", "id": result.artifact_id} if safe_result else None; job.error = safe_error; job.revision = int(job.revision or 1) + 1; job.etag = str(job.revision)
                attempt.result_ref = _json(job.result_ref); attempt.error = safe_error; attempt.finished_at = now; attempt.lease_until = None
                module_job.result_ref = _json(job.result_ref); module_job.error = safe_error; module_job.lease_until = None
                row.finished_at, row.error = now, safe_error
                row.candidate_status = candidate_status or ("CREATED" if final == "SUCCEEDED" and candidate_id else "VALIDATING" if final == "SUCCEEDED" else "NOT_REQUESTED")
                row.candidate_model_id = candidate_id if candidate_status == "CREATED" or candidate_id else None
                await self._event(session, job.id, "job.canceled" if canceled else f"job.{final.lower()}", {"status": final, "attempt": claim.message.attempt, "error": safe_error})
                return JobStatus.CANCELED if canceled else True

    @staticmethod
    async def _event(session: AsyncSession, job_id: str, event_type: str, data: Mapping[str, Any]) -> None:
        latest = (await session.execute(select(JobEvent.seq).where(JobEvent.job_id == job_id).order_by(JobEvent.seq.desc()).limit(1))).first()
        seq = int(latest[0]) + 1 if latest else 0
        session.add(JobEvent(id=f"event_{uuid4().hex}", job_id=job_id, stream_id=job_id, seq=seq, type=event_type, schema_version="1.0", data=dict(data), timestamp=_now()))


class TrainingProductionWorker:
    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession], templates: object | None = None, candidate_registration: object | None = None, data_loader: object | None = None, executor: Callable[..., Any] | None = None, lease_seconds: int = 60):
        self.store = AsyncTrainingExecutionStore(session_factory, lease_seconds=lease_seconds)
        self.templates, self.candidate_registration, self.data_loader, self.executor = templates, candidate_registration, data_loader, executor

    async def execute(self, message: WorkerMessage | Mapping[str, Any]) -> TrainingWorkerResult:
        message = message if isinstance(message, WorkerMessage) else WorkerMessage.model_validate(message)
        claim, status = await self.store.claim(message)
        if claim is None:
            return TrainingWorkerResult(JobStatus(status), replayed=True)
        handler = self.executor
        template = None
        try:
            spec = claim.experiment.spec
            getter = getattr(self.templates, "get", None)
            lookup = getattr(self.templates, "handler", None)
            if callable(getter):
                template = await _call(getter, spec.execution.template_id, spec.execution.template_version)
            if handler is None and callable(lookup):
                handler = await _call(lookup, spec.execution.template_id, spec.execution.template_version)
            if template is None or getattr(template, "health", None) != "READY" or handler is None:
                raise TrainingWorkerError("TRAINING_TEMPLATE_UNAVAILABLE")
            if self.data_loader is None or await _call(self.data_loader, spec.data_version_id) is None:
                raise TrainingWorkerError("TRAINING_DATA_UNAVAILABLE")
            if self.candidate_registration is None:
                raise TrainingWorkerError("TRAINING_CANDIDATE_DEPENDENCY_UNAVAILABLE")
            value = await _call(handler, spec, attempt=message.attempt)
            result = value if isinstance(value, CandidateResult) else CandidateResult.model_validate(value)
        except TrainingWorkerError as exc:
            error = _safe_error(exc.code)
            await self.store.finish(claim, status="FAILED", error=error)
            return TrainingWorkerResult(JobStatus.FAILED, error=error)
        except TimeoutError:
            error = _safe_error("TRAINING_TIMEOUT", "训练执行超时")
            await self.store.finish(claim, status="TIMED_OUT", error=error)
            return TrainingWorkerResult(JobStatus.TIMED_OUT, error=error)
        except Exception:
            error = _safe_error("TRAINING_EXECUTION_FAILED")
            await self.store.finish(claim, status="FAILED", error=error)
            return TrainingWorkerResult(JobStatus.FAILED, error=error)
        candidate = None
        try:
            register = getattr(self.candidate_registration, "register_candidate", self.candidate_registration)
            candidate = await _call(register, project_id=claim.experiment.spec.project_id, experiment=claim.experiment, result=result)
            candidate_id = candidate if isinstance(candidate, str) else candidate.get("id") if isinstance(candidate, Mapping) else getattr(candidate, "id", None)
            if not candidate_id:
                raise TrainingWorkerError("TRAINING_CANDIDATE_REGISTRATION_INVALID")
        except TrainingWorkerError as exc:
            error = _safe_error(exc.code)
            await self.store.finish(claim, status="FAILED", error=error, candidate_status="REJECTED")
            return TrainingWorkerResult(JobStatus.FAILED, error=error)
        except Exception:
            error = _safe_error("TRAINING_CANDIDATE_REGISTRATION_FAILED")
            await self.store.finish(claim, status="FAILED", error=error, candidate_status="REJECTED")
            return TrainingWorkerResult(JobStatus.FAILED, error=error)
        finished = await self.store.finish(claim, status="SUCCEEDED", result=result, candidate_id=str(candidate_id), candidate_status="CREATED")
        if finished is JobStatus.CANCELED:
            compensate = getattr(self.candidate_registration, "compensate_candidate", None)
            if callable(compensate):
                try:
                    await _call(compensate, candidate=candidate, project_id=claim.experiment.spec.project_id)
                except Exception:
                    pass
            return TrainingWorkerResult(JobStatus.CANCELED, replayed=True)
        if not finished:
            compensate = getattr(self.candidate_registration, "compensate_candidate", None)
            if callable(compensate):
                try:
                    await _call(compensate, candidate=candidate, project_id=claim.experiment.spec.project_id)
                except Exception:
                    pass
            return TrainingWorkerResult(JobStatus.RUNNING, replayed=True)
        return TrainingWorkerResult(JobStatus.SUCCEEDED, result=result)

    async def cancel(self, message: WorkerMessage | Mapping[str, Any]) -> TrainingWorkerResult:
        message = message if isinstance(message, WorkerMessage) else WorkerMessage.model_validate(message)
        return await self.store.cancel(message)

    async def retry(self, message: WorkerMessage | Mapping[str, Any]) -> TrainingWorkerResult:
        return await self.execute(message)

    async def handle(self, message: WorkerMessage | Mapping[str, Any], *, event_type: str | None = None) -> TrainingWorkerResult:
        return await (self.cancel(message) if event_type == "job.cancel" else self.execute(message))


AsyncTrainingWorker = TrainingProductionWorker


def build_training_worker_handler(*, session_factory: async_sessionmaker[AsyncSession], templates: object | None = None, candidate_registration: object | None = None, data_loader: object | None = None, executor: Callable[..., Any] | None = None, lease_seconds: int = 60) -> TrainingProductionWorker:
    return TrainingProductionWorker(session_factory=session_factory, templates=templates, candidate_registration=candidate_registration, data_loader=data_loader, executor=executor, lease_seconds=lease_seconds)


__all__ = ["AsyncTrainingExecutionStore", "AsyncTrainingWorker", "TrainingProductionWorker", "TrainingWorkerError", "TrainingWorkerResult", "build_training_worker_handler"]
