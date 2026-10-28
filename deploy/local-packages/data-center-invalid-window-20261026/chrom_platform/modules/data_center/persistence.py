"""SQLAlchemy persistence adapters owned by the data-center module.

The adapters deliberately receive a caller-owned :class:`Session`.  They only
flush rows; transaction boundaries, commit and close remain with the
application UOW.  ``DataCenterBase`` is separate from the M1 metadata so a
trimmed build can register these tables explicitly.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any, Callable, Iterable
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    Index,
    func,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from chrom_platform.infrastructure.db import Base, MYSQL_TABLE_KWARGS
from chrom_platform.infrastructure.models import AuditEvent, Job
from chrom_platform.artifact_core.models import Artifact, Structure

_PROJECT_ID = Base.metadata.tables["project"].c.id
_ARTIFACT_PROJECT_ID = Base.metadata.tables["artifact"].c.project_id
_ARTIFACT_ID = Base.metadata.tables["artifact"].c.id
_STRUCTURE_PROJECT_ID = Base.metadata.tables["structure"].c.project_id
_STRUCTURE_ID = Base.metadata.tables["structure"].c.id
_JOB_ID = Base.metadata.tables["job"].c.id

from .errors import (
    ConfirmationRequiredError,
    DataConflictError,
    DataNotFoundError,
    DataUnavailableError,
)
from .ports import PredictionRecordDraft, PredictionRecordStagingPort, PredictionReplaySourceData
from .schemas import (
    ConfirmationConsumeRequest,
    DataCommitRequest,
    DataDomainSchema,
    DataImport,
    DataImportValidation,
    DataVersion,
    DataVersionDraft,
    DomainField,
    ExportCreate,
    ExportView,
    PredictionDraftView,
    PredictionRecord,
    ValidationCounts,
)


class DataCenterBase(DeclarativeBase):
    """Metadata registry for the optional data-center build."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _public_prediction_input_snapshot(value: object) -> dict[str, Any]:
    """Explicitly map persisted execution input to the public record shape."""

    if not isinstance(value, Mapping):
        raise DataUnavailableError("预测输入快照不可用")
    required = {"structure_refs", "normalized_structures", "input"}
    if not required.issubset(value):
        # Preserve legacy non-prediction fixtures; an internal SFC snapshot
        # always has the required public fields and is validated below.
        if "modifier_structures" in value:
            raise DataUnavailableError("预测输入快照不可用")
        return dict(value)
    from chrom_platform.modules.prediction.schemas import PredictionInputSnapshot

    try:
        public = PredictionInputSnapshot.model_validate({
            "structure_refs": value["structure_refs"],
            "normalized_structures": value["normalized_structures"],
            "input": value["input"],
        })
    except (TypeError, ValueError):
        raise DataUnavailableError("预测输入快照不可用") from None
    return public.model_dump(mode="json")


def _aware(value: datetime | None) -> datetime:
    if value is None:
        return _now()
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _confirmation_hash(request: ConfirmationConsumeRequest) -> str:
    payload = {
        "confirmation_id": request.confirmation_id,
        "actor_id": request.actor_id,
        "project_id": request.project_id,
        "resource_ref": request.resource_ref.model_dump(mode="json"),
        "action": request.action,
        "revision": request.revision,
        "config_hash": request.config_hash,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _confirmation_binding_hash(row: Any) -> str:
    payload = {
        "project_id": row.project_id,
        "actor_id": row.actor_id,
        "resource_ref": row.resource_ref,
        "action": row.action,
        "revision": row.revision,
        "config_hash": row.config_hash,
        "policy_version": row.policy_version,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class DataDomainRow(DataCenterBase):
    __tablename__ = "data_domain"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    chromatography_type: Mapped[str] = mapped_column(String(32), nullable=False)
    record_schema: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    unit_definitions: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False)
    identity_fields: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    deduplication_policy: Mapped[str] = mapped_column(String(32), nullable=False)
    supported_import_modes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    __table_args__ = (
        CheckConstraint(
            "deduplication_policy IN ('reject','update_existing')",
            name="ck_data_domain_deduplication_policy",
        ),
        MYSQL_TABLE_KWARGS,
    )


class DataImportRow(DataCenterBase):
    __tablename__ = "data_import"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(96), nullable=False)
    domain_id: Mapped[str] = mapped_column(String(96), nullable=False)
    artifact_id: Mapped[str] = mapped_column(String(96), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    import_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    field_mapping: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False)
    source_description: Mapped[str] = mapped_column(Text, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    etag: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    job_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    validation_ref: Mapped[str | None] = mapped_column(String(96), nullable=True)
    new_data_version_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    request_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(("project_id",), (_PROJECT_ID,), name="fk_data_import_project_id_project", ondelete="RESTRICT"),
        ForeignKeyConstraint(("project_id", "artifact_id"), (_ARTIFACT_PROJECT_ID, _ARTIFACT_ID), name="fk_data_import_project_artifact", ondelete="RESTRICT"),
        ForeignKeyConstraint(("job_id",), (_JOB_ID,), name="fk_data_import_job_id_job", ondelete="RESTRICT"),
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint("CHAR_LENGTH(config_hash) = 64", name="config_hash_length"),
        CheckConstraint("status IN ('DRAFT','VALIDATING','INVALID','READY','COMMITTING','COMMITTED','FAILED')", name="status"),
        CheckConstraint("import_mode IN ('append','upsert')", name="ck_data_import_import_mode"),
        Index("ix_data_import_project_status", "project_id", "status", "created_at"),
        MYSQL_TABLE_KWARGS,
    )


