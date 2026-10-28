from __future__ import annotations

import pytest
from pydantic import ValidationError

from chrom_platform.core.dto import ResourceRef
from chrom_platform.modules.training.persistence import (
    InMemoryCandidateRegistration,
    InMemoryConfirmationPort,
    InMemoryTrainingRepository,
    StaticEvaluationProtocolRegistry,
    StaticTrainingTemplateRegistry,
)
from chrom_platform.modules.training.schemas import (
    EvaluationProtocol,
    ExperimentCreate,
    ExperimentPatch,
    ResourceBudget,
    SplitPredefined,
    SubmitRequest,
    TemplateExecution,
    TrainingTemplate,
    WorkerMessage,
)
from chrom_platform.modules.training.service import TrainingService
from chrom_platform.modules.training.worker import TrainingWorker
from chrom_platform.modules.training.errors import TrainingConflictError


class Ref:
    def __init__(self, id: str, status: str = "READY"):
        self.id, self.status = id, status


def template() -> TrainingTemplate:
    return TrainingTemplate(
        id="fixed-test", version="1", task_type="sfc_separation", data_schema_id="sfc-v1",
        feature_versions=["fv1"], allowed_targets=["label"], parameters_schema={},
        split_schema={"strategies": ["random"]}, output_manifest_schema={},
        evaluation_protocols=[ResourceRef(type="evaluation_protocol", id="ep1")],
        resource_limits=ResourceBudget(cpu_cores=4, memory_mb=4096, gpu_count=0, max_runtime_seconds=100),
        code_version="test", dependency_lock_hash="a" * 64, health="READY",
    )


def request() -> ExperimentCreate:
    return ExperimentCreate(
        project_id="p1", name="fixed", task_type="sfc_separation",
        execution=TemplateExecution(kind="template", template_id="fixed-test", template_version="1"),
        data_version_id="dv1", feature_version_id="fv1", target_field="label",
        split={"strategy": "random", "train_fraction": 0.8, "validation_fraction": 0.1, "test_fraction": 0.1, "random_seed": 7},
        evaluation_protocol=EvaluationProtocol(id="ep1", version="1", status="APPROVED"),
        resource_budget=ResourceBudget(cpu_cores=1, memory_mb=512, gpu_count=0, max_runtime_seconds=10),
    )


def build_service(*, handler=None, registration=True):
    repo = InMemoryTrainingRepository()
    registry = StaticTrainingTemplateRegistry([template()], {("fixed-test", "1"): handler} if handler else {})
    confirmation = InMemoryConfirmationPort()
    candidate = InMemoryCandidateRegistration(repo) if registration else None
    value = TrainingService(repo, templates=registry, data_versions=type("D", (), {"get": lambda *_: Ref("dv1"), "reference": lambda *_: Ref("dv1")})(), feature_versions=type("F", (), {"get": lambda *_: Ref("fv1")})(), evaluation_protocols=StaticEvaluationProtocolRegistry([EvaluationProtocol(id="ep1", version="1", status="APPROVED")]), confirmation=confirmation, candidate_registration=candidate)
    return repo, confirmation, registry, value, candidate


def test_contract_rejects_unknown_and_executable_fields():
    with pytest.raises(ValidationError):
        ExperimentCreate(**request().model_dump(), command="rm")
    with pytest.raises(ValidationError):
        ExperimentPatch(command="python")
    for key in ("script", "python", "entrypoint", "requirements", "server_path"):
        with pytest.raises(ValidationError):
            ExperimentCreate(**{**request().model_dump(), "parameters": {"nested": {key: "x"}}})
    with pytest.raises(ValidationError):
        ExperimentCreate(**{**request().model_dump(), "parameters": {"x": object()}})


def test_split_union_keeps_strategy_specific_fields_and_unhandled_strategy_invalid():
    predefined = {"strategy": "predefined", "split_manifest_artifact_id": "split-1"}
    value = ExperimentCreate(**{**request().model_dump(), "split": predefined})
    assert isinstance(value.split, SplitPredefined)
    repo, _, _, app_service, _ = build_service(handler=lambda spec, *, attempt: {"artifact_id": "a", "manifest": {}})
    exp = app_service.create(value)
    report = app_service.validate("p1", exp.id)
    assert not report.valid
    assert any(error.code == "SPLIT_HANDLER_UNAVAILABLE" for error in report.field_errors)


