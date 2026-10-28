from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from chrom_platform.artifact_core.models import Artifact
from chrom_platform.artifact_core.service import ArtifactService
from chrom_platform.artifact_core.storage import LocalArtifactStore
from chrom_platform.artifact_core.uow import SQLAlchemyArtifactUnitOfWork
from chrom_platform.artifact_core.validation_worker import ArtifactValidationWorker
from chrom_platform.artifact_core.schemas import UploadCreate
from chrom_platform.core.dto import ActorContext
from chrom_platform.infrastructure.db import Base
from chrom_platform.infrastructure.models import Job, JobEvent, OutboxEvent, Project


def _db(tmp_path: Path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    event.listen(engine, "connect", lambda conn, _record: conn.create_function(
        "CHAR_LENGTH", 1, lambda value: len(value) if value is not None else None,
    ))
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        session.add(Project(id="p1", slug="p1", name="Project 1"))
    return engine, factory, LocalArtifactStore(tmp_path / "objects")


def _actor() -> ActorContext:
    return ActorContext(
        actor_id="actor-1", tenant_id=None, project_ids=["p1"],
        auth_method="deployment_default", permissions=["artifact:read", "artifact:write", "task:read"],
        policy_version="test",
    )


def _upload(factory, store: LocalArtifactStore, content: bytes) -> tuple[str, str]:
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
        uow.commit()
    return upload.artifact_id, accepted.job_id


def _window(contents: list[bytes]):
    return SimpleNamespace(
        window_id="batch-csv-20261022-v1", project_id="p1",
        profile_id="reviewed-csv-profile-v1", exe_sha256="a" * 64,
        allowed_inputs=frozenset((hashlib.sha256(item).hexdigest(), len(item)) for item in contents),
        input_manifest_sha256="b" * 64, build_manifest_sha256="c" * 64,
    )


def _worker(store: LocalArtifactStore, window=None, scanner=None) -> ArtifactValidationWorker:
    return ArtifactValidationWorker(
        session_factory=None, store=store, scanner=scanner, actor=_actor(), scan_window=window,
    )


def _claim(worker: ArtifactValidationWorker, factory, job_id: str):
    with factory() as session, session.begin():
        dispatch = session.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_id == job_id))
        return worker._claim_sync(session, {
            "job_id": job_id, "attempt": 1, "dispatch_id": dispatch.dispatch_id,
        }, lease_guard=None)


def _finish_clean(worker: ArtifactValidationWorker, factory, claim, *, signature_version="1.461.140.0") -> None:
    now = datetime.now(timezone.utc)
    evidence = {
        "artifact_id": claim.artifact_id, "scan_attempt_id": claim.scan_attempt_id,
        "input_sha256": claim.sha256, "input_size_bytes": claim.size_bytes,
        "provider_id": "defender", "product_version": "4.18.25080.5",
        "engine_version": "1.1.26090.9", "signature_version": signature_version,
        "signature_updated_at_utc": now.isoformat(), "started_at_utc": now.isoformat(),
        "finished_at_utc": now.isoformat(), "profile_id": worker.scan_window.profile_id,
        "exe_sha256": worker.scan_window.exe_sha256, "exit_code": 0,
        "verdict": "CLEAN", "error_code": None, "output_truncated": False,
    }
    with factory.begin() as session:
        worker._finish_sync(
            session, claim, status="SUCCEEDED", scan_status="PASSED", error_code=None,
            scan_evidence=evidence, scan_attempt_id=claim.scan_attempt_id,
            lease_guard=None,
        )


def _reservation_count(factory) -> int:
    with factory() as session:
        return len(session.scalars(select(JobEvent).where(
            JobEvent.type == "artifact.csv_window.reserved",
        )).all())


@pytest.mark.parametrize("receipt_state", ["clean", "wrong_signature", "missing_receipt"])
def test_csv_policy_transition_preserves_prior_receipt_and_scan_budget(tmp_path, receipt_state):
    engine, factory, store = _db(tmp_path)
    try:
        contents = [f"id,value\na,{n}\n".encode() for n in range(1, 6)]
        _, first_job = _upload(factory, store, contents[0])
        old_window = _window(contents)
        old_window.window_id = "batch-csv-20261022-v2"
        old_worker = _worker(store, old_window)
        claim = _claim(old_worker, factory, first_job)
        _finish_clean(old_worker, factory, claim, signature_version="1.461.143.0")
        with factory.begin() as session:
            receipt = session.scalar(select(JobEvent).where(
                JobEvent.job_id == first_job, JobEvent.type == "artifact.scan.evidence",
            ))
            if receipt_state == "wrong_signature":
                receipt.data = {**receipt.data, "evidence": {**receipt.data["evidence"], "signature_version": "1.461.144.0"}}
            elif receipt_state == "missing_receipt":
                session.delete(receipt)
            original_data = None if receipt_state == "missing_receipt" else dict(receipt.data)
        current_window = _window(contents)
        current_window.window_id = old_window.window_id
        current_window.profile_id = "csv-current-signatures-policy"
        current_window.prior_profiles = ((old_window.profile_id, "1.461.143.0"),)
        current_worker = _worker(store, current_window)
        _, duplicate_job = _upload(factory, store, contents[0])
        assert _claim(current_worker, factory, duplicate_job) is None
        assert _reservation_count(factory) == 1
        _, next_job = _upload(factory, store, contents[1])
        next_claim = _claim(current_worker, factory, next_job)
        assert (next_claim is not None) == (receipt_state == "clean")
        assert _reservation_count(factory) == (2 if receipt_state == "clean" else 1)
        with factory() as session:
            persisted = session.scalar(select(JobEvent).where(
                JobEvent.job_id == first_job, JobEvent.type == "artifact.scan.evidence",
            ))
            assert (None if persisted is None else persisted.data) == original_data
    finally:
        engine.dispose()