class DataImportValidationRow(DataCenterBase):
    __tablename__ = "data_import_validation"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(96), nullable=False)
    import_id: Mapped[str] = mapped_column(String(96), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    counts: Mapped[dict[str, int]] = mapped_column(JSON, nullable=False)
    report_artifact_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    warnings: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    parser_revision: Mapped[str | None] = mapped_column(String(128), nullable=True)
    parser_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(("project_id",), (_PROJECT_ID,), name="fk_data_import_validation_project_id_project", ondelete="RESTRICT"),
        ForeignKeyConstraint(("import_id",), (DataImportRow.__table__.c.id,), name="fk_data_import_validation_import_id_data_import", ondelete="RESTRICT"),
        ForeignKeyConstraint(("project_id", "report_artifact_id"), (_ARTIFACT_PROJECT_ID, _ARTIFACT_ID), name="fk_data_import_validation_project_artifact", ondelete="RESTRICT"),
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint("status IN ('PENDING','VALID','STALE','INVALID')", name="status"),
        CheckConstraint("parser_hash IS NULL OR CHAR_LENGTH(parser_hash) = 64", name="parser_hash_length"),
        Index("ix_data_import_validation_import", "project_id", "import_id", "created_at"),
        MYSQL_TABLE_KWARGS,
    )


class DataVersionRow(DataCenterBase):
    __tablename__ = "data_version"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(96), nullable=False)
    domain_id: Mapped[str] = mapped_column(String(96), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    record_count: Mapped[int] = mapped_column(Integer, nullable=False)
    source_import_id: Mapped[str] = mapped_column(String(96), nullable=False)
    snapshot_artifact_id: Mapped[str] = mapped_column(String(96), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    quality_report_ref: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    referenced_by_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        ForeignKeyConstraint(("project_id",), (_PROJECT_ID,), name="fk_data_version_project_id_project", ondelete="RESTRICT"),
        ForeignKeyConstraint(("source_import_id",), (DataImportRow.__table__.c.id,), name="fk_data_version_source_import_id_data_import", ondelete="RESTRICT"),
        ForeignKeyConstraint(("project_id", "snapshot_artifact_id"), (_ARTIFACT_PROJECT_ID, _ARTIFACT_ID), name="fk_data_version_project_artifact", ondelete="RESTRICT"),
        UniqueConstraint("project_id", "domain_id", "version", name="uq_data_version_scope_number"),
        UniqueConstraint("project_id", "id", name="uq_data_version_project_id_id"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("record_count >= 0", name="record_count_nonnegative"),
        CheckConstraint("referenced_by_count >= 0", name="referenced_by_nonnegative"),
        CheckConstraint("CHAR_LENGTH(sha256) = 64", name="sha256_length"),
        CheckConstraint("status IN ('READY','ARCHIVED')", name="status"),
        Index("ix_data_version_scope_status", "project_id", "domain_id", "status", "version"),
        MYSQL_TABLE_KWARGS,
    )


class DataVersionReferenceRow(DataCenterBase):
    __tablename__ = "data_version_reference"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(96), nullable=False)
    version_id: Mapped[str] = mapped_column(String(96), nullable=False)
    reference_id: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(("project_id",), (_PROJECT_ID,), name="fk_data_version_reference_project_id_project", ondelete="RESTRICT"),
        ForeignKeyConstraint(("version_id",), (DataVersionRow.__table__.c.id,), name="fk_data_version_reference_version_id_data_version", ondelete="RESTRICT"),
        UniqueConstraint("project_id", "version_id", "reference_id", name="uq_data_version_reference"),
        Index("ix_data_version_reference_version", "project_id", "version_id"),
        MYSQL_TABLE_KWARGS,
    )


class MoleculeRow(DataCenterBase):
    """Project-local molecule identity; structures are never merged by key alone."""

    __tablename__ = "molecule"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(96), nullable=False)
    structure_id: Mapped[str] = mapped_column(String(96), nullable=False)
    canonical_smiles: Mapped[str] = mapped_column(Text, nullable=False)
    inchikey: Mapped[str | None] = mapped_column(String(32), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    quality_status: Mapped[str] = mapped_column(String(16), nullable=False, default="UNKNOWN")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(("project_id",), (_PROJECT_ID,), name="fk_molecule_project_id_project", ondelete="RESTRICT"),
        ForeignKeyConstraint(("project_id", "structure_id"), (_STRUCTURE_PROJECT_ID, _STRUCTURE_ID), name="fk_molecule_project_structure", ondelete="RESTRICT"),
        UniqueConstraint("project_id", "id", name="uq_molecule_project_id_id"),
        UniqueConstraint("project_id", "structure_id", name="uq_molecule_project_structure"),
        CheckConstraint("quality_status IN ('UNKNOWN','VALID','WARNING','INVALID')", name="ck_molecule_quality_status"),
        Index("ix_molecule_project_created", "project_id", "created_at"),
        MYSQL_TABLE_KWARGS,
    )


class ExperimentRecordRow(DataCenterBase):
    """Immutable observed values, repeated per full immutable data version."""

    __tablename__ = "experiment_record"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(96), nullable=False)
    molecule_id: Mapped[str] = mapped_column(String(96), nullable=False)
    domain_id: Mapped[str] = mapped_column(String(96), nullable=False)
    chromatography_type: Mapped[str] = mapped_column(String(32), nullable=False)
    data_version_id: Mapped[str] = mapped_column(String(96), nullable=False)
    identity_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    values: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(("project_id",), (_PROJECT_ID,), name="fk_experiment_record_project_id_project", ondelete="RESTRICT"),
        ForeignKeyConstraint(("project_id", "molecule_id"), (MoleculeRow.__table__.c.project_id, MoleculeRow.__table__.c.id), name="fk_experiment_record_project_molecule", ondelete="RESTRICT"),
        ForeignKeyConstraint(("project_id", "data_version_id"), (DataVersionRow.__table__.c.project_id, DataVersionRow.__table__.c.id), name="fk_experiment_record_project_data_version", ondelete="RESTRICT"),
        UniqueConstraint("project_id", "domain_id", "data_version_id", "identity_hash", name="uq_experiment_record_version_identity"),
        CheckConstraint("CHAR_LENGTH(identity_hash) = 64", name="ck_experiment_record_identity_hash_length"),
        Index("ix_experiment_record_project_molecule", "project_id", "molecule_id"),
        Index("ix_experiment_record_project_version", "project_id", "data_version_id"),
        MYSQL_TABLE_KWARGS,
    )


