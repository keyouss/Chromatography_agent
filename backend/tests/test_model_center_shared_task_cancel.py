from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from chrom_platform.infrastructure.db import Base
from chrom_platform.infrastructure.models import Job, JobAttempt, JobEvent, OutboxEvent, Project
from chrom_platform.artifact_core.model_package_manifest import EXPECTED_MEMBERS
from chrom_platform.modules.model_center.asset_verifier import ModelPackageAssetVerifier
from chrom_platform.modules.model_center.errors import ModelCenterConflictError
from chrom_platform.modules.model_center.persistence import ModelCenterBase, SQLAlchemyModelCenterRepository
from chrom_platform.modules.model_center.schemas import ModelRegistration, now_utc
from chrom_platform.modules.model_center.worker import RegistrationValidationWorker


@pytest.fixture()
def session_factory():
    db_path = Path(__file__).with_name(f".shared-task-{uuid4().hex}.sqlite3")
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _foreign_keys(dbapi_connection, _):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine, tables=[
        Project.__table__, Job.__table__, JobAttempt.__table__, JobEvent.__table__, OutboxEvent.__table__,
    ])
    ModelCenterBase.metadata.create_all(engine)
    try:
        yield lambda: Session(engine, expire_on_commit=False)
    finally:
        engine.dispose()
        db_path.unlink(missing_ok=True)


def _seed(factory, *, queued=False):
    now = now_utc()
    execute_id = "execute-1"
    cancel_id = "cancel-1"
    with factory() as session:
        session.add(Project(id="project-1", slug="project-1", name="Project"))
        session.flush()
        session.add(Job(
            id="job-1", project_id="project-1", owner_id="owner-1",
            kind="model_registration_validation",
            resource_ref={"type": "registration", "id": "registration-1"},
            status="QUEUED" if queued else "RUNNING", attempt=1, progress={}, cancelable=True,
            retryable=True, resume_mode="none", created_at=now, updated_at=now,
            started_at=now, deadline_at=now + timedelta(minutes=10),
            revision=1, etag='"1"',
        ))
        session.add(JobAttempt(
            id="attempt-1", job_id="job-1", attempt=1, status="QUEUED" if queued else "RUNNING",
            resume_mode="none", execution_token="token-1", fencing_token=1,
            started_at=now,
        ))
        session.add_all([
            OutboxEvent(
                dispatch_id=execute_id, aggregate_type="job", aggregate_id="job-1",
                event_type="job.execute",
                payload={"job_id": "job-1", "attempt": 1, "dispatch_id": execute_id},
                status="PROCESSING", attempts=1, available_at=now, created_at=now,
                lease_owner="worker-1", lease_token=1, lease_expires_at=now + timedelta(minutes=1),
            ),
            OutboxEvent(
                dispatch_id=cancel_id, aggregate_type="job", aggregate_id="job-1",
                event_type="job.cancel",
                payload={"job_id": "job-1", "attempt": 1, "dispatch_id": cancel_id},
                status="PROCESSING", attempts=1, available_at=now, created_at=now,
                lease_owner="worker-2", lease_token=1, lease_expires_at=now + timedelta(minutes=1),
            ),
        ])
        session.add(JobEvent(
            id="event-queued", job_id="job-1", stream_id="job-1", seq=0,
            type="job.queued", schema_version="1.0", data={"attempt": 1}, timestamp=now,
        ))
        session.flush()
        repo = SQLAlchemyModelCenterRepository(session)
        repo.save_registration(ModelRegistration(
            id="registration-1", project_id="project-1", revision=1,
            config_hash="a" * 64, etag='"1"', status="VALIDATING",
            source={"kind": "external_package", "artifact_id": "artifact-1"},
            name="candidate", task_type="gc", job_id="job-1", attempt=1,
            dispatch_id=execute_id, created_at=now, updated_at=now,
        ))
        session.commit()
    return execute_id, cancel_id


