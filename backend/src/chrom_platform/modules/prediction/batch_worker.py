"""Synchronous batch worker core used by the reviewed worker assembly.

The queue carries only a batch job reference.  Each row is executed through
the already verified prediction registry and receives its own durable result.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import time
from collections.abc import Callable, Collection
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Mapping

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session
from pydantic import TypeAdapter, ValidationError

from chrom_platform.artifact_core.models import Artifact
from chrom_platform.core.dto import ActorContext
from chrom_platform.infrastructure.models import Job, JobAttempt, JobEvent, OutboxEvent
from chrom_platform.task_core.repository import SQLAlchemyTaskRepository
from chrom_platform.task_core.service import OperationContext, sync_job_lifecycle
from chrom_platform.task_core.state import JobStatus

from .batch_service import (
    BatchExportRow,
    BatchPredictionRow,
    BatchValidationItemRow,
    BatchRowResultRow,
    BatchValidationRow,
    BatchPredictionService,
    BatchServiceError,
    _etag as _batch_etag,
    _hash,
    default_batch_template_registry,
)
from .batch import BatchResolvedInput, TlcBatchResolvedInputV1, TlcBatchResolvedInputV2
from .production_worker import _merge_prediction_warnings, _merge_record_warnings, _result_warnings, _warning_items
from .schemas import (
    ModelSnapshot, PredictionInputSnapshot,
    TlcInput, TlcPredictionCreate,
)


_BATCH_RESOLVED_INPUT = TypeAdapter(BatchResolvedInput)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class BatchWorkerError(RuntimeError):
    pass


class BatchPredictionWorker:
    def __init__(self, session: Session, registry: object, *, enabled_batch_types: Collection[str] | None = None, record_writer: object | None = None) -> None:
        self.session, self.registry = session, registry
        self.enabled_batch_types = frozenset(enabled_batch_types or ())
        self.record_writer = record_writer

    @staticmethod
    def _error(exc: BaseException) -> dict[str, str]:
        payload = getattr(exc, "payload", None)
        return {
            "code": getattr(payload, "code", "PREDICTION_EXECUTION_FAILED"),
            "message": getattr(payload, "message", "预测执行失败"),
        }

    @staticmethod
    def _execution_input(
        batch: BatchPredictionRow,
        validation: BatchValidationRow,
        row: BatchRowResultRow,
    ) -> tuple[object, list[str], dict[str, str] | None]:
        try:
            resolved = _BATCH_RESOLVED_INPUT.validate_python(row.input_snapshot or {})
        except ValidationError as exc:
            raise BatchWorkerError("BATCH_LEGACY_DATA_REQUIRES_REVALIDATION") from exc
        if (
            resolved.chromatography_type != batch.chromatography_type
            or resolved.template_version != batch.template_version
            or resolved.project_id != batch.project_id
            or resolved.draft_revision != validation.draft_revision
            or resolved.validation_id != validation.id
        ):
            raise BatchWorkerError("BATCH_VALIDATION_SNAPSHOT_INVALID")
        if isinstance(resolved, TlcBatchResolvedInputV1):
            # Legacy reference-only TLC rows do not freeze the structure used
            # by the model. Revalidation with tlc-batch-2 is required.
            raise BatchWorkerError("BATCH_LEGACY_DATA_REQUIRES_REVALIDATION")
        if isinstance(resolved, TlcBatchResolvedInputV2):
            request = TlcPredictionCreate.model_validate({
                "project_id": batch.project_id,
                "schema_version": batch.schema_version,
                "model_selection": batch.model_selection,
                "chromatography_type": "tlc",
                "tool": "single",
                "structure_refs": [resolved.structure_ref],
                "input": resolved.solvent_conditions.model_dump(mode="json"),
            })
            normalized_structures = [resolved.normalized_smiles]
            normalized_modifier_structures = None
        else:
            request = resolved.request
            normalized_structures = list(resolved.normalized_structures)
            normalized_modifier_structures = getattr(resolved, "normalized_modifier_structures", None)
            if normalized_modifier_structures is not None:
                normalized_modifier_structures = dict(normalized_modifier_structures)
                modifier_refs = {
                    item.structure_ref for item in request.input.modifier_components
                }
                if set(normalized_modifier_structures) != modifier_refs:
                    raise BatchWorkerError("BATCH_VALIDATION_SNAPSHOT_INVALID")
        if (
            request.project_id != batch.project_id
            or request.chromatography_type != batch.chromatography_type
            or request.schema_version != batch.schema_version
            or _hash(request.model_selection.model_dump(mode="json")) != _hash(batch.model_selection)
            or len(normalized_structures) != len(request.structure_refs)
        ):
            raise BatchWorkerError("BATCH_VALIDATION_SNAPSHOT_INVALID")
        return request, normalized_structures, normalized_modifier_structures

    @staticmethod
    def _public_input_snapshot(request: object, normalized_structures: list[str]) -> dict[str, Any]:
        snapshot = PredictionInputSnapshot(
            structure_refs=list(request.structure_refs),
            normalized_structures=normalized_structures,
            input=request.input,
        )
        return snapshot.model_dump(mode="json")

    def _write_record(
        self,
        batch: BatchPredictionRow,
        row: BatchRowResultRow,
        model: ModelSnapshot,
        input_snapshot: dict[str, Any],
        *,
        status: str,
        warnings: list[dict[str, Any]],
    ) -> None:
        if self.record_writer is None:
            return
        self.record_writer.write_batch_row_record(
            session=self.session,
            batch_id=batch.id,
            row_id=row.row_id,
            project_id=batch.project_id,
            actor_id=batch.actor_id,
            source="service",
            chromatography_type=batch.chromatography_type,
            model_snapshot=model.model_dump(mode="json"),
            input_snapshot=input_snapshot,
            status=status,
            warnings=_merge_record_warnings([], warnings),
            artifact_refs=[{"type": "artifact", "id": batch.input_artifact_id}],
        )

    @staticmethod
    def _assert_resolved_snapshot(session: Session, batch: BatchPredictionRow, validation: BatchValidationRow) -> None:
        """Reject pre-0014 rows until they have a fresh immutable validation.

        Migration 0014 intentionally leaves snapshot fields nullable so legacy
        records remain queryable. They are not executable from their mutable
        historical inputs. Revalidation must materialize a complete new set of
        validation items and matching result projections before a worker may
        claim the batch.
        """

        items = list(session.scalars(select(BatchValidationItemRow).where(
            BatchValidationItemRow.validation_id == validation.id,
        ).order_by(BatchValidationItemRow.source_row_number, BatchValidationItemRow.row_id)))
        if (not validation.resolved_input_hash or not items
                or len(items) != int(validation.total_rows or 0)
                or sum(item.status == "VALID" for item in items) != int(validation.valid_rows or 0)
                or sum(item.status == "INVALID" for item in items) != int(validation.invalid_rows or 0)):
            raise BatchWorkerError("BATCH_LEGACY_DATA_REQUIRES_REVALIDATION")

        aggregate: list[dict[str, str]] = []
        item_by_row: dict[str, BatchValidationItemRow] = {}
        for item in items:
            if item.row_id in item_by_row or item.status not in {"VALID", "INVALID"}:
                raise BatchWorkerError("BATCH_VALIDATION_SNAPSHOT_INVALID")
            item_by_row[item.row_id] = item
            if item.status == "VALID":
                snapshot, digest = item.resolved_input_snapshot, item.resolved_input_hash
                if not isinstance(snapshot, dict) or not digest or _hash(snapshot) != digest:
                    raise BatchWorkerError("BATCH_LEGACY_DATA_REQUIRES_REVALIDATION")
                try:
                    resolved = _BATCH_RESOLVED_INPUT.validate_python(snapshot)
                except ValidationError as exc:
                    raise BatchWorkerError("BATCH_LEGACY_DATA_REQUIRES_REVALIDATION") from exc
                if (
                    resolved.chromatography_type != batch.chromatography_type
                    or resolved.template_version != batch.template_version
                    or resolved.project_id != batch.project_id
                    or resolved.draft_revision != validation.draft_revision
                    or resolved.validation_id != validation.id
                ):
                    raise BatchWorkerError("BATCH_VALIDATION_SNAPSHOT_INVALID")
                if isinstance(resolved, TlcBatchResolvedInputV1):
                    raise BatchWorkerError("BATCH_LEGACY_DATA_REQUIRES_REVALIDATION")
                aggregate.append({"row_id": item.row_id, "input_hash": digest})
        if _hash(aggregate) != validation.resolved_input_hash:
            raise BatchWorkerError("BATCH_VALIDATION_SNAPSHOT_INVALID")

        projections = list(session.scalars(select(BatchRowResultRow).where(
            BatchRowResultRow.batch_id == batch.id,
            BatchRowResultRow.validation_id == validation.id,
        )))
        projections_by_row: dict[str, list[BatchRowResultRow]] = {}
        for result in projections:
            projections_by_row.setdefault(result.row_id, []).append(result)
            item = item_by_row.get(result.row_id)
            if item is None:
                raise BatchWorkerError("BATCH_VALIDATION_SNAPSHOT_INVALID")
            if item.status == "VALID":
                if (result.input_snapshot != item.resolved_input_snapshot
                        or _hash(result.input_snapshot) != item.resolved_input_hash):
                    raise BatchWorkerError("BATCH_LEGACY_DATA_REQUIRES_REVALIDATION")
        if set(projections_by_row) != set(item_by_row):
            # This also prevents the legacy failure mode where the query below
            # finds no rows and an empty batch is finalized as successful.
            raise BatchWorkerError("BATCH_LEGACY_DATA_REQUIRES_REVALIDATION")

    def execute(self, batch_id: str, *, actor: ActorContext | None = None, expected_attempt: int | None = None, expected_fencing_token: int | None = None, lease_guard: object | None = None) -> JobStatus:
        batch = self.session.scalar(select(BatchPredictionRow).where(BatchPredictionRow.id == batch_id).with_for_update())
        if batch is None or (actor is not None and not actor.can_access_project(batch.project_id)):
            raise BatchWorkerError("BATCH_NOT_FOUND")
        if batch.chromatography_type not in self.enabled_batch_types:
            raise BatchWorkerError("BATCH_TYPE_NOT_ENABLED")
        if not batch.job_id or not batch.submitted_at or not batch.validation_id:
            raise BatchWorkerError("BATCH_JOB_NOT_FOUND")
        validation = self.session.scalar(select(BatchValidationRow).where(BatchValidationRow.id == batch.validation_id))
        if validation is None or validation.status != "READY":
            raise BatchWorkerError("BATCH_VALIDATION_SNAPSHOT_INVALID")
        self._assert_resolved_snapshot(self.session, batch, validation)
        job = self.session.scalar(select(Job).where(Job.id == batch.job_id).with_for_update())
        if job is None or job.kind != "batch_prediction":
            raise BatchWorkerError("BATCH_JOB_FENCE_INVALID")
        if expected_attempt is not None and int(job.attempt) != expected_attempt:
            raise BatchWorkerError("BATCH_ATTEMPT_FENCE_INVALID")
        if job.status in {status.value for status in (JobStatus.SUCCEEDED, JobStatus.PARTIAL, JobStatus.FAILED, JobStatus.CANCELED)}:
            return JobStatus(job.status)
        attempt = self.session.scalar(select(JobAttempt).where(JobAttempt.job_id == job.id, JobAttempt.attempt == job.attempt).with_for_update())
        if attempt is None:
            raise BatchWorkerError("BATCH_ATTEMPT_NOT_FOUND")
        _assert_attempt_fence(attempt, expected_attempt=expected_attempt, expected_fencing_token=expected_fencing_token)
        job.status = JobStatus.RUNNING.value
        batch.execution_status = JobStatus.RUNNING.value
        attempt.status = JobStatus.RUNNING.value
        job.revision += 1
        job.etag = _job_etag(job)
        self.session.flush()
        # Release the claim lock before adapter execution so cancellation can
        # acquire the Job row between two row evaluations.
        self.session.commit()
        adapter_execute = getattr(self.registry, "execute_snapshot")
        rows = list(self.session.scalars(select(BatchRowResultRow).where(
            BatchRowResultRow.batch_id == batch.id,
            BatchRowResultRow.validation_id == batch.validation_id,
            BatchRowResultRow.status.in_(("QUEUED", "RUNNING")),
        ).order_by(BatchRowResultRow.source_row_number)))
        model = ModelSnapshot.model_validate(validation.model_snapshot or {})
        if _hash(model.model_dump(mode="json")) != _hash(batch.model_snapshot or {}):
            raise BatchWorkerError("BATCH_MODEL_SNAPSHOT_MISMATCH")
        started = time.monotonic()
        for row in rows:
            _check_lease(lease_guard)
            control = self.session.scalar(select(Job).where(Job.id == batch.job_id))
            current_attempt = self.session.scalar(select(JobAttempt).where(JobAttempt.job_id == batch.job_id, JobAttempt.attempt == (expected_attempt or (control.attempt if control else 0))))
            if control is None or current_attempt is None:
                raise BatchWorkerError("BATCH_ATTEMPT_FENCE_INVALID")
            _assert_attempt_fence(current_attempt, expected_attempt=expected_attempt, expected_fencing_token=expected_fencing_token)
            if control.status in {JobStatus.CANCEL_REQUESTED.value, JobStatus.CANCELED.value}:
                pending = list(self.session.scalars(select(BatchRowResultRow).where(
                    BatchRowResultRow.batch_id == batch.id,
                    BatchRowResultRow.validation_id == batch.validation_id,
                    BatchRowResultRow.status.in_(("QUEUED", "RUNNING")),
                ).with_for_update()))
                for pending_row in pending:
                    pending_row.status = "CANCELED"
                    pending_row.result = None
                    pending_row.error = None
                job.status = JobStatus.CANCELED.value
                self.session.flush()
                break
            row = self.session.scalar(select(BatchRowResultRow).where(BatchRowResultRow.id == row.id).with_for_update())
            if row is None or row.status not in {"QUEUED", "RUNNING"}:
                continue
            item = self.session.scalar(select(BatchValidationItemRow).where(
                BatchValidationItemRow.validation_id == batch.validation_id,
                BatchValidationItemRow.row_id == row.row_id,
            ))
            if (item is None or item.resolved_input_snapshot != row.input_snapshot
                    or not item.resolved_input_hash or _hash(row.input_snapshot) != item.resolved_input_hash):
                row.status, row.error = "FAILED", {"code": "BATCH_INPUT_SNAPSHOT_MISMATCH", "message": "解析输入快照校验失败"}
                self.session.flush()
                self.session.commit()
                continue
            if time.monotonic() - started > int((batch.resource_budget or {}).get("max_runtime_seconds", 0)):
                row.status, row.error = "FAILED", {"code": "BATCH_RUNTIME_BUDGET_EXCEEDED", "message": "批量运行时间超过资源预算"}
                self.session.flush()
                self.session.commit()
                continue
            row.status = "RUNNING"
            self.session.flush()
            self.session.commit()
            science_error: Exception | None = None
            science_started = False
            request: object | None = None
            public_input_snapshot: dict[str, Any] | None = None
            result: object | None = None
            try:
                request, normalized_structures, normalized_modifier_structures = self._execution_input(
                    batch, validation, row,
                )
                public_input_snapshot = self._public_input_snapshot(request, normalized_structures)
                execution_kwargs: dict[str, Any] = {"model_snapshot": model}
                parameters = inspect.signature(adapter_execute).parameters
                if "normalized_structures" in parameters:
                    execution_kwargs["normalized_structures"] = normalized_structures
                if "normalized_modifier_structures" in parameters:
                    execution_kwargs["normalized_modifier_structures"] = normalized_modifier_structures
                science_started = True
                result = adapter_execute(batch.chromatography_type, request, **execution_kwargs)
            except Exception as exc:
                science_error = exc

            # The adapter can run longer than the lease window. Recheck
            # ownership immediately before publishing either terminal result.
            _check_lease(lease_guard)
            control = self.session.scalar(select(Job).where(Job.id == batch.job_id).with_for_update())
            current_attempt = self.session.scalar(select(JobAttempt).where(JobAttempt.job_id == batch.job_id, JobAttempt.attempt == (expected_attempt or (control.attempt if control else 0))).with_for_update())
            if control is None or current_attempt is None:
                raise BatchWorkerError("BATCH_ATTEMPT_FENCE_INVALID")
            _assert_attempt_fence(current_attempt, expected_attempt=expected_attempt, expected_fencing_token=expected_fencing_token)
            if control.status in {JobStatus.CANCEL_REQUESTED.value, JobStatus.CANCELED.value}:
                row = self.session.scalar(select(BatchRowResultRow).where(BatchRowResultRow.id == row.id).with_for_update())
                if row is not None and row.status in {"QUEUED", "RUNNING"}:
                    row.status, row.result, row.error = "CANCELED", None, None
                    if science_started and public_input_snapshot is not None:
                        self._write_record(
                            batch, row, model, public_input_snapshot,
                            status="CANCELED", warnings=[],
                        )
                self.session.flush()
                self.session.commit()
                continue
            row = self.session.scalar(select(BatchRowResultRow).where(BatchRowResultRow.id == row.id).with_for_update())
            if row is None or row.status not in {"QUEUED", "RUNNING"}:
                continue
            if science_error is None:
                row.result = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
                row.warnings = _merge_prediction_warnings(row.warnings, _result_warnings(result))
                row.status = "SUCCEEDED"
            else:
                row.error = self._error(science_error)
                row.status = "FAILED"
            if science_started and public_input_snapshot is not None:
                self._write_record(
                    batch, row, model, public_input_snapshot,
                    status=row.status, warnings=list(row.warnings or []),
                )
            self.session.flush()
            self.session.commit()
        counts = _counts(self.session, batch.id, batch.validation_id)
        batch = self.session.scalar(select(BatchPredictionRow).where(BatchPredictionRow.id == batch.id).with_for_update())
        job = self.session.scalar(select(Job).where(Job.id == batch.job_id).with_for_update())
        attempt = self.session.scalar(select(JobAttempt).where(JobAttempt.job_id == job.id, JobAttempt.attempt == job.attempt).with_for_update())
        if batch is None or job is None or attempt is None:
            raise BatchWorkerError("BATCH_ATTEMPT_NOT_FOUND")
        _assert_attempt_fence(attempt, expected_attempt=expected_attempt, expected_fencing_token=expected_fencing_token)
        batch.counts = counts
        completed = counts["succeeded"] + counts["failed"] + counts["skipped"] + counts["canceled"]
        batch.progress = completed / counts["total"] if counts["total"] else 1.0
        if counts["canceled"] or job.status in {JobStatus.CANCEL_REQUESTED.value, JobStatus.CANCELED.value}:
            final = JobStatus.CANCELED
        elif counts["failed"] and counts["succeeded"]:
            final = JobStatus.PARTIAL
        elif counts["failed"]:
            final = JobStatus.FAILED
        elif counts["canceled"]:
            final = JobStatus.CANCELED
        else:
            final = JobStatus.SUCCEEDED
        _finalize_prediction(self.session, job, attempt, batch, final, expected_attempt=expected_attempt, expected_fencing_token=expected_fencing_token)
        return final


def _check_lease(lease_guard: object | None) -> None:
    check = getattr(lease_guard, "check", None)
    if callable(check):
        check()


def _assert_attempt_fence(attempt: JobAttempt, *, expected_attempt: int | None, expected_fencing_token: int | None) -> None:
    if expected_attempt is not None and int(attempt.attempt) != int(expected_attempt):
        raise BatchWorkerError("BATCH_ATTEMPT_FENCE_INVALID")
    if expected_fencing_token is not None and int(attempt.fencing_token or 0) != int(expected_fencing_token):
        raise BatchWorkerError("BATCH_FENCING_INVALID")


def _batch_projection_etag(batch: BatchPredictionRow) -> str:
    return _batch_etag({
        "id": batch.id,
        "revision": int(batch.revision or 1),
        "draft_status": batch.draft_status,
        "execution_status": batch.execution_status,
        "counts": batch.counts or {},
        "progress": batch.progress,
    })


def _finalize_prediction(
    session: Session,
    job: Job,
    attempt: JobAttempt,
    batch: BatchPredictionRow,
    requested: JobStatus,
    *,
    expected_attempt: int | None,
    expected_fencing_token: int | None,
    error: dict[str, Any] | None = None,
) -> JobStatus:
    """Apply the batch projection and shared Job/Attempt terminal CAS together."""
    current_job = session.scalar(select(Job).where(Job.id == job.id).with_for_update())
    current_attempt = session.scalar(select(JobAttempt).where(JobAttempt.job_id == job.id, JobAttempt.attempt == job.attempt).with_for_update())
    if current_job is None or current_attempt is None:
        raise BatchWorkerError("BATCH_ATTEMPT_NOT_FOUND")
    _assert_attempt_fence(current_attempt, expected_attempt=expected_attempt, expected_fencing_token=expected_fencing_token)
    current_batch = session.scalar(select(BatchPredictionRow).where(BatchPredictionRow.id == batch.id).with_for_update())
    if current_batch is None or current_batch.job_id != current_job.id:
        raise BatchWorkerError("WORKER_JOB_RESOURCE_INVALID")
    # Lock every current row before computing the terminal projection.  The
    # count helper deliberately collapses historical retry attempts by row_id.
    locked_rows = list(session.scalars(select(BatchRowResultRow).where(
        BatchRowResultRow.batch_id == current_batch.id,
        BatchRowResultRow.validation_id == current_batch.validation_id,
    ).with_for_update()))
    if requested is JobStatus.FAILED:
        failure = error or {"code": "BATCH_EXECUTION_FAILED", "message": "批量任务执行失败"}
        for row in locked_rows:
            if row.status == "RUNNING":
                row.status, row.error, row.result = "FAILED", dict(failure), None
    counts = _counts(session, current_batch.id, current_batch.validation_id)
    completed = counts["succeeded"] + counts["failed"] + counts["skipped"] + counts["canceled"]
    current_batch.counts = counts
    current_batch.progress = completed / counts["total"] if counts["total"] else 1.0
    if current_job.status in {JobStatus.CANCEL_REQUESTED.value, JobStatus.CANCELED.value}:
        requested = JobStatus.CANCELED
    current_batch.execution_status = requested.value
    current_job.progress = {"completed": completed, "total": counts["total"], "unit": "rows", "stage": "finished"}
    current_batch.revision = int(current_batch.revision or 1) + 1
    current_batch.updated_at = _now()
    current_batch.etag = _batch_projection_etag(current_batch)
    result_ref = {"type": "batch_prediction", "id": current_batch.id}
    repo = SQLAlchemyTaskRepository(lambda: session)
    finalized = sync_job_lifecycle(
        repo, session, job_id=current_job.id, status=requested,
        attempt=int(current_job.attempt), expected_attempt=expected_attempt,
        fencing_token=expected_fencing_token, result_ref=result_ref,
        error=error, finished_at=_now(), resource_ref={"type": "batch_prediction", "id": current_batch.id},
    )
    if finalized is None:
        raise BatchWorkerError("BATCH_FENCING_INVALID")
    current_attempt = session.scalar(select(JobAttempt).where(JobAttempt.job_id == current_job.id, JobAttempt.attempt == current_job.attempt).with_for_update())
    if current_attempt is not None:
        current_attempt.lease_until = None
        current_attempt.finished_at = current_attempt.finished_at or _now()
    session.flush()
    return JobStatus(finalized.status)


def _finalize_validation(
    session: Session,
    job: Job,
    attempt: JobAttempt,
    validation_id: str,
    requested: JobStatus,
    *,
    expected_attempt: int | None,
    expected_fencing_token: int | None,
    error: dict[str, Any] | None = None,
) -> JobStatus:
    current_job = session.scalar(select(Job).where(Job.id == job.id).with_for_update())
    current_attempt = session.scalar(select(JobAttempt).where(JobAttempt.job_id == job.id, JobAttempt.attempt == job.attempt).with_for_update())
    if current_job is None or current_attempt is None:
        raise BatchWorkerError("BATCH_ATTEMPT_NOT_FOUND")
    _assert_attempt_fence(current_attempt, expected_attempt=expected_attempt, expected_fencing_token=expected_fencing_token)
    validation = session.scalar(select(BatchValidationRow).where(BatchValidationRow.id == validation_id).with_for_update())
    if validation is None:
        raise BatchWorkerError("WORKER_JOB_RESOURCE_INVALID")
    batch = session.scalar(select(BatchPredictionRow).where(BatchPredictionRow.id == validation.batch_id).with_for_update())
    if batch is None or batch.project_id != current_job.project_id:
        raise BatchWorkerError("WORKER_JOB_RESOURCE_INVALID")
    if current_job.status in {JobStatus.CANCEL_REQUESTED.value, JobStatus.CANCELED.value}:
        requested = JobStatus.CANCELED
    if requested is JobStatus.CANCELED:
        # Cancellation is represented by the Job/Batch execution state. The
        # public BatchValidation DTO has no CANCELED status, so keep its
        # projection within the existing contract.
        validation.status = "FAILED"
        if not any(
            isinstance(warning, Mapping)
            and warning.get("code") == "BATCH_VALIDATION_CANCELED"
            for warning in (validation.warnings or [])
        ):
            validation.warnings = [
                *(validation.warnings or []),
                {"code": "BATCH_VALIDATION_CANCELED", "message": "校验任务已取消，可重新校验草稿"},
            ]
        batch.draft_status = "INVALID"
    elif requested is JobStatus.FAILED:
        validation.status = "INVALID"
        batch.draft_status = "INVALID"
    batch.execution_status = requested.value
    if requested is not JobStatus.SUCCEEDED:
        batch.revision = int(batch.revision or 1) + 1
    batch.updated_at = _now()
    batch.etag = _batch_projection_etag(batch)
    repo = SQLAlchemyTaskRepository(lambda: session)
    finalized = sync_job_lifecycle(
        repo, session, job_id=current_job.id, status=requested,
        attempt=int(current_job.attempt), expected_attempt=expected_attempt,
        fencing_token=expected_fencing_token,
        result_ref={"type": "batch_validation", "id": validation.id},
        error=error, finished_at=_now(), resource_ref={"type": "batch_validation", "id": validation.id},
    )
    if finalized is None:
        raise BatchWorkerError("BATCH_FENCING_INVALID")
    current_attempt = session.scalar(select(JobAttempt).where(JobAttempt.job_id == current_job.id, JobAttempt.attempt == current_job.attempt).with_for_update())
    if current_attempt is not None:
        current_attempt.lease_until = None
        current_attempt.finished_at = current_attempt.finished_at or _now()
    session.flush()
    return JobStatus(finalized.status)


def _finalize_validation_job_by_ref(
    session: Session,
    job_id: str,
    expected_attempt: int,
    expected_fencing_token: int | None,
    ref: Mapping[str, Any],
    requested: JobStatus,
) -> JobStatus:
    """Commit the validation job projection in its dedicated sync session."""
    try:
        result = _finalize_job_by_ref(
            session, job_id, ref, requested,
            expected_attempt=expected_attempt,
            expected_fencing_token=expected_fencing_token,
        )
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise


def _finalize_export(
    session: Session,
    job: Job,
    attempt: JobAttempt,
    export: BatchExportRow,
    requested: JobStatus,
    *,
    expected_attempt: int,
    expected_fencing_token: int | None,
    error: dict[str, Any] | None = None,
) -> JobStatus:
    current_job = session.scalar(select(Job).where(Job.id == job.id).with_for_update())
    current_attempt = session.scalar(select(JobAttempt).where(JobAttempt.job_id == job.id, JobAttempt.attempt == expected_attempt).with_for_update())
    current_export = session.scalar(select(BatchExportRow).where(BatchExportRow.id == export.id).with_for_update())
    if current_job is None or current_attempt is None or current_export is None or current_export.job_id != current_job.id:
        raise BatchWorkerError("WORKER_JOB_RESOURCE_INVALID")
    _assert_attempt_fence(current_attempt, expected_attempt=expected_attempt, expected_fencing_token=expected_fencing_token)
    if current_job.status in {JobStatus.CANCEL_REQUESTED.value, JobStatus.CANCELED.value}:
        requested = JobStatus.CANCELED
    current_export.status = requested.value
    current_export.error = error
    current_export.completed_at = _now()
    result_ref = {"type": "artifact", "id": current_export.artifact_id} if requested is JobStatus.SUCCEEDED and current_export.artifact_id else None
    finalized = sync_job_lifecycle(
        SQLAlchemyTaskRepository(lambda: session), session, job_id=current_job.id,
        status=requested, attempt=expected_attempt, expected_attempt=expected_attempt,
        fencing_token=expected_fencing_token, result_ref=result_ref, error=error,
        finished_at=_now(), resource_ref={"type": "batch_export", "id": current_export.id},
    )
    if finalized is None:
        raise BatchWorkerError("BATCH_FENCING_INVALID")
    current_attempt.lease_until = None
    current_attempt.finished_at = current_attempt.finished_at or _now()
    session.flush()
    return JobStatus(finalized.status)


def _public_batch_export_snapshot(
    source: Mapping[str, Any], batch: BatchPredictionRow,
    validation: BatchValidationRow | None,
) -> dict[str, Any]:
    """Project frozen batch rows to the allowlisted generic export DTO."""
    projected = dict(source)
    raw_records = source.get("records")
    if not isinstance(raw_records, list):
        return projected
    include_input = isinstance(source.get("include"), list) and "input_snapshot" in source["include"]
    records: list[Any] = []
    for raw in raw_records:
        if not isinstance(raw, Mapping):
            records.append(raw)
            continue
        record = dict(raw)
        if isinstance(record.get("warnings"), list):
            record["warnings"] = _merge_record_warnings(
                [], _warning_items(record["warnings"]),
            )
        if record.get("status") == "SKIPPED":
            # Invalid/excluded rows have no executable prediction input.
            record.pop("input_snapshot", None)
        elif "input_snapshot" in record:
            raw_input = record["input_snapshot"]
            if not isinstance(raw_input, Mapping):
                raise BatchWorkerError("BATCH_EXPORT_INPUT_SNAPSHOT_INVALID")
            try:
                if validation is None:
                    raise BatchWorkerError("BATCH_EXPORT_INPUT_SNAPSHOT_INVALID")
                request, normalized_structures, _modifiers = BatchPredictionWorker._execution_input(
                    batch, validation, SimpleNamespace(input_snapshot=raw_input),
                )
                record["input_snapshot"] = BatchPredictionWorker._public_input_snapshot(
                    request, normalized_structures,
                )
            except BatchWorkerError as exc:
                if str(exc) != "BATCH_LEGACY_DATA_REQUIRES_REVALIDATION":
                    raise
                if batch.template_version != "tlc-batch-1":
                    raise BatchWorkerError("BATCH_EXPORT_INPUT_SNAPSHOT_INVALID") from exc
                try:
                    public_input = PredictionInputSnapshot.model_validate(raw_input)
                    if not isinstance(public_input.input, TlcInput):
                        raise BatchWorkerError("BATCH_EXPORT_INPUT_SNAPSHOT_INVALID")
                    record["input_snapshot"] = public_input.model_dump(mode="json")
                except (TypeError, ValueError) as validation_error:
                    raise BatchWorkerError("BATCH_EXPORT_INPUT_SNAPSHOT_INVALID") from validation_error
        elif include_input:
            raise BatchWorkerError("BATCH_EXPORT_INPUT_SNAPSHOT_INVALID")
        records.append(record)
    projected["records"] = records
    return projected


def _finalize_job_by_ref(
    session: Session,
    job_id: str,
    ref: Mapping[str, Any],
    requested: JobStatus,
    *,
    expected_attempt: int,
    expected_fencing_token: int | None,
    error: dict[str, Any] | None = None,
) -> JobStatus:
    job = session.scalar(select(Job).where(Job.id == job_id).with_for_update())
    attempt = session.scalar(select(JobAttempt).where(JobAttempt.job_id == job_id, JobAttempt.attempt == expected_attempt).with_for_update())
    if job is None or attempt is None:
        raise BatchWorkerError("BATCH_ATTEMPT_NOT_FOUND")
    _assert_attempt_fence(attempt, expected_attempt=expected_attempt, expected_fencing_token=expected_fencing_token)
    if ref.get("type") == "batch_prediction":
        batch = session.scalar(select(BatchPredictionRow).where(BatchPredictionRow.id == ref.get("id")).with_for_update())
        if batch is None:
            raise BatchWorkerError("WORKER_JOB_RESOURCE_INVALID")
        return _finalize_prediction(
            session, job, attempt, batch, requested,
            expected_attempt=expected_attempt, expected_fencing_token=expected_fencing_token, error=error,
        )
    if ref.get("type") == "batch_validation":
        return _finalize_validation(
            session, job, attempt, str(ref.get("id")), requested,
            expected_attempt=expected_attempt, expected_fencing_token=expected_fencing_token, error=error,
        )
    if ref.get("type") == "batch_export":
        export = session.scalar(select(BatchExportRow).where(BatchExportRow.id == ref.get("id")).with_for_update())
        if export is None:
            raise BatchWorkerError("WORKER_JOB_RESOURCE_INVALID")
        return _finalize_export(
            session, job, attempt, export, requested, expected_attempt=expected_attempt,
            expected_fencing_token=expected_fencing_token, error=error,
        )
    raise BatchWorkerError("WORKER_JOB_RESOURCE_INVALID")


class BatchPredictionProductionWorker:
    """Async production boundary for batch validation and execution jobs."""

    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession], registry: object, actor: ActorContext, sync_session_factory: object | None = None, artifact: object | None = None, artifact_store: object | None = None, enabled_batch_types: Collection[str] | None = None, prediction_create_service_factory: Callable[[Session], object] | None = None, record_writer: object | None = None) -> None:
        self.session_factory, self.registry, self.actor = session_factory, registry, actor
        self.sync_session_factory, self.artifact, self.artifact_store = sync_session_factory, artifact, artifact_store
        self.enabled_batch_types = frozenset(enabled_batch_types or ())
        self.prediction_create_service_factory = prediction_create_service_factory
        self.record_writer = record_writer

    @staticmethod
    def _message(payload: Mapping[str, Any], *, event_type: str | None = None) -> tuple[str, int, str, int | None]:
        expected_keys = {"job_id", "attempt", "dispatch_id", "fencing_token"} if event_type == "job.cancel" else {"job_id", "attempt", "dispatch_id"}
        if not isinstance(payload, Mapping) or set(payload) != expected_keys:
            raise BatchWorkerError("INVALID_WORKER_PAYLOAD")
        job_id, attempt, dispatch_id = payload["job_id"], payload["attempt"], payload["dispatch_id"]
        if not isinstance(job_id, str) or not job_id or not isinstance(dispatch_id, str) or not dispatch_id or isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
            raise BatchWorkerError("INVALID_WORKER_PAYLOAD")
        fencing_token = payload.get("fencing_token")
        if event_type == "job.cancel" and (isinstance(fencing_token, bool) or not isinstance(fencing_token, int) or fencing_token < 0):
            raise BatchWorkerError("INVALID_WORKER_PAYLOAD")
        return job_id, attempt, dispatch_id, fencing_token

    @staticmethod
    def _locked(session: Session, job_id: str, attempt: int, dispatch_id: str, event_type: str | None, *, inbound_fencing_token: int | None = None) -> tuple[Job, JobAttempt, OutboxEvent, dict[str, Any]]:
        job = session.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if job is None or job.kind != "batch_prediction" or int(job.attempt) != attempt:
            raise BatchWorkerError("WORKER_JOB_FENCE_INVALID")
        dispatch_query = select(OutboxEvent).where(
            OutboxEvent.dispatch_id == dispatch_id,
            OutboxEvent.aggregate_type == "job",
            OutboxEvent.aggregate_id == job.id,
        )
        if event_type is not None:
            dispatch_query = dispatch_query.where(OutboxEvent.event_type == event_type)
        dispatch = session.scalar(dispatch_query.with_for_update())
        if dispatch is None or dispatch.event_type not in {"job.execute", "job.cancel"} or dispatch.status not in {"PENDING", "PROCESSING"}:
            raise BatchWorkerError("WORKER_DISPATCH_FENCE_INVALID")
        attempt_row = session.scalar(select(JobAttempt).where(JobAttempt.job_id == job.id, JobAttempt.attempt == attempt).with_for_update())
        if attempt_row is None:
            raise BatchWorkerError("BATCH_ATTEMPT_NOT_FOUND")
        expected = {"job_id": job.id, "attempt": attempt, "dispatch_id": dispatch_id}
        payload = dict(dispatch.payload or {})
        if dispatch.event_type == "job.cancel":
            token = payload.get("fencing_token")
            if (set(payload) != {"job_id", "attempt", "dispatch_id", "fencing_token"}
                    or isinstance(token, bool) or not isinstance(token, int)
                    or token < 0 or token != int(attempt_row.fencing_token or 0)
                    or (inbound_fencing_token is not None and token != inbound_fencing_token)):
                raise BatchWorkerError("WORKER_DISPATCH_FENCE_INVALID")
            expected["fencing_token"] = token
        if payload != expected:
            raise BatchWorkerError("WORKER_DISPATCH_FENCE_INVALID")
        ref = job.resource_ref if isinstance(job.resource_ref, Mapping) else {}
        if ref.get("type") not in {"batch_validation", "batch_prediction", "batch_export"} or not isinstance(ref.get("id"), str):
            raise BatchWorkerError("WORKER_JOB_RESOURCE_INVALID")
        return job, attempt_row, dispatch, dict(ref)

    def _assert_claim_batch_enabled(self, session: Session, job: Job, ref: Mapping[str, Any]) -> None:
        """Bind a queued worker claim to its persisted parent batch before state changes."""
        if not self.actor.can_access_project(job.project_id):
            raise BatchWorkerError("WORKER_JOB_RESOURCE_INVALID")
        ref_type = ref.get("type")
        ref_id = ref.get("id")
        if ref_type == "batch_prediction":
            batch = session.scalar(select(BatchPredictionRow).where(BatchPredictionRow.id == ref_id).with_for_update())
            if batch is None or batch.project_id != job.project_id or batch.job_id != job.id:
                raise BatchWorkerError("WORKER_JOB_RESOURCE_INVALID")
        elif ref_type == "batch_validation":
            validation = session.scalar(select(BatchValidationRow).where(BatchValidationRow.id == ref_id).with_for_update())
            if validation is None or validation.project_id != job.project_id:
                raise BatchWorkerError("WORKER_JOB_RESOURCE_INVALID")
            batch = session.scalar(select(BatchPredictionRow).where(BatchPredictionRow.id == validation.batch_id).with_for_update())
            # A validation job can finish after the draft has advanced. Keep the
            # old validation resource claimable so run_validation can persist STALE.
            if batch is None or batch.project_id != job.project_id or validation.project_id != batch.project_id:
                raise BatchWorkerError("WORKER_JOB_RESOURCE_INVALID")
        elif ref_type == "batch_export":
            export = session.scalar(select(BatchExportRow).where(BatchExportRow.id == ref_id).with_for_update())
            if export is None or export.job_id != job.id or export.project_id != job.project_id:
                raise BatchWorkerError("WORKER_JOB_RESOURCE_INVALID")
            batch = session.scalar(select(BatchPredictionRow).where(BatchPredictionRow.id == export.batch_id).with_for_update())
            # Export jobs have their own job id; do not compare it to batch.job_id.
            if batch is None or batch.project_id != job.project_id or export.project_id != batch.project_id:
                raise BatchWorkerError("WORKER_JOB_RESOURCE_INVALID")
        else:
            raise BatchWorkerError("WORKER_JOB_RESOURCE_INVALID")

        if batch.chromatography_type not in self.enabled_batch_types:
            raise BatchWorkerError("BATCH_TYPE_NOT_ENABLED")

    @staticmethod
    def _finish(job: Job, attempt: JobAttempt, status: JobStatus, *, error: dict[str, str] | None = None) -> None:
        now = _now()
        job.status, job.error, job.finished_at = status.value, error, now
        job.updated_at, job.revision = now, job.revision + 1
        job.etag = _job_etag(job)
        attempt.status, attempt.error, attempt.finished_at, attempt.lease_until = status.value, error, now, None

    @staticmethod
    def _cancel_locked(session: Session, job: Job, attempt: JobAttempt, ref: Mapping[str, Any]) -> JobStatus:
        if ref["type"] == "batch_prediction":
            batch = session.scalar(select(BatchPredictionRow).where(BatchPredictionRow.id == ref["id"]).with_for_update())
            if batch is None or batch.job_id != job.id:
                raise BatchWorkerError("WORKER_JOB_RESOURCE_INVALID")
            rows = list(session.scalars(select(BatchRowResultRow).where(
                BatchRowResultRow.batch_id == batch.id,
                BatchRowResultRow.validation_id == batch.validation_id,
                BatchRowResultRow.status.in_(("QUEUED", "RUNNING")),
            ).with_for_update()))
            for row in rows:
                row.status, row.result, row.error = "CANCELED", None, None
            return _finalize_prediction(
                session, job, attempt, batch, JobStatus.CANCELED,
                expected_attempt=int(job.attempt), expected_fencing_token=(int(attempt.fencing_token) if attempt.fencing_token is not None else None),
            )
        elif ref["type"] == "batch_validation":
            validation = session.scalar(select(BatchValidationRow).where(BatchValidationRow.id == ref["id"]).with_for_update())
            if validation is None or validation.project_id != job.project_id:
                raise BatchWorkerError("WORKER_JOB_RESOURCE_INVALID")
            return _finalize_validation(
                session, job, attempt, validation.id, JobStatus.CANCELED,
                expected_attempt=int(job.attempt), expected_fencing_token=(int(attempt.fencing_token) if attempt.fencing_token is not None else None),
            )
        else:
            export = session.scalar(select(BatchExportRow).where(BatchExportRow.id == ref["id"]).with_for_update())
            if export is None or export.job_id != job.id:
                raise BatchWorkerError("WORKER_JOB_RESOURCE_INVALID")
            return _finalize_export(
                session, job, attempt, export, JobStatus.CANCELED,
                expected_attempt=int(job.attempt), expected_fencing_token=(int(attempt.fencing_token) if attempt.fencing_token is not None else None),
                error={"code": "BATCH_EXPORT_CANCELED", "message": "批量导出已取消"},
            )

    @staticmethod
    def _start_locked(job: Job, attempt: JobAttempt) -> None:
        if job.status != JobStatus.QUEUED.value:
            return
        now = _now()
        job.status = JobStatus.RUNNING.value
        job.started_at = job.started_at or now
        job.updated_at = now
        job.revision += 1
        job.etag = _job_etag(job)
        attempt.status = JobStatus.RUNNING.value
        attempt.started_at = attempt.started_at or now
        attempt.execution_token = attempt.execution_token or f"batch_exec_{job.id}_{job.attempt}"
        attempt.fencing_token = int(attempt.fencing_token or 0) + 1

    async def _run_sync(self, fn: object, *args: object) -> object:
        factory = self.sync_session_factory
        if not callable(factory):
            raise BatchWorkerError("BATCH_SYNC_SESSION_FACTORY_UNAVAILABLE")
        def call() -> object:
            session = factory()
            try:
                return fn(session, *args)
            finally:
                session.close()
        return await asyncio.to_thread(call)

    def _execute_batch(self, session: Session, batch_id: str, attempt: int, fencing_token: int, lease_guard: object | None) -> JobStatus:
        try:
            result = BatchPredictionWorker(
                session, self.registry, enabled_batch_types=self.enabled_batch_types,
                record_writer=self.record_writer,
            ).execute(
                batch_id, actor=self.actor, expected_attempt=attempt, expected_fencing_token=fencing_token, lease_guard=lease_guard,
            )
            session.commit()
            return result
        except Exception:
            session.rollback()
            raise

    def _execute_export(self, session: Session, export_id: str, attempt: int, fencing_token: int, lease_guard: object | None) -> JobStatus:
        export = session.scalar(select(BatchExportRow).where(BatchExportRow.id == export_id).with_for_update())
        if export is None:
            raise BatchWorkerError("WORKER_JOB_RESOURCE_INVALID")
        batch = session.get(BatchPredictionRow, export.batch_id)
        if batch is None or batch.project_id != export.project_id:
            raise BatchWorkerError("WORKER_JOB_RESOURCE_INVALID")
        if batch.chromatography_type not in self.enabled_batch_types:
            raise BatchWorkerError("BATCH_TYPE_NOT_ENABLED")
        job = session.scalar(select(Job).where(Job.id == export.job_id).with_for_update())
        attempt_row = session.scalar(select(JobAttempt).where(JobAttempt.job_id == export.job_id, JobAttempt.attempt == attempt).with_for_update())
        if job is None or attempt_row is None or job.kind != "batch_prediction" or job.resource_ref != {"type": "batch_export", "id": export.id}:
            raise BatchWorkerError("WORKER_JOB_RESOURCE_INVALID")
        _assert_attempt_fence(attempt_row, expected_attempt=attempt, expected_fencing_token=fencing_token)
        store = self.artifact_store
        if store is None or not getattr(store, "configured", True):
            raise BatchWorkerError("BATCH_EXPORT_STORAGE_UNAVAILABLE")
        source = dict(export.source_snapshot or {})
        if not source or not export.source_snapshot_hash or _hash(source) != export.source_snapshot_hash:
            raise BatchWorkerError("BATCH_EXPORT_SOURCE_SNAPSHOT_INVALID")
        artifact_id = "artifact_batch_" + hashlib.sha256(f"{export.id}:batch-export:1.0".encode("utf-8")).hexdigest()[:32]
        storage_identity = "batch_export_" + hashlib.sha256(
            f"{export.id}:attempt:{attempt}:fencing:{fencing_token}".encode("utf-8")
        ).hexdigest()[:40]
        storage_key: str | None = None
        try:
            source_binding = source.get("source")
            validation_id = source_binding.get("validation_id") if isinstance(source_binding, Mapping) else None
            validation = session.scalar(select(BatchValidationRow).where(
                BatchValidationRow.id == validation_id,
                BatchValidationRow.project_id == export.project_id,
                BatchValidationRow.batch_id == batch.id,
            )) if isinstance(validation_id, str) else None
            source = _public_batch_export_snapshot(source, batch, validation)
            if lease_guard is not None:
                lease_guard.check()
            from chrom_platform.modules.data_center.exports import render_export

            body, media_type, extension = render_export(export.format, source)
            storage_key = store.key_for(storage_identity)
            storage_key, size, digest = store.put(storage_identity, body, expected_size=len(body))
            if lease_guard is not None:
                lease_guard.check()
            artifact = session.scalar(select(Artifact).where(Artifact.id == artifact_id, Artifact.project_id == export.project_id).with_for_update())
            if artifact is None:
                artifact = Artifact(
                    id=artifact_id, project_id=export.project_id, kind="batch_export",
                    title=f"batch-{export.id}.{extension}", media_type=media_type,
                    size_bytes=size, sha256=digest, storage_key=storage_key,
                    status="AVAILABLE", scan_status="PASSED",
                    metadata_json={"file_name": f"batch-{export.id}.{extension}",
                                   "source_ref": {"type": "batch_export", "id": export.id},
                                   "source_snapshot_hash": export.source_snapshot_hash,
                                   "generation": "batch_export_v1"},
                    created_by=job.owner_id,
                )
                session.add(artifact)
            elif artifact.sha256 != digest or artifact.size_bytes != size:
                raise BatchWorkerError("BATCH_EXPORT_ARTIFACT_CONFLICT")
            export.artifact_id = artifact_id
            result = _finalize_export(session, job, attempt_row, export, JobStatus.SUCCEEDED,
                                      expected_attempt=attempt, expected_fencing_token=fencing_token)
            if lease_guard is not None:
                lease_guard.check()
            session.commit()
            return result
        except Exception as exc:
            session.rollback()
            if storage_key is not None:
                try:
                    published_key = session.scalar(select(Artifact.storage_key).where(Artifact.id == artifact_id))
                    if published_key != storage_key:
                        store.remove(storage_key)
                except Exception:
                    pass
            if lease_guard is not None:
                lease_guard.check()
            session.expire_all()
            export = session.scalar(select(BatchExportRow).where(BatchExportRow.id == export_id).with_for_update())
            job = session.scalar(select(Job).where(Job.id == export.job_id).with_for_update()) if export else None
            attempt_row = session.scalar(select(JobAttempt).where(JobAttempt.job_id == job.id, JobAttempt.attempt == attempt).with_for_update()) if job else None
            if export is None or job is None or attempt_row is None:
                raise BatchWorkerError("WORKER_JOB_RESOURCE_INVALID") from exc
            error = {"code": "BATCH_EXPORT_GENERATION_FAILED", "message": "批量导出生成失败"}
            result = _finalize_export(session, job, attempt_row, export, JobStatus.FAILED,
                                      expected_attempt=attempt, expected_fencing_token=fencing_token, error=error)
            session.commit()
            return result

    def _validate_batch(self, session: Session, validation_id: str, lease_guard: object | None) -> object:
        _check_lease(lease_guard)
        prediction_create_service = (
            self.prediction_create_service_factory(session)
            if self.prediction_create_service_factory is not None else None
        )
        service = BatchPredictionService(
            session, artifact=self.artifact, capabilities=self.registry,
            prediction_create_service=prediction_create_service,
            template_registry=default_batch_template_registry(),
            enabled_batch_types=self.enabled_batch_types,
        )
        try:
            result = service.run_validation(
                OperationContext(actor=self.actor, interaction="worker", request_id=f"batch-validation-{validation_id}"),
                validation_id,
            )
        except BatchServiceError:
            # Invalid user rows are a durable validation outcome.  Persist
            # that projection while allowing the outbox handler to report the
            # task failure for retryable infrastructure errors.
            session.commit()
            raise
        _check_lease(lease_guard)
        session.commit()
        return result

    async def _claim(self, payload: Mapping[str, Any], *, event_type: str | None, lease_guard: object | None) -> tuple[str, int, int, dict[str, Any], JobStatus | None]:
        job_id, expected_attempt, dispatch_id, _payload_fencing_token = self._message(payload, event_type=event_type)
        _check_lease(lease_guard)
        async with self.session_factory() as session:
            async with session.begin():
                def locked(sync_session: Session) -> tuple[Job, JobAttempt, OutboxEvent, dict[str, Any]]:
                    claim = self._locked(sync_session, job_id, expected_attempt, dispatch_id, event_type, inbound_fencing_token=_payload_fencing_token)
                    self._assert_claim_batch_enabled(sync_session, claim[0], claim[3])
                    return claim
                job, attempt, _dispatch, ref = await session.run_sync(locked)
                _check_lease(lease_guard)
                status = JobStatus(job.status)
                if status in _TERMINAL:
                    return job_id, expected_attempt, int(attempt.fencing_token or 0), ref, status
                if event_type == "job.cancel":
                    canceled = await session.run_sync(lambda sync: self._cancel_locked(sync, job, attempt, ref))
                    return job_id, expected_attempt, int(attempt.fencing_token or 0), ref, canceled
                if status is JobStatus.CANCEL_REQUESTED:
                    return job_id, expected_attempt, int(attempt.fencing_token or 0), ref, JobStatus.CANCELED
                self._start_locked(job, attempt)
                return job_id, expected_attempt, int(attempt.fencing_token or 0), ref, None

    async def handle(self, payload: Mapping[str, Any], *, event_type: str | None = None, lease_guard: object | None = None) -> object:
        job_id, attempt, fencing_token, ref, terminal = await self._claim(payload, event_type=event_type, lease_guard=lease_guard)
        if terminal is not None:
            return terminal
        try:
            if ref["type"] == "batch_validation":
                if self.artifact is None:
                    raise BatchWorkerError("BATCH_VALIDATION_DEPENDENCY_UNAVAILABLE")
                validation_result = await self._run_sync(self._validate_batch, ref["id"], lease_guard)
                validation_status = str(getattr(validation_result, "status", ""))
                terminal_status = JobStatus.SUCCEEDED if validation_status == "READY" else JobStatus.FAILED
                await self._run_sync(
                    _finalize_validation_job_by_ref,
                    job_id, attempt, fencing_token, ref, terminal_status,
                )
                result = validation_result
            elif ref["type"] == "batch_prediction":
                result = await self._run_sync(self._execute_batch, ref["id"], attempt, fencing_token, lease_guard)
            elif ref["type"] == "batch_export":
                result = await self._run_sync(self._execute_export, ref["id"], attempt, fencing_token, lease_guard)
            else:
                raise BatchWorkerError("WORKER_JOB_RESOURCE_INVALID")
            return result
        except Exception as exc:
            error = {"code": type(exc).__name__.upper(), "message": "批量任务执行失败"}
            async with self.session_factory() as session:
                async with session.begin():
                    def fail(sync: Session) -> None:
                        job_row, attempt_row, _dispatch, ref_row = self._locked(
                            sync, job_id, attempt, str(payload["dispatch_id"]), event_type,
                            inbound_fencing_token=(payload.get("fencing_token") if event_type == "job.cancel" else None),
                        )
                        if JobStatus(job_row.status) not in _TERMINAL:
                            _finalize_job_by_ref(sync, job_id, ref_row, JobStatus.FAILED,
                                                 expected_attempt=attempt,
                                                 expected_fencing_token=fencing_token,
                                                 error=error)
                    await session.run_sync(fail)
            raise

    async def execute(self, payload: Mapping[str, Any], *, lease_guard: object | None = None) -> object:
        return await self.handle(payload, lease_guard=lease_guard)

    async def cancel(self, payload: Mapping[str, Any], *, lease_guard: object | None = None) -> object:
        return await self.handle(payload, event_type="job.cancel", lease_guard=lease_guard)

    async def retry(self, payload: Mapping[str, Any], *, lease_guard: object | None = None) -> object:
        return await self.handle(payload, event_type="job.execute", lease_guard=lease_guard)


_TERMINAL = frozenset({
    JobStatus.SUCCEEDED, JobStatus.PARTIAL, JobStatus.FAILED,
    JobStatus.CANCELED, JobStatus.TIMED_OUT, JobStatus.INTERRUPTED,
})


def _job_etag(job: Job) -> str:
    return hashlib.sha256(json.dumps({
        "id": job.id, "project_id": job.project_id, "status": job.status,
        "attempt": job.attempt, "revision": job.revision,
        "progress": job.progress, "result_ref": job.result_ref, "error": job.error,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _counts(session: Session, batch_id: str, validation_id: str | None = None) -> dict[str, int]:
    statement = select(BatchRowResultRow).where(BatchRowResultRow.batch_id == batch_id)
    if validation_id is not None:
        statement = statement.where(BatchRowResultRow.validation_id == validation_id)
    rows = list(session.scalars(statement.order_by(BatchRowResultRow.source_row_number, BatchRowResultRow.attempt.desc(), BatchRowResultRow.id.desc())))
    latest: dict[str, BatchRowResultRow] = {}
    for row in rows:
        current = latest.get(row.row_id)
        if current is None or int(row.attempt) > int(current.attempt):
            latest[row.row_id] = row
    counts = {key: 0 for key in ("total", "queued", "running", "succeeded", "failed", "skipped", "canceled")}
    counts["total"] = len(latest)
    for row in latest.values():
        counts[row.status.lower()] = counts.get(row.status.lower(), 0) + 1
    return counts


__all__ = ["BatchPredictionProductionWorker", "BatchPredictionWorker", "BatchWorkerError"]
