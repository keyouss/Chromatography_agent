"""Controlled Model Center consumers.

Consumers receive only the stable three-field worker message and resolve the
business ID from the persisted platform Job.  No message field is treated as
a path, loader name, or arbitrary import.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import inspect
import json
from typing import Any, Mapping

from .errors import ModelCenterConflictError
from .ports import (
    AuditPort, DeploymentLoader, EvaluationProtocolPort, EvaluationRunner,
    ModelAssetVerifier, ModelCenterRepository,
)
from .schemas import (
    AuditEvent, CheckItem, ComparisonRole, DataSnapshot, Deployment,
    DeploymentRef, ModelEvaluation, RegistrationValidation, ModelRegistration,
    now_utc, new_id,
)
from chrom_platform.task_core.outbox_dispatch import LeaseLostError


@dataclass(frozen=True)
class ModelCenterWorkerMessage:
    job_id: str
    attempt: int
    dispatch_id: str


def _sync(repository: ModelCenterRepository, record: Any, status: str, *, lease_guard: object | None = None, **kwargs: Any) -> bool:
    """Return false for a stale shared Job fence without changing the record."""
    method = getattr(repository, "sync_model_lifecycle", None)
    if not callable(method):
        kind = type(record).__name__.lower()
        method = getattr(repository, f"sync_{'registration' if 'registration' in kind else 'evaluation' if 'evaluation' in kind else 'deployment'}_lifecycle", None)
    if not getattr(record, "job_id", None):
        return True
    # A persisted asynchronous record must never reach a terminal business
    # state without the shared Job/Attempt/Event projection port.
    if not callable(method):
        return False
    if lease_guard is None:
        return method(record, status, **kwargs) is not None
    return method(record, status, lease_guard=lease_guard, **kwargs) is not None


def _fenced(record: Any, *, attempt: int | None, dispatch_id: str | None) -> None:
    if attempt is not None and int(attempt) != int(getattr(record, "attempt", 1)):
        raise ModelCenterConflictError("model-center attempt is stale")
    if dispatch_id is not None and dispatch_id != getattr(record, "dispatch_id", None):
        raise ModelCenterConflictError("model-center dispatch is stale")


def _cancel_fenced(repository: ModelCenterRepository, record: Any, *, attempt: int | None, dispatch_id: str | None) -> None:
    if attempt is None or not dispatch_id:
        raise ModelCenterConflictError("cancel dispatch is required")
    if int(attempt) != int(getattr(record, "attempt", 1)):
        raise ModelCenterConflictError("model-center attempt is stale")
    if getattr(record, "job_id", None):
        validator = getattr(repository, "validate_model_cancel_dispatch", None)
        if not callable(validator) or not validator(record, dispatch_id):
            raise ModelCenterConflictError("cancel dispatch is stale")


def _error(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _check_lease(lease_guard: object | None) -> None:
    if lease_guard is not None and callable(getattr(lease_guard, "check", None)):
        lease_guard.check()


def _operation_key(dispatch_id: str | None, resource_id: str, operation: str) -> str:
    return f"{dispatch_id or 'legacy'}:{operation}:{resource_id}"


def _external(callable_obj: object, *, operation_key: str, require_operation_key: bool = False, **kwargs: Any) -> Any:
    """Pass a stable key to adapters that implement idempotent operations."""
    method = callable_obj
    try:
        parameters = inspect.signature(method).parameters
    except (TypeError, ValueError):
        parameters = {}
    supports_key = "operation_key" in parameters or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values())
    if require_operation_key and not supports_key:
        raise ModelCenterConflictError("MODEL_OPERATION_KEY_UNSUPPORTED")
    if supports_key:
        kwargs["operation_key"] = operation_key
    return method(**kwargs)  # type: ignore[misc]


def _supports_keyword(callable_obj: object, keyword: str) -> bool:
    try:
        parameters = inspect.signature(callable_obj).parameters
    except (TypeError, ValueError):
        return False
    return keyword in parameters or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )


class DeploymentWorker:
    def __init__(self, repository: ModelCenterRepository, *, loader: DeploymentLoader | None = None, audit: AuditPort | None = None):
        self.repository, self.loader, self.audit = repository, loader, audit

    def _fail(self, deployment: Deployment, message: str, *, lease_guard: object | None = None) -> Deployment:
        error = {"code": "DEPLOYMENT_FAILED", "message": message}
        sync = getattr(self.repository, "sync_deployment_lifecycle", None)
        if callable(sync) and not _sync(self.repository, deployment, "FAILED", lease_guard=lease_guard, error=error):
            return deployment
        value = deployment.model_copy(update={"status": "FAILED", "error": error, "finished_at": now_utc()})
        self.repository.save_deployment(value)
        return value

    def execute(self, project_id: str, deployment_id: str, *, attempt: int | None = None, dispatch_id: str | None = None, lease_guard: object | None = None) -> Deployment:
        deployment = self.repository.get_deployment(project_id, deployment_id)
        if deployment is None: raise KeyError("deployment does not exist")
        if attempt is not None and attempt != deployment.attempt:
            raise ModelCenterConflictError("deployment attempt is stale")
        if dispatch_id is not None and dispatch_id != deployment.dispatch_id:
            raise ModelCenterConflictError("deployment dispatch is stale")
        if deployment.status in {"SUCCEEDED", "FAILED", "CANCELED"}: return deployment
        sync = getattr(self.repository, "sync_deployment_lifecycle", None)
        if callable(sync) and not _sync(self.repository, deployment, "RUNNING", lease_guard=lease_guard):
            return deployment
        model = self.repository.get_model(project_id, deployment.target_model_id)
        if model is None: return self._fail(deployment, "target model does not exist", lease_guard=lease_guard)
        if self.loader is None: return self._fail(deployment, "deployment loader is not configured", lease_guard=lease_guard)
        try:
            _check_lease(lease_guard)
            _external(self.loader.preload, operation_key=_operation_key(dispatch_id, deployment.id, "deployment_preload"), require_operation_key=True, model=model, scope_id=deployment.scope_id)
            _check_lease(lease_guard)
        except LeaseLostError:
            return deployment
        except Exception:
            # Preload happens before the transaction, so a failed asset can never
            # change the production pointer or Champion relationship.
            return self._fail(deployment, "controlled deployment preload failed", lease_guard=lease_guard)
        running = deployment.model_copy(update={"status": "RUNNING"})
        self.repository.save_deployment(running)
        try:
            _check_lease(lease_guard)
            with self.repository.transaction():
                pointer = self.repository.get_pointer(project_id, deployment.scope_id, deployment.stage)
                updated_pointer = self.repository.cas_pointer(pointer, expected_revision=deployment.expected_pointer_revision, model_id=model.id)
                old_model = self.repository.get_model(project_id, pointer.model_id) if pointer.model_id else None
                now = now_utc()
                ref = DeploymentRef(id=deployment.id, scope_id=deployment.scope_id, stage=deployment.stage, status="SUCCEEDED", is_current=True)
                refs = [item.model_copy(update={"is_current": False}) for item in model.deployments if item.scope_id != deployment.scope_id or item.stage != deployment.stage]
                self.repository.save_model(model.model_copy(update={"deployment_status": deployment.stage, "deployments": refs + [ref], "comparison_roles": [item for item in model.comparison_roles if item.scope_id != deployment.scope_id] + ([ComparisonRole(scope_id=deployment.scope_id, role="CHAMPION")] if deployment.stage == "PRODUCTION" else []), "updated_at": now}))
                if old_model is not None and old_model.id != model.id:
                    old_roles = [item for item in old_model.comparison_roles if item.scope_id != deployment.scope_id]
                    old_roles.append(ComparisonRole(scope_id=deployment.scope_id, role="CHALLENGER"))
                    old_refs = [item.model_copy(update={"is_current": False}) for item in old_model.deployments]
                    self.repository.save_model(old_model.model_copy(update={"deployment_status": "NOT_DEPLOYED", "comparison_roles": old_roles, "deployments": old_refs, "updated_at": now}))
                finished = running.model_copy(update={"status": "SUCCEEDED", "pointer_revision": updated_pointer.revision, "finished_at": now})
                _check_lease(lease_guard)
                if not _sync(self.repository, finished, "SUCCEEDED", lease_guard=lease_guard, result_ref={"type": "deployment", "id": deployment.id}):
                    raise ModelCenterConflictError("shared job lifecycle rejected deployment completion")
                self.repository.save_deployment(finished)
                event = AuditEvent(id=new_id("audit"), project_id=project_id, actor_id="worker", interaction="worker", action=f"model_deployment.{deployment.operation.lower()}", resource_ref={"type": "deployment", "id": deployment.id}, result="SUCCEEDED", before_summary={"pointer_revision": pointer.revision, "model_id": pointer.model_id}, after_summary={"pointer_revision": updated_pointer.revision, "model_id": model.id}, timestamp=now)
                if self.audit is not None and callable(getattr(self.audit, "record", None)): self.audit.record(event)
                else: self.repository.record_audit(event)
                return finished
        except LeaseLostError:
            return deployment
        except LeaseLostError:
            return deployment
        except Exception:
            return self._fail(running, "controlled deployment execution failed", lease_guard=lease_guard)

    handle = execute

    def cancel(self, project_id: str, deployment_id: str, *, attempt: int | None = None, dispatch_id: str | None = None, lease_guard: object | None = None):
        deployment = self.repository.get_deployment(project_id, deployment_id)
        if deployment is None: raise KeyError("deployment does not exist")
        _cancel_fenced(self.repository, deployment, attempt=attempt, dispatch_id=dispatch_id)
        if deployment.status in {"SUCCEEDED", "FAILED", "CANCELED"}: return deployment
        if not _sync(self.repository, deployment, "CANCELED", lease_guard=lease_guard): return deployment
        return self.repository.save_deployment(deployment.model_copy(update={"status": "CANCELED", "error": _error("DEPLOYMENT_CANCELED", "deployment canceled"), "finished_at": now_utc()}))


class RegistrationValidationWorker:
    def __init__(
        self, repository: ModelCenterRepository, *, verifier: ModelAssetVerifier | None = None,
        package_recovery: object | None = None,
    ):
        self.repository, self.verifier = repository, verifier
        self.package_recovery = package_recovery

    def _finish(self, registration: ModelRegistration, *, valid: bool, checks: list[CheckItem], errors: list[dict[str, str]], message: str | None = None, lease_guard: object | None = None):
        _check_lease(lease_guard)
        revision = registration.revision + 1
        report = RegistrationValidation(registration_id=registration.id, revision=revision, etag=f'"{revision}"', valid=valid, checks=checks, field_errors=errors)
        value = registration.model_copy(update={"status": "VALID" if valid else "INVALID", "manifest_snapshot": (registration.manifest_snapshot or {}) if valid else registration.manifest_snapshot, "checks": checks, "validation_report": report, "revision": revision, "etag": report.etag, "error_report_ref": None if valid else {"type": "registration_validation", "id": registration.id}, "updated_at": now_utc()})
        if not _sync(self.repository, registration, "SUCCEEDED" if valid else "FAILED", lease_guard=lease_guard, result_ref={"type": "model_registration", "id": registration.id} if valid else None, error=None if valid else _error("REGISTRATION_VALIDATION_FAILED", message or "模型资产验证失败")):
            return registration
        self.repository.save_registration(value)
        return value

    def _fail(self, registration: ModelRegistration, message: str, *, lease_guard: object | None = None):
        checks = registration.checks or [CheckItem(step="worker", status="FAILED", message=message)]
        return self._finish(registration, valid=False, checks=checks, errors=[{"path": "worker", "code": "REGISTRATION_VALIDATION_FAILED", "message": message}], message=message, lease_guard=lease_guard)

    @staticmethod
    def _package_source(registration: ModelRegistration) -> bool:
        source = registration.source.model_dump(mode="json")
        return source.get("kind") == "external_package"

    def _package_cancel_state(self, registration: ModelRegistration, execute_dispatch_id: str) -> str:
        reader = getattr(self.repository, "model_package_cancel_state", None)
        if not callable(reader):
            raise ModelCenterConflictError("MODEL_PACKAGE_CANCEL_READER_UNAVAILABLE")
        return reader(registration, execute_dispatch_id=execute_dispatch_id)

    def _package_recovery_binding(
        self, registration: ModelRegistration, execute_dispatch_id: str,
        lease_guard: object | None,
    ) -> dict[str, object]:
        claim = getattr(lease_guard, "claim", None) if lease_guard is not None else None
        resource_ref = getattr(claim, "resource_ref", None)
        if (claim is None or getattr(claim, "dispatch_id", None) != execute_dispatch_id
                or getattr(claim, "aggregate_type", None) != "job"
                or getattr(claim, "aggregate_id", None) != registration.job_id
                or getattr(claim, "project_id", None) != registration.project_id
                or not isinstance(resource_ref, Mapping)
                or resource_ref.get("type") not in {"registration", "model_registration"}
                or resource_ref.get("id") != registration.id
                or not isinstance(getattr(claim, "lease_owner", None), str)
                or type(getattr(claim, "lease_token", None)) is not int):
            raise ModelCenterConflictError("MODEL_PACKAGE_RECOVERY_LEASE_BINDING_INVALID")
        return {
            "project_id": registration.project_id,
            "registration_id": registration.id,
            "job_id": str(registration.job_id),
            "attempt": int(registration.attempt),
            "execute_dispatch_id": execute_dispatch_id,
            "resource_type": str(resource_ref["type"]),
            "resource_id": str(resource_ref["id"]),
            "artifact_id": str(registration.source.model_dump(mode="json").get("artifact_id", "")),
            "lease_owner": str(claim.lease_owner),
            "lease_token": int(claim.lease_token),
        }

    def _record_prelaunch_stop(self, registration: ModelRegistration, execute_dispatch_id: str, lease_guard: object | None) -> None:
        recorder = getattr(self.repository, "record_model_package_termination", None)
        if not callable(recorder):
            raise ModelCenterConflictError("MODEL_PACKAGE_TERMINATION_STORE_UNAVAILABLE")
        request_ref = f"prelaunch:{registration.id}:{registration.attempt}"
        request_sha256 = hashlib.sha256(
            f"{registration.project_id}:{registration.job_id}:{registration.attempt}:{execute_dispatch_id}:never-started".encode("utf-8")
        ).hexdigest()
        recorder(
            registration, execute_dispatch_id=execute_dispatch_id,
            request_ref=request_ref, request_sha256=request_sha256,
            termination={
                "started": False, "never_started": True, "confirmed": True,
                "process_id": None, "creation_filetime": None, "exit_filetime": None,
                "exit_code": None, "job_active_processes": 0,
                "cleanup_completed": True, "output_drained": True,
                "stop_cause": "cancel_before_start",
            }, lease_guard=lease_guard,
        )

    def _record_executor_stop(self, registration: ModelRegistration, execute_dispatch_id: str, receipt: object, lease_guard: object | None) -> bool:
        if not isinstance(receipt, Mapping):
            return False
        termination = receipt.get("termination")
        request_ref = receipt.get("request_ref")
        request_sha256 = receipt.get("request_sha256")
        if not isinstance(termination, Mapping) or not isinstance(request_ref, str) or not isinstance(request_sha256, str):
            return False
        if termination.get("request_ref") != request_ref or termination.get("request_sha256") != request_sha256:
            raise ModelCenterConflictError("MODEL_PACKAGE_TERMINATION_RECEIPT_BINDING_INVALID")
        preflight_binding = {
            "op": "verify_gc_safetensors",
            "project_id": registration.project_id,
            "source_kind": registration.source.model_dump(mode="json").get("kind"),
            "artifact_id": registration.source.model_dump(mode="json").get("artifact_id"),
            "task_type": registration.task_type,
            "operation_key": _operation_key(execute_dispatch_id, registration.id, "registration_verify"),
        }
        preflight_sha = hashlib.sha256(
            json.dumps(preflight_binding, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        preflight_stop = (
            receipt.get("executor_status") == "NOT_STARTED"
            and receipt.get("operation_key") == preflight_binding["operation_key"]
            and request_ref == f"preflight:{preflight_sha[:48]}"
            and request_sha256 == preflight_sha
            and termination.get("started") is False
            and termination.get("never_started") is True
            and termination.get("confirmed") is True
            and termination.get("cleanup_completed") is True
            and termination.get("output_drained") is True
            and termination.get("process_id") is None
            and termination.get("creation_filetime") is None
            and termination.get("exit_filetime") is None
            and termination.get("exit_code") is None
            and termination.get("job_active_processes") == 0
        )
        durable_stop = receipt.get("ledger_stop_confirmed") is True and (
            termination.get("confirmed") is True
            and termination.get("cleanup_completed") is True
            and termination.get("output_drained") is True
        )
        if not (preflight_stop or durable_stop):
            # In particular, never project a parent claim into the public
            # JobEvent stream when the private recovery sink did not commit it.
            return False
        recorder = getattr(self.repository, "record_model_package_termination", None)
        if not callable(recorder):
            raise ModelCenterConflictError("MODEL_PACKAGE_TERMINATION_STORE_UNAVAILABLE")
        recorder(
            registration, execute_dispatch_id=execute_dispatch_id,
            request_ref=request_ref, request_sha256=request_sha256,
            termination={key: value for key, value in termination.items() if key not in {"request_ref", "request_sha256"}},
            lease_guard=lease_guard,
        )
        return preflight_stop or durable_stop

    def execute(self, project_id: str, registration_id: str, *, attempt: int | None = None, dispatch_id: str | None = None, lease_guard: object | None = None):
        registration = self.repository.get_registration(project_id, registration_id)
        if registration is None:
            raise KeyError("registration does not exist")
        _fenced(registration, attempt=attempt, dispatch_id=dispatch_id)
        if registration.status in {"VALID", "INVALID", "REGISTERED", "CANCELED"}:
            return registration
        source = registration.source.model_dump(mode="json")
        package_source = source.get("kind") == "external_package"
        package_dispatch_id = dispatch_id or registration.dispatch_id
        if package_source and registration.job_id:
            cancel_state = self._package_cancel_state(registration, package_dispatch_id)
            if cancel_state not in {"RUNNING", "READY"}:
                if cancel_state == "NEVER_STARTED":
                    self._record_prelaunch_stop(registration, package_dispatch_id, lease_guard)
                return registration
        if not _sync(self.repository, registration, "RUNNING", lease_guard=lease_guard):
            return registration
        if self.verifier is None:
            return self._fail(registration, "model asset verifier is not configured", lease_guard=lease_guard)
        try:
            _check_lease(lease_guard)
            verify_kwargs: dict[str, Any] = {
                "project_id": project_id,
                "source": source,
                "task_type": registration.task_type,
            }
            fence_check = getattr(lease_guard, "check", None) if lease_guard is not None else None
            if callable(fence_check) and _supports_keyword(self.verifier.verify, "check_fence"):
                verify_kwargs["check_fence"] = fence_check
            elif source.get("kind") == "external_package" and lease_guard is not None:
                raise ModelCenterConflictError("MODEL_ASSET_VERIFIER_LEASE_FENCE_UNSUPPORTED")
            if package_source and registration.job_id:
                verify_kwargs["cancelled"] = lambda: self._package_cancel_state(registration, package_dispatch_id) not in {"RUNNING", "READY"}
                recovery_binding = self._package_recovery_binding(registration, package_dispatch_id, lease_guard)
                if (self.package_recovery is None
                        or not _supports_keyword(self.verifier.verify, "recovery_port")
                        or not _supports_keyword(self.verifier.verify, "recovery_binding")):
                    raise ModelCenterConflictError("MODEL_PACKAGE_RECOVERY_UNAVAILABLE")
                verify_kwargs["recovery_port"] = self.package_recovery
                verify_kwargs["recovery_binding"] = recovery_binding
            evidence = _external(
                self.verifier.verify,
                operation_key=_operation_key(dispatch_id, registration.id, "registration_verify"),
                require_operation_key=True,
                **verify_kwargs,
            )
            _check_lease(lease_guard)
            if package_source and registration.job_id:
                if isinstance(evidence, Mapping) and evidence.get("error_code") in {
                    "MODEL_PACKAGE_RECOVERY_UNAVAILABLE", "MODEL_PACKAGE_RECOVERY_REQUIRED",
                    "MODEL_PACKAGE_RECOVERY_REQUEST_REUSED",
                }:
                    return registration
                execution_receipt = evidence.get("_execution_receipt") if isinstance(evidence, Mapping) else None
                # Once verify() has been called, missing receipt data cannot
                # prove that no child was launched. Keep the registration
                # nonterminal rather than manufacturing a prelaunch receipt.
                if not isinstance(execution_receipt, Mapping):
                    return registration
                stop_confirmed = self._record_executor_stop(
                    registration, package_dispatch_id, execution_receipt, lease_guard,
                )
                if not stop_confirmed:
                    return registration
                try:
                    cancel_state = self._package_cancel_state(registration, package_dispatch_id)
                except Exception:
                    # Keep the stop receipt durable, but never infer a clear
                    # cancellation state from a failed fresh read.
                    return registration
                unconfirmed = (
                    isinstance(execution_receipt, Mapping)
                    and execution_receipt.get("executor_error_code") == "TERMINATION_UNCONFIRMED"
                )
                if unconfirmed:
                    return registration
                if cancel_state not in {"RUNNING", "READY"}:
                    return registration
            from .service import _asset_evidence
            manifest, checks, errors = _asset_evidence(evidence, source=source)
            if not errors and bool(evidence.get("valid", True)):
                return self._finish(registration.model_copy(update={"manifest_snapshot": manifest}), valid=True, checks=checks, errors=[], lease_guard=lease_guard)
            return self._finish(registration.model_copy(update={"manifest_snapshot": manifest}), valid=False, checks=checks, errors=errors, message="模型资产验证失败", lease_guard=lease_guard)
        except LeaseLostError:
            return registration
        except ModelCenterConflictError as exc:
            if package_source and registration.job_id:
                # A fresh cancellation read or receipt failure is not a normal
                # validation failure and must remain retryable/nonterminal.
                raise
            if "MODEL_ASSET_VERIFIER_LEASE_FENCE_UNSUPPORTED" in str(exc):
                return self._fail(registration, "controlled asset verification failed", lease_guard=lease_guard)
            if package_source:
                raise
            return self._fail(registration, "controlled asset verification failed", lease_guard=lease_guard)
        except Exception:
            if package_source:
                # A fresh cancellation read or receipt failure is not a normal
                # validation failure and must remain retryable/nonterminal.
                raise
            return self._fail(registration, "controlled asset verification failed", lease_guard=lease_guard)

    handle = execute

    def cancel(self, project_id: str, registration_id: str, *, attempt: int | None = None, dispatch_id: str | None = None, lease_guard: object | None = None):
        value = self.repository.get_registration(project_id, registration_id)
        if value is None: raise KeyError("registration does not exist")
        _cancel_fenced(self.repository, value, attempt=attempt, dispatch_id=dispatch_id)
        if value.status in {"VALID", "INVALID", "REGISTERED", "CANCELED"}: return value
        if self._package_source(value) and value.job_id:
            state = self._package_cancel_state(value, value.dispatch_id)
            if state == "NEVER_STARTED":
                self._record_prelaunch_stop(value, value.dispatch_id, lease_guard)
            else:
                reader = getattr(self.repository, "model_package_termination_receipt", None)
                receipt = reader(value, execute_dispatch_id=value.dispatch_id) if callable(reader) else None
                termination = receipt.get("termination") if isinstance(receipt, Mapping) else None
                if (not isinstance(termination, Mapping)
                        or termination.get("confirmed") is not True
                        or termination.get("cleanup_completed") is not True):
                    termination = None
                if termination is None and self.package_recovery is not None:
                    recover = getattr(self.package_recovery, "confirmed_receipt_for_attempt", None)
                    if callable(recover):
                        receipt = recover(
                            project_id=value.project_id, registration_id=value.id,
                            job_id=str(value.job_id), attempt=int(value.attempt),
                            execute_dispatch_id=str(value.dispatch_id),
                            artifact_id=str(value.source.model_dump(mode="json").get("artifact_id", "")),
                        )
                        if isinstance(receipt, Mapping):
                            termination = receipt.get("termination")
                if not isinstance(termination, Mapping):
                    raise ModelCenterConflictError("MODEL_PACKAGE_TERMINATION_PENDING")
                if termination.get("confirmed") is not True or termination.get("cleanup_completed") is not True:
                    raise ModelCenterConflictError("MODEL_PACKAGE_TERMINATION_UNCONFIRMED")
        if not _sync(self.repository, value, "CANCELED", lease_guard=lease_guard): return value
        return self.repository.save_registration(value.model_copy(update={"status": "CANCELED", "updated_at": now_utc()}))


class EvaluationWorker:
    def __init__(self, repository: ModelCenterRepository, *, protocols: EvaluationProtocolPort | None = None, runner: EvaluationRunner | None = None):
        self.repository, self.protocols, self.runner = repository, protocols, runner

    def _fail(self, value: ModelEvaluation, message: str, *, lease_guard: object | None = None):
        error = _error("EVALUATION_FAILED", message)
        if not _sync(self.repository, value, "FAILED", lease_guard=lease_guard, error=error): return value
        return self.repository.save_evaluation(value.model_copy(update={"status": "FAILED", "pass_status": "FAILED", "error": error, "updated_at": now_utc()}))

    def execute(self, project_id: str, evaluation_id: str, *, attempt: int | None = None, dispatch_id: str | None = None, lease_guard: object | None = None):
        value = self.repository.get_evaluation(project_id, evaluation_id)
        if value is None: raise KeyError("evaluation does not exist")
        _fenced(value, attempt=attempt, dispatch_id=dispatch_id)
        if value.status in {"SUCCEEDED", "FAILED", "CANCELED"}: return value
        if not _sync(self.repository, value, "RUNNING", lease_guard=lease_guard): return value
        model = self.repository.get_model(project_id, value.model_snapshot.id)
        if model is None: return self._fail(value, "evaluation model does not exist", lease_guard=lease_guard)
        if self.protocols is None or self.runner is None:
            return self._fail(value, "evaluation runner or protocol registry is not configured", lease_guard=lease_guard)
        try:
            _check_lease(lease_guard)
            protocol = _external(self.protocols.get, operation_key=_operation_key(dispatch_id, value.id, "evaluation_protocol"), protocol_id=value.protocol_snapshot.id, version=value.protocol_snapshot.version)
            _check_lease(lease_guard)
            if protocol is None or getattr(protocol, "status", None) != "APPROVED":
                return self._fail(value, "evaluation protocol is unavailable", lease_guard=lease_guard)
            _check_lease(lease_guard)
            result = _external(self.runner.run, operation_key=_operation_key(dispatch_id, value.id, "evaluation_run"), require_operation_key=True, model=model, protocol=value.protocol_snapshot, data_snapshot=value.data_snapshot)
            _check_lease(lease_guard)
            if not isinstance(result, Mapping):
                return self._fail(value, "evaluation runner returned no result", lease_guard=lease_guard)
            passed = str(result.get("pass_status", "FAILED"))
            if passed not in {"PASSED", "FAILED", "PENDING"} or passed != "PASSED":
                return self._fail(value, "evaluation did not pass", lease_guard=lease_guard)
            if not _sync(self.repository, value, "SUCCEEDED", lease_guard=lease_guard, result_ref={"type": "model_evaluation", "id": value.id}): return value
            return self.repository.save_evaluation(value.model_copy(update={"status": "SUCCEEDED", "metrics": dict(result.get("metrics", {})), "pass_status": "PASSED", "report_artifact_id": result.get("report_artifact_id"), "error": None, "updated_at": now_utc()}))
        except LeaseLostError:
            return value
        except Exception:
            return self._fail(value, "controlled evaluation execution failed", lease_guard=lease_guard)

    handle = execute

    def cancel(self, project_id: str, evaluation_id: str, *, attempt: int | None = None, dispatch_id: str | None = None, lease_guard: object | None = None):
        value = self.repository.get_evaluation(project_id, evaluation_id)
        if value is None: raise KeyError("evaluation does not exist")
        _cancel_fenced(self.repository, value, attempt=attempt, dispatch_id=dispatch_id)
        if value.status in {"SUCCEEDED", "FAILED", "CANCELED"}: return value
        if not _sync(self.repository, value, "CANCELED", lease_guard=lease_guard): return value
        return self.repository.save_evaluation(value.model_copy(update={"status": "CANCELED", "error": _error("EVALUATION_CANCELED", "evaluation canceled"), "updated_at": now_utc()}))


class ModelCenterConsumer:
    """Static handler registry entry point for execute and cancel outbox rows."""
    def __init__(self, repository: ModelCenterRepository, *, verifier: ModelAssetVerifier | None = None, protocols: EvaluationProtocolPort | None = None, runner: EvaluationRunner | None = None, loader: DeploymentLoader | None = None, audit: AuditPort | None = None, package_recovery: object | None = None):
        self.repository = repository
        self.deployment = DeploymentWorker(repository, loader=loader, audit=audit)
        self.registration = RegistrationValidationWorker(repository, verifier=verifier, package_recovery=package_recovery)
        self.evaluation = EvaluationWorker(repository, protocols=protocols, runner=runner)

    def _resolve(self, message: ModelCenterWorkerMessage, *, event_type: str):
        resolver = getattr(self.repository, "resolve_model_job", None)
        if not callable(resolver):
            raise ModelCenterConflictError("model-center job resolver is not configured")
        return resolver(message, event_type=event_type)

    def handle(self, message: ModelCenterWorkerMessage, *, lease_guard: object | None = None):
        resolved = self._resolve(message, event_type="job.execute")
        if resolved is None: return None
        record = resolved["record"]; kind = resolved["kind"]; project_id = resolved["job"].project_id
        if kind == "model_deployment": return self.deployment.execute(project_id, record.id, attempt=message.attempt, dispatch_id=message.dispatch_id, lease_guard=lease_guard)
        if kind == "model_registration_validation": return self.registration.execute(project_id, record.id, attempt=message.attempt, dispatch_id=message.dispatch_id, lease_guard=lease_guard)
        if kind == "model_evaluation": return self.evaluation.execute(project_id, record.id, attempt=message.attempt, dispatch_id=message.dispatch_id, lease_guard=lease_guard)
        return None

    def handle_cancel(self, message: ModelCenterWorkerMessage, *, lease_guard: object | None = None):
        resolved = self._resolve(message, event_type="job.cancel")
        if resolved is None: return None
        record = resolved["record"]; project_id = resolved["job"].project_id
        if resolved["kind"] == "model_deployment": return self.deployment.cancel(project_id, record.id, attempt=message.attempt, dispatch_id=message.dispatch_id, lease_guard=lease_guard)
        if resolved["kind"] == "model_registration_validation": return self.registration.cancel(project_id, record.id, attempt=message.attempt, dispatch_id=message.dispatch_id, lease_guard=lease_guard)
        if resolved["kind"] == "model_evaluation": return self.evaluation.cancel(project_id, record.id, attempt=message.attempt, dispatch_id=message.dispatch_id, lease_guard=lease_guard)
        return None

    __call__ = handle


RegistrationWorker = RegistrationValidationWorker
ModelEvaluationWorker = EvaluationWorker
ModelCenterWorker = ModelCenterConsumer
WorkerMessage = ModelCenterWorkerMessage

__all__ = [
    "DeploymentWorker", "RegistrationValidationWorker", "RegistrationWorker",
    "EvaluationWorker", "ModelEvaluationWorker", "ModelCenterConsumer",
    "ModelCenterWorker", "ModelCenterWorkerMessage", "WorkerMessage",
]
