"""Application service for model registration, evaluation and deployment governance."""

from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime
import hashlib
import json
import re
from typing import Any, Mapping

from chrom_platform.core.dto import ActorContext, ResourceRef

from .errors import (
    ModelCenterConfirmationError, ModelCenterConflictError, ModelCenterError,
    ModelCenterPreconditionError,
    ModelCenterNotFoundError, ModelCenterPermissionError,
    ModelCenterUnavailableError, ModelCenterValidationError,
)
from .persistence import InMemoryModelCenterRepository
from .ports import (
    AuditPort, ArtifactPort, ConfirmationPort, DeploymentLoader,
    EvaluationProtocolPort, EvaluationRunner, JobPort, ModelAssetVerifier,
    ModelCenterRepository,
)
from .schemas import (
    ApprovalDecision, ApprovalRequest, ApprovalRequestCreate, Artifact, AuditEvent,
    ComparisonCreate, DataSnapshot, Deployment, DeploymentAccepted,
    DeploymentDraft, DeploymentDraftCreate, DeploymentRef, DeploymentValidation,
    EvaluationAccepted, EvaluationCreate, EvaluationProtocol, Model, ModelComparison, ModelDetail,
    ModelEvaluation, ModelRegistration, ModelRegistrationCreate, ModelSnapshot,
    ProductionPointer, RegistrationAccepted, RegistrationValidation, RollbackDraftCreate,
    CheckItem, ComparisonRole, ModelCard, new_id, now_utc,
)


_REQUIRED_ASSET_CHECKS = frozenset({
    "manifest/hash", "allowlisted loader/format", "dependency/runtime",
    "schema", "load", "golden", "finite values",
})


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def config_hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _get(value: object, name: str, default: object = None) -> object:
    if isinstance(value, Mapping): return value.get(name, default)
    return getattr(value, name, default)


def _actor_id(actor: ActorContext | str | None) -> str:
    if isinstance(actor, ActorContext): return actor.actor_id
    return str(actor or "service")


def _idempotency_scope(project_id: str, actor: ActorContext | str | None, method: str, route: str, key: str) -> str:
    return f"{_actor_id(actor)}:{project_id}:{method.upper()}:{route}:{key}"


def _mark_replayed(value: object):
    if hasattr(value, "_idempotency_replayed"):
        value._idempotency_replayed = True
    return value


def _permission(actor: ActorContext | str | None, permission: str, *, interaction: str, dangerous: bool = False) -> None:
    if dangerous and interaction.lower() in {"agent_tool", "assistant", "assistant_tool", "agent"}:
        raise ModelCenterPermissionError("agent 工具不能执行该治理操作")
    if isinstance(actor, ActorContext) and not actor.can(permission):
        raise ModelCenterPermissionError(f"缺少 {permission} 权限")


def _sha(value: object) -> str:
    return value if isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value) else hashlib.sha256(_canonical(value).encode()).hexdigest()


def _asset_evidence(evidence: Mapping[str, Any], *, source: Mapping[str, Any]) -> tuple[dict[str, object], list[CheckItem], list[dict[str, str]]]:
    """Normalize and validate worker evidence before a model can be created."""
    raw_manifest = evidence.get("manifest", {})
    manifest = dict(raw_manifest) if isinstance(raw_manifest, Mapping) else {}
    evidence_sha = evidence.get("artifact_sha256") or evidence.get("sha256")
    manifest_sha = manifest.get("artifact_sha256") or manifest.get("sha256")
    if evidence_sha and "artifact_sha256" not in manifest:
        manifest["artifact_sha256"] = evidence_sha
        manifest_sha = evidence_sha
    if evidence.get("loader_id") and "loader_id" not in manifest:
        manifest["loader_id"] = evidence["loader_id"]

    raw_checks = evidence.get("checks", [])
    if isinstance(raw_checks, Mapping):
        raw_checks = [{"step": key, "status": value} for key, value in raw_checks.items()]
    checks: list[CheckItem] = []
    for item in raw_checks if isinstance(raw_checks, list) else []:
        if isinstance(item, CheckItem):
            checks.append(item)
        elif isinstance(item, Mapping):
            checks.append(CheckItem(
                step=str(item.get("step", "unknown")),
                status=str(item.get("status", "FAILED")),
                report_ref=item.get("report_ref"),
                message=item.get("message"),
            ))

    errors: list[dict[str, str]] = []
    steps = {item.step: item for item in checks}
    missing = _REQUIRED_ASSET_CHECKS - set(steps)
    if missing:
        errors.append({"path": "checks", "code": "CHECKS_INCOMPLETE", "message": "模型验证清单不完整"})
    if any(steps.get(step) is None or steps[step].status != "PASSED" for step in _REQUIRED_ASSET_CHECKS):
        errors.append({"path": "checks", "code": "ASSET_NOT_VERIFIED", "message": "模型资产未通过全部验证"})
    if evidence.get("valid") is False:
        errors.append({"path": "asset", "code": "ASSET_NOT_VERIFIED", "message": "模型资产验证失败"})
    if evidence.get("loader_allowlisted") is False or manifest.get("loader_allowlisted") is False:
        errors.append({"path": "manifest.loader_id", "code": "UNTRUSTED_LOADER_OR_FORMAT", "message": "模型 Loader 不在允许清单"})
    fmt = str(manifest.get("format", evidence.get("format", ""))).lower()
    if fmt in {"pickle", "pkl", "joblib", "custom_pickle", "python"} or not manifest.get("loader_id"):
        errors.append({"path": "manifest", "code": "UNTRUSTED_LOADER_OR_FORMAT", "message": "只允许预注册的非代码 Loader 和数据型权重格式"})
    for field in ("version", "input_schema_version", "output_schema_version"):
        if not isinstance(manifest.get(field), str) or not manifest[field].strip():
            errors.append({"path": f"manifest.{field}", "code": "SCHEMA_MISSING", "message": "manifest 缺少必需 schema 字段"})
    if not isinstance(evidence_sha, str) or not isinstance(manifest_sha, str) or evidence_sha != manifest_sha or not re.fullmatch(r"[0-9a-f]{64}", str(evidence_sha)):
        errors.append({"path": "artifact_sha256", "code": "SHA256_MISMATCH", "message": "制品 SHA-256 缺失或与 manifest 不一致"})
    if evidence.get("finite") is False or evidence.get("golden") is False:
        errors.append({"path": "checks", "code": "ASSET_NOT_VERIFIED", "message": "黄金样例或有限值检查失败"})
    return manifest, checks, errors


