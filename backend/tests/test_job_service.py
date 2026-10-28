from __future__ import annotations

from contextlib import contextmanager

import pytest

from chrom_platform.core.dto import ActorContext
from chrom_platform.task_core.repository import SQLAlchemyTaskRepository
from chrom_platform.task_core.schemas import JobCreateRequest
from chrom_platform.task_core.service import JobService, OperationContext, TaskError
from chrom_platform.task_core.state import JobStatus
from chrom_platform.infrastructure.models import IdempotencyRecord
from sqlalchemy.exc import IntegrityError


class FakeRepo:
    def __init__(self):
        self.rows = []
        self.commits = 0
        self.tx_rows = []
        self.outer_rollback_called = False
        self.savepoint_rollback_called = False

    def rollback(self):
        self.outer_rollback_called = True

    @contextmanager
    def begin_nested(self):
        try:
            yield self
        except Exception:
            self.savepoint_rollback_called = True
            raise

    @contextmanager
    def transaction(self, session=None):
        self.tx_rows.append([])
        try:
            yield self
            self.commits += 1
        finally:
            pass

    def add(self, _session, row):
        self.rows.append(row)
        self.tx_rows[-1].append(row)

    def flush(self, _session):
        return None

    def complete_idempotency(self, row, *, status, result, status_code, completed_at):
        row.status = status
        row.result_resource_ref = result
        row.result_status_code = status_code
        row.completed_at = completed_at

    def get_job(self, _session, job_id, *, for_update=False):
        return next((x for x in self.rows if getattr(x, "id", None) == job_id and x.__class__.__name__ == "Job"), None)

    def get_idempotency(self, _session, scope, *, for_update=False):
        return next((x for x in self.rows if x.__class__.__name__ == "IdempotencyRecord" and (x.actor_id, x.project_id, x.method, x.route_identity, x.key) == (scope.actor_id, scope.project_id, scope.method, scope.route_identity, scope.key)), None)

    def get_attempts(self, _session, job_id, *, limit=None, after_attempt=None, after_id=None):
        rows = [x for x in self.rows if x.__class__.__name__ == "JobAttempt" and x.job_id == job_id]
        rows.sort(key=lambda x: (x.attempt, x.id))
        if after_attempt is not None and after_id is not None:
            rows = [x for x in rows if (x.attempt, x.id) > (after_attempt, after_id)]
        return rows[:limit] if limit is not None else rows

    def get_events(self, _session, job_id, after_seq, limit):
        return [x for x in self.rows if x.__class__.__name__ == "JobEvent" and x.job_id == job_id and x.seq > after_seq][:limit]

    def get_confirmation(self, _session, confirmation_id, *, for_update=False):
        return next((x for x in self.rows if x.__class__.__name__ == "Confirmation" and x.id == confirmation_id), None)

    def list_jobs(self, _session, project_id, *, status=None, kind=None, limit=None, after_created_at=None, after_id=None):
        rows = [x for x in self.rows if x.__class__.__name__ == "Job" and x.project_id == project_id and (status is None or x.status == status) and (kind is None or x.kind == kind)]
        rows.sort(key=lambda x: (x.created_at, x.id), reverse=True)
        if after_created_at is not None and after_id is not None:
            rows = [x for x in rows if (x.created_at, x.id) < (after_created_at, after_id)]
        return rows[:limit] if limit is not None else rows

    def next_event_seq(self, _session, job_id):
        seqs = [x.seq for x in self.rows if x.__class__.__name__ == "JobEvent" and x.job_id == job_id]
        return max(seqs, default=-1) + 1


class FakePolicy:
    def __init__(self, *, requires_confirmation=False):
        self.requires_confirmation = requires_confirmation

    def required_permission(self, operation, *, kind, resource_ref, action=None):
        return f"business:{operation}"

    def authorize_resource(self, _ctx, _target, *, action):
        return True

    def retry_requires_confirmation(self, *, kind, resource_ref):
        return self.requires_confirmation

    def resolve_target(self, _ctx, resource_ref, action):
        from chrom_platform.task_core.service import TargetSnapshot

        return TargetSnapshot("p1", resource_ref, 1, "a" * 64, "etag-1", "p1")


def ctx(projects=("p1",)):
    return OperationContext(ActorContext(actor_id="a1", tenant_id=None, project_ids=list(projects), auth_method="deployment_default", permissions=["*"], policy_version="p1"))


def request(kind="training"):
    return JobCreateRequest(kind=kind, resource_ref={"type": "experiment", "id": "e1"}, retryable=True)


