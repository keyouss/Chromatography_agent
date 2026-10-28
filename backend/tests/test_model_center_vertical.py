from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from chrom_platform.core.dto import ActorContext
from chrom_platform.modules.model_center.errors import ModelCenterConfirmationError, ModelCenterConflictError, ModelCenterPermissionError, ModelCenterValidationError
from chrom_platform.modules.model_center.persistence import (
    InMemoryArtifactPort, InMemoryAuditPort, InMemoryConfirmationPort,
    InMemoryJobPort, InMemoryModelCenterRepository,
)
from chrom_platform.modules.model_center.ports import (
    StaticDeploymentLoader, StaticEvaluationProtocolRegistry,
    StaticEvaluationRunner, StaticModelAssetVerifier,
)
from chrom_platform.modules.model_center.schemas import (
    ApprovalDecision, ApprovalRequestCreate, DeploymentDraftCreate,
    EvaluationCreate, EvaluationProtocol, ModelRegistrationCreate,
    RollbackDraftCreate,
)
from chrom_platform.modules.model_center.schemas import now_utc
from chrom_platform.modules.model_center.service import ModelCenterService
from chrom_platform.modules.model_center.worker import DeploymentWorker


def actor(*permissions: str, actor_id: str = "human"):
    return ActorContext(actor_id=actor_id, project_ids=["p1"], auth_method="deployment_default", permissions=list(permissions), policy_version="test")


def checks():
    return [{"step": step, "status": "PASSED"} for step in ("manifest/hash", "allowlisted loader/format", "dependency/runtime", "schema", "load", "golden", "finite values")]


def build(*, loader=None):
    repo = InMemoryModelCenterRepository(); confirmation = InMemoryConfirmationPort(); audit = InMemoryAuditPort(); jobs = InMemoryJobPort()
    protocols = StaticEvaluationProtocolRegistry([EvaluationProtocol(id="ep", version="1", task_type="sfc", data_version_id="dv", split_manifest_sha256="a" * 64, metrics=[{"key": "mae"}], independence_description="held out")])
    artifacts = InMemoryArtifactPort([{"id": artifact_id, "project_id": "p1", "kind": "model_package", "status": "AVAILABLE", "scan_status": "PASSED"} for artifact_id in ("art-1", "a")])
    service = ModelCenterService(repo, asset_verifier=StaticModelAssetVerifier({"valid": True, "loader_id": "safe", "artifact_sha256": "b" * 64, "manifest": {"format": "safetensors", "loader_id": "safe", "version": "1", "input_schema_version": "in-1", "output_schema_version": "out-1", "scope_ids": ["scope"]}, "checks": checks()}), artifacts=artifacts, evaluation_protocols=protocols, evaluation_runner=StaticEvaluationRunner(), loader=loader, confirmation=confirmation, audit=audit, jobs=jobs)
    return service, repo, confirmation, audit, jobs


def candidate(service, confirmation):
    registration = service.create_registration(ModelRegistrationCreate(project_id="p1", source={"kind": "external_package", "artifact_id": "art-1"}, name="candidate", task_type="sfc"))
    report = service.validate_registration("p1", registration.id)
    confirmation.add("reg-confirm", project_id="p1", resource_ref={"type": "model_registration", "id": registration.id}, action="model_registration.register", revision=report.revision, config_hash=registration.config_hash, actor_id="human")
    model = service.register("p1", registration.id, confirmation_id="reg-confirm", actor=actor("model:register"))
    return model


def test_dto_is_strict_and_asset_must_be_verified():
    with pytest.raises(ValidationError):
        ModelRegistrationCreate(project_id="p1", source={"kind": "external_package", "artifact_id": "a"}, name="x", task_type="sfc", command="python")
    service, _, _, _, _ = build()
    registration = service.create_registration(ModelRegistrationCreate(project_id="p1", source={"kind": "external_package", "artifact_id": "a"}, name="x", task_type="sfc"))
    service.asset_verifier = StaticModelAssetVerifier({"valid": False, "checks": checks()})
    report = service.validate_registration("p1", registration.id)
    assert not report.valid


