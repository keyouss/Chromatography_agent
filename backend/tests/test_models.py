from datetime import datetime, timedelta, timezone

from sqlalchemy import BigInteger, ForeignKeyConstraint, create_engine
from sqlalchemy.exc import IntegrityError
import pytest
from sqlalchemy.orm import Session

from chrom_platform.artifact_core.models import Artifact, Structure
from chrom_platform.core.dto import ActorContext
from chrom_platform.core.idempotency import IdempotencyScope
from chrom_platform.infrastructure.db import Base, create_database_engine, validate_mysql_url
from chrom_platform.infrastructure.models import IdempotencyRecord as IdempotencyRow
from chrom_platform.infrastructure.metadata import Base as MetadataBase
from chrom_platform.infrastructure.models import (
    AuditEvent,
    Confirmation,
    ExecutionLedger,
    IdempotencyRecord,
    Job,
    JobAttempt,
    JobEvent,
    OutboxEvent,
    Project,
)
from chrom_platform.infrastructure.repositories import SQLAlchemyIdempotencyStore, SQLAlchemyProjectReader


def test_m1_metadata_contains_expected_entities_and_mysql_options() -> None:
    expected = {
        "project",
        "artifact",
        "structure",
        "job",
        "job_attempt",
        "job_event",
        "outbox_event",
        "idempotency_record",
        "confirmation",
        "audit_event",
        "execution_ledger",
    }
    assert set(MetadataBase.metadata.tables) == expected
    for table in MetadataBase.metadata.tables.values():
        assert table.dialect_options["mysql"]["engine"] == "InnoDB"
        assert table.dialect_options["mysql"]["charset"] == "utf8mb4"


def test_model_constraints_bind_scope_and_fencing() -> None:
    assert isinstance(Artifact.__table__.c.size_bytes.type, BigInteger)
    assert any(
        isinstance(constraint, ForeignKeyConstraint)
        and constraint.name == "fk_structure_project_artifact"
        for constraint in Structure.__table__.constraints
    )
    assert {"actor_id", "project_id", "route_identity", "key"} <= set(IdempotencyRecord.__table__.c.keys())
    assert "fencing_token" in JobAttempt.__table__.c
    assert "operation_key" in ExecutionLedger.__table__.c
    assert "error_code" in IdempotencyRecord.__table__.c
    assert "confirmation_id" in AuditEvent.__table__.c
    assert any(constraint.name == "uq_idempotency_scope_key" for constraint in IdempotencyRecord.__table__.constraints)


def test_database_url_is_mysql_only() -> None:
    assert validate_mysql_url("mysql+pymysql://user:pass@localhost/db").get_backend_name() == "mysql"
    try:
        create_database_engine("sqlite:///:memory:")
    except ValueError as exc:
        assert "MySQL" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("SQLite must never be accepted as the persistence backend")


class _ScalarResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return list(self.rows)


class _FakeSession:
    def __init__(self, *, scalar_row=None, rows=(), duplicate=False):
        self.scalar_row = scalar_row
        self.rows = rows
        self.duplicate = duplicate
        self.added = []
        self.scalar_calls = 0
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def add(self, row):
        self.added.append(row)

    def flush(self):
        if self.duplicate:
            raise IntegrityError("INSERT", {}, RuntimeError("duplicate"))

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def scalar(self, _statement):
        self.scalar_calls += 1
        return self.scalar_row

    def scalars(self, _statement):
        return _ScalarResult(self.rows)

    def close(self):
        self.closed = True


def _scope() -> IdempotencyScope:
    return IdempotencyScope("actor-1", "project-1", "POST", "/api/v1/jobs", "k-1")


def test_project_reader_uses_real_db_rows_and_actor_project_ids() -> None:
    project = Project(id="project-1", slug="p1", name="P1", status="ACTIVE", revision=1)
    session = _FakeSession(rows=[project])
    actor = ActorContext(
        actor_id="actor-1",
        tenant_id=None,
        project_ids=["project-1", "missing"],
        auth_method="deployment_default",
        permissions=["*"],
        policy_version="p1",
    )
    reader = SQLAlchemyProjectReader(lambda: session)
    assert reader.list_accessible(actor, limit=10, after_id=None) == [project]
    assert reader.get_accessible(actor, "missing") is None
    assert session.closed


def test_project_reader_keyset_pages_limit_plus_one_and_scope_isolation() -> None:
    # This exercises ordinary SQLAlchemy keyset SELECT semantics only. It does
    # not claim SQLite coverage for MySQL locks, JSON, or FULLTEXT behavior.
    engine = create_engine("sqlite:///:memory:")
    Project.__table__.create(engine)
    with Session(engine) as seed:
        seed.add_all(
            Project(id=f"p{i:04d}", slug=f"slug-{i:04d}", name=f"P{i:04d}", status="ACTIVE", revision=1)
            for i in range(1, 206)
        )
        seed.commit()

    actor = ActorContext(
        actor_id="actor-1",
        tenant_id=None,
        project_ids=[*(f"p{i:04d}" for i in range(1, 206)), "p0051x"],
        auth_method="deployment_default",
        permissions=["*"],
        policy_version="p1",
    )
    reader = SQLAlchemyProjectReader(lambda: Session(engine))
    first = reader.list_accessible(actor, limit=51, after_id=None)
    assert len(first) == 51
    assert first[0].id == "p0001"
    assert first[-1].id == "p0051"

    # A row inserted after the cursor is visible on a later page; deleting a
    # row before that page does not shift the cursor into a duplicate.
    with Session(engine) as change:
        change.add(Project(id="p0051x", slug="slug-0051x", name="inserted", status="ACTIVE", revision=1))
        change.query(Project).filter(Project.id == "p0052").delete()
        change.commit()

    pages = [first]
    after_id = first[-1].id
    while True:
        page = reader.list_accessible(actor, limit=51, after_id=after_id)
        if not page:
            break
        pages.append(page)
        after_id = page[-1].id
    ids = [row.id for page in pages for row in page]
    assert len(ids) == len(set(ids))
    assert "p0052" not in ids
    assert "p0051x" in ids
    assert len(ids) == 205

    isolated = ActorContext(
        actor_id="actor-2",
        tenant_id=None,
        project_ids=["p0001", "p0100", "p0205"],
        auth_method="deployment_default",
        permissions=["*"],
        policy_version="p1",
    )
    assert [row.id for row in reader.list_accessible(isolated, limit=201, after_id=None)] == [
        "p0001", "p0100", "p0205"
    ]


