"""Server-owned construction and SQL evidence reader for the first GC package verifier.

The scan reader deliberately accepts only an explicit package-scan report
persisted by the referenced artifact-validation event.  The current CSV
scanner event has no member hashes or scan-time file identity and therefore
does not qualify as package evidence.
"""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from chrom_platform.artifact_core import model_package_manifest, scanner_defender
from chrom_platform.artifact_core.repository import SQLAlchemyProjectArtifactReader
from chrom_platform.artifact_core.storage import ArtifactStore
from chrom_platform.infrastructure.models import Job, JobAttempt, JobEvent
from chrom_platform.modules.model_center.asset_verifier import (
    ModelPackageAssetVerifier,
    _scan_report_is_bound,
)


_SCAN_EVENT = "artifact.scan.evidence"
_SCAN_REPORT_KEY = "package_scan_report"
_REPORT_SCHEMA_VERSION = "1.0"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class SQLAlchemyModelPackageScanEvidenceReader:
    """Read only the unique successful scan event for the current JobAttempt."""

    def __init__(self, session: Session, *, approved_scan_profile_ids: tuple[str, ...] = ()) -> None:
        if callable(session):
            raise TypeError("scan evidence reader requires a caller-owned Session")
        self._session = session
        self._approved_scan_profile_ids = frozenset(approved_scan_profile_ids)

    def get(
        self, *, project_id: str, report_ref: Mapping[str, object],
    ) -> Mapping[str, object] | None:
        if (not isinstance(project_id, str) or not project_id
                or not isinstance(report_ref, Mapping)
                or set(report_ref) != {"type", "id"}
                or report_ref.get("type") != "job"
                or not isinstance(report_ref.get("id"), str)
                or not report_ref.get("id")):
            return None

        job_id = str(report_ref["id"])
        job = self._session.scalar(
            select(Job).where(Job.id == job_id).execution_options(populate_existing=True)
        )
        if (job is None or job.kind != "artifact_validation"
                or job.status != "SUCCEEDED" or job.project_id != project_id
                or type(job.attempt) is not int or job.attempt < 1):
            return None
        resource_ref = job.resource_ref if isinstance(job.resource_ref, Mapping) else {}
        artifact_id = resource_ref.get("id")
        if (resource_ref.get("type") != "artifact"
                or not isinstance(artifact_id, str) or not artifact_id):
            return None

        attempts = list(self._session.scalars(
            select(JobAttempt).where(
                JobAttempt.job_id == job.id,
                JobAttempt.attempt == job.attempt,
            ).execution_options(populate_existing=True)
        ))
        if len(attempts) != 1:
            return None
        attempt = attempts[0]
        if attempt.status != "SUCCEEDED" or not isinstance(attempt.id, str) or not attempt.id:
            return None

        events = list(self._session.scalars(
            select(JobEvent).where(
                JobEvent.job_id == job.id,
                JobEvent.type == _SCAN_EVENT,
            ).execution_options(populate_existing=True)
        ))
        current_events = []
        for event in events:
            data = event.data if isinstance(event.data, Mapping) else {}
            if data.get("attempt_id") == attempt.id:
                current_events.append((event, data))
        if len(current_events) != 1:
            return None
        event, data = current_events[0]
        if (event.schema_version != _REPORT_SCHEMA_VERSION
                or data.get("job_id") != job.id
                or data.get("attempt") != job.attempt
                or data.get("project_id") != job.project_id
                or data.get("artifact_id") != artifact_id
                or data.get("validation_status") != "SUCCEEDED"):
            return None

        scanner_evidence = data.get("evidence")
        report = data.get(_SCAN_REPORT_KEY)
        if not isinstance(scanner_evidence, Mapping) or not isinstance(report, Mapping):
            return None
        # A complete package report must be persisted by the scanner producer;
        # the reader never derives member records or file identity from bytes.
        if (report.get("schema_version") != _REPORT_SCHEMA_VERSION
                or report.get("validation_job_id") != job.id
                or report.get("project_id") != job.project_id
                or report.get("artifact_id") != artifact_id
                or report.get("status") != job.status
                or report.get("validation_status") != data.get("validation_status")
                or report.get("scan_attempt_id") != data.get("scan_attempt_id")
                or not isinstance(report.get("container"), Mapping)
                or not isinstance(report.get("members"), list)):
            return None
        container = report["container"]
        raw_profile_id = scanner_evidence.get("profile_id")
        raw_scan_attempt_id = scanner_evidence.get("scan_attempt_id")
        raw_sha256 = scanner_evidence.get("input_sha256")
        raw_size = scanner_evidence.get("input_size_bytes")
        exit_code = scanner_evidence.get("exit_code")
        if (scanner_evidence.get("artifact_id") != artifact_id
                or scanner_evidence.get("scan_attempt_id") != data.get("scan_attempt_id")
                or not isinstance(raw_scan_attempt_id, str) or not raw_scan_attempt_id
                or not isinstance(scanner_evidence.get("provider_id"), str)
                or not scanner_evidence.get("provider_id")
                or not isinstance(raw_profile_id, str) or not raw_profile_id
                or raw_profile_id not in self._approved_scan_profile_ids
                or "error_code" not in scanner_evidence or scanner_evidence.get("error_code") is not None
                or scanner_evidence.get("output_truncated") is not False
                or type(exit_code) is not int
                or not isinstance(raw_sha256, str) or _SHA256.fullmatch(raw_sha256) is None
                or type(raw_size) is not int or raw_size <= 0
                or container.get("sha256") != raw_sha256
                or container.get("size_bytes") != raw_size
                or container.get("verdict") != scanner_evidence.get("verdict")
                or container.get("profile_id") != raw_profile_id):
            return None

        identity = container.get("identity")
        if (not isinstance(identity, list) or len(identity) != 2
                or any(type(part) is not int for part in identity)):
            return None
        # The report's identity is used here only to validate its shape.  The
        # asset verifier later compares it to the independently observed
        # storage ScanTarget.identity before any executor call.
        report_ok, _members = _scan_report_is_bound(
            report,
            project_id=job.project_id,
            artifact_id=artifact_id,
            sha256=raw_sha256,
            size_bytes=raw_size,
            identity=tuple(identity),
            approved_profile_ids=self._approved_scan_profile_ids,
        )
        if not report_ok:
            return None
        return dict(report)


