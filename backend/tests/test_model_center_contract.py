from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from chrom_platform.core.dto import ActorContext
from chrom_platform.core.errors import APIError
from chrom_platform.modules.model_center.errors import ModelCenterConfirmationError, ModelCenterConflictError, ModelCenterPermissionError
from chrom_platform.modules.model_center.persistence import (
    InMemoryArtifactPort,
    InMemoryAuditPort,
    InMemoryConfirmationPort,
    InMemoryJobPort,
    InMemoryModelCenterRepository,
)
from chrom_platform.modules.model_center.ports import (
    StaticDeploymentLoader,
    StaticEvaluationProtocolRegistry,
    StaticEvaluationRunner,
    StaticModelAssetVerifier,
)
from chrom_platform.modules.model_center.router import create_router
from chrom_platform.modules.model_center.schemas import (
    ApprovalDecision,
    ApprovalRequestCreate,
    ComparisonCreate,
    DeploymentDraftCreate,
    EvaluationCreate,
    EvaluationProtocol,
    Model,
    ModelRegistrationCreate,
    RollbackDraftCreate,
    now_utc,
)
from chrom_platform.modules.model_center.service import ModelCenterService
from chrom_platform.modules.model_center.worker import DeploymentWorker


def _actor(*permissions: str) -> ActorContext:
    return ActorContext(actor_id="human", project_ids=["p1"], auth_method="deployment_default", permissions=list(permissions), policy_version="test")


def _checks() -> list[dict[str, str]]:
    return [{"step": step, "status": "PASSED"} for step in ("manifest/hash", "allowlisted loader/format", "dependency/runtime", "schema", "load", "golden", "finite values")]


def _service(*, artifacts=None, loader=None):
    repo = InMemoryModelCenterRepository()
    if artifacts is None:
        artifacts = InMemoryArtifactPort([{"id": "art-1", "project_id": "p1", "kind": "model_package", "status": "AVAILABLE", "scan_status": "PASSED"}])
    protocols = StaticEvaluationProtocolRegistry([
        EvaluationProtocol(id="ep", version="1", task_type="sfc", data_version_id="dv", split_manifest_sha256="a" * 64, metrics=[{"key": "mae"}], independence_description="held out")
    ])
    return ModelCenterService(
        repo,
        asset_verifier=StaticModelAssetVerifier({"valid": True, "loader_id": "safe", "artifact_sha256": "b" * 64, "manifest": {"format": "safetensors", "loader_id": "safe", "version": "1", "input_schema_version": "in-1", "output_schema_version": "out-1", "scope_ids": ["scope"]}, "checks": _checks()}),
        artifacts=artifacts,
        evaluation_protocols=protocols,
        evaluation_runner=StaticEvaluationRunner(),
        loader=loader,
        confirmation=InMemoryConfirmationPort(),
        audit=InMemoryAuditPort(),
        jobs=InMemoryJobPort(),
    )


def _client(service: ModelCenterService, permissions=("*",)) -> TestClient:
    app = FastAPI()
    app.state.actor_context = _actor(*permissions)

    @app.exception_handler(APIError)
    async def _error(_: Request, exc: APIError):
        return JSONResponse(status_code=exc.status_code, content={"error": exc.payload.model_dump(mode="json")})

    app.include_router(create_router(service=service), prefix="/api/v1")
    return TestClient(app)


def _candidate(service: ModelCenterService):
    registration = service.create_registration(ModelRegistrationCreate(project_id="p1", source={"kind": "external_package", "artifact_id": "art-1"}, name="candidate", task_type="sfc"))
    report = service.validate_registration("p1", registration.id)
    service.confirmation.add("reg", project_id="p1", resource_ref={"type": "model_registration", "id": registration.id}, action="model_registration.register", revision=report.revision, config_hash=registration.config_hash, actor_id="human")
    return service.register("p1", registration.id, confirmation_id="reg", actor=_actor("model:register"))