def _cancel_request(factory):
    now = now_utc()
    with factory() as session:
        job = session.get(Job, "job-1")
        job.status = "CANCEL_REQUESTED"
        next_seq = max(session.scalars(select(JobEvent.seq).where(JobEvent.job_id == job.id)).all(), default=-1) + 1
        # JobService's cancel request changes Job but leaves the current
        # JobAttempt RUNNING until the worker/cancel handler confirms stop.
        session.add(JobEvent(
            id=f"event-cancel-requested-{next_seq}", job_id=job.id, stream_id=job.id, seq=next_seq,
            type="job.cancel_requested", schema_version="1.0",
            data={"attempt": 1}, timestamp=now,
        ))
        session.commit()


def _confirmed_termination():
    return {
        "started": True, "never_started": False, "confirmed": True,
        "process_id": 812, "creation_filetime": 100, "exit_filetime": 200,
        "exit_code": 1, "job_active_processes": 0,
        "cleanup_completed": True, "output_drained": True, "stop_cause": "cancelled",
    }


def _unconfirmed_termination():
    return {
        "started": True, "never_started": False, "confirmed": False,
        "process_id": 812, "creation_filetime": 100, "exit_filetime": None,
        "exit_code": None, "job_active_processes": 1,
        "cleanup_completed": False, "output_drained": False, "stop_cause": "cancelled",
    }


def test_fresh_cancel_reader_binds_current_job_attempt_and_execute_dispatch(session_factory):
    execute_id, _ = _seed(session_factory)
    with session_factory() as session:
        repo = SQLAlchemyModelCenterRepository(session, fresh_session_factory=session_factory)
        registration = repo.get_registration("project-1", "registration-1")
        assert repo.model_package_cancel_state(registration, execute_dispatch_id=execute_id) == "RUNNING"

    _cancel_request(session_factory)
    with session_factory() as session:
        repo = SQLAlchemyModelCenterRepository(session, fresh_session_factory=session_factory)
        registration = repo.get_registration("project-1", "registration-1")
        assert repo.model_package_cancel_state(registration, execute_dispatch_id=execute_id) == "CANCEL_REQUESTED"
        with pytest.raises(ModelCenterConflictError):
            repo.model_package_cancel_state(registration, execute_dispatch_id="stale-dispatch")


def test_unconfirmed_child_stop_never_cancels_registration_or_job(session_factory):
    execute_id, cancel_id = _seed(session_factory)
    _cancel_request(session_factory)
    with session_factory() as session:
        repo = SQLAlchemyModelCenterRepository(session, fresh_session_factory=session_factory)
        registration = repo.get_registration("project-1", "registration-1")
        repo.record_model_package_termination(
            registration, execute_dispatch_id=execute_id, request_ref="request-1",
            request_sha256="b" * 64, termination=_unconfirmed_termination(),
        )
        session.commit()

    with session_factory() as session:
        repo = SQLAlchemyModelCenterRepository(session, fresh_session_factory=session_factory)
        worker = RegistrationValidationWorker(repo)
        with pytest.raises(ModelCenterConflictError, match="TERMINATION_UNCONFIRMED"):
            worker.cancel("project-1", "registration-1", attempt=1, dispatch_id=cancel_id)
        assert repo.get_registration("project-1", "registration-1").status == "VALIDATING"
        assert session.get(Job, "job-1").status == "CANCEL_REQUESTED"


def test_confirmed_receipt_allows_current_cancel_dispatch_to_finish(session_factory):
    execute_id, cancel_id = _seed(session_factory)
    _cancel_request(session_factory)
    with session_factory() as session:
        repo = SQLAlchemyModelCenterRepository(session, fresh_session_factory=session_factory)
        registration = repo.get_registration("project-1", "registration-1")
        repo.record_model_package_termination(
            registration, execute_dispatch_id=execute_id, request_ref="request-2",
            request_sha256="c" * 64, termination=_confirmed_termination(),
        )
        session.commit()

    with session_factory() as session:
        repo = SQLAlchemyModelCenterRepository(session, fresh_session_factory=session_factory)
        result = RegistrationValidationWorker(repo).cancel(
            "project-1", "registration-1", attempt=1, dispatch_id=cancel_id,
        )
        session.commit()
        assert result.status == "CANCELED"
        assert session.get(Job, "job-1").status == "CANCELED"
        assert session.scalar(select(JobAttempt).where(JobAttempt.job_id == "job-1", JobAttempt.attempt == 1)).status == "CANCELED"