class ModelCenterService:
    def __init__(
        self,
        repository: ModelCenterRepository | None = None,
        *,
        asset_verifier: ModelAssetVerifier | None = None,
        artifacts: ArtifactPort | None = None,
        evaluation_protocols: EvaluationProtocolPort | None = None,
        evaluation_runner: EvaluationRunner | None = None,
        loader: DeploymentLoader | None = None,
        confirmation: ConfirmationPort | None = None,
        audit: AuditPort | None = None,
        jobs: JobPort | None = None,
    ):
        if repository is None:
            raise ModelCenterUnavailableError("model center repository is not configured")
        self.repository = repository
        self.asset_verifier, self.artifacts = asset_verifier, artifacts
        self.evaluation_protocols, self.evaluation_runner = evaluation_protocols, evaluation_runner
        self.loader, self.confirmation, self.audit, self.jobs = loader, confirmation, audit, jobs

    def _require(self, value: object, message: str) -> object:
        if value is None: raise ModelCenterUnavailableError(message)
        return value

    def _submit_job(self, *, project_id: str, owner_id: str, kind: str, resource_ref: dict[str, str]):
        """Create the canonical platform Job and its job.execute outbox row."""
        jobs = self._require(self.jobs, "model worker is not configured")
        submit = getattr(jobs, "submit", None)
        if not callable(submit):
            raise ModelCenterUnavailableError("model worker is not configured")
        try:
            value = submit(kind=kind, resource_ref=resource_ref, project_id=project_id, owner_id=owner_id)
        except TypeError as exc:
            raise ModelCenterUnavailableError("model worker does not expose the platform Job port") from exc
        if not isinstance(value, Mapping) or any(not isinstance(value.get(key), str) or not value.get(key) for key in ("job_id", "dispatch_id")) or type(value.get("attempt")) is not int or value["attempt"] < 1:
            raise ModelCenterUnavailableError("model worker returned an invalid platform dispatch")
        return value

    def _registration(self, project_id: str, registration_id: str) -> ModelRegistration:
        value = self.repository.get_registration(project_id, registration_id)
        if value is None: raise ModelCenterNotFoundError("模型登记不存在")
        return value

    def _model(self, project_id: str, model_id: str) -> Model:
        value = self.repository.get_model(project_id, model_id)
        if value is None: raise ModelCenterNotFoundError("模型不存在")
        return value

    def _evaluation(self, project_id: str, evaluation_id: str) -> ModelEvaluation:
        value = self.repository.get_evaluation(project_id, evaluation_id)
        if value is None: raise ModelCenterNotFoundError("模型评测不存在")
        return value

    def _audit(self, *, project_id: str, actor: ActorContext | str | None, interaction: str, action: str, resource: dict[str, str], result: str, before: dict[str, object] | None = None, after: dict[str, object] | None = None, confirmation_id: str | None = None, config_hash_value: str | None = None) -> None:
        port = self._require(self.audit, "model center audit port is not configured")
        event = AuditEvent(id=new_id("audit"), project_id=project_id, actor_id=_actor_id(actor), interaction=interaction, action=action, resource_ref=resource, result=result, before_summary=before or {}, after_summary=after or {}, confirmation_id=confirmation_id, config_hash=config_hash_value, timestamp=now_utc())
        recorder = getattr(port, "record", None)
        if callable(recorder): recorder(event)
        else: self.repository.record_audit(event)

    def _consume(self, *, confirmation_id: str, project_id: str, resource_ref: dict[str, str], action: str, revision: int, config_hash_value: str, actor: ActorContext | str | None) -> None:
        port = self._require(self.confirmation, "model center confirmation port is not configured")
        try:
            port.consume(confirmation_id=confirmation_id, project_id=project_id, resource_ref=resource_ref, action=action, revision=revision, config_hash=config_hash_value, result_ref=resource_ref, actor_id=_actor_id(actor))
        except TypeError as exc:
            if "actor_id" not in str(exc): raise
            try:
                port.consume(confirmation_id=confirmation_id, project_id=project_id, resource_ref=resource_ref, action=action, revision=revision, config_hash=config_hash_value, result_ref=resource_ref)
            except Exception as fallback_exc:
                raise ModelCenterConfirmationError("Confirmation 无效、过期或绑定不匹配") from fallback_exc
        except Exception as exc:
            raise ModelCenterConfirmationError("Confirmation 无效、过期或绑定不匹配") from exc

    def create_registration(self, request: ModelRegistrationCreate, *, idempotency_key: str | None = None, actor: ActorContext | str | None = None) -> ModelRegistration:
        _permission(actor, "model:register", interaction="human")
        payload_hash = config_hash(request.model_dump(mode="json"))
        if idempotency_key:
            scope = _idempotency_scope(request.project_id, actor, "POST", "/models/registrations", idempotency_key)
            replay = self.repository.idempotent_get(scope, payload_hash)
            if replay is not None: return _mark_replayed(replay)
        artifacts = self._require(self.artifacts, "model registration artifact reader is not configured")
        artifact_id = request.source.artifact_id
        artifact = artifacts.get(request.project_id, artifact_id)
        if artifact is None or _get(artifact, "project_id") != request.project_id or _get(artifact, "id") != artifact_id:
            raise ModelCenterNotFoundError("模型制品不存在")
        if any(_get(artifact, field) is None for field in ("kind", "status", "scan_status")):
            raise ModelCenterValidationError("模型制品状态字段不完整")
        if _get(artifact, "kind") != "model_package":
            raise ModelCenterValidationError("模型制品类型不符合登记要求")
        if _get(artifact, "status") != "AVAILABLE" or _get(artifact, "scan_status") != "PASSED":
            raise ModelCenterValidationError("模型制品尚未通过入场校验")
        now = now_utc()
        value = ModelRegistration(id=new_id("reg"), project_id=request.project_id, revision=1, config_hash=payload_hash, etag='"1"', status="DRAFT", source=request.source, name=request.name, task_type=request.task_type, created_at=now, updated_at=now)
        self.repository.save_registration(value)
        if idempotency_key: self.repository.idempotent_put(scope, payload_hash, value)
        return value

    def get_registration(self, project_id: str, registration_id: str) -> ModelRegistration:
        return self._registration(project_id, registration_id)

    def validate_registration(self, project_id: str, registration_id: str, *, execute: bool = True, actor: ActorContext | str | None = None) -> RegistrationValidation:
        current = self._registration(project_id, registration_id)
        verifier = self._require(self.asset_verifier, "model asset verifier is not configured")
        source = current.source.model_dump(mode="json")
        if self.artifacts is not None:
            artifact_id = str(source["artifact_id"])
            artifact = self.artifacts.get(project_id, artifact_id)
            if artifact is None or str(_get(artifact, "status", "AVAILABLE")) not in {"AVAILABLE", "READY", "COMPLETED"}:
                raise ModelCenterValidationError("模型制品不存在、未完成校验或不可用")
        if not execute:
            job = self._submit_job(project_id=project_id, owner_id=_actor_id(actor), kind="model_registration_validation", resource_ref={"type": "model_registration", "id": current.id})
            next_revision = current.revision + 1
            current = current.model_copy(update={"status": "VALIDATING", "job_id": str(job["job_id"]), "attempt": int(job["attempt"]), "dispatch_id": str(job["dispatch_id"]), "revision": next_revision, "etag": f'"{next_revision}"', "updated_at": now_utc()})
            self.repository.save_registration(current)
            report = RegistrationValidation(registration_id=current.id, revision=current.revision, etag=current.etag, valid=False, checks=[CheckItem(step=step, status="PENDING") for step in sorted(_REQUIRED_ASSET_CHECKS)], warnings=[{"code": "VALIDATION_QUEUED", "message": "模型资产验证已提交受控 Worker"}])
            return report
        try:
            evidence = verifier.verify(project_id=project_id, source=source, task_type=current.task_type)
        except ModelCenterError: raise
        except Exception as exc:
            raise ModelCenterUnavailableError("model asset verification is unavailable") from exc
        manifest, checks, errors = _asset_evidence(evidence, source=source)
        valid = not errors and bool(evidence.get("valid", True))
        status = "VALID" if valid else "INVALID"
        next_revision = current.revision + 1
        next_etag = f'"{next_revision}"'
        report = RegistrationValidation(registration_id=current.id, revision=next_revision, etag=next_etag, valid=valid, checks=checks, field_errors=errors)
        updated = current.model_copy(update={"status": status, "manifest_snapshot": manifest, "checks": checks, "validation_report": report, "revision": next_revision, "etag": next_etag, "updated_at": now_utc(), "error_report_ref": None if valid else {"type": "registration_validation", "id": current.id}})
        self.repository.save_registration(updated)
        return report

    def queue_registration_validation(self, project_id: str, registration_id: str, *, idempotency_key: str, if_match: str, actor: ActorContext | str | None = None) -> RegistrationAccepted:
        """Queue registration validation once and expose the Accepted contract."""
        _permission(actor, "model:register", interaction="human")
        current = self._registration(project_id, registration_id)
        request_hash = config_hash({"registration_id": registration_id, "config_hash": current.config_hash, "body": {}})
        scope = _idempotency_scope(project_id, actor, "POST", "/models/registrations/{registration_id}/validate", idempotency_key)
        replay = self.repository.idempotent_get(scope, request_hash)
        if replay is not None:
            return _mark_replayed(replay if isinstance(replay, RegistrationAccepted) else RegistrationAccepted.model_validate(replay))
        if current.etag != if_match:
            raise ModelCenterPreconditionError("model registration ETag is stale")
        self.validate_registration(project_id, registration_id, execute=False, actor=actor)
        value = self._registration(project_id, registration_id)
        if not value.job_id:
            raise ModelCenterUnavailableError("registration validation did not create a platform Job")
        accepted = RegistrationAccepted(id=value.id, job_id=value.job_id, status="VALIDATING", resource_ref=ResourceRef(type="model_registration", id=value.id))
        self.repository.idempotent_put(scope, request_hash, accepted)
        return accepted

    def register(self, project_id: str, registration_id: str, *, confirmation_id: str, actor: ActorContext | str | None = None, interaction: str = "human", idempotency_key: str | None = None, if_match: str | None = None) -> Model:
        _permission(actor, "model:register", interaction=interaction)
        current = self._registration(project_id, registration_id)
        request_hash = config_hash({"registration_id": registration_id, "config_hash": current.config_hash, "confirmation_id": confirmation_id})
        idem_scope = _idempotency_scope(project_id, actor, "POST", "/models/registrations/{registration_id}/register", idempotency_key) if idempotency_key else None
        if idempotency_key:
            replay = self.repository.idempotent_get(idem_scope, request_hash)
            if replay is not None: return _mark_replayed(replay)
        if if_match is not None and current.etag != if_match: raise ModelCenterPreconditionError("model registration ETag is stale")
        if current.status != "VALID" or current.validation_report is None or not current.validation_report.valid:
            raise ModelCenterConflictError("登记必须先通过当前 revision 的验证")
        self._consume(confirmation_id=confirmation_id, project_id=project_id, resource_ref={"type": "model_registration", "id": registration_id}, action="model_registration.register", revision=current.revision, config_hash_value=current.config_hash, actor=actor)
        manifest = current.manifest_snapshot or {}
        now = now_utc()
        model_id = new_id("model")
        model = Model(id=model_id, project_id=project_id, name=current.name, version=str(manifest.get("version", "1")), task_type=current.task_type, artifact_id=str(current.source.artifact_id), sha256=str(manifest.get("artifact_sha256", manifest.get("sha256", "0" * 64))), lifecycle_status="CANDIDATE", input_schema_version=str(manifest.get("input_schema_version", "1")), output_schema_version=str(manifest.get("output_schema_version", "1")), feature_version=manifest.get("feature_version"), source={"kind": current.source.kind, "experiment_id": getattr(current.source, "experiment_id", None), "registration_id": current.id}, applicability={"scope_ids": manifest.get("scope_ids", [])}, model_card=ModelCard(summary=current.name), created_at=now, updated_at=now)
        self.repository.save_model(model)
        self.repository.save_registration(current.model_copy(update={"status": "REGISTERED", "model_id": model.id, "updated_at": now}))
        self._audit(project_id=project_id, actor=actor, interaction=interaction, action="model_registration.register", resource={"type": "model", "id": model.id}, result="SUCCEEDED", after={"lifecycle_status": model.lifecycle_status}, confirmation_id=confirmation_id, config_hash_value=current.config_hash)
        if idempotency_key: self.repository.idempotent_put(idem_scope, request_hash, model)
        return model

    def register_candidate(self, *, project_id: str, experiment: object, result: object):
        """Training-worker port: create a Candidate without approval or publish.

        The worker is already downstream of the confirmed training submission;
        it still goes through the same allowlisted asset verifier and returns
        the training module's small candidate DTO.
        """
        verifier = self._require(self.asset_verifier, "model asset verifier is not configured")
        artifact_id = str(_get(result, "artifact_id", "")); manifest = _get(result, "manifest", {})
        if not artifact_id or not isinstance(manifest, Mapping):
            raise ModelCenterValidationError("训练产物必须只引用已登记 artifact_id 并携带 manifest")
        task_type = str(_get(_get(experiment, "spec", experiment), "task_type", _get(experiment, "task_type", "")))
        experiment_id = str(_get(experiment, "id", ""))
        evidence = verifier.verify(project_id=project_id, source={"kind": "training_result", "experiment_id": experiment_id, "artifact_id": artifact_id, "manifest": manifest}, task_type=task_type)
        checked_manifest, _, errors = _asset_evidence(evidence, source={"artifact_id": artifact_id})
        if errors:
            raise ModelCenterValidationError("训练产物未通过完整模型资产验证", details={"errors": errors})
        import importlib
        training_schemas = importlib.import_module("chrom_platform.modules.training.schemas")
        # Candidate identity is the Model Center model identity.  Training
        # stores this returned ``id`` as candidate_model_id, so the worker,
        # model-center lookup, and later approval flow all address one object.
        now = now_utc(); model = Model(id=new_id("model"), project_id=project_id, name=str(_get(experiment, "name", "training candidate")), version=str(checked_manifest["version"]), task_type=task_type, artifact_id=artifact_id, sha256=str(checked_manifest["artifact_sha256"]), lifecycle_status="CANDIDATE", input_schema_version=str(checked_manifest["input_schema_version"]), output_schema_version=str(checked_manifest["output_schema_version"]), source={"kind": "training_result", "experiment_id": experiment_id, "registration_id": None}, applicability={"scope_ids": checked_manifest.get("scope_ids", [])}, created_at=now, updated_at=now)
        candidate = training_schemas.TrainingCandidate(id=model.id, project_id=project_id, experiment_id=experiment_id, artifact_id=artifact_id, status="CANDIDATE", published=False, created_at=now)
        self.repository.save_model(model)
        return candidate

    def list_models(self, project_id: str, *, task_type: str | None = None, lifecycle: str | None = None, scope_id: str | None = None):
        values = list(self.repository.list_models(project_id, task_type=task_type, lifecycle=lifecycle))
        if scope_id is None:
            return values
        result = []
        for model in values:
            scopes = model.applicability.get("scope_ids", []) if isinstance(model.applicability, Mapping) else []
            deployed = any(item.scope_id == scope_id for item in model.deployments)
            roles = any(item.scope_id == scope_id for item in model.comparison_roles)
            if not scopes or scope_id in scopes or deployed or roles:
                result.append(model)
        return result

    def get_model(self, project_id: str, model_id: str, *, scope_id: str | None = None) -> ModelDetail:
        model = self._model(project_id, model_id)
        active_scope = scope_id
        if active_scope is None:
            current = [item.scope_id for item in model.deployments if item.is_current]
            active_scope = current[0] if current else None
        return ModelDetail(model=model, evaluations=list(self.repository.list_evaluations(project_id, model_id)), registration_ref=str(model.source.get("registration_id", "")), approval_refs=[], active_scope_id=active_scope)

    def list_model_artifacts(self, project_id: str, model_id: str) -> list[Artifact]:
        model = self._model(project_id, model_id)
        port = self._require(self.artifacts, "artifact service is not configured")
        values: list[object]
        listing = getattr(port, "list", None)
        if callable(listing):
            values = list(listing(project_id=project_id, model_id=model.id, artifact_id=model.artifact_id))
        else:
            one = port.get(project_id, model.artifact_id)
            values = [] if one is None else [one]
        result = []
        for value in values:
            if isinstance(value, Artifact):
                result.append(value)
                continue
            data = value.model_dump(mode="json") if hasattr(value, "model_dump") else dict(value) if isinstance(value, Mapping) else {"id": str(getattr(value, "id", model.artifact_id))}
            data.setdefault("id", model.artifact_id)
            data.setdefault("project_id", project_id)
            data.setdefault("sha256", model.sha256)
            result.append(Artifact.model_validate(data))
        if not result and listing is None:
            result.append(Artifact(id=model.artifact_id, project_id=project_id, sha256=model.sha256))
        return result

    def prediction_snapshot(self, project_id: str, model_id: str, scope_id: str):
        """Return the exact field identity consumed by prediction adapters."""
        model = self._model(project_id, model_id)
        if model.lifecycle_status != "APPROVED":
            raise ModelCenterConflictError("只有已审批模型可以生成预测快照")
        scopes = model.applicability.get("scope_ids", []) if isinstance(model.applicability, Mapping) else []
        if scopes and scope_id not in scopes:
            raise ModelCenterConflictError("模型不适用当前业务 scope")
        from chrom_platform.modules.prediction.schemas import ModelSnapshot as PredictionModelSnapshot
        return PredictionModelSnapshot(id=model.id, version=model.version, business_scope_id=scope_id, artifact_sha256=model.sha256, input_schema_version=model.input_schema_version, output_schema_version=model.output_schema_version)

    snapshot_for_prediction = prediction_snapshot

    def list_protocols(self, task_type: str | None = None):
        port = self._require(self.evaluation_protocols, "evaluation protocol registry is not configured")
        return list(port.list(task_type=task_type))

    def create_evaluation(self, request: EvaluationCreate, *, actor: ActorContext | str | None = None, interaction: str = "human", idempotency_key: str | None = None, execute: bool = True) -> ModelEvaluation:
        _permission(actor, "model:evaluate", interaction=interaction)
        model = self._model(request.project_id, request.model_id)
        protocols = self._require(self.evaluation_protocols, "evaluation protocol registry is not configured")
        protocol = protocols.get(request.protocol_id, request.protocol_version)
        if protocol is None or protocol.status != "APPROVED" or protocol.task_type != model.task_type:
            raise ModelCenterValidationError("评测协议不存在、未审核或任务不匹配")
        request_hash = config_hash(request.model_dump(mode="json"))
        idem_scope = _idempotency_scope(request.project_id, actor, "POST", "/model-evaluations", idempotency_key) if idempotency_key else None
        if idem_scope:
            replay = self.repository.idempotent_get(idem_scope, request_hash)
            if replay is not None: return _mark_replayed(replay)
        protocol = protocol.model_copy(deep=True)
        scope = (protocol.scope.get("scope_id") if isinstance(protocol.scope, dict) else None) or (protocol.scope.get("id") if isinstance(protocol.scope, dict) else None) or "evaluation"
        data = DataSnapshot(data_version_id=protocol.data_version_id, split_manifest_sha256=protocol.split_manifest_sha256, snapshot_hash=_sha(protocol.model_dump(mode="json")))
        snapshot = ModelSnapshot(id=model.id, version=model.version, business_scope_id=str(scope), artifact_sha256=model.sha256, input_schema_version=model.input_schema_version, output_schema_version=model.output_schema_version)
        now = now_utc(); value = ModelEvaluation(id=new_id("eval"), project_id=request.project_id, status="QUEUED", model_snapshot=snapshot, protocol_snapshot=protocol, data_snapshot=data, created_at=now, updated_at=now)
        if not execute:
            job = self._submit_job(project_id=request.project_id, owner_id=_actor_id(actor), kind="model_evaluation", resource_ref={"type": "model_evaluation", "id": value.id})
            value = value.model_copy(update={"job_id": str(job["job_id"]), "attempt": int(job["attempt"]), "dispatch_id": str(job["dispatch_id"])})
            self.repository.save_evaluation(value)
            if idem_scope: self.repository.idempotent_put(idem_scope, request_hash, value)
            return value
        runner = self._require(self.evaluation_runner, "evaluation worker is not configured")
        try:
            result = runner.run(model=model, protocol=protocol, data_snapshot=data)
        except Exception:
            value = value.model_copy(update={"status": "FAILED", "pass_status": "FAILED", "error": {"code": "EVALUATION_FAILED", "message": "模型评测执行失败"}, "updated_at": now_utc()})
            self.repository.save_evaluation(value)
            if idem_scope: self.repository.idempotent_put(idem_scope, request_hash, value)
            return value
        if not isinstance(result, Mapping):
            result = {}
        pass_status = str(result.get("pass_status", "PENDING")); status = str(result.get("status", "SUCCEEDED" if pass_status == "PASSED" else "FAILED"))
        value = value.model_copy(update={"status": status if status in {"SUCCEEDED", "FAILED"} else "SUCCEEDED", "metrics": dict(result.get("metrics", {})), "pass_status": pass_status if pass_status in {"PASSED", "FAILED", "PENDING"} else "FAILED", "report_artifact_id": result.get("report_artifact_id"), "error": result.get("error"), "updated_at": now_utc()})
        self.repository.save_evaluation(value)
        if idem_scope: self.repository.idempotent_put(idem_scope, request_hash, value)
        return value

    def get_evaluation(self, project_id: str, evaluation_id: str) -> ModelEvaluation: return self._evaluation(project_id, evaluation_id)

    def queue_evaluation(self, request: EvaluationCreate, *, actor: ActorContext | str | None = None, idempotency_key: str) -> EvaluationAccepted:
        _permission(actor, "model:evaluate", interaction="human")
        request_hash = config_hash(request.model_dump(mode="json"))
        scope = _idempotency_scope(request.project_id, actor, "POST", "/model-evaluations", idempotency_key) + ":accepted"
        replay = self.repository.idempotent_get(scope, request_hash)
        if replay is not None:
            return _mark_replayed(replay if isinstance(replay, EvaluationAccepted) else EvaluationAccepted.model_validate(replay))
        value = self.create_evaluation(request, actor=actor, idempotency_key=idempotency_key, execute=False)
        if not value.job_id:
            raise ModelCenterUnavailableError("evaluation did not create a platform Job")
        accepted = EvaluationAccepted(id=value.id, job_id=value.job_id, status="QUEUED", resource_ref=ResourceRef(type="model_evaluation", id=value.id))
        self.repository.idempotent_put(scope, request_hash, accepted)
        return accepted

    def compare(self, request: ComparisonCreate, *, actor: ActorContext | str | None = None, interaction: str = "human") -> ModelComparison:
        _permission(actor, "model:evaluate", interaction=interaction)
        models = [self._model(request.project_id, mid) for mid in request.model_ids]
        evaluations = []
        for model in models:
            matches = [e for e in self.repository.list_evaluations(request.project_id, model.id) if e.protocol_snapshot.id == request.protocol_id and e.protocol_snapshot.version == request.protocol_version and e.status == "SUCCEEDED" and e.pass_status in {"PASSED", "FAILED"}]
            if not matches: raise ModelCenterConflictError("两个模型必须已有同协议、同数据快照的完成评测")
            evaluations.append(matches[0])
        if any(e.model_snapshot.business_scope_id != request.scope_id for e in evaluations):
            raise ModelCenterConflictError("评测 scope 与比较请求 scope 不一致")
        if any(e.data_snapshot.snapshot_hash != evaluations[0].data_snapshot.snapshot_hash for e in evaluations[1:]): raise ModelCenterConflictError("评测数据快照不一致")
        value = ModelComparison(id=new_id("cmp"), project_id=request.project_id, model_ids=request.model_ids, protocol_snapshot=evaluations[0].protocol_snapshot, data_snapshot=evaluations[0].data_snapshot, scope_id=request.scope_id, metrics={mid: ev.metrics for mid, ev in zip(request.model_ids, evaluations)}, created_at=now_utc())
        saver = getattr(self.repository, "save_comparison", None)
        if callable(saver): saver(value)
        return value

    def create_approval_request(self, project_id: str, model_id: str, request: ApprovalRequestCreate, *, actor: ActorContext | str | None = None, interaction: str = "human", idempotency_key: str | None = None) -> ApprovalRequest:
        _permission(actor, "model:approve", interaction=interaction)
        evaluation = self._evaluation(project_id, request.evaluation_id)
        model = self._model(project_id, model_id)
        if evaluation.model_snapshot.id != model.id or evaluation.pass_status != "PASSED": raise ModelCenterValidationError("审批必须绑定通过的同一模型评测")
        request_hash = config_hash({"model_id": model_id, **request.model_dump(mode="json")})
        idem_scope = _idempotency_scope(project_id, actor, "POST", "/models/{model_id}/approval-requests", idempotency_key) if idempotency_key else None
        if idempotency_key:
            replay = self.repository.idempotent_get(idem_scope, request_hash)
            if replay is not None: return _mark_replayed(replay)
        now = now_utc(); value = ApprovalRequest(id=new_id("approval"), project_id=project_id, model_id=model.id, evaluation_snapshot=evaluation, scope_ids=request.scope_ids, status="PENDING", requested_by=_actor_id(actor), reason=request.reason, created_at=now)
        self.repository.save_approval(value)
        if idempotency_key: self.repository.idempotent_put(idem_scope, request_hash, value)
        return value

    def get_approval_request(self, project_id: str, approval_id: str) -> ApprovalRequest:
        value = self.repository.get_approval(project_id, approval_id)
        if value is None: raise ModelCenterNotFoundError("审批申请不存在")
        return value

    def decide_approval(self, project_id: str, approval_id: str, decision: ApprovalDecision, *, approve: bool, actor: ActorContext | str | None = None, interaction: str = "human", idempotency_key: str | None = None, if_match: str | None = None) -> ApprovalRequest:
        _permission(actor, "model:approve", interaction=interaction, dangerous=True)
        current = self.get_approval_request(project_id, approval_id)
        request_hash = config_hash({"approval_id": approval_id, "decision": decision.model_dump(mode="json"), "approve": approve})
        idem_scope = _idempotency_scope(project_id, actor, "POST", f"/approval-requests/{{approval_id}}/{'approve' if approve else 'reject'}", idempotency_key) if idempotency_key else None
        if idempotency_key:
            replay = self.repository.idempotent_get(idem_scope, request_hash)
            if replay is not None: return _mark_replayed(replay)
        if if_match is not None and current.etag != if_match:
            raise ModelCenterPreconditionError("approval request ETag is stale")
        if current.revision != decision.expected_revision: raise ModelCenterConflictError("审批 revision 已过期")
        if current.status != "PENDING": return current
        model = self._model(project_id, current.model_id)
        status = "APPROVED" if approve else "REJECTED"; lifecycle = "APPROVED" if approve else "REJECTED"; now = now_utc()
        value = current.model_copy(update={"status": status, "decided_by": _actor_id(actor), "reason": decision.reason, "revision": current.revision + 1, "etag": f'"{current.revision + 1}"', "decided_at": now})
        with self.repository.transaction():
            self.repository.save_approval(value); self.repository.save_model(model.model_copy(update={"lifecycle_status": lifecycle, "revision": model.revision + 1, "etag": f'"{model.revision + 1}"', "updated_at": now}))
            self._audit(project_id=project_id, actor=actor, interaction=interaction, action="model.approval.approve" if approve else "model.approval.reject", resource={"type": "approval_request", "id": approval_id}, result="SUCCEEDED", before={"status": "PENDING"}, after={"status": status})
        if idempotency_key: self.repository.idempotent_put(idem_scope, request_hash, value)
        return value

    def create_deployment_draft(self, request: DeploymentDraftCreate, *, actor: ActorContext | str | None = None, interaction: str = "human", idempotency_key: str | None = None) -> DeploymentDraft:
        _permission(actor, "model:deploy", interaction=interaction, dangerous=True)
        model = self._model(request.project_id, request.target_model_id)
        operation = "ROLLBACK" if request.rollback_model_id else "PUBLISH"
        now = now_utc(); payload = request.model_dump(mode="json"); request_hash = config_hash(payload)
        idem_scope = _idempotency_scope(request.project_id, actor, "POST", "/model-deployment-drafts", idempotency_key) if idempotency_key else None
        if idempotency_key:
            replay = self.repository.idempotent_get(idem_scope, request_hash)
            if replay is not None: return _mark_replayed(replay)
        value = DeploymentDraft(id=new_id("draft"), project_id=request.project_id, scope_id=request.scope_id, target_model_id=request.target_model_id, stage=request.stage, expected_pointer_revision=request.expected_pointer_revision, rollback_model_id=request.rollback_model_id, config_hash=request_hash, status="DRAFT", operation=operation, created_at=now, updated_at=now)
        self.repository.save_draft(value)
        if idempotency_key: self.repository.idempotent_put(idem_scope, request_hash, value)
        return value

    def get_draft(self, project_id: str, draft_id: str) -> DeploymentDraft:
        value = self.repository.get_draft(project_id, draft_id)
        if value is None: raise ModelCenterNotFoundError("部署草稿不存在")
        return value

    def validate_deployment(self, project_id: str, draft_id: str) -> DeploymentValidation:
        draft = self.get_draft(project_id, draft_id); model = self._model(project_id, draft.target_model_id); errors: list[dict[str, str]] = []
        pointer = self.repository.get_pointer(project_id, draft.scope_id, draft.stage)
        if draft.expected_pointer_revision != pointer.revision: errors.append({"path": "expected_pointer_revision", "code": "POINTER_REVISION_STALE", "message": "生产指针 revision 已变化"})
        if model.lifecycle_status != "APPROVED": errors.append({"path": "target_model_id", "code": "MODEL_NOT_APPROVED", "message": "目标模型未审批"})
        scopes = model.applicability.get("scope_ids", []) if isinstance(model.applicability, dict) else []
        if scopes and draft.scope_id not in scopes: errors.append({"path": "scope_id", "code": "SCOPE_NOT_APPLICABLE", "message": "模型不适用当前 scope"})
        if draft.operation == "ROLLBACK" and not draft.rollback_model_id: errors.append({"path": "rollback_model_id", "code": "ROLLBACK_TARGET_MISSING", "message": "回滚必须绑定历史模型"})
        next_revision = draft.revision + 1
        next_etag = f'"{next_revision}"'
        report = DeploymentValidation(draft_id=draft.id, revision=next_revision, etag=next_etag, valid=not errors, field_errors=errors)
        self.repository.save_draft(draft.model_copy(update={"status": "READY" if not errors else "INVALID", "revision": next_revision, "etag": next_etag, "validation_report": report, "updated_at": now_utc()})); return report

    def validate_deployment_request(self, project_id: str, draft_id: str, *, if_match: str, idempotency_key: str, actor: ActorContext | str | None = None) -> DeploymentDraft:
        _permission(actor, "model:deploy", interaction="human")
        draft = self.get_draft(project_id, draft_id)
        request_hash = config_hash({"draft_id": draft_id, "config_hash": draft.config_hash, "body": {}})
        scope = _idempotency_scope(project_id, actor, "POST", "/model-deployment-drafts/{draft_id}/validate", idempotency_key)
        replay = self.repository.idempotent_get(scope, request_hash)
        if replay is not None:
            return _mark_replayed(replay if isinstance(replay, DeploymentDraft) else DeploymentDraft.model_validate(replay))
        if draft.etag != if_match:
            raise ModelCenterPreconditionError("deployment draft ETag is stale")
        self.validate_deployment(project_id, draft_id)
        value = self.get_draft(project_id, draft_id)
        self.repository.idempotent_put(scope, request_hash, value)
        return value

    def submit_deployment(self, project_id: str, draft_id: str, *, confirmation_id: str, actor: ActorContext | str | None = None, interaction: str = "human", idempotency_key: str | None = None, if_match: str | None = None) -> DeploymentAccepted:
        _permission(actor, "model:deploy", interaction=interaction, dangerous=True)
        draft = self.get_draft(project_id, draft_id)
        req_hash = config_hash({"draft_id": draft.id, "config_hash": draft.config_hash, "confirmation_id": confirmation_id})
        idem_scope = _idempotency_scope(project_id, actor, "POST", "/model-deployment-drafts/{draft_id}/submit", idempotency_key) if idempotency_key else None
        if idempotency_key:
            replay = self.repository.idempotent_get(idem_scope, req_hash)
            if replay is not None: return _mark_replayed(replay)
        if if_match is not None and draft.etag != if_match: raise ModelCenterPreconditionError("deployment draft ETag is stale")
        if draft.status != "READY" or not draft.validation_report or not draft.validation_report.valid: raise ModelCenterConflictError("部署草稿必须先通过当前 revision 的验证")
        if self.jobs is None: raise ModelCenterUnavailableError("model deployment worker is not configured")
        self._consume(confirmation_id=confirmation_id, project_id=project_id, resource_ref={"type": "deployment_draft", "id": draft.id}, action="model_deployment.publish", revision=draft.revision, config_hash_value=draft.config_hash, actor=actor)
        deployment_id = new_id("deployment")
        # The stable job message only carries three transport fields.  The
        # business identity therefore has to be present in Job.resource_ref.
        # Create the module row first so the job can point at its own ID.
        deployment = Deployment(id=deployment_id, project_id=project_id, scope_id=draft.scope_id, stage=draft.stage, target_model_id=draft.target_model_id, operation=draft.operation, status="QUEUED", expected_pointer_revision=draft.expected_pointer_revision, draft_id=draft.id, created_at=now_utc())
        job = self._submit_job(project_id=project_id, owner_id=_actor_id(actor), kind="model_deployment", resource_ref={"type": "deployment", "id": deployment.id}); job_id = str(job["job_id"])
        dispatch_id = str(job["dispatch_id"])
        attempt = int(job["attempt"])
        deployment = deployment.model_copy(update={"attempt": attempt, "dispatch_id": dispatch_id})
        self.repository.save_deployment(deployment); self.repository.save_draft(draft.model_copy(update={"status": "SUBMITTED", "confirmation_id": confirmation_id, "updated_at": now_utc()}))
        self._audit(project_id=project_id, actor=actor, interaction=interaction, action="model_deployment.publish", resource={"type": "deployment", "id": deployment.id}, result="SUCCEEDED", after={"status": "QUEUED"}, confirmation_id=confirmation_id, config_hash_value=draft.config_hash)
        accepted = DeploymentAccepted(id=deployment.id, job_id=job_id, status="QUEUED", resource_ref={"type": "deployment", "id": deployment.id}, attempt=attempt, dispatch_id=dispatch_id)
        if idempotency_key: self.repository.idempotent_put(idem_scope, req_hash, accepted)
        return accepted

    def create_rollback_draft(self, project_id: str, deployment_id: str, request: RollbackDraftCreate, *, actor: ActorContext | str | None = None, interaction: str = "human", idempotency_key: str | None = None) -> DeploymentDraft:
        _permission(actor, "model:deploy", interaction=interaction, dangerous=True)
        request_hash = config_hash({"deployment_id": deployment_id, **request.model_dump(mode="json")})
        idem_scope = _idempotency_scope(project_id, actor, "POST", "/model-deployments/{deployment_id}/rollback-drafts", idempotency_key) if idempotency_key else None
        if idem_scope:
            replay = self.repository.idempotent_get(idem_scope, request_hash)
            if replay is not None: return _mark_replayed(replay)
        source = self.repository.get_deployment(project_id, deployment_id)
        if source is None or source.status != "SUCCEEDED": raise ModelCenterConflictError("无历史模型可回滚")
        pointer = self.repository.get_pointer(project_id, source.scope_id, source.stage)
        if request.target_historical_model_id == pointer.model_id: raise ModelCenterConflictError("目标已经是当前生产模型")
        history = [d for d in self.repository.list_deployments(project_id, source.scope_id) if d.status == "SUCCEEDED" and d.stage == source.stage and d.target_model_id == request.target_historical_model_id]
        if not history: raise ModelCenterConflictError("无历史模型可回滚")
        draft = self.create_deployment_draft(DeploymentDraftCreate(project_id=project_id, scope_id=source.scope_id, target_model_id=request.target_historical_model_id, stage=source.stage, expected_pointer_revision=request.expected_pointer_revision, rollback_model_id=request.target_historical_model_id), actor=actor, interaction=interaction)
        rollback_hash = config_hash({"draft": draft.config_hash, "source_deployment_id": deployment_id, "reason": request.reason})
        value = self.repository.save_draft(draft.model_copy(update={"source_deployment_id": deployment_id, "reason": request.reason, "config_hash": rollback_hash, "updated_at": now_utc()}))
        self._audit(project_id=project_id, actor=actor, interaction=interaction, action="model_deployment.rollback_draft", resource={"type": "deployment_draft", "id": value.id}, result="SUCCEEDED", after={"operation": "ROLLBACK", "source_deployment_id": deployment_id, "target_model_id": value.target_model_id}, config_hash_value=value.config_hash)
        if idem_scope: self.repository.idempotent_put(idem_scope, request_hash, value)
        return value

    def get_pointer(self, project_id: str, scope_id: str, stage: str = "PRODUCTION") -> ProductionPointer:
        return self.repository.get_pointer(project_id, scope_id, stage)

    def list_deployments(self, project_id: str, scope_id: str | None = None): return list(self.repository.list_deployments(project_id, scope_id))

    def get_deployment(self, project_id: str, deployment_id: str) -> Deployment:
        value = self.repository.get_deployment(project_id, deployment_id)
        if value is None:
            raise ModelCenterNotFoundError("部署记录不存在")
        return value


__all__ = ["ModelCenterService", "config_hash"]
