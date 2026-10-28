"""Fenced artifact validation consumer for the base artifact service."""
from __future__ import annotations
import asyncio
import csv
import io
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from sqlalchemy import select
from sqlalchemy.orm import Session
from chrom_platform.core.dto import ActorContext
from chrom_platform.artifact_core.models import Artifact as ArtifactRow
from chrom_platform.infrastructure.models import Job, JobAttempt, JobEvent, OutboxEvent, Project
from chrom_platform.task_core.repository import SQLAlchemyTaskRepository
from chrom_platform.task_core.service import sync_job_lifecycle
from chrom_platform.task_core.state import JobStatus
from .storage import LocalArtifactStore, ScanTarget, StorageContentInvalid
from .scanner_defender import ScanEvidence
from .model_package_manifest import EXPECTED_MEMBERS, GOLDEN_NAME, MANIFEST_NAME, WEIGHTS_NAME, PackageValidationError, inspect_model_package
from .model_package_scan import ModelPackageScanError, approved_package_profile_ids, package_profile_matches, package_scan_profiles_approved, scan_model_package_members
_MAX_CSV_ROWS = 500000
_MAX_CSV_COLUMNS = 512
_MAX_CSV_CELL_CHARS = 131072

@dataclass(frozen=True)
class DatasetCsvScanPolicy:
    """Server-owned, separately reviewed dataset input admission; never upload data."""
    project_id: str
    profile_id: str
    encoding: str
    allowed_inputs: frozenset[tuple[str, int]]
    max_size_bytes: int = 16 * 1024 * 1024
    allowed_kind: str = 'dataset_package'
    source_ref_type: str = 'artifact'
_SCAN_EVIDENCE_FIELDS = ('artifact_id', 'scan_attempt_id', 'input_sha256', 'input_size_bytes', 'provider_id', 'product_version', 'engine_version', 'signature_version', 'signature_updated_at_utc', 'started_at_utc', 'finished_at_utc', 'profile_id', 'exit_code', 'verdict', 'error_code', 'output_truncated', 'exe_sha256', 'signature_version_after', 'signature_updated_at_utc_after')

class ArtifactValidationError(RuntimeError):

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)

@dataclass(frozen=True)
class _Claim:
    job_id: str
    project_id: str
    artifact_id: str
    attempt: int
    attempt_id: str
    fencing_token: int
    dispatch_id: str
    storage_key: str
    sha256: str
    size_bytes: int
    media_type: str
    kind: str
    source_ref_type: str = 'artifact'
    scan_attempt_id: str | None = None

def _now() -> datetime:
    return datetime.now(timezone.utc)

def _csv_structure_check(path: Path, *, encoding: str='utf-8-sig', max_rows: int=_MAX_CSV_ROWS, max_columns: int=_MAX_CSV_COLUMNS) -> str | None:
    """Check bounded CSV syntax only; domain schemas remain downstream."""
    try:
        with path.open('rb') as raw:
            text = io.TextIOWrapper(raw, encoding=encoding, newline='', errors='strict')
            reader = csv.reader(text, strict=True)
            header = next(reader, None)
            if not header or len(header) > max_columns:
                return 'CSV_HEADER_INVALID'
            if any((not cell or len(cell) > _MAX_CSV_CELL_CHARS for cell in header)):
                return 'CSV_HEADER_INVALID'
            if len(set(header)) != len(header):
                return 'CSV_HEADER_DUPLICATE'
            rows = 0
            for row in reader:
                rows += 1
                if rows > max_rows:
                    return 'CSV_ROW_LIMIT_EXCEEDED'
                if len(row) != len(header) or any((len(cell) > _MAX_CSV_CELL_CHARS for cell in row)):
                    return 'CSV_ROW_SHAPE_INVALID'
            return None if rows else 'CSV_DATA_EMPTY'
    except (OSError, UnicodeError, csv.Error, StopIteration):
        return 'CSV_SYNTAX_INVALID'