def test_worker_passes_fresh_cancel_callback_and_persists_confirmed_child_stop(session_factory):
    execute_id, cancel_id = _seed(session_factory, queued=True)

    class Verifier:
        calls = 0
        def verify(self, *, project_id, source, task_type, operation_key, cancelled, check_fence=None):
            session.commit()  # mirrors worker_cli's commit-before-long-call boundary
            self.calls += 1
            assert cancelled() is False
            _cancel_request(session_factory)
            assert cancelled() is True
            receipt = _confirmed_termination()
            receipt.update(request_ref="request-worker", request_sha256="e" * 64)
            return {
                "valid": False,
                "error_code": "MODEL_PACKAGE_VALIDATION_CANCELLED",
                "_execution_receipt": {
                    "request_ref": "request-worker",
                    "request_sha256": "e" * 64,
                    "termination": receipt,
                    "executor_status": "CANCELLED",
                    "executor_error_code": "CANCELLED",
                },
            }

    with session_factory() as session:
        repo = SQLAlchemyModelCenterRepository(session, fresh_session_factory=session_factory)
        registration = repo.get_registration("project-1", "registration-1")
        recorded = []
        record_original = repo.record_model_package_termination
        def record_spy(*args, **kwargs):
            recorded.append(kwargs)
            return record_original(*args, **kwargs)
        repo.record_model_package_termination = record_spy
        verifier = Verifier()
        result = RegistrationValidationWorker(repo, verifier=verifier).execute(
            "project-1", "registration-1", attempt=1, dispatch_id=execute_id,
        )
        session.commit()
        assert verifier.calls == 1
        assert recorded
        assert result.status == "VALIDATING"
        stored_events = session.scalars(select(JobEvent).where(JobEvent.job_id == "job-1", JobEvent.type == "model_package.termination")).all()
        assert len(stored_events) == 1, stored_events
        receipt = repo.model_package_termination_receipt(registration, execute_dispatch_id=execute_id)
        assert receipt is not None
        assert receipt["termination"]["confirmed"] is True

    with session_factory() as session:
        repo = SQLAlchemyModelCenterRepository(session, fresh_session_factory=session_factory)
        result = RegistrationValidationWorker(repo).cancel(
            "project-1", "registration-1", attempt=1, dispatch_id=cancel_id,
        )
        session.commit()
        assert result.status == "CANCELED"


@pytest.mark.parametrize("raise_after_cancel", [False, True])
def test_called_verifier_without_receipt_never_fabricates_never_started(session_factory, raise_after_cancel):
    execute_id, cancel_id = _seed(session_factory)

    class MissingReceiptVerifier:
        def verify(self, *, project_id, source, task_type, operation_key, cancelled, check_fence=None):
            _cancel_request(session_factory)
            assert cancelled() is True
            if raise_after_cancel:
                raise RuntimeError("synthetic post-invocation failure")
            return {"valid": True, "checks": []}

    with session_factory() as session:
        repo = SQLAlchemyModelCenterRepository(session, fresh_session_factory=session_factory)
        worker = RegistrationValidationWorker(repo, verifier=MissingReceiptVerifier())
        if raise_after_cancel:
            with pytest.raises(RuntimeError, match="synthetic post-invocation failure"):
                worker.execute("project-1", "registration-1", attempt=1, dispatch_id=execute_id)
            result = repo.get_registration("project-1", "registration-1")
        else:
            result = worker.execute("project-1", "registration-1", attempt=1, dispatch_id=execute_id)
        session.commit()
        assert result.status == "VALIDATING"
        assert repo.model_package_termination_receipt(
            result, execute_dispatch_id=execute_id,
        ) is None
        assert session.scalar(
            select(JobEvent).where(
                JobEvent.job_id == "job-1", JobEvent.type == "model_package.termination",
            )
        ) is None

    with session_factory() as session:
        repo = SQLAlchemyModelCenterRepository(session, fresh_session_factory=session_factory)
        with pytest.raises(ModelCenterConflictError, match="TERMINATION_PENDING"):
            RegistrationValidationWorker(repo).cancel(
                "project-1", "registration-1", attempt=1, dispatch_id=cancel_id,
            )
        assert repo.get_registration("project-1", "registration-1").status == "VALIDATING"


