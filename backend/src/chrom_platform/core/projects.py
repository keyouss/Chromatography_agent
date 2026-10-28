"""Project resource read port used by the application layer."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from .dto import ActorContext


class ProjectReader(Protocol):
    """Read a bounded keyset page within a server-selected scope.

    Implementations must apply ``actor`` authorization and ``id > after_id``
    in the database query, order by ``Project.id`` ascending, and return at
    most ``limit`` rows.  The application requests ``limit + 1`` to determine
    ``has_more`` without materializing the complete project set.
    """

    def list_accessible(
        self,
        actor: ActorContext,
        *,
        limit: int,
        after_id: str | None,
    ) -> Sequence[object]: ...


@dataclass(frozen=True)
class ConfiguredProject:
    """Read-only project resource used when local DB setup is absent.

    This is intentionally initialized from server configuration only.  It
    prevents a browser supplied ``project-default`` value from becoming a
    project and keeps contract generation/local smoke tests deterministic.
    """

    id: str
    name: str
    status: str = "ACTIVE"


class ConfiguredProjectReader:
    """Idempotent, in-process project reader for a configured local scope."""

    def __init__(self, project_ids: Sequence[str]) -> None:
        self._projects = tuple(
            ConfiguredProject(id=value, name=value)
            for value in dict.fromkeys(item.strip() for item in project_ids if item and item.strip())
        )

    def list_accessible(
        self,
        actor: ActorContext,
        *,
        limit: int,
        after_id: str | None,
    ) -> Sequence[ConfiguredProject]:
        if not 1 <= limit <= 201:
            raise ValueError("limit must be between 1 and 201")
        allowed = set(actor.project_ids)
        values = [item for item in self._projects if item.id in allowed and (after_id is None or item.id > after_id)]
        return values[:limit]

    def get_accessible(self, actor: ActorContext, project_id: str) -> ConfiguredProject | None:
        return next((item for item in self._projects if item.id == project_id and actor.can_access_project(project_id)), None)


def project_id_from_row(row: object) -> str | None:
    value = getattr(row, "id", None)
    if value is None and isinstance(row, dict):
        value = row.get("id")
    return str(value) if value is not None and str(value) else None


def project_name_from_row(row: object) -> str | None:
    value = getattr(row, "name", None)
    if value is None and isinstance(row, dict):
        value = row.get("name")
    return str(value) if value is not None and str(value) else None


@dataclass(frozen=True)
class ProjectResolution:
    project_id: str | None
    projects: tuple[tuple[str, str | None], ...]
    status: str


class ProjectSelectionError(ValueError):
    """A request cannot be safely bound to a server-accessible project."""

    def __init__(self, code: str, message: str, *, status_code: int, details: dict[str, object] | None = None) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        super().__init__(message)


def resolve_project(
    actor: ActorContext,
    reader: ProjectReader,
    requested_project_id: str | None = None,
    *,
    default_project_id: str | None = None,
) -> ProjectResolution:
    """Resolve a request project only from the server-verified reader set."""

    rows = reader.list_accessible(actor, limit=201, after_id=None)
    projects: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for row in rows:
        project_id = project_id_from_row(row)
        if project_id and project_id not in seen:
            seen.add(project_id)
            projects.append((project_id, project_name_from_row(row)))
    project_ids = {item[0] for item in projects}
    if requested_project_id is not None:
        if requested_project_id not in project_ids:
            raise ProjectSelectionError(
                "RESOURCE_NOT_FOUND",
                "项目不存在或当前主体不可见",
                status_code=404,
            )
        return ProjectResolution(requested_project_id, tuple(projects), "resolved")
    configured_default = default_project_id or actor.default_project_id
    if configured_default and configured_default in project_ids:
        return ProjectResolution(configured_default, tuple(projects), "resolved")
    if len(projects) == 1:
        return ProjectResolution(projects[0][0], tuple(projects), "resolved")
    if not projects:
        raise ProjectSelectionError(
            "PROJECT_SELECTION_REQUIRED",
            "当前主体没有可访问项目，请先初始化或配置项目",
            status_code=422,
            details={"selection": "no_accessible_projects", "accessible_projects": []},
        )
    raise ProjectSelectionError(
        "PROJECT_SELECTION_REQUIRED",
        "存在多个可访问项目，请明确选择项目",
        status_code=422,
        details={"selection": "needs_selection", "accessible_projects": [item[0] for item in projects]},
    )


__all__ = [
    "ConfiguredProject",
    "ConfiguredProjectReader",
    "ProjectReader",
    "ProjectResolution",
    "ProjectSelectionError",
    "project_id_from_row",
    "project_name_from_row",
    "resolve_project",
]