def test_evaluation_snapshot_and_approval_are_human_bound():
    service, _, confirmation, _, _ = build()
    model = candidate(service, confirmation)
    evaluation = service.create_evaluation(EvaluationCreate(project_id="p1", model_id=model.id, protocol_id="ep", protocol_version="1"), actor=actor("model:evaluate"))
    request = service.create_approval_request("p1", model.id, ApprovalRequestCreate(evaluation_id=evaluation.id, scope_ids=["scope"], reason="review"), actor=actor("model:approve"))
    with pytest.raises(ModelCenterPermissionError):
        service.decide_approval("p1", request.id, ApprovalDecision(reason="bot", expected_revision=1), approve=True, actor=actor("model:approve"), interaction="agent_tool")
    approved = service.decide_approval("p1", request.id, ApprovalDecision(reason="human", expected_revision=1), approve=True, actor=actor("model:approve"))
    assert approved.status == "APPROVED"
    assert evaluation.model_snapshot.id == model.id
    assert evaluation.data_snapshot.data_version_id == "dv"


def test_publish_worker_keeps_old_pointer_on_failure_and_cas_conflict():
    service, repo, confirmation, _, _ = build(loader=StaticDeploymentLoader())
    model = candidate(service, confirmation)
    evaluation = service.create_evaluation(EvaluationCreate(project_id="p1", model_id=model.id, protocol_id="ep", protocol_version="1"), actor=actor("model:evaluate"))
    approval = service.create_approval_request("p1", model.id, ApprovalRequestCreate(evaluation_id=evaluation.id, scope_ids=["scope"], reason="review"), actor=actor("model:approve"))
    service.decide_approval("p1", approval.id, ApprovalDecision(reason="ok", expected_revision=1), approve=True, actor=actor("model:approve"))
    draft = service.create_deployment_draft(DeploymentDraftCreate(project_id="p1", scope_id="scope", target_model_id=model.id, expected_pointer_revision=0), actor=actor("model:deploy"))
    report = service.validate_deployment("p1", draft.id)
    assert report.valid
    confirmation.add("deploy-confirm", project_id="p1", resource_ref={"type": "deployment_draft", "id": draft.id}, action="model_deployment.publish", revision=report.revision, config_hash=draft.config_hash, actor_id="human")
    accepted = service.submit_deployment("p1", draft.id, confirmation_id="deploy-confirm", actor=actor("model:deploy"))
    deployment = DeploymentWorker(repo, loader=StaticDeploymentLoader()).execute("p1", accepted.id)
    assert deployment.status == "SUCCEEDED"
    assert repo.get_pointer("p1", "scope", "PRODUCTION").model_id == model.id
    old_revision = repo.get_pointer("p1", "scope", "PRODUCTION").revision
    failed = DeploymentWorker(repo, loader=StaticDeploymentLoader(fail=True)).execute("p1", accepted.id)
    assert failed.status == "SUCCEEDED" and repo.get_pointer("p1", "scope", "PRODUCTION").revision == old_revision
    with pytest.raises(ModelCenterConflictError):
        repo.cas_pointer(repo.get_pointer("p1", "scope", "PRODUCTION"), expected_revision=0, model_id=model.id)


def test_artifact_reference_is_identifier_only_and_sha_manifest_must_match():
    with pytest.raises(ValidationError):
        ModelRegistrationCreate(project_id="p1", source={"kind": "external_package", "artifact_id": "C:\\models\\x"}, name="x", task_type="sfc")
    service, _, _, _, _ = build()
    registration = service.create_registration(ModelRegistrationCreate(project_id="p1", source={"kind": "external_package", "artifact_id": "a"}, name="x", task_type="sfc"))
    bad = {"valid": True, "loader_id": "safe", "artifact_sha256": "b" * 64, "manifest": {"format": "safetensors", "loader_id": "safe", "artifact_sha256": "c" * 64, "version": "1", "input_schema_version": "in-1", "output_schema_version": "out-1"}, "checks": checks()}
    service.asset_verifier = StaticModelAssetVerifier(bad)
    report = service.validate_registration("p1", registration.id)
    assert not report.valid
    assert any(item["code"] == "SHA256_MISMATCH" for item in report.field_errors)


def test_candidate_creation_requires_every_worker_check_and_never_publishes():
    service, repo, _, _, _ = build()
    experiment = {"id": "exp-1", "name": "trained", "spec": {"task_type": "sfc"}}
    result = {"artifact_id": "artifact-1", "manifest": {"version": "1", "input_schema_version": "in-1", "output_schema_version": "out-1", "scope_ids": ["scope"]}}
    service.asset_verifier = StaticModelAssetVerifier({"valid": True, "loader_id": "safe", "artifact_sha256": "b" * 64, "manifest": {"format": "safetensors", "loader_id": "safe", "version": "1", "input_schema_version": "in-1", "output_schema_version": "out-1", "scope_ids": ["scope"]}, "checks": checks()[:-1]})
    with pytest.raises(ModelCenterValidationError):
        service.register_candidate(project_id="p1", experiment=experiment, result=result)
    service.asset_verifier = StaticModelAssetVerifier({"valid": True, "loader_id": "safe", "artifact_sha256": "b" * 64, "manifest": {"format": "safetensors", "loader_id": "safe", "version": "1", "input_schema_version": "in-1", "output_schema_version": "out-1", "scope_ids": ["scope"]}, "checks": checks()})
    candidate = service.register_candidate(project_id="p1", experiment=experiment, result=result)
    model = next(iter(repo.models.values()))
    assert candidate.id == model.id
    assert candidate.status == "CANDIDATE" and not candidate.published
    assert model.lifecycle_status == "CANDIDATE"
    assert not model.deployments