def test_create_is_idempotent_and_outbox_is_pending():
    repo = FakeRepo()
    result = JobService(repo, policy=FakePolicy(), queue_available=False).create(ctx(), "p1", request(), idempotency_key="k1")
    assert result.status == "QUEUED"
    assert result.execution_available is False
    assert [x for x in repo.rows if x.__class__.__name__ == "OutboxEvent"][0].status == "PENDING"
    replay = JobService(repo, policy=FakePolicy(), queue_available=False).create(ctx(), "p1", request(), idempotency_key="k1")
    assert replay.replayed is True
    assert replay.id == result.id
    with pytest.raises(TaskError) as exc:
        JobService(repo, policy=FakePolicy()).create(ctx(), "p1", request("prediction"), idempotency_key="k1")
    assert exc.value.code == "IDEMPOTENCY_KEY_REUSED"


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
def test_get_job_action_flags_follow_state_machine(status, expected_cancelable, expected_retryable):
    repo = FakeRepo()
    service = JobService(repo, policy=FakePolicy())
    created = service.create(ctx(), "p1", request(), idempotency_key="create")
    row = repo.get_job(None, created.id)
    row.status = status.value

    view = service.get(ctx(), created.id)

    assert view.status is status
    assert view.cancelable is expected_cancelable
    assert view.retryable is expected_retryable


def test_job_view_idempotency_snapshot_action_flags_match_snapshot_status():
    from chrom_platform.task_core.service import _job_view_from_data

    repo = FakeRepo()
    created = JobService(repo, policy=FakePolicy()).create(ctx(), "p1", request(), idempotency_key="create")
    snapshot = created.model_dump(mode="json")
    snapshot.update(status="SUCCEEDED", cancelable=True, retryable=True)

    view = _job_view_from_data(snapshot)

    assert view.status is JobStatus.SUCCEEDED
    assert view.cancelable is False
    assert view.retryable is False


def test_cancel_requires_if_match_and_rejects_stale_etag():
    repo = FakeRepo()
    service = JobService(repo, policy=FakePolicy())
    created = service.create(ctx(), "p1", request(), idempotency_key="k1")
    with pytest.raises(TaskError) as missing:
        service.cancel(ctx(), created.id, if_match=None, idempotency_key="cancel-1")
    assert missing.value.status_code == 428
    with pytest.raises(TaskError) as stale:
        service.cancel(ctx(), created.id, if_match="old", idempotency_key="cancel-2")
    assert stale.value.status_code == 412
    changed = service.cancel(ctx(), created.id, if_match=created.etag, idempotency_key="cancel-3")
    assert changed.status in {"CANCELED", "CANCEL_REQUESTED"}


def test_project_isolation_is_enforced_before_task_lookup():
    repo = FakeRepo()
    service = JobService(repo, policy=FakePolicy())
    with pytest.raises(TaskError) as exc:
        service.create(ctx(("other",)), "p1", request(), idempotency_key="k1")
    assert exc.value.status_code == 404


def test_missing_policy_is_unavailable_for_read_cancel_and_retry():
    repo = FakeRepo()
    service = JobService(repo)
    created = service.create(ctx(), "p1", request(), idempotency_key="k1")
    with pytest.raises(TaskError) as read_error:
        service.get(ctx(), created.id)
    assert read_error.value.status_code == 503
    with pytest.raises(TaskError) as cancel_error:
        service.cancel(ctx(), created.id, if_match=created.etag, idempotency_key="c1")
    assert cancel_error.value.status_code == 503
    with pytest.raises(TaskError) as retry_error:
        service.retry(ctx(), created.id, if_match=created.etag, idempotency_key="r1")
    assert retry_error.value.status_code == 503


def test_events_page_uses_lookahead_for_exact_limit():
    repo = FakeRepo()
    service = JobService(repo, policy=FakePolicy())
    created = service.create(ctx(), "p1", request(), idempotency_key="k1")
    canceled = service.cancel(ctx(), created.id, if_match=created.etag, idempotency_key="c1")
    page = service.events_page(ctx(), canceled.id, after_seq=-1, limit=1)
    assert len(page.events) == 1
    assert page.has_more is True


