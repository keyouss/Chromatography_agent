from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from chrom_platform.artifact_core.models import Artifact
from chrom_platform.artifact_core.schemas import UploadCreate
from chrom_platform.artifact_core.service import ArtifactService
from chrom_platform.artifact_core.storage import LocalArtifactStore, StorageContentInvalid
from chrom_platform.artifact_core.uow import SQLAlchemyArtifactUnitOfWork
from chrom_platform.artifact_core.scanner_defender import DefenderCommandScanner, DefenderStatus, ScanEvidence
from chrom_platform.artifact_core.service import ArtifactServiceError
from chrom_platform.artifact_core.validation_worker import ArtifactValidationError, ArtifactValidationWorker
from chrom_platform.core.dto import ActorContext
from chrom_platform.infrastructure.db import Base
from chrom_platform.infrastructure.models import Job, JobEvent, JobAttempt, OutboxEvent, Project
from chrom_platform.task_core.policy import DeploymentTaskPolicy
from chrom_platform.task_core.repository import SQLAlchemyTaskRepository
from chrom_platform.task_core.service import JobService, OperationContext


class FakeScanner:
    def __init__(self, status: str = "PASSED", verdict: str | None = None, on_scan=None) -> None:
        self.status = status
        self.verdict = verdict or ("CLEAN" if status == "PASSED" else "INDETERMINATE")
        self.on_scan = on_scan
        self.calls = []

    def scan(self, **kwargs):
        self.calls.append(kwargs)
        now = datetime.now(timezone.utc)
        evidence = ScanEvidence(
            artifact_id=kwargs["artifact_id"],
            scan_attempt_id=kwargs["scan_attempt_id"],
            input_sha256=kwargs["input_sha256"],
            input_size_bytes=kwargs["input_size_bytes"],
            provider_id="test-provider",
            product_version="test-product",
            engine_version="test-engine",
            signature_version="test-signature",
            signature_updated_at_utc=now,
            started_at_utc=now,
            finished_at_utc=now,
            profile_id="test-approved-profile",
            exit_code=0 if self.status == "PASSED" else None,
            verdict=self.verdict,
            error_code=None,
            output_truncated=False,
        )
        if self.on_scan is not None:
            self.on_scan(kwargs)
        return SimpleNamespace(status=self.status, warning=None, evidence=evidence)


def _database(tmp_path: Path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    @event.listens_for(engine, "connect")
    def _register_char_length(dbapi_connection, _connection_record) -> None:
        dbapi_connection.create_function("CHAR_LENGTH", 1, lambda value: len(value) if value is not None else None)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        session.add(Project(id="p1", slug="p1", name="Project 1"))
    return engine, factory


def _actor(*, projects=("p1",), permissions=("artifact:read", "artifact:write", "task:read")) -> ActorContext:
    return ActorContext(
        actor_id="actor-1", tenant_id=None, project_ids=list(projects),
        auth_method="deployment_default", permissions=list(permissions), policy_version="test",
    )


def _accepted_upload(factory, store: LocalArtifactStore, content: bytes, *, kind: str = "batch_input"):
    uow = SQLAlchemyArtifactUnitOfWork(factory, validation_queue_available=True)
    with uow:
        service = ArtifactService(None, store).with_repository(uow.artifacts, jobs=uow.jobs)
        upload = service.create_upload(
            UploadCreate(
                project_id="p1", kind=kind, file_name="input.csv",
                media_type="text/csv", size_bytes=len(content),
                expected_sha256=hashlib.sha256(content).hexdigest(),
            ),
            _actor(),
        )
        service.receive_content(upload.id, content, _actor())
        accepted = service.complete_upload(upload.id, _actor())
        uow.commit()
    return upload.artifact_id, accepted.job_id


def _worker(factory, store, scanner, actor=None):
    return ArtifactValidationWorker(
        session_factory=None, store=store, scanner=scanner, actor=actor or _actor(),
    )


def _claim(worker, factory, job_id: str):
    with factory() as session, session.begin():
        dispatch = session.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_id == job_id))
        return worker._claim_sync(
            session,
            {"job_id": job_id, "attempt": 1, "dispatch_id": dispatch.dispatch_id},
            lease_guard=None,
        )


def _finish(worker, factory, claim, status, scan_status, error, scan_attempt_id, evidence):
    with factory() as session, session.begin():
        worker._finish_sync(
            session, claim, status=status, scan_status=scan_status,
            error_code=error, scan_evidence=evidence,
            scan_attempt_id=scan_attempt_id, lease_guard=None,
        )