def test_validation_requires_immutable_feature_approved_protocol_and_template_parameters():
    repo, _, registry, app_service, _ = build_service(handler=lambda spec, *, attempt: {"artifact_id": "a", "manifest": {}})
    exp = app_service.create(ExperimentCreate(**{**request().model_dump(), "parameters": {"learning_rate": 0.1}}))
    report = app_service.validate("p1", exp.id)
    assert not report.valid
    assert {error.code for error in report.field_errors} >= {"PARAMETERS_NOT_ALLOWED"}

    class Mutable:
        id = "fv1"
        status = "DRAFT"

    app_service.feature_versions = type("F", (), {"get": lambda *_: Mutable()})()
    exp = app_service.get("p1", exp.id)
    report = app_service.validate("p1", exp.id)
    assert any(error.code == "FEATURE_VERSION_NOT_FOUND" for error in report.field_errors)

    app_service.evaluation_protocols = StaticEvaluationProtocolRegistry([EvaluationProtocol(id="ep1", version="1", status="REVIEWED")])
    report = app_service.validate("p1", exp.id)
    assert any(error.code == "EVALUATION_PROTOCOL_NOT_APPROVED" for error in report.field_errors)


def test_confirmation_subject_expiry_once_and_transaction_rollback():
    from datetime import timedelta
    from chrom_platform.modules.training.persistence import now_utc

    confirmation = InMemoryConfirmationPort()
    confirmation.add("expired", project_id="p1", resource_ref={"type": "experiment", "id": "e1"}, action="training.submit", revision=1, config_hash="a" * 64, actor_id="a1", expires_at=now_utc() - timedelta(seconds=1))
    with pytest.raises(TrainingConflictError):
        confirmation.consume(confirmation_id="expired", project_id="p1", resource_ref={"type": "experiment", "id": "e1"}, action="training.submit", revision=1, config_hash="a" * 64, result_ref={"type": "experiment", "id": "e1"}, actor_id="a1")

    confirmation.add("once", project_id="p1", resource_ref={"type": "experiment", "id": "e1"}, action="training.submit", revision=1, config_hash="a" * 64, actor_id="a1")
    confirmation.consume(confirmation_id="once", project_id="p1", resource_ref={"type": "experiment", "id": "e1"}, action="training.submit", revision=1, config_hash="a" * 64, result_ref={"type": "experiment", "id": "e1"}, actor_id="a1")
    with pytest.raises(TrainingConflictError):
        confirmation.consume(confirmation_id="once", project_id="p1", resource_ref={"type": "experiment", "id": "e1"}, action="training.submit", revision=1, config_hash="a" * 64, result_ref={"type": "experiment", "id": "e1"}, actor_id="a1")

    repo, confirmation, _, app_service, _ = build_service(handler=lambda spec, *, attempt: {"artifact_id": "a", "manifest": {}})
    exp = app_service.create(request())
    app_service.validate("p1", exp.id)
    confirmation.add("rollback", project_id="p1", resource_ref={"type": "experiment", "id": exp.id}, action="training.submit", revision=exp.revision, config_hash=exp.config_hash, actor_id="a1")
    repo.create_submission = lambda **_: (_ for _ in ()).throw(RuntimeError("db unavailable"))
    with pytest.raises(TrainingConflictError):
        app_service.submit("p1", exp.id, "rollback", actor_id="a1", if_match=exp.etag)
    assert confirmation.records["rollback"]["status"] == "CONFIRMED"