def test_model_center_exposes_all_22_typed_operations_and_flat_pages():
    app = FastAPI()
    app.include_router(create_router(), prefix="/api/v1")
    document = app.openapi()
    expected = {
        "/api/v1/models", "/api/v1/models/registrations", "/api/v1/models/registrations/{registration_id}",
        "/api/v1/models/registrations/{registration_id}/validate", "/api/v1/models/registrations/{registration_id}/register",
        "/api/v1/models/{model_id}", "/api/v1/models/{model_id}/artifacts", "/api/v1/evaluation-protocols",
        "/api/v1/model-evaluations", "/api/v1/model-evaluations/{evaluation_id}", "/api/v1/model-comparisons",
        "/api/v1/models/{model_id}/approval-requests", "/api/v1/approval-requests/{approval_id}",
        "/api/v1/approval-requests/{approval_id}/approve", "/api/v1/approval-requests/{approval_id}/reject",
        "/api/v1/model-deployment-drafts", "/api/v1/model-deployment-drafts/{draft_id}",
        "/api/v1/model-deployment-drafts/{draft_id}/validate", "/api/v1/model-deployment-drafts/{draft_id}/submit",
        "/api/v1/model-deployments", "/api/v1/model-deployments/{deployment_id}",
        "/api/v1/model-deployments/{deployment_id}/rollback-drafts",
    }
    assert expected <= set(document["paths"])
    for path in expected:
        for method, operation in document["paths"][path].items():
            if method not in {"get", "post"}:
                continue
            success = next(code for code in ("200", "201", "202") if code in operation["responses"])
            content = operation["responses"][success].get("content", {}).get("application/json", {})
            assert "$ref" in content.get("schema", {}), (method, path)
    for path in ("/api/v1/models", "/api/v1/evaluation-protocols", "/api/v1/model-deployments", "/api/v1/models/{model_id}/artifacts"):
        schema = document["paths"][path]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        assert "ListEnvelope_" in schema["$ref"]
    serialized = str(document["paths"])
    assert "SuccessEnvelope[object]" not in serialized and "SuccessEnvelope[dict]" not in serialized