class PredictionRecordRow(DataCenterBase):
    __tablename__ = "prediction_record"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(96), nullable=False)
    prediction_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    batch_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(96), nullable=False)
    chromatography_type: Mapped[str] = mapped_column(String(32), nullable=False)
    model_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    result_ref: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    warnings: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    artifact_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    etag: Mapped[str] = mapped_column(String(128), nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(("project_id",), (_PROJECT_ID,), name="fk_prediction_record_project_id_project", ondelete="RESTRICT"),
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint("status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELED','TIMED_OUT','INTERRUPTED','ARCHIVED')", name="status"),
        Index("ix_prediction_record_project_created", "project_id", "created_at", "id"),
        MYSQL_TABLE_KWARGS,
    )


class PredictionDraftRow(DataCenterBase):
    """Durable, immutable replay input copied from a prediction record."""

    __tablename__ = "prediction_draft"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(96), nullable=False)
    source_record_id: Mapped[str] = mapped_column(String(96), nullable=False)
    source_prediction_id: Mapped[str] = mapped_column(String(96), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(96), nullable=False)
    chromatography_type: Mapped[str] = mapped_column(String(32), nullable=False)
    tool: Mapped[str] = mapped_column(String(96), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    structure_refs: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    structure_snapshots: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    modifier_structure_snapshots: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    input: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    model_selection: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    source_model_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    source_record_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    etag: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(("project_id",), (_PROJECT_ID,), name="fk_prediction_draft_project_id_project", ondelete="RESTRICT"),
        ForeignKeyConstraint(("source_record_id",), (PredictionRecordRow.__table__.c.id,), name="fk_prediction_draft_source_record_id_prediction_record", ondelete="RESTRICT"),
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint("source_record_revision >= 1", name="source_record_revision_positive"),
        Index("ix_prediction_draft_project_updated", "project_id", "updated_at", "id"),
        Index("ix_prediction_draft_project_source", "project_id", "source_record_id"),
        MYSQL_TABLE_KWARGS,
    )


class ExportRow(DataCenterBase):
    """Immutable source snapshots and lifecycle projection for record exports."""

    __tablename__ = "export"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(96), nullable=False)
    job_id: Mapped[str] = mapped_column(String(96), nullable=False, unique=True)
    format: Mapped[str] = mapped_column(String(16), nullable=False)
    format_version: Mapped[str] = mapped_column(String(32), nullable=False, default="1.0")
    source_snapshots: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="QUEUED")
    artifact_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (
        ForeignKeyConstraint(("project_id",), (_PROJECT_ID,), name="fk_export_project_id_project", ondelete="RESTRICT"),
        ForeignKeyConstraint(("job_id",), (_JOB_ID,), name="fk_export_job_id_job", ondelete="RESTRICT"),
        ForeignKeyConstraint(("project_id", "artifact_id"), (_ARTIFACT_PROJECT_ID, _ARTIFACT_ID), name="fk_export_project_artifact", ondelete="RESTRICT"),
        CheckConstraint("format IN ('json','csv','xlsx')", name="export_format"),
        CheckConstraint("status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELED','TIMED_OUT','INTERRUPTED')", name="export_status"),
        Index("ix_export_project_created", "project_id", "created_at", "id"),
        MYSQL_TABLE_KWARGS,
    )


def _domain_dto(row: DataDomainRow) -> DataDomainSchema:
    return DataDomainSchema(
        id=row.id,
        version=row.version,
        chromatography_type=row.chromatography_type,
        record_schema={key: DomainField.model_validate(value) for key, value in row.record_schema.items()},
        unit_definitions=row.unit_definitions,
        identity_fields=row.identity_fields,
        deduplication_policy=row.deduplication_policy,
        supported_import_modes=row.supported_import_modes,
    )


def _import_dto(row: DataImportRow) -> DataImport:
    return DataImport(
        id=row.id, project_id=row.project_id, domain_id=row.domain_id, artifact_id=row.artifact_id,
        schema_version=row.schema_version, import_mode=row.import_mode, field_mapping=row.field_mapping,
        source_description=row.source_description, revision=row.revision, config_hash=row.config_hash,
        etag=row.etag, status=row.status, job_id=row.job_id, validation_ref=row.validation_ref,
        new_data_version_id=row.new_data_version_id, request_snapshot=row.request_snapshot,
        created_at=_aware(row.created_at), updated_at=_aware(row.updated_at),
    )


def _validation_dto(row: DataImportValidationRow) -> DataImportValidation:
    return DataImportValidation(
        id=row.id, import_id=row.import_id, revision=row.revision, config_hash=row.config_hash,
        status=row.status, counts=ValidationCounts.model_validate(row.counts),
        report_artifact_id=row.report_artifact_id, warnings=row.warnings, created_at=_aware(row.created_at),
        parser_revision=row.parser_revision, parser_hash=row.parser_hash,
    )