def _evidence_payload(evidence: object | None) -> dict[str, Any] | None:
    if evidence is None:
        return None
    if not isinstance(evidence, ScanEvidence):
        raise ArtifactValidationError('SCAN_EVIDENCE_INVALID')
    result: dict[str, Any] = {}
    for name in _SCAN_EVIDENCE_FIELDS:
        value = getattr(evidence, name, None)
        if name in {'exe_sha256', 'signature_version_after', 'signature_updated_at_utc_after'} and value is None:
            continue
        if name in {'signature_updated_at_utc', 'signature_updated_at_utc_after', 'started_at_utc', 'finished_at_utc'}:
            if value is not None and (not isinstance(value, datetime)):
                raise ArtifactValidationError('SCAN_EVIDENCE_INVALID')
        elif name == 'input_size_bytes':
            if isinstance(value, bool) or not isinstance(value, int):
                raise ArtifactValidationError('SCAN_EVIDENCE_INVALID')
        elif name == 'exit_code':
            if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
                raise ArtifactValidationError('SCAN_EVIDENCE_INVALID')
        elif name == 'output_truncated':
            if not isinstance(value, bool):
                raise ArtifactValidationError('SCAN_EVIDENCE_INVALID')
        elif value is not None and (not isinstance(value, str)):
            raise ArtifactValidationError('SCAN_EVIDENCE_INVALID')
        if isinstance(value, datetime):
            value = value.astimezone(timezone.utc).isoformat()
        result[name] = value
    if not all((result.get(name) is not None for name in ('artifact_id', 'scan_attempt_id', 'input_sha256', 'input_size_bytes', 'provider_id', 'started_at_utc', 'finished_at_utc', 'verdict'))):
        raise ArtifactValidationError('SCAN_EVIDENCE_INCOMPLETE')
    return result

def _event_seq(session: Session, job_id: str) -> int:
    value = session.scalar(select(JobEvent.seq).where(JobEvent.job_id == job_id).order_by(JobEvent.seq.desc()).limit(1))
    return int(value) + 1 if value is not None else 0

def _package_scan_report_is_bound(report: object, *, job: Job, attempt: JobAttempt, artifact: ArtifactRow | None, status: str, scan_status: str, scan_attempt_id: str | None, scan_evidence: Mapping[str, Any] | None) -> bool:
    if not isinstance(report, Mapping) or artifact is None or artifact.kind != 'model_package' or (status != 'SUCCEEDED') or (scan_status != 'PASSED') or (not isinstance(scan_attempt_id, str)) or (not scan_attempt_id) or (job.status != JobStatus.RUNNING.value) or (attempt.status not in {JobStatus.RUNNING.value, JobStatus.QUEUED.value}):
        return False
    resource_ref = job.resource_ref if isinstance(job.resource_ref, Mapping) else {}
    if report.get('schema_version') != '1.0' or report.get('status') != 'SUCCEEDED' or report.get('validation_status') != 'SUCCEEDED' or (report.get('validation_job_id') != job.id) or (report.get('job_id') != job.id) or (type(report.get('attempt')) is not int) or (report.get('attempt') != attempt.attempt) or (report.get('attempt_id') != attempt.id) or (report.get('project_id') != job.project_id) or (report.get('artifact_id') != artifact.id) or (resource_ref.get('type') != 'artifact') or (resource_ref.get('id') != artifact.id) or (report.get('scan_attempt_id') != scan_attempt_id):
        return False
    if not isinstance(scan_evidence, Mapping):
        return False
    container = report.get('container')
    if not isinstance(container, Mapping):
        return False
    evidence_profile = scan_evidence.get('profile_id')
    evidence_product = scan_evidence.get('product_version')
    evidence_sha = scan_evidence.get('input_sha256')
    evidence_size = scan_evidence.get('input_size_bytes')
    if scan_evidence.get('artifact_id') != artifact.id or scan_evidence.get('scan_attempt_id') != scan_attempt_id or evidence_sha != artifact.sha256 or (type(evidence_size) is not int) or (evidence_size != artifact.size_bytes) or (scan_evidence.get('verdict') != 'CLEAN') or (scan_evidence.get('exit_code') != 0) or (scan_evidence.get('error_code') is not None) or (scan_evidence.get('output_truncated') is not False) or any((not isinstance(scan_evidence.get(field), str) or not scan_evidence[field] for field in ('provider_id', 'product_version', 'engine_version', 'signature_version', 'signature_updated_at_utc', 'started_at_utc', 'finished_at_utc'))) or (not package_profile_matches(evidence_product, evidence_profile)) or (container.get('sha256') != artifact.sha256) or (container.get('size_bytes') != artifact.size_bytes) or (container.get('verdict') != 'CLEAN') or (container.get('profile_id') != evidence_profile):
        return False
    identity = container.get('identity')
    if not isinstance(identity, list) or len(identity) != 2 or type(identity[0]) is not int or (identity[0] < 0) or (type(identity[1]) is not int) or (identity[1] <= 0):
        return False
    members = report.get('members')
    if not isinstance(members, list) or len(members) != 3:
        return False
    names: set[str] = set()
    for member in members:
        if not isinstance(member, Mapping):
            return False
        name = member.get('name')
        digest = member.get('sha256')
        size = member.get('size_bytes')
        member_scan_id = member.get('scan_attempt_id')
        evidence = member.get('evidence')
        if name not in EXPECTED_MEMBERS or name in names or (not isinstance(digest, str)) or (re.fullmatch('[0-9a-f]{64}', digest) is None) or (type(size) is not int) or (size <= 0) or (not isinstance(member_scan_id, str)) or (not member_scan_id) or (member.get('scan_status') != 'PASSED') or (member.get('verdict') != 'CLEAN') or (member.get('profile_id') != evidence_profile) or (not isinstance(evidence, Mapping)) or (evidence.get('artifact_id') != artifact.id) or (evidence.get('scan_attempt_id') != member_scan_id) or (evidence.get('input_sha256') != digest) or (type(evidence.get('input_size_bytes')) is not int) or (evidence.get('input_size_bytes') != size) or (evidence.get('verdict') != 'CLEAN') or (evidence.get('exit_code') != 0) or (evidence.get('error_code') is not None) or (evidence.get('output_truncated') is not False) or (evidence.get('profile_id') != evidence_profile) or any((evidence.get(field) != scan_evidence.get(field) for field in ('provider_id', 'product_version', 'engine_version', 'signature_version', 'signature_updated_at_utc'))) or any((not isinstance(evidence.get(field), str) or not evidence[field] for field in ('provider_id', 'product_version', 'engine_version', 'signature_version', 'signature_updated_at_utc', 'started_at_utc', 'finished_at_utc'))) or (not package_profile_matches(evidence.get('product_version'), evidence.get('profile_id'))):
            return False
        names.add(name)
    return names == EXPECTED_MEMBERS