def test_registration_and_evaluation_accepted_have_location_and_strict_empty_body():
    service = _service()
    client = _client(service)
    created = client.post("/api/v1/models/registrations", headers={"Idempotency-Key": "create"}, json={"project_id": "p1", "source": {"kind": "external_package", "artifact_id": "art-1"}, "name": "candidate", "task_type": "sfc"})
    assert created.status_code == 201
    registration_id = created.json()["data"]["id"]
    accepted = client.post(f"/api/v1/models/registrations/{registration_id}/validate?project_id=p1", headers={"If-Match": '"1"', "Idempotency-Key": "validate"}, json={})
    assert accepted.status_code == 202 and accepted.headers["location"].endswith(registration_id)
    assert set(accepted.json()["data"]) == {"id", "job_id", "status", "resource_ref"}
    replay = client.post(f"/api/v1/models/registrations/{registration_id}/validate?project_id=p1", headers={"If-Match": '"1"', "Idempotency-Key": "validate"}, json={})
    assert replay.status_code == 202 and replay.headers["idempotency-replayed"] == "true"
    assert client.post(f"/api/v1/models/registrations/{registration_id}/validate?project_id=p1", headers={"If-Match": '"1"', "Idempotency-Key": "validate-2"}, json={"extra": True}).status_code == 422

    model = _candidate(service)
    evaluation = client.post("/api/v1/model-evaluations", headers={"Idempotency-Key": "eval"}, json={"project_id": "p1", "model_id": model.id, "protocol_id": "ep", "protocol_version": "1"})
    assert evaluation.status_code == 202 and evaluation.headers["location"].endswith(evaluation.json()["data"]["id"])
    assert set(evaluation.json()["data"]) == {"id", "job_id", "status", "resource_ref"}
    evaluation_replay = client.post("/api/v1/model-evaluations", headers={"Idempotency-Key": "eval"}, json={"project_id": "p1", "model_id": model.id, "protocol_id": "ep", "protocol_version": "1"})
    assert evaluation_replay.status_code == 202 and evaluation_replay.headers["idempotency-replayed"] == "true"
    evaluation_id = service.create_evaluation(EvaluationCreate(project_id="p1", model_id=model.id, protocol_id="ep", protocol_version="1"), actor=_actor("model:evaluate"), execute=True).id
    approval = service.create_approval_request("p1", model.id, ApprovalRequestCreate(evaluation_id=evaluation_id, scope_ids=["scope"], reason="route"), actor=_actor("model:approve"))
    service.decide_approval("p1", approval.id, ApprovalDecision(reason="human", expected_revision=1), approve=True, actor=_actor("model:approve"), if_match='"1"')
    detail = client.get(f"/api/v1/models/{model.id}?project_id=p1")
    assert detail.status_code == 200 and detail.headers["etag"] == '"2"'
    draft = client.post("/api/v1/model-deployment-drafts", headers={"Idempotency-Key": "draft"}, json={"project_id": "p1", "scope_id": "scope", "target_model_id": model.id, "expected_pointer_revision": 0})
    draft_id = draft.json()["data"]["id"]
    ready = client.post(f"/api/v1/model-deployment-drafts/{draft_id}/validate?project_id=p1", headers={"If-Match": '"1"', "Idempotency-Key": "draft-validate"}, json={})
    assert ready.status_code == 200 and ready.json()["data"]["status"] == "READY"
    assert ready.headers["etag"] == '"2"'
    service.confirmation.add("route-deploy", project_id="p1", resource_ref={"type": "deployment_draft", "id": draft_id}, action="model_deployment.publish", revision=2, config_hash=ready.json()["data"]["config_hash"], actor_id="human")
    submitted = client.post(f"/api/v1/model-deployment-drafts/{draft_id}/submit?project_id=p1", headers={"Idempotency-Key": "submit"}, json={"confirmation_id": "route-deploy"})
    assert submitted.status_code == 202 and submitted.headers["location"].endswith(submitted.json()["data"]["id"])
    submitted_replay = client.post(f"/api/v1/model-deployment-drafts/{draft_id}/submit?project_id=p1", headers={"Idempotency-Key": "submit"}, json={"confirmation_id": "route-deploy"})
    assert submitted_replay.status_code == 202 and submitted_replay.headers["idempotency-replayed"] == "true"


def test_register_replay_precedes_registered_state_and_does_not_reconsume_confirmation():
    service = _service()
    registration = service.create_registration(ModelRegistrationCreate(project_id="p1", source={"kind": "external_package", "artifact_id": "art-1"}, name="register-replay", task_type="sfc"))
    report = service.validate_registration("p1", registration.id)
    service.confirmation.add("register-replay", project_id="p1", resource_ref={"type": "model_registration", "id": registration.id}, action="model_registration.register", revision=report.revision, config_hash=registration.config_hash, actor_id="human")
    first = service.register("p1", registration.id, confirmation_id="register-replay", actor=_actor("model:register"), idempotency_key="register-key")
    replay = service.register("p1", registration.id, confirmation_id="register-replay", actor=_actor("model:register"), idempotency_key="register-key", if_match='"stale"')
    assert replay.id == first.id and replay._idempotency_replayed is True


