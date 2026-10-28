from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from chrom_platform.infrastructure.db import Base
from chrom_platform.infrastructure.models import ExecutionLedger, Job, JobAttempt, JobEvent, OutboxEvent, Project
from chrom_platform.artifact_core.model_package_manifest import EXPECTED_MEMBERS, FIXED_LOADER_ID
from chrom_platform.modules.model_center.asset_verifier import ModelPackageAssetVerifier
from chrom_platform.modules.model_center.errors import ModelCenterConflictError
from chrom_platform.modules.model_center.package_execution_ledger import SQLAlchemyModelPackageExecutionLedger
from chrom_platform.modules.model_center.persistence import ModelCenterBase, SQLAlchemyModelCenterRepository
from chrom_platform.modules.model_center.schemas import now_utc
from chrom_platform.modules.model_center.worker import RegistrationValidationWorker
from chrom_platform.task_core.outbox_dispatch import LeaseLostError

from test_model_package_execution_ledger import seed_package_job


@pytest.fixture()
def session_factory():
    db_path = Path(__file__).with_name(f".package-recovery-{uuid4().hex}.sqlite3")
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False, "timeout": 15})

    @event.listens_for(engine, "connect")
    def _foreign_keys(dbapi_connection, _):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")
        dbapi_connection.execute("PRAGMA busy_timeout=15000")

    Base.metadata.create_all(engine, tables=[
        Project.__table__, Job.__table__, JobAttempt.__table__, JobEvent.__table__,
        OutboxEvent.__table__, ExecutionLedger.__table__,
    ])
    ModelCenterBase.metadata.create_all(engine)
    try:
        yield lambda: Session(engine, expire_on_commit=False)
    finally:
        engine.dispose()
        db_path.unlink(missing_ok=True)


def _claim(binding, *, dispatch_id=None, owner=None):
    return SimpleNamespace(
        dispatch_id=dispatch_id or binding["execute_dispatch_id"],
        aggregate_type="job", aggregate_id=binding["job_id"],
        project_id=binding["project_id"], resource_ref={
            "type": binding["resource_type"], "id": binding["resource_id"],
        }, lease_owner=owner or binding["lease_owner"], lease_token=1,
    )


class _LeaseGuard:
    def __init__(self, claim, *, lost_at=None):
        self.claim = claim
        self._checks = 0
        self._lost_at = lost_at

    def check(self):
        self._checks += 1
        if self._lost_at is not None and self._checks >= self._lost_at:
            raise LeaseLostError("OUTBOX_LEASE_LOST")


def _artifact(binding):
    names = sorted(EXPECTED_MEMBERS)
    member_hashes = {name: (str(index + 1) * 64) for index, name in enumerate(names)}
    member_sizes = {name: 100 + index for index, name in enumerate(names)}
    return SimpleNamespace(
        id=binding["artifact_id"], project_id=binding["project_id"],
        kind="model_package", status="AVAILABLE", scan_status="PASSED",
        sha256=binding["artifact_sha256"], size_bytes=binding["artifact_size_bytes"],
        storage_key="synthetic-storage-key",
        metadata_json={"validation_report_ref": {"type": "job", "id": "scan-job"}},
        member_hashes=member_hashes, member_sizes=member_sizes,
    )


class _Storage:
    def __init__(self, binding):
        self.binding = binding

    def prepare_scan_target(self, storage_key, *, expected_sha256, expected_size):
        return SimpleNamespace(
            path="synthetic-package-path-never-opened",
            sha256=expected_sha256, size_bytes=expected_size, identity=(12, 34),
        )

    def assert_scan_target_unchanged(self, _target):
        return None


class _ScanEvidence:
    def __init__(self, binding):
        artifact = _artifact(binding)
        self._report = {
            "schema_version": "1.0", "project_id": binding["project_id"],
            "artifact_id": binding["artifact_id"], "validation_status": "SUCCEEDED",
            "status": "SUCCEEDED", "validation_job_id": "scan-job",
            "scan_attempt_id": "scan-attempt-1",
            "container": {
                "sha256": binding["artifact_sha256"],
                "size_bytes": binding["artifact_size_bytes"], "identity": [12, 34],
                "verdict": "CLEAN", "profile_id": "approved-scan-profile",
            },
            "members": [
                {"name": name, "sha256": artifact.member_hashes[name],
                 "size_bytes": artifact.member_sizes[name], "verdict": "CLEAN",
                 "profile_id": "approved-scan-profile"}
                for name in sorted(EXPECTED_MEMBERS)
            ],
        }

    def get(self, *, project_id, report_ref):
        return dict(self._report)


