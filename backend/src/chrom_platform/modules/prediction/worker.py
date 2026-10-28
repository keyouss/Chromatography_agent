"""Durable prediction worker handler.

The queue actor should pass only ``job_id, attempt, dispatch_id``.  The handler
loads the immutable snapshots from persistence and never resolves Champion.
"""

from __future__ import annotations

from dataclasses import dataclass
import inspect
from typing import Any, Protocol

from pydantic import TypeAdapter

from chrom_platform.core.errors import APIError
from chrom_platform.task_core.state import JobStatus

from .adapters import CapabilityRegistry
from .schemas import (
    ModelSnapshot, PredictionCreate, PredictionExecutionInputSnapshot,
    PredictionExecutionRequest, build_prediction_execution_request,
    PublicErrorSummary, validate_hplc_execution_binding, validate_gc_execution_binding,
)


@dataclass(frozen=True)
class WorkerResult:
    status: JobStatus
    result: object | None = None
    error: PublicErrorSummary | None = None
    replayed: bool = False


class PredictionExecutionStore(Protocol):
    def load_execution(self, job_id: str) -> object | None: ...
    def mark_running(self, item: object, *, expected_attempt: int | None = None, expected_dispatch_id: str | None = None) -> bool | None: ...
    def mark_succeeded(self, item: object, result: object) -> None: ...
    def mark_failed(self, item: object, error: PublicErrorSummary) -> None: ...


def _attr(item: object, name: str) -> Any:
    return item.get(name) if isinstance(item, dict) else getattr(item, name, None)


def _public_error(exc: BaseException) -> PublicErrorSummary:
    if isinstance(exc, APIError):
        payload = exc.payload
        return PublicErrorSummary(code=payload.code, message=payload.message, retryable=payload.retryable)
    return PublicErrorSummary(code="PREDICTION_EXECUTION_FAILED", message="预测执行失败", retryable=True)


def _request_from_snapshot(item: object) -> PredictionExecutionRequest:
    existing = getattr(item, "request", None)
    model = getattr(item, "model_snapshot")
    if not isinstance(model, ModelSnapshot):
        model = ModelSnapshot.model_validate(model)
    input_snapshot: PredictionExecutionInputSnapshot = getattr(item, "input_snapshot")
    if not isinstance(input_snapshot, PredictionExecutionInputSnapshot):
        raw_snapshot = (
            input_snapshot.model_dump(mode="python")
            if hasattr(input_snapshot, "model_dump")
            else input_snapshot
        )
        input_snapshot = PredictionExecutionInputSnapshot.model_validate(raw_snapshot)
    # Rebuild from the persisted input snapshot and exact model binding.  This
    # prevents queue payloads or a later Champion pointer from changing input.
    source = (
        existing.model_dump(mode="python")
        if hasattr(existing, "model_dump")
        else dict(existing)
        if isinstance(existing, dict)
        else {
            "project_id": getattr(item, "project_id"),
            "chromatography_type": getattr(item, "type_id"),
            "tool": getattr(item, "tool"),
            "schema_version": getattr(item, "schema_version"),
            "structure_refs": input_snapshot.structure_refs,
            "input": input_snapshot.input.model_dump(mode="python"),
        }
    )
    source.update(
        {
            "project_id": getattr(item, "project_id"),
            "chromatography_type": getattr(item, "type_id"),
            "tool": getattr(item, "tool"),
            "schema_version": getattr(item, "schema_version"),
            "structure_refs": list(input_snapshot.structure_refs),
            "input": input_snapshot.input.model_dump(mode="python"),
            "model_selection": {
                "mode": "exact",
                "model_id": model.id,
                "model_version": model.version,
                "preprocessing_profile_id": model.preprocessing_profile_id,
                "preprocessing_profile_sha256": model.preprocessing_profile_sha256,
            },
        }
    )
    if getattr(item, "type_id", None) == "sfc" or getattr(existing, "chromatography_type", None) == "sfc":
        original = existing if existing is not None else source
        return build_prediction_execution_request(
            original,
            project_id=getattr(item, "project_id"),
            expected_type=getattr(item, "type_id"),
            expected_tool=getattr(item, "tool"),
            expected_schema_version=getattr(item, "schema_version"),
            model_snapshot=model,
            input_snapshot=input_snapshot,
        )
    return TypeAdapter(PredictionCreate).validate_python(source)