class ArtifactValidationWorker:
    """Runs scanner and CSV work outside DB transactions with attempt fencing."""

    def __init__(self, *, session_factory, store: LocalArtifactStore | None, scanner: object | None, dataset_scanner: object | None=None, actor: ActorContext, dataset_csv_policy: DatasetCsvScanPolicy | None=None, reviewed_dataset_profile_ids: frozenset[str]=frozenset()) -> None:
        self.session_factory = session_factory
        self.store = store
        self.scanner = scanner
        self.dataset_scanner = dataset_scanner
        self.actor = actor
        self.dataset_csv_policy = dataset_csv_policy
        self.reviewed_dataset_profile_ids = reviewed_dataset_profile_ids
        self.task_repository = SQLAlchemyTaskRepository(lambda: None)

    def _claim_sync(self, session: Session, payload: Mapping[str, Any], *, lease_guard: object | None) -> _Claim | None:
        if lease_guard is not None:
            lease_guard.check()
        job_id, dispatch_id, attempt_no = (payload.get('job_id'), payload.get('dispatch_id'), payload.get('attempt'))
        if not isinstance(job_id, str) or not job_id or (not isinstance(dispatch_id, str)) or (not dispatch_id) or isinstance(attempt_no, bool) or (not isinstance(attempt_no, int)) or (attempt_no < 1):
            raise ArtifactValidationError('INVALID_WORKER_PAYLOAD')
        job = session.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if job is None or job.kind != 'artifact_validation' or int(job.attempt) != attempt_no:
            raise ArtifactValidationError('WORKER_JOB_FENCE_INVALID')
        if not self.actor.can_access_project(job.project_id) or not self.actor.can('artifact:read'):
            raise ArtifactValidationError('ARTIFACT_SCOPE_UNAUTHORIZED')
        expected_ref = job.resource_ref if isinstance(job.resource_ref, Mapping) else {}
        if expected_ref.get('type') != 'artifact' or not isinstance(expected_ref.get('id'), str) or (not expected_ref.get('id')):
            raise ArtifactValidationError('WORKER_JOB_RESOURCE_INVALID')
        dispatch = session.scalar(select(OutboxEvent).where(OutboxEvent.dispatch_id == dispatch_id, OutboxEvent.aggregate_type == 'job', OutboxEvent.aggregate_id == job_id, OutboxEvent.event_type == 'job.execute').with_for_update())
        if dispatch is None or dict(dispatch.payload or {}) != {'job_id': job_id, 'attempt': attempt_no, 'dispatch_id': dispatch_id}:
            raise ArtifactValidationError('WORKER_DISPATCH_FENCE_INVALID')
        attempt = session.scalar(select(JobAttempt).where(JobAttempt.job_id == job_id, JobAttempt.attempt == attempt_no).with_for_update())
        if attempt is None:
            raise ArtifactValidationError('WORKER_ATTEMPT_MISSING')
        if job.status in {status.value for status in _TERMINAL}:
            return None
        now = _now()
        if (job.status, attempt.status) not in {(JobStatus.QUEUED.value, JobStatus.QUEUED.value), (JobStatus.RUNNING.value, JobStatus.RUNNING.value)}:
            raise ArtifactValidationError('WORKER_JOB_STATE_INVALID')
        artifact = session.scalar(select(ArtifactRow).where(ArtifactRow.id == expected_ref['id'], ArtifactRow.project_id == job.project_id).with_for_update())
        if (job.status, attempt.status) == (JobStatus.RUNNING.value, JobStatus.RUNNING.value):
            attempt.fencing_token = int(attempt.fencing_token or 0) + 1
            self._finish_locked(session, job, attempt, artifact=artifact, status='FAILED', scan_status='FAILED', error_code='ARTIFACT_SCAN_REDELIVERY_UNSAFE', scan_evidence=None, scan_attempt_id=None, dispatch_id=dispatch_id, lease_guard=lease_guard)
            return None
        attempt.fencing_token = int(attempt.fencing_token or 0) + 1
        attempt.execution_token = f'artifact-validation:{dispatch_id}:{attempt_no}'
        token = int(attempt.fencing_token)
        started = sync_job_lifecycle(self.task_repository, session, job_id=job_id, status=JobStatus.RUNNING, attempt=attempt_no, expected_attempt=attempt_no, fencing_token=token, dispatch_id=dispatch_id, expected_dispatch_id=dispatch_id, resource_ref={'type': 'artifact', 'id': expected_ref['id']}, started_at=now)
        if started is None:
            raise ArtifactValidationError('WORKER_JOB_FENCE_INVALID')
        if artifact is None:
            self._finish_locked(session, job, attempt, artifact=None, status='FAILED', scan_status='FAILED', error_code='ARTIFACT_NOT_FOUND', scan_evidence=None, scan_attempt_id=None, dispatch_id=dispatch_id, lease_guard=lease_guard)
            return None
        if artifact.status != 'VALIDATING' or artifact.scan_status != 'PENDING':
            self._finish_locked(session, job, attempt, artifact=artifact, status='FAILED', scan_status='FAILED', error_code='ARTIFACT_STATE_INVALID', scan_evidence=None, scan_attempt_id=None, dispatch_id=dispatch_id, lease_guard=lease_guard)
            return None
        scan_attempt_id = None
        return _Claim(job_id=job.id, project_id=job.project_id, artifact_id=artifact.id, attempt=attempt_no, attempt_id=attempt.id, fencing_token=token, dispatch_id=dispatch_id, storage_key=artifact.storage_key, sha256=str(artifact.sha256 or ''), size_bytes=int(artifact.size_bytes), media_type=artifact.media_type, kind=artifact.kind, source_ref_type=str(expected_ref.get('type', '')), scan_attempt_id=scan_attempt_id)

    async def _claim(self, payload: Mapping[str, Any], *, lease_guard: object | None) -> _Claim | None:
        async with self.session_factory() as session:
            async with session.begin():
                return await session.run_sync(lambda sync: self._claim_sync(sync, payload, lease_guard=lease_guard))

    def _finish_locked(self, session: Session, job: Job, attempt: JobAttempt, *, artifact: ArtifactRow | None, status: str, scan_status: str, error_code: str | None, scan_evidence: dict[str, Any] | None, scan_attempt_id: str | None, dispatch_id: str, lease_guard: object | None, package_scan_report: dict[str, Any] | None=None) -> None:
        if lease_guard is not None:
            lease_guard.check()
        if int(job.attempt) != int(attempt.attempt) or job.kind != 'artifact_validation':
            raise ArtifactValidationError('WORKER_JOB_FENCE_INVALID')
        if attempt.status not in {JobStatus.RUNNING.value, JobStatus.QUEUED.value}:
            raise ArtifactValidationError('WORKER_ATTEMPT_FENCE_INVALID')
        if artifact is not None and (artifact.project_id != job.project_id or artifact.id != (job.resource_ref or {}).get('id')):
            raise ArtifactValidationError('ARTIFACT_PROJECT_MISMATCH')
        if artifact is not None and artifact.kind == 'model_package':
            report_bound = _package_scan_report_is_bound(package_scan_report, job=job, attempt=attempt, artifact=artifact, status=status, scan_status=scan_status, scan_attempt_id=scan_attempt_id, scan_evidence=scan_evidence)
            if status == 'SUCCEEDED' and (not report_bound) or (package_scan_report is not None and (not report_bound)):
                raise ArtifactValidationError('PACKAGE_SCAN_REPORT_REQUIRED_OR_INVALID')
        elif package_scan_report is not None:
            raise ArtifactValidationError('PACKAGE_SCAN_REPORT_RESOURCE_INVALID')
        data = {'job_id': job.id, 'attempt': int(attempt.attempt), 'attempt_id': attempt.id, 'project_id': job.project_id, 'artifact_id': (job.resource_ref or {}).get('id'), 'validation_status': status, 'scan_attempt_id': scan_attempt_id, 'error_code': error_code, 'evidence': scan_evidence}
        if package_scan_report is not None:
            if status != 'SUCCEEDED' or scan_status != 'PASSED':
                raise ArtifactValidationError('PACKAGE_SCAN_REPORT_STATUS_INVALID')
            data['package_scan_report'] = package_scan_report
        session.add(JobEvent(id=f'event_{uuid.uuid4().hex}', job_id=job.id, stream_id=job.id, seq=_event_seq(session, job.id), type='artifact.scan.evidence', schema_version='1.0', data=data, timestamp=_now()))
        if artifact is not None and artifact.status == 'VALIDATING':
            artifact.status = 'AVAILABLE' if status == 'SUCCEEDED' else 'QUARANTINED' if status == 'REJECTED' else 'FAILED'
            artifact.scan_status = scan_status
            metadata = dict(artifact.metadata_json or {})
            metadata['validation_report_ref'] = {'type': 'job', 'id': job.id}
            artifact.metadata_json = metadata
            artifact.updated_at = _now()
        final = sync_job_lifecycle(self.task_repository, session, job_id=job.id, status=JobStatus.SUCCEEDED if status == 'SUCCEEDED' else JobStatus.FAILED, attempt=int(attempt.attempt), expected_attempt=int(attempt.attempt), fencing_token=int(attempt.fencing_token or 0), dispatch_id=dispatch_id, expected_dispatch_id=dispatch_id, resource_ref=job.resource_ref, result_ref={'type': 'artifact', 'id': (job.resource_ref or {}).get('id')} if status == 'SUCCEEDED' else None, error={'code': error_code or 'ARTIFACT_VALIDATION_REJECTED'} if status != 'SUCCEEDED' else None, finished_at=_now())
        if final is None:
            raise ArtifactValidationError('WORKER_JOB_FENCE_INVALID')

    def _finish_sync(self, session: Session, claim: _Claim, *, status: str, scan_status: str, error_code: str | None, scan_evidence: dict[str, Any] | None, scan_attempt_id: str | None, lease_guard: object | None, package_scan_report: dict[str, Any] | None=None) -> None:
        if lease_guard is not None:
            lease_guard.check()
        job = session.scalar(select(Job).where(Job.id == claim.job_id).with_for_update())
        attempt = session.scalar(select(JobAttempt).where(JobAttempt.job_id == claim.job_id, JobAttempt.attempt == claim.attempt).with_for_update())
        if job is None or attempt is None or attempt.id != claim.attempt_id:
            raise ArtifactValidationError('WORKER_ATTEMPT_FENCE_INVALID')
        if int(attempt.fencing_token or 0) != claim.fencing_token:
            raise ArtifactValidationError('WORKER_ATTEMPT_FENCE_INVALID')
        artifact = session.scalar(select(ArtifactRow).where(ArtifactRow.id == claim.artifact_id, ArtifactRow.project_id == claim.project_id).with_for_update())
        if artifact is None or artifact.status != 'VALIDATING':
            raise ArtifactValidationError('ARTIFACT_STATE_INVALID')
        if artifact.storage_key != claim.storage_key or artifact.sha256 != claim.sha256 or int(artifact.size_bytes) != claim.size_bytes or (artifact.kind != claim.kind) or (artifact.media_type != claim.media_type):
            self._finish_locked(session, job, attempt, artifact=None, status='FAILED', scan_status='FAILED', error_code='ARTIFACT_SNAPSHOT_CHANGED', scan_evidence=scan_evidence, scan_attempt_id=scan_attempt_id, dispatch_id=claim.dispatch_id, lease_guard=lease_guard)
            return
        self._finish_locked(session, job, attempt, artifact=artifact, status=status, scan_status=scan_status, error_code=error_code, scan_evidence=scan_evidence, scan_attempt_id=scan_attempt_id, dispatch_id=claim.dispatch_id, lease_guard=lease_guard, package_scan_report=package_scan_report)

    @staticmethod
    def _clean_scan_evidence(outcome: object, *, artifact_id: str, sha256: str, size_bytes: int, scan_attempt_id: str, approved_profiles: frozenset[str]) -> dict[str, Any]:
        if getattr(outcome, 'status', None) != 'PASSED':
            raise ArtifactValidationError('PACKAGE_SCAN_NOT_PASSED')
        return ArtifactValidationWorker._clean_evidence_payload(getattr(outcome, 'evidence', None), artifact_id=artifact_id, sha256=sha256, size_bytes=size_bytes, scan_attempt_id=scan_attempt_id, approved_profiles=approved_profiles)

    @staticmethod
    def _clean_evidence_payload(raw_evidence: object | None, *, artifact_id: str, sha256: str, size_bytes: int, scan_attempt_id: str, approved_profiles: frozenset[str]) -> dict[str, Any]:
        evidence = _evidence_payload(raw_evidence)
        if evidence is None:
            raise ArtifactValidationError('SCAN_EVIDENCE_MISSING')
        if evidence['artifact_id'] != artifact_id or evidence['scan_attempt_id'] != scan_attempt_id or evidence['input_sha256'] != sha256 or (evidence['input_size_bytes'] != size_bytes):
            raise ArtifactValidationError('SCAN_EVIDENCE_BINDING_INVALID')
        if evidence['verdict'] != 'CLEAN' or evidence['exit_code'] != 0 or evidence['output_truncated'] is not False or (evidence.get('error_code') is not None) or (evidence.get('profile_id') not in approved_profiles) or (not package_profile_matches(evidence.get('product_version'), evidence.get('profile_id'))) or any((evidence.get(field) in (None, '') for field in ('provider_id', 'product_version', 'engine_version', 'signature_version', 'signature_updated_at_utc', 'started_at_utc', 'finished_at_utc'))):
            raise ArtifactValidationError('SCAN_EVIDENCE_NOT_APPROVED')
        return evidence

    def _validate_model_package_and_scan(self, claim: _Claim, lease_guard: object | None=None) -> tuple[str, str, str | None, str | None, dict[str, Any] | None, dict[str, Any] | None]:
        if self.store is None or not self.store.configured:
            return ('FAILED', 'FAILED', 'ARTIFACT_STORE_UNAVAILABLE', None, None, None)
        if self.scanner is None:
            return ('FAILED', 'FAILED', 'SCANNER_UNAVAILABLE', None, None, None)
        if claim.kind != 'model_package' or claim.media_type != 'application/zip':
            return ('REJECTED', 'REJECTED', 'ARTIFACT_FORMAT_UNSUPPORTED', None, None, None)
        if not package_scan_profiles_approved():
            return ('FAILED', 'FAILED', 'SCANNER_PROFILE_UNAVAILABLE', None, None, None)
        scan_attempt_id = f'sca_{uuid.uuid4().hex}'
        outer_evidence: dict[str, Any] | None = None
        outer_status = 'FAILED'
        try:
            if lease_guard is not None:
                lease_guard.check()
            target = self.store.prepare_scan_target(claim.storage_key, expected_sha256=claim.sha256, expected_size=claim.size_bytes)
            self.store.assert_scan_target_unchanged(target)
            outer = self.scanner.scan(path=target.path, artifact_id=claim.artifact_id, input_sha256=claim.sha256, input_size_bytes=claim.size_bytes, scan_attempt_id=scan_attempt_id)
            outer_status = str(getattr(outer, 'status', 'FAILED'))
            if lease_guard is not None:
                lease_guard.check()
            outer_evidence = _evidence_payload(getattr(outer, 'evidence', None))
            self.store.assert_scan_target_unchanged(target)
            approved_profiles = approved_package_profile_ids()
            outer_evidence = self._clean_scan_evidence(outer, artifact_id=claim.artifact_id, sha256=claim.sha256, size_bytes=claim.size_bytes, scan_attempt_id=scan_attempt_id, approved_profiles=approved_profiles)
            package = inspect_model_package(target.path, expected_sha256=claim.sha256, expected_size_bytes=claim.size_bytes)
            members = scan_model_package_members(package=package, target=target, store=self.store, scanner=self.scanner, artifact_id=claim.artifact_id, lease_guard=lease_guard)
            member_records: list[dict[str, Any]] = []
            if len(members) != 3 or {record.get('name') for record in members} != {'manifest.json', 'weights.safetensors', 'golden.json'}:
                raise ArtifactValidationError('PACKAGE_MEMBER_SCAN_SET_INVALID')
            for record in members:
                if record['scan_status'] != 'PASSED':
                    raise ArtifactValidationError('PACKAGE_SCAN_NOT_PASSED')
                evidence = self._clean_evidence_payload(record['evidence'], artifact_id=claim.artifact_id, sha256=record['sha256'], size_bytes=record['size_bytes'], scan_attempt_id=record['scan_attempt_id'], approved_profiles=approved_profiles)
                if evidence['profile_id'] != outer_evidence['profile_id']:
                    raise ArtifactValidationError('PACKAGE_SCAN_PROFILE_MISMATCH')
                if any((evidence[field] != outer_evidence[field] for field in ('provider_id', 'product_version', 'engine_version', 'signature_version', 'signature_updated_at_utc'))):
                    raise ArtifactValidationError('PACKAGE_SCAN_ENGINE_IDENTITY_MISMATCH')
                member_records.append({'name': record['name'], 'sha256': record['sha256'], 'size_bytes': record['size_bytes'], 'scan_attempt_id': record['scan_attempt_id'], 'scan_status': record['scan_status'], 'verdict': evidence['verdict'], 'profile_id': evidence['profile_id'], 'evidence': evidence})
            self.store.assert_scan_target_unchanged(target)
            if lease_guard is not None:
                lease_guard.check()
            report = {'schema_version': '1.0', 'status': 'SUCCEEDED', 'validation_job_id': claim.job_id, 'job_id': claim.job_id, 'attempt': claim.attempt, 'attempt_id': claim.attempt_id, 'project_id': claim.project_id, 'artifact_id': claim.artifact_id, 'validation_status': 'SUCCEEDED', 'scan_attempt_id': scan_attempt_id, 'container': {'sha256': claim.sha256, 'size_bytes': claim.size_bytes, 'identity': list(target.identity), 'verdict': outer_evidence['verdict'], 'profile_id': outer_evidence['profile_id']}, 'members': member_records}
            return ('SUCCEEDED', 'PASSED', None, scan_attempt_id, outer_evidence, report)
        except StorageContentInvalid:
            return ('FAILED', 'FAILED', 'ARTIFACT_CONTENT_IDENTITY_INVALID', scan_attempt_id, outer_evidence, None)
        except PackageValidationError as exc:
            return ('REJECTED', 'REJECTED', exc.code, scan_attempt_id, outer_evidence, None)
        except ModelPackageScanError as exc:
            return ('FAILED', 'FAILED', exc.code, scan_attempt_id, outer_evidence, None)
        except ArtifactValidationError as exc:
            status = 'REJECTED' if outer_status == 'REJECTED' else 'FAILED'
            scan_status = 'REJECTED' if outer_status == 'REJECTED' else 'FAILED'
            return (status, scan_status, exc.code, scan_attempt_id, outer_evidence, None)
        except Exception:
            return ('FAILED', 'FAILED', 'ARTIFACT_VALIDATION_FAILED', scan_attempt_id, outer_evidence, None)

    def _validate_and_scan(self, claim: _Claim) -> tuple[str, str, str | None, str | None, dict[str, Any] | None]:
        dataset_policy = None
        if claim.kind == 'dataset_package':
            dataset_policy = self.dataset_csv_policy
            if not isinstance(dataset_policy, DatasetCsvScanPolicy) or not dataset_policy.profile_id or dataset_policy.profile_id not in self.reviewed_dataset_profile_ids:
                return ('FAILED', 'FAILED', 'DATASET_CSV_PROFILE_NOT_APPROVED', None, None)
            if claim.media_type != 'text/csv' or dataset_policy.project_id != claim.project_id or dataset_policy.allowed_kind != claim.kind or (dataset_policy.source_ref_type != claim.source_ref_type) or (not isinstance(dataset_policy.encoding, str)) or (dataset_policy.encoding not in {'utf-8', 'utf-8-sig', 'gb18030'}) or isinstance(dataset_policy.max_size_bytes, bool) or (not isinstance(dataset_policy.max_size_bytes, int)) or (not 0 < dataset_policy.max_size_bytes <= 16 * 1024 * 1024) or (not isinstance(dataset_policy.allowed_inputs, frozenset)) or ((claim.sha256, claim.size_bytes) not in dataset_policy.allowed_inputs):
                return ('REJECTED', 'REJECTED', 'DATASET_CSV_ADMISSION_INVALID', None, None)
        if self.store is None or not self.store.configured:
            return ('FAILED', 'FAILED', 'ARTIFACT_STORE_UNAVAILABLE', None, None)
        if dataset_policy is not None and claim.size_bytes > min(dataset_policy.max_size_bytes, self.store.max_size_bytes):
            return ('REJECTED', 'REJECTED', 'DATASET_CSV_SIZE_LIMIT_EXCEEDED', None, None)
        if dataset_policy is None:
            scanner = self.scanner
        else:
            scanner = self.dataset_scanner
            if scanner is None and self.session_factory is None:
                scanner = self.scanner
        if scanner is None:
            return ('FAILED', 'FAILED', 'SCANNER_UNAVAILABLE', None, None)
        if claim.kind != 'batch_input' and dataset_policy is None or claim.media_type != 'text/csv':
            return ('REJECTED', 'REJECTED', 'ARTIFACT_FORMAT_UNSUPPORTED', None, None)
        scan_attempt_id = claim.scan_attempt_id or f'sca_{uuid.uuid4().hex}'
        target: ScanTarget | None = None
        try:
            target = self.store.prepare_scan_target(claim.storage_key, expected_sha256=claim.sha256, expected_size=claim.size_bytes)
            scan_args = {'path': target.path, 'artifact_id': claim.artifact_id, 'input_sha256': claim.sha256, 'input_size_bytes': claim.size_bytes, 'scan_attempt_id': scan_attempt_id}
            if dataset_policy is not None:
                dataset_scan = getattr(scanner, 'scan_dataset', None)
                if not callable(dataset_scan) or not isinstance(getattr(target, 'identity', None), tuple):
                    return ('FAILED', 'FAILED', 'DATASET_SCANNER_BINDING_UNAVAILABLE', scan_attempt_id, None)
                outcome = dataset_scan(**scan_args, project_id=claim.project_id, profile_id=dataset_policy.profile_id, target_identity=target.identity)
            else:
                outcome = scanner.scan(**scan_args)
            if getattr(outcome, 'status', None) not in {'PASSED', 'REJECTED', 'FAILED'}:
                return ('FAILED', 'FAILED', 'SCAN_OUTCOME_INVALID', scan_attempt_id, None)
            evidence = _evidence_payload(getattr(outcome, 'evidence', None))
            if evidence is None:
                return ('FAILED', 'FAILED', 'SCAN_EVIDENCE_MISSING', scan_attempt_id, None)
            if evidence['artifact_id'] != claim.artifact_id or evidence['scan_attempt_id'] != scan_attempt_id or evidence['input_sha256'] != claim.sha256 or (evidence['input_size_bytes'] != claim.size_bytes):
                return ('FAILED', 'FAILED', 'SCAN_EVIDENCE_BINDING_INVALID', scan_attempt_id, evidence)
            if dataset_policy is not None and evidence['profile_id'] != dataset_policy.profile_id:
                return ('FAILED', 'FAILED', 'DATASET_CSV_PROFILE_EVIDENCE_MISMATCH', scan_attempt_id, evidence)
            if evidence['verdict'] not in {'CLEAN', 'DETECTED', 'INDETERMINATE'}:
                return ('FAILED', 'FAILED', 'SCAN_VERDICT_INVALID', scan_attempt_id, evidence)
            if outcome.status == 'PASSED' and (evidence['verdict'] != 'CLEAN' or evidence['exit_code'] != 0 or evidence['output_truncated'] is not False or any((evidence.get(field) in (None, '') for field in ('product_version', 'engine_version', 'signature_version', 'signature_updated_at_utc', 'profile_id')))):
                return ('FAILED', 'FAILED', 'SCAN_EVIDENCE_NOT_APPROVED', scan_attempt_id, evidence)
            self.store.assert_scan_target_unchanged(target)
            scan_status = str(outcome.status)
            if outcome.status != 'PASSED':
                return ('REJECTED' if outcome.status == 'REJECTED' else 'FAILED', scan_status, 'MALWARE_SCAN_NOT_PASSED', scan_attempt_id, evidence)
            csv_error = _csv_structure_check(target.path, encoding=dataset_policy.encoding, max_rows=10000, max_columns=64) if dataset_policy is not None else _csv_structure_check(target.path)
            self.store.assert_scan_target_unchanged(target)
            if csv_error:
                return ('REJECTED', 'PASSED', csv_error, scan_attempt_id, evidence)
            return ('SUCCEEDED', 'PASSED', None, scan_attempt_id, evidence)
        except StorageContentInvalid:
            return ('FAILED', 'FAILED', 'ARTIFACT_CONTENT_IDENTITY_INVALID', scan_attempt_id, None)
        except ArtifactValidationError as exc:
            return ('FAILED', 'FAILED', exc.code, scan_attempt_id, None)
        except Exception:
            return ('FAILED', 'FAILED', 'ARTIFACT_VALIDATION_FAILED', scan_attempt_id, None)

    async def handle(self, payload: Mapping[str, Any], *, event_type: str='job.execute', lease_guard: object | None=None) -> object:
        if event_type != 'job.execute':
            raise ArtifactValidationError('WORKER_EVENT_UNSUPPORTED')
        claim = await self._claim(payload, lease_guard=lease_guard)
        if claim is None:
            return {'status': 'IGNORED'}
        if claim.kind == 'model_package':
            status, scan_status, error_code, scan_attempt_id, evidence, package_report = await asyncio.to_thread(self._validate_model_package_and_scan, claim, lease_guard)
        else:
            status, scan_status, error_code, scan_attempt_id, evidence = await asyncio.to_thread(self._validate_and_scan, claim)
            package_report = None
        if lease_guard is not None:
            lease_guard.check()
        async with self.session_factory() as session:
            async with session.begin():
                await session.run_sync(lambda sync: self._finish_sync(sync, claim, status=status, scan_status=scan_status, error_code=error_code, scan_evidence=evidence, scan_attempt_id=scan_attempt_id, lease_guard=lease_guard, package_scan_report=package_report))
        return {'status': status}

    async def execute(self, payload: Mapping[str, Any], lease_guard: object | None=None) -> object:
        return await self.handle(payload, lease_guard=lease_guard)

    async def cancel(self, payload: Mapping[str, Any], lease_guard: object | None=None) -> object:
        del payload, lease_guard
        raise ArtifactValidationError('ARTIFACT_VALIDATION_NOT_CANCELABLE')
_TERMINAL = frozenset({JobStatus.SUCCEEDED, JobStatus.PARTIAL, JobStatus.FAILED, JobStatus.CANCELED, JobStatus.TIMED_OUT, JobStatus.INTERRUPTED})
__all__ = ['ArtifactValidationError', 'ArtifactValidationWorker']
