from __future__ import annotations

from types import SimpleNamespace

import pytest

from chrom_platform.artifact_core.repository import SQLAlchemyProjectArtifactReader
from chrom_platform.modules.model_center.errors import (
    ModelCenterNotFoundError,
    ModelCenterUnavailableError,
    ModelCenterValidationError,
)
from chrom_platform.modules.model_center.persistence import InMemoryModelCenterRepository
from chrom_platform.modules.model_center.schemas import ModelRegistrationCreate
from chrom_platform.modules.model_center.service import ModelCenterService


class ArtifactReader:
    def __init__(self, artifact):
        self.artifact = artifact
        self.calls = []

    def get(self, project_id, artifact_id):
        self.calls.append((project_id, artifact_id))
        return self.artifact


def request(*, artifact_id="artifact-1"):
    return ModelRegistrationCreate(
        project_id="project-1",
        source={"kind": "external_package", "artifact_id": artifact_id},
        name="candidate",
        task_type="sfc",
    )


def ready_artifact(**updates):
    values = {
        "id": "artifact-1",
        "project_id": "project-1",
        "kind": "model_package",
        "status": "AVAILABLE",
        "scan_status": "PASSED",
    }
    values.update(updates)
    return values


def build(artifacts=None):
    repository = InMemoryModelCenterRepository()
    return ModelCenterService(repository, artifacts=artifacts), repository


def assert_no_write(repository):
    assert repository.registrations == {}
    assert repository.idempotency == {}


def test_create_requires_reader_and_fails_closed_without_writing():
    service, repository = build()

    with pytest.raises(ModelCenterUnavailableError):
        service.create_registration(request(), idempotency_key="new-key")

    assert_no_write(repository)


@pytest.mark.parametrize(
    ("artifact", "error"),
    [
        (None, ModelCenterNotFoundError),
        (ready_artifact(project_id="other-project"), ModelCenterNotFoundError),
        (ready_artifact(id="other-artifact"), ModelCenterNotFoundError),
        ({"id": "artifact-1", "project_id": "project-1", "status": "AVAILABLE", "scan_status": "PASSED"}, ModelCenterValidationError),
        (ready_artifact(kind="dataset"), ModelCenterValidationError),
        (ready_artifact(status="VALIDATING"), ModelCenterValidationError),
        (ready_artifact(status="ARCHIVED"), ModelCenterValidationError),
        (ready_artifact(scan_status="PENDING"), ModelCenterValidationError),
        (ready_artifact(scan_status="NOT_CONFIGURED"), ModelCenterValidationError),
        (ready_artifact(scan_status="FAILED"), ModelCenterValidationError),
        (SimpleNamespace(id="artifact-1", project_id="project-1", kind="model_package", status="AVAILABLE"), ModelCenterValidationError),
    ],
)
def test_invalid_artifacts_are_rejected_before_registration_or_idempotency_write(artifact, error):
    port = ArtifactReader(artifact)
    service, repository = build(port)

    with pytest.raises(error):
        service.create_registration(request(), idempotency_key="new-key")

    assert port.calls == [("project-1", "artifact-1")]
    assert_no_write(repository)


def test_qualified_artifact_creates_draft_and_get_without_model_verifier():
    service, repository = build(ArtifactReader(ready_artifact()))
    assert service.asset_verifier is None

    registration = service.create_registration(request(), idempotency_key="create-key")
    fetched = service.get_registration("project-1", registration.id)

    assert fetched.id == registration.id
    assert fetched.status == "DRAFT"
    assert repository.idempotency
    assert service.asset_verifier is None


def test_successful_idempotency_replay_keeps_existing_behavior_before_artifact_lookup():
    port = ArtifactReader(ready_artifact())
    service, repository = build(port)
    created = service.create_registration(request(), idempotency_key="create-key")
    service.artifacts = None

    replay = service.create_registration(request(), idempotency_key="create-key")

    assert replay.id == created.id
    assert replay._idempotency_replayed is True
    assert port.calls == [("project-1", "artifact-1")]
    assert len(repository.registrations) == 1


def test_sqlalchemy_reader_queries_by_project_and_artifact_on_caller_session():
    artifact = object()

    class SessionStub:
        def __init__(self):
            self.statement = None

        def scalar(self, statement):
            self.statement = statement
            return artifact

    session = SessionStub()
    reader = SQLAlchemyProjectArtifactReader(session)

    assert reader.get("project-1", "artifact-1") is artifact
    assert session.statement is not None
    assert "artifact.project_id" in str(session.statement)
    assert "artifact.id" in str(session.statement)
    assert set(session.statement.compile().params.values()) == {"project-1", "artifact-1"}


def test_sqlalchemy_reader_rejects_session_factory():
    with pytest.raises(TypeError):
        SQLAlchemyProjectArtifactReader(lambda: None)