def _change_artifact_type(factory, artifact_id: str, *, kind: str, media_type: str) -> None:
    with factory.begin() as session:
        artifact = session.get(Artifact, artifact_id)
        artifact.kind = kind
        artifact.media_type = media_type


def test_same_hash_other_artifact_is_blocked_after_worker_restart(tmp_path: Path) -> None:
    engine, factory, store = _db(tmp_path)
    try:
        content = b"id,value\na,1\n"
        allowed = [content] + [f"id,value\na,{n}\n".encode() for n in range(2, 6)]
        first_id, first_job = _upload(factory, store, content)
        second_id, second_job = _upload(factory, store, content)
        first_worker = _worker(store, _window(allowed))
        first = _claim(first_worker, factory, first_job)
        assert first is not None and first.scan_attempt_id
        _finish_clean(first_worker, factory, first)

        restarted_worker = _worker(store, _window(allowed))
        assert _claim(restarted_worker, factory, second_job) is None
        with factory() as session:
            first_artifact = session.get(Artifact, first_id)
            second_artifact = session.get(Artifact, second_id)
            second = session.get(Job, second_job)
            event_row = session.scalar(select(JobEvent).where(
                JobEvent.job_id == first_job, JobEvent.type == "artifact.scan.evidence",
            ))
            assert first_artifact.status == "AVAILABLE"
            assert second_artifact.status == "FAILED"
            assert second.error == {"code": "ARTIFACT_CSV_WINDOW_INPUT_ALREADY_RESERVED"}
            assert event_row.data["window_id"] == "batch-csv-20261022-v1"
        assert _reservation_count(factory) == 1
    finally:
        engine.dispose()


@pytest.mark.parametrize("prior_state", ["failed", "missing_receipt"])
def test_unresolved_first_reservation_blocks_next_file(tmp_path: Path, prior_state: str) -> None:
    engine, factory, store = _db(tmp_path)
    try:
        contents = [f"id,value\na,{n}\n".encode() for n in range(1, 6)]
        first_id, first_job = _upload(factory, store, contents[0])
        _, second_job = _upload(factory, store, contents[1])
        worker = _worker(store, _window(contents))
        claim = _claim(worker, factory, first_job)
        assert claim is not None
        if prior_state == "failed":
            with factory.begin() as session:
                worker._finish_sync(
                    session, claim, status="FAILED", scan_status="FAILED", error_code="SCAN_FAILED",
                    scan_evidence=None, scan_attempt_id=claim.scan_attempt_id, lease_guard=None,
                )
        assert _claim(worker, factory, second_job) is None
        with factory() as session:
            second_artifact = session.scalar(select(Artifact).where(Artifact.id != first_id))
            second = session.get(Job, second_job)
            assert second_artifact.status == "FAILED"
            assert second.error == {"code": "ARTIFACT_CSV_WINDOW_PRIOR_RESERVATION_UNRESOLVED"}
        assert _reservation_count(factory) == 1
    finally:
        engine.dispose()


def test_window_never_reserves_more_than_five_and_default_worker_is_unchanged(tmp_path: Path) -> None:
    engine, factory, store = _db(tmp_path)
    try:
        contents = [f"id,value\na,{n}\n".encode() for n in range(1, 6)]
        jobs = [_upload(factory, store, item) for item in contents]
        worker = _worker(store, _window(contents))
        for _, job_id in jobs:
            claim = _claim(worker, factory, job_id)
            assert claim is not None and claim.scan_attempt_id
            _finish_clean(worker, factory, claim)
        _, overflow_job = _upload(factory, store, contents[0])
        assert _claim(worker, factory, overflow_job) is None
        assert _reservation_count(factory) == 5

        default_content = b"id,value\na,6\n"
        _, default_job = _upload(factory, store, default_content)
        default_claim = _claim(_worker(store), factory, default_job)
        assert default_claim is not None
        assert default_claim.scan_attempt_id is None
        assert default_claim.scan_window_id is None
        assert _reservation_count(factory) == 5

        same_hash_id, same_hash_job = _upload(factory, store, contents[0])
        _change_artifact_type(
            factory, same_hash_id, kind="model_package", media_type="application/zip",
        )
        assert _claim(worker, factory, same_hash_job) is None
        with factory() as session:
            other_kind = session.get(Artifact, same_hash_id)
            assert other_kind.status == "FAILED"
            assert session.get(Job, same_hash_job).error == {
                "code": "ARTIFACT_CSV_WINDOW_RESOURCE_UNSUPPORTED",
            }
        assert _reservation_count(factory) == 5
    finally:
        engine.dispose()