def test_project_reader_rejects_invalid_limits_and_cursor() -> None:
    reader = SQLAlchemyProjectReader(lambda: _FakeSession())
    actor = ActorContext(
        actor_id="actor-1",
        tenant_id=None,
        project_ids=["project-1"],
        auth_method="deployment_default",
        permissions=["*"],
        policy_version="p1",
    )
    with pytest.raises(ValueError):
        reader.list_accessible(actor, limit=0, after_id=None)
    with pytest.raises(ValueError):
        reader.list_accessible(actor, limit=202, after_id=None)
    with pytest.raises(ValueError):
        reader.list_accessible(actor, limit=1, after_id="")


def test_idempotency_begin_inserts_before_any_read_and_marks_acquisition() -> None:
    session = _FakeSession()
    store = SQLAlchemyIdempotencyStore(lambda: session)
    result = store.begin(_scope(), "a" * 64)
    assert result.acquired is True
    assert result.record.status == "IN_PROGRESS"
    assert session.scalar_calls == 0
    assert session.commits == 1


def test_idempotency_duplicate_reads_existing_and_complete_locks_record() -> None:
    row = IdempotencyRow(
        id="idem-1",
        actor_id="actor-1",
        project_id="project-1",
        method="POST",
        route_identity="/api/v1/jobs",
        key="k-1",
        payload_hash="a" * 64,
        status="IN_PROGRESS",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    session = _FakeSession(scalar_row=row, duplicate=True)
    store = SQLAlchemyIdempotencyStore(lambda: session)
    result = store.begin(_scope(), "a" * 64)
    assert result.acquired is False
    assert result.record.payload_hash == "a" * 64
    assert session.rollbacks == 1
    store.complete(_scope(), "a" * 64, 201, {"id": "resource-1"})
    assert row.status == "COMPLETED"
    assert row.result_status_code == 201
    assert row.result_resource_ref == {"id": "resource-1"}


def test_idempotency_duplicate_with_different_hash_is_deferred_to_application() -> None:
    row = IdempotencyRow(
        id="idem-2",
        actor_id="actor-1",
        project_id="project-1",
        method="POST",
        route_identity="/api/v1/jobs",
        key="k-1",
        payload_hash="a" * 64,
        status="IN_PROGRESS",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    session = _FakeSession(scalar_row=row, duplicate=True)
    result = SQLAlchemyIdempotencyStore(lambda: session).begin(_scope(), "b" * 64)
    assert result.acquired is False
    assert result.record.payload_hash == "a" * 64


def test_idempotency_failed_record_is_retryable_and_fail_is_public_code_only() -> None:
    row = IdempotencyRow(
        id="idem-3",
        actor_id="actor-1",
        project_id="project-1",
        method="POST",
        route_identity="/api/v1/jobs",
        key="k-1",
        payload_hash="a" * 64,
        status="IN_PROGRESS",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    session = _FakeSession(scalar_row=row)
    store = SQLAlchemyIdempotencyStore(lambda: session)
    store.fail(_scope(), "a" * 64, "VALIDATION_FAILED")
    assert row.status == "FAILED"
    assert row.error_code == "VALIDATION_FAILED"
    store.fail(_scope(), "a" * 64, "VALIDATION_FAILED")
    with pytest.raises(ValueError):
        store.fail(_scope(), "a" * 64, "SELECT_PASSWORD")

    retry_session = _FakeSession(scalar_row=row, duplicate=True)
    retry = SQLAlchemyIdempotencyStore(lambda: retry_session).begin(_scope(), "a" * 64)
    assert retry.acquired is True
    assert row.status == "IN_PROGRESS"
    assert row.error_code is None


def test_idempotency_expired_record_can_be_atomically_reused() -> None:
    row = IdempotencyRow(
        id="idem-4",
        actor_id="actor-1",
        project_id="project-1",
        method="POST",
        route_identity="/api/v1/jobs",
        key="k-1",
        payload_hash="a" * 64,
        status="COMPLETED",
        result_status_code=201,
        result_resource_ref={"id": "old"},
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    session = _FakeSession(scalar_row=row, duplicate=True)
    result = SQLAlchemyIdempotencyStore(lambda: session).begin(_scope(), "b" * 64)
    assert result.acquired is True
    assert result.record.payload_hash == "b" * 64
    assert row.status == "IN_PROGRESS"
    assert row.result_resource_ref is None
    assert row.result_status_code is None