def _valid_evidence(binding):
    names = sorted(EXPECTED_MEMBERS)
    member_hashes = {name: (str(index + 1) * 64) for index, name in enumerate(names)}
    member_sizes = {name: 100 + index for index, name in enumerate(names)}
    checks = [
        {"step": step, "status": "PASSED", "message": "synthetic mechanism fixture"}
        for step in (
            "manifest/hash", "allowlisted loader/format", "dependency/runtime",
            "schema", "load", "golden", "finite values",
        )
    ]
    return {
        "valid": True, "project_id": binding["project_id"],
        "artifact_id": binding["artifact_id"], "task_type": "gc",
        "artifact_sha256": binding["artifact_sha256"],
        "manifest": {"artifact_sha256": binding["artifact_sha256"],
                     "loader_id": FIXED_LOADER_ID, "format": "safetensors"},
        "member_hashes": member_hashes, "member_sizes": member_sizes,
        "checks": checks,
    }


class _FakeParentExecutor:
    def __init__(self, *, fail_sink=False):
        self.fail_sink = fail_sink
        self.stop_receipt = None

    def execute(self, request, *, cancelled=None, check_fence=None, before_launch=None, stop_sink=None):
        ref = f"synthetic-{request['artifact_id']}"
        digest = "9" * 64
        before_launch(ref, digest)
        termination = {
            "request_ref": ref, "request_sha256": digest,
            "started": True, "never_started": False, "confirmed": True,
            "process_id": 1234, "creation_filetime": 1000, "exit_filetime": 2000,
            "exit_code": 1, "job_active_processes": 0, "output_drained": True,
            "cleanup_completed": True, "stop_cause": "completed",
        }
        self.stop_receipt = termination
        if self.fail_sink:
            return {
                "status": "FAILED", "error_code": "STOP_RECEIPT_UNAVAILABLE",
                "request_ref": ref, "request_sha256": digest,
                "termination": termination,
            }
        stop_sink(ref, digest, termination)
        return {
            "status": "SUCCEEDED", "error_code": None,
            "request_ref": ref, "request_sha256": digest,
            "termination": termination, "evidence": _valid_evidence({
                "project_id": request["project_id"], "artifact_id": request["artifact_id"],
                "artifact_sha256": request["expected_sha256"],
            }),
        }


def _make_verifier(binding, factory, *, fail_sink=False):
    executor = _FakeParentExecutor(fail_sink=fail_sink)
    artifacts = SimpleNamespace(get=lambda project_id, artifact_id: _artifact(binding))
    return ModelPackageAssetVerifier(
        artifacts=artifacts, storage=_Storage(binding), scan_evidence=_ScanEvidence(binding),
        executor=executor, allowed_loader_ids=(FIXED_LOADER_ID,),
        approved_scan_profile_ids=("approved-scan-profile",),
    ), executor


def _request_cancel(factory, binding):
    now = now_utc()
    cancel_id = f"cancel-{binding['registration_id']}"
    with factory() as session:
        job = session.get(Job, binding["job_id"])
        job.status = "CANCEL_REQUESTED"
        session.add(OutboxEvent(
            dispatch_id=cancel_id, aggregate_type="job", aggregate_id=binding["job_id"],
            event_type="job.cancel",
            payload={"job_id": binding["job_id"], "attempt": 1, "dispatch_id": cancel_id},
            status="PROCESSING", attempts=1, available_at=now, created_at=now,
            lease_owner="cancel-worker", lease_token=1,
            lease_expires_at=now + timedelta(minutes=2),
        ))
        session.add(JobEvent(
            id=f"cancel-requested-{binding['registration_id']}", job_id=binding["job_id"],
            stream_id=binding["job_id"], seq=0, type="job.cancel_requested",
            schema_version="1.0", data={"attempt": 1}, timestamp=now,
        ))
        session.commit()
    return cancel_id


def test_postreturn_lease_lost_keeps_only_private_confirmed_receipt(session_factory):
    binding = seed_package_job(session_factory, "postreturn")
    ledger = SQLAlchemyModelPackageExecutionLedger(session_factory, _allow_sqlite_for_tests=True)
    verifier, _executor = _make_verifier(binding, session_factory)
    with session_factory() as session:
        repo = SQLAlchemyModelCenterRepository(session, fresh_session_factory=session_factory)
        worker = RegistrationValidationWorker(repo, verifier=verifier, package_recovery=ledger)
        guard = _LeaseGuard(_claim(binding), lost_at=4)
        result = worker.execute(
            binding["project_id"], binding["registration_id"], attempt=1,
            dispatch_id=binding["execute_dispatch_id"], lease_guard=guard,
        )
        assert result.status == "VALIDATING"
        assert session.get(Job, binding["job_id"]).status == "RUNNING"
        assert session.get(JobAttempt, f"attempt-postreturn").status == "RUNNING"
        assert session.scalar(select(JobEvent).where(
            JobEvent.job_id == binding["job_id"], JobEvent.type == "model_package.termination",
        )) is None

    assert ledger.confirmed_stop(
        {**binding}, "synthetic-artifact-fixed", "9" * 64,
    ) is True


