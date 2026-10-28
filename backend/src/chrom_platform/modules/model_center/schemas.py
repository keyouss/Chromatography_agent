"""Strict public and durable DTOs for the model-center vertical."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal, Optional, Union
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator
from chrom_platform.core.dto import ResourceRef


class ModelCenterModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=False)
    _idempotency_replayed: bool = PrivateAttr(default=False)


Id = Annotated[str, Field(min_length=1, max_length=96)]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
# Artifact references are opaque server identifiers.  A path, URL or loader
# expression would violate the artifact_id-only boundary at the trust edge.
ArtifactId = Annotated[str, Field(min_length=1, max_length=96, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")]
LifecycleStatus = Literal["VALIDATING", "CANDIDATE", "APPROVED", "REJECTED", "RETIRED"]
Stage = Literal["STAGING", "PRODUCTION"]
DeploymentStatus = Literal["NOT_DEPLOYED", "STAGING", "PRODUCTION"]
Role = Literal["CHAMPION", "CHALLENGER"]


class ExternalPackageSource(ModelCenterModel):
    kind: Literal["external_package"]
    artifact_id: ArtifactId


class TrainingResultSource(ModelCenterModel):
    kind: Literal["training_result"]
    experiment_id: Id
    artifact_id: ArtifactId


RegistrationSource = Annotated[Union[ExternalPackageSource, TrainingResultSource], Field(discriminator="kind")]


class ModelRegistrationCreate(ModelCenterModel):
    project_id: Id
    source: RegistrationSource
    name: Annotated[str, Field(min_length=1, max_length=200)]
    task_type: Annotated[str, Field(min_length=1, max_length=96)]


class CheckItem(ModelCenterModel):
    step: Annotated[str, Field(min_length=1, max_length=96)]
    status: Literal["PENDING", "RUNNING", "PASSED", "FAILED"]
    report_ref: Optional[dict[str, str]] = None
    checked_at: Optional[datetime] = None
    message: Optional[str] = None


class RegistrationValidation(ModelCenterModel):
    registration_id: Id
    revision: int = Field(ge=1)
    etag: str
    valid: bool
    checks: list[CheckItem]
    field_errors: list[dict[str, str]] = Field(default_factory=list)
    warnings: list[dict[str, str]] = Field(default_factory=list)


class EmptyRequest(ModelCenterModel):
    """Strict empty request body used by validation operations."""

    pass


class RegistrationAccepted(ModelCenterModel):
    id: Id
    job_id: Id
    status: Literal["QUEUED", "VALIDATING", "SUCCEEDED", "FAILED", "CANCELED"]
    resource_ref: ResourceRef


class ModelRegistration(ModelCenterModel):
    id: Id
    project_id: Id
    revision: int = Field(ge=1)
    config_hash: Sha256
    etag: str
    status: Literal["DRAFT", "VALIDATING", "VALID", "INVALID", "REGISTERED", "CANCELED"]
    source: RegistrationSource
    name: str
    task_type: str
    manifest_snapshot: Optional[dict[str, object]] = None
    checks: list[CheckItem] = Field(default_factory=list)
    job_id: Optional[str] = None
    attempt: int = Field(default=1, ge=1)
    dispatch_id: Optional[Id] = None
    model_id: Optional[str] = None
    error_report_ref: Optional[dict[str, str]] = None
    validation_report: Optional[RegistrationValidation] = None
    created_at: datetime
    updated_at: datetime


class ModelCard(ModelCenterModel):
    summary: str = ""
    training_data_ref: Optional[dict[str, str]] = None
    metrics: dict[str, object] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    license: Optional[str] = None


class DeploymentRef(ModelCenterModel):
    id: Id
    scope_id: Id
    stage: Stage
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED"]
    is_current: bool = False


class ComparisonRole(ModelCenterModel):
    scope_id: Id
    role: Role


class Model(ModelCenterModel):
    id: Id
    project_id: Id
    name: str
    version: str
    task_type: str
    artifact_id: Id
    sha256: Sha256
    lifecycle_status: LifecycleStatus
    deployment_status: DeploymentStatus = "NOT_DEPLOYED"
    input_schema_version: str
    output_schema_version: str
    feature_version: Optional[str] = None
    source: dict[str, Optional[str]]
    applicability: dict[str, object] = Field(default_factory=dict)
    model_card: ModelCard = Field(default_factory=ModelCard)
    deployments: list[DeploymentRef] = Field(default_factory=list)
    comparison_roles: list[ComparisonRole] = Field(default_factory=list)
    revision: int = 1
    etag: str = '"1"'
    created_at: datetime
    updated_at: datetime


class ModelDetail(ModelCenterModel):
    model: Model
    evaluations: list["ModelEvaluation"] = Field(default_factory=list)
    approval_refs: list[str] = Field(default_factory=list)
    registration_ref: str
    active_scope_id: Optional[str] = None


class Artifact(ModelCenterModel):
    """Read-only artifact metadata exposed through the model relationship."""

    id: Id
    project_id: Id
    owner_id: Id = "model-center"
    kind: str = "model_package"
    file_name: str = ""
    media_type: str = "application/octet-stream"
    size_bytes: int = Field(default=0, ge=0)
    sha256: Optional[Sha256] = None
    status: str = "AVAILABLE"
    scan_status: str = "PASSED"
    validation_report_ref: Optional[ResourceRef] = None
    source_ref: Optional[ResourceRef] = None
    retention_until: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    download_allowed: bool = False
    warnings: list[dict[str, object]] = Field(default_factory=list)


class EvaluationProtocol(ModelCenterModel):
    id: Id
    version: Annotated[str, Field(min_length=1, max_length=64)]
    task_type: str
    data_version_id: Id
    split_manifest_sha256: Sha256
    metrics: list[dict[str, object]] = Field(min_length=1)
    thresholds: dict[str, object] = Field(default_factory=dict)
    scope: dict[str, object] = Field(default_factory=dict)
    resource_limit: dict[str, object] = Field(default_factory=dict)
    independence_description: str = Field(min_length=1)
    status: Literal["DRAFT", "APPROVED", "RETIRED"] = "APPROVED"


class EvaluationCreate(ModelCenterModel):
    project_id: Id
    model_id: Id
    protocol_id: Id
    protocol_version: str


class ModelSnapshot(ModelCenterModel):
    id: Id
    version: str
    business_scope_id: Id
    artifact_sha256: Sha256
    input_schema_version: str
    output_schema_version: str


class DataSnapshot(ModelCenterModel):
    data_version_id: Id
    split_manifest_sha256: Sha256
    snapshot_hash: Sha256


class ModelEvaluation(ModelCenterModel):
    id: Id
    project_id: Id
    job_id: Optional[str] = None
    attempt: int = Field(default=1, ge=1)
    dispatch_id: Optional[Id] = None
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELED"]
    model_snapshot: ModelSnapshot
    protocol_snapshot: EvaluationProtocol
    data_snapshot: DataSnapshot
    metrics: dict[str, object] = Field(default_factory=dict)
    pass_status: Literal["PENDING", "PASSED", "FAILED"] = "PENDING"
    report_artifact_id: Optional[str] = None
    error: Optional[dict[str, str]] = None
    revision: int = 1
    etag: str = '"1"'
    created_at: datetime
    updated_at: datetime


class EvaluationAccepted(ModelCenterModel):
    id: Id
    job_id: Id
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELED"]
    resource_ref: ResourceRef


class ComparisonCreate(ModelCenterModel):
    project_id: Id
    model_ids: list[Id] = Field(min_length=2, max_length=2)
    protocol_id: Id
    protocol_version: str
    scope_id: Id

    @model_validator(mode="after")
    def distinct_models(self):
        if self.model_ids[0] == self.model_ids[1]:
            raise ValueError("model_ids must identify two models")
        return self


class ModelComparison(ModelCenterModel):
    id: Id
    project_id: Id
    model_ids: list[Id]
    protocol_snapshot: EvaluationProtocol
    data_snapshot: DataSnapshot
    scope_id: Id
    metrics: dict[str, object] = Field(default_factory=dict)
    created_at: datetime


class ApprovalRequestCreate(ModelCenterModel):
    evaluation_id: Id
    scope_ids: list[Id] = Field(min_length=1)
    reason: Annotated[str, Field(min_length=1, max_length=2_000)]


class ApprovalDecision(ModelCenterModel):
    reason: Annotated[str, Field(min_length=1, max_length=2_000)]
    expected_revision: int = Field(ge=1)


class ApprovalRequest(ModelCenterModel):
    id: Id
    project_id: Id
    model_id: Id
    evaluation_snapshot: ModelEvaluation
    scope_ids: list[Id]
    status: Literal["PENDING", "APPROVED", "REJECTED"]
    requested_by: Id
    decided_by: Optional[str] = None
    reason: str
    revision: int = 1
    etag: str = '"1"'
    created_at: datetime
    decided_at: Optional[datetime] = None


class DeploymentDraftCreate(ModelCenterModel):
    project_id: Id
    scope_id: Id
    target_model_id: Id
    stage: Stage = "PRODUCTION"
    expected_pointer_revision: int = Field(ge=0)
    rollback_model_id: Optional[Id] = None


class RollbackDraftCreate(ModelCenterModel):
    target_historical_model_id: Id
    expected_pointer_revision: int = Field(ge=0)
    reason: Annotated[str, Field(min_length=1, max_length=2_000)]


class DeploymentValidation(ModelCenterModel):
    draft_id: Id
    revision: int
    etag: str
    valid: bool
    field_errors: list[dict[str, str]] = Field(default_factory=list)
    warnings: list[dict[str, str]] = Field(default_factory=list)


class DeploymentDraft(ModelCenterModel):
    id: Id
    project_id: Id
    scope_id: Id
    target_model_id: Id
    stage: Stage
    expected_pointer_revision: int
    rollback_model_id: Optional[str] = None
    revision: int = 1
    config_hash: Sha256
    etag: str = '"1"'
    status: Literal["DRAFT", "READY", "INVALID", "SUBMITTED"]
    validation_report: Optional[DeploymentValidation] = None
    source_deployment_id: Optional[str] = None
    operation: Literal["PUBLISH", "ROLLBACK"]
    confirmation_id: Optional[str] = None
    reason: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ProductionPointer(ModelCenterModel):
    project_id: Id
    scope_id: Id
    stage: Stage
    model_id: Optional[str] = None
    revision: int = 0
    updated_at: datetime


class Deployment(ModelCenterModel):
    id: Id
    project_id: Id
    scope_id: Id
    stage: Stage
    target_model_id: Id
    operation: Literal["PUBLISH", "ROLLBACK"]
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELED"]
    expected_pointer_revision: int
    pointer_revision: Optional[int] = None
    error: Optional[dict[str, str]] = None
    draft_id: Id
    attempt: int = Field(default=1, ge=1)
    dispatch_id: Optional[Id] = None
    created_at: datetime
    finished_at: Optional[datetime] = None


class DeploymentAccepted(ModelCenterModel):
    id: Id
    job_id: Id
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED"]
    resource_ref: ResourceRef
    attempt: int = Field(default=1, ge=1)
    dispatch_id: Optional[Id] = None


class ConfirmationRequest(ModelCenterModel):
    confirmation_id: Id


class AuditEvent(ModelCenterModel):
    id: Id
    project_id: Id
    actor_id: str
    interaction: str
    action: str
    resource_ref: ResourceRef
    before_summary: dict[str, object] = Field(default_factory=dict)
    after_summary: dict[str, object] = Field(default_factory=dict)
    confirmation_id: Optional[str] = None
    config_hash: Optional[str] = None
    result: Literal["SUCCEEDED", "FAILED"]
    timestamp: datetime


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [name for name in globals() if not name.startswith("_")]
