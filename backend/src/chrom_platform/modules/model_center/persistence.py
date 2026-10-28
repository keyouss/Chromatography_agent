"""Small explicit in-memory adapter for tests and local vertical development.

Production must inject a transactional repository.  This adapter is never used
implicitly by the router or worker.
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import json
from typing import Any, Sequence

from sqlalchemy import (
    JSON, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, func,
    Integer, MetaData, String, Text, UniqueConstraint, update, select, desc,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.types import TypeDecorator

from chrom_platform.infrastructure.models import Job, JobAttempt, JobEvent, OutboxEvent
from chrom_platform.core.dto import ResourceRef
from chrom_platform.task_core.repository import SQLAlchemyTaskRepository
from chrom_platform.task_core.service import add_job_rows, sync_job_lifecycle
from chrom_platform.task_core.state import JobStatus

from .errors import ModelCenterConflictError
from .schemas import (
    ApprovalRequest, AuditEvent, Deployment, DeploymentAccepted, DeploymentDraft,
    EvaluationAccepted, EvaluationProtocol, Model, ModelComparison, ModelEvaluation, ModelRegistration,
    ProductionPointer, new_id, now_utc,
    RegistrationAccepted,
)


class InMemoryModelCenterRepository:
    def __init__(self):
        self.registrations: dict[str, ModelRegistration] = {}
        self.models: dict[str, Model] = {}
        self.evaluations: dict[str, ModelEvaluation] = {}
        self.comparisons: dict[str, ModelComparison] = {}
        self.approvals: dict[str, ApprovalRequest] = {}
        self.drafts: dict[str, DeploymentDraft] = {}
        self.deployments: dict[str, Deployment] = {}
        self.deployment_ledger: list[Deployment] = []
        self.pointers: dict[tuple[str, str, str], ProductionPointer] = {}
        self.audits: list[AuditEvent] = []
        self.idempotency: dict[str, tuple[str, Any]] = {}

    @contextmanager
    def transaction(self):
        snapshot = deepcopy(self.__dict__)
        try:
            yield self
        except Exception:
            self.__dict__.clear()
            self.__dict__.update(snapshot)
            raise

    def save_registration(self, value): self.registrations[value.id] = value; return value
    def get_registration(self, project_id, registration_id):
        value = self.registrations.get(registration_id)
        return value if value and value.project_id == project_id else None
    def save_model(self, value): self.models[value.id] = value; return value
    def get_model(self, project_id, model_id):
        value = self.models.get(model_id)
        return value if value and value.project_id == project_id else None
    def list_models(self, project_id, *, task_type=None, lifecycle=None):
        values = [m for m in self.models.values() if m.project_id == project_id and (task_type is None or m.task_type == task_type) and (lifecycle is None or m.lifecycle_status == lifecycle)]
        return sorted(values, key=lambda m: (m.created_at, m.id), reverse=True)
    def save_evaluation(self, value): self.evaluations[value.id] = value; return value
    def get_evaluation(self, project_id, evaluation_id):
        value = self.evaluations.get(evaluation_id)
        return value if value and value.project_id == project_id else None
    def list_evaluations(self, project_id, model_id=None):
        values = [e for e in self.evaluations.values() if e.project_id == project_id and (model_id is None or e.model_snapshot.id == model_id)]
        return sorted(values, key=lambda e: (e.created_at, e.id), reverse=True)
    def save_comparison(self, value): self.comparisons[value.id] = value; return value
    def get_comparison(self, project_id, comparison_id):
        value = self.comparisons.get(comparison_id)
        return value if value and value.project_id == project_id else None
    def save_approval(self, value): self.approvals[value.id] = value; return value
    def get_approval(self, project_id, approval_id):
        value = self.approvals.get(approval_id)
        return value if value and value.project_id == project_id else None
    def save_draft(self, value): self.drafts[value.id] = value; return value
    def get_draft(self, project_id, draft_id):
        value = self.drafts.get(draft_id)
        return value if value and value.project_id == project_id else None
    def save_deployment(self, value): self.deployments[value.id] = value; self.deployment_ledger.append(value.model_copy(deep=True)); return value
    def get_deployment(self, project_id, deployment_id):
        value = self.deployments.get(deployment_id)
        return value if value and value.project_id == project_id else None
    def list_deployments(self, project_id, scope_id=None):
        values = [d for d in self.deployments.values() if d.project_id == project_id and (scope_id is None or d.scope_id == scope_id)]
        return sorted(values, key=lambda d: (d.created_at, d.id), reverse=True)
    def get_pointer(self, project_id, scope_id, stage):
        key = (project_id, scope_id, stage)
        value = self.pointers.get(key)
        if value is None:
            value = ProductionPointer(project_id=project_id, scope_id=scope_id, stage=stage, model_id=None, revision=0, updated_at=now_utc())
            self.pointers[key] = value
        return value
    def cas_pointer(self, pointer=None, *, expected_revision, model_id, project_id=None, scope_id=None, stage=None):
        if pointer is None:
            pointer = self.get_pointer(str(project_id), str(scope_id), str(stage))
        current = self.get_pointer(pointer.project_id, pointer.scope_id, pointer.stage)
        if current.revision != expected_revision:
            raise ModelCenterConflictError("production pointer revision is stale", details={"expected_revision": expected_revision, "actual_revision": current.revision})
        value = current.model_copy(update={"model_id": model_id, "revision": current.revision + 1, "updated_at": now_utc()})
        self.pointers[(pointer.project_id, pointer.scope_id, pointer.stage)] = value
        return value
    def record_audit(self, event): self.audits.append(event); return event
    def idempotent_get(self, scope, request_hash):
        value = self.idempotency.get(scope)
        if value is None: return None
        old_hash, result = value
        if old_hash != request_hash:
            raise ModelCenterConflictError("idempotency key was reused with a different request")
        return deepcopy(result)
    def idempotent_put(self, scope, request_hash, value): self.idempotency[scope] = (request_hash, deepcopy(value))


class InMemoryArtifactPort:
    def __init__(self, artifacts: Sequence[object] = ()):
        self.artifacts = {}
        for value in artifacts:
            key = getattr(value, "id", None) or (value.get("id") if isinstance(value, dict) else None)
            if key: self.artifacts[str(key)] = value
    def get(self, project_id: str, artifact_id: str):
        value = self.artifacts.get(artifact_id)
        if value is None: return None
        owner = getattr(value, "project_id", None) or (value.get("project_id") if isinstance(value, dict) else None)
        return value if owner in (None, project_id) else None
    def list(self, *, project_id: str, model_id: str | None = None, artifact_id: str | None = None):
        values = []
        for value in self.artifacts.values():
            owner = getattr(value, "project_id", None) or (value.get("project_id") if isinstance(value, dict) else None)
            if owner not in (None, project_id):
                continue
            value_id = str(getattr(value, "id", None) or (value.get("id") if isinstance(value, dict) else ""))
            if artifact_id is None or value_id == artifact_id:
                values.append(value)
        return values


__all__ = ["InMemoryModelCenterRepository", "InMemoryArtifactPort"]


class InMemoryConfirmationPort:
    """Explicit test confirmation ledger with revision/hash binding."""
    def __init__(self): self.records: dict[str, dict[str, Any]] = {}
    def add(self, confirmation_id: str, **binding: Any): self.records[confirmation_id] = {**binding, "status": "CONFIRMED"}
    def consume(self, *, confirmation_id: str, project_id: str, resource_ref: dict[str, str], action: str, revision: int, config_hash: str, result_ref: dict[str, str], actor_id: str = "service"):
        row = self.records.get(confirmation_id)
        if row is None or row.get("status") != "CONFIRMED": raise ValueError("confirmation is missing or consumed")
        expiry = row.get("expires_at")
        if expiry is not None and expiry <= now_utc():
            raise ValueError("confirmation has expired")
        for key, value in (("project_id", project_id), ("resource_ref", resource_ref), ("action", action), ("revision", revision), ("config_hash", config_hash)):
            if key in row and row[key] != value: raise ValueError("confirmation binding mismatch")
        if row.get("actor_id") is not None and row["actor_id"] != actor_id: raise ValueError("confirmation actor mismatch")
        row["status"], row["result_ref"] = "CONSUMED", result_ref
        return row


class InMemoryAuditPort:
    def __init__(self): self.events: list[Any] = []
    def record(self, event): self.events.append(event); return event


class InMemoryJobPort:
    def __init__(self): self.jobs: list[dict[str, str]] = []
    def submit(self, *, kind: str, resource_ref: dict[str, str], project_id: str | None = None, owner_id: str | None = None):
        from .schemas import new_id
        value = {"job_id": new_id("job"), "kind": kind, "attempt": 1, "dispatch_id": new_id("dispatch"), "project_id": project_id or "test-project", "owner_id": owner_id or "test-owner", **resource_ref}; self.jobs.append(value); return value


__all__ += ["InMemoryConfirmationPort", "InMemoryAuditPort", "InMemoryJobPort"]


# The model-center schema is module-owned.  It deliberately does not import
# the platform Base: a trimmed build can create these tables independently.
_NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class ModelCenterBase(DeclarativeBase):
    metadata = MetaData(naming_convention=_NAMING)


MODEL_CENTER_METADATA = ModelCenterBase.metadata
model_center_metadata = MODEL_CENTER_METADATA


class _UTCDateTime(TypeDecorator[datetime]):
    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        return dialect.type_descriptor(DateTime(timezone=False))

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        return value.replace(tzinfo=timezone.utc) if value is not None else None


_TABLE = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"}


def _json(value: Any) -> Any:
    """Round-trip JSON once so NaN, datetime and custom objects cannot leak in."""
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False))


def _same_json(left: Any, right: Any) -> bool:
    return _json(left) == _json(right)


class ModelRegistrationRow(ModelCenterBase):
    __tablename__ = "registration"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(96), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    etag: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    task_type: Mapped[str] = mapped_column(String(96), nullable=False)
    manifest_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    checks: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    job_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    dispatch_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    model_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    error_report_ref: Mapped[dict[str, str] | None] = mapped_column(JSON, nullable=True)
    validation_report: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(_UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(_UTCDateTime(), nullable=False)
    __table_args__ = (
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint("length(config_hash) = 64", name="config_hash_length"),
        CheckConstraint("status IN ('DRAFT','VALIDATING','VALID','INVALID','REGISTERED','CANCELED')", name="status"),
        Index("ix_registration_project_status", "project_id", "status"), _TABLE,
    )


class ModelRow(ModelCenterBase):
    __tablename__ = "model"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(96), nullable=False)
    registration_id: Mapped[str | None] = mapped_column(String(96), ForeignKey("registration.id", ondelete="RESTRICT", name="fk_model_registration_id_registration"), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[str] = mapped_column(String(96), nullable=False)
    task_type: Mapped[str] = mapped_column(String(96), nullable=False)
    artifact_id: Mapped[str] = mapped_column(String(96), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    lifecycle_status: Mapped[str] = mapped_column(String(16), nullable=False)
    deployment_status: Mapped[str] = mapped_column(String(16), nullable=False)
    input_schema_version: Mapped[str] = mapped_column(String(96), nullable=False)
    output_schema_version: Mapped[str] = mapped_column(String(96), nullable=False)
    feature_version: Mapped[str | None] = mapped_column(String(96), nullable=True)
    source: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    applicability: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    model_card: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    deployments: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    comparison_roles: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    etag: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(_UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(_UTCDateTime(), nullable=False)
    __table_args__ = (
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint("length(sha256) = 64", name="sha256_length"),
        CheckConstraint("lifecycle_status IN ('VALIDATING','CANDIDATE','APPROVED','REJECTED','RETIRED')", name="lifecycle_status"),
        CheckConstraint("deployment_status IN ('NOT_DEPLOYED','STAGING','PRODUCTION')", name="deployment_status"),
        UniqueConstraint("project_id", "name", "version", name="uq_model_project_name_version"),
        Index("ix_model_project_task", "project_id", "task_type"), _TABLE,
    )


class EvaluationProtocolRow(ModelCenterBase):
    __tablename__ = "evaluation_protocol"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_type: Mapped[str] = mapped_column(String(96), nullable=False)
    data_version_id: Mapped[str] = mapped_column(String(96), nullable=False)
    split_manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    __table_args__ = (
        CheckConstraint("status IN ('DRAFT','APPROVED','RETIRED')", name="status"),
        CheckConstraint("length(split_manifest_sha256) = 64", name="split_manifest_sha256_length"), _TABLE,
    )


class EvaluationRow(ModelCenterBase):
    __tablename__ = "evaluation"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(96), nullable=False)
    model_id: Mapped[str] = mapped_column(String(96), ForeignKey("model.id", ondelete="RESTRICT", name="fk_evaluation_model_id_model"), nullable=False)
    protocol_id: Mapped[str] = mapped_column(String(96), nullable=False)
    protocol_version: Mapped[str] = mapped_column(String(64), nullable=False)
    job_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    dispatch_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    model_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    protocol_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    data_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    pass_status: Mapped[str] = mapped_column(String(16), nullable=False)
    report_artifact_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    error: Mapped[dict[str, str] | None] = mapped_column(JSON, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    etag: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(_UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(_UTCDateTime(), nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(("protocol_id", "protocol_version"), ("evaluation_protocol.id", "evaluation_protocol.version"), name="fk_evaluation_protocol_id_evaluation_protocol", ondelete="RESTRICT"),
        CheckConstraint("status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELED')", name="status"),
        CheckConstraint("pass_status IN ('PENDING','PASSED','FAILED')", name="pass_status"),
        Index("ix_evaluation_project_model", "project_id", "model_id"), _TABLE,
    )


class ApprovalRequestRow(ModelCenterBase):
    __tablename__ = "approval_request"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(96), nullable=False)
    model_id: Mapped[str] = mapped_column(String(96), ForeignKey("model.id", ondelete="RESTRICT", name="fk_approval_request_model_id_model"), nullable=False)
    evaluation_id: Mapped[str] = mapped_column(String(96), ForeignKey("evaluation.id", ondelete="RESTRICT", name="fk_approval_request_evaluation_id_evaluation"), nullable=False)
    evaluation_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    scope_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    requested_by: Mapped[str] = mapped_column(String(96), nullable=False)
    decided_by: Mapped[str | None] = mapped_column(String(96), nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    etag: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(_UTCDateTime(), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(_UTCDateTime(), nullable=True)
    __table_args__ = (
        CheckConstraint("status IN ('PENDING','APPROVED','REJECTED')", name="status"),
        CheckConstraint("revision >= 1", name="revision_positive"),
        Index("ix_approval_project_status", "project_id", "status"), _TABLE,
    )


class DeploymentDraftRow(ModelCenterBase):
    __tablename__ = "deployment_draft"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(96), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(96), nullable=False)
    target_model_id: Mapped[str] = mapped_column(String(96), ForeignKey("model.id", ondelete="RESTRICT", name="fk_deployment_draft_target_model_id_model"), nullable=False)
    stage: Mapped[str] = mapped_column(String(16), nullable=False)
    expected_pointer_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    rollback_model_id: Mapped[str | None] = mapped_column(String(96), ForeignKey("model.id", ondelete="RESTRICT", name="fk_deployment_draft_rollback_model_id_model"), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    etag: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    validation_report: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    source_deployment_id: Mapped[str | None] = mapped_column(String(96), ForeignKey("deployment.id", ondelete="RESTRICT", name="fk_deployment_draft_source_deployment_id_deployment"), nullable=True)
    operation: Mapped[str] = mapped_column(String(16), nullable=False)
    confirmation_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(_UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(_UTCDateTime(), nullable=False)
    __table_args__ = (
        CheckConstraint("stage IN ('STAGING','PRODUCTION')", name="stage"),
        CheckConstraint("status IN ('DRAFT','READY','INVALID','SUBMITTED')", name="status"),
        CheckConstraint("operation IN ('PUBLISH','ROLLBACK')", name="operation"),
        CheckConstraint("expected_pointer_revision >= 0", name="expected_pointer_revision_nonnegative"),
        Index("ix_deployment_draft_project_scope", "project_id", "scope_id"), _TABLE,
    )


class DeploymentRow(ModelCenterBase):
    __tablename__ = "deployment"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(96), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(96), nullable=False)
    stage: Mapped[str] = mapped_column(String(16), nullable=False)
    target_model_id: Mapped[str] = mapped_column(String(96), ForeignKey("model.id", ondelete="RESTRICT", name="fk_deployment_target_model_id_model"), nullable=False)
    operation: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    expected_pointer_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    pointer_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[dict[str, str] | None] = mapped_column(JSON, nullable=True)
    draft_id: Mapped[str] = mapped_column(String(96), ForeignKey("deployment_draft.id", ondelete="RESTRICT", name="fk_deployment_draft_id_deployment_draft"), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    dispatch_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_UTCDateTime(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(_UTCDateTime(), nullable=True)
    __table_args__ = (
        CheckConstraint("stage IN ('STAGING','PRODUCTION')", name="stage"),
        CheckConstraint("operation IN ('PUBLISH','ROLLBACK')", name="operation"),
        CheckConstraint("status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELED')", name="status"),
        CheckConstraint("attempt >= 1", name="attempt_positive"),
        Index("ix_deployment_project_scope_created", "project_id", "scope_id", "created_at"), _TABLE,
    )


class ProductionPointerRow(ModelCenterBase):
    __tablename__ = "production_pointer"
    project_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    scope_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    stage: Mapped[str] = mapped_column(String(16), primary_key=True)
    # A pointer may be seeded before the corresponding asset is registered;
    # governance validation resolves the identifier before publication.
    model_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(_UTCDateTime(), nullable=False)
    __table_args__ = (
        CheckConstraint("stage IN ('STAGING','PRODUCTION')", name="stage"),
        CheckConstraint("revision >= 0", name="revision_nonnegative"), _TABLE,
    )


class DeploymentLedgerRow(ModelCenterBase):
    __tablename__ = "deployment_ledger"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    deployment_id: Mapped[str] = mapped_column(String(96), ForeignKey("deployment.id", ondelete="RESTRICT", name="fk_deployment_ledger_deployment_id_deployment"), nullable=False)
    project_id: Mapped[str] = mapped_column(String(96), nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(_UTCDateTime(), nullable=False)
    __table_args__ = (Index("ix_deployment_ledger_project", "project_id", "recorded_at"), _TABLE)


class ModelCenterIdempotencyRow(ModelCenterBase):
    __tablename__ = "idempotency"
    scope: Mapped[str] = mapped_column(String(512), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_type: Mapped[str | None] = mapped_column(String(96), nullable=True)
    result: Mapped[Any] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(_UTCDateTime(), nullable=False)
    __table_args__ = (Index("ix_model_center_idempotency_created", "created_at"), _TABLE)


class ModelCenterAuditRow(ModelCenterBase):
    # M1 owns the generic audit_event table.  Model-center records use a
    # module-owned name so composing registries never assigns one table twice.
    __tablename__ = "model_center_audit_event"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(96), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(96), nullable=False)
    interaction: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(160), nullable=False)
    resource_ref: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    before_summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    after_summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    confirmation_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    config_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(_UTCDateTime(), nullable=False)
    __table_args__ = (Index("ix_model_center_audit_project_timestamp", "project_id", "timestamp"), _TABLE)


class ComparisonRow(ModelCenterBase):
    __tablename__ = "model_comparison"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(96), nullable=False)
    model_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    protocol_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    data_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    scope_id: Mapped[str] = mapped_column(String(96), nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(_UTCDateTime(), nullable=False)
    __table_args__ = (_TABLE,)


_DTO_TYPES = {
    "ModelRegistration": ModelRegistration, "Model": Model,
    "ModelEvaluation": ModelEvaluation, "ModelComparison": ModelComparison,
    "ApprovalRequest": ApprovalRequest, "DeploymentDraft": DeploymentDraft,
    "Deployment": Deployment, "DeploymentAccepted": DeploymentAccepted,
    "EvaluationAccepted": EvaluationAccepted, "RegistrationAccepted": RegistrationAccepted,
    "ProductionPointer": ProductionPointer, "AuditEvent": AuditEvent,
}


class SQLAlchemyModelCenterRepository:
    """SQLAlchemy adapter bound to a caller-owned Session.

    It never commits or closes the session.  ``transaction`` is a savepoint,
    which lets the deployment worker atomically update pointer, models and
    deployment while leaving the outer request transaction in charge.
    """

    def __init__(self, session: Session, *, id_factory=new_id, fresh_session_factory=None):
        if callable(session) or not all(hasattr(session, name) for name in ("add", "flush", "scalar")):
            raise TypeError("SQLAlchemyModelCenterRepository requires a caller-owned Session")
        self.session = session
        self._id_factory = id_factory
        self._fresh_session_factory = fresh_session_factory
        self.shared_job_lifecycle = True

    @staticmethod
    def _model_package_binding(session, record, execute_dispatch_id: str, *, lock: bool = False):
        job_id = getattr(record, "job_id", None)
        project_id = getattr(record, "project_id", None)
        attempt_no = int(getattr(record, "attempt", 0) or 0)
        if not job_id or not project_id or not execute_dispatch_id or attempt_no < 1:
            raise ModelCenterConflictError("MODEL_PACKAGE_JOB_BINDING_INVALID")
        job_query = select(Job).where(Job.id == job_id, Job.project_id == project_id).execution_options(populate_existing=True)
        if lock:
            job_query = job_query.with_for_update()
        job = session.scalar(job_query)
        dispatch = session.get(OutboxEvent, execute_dispatch_id)
        attempt = session.scalar(select(JobAttempt).where(JobAttempt.job_id == job_id, JobAttempt.attempt == attempt_no))
        if job is None or dispatch is None or attempt is None:
            raise ModelCenterConflictError("MODEL_PACKAGE_JOB_BINDING_MISSING")
        ref = job.resource_ref or {}
        expected = {"job_id": job.id, "attempt": attempt_no, "dispatch_id": execute_dispatch_id}
        if (
            job.kind != "model_registration_validation"
            or int(job.attempt) != attempt_no
            or ref.get("type") not in {"registration", "model_registration"}
            or ref.get("id") != getattr(record, "id", None)
            or dispatch.aggregate_type != "job"
            or dispatch.aggregate_id != job.id
            or dispatch.event_type != "job.execute"
            or dict(dispatch.payload or {}) != expected
            or getattr(record, "dispatch_id", None) != execute_dispatch_id
        ):
            raise ModelCenterConflictError("MODEL_PACKAGE_JOB_BINDING_STALE")
        return job, dispatch, attempt

    def model_package_cancel_state(self, record, *, execute_dispatch_id: str) -> str:
        """Read cancellation from a fresh session and fail closed on stale bindings."""
        factory = self._fresh_session_factory
        if not callable(factory):
            raise ModelCenterConflictError("MODEL_PACKAGE_CANCEL_READER_UNAVAILABLE")
        session = factory()
        try:
            job, _dispatch, attempt = self._model_package_binding(session, record, execute_dispatch_id)
            if job.status == "CANCELED" and attempt.status == "QUEUED" and _dispatch.status in {"PENDING", "PROCESSING", "SENT", "FAILED"}:
                return "NEVER_STARTED"
            if job.status == "QUEUED" and attempt.status == "QUEUED" and _dispatch.status in {"PROCESSING", "SENT"}:
                return "READY"
            if job.status == "RUNNING":
                if attempt.status != "RUNNING":
                    raise ModelCenterConflictError("MODEL_PACKAGE_ATTEMPT_NOT_ACTIVE")
                return "RUNNING"
            if job.status != "CANCEL_REQUESTED" or attempt.status not in {"RUNNING", "CANCEL_REQUESTED"}:
                raise ModelCenterConflictError("MODEL_PACKAGE_JOB_NOT_ACTIVE")
            cancel_event = session.scalar(
                select(JobEvent).where(
                    JobEvent.job_id == job.id,
                    JobEvent.type == "job.cancel_requested",
                ).order_by(JobEvent.seq.desc()).limit(1)
            )
            data = cancel_event.data if cancel_event is not None else {}
            if not isinstance(data, dict) or int(data.get("attempt", -1)) != int(job.attempt):
                raise ModelCenterConflictError("MODEL_PACKAGE_CANCEL_EVENT_BINDING_INVALID")
            return "CANCEL_REQUESTED"
        finally:
            session.close()

    def record_model_package_termination(
        self, record, *, execute_dispatch_id: str, request_ref: str,
        request_sha256: str, termination: dict[str, Any], lease_guard=None,
    ) -> None:
        """Persist a bounded stop receipt in the existing job event stream."""
        job, _dispatch, attempt = self._model_package_binding(self.session, record, execute_dispatch_id, lock=True)
        if int(job.attempt) != int(getattr(record, "attempt", 0)) or attempt.status not in {"RUNNING", "CANCEL_REQUESTED"}:
            if not (job.status == "CANCELED" and attempt.status == "QUEUED"):
                raise ModelCenterConflictError("MODEL_PACKAGE_TERMINATION_BINDING_STALE")
        self._assert_lease(lease_guard)
        allowed = {
            "started", "never_started", "confirmed", "process_id", "creation_filetime",
            "exit_filetime", "exit_code", "job_active_processes", "cleanup_completed", "stop_cause",
            "output_drained",
        }
        if set(termination) != allowed or not isinstance(request_ref, str) or not request_ref or len(request_ref) > 96:
            raise ModelCenterConflictError("MODEL_PACKAGE_TERMINATION_RECEIPT_INVALID")
        if not isinstance(request_sha256, str) or len(request_sha256) != 64 or any(ch not in "0123456789abcdef" for ch in request_sha256):
            raise ModelCenterConflictError("MODEL_PACKAGE_TERMINATION_RECEIPT_INVALID")
        confirmed = termination.get("confirmed") is True and termination.get("cleanup_completed") is True
        never_started = termination.get("started") is False and termination.get("never_started") is True
        if confirmed and not never_started:
            if (type(termination.get("process_id")) is not int or termination.get("process_id") <= 0
                    or type(termination.get("creation_filetime")) is not int or termination.get("creation_filetime") <= 0
                    or type(termination.get("exit_filetime")) is not int or termination.get("exit_filetime") < termination.get("creation_filetime")
                    or type(termination.get("exit_code")) is not int or termination.get("exit_code") == 259
                    or termination.get("job_active_processes") != 0 or termination.get("output_drained") is not True):
                raise ModelCenterConflictError("MODEL_PACKAGE_TERMINATION_RECEIPT_INVALID")
        existing = self.session.scalar(
            select(JobEvent).where(
                JobEvent.job_id == job.id, JobEvent.type == "model_package.termination"
            ).order_by(JobEvent.seq.desc()).limit(1)
        )
        if existing is not None and isinstance(existing.data, dict):
            previous = existing.data
            if previous.get("attempt") == int(job.attempt) and previous.get("execute_dispatch_id") == execute_dispatch_id:
                if previous.get("request_ref") == request_ref and previous.get("request_sha256") == request_sha256 and previous.get("termination") == termination:
                    return
                raise ModelCenterConflictError("MODEL_PACKAGE_TERMINATION_RECEIPT_CONFLICT")
        seq = self.session.scalar(select(func.max(JobEvent.seq)).where(JobEvent.job_id == job.id))
        receipt = {
            "attempt": int(job.attempt), "execute_dispatch_id": execute_dispatch_id,
            "request_ref": request_ref, "request_sha256": request_sha256,
            "termination": {key: termination[key] for key in sorted(allowed)},
        }
        self.session.add(JobEvent(
            id=self._id_factory("event"), job_id=job.id, stream_id=job.id,
            seq=int(seq or 0) + 1, type="model_package.termination",
            schema_version="1.0", data=receipt, timestamp=now_utc(),
        ))
        self.session.flush()

    def model_package_termination_receipt(self, record, *, execute_dispatch_id: str) -> dict[str, Any] | None:
        job, _dispatch, _attempt = self._model_package_binding(self.session, record, execute_dispatch_id)
        row = self.session.scalar(
            select(JobEvent).where(
                JobEvent.job_id == job.id, JobEvent.type == "model_package.termination"
            ).order_by(JobEvent.seq.desc()).limit(1)
        )
        data = row.data if row is not None else None
        if not isinstance(data, dict):
            return None
        if data.get("attempt") != int(job.attempt) or data.get("execute_dispatch_id") != execute_dispatch_id:
            return None
        termination = data.get("termination")
        if not isinstance(termination, dict):
            return None
        return dict(data)

    def _assert_lease(self, lease_guard: object | None) -> None:
        """Lock the outbox row so final projection and lease CAS share a transaction."""
        if lease_guard is None:
            return
        claim = getattr(lease_guard, "claim", None)
        dispatch_id = getattr(claim, "dispatch_id", None)
        dispatch = self.session.scalar(
            select(OutboxEvent)
            .where(OutboxEvent.dispatch_id == dispatch_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ) if dispatch_id else None
        if (
            dispatch is None
            or dispatch.status != "PROCESSING"
            or dispatch.lease_owner != getattr(claim, "lease_owner", None)
            or int(dispatch.lease_token or 0) != int(getattr(claim, "lease_token", 0))
            or dispatch.lease_expires_at is None
            or dispatch.lease_expires_at <= now_utc()
        ):
            raise ModelCenterConflictError("model-center lease lost")

    def sync_deployment_lifecycle(self, deployment, status, *, error=None, result_ref=None, lease_guard=None):
        """Fence and project the platform Job in this repository transaction."""
        dispatch_id = getattr(deployment, "dispatch_id", None)
        if not dispatch_id:
            return None
        job = self.session.scalar(select(Job).join(OutboxEvent, OutboxEvent.aggregate_id == Job.id).where(OutboxEvent.dispatch_id == dispatch_id, Job.project_id == deployment.project_id).with_for_update())
        if job is None:
            return None
        self._assert_lease(lease_guard)
        return sync_job_lifecycle(SQLAlchemyTaskRepository(lambda: self.session), self.session, job_id=job.id, status=status, attempt=int(deployment.attempt), expected_attempt=int(deployment.attempt), error=error, result_ref=result_ref)

    def resolve_model_job(self, message, *, event_type="job.execute"):
        """Resolve the three-field transport message through Job.resource_ref.

        The broker payload is deliberately not trusted with a business ID.
        Both the Job row and its execute outbox row must agree before a
        consumer can touch a module record.
        """
        job = self.session.scalar(select(Job).where(Job.id == message.job_id).with_for_update().execution_options(populate_existing=True))
        dispatch = self.session.scalar(select(OutboxEvent).where(OutboxEvent.dispatch_id == message.dispatch_id).with_for_update().execution_options(populate_existing=True))
        if job is None or dispatch is None:
            return None
        expected = {"job_id": message.job_id, "attempt": int(message.attempt), "dispatch_id": message.dispatch_id}
        if dispatch.aggregate_type != "job" or dispatch.aggregate_id != job.id or dispatch.event_type != event_type or dict(dispatch.payload or {}) != expected:
            return None
        if int(job.attempt) != int(message.attempt):
            return None
        ref = job.resource_ref or {}
        ref_type, resource_id = ref.get("type"), ref.get("id")
        if not isinstance(ref_type, str) or not isinstance(resource_id, str) or not resource_id:
            return None
        records = {
            "deployment": ("model_deployment", self.get_deployment),
            "model_deployment": ("model_deployment", self.get_deployment),
            "deployment_draft": ("model_deployment", self.get_deployment),
            "model_registration": ("model_registration_validation", self.get_registration),
            "registration": ("model_registration_validation", self.get_registration),
            "model_evaluation": ("model_evaluation", self.get_evaluation),
            "evaluation": ("model_evaluation", self.get_evaluation),
        }
        mapped = records.get(ref_type)
        if mapped is None or mapped[0] != job.kind:
            return None
        record = None if ref_type == "deployment_draft" else mapped[1](job.project_id, resource_id)
        if record is None:
            return None
        # Legacy deployment jobs pointed at the draft. Resolve only when one
        # unambiguous queued deployment belongs to that draft.
        if ref_type == "deployment_draft":
            matches = [item for item in self.list_deployments(job.project_id) if item.draft_id == resource_id and item.status in {"QUEUED", "RUNNING"}]
            if len(matches) != 1:
                return None
            record = matches[0]
        if getattr(record, "job_id", None) not in (None, job.id):
            return None
        if event_type == "job.cancel" and int(getattr(record, "attempt", 1)) != int(job.attempt):
            return None
        if event_type == "job.execute" and getattr(record, "dispatch_id", None) not in (None, message.dispatch_id):
            # JobService retry owns creation of the canonical attempt/outbox.
            # Bind that new dispatch here, while the Job row is locked, before
            # any model asset/evaluation/pointer side effect can run.
            old_attempt = int(getattr(record, "attempt", 1))
            old_status = getattr(record, "status", "")
            if int(job.attempt) <= old_attempt or old_status not in {"FAILED", "INVALID", "CANCELED"}:
                return None
            reset = {"attempt": int(job.attempt), "dispatch_id": message.dispatch_id}
            if ref_type in {"model_registration", "registration"}:
                reset.update(status="VALIDATING", error_report_ref=None)
                record = record.model_copy(update=reset); self.save_registration(record)
            elif ref_type in {"model_evaluation", "evaluation"}:
                reset.update(status="QUEUED", pass_status="PENDING", error=None)
                record = record.model_copy(update=reset); self.save_evaluation(record)
            else:
                reset.update(status="QUEUED", error=None, finished_at=None)
                record = record.model_copy(update=reset); self.save_deployment(record)
        return {"job": job, "record": record, "kind": job.kind, "resource_type": ref_type}

    def sync_model_lifecycle(self, record, status, *, error=None, result_ref=None, lease_guard=None):
        """Project a module terminal/start state and shared Job atomically."""
        dispatch_id = getattr(record, "dispatch_id", None)
        job_id = getattr(record, "job_id", None)
        if not dispatch_id or not job_id:
            return None
        job = self.session.scalar(select(Job).where(Job.id == job_id, Job.project_id == record.project_id).with_for_update().execution_options(populate_existing=True))
        if job is None:
            return None
        if job.attempt != int(getattr(record, "attempt", 1)):
            return None
        if job.kind == "model_registration_validation":
            if status == "RUNNING" and job.status not in {"QUEUED", "RUNNING"}:
                return None
            # Cancellation wins every registration terminal-write race.  The
            # cancel handler may write CANCELED only after it has validated a
            # bound termination receipt.
            if status in {"SUCCEEDED", "FAILED"} and job.status == "CANCEL_REQUESTED":
                return None
        # Refresh the current JobAttempt identity before the task lifecycle
        # projection performs its own locked lookup in this Session.
        self.session.scalar(
            select(JobAttempt).where(
                JobAttempt.job_id == job.id, JobAttempt.attempt == int(job.attempt)
            ).with_for_update().execution_options(populate_existing=True)
        )
        self._assert_lease(lease_guard)
        return sync_job_lifecycle(SQLAlchemyTaskRepository(lambda: self.session), self.session, job_id=job.id, status=status, attempt=int(record.attempt), expected_attempt=int(record.attempt), error=error, result_ref=result_ref)

    def validate_model_cancel_dispatch(self, record, dispatch_id: str) -> bool:
        """Validate a current-attempt job.cancel outbox row before mutation."""
        if not getattr(record, "job_id", None) or not dispatch_id:
            return False
        job = self.session.scalar(select(Job).where(Job.id == record.job_id, Job.project_id == record.project_id).with_for_update().execution_options(populate_existing=True))
        dispatch = self.session.scalar(select(OutboxEvent).where(OutboxEvent.dispatch_id == dispatch_id).with_for_update().execution_options(populate_existing=True))
        if job is None or dispatch is None or int(job.attempt) != int(getattr(record, "attempt", 1)):
            return False
        payload = {"job_id": job.id, "attempt": int(job.attempt), "dispatch_id": dispatch_id}
        return dispatch.aggregate_type == "job" and dispatch.aggregate_id == job.id and dispatch.event_type == "job.cancel" and dict(dispatch.payload or {}) == payload

    sync_registration_lifecycle = sync_model_lifecycle
    sync_evaluation_lifecycle = sync_model_lifecycle

    @contextmanager
    def transaction(self):
        with self.session.begin_nested():
            yield self

    @staticmethod
    def _conflict(message: str, **details: Any) -> ModelCenterConflictError:
        return ModelCenterConflictError(message, details=details)

    def _one(self, row_type: Any, project_id: str, resource_id: str):
        row = self.session.scalar(select(row_type).where(row_type.id == resource_id, row_type.project_id == project_id))
        return row

    def save_registration(self, value):
        row = self.session.get(ModelRegistrationRow, value.id)
        if row is not None and row.project_id != value.project_id:
            raise self._conflict("registration belongs to another project")
        if row is None:
            row = ModelRegistrationRow(id=value.id)
            self.session.add(row)
        elif (not _same_json(row.source, value.source) or
              (row.manifest_snapshot is not None and not _same_json(row.manifest_snapshot, value.manifest_snapshot))):
            raise self._conflict("registration manifest/source is immutable")
        data = _json(value.model_dump(mode="json"))
        for key in ("project_id", "revision", "config_hash", "etag", "status", "source", "name", "task_type", "manifest_snapshot", "checks", "job_id", "attempt", "dispatch_id", "model_id", "error_report_ref", "validation_report", "created_at", "updated_at"):
            setattr(row, key, data.get(key) if key not in {"created_at", "updated_at"} else getattr(value, key))
        self.session.flush()
        return value

    def get_registration(self, project_id, registration_id):
        row = self._one(ModelRegistrationRow, project_id, registration_id)
        if row is None: return None
        return ModelRegistration.model_validate({key: getattr(row, key) for key in ("id", "project_id", "revision", "config_hash", "etag", "status", "source", "name", "task_type", "manifest_snapshot", "checks", "job_id", "attempt", "dispatch_id", "model_id", "error_report_ref", "validation_report", "created_at", "updated_at")})

    def save_model(self, value):
        row = self.session.get(ModelRow, value.id)
        if row is not None and row.project_id != value.project_id:
            raise self._conflict("model belongs to another project")
        if row is None:
            row = ModelRow(id=value.id); self.session.add(row)
        elif any(not _same_json(getattr(row, field), getattr(value, field)) for field in ("artifact_id", "sha256", "input_schema_version", "output_schema_version", "source")):
            raise self._conflict("model artifact/manifest is immutable")
        data = _json(value.model_dump(mode="json")); source = data["source"]
        row.registration_id = source.get("registration_id") if isinstance(source, dict) else None
        for key in ("project_id", "name", "version", "task_type", "artifact_id", "sha256", "lifecycle_status", "deployment_status", "input_schema_version", "output_schema_version", "feature_version", "source", "applicability", "model_card", "deployments", "comparison_roles", "revision", "etag"):
            setattr(row, key, data[key])
        row.created_at, row.updated_at = value.created_at, value.updated_at
        self.session.flush(); return value

    def _model_dto(self, row):
        if row is None: return None
        data = {key: getattr(row, key) for key in ("id", "project_id", "name", "version", "task_type", "artifact_id", "sha256", "lifecycle_status", "deployment_status", "input_schema_version", "output_schema_version", "feature_version", "source", "applicability", "model_card", "deployments", "comparison_roles", "revision", "etag")}
        data.update(created_at=row.created_at, updated_at=row.updated_at)
        return Model.model_validate(data)

    def get_model(self, project_id, model_id): return self._model_dto(self._one(ModelRow, project_id, model_id))

    def list_models(self, project_id, *, task_type=None, lifecycle=None):
        stmt = select(ModelRow).where(ModelRow.project_id == project_id)
        if task_type is not None: stmt = stmt.where(ModelRow.task_type == task_type)
        if lifecycle is not None: stmt = stmt.where(ModelRow.lifecycle_status == lifecycle)
        return [self._model_dto(row) for row in self.session.scalars(stmt.order_by(desc(ModelRow.created_at), desc(ModelRow.id)))]

    def save_evaluation_protocol(self, value):
        row = self.session.get(EvaluationProtocolRow, (value.id, value.version))
        if row is None:
            row = EvaluationProtocolRow(id=value.id, version=value.version); self.session.add(row)
        elif not _same_json(row.payload, value):
            raise self._conflict("evaluation protocol is immutable")
        data = _json(value.model_dump(mode="json")); row.task_type, row.data_version_id = data["task_type"], data["data_version_id"]
        row.split_manifest_sha256, row.payload, row.status = data["split_manifest_sha256"], data, data["status"]
        self.session.flush(); return value

    def get_evaluation_protocol(self, protocol_id, version):
        row = self.session.get(EvaluationProtocolRow, (protocol_id, version))
        return EvaluationProtocol.model_validate(row.payload) if row else None

    def list_evaluation_protocols(self, task_type=None):
        stmt = select(EvaluationProtocolRow)
        if task_type is not None: stmt = stmt.where(EvaluationProtocolRow.task_type == task_type)
        return [EvaluationProtocol.model_validate(row.payload) for row in self.session.scalars(stmt.order_by(EvaluationProtocolRow.id, EvaluationProtocolRow.version))]

    # Also implements EvaluationProtocolPort so one explicit repository can
    # be injected into a service in a persistence-backed vertical.
    get = get_evaluation_protocol
    list = list_evaluation_protocols
    save_protocol = save_evaluation_protocol

    def save_evaluation(self, value):
        row = self.session.get(EvaluationRow, value.id)
        data = _json(value.model_dump(mode="json"))
        if row is None:
            row = EvaluationRow(id=value.id); self.session.add(row)
        elif row.project_id != value.project_id:
            raise self._conflict("evaluation belongs to another project")
        elif any(not _same_json(getattr(row, field), data[field]) for field in ("model_snapshot", "protocol_snapshot", "data_snapshot")):
            raise self._conflict("evaluation snapshots are immutable")
        for key in ("project_id", "job_id", "attempt", "dispatch_id", "status", "model_snapshot", "protocol_snapshot", "data_snapshot", "metrics", "pass_status", "report_artifact_id", "error", "revision", "etag"):
            setattr(row, key, data[key])
        row.model_id, row.protocol_id, row.protocol_version = data["model_snapshot"]["id"], data["protocol_snapshot"]["id"], data["protocol_snapshot"]["version"]
        row.created_at, row.updated_at = value.created_at, value.updated_at
        self.session.flush(); return value

    def _evaluation_dto(self, row):
        if row is None: return None
        data = {key: getattr(row, key) for key in ("id", "job_id", "attempt", "dispatch_id", "status", "model_snapshot", "protocol_snapshot", "data_snapshot", "metrics", "pass_status", "report_artifact_id", "error", "revision", "etag")}
        data.update(project_id=row.project_id, created_at=row.created_at, updated_at=row.updated_at)
        return ModelEvaluation.model_validate(data)

    def get_evaluation(self, project_id, evaluation_id): return self._evaluation_dto(self._one(EvaluationRow, project_id, evaluation_id))

    def list_evaluations(self, project_id, model_id=None):
        stmt = select(EvaluationRow).where(EvaluationRow.project_id == project_id)
        if model_id is not None: stmt = stmt.where(EvaluationRow.model_id == model_id)
        return [self._evaluation_dto(row) for row in self.session.scalars(stmt.order_by(desc(EvaluationRow.created_at), desc(EvaluationRow.id)))]

    def save_comparison(self, value):
        row = self.session.get(ComparisonRow, value.id)
        if row is not None and row.project_id != value.project_id: raise self._conflict("comparison belongs to another project")
        if row is None: row = ComparisonRow(id=value.id); self.session.add(row)
        data = _json(value.model_dump(mode="json"))
        for key in ("project_id", "model_ids", "protocol_snapshot", "data_snapshot", "scope_id", "metrics"): setattr(row, key, data[key])
        row.created_at = value.created_at; self.session.flush(); return value

    def get_comparison(self, project_id, comparison_id):
        row = self.session.scalar(select(ComparisonRow).where(ComparisonRow.id == comparison_id, ComparisonRow.project_id == project_id))
        if row is None: return None
        return ModelComparison.model_validate({"id": row.id, "project_id": row.project_id, "model_ids": row.model_ids, "protocol_snapshot": row.protocol_snapshot, "data_snapshot": row.data_snapshot, "scope_id": row.scope_id, "metrics": row.metrics, "created_at": row.created_at})

    def save_approval(self, value):
        row = self.session.get(ApprovalRequestRow, value.id); data = _json(value.model_dump(mode="json"))
        if row is None: row = ApprovalRequestRow(id=value.id); self.session.add(row)
        elif row.project_id != value.project_id: raise self._conflict("approval belongs to another project")
        elif not _same_json(row.evaluation_snapshot, data["evaluation_snapshot"]): raise self._conflict("approval evaluation snapshot is immutable")
        for key in ("project_id", "model_id", "evaluation_snapshot", "scope_ids", "status", "requested_by", "decided_by", "reason", "revision", "etag"): setattr(row, key, data[key])
        row.evaluation_id = data["evaluation_snapshot"]["id"]; row.created_at, row.decided_at = value.created_at, value.decided_at
        self.session.flush(); return value

    def get_approval(self, project_id, approval_id):
        row = self._one(ApprovalRequestRow, project_id, approval_id)
        if row is None: return None
        return ApprovalRequest.model_validate({key: getattr(row, key) for key in ("id", "project_id", "model_id", "evaluation_snapshot", "scope_ids", "status", "requested_by", "decided_by", "reason", "revision", "etag", "created_at", "decided_at")})

    def save_draft(self, value):
        row = self.session.get(DeploymentDraftRow, value.id); data = _json(value.model_dump(mode="json"))
        if row is None: row = DeploymentDraftRow(id=value.id); self.session.add(row)
        elif row.project_id != value.project_id: raise self._conflict("deployment draft belongs to another project")
        elif row.revision > value.revision: raise self._conflict("deployment draft revision is stale", expected_revision=value.revision, actual_revision=row.revision)
        for key in ("project_id", "scope_id", "target_model_id", "stage", "expected_pointer_revision", "rollback_model_id", "revision", "config_hash", "etag", "status", "validation_report", "source_deployment_id", "operation", "confirmation_id", "reason"): setattr(row, key, data[key])
        row.created_at, row.updated_at = value.created_at, value.updated_at; self.session.flush(); return value

    def get_draft(self, project_id, draft_id):
        row = self.session.scalar(select(DeploymentDraftRow).where(DeploymentDraftRow.id == draft_id, DeploymentDraftRow.project_id == project_id))
        if row is None: return None
        return DeploymentDraft.model_validate({key: getattr(row, key) for key in ("id", "project_id", "scope_id", "target_model_id", "stage", "expected_pointer_revision", "rollback_model_id", "revision", "config_hash", "etag", "status", "validation_report", "source_deployment_id", "operation", "confirmation_id", "reason", "created_at", "updated_at")})

    def save_deployment(self, value):
        row = self.session.get(DeploymentRow, value.id); data = _json(value.model_dump(mode="json"))
        if row is None: row = DeploymentRow(id=value.id); self.session.add(row)
        elif row.project_id != value.project_id: raise self._conflict("deployment belongs to another project")
        elif any(getattr(row, key) != data[key] for key in ("scope_id", "stage", "target_model_id", "operation", "expected_pointer_revision", "draft_id", "attempt")):
            raise self._conflict("deployment identity is immutable")
        for key in ("project_id", "scope_id", "stage", "target_model_id", "operation", "status", "expected_pointer_revision", "pointer_revision", "error", "draft_id", "attempt", "dispatch_id"): setattr(row, key, data[key])
        row.created_at, row.finished_at = value.created_at, value.finished_at
        self.session.flush()
        self.session.add(DeploymentLedgerRow(id=self._id_factory("ledger"), deployment_id=value.id, project_id=value.project_id, snapshot=data, recorded_at=now_utc()))
        self.session.flush(); return value

    def _deployment_dto(self, row):
        if row is None: return None
        return Deployment.model_validate({key: getattr(row, key) for key in ("id", "project_id", "scope_id", "stage", "target_model_id", "operation", "status", "expected_pointer_revision", "pointer_revision", "error", "draft_id", "attempt", "dispatch_id", "created_at", "finished_at")})

    def get_deployment(self, project_id, deployment_id):
        row = self.session.scalar(select(DeploymentRow).where(DeploymentRow.id == deployment_id, DeploymentRow.project_id == project_id)); return self._deployment_dto(row)

    def list_deployments(self, project_id, scope_id=None):
        stmt = select(DeploymentRow).where(DeploymentRow.project_id == project_id)
        if scope_id is not None: stmt = stmt.where(DeploymentRow.scope_id == scope_id)
        return [self._deployment_dto(row) for row in self.session.scalars(stmt.order_by(desc(DeploymentRow.created_at), desc(DeploymentRow.id)))]

    def get_pointer(self, project_id, scope_id, stage):
        row = self.session.scalar(select(ProductionPointerRow).where(ProductionPointerRow.project_id == project_id, ProductionPointerRow.scope_id == scope_id, ProductionPointerRow.stage == stage))
        if row is None: return ProductionPointer(project_id=project_id, scope_id=scope_id, stage=stage, model_id=None, revision=0, updated_at=now_utc())
        return ProductionPointer.model_validate({"project_id": row.project_id, "scope_id": row.scope_id, "stage": row.stage, "model_id": row.model_id, "revision": row.revision, "updated_at": row.updated_at})

    def cas_pointer(self, pointer=None, *, expected_revision, model_id, project_id=None, scope_id=None, stage=None):
        if pointer is not None: project_id, scope_id, stage = pointer.project_id, pointer.scope_id, pointer.stage
        if project_id is None or scope_id is None or stage is None: raise ValueError("pointer identity is required")
        now = now_utc()
        result = self.session.execute(update(ProductionPointerRow).where(ProductionPointerRow.project_id == project_id, ProductionPointerRow.scope_id == scope_id, ProductionPointerRow.stage == stage, ProductionPointerRow.revision == expected_revision).values(model_id=model_id, revision=expected_revision + 1, updated_at=now))
        if result.rowcount != 1:
            if expected_revision != 0 or self.session.scalar(select(ProductionPointerRow).where(ProductionPointerRow.project_id == project_id, ProductionPointerRow.scope_id == scope_id, ProductionPointerRow.stage == stage)) is not None:
                current = self.get_pointer(project_id, scope_id, stage)
                raise self._conflict("production pointer revision is stale", expected_revision=expected_revision, actual_revision=current.revision)
            try:
                self.session.add(ProductionPointerRow(project_id=project_id, scope_id=scope_id, stage=stage, model_id=model_id, revision=1, updated_at=now)); self.session.flush()
            except IntegrityError as exc:
                raise self._conflict("production pointer revision is stale", expected_revision=expected_revision) from exc
        self.session.flush()
        return self.get_pointer(project_id, scope_id, stage)

    def record_audit(self, event):
        row = self.session.get(ModelCenterAuditRow, event.id)
        if row is not None:
            if row.project_id != event.project_id or not _same_json(row.after_summary, event.after_summary): raise self._conflict("audit event is immutable")
            return event
        data = _json(event.model_dump(mode="json")); self.session.add(ModelCenterAuditRow(id=event.id, project_id=event.project_id, actor_id=event.actor_id, interaction=event.interaction, action=event.action, resource_ref=data["resource_ref"], before_summary=data["before_summary"], after_summary=data["after_summary"], confirmation_id=data["confirmation_id"], config_hash=data["config_hash"], result=data["result"], timestamp=event.timestamp)); self.session.flush(); return event

    def idempotent_get(self, scope, request_hash):
        row = self.session.get(ModelCenterIdempotencyRow, scope)
        if row is None: return None
        if row.request_hash != request_hash: raise self._conflict("idempotency key was reused with a different request")
        if row.result_type is None: return None
        cls = _DTO_TYPES.get(row.result_type)
        return cls.model_validate(row.result) if cls else _json(row.result)

    def idempotent_put(self, scope, request_hash, value):
        row = self.session.get(ModelCenterIdempotencyRow, scope)
        if row is not None:
            if row.request_hash != request_hash: raise self._conflict("idempotency key was reused with a different request")
            return
        row = ModelCenterIdempotencyRow(scope=scope, request_hash=request_hash, result_type=type(value).__name__ if value is not None else None, result=_json(value) if value is not None else None, created_at=now_utc())
        self.session.add(row); self.session.flush()


class SQLAlchemyModelCenterJobPort:
    """Create canonical M1 jobs for model-center asynchronous operations."""

    def __init__(self, session: Session, *, id_factory=new_id):
        if callable(session) or not all(hasattr(session, name) for name in ("add", "flush")):
            raise TypeError("SQLAlchemyModelCenterJobPort requires a caller-owned Session")
        self.session = session
        self._id_factory = id_factory

    def submit(self, *, kind: str, resource_ref: dict[str, str], project_id: str | None = None, owner_id: str | None = None):
        project = project_id
        if not project or not owner_id:
            raise ValueError("model-center job submission requires server project_id and owner_id")
        task_repository = SQLAlchemyTaskRepository(lambda: self.session)
        job = add_job_rows(
            task_repository,
            self.session,
            project_id=project,
            owner_id=owner_id,
            kind=kind,
            resource_ref=ResourceRef.model_validate(resource_ref),
            cancelable=True,
            retryable=True,
            resume_mode="restart",
            deadline_at=None,
        )
        self.session.flush()
        from chrom_platform.infrastructure.models import OutboxEvent
        dispatch = self.session.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_type == "job", OutboxEvent.aggregate_id == job.id, OutboxEvent.event_type == "job.execute"))
        if dispatch is None or set(dispatch.payload or {}) != {"job_id", "attempt", "dispatch_id"}:
            raise RuntimeError("platform job execute outbox was not created")
        return {"job_id": job.id, "attempt": int(job.attempt), "dispatch_id": dispatch.dispatch_id}


__all__ += [
    "ModelCenterBase", "MODEL_CENTER_METADATA", "model_center_metadata", "ModelRegistrationRow", "ModelRow", "EvaluationProtocolRow", "EvaluationRow",
    "ApprovalRequestRow", "DeploymentDraftRow", "DeploymentRow", "ProductionPointerRow",
    "DeploymentLedgerRow", "ModelCenterIdempotencyRow", "ModelCenterAuditRow", "ComparisonRow",
    "SQLAlchemyModelCenterRepository", "SQLAlchemyModelCenterJobPort",
]

SQLAlchemyModelCenterPersistence = SQLAlchemyModelCenterRepository
__all__.append("SQLAlchemyModelCenterPersistence")
