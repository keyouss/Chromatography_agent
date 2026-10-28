"""Ports keep model-center policy independent from storage, queues and loaders."""

from __future__ import annotations

from contextlib import nullcontext
from typing import Any, Callable, Mapping, Protocol, Sequence

from .schemas import (
    AuditEvent, DataSnapshot, Deployment, DeploymentDraft, EvaluationProtocol,
    Model, ModelEvaluation, ModelRegistration, ProductionPointer,
)


class ModelCenterRepository(Protocol):
    def transaction(self): ...
    def save_registration(self, value: ModelRegistration) -> ModelRegistration: ...
    def get_registration(self, project_id: str, registration_id: str) -> ModelRegistration | None: ...
    def save_model(self, value: Model) -> Model: ...
    def get_model(self, project_id: str, model_id: str) -> Model | None: ...
    def list_models(self, project_id: str, *, task_type: str | None = None, lifecycle: str | None = None) -> Sequence[Model]: ...
    def save_evaluation(self, value: ModelEvaluation) -> ModelEvaluation: ...
    def get_evaluation(self, project_id: str, evaluation_id: str) -> ModelEvaluation | None: ...
    def list_evaluations(self, project_id: str, model_id: str | None = None) -> Sequence[ModelEvaluation]: ...
    def save_approval(self, value: Any) -> Any: ...
    def get_approval(self, project_id: str, approval_id: str) -> Any | None: ...
    def save_draft(self, value: DeploymentDraft) -> DeploymentDraft: ...
    def get_draft(self, project_id: str, draft_id: str) -> DeploymentDraft | None: ...
    def save_deployment(self, value: Deployment) -> Deployment: ...
    def get_deployment(self, project_id: str, deployment_id: str) -> Deployment | None: ...
    def list_deployments(self, project_id: str, scope_id: str | None = None) -> Sequence[Deployment]: ...
    def get_pointer(self, project_id: str, scope_id: str, stage: str) -> ProductionPointer: ...
    def cas_pointer(self, pointer: ProductionPointer, *, expected_revision: int, model_id: str) -> ProductionPointer: ...
    def record_audit(self, event: AuditEvent) -> AuditEvent: ...
    def idempotent_get(self, scope: str, request_hash: str) -> Any | None: ...
    def idempotent_put(self, scope: str, request_hash: str, value: Any) -> None: ...


class ArtifactPort(Protocol):
    def get(self, project_id: str, artifact_id: str) -> object | None: ...
    def list(self, *, project_id: str, model_id: str | None = None, artifact_id: str | None = None) -> Sequence[object]: ...


class ModelAssetVerifier(Protocol):
    """Runs only in the controlled worker; never loads an artifact in HTTP."""

    def verify(
        self, *, project_id: str, source: Mapping[str, Any], task_type: str,
        operation_key: str = "service",
        cancelled: Callable[[], bool] | None = None,
        check_fence: Callable[[], None] | None = None,
        recovery_port: "ModelPackageExecutionRecoveryPort | None" = None,
        recovery_binding: Mapping[str, object] | None = None,
    ) -> Mapping[str, Any]: ...


class ModelPackageExecutionRecoveryPort(Protocol):
    """Private launch/stop journal; it never projects model validity."""

    def before_launch(self, binding: Mapping[str, object], request_ref: str, request_sha256: str) -> None: ...
    def stop_sink(
        self, binding: Mapping[str, object], request_ref: str, request_sha256: str,
        termination: Mapping[str, object],
    ) -> None: ...
    def confirmed_stop(
        self, binding: Mapping[str, object], request_ref: str, request_sha256: str,
    ) -> bool: ...
    def confirmed_receipt_for_attempt(
        self, *, project_id: str, registration_id: str, job_id: str, attempt: int,
        execute_dispatch_id: str, artifact_id: str,
    ) -> Mapping[str, object] | None: ...


class EvaluationProtocolPort(Protocol):
    def get(self, protocol_id: str, version: str) -> EvaluationProtocol | object | None: ...
    def list(self, task_type: str | None = None) -> Sequence[EvaluationProtocol]: ...


class EvaluationRunner(Protocol):
    def run(self, *, model: Model, protocol: EvaluationProtocol, data_snapshot: DataSnapshot, operation_key: str = "service") -> Mapping[str, Any]: ...


class DeploymentLoader(Protocol):
    def preload(self, *, model: Model, scope_id: str, operation_key: str = "service") -> object: ...


class ConfirmationPort(Protocol):
    def consume(self, **kwargs: Any) -> object: ...


class AuditPort(Protocol):
    def record(self, event: AuditEvent) -> object: ...


class JobPort(Protocol):
    def submit(self, *, kind: str, resource_ref: Mapping[str, str], project_id: str, owner_id: str) -> Mapping[str, str]: ...


class StaticModelAssetVerifier:
    """Explicit test adapter; production wiring must provide a real verifier."""

    def __init__(self, result: Mapping[str, Any]):
        self.result = dict(result)

    def verify(
        self, *, project_id: str, source: Mapping[str, Any], task_type: str,
        operation_key: str = "service",
        cancelled: Callable[[], bool] | None = None,
        check_fence: Callable[[], None] | None = None,
        recovery_port: ModelPackageExecutionRecoveryPort | None = None,
        recovery_binding: Mapping[str, object] | None = None,
    ) -> Mapping[str, Any]:
        if cancelled is not None and cancelled():
            return {**self.result, "valid": False, "error_code": "CANCELLED"}
        if check_fence is not None:
            check_fence()
        return dict(self.result)


class StaticEvaluationProtocolRegistry:
    def __init__(self, protocols: Sequence[EvaluationProtocol] = ()):
        self.protocols = {(item.id, item.version): item for item in protocols}

    def get(self, protocol_id: str, version: str):
        return self.protocols.get((protocol_id, version))

    def list(self, task_type: str | None = None):
        return [p for p in self.protocols.values() if task_type is None or p.task_type == task_type]


class StaticEvaluationRunner:
    def __init__(self, result: Mapping[str, Any] | None = None):
        self.result = dict(result or {"metrics": {}, "pass_status": "PASSED"})

    def run(self, *, model: Model, protocol: EvaluationProtocol, data_snapshot: DataSnapshot, operation_key: str = "service"):
        return dict(self.result)


class StaticDeploymentLoader:
    def __init__(self, *, fail: bool = False):
        self.fail = fail

    def preload(self, *, model: Model, scope_id: str, operation_key: str = "service"):
        if self.fail:
            raise RuntimeError("model preload failed")
        return {"model_id": model.id, "scope_id": scope_id}


__all__ = [name for name in globals() if not name.startswith("_")]
