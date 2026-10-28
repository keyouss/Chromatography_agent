"""Fake-only worker ordering and closed approval checks for model packages."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from chrom_platform.artifact_core import model_package_scan, validation_worker
from chrom_platform.artifact_core.models import Artifact
from chrom_platform.artifact_core.model_package_manifest import InspectedPackage, MANIFEST_NAME, WEIGHTS_NAME, GOLDEN_NAME
from chrom_platform.artifact_core.scanner_defender import ScanEvidence
from chrom_platform.artifact_core.schemas import UploadCreate
from chrom_platform.artifact_core.service import ArtifactService
from chrom_platform.artifact_core.storage import LocalArtifactStore
from chrom_platform.artifact_core.uow import SQLAlchemyArtifactUnitOfWork
from chrom_platform.artifact_core.validation_worker import ArtifactValidationWorker, _Claim
from chrom_platform.core.dto import ActorContext
from chrom_platform.infrastructure.db import Base
from chrom_platform.infrastructure.models import Job, JobAttempt, JobEvent, OutboxEvent, Project
from chrom_platform.modules.model_center.production_verifier import SQLAlchemyModelPackageScanEvidenceReader

TEST_SIGNATURE_TIME = datetime(2026, 10, 16, tzinfo=timezone.utc)


class _Scanner:
    def __init__(self, on_scan=None) -> None:
        self.calls = []
        self.on_scan = on_scan

    def scan(self, **kwargs):
        self.calls.append(kwargs)
        if self.on_scan:
            self.on_scan(kwargs)
        now = datetime.now(timezone.utc)
        return SimpleNamespace(status="PASSED", evidence=ScanEvidence(
            artifact_id=kwargs["artifact_id"], scan_attempt_id=kwargs["scan_attempt_id"],
            input_sha256=kwargs["input_sha256"], input_size_bytes=kwargs["input_size_bytes"],
            provider_id="fake-provider", product_version="fake-product", engine_version="fake-engine",
            signature_version="fake-signature", signature_updated_at_utc=TEST_SIGNATURE_TIME,
            started_at_utc=now, finished_at_utc=now, profile_id="fake-profile",
            exit_code=0, verdict="CLEAN", error_code=None, output_truncated=False,
        ))


def _claim(content: bytes) -> _Claim:
    return _Claim(
        job_id="job1", project_id="project1", artifact_id="artifact1", attempt=1,
        attempt_id="attempt1", fencing_token=1, dispatch_id="dispatch1",
        storage_key="objects/artifact1/content", sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content), media_type="application/zip", kind="model_package",
    )


def _worker(store: LocalArtifactStore, scanner: _Scanner) -> ArtifactValidationWorker:
    actor = ActorContext(
        actor_id="test", tenant_id=None, project_ids=["project1"],
        auth_method="deployment_default", permissions=["artifact:read"], policy_version="test",
    )
    return ArtifactValidationWorker(session_factory=None, store=store, scanner=scanner, actor=actor)


def test_unapproved_profile_blocks_before_outer_scan_parse_and_member_write(tmp_path: Path, monkeypatch) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    content = b"unparsed-container"
    key, size, digest = store.put("artifact1", content, expected_size=len(content))
    claim = _claim(content)
    assert key == claim.storage_key and size == claim.size_bytes and digest == claim.sha256
    scanner = _Scanner()
    monkeypatch.setattr(validation_worker, "package_scan_profiles_approved", lambda: False)
    monkeypatch.setattr(validation_worker, "inspect_model_package", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("parser called")))
    monkeypatch.setattr(validation_worker, "scan_model_package_members", lambda **kwargs: (_ for _ in ()).throw(AssertionError("member writer called")))

    result = _worker(store, scanner)._validate_model_package_and_scan(claim)

    assert result[:3] == ("FAILED", "FAILED", "SCANNER_PROFILE_UNAVAILABLE")
    assert scanner.calls == []
    assert not list((tmp_path / "store").rglob(".model-package-scan-*"))


def test_outer_scan_precedes_parser_then_exact_three_member_reports(tmp_path: Path, monkeypatch) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    content = b"outer-container-bytes"
    store.put("artifact1", content, expected_size=len(content))
    claim = _claim(content)
    phases: list[str] = []
    scanner = _Scanner(lambda _kwargs: phases.append("outer_scan"))
    member_contents = {
        MANIFEST_NAME: b"manifest", WEIGHTS_NAME: b"weights", GOLDEN_NAME: b"golden",
    }
    package = InspectedPackage(
        manifest={}, manifest_bytes=member_contents[MANIFEST_NAME],
        weights_bytes=member_contents[WEIGHTS_NAME], golden_bytes=member_contents[GOLDEN_NAME],
        member_hashes={name: hashlib.sha256(value).hexdigest() for name, value in member_contents.items()},
        member_sizes={name: len(value) for name, value in member_contents.items()},
        weights_header=SimpleNamespace(), container_sha256=claim.sha256,
        container_size_bytes=claim.size_bytes, limits_approved=False,
    )

    def inspect(*_args, **_kwargs):
        assert phases == ["outer_scan"]
        phases.append("bounded_parse")
        return package

    def scan_members(**kwargs):
        assert phases == ["outer_scan", "bounded_parse"]
        phases.append("member_scans")
        now = datetime.now(timezone.utc)
        records = []
        for name in (MANIFEST_NAME, WEIGHTS_NAME, GOLDEN_NAME):
            sha = package.member_hashes[name]
            size = package.member_sizes[name]
            scan_id = f"scan-{name}"
            evidence = ScanEvidence(
                artifact_id=claim.artifact_id, scan_attempt_id=scan_id,
                input_sha256=sha, input_size_bytes=size, provider_id="fake-provider",
                product_version="fake-product", engine_version="fake-engine",
                signature_version="fake-signature", signature_updated_at_utc=TEST_SIGNATURE_TIME,
                started_at_utc=now, finished_at_utc=now, profile_id="fake-profile",
                exit_code=0, verdict="CLEAN", error_code=None, output_truncated=False,
            )
            records.append({
                "name": name, "sha256": sha, "size_bytes": size,
                "scan_attempt_id": scan_id, "scan_status": "PASSED", "evidence": evidence,
            })
        return records

    monkeypatch.setattr(validation_worker, "package_scan_profiles_approved", lambda: True)
    monkeypatch.setattr(validation_worker, "approved_package_profile_ids", lambda: frozenset({"fake-profile"}))
    monkeypatch.setattr(validation_worker, "package_profile_matches", lambda _product, profile: profile == "fake-profile")
    monkeypatch.setattr(validation_worker, "inspect_model_package", inspect)
    monkeypatch.setattr(validation_worker, "scan_model_package_members", scan_members)

    result = _worker(store, scanner)._validate_model_package_and_scan(claim)

    assert result[:3] == ("SUCCEEDED", "PASSED", None)
    assert phases == ["outer_scan", "bounded_parse", "member_scans"]
    report = result[5]
    assert [member["name"] for member in report["members"]] == [MANIFEST_NAME, WEIGHTS_NAME, GOLDEN_NAME]
    assert report["container"]["sha256"] == claim.sha256
    assert all("path" not in member["evidence"] for member in report["members"])


def test_worker_persists_complete_report_with_available_artifact(tmp_path: Path, monkeypatch) -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _char_length(connection, _record) -> None:
        connection.create_function("CHAR_LENGTH", 1, lambda value: len(value) if value is not None else None)

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    actor = ActorContext(
        actor_id="test", tenant_id=None, project_ids=["project1"],
        auth_method="deployment_default",
        permissions=["artifact:read", "artifact:write", "task:read"], policy_version="test",
    )
    store = LocalArtifactStore(tmp_path / "store")
    content = b"fake-container-for-worker-evidence"
    digest = hashlib.sha256(content).hexdigest()
    try:
        with factory.begin() as session:
            session.add(Project(id="project1", slug="project1", name="Project 1"))
        uow = SQLAlchemyArtifactUnitOfWork(factory, validation_queue_available=True)
        with uow:
            service = ArtifactService(None, store).with_repository(uow.artifacts, jobs=uow.jobs)
            upload = service.create_upload(
                UploadCreate(
                    project_id="project1", kind="model_package", file_name="package.zip",
                    media_type="application/zip", size_bytes=len(content), expected_sha256=digest,
                ), actor,
            )
            service.receive_content(upload.id, content, actor)
            accepted = service.complete_upload(upload.id, actor)
            uow.commit()

        with factory() as session, session.begin():
            dispatch = session.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_id == accepted.job_id))
            worker = ArtifactValidationWorker(session_factory=None, store=store, scanner=_Scanner(), actor=actor)
            claim = worker._claim_sync(
                session,
                {"job_id": accepted.job_id, "attempt": 1, "dispatch_id": dispatch.dispatch_id},
                lease_guard=None,
            )
        assert claim is not None
        member_bytes = {MANIFEST_NAME: b"manifest", WEIGHTS_NAME: b"weights", GOLDEN_NAME: b"golden"}
        package = InspectedPackage(
            manifest={}, manifest_bytes=member_bytes[MANIFEST_NAME],
            weights_bytes=member_bytes[WEIGHTS_NAME], golden_bytes=member_bytes[GOLDEN_NAME],
            member_hashes={name: hashlib.sha256(data).hexdigest() for name, data in member_bytes.items()},
            member_sizes={name: len(data) for name, data in member_bytes.items()},
            weights_header=SimpleNamespace(), container_sha256=digest,
            container_size_bytes=len(content), limits_approved=False,
        )
        now = datetime.now(timezone.utc)

        def evidence_for(name: str, scan_id: str) -> ScanEvidence:
            data = member_bytes[name]
            return ScanEvidence(
                artifact_id=claim.artifact_id, scan_attempt_id=scan_id,
                input_sha256=hashlib.sha256(data).hexdigest(), input_size_bytes=len(data),
                provider_id="fake-provider", product_version="fake-product",
                engine_version="fake-engine", signature_version="fake-signature",
                signature_updated_at_utc=TEST_SIGNATURE_TIME, started_at_utc=now, finished_at_utc=now,
                profile_id="fake-profile", exit_code=0, verdict="CLEAN",
                error_code=None, output_truncated=False,
            )

        records = []
        for name in (MANIFEST_NAME, WEIGHTS_NAME, GOLDEN_NAME):
            scan_id = f"member-{name}"
            records.append({
                "name": name, "sha256": package.member_hashes[name],
                "size_bytes": package.member_sizes[name], "scan_attempt_id": scan_id,
                "scan_status": "PASSED", "evidence": evidence_for(name, scan_id),
            })
        monkeypatch.setattr(validation_worker, "package_scan_profiles_approved", lambda: True)
        monkeypatch.setattr(validation_worker, "approved_package_profile_ids", lambda: frozenset({"fake-profile"}))
        monkeypatch.setattr(validation_worker, "package_profile_matches", lambda _product, profile: profile == "fake-profile")
        monkeypatch.setattr(validation_worker, "inspect_model_package", lambda *_args, **_kwargs: package)
        monkeypatch.setattr(validation_worker, "scan_model_package_members", lambda **_kwargs: records)
        status, scan_status, error, scan_id, evidence, report = worker._validate_model_package_and_scan(claim)
        assert (status, scan_status, error) == ("SUCCEEDED", "PASSED", None)
        with factory.begin() as session:
            worker._finish_sync(
                session, claim, status=status, scan_status=scan_status, error_code=error,
                scan_evidence=evidence, scan_attempt_id=scan_id, lease_guard=None,
                package_scan_report=report,
            )
        with factory() as session:
            artifact = session.get(Artifact, claim.artifact_id)
            job = session.get(Job, claim.job_id)
            attempt = session.scalar(select(JobAttempt).where(JobAttempt.job_id == claim.job_id))
            event_row = session.scalar(select(JobEvent).where(
                JobEvent.job_id == claim.job_id, JobEvent.type == "artifact.scan.evidence",
            ))
            assert artifact.status == "AVAILABLE" and artifact.scan_status == "PASSED"
            assert job.status == "SUCCEEDED" and attempt.status == "SUCCEEDED"
            assert event_row.data["package_scan_report"]["attempt_id"] == claim.attempt_id
            assert event_row.data["package_scan_report"]["status"] == "SUCCEEDED"
            assert event_row.data["package_scan_report"]["validation_job_id"] == claim.job_id
            assert [item["name"] for item in event_row.data["package_scan_report"]["members"]] == [
                MANIFEST_NAME, WEIGHTS_NAME, GOLDEN_NAME,
            ]
            assert all(item["scan_status"] == "PASSED" for item in event_row.data["package_scan_report"]["members"])
            assert "path" not in str(event_row.data)
            accepted_report = SQLAlchemyModelPackageScanEvidenceReader(
                session, approved_scan_profile_ids=("fake-profile",),
            ).get(
                project_id=claim.project_id,
                report_ref={"type": "job", "id": claim.job_id},
            )
            assert accepted_report == event_row.data["package_scan_report"]
    finally:
        engine.dispose()


@pytest.mark.parametrize("mutation", ["missing", "job", "attempt", "project", "artifact", "scan", "member_scan"])
def test_package_success_without_fully_bound_report_cannot_mark_available(mutation, monkeypatch) -> None:
    worker = _worker(None, _Scanner())
    monkeypatch.setattr(validation_worker, "package_profile_matches", lambda _product, _profile: True)
    digest = "a" * 64
    job = SimpleNamespace(
        id="job1", attempt=1, kind="artifact_validation", project_id="project1",
        resource_ref={"type": "artifact", "id": "artifact1"}, status="RUNNING",
    )
    attempt = SimpleNamespace(
        attempt=1, id="attempt1", status="RUNNING", fencing_token=1,
    )
    artifact = SimpleNamespace(
        id="artifact1", project_id="project1", kind="model_package", status="VALIDATING",
        metadata_json={}, updated_at=None, sha256=digest, size_bytes=40,
    )

    outer_evidence = {
        "artifact_id": artifact.id, "scan_attempt_id": "scan1", "input_sha256": digest,
        "input_size_bytes": artifact.size_bytes, "provider_id": "fake-provider",
        "product_version": "fake-product", "engine_version": "fake-engine",
        "signature_version": "fake-signature", "signature_updated_at_utc": "2026-10-16T00:00:00+00:00",
        "started_at_utc": "2026-10-16T00:00:01+00:00", "finished_at_utc": "2026-10-16T00:00:02+00:00",
        "profile_id": "fake-profile", "exit_code": 0, "verdict": "CLEAN",
        "error_code": None, "output_truncated": False,
    }
    report = {
        "schema_version": "1.0", "status": "SUCCEEDED", "validation_status": "SUCCEEDED",
        "validation_job_id": job.id, "job_id": job.id, "attempt": 1, "attempt_id": attempt.id,
        "project_id": job.project_id, "artifact_id": artifact.id, "scan_attempt_id": "scan1",
        "container": {"sha256": digest, "size_bytes": artifact.size_bytes, "identity": [1, 2],
                      "verdict": "CLEAN", "profile_id": "fake-profile"},
        "members": [],
    }
    for index, name in enumerate((MANIFEST_NAME, WEIGHTS_NAME, GOLDEN_NAME)):
        member_digest = f"{index + 1:x}" * 64
        member_scan_id = f"member-scan-{index}"
        member_evidence = {
            **outer_evidence, "scan_attempt_id": member_scan_id,
            "input_sha256": member_digest, "input_size_bytes": index + 1,
        }
        report["members"].append({
            "name": name, "sha256": member_digest, "size_bytes": index + 1,
            "scan_attempt_id": member_scan_id, "scan_status": "PASSED",
            "verdict": "CLEAN", "profile_id": "fake-profile", "evidence": member_evidence,
        })
    if mutation == "missing":
        report = None
    elif mutation in {"job", "attempt", "project", "artifact", "scan"}:
        report = dict(report)
        report[{"job": "validation_job_id", "attempt": "attempt_id", "project": "project_id",
                "artifact": "artifact_id", "scan": "scan_attempt_id"}[mutation]] = "wrong-binding"
    elif mutation == "member_scan":
        report = dict(report)
        report["members"] = [dict(member) for member in report["members"]]
        report["members"][0]["scan_status"] = "REJECTED"

    class _Session:
        def __init__(self):
            self.added = []

        def add(self, value):
            self.added.append(value)

    session = _Session()
    try:
        worker._finish_locked(
            session, job, attempt, artifact=artifact, status="SUCCEEDED", scan_status="PASSED",
            error_code=None, scan_evidence=outer_evidence, scan_attempt_id="scan1", dispatch_id="dispatch1",
            lease_guard=None, package_scan_report=report,
        )
    except Exception as exc:
        assert getattr(exc, "code", None) == "PACKAGE_SCAN_REPORT_REQUIRED_OR_INVALID"
    else:
        raise AssertionError("missing report must prevent AVAILABLE")
    assert session.added == []
    assert artifact.status == "VALIDATING"


def test_lease_loss_after_first_member_scan_stops_package_without_report(tmp_path: Path, monkeypatch) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    content = b"fake-container-for-lease-loss"
    store.put("artifact1", content, expected_size=len(content))
    claim = _claim(content)
    member_bytes = {MANIFEST_NAME: b"manifest", WEIGHTS_NAME: b"weights", GOLDEN_NAME: b"golden"}
    package = InspectedPackage(
        manifest={}, manifest_bytes=member_bytes[MANIFEST_NAME],
        weights_bytes=member_bytes[WEIGHTS_NAME], golden_bytes=member_bytes[GOLDEN_NAME],
        member_hashes={name: hashlib.sha256(data).hexdigest() for name, data in member_bytes.items()},
        member_sizes={name: len(data) for name, data in member_bytes.items()},
        weights_header=SimpleNamespace(), container_sha256=claim.sha256,
        container_size_bytes=claim.size_bytes, limits_approved=False,
    )
    monkeypatch.setattr(validation_worker, "package_scan_profiles_approved", lambda: True)
    monkeypatch.setattr(validation_worker, "approved_package_profile_ids", lambda: frozenset({"fake-profile"}))
    monkeypatch.setattr(validation_worker, "package_profile_matches", lambda _product, profile: profile == "fake-profile")
    monkeypatch.setattr(model_package_scan.scanner_defender, "_PROFILE_EVIDENCE_APPROVED", True)
    monkeypatch.setattr(model_package_scan.scanner_defender, "_APPROVED_PROFILES", {
        "fake-product": SimpleNamespace(product_version="fake-product", profile_id="fake-profile"),
    })
    monkeypatch.setattr(validation_worker, "inspect_model_package", lambda *_args, **_kwargs: package)

    class _LeaseLost:
        checks = 0

        def check(self) -> None:
            self.checks += 1
            if self.checks == 5:
                raise RuntimeError("lease lost after first member scan")

    scanner = _Scanner()
    result = _worker(store, scanner)._validate_model_package_and_scan(claim, _LeaseLost())

    assert result[0] == "FAILED"
    assert result[5] is None
    assert len(scanner.calls) == 2  # outer container plus exactly one member
    assert list((tmp_path / "store").rglob(".model-package-scan-*")) == []
