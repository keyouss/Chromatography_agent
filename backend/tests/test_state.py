from datetime import datetime, timedelta, timezone

import pytest

from chrom_platform.task_core.state import (
    ConfirmationBinding,
    ConfirmationRecord,
    ConfirmationStatus,
    InvalidTransition,
    JobStatus,
    OutboxStatus,
    fencing_token_matches,
    idempotency_scope,
    job_action_flags,
    transition_outbox,
    transition_job,
)


@pytest.mark.parametrize(
    "status, expected_cancelable, expected_retryable",
    [
        (JobStatus.QUEUED, True, False),
        (JobStatus.RUNNING, True, False),
        (JobStatus.CANCEL_REQUESTED, False, False),
        (JobStatus.SUCCEEDED, False, False),
        (JobStatus.PARTIAL, False, False),
        (JobStatus.FAILED, False, True),
        (JobStatus.CANCELED, False, True),
        (JobStatus.TIMED_OUT, False, True),
        (JobStatus.INTERRUPTED, False, True),
    ],
)
def test_job_action_flags_follow_the_state_machine(status, expected_cancelable, expected_retryable):
    assert job_action_flags(status, cancelable=True, retryable=True) == (
        expected_cancelable,
        expected_retryable,
    )


def test_job_action_flags_preserve_configured_capability_limits():
    assert job_action_flags(JobStatus.QUEUED, cancelable=False, retryable=True) == (False, False)
    assert job_action_flags(JobStatus.FAILED, cancelable=True, retryable=False) == (False, False)


def test_job_state_machine_accepts_cancel_and_retry_paths() -> None:
    assert transition_job(JobStatus.QUEUED, JobStatus.RUNNING) is JobStatus.RUNNING
    assert transition_job(JobStatus.RUNNING, JobStatus.CANCEL_REQUESTED) is JobStatus.CANCEL_REQUESTED
    assert (
        transition_job(JobStatus.CANCEL_REQUESTED, JobStatus.SUCCEEDED, actual_finished=True)
        is JobStatus.SUCCEEDED
    )
    assert transition_job(JobStatus.FAILED, JobStatus.QUEUED, retry=True) is JobStatus.QUEUED
    assert transition_job(JobStatus.INTERRUPTED, JobStatus.QUEUED, coordinator=True) is JobStatus.QUEUED


def test_job_state_machine_rejects_unsafe_transitions() -> None:
    with pytest.raises(InvalidTransition):
        transition_job(JobStatus.SUCCEEDED, JobStatus.QUEUED, retry=True)
    with pytest.raises(InvalidTransition):
        transition_job(JobStatus.CANCEL_REQUESTED, JobStatus.SUCCEEDED, actual_finished=False)
    with pytest.raises(InvalidTransition):
        transition_job(JobStatus.QUEUED, JobStatus.SUCCEEDED)


def _binding(*, actor_id: str = "actor-1", revision: int = 1) -> ConfirmationBinding:
    return ConfirmationBinding(
        project_id="project-1",
        actor_id=actor_id,
        resource_ref={"type": "experiment", "id": "exp-1"},
        action="experiment.submit",
        revision=revision,
        config_hash="a" * 64,
        policy_version="policy-v1",
    )


def test_confirmation_binding_and_one_time_consumption() -> None:
    now = datetime(2026, 9, 20, tzinfo=timezone.utc)
    record = ConfirmationRecord(_binding(), now + timedelta(minutes=30))
    record.consume(_binding(), result_ref={"job_id": "job-1"}, request_hash="request-1", now=now)
    assert record.status is ConfirmationStatus.CONSUMED
    assert record.consumed_result_ref == {"job_id": "job-1"}

    # A network retry is idempotent; another request cannot reuse the action.
    record.consume(_binding(), result_ref={"job_id": "job-1"}, request_hash="request-1", now=now)
    with pytest.raises(InvalidTransition):
        record.consume(_binding(), result_ref={"job_id": "job-2"}, request_hash="request-2", now=now)
    with pytest.raises(InvalidTransition):
        record.consume(_binding(revision=2), result_ref={}, request_hash="request-1", now=now)


def test_confirmation_expiry_and_revoke_are_terminal() -> None:
    now = datetime(2026, 9, 20, tzinfo=timezone.utc)
    expired = ConfirmationRecord(_binding(), now)
    with pytest.raises(InvalidTransition):
        expired.consume(_binding(), result_ref={}, request_hash="request-1", now=now)
    assert expired.status is ConfirmationStatus.EXPIRED

    revoked = ConfirmationRecord(_binding(), now + timedelta(minutes=30))
    revoked.revoke()
    assert revoked.status is ConfirmationStatus.REVOKED
    with pytest.raises(InvalidTransition):
        revoked.revoke()


def test_outbox_fencing_and_idempotency_scope_are_server_bound() -> None:
    scope = idempotency_scope(
        actor_id="actor-1", project_id="project-1", method="POST", route_identity="/jobs", key="k-1"
    )
    assert scope == ("actor-1", "project-1", "POST", "/jobs", "k-1")
    assert idempotency_scope(
        actor_id="actor-2", project_id="project-1", method="POST", route_identity="/jobs", key="k-1"
    ) != scope
    with pytest.raises(ValueError):
        idempotency_scope(actor_id="", project_id="project-1", method="POST", route_identity="/jobs", key="k")

    assert fencing_token_matches(worker_token=4, current_token=4)
    assert not fencing_token_matches(worker_token=3, current_token=4)
    assert not fencing_token_matches(worker_token=0, current_token=0)
    assert transition_outbox(OutboxStatus.PROCESSING, published=False) is OutboxStatus.PENDING
    assert transition_outbox(OutboxStatus.PENDING, published=True) is OutboxStatus.SENT
    assert transition_outbox(OutboxStatus.SENT, published=False) is OutboxStatus.SENT