def _version_dto(row: DataVersionRow) -> DataVersion:
    return DataVersion(
        id=row.id, project_id=row.project_id, domain_id=row.domain_id, version=row.version,
        sha256=row.sha256, status=row.status, record_count=row.record_count,
        source_import_id=row.source_import_id, snapshot_artifact_id=row.snapshot_artifact_id,
        schema_version=row.schema_version, quality_report_ref=row.quality_report_ref,
        created_at=_aware(row.created_at), referenced_by_count=row.referenced_by_count,
    )


class SQLAlchemyDataDomainRegistry:
    """Trusted registry backed by module-owned rows."""

    def __init__(self, session: Session):
        self.session = session

    def list(self) -> tuple[DataDomainSchema, ...]:
        rows = self.session.scalars(select(DataDomainRow).order_by(DataDomainRow.id, DataDomainRow.version)).all()
        return tuple(_domain_dto(row) for row in rows)

    def get(self, domain_id: str, schema_version: str | None = None) -> DataDomainSchema | None:
        stmt = select(DataDomainRow).where(DataDomainRow.id == domain_id)
        if schema_version is not None:
            stmt = stmt.where(DataDomainRow.version == schema_version)
        row = self.session.scalars(stmt.order_by(DataDomainRow.version.desc())).first()
        return _domain_dto(row) if row else None

    def register(self, schema: DataDomainSchema) -> DataDomainSchema:
        row = DataDomainRow(
            id=schema.id, version=schema.version, chromatography_type=schema.chromatography_type,
            record_schema={key: value.model_dump(mode="json") for key, value in schema.record_schema.items()},
            unit_definitions=schema.unit_definitions, identity_fields=schema.identity_fields,
            deduplication_policy=schema.deduplication_policy, supported_import_modes=schema.supported_import_modes,
        )
        self.session.add(row)
        self.session.flush()
        return schema


