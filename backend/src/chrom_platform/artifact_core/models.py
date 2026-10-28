"""Artifact and normalized structure persistence models."""

from __future__ import annotations

from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from chrom_platform.infrastructure.db import Base, MYSQL_TABLE_KWARGS, UTCDateTime, utc_now


class Artifact(Base):
    __tablename__ = "artifact"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(96), ForeignKey("project.id", ondelete="RESTRICT"), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    media_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="CREATED")
    scan_status: Mapped[str] = mapped_column(String(20), nullable=False, default="NOT_CONFIGURED")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_by: Mapped[str] = mapped_column(String(96), nullable=False)
    retention_until: Mapped[Any | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[Any] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[Any] = mapped_column(UTCDateTime(), nullable=False, default=utc_now, onupdate=utc_now)

    __table_args__ = (
        CheckConstraint("size_bytes > 0", name="artifact_size_positive"),
        CheckConstraint("sha256 IS NULL OR CHAR_LENGTH(sha256) = 64", name="artifact_sha256_length"),
        CheckConstraint(
            "status IN ('CREATED','RECEIVED','VALIDATING','AVAILABLE','QUARANTINED','FAILED','ARCHIVED','DELETING','DELETED','EXPIRED')",
            name="artifact_status",
        ),
        CheckConstraint("scan_status IN ('NOT_CONFIGURED','PENDING','PASSED','REJECTED','FAILED')", name="artifact_scan_status"),
        Index("ix_artifact_project_status_created", "project_id", "status", "created_at"),
        Index("ix_artifact_project_sha256", "project_id", "sha256"),
        UniqueConstraint("project_id", "id", name="uq_artifact_project_id"),
        MYSQL_TABLE_KWARGS,
    )


class Structure(Base):
    __tablename__ = "structure"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(96), ForeignKey("project.id", ondelete="RESTRICT"), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    original_input: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    canonical_smiles: Mapped[str] = mapped_column(Text, nullable=False)
    is_valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    has_stereochemistry: Mapped[bool] = mapped_column(Boolean, nullable=False)
    stereochemistry_status: Mapped[str] = mapped_column(String(20), nullable=False)
    stereochemistry_warnings: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_artifact_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    created_at: Mapped[Any] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)

    __table_args__ = (
        UniqueConstraint("project_id", "snapshot_hash", name="uq_structure_project_snapshot_hash"),
        UniqueConstraint("project_id", "id", name="uq_structure_project_id_id"),
        CheckConstraint("is_valid = 1", name="structure_valid"),
        CheckConstraint(
            "stereochemistry_status IN ('specified','unspecified','not_applicable')",
            name="structure_stereochemistry_status",
        ),
        CheckConstraint("CHAR_LENGTH(snapshot_hash) = 64", name="structure_snapshot_hash_length"),
        Index("ix_structure_project_created", "project_id", "created_at"),
        # A composite FK prevents a structure in project A from referring to
        # an artifact owned by project B while retaining nullable source refs.
        ForeignKeyConstraint(
            ["project_id", "source_artifact_id"],
            ["artifact.project_id", "artifact.id"],
            ondelete="RESTRICT",
            name="fk_structure_project_artifact",
        ),
        MYSQL_TABLE_KWARGS,
    )
