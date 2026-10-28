from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import hashlib
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from chrom_platform.infrastructure.db import Base
from chrom_platform.infrastructure.models import ExecutionLedger, Job, JobAttempt, OutboxEvent, Project
from chrom_platform.modules.model_center.errors import ModelCenterConflictError
from chrom_platform.modules.model_center.package_execution_ledger import (
    ModelPackageRecoveryError,
    SQLAlchemyModelPackageExecutionLedger,
)
from chrom_platform.modules.model_center.persistence import ModelCenterBase, SQLAlchemyModelCenterRepository
from chrom_platform.modules.model_center.schemas import ModelRegistration, now_utc


@pytest.fixture()
def session_factory():
    db_path = Path(__file__).with_name(f".package-ledger-{uuid4().hex}.sqlite3")
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False, "timeout": 15})

    @event.listens_for(engine, "connect")
    def _foreign_keys(dbapi_connection, _):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")
        dbapi_connection.execute("PRAGMA busy_timeout=15000")

    Base.metadata.create_all(engine, tables=[
        Project.__table__, Job.__table__, JobAttempt.__table__, OutboxEvent.__table__, ExecutionLedger.__table__,
    ])
    ModelCenterBase.metadata.create_all(engine)
    try:
        yield lambda: Session(engine, expire_on_commit=False)
    finally:
        engine.dispose()
        db_path.unlink(missing_ok=True)


def seed_package_job(factory, suffix: str) -> dict[str, object]:
    now = now_utc()
    project_id = "project-ledger"
    job_id = f"job-{suffix}"
    registration_id = f"registration-{suffix}"
    dispatch_id = f"execute-{suffix}"
    with factory() as session:
        if session.get(Project, project_id) is None:
            session.add(Project(id=project_id, slug=project_id, name="Ledger test"))
            session.flush()
        session.add(Job(
            id=job_id, project_id=project_id, owner_id="owner",
            kind="model_registration_validation",
            resource_ref={"type": "model_registration", "id": registration_id},
            status="RUNNING", attempt=1, progress={}, cancelable=True,
            retryable=True, resume_mode="none", created_at=now, updated_at=now,
            started_at=now, deadline_at=now + timedelta(minutes=5),
            revision=1, etag='"1"',
        ))
        session.add(JobAttempt(
            id=f"attempt-{suffix}", job_id=job_id, attempt=1, status="RUNNING",
            resume_mode="none", execution_token=f"token-{suffix}", fencing_token=1,
            started_at=now,
        ))
        session.add(OutboxEvent(
            dispatch_id=dispatch_id, aggregate_type="job", aggregate_id=job_id,
            event_type="job.execute",
            payload={"job_id": job_id, "attempt": 1, "dispatch_id": dispatch_id},
            status="PROCESSING", attempts=1, available_at=now, created_at=now,
            lease_owner=f"worker-{suffix}", lease_token=1,
            lease_expires_at=now + timedelta(minutes=2),
        ))
        repo = SQLAlchemyModelCenterRepository(session)
        repo.save_registration(ModelRegistration(
            id=registration_id, project_id=project_id, revision=1,
            config_hash="a" * 64, etag='"1"', status="VALIDATING",
            source={"kind": "external_package", "artifact_id": "artifact-fixed"},
            name="synthetic candidate", task_type="gc", job_id=job_id,
            attempt=1, dispatch_id=dispatch_id, created_at=now, updated_at=now,
        ))
        session.commit()
    return {
        "project_id": project_id, "registration_id": registration_id,
        "job_id": job_id, "attempt": 1, "execute_dispatch_id": dispatch_id,
        "resource_type": "model_registration", "resource_id": registration_id,
        "artifact_id": "artifact-fixed", "artifact_sha256": "a" * 64,
        "artifact_size_bytes": 64, "lease_owner": f"worker-{suffix}", "lease_token": 1,
    }


def scenario_unconfirmed_global_gate(factory, *, allow_sqlite_for_tests: bool = False) -> None:
    ledger = SQLAlchemyModelPackageExecutionLedger(
        factory, _allow_sqlite_for_tests=allow_sqlite_for_tests,
    )
    first = seed_package_job(factory, "first")
    second = seed_package_job(factory, "second")
    ref, digest = "synthetic-ref-first", "b" * 64
    ledger.before_launch(first, ref, digest)
    ledger.stop_sink(first, ref, digest, {
        "request_ref": ref, "request_sha256": digest,
        "started": False, "never_started": True, "confirmed": False,
        "process_id": None, "creation_filetime": None, "exit_filetime": None,
        "exit_code": None, "job_active_processes": None,
        "output_drained": False, "cleanup_completed": False,
        "stop_cause": "unknown",
    })
    with pytest.raises(ModelPackageRecoveryError, match="RECOVERY_REQUIRED"):
        ledger.before_launch(second, "synthetic-ref-second", "c" * 64)