class PredictionWorker:
    def __init__(self, *, registry: CapabilityRegistry, store: PredictionExecutionStore) -> None:
        self.registry = registry
        self.store = store

    def handle(
        self,
        work: object | str,
        *,
        attempt: int | None = None,
        dispatch_id: str | None = None,
    ) -> WorkerResult:
        item = self.store.load_execution(work) if isinstance(work, str) else work
        if item is None:
            raise APIError(404, "RESOURCE_NOT_FOUND", "预测任务不存在或不可见")
        status_value = _attr(item, "status") or JobStatus.QUEUED
        status = status_value if isinstance(status_value, JobStatus) else JobStatus(status_value)
        if status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELED, JobStatus.TIMED_OUT, JobStatus.INTERRUPTED}:
            return WorkerResult(status=status, replayed=True)
        persisted_attempt = _attr(item, "attempt")
        if attempt is not None and persisted_attempt is not None and int(attempt) != int(persisted_attempt):
            return WorkerResult(status=status, replayed=True)
        persisted_dispatch = _attr(item, "dispatch_id")
        if dispatch_id is not None and persisted_dispatch is not None and dispatch_id != str(persisted_dispatch):
            return WorkerResult(status=status, replayed=True)
        try:
            claimed = self.store.mark_running(item, expected_attempt=persisted_attempt, expected_dispatch_id=dispatch_id or persisted_dispatch)
        except TypeError:
            # Small in-memory ports from older integrations do not expose the
            # CAS keyword arguments; production persistence does.
            claimed = self.store.mark_running(item)
        if claimed is False:
            return WorkerResult(status=JobStatus.RUNNING, replayed=True)
        try:
            request = _request_from_snapshot(item)
            model_snapshot = getattr(item, "model_snapshot")
            if not isinstance(model_snapshot, ModelSnapshot):
                model_snapshot = ModelSnapshot.model_validate(model_snapshot)
            input_snapshot = getattr(item, "input_snapshot")
            if not isinstance(input_snapshot, PredictionExecutionInputSnapshot):
                raw_snapshot = (
                    input_snapshot.model_dump(mode="python")
                    if hasattr(input_snapshot, "model_dump")
                    else input_snapshot
                )
                input_snapshot = PredictionExecutionInputSnapshot.model_validate(raw_snapshot)
            validate_hplc_execution_binding(
                request=request, model_snapshot=model_snapshot, input_snapshot=input_snapshot,
            )
            validate_gc_execution_binding(
                request=request, model_snapshot=model_snapshot, input_snapshot=input_snapshot,
            )
            execute_snapshot = self.registry.execute_snapshot
            execution_kwargs = {"model_snapshot": model_snapshot}
            parameters = inspect.signature(execute_snapshot).parameters
            if "normalized_structures" in parameters:
                execution_kwargs["normalized_structures"] = input_snapshot.normalized_structures
            if "normalized_modifier_structures" in parameters:
                execution_kwargs["normalized_modifier_structures"] = {
                    value.structure_ref: value.normalized_smiles
                    for value in input_snapshot.modifier_structures
                }
            result = execute_snapshot(getattr(item, "type_id"), request, **execution_kwargs)
        except Exception as exc:  # noqa: BLE001 - worker must persist every public failure
            error = _public_error(exc)
            self.store.mark_failed(item, error)
            return WorkerResult(status=JobStatus.FAILED, error=error)
        self.store.mark_succeeded(item, result)
        return WorkerResult(status=JobStatus.SUCCEEDED, result=result)


PredictionWorkerHandler = PredictionWorker


def handle_prediction_job(
    *, registry: CapabilityRegistry, store: PredictionExecutionStore, job_id: str, attempt: int | None = None, dispatch_id: str | None = None
) -> WorkerResult:
    return PredictionWorker(registry=registry, store=store).handle(job_id, attempt=attempt, dispatch_id=dispatch_id)


__all__ = ["PredictionExecutionStore", "PredictionWorker", "PredictionWorkerHandler", "WorkerResult", "handle_prediction_job"]
