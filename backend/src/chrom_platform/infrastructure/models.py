"""M1 platform-owned SQLAlchemy models."""

from __future__ import annotations

from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from chrom_platform.task_core.state import ConfirmationStatus, JobStatus

from .db import Base, MYSQL_TABLE_KWARGS, UTCDateTime, utc_now


def _json() -> Mapped[dict[str, Any]]:
    return mapped_column(JSON, nullable=False, default=dict)


class Project(Base):
    __tablename__ = "project"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    slug: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    tenant_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[Any] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[Any] = mapped_column(UTCDateTime(), nullable=False, default=utc_now, onupdate=utc_now)

    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE','ARCHIVED')", name="project_status"),
        CheckConstraint("revision >= 1", name="project_revision_positive"),
        MYSQL_TABLE_KWARGS,
    )


class AuditEvent(Base):
    __tablename__ = "audit_event"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(
        String(96), ForeignKey("project.id", ondelete="RESTRICT"), nullable=True
    )
    actor_id: Mapped[str] = mapped_column(String(96), nullable=False)
    auth_method: Mapped[str] = mapped_column(String(32), nullable=False)
    interaction: Mapped[str] = mapped_column(String(16), nullable=False)
    request_id: Mapped[str] = mapped_column(String(96), nullable=False)
    action: Mapped[str] = mapped_column(String(160), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    resource_ref: Mapped[dict[str, Any]] = _json()
    before_summary: Mapped[dict[str, Any]] = _json()
    after_summary: Mapped[dict[str, Any]] = _json()
    confirmation_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    config_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    agent_run_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    timestamp: Mapped[Any] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)

    __table_args__ = (
        CheckConstraint("interaction IN ('human','agent_tool','worker','service')", name="audit_interaction"),
        CheckConstraint("result IN ('SUCCEEDED','FAILED')", name="audit_result"),
        Index("ix_audit_event_project_timestamp", "project_id", "timestamp"),
        Index("ix_audit_event_request_id", "request_id"),
        MYSQL_TABLE_KWARGS,
    )


class Confirmation(Base):
    __tablename__ = "confirmation"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(96), ForeignKey("project.id", ondelete="RESTRICT"), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(96), nullable=False)
    resource_ref: Mapped[dict[str, Any]] = _json()
    action: Mapped[str] = mapped_column(String(160), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    binding_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    summary_snapshot: Mapped[dict[str, Any]] = _json()
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=ConfirmationStatus.CONFIRMED.value)
    created_at: Mapped[Any] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    expires_at: Mapped[Any] = mapped_column(UTCDateTime(), nullable=False)
    consumed_at: Mapped[Any | None] = mapped_column(UTCDateTime(), nullable=True)
    consumed_result_ref: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    consumed_request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    revoked_at: Mapped[Any | None] = mapped_column(UTCDateTime(), nullable=True)

    __table_args__ = (
        CheckConstraint("revision >= 1", name="confirmation_revision_positive"),
        CheckConstraint("CHAR_LENGTH(config_hash) = 64", name="confirmation_config_hash_length"),
        CheckConstraint("CHAR_LENGTH(binding_hash) = 64", name="confirmation_binding_hash_length"),
        CheckConstraint(
            "status IN ('CONFIRMED','CONSUMED','EXPIRED','INVALIDATED','REVOKED')",
            name="confirmation_status",
        ),
        Index("ix_confirmation_resource", "project_id", "action", "status"),
        CheckConstraint("expires_at > created_at", name="confirmation_expiry_after_creation"),
        MYSQL_TABLE_KWARGS,
    )


class Job(Base):
    __tablename__ = "job"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(96), ForeignKey("project.id", ondelete="RESTRICT"), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(96), nullable=False)
    kind: Mapped[str] = mapped_column(String(96), nullable=False)
    resource_ref: Mapped[dict[str, Any]] = _json()
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=JobStatus.QUEUED.value)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    progress: Mapped[dict[str, Any]] = _json()
    cancelable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    retryable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    resume_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="none")
    created_at: Mapped[Any] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[Any] = mapped_column(UTCDateTime(), nullable=False, default=utc_now, onupdate=utc_now)
    started_at: Mapped[Any | None] = mapped_column(UTCDateTime(), nullable=True)
    finished_at: Mapped[Any | None] = mapped_column(UTCDateTime(), nullable=True)
    deadline_at: Mapped[Any | None] = mapped_column(UTCDateTime(), nullable=True)
    result_ref: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    etag: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        CheckConstraint("attempt >= 1", name="job_attempt_positive"),
        CheckConstraint("revision >= 1", name="job_revision_positive"),
        CheckConstraint(
            "status IN ('QUEUED','RUNNING','CANCEL_REQUESTED','SUCCEEDED','PARTIAL','FAILED','CANCELED','TIMED_OUT','INTERRUPTED')",
            name="job_status",
        ),
        CheckConstraint("resume_mode IN ('none','restart','checkpoint')", name="job_resume_mode"),
        Index("ix_job_project_status_created", "project_id", "status", "created_at"),
        MYSQL_TABLE_KWARGS,
    )


