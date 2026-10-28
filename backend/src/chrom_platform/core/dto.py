from __future__ import annotations

from datetime import datetime
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=False)


PublicId = str
T = TypeVar("T")


class ResourceRef(StrictModel):
    type: str = Field(min_length=1, max_length=64)
    id: PublicId = Field(min_length=1, max_length=96)


class WarningDTO(StrictModel):
    code: str = Field(min_length=1, max_length=96)
    message: str = Field(min_length=1, max_length=2_000)
    resource_ref: ResourceRef | None = None
    field: str | None = None


class ErrorDetail(StrictModel):
    code: str
    message: str
    field_errors: list[dict[str, str]] = Field(default_factory=list)
    retryable: bool = False
    details: dict[str, object] = Field(default_factory=dict)


class ErrorEnvelope(StrictModel):
    error: ErrorDetail
    request_id: PublicId


class SuccessEnvelope(StrictModel, Generic[T]):
    data: T
    warnings: list[WarningDTO] = Field(default_factory=list)
    request_id: PublicId


class PageInfo(StrictModel):
    next_cursor: str | None = None
    limit: int = Field(ge=1, le=200)
    has_more: bool


class ListEnvelope(StrictModel, Generic[T]):
    data: list[T]
    page: PageInfo
    warnings: list[WarningDTO] = Field(default_factory=list)
    request_id: PublicId


class ActorContext(StrictModel):
    actor_id: PublicId
    tenant_id: PublicId | None = None
    project_ids: list[PublicId]
    auth_method: Literal["deployment_default", "session", "bearer", "service"]
    permissions: list[str]
    policy_version: str
    # Server-owned preference only.  It is never accepted from request JSON.
    # The project resolver still verifies it against the persisted accessible
    # project set before using it.
    default_project_id: PublicId | None = None

    def can_access_project(self, project_id: str) -> bool:
        """Return whether this server-created actor may use ``project_id``."""

        return project_id in self.project_ids

    def can(self, permission: str) -> bool:
        """Check a server-side permission without trusting request JSON."""

        return "*" in self.permissions or permission in self.permissions


class CapabilityHealth(StrictModel):
    status: Literal["READY", "DEGRADED", "UNAVAILABLE", "UNKNOWN"]
    reason: str | None = None
    checked_at: datetime


class CapabilityView(StrictModel):
    id: str
    status: Literal["READY", "DEGRADED", "UNAVAILABLE", "UNKNOWN"]
    schema_version: str
    reason: str | None = None


class Capabilities(StrictModel):
    build_id: str
    api_version: str
    modules: list[str]
    chromatography_types: list[str]
    assistant_modes: list[str]
    export_formats: list[str]
    auth_mode: Literal["deployment_default", "session", "bearer", "service"]
    data_origin: Literal["production", "fixture"]
    capabilities: list[CapabilityView]


class VersionInfo(StrictModel):
    application_version: str
    build_id: str
    api_schema_version: str
    migration_version: str
    dependency_lock_hash: str | None = None


class AccessContextActor(StrictModel):
    id: PublicId
    auth_mode: Literal["deployment_default", "session", "bearer", "service"]
    policy_version: str


class AccessibleProjectView(StrictModel):
    id: PublicId
    name: str | None = None


class AccessContextView(StrictModel):
    """Non-sensitive, server-derived project selection context."""

    actor: AccessContextActor
    tenant: PublicId | None = None
    permissions: list[str]
    accessible_projects: list[AccessibleProjectView]
    default_project_id: PublicId | None = None
    project_selection: Literal["resolved", "needs_selection", "no_accessible_projects"]
    # Compatibility fields are retained while clients migrate to the typed
    # context.  They are projections of the same server-owned values.
    actor_id: PublicId
    auth_mode: Literal["deployment_default", "session", "bearer", "service"]
    allowed_actions: list[str]
    is_multi_user_authenticated: Literal[False]


class ProjectView(StrictModel):
    id: PublicId
    name: str
    status: str


class DependencyHealth(StrictModel):
    id: str
    status: Literal["READY", "DEGRADED", "UNAVAILABLE", "UNKNOWN"]
    checked_at: datetime
    reason: str | None = None


class Readiness(StrictModel):
    core_ready: bool
    checked_at: datetime
    capabilities: list[CapabilityView]


class BuildManifest(StrictModel):
    manifest_version: Literal["1.0"]
    build_id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    modules: list[str]
    chromatography_types: list[str]
    prediction_tools: dict[str, list[str]]
    utilities: list[str]
    export_formats: list[str]
    auth_provider: Literal["deployment_default"]
    assistant_features: list[str] | None = None