def test_confirmation_expiry_and_single_use_are_enforced():
    confirmation = InMemoryConfirmationPort()
    confirmation.add("expired", project_id="p1", resource_ref={"type": "model", "id": "m1"}, action="model.publish", revision=1, config_hash="a" * 64, actor_id="human", expires_at=now_utc() - timedelta(seconds=1))
    with pytest.raises(ValueError):
        confirmation.consume(confirmation_id="expired", project_id="p1", resource_ref={"type": "model", "id": "m1"}, action="model.publish", revision=1, config_hash="a" * 64, result_ref={"type": "model", "id": "m1"}, actor_id="human")
    confirmation.add("once", project_id="p1", resource_ref={"type": "model", "id": "m1"}, action="model.publish", revision=1, config_hash="a" * 64, actor_id="human")
    kwargs = {"confirmation_id": "once", "project_id": "p1", "resource_ref": {"type": "model", "id": "m1"}, "action": "model.publish", "revision": 1, "config_hash": "a" * 64, "result_ref": {"type": "model", "id": "m1"}, "actor_id": "human"}
    confirmation.consume(**kwargs)
    with pytest.raises(ValueError):
        confirmation.consume(**kwargs)


def test_evaluation_captures_immutable_protocol_snapshot_and_api_queue_path():
    service, repo, _, _, jobs = build()
    # Build one candidate with the normal confirmation helper, then evaluate it.
    registration = service.create_registration(ModelRegistrationCreate(project_id="p1", source={"kind": "external_package", "artifact_id": "art-1"}, name="candidate", task_type="sfc"))
    report = service.validate_registration("p1", registration.id)
    confirmation = service.confirmation
    confirmation.add("reg", project_id="p1", resource_ref={"type": "model_registration", "id": registration.id}, action="model_registration.register", revision=report.revision, config_hash=registration.config_hash, actor_id="human")
    model = service.register("p1", registration.id, confirmation_id="reg", actor=actor("model:register"))
    evaluation = service.create_evaluation(EvaluationCreate(project_id="p1", model_id=model.id, protocol_id="ep", protocol_version="1"), actor=actor("model:evaluate"))
    original = evaluation.protocol_snapshot.scope
    protocols = service.evaluation_protocols
    protocols.protocols[("ep", "1")] = protocols.protocols[("ep", "1")].model_copy(update={"scope": {"scope_id": "mutated"}})
    assert evaluation.protocol_snapshot.scope == original
    queued = service.create_evaluation(EvaluationCreate(project_id="p1", model_id=model.id, protocol_id="ep", protocol_version="1"), actor=actor("model:evaluate"), execute=False)
    assert queued.status == "QUEUED" and queued.job_id and jobs.jobs[-1]["kind"] == "model_evaluation"


def test_worker_rejects_stale_attempt_or_dispatch_without_touching_pointer():
    service, repo, confirmation, _, _ = build(loader=StaticDeploymentLoader())
    model = candidate(service, confirmation)
    model = repo.save_model(model.model_copy(update={"lifecycle_status": "APPROVED"}))
    draft = service.create_deployment_draft(DeploymentDraftCreate(project_id="p1", scope_id="scope", target_model_id=model.id, expected_pointer_revision=0), actor=actor("model:deploy"))
    report = service.validate_deployment("p1", draft.id)
    confirmation.add("deploy", project_id="p1", resource_ref={"type": "deployment_draft", "id": draft.id}, action="model_deployment.publish", revision=report.revision, config_hash=draft.config_hash, actor_id="human")
    accepted = service.submit_deployment("p1", draft.id, confirmation_id="deploy", actor=actor("model:deploy"))
    worker = DeploymentWorker(repo, loader=StaticDeploymentLoader())
    with pytest.raises(ModelCenterConflictError):
        worker.execute("p1", accepted.id, attempt=accepted.attempt + 1)
    assert repo.get_pointer("p1", "scope", "PRODUCTION").revision == 0