class JobAttempt(Base):
    __tablename__ = "job_attempt"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(96), ForeignKey("job.id", ondelete="RESTRICT"), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    resume_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    execution_token: Mapped[str | None] = mapped_column(String(128), nullable=True)
    fencing_token: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    lease_until: Mapped[Any | None] = mapped_column(UTCDateTime(), nullable=True)
    checkpoint_ref: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[Any | None] = mapped_column(UTCDateTime(), nullable=True)
    finished_at: Mapped[Any | None] = mapped_column(UTCDateTime(), nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    result_ref: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        UniqueConstraint("job_id", "attempt", name="uq_job_attempt_job_attempt"),
        CheckConstraint("attempt >= 1", name="job_attempt_number_positive"),
        CheckConstraint("resume_mode IN ('none','restart','checkpoint')", name="job_attempt_resume_mode"),
        CheckConstraint(
            "status IN ('QUEUED','RUNNING','CANCEL_REQUESTED','SUCCEEDED','PARTIAL','FAILED','CANCELED','TIMED_OUT','INTERRUPTED')",
            name="job_attempt_status",
        ),
        Index("ix_job_attempt_job_status", "job_id", "status"),
        MYSQL_TABLE_KWARGS,
    )


class JobEvent(Base):
    __tablename__ = "job_event"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(96), ForeignKey("job.id", ondelete="RESTRICT"), nullable=False)
    stream_id: Mapped[str] = mapped_column(String(96), nullable=False)
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    type: Mapped[str] = mapped_column(String(96), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False, default="1.0")
    data: Mapped[dict[str, Any]] = _json()
    timestamp: Mapped[Any] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)

    __table_args__ = (
        UniqueConstraint("stream_id", "seq", name="uq_job_event_stream_seq"),
        UniqueConstraint("job_id", "seq", name="uq_job_event_job_seq"),
        CheckConstraint("seq >= 0", name="job_event_seq_nonnegative"),
        Index("ix_job_event_job_seq", "job_id", "seq"),
        MYSQL_TABLE_KWARGS,
    )


class OutboxEvent(Base):
    __tablename__ = "outbox_event"

    dispatch_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(96), nullable=False)
    event_type: Mapped[str] = mapped_column(String(96), nullable=False)
    payload: Mapped[dict[str, Any]] = _json()
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[Any] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    dispatched_at: Mapped[Any | None] = mapped_column(UTCDateTime(), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[Any] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_token: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    lease_expires_at: Mapped[Any | None] = mapped_column(UTCDateTime(), nullable=True)

    __table_args__ = (
        CheckConstraint("status IN ('PENDING','PROCESSING','SENT','FAILED')", name="outbox_status"),
        CheckConstraint("attempts >= 0", name="outbox_attempts_nonnegative"),
        Index("ix_outbox_status_available", "status", "available_at"),
        Index("ix_outbox_lease", "status", "lease_expires_at"),
        Index("ix_outbox_aggregate", "aggregate_type", "aggregate_id"),
        MYSQL_TABLE_KWARGS,
    )


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_record"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(96), nullable=False)
    project_id: Mapped[str] = mapped_column(String(96), ForeignKey("project.id", ondelete="RESTRICT"), nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    route_identity: Mapped[str] = mapped_column(String(128), nullable=False)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="IN_PROGRESS")
    error_code: Mapped[str | None] = mapped_column(String(96), nullable=True)
    result_resource_ref: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    result_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expires_at: Mapped[Any] = mapped_column(UTCDateTime(), nullable=False)
    created_at: Mapped[Any] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    completed_at: Mapped[Any | None] = mapped_column(UTCDateTime(), nullable=True)

    __table_args__ = (
        UniqueConstraint("actor_id", "project_id", "method", "route_identity", "key", name="uq_idempotency_scope_key"),
        CheckConstraint("status IN ('IN_PROGRESS','COMPLETED','FAILED')", name="idempotency_status"),
        CheckConstraint(
            "error_code IS NULL OR error_code REGEXP '^[A-Z][A-Z0-9_]*$'",
            name="idempotency_error_code_format",
        ),
        CheckConstraint("CHAR_LENGTH(payload_hash) = 64", name="idempotency_payload_hash_length"),
        Index("ix_idempotency_expires", "expires_at"),
        MYSQL_TABLE_KWARGS,
    )


class ExecutionLedger(Base):
    __tablename__ = "execution_ledger"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    operation_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    operation_type: Mapped[str] = mapped_column(String(96), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="STARTED")
    job_id: Mapped[str | None] = mapped_column(String(96), ForeignKey("job.id", ondelete="RESTRICT"), nullable=True)
    result_ref: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[Any] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    finished_at: Mapped[Any | None] = mapped_column(UTCDateTime(), nullable=True)
    updated_at: Mapped[Any] = mapped_column(UTCDateTime(), nullable=False, default=utc_now, onupdate=utc_now)

    __table_args__ = (
        CheckConstraint("status IN ('STARTED','SUCCEEDED','FAILED','UNKNOWN')", name="execution_ledger_status"),
        Index("ix_execution_ledger_job", "job_id"),
        MYSQL_TABLE_KWARGS,
    )