class SQLAlchemyDataRepository:
    """DataRepository implementation using a caller-owned SQLAlchemy session."""

    def __init__(self, session: Session, *, id_factory: Callable[[str], str] = _id, snapshot_provider: Callable[[DataCommitRequest, ConfirmationConsumeRequest], dict[str, Any]] | None = None, parser_revision: str | None = None, parser_hash: str | None = None):
        if callable(session) or not all(hasattr(session, name) for name in ("add", "flush", "scalar")):
            raise TypeError("SQLAlchemyDataRepository requires a caller-owned Session")
        self.session = session
        self._id_factory = id_factory
        self._snapshot_provider = snapshot_provider
        self._parser_revision = parser_revision
        self._parser_hash = parser_hash

    def allocate_and_save_new_data_version(self, draft: DataVersionDraft, *, version_id: str | None = None) -> DataVersion:
        # Lock the latest concrete row.  Locking ``MAX(version)`` does not
        # lock an empty range on MySQL, so two first writers can both observe
        # zero and believe they own version 1.  The scope unique constraint is
        # still the final race detector when no row exists yet.
        latest = self.session.scalar(
            select(DataVersionRow)
            .where(
                DataVersionRow.project_id == draft.project_id,
                DataVersionRow.domain_id == draft.domain_id,
            )
            .order_by(DataVersionRow.version.desc())
            .limit(1)
            .with_for_update()
        )
        number = int(latest.version if latest is not None else 0) + 1
        row = DataVersionRow(
            id=version_id or self._id_factory("dv"), project_id=draft.project_id, domain_id=draft.domain_id,
            version=number, sha256=draft.sha256, status="READY", record_count=draft.record_count,
            source_import_id=draft.source_import_id, snapshot_artifact_id=draft.snapshot_artifact_id,
            schema_version=draft.schema_version,
            quality_report_ref=draft.quality_report_ref.model_dump(mode="json") if hasattr(draft.quality_report_ref, "model_dump") else draft.quality_report_ref,
            created_at=_now(), referenced_by_count=0,
        )
        try:
            with self.session.begin_nested():
                self.session.add(row)
                self.session.flush()
        except IntegrityError as exc:
            raise DataConflictError("数据版本号并发冲突，请重试") from exc
        return _version_dto(row)

    def get_data_version(self, project_id: str, version_id: str) -> DataVersion | None:
        row = self.session.scalar(select(DataVersionRow).where(DataVersionRow.id == version_id, DataVersionRow.project_id == project_id))
        return _version_dto(row) if row else None

    def get_data_version_any(self, project_ids: Iterable[str], version_id: str) -> DataVersion | None:
        """Resolve a resource only inside the actor's already authorized scope."""

        row = self.session.scalar(
            select(DataVersionRow).where(
                DataVersionRow.id == version_id,
                DataVersionRow.project_id.in_(list(project_ids)),
            )
        )
        return _version_dto(row) if row else None

    def list_data_versions(self, project_id: str, domain_id: str, *, status: str | None = None, limit: int | None = None, after_id: str | None = None) -> list[DataVersion]:
        stmt = select(DataVersionRow).where(DataVersionRow.project_id == project_id, DataVersionRow.domain_id == domain_id)
        if status:
            stmt = stmt.where(DataVersionRow.status == status)
        if after_id:
            after = self.session.scalar(select(DataVersionRow).where(DataVersionRow.id == after_id, DataVersionRow.project_id == project_id))
            if after is not None:
                stmt = stmt.where((DataVersionRow.version < after.version) | ((DataVersionRow.version == after.version) & (DataVersionRow.id < after.id)))
        stmt = stmt.order_by(DataVersionRow.version.desc(), DataVersionRow.id.desc())
        if limit is not None:
            stmt = stmt.limit(limit)
        rows = self.session.scalars(stmt).all()
        return [_version_dto(row) for row in rows]

    def add_data_version_reference(self, project_id: str, version_id: str, reference_id: str) -> DataVersion:
        row = self.session.scalar(select(DataVersionRow).where(DataVersionRow.id == version_id, DataVersionRow.project_id == project_id).with_for_update())
        if row is None:
            raise DataNotFoundError("数据版本不存在或不属于当前项目")
        if row.status == "ARCHIVED":
            raise DataConflictError("已归档的数据版本不能新增引用")
        try:
            with self.session.begin_nested():
                self.session.add(DataVersionReferenceRow(id=self._id_factory("dvr"), project_id=project_id, version_id=version_id, reference_id=reference_id, created_at=_now()))
                self.session.flush()
        except IntegrityError:
            pass
        count = self.session.scalar(select(func.count()).select_from(DataVersionReferenceRow).where(DataVersionReferenceRow.project_id == project_id, DataVersionReferenceRow.version_id == version_id))
        row.referenced_by_count = int(count or 0)
        self.session.flush()
        return _version_dto(row)

    def archive_data_version(self, project_id: str, version_id: str) -> DataVersion:
        row = self.session.scalar(select(DataVersionRow).where(DataVersionRow.id == version_id, DataVersionRow.project_id == project_id).with_for_update())
        if row is None:
            raise DataNotFoundError("数据版本不存在或不属于当前项目")
        if row.referenced_by_count or self.session.scalar(select(func.count()).select_from(DataVersionReferenceRow).where(DataVersionReferenceRow.project_id == project_id, DataVersionReferenceRow.version_id == version_id)):
            raise DataConflictError("已被训练或评测引用的数据版本不能归档")
        row.status = "ARCHIVED"
        self.session.flush()
        return _version_dto(row)

    def save_import(self, value: DataImport) -> DataImport:
        row = self.session.scalar(select(DataImportRow).where(DataImportRow.id == value.id).with_for_update())
        if row is not None and row.project_id != value.project_id:
            raise DataConflictError("data_import 已属于其他项目")
        if row is not None and row.revision > value.revision:
            raise DataConflictError("data_import revision 已过期")
        if row is not None and row.revision == value.revision and row.config_hash != value.config_hash:
            raise DataConflictError("data_import revision 已被其他配置占用")
        if row is None:
            row = DataImportRow(id=value.id)
            self.session.add(row)
        for field in ("project_id", "domain_id", "artifact_id", "schema_version", "import_mode", "field_mapping", "source_description", "revision", "config_hash", "etag", "status", "job_id", "validation_ref", "new_data_version_id", "request_snapshot", "created_at", "updated_at"):
            setattr(row, field, getattr(value, field))
        self.session.flush()
        return _import_dto(row)

    def patch_import(
        self,
        value: DataImport,
        *,
        expected_status: str,
        expected_revision: int,
        expected_etag: str,
        expected_job_id: str | None,
    ) -> DataImport:
        row = self.session.scalar(
            select(DataImportRow)
            .where(DataImportRow.id == value.id)
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if row is None or row.project_id != value.project_id:
            raise DataConflictError("data_import 在修改期间已变化")
        if (
            row.status != expected_status
            or row.revision != expected_revision
            or row.etag != expected_etag
            or row.job_id != expected_job_id
        ):
            raise DataConflictError("data_import 在修改期间已变化")
        for field in (
            "project_id", "domain_id", "artifact_id", "schema_version", "import_mode",
            "field_mapping", "source_description", "revision", "config_hash", "etag",
            "status", "job_id", "validation_ref", "new_data_version_id", "request_snapshot",
            "created_at", "updated_at",
        ):
            setattr(row, field, getattr(value, field))
        self.session.flush()
        return _import_dto(row)

    def get_import(self, project_id: str, import_id: str) -> DataImport | None:
        row = self.session.scalar(select(DataImportRow).where(DataImportRow.id == import_id, DataImportRow.project_id == project_id))
        return _import_dto(row) if row else None

    def save_validation(self, project_id: str, value: DataImportValidation) -> DataImportValidation:
        current = self.get_import(project_id, value.import_id)
        status = value.status
        if current is not None and (current.revision != value.revision or current.config_hash != value.config_hash):
            status = "STALE"
        if self._parser_revision is not None and value.parser_revision != self._parser_revision:
            status = "STALE"
        if self._parser_hash is not None and value.parser_hash != self._parser_hash:
            status = "STALE"
        row = self.session.scalar(select(DataImportValidationRow).where(DataImportValidationRow.id == value.id))
        if row is not None and (row.project_id != project_id or row.import_id != value.import_id):
            raise DataConflictError("data_import validation 已绑定其他项目或导入")
        if row is None:
            row = DataImportValidationRow(id=value.id)
            self.session.add(row)
        row.project_id = project_id
        row.import_id = value.import_id
        row.revision = value.revision
        row.config_hash = value.config_hash
        row.status = status
        row.counts = value.counts.model_dump(mode="json")
        row.report_artifact_id = value.report_artifact_id
        row.warnings = list(value.warnings)
        row.parser_revision = value.parser_revision
        row.parser_hash = value.parser_hash
        row.created_at = value.created_at
        self.session.flush()
        return _validation_dto(row)

    def get_validation(self, project_id: str, import_id: str) -> DataImportValidation | None:
        row = self.session.scalars(select(DataImportValidationRow).where(DataImportValidationRow.project_id == project_id, DataImportValidationRow.import_id == import_id).order_by(DataImportValidationRow.created_at.desc())).first()
        return _validation_dto(row) if row else None

    def get_import_any(self, project_ids: Iterable[str], import_id: str) -> DataImport | None:
        row = self.session.scalar(select(DataImportRow).where(DataImportRow.id == import_id, DataImportRow.project_id.in_(list(project_ids))))
        return _import_dto(row) if row else None

    def commit(self, request: DataCommitRequest, confirmation: ConfirmationConsumeRequest, *, snapshot_artifact_id: str | None = None, sha256: str | None = None, record_count: int | None = None) -> DataVersion:
        """Atomically consume confirmation, publish version and mark import committed.

        A parser/scanner must provide snapshot metadata.  Missing metadata is a
        real unavailable capability and is never replaced with a fabricated
        hash or record count.
        """
        if (not snapshot_artifact_id or not sha256 or record_count is None) and self._snapshot_provider is not None:
            try:
                supplied = self._snapshot_provider(request, confirmation)
            except Exception as exc:
                raise DataUnavailableError("快照解析器暂不可用，不能提交数据版本") from exc
            if not isinstance(supplied, dict):
                raise DataUnavailableError("快照解析器返回了无效元数据")
            snapshot_artifact_id = snapshot_artifact_id or supplied.get("snapshot_artifact_id")
            sha256 = sha256 or supplied.get("sha256")
            record_count = record_count if record_count is not None else supplied.get("record_count")
        if not snapshot_artifact_id or not sha256 or record_count is None:
            raise DataUnavailableError("解析器未提供不可变快照元数据，不能提交数据版本")
        if not isinstance(snapshot_artifact_id, str) or not snapshot_artifact_id.strip():
            raise DataUnavailableError("解析器返回的快照制品标识无效")
        if not isinstance(sha256, str) or _SHA256.fullmatch(sha256) is None:
            raise DataUnavailableError("解析器返回的快照哈希无效")
        if isinstance(record_count, bool) or not isinstance(record_count, int) or record_count < 0:
            raise DataUnavailableError("解析器返回的记录数量无效")
        import_row = self.session.scalar(select(DataImportRow).where(DataImportRow.id == request.data_import.id, DataImportRow.project_id == confirmation.project_id).with_for_update())
        if import_row is None:
            raise DataNotFoundError("数据导入不存在或不属于当前项目")
        if import_row.status == "COMMITTED" and import_row.new_data_version_id:
            existing = self.get_data_version(confirmation.project_id, import_row.new_data_version_id)
            if existing:
                return existing
        if import_row.revision != confirmation.revision or import_row.config_hash != confirmation.config_hash:
            raise DataConflictError("确认绑定的导入 revision 已过期")
        if request.validation.status != "VALID" or request.validation.revision != import_row.revision or request.validation.config_hash != import_row.config_hash:
            raise DataConflictError("导入验证结果已过期或未通过")
        self._consume_confirmation(confirmation)
        version = self.allocate_and_save_new_data_version(DataVersionDraft(
            project_id=import_row.project_id, domain_id=import_row.domain_id, source_import_id=import_row.id,
            snapshot_artifact_id=snapshot_artifact_id, schema_version=import_row.schema_version,
            sha256=sha256, record_count=record_count, quality_report_ref=None,
        ))
        import_row.status = "COMMITTED"
        import_row.new_data_version_id = version.id
        try:
            from chrom_platform.infrastructure.models import Confirmation
            confirmation_row = self.session.scalar(select(Confirmation).where(Confirmation.id == confirmation.confirmation_id))
            if confirmation_row is not None:
                confirmation_row.consumed_result_ref = {"type": "data_version", "id": version.id}
        except ImportError:
            pass
        import_row.updated_at = _now()
        self.session.flush()
        return version

    def _consume_confirmation(self, request: ConfirmationConsumeRequest) -> None:
        # Importing the shared row here avoids widening infrastructure models;
        # its lifecycle remains owned by task-core's confirmation service.
        try:
            from chrom_platform.infrastructure.models import Confirmation
        except ImportError as exc:  # pragma: no cover - trimmed deployments
            raise DataUnavailableError("Confirmation 持久化未纳入构建") from exc
        row = self.session.scalar(select(Confirmation).where(Confirmation.id == request.confirmation_id).with_for_update())
        if row is None or row.project_id != request.project_id:
            raise ConfirmationRequiredError("Confirmation 不存在或项目不匹配")
        if request.actor_id is not None and row.actor_id != request.actor_id:
            raise ConfirmationRequiredError("Confirmation 主体不匹配")
        if row.action != request.action or row.revision != request.revision or row.config_hash != request.config_hash:
            raise ConfirmationRequiredError("Confirmation 未绑定当前导入 revision")
        ref = row.resource_ref or {}
        if ref.get("type") != request.resource_ref.type or ref.get("id") != request.resource_ref.id:
            raise ConfirmationRequiredError("Confirmation 资源绑定不匹配")
        request_hash = _confirmation_hash(request)
        if row.status == "CONSUMED":
            if row.consumed_request_hash != request_hash:
                raise ConfirmationRequiredError("Confirmation 已被其他语义的请求消费")
            return
        if row.status != "CONFIRMED" or _aware(row.expires_at) <= _now():
            raise ConfirmationRequiredError("Confirmation 不可消费")
        row.status = "CONSUMED"
        row.consumed_at = _now()
        row.consumed_request_hash = request_hash
        self.session.flush()


def _prediction_dto(row: PredictionRecordRow) -> PredictionRecord:
    return PredictionRecord(
        id=row.id, project_id=row.project_id, prediction_id=row.prediction_id, batch_id=row.batch_id,
        source=row.source, actor_id=row.actor_id, chromatography_type=row.chromatography_type,
        model_snapshot=row.model_snapshot,
        input_snapshot=_public_prediction_input_snapshot(row.input_snapshot),
        result_ref=row.result_ref,
        warnings=row.warnings, status=row.status, archived_at=_aware(row.archived_at) if row.archived_at else None,
        created_at=_aware(row.created_at), artifact_refs=row.artifact_refs, revision=row.revision, etag=row.etag,
    )


def _prediction_draft_dto(row: PredictionDraftRow) -> PredictionDraftView:
    from chrom_platform.core.dto import ResourceRef

    return PredictionDraftView(
        id=row.id, source_record_id=row.source_record_id, source_prediction_id=row.source_prediction_id,
        project_id=row.project_id, chromatography_type=row.chromatography_type, tool=row.tool,
        structure_refs=[ResourceRef(type="structure", id=value) for value in row.structure_refs],
        structure_snapshots=row.structure_snapshots,
        modifier_structure_snapshots=row.modifier_structure_snapshots, input=row.input,
        schema_version=row.schema_version, model_selection=row.model_selection,
        source_model_snapshot=row.source_model_snapshot, source_record_revision=row.source_record_revision,
        revision=row.revision, etag=row.etag, created_at=_aware(row.created_at), updated_at=_aware(row.updated_at),
    )


class SQLAlchemyPredictionRecordRepository:
    """Project-scoped prediction record and archive adapter."""

    def __init__(self, session: Session, *, id_factory: Callable[[str], str] = _id):
        if callable(session) or not all(hasattr(session, name) for name in ("add", "flush", "scalar")):
            raise TypeError("SQLAlchemyPredictionRecordRepository requires a caller-owned Session")
        self.session = session
        self._id_factory = id_factory

    def get(self, project_id: str, record_id: str) -> PredictionRecord | None:
        row = self.session.scalar(select(PredictionRecordRow).where(PredictionRecordRow.id == record_id, PredictionRecordRow.project_id == project_id))
        return _prediction_dto(row) if row else None

    def get_prediction_any(self, project_ids: Iterable[str], record_id: str) -> PredictionRecord | None:
        row = self.session.scalar(
            select(PredictionRecordRow).where(
                PredictionRecordRow.id == record_id,
                PredictionRecordRow.project_id.in_(list(project_ids)),
            )
        )
        return _prediction_dto(row) if row else None

    def list(self, project_id: str, *, chromatography_type: str | None = None, status: str | None = None, limit: int = 50, after_id: str | None = None) -> list[PredictionRecord]:
        stmt = select(PredictionRecordRow).where(PredictionRecordRow.project_id == project_id)
        if chromatography_type:
            stmt = stmt.where(PredictionRecordRow.chromatography_type == chromatography_type)
        if status:
            stmt = stmt.where(PredictionRecordRow.status == status)
        if after_id:
            after = self.session.scalar(select(PredictionRecordRow).where(PredictionRecordRow.id == after_id, PredictionRecordRow.project_id == project_id))
            if after is not None:
                stmt = stmt.where((PredictionRecordRow.created_at < after.created_at) | ((PredictionRecordRow.created_at == after.created_at) & (PredictionRecordRow.id < after.id)))
        rows = self.session.scalars(stmt.order_by(PredictionRecordRow.created_at.desc(), PredictionRecordRow.id.desc()).limit(limit)).all()
        return [_prediction_dto(row) for row in rows]

    def create_replay_draft(self, record: PredictionRecord, source: PredictionReplaySourceData, *, actor_id: str) -> PredictionDraftView:
        if source.project_id != record.project_id or source.chromatography_type != record.chromatography_type:
            raise DataUnavailableError("原始预测快照项目或类型绑定不一致")
        now = _now()
        draft_id = self._id_factory("pd")
        draft = PredictionDraftRow(
            id=draft_id, project_id=record.project_id, source_record_id=record.id,
            source_prediction_id=source.prediction_id, actor_id=actor_id,
            chromatography_type=record.chromatography_type, tool=source.tool,
            schema_version=source.schema_version, structure_refs=list(source.structure_refs),
            structure_snapshots=list(source.structure_snapshots),
            modifier_structure_snapshots=list(source.modifier_structure_snapshots),
            input=dict(source.input), model_selection=dict(source.model_selection),
            source_model_snapshot=dict(source.model_snapshot), source_record_revision=record.revision,
            revision=1, etag=f'"{hashlib.sha256((draft_id + ":1").encode()).hexdigest()[:48]}-1"',
            created_at=now, updated_at=now,
        )
        self.session.add(draft)
        self.session.flush()
        return _prediction_draft_dto(draft)

    def get_replay_draft(self, project_id: str, draft_id: str) -> PredictionDraftView | None:
        row = self.session.scalar(select(PredictionDraftRow).where(
            PredictionDraftRow.id == draft_id, PredictionDraftRow.project_id == project_id,
        ))
        return _prediction_draft_dto(row) if row else None

    def get_replay_draft_any(self, project_ids: Iterable[str], draft_id: str) -> PredictionDraftView | None:
        row = self.session.scalar(select(PredictionDraftRow).where(
            PredictionDraftRow.id == draft_id, PredictionDraftRow.project_id.in_(list(project_ids)),
        ))
        return _prediction_draft_dto(row) if row else None

    def archive(self, project_id: str, record_id: str, *, confirmation_id: str, actor: Any, consume_confirmation: Any) -> PredictionRecord:
        row = self.session.scalar(select(PredictionRecordRow).where(PredictionRecordRow.id == record_id, PredictionRecordRow.project_id == project_id).with_for_update())
        if row is None:
            raise DataNotFoundError("预测记录不存在")
        current = _prediction_dto(row)
        consume_confirmation(confirmation_id, actor, current, self.session)
        row.status = "ARCHIVED"
        row.archived_at = _now()
        row.revision += 1
        row.etag = f'"{hashlib.sha256((row.id + str(row.revision)).encode()).hexdigest()[:48]}-{row.revision}"'
        self.session.flush()
        return _prediction_dto(row)

class SQLAlchemyPredictionRecordWriter(PredictionRecordStagingPort):
    """Public data-center writer used by prediction's atomic adapter."""

    @staticmethod
    def _etag(row: PredictionRecordRow) -> str:
        payload = {"id": row.id, "revision": row.revision, "status": row.status, "result_ref": row.result_ref}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()

    def stage_prediction_record(self, draft: PredictionRecordDraft, *, session: Session) -> None:
        session.add(PredictionRecordRow(
            id=draft.id,
            prediction_id=draft.prediction_id,
            batch_id=draft.batch_id,
            project_id=draft.project_id,
            source=draft.source,
            actor_id=draft.actor_id,
            chromatography_type=draft.chromatography_type,
            model_snapshot=draft.model_snapshot,
            input_snapshot=draft.input_snapshot,
            result_ref=draft.result_ref,
            warnings=draft.warnings,
            status=draft.status,
            created_at=draft.created_at,
            artifact_refs=draft.artifact_refs,
            revision=draft.revision,
            etag=draft.etag,
        ))

    def update_prediction_record(
        self,
        record_id: str,
        *,
        status: str,
        result_ref: dict[str, Any] | None = None,
        session: Session,
    ) -> None:
        row = session.scalar(select(PredictionRecordRow).where(PredictionRecordRow.id == record_id).with_for_update())
        if row is None:
            raise RuntimeError("PREDICTION_RECORD_NOT_FOUND")
        row.status = status
        row.result_ref = result_ref
        row.revision += 1
        row.etag = self._etag(row)
        session.flush()

    @staticmethod
    def _batch_row_record_id(batch_id: str, row_id: str) -> str:
        identity = json.dumps([batch_id, row_id], ensure_ascii=False, separators=(",", ":"))
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return f"batchrow_{digest}"

    def write_batch_row_record(
        self,
        *,
        session: Session,
        batch_id: str,
        row_id: str,
        project_id: str,
        actor_id: str,
        source: str,
        chromatography_type: str,
        model_snapshot: dict[str, Any],
        input_snapshot: dict[str, Any],
        status: str,
        warnings: list[str],
        artifact_refs: list[dict[str, Any]],
    ) -> PredictionRecordRow:
        """Persist one actually executed batch row in the caller's transaction.

        The result link intentionally points at the batch.  Row identity lives
        in this record's deterministic ID and never contaminates the scientific
        input snapshot.  Retry updates are idempotent and cannot replace the
        frozen scientific identity.
        """
        if status not in {"RUNNING", "SUCCEEDED", "FAILED", "CANCELED", "TIMED_OUT", "INTERRUPTED"}:
            raise ValueError("BATCH_ROW_RECORD_REQUIRES_EXECUTED_STATUS")

        record_id = self._batch_row_record_id(batch_id, row_id)
        row = session.scalar(
            select(PredictionRecordRow)
            .where(PredictionRecordRow.id == record_id)
            .with_for_update()
        )
        frozen_identity = {
            "batch_id": batch_id,
            "project_id": project_id,
            "actor_id": actor_id,
            "source": source,
            "chromatography_type": chromatography_type,
            "model_snapshot": model_snapshot,
            "input_snapshot": input_snapshot,
            "artifact_refs": artifact_refs,
        }
        if row is None:
            row = PredictionRecordRow(
                id=record_id,
                prediction_id=None,
                batch_id=batch_id,
                project_id=project_id,
                source=source,
                actor_id=actor_id,
                chromatography_type=chromatography_type,
                model_snapshot=deepcopy(model_snapshot),
                input_snapshot=deepcopy(input_snapshot),
                result_ref={"type": "batch_prediction", "id": batch_id},
                warnings=list(warnings),
                status=status,
                archived_at=None,
                created_at=_now(),
                artifact_refs=deepcopy(artifact_refs),
                revision=1,
                etag="",
            )
            row.etag = self._etag(row)
            session.add(row)
            session.flush()
            return row

        existing_identity = {
            "batch_id": row.batch_id,
            "project_id": row.project_id,
            "actor_id": row.actor_id,
            "source": row.source,
            "chromatography_type": row.chromatography_type,
            "model_snapshot": row.model_snapshot,
            "input_snapshot": row.input_snapshot,
            "artifact_refs": row.artifact_refs,
        }
        if existing_identity != frozen_identity:
            raise RuntimeError("BATCH_ROW_RECORD_IDENTITY_MISMATCH")
        if row.status == "ARCHIVED":
            return row

        next_warnings = list(warnings)
        next_result_ref = {"type": "batch_prediction", "id": batch_id}
        if row.status == status and row.warnings == next_warnings and row.result_ref == next_result_ref:
            return row

        row.status = status
        row.warnings = next_warnings
        row.result_ref = next_result_ref
        row.revision += 1
        row.etag = self._etag(row)
        session.flush()
        return row


__all__ = [
    "DataCenterBase", "DataDomainRow", "DataImportRow", "DataImportValidationRow",
    "DataVersionRow", "DataVersionReferenceRow", "MoleculeRow", "ExperimentRecordRow",
    "PredictionRecordRow", "PredictionDraftRow",
    "SQLAlchemyDataDomainRegistry", "SQLAlchemyDataRepository", "SQLAlchemyPredictionRecordRepository",
    "SQLAlchemyPredictionRecordWriter",
    "DataCenterRepository", "DataCenterDomainRegistry",
]

# Descriptive aliases keep assembly code independent from the implementation
# name while retaining one concrete adapter.
DataCenterRepository = SQLAlchemyDataRepository
DataCenterDomainRegistry = SQLAlchemyDataDomainRegistry
