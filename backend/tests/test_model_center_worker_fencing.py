from __future__ import annotations

from datetime import datetime, timezone

from chrom_platform.modules.model_center.persistence import InMemoryModelCenterRepository
from chrom_platform.modules.model_center.schemas import ModelRegistration
from chrom_platform.modules.model_center.worker import RegistrationValidationWorker
from chrom_platform.task_core.outbox_dispatch import LeaseLostError


def _registration(*, source_kind: str = "external_package") -> ModelRegistration:
    source = (
        {"kind": "external_package", "artifact_id": "artifact-1"}
        if source_kind == "external_package"
        else {"kind": "training_result", "experiment_id": "experiment-1", "artifact_id": "artifact-1"}
    )
    now = datetime.now(timezone.utc)
    return ModelRegistration(
        id="registration-1",
        project_id="project-1",
        revision=1,
        config_hash="a" * 64,
        etag='"1"',
        status="VALIDATING",
        source=source,
        name="candidate",
        task_type="gc",
        created_at=now,
        updated_at=now,
    )


class _LoseOnSecondCheck:
    def __init__(self) -> None:
        self.calls = 0

    def check(self) -> None:
        self.calls += 1
        if self.calls >= 2:
            raise LeaseLostError("OUTBOX_LEASE_LOST")


class _Guard:
    def check(self) -> None:
        return None


class _FencedVerifier:
    def __init__(self) -> None:
        self.fence_called = False

    def verify(self, *, project_id, source, task_type, operation_key="service", check_fence=None):
        assert callable(check_fence)
        self.fence_called = True
        check_fence()
        raise AssertionError("lease loss must stop verification")


class _LegacyVerifier:
    def __init__(self) -> None:
        self.calls = 0

    def verify(self, *, project_id, source, task_type, operation_key="service"):
        self.calls += 1
        return {"valid": False}


def test_lease_loss_inside_verifier_callback_does_not_write_success() -> None:
    repository = InMemoryModelCenterRepository()
    registration = repository.save_registration(_registration())
    verifier = _FencedVerifier()
    guard = _LoseOnSecondCheck()

    result = RegistrationValidationWorker(repository, verifier=verifier).execute(
        registration.project_id,
        registration.id,
        lease_guard=guard,
    )

    assert verifier.fence_called is True
    assert guard.calls == 2
    assert result.status == "VALIDATING"
    assert repository.get_registration(registration.project_id, registration.id) == registration


def test_package_verifier_without_fence_fails_closed_but_legacy_nonpackage_verifier_runs() -> None:
    repository = InMemoryModelCenterRepository()
    package_registration = repository.save_registration(_registration())
    unsupported = _LegacyVerifier()

    failed = RegistrationValidationWorker(repository, verifier=unsupported).execute(
        package_registration.project_id,
        package_registration.id,
        lease_guard=_Guard(),
    )

    assert unsupported.calls == 0
    assert failed.status == "INVALID"

    repository = InMemoryModelCenterRepository()
    training_registration = repository.save_registration(_registration(source_kind="training_result"))
    legacy = _LegacyVerifier()
    result = RegistrationValidationWorker(repository, verifier=legacy).execute(
        training_registration.project_id,
        training_registration.id,
        lease_guard=_Guard(),
    )

    assert legacy.calls == 1
    assert result.status == "INVALID"