def test_artifact_complete_upload_creates_job_and_outbox_in_same_uow(tmp_path: Path) -> None:
    engine, factory = _database(tmp_path)
    try:
        store = LocalArtifactStore(tmp_path / "objects")
        artifact_id, job_id = _accepted_upload(factory, store, b"id,value\na,1\n")
        with factory() as session:
            artifact = session.get(Artifact, artifact_id)
            job = session.get(Job, job_id)
            dispatch = session.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_id == job_id))
            attempt = session.scalar(select(JobAttempt).where(JobAttempt.job_id == job_id))
            assert artifact.status == "VALIDATING" and artifact.scan_status == "PENDING"
            assert job.kind == "artifact_validation" and job.resource_ref == {"type": "artifact", "id": artifact_id}
            assert attempt.status == "QUEUED"
            assert dispatch.payload == {"job_id": job_id, "attempt": 1, "dispatch_id": dispatch.dispatch_id}
            events = session.scalars(select(JobEvent).where(JobEvent.job_id == job_id).order_by(JobEvent.seq)).all()
            assert [event.seq for event in events] == [0]
    finally:
        engine.dispose()


def test_validation_persists_bound_evidence_and_available_state(tmp_path: Path) -> None:
    engine, factory = _database(tmp_path)
    try:
        store = LocalArtifactStore(tmp_path / "objects")
        artifact_id, job_id = _accepted_upload(factory, store, b"id,value\na,1\n")
        scanner = FakeScanner()
        worker = _worker(factory, store, scanner)
        claim = _claim(worker, factory, job_id)
        assert claim is not None
        status, scan_status, error, scan_attempt_id, evidence = worker._validate_and_scan(claim)
        _finish(worker, factory, claim, status, scan_status, error, scan_attempt_id, evidence)
        with factory() as session:
            artifact = session.get(Artifact, artifact_id)
            job = session.get(Job, job_id)
            event = session.scalar(select(JobEvent).where(JobEvent.job_id == job_id, JobEvent.type == "artifact.scan.evidence"))
            events = session.scalars(select(JobEvent).where(JobEvent.job_id == job_id).order_by(JobEvent.seq)).all()
            assert artifact.status == "AVAILABLE" and artifact.scan_status == "PASSED"
            assert artifact.metadata_json["validation_report_ref"] == {"type": "job", "id": job_id}
            assert job.status == "SUCCEEDED" and job.result_ref == {"type": "artifact", "id": artifact_id}
            assert event.data["attempt_id"] == claim.attempt_id
            assert event.data["artifact_id"] == artifact_id
            assert event.data["evidence"]["input_sha256"] == hashlib.sha256(b"id,value\na,1\n").hexdigest()
            assert "path" not in event.data["evidence"]
            assert [row.seq for row in events] == [0, 1, 2, 3]
        snapshot = JobService(
            SQLAlchemyTaskRepository(factory), policy=DeploymentTaskPolicy(),
        ).event_stream_snapshot(OperationContext(actor=_actor()), job_id, after_seq=-1)
        persisted_event = next(item for item in snapshot.events if item.type == "artifact.scan.evidence")
        assert persisted_event.data["evidence"]["input_sha256"] == hashlib.sha256(b"id,value\na,1\n").hexdigest()
        assert len(scanner.calls) == 1
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("storage_key", "objects/replaced/content"),
        ("sha256", "0" * 64),
        ("size_bytes", None),
        ("kind", "model_package"),
        ("media_type", "application/zip"),
    ],
)
def test_artifact_snapshot_change_during_scan_fails_job_without_releasing_row(
    tmp_path: Path, field: str, replacement: object,
) -> None:
    engine, factory = _database(tmp_path)
    try:
        store = LocalArtifactStore(tmp_path / "objects")
        artifact_id, job_id = _accepted_upload(factory, store, b"id,value\na,1\n")
        claim = _claim(_worker(factory, store, FakeScanner()), factory, job_id)
        assert claim is not None

        def mutate_claimed_row(_kwargs) -> None:
            with factory.begin() as session:
                artifact = session.get(Artifact, artifact_id)
                assert artifact is not None
                value = int(artifact.size_bytes) + 1 if field == "size_bytes" else replacement
                setattr(artifact, field, value)

        scanner = FakeScanner(on_scan=mutate_claimed_row)
        worker = _worker(factory, store, scanner)
        status, scan_status, error, scan_attempt_id, evidence = worker._validate_and_scan(claim)
        assert (status, scan_status, error) == ("SUCCEEDED", "PASSED", None)
        _finish(worker, factory, claim, status, scan_status, error, scan_attempt_id, evidence)

        with factory() as session:
            artifact = session.get(Artifact, artifact_id)
            job = session.get(Job, job_id)
            event = session.scalar(select(JobEvent).where(
                JobEvent.job_id == job_id, JobEvent.type == "artifact.scan.evidence",
            ))
            expected_value = int(claim.size_bytes) + 1 if field == "size_bytes" else replacement
            assert getattr(artifact, field) == expected_value
            assert artifact.status == "VALIDATING" and artifact.scan_status == "PENDING"
            assert "validation_report_ref" not in artifact.metadata_json
            assert job.status == "FAILED" and job.result_ref is None
            assert job.error == {"code": "ARTIFACT_SNAPSHOT_CHANGED"}
            assert event.data["validation_status"] == "FAILED"
            assert event.data["error_code"] == "ARTIFACT_SNAPSHOT_CHANGED"
            assert event.data["evidence"]["input_sha256"] == claim.sha256
    finally:
        engine.dispose()


