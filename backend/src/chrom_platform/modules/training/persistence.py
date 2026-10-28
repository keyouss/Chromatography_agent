"""Module-owned persistence adapters.

The in-memory adapter is intended for contract tests and local composition.
Production assembly can provide a repository backed by the same port without
changing the service or worker.  IDs and rows are intentionally independent
from task-core tables so a trimmed training build has a complete boundary.
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from threading import RLock
from typing import Any, Callable, Sequence
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, MetaData, String, Text, UniqueConstraint, select, update
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from chrom_platform.infrastructure.models import Job, JobAttempt, JobEvent, OutboxEvent
from chrom_platform.task_core.service import add_job_rows, sync_job_lifecycle
from chrom_platform.task_core.repository import SQLAlchemyTaskRepository
from chrom_platform.core.dto import ResourceRef

from .errors import TrainingConflictError, TrainingNotFoundError
from .schemas import (
    CandidateResult,
    EvaluationProtocol,
    Experiment,
    ResourceBudget,
    TrainingAuditEvent,
    TrainingCandidate,
    TrainingJob,
    TrainingOutbox,
    TrainingTemplate,
    WorkerMessage,
    ExperimentMetrics,
    TrainingArtifact,
    TrainingEvent,
    LogEntry,
)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _aware_dt(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


class TrainingBase(DeclarativeBase):
    """Metadata owned by the optional training build."""

    metadata = MetaData(naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_name)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    })


MYSQL_TABLE_KWARGS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}


class TrainingTemplateRow(TrainingBase):
    __tablename__ = "training_template"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_type: Mapped[str] = mapped_column(String(96), nullable=False)
    definition: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    __table_args__ = (MYSQL_TABLE_KWARGS,)


class TrainingExperimentRow(TrainingBase):
    __tablename__ = "training_experiment"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(96), nullable=False)
    # Cross-module FK is attached by M3 assembly; the trimmed training
    # registry stays independently mappable.
    data_version_id: Mapped[str] = mapped_column(String(96), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    etag: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    candidate_status: Mapped[str] = mapped_column(String(32), nullable=False)
    current_attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    spec: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    validation_report: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    candidate_model_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    spec_locked: Mapped[bool] = mapped_column(nullable=False, default=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    __table_args__ = (
        CheckConstraint("revision >= 1", name="training_experiment_revision_positive"),
        CheckConstraint("CHAR_LENGTH(config_hash) = 64", name="training_experiment_config_hash_length"),
        CheckConstraint("status IN ('DRAFT','VALIDATING','INVALID','READY','QUEUED','RUNNING','CANCEL_REQUESTED','SUCCEEDED','FAILED','CANCELED','TIMED_OUT','INTERRUPTED')", name="training_experiment_status"),
        CheckConstraint("candidate_status IN ('NOT_REQUESTED','VALIDATING','CREATED','REJECTED')", name="training_experiment_candidate_status"),
        Index("ix_training_experiment_project_status", "project_id", "status", "created_at"),
        MYSQL_TABLE_KWARGS,
    )


class TrainingJobRow(TrainingBase):
    __tablename__ = "training_job"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(96), nullable=False)
    experiment_id: Mapped[str] = mapped_column(String(96), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    dispatch_id: Mapped[str] = mapped_column(String(96), nullable=False, unique=True)
    result_ref: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    execution_token: Mapped[str | None] = mapped_column(String(128), nullable=True)
    fencing_token: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    __table_args__ = (
        CheckConstraint("attempt >= 1", name="training_job_attempt_positive"),
        CheckConstraint("status IN ('QUEUED','RUNNING','CANCEL_REQUESTED','SUCCEEDED','FAILED','CANCELED','TIMED_OUT','INTERRUPTED')", name="training_job_status"),
        Index("ix_training_job_project_status", "project_id", "status", "created_at"),
        MYSQL_TABLE_KWARGS,
    )


class TrainingOutboxRow(TrainingBase):
    __tablename__ = "training_outbox"
    dispatch_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(96), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_token: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (
        CheckConstraint("attempts >= 0", name="training_outbox_attempts_nonnegative"),
        CheckConstraint("status IN ('PENDING','PROCESSING','SENT','FAILED')", name="training_outbox_status"),
        Index("ix_training_outbox_status_created", "status", "created_at"),
        MYSQL_TABLE_KWARGS,
    )


class TrainingAuditEventRow(TrainingBase):
    __tablename__ = "training_audit_event"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(96), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(96), nullable=False)
    action: Mapped[str] = mapped_column(String(160), nullable=False)
    resource_ref: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    confirmation_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    config_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    __table_args__ = (
        CheckConstraint("result IN ('SUCCEEDED','FAILED')", name="training_audit_event_result"),
        CheckConstraint("config_hash IS NULL OR CHAR_LENGTH(config_hash) = 64", name="training_audit_event_config_hash_length"),
        Index("ix_training_audit_event_project_created", "project_id", "created_at"),
        MYSQL_TABLE_KWARGS,
    )


class TrainingCandidateRow(TrainingBase):
    __tablename__ = "training_candidate"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(96), nullable=False)
    experiment_id: Mapped[str] = mapped_column(String(96), nullable=False, unique=True)
    artifact_id: Mapped[str] = mapped_column(String(96), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    published: Mapped[bool] = mapped_column(nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    __table_args__ = (
        CheckConstraint("status IN ('CANDIDATE','REJECTED')", name="training_candidate_status"),
        Index("ix_training_candidate_project_status", "project_id", "status", "created_at"),
        MYSQL_TABLE_KWARGS,
    )


class TrainingMetricRow(TrainingBase):
    """Immutable worker metrics kept separate from the experiment snapshot."""

    __tablename__ = "experiment_metric"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    experiment_id: Mapped[str] = mapped_column(String(96), nullable=False)
    metric_key: Mapped[str] = mapped_column(String(128), nullable=False)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    split: Mapped[str] = mapped_column(String(32), nullable=False, default="test")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    __table_args__ = (
        UniqueConstraint("experiment_id", "metric_key", "split", name="uq_experiment_metric_key"),
        Index("ix_experiment_metric_experiment", "experiment_id", "created_at"),
        MYSQL_TABLE_KWARGS,
    )


class TrainingIdempotencyRow(TrainingBase):
    """Submission idempotency is module-owned and transaction-scoped."""

    __tablename__ = "training_idempotency"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(96), nullable=False)
    project_id: Mapped[str] = mapped_column(String(96), nullable=False)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    job_id: Mapped[str] = mapped_column(String(96), ForeignKey("training_job.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    __table_args__ = (
        UniqueConstraint("actor_id", "project_id", "key", name="uq_training_idempotency_scope_key"),
        # Keep the explicit name aligned with 0004.  TrainingBase's naming
        # convention adds ``ck_training_idempotency_`` to this token, so using
        # the table-prefixed token here would produce a different constraint
        # name from the deployed migration.
        CheckConstraint("CHAR_LENGTH(request_hash) = 64", name="request_hash_length"),
        Index("ix_training_idempotency_job", "job_id"),
        MYSQL_TABLE_KWARGS,
    )


@dataclass(frozen=True)
class TrainingExecutionClaim:
    """Worker lease returned by the short, locking claim transaction."""

    job: TrainingJob
    experiment: Experiment
    execution_token: str
    fencing_token: int
    lease_until: datetime


@dataclass(frozen=True)
class TrainingDispatchLease:
    """Transport lease fenced by the durable outbox attempts counter.

    ``token`` is the monotonic attempts value and is checked together with the
    durable owner and expiry on every ack/retry.
    """

    message: WorkerMessage
    owner: str
    token: int
    lease_expires_at: datetime | None = None


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


class StaticTrainingTemplateRegistry:
    """A build-time registry; no importlib or request-controlled lookup exists."""

    def __init__(self, templates: Sequence[TrainingTemplate] = (), handlers: dict[tuple[str, str], Callable[..., Any]] | None = None):
        self._templates = {(item.id, item.version): item for item in templates}
        self._handlers = dict(handlers or {})

    def list(self, *, task_type: str | None = None) -> list[TrainingTemplate]:
        values = list(self._templates.values())
        return [item for item in values if task_type is None or item.task_type == task_type]

    def get(self, template_id: str, version: str) -> TrainingTemplate | None:
        return self._templates.get((template_id, version))

    def handler(self, template_id: str, version: str) -> Callable[..., Any] | None:
        return self._handlers.get((template_id, version))


class StaticEvaluationProtocolRegistry:
    def __init__(self, protocols: Sequence[EvaluationProtocol | object] = ()):
        self._protocols = {}
        for value in protocols:
            self._protocols[(str(value.id), str(value.version))] = value

    def get(self, protocol_id: str, version: str) -> object | None:
        return self._protocols.get((protocol_id, version))


class SQLAlchemyTrainingRepository:
    """Production repository bound to a caller-owned SQLAlchemy Session.

    The adapter never commits or closes the session.  ``transaction`` uses a
    savepoint so request/worker code can compose this module with the shared
    transaction.  Worker claim and finish perform all stale checks before any
    write, and finish requires the execution and fencing token issued by claim.
    """

    def __init__(self, session: Session, *, id_factory: Callable[[str], str] = new_id, lease_seconds: int = 60):
        if callable(session) or not all(hasattr(session, name) for name in ("add", "add_all", "flush", "scalar", "scalars", "get")):
            raise TypeError("SQLAlchemyTrainingRepository requires a caller-owned Session")
        self.session = session
        self._id_factory = id_factory
        self.lease_seconds = max(1, int(lease_seconds))
        # Production transport is the platform OutboxEvent.  The module table
        # remains mappable for trimmed/legacy builds but is not a second queue.
        self.canonical_transport = True

    @contextmanager
    def transaction(self):
        with self.session.begin_nested():
            yield self

    @staticmethod
    def _conflict(message: str) -> TrainingConflictError:
        return TrainingConflictError(message)

    @staticmethod
    def _json(value: object) -> Any:
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        return value

    def _experiment_dto(self, row: TrainingExperimentRow | None) -> Experiment | None:
        if row is None:
            return None
        return Experiment.model_validate({
            "id": row.id, "revision": row.revision, "config_hash": row.config_hash,
            "etag": row.etag, "spec": row.spec, "status": row.status,
            "validation_report": row.validation_report, "job_id": row.job_id,
            "current_attempt": int(getattr(row, "current_attempt", 0) or 0),
            "candidate_status": row.candidate_status, "candidate_model_id": row.candidate_model_id,
            "spec_locked": bool(getattr(row, "spec_locked", False)), "created_at": row.created_at,
            "started_at": getattr(row, "started_at", None), "finished_at": getattr(row, "finished_at", None),
            "error": getattr(row, "error", None),
        })

    def _job_dto(self, row: TrainingJobRow | None) -> TrainingJob | None:
        if row is None:
            return None
        return TrainingJob.model_validate({
            "id": row.id, "project_id": row.project_id, "experiment_id": row.experiment_id,
            "status": row.status, "attempt": row.attempt, "dispatch_id": row.dispatch_id,
            "created_at": row.created_at, "started_at": getattr(row, "started_at", None),
            "finished_at": getattr(row, "finished_at", None), "result_ref": row.result_ref,
            "error": row.error,
        })

    def _outbox_dto(self, row: TrainingOutboxRow | None) -> TrainingOutbox | None:
        if row is None:
            return None
        return TrainingOutbox.model_validate({
            "dispatch_id": row.dispatch_id, "job_id": row.job_id, "status": row.status,
            "attempts": row.attempts, "payload": row.payload, "created_at": row.created_at,
            "lease_owner": getattr(row, "lease_owner", None), "lease_token": int(getattr(row, "lease_token", 0) or 0),
            "lease_expires_at": getattr(row, "lease_expires_at", None),
        })

    def _sync_platform_job(
        self,
        row: TrainingJobRow,
        *,
        status: str,
        result_ref: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
    ) -> None:
        """Project training state onto the canonical M1 task row."""
        platform_job = self.session.get(Job, row.id)
        if platform_job is None:
            # The composed M3 metadata adds the real FK.  This no-op keeps
            # module-owned metadata usable in trimmed contract tests.
            return
        old_status = platform_job.status
        platform_job.status = status
        platform_job.attempt = row.attempt
        platform_job.result_ref = result_ref if result_ref is not None else platform_job.result_ref
        platform_job.error = error if error is not None else platform_job.error
        if started_at is not None:
            platform_job.started_at = started_at
        if finished_at is not None:
            platform_job.finished_at = finished_at
        platform_job.updated_at = now_utc()
        if old_status != status:
            platform_job.revision = int(platform_job.revision or 1) + 1
            platform_job.etag = f'"{platform_job.revision}"'
            attempt = self.session.scalar(select(JobAttempt).where(JobAttempt.job_id == row.id, JobAttempt.attempt == row.attempt).with_for_update())
            if attempt is not None:
                attempt.status = status
                attempt.result_ref = result_ref if result_ref is not None else attempt.result_ref
                attempt.error = error if error is not None else attempt.error
                attempt.started_at = started_at or attempt.started_at
                attempt.finished_at = finished_at or attempt.finished_at
            seq = self.session.scalar(select(JobEvent.seq).where(JobEvent.job_id == row.id).order_by(JobEvent.seq.desc()).limit(1))
            self.session.add(JobEvent(id=self._id_factory("event"), job_id=row.id, stream_id=row.id, seq=int(seq if seq is not None else -1) + 1, type=f"job.{status.lower()}", schema_version="1.0", data={"status": status, "attempt": row.attempt}, timestamp=now_utc()))

    def _sync_platform_outbox(self, row: TrainingOutboxRow, *, status: str, owner: str | None = None, token: int | None = None, expires_at: datetime | None = None) -> None:
        shared = self.session.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_type == "job", OutboxEvent.aggregate_id == row.job_id, OutboxEvent.event_type == "job.execute", OutboxEvent.dispatch_id == row.dispatch_id).with_for_update())
        if shared is None:
            return
        shared.status = status
        shared.attempts = int(row.attempts or 0)
        shared.lease_owner = owner
        shared.lease_token = int(token if token is not None else getattr(row, "lease_token", 0) or 0)
        shared.lease_expires_at = expires_at

    def save_experiment(self, value: Experiment) -> Experiment:
        row = self.session.get(TrainingExperimentRow, value.id)
        data = value.model_dump(mode="json")
        if row is None:
            row = TrainingExperimentRow(id=value.id)
            self.session.add(row)
        elif row.project_id != value.spec.project_id:
            raise self._conflict("experiment belongs to another project")
        elif int(row.revision) > int(value.revision):
            raise self._conflict("experiment revision is stale")
        elif bool(getattr(row, "spec_locked", False)) and (
            row.config_hash != value.config_hash or row.spec != data["spec"] or int(row.revision) != int(value.revision)
        ):
            raise self._conflict("submitted experiment specification is immutable")
        for key in ("project_id", "data_version_id", "revision", "config_hash", "etag", "status", "candidate_status", "current_attempt", "spec", "validation_report", "job_id", "candidate_model_id", "spec_locked", "error"):
            setattr(row, key, data["spec"]["project_id"] if key == "project_id" else (data["spec"]["data_version_id"] if key == "data_version_id" else data.get(key)))
        row.created_at, row.started_at, row.finished_at = value.created_at, value.started_at, value.finished_at
        self.session.flush()
        return value

    def get_experiment(self, project_id: str, experiment_id: str) -> Experiment | None:
        row = self.session.scalar(select(TrainingExperimentRow).where(TrainingExperimentRow.id == experiment_id, TrainingExperimentRow.project_id == project_id))
        return self._experiment_dto(row)

    def list_experiments(self, project_id: str, *, task_type: str | None = None, status: str | None = None) -> list[Experiment]:
        stmt = select(TrainingExperimentRow).where(TrainingExperimentRow.project_id == project_id)
        if task_type is not None:
            stmt = stmt.where(TrainingExperimentRow.spec["task_type"].as_string() == task_type)
        if status is not None:
            stmt = stmt.where(TrainingExperimentRow.status == status)
        return [self._experiment_dto(row) for row in self.session.scalars(stmt.order_by(TrainingExperimentRow.created_at.desc(), TrainingExperimentRow.id.desc()))]

    def create_submission(self, *, experiment: Experiment, confirmation_id: str, actor_id: str, idempotency_key: str | None) -> tuple[TrainingJob, bool]:
        row = self.session.scalar(select(TrainingExperimentRow).where(TrainingExperimentRow.id == experiment.id, TrainingExperimentRow.project_id == experiment.spec.project_id).with_for_update())
        if row is None:
            raise TrainingNotFoundError("training experiment does not exist")
        current = self._experiment_dto(row)
        if current is None:
            raise TrainingNotFoundError("training experiment does not exist")
        digest = _hash({"experiment_id": current.id, "revision": current.revision, "config_hash": current.config_hash, "confirmation_id": confirmation_id})
        if idempotency_key:
            idem = self.session.scalar(select(TrainingIdempotencyRow).where(TrainingIdempotencyRow.actor_id == actor_id, TrainingIdempotencyRow.project_id == current.spec.project_id, TrainingIdempotencyRow.key == idempotency_key).with_for_update())
            if idem is not None:
                if idem.request_hash != digest:
                    raise self._conflict("idempotency key was reused for another submission")
                replay = self.get_job(idem.job_id)
                if replay is None:
                    raise self._conflict("idempotency record has no job")
                return replay, True
        if current.job_id:
            if idempotency_key:
                replay = self.get_submission_replay(experiment=current, confirmation_id=confirmation_id, actor_id=actor_id, idempotency_key=idempotency_key)
                if replay is not None:
                    return replay, True
            raise self._conflict("experiment already has a submission")
        now = now_utc()
        job_id, dispatch_id = self._id_factory("job"), self._id_factory("dispatch")
        job = TrainingJob(id=job_id, project_id=current.spec.project_id, experiment_id=current.id, status="QUEUED", attempt=1, dispatch_id=dispatch_id, created_at=now)
        outbox = TrainingOutbox(dispatch_id=dispatch_id, job_id=job_id, status="PENDING", attempts=0, payload=WorkerMessage(job_id=job_id, attempt=1, dispatch_id=dispatch_id), created_at=now)
        # The shared task rows and module execution extension are one unit.
        # The caller owns the surrounding transaction, so a failed extension
        # insert rolls back the public Job as well.
        add_job_rows(
            SQLAlchemyTaskRepository(lambda: self.session), self.session,
            project_id=current.spec.project_id, owner_id=actor_id,
            kind="training", resource_ref=ResourceRef(type="experiment", id=current.id),
            cancelable=True, retryable=False, resume_mode="none", deadline_at=None,
            job_id=job_id, dispatch_id=dispatch_id,
        )
        row.job_id, row.status, row.spec_locked = job_id, "QUEUED", True
        row.current_attempt = 1
        self.session.add(TrainingJobRow(id=job.id, project_id=job.project_id, experiment_id=job.experiment_id, status=job.status, attempt=1, dispatch_id=dispatch_id, created_at=now))
        if not self.canonical_transport:
            self.session.add(TrainingOutboxRow(dispatch_id=dispatch_id, job_id=job_id, status="PENDING", attempts=0, payload=outbox.payload.model_dump(mode="json"), created_at=now, lease_token=0))
        self.session.add(TrainingAuditEventRow(id=self._id_factory("audit"), project_id=current.spec.project_id, actor_id=actor_id, action="training.submit", resource_ref={"type": "experiment", "id": current.id}, result="SUCCEEDED", confirmation_id=confirmation_id, job_id=job_id, config_hash=current.config_hash, created_at=now))
        if idempotency_key:
            self.session.add(TrainingIdempotencyRow(id=self._id_factory("idem"), actor_id=actor_id, project_id=current.spec.project_id, key=idempotency_key, request_hash=digest, job_id=job_id, created_at=now))
        self.session.flush()
        return job, False

    def get_submission_replay(self, *, experiment: Experiment, confirmation_id: str, actor_id: str, idempotency_key: str) -> TrainingJob | None:
        """Return a replay only when the caller owns the original idempotency scope."""
        digest = _hash({"experiment_id": experiment.id, "revision": experiment.revision, "config_hash": experiment.config_hash, "confirmation_id": confirmation_id})
        row = self.session.scalar(select(TrainingIdempotencyRow).where(
            TrainingIdempotencyRow.actor_id == actor_id,
            TrainingIdempotencyRow.project_id == experiment.spec.project_id,
            TrainingIdempotencyRow.key == idempotency_key,
        ))
        if row is None:
            return None
        if row.request_hash != digest:
            raise self._conflict("idempotency key was reused for another submission")
        replay = self.get_job(row.job_id)
        if replay is None or experiment.job_id != replay.id:
            raise self._conflict("idempotency record does not match the experiment")
        return replay

    def get_job(self, job_id: str) -> TrainingJob | None:
        return self._job_dto(self.session.get(TrainingJobRow, job_id))

    def save_job(self, value: TrainingJob) -> TrainingJob:
        row = self.session.get(TrainingJobRow, value.id)
        if row is None:
            row = TrainingJobRow(id=value.id)
            self.session.add(row)
        elif row.project_id != value.project_id or row.experiment_id != value.experiment_id or row.attempt != value.attempt or row.dispatch_id != value.dispatch_id:
            raise self._conflict("job identity is immutable")
        data = value.model_dump(mode="json")
        for key in ("project_id", "experiment_id", "status", "attempt", "dispatch_id", "result_ref", "error"):
            setattr(row, key, data.get(key))
        row.created_at, row.started_at, row.finished_at = value.created_at, value.started_at, value.finished_at
        self._sync_platform_job(row, status=value.status, result_ref=value.result_ref, error=value.error, started_at=value.started_at, finished_at=value.finished_at)
        self.session.flush()
        return value

    def get_outbox(self, dispatch_id: str) -> TrainingOutbox | None:
        return self._outbox_dto(self.session.get(TrainingOutboxRow, dispatch_id))

    def save_outbox(self, value: TrainingOutbox) -> TrainingOutbox:
        row = self.session.get(TrainingOutboxRow, value.dispatch_id)
        if row is None:
            row = TrainingOutboxRow(dispatch_id=value.dispatch_id)
            self.session.add(row)
        elif row.job_id != value.job_id:
            raise self._conflict("dispatch identity is immutable")
        data = value.model_dump(mode="json")
        for key in ("job_id", "status", "attempts", "payload", "lease_owner", "lease_token", "lease_expires_at"):
            setattr(row, key, data[key])
        row.created_at = value.created_at
        self.session.flush()
        return value

    def cancel_outbox(self, dispatch_id: str) -> bool:
        if self.canonical_transport:
            row = self.session.scalar(select(OutboxEvent).where(OutboxEvent.dispatch_id == dispatch_id, OutboxEvent.event_type == "job.execute").with_for_update())
            if row is None or row.status in {"SENT", "FAILED"}:
                return False
            # FAILED is non-deliverable for a canceled Job because every
            # canonical claim also fences on Job.status == QUEUED.
            row.status = "FAILED"
            row.lease_owner = None
            row.lease_expires_at = None
            self.session.flush()
            return True
        row = self.session.scalar(select(TrainingOutboxRow).where(TrainingOutboxRow.dispatch_id == dispatch_id).with_for_update())
        if row is None or row.status != "PENDING":
            return False
        row.status = "FAILED"
        row.lease_owner = None
        row.lease_expires_at = None
        self._sync_platform_outbox(row, status="FAILED", owner=None, token=getattr(row, "lease_token", 0), expires_at=None)
        self.session.flush()
        return True

    @staticmethod
    def _transport_message(row: TrainingOutboxRow) -> WorkerMessage:
        payload = dict(row.payload or {})
        if set(payload) != {"job_id", "attempt", "dispatch_id"}:
            raise TrainingConflictError("training outbox payload is not a stable three-field message")
        message = WorkerMessage.model_validate(payload)
        if message.job_id != row.job_id or message.dispatch_id != row.dispatch_id:
            raise TrainingConflictError("training outbox identity does not match payload")
        return message

    def claim_outbox(self, message: WorkerMessage, *, owner: str) -> TrainingDispatchLease | None:
        """Claim transport delivery; this is the sole outbox owner."""
        if not isinstance(owner, str) or not owner:
            raise ValueError("outbox owner is required")
        row = self.session.scalar(select(TrainingOutboxRow).where(TrainingOutboxRow.dispatch_id == message.dispatch_id, TrainingOutboxRow.job_id == message.job_id).with_for_update())
        now = now_utc()
        expired = row is not None and row.status == "PROCESSING" and row.lease_expires_at is not None and _aware_dt(row.lease_expires_at) <= now
        if row is None or row.status not in {"PENDING", "FAILED"} and not expired:
            return None
        stable = self._transport_message(row)
        if stable != message:
            return None
        job = self.session.scalar(select(TrainingJobRow).where(TrainingJobRow.id == message.job_id).with_for_update())
        if job is None or job.status != "QUEUED" or job.attempt != message.attempt or job.dispatch_id != message.dispatch_id:
            return None
        row.status = "PROCESSING"
        row.attempts = int(row.attempts or 0) + 1
        row.lease_owner = owner
        row.lease_token = int(row.lease_token or 0) + 1
        row.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
        self._sync_platform_outbox(row, status="PROCESSING", owner=owner, token=row.lease_token, expires_at=row.lease_expires_at)
        self.session.flush()
        return TrainingDispatchLease(message=message, owner=owner, token=int(row.lease_token), lease_expires_at=row.lease_expires_at)

    def claim_next_outbox(self, *, owner: str) -> TrainingDispatchLease | None:
        """Claim the next eligible training message with a MySQL row lock."""
        if not isinstance(owner, str) or not owner:
            raise ValueError("outbox owner is required")
        row = self.session.scalar(
            select(TrainingOutboxRow)
            .where(TrainingOutboxRow.status.in_(("PENDING", "FAILED")))
            .order_by(TrainingOutboxRow.created_at, TrainingOutboxRow.dispatch_id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if row is None:
            return None
        message = self._transport_message(row)
        return self.claim_outbox(message, owner=owner)

    def recover_outbox(self, message: WorkerMessage, *, owner: str) -> TrainingDispatchLease | None:
        """Reclaim a PROCESSING row after an external dispatcher crash.

        There is no timestamp column in the frozen table; callers must invoke
        this only after their broker/process recovery policy declares the old
        owner dead.  The attempts CAS fences the old owner from ack/retry.
        """
        if not isinstance(owner, str) or not owner:
            raise ValueError("outbox owner is required")
        row = self.session.scalar(select(TrainingOutboxRow).where(TrainingOutboxRow.dispatch_id == message.dispatch_id, TrainingOutboxRow.job_id == message.job_id).with_for_update())
        if row is None or row.status != "PROCESSING":
            return None
        stable = self._transport_message(row)
        if stable != message:
            return None
        job = self.session.scalar(select(TrainingJobRow).where(TrainingJobRow.id == message.job_id).with_for_update())
        if job is None or job.attempt != message.attempt or job.dispatch_id != message.dispatch_id:
            return None
        row.attempts = int(row.attempts or 0) + 1
        now = now_utc()
        row.lease_owner = owner
        row.lease_token = int(row.lease_token or 0) + 1
        row.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
        self._sync_platform_outbox(row, status="PROCESSING", owner=owner, token=row.lease_token, expires_at=row.lease_expires_at)
        self.session.flush()
        return TrainingDispatchLease(message=message, owner=owner, token=int(row.lease_token), lease_expires_at=row.lease_expires_at)

    def ack_outbox(self, lease: TrainingDispatchLease) -> bool:
        row = self.session.scalar(select(TrainingOutboxRow).where(TrainingOutboxRow.dispatch_id == lease.message.dispatch_id, TrainingOutboxRow.job_id == lease.message.job_id).with_for_update())
        if row is None or row.status != "PROCESSING" or row.lease_owner != lease.owner or int(getattr(row, "lease_token", 0) or 0) != lease.token:
            return False
        if self._transport_message(row) != lease.message:
            return False
        row.status = "SENT"
        row.lease_expires_at = None
        self._sync_platform_outbox(row, status="SENT", owner=None, token=lease.token, expires_at=None)
        self.session.flush()
        return True

    def retry_outbox(self, lease: TrainingDispatchLease) -> bool:
        row = self.session.scalar(select(TrainingOutboxRow).where(TrainingOutboxRow.dispatch_id == lease.message.dispatch_id, TrainingOutboxRow.job_id == lease.message.job_id).with_for_update())
        if row is None or row.status != "PROCESSING" or row.lease_owner != lease.owner or int(getattr(row, "lease_token", 0) or 0) != lease.token:
            return False
        if self._transport_message(row) != lease.message:
            return False
        row.status = "FAILED"
        row.lease_owner = None
        row.lease_expires_at = None
        self._sync_platform_outbox(row, status="FAILED", owner=None, token=lease.token, expires_at=None)
        self.session.flush()
        return True

    def claim_shared_outbox(self, message: WorkerMessage, *, owner: str) -> TrainingDispatchLease | None:
        row = self.session.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_type == "job", OutboxEvent.aggregate_id == message.job_id, OutboxEvent.dispatch_id == message.dispatch_id, OutboxEvent.event_type == "job.execute").with_for_update())
        job = self.session.get(Job, message.job_id)
        now = now_utc()
        expired = row is not None and row.status == "PROCESSING" and row.lease_expires_at is not None and _aware_dt(row.lease_expires_at) <= now
        if row is None or job is None or job.status != "QUEUED" or (row.status not in {"PENDING", "FAILED"} and not expired) or (row.payload or {}) != message.model_dump(mode="json"):
            return None
        row.status = "PROCESSING"; row.attempts = int(row.attempts or 0) + 1; row.lease_owner = owner; row.lease_token = int(row.lease_token or 0) + 1; row.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
        self.session.flush()
        return TrainingDispatchLease(message=message, owner=owner, token=int(row.lease_token), lease_expires_at=row.lease_expires_at)

    def claim_shared_next(self, *, owner: str) -> TrainingDispatchLease | None:
        row = self.session.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_type == "job", OutboxEvent.event_type == "job.execute", OutboxEvent.status.in_(("PENDING", "FAILED"))).order_by(OutboxEvent.created_at, OutboxEvent.dispatch_id).limit(1).with_for_update(skip_locked=True))
        if row is None:
            return None
        return self.claim_shared_outbox(WorkerMessage.model_validate(row.payload), owner=owner)

    def recover_shared_outbox(self, message: WorkerMessage, *, owner: str) -> TrainingDispatchLease | None:
        row = self.session.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_type == "job", OutboxEvent.aggregate_id == message.job_id, OutboxEvent.dispatch_id == message.dispatch_id, OutboxEvent.event_type == "job.execute").with_for_update())
        if row is None or row.status != "PROCESSING" or (row.payload or {}) != message.model_dump(mode="json"):
            return None
        # Recovery is valid only while the shared Job is still queued.  Lock
        # the Job with the outbox row so cancellation cannot race a new lease.
        job = self.session.scalar(select(Job).where(Job.id == message.job_id).with_for_update())
        job_status = getattr(getattr(job, "status", None), "value", getattr(job, "status", None))
        if job is None or job_status != "QUEUED" or int(getattr(job, "attempt", 0) or 0) != message.attempt:
            row.status = "FAILED"
            row.lease_owner = None
            row.lease_expires_at = None
            self.session.flush()
            return None
        row.attempts = int(row.attempts or 0) + 1; row.lease_owner = owner; row.lease_token = int(row.lease_token or 0) + 1; row.lease_expires_at = now_utc() + timedelta(seconds=self.lease_seconds)
        self.session.flush()
        return TrainingDispatchLease(message=message, owner=owner, token=int(row.lease_token), lease_expires_at=row.lease_expires_at)

    def ack_shared_outbox(self, lease: TrainingDispatchLease) -> bool:
        row = self.session.scalar(select(OutboxEvent).where(OutboxEvent.dispatch_id == lease.message.dispatch_id, OutboxEvent.aggregate_id == lease.message.job_id).with_for_update())
        if row is None or row.status != "PROCESSING" or row.lease_owner != lease.owner or int(row.lease_token or 0) != lease.token:
            return False
        row.status = "SENT"; row.lease_owner = None; row.lease_expires_at = None; self.session.flush(); return True

    def retry_shared_outbox(self, lease: TrainingDispatchLease) -> bool:
        row = self.session.scalar(select(OutboxEvent).where(OutboxEvent.dispatch_id == lease.message.dispatch_id, OutboxEvent.aggregate_id == lease.message.job_id).with_for_update())
        if row is None or row.status != "PROCESSING" or row.lease_owner != lease.owner or int(row.lease_token or 0) != lease.token:
            return False
        row.status = "FAILED"; row.lease_owner = None; row.lease_expires_at = None; self.session.flush(); return True

    def list_audits(self, project_id: str) -> list[TrainingAuditEvent]:
        rows = self.session.scalars(select(TrainingAuditEventRow).where(TrainingAuditEventRow.project_id == project_id).order_by(TrainingAuditEventRow.created_at, TrainingAuditEventRow.id))
        return [TrainingAuditEvent.model_validate({"id": row.id, "project_id": row.project_id, "actor_id": row.actor_id, "action": row.action, "resource_ref": row.resource_ref, "result": row.result, "confirmation_id": row.confirmation_id, "job_id": row.job_id, "config_hash": row.config_hash, "created_at": row.created_at}) for row in rows]

    def save_candidate(self, value: TrainingCandidate) -> TrainingCandidate:
        row = self.session.get(TrainingCandidateRow, value.id)
        if row is None:
            row = TrainingCandidateRow(id=value.id)
            self.session.add(row)
        elif row.project_id != value.project_id or row.experiment_id != value.experiment_id:
            raise self._conflict("candidate identity is immutable")
        data = value.model_dump(mode="json")
        for key in ("project_id", "experiment_id", "artifact_id", "status", "published"):
            setattr(row, key, data[key])
        row.created_at = value.created_at
        self.session.flush()
        return value

    def get_candidate(self, candidate_id: str) -> TrainingCandidate | None:
        row = self.session.get(TrainingCandidateRow, candidate_id)
        if row is None:
            return None
        return TrainingCandidate.model_validate({"id": row.id, "project_id": row.project_id, "experiment_id": row.experiment_id, "artifact_id": row.artifact_id, "status": row.status, "published": row.published, "created_at": row.created_at})

    # Telemetry stores are deliberately separate from the experiment row.  A
    # deployment can bind these methods to the shared JobEvent/log/artifact
    # tables without changing the public service contract; an unbound store
    # returns an honest empty result rather than manufacturing records.
    def list_events(self, project_id: str, experiment_id: str, *, after_seq: int = 0, limit: int = 100) -> tuple[list[TrainingEvent], bool]:
        return [], False

    def list_logs(self, project_id: str, experiment_id: str, *, after_seq: int = 0, limit: int = 100) -> tuple[list[LogEntry], bool]:
        return [], False

    def get_metrics(self, project_id: str, experiment_id: str, *, attempt: int, after_step: int | None = None) -> ExperimentMetrics:
        experiment = self.get_experiment(project_id, experiment_id)
        if experiment is None:
            raise TrainingNotFoundError("experiment does not exist")
        return ExperimentMetrics(experiment_id=experiment_id, attempt=attempt, protocol_snapshot={"id": experiment.spec.evaluation_protocol.id, "version": experiment.spec.evaluation_protocol.version}, series=[], final_metrics=[], next_step=None)

    def list_artifacts(self, project_id: str, experiment_id: str, *, after_id: str | None = None, limit: int = 50) -> tuple[list[TrainingArtifact], bool]:
        return [], False

    def comparison_metrics(self, project_id: str, experiment_id: str, *, protocol_id: str, protocol_version: str) -> dict[str, float] | None:
        return None

    def claim(self, message: WorkerMessage, *, lease_seconds: int | None = None) -> TrainingExecutionClaim | None:
        """Atomically claim only the current queued dispatch."""
        job = self.session.scalar(select(TrainingJobRow).where(TrainingJobRow.id == message.job_id).with_for_update())
        if job is None or job.attempt != message.attempt or job.dispatch_id != message.dispatch_id or job.status != "QUEUED":
            return None
        dispatch = self.session.scalar(select(OutboxEvent).where(OutboxEvent.dispatch_id == message.dispatch_id, OutboxEvent.aggregate_id == message.job_id, OutboxEvent.event_type == "job.execute").with_for_update()) if self.canonical_transport else self.session.scalar(select(TrainingOutboxRow).where(TrainingOutboxRow.dispatch_id == message.dispatch_id, TrainingOutboxRow.job_id == message.job_id).with_for_update())
        # Transport ownership belongs to claim_outbox/recover_outbox.  The
        # business worker may execute only a dispatch already leased by that
        # owner; it does not transition transport state.
        if dispatch is None or dispatch.status != "PROCESSING":
            return None
        experiment_row = self.session.scalar(select(TrainingExperimentRow).where(TrainingExperimentRow.id == job.experiment_id, TrainingExperimentRow.project_id == job.project_id).with_for_update())
        if experiment_row is None or experiment_row.status not in {"QUEUED", "DRAFT", "READY"}:
            return None
        now = now_utc()
        token, fence = self._id_factory("exec"), int(job.fencing_token or 0) + 1
        job.status, job.started_at, job.execution_token, job.fencing_token, job.lease_until = "RUNNING", job.started_at or now, token, fence, now + timedelta(seconds=lease_seconds or self.lease_seconds)
        self._sync_platform_job(job, status="RUNNING", started_at=job.started_at)
        experiment_row.status, experiment_row.started_at, experiment_row.current_attempt = "RUNNING", job.started_at, job.attempt
        self.session.flush()
        return TrainingExecutionClaim(self._job_dto(job), self._experiment_dto(experiment_row), token, fence, job.lease_until)

    def finish(self, message: WorkerMessage, *, execution_token: str, fencing_token: int, status: str, result: CandidateResult | None = None, error: dict[str, Any] | None = None) -> bool:
        """Finish iff this worker still owns the live lease; stale writes are no-ops."""
        job = self.session.scalar(select(TrainingJobRow).where(TrainingJobRow.id == message.job_id).with_for_update())
        if job is None or job.attempt != message.attempt or job.dispatch_id != message.dispatch_id or job.status != "RUNNING" or job.execution_token != execution_token or int(job.fencing_token or 0) != int(fencing_token) or (_aware_dt(job.lease_until) is not None and _aware_dt(job.lease_until) <= now_utc()):
            return False
        experiment = self.session.scalar(select(TrainingExperimentRow).where(TrainingExperimentRow.id == job.experiment_id, TrainingExperimentRow.project_id == job.project_id).with_for_update())
        if experiment is None:
            return False
        dispatch = self.session.scalar(select(OutboxEvent).where(OutboxEvent.dispatch_id == message.dispatch_id, OutboxEvent.aggregate_id == message.job_id, OutboxEvent.event_type == "job.execute").with_for_update()) if self.canonical_transport else self.session.scalar(select(TrainingOutboxRow).where(TrainingOutboxRow.dispatch_id == message.dispatch_id, TrainingOutboxRow.job_id == message.job_id).with_for_update())
        if dispatch is None:
            return False
        now = now_utc()
        result_ref = {"type": "artifact", "id": result.artifact_id} if result is not None and error is None else None
        job.status, job.result_ref, job.error, job.finished_at, job.lease_until = status, result_ref, error, now, None
        self._sync_platform_job(job, status=status, result_ref=result_ref, error=error, finished_at=now)
        experiment.status, experiment.finished_at, experiment.error = status, now, error
        # The transport dispatcher, rather than the business worker, acks the
        # broker delivery after send.  This worker only finishes the business
        # Job and its execution audit.
        if status == "SUCCEEDED":
            experiment.candidate_status = "VALIDATING"
        else:
            experiment.candidate_status, experiment.candidate_model_id = "NOT_REQUESTED", None
        self.session.add(TrainingAuditEventRow(
            id=self._id_factory("audit_execute"),
            project_id=job.project_id,
            actor_id="worker",
            action="training.execute",
            resource_ref={"type": "experiment", "id": experiment.id},
            result="SUCCEEDED" if status == "SUCCEEDED" else "FAILED",
            confirmation_id=None,
            job_id=job.id,
            config_hash=experiment.config_hash,
            created_at=now,
        ))
        self.session.flush()
        return True


class InMemoryTrainingRepository:
    """Small deterministic repository used by tests and development assembly."""

    def __init__(self):
        self._lock = RLock()
        self.experiments: dict[str, Experiment] = {}
        self.jobs: dict[str, TrainingJob] = {}
        self.outbox: dict[str, TrainingOutbox] = {}
        self.audits: list[TrainingAuditEvent] = []
        self.candidates: dict[str, TrainingCandidate] = {}
        self._idempotency: dict[tuple[str, str, str], tuple[str, str]] = {}
        self.events: dict[str, list[TrainingEvent]] = {}
        self.logs: dict[str, list[LogEntry]] = {}
        self.metrics: dict[tuple[str, int], ExperimentMetrics] = {}
        self.artifacts: dict[str, list[TrainingArtifact]] = {}
        self.comparison_results: dict[str, dict[str, float]] = {}
        self._transport_tokens: dict[str, int] = {}

    @contextmanager
    def transaction(self):
        with self._lock:
            snapshot = deepcopy((self.experiments, self.jobs, self.outbox, self.audits, self.candidates, self._idempotency, self.events, self.logs, self.metrics, self.artifacts, self.comparison_results, self._transport_tokens))
            try:
                yield self
            except Exception:
                self.experiments, self.jobs, self.outbox, self.audits, self.candidates, self._idempotency, self.events, self.logs, self.metrics, self.artifacts, self.comparison_results, self._transport_tokens = snapshot
                raise

    def save_experiment(self, value: Experiment) -> Experiment:
        with self._lock:
            self.experiments[value.id] = value.model_copy(deep=True)
            return value.model_copy(deep=True)

    def get_experiment(self, project_id: str, experiment_id: str) -> Experiment | None:
        with self._lock:
            value = self.experiments.get(experiment_id)
            if value is None or value.spec.project_id != project_id:
                return None
            return value.model_copy(deep=True)

    def list_experiments(self, project_id: str, *, task_type: str | None = None, status: str | None = None) -> list[Experiment]:
        with self._lock:
            values = [value for value in self.experiments.values() if value.spec.project_id == project_id]
            if task_type is not None:
                values = [value for value in values if value.spec.task_type == task_type]
            if status is not None:
                values = [value for value in values if value.status == status]
            values.sort(key=lambda value: (value.created_at, value.id), reverse=True)
            return [value.model_copy(deep=True) for value in values]

    def create_submission(self, *, experiment: Experiment, confirmation_id: str, actor_id: str, idempotency_key: str | None) -> tuple[TrainingJob, bool]:
        with self._lock:
            if idempotency_key:
                key = (actor_id, experiment.spec.project_id, idempotency_key)
                digest = _hash({"experiment_id": experiment.id, "revision": experiment.revision, "config_hash": experiment.config_hash, "confirmation_id": confirmation_id})
                old = self._idempotency.get(key)
                if old is not None:
                    if old[0] != digest:
                        raise TrainingConflictError("idempotency key was reused for another submission")
                    current = self.jobs.get(old[1])
                    if current is None:
                        raise TrainingConflictError("idempotency record has no job")
                    return current.model_copy(deep=True), True
            if experiment.job_id is not None:
                if idempotency_key:
                    replay = self.get_submission_replay(experiment=experiment, confirmation_id=confirmation_id, actor_id=actor_id, idempotency_key=idempotency_key)
                    if replay is not None:
                        return replay, True
                raise TrainingConflictError("experiment already has a submission")
            job_id = new_id("job")
            dispatch_id = new_id("dispatch")
            now = now_utc()
            job = TrainingJob(id=job_id, project_id=experiment.spec.project_id, experiment_id=experiment.id, status="QUEUED", attempt=1, dispatch_id=dispatch_id, created_at=now)
            outbox = TrainingOutbox(dispatch_id=dispatch_id, job_id=job_id, status="PENDING", attempts=0, payload=WorkerMessage(job_id=job_id, attempt=1, dispatch_id=dispatch_id), created_at=now)
            locked = experiment.model_copy(update={"job_id": job_id, "status": "QUEUED", "current_attempt": 1, "spec_locked": True})
            self.experiments[experiment.id] = locked
            self.jobs[job_id] = job
            self.outbox[dispatch_id] = outbox
            self.audits.append(TrainingAuditEvent(id=new_id("audit"), project_id=experiment.spec.project_id, actor_id=actor_id, action="training.submit", resource_ref={"type": "experiment", "id": experiment.id}, result="SUCCEEDED", confirmation_id=confirmation_id, job_id=job_id, config_hash=experiment.config_hash, created_at=now))
            if idempotency_key:
                self._idempotency[(actor_id, experiment.spec.project_id, idempotency_key)] = (digest, job_id)
            return job, False

    def get_submission_replay(self, *, experiment: Experiment, confirmation_id: str, actor_id: str, idempotency_key: str) -> TrainingJob | None:
        with self._lock:
            digest = _hash({"experiment_id": experiment.id, "revision": experiment.revision, "config_hash": experiment.config_hash, "confirmation_id": confirmation_id})
            old = self._idempotency.get((actor_id, experiment.spec.project_id, idempotency_key))
            if old is None:
                return None
            if old[0] != digest:
                raise TrainingConflictError("idempotency key was reused for another submission")
            replay = self.jobs.get(old[1])
            if replay is None or experiment.job_id != replay.id:
                raise TrainingConflictError("idempotency record does not match the experiment")
            return replay.model_copy(deep=True)

    def get_job(self, job_id: str) -> TrainingJob | None:
        with self._lock:
            value = self.jobs.get(job_id)
            return value.model_copy(deep=True) if value else None

    def save_job(self, value: TrainingJob) -> TrainingJob:
        with self._lock:
            self.jobs[value.id] = value.model_copy(deep=True)
            return value.model_copy(deep=True)

    def get_outbox(self, dispatch_id: str) -> TrainingOutbox | None:
        with self._lock:
            value = self.outbox.get(dispatch_id)
            return value.model_copy(deep=True) if value else None

    def save_outbox(self, value: TrainingOutbox) -> TrainingOutbox:
        with self._lock:
            self.outbox[value.dispatch_id] = value.model_copy(deep=True)
            return value.model_copy(deep=True)

    def cancel_outbox(self, dispatch_id: str) -> bool:
        with self._lock:
            row = self.outbox.get(dispatch_id)
            if row is None or row.status != "PENDING":
                return False
            self.outbox[dispatch_id] = row.model_copy(update={"status": "FAILED", "lease_owner": None, "lease_expires_at": None})
            return True

    def claim_outbox(self, message: WorkerMessage, *, owner: str) -> TrainingDispatchLease | None:
        if not isinstance(owner, str) or not owner:
            raise ValueError("outbox owner is required")
        with self._lock:
            row = self.outbox.get(message.dispatch_id)
            job = self.jobs.get(message.job_id)
            expired = row is not None and row.status == "PROCESSING" and row.lease_expires_at is not None and _aware_dt(row.lease_expires_at) <= now_utc()
            if row is None or job is None or job.status != "QUEUED" or (row.status not in {"PENDING", "FAILED"} and not expired) or row.job_id != message.job_id or job.attempt != message.attempt or job.dispatch_id != message.dispatch_id:
                return None
            if row.payload != message:
                raise TrainingConflictError("training outbox identity does not match payload")
            token = int(row.attempts) + 1
            expires = now_utc() + timedelta(seconds=60)
            lease_token = int(row.lease_token or 0) + 1
            self.outbox[row.dispatch_id] = row.model_copy(update={"status": "PROCESSING", "attempts": token, "lease_owner": owner, "lease_token": lease_token, "lease_expires_at": expires})
            self._transport_tokens[row.dispatch_id] = lease_token
            return TrainingDispatchLease(message=message, owner=owner, token=lease_token, lease_expires_at=expires)

    def claim_next_outbox(self, *, owner: str) -> TrainingDispatchLease | None:
        if not isinstance(owner, str) or not owner:
            raise ValueError("outbox owner is required")
        with self._lock:
            eligible = sorted(
                (row for row in self.outbox.values() if row.status in {"PENDING", "FAILED"}),
                key=lambda row: (row.created_at, row.dispatch_id),
            )
            for row in eligible:
                return self.claim_outbox(row.payload, owner=owner)
            return None

    def recover_outbox(self, message: WorkerMessage, *, owner: str) -> TrainingDispatchLease | None:
        if not isinstance(owner, str) or not owner:
            raise ValueError("outbox owner is required")
        with self._lock:
            row = self.outbox.get(message.dispatch_id)
            job = self.jobs.get(message.job_id)
            if row is None or job is None or row.status != "PROCESSING" or row.job_id != message.job_id or job.attempt != message.attempt or job.dispatch_id != message.dispatch_id:
                return None
            token = int(row.attempts) + 1
            expires = now_utc() + timedelta(seconds=60)
            lease_token = int(row.lease_token or 0) + 1
            self.outbox[row.dispatch_id] = row.model_copy(update={"attempts": token, "lease_owner": owner, "lease_token": lease_token, "lease_expires_at": expires})
            self._transport_tokens[row.dispatch_id] = lease_token
            return TrainingDispatchLease(message=message, owner=owner, token=lease_token, lease_expires_at=expires)

    def ack_outbox(self, lease: TrainingDispatchLease) -> bool:
        with self._lock:
            row = self.outbox.get(lease.message.dispatch_id)
            if row is None or row.status != "PROCESSING" or row.job_id != lease.message.job_id or row.lease_owner != lease.owner or int(row.lease_token) != lease.token or row.payload != lease.message:
                return False
            self.outbox[row.dispatch_id] = row.model_copy(update={"status": "SENT", "lease_owner": None, "lease_expires_at": None})
            return True

    def retry_outbox(self, lease: TrainingDispatchLease) -> bool:
        with self._lock:
            row = self.outbox.get(lease.message.dispatch_id)
            if row is None or row.status != "PROCESSING" or row.job_id != lease.message.job_id or row.lease_owner != lease.owner or int(row.lease_token) != lease.token or row.payload != lease.message:
                return False
            self.outbox[row.dispatch_id] = row.model_copy(update={"status": "FAILED", "lease_owner": None, "lease_expires_at": None})
            return True

    def list_audits(self, project_id: str) -> list[TrainingAuditEvent]:
        return [item.model_copy(deep=True) for item in self.audits if item.project_id == project_id]

    def save_candidate(self, value: TrainingCandidate) -> TrainingCandidate:
        with self._lock:
            self.candidates[value.id] = value.model_copy(deep=True)
            return value.model_copy(deep=True)

    def get_candidate(self, candidate_id: str) -> TrainingCandidate | None:
        with self._lock:
            value = self.candidates.get(candidate_id)
            return value.model_copy(deep=True) if value else None


    def list_events(self, project_id: str, experiment_id: str, *, after_seq: int = 0, limit: int = 100) -> tuple[list[TrainingEvent], bool]:
        if self.get_experiment(project_id, experiment_id) is None:
            return [], False
        rows = [item for item in self.events.get(experiment_id, []) if item.seq > after_seq]
        return [item.model_copy(deep=True) for item in rows[:limit]], len(rows) > limit

    def list_logs(self, project_id: str, experiment_id: str, *, after_seq: int = 0, limit: int = 100) -> tuple[list[LogEntry], bool]:
        if self.get_experiment(project_id, experiment_id) is None:
            return [], False
        rows = [item for item in self.logs.get(experiment_id, []) if item.seq > after_seq]
        return [item.model_copy(deep=True) for item in rows[:limit]], len(rows) > limit

    def get_metrics(self, project_id: str, experiment_id: str, *, attempt: int, after_step: int | None = None) -> ExperimentMetrics:
        experiment = self.get_experiment(project_id, experiment_id)
        if experiment is None:
            raise TrainingNotFoundError("experiment does not exist")
        value = self.metrics.get((experiment_id, attempt))
        if value is None:
            return ExperimentMetrics(experiment_id=experiment_id, attempt=attempt, protocol_snapshot={"id": experiment.spec.evaluation_protocol.id, "version": experiment.spec.evaluation_protocol.version}, series=[], final_metrics=[], next_step=None)
        if after_step is None:
            return value.model_copy(deep=True)
        return value.model_copy(update={"series": [item for item in value.series if item.step > after_step]}, deep=True)

    def list_artifacts(self, project_id: str, experiment_id: str, *, after_id: str | None = None, limit: int = 50) -> tuple[list[TrainingArtifact], bool]:
        if self.get_experiment(project_id, experiment_id) is None:
            return [], False
        rows = list(self.artifacts.get(experiment_id, []))
        if after_id is not None:
            for index, item in enumerate(rows):
                if item.id == after_id:
                    rows = rows[index + 1:]
                    break
        return [item.model_copy(deep=True) for item in rows[:limit]], len(rows) > limit

    def comparison_metrics(self, project_id: str, experiment_id: str, *, protocol_id: str, protocol_version: str) -> dict[str, float] | None:
        experiment = self.get_experiment(project_id, experiment_id)
        if experiment is None or experiment.spec.evaluation_protocol.id != protocol_id or experiment.spec.evaluation_protocol.version != protocol_version:
            return None
        return dict(self.comparison_results.get(experiment_id, {})) or None


class TrainingOutboxDispatcher:
    """Single transport owner; business workers receive only leased messages."""

    def __init__(self, repository: object, *, owner: str):
        if not isinstance(owner, str) or not owner:
            raise ValueError("dispatcher owner is required")
        self.repository, self.owner = repository, owner

    def claim(self, message: WorkerMessage):
        method = getattr(self.repository, "claim_shared_outbox", None) if getattr(self.repository, "canonical_transport", False) else None
        return (method(message, owner=self.owner) if callable(method) else self.repository.claim_outbox(message, owner=self.owner))

    def claim_next(self):
        """Poll one message; business execution remains outside this lease call."""
        method = getattr(self.repository, "claim_shared_next", None) if getattr(self.repository, "canonical_transport", False) else None
        return (method(owner=self.owner) if callable(method) else self.repository.claim_next_outbox(owner=self.owner))

    def recover(self, message: WorkerMessage):
        method = getattr(self.repository, "recover_shared_outbox", None) if getattr(self.repository, "canonical_transport", False) else None
        return (method(message, owner=self.owner) if callable(method) else self.repository.recover_outbox(message, owner=self.owner))

    def ack(self, lease: object) -> bool:
        method = getattr(self.repository, "ack_shared_outbox", None) if getattr(self.repository, "canonical_transport", False) else None
        return bool(method(lease) if callable(method) else self.repository.ack_outbox(lease))

    def retry(self, lease: object) -> bool:
        method = getattr(self.repository, "retry_shared_outbox", None) if getattr(self.repository, "canonical_transport", False) else None
        return bool(method(lease) if callable(method) else self.repository.retry_outbox(lease))


class InMemoryCandidateRegistration:
    """Explicit registration port; it never publishes a model."""

    def __init__(self, repository: InMemoryTrainingRepository | None = None, *, fail: bool = False):
        self.repository = repository
        self.fail = fail
        self.calls = 0

    def register_candidate(self, *, project_id: str, experiment: Experiment, result: CandidateResult) -> TrainingCandidate:
        self.calls += 1
        if self.fail:
            raise ValueError("candidate validation failed")
        value = TrainingCandidate(id=new_id("candidate"), project_id=project_id, experiment_id=experiment.id, artifact_id=result.artifact_id, status="CANDIDATE", published=False, created_at=now_utc())
        if self.repository:
            self.repository.save_candidate(value)
        return value


class SQLAlchemyCandidateRegistration:
    """Registers a validated artifact as a candidate; publication is separate."""

    def __init__(self, repository: SQLAlchemyTrainingRepository):
        self.repository = repository

    def register_candidate(self, *, project_id: str, experiment: Experiment, result: CandidateResult) -> TrainingCandidate:
        existing = self.repository.session.scalar(select(TrainingCandidateRow).where(TrainingCandidateRow.experiment_id == experiment.id).with_for_update())
        if existing is not None:
            if existing.project_id != project_id or existing.artifact_id != result.artifact_id:
                raise TrainingConflictError("candidate for experiment is immutable")
            return self.repository.get_candidate(existing.id)  # type: ignore[return-value]
        value = TrainingCandidate(id=self.repository._id_factory("candidate"), project_id=project_id, experiment_id=experiment.id, artifact_id=result.artifact_id, status="CANDIDATE", published=False, created_at=now_utc())
        # Metrics have their own immutable rows in 0004; keeping them outside
        # the candidate JSON makes the experiment result queryable and avoids
        # losing the worker's measured values at registration time.
        for metric_key, metric_value in result.metrics.items():
            value_json = metric_value if isinstance(metric_value, dict) else {"value": metric_value}
            self.repository.session.add(TrainingMetricRow(
                id=self.repository._id_factory("metric"),
                experiment_id=experiment.id,
                metric_key=str(metric_key),
                value=value_json,
                split="test",
                created_at=now_utc(),
            ))
        return self.repository.save_candidate(value)


class InMemoryConfirmationPort:
    """Strict confirmation test adapter with revision/hash binding."""

    def __init__(self):
        self.records: dict[str, dict[str, Any]] = {}

    @contextmanager
    def transaction(self):
        snapshot = deepcopy(self.records)
        try:
            yield self
        except Exception:
            self.records = snapshot
            raise

    def add(
        self,
        confirmation_id: str,
        *,
        project_id: str,
        resource_ref: dict[str, str],
        action: str,
        revision: int,
        config_hash: str,
        actor_id: str | None = None,
        expires_at: datetime | None = None,
    ):
        self.records[confirmation_id] = {
            "project_id": project_id, "resource_ref": resource_ref, "action": action,
            "revision": revision, "config_hash": config_hash, "actor_id": actor_id,
            "expires_at": expires_at, "status": "CONFIRMED",
        }

    def consume(
        self,
        *,
        confirmation_id: str,
        project_id: str,
        resource_ref: dict[str, str],
        action: str,
        revision: int,
        config_hash: str,
        result_ref: dict[str, str],
        actor_id: str | None = None,
    ) -> object:
        row = self.records.get(confirmation_id)
        if row is None or row["status"] != "CONFIRMED":
            raise TrainingConflictError("confirmation is missing or already consumed")
        if row.get("expires_at") is not None and row["expires_at"] <= now_utc():
            raise TrainingConflictError("confirmation has expired")
        if any(row[key] != value for key, value in (("project_id", project_id), ("resource_ref", resource_ref), ("action", action), ("revision", revision), ("config_hash", config_hash))):
            raise TrainingConflictError("confirmation binding does not match experiment")
        if row.get("actor_id") is not None and row["actor_id"] != actor_id:
            raise TrainingConflictError("confirmation subject does not match actor")
        row["status"] = "CONSUMED"
        row["result_ref"] = result_ref
        return row


TrainingRepositoryInMemory = InMemoryTrainingRepository
TemplateRegistry = StaticTrainingTemplateRegistry


__all__ = [
    "InMemoryCandidateRegistration", "SQLAlchemyCandidateRegistration", "InMemoryConfirmationPort", "InMemoryTrainingRepository",
    "SQLAlchemyTrainingRepository", "TrainingExecutionClaim", "TrainingIdempotencyRow",
    "StaticEvaluationProtocolRegistry", "StaticTrainingTemplateRegistry", "TemplateRegistry",
    "TrainingAuditEventRow",
    "TrainingBase", "TrainingCandidateRow", "TrainingExperimentRow", "TrainingJobRow",
    "TrainingDispatchLease", "TrainingMetricRow", "TrainingOutboxDispatcher", "TrainingOutboxRow", "TrainingRepositoryInMemory", "TrainingTemplateRow", "new_id", "now_utc",
]
