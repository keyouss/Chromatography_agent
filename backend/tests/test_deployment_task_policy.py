from __future__ import annotations

import pytest

from chrom_platform.core.dto import ActorContext
from chrom_platform.task_core.policy import DeploymentTaskPolicy
from chrom_platform.task_core.schemas import JobCreateRequest
from chrom_platform.task_core.service import JobService, OperationContext, TaskError

from test_job_service import FakeRepo


def _ctx(*, actor_id: str = "deployment-default", projects: tuple[str, ...] = ("priority-local",), permissions: list[str] | None = None) -> OperationContext:
    return OperationContext(
        ActorContext(
            actor_id=actor_id,
            tenant_id=None,
            project_ids=list(projects),
            auth_method="deployment_default",
            permissions=permissions if permissions is not None else ["task:read"],
            policy_version="deployment-default-v1",
        )
    )


def _request() -> JobCreateRequest:
    return JobCreateRequest(kind="prediction", resource_ref={"type": "prediction", "id": "pred-1"})


def test_priority_local_policy_reads_owned_job_attempts_and_events() -> None:
    repo = FakeRepo()
    service = JobService(repo, policy=DeploymentTaskPolicy())
    context = _ctx()
    created = service.create(context, "priority-local", _request(), idempotency_key="create-1")

    assert service.get(context, created.id).owner_id == "deployment-default"
    assert len(service.attempts(context, created.id)) == 1
    assert [event.type for event in service.events(context, created.id)] == ["job.queued"]


def test_priority_local_policy_rejects_cross_project_and_non_owner_reads() -> None:
    repo = FakeRepo()
    service = JobService(repo, policy=DeploymentTaskPolicy())
    owner = _ctx()
    created = service.create(owner, "priority-local", _request(), idempotency_key="create-2")

    with pytest.raises(TaskError) as cross_project:
        service.get(_ctx(projects=("other-project",)), created.id)
    assert cross_project.value.status_code == 404

    with pytest.raises(TaskError) as non_owner:
        service.get(_ctx(actor_id="other-actor"), created.id)
    assert non_owner.value.status_code == 403


def test_task_read_any_implies_read_for_same_project_only() -> None:
    repo = FakeRepo()
    service = JobService(repo, policy=DeploymentTaskPolicy())
    owner = _ctx()
    created = service.create(owner, "priority-local", _request(), idempotency_key="create-any")

    administrator = _ctx(actor_id="administrator", permissions=["task:read:any"])
    assert service.get(administrator, created.id).id == created.id

    with pytest.raises(TaskError) as cross_project:
        service.get(_ctx(actor_id="administrator", projects=("other-project",), permissions=["task:read:any"]), created.id)
    assert cross_project.value.status_code == 404

    with pytest.raises(TaskError) as no_read_permission:
        service.get(_ctx(actor_id="other-actor", permissions=[]), created.id)
    assert no_read_permission.value.status_code == 403


def test_unassembled_policy_remains_controlled_503_and_request_actor_is_not_used() -> None:
    repo = FakeRepo()
    service = JobService(repo, policy=None)
    owner = _ctx()
    created = service.create(owner, "priority-local", _request(), idempotency_key="create-3")

    with pytest.raises(TaskError) as missing_policy:
        service.get(owner, created.id)
    assert missing_policy.value.status_code == 503
    assert missing_policy.value.code == "POLICY_UNAVAILABLE"

    # A request-shaped actor value cannot grant access; only the server-built
    # OperationContext reaches the service boundary.
    with pytest.raises(TaskError) as forged_actor:
        JobService(repo, policy=DeploymentTaskPolicy()).get(_ctx(actor_id="deployment-default", projects=("other-project",)), created.id)
    assert forged_actor.value.status_code == 404
