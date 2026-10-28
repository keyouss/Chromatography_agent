"""Strict public contracts for fixed-template training.

The request DTOs intentionally have no fields that could name code, a
command, a server path, an entrypoint or a dependency list.
"""

from __future__ import annotations

from datetime import datetime
import math
from typing import Annotated, Any, Literal, Union

from pydantic import ConfigDict, Field, field_validator, model_validator

from chrom_platform.core.dto import ResourceRef, StrictModel


class TrainingStrictModel(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True)


Health = Literal["READY", "DEGRADED", "UNAVAILABLE", "UNKNOWN"]
ExperimentStatus = Literal[
    "DRAFT", "VALIDATING", "INVALID", "READY", "QUEUED", "RUNNING",
    "CANCEL_REQUESTED", "SUCCEEDED", "FAILED", "CANCELED", "TIMED_OUT", "INTERRUPTED",
]
CandidateStatus = Literal["NOT_REQUESTED", "VALIDATING", "CREATED", "REJECTED"]
JobStatus = Literal[
    "QUEUED", "RUNNING", "CANCEL_REQUESTED", "SUCCEEDED", "FAILED",
    "CANCELED", "TIMED_OUT", "INTERRUPTED",
]


class SplitRandom(TrainingStrictModel):
    strategy: Literal["random"]
    train_fraction: float = Field(gt=0, lt=1)
    validation_fraction: float = Field(gt=0, lt=1)
    test_fraction: float = Field(gt=0, lt=1)
    random_seed: int = Field(ge=0, le=2_147_483_647)

    @model_validator(mode="after")
    def fractions_sum(self) -> "SplitRandom":
        if abs(self.train_fraction + self.validation_fraction + self.test_fraction - 1.0) > 1e-9:
            raise ValueError("split fractions must sum to 1")
        return self


class SplitScaffold(SplitRandom):
    strategy: Literal["scaffold"]


class SplitGroup(SplitRandom):
    strategy: Literal["group"]
    group_field: str = Field(min_length=1, max_length=128)


class SplitPredefined(TrainingStrictModel):
    strategy: Literal["predefined"]
    split_manifest_artifact_id: str = Field(min_length=1, max_length=96)


SplitConfig = Annotated[
    Union[SplitRandom, SplitScaffold, SplitGroup, SplitPredefined],
    Field(discriminator="strategy"),
]


_FORBIDDEN_PARAMETER_KEYS = {
    "code", "command", "python", "script", "entrypoint", "requirements",
    "dependency", "dependencies", "code_artifact", "module", "import",
    "server_path", "working_dir", "sandbox",
}


def _validate_parameters(value: dict[str, Any] | None) -> dict[str, Any] | None:
    """Keep template parameters data-only; execution comes from the registry."""

    def walk(item: Any) -> None:
        if item is None or isinstance(item, (str, bool, int)):
            return
        if isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError("parameters must contain finite JSON numbers")
            return
        if isinstance(item, list):
            for child in item:
                walk(child)
            return
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str) or key.lower() in _FORBIDDEN_PARAMETER_KEYS:
                    raise ValueError("parameters cannot contain executable or dependency fields")
                walk(child)
            return
        raise ValueError("parameters must contain JSON data only")

    if value is not None:
        walk(value)
    return value


class TemplateExecution(TrainingStrictModel):
    kind: Literal["template"]
    template_id: str = Field(min_length=1, max_length=96)
    template_version: str = Field(min_length=1, max_length=64)


class EvaluationProtocol(TrainingStrictModel):
    id: str = Field(min_length=1, max_length=96)
    version: str = Field(min_length=1, max_length=64)
    status: Literal["DRAFT", "REVIEWED", "APPROVED", "READY", "REJECTED"] = "DRAFT"
    reviewed: bool | None = None
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    task_type: str | None = Field(default=None, max_length=96)
    metrics: list[str] = Field(default_factory=list)

    @property
    def is_approved(self) -> bool:
        return self.status in {"REVIEWED", "APPROVED", "READY"} or self.reviewed is True


class ResourceBudget(TrainingStrictModel):
    cpu_cores: int = Field(ge=1, le=256)
    memory_mb: int = Field(ge=128, le=1_048_576)
    gpu_count: int = Field(ge=0, le=64)
    max_runtime_seconds: int = Field(ge=1, le=7 * 24 * 60 * 60)