def test_preverify_cancel_requested_for_running_attempt_is_not_never_started(session_factory):
    execute_id, cancel_id = _seed(session_factory)
    _cancel_request(session_factory)

    class MustNotRunVerifier:
        def verify(self, **_kwargs):
            raise AssertionError("cancel-requested running attempt must not be treated as prelaunch")

    with session_factory() as session:
        repo = SQLAlchemyModelCenterRepository(session, fresh_session_factory=session_factory)
        worker = RegistrationValidationWorker(repo, verifier=MustNotRunVerifier())
        registration = worker.execute("project-1", "registration-1", attempt=1, dispatch_id=execute_id)
        session.commit()
        assert registration.status == "VALIDATING"
        assert repo.model_package_termination_receipt(
            registration, execute_dispatch_id=execute_id,
        ) is None

    with session_factory() as session:
        repo = SQLAlchemyModelCenterRepository(session, fresh_session_factory=session_factory)
        with pytest.raises(ModelCenterConflictError, match="TERMINATION_PENDING"):
            RegistrationValidationWorker(repo).cancel(
                "project-1", "registration-1", attempt=1, dispatch_id=cancel_id,
            )


def test_missing_artifact_has_bound_preflight_receipt_and_finishes_failed(session_factory):
    execute_id, _ = _seed(session_factory)

    class MissingArtifact:
        def get(self, project_id, artifact_id):
            assert (project_id, artifact_id) == ("project-1", "artifact-1")
            return None

    class UnusedStorage:
        def prepare_scan_target(self, *_args, **_kwargs):
            raise AssertionError("missing artifact must stop before storage")

        def assert_scan_target_unchanged(self, _target):
            raise AssertionError("missing artifact must stop before storage")

    class UnusedScanEvidence:
        def get(self, **_kwargs):
            raise AssertionError("missing artifact must stop before scan lookup")

    class CountingExecutor:
        calls = 0

        def execute(self, *_args, **_kwargs):
            self.calls += 1
            raise AssertionError("preflight rejection must not invoke executor")

    executor = CountingExecutor()
    verifier = ModelPackageAssetVerifier(
        artifacts=MissingArtifact(), storage=UnusedStorage(),
        scan_evidence=UnusedScanEvidence(), executor=executor,
    )
    direct = verifier.verify(
        project_id="project-1", source={"kind": "external_package", "artifact_id": "artifact-1"},
        task_type="gc", operation_key="preflight-operation",
    )
    assert direct["error_code"] == "MODEL_PACKAGE_ARTIFACT_MISSING"
    direct_receipt = direct["_execution_receipt"]
    assert direct_receipt["executor_status"] == "NOT_STARTED"
    assert direct_receipt["termination"]["never_started"] is True
    assert direct_receipt["request_ref"].startswith("preflight:")
    assert "package_path" not in direct_receipt
    assert executor.calls == 0

    with session_factory() as session:
        repo = SQLAlchemyModelCenterRepository(session, fresh_session_factory=session_factory)
        result = RegistrationValidationWorker(repo, verifier=verifier).execute(
            "project-1", "registration-1", attempt=1, dispatch_id=execute_id,
        )
        session.commit()
        assert result.status == "INVALID"
        assert session.get(Job, "job-1").status == "FAILED"
        stored = repo.model_package_termination_receipt(result, execute_dispatch_id=execute_id)
        assert stored is not None
        assert stored["termination"]["never_started"] is True
        assert stored["request_ref"].startswith("preflight:")
        assert executor.calls == 0