def test_invalid_csv_is_rejected_after_scan_and_evidence_is_kept(tmp_path: Path) -> None:
    engine, factory = _database(tmp_path)
    try:
        store = LocalArtifactStore(tmp_path / "objects")
        artifact_id, job_id = _accepted_upload(factory, store, b"id,value\na\n")
        scanner = FakeScanner()
        worker = _worker(factory, store, scanner)
        claim = _claim(worker, factory, job_id)
        assert claim is not None
        result = worker._validate_and_scan(claim)
        assert result[0:3] == ("REJECTED", "PASSED", "CSV_ROW_SHAPE_INVALID")
        _finish(worker, factory, claim, *result)
        with factory() as session:
            artifact = session.get(Artifact, artifact_id)
            job = session.get(Job, job_id)
            event = session.scalar(select(JobEvent).where(JobEvent.job_id == job_id, JobEvent.type == "artifact.scan.evidence"))
            assert artifact.status == "QUARANTINED" and artifact.scan_status == "PASSED"
            assert job.status == "FAILED"
            assert event.data["validation_status"] == "REJECTED"
            assert event.data["evidence"]["verdict"] == "CLEAN"
        assert len(scanner.calls) == 1
    finally:
        engine.dispose()


def test_project_denial_precedes_artifact_read_and_scanner_call(tmp_path: Path) -> None:
    engine, factory = _database(tmp_path)
    try:
        store = LocalArtifactStore(tmp_path / "objects")
        _, job_id = _accepted_upload(factory, store, b"id,value\na,1\n")
        scanner = FakeScanner()
        worker = _worker(factory, store, scanner, actor=_actor(projects=(), permissions=("artifact:read",)))
        with factory() as session, session.begin():
            dispatch = session.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_id == job_id))
            with pytest.raises(ArtifactValidationError, match="ARTIFACT_SCOPE_UNAUTHORIZED"):
                worker._claim_sync(
                    session,
                    {"job_id": job_id, "attempt": 1, "dispatch_id": dispatch.dispatch_id},
                    lease_guard=None,
                )
        assert scanner.calls == []
    finally:
        engine.dispose()


def test_stale_fencing_token_cannot_persist_worker_result(tmp_path: Path) -> None:
    engine, factory = _database(tmp_path)
    try:
        store = LocalArtifactStore(tmp_path / "objects")
        artifact_id, job_id = _accepted_upload(factory, store, b"id,value\na,1\n")
        worker = _worker(factory, store, FakeScanner())
        claim = _claim(worker, factory, job_id)
        assert claim is not None
        with factory.begin() as session:
            attempt = session.scalar(select(JobAttempt).where(JobAttempt.job_id == job_id))
            attempt.fencing_token += 1
        with pytest.raises(ArtifactValidationError, match="WORKER_ATTEMPT_FENCE_INVALID"):
            _finish(worker, factory, claim, "SUCCEEDED", "PASSED", None, "sca-test", None)
        with factory() as session:
            assert session.get(Artifact, artifact_id).status == "VALIDATING"
            assert session.scalar(select(Job).where(Job.id == job_id)).status == "RUNNING"
            assert session.scalar(select(JobEvent).where(JobEvent.job_id == job_id, JobEvent.type == "artifact.scan.evidence")) is None
    finally:
        engine.dispose()