class TrainingExperimentSpec(TrainingStrictModel):
    project_id: str = Field(min_length=1, max_length=96)
    name: str = Field(min_length=1, max_length=255)
    task_type: str = Field(min_length=1, max_length=96)
    execution: TemplateExecution
    data_version_id: str = Field(min_length=1, max_length=96)
    feature_version_id: str = Field(min_length=1, max_length=96)
    target_field: str = Field(min_length=1, max_length=128)
    split: SplitConfig
    parameters: dict[str, Any] = Field(default_factory=dict)
    evaluation_protocol: EvaluationProtocol
    resource_budget: ResourceBudget

    @field_validator("parameters")
    @classmethod
    def no_executable_parameter_keys(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_parameters(value) or {}


ExperimentSpec = TrainingExperimentSpec


class ExperimentCreate(TrainingExperimentSpec):
    pass


class ExperimentPatch(TrainingStrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    target_field: str | None = Field(default=None, min_length=1, max_length=128)
    split: SplitConfig | None = None
    parameters: dict[str, Any] | None = None
    resource_budget: ResourceBudget | None = None
    evaluation_protocol: EvaluationProtocol | None = None

    _parameters_are_data = field_validator("parameters")(_validate_parameters)

    @model_validator(mode="after")
    def non_empty(self) -> "ExperimentPatch":
        if all(getattr(self, name) is None for name in ("name", "target_field", "split", "parameters", "resource_budget", "evaluation_protocol")):
            raise ValueError("at least one experiment field is required")
        return self


class ValidationFieldError(TrainingStrictModel):
    path: str
    code: str
    message: str


class ExperimentValidation(TrainingStrictModel):
    experiment_id: str
    revision: int = Field(ge=1)
    etag: str = Field(min_length=1)
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    valid: bool
    status: Literal["READY", "INVALID"]
    field_errors: list[ValidationFieldError] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    resource_estimate: dict[str, Any] = Field(default_factory=dict)


class TrainingTemplate(TrainingStrictModel):
    id: str = Field(min_length=1, max_length=96)
    version: str = Field(min_length=1, max_length=64)
    task_type: str = Field(min_length=1, max_length=96)
    data_schema_id: str = Field(min_length=1, max_length=96)
    feature_versions: list[str] = Field(min_length=1)
    allowed_targets: list[str] = Field(min_length=1)
    parameters_schema: dict[str, Any]
    split_schema: dict[str, Any]
    output_manifest_schema: dict[str, Any]
    evaluation_protocols: list[ResourceRef] = Field(min_length=1)
    resource_limits: ResourceBudget
    code_version: str = Field(min_length=1, max_length=128)
    dependency_lock_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    checkpoint_support: bool = False
    health: Health


class Experiment(TrainingStrictModel):
    id: str
    revision: int = Field(ge=1)
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    etag: str = Field(min_length=1, max_length=128)
    spec: TrainingExperimentSpec
    status: ExperimentStatus
    validation_report: ExperimentValidation | None = None
    job_id: str | None = None
    current_attempt: int = Field(ge=0)
    candidate_status: CandidateStatus
    candidate_model_id: str | None = None
    spec_locked: bool = False
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: dict[str, Any] | None = None


class EmptyRequest(TrainingStrictModel):
    """Strict JSON object used by operations whose body is exactly ``{}``."""

    pass


class TrainingEvent(TrainingStrictModel):
    id: str = Field(min_length=1, max_length=96)
    job_id: str = Field(min_length=1, max_length=96)
    stream_id: str = Field(min_length=1, max_length=96)
    seq: int = Field(ge=0)
    type: str = Field(min_length=1, max_length=96)
    schema_version: str = Field(min_length=1, max_length=64)
    data: dict[str, Any]
    timestamp: datetime


class TrainingEventPage(TrainingStrictModel):
    events: list[TrainingEvent]
    next_seq: str
    has_more: bool
    stream_status: Literal["OPEN", "TERMINAL"]


class EventPage(TrainingEventPage):
    pass


class LogEntry(TrainingStrictModel):
    seq: int = Field(ge=0)
    timestamp: datetime
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"]
    message: str = Field(min_length=1, max_length=2_000)
    stage: str = Field(min_length=1, max_length=96)
    truncated: bool = False


class MetricPoint(TrainingStrictModel):
    key: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=255)
    value: float
    unit: str | None = Field(default=None, max_length=64)
    step: int = Field(ge=0)
    split: str = Field(min_length=1, max_length=32)
    attempt: int = Field(ge=1)
    timestamp: datetime

    @field_validator("value")
    @classmethod
    def finite_value(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("metric value must be finite")
        return value


class ExperimentMetrics(TrainingStrictModel):
    experiment_id: str
    attempt: int = Field(ge=1)
    protocol_snapshot: dict[str, Any]
    series: list[MetricPoint]
    final_metrics: list[MetricPoint]
    next_step: int | None = Field(default=None, ge=0)


class TrainingArtifact(TrainingStrictModel):
    id: str = Field(min_length=1, max_length=96)
    project_id: str = Field(min_length=1, max_length=96)
    kind: str = Field(min_length=1, max_length=64)
    file_name: str = Field(min_length=1, max_length=255)
    media_type: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(ge=0)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    status: Literal["CREATED", "RECEIVED", "VALIDATING", "AVAILABLE", "QUARANTINED", "FAILED", "ARCHIVED", "DELETING", "DELETED", "EXPIRED"]
    created_at: datetime


class Artifact(TrainingArtifact):
    pass


class ExperimentComparisonMetricValue(TrainingStrictModel):
    resource_id: str
    value: float

    @field_validator("value")
    @classmethod
    def finite_value(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("comparison value must be finite")
        return value


class ExperimentComparisonMetric(TrainingStrictModel):
    key: str
    unit: str | None = None
    values: list[ExperimentComparisonMetricValue]
    direction: Literal["higher", "lower", "none"]


class ExperimentComparisonRequest(TrainingStrictModel):
    experiment_ids: list[str] = Field(min_length=2, max_length=10)
    protocol_id: str = Field(min_length=1, max_length=96)
    protocol_version: str = Field(min_length=1, max_length=64)


class ExperimentComparisonCreate(ExperimentComparisonRequest):
    pass


class ExperimentComparison(TrainingStrictModel):
    experiment_ids: list[str] = Field(min_length=2, max_length=10)
    protocol_snapshot: dict[str, Any]
    data_snapshot: dict[str, Any]
    metrics: list[ExperimentComparisonMetric]
    warnings: list[str] = Field(default_factory=list)


class SubmitRequest(TrainingStrictModel):
    confirmation_id: str = Field(min_length=1, max_length=96)


class CancelRequest(TrainingStrictModel):
    reason: str | None = Field(default=None, max_length=2_000)


class ExperimentAccepted(TrainingStrictModel):
    id: str
    job_id: str
    status: Literal["QUEUED"]
    resource_ref: ResourceRef


class TrainingJob(TrainingStrictModel):
    id: str
    project_id: str
    experiment_id: str
    status: JobStatus
    attempt: int = Field(ge=1)
    dispatch_id: str
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result_ref: ResourceRef | None = None
    error: dict[str, Any] | None = None


class WorkerMessage(TrainingStrictModel):
    job_id: str = Field(min_length=1, max_length=96)
    attempt: int = Field(ge=1)
    dispatch_id: str = Field(min_length=1, max_length=96)


class CandidateResult(TrainingStrictModel):
    artifact_id: str = Field(min_length=1, max_length=96)
    manifest: dict[str, Any]
    metrics: dict[str, Any] = Field(default_factory=dict)


class TrainingCandidate(TrainingStrictModel):
    id: str
    project_id: str
    experiment_id: str
    artifact_id: str
    status: Literal["CANDIDATE", "REJECTED"]
    published: bool = False
    created_at: datetime


class TrainingOutbox(TrainingStrictModel):
    dispatch_id: str
    job_id: str
    status: Literal["PENDING", "PROCESSING", "SENT", "FAILED"]
    attempts: int = Field(ge=0)
    payload: WorkerMessage
    created_at: datetime
    lease_owner: str | None = None
    lease_token: int = Field(default=0, ge=0)
    lease_expires_at: datetime | None = None


class TrainingAuditEvent(TrainingStrictModel):
    id: str
    project_id: str
    actor_id: str
    action: str
    resource_ref: ResourceRef
    result: Literal["SUCCEEDED", "FAILED"]
    confirmation_id: str | None = None
    job_id: str | None = None
    config_hash: str | None = None
    created_at: datetime


__all__ = [
    "CandidateResult", "CandidateStatus", "EvaluationProtocol", "Experiment",
    "CancelRequest", "EmptyRequest", "ExperimentAccepted", "ExperimentComparison", "ExperimentComparisonCreate", "ExperimentComparisonMetric", "ExperimentComparisonMetricValue", "ExperimentComparisonRequest", "ExperimentCreate", "ExperimentMetrics", "ExperimentPatch", "ExperimentSpec",
    "ExperimentStatus", "ExperimentValidation", "JobStatus", "ResourceBudget",
    "LogEntry", "MetricPoint", "SplitConfig", "SplitGroup", "SplitPredefined", "SplitRandom", "SplitScaffold",
    "Artifact", "EventPage", "SubmitRequest", "TemplateExecution", "TrainingArtifact", "TrainingAuditEvent", "TrainingCandidate", "TrainingEvent", "TrainingEventPage",
    "TrainingExperimentSpec", "TrainingJob", "TrainingOutbox", "TrainingTemplate",
    "WorkerMessage",
]