def test_worker_rejects_stale_attempt_and_does_not_reexecute_running_attempt():
    calls = 0

    def handler(spec, *, attempt):
        nonlocal calls
        calls += 1
        return {"artifact_id": "artifact-1", "manifest": {"schema": "1"}}

    repo, confirmation, registry, app_service, candidate = build_service(handler=handler)
    exp = app_service.create(request())
    app_service.validate("p1", exp.id)
    confirmation.add("c1", project_id="p1", resource_ref={"type": "experiment", "id": exp.id}, action="training.submit", revision=exp.revision, config_hash=exp.config_hash, actor_id="a1")
    accepted = app_service.submit("p1", exp.id, "c1", actor_id="a1", if_match=exp.etag)
    job = repo.get_job(accepted.job_id)
    worker = TrainingWorker(repo, registry, candidate_registration=candidate)
    with pytest.raises(TrainingConflictError):
        worker.process(WorkerMessage(job_id=job.id, attempt=2, dispatch_id=job.dispatch_id))
    repo.save_job(job.model_copy(update={"status": "RUNNING"}))
    assert worker.process(WorkerMessage(job_id=job.id, attempt=1, dispatch_id=job.dispatch_id)).status == "RUNNING"
    assert calls == 0


def test_lifecycle_freezes_spec_and_duplicate_submit_is_idempotent():
    def handler(spec, *, attempt):
        return {"artifact_id": "artifact-1", "manifest": {"schema": "1"}}

    repo, confirmation, registry, app_service, candidate = build_service(handler=handler)
    exp = app_service.create(request())
    report = app_service.validate("p1", exp.id)
    assert report.valid
    confirmation.add("c1", project_id="p1", resource_ref={"type": "experiment", "id": exp.id}, action="training.submit", revision=exp.revision, config_hash=exp.config_hash)
    accepted = app_service.submit("p1", exp.id, SubmitRequest(confirmation_id="c1"), actor_id="a1", if_match=exp.etag, idempotency_key="k1")
    assert app_service.get("p1", exp.id).spec_locked
    with pytest.raises(Exception):
        app_service.patch("p1", exp.id, ExperimentPatch(name="changed"))
    assert app_service.submit("p1", exp.id, SubmitRequest(confirmation_id="c1"), actor_id="a1", if_match=exp.etag, idempotency_key="k1").job_id == accepted.job_id
    job = repo.get_job(accepted.job_id)
    worker = TrainingWorker(repo, registry, candidate_registration=candidate)
    result = worker.process(WorkerMessage(job_id=job.id, attempt=1, dispatch_id=job.dispatch_id))
    assert result.status == "SUCCEEDED"
    assert app_service.get("p1", exp.id).candidate_status == "CREATED"
    assert not next(iter(repo.candidates.values())).published


def test_submission_replay_keeps_actor_and_confirmation_scope():
    repo, confirmation, _, app_service, _ = build_service(handler=lambda spec, *, attempt: {"artifact_id": "artifact-1", "manifest": {}})
    exp = app_service.create(request())
    app_service.validate("p1", exp.id)
    confirmation.add("c1", project_id="p1", resource_ref={"type": "experiment", "id": exp.id}, action="training.submit", revision=exp.revision, config_hash=exp.config_hash, actor_id="a1")
    app_service.submit("p1", exp.id, "c1", actor_id="a1", if_match=exp.etag, idempotency_key="k1")
    with pytest.raises(TrainingConflictError):
        app_service.submit("p1", exp.id, "c1", actor_id="a2", if_match=exp.etag, idempotency_key="k2")
    with pytest.raises(TrainingConflictError):
        app_service.submit("p1", exp.id, "other-confirmation", actor_id="a1", if_match=exp.etag, idempotency_key="k1")


def test_failure_cancel_timeout_never_registers_candidate():
    def handler(spec, *, attempt):
        raise RuntimeError("expected")

    repo, confirmation, registry, app_service, candidate = build_service(handler=handler)
    exp = app_service.create(request())
    app_service.validate("p1", exp.id)
    confirmation.add("c1", project_id="p1", resource_ref={"type": "experiment", "id": exp.id}, action="training.submit", revision=exp.revision, config_hash=exp.config_hash)
    accepted = app_service.submit("p1", exp.id, "c1", if_match=exp.etag)
    job = repo.get_job(accepted.job_id)
    result = TrainingWorker(repo, registry, candidate_registration=candidate).process(WorkerMessage(job_id=job.id, attempt=1, dispatch_id=job.dispatch_id))
    assert result.status == "FAILED"
    assert candidate.calls == 0
    assert not repo.candidates