def test_missing_artifact_resource_fails_with_bound_event(tmp_path: Path) -> None:
    engine, factory = _database(tmp_path)
    try:
        store = LocalArtifactStore(tmp_path / "objects")
        artifact_id, job_id = _accepted_upload(factory, store, b"id,value\na,1\n")
        worker = _worker(factory, store, FakeScanner())
        with factory.begin() as session:
            job = session.get(Job, job_id)
            job.resource_ref = {"type": "artifact", "id": "missing-artifact"}
        claim = _claim(worker, factory, job_id)
        assert claim is None
        with factory() as session:
            job = session.get(Job, job_id)
            event = session.scalar(select(JobEvent).where(JobEvent.job_id == job_id, JobEvent.type == "artifact.scan.evidence"))
            assert job.status == "FAILED"
            assert event.data["artifact_id"] == "missing-artifact"
            assert event.data["error_code"] == "ARTIFACT_NOT_FOUND"
            assert session.get(Artifact, artifact_id).status == "VALIDATING"
    finally:
        engine.dispose()


def test_same_attempt_redelivery_fences_and_fails_without_second_scan(tmp_path: Path) -> None:
    engine, factory = _database(tmp_path)
    try:
        store = LocalArtifactStore(tmp_path / "objects")
        _, job_id = _accepted_upload(factory, store, b"id,value\na,1\n")
        scanner = FakeScanner()
        worker = _worker(factory, store, scanner)
        claim = _claim(worker, factory, job_id)
        assert claim is not None
        with factory() as session, session.begin():
            dispatch = session.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_id == job_id))
            result = worker._claim_sync(
                session,
                {"job_id": job_id, "attempt": 1, "dispatch_id": dispatch.dispatch_id},
                lease_guard=None,
            )
            assert result is None
        assert scanner.calls == []
        with factory() as session:
            attempt = session.scalar(select(JobAttempt).where(JobAttempt.job_id == job_id))
            artifact = session.scalar(select(Artifact).where(Artifact.id == claim.artifact_id))
            job = session.get(Job, job_id)
            event = session.scalar(select(JobEvent).where(JobEvent.job_id == job_id, JobEvent.type == "artifact.scan.evidence"))
            assert attempt.fencing_token == claim.fencing_token + 1
            assert job.status == "FAILED"
            assert artifact.status == "FAILED" and artifact.scan_status == "FAILED"
            assert event.data["error_code"] == "ARTIFACT_SCAN_REDELIVERY_UNSAFE"
    finally:
        engine.dispose()


def test_unsupported_kind_and_unknown_verdict_fail_closed(tmp_path: Path) -> None:
    engine, factory = _database(tmp_path)
    try:
        store = LocalArtifactStore(tmp_path / "objects")
        artifact_id, job_id = _accepted_upload(factory, store, b"id,value\na,1\n", kind="model_package")
        scanner = FakeScanner()
        worker = _worker(factory, store, scanner)
        claim = _claim(worker, factory, job_id)
        result = worker._validate_and_scan(claim)
        assert result[:3] == ("REJECTED", "REJECTED", "ARTIFACT_FORMAT_UNSUPPORTED")
        assert scanner.calls == []
        _finish(worker, factory, claim, *result)
        with factory() as session:
            assert session.get(Artifact, artifact_id).status == "QUARANTINED"
        artifact_id2, job_id2 = _accepted_upload(factory, store, b"id,value\na,1\n")
        bad_scanner = FakeScanner(verdict="UNKNOWN")
        bad_worker = _worker(factory, store, bad_scanner)
        claim2 = _claim(bad_worker, factory, job_id2)
        result2 = bad_worker._validate_and_scan(claim2)
        assert result2[:3] == ("FAILED", "FAILED", "SCAN_VERDICT_INVALID")
        _finish(bad_worker, factory, claim2, *result2)
        with factory() as session:
            assert session.get(Artifact, artifact_id2).status == "FAILED"
    finally:
        engine.dispose()


def test_production_defender_profile_stays_unavailable_without_process_scan(tmp_path: Path) -> None:
    launched = []
    now = datetime.now(timezone.utc)
    status = DefenderStatus(
        product_version="unapproved-test-build",
        engine_version="test-engine",
        signature_version="test-signature",
        signature_updated_at_utc=now,
        antivirus_enabled=True,
        authenticode_status="Valid",
        signer_subject="CN=Microsoft Windows Publisher",
    )
    scanner = DefenderCommandScanner(
        runner=lambda *args, **kwargs: launched.append((args, kwargs)),
        status_reader=lambda: status,
    )
    outcome = scanner.scan(
        path=(tmp_path / "harmless.csv").resolve(), artifact_id="art_test",
        input_sha256="a" * 64, input_size_bytes=1, scan_attempt_id="sca_test",
    )
    assert outcome.status == "FAILED"
    assert outcome.evidence.verdict == "INDETERMINATE"
    assert outcome.evidence.error_code == "DEFENDER_PROFILE_UNSUPPORTED"
    assert launched == []