def test_direct_validation_bumps_revision_and_invalidates_old_confirmation_once():
    service = _service()
    registration = service.create_registration(ModelRegistrationCreate(project_id="p1", source={"kind": "external_package", "artifact_id": "art-1"}, name="direct-validation", task_type="sfc"))
    service.confirmation.add("old-register", project_id="p1", resource_ref={"type": "model_registration", "id": registration.id}, action="model_registration.register", revision=registration.revision, config_hash=registration.config_hash, actor_id="human")
    report = service.validate_registration("p1", registration.id, execute=True)
    current_registration = service.get_registration("p1", registration.id)
    assert report.valid and report.revision == 2 and report.etag == '"2"'
    assert current_registration.revision == 2 and current_registration.etag == '"2"'
    with pytest.raises(ModelCenterConfirmationError):
        service.register("p1", registration.id, confirmation_id="old-register", actor=_actor("model:register"))
    service.confirmation.add("new-register", project_id="p1", resource_ref={"type": "model_registration", "id": registration.id}, action="model_registration.register", revision=report.revision, config_hash=registration.config_hash, actor_id="human")
    model = service.register("p1", registration.id, confirmation_id="new-register", actor=_actor("model:register"))
    service.repository.save_model(model.model_copy(update={"lifecycle_status": "APPROVED"}))
    draft = service.create_deployment_draft(DeploymentDraftCreate(project_id="p1", scope_id="scope", target_model_id=model.id, expected_pointer_revision=0), actor=_actor("model:deploy"))
    service.confirmation.add("old-submit", project_id="p1", resource_ref={"type": "deployment_draft", "id": draft.id}, action="model_deployment.publish", revision=draft.revision, config_hash=draft.config_hash, actor_id="human")
    deployment_report = service.validate_deployment("p1", draft.id)
    current_draft = service.get_draft("p1", draft.id)
    assert deployment_report.valid and deployment_report.revision == 2 and deployment_report.etag == '"2"'
    assert current_draft.revision == 2 and current_draft.etag == '"2"'
    with pytest.raises(ModelCenterConfirmationError):
        service.submit_deployment("p1", draft.id, confirmation_id="old-submit", actor=_actor("model:deploy"))


def test_model_artifacts_require_project_permission_and_use_real_cursor_page():
    class ArtifactList(InMemoryArtifactPort):
        def list(self, *, project_id, model_id=None, artifact_id=None):
            return [{"id": "art-1", "project_id": project_id, "sha256": "a" * 64}, {"id": "art-2", "project_id": project_id, "sha256": "b" * 64}]

    service = _service(artifacts=ArtifactList([{"id": "art-1", "project_id": "p1", "kind": "model_package", "status": "AVAILABLE", "scan_status": "PASSED", "sha256": "a" * 64}]))
    model = _candidate(service)
    first = _client(service, permissions=("model:read",)).get(f"/api/v1/models/{model.id}/artifacts?project_id=p1&limit=1")
    assert first.json()["page"]["has_more"] is True
    cursor = first.json()["page"]["next_cursor"]
    assert _client(service, permissions=("model:read",)).get(f"/api/v1/models/{model.id}/artifacts?project_id=p1&limit=1&cursor={cursor}").status_code == 200
    tampered = cursor[:-10] + ("A" if cursor[-10] != "A" else "B") + cursor[-9:]
    assert _client(service, permissions=("model:read",)).get(f"/api/v1/models/{model.id}/artifacts?project_id=p1&cursor={tampered}").status_code == 422
    forbidden = _client(service, permissions=()).get(f"/api/v1/models/{model.id}/artifacts?project_id=p1")
    assert forbidden.status_code == 403
    _candidate(service)
    models = _client(service, permissions=("model:read",)).get("/api/v1/models?project_id=p1&limit=1")
    model_cursor = models.json()["page"]["next_cursor"]
    assert _client(service, permissions=("model:read",)).get(f"/api/v1/models?project_id=p1&limit=1&lifecycle=CANDIDATE&cursor={model_cursor}").status_code == 422