def test_idempotency_unique_collision_is_reread_inside_transaction():
    class CollisionRepo(FakeRepo):
        def __init__(self):
            super().__init__()
            from chrom_platform.task_core.service import _hash

            self.existing = IdempotencyRecord(id="idem-existing", actor_id="a1", project_id="p1", method="POST", route_identity="/api/v1/jobs", key="k1", payload_hash=_hash({"project_id": "p1", "request": request().model_dump(mode="json")}), status="FAILED", expires_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc) + __import__("datetime").timedelta(hours=1))
            self.rows.append(self.existing)
            self.collide = True
            self.first_lookup = True

        def flush(self, _session):
            if self.collide:
                self.collide = False
                raise IntegrityError("INSERT", {}, RuntimeError("duplicate"))

        def get_idempotency(self, _session, scope, *, for_update=False):
            if (scope.actor_id, scope.project_id, scope.method, scope.route_identity, scope.key) == (self.existing.actor_id, self.existing.project_id, self.existing.method, self.existing.route_identity, self.existing.key):
                if self.first_lookup:
                    self.first_lookup = False
                    return None
                return self.existing
            return super().get_idempotency(_session, scope, for_update=for_update)

    repo = CollisionRepo()
    result = JobService(repo, policy=FakePolicy()).create(ctx(), "p1", request(), idempotency_key="k1")
    assert result.status == "QUEUED"
    assert repo.savepoint_rollback_called is True
    assert repo.outer_rollback_called is False
    assert any(x.__class__.__name__ == "OutboxEvent" for x in repo.rows)


def test_list_filters_invisible_resources_instead_of_forbidden_whole_page():
    class ItemPolicy(FakePolicy):
        def required_permission(self, operation, *, kind, resource_ref, action=None):
            return f"business:{operation}-{resource_ref['id']}"

    repo = FakeRepo()
    policy = ItemPolicy()
    actor = ActorContext(actor_id="a1", tenant_id=None, project_ids=["p1"], auth_method="deployment_default", permissions=["business:read-e1"], policy_version="p1")
    operation = OperationContext(actor)
    service = JobService(repo, policy=policy)
    service.create(operation, "p1", request(), idempotency_key="one")
    service.create(operation, "p1", JobCreateRequest(kind="training", resource_ref={"type": "experiment", "id": "e2"}), idempotency_key="two")
    page = service.list_page(operation, "p1", limit=50)
    assert [item.resource_ref.id for item in page.jobs] == ["e1"]


def test_job_and_attempt_pages_use_opaque_keyset_cursor():
    repo = FakeRepo()
    service = JobService(repo, policy=FakePolicy())
    first = service.create(ctx(), "p1", request(), idempotency_key="one")
    second = service.create(ctx(), "p1", request("prediction"), idempotency_key="two")
    page = service.list_page(ctx(), "p1", limit=1)
    assert page.has_more is True and page.next_cursor
    following = service.list_page(ctx(), "p1", limit=1, cursor=page.next_cursor)
    assert len(following.jobs) == 1 and following.jobs[0].id != page.jobs[0].id
    failed_row = next(x for x in repo.rows if x.__class__.__name__ == "Job" and x.id == first.id)
    failed_row.status = "FAILED"
    failed_row.retryable = True
    retry = service.retry(ctx(), first.id, if_match=first.etag, idempotency_key="retry")
    attempts = service.attempts_page(ctx(), first.id, limit=1)
    assert attempts.has_more is True and attempts.next_cursor
    assert len(service.attempts_page(ctx(), first.id, limit=1, cursor=attempts.next_cursor).attempts) == 1


def test_list_scans_past_invisible_chunks_without_leaking_or_skipping():
    class ItemPolicy(FakePolicy):
        def required_permission(self, operation, *, kind, resource_ref, action=None):
            return f"business:{operation}-{resource_ref['id']}"

    policy = ItemPolicy()
    repo = FakeRepo()
    actor = ActorContext(actor_id="a1", tenant_id=None, project_ids=["p1"], auth_method="deployment_default", permissions=["business:read-e3", "business:read-e4"], policy_version="p1")
    operation = OperationContext(actor)
    service = JobService(repo, policy=policy)
    for key, resource_id in (("a", "e1"), ("b", "e2"), ("c", "e3"), ("d", "e4")):
        service.create(operation, "p1", JobCreateRequest(kind="training", resource_ref={"type": "experiment", "id": resource_id}), idempotency_key=key)
    first = service.list_page(operation, "p1", limit=1)
    second = service.list_page(operation, "p1", limit=1, cursor=first.next_cursor)
    returned = [item.resource_ref.id for item in first.jobs + second.jobs]
    assert set(returned) == {"e3", "e4"}
    assert len(returned) == len(set(returned)) == 2


def test_sqlalchemy_repository_builds_keyset_job_statement_with_limit():
    from datetime import datetime, timezone

    class StatementSession:
        def __init__(self):
            self.statement = None

        def scalars(self, statement):
            self.statement = statement
            return []

    session = StatementSession()
    repository = SQLAlchemyTaskRepository(lambda: session)
    repository.list_jobs(session, "p1", status="QUEUED", kind="training", limit=3, after_created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), after_id="job-1")
    sql = str(session.statement)
    assert "LIMIT" in sql.upper()
    assert "job.created_at" in sql