def scenario_late_receipt_preserves_new_generation(factory, *, allow_sqlite_for_tests: bool = False) -> None:
    ledger = SQLAlchemyModelPackageExecutionLedger(
        factory, _allow_sqlite_for_tests=allow_sqlite_for_tests,
    )
    first = seed_package_job(factory, "old")
    second = seed_package_job(factory, "new")
    ref1, digest1 = "synthetic-ref-old", "d" * 64
    receipt1 = {
        "request_ref": ref1, "request_sha256": digest1,
        "started": True, "never_started": False, "confirmed": True,
        "process_id": 107, "creation_filetime": 1000, "exit_filetime": 2000,
        "exit_code": 1, "job_active_processes": 0,
        "output_drained": True, "cleanup_completed": True,
        "stop_cause": "completed",
    }
    ledger.before_launch(first, ref1, digest1)
    ledger.stop_sink(first, ref1, digest1, receipt1)
    ref2, digest2 = "synthetic-ref-new", "e" * 64
    ledger.before_launch(second, ref2, digest2)
    # Replaying a historical exact proof is idempotent and cannot clear the
    # newly active generation's slot.
    ledger.stop_sink(first, ref1, digest1, receipt1)
    with factory() as session:
        slot = session.scalar(select(ExecutionLedger).where(
            ExecutionLedger.operation_key == "model_package:global_child_slot:v1",
        ))
        assert slot is not None and slot.status == "STARTED"
        assert slot.result_ref["active_ref"] == ref2


def scenario_concurrent_first_slot(factory) -> None:
    ledger = SQLAlchemyModelPackageExecutionLedger(factory)
    bindings = [seed_package_job(factory, "race-a"), seed_package_job(factory, "race-b")]
    barrier = Barrier(2)

    def launch(index: int) -> str:
        barrier.wait(timeout=10)
        try:
            ledger.before_launch(bindings[index], f"race-ref-{index}", str(index + 1) * 64)
            return "started"
        except ModelPackageRecoveryError:
            return "blocked"

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(launch, (0, 1)))
    assert sorted(result) == ["blocked", "started"]


def test_partial_parent_receipt_is_durable_and_blocks_every_job(session_factory):
    scenario_unconfirmed_global_gate(session_factory, allow_sqlite_for_tests=True)
    with session_factory() as session:
        slot = session.scalar(select(ExecutionLedger).where(
            ExecutionLedger.operation_key == "model_package:global_child_slot:v1",
        ))
        invocations = list(session.scalars(select(ExecutionLedger).where(
            ExecutionLedger.operation_type == "model_package.child_invocation",
        )))
        assert slot is not None and slot.job_id is None and slot.status == "UNKNOWN"
        assert len(invocations) == 1 and invocations[0].status == "UNKNOWN"
        assert invocations[0].result_ref["termination"]["job_active_processes"] is None


def test_late_duplicate_stop_proof_cannot_clear_new_active_generation(session_factory):
    scenario_late_receipt_preserves_new_generation(session_factory, allow_sqlite_for_tests=True)


def test_unsupported_sqlite_backend_fails_before_any_launch_intent(session_factory):
    ledger = SQLAlchemyModelPackageExecutionLedger(session_factory)
    bindings = [seed_package_job(session_factory, "sqlite-deny-a"), seed_package_job(session_factory, "sqlite-deny-b")]
    launch_calls = []

    def launch_after_intent(binding, index):
        ledger.before_launch(binding, f"sqlite-deny-ref-{index}", str(index + 1) * 64)
        launch_calls.append(index)

    for index, binding in enumerate(bindings):
        with pytest.raises(ModelPackageRecoveryError, match="BACKEND_UNSUPPORTED"):
            launch_after_intent(binding, index)
    assert launch_calls == []
    with session_factory() as session:
        assert session.scalar(select(ExecutionLedger).where(
            ExecutionLedger.operation_key == "model_package:global_child_slot:v1",
        )) is None
        assert list(session.scalars(select(ExecutionLedger).where(
            ExecutionLedger.operation_type == "model_package.child_invocation",
        ))) == []


@pytest.mark.parametrize(
    "change",
    ["drop_pid", "change_pid", "drop_creation", "change_creation", "change_exit_time", "different_unknown"],
)
def test_unknown_receipt_only_allows_exact_replay_and_preserves_original(change, session_factory):
    ledger = SQLAlchemyModelPackageExecutionLedger(session_factory, _allow_sqlite_for_tests=True)
    binding = seed_package_job(session_factory, f"unknown-merge-{change}")
    ref, digest = f"unknown-merge-{change}-ref", "a" * 64
    original = {
        "request_ref": ref, "request_sha256": digest,
        "started": True, "never_started": False, "confirmed": False,
        "process_id": 107, "creation_filetime": 1000, "exit_filetime": None,
        "exit_code": 259, "job_active_processes": 1,
        "output_drained": False, "cleanup_completed": False,
        "stop_cause": "unknown",
    }
    ledger.before_launch(binding, ref, digest)
    ledger.stop_sink(binding, ref, digest, original)
    ledger.stop_sink(binding, ref, digest, original)
    changed = dict(original)
    if change == "drop_pid":
        changed["process_id"] = None
    elif change == "change_pid":
        changed["process_id"] = 108
    elif change == "drop_creation":
        changed["creation_filetime"] = None
    elif change == "change_creation":
        changed["creation_filetime"] = 1001
    elif change == "change_exit_time":
        changed["exit_filetime"] = 2000
    elif change == "different_unknown":
        changed["job_active_processes"] = None
    with pytest.raises(ModelPackageRecoveryError, match="RECEIPT_CONFLICT"):
        ledger.stop_sink(binding, ref, digest, changed)

    with session_factory() as fresh_session:
        invocation = fresh_session.scalar(select(ExecutionLedger).where(
            ExecutionLedger.operation_key == f"model_package:invocation:v1:{hashlib.sha256(ref.encode()).hexdigest()}",
        ))
        slot = fresh_session.scalar(select(ExecutionLedger).where(
            ExecutionLedger.operation_key == "model_package:global_child_slot:v1",
        ))
        assert invocation is not None and invocation.status == "UNKNOWN"
        assert invocation.result_ref["termination"] == original
        assert slot is not None and slot.status == "UNKNOWN"
        assert slot.result_ref["active_ref"] == ref


