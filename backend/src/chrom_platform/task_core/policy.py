"""Server-owned task policy for the local deployment profile.

The deployment actor and its project scope are created by the server from the
protected runtime configuration.  This policy only makes the read path
available when that application-owned policy is assembled; it never accepts
actor, project, or permission values from task request payloads.
"""

from __future__ import annotations

from typing import Any, Mapping

from chrom_platform.core.dto import ResourceRef

from .service import OperationContext, TargetSnapshot, TaskError


class DeploymentTaskPolicy:
    """Least-privilege task policy used by the priority-local deployment."""

    policy_version = "deployment-default-v1"

    def required_permission(
        self,
        operation: str,
        *,
        kind: str,
        resource_ref: Mapping[str, Any],
        action: str | None = None,
    ) -> str | None:
        del kind, resource_ref, action
        if operation == "read":
            return "task:read"
        if operation == "cancel":
            return "task:cancel"
        if operation == "retry":
            return "task:retry"
        if operation == "confirm":
            return "task:confirm"
        return None

    def authorize_job(self, ctx: OperationContext, job: Any) -> bool:
        """Permit a scoped actor to read its own job only.

        An explicitly server-granted ``task:read:any`` permission is reserved
        for future administrative readers.  It is never inferred from a
        request field or from project membership alone.
        """

        if not ctx.actor.can_access_project(job.project_id):
            return False
        if ctx.actor.can("task:read:any"):
            return True
        return ctx.actor.can("task:read") and job.owner_id == ctx.actor.actor_id

    def authorize_resource(
        self,
        ctx: OperationContext,
        target: TargetSnapshot,
        *,
        action: str,
    ) -> bool:
        del action
        return ctx.actor.can_access_project(target.project_id)

    def retry_requires_confirmation(self, *, kind: str, resource_ref: Mapping[str, Any]) -> bool:
        del kind, resource_ref
        return True

    def resolve_target(
        self,
        ctx: OperationContext,
        resource_ref: ResourceRef,
        action: str,
    ) -> TargetSnapshot:
        del ctx, resource_ref, action
        raise TaskError(503, "POLICY_UNAVAILABLE", "确认目标策略未配置", retryable=True)

    def filter_visible_jobs(self, ctx: OperationContext, rows: list[Any]) -> list[Any]:
        return [row for row in rows if self.authorize_job(ctx, row)]


__all__ = ["DeploymentTaskPolicy"]
