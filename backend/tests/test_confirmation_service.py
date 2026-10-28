from __future__ import annotations

from datetime import timedelta

import pytest

from chrom_platform.core.dto import ActorContext, ResourceRef
from chrom_platform.task_core.schemas import ConfirmationCreateRequest, ConfirmationPreviewRequest
from chrom_platform.task_core.service import (
    ConfirmedJobSpec,
    ConfirmationService,
    JobService,
    OperationContext,
    TargetSnapshot,
    TaskError,
)
from chrom_platform.infrastructure.models import AuditEvent, Job
from test_job_service import FakePolicy, FakeRepo, ctx, request as job_request


def target(revision=1, actor_project="p1"):
    return TargetSnapshot(actor_project, ResourceRef(type="experiment", id="e1"), revision, "a" * 64, "etag-1", "p1", summary=[{"label": "rows", "value": 1}])


def confirmation_request(t):
    return ConfirmationCreateRequest(resource_ref=t.resource_ref, action="training.submit", revision=t.revision, config_hash=t.config_hash, policy_version=t.policy_version)


def test_confirmation_binds_actor_project_revision_hash_and_consumes_once():
    repo = FakeRepo()
    jobs = JobService(repo, policy=FakePolicy())
    service = ConfirmationService(repo, policy=FakePolicy(), job_service=jobs)
    created = service.create(ctx(), target(), confirmation_request(target()), if_match="etag-1", idempotency_key="confirm-1")
    spec = ConfirmedJobSpec("p1", "training", target().resource_ref, "training.submit")
    consumed = service.consume(ctx(), created.id, target=target(), job=spec, idempotency_key="consume-1")
    assert consumed.status == "QUEUED"
    assert len([x for x in repo.rows if x.__class__.__name__ == "OutboxEvent"]) == 1
    assert len([x for x in repo.rows if x.__class__.__name__ == "AuditEvent"]) == 1
    replay = service.consume(ctx(), created.id, target=target(), job=spec, idempotency_key="consume-2")
    assert replay.replayed is True
    assert replay.id == consumed.id
    with pytest.raises(TaskError) as different:
        service.consume(ctx(), created.id, target=target(2), job=spec, idempotency_key="consume-3")
    assert different.value.code == "CONFIRMATION_INVALID"


def test_confirmation_requires_if_match_and_expiry_is_real_state():
    repo = FakeRepo()
    service = ConfirmationService(repo, policy=FakePolicy(), job_service=JobService(repo, policy=FakePolicy()))
    with pytest.raises(TaskError) as missing:
        service.create(ctx(), target(), confirmation_request(target()), if_match=None, idempotency_key="c1")
    assert missing.value.status_code == 428
    created = service.create(ctx(), target(), confirmation_request(target()), if_match="etag-1", idempotency_key="c2")
    row = repo.get_confirmation(repo, created.id) if hasattr(repo, "get_confirmation") else next(x for x in repo.rows if x.__class__.__name__ == "Confirmation")
    row.expires_at = row.created_at - timedelta(seconds=1)
    with pytest.raises(TaskError) as expired:
        service.consume(ctx(), created.id, target=target(), job=ConfirmedJobSpec("p1", "training", target().resource_ref, "training.submit"), idempotency_key="c3")
    assert expired.value.code == "CONFIRMATION_EXPIRED"
    assert row.status == "EXPIRED"


def test_preview_preserves_server_blockers_and_can_confirm():
    repo = FakeRepo()
    policy = FakePolicy()
    service = ConfirmationService(repo, policy=policy)
    blocked = TargetSnapshot("p1", ResourceRef(type="experiment", id="e1"), 1, "a" * 64, "etag-1", "p1", can_confirm=False, blockers=[{"code": "NOT_READY", "message": "validation pending"}])
    preview = service.preview(ctx(), blocked, ConfirmationPreviewRequest(resource_ref=blocked.resource_ref, action="training.submit"))
    assert preview.can_confirm is False
    assert preview.blockers[0].code == "NOT_READY"


def test_revoke_keeps_reason_in_audit_summary():
    repo = FakeRepo()
    policy = FakePolicy()
    service = ConfirmationService(repo, policy=policy, resolver=lambda _ctx, ref, _action: target())
    t = target()
    created = service.create(ctx(), t, confirmation_request(t), if_match=t.etag, idempotency_key="create-revoke")
    revoked = service.revoke(ctx(), created.id, reason="outdated validation", idempotency_key="revoke-1")
    assert revoked.status == "REVOKED"
    audit = next(x for x in repo.rows if isinstance(x, AuditEvent) and x.action == "confirmation.revoke")
    assert audit.after_summary["reason"] == "outdated validation"


def test_retry_payload_and_policy_require_and_consume_job_confirmation():
    repo = FakeRepo()
    policy = FakePolicy(requires_confirmation=True)
    confirmation_service = ConfirmationService(repo, policy=policy)
    jobs = JobService(repo, policy=policy, confirmation_service=confirmation_service)
    confirmation_service.job_service = jobs
    created = jobs.create(ctx(), "p1", job_request(), idempotency_key="j1")
    job_row = next(x for x in repo.rows if isinstance(x, Job) and x.id == created.id)
    job_row.status = "FAILED"
    job_row.retryable = True
    with pytest.raises(TaskError) as required:
        jobs.retry(ctx(), created.id, if_match=created.etag, idempotency_key="retry-1", confirmation_id=None)
    assert required.value.code == "CONFIRMATION_REQUIRED"
    t = target()
    req = ConfirmationCreateRequest(resource_ref=t.resource_ref, action="job.retry", revision=1, config_hash=t.config_hash, policy_version=t.policy_version)
    confirmed = confirmation_service.create(ctx(), t, req, if_match=t.etag, idempotency_key="confirm-retry")
    retried = jobs.retry(ctx(), created.id, if_match=created.etag, idempotency_key="retry-2", confirmation_id=confirmed.id)
    assert retried.status == "QUEUED"
    assert any(isinstance(x, AuditEvent) and x.before_summary and x.after_summary for x in repo.rows)