def test_executor_exception_after_attempt_never_gets_preflight_proof(session_factory):
    execute_id, cancel_id = _seed(session_factory)
    artifact_sha = "e" * 64
    artifact_id = "artifact-1"
    identity = (41, 53)
    member_rows = [
        {"name": name, "sha256": f"{index + 1:064x}", "size_bytes": 1,
         "verdict": "CLEAN", "profile_id": "approved-profile"}
        for index, name in enumerate(sorted(EXPECTED_MEMBERS))
    ]
    report = {
        "schema_version": "1.0", "project_id": "project-1", "artifact_id": artifact_id,
        "validation_status": "SUCCEEDED", "status": "SUCCEEDED",
        "validation_job_id": "scan-job-2", "scan_attempt_id": "scan-attempt-2",
        "container": {
            "sha256": artifact_sha, "size_bytes": 12, "identity": list(identity),
            "verdict": "CLEAN", "profile_id": "approved-profile",
        },
        "members": member_rows,
    }

    class Artifacts:
        def get(self, project_id, found_artifact_id):
            assert (project_id, found_artifact_id) == ("project-1", artifact_id)
            return SimpleNamespace(
                project_id=project_id, id=artifact_id, kind="model_package", status="AVAILABLE",
                scan_status="PASSED", sha256=artifact_sha, size_bytes=12, storage_key="server-key",
                metadata_json={"validation_report_ref": {"type": "job", "id": "scan-job-2"}},
            )

    class Storage:
        def prepare_scan_target(self, *_args, **_kwargs):
            return SimpleNamespace(path="unused-by-fake-executor", sha256=artifact_sha,
                                   size_bytes=12, identity=identity)

        def assert_scan_target_unchanged(self, _target):
            return None

    class ScanEvidence:
        def get(self, *, project_id, report_ref):
            assert project_id == "project-1"
            assert report_ref == {"type": "job", "id": "scan-job-2"}
            return report

    class RaisingExecutor:
        calls = 0

        def execute(self, _request, *, cancelled=None, check_fence=None):
            self.calls += 1
            _cancel_request(session_factory)
            raise RuntimeError("synthetic executor outcome unknown")

    executor = RaisingExecutor()
    verifier = ModelPackageAssetVerifier(
        artifacts=Artifacts(), storage=Storage(), scan_evidence=ScanEvidence(),
        executor=executor, approved_scan_profile_ids=("approved-profile",),
    )
    with session_factory() as session:
        repo = SQLAlchemyModelCenterRepository(session, fresh_session_factory=session_factory)
        result = RegistrationValidationWorker(repo, verifier=verifier).execute(
            "project-1", "registration-1", attempt=1, dispatch_id=execute_id,
        )
        session.commit()
        assert result.status == "VALIDATING"
        assert executor.calls == 1
        assert repo.model_package_termination_receipt(
            result, execute_dispatch_id=execute_id,
        ) is None
        assert session.scalar(
            select(JobEvent).where(
                JobEvent.job_id == "job-1", JobEvent.type == "model_package.termination",
            )
        ) is None

    with session_factory() as session:
        repo = SQLAlchemyModelCenterRepository(session, fresh_session_factory=session_factory)
        with pytest.raises(ModelCenterConflictError, match="TERMINATION_PENDING"):
            RegistrationValidationWorker(repo).cancel(
                "project-1", "registration-1", attempt=1, dispatch_id=cancel_id,
            )


