"""SQLAlchemy projections owned by batch prediction."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import ForeignKeyConstraint, Index, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from chrom_platform.artifact_core.models import Artifact  # registers artifact FK table
from chrom_platform.infrastructure.db import Base, MYSQL_TABLE_KWARGS
from chrom_platform.infrastructure.models import Job, Project  # registers project/job FK tables


def _now() -> datetime:
    return datetime.now(timezone.utc)


_PROJECT_ID = Base.metadata.tables["project"].c.id
_JOB_ID = Base.metadata.tables["job"].c.id
_ARTIFACT = Base.metadata.tables["artifact"]


class BatchPredictionRow(Base):
    """Durable draft/projection; row-level details live in child projections."""

    __tablename__ = "batch_prediction"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(96), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(96), nullable=False)
    chromatography_type: Mapped[str] = mapped_column(String(32), nullable=False)
    tool: Mapped[str] = mapped_column(String(32), nullable=False, default="batch")
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    model_selection: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    model_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    input_artifact_id: Mapped[str] = mapped_column(String(96), nullable=False)
    template_version: Mapped[str] = mapped_column(String(64), nullable=False)
    field_mapping: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False)
    common_input: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    invalid_row_policy: Mapped[str] = mapped_column(String(24), nullable=False)
    resource_budget: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    etag: Mapped[str] = mapped_column(String(128), nullable=False)
    draft_status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")
    validation_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    attempt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    execution_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(nullable=True)
    counts: Mapped[dict[str, int]] = mapped_column(JSON, nullable=False, default=dict)
    progress: Mapped[float] = mapped_column(nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(nullable=False, default=_now, onupdate=_now)

    __table_args__ = (
        ForeignKeyConstraint(("project_id",), (_PROJECT_ID,), ondelete="RESTRICT"),
        ForeignKeyConstraint(("job_id",), (_JOB_ID,), ondelete="RESTRICT"),
        ForeignKeyConstraint(("project_id", "input_artifact_id"), (_ARTIFACT.c.project_id, _ARTIFACT.c.id), ondelete="RESTRICT"),
        Index("ix_batch_prediction_project_status", "project_id", "draft_status", "created_at"),
        MYSQL_TABLE_KWARGS,
    )


class BatchValidationRow(Base):
    __tablename__ = "batch_validation"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(96), nullable=False)
    batch_id: Mapped[str] = mapped_column(String(96), nullable=False)
    draft_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    template_version: Mapped[str] = mapped_column(String(64), nullable=False)
    model_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    total_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    valid_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    invalid_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    excluded_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_evaluations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    resource_estimate: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    resolved_input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    errors_report_artifact_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    warnings: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=_now)


class BatchValidationItemRow(Base):
    __tablename__ = "batch_validation_item"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(96), nullable=False)
    validation_id: Mapped[str] = mapped_column(String(96), nullable=False)
    row_id: Mapped[str] = mapped_column(String(96), nullable=False)
    source_row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    normalized_input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    resolved_input_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    resolved_input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    field_errors: Mapped[list[dict[str, str]]] = mapped_column(JSON, nullable=False, default=list)
    warnings: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=_now)


class BatchRowResultRow(Base):
    __tablename__ = "batch_row_result"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(96), nullable=False)
    batch_id: Mapped[str] = mapped_column(String(96), nullable=False)
    validation_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    row_id: Mapped[str] = mapped_column(String(96), nullable=False)
    source_row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[dict[str, str] | None] = mapped_column(JSON, nullable=True)
    warnings: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=_now)


class BatchRetryPlanRow(Base):
    __tablename__ = "batch_retry_plan"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(96), nullable=False)
    parent_batch_id: Mapped[str] = mapped_column(String(96), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    etag: Mapped[str] = mapped_column(String(128), nullable=False)
    row_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    expected_attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    estimated_evaluations: Mapped[int] = mapped_column(Integer, nullable=False)
    validation_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    model_snapshot_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resolved_input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=_now)


class BatchExportRow(Base):
    __tablename__ = "batch_export"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(96), nullable=False)
    batch_id: Mapped[str] = mapped_column(String(96), nullable=False)
    job_id: Mapped[str] = mapped_column(String(96), nullable=False)
    format: Mapped[str] = mapped_column(String(16), nullable=False)
    include: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    source_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    source_snapshot_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="QUEUED")
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    artifact_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=_now)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