class _ExecutionDisabled:
    """Sentinel executor for a verifier whose approval gates remain closed."""

    def execute(self, *_args: object, **_kwargs: object) -> Mapping[str, object]:
        raise RuntimeError("MODEL_PACKAGE_APPROVAL_GATES_CLOSED")


def _approved_scan_profile_ids() -> tuple[str, ...]:
    if getattr(scanner_defender, "_PROFILE_EVIDENCE_APPROVED", False) is not True:
        return ()
    profiles = getattr(scanner_defender, "_APPROVED_PROFILES", None)
    if not isinstance(profiles, Mapping) or not profiles:
        return ()
    values: list[str] = []
    for product_version, profile in profiles.items():
        profile_id = getattr(profile, "profile_id", None)
        if (not isinstance(product_version, str) or not product_version
                or getattr(profile, "product_version", None) != product_version
                or not isinstance(profile_id, str) or not profile_id):
            return ()
        values.append(profile_id)
    return tuple(sorted(set(values)))


def build_production_model_package_verifier(
    *, session: Session, artifact_store: ArtifactStore | None,
) -> ModelPackageAssetVerifier:
    """Build one verifier for this worker-thread Session and trusted store.

    Both release gates are read from fixed source constants.  If either is
    closed, the returned verifier has no loader allowlist and cannot call an
    executor; its ordinary preflight path returns a bound NOT_STARTED receipt.
    """
    if callable(session):
        raise TypeError("production verifier requires the current worker Session")

    profiles = _approved_scan_profile_ids()
    resource_limits_approved = model_package_manifest.RESOURCE_LIMITS_APPROVED is True
    storage_configured = artifact_store is not None and getattr(artifact_store, "configured", False) is True
    gates_open = bool(profiles) and resource_limits_approved and storage_configured

    if gates_open:
        from chrom_platform.modules.model_center.model_package_executor import ControlledModelPackageExecutor

        executor: object = ControlledModelPackageExecutor()
        loader_ids = (model_package_manifest.FIXED_LOADER_ID,)
    else:
        executor = _ExecutionDisabled()
        loader_ids = ()

    return ModelPackageAssetVerifier(
        artifacts=SQLAlchemyProjectArtifactReader(session),
        storage=artifact_store,  # type: ignore[arg-type]
        scan_evidence=SQLAlchemyModelPackageScanEvidenceReader(
            session, approved_scan_profile_ids=profiles if gates_open else (),
        ),
        executor=executor,  # type: ignore[arg-type]
        allowed_loader_ids=loader_ids,
        approved_scan_profile_ids=profiles if gates_open else (),
    )


__all__ = [
    "SQLAlchemyModelPackageScanEvidenceReader",
    "build_production_model_package_verifier",
]
