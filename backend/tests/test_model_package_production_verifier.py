from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from chrom_platform.infrastructure.db import Base
from chrom_platform.infrastructure.models import Job, JobAttempt, JobEvent, Project
from chrom_platform.modules.model_center.production_verifier import (
    SQLAlchemyModelPackageScanEvidenceReader,
    build_production_model_package_verifier,
)
from chrom_platform.modules.model_center.schemas import now_utc


@pytest.fixture()
def scan_session():
    path = Path(__file__).with_name(f".package-scan-reader-{uuid4().hex}.sqlite3")
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine, tables=[
        Project.__table__, Job.__table__, JobAttempt.__table__, JobEvent.__table__,
    ])
    try:
        with Session(engine, expire_on_commit=False) as session:
            yield session
    finally:
        engine.dispose()
        path.unlink(missing_ok=True)


def _seed_scan(
    session: Session, *, include_report: bool = True, duplicate: bool = False,
    raw_profile_id: str = "test-profile", omit_member: bool = False, suffix: str = "",
) -> tuple[str, dict[str, str]]:
    now = now_utc()
    suffix_part = f"-{suffix}" if suffix else ""
    project_id = f"project-scan{suffix_part}"
    job_id = f"scan-job{suffix_part}"
    artifact_id = f"model-package{suffix_part}"
    attempt_id = f"scan-attempt{suffix_part}-2"
    session.add(Project(id=project_id, slug=project_id, name="scan reader test"))
    session.flush()
    session.add(Job(
        id=job_id, project_id=project_id, owner_id="owner", kind="artifact_validation",
        resource_ref={"type": "artifact", "id": artifact_id}, status="SUCCEEDED", attempt=2,
        progress={}, cancelable=False, retryable=False, resume_mode="none", created_at=now,
        updated_at=now, started_at=now, finished_at=now, revision=1, etag='"1"',
    ))
    session.add_all([
        JobAttempt(id=f"scan-attempt{suffix_part}-1", job_id=job_id, attempt=1, status="FAILED", resume_mode="none"),
        JobAttempt(id=attempt_id, job_id=job_id, attempt=2, status="SUCCEEDED", resume_mode="none"),
    ])
    scanner_evidence = {
        "artifact_id": artifact_id,
        "scan_attempt_id": "defender-scan-2",
        "input_sha256": "a" * 64,
        "input_size_bytes": 4096,
        "provider_id": "synthetic-test-provider",
        "profile_id": raw_profile_id,
        "exit_code": 0,
        "verdict": "CLEAN",
        "error_code": None,
        "output_truncated": False,
    }
    data: dict[str, object] = {
        "job_id": job_id, "attempt": 2, "attempt_id": attempt_id,
        "project_id": project_id, "artifact_id": artifact_id,
        "validation_status": "SUCCEEDED", "scan_attempt_id": "defender-scan-2",
        "evidence": scanner_evidence,
    }
    if include_report:
        data["package_scan_report"] = {
            "schema_version": "1.0", "project_id": project_id, "artifact_id": artifact_id,
            "validation_status": "SUCCEEDED", "status": "SUCCEEDED",
            "validation_job_id": job_id, "scan_attempt_id": "defender-scan-2",
            "container": {
                "sha256": "a" * 64, "size_bytes": 4096, "identity": [11, 22],
                "verdict": "CLEAN", "profile_id": "test-profile",
            },
            "members": [
                {"name": "manifest.json", "sha256": "b" * 64, "size_bytes": 10, "verdict": "CLEAN", "profile_id": "test-profile"},
                {"name": "weights.safetensors", "sha256": "c" * 64, "size_bytes": 100, "verdict": "CLEAN", "profile_id": "test-profile"},
                {"name": "golden.json", "sha256": "d" * 64, "size_bytes": 20, "verdict": "CLEAN", "profile_id": "test-profile"},
            ],
        }
        if omit_member:
            data["package_scan_report"]["members"] = data["package_scan_report"]["members"][:-1]
    session.add(JobEvent(
        id=f"scan-event{suffix_part}-2", job_id=job_id, stream_id=job_id, seq=1,
        type="artifact.scan.evidence", schema_version="1.0", data=data, timestamp=now,
    ))
    if duplicate:
        session.add(JobEvent(
            id=f"scan-event{suffix_part}-duplicate", job_id=job_id, stream_id=job_id, seq=2,
            type="artifact.scan.evidence", schema_version="1.0", data=data, timestamp=now,
        ))
    session.flush()
    return job_id, {"type": "job", "id": job_id}