def test_unconfigured_queue_rejects_complete_without_job_or_outbox(tmp_path: Path) -> None:
    engine, factory = _database(tmp_path)
    try:
        store = LocalArtifactStore(tmp_path / "objects")
        uow = SQLAlchemyArtifactUnitOfWork(factory)
        content = b"id,value\na,1\n"
        with uow:
            service = ArtifactService(None, store).with_repository(uow.artifacts, jobs=uow.jobs)
            upload = service.create_upload(
                UploadCreate(
                    project_id="p1", kind="batch_input", file_name="input.csv",
                    media_type="text/csv", size_bytes=len(content),
                    expected_sha256=hashlib.sha256(content).hexdigest(),
                ),
                _actor(),
            )
            service.receive_content(upload.id, content, _actor())
            uow.commit()
        with SQLAlchemyArtifactUnitOfWork(factory) as blocked_uow:
            blocked_service = ArtifactService(None, store).with_repository(
                blocked_uow.artifacts, jobs=blocked_uow.jobs,
            )
            with pytest.raises(ArtifactServiceError) as error:
                blocked_service.complete_upload(upload.id, _actor())
            assert error.value.code == "DEPENDENCY_UNAVAILABLE" and error.value.status_code == 503
        with factory() as session:
            artifact = session.get(Artifact, upload.artifact_id)
            assert artifact.status == "RECEIVED"
            assert session.scalar(select(Job)) is None
            assert session.scalar(select(OutboxEvent)) is None
    finally:
        engine.dispose()


def test_artifact_job_and_outbox_rollback_with_uncommitted_upload_uow(tmp_path: Path) -> None:
    engine, factory = _database(tmp_path)
    try:
        store = LocalArtifactStore(tmp_path / "objects")
        content = b"id,value\na,1\n"
        with SQLAlchemyArtifactUnitOfWork(factory, validation_queue_available=True) as uow:
            service = ArtifactService(None, store).with_repository(uow.artifacts, jobs=uow.jobs)
            upload = service.create_upload(
                UploadCreate(
                    project_id="p1", kind="batch_input", file_name="input.csv",
                    media_type="text/csv", size_bytes=len(content),
                    expected_sha256=hashlib.sha256(content).hexdigest(),
                ),
                _actor(),
            )
            service.receive_content(upload.id, content, _actor())
            accepted = service.complete_upload(upload.id, _actor())
            assert accepted.job_id
        with factory() as session:
            assert session.get(Artifact, upload.artifact_id) is None
            assert session.scalar(select(Job)) is None
            assert session.scalar(select(OutboxEvent)) is None
    finally:
        engine.dispose()


def test_scan_target_rejects_escape_and_detects_content_change(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "objects")
    content = b"id,value\na,1\n"
    key, size, digest = store.put(
        "artifact-test", content,
        expected_size=len(content), expected_sha256=hashlib.sha256(content).hexdigest(),
    )
    with pytest.raises(StorageContentInvalid):
        store.prepare_scan_target("../outside", expected_sha256=digest, expected_size=size)
    target = store.prepare_scan_target(key, expected_sha256=digest, expected_size=size)
    replacement = target.path.with_suffix(".replacement")
    replacement.write_bytes(content)
    os.replace(replacement, target.path)
    with pytest.raises(StorageContentInvalid):
        store.assert_scan_target_unchanged(target)


def test_scan_target_rejects_reparse_components_when_supported(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "objects")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "content").write_bytes(b"id,value\na,1\n")
    link = store._require_root() / "objects" / "linked"
    link.parent.mkdir(parents=True)
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlink creation is not available on this Windows account")
    with pytest.raises(StorageContentInvalid):
        store.prepare_scan_target(
            "objects/linked/content", expected_sha256=hashlib.sha256(b"id,value\na,1\n").hexdigest(),
            expected_size=len(b"id,value\na,1\n"),
        )


@pytest.mark.parametrize("replacement", [b"id,value\nb,2\n", b"id,value\na,1\nmore"])
def test_scan_target_detects_same_path_content_and_size_changes(tmp_path: Path, replacement: bytes) -> None:
    store = LocalArtifactStore(tmp_path / "objects")
    original = b"id,value\na,1\n"
    key, size, digest = store.put(
        "artifact-change", original,
        expected_size=len(original), expected_sha256=hashlib.sha256(original).hexdigest(),
    )
    target = store.prepare_scan_target(key, expected_sha256=digest, expected_size=size)
    target.path.write_bytes(replacement)
    with pytest.raises(StorageContentInvalid):
        store.assert_scan_target_unchanged(target)
