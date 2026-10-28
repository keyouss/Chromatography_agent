"""Strict public contracts for the task core.

The task module deliberately keeps actor and project scope out of request
models.  Those values are supplied by the server side operation context.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import ConfigDict, Field, field_validator

from chrom_platform.core.dto import ResourceRef, StrictModel

from .state import ConfirmationStatus, JobStatus


class TaskStrictModel(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=False)


class JobCreateRequest(TaskStrictModel):
    kind: str = Field(min_length=1, max_length=96)
    resource_ref: ResourceRef
    cancelable: bool = True
    retryable: bool = False
    resume_mode: Literal["none", "restart", "checkpoint"] = "none"
    deadline_at: datetime | None = None


class JobCancelRequest(TaskStrictModel):
    reason: str | None = Field(default=None, max_length=2_000)


class JobRetryRequest(TaskStrictModel):
    confirmation_id: str | None = Field(default=None, min_length=1, max_length=96)


class ConfirmationPreviewRequest(TaskStrictModel):
    resource_ref: ResourceRef
    action: str = Field(min_length=1, max_length=160)


class ConfirmationCreateRequest(TaskStrictModel):
    resource_ref: ResourceRef
    action: str = Field(min_length=1, max_length=160)
    revision: int = Field(ge=1)
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_version: str = Field(min_length=1, max_length=64)


class ConfirmationRevokeRequest(TaskStrictModel):
    reason: str = Field(min_length=1, max_length=2_000)


class JobView(TaskStrictModel):
    id: str
    project_id: str
    owner_id: str
    kind: str
    resource_ref: ResourceRef
    status: JobStatus
    attempt: int = Field(ge=1)
    progress: dict[str, Any]
    cancelable: bool
    retryable: bool
    resume_mode: Literal["none", "restart", "checkpoint"]
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    deadline_at: datetime | None
    result_ref: ResourceRef | None
    error: dict[str, Any] | None
    revision: int = Field(ge=1)
    etag: str = Field(min_length=1, max_length=128)


class JobAttemptView(TaskStrictModel):
    id: str
    job_id: str
    attempt: int = Field(ge=1)
    status: JobStatus
    resume_mode: Literal["none", "restart", "checkpoint"]
    checkpoint_ref: ResourceRef | None
    started_at: datetime | None
    finished_at: datetime | None
    error: dict[str, Any] | None
    result_ref: ResourceRef | None


class JobEventView(TaskStrictModel):
    id: str
    job_id: str
    stream_id: str
    seq: str = Field(pattern=r"^(0|[1-9][0-9]{0,18})$", max_length=19)
    type: str
    schema_version: str
    data: dict[str, Any]
    timestamp: datetime

    @field_validator("seq")
    @classmethod
    def validate_bigint_seq(cls, value: str) -> str:
        if int(value) > (1 << 63) - 1:
            raise ValueError("seq exceeds signed MySQL BIGINT")
        return value


class ConfirmationView(TaskStrictModel):
    id: str
    project_id: str
    actor_id: str
    resource_ref: ResourceRef
    action: str
    revision: int = Field(ge=1)
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_version: str
    summary_snapshot: dict[str, Any]
    status: ConfirmationStatus
    created_at: datetime
    expires_at: datetime
    consumed_at: datetime | None
    consumed_result_ref: dict[str, Any] | None


class ConfirmationSummaryItem(TaskStrictModel):
    label: str
    value: Any
    unit: str | None = None


class ConfirmationDependency(TaskStrictModel):
    type: str
    id: str
    version: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ConfirmationBlocker(TaskStrictModel):
    code: str
    message: str


class ConfirmationPreview(TaskStrictModel):
    resource_ref: ResourceRef
    action: str
    revision: int = Field(ge=1)
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    etag: str = Field(min_length=1, max_length=128)
    summary: list[ConfirmationSummaryItem]
    dependencies: list[ConfirmationDependency]
    impact: dict[str, Any]
    can_confirm: bool
    blockers: list[ConfirmationBlocker]
    policy_version: str


class EventPage(TaskStrictModel):
    events: list[JobEventView]
    next_seq: str | None = Field(pattern=r"^(0|[1-9][0-9]{0,18})$", max_length=19)
    has_more: bool
    stream_status: Literal["OPEN", "TERMINAL"]

    @field_validator("next_seq")
    @classmethod
    def validate_bigint_next_seq(cls, value: str | None) -> str | None:
        if value is not None and int(value) > (1 << 63) - 1:
            raise ValueError("next_seq exceeds signed MySQL BIGINT")
        return value


__all__ = [
    "ConfirmationBlocker",
    "ConfirmationCreateRequest",
    "ConfirmationDependency",
    "ConfirmationPreview",
    "ConfirmationPreviewRequest",
    "ConfirmationRevokeRequest",
    "ConfirmationSummaryItem",
    "ConfirmationView",
    "EventPage",
    "JobAttemptView",
    "JobCancelRequest",
    "JobCreateRequest",
    "JobEventView",
    "JobRetryRequest",
    "JobView",
]
