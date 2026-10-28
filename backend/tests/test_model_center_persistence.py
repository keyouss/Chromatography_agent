from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from chrom_platform.modules.model_center.errors import ModelCenterConflictError
from chrom_platform.modules.model_center.persistence import (
    DeploymentLedgerRow,
    ModelCenterBase,
    SQLAlchemyModelCenterRepository,
)
from chrom_platform.modules.model_center.schemas import (
    DataSnapshot,
    Deployment,
    DeploymentDraft,
    EvaluationProtocol,
    Model,
    ModelEvaluation,
    ModelRegistration,
    ModelSnapshot,
    now_utc,
)


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _foreign_keys(dbapi_connection, _):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    ModelCenterBase.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _registration(project="p1"):
    now = now_utc()
    return ModelRegistration(
        id="reg-1", project_id=project, revision=1, config_hash="a" * 64,
        etag='"1"', status="DRAFT",
        source={"kind": "external_package", "artifact_id": "artifact-1"},
        name="candidate", task_type="sfc", created_at=now, updated_at=now,
    )


def _model(project="p1"):
    now = now_utc()
    return Model(
        id="model-1", project_id=project, name="candidate", version="1",
        task_type="sfc", artifact_id="artifact-1", sha256="b" * 64,
        lifecycle_status="APPROVED", input_schema_version="in-1",
        output_schema_version="out-1", source={"registration_id": "reg-1"},
        created_at=now, updated_at=now,
    )


def _evaluation(model, project="p1"):
    protocol = EvaluationProtocol(
        id="ep-1", version="1", task_type="sfc", data_version_id="dv-1",
        split_manifest_sha256="c" * 64, metrics=[{"key": "mae"}],
        independence_description="held out",
    )
    return protocol, ModelEvaluation(
        id="eval-1", project_id=project, status="SUCCEEDED",
        model_snapshot=ModelSnapshot(
            id=model.id, version=model.version, business_scope_id="scope-1",
            artifact_sha256=model.sha256, input_schema_version="in-1",
            output_schema_version="out-1",
        ), protocol_snapshot=protocol,
        data_snapshot=DataSnapshot(
            data_version_id="dv-1", split_manifest_sha256="c" * 64,
            snapshot_hash="d" * 64,
        ), metrics={"mae": 0.1}, pass_status="PASSED",
        created_at=model.created_at, updated_at=model.updated_at,
    )


def test_metadata_is_module_owned_and_round_trips_json_and_project_scope(db):
    repo = SQLAlchemyModelCenterRepository(db)
    registration = _registration()
    repo.save_registration(registration)
    assert repo.get_registration("p1", registration.id).source.artifact_id == "artifact-1"
    assert repo.get_registration("p2", registration.id) is None
    with pytest.raises(ModelCenterConflictError):
        repo.save_registration(registration.model_copy(update={"source": {"kind": "external_package", "artifact_id": "other"}}))

    model = _model()
    repo.save_model(model)
    with pytest.raises(ModelCenterConflictError):
        repo.save_model(model.model_copy(update={"sha256": "e" * 64}))
    assert {"registration", "model", "evaluation", "production_pointer", "deployment_ledger", "idempotency"} <= set(ModelCenterBase.metadata.tables)


def test_evaluation_snapshot_and_idempotency_are_immutable(db):
    repo = SQLAlchemyModelCenterRepository(db)
    repo.save_registration(_registration())
    model = _model(); repo.save_model(model)
    protocol, evaluation = _evaluation(model)
    repo.save_evaluation_protocol(protocol)
    repo.save_evaluation(evaluation)
    with pytest.raises(ModelCenterConflictError):
        repo.save_evaluation(evaluation.model_copy(update={"data_snapshot": evaluation.data_snapshot.model_copy(update={"snapshot_hash": "e" * 64})}))
    repo.idempotent_put("scope-1", "a" * 64, model)
    assert repo.idempotent_get("scope-1", "a" * 64).id == model.id
    with pytest.raises(ModelCenterConflictError):
        repo.idempotent_get("scope-1", "b" * 64)


def test_pointer_cas_and_nested_transaction_rollback_keep_old_champion(db):
    repo = SQLAlchemyModelCenterRepository(db)
    pointer = repo.get_pointer("p1", "scope-1", "PRODUCTION")
    updated = repo.cas_pointer(pointer, expected_revision=0, model_id="model-1")
    assert updated.revision == 1
    with pytest.raises(ModelCenterConflictError):
        repo.cas_pointer(updated, expected_revision=0, model_id="model-2")
    before = repo.get_pointer("p1", "scope-1", "PRODUCTION")
    with pytest.raises(RuntimeError):
        with repo.transaction():
            repo.cas_pointer(before, expected_revision=1, model_id="model-2")
            raise RuntimeError("worker failure")
    assert repo.get_pointer("p1", "scope-1", "PRODUCTION").model_id == "model-1"


def test_deployment_ledger_is_append_only_and_session_remains_caller_owned(db):
    repo = SQLAlchemyModelCenterRepository(db)
    now = now_utc()
    # The referenced draft/model rows are intentionally created through the
    # repository to exercise normal FK ordering.
    repo.save_registration(_registration())
    repo.save_model(_model())
    draft = DeploymentDraft(
        id="draft-1", project_id="p1", scope_id="scope-1", target_model_id="model-1",
        stage="PRODUCTION", expected_pointer_revision=0, revision=1,
        config_hash="f" * 64, etag='"1"', status="READY", operation="PUBLISH",
        created_at=now, updated_at=now,
    )
    repo.save_draft(draft)
    deployment = Deployment(
        id="deployment-1", project_id="p1", scope_id="scope-1", stage="PRODUCTION",
        target_model_id="model-1", operation="PUBLISH", status="QUEUED",
        expected_pointer_revision=0, draft_id=draft.id, created_at=now,
    )
    repo.save_deployment(deployment)
    repo.save_deployment(deployment.model_copy(update={"status": "FAILED", "finished_at": now}))
    rows = db.scalars(select(DeploymentLedgerRow).where(DeploymentLedgerRow.deployment_id == deployment.id)).all()
    assert len(rows) == 2 and rows[0].snapshot["status"] == "QUEUED"
    assert db.is_active