def test_asset_verifier_preserves_unconfirmed_receipt_on_storage_error():
    artifact_sha = "f" * 64
    artifact_id = "artifact-1"
    identity = (71, 83)
    member_rows = [
        {"name": name, "sha256": f"{index + 1:064x}", "size_bytes": 1,
         "verdict": "CLEAN", "profile_id": "approved-profile"}
        for index, name in enumerate(sorted(EXPECTED_MEMBERS))
    ]
    report = {
        "schema_version": "1.0", "project_id": "project-1", "artifact_id": artifact_id,
        "validation_status": "SUCCEEDED", "status": "SUCCEEDED",
        "validation_job_id": "scan-job-1", "scan_attempt_id": "scan-attempt-1",
        "container": {
            "sha256": artifact_sha, "size_bytes": 10, "identity": list(identity),
            "verdict": "CLEAN", "profile_id": "approved-profile",
        },
        "members": member_rows,
    }

    class Artifacts:
        def get(self, project_id, artifact_id):
            assert (project_id, artifact_id) == ("project-1", "artifact-1")
            return SimpleNamespace(
                project_id=project_id, id=artifact_id, kind="model_package", status="AVAILABLE",
                scan_status="PASSED", sha256=artifact_sha, size_bytes=10, storage_key="server-key",
                metadata_json={"validation_report_ref": {"type": "job", "id": "scan-job-1"}},
            )

    class Storage:
        def prepare_scan_target(self, storage_key, *, expected_sha256, expected_size):
            assert (storage_key, expected_sha256, expected_size) == ("server-key", artifact_sha, 10)
            return SimpleNamespace(path="unused-by-fake-executor", sha256=artifact_sha,
                                   size_bytes=10, identity=identity)

        def assert_scan_target_unchanged(self, _target):
            raise OSError("synthetic post-executor storage change")

    class ScanEvidence:
        def get(self, *, project_id, report_ref):
            assert project_id == "project-1"
            assert report_ref == {"type": "job", "id": "scan-job-1"}
            return report

    expected_request_ref = "request-unconfirmed"
    expected_request_hash = "9" * 64
    termination = {
        "request_ref": expected_request_ref,
        "request_sha256": expected_request_hash,
        "started": True, "never_started": False, "confirmed": False,
        "process_id": 812, "creation_filetime": 100, "exit_filetime": None,
        "exit_code": None, "job_active_processes": 1, "output_drained": False,
        "cleanup_completed": False, "stop_cause": "cancelled",
    }

    class Executor:
        def execute(self, request, *, cancelled=None, check_fence=None):
            assert request["op"] == "verify_gc_safetensors"
            return {
                "request_ref": expected_request_ref,
                "request_sha256": expected_request_hash,
                "status": "FAILED", "error_code": "TERMINATION_UNCONFIRMED",
                "termination": termination,
            }

    verifier = ModelPackageAssetVerifier(
        artifacts=Artifacts(), storage=Storage(), scan_evidence=ScanEvidence(),
        executor=Executor(), approved_scan_profile_ids=("approved-profile",),
    )
    result = verifier.verify(
        project_id="project-1", source={"kind": "external_package", "artifact_id": artifact_id},
        task_type="gc", operation_key="synthetic-operation",
    )
    receipt = result.get("_execution_receipt")
    assert result["valid"] is False
    assert isinstance(receipt, dict)
    assert receipt["executor_error_code"] == "TERMINATION_UNCONFIRMED"
    assert receipt["request_ref"] == expected_request_ref
    assert receipt["request_sha256"] == expected_request_hash
    assert receipt["termination"]["confirmed"] is False


def test_queued_cancel_records_never_started_receipt_and_finishes(session_factory):
    execute_id, cancel_id = _seed(session_factory, queued=True)
    with session_factory() as session:
        session.get(Job, "job-1").status = "CANCELED"
        session.add(JobEvent(
            id="event-canceled", job_id="job-1", stream_id="job-1", seq=1,
            type="job.canceled", schema_version="1.0", data={"attempt": 1}, timestamp=now_utc(),
        ))
        session.commit()

    with session_factory() as session:
        events_before = session.scalar(
            select(func.count()).select_from(JobEvent).where(
                JobEvent.job_id == "job-1", JobEvent.type.like("job.%")
            )
        )
        repo = SQLAlchemyModelCenterRepository(session, fresh_session_factory=session_factory)
        registration = repo.get_registration("project-1", "registration-1")
        assert repo.model_package_cancel_state(registration, execute_dispatch_id=execute_id) == "NEVER_STARTED"
        result = RegistrationValidationWorker(repo).cancel(
            "project-1", "registration-1", attempt=1, dispatch_id=cancel_id,
        )
        session.commit()
        assert result.status == "CANCELED"
        assert session.scalar(
            select(JobAttempt).where(JobAttempt.job_id == "job-1", JobAttempt.attempt == 1)
        ).status == "CANCELED"
        assert session.scalar(
            select(func.count()).select_from(JobEvent).where(
                JobEvent.job_id == "job-1", JobEvent.type.like("job.%")
            )
        ) == events_before
        stored = repo.model_package_termination_receipt(registration, execute_dispatch_id=execute_id)
        assert stored["termination"]["never_started"] is True