def test_human_approval_reject_and_rollback_are_audited_without_direct_publish():
    service = _service(loader=StaticDeploymentLoader())
    model = _candidate(service)
    evaluation = service.create_evaluation(EvaluationCreate(project_id="p1", model_id=model.id, protocol_id="ep", protocol_version="1"), actor=_actor("model:evaluate"))
    approval = service.create_approval_request("p1", model.id, ApprovalRequestCreate(evaluation_id=evaluation.id, scope_ids=["scope"], reason="review"), actor=_actor("model:approve"))
    approved = service.decide_approval("p1", approval.id, ApprovalDecision(reason="human", expected_revision=1), approve=True, actor=_actor("model:approve"), if_match='"1"')
    assert approved.status == "APPROVED"
    rejected_model = _candidate(service)
    rejected_eval = service.create_evaluation(EvaluationCreate(project_id="p1", model_id=rejected_model.id, protocol_id="ep", protocol_version="1"), actor=_actor("model:evaluate"))
    rejected_request = service.create_approval_request("p1", rejected_model.id, ApprovalRequestCreate(evaluation_id=rejected_eval.id, scope_ids=["scope"], reason="second review"), actor=_actor("model:approve"))
    rejected = service.decide_approval("p1", rejected_request.id, ApprovalDecision(reason="human reject", expected_revision=1), approve=False, actor=_actor("model:approve"), if_match='"1"')
    assert rejected.status == "REJECTED"
    def publish(value, revision, token):
        draft = service.create_deployment_draft(DeploymentDraftCreate(project_id="p1", scope_id="scope", target_model_id=value.id, expected_pointer_revision=revision), actor=_actor("model:deploy"))
        report = service.validate_deployment("p1", draft.id)
        service.confirmation.add(token, project_id="p1", resource_ref={"type": "deployment_draft", "id": draft.id}, action="model_deployment.publish", revision=report.revision, config_hash=draft.config_hash, actor_id="human")
        accepted = service.submit_deployment("p1", draft.id, confirmation_id=token, actor=_actor("model:deploy"))
        return DeploymentWorker(service.repository, loader=StaticDeploymentLoader()).execute("p1", accepted.id)

    first = publish(model, 0, "deploy-1")
    second_model = _candidate(service)
    second_eval = service.create_evaluation(EvaluationCreate(project_id="p1", model_id=second_model.id, protocol_id="ep", protocol_version="1"), actor=_actor("model:evaluate"))
    second_approval = service.create_approval_request("p1", second_model.id, ApprovalRequestCreate(evaluation_id=second_eval.id, scope_ids=["scope"], reason="review"), actor=_actor("model:approve"))
    service.decide_approval("p1", second_approval.id, ApprovalDecision(reason="human", expected_revision=1), approve=True, actor=_actor("model:approve"), if_match='"1"')
    second = publish(second_model, 1, "deploy-2")
    rollback = service.create_rollback_draft("p1", second.id, RollbackDraftCreate(target_historical_model_id=model.id, expected_pointer_revision=2, reason="audit"), actor=_actor("model:deploy"))
    assert first.status == "SUCCEEDED" and second.status == "SUCCEEDED"
    assert rollback.operation == "ROLLBACK" and rollback.status == "DRAFT"
    assert service.repository.get_pointer("p1", "scope", "PRODUCTION").model_id == second_model.id
    assert any(event.action == "model_deployment.rollback_draft" for event in service.audit.events)


def test_pointer_component_cas_allows_one_revision_and_rejects_stale_without_overwriting():
    repo = InMemoryModelCenterRepository()
    pointer = repo.get_pointer("p1", "scope", "PRODUCTION")
    first = repo.cas_pointer(pointer, expected_revision=0, model_id="model-a")
    with pytest.raises(ModelCenterConflictError):
        repo.cas_pointer(first, expected_revision=0, model_id="model-b")
    assert repo.get_pointer("p1", "scope", "PRODUCTION").model_id == "model-a"


def test_comparison_rejects_evaluation_scope_mismatch():
    service = _service()
    first, second = _candidate(service), _candidate(service)
    evaluations = [service.create_evaluation(EvaluationCreate(project_id="p1", model_id=model.id, protocol_id="ep", protocol_version="1"), actor=_actor("model:evaluate")) for model in (first, second)]
    with pytest.raises(ModelCenterConflictError):
        service.compare(ComparisonCreate(project_id="p1", model_ids=[first.id, second.id], protocol_id="ep", protocol_version="1", scope_id="scope"), actor=_actor("model:evaluate"))