def test_new_owner_cancel_consumes_ledger_proof_without_old_event(session_factory):
    binding = seed_package_job(session_factory, "recover-cancel")
    ledger = SQLAlchemyModelPackageExecutionLedger(session_factory, _allow_sqlite_for_tests=True)
    request_ref, request_sha = "synthetic-artifact-fixed", "9" * 64
    ledger.before_launch(binding, request_ref, request_sha)
    termination = {
        "request_ref": request_ref, "request_sha256": request_sha,
        "started": True, "never_started": False, "confirmed": True,
        "process_id": 1234, "creation_filetime": 1000, "exit_filetime": 2000,
        "exit_code": 1, "job_active_processes": 0,
        "output_drained": True, "cleanup_completed": True, "stop_cause": "cancelled",
    }
    ledger.stop_sink(binding, request_ref, request_sha, termination)
    cancel_id = _request_cancel(session_factory, binding)
    with session_factory() as session:
        repo = SQLAlchemyModelCenterRepository(session, fresh_session_factory=session_factory)
        worker = RegistrationValidationWorker(repo, package_recovery=ledger)
        cancel_claim = SimpleNamespace(
            dispatch_id=cancel_id, aggregate_type="job", aggregate_id=binding["job_id"],
            project_id=binding["project_id"], lease_owner="cancel-worker", lease_token=1,
        )
        result = worker.cancel(
            binding["project_id"], binding["registration_id"], attempt=1,
            dispatch_id=cancel_id, lease_guard=_LeaseGuard(cancel_claim),
        )
        assert result.status == "CANCELED"
        assert session.get(Job, binding["job_id"]).status == "CANCELED"
        assert session.get(JobAttempt, "attempt-recover-cancel").status == "CANCELED"
        assert session.scalar(select(JobEvent).where(
            JobEvent.job_id == binding["job_id"], JobEvent.type == "model_package.termination",
        )) is None


def test_failed_stop_sink_does_not_publish_or_authorize_cancellation(session_factory):
    binding = seed_package_job(session_factory, "sink-failed")
    ledger = SQLAlchemyModelPackageExecutionLedger(session_factory, _allow_sqlite_for_tests=True)
    verifier, _executor = _make_verifier(binding, session_factory, fail_sink=True)
    with session_factory() as session:
        repo = SQLAlchemyModelCenterRepository(session, fresh_session_factory=session_factory)
        worker = RegistrationValidationWorker(repo, verifier=verifier, package_recovery=ledger)
        result = worker.execute(
            binding["project_id"], binding["registration_id"], attempt=1,
            dispatch_id=binding["execute_dispatch_id"], lease_guard=_LeaseGuard(_claim(binding)),
        )
        assert result.status == "VALIDATING"
        assert session.scalar(select(JobEvent).where(
            JobEvent.job_id == binding["job_id"], JobEvent.type == "model_package.termination",
        )) is None

    cancel_id = _request_cancel(session_factory, binding)
    with session_factory() as session:
        repo = SQLAlchemyModelCenterRepository(session, fresh_session_factory=session_factory)
        worker = RegistrationValidationWorker(repo, package_recovery=ledger)
        cancel_claim = SimpleNamespace(
            dispatch_id=cancel_id, aggregate_type="job", aggregate_id=binding["job_id"],
            project_id=binding["project_id"], lease_owner="cancel-worker", lease_token=1,
        )
        with pytest.raises(ModelCenterConflictError, match="TERMINATION_PENDING"):
            worker.cancel(
                binding["project_id"], binding["registration_id"], attempt=1,
                dispatch_id=cancel_id, lease_guard=_LeaseGuard(cancel_claim),
            )
        assert session.get(Job, binding["job_id"]).status == "CANCEL_REQUESTED"
        assert session.get(JobAttempt, "attempt-sink-failed").status == "RUNNING"
        assert session.scalar(select(JobEvent).where(
            JobEvent.job_id == binding["job_id"], JobEvent.type == "model_package.termination",
        )) is None