def test_unknown_receipt_can_only_upgrade_to_strict_confirmed_proof(session_factory):
    ledger = SQLAlchemyModelPackageExecutionLedger(session_factory, _allow_sqlite_for_tests=True)
    binding = seed_package_job(session_factory, "unknown-upgrade")
    ref, digest = "unknown-upgrade-ref", "b" * 64
    partial = {
        "request_ref": ref, "request_sha256": digest,
        "started": True, "never_started": False, "confirmed": False,
        "process_id": 207, "creation_filetime": 3000, "exit_filetime": None,
        "exit_code": 259, "job_active_processes": 1,
        "output_drained": False, "cleanup_completed": False,
        "stop_cause": "cancelled",
    }
    confirmed = {
        **partial, "confirmed": True, "exit_filetime": 4000, "exit_code": 0,
        "job_active_processes": 0, "output_drained": True, "cleanup_completed": True,
    }
    ledger.before_launch(binding, ref, digest)
    ledger.stop_sink(binding, ref, digest, partial)
    ledger.stop_sink(binding, ref, digest, confirmed)

    with session_factory() as fresh_session:
        invocation = fresh_session.scalar(select(ExecutionLedger).where(
            ExecutionLedger.operation_key == f"model_package:invocation:v1:{hashlib.sha256(ref.encode()).hexdigest()}",
        ))
        slot = fresh_session.scalar(select(ExecutionLedger).where(
            ExecutionLedger.operation_key == "model_package:global_child_slot:v1",
        ))
        assert invocation is not None and invocation.status == "SUCCEEDED"
        assert invocation.result_ref["phase"] == "STOP_CONFIRMED"
        assert invocation.result_ref["termination"] == confirmed
        assert slot is not None and slot.status == "SUCCEEDED"
        assert slot.result_ref["phase"] == "IDLE"


@pytest.mark.parametrize(("bad_table", "bad_engine"), [("job_attempt", None), ("outbox_event", "MyISAM")])
def test_mysql_backend_rejects_missing_or_non_innodb_tables(bad_table, bad_engine):
    class Result:
        def __init__(self, engine):
            self.engine = engine

        def scalar_one_or_none(self):
            return self.engine

    class FakeSession:
        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="mysql"))

        def execute(self, _statement, params):
            return Result(bad_engine if params["table_name"] == bad_table else "InnoDB")

    ledger = SQLAlchemyModelPackageExecutionLedger(lambda: None)
    with pytest.raises(ModelPackageRecoveryError, match="BACKEND_UNSUPPORTED"):
        ledger._require_supported_backend(FakeSession())


def test_mysql_backend_accepts_all_required_innodb_tables():
    class Result:
        def scalar_one_or_none(self):
            return "InnoDB"

    class FakeSession:
        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="mysql"))

        def execute(self, _statement, _params):
            return Result()

    ledger = SQLAlchemyModelPackageExecutionLedger(lambda: None)
    ledger._require_supported_backend(FakeSession())


def test_incomplete_or_contradictory_confirmed_receipt_keeps_launch_gate(session_factory):
    ledger = SQLAlchemyModelPackageExecutionLedger(session_factory, _allow_sqlite_for_tests=True)
    binding = seed_package_job(session_factory, "invalid-proof")
    ref, digest = "invalid-proof-ref", "f" * 64
    ledger.before_launch(binding, ref, digest)
    invalid = {
        "request_ref": ref, "request_sha256": digest,
        "started": True, "never_started": False, "confirmed": True,
        "process_id": 107, "creation_filetime": 1000, "exit_filetime": 2000,
        "exit_code": 259, "job_active_processes": 0,
        "output_drained": True, "cleanup_completed": True,
        "stop_cause": "completed",
    }
    with pytest.raises(ModelPackageRecoveryError, match="TERMINATION_RECEIPT_INVALID"):
        ledger.stop_sink(binding, ref, digest, invalid)
    with session_factory() as session:
        slot = session.scalar(select(ExecutionLedger).where(
            ExecutionLedger.operation_key == "model_package:global_child_slot:v1",
        ))
        assert slot is not None and slot.status == "STARTED"