def test_scan_reader_returns_only_explicitly_persisted_current_attempt_report(scan_session):
    job_id, report_ref = _seed_scan(scan_session)
    report = SQLAlchemyModelPackageScanEvidenceReader(
        scan_session, approved_scan_profile_ids=("test-profile",),
    ).get(
        project_id="project-scan", report_ref=report_ref,
    )
    assert report is not None
    assert report["validation_job_id"] == job_id
    assert len(report["members"]) == 3
    assert report["container"]["identity"] == [11, 22]


def test_scan_reader_rejects_legacy_single_file_csv_event_without_members(scan_session):
    _job_id, report_ref = _seed_scan(scan_session, include_report=False)
    assert SQLAlchemyModelPackageScanEvidenceReader(
        scan_session, approved_scan_profile_ids=("test-profile",),
    ).get(
        project_id="project-scan", report_ref=report_ref,
    ) is None


def test_scan_reader_rejects_profile_mismatch_and_missing_member(scan_session):
    _job_id, report_ref = _seed_scan(scan_session, raw_profile_id="other-profile")
    reader = SQLAlchemyModelPackageScanEvidenceReader(
        scan_session, approved_scan_profile_ids=("test-profile", "other-profile"),
    )
    assert reader.get(project_id="project-scan", report_ref=report_ref) is None

    scan_session.rollback()
    _job_id, report_ref = _seed_scan(scan_session, omit_member=True)
    reader = SQLAlchemyModelPackageScanEvidenceReader(
        scan_session, approved_scan_profile_ids=("test-profile",),
    )
    assert reader.get(project_id="project-scan", report_ref=report_ref) is None


def test_scan_reader_rejects_duplicate_evidence_for_current_attempt(scan_session):
    _job_id, report_ref = _seed_scan(scan_session, duplicate=True)
    assert SQLAlchemyModelPackageScanEvidenceReader(
        scan_session, approved_scan_profile_ids=("test-profile",),
    ).get(
        project_id="project-scan", report_ref=report_ref,
    ) is None


def test_scan_reader_refreshes_cached_rows_after_other_session_changes_them(scan_session):
    for changed_row in ("job", "attempt", "event"):
        job_id, report_ref = _seed_scan(scan_session, suffix=changed_row)
        project_id = f"project-scan-{changed_row}"
        cached_job = scan_session.get(Job, job_id)
        cached_attempt = scan_session.query(JobAttempt).filter_by(job_id=job_id, attempt=2).one()
        cached_event = scan_session.get(JobEvent, f"scan-event-{changed_row}-2")
        assert cached_job.status == "SUCCEEDED" and cached_attempt.status == "SUCCEEDED"
        assert "package_scan_report" in cached_event.data
        scan_session.commit()  # retain identity-map values without holding a read transaction

        with Session(scan_session.get_bind(), expire_on_commit=False) as current_session:
            if changed_row == "job":
                current_session.get(Job, job_id).status = "FAILED"
            elif changed_row == "attempt":
                current_attempt = current_session.query(JobAttempt).filter_by(job_id=job_id, attempt=2).one()
                current_attempt.status = "FAILED"
            else:
                current_event = current_session.get(JobEvent, f"scan-event-{changed_row}-2")
                current_data = dict(current_event.data)
                current_data.pop("package_scan_report")
                current_event.data = current_data
            current_session.commit()

        assert cached_job.status == "SUCCEEDED" and cached_attempt.status == "SUCCEEDED"
        assert "package_scan_report" in cached_event.data
        reader = SQLAlchemyModelPackageScanEvidenceReader(
            scan_session, approved_scan_profile_ids=("test-profile",),
        )
        assert reader.get(project_id=project_id, report_ref=report_ref) is None
        if changed_row == "job":
            assert cached_job.status == "FAILED"
        elif changed_row == "attempt":
            assert cached_attempt.status == "FAILED"
        else:
            assert "package_scan_report" not in cached_event.data
        scan_session.rollback()


def test_production_factory_is_instance_scoped_and_closed_gates_never_execute(scan_session):
    artifact_store = SimpleNamespace(configured=True)
    verifier = build_production_model_package_verifier(
        session=scan_session, artifact_store=artifact_store,
    )
    assert verifier.artifacts._session is scan_session
    assert verifier.scan_evidence._session is scan_session
    assert verifier.allowed_loader_ids == ()
    assert verifier.approved_scan_profile_ids == frozenset()

    result = verifier.verify(
        project_id="project-scan",
        source={"kind": "external_package", "artifact_id": "model-package"},
        task_type="gc",
        operation_key="test-closed-gates",
    )
    assert result["valid"] is False
    assert result["error_code"] == "MODEL_PACKAGE_LOADER_UNAVAILABLE"
    receipt = result["_execution_receipt"]
    assert receipt["executor_status"] == "NOT_STARTED"
    assert receipt["termination"]["never_started"] is True