def test_fresh_cancel_commit_defeats_expire_on_commit_false_terminal_race(session_factory):
    _seed(session_factory)
    with session_factory() as session:
        repo = SQLAlchemyModelCenterRepository(session, fresh_session_factory=session_factory)
        registration = repo.get_registration("project-1", "registration-1")
        cached_job = session.get(Job, "job-1")
        assert cached_job.status == "RUNNING"
        _cancel_request(session_factory)
        assert cached_job.status == "RUNNING"  # intentionally stale identity-map value
        result = repo.sync_model_lifecycle(registration, "SUCCEEDED", result_ref={"type": "model_registration", "id": registration.id})
        assert result is None
        session.rollback()
    with session_factory() as session:
        assert session.get(Job, "job-1").status == "CANCEL_REQUESTED"


def test_duplicate_stop_receipt_is_idempotent_and_old_attempt_is_rejected(session_factory):
    execute_id, _ = _seed(session_factory)
    _cancel_request(session_factory)
    with session_factory() as session:
        repo = SQLAlchemyModelCenterRepository(session, fresh_session_factory=session_factory)
        registration = repo.get_registration("project-1", "registration-1")
        kwargs = dict(
            execute_dispatch_id=execute_id, request_ref="request-3",
            request_sha256="d" * 64, termination=_confirmed_termination(),
        )
        repo.record_model_package_termination(registration, **kwargs)
        repo.record_model_package_termination(registration, **kwargs)
        session.commit()
        assert len(session.scalars(select(JobEvent).where(JobEvent.job_id == "job-1", JobEvent.type == "model_package.termination")).all()) == 1
        assert repo.model_package_termination_receipt(registration, execute_dispatch_id=execute_id)["request_ref"] == "request-3"

    with session_factory() as session:
        job = session.get(Job, "job-1")
        job.attempt = 2
        session.commit()
    with session_factory() as session:
        repo = SQLAlchemyModelCenterRepository(session, fresh_session_factory=session_factory)
        registration = repo.get_registration("project-1", "registration-1")
        with pytest.raises(ModelCenterConflictError, match="STALE"):
            repo.model_package_cancel_state(registration, execute_dispatch_id=execute_id)


def test_stale_cached_outbox_lease_cannot_project_terminal_state(session_factory):
    execute_id, _ = _seed(session_factory)
    claim = SimpleNamespace(
        dispatch_id=execute_id, lease_owner="worker-1", lease_token=1,
    )
    with session_factory() as session:
        repo = SQLAlchemyModelCenterRepository(session, fresh_session_factory=session_factory)
        registration = repo.get_registration("project-1", "registration-1")
        cached_dispatch = session.scalar(
            select(OutboxEvent).where(OutboxEvent.dispatch_id == execute_id)
        )
        assert (cached_dispatch.lease_owner, cached_dispatch.lease_token) == ("worker-1", 1)
        # End the read transaction while retaining the identity-map object.
        # Production uses expire_on_commit=False, so it remains stale below.
        session.commit()

        with session_factory() as other:
            current = other.scalar(
                select(OutboxEvent).where(OutboxEvent.dispatch_id == execute_id)
            )
            current.lease_owner = "worker-2"
            current.lease_token = 2
            other.commit()

        assert (cached_dispatch.lease_owner, cached_dispatch.lease_token) == ("worker-1", 1)
        with pytest.raises(ModelCenterConflictError, match="lease lost"):
            repo.sync_model_lifecycle(
                registration, "SUCCEEDED",
                result_ref={"type": "model_registration", "id": registration.id},
                lease_guard=SimpleNamespace(claim=claim),
            )
        session.rollback()

    with session_factory() as verify:
        assert verify.get(Job, "job-1").status == "RUNNING"
        stored_registration = SQLAlchemyModelCenterRepository(verify).get_registration(
            "project-1", "registration-1"
        )
        assert stored_registration.status == "VALIDATING"
        assert verify.scalar(
            select(func.count()).select_from(JobEvent).where(
                JobEvent.job_id == "job-1", JobEvent.type == "model_package.termination"
            )
        ) == 0
