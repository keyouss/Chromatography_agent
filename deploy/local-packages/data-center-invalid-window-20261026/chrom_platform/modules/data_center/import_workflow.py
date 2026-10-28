"""Transactional acceptance and confirmation binding for data imports."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from contextlib import nullcontext
from datetime import datetime, timezone
from typing import Any, get_args
from uuid import uuid4

from sqlalchemy import select

from chrom_platform.core.dto import ActorContext, ResourceRef
from chrom_platform.infrastructure.models import AuditEvent, Job, JobAttempt, Project
from chrom_platform.task_core.repository import SQLAlchemyTaskRepository
from chrom_platform.task_core.service import (
    OperationContext,
    TargetSnapshot,
    TaskError,
    add_job_rows,
)

from .errors import DataConflictError, DataNotFoundError, DataUnavailableError, DataValidationError
from .import_artifacts import SQLAlchemyImportArtifactPort
from .import_report import CsvImportValidationReport
from .persistence import (
    DataImportRow,
    DataImportValidationRow,
    DataVersionRow,
    SQLAlchemyDataDomainRegistry,
    SQLAlchemyDataRepository,
)
from .schemas import AsyncAccepted, DataImportValidation


_ACTOR_AUTH_METHODS = frozenset(get_args(ActorContext.model_fields["auth_method"].annotation))
_AUDIT_INTERACTIONS = frozenset({"human", "agent_tool", "worker", "service"})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _confirmation_summary(report: Any, source_sha256: str, *, extra: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    return [
        {"label": "record_count", "value": report.row_count},
        {"label": "source_sha256", "value": source_sha256},
        *(extra or []),
    ]


def _validation_dependency(validation: Any, report_sha256: str) -> list[dict[str, str]]:
    return [{
        "type": "data_import_validation",
        "id": validation.id,
        "version": str(validation.revision),
        "sha256": report_sha256,
    }]


def _add_commit_audit(session: Any, *, ctx: OperationContext, project_id: str, resource_ref: ResourceRef,
                      confirmation_id: str, binding_hash: str, job_id: str,
                      before_summary: dict[str, Any], after_summary: dict[str, Any]) -> None:
    if ctx.actor.auth_method not in _ACTOR_AUTH_METHODS or ctx.interaction not in _AUDIT_INTERACTIONS:
        raise DataConflictError("提交审计来源无效")
    session.add(AuditEvent(
        id=f"audit_{uuid4().hex}", project_id=project_id, actor_id=ctx.actor.actor_id,
        auth_method=ctx.actor.auth_method, interaction=ctx.interaction, request_id=ctx.request_id,
        action="data_import.commit", resource_type=resource_ref.type, resource_id=resource_ref.id,
        resource_ref=resource_ref.model_dump(mode="json"), before_summary=before_summary,
        after_summary=after_summary, confirmation_id=confirmation_id, config_hash=binding_hash,
        job_id=job_id, agent_run_id=ctx.agent_run_id, result="SUCCEEDED", timestamp=_now(),
    ))
    session.flush()


class _BorrowedSession:
    """Context facade that never closes or commits the caller's request UOW."""

    def __init__(self, session: Any) -> None:
        self.session = session

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def begin(self):
        return nullcontext(self.session)

    def __getattr__(self, name: str):
        return getattr(self.session, name)


class SQLAlchemyImportWorkflow:
    """Create canonical jobs and consume confirmations in caller-owned transactions."""

    def __init__(
        self,
        session: Any,
        artifact_store: Any,
        confirmation_service: Any,
        *,
        profile_resolver: Callable[[str, str], Any | None] | None = None,
        id_factory: Callable[[str], str] | None = None,
    ) -> None:
        if callable(session) or not hasattr(session, "scalar"):
            raise TypeError("SQLAlchemyImportWorkflow requires a caller-owned request Session")
        self.session = session
        self.session_factory = lambda: _BorrowedSession(session)
        self.artifact_store = artifact_store
        self.confirmation_service = confirmation_service
        self.profile_resolver = profile_resolver
        self.id_factory = id_factory

    @staticmethod
    def _authorize(ctx: OperationContext, project_id: str, *, write: bool) -> None:
        actor = ctx.actor
        if not actor.can_access_project(project_id):
            status_code = 403 if write else 404
            code = "PERMISSION_DENIED" if write else "RESOURCE_NOT_FOUND"
            raise TaskError(status_code, code, "当前主体不可访问该项目")
        permissions = ("data:write", "dataset:write", "dataset:import") if write else ("data:read", "dataset:read")
        if not actor.can("*") and not any(actor.can(permission) for permission in permissions):
            raise TaskError(403, "PERMISSION_DENIED", "当前主体缺少数据操作权限")

    def _binding(
        self, session: Any, project_id: str, import_id: str, *, allow_failed: bool = False,
    ) -> tuple[Any, Any, Any, str, str, str | None]:
        from chrom_platform.artifact_core.models import Artifact

        repo = SQLAlchemyDataRepository(session)
        value = repo.get_import(project_id, import_id)
        if value is None:
            raise DataNotFoundError("数据导入不存在")
        accepted_statuses = {"READY", "FAILED"} if allow_failed else {"READY"}
        if value.status not in accepted_statuses or not value.validation_ref:
            raise DataConflictError("只有当前校验通过的导入可以提交")
        validation_row = session.scalar(select(DataImportValidationRow).where(
            DataImportValidationRow.project_id == project_id,
            DataImportValidationRow.import_id == import_id,
            DataImportValidationRow.id == value.validation_ref,
        ))
        if validation_row is None or validation_row.status != "VALID":
            raise DataConflictError("导入验证结果不存在或已过期")
        validation = repo.get_validation(project_id, import_id)
        if validation is None or validation.id != validation_row.id or validation.status != "VALID":
            raise DataConflictError("导入验证结果不存在或已过期")
        if validation.counts.invalid or validation.counts.duplicate:
            raise DataValidationError("存在无效或重复记录，不能提交")
        artifact_port = SQLAlchemyImportArtifactPort(session, self.artifact_store)
        source = artifact_port.read_input(project_id, value.artifact_id)
        report_row = session.scalar(select(Artifact).where(
            Artifact.id == validation.report_artifact_id,
            Artifact.project_id == project_id,
        )) if validation.report_artifact_id else None
        if (
            report_row is None or report_row.status != "AVAILABLE"
            or report_row.kind != "export_report"
            or report_row.scan_status != "NOT_CONFIGURED"
            or (report_row.metadata_json or {}).get("generation") != "data_import_validation_report_v1"
            or (report_row.metadata_json or {}).get("source_ref") != {"type": "data_import_validation", "id": validation.id}
        ):
            raise DataUnavailableError("导入验证报告不可用")
        report_bytes = self.artifact_store.read(report_row.storage_key)
        if len(report_bytes) != report_row.size_bytes or hashlib.sha256(report_bytes).hexdigest() != report_row.sha256:
            raise DataValidationError("导入验证报告哈希不匹配")
        try:
            report = CsvImportValidationReport.model_validate_json(report_bytes)
        except (TypeError, ValueError):
            raise DataValidationError("导入验证报告格式无效") from None
        if (
            report.import_id != import_id or report.project_id != project_id
            or report.revision != value.revision or report.config_hash != value.config_hash
            or report.source_artifact_id != value.artifact_id or report.source_sha256 != source.sha256
            or report.status != "VALID" or report.domain_id != value.domain_id
            or report.domain_schema_version != value.schema_version
        ):
            raise DataConflictError("导入验证绑定已过期或与输入制品不一致")
        domain = SQLAlchemyDataDomainRegistry(session).get(value.domain_id, value.schema_version)
        if domain is None:
            raise DataUnavailableError("受信数据域定义不可用")
        profile = self.profile_resolver(value.domain_id, value.schema_version) if self.profile_resolver is not None else None
        if (
            profile is None or domain.deduplication_policy != "reject"
            or "append" not in domain.supported_import_modes
        ):
            raise DataUnavailableError("数据域没有可用的受信导入profile")
        from .import_worker import _parser_binding_hash
        parser_hash, profile_hash, domain_hash = _parser_binding_hash(profile, domain)
        latest_parent = session.scalar(select(DataVersionRow).where(
            DataVersionRow.project_id == project_id,
            DataVersionRow.domain_id == value.domain_id,
            DataVersionRow.status == "READY",
        ).order_by(DataVersionRow.version.desc()).limit(1))
        parent_version_id = latest_parent.id if latest_parent is not None else None
        parent_version_sha256 = latest_parent.sha256 if latest_parent is not None else None
        frozen_parent = (value.request_snapshot or {}).get("validation_parent_version_id")
        if frozen_parent != parent_version_id:
            raise DataConflictError("数据域当前版本已变化，需重新校验并确认")
        frozen = value.request_snapshot or {}
        validation_binding = frozen.get("validation_binding")
        if not isinstance(validation_binding, dict) or (
            validation_binding.get("source_sha256") != source.sha256
            or validation_binding.get("parser_hash") != validation.parser_hash
            or validation_binding.get("parent_version_id") != parent_version_id
            or validation_binding.get("parent_version_sha256") != parent_version_sha256
            or validation_binding.get("revision") != value.revision
            or validation_binding.get("config_hash") != value.config_hash
            or validation_binding.get("profile_id") != profile.profile_id
            or validation_binding.get("profile_hash") != profile_hash
            or validation_binding.get("domain_hash") != domain_hash
            or validation_binding.get("parser_hash") != parser_hash
            or validation.parser_hash != parser_hash
            or validation_binding.get("report_sha256") != report_row.sha256
        ):
            raise DataConflictError("冻结profile、解析器或父版本身份与验证结果不一致")
        binding = {
            "project_id": project_id,
            "import_id": import_id,
            "revision": value.revision,
            "config_hash": value.config_hash,
            "domain_id": value.domain_id,
            "schema_version": value.schema_version,
            "source_artifact_id": value.artifact_id,
            "source_sha256": source.sha256,
            "validation_id": validation.id,
            "report_artifact_id": validation.report_artifact_id,
            "report_sha256": report_row.sha256,
            "parser_revision": validation.parser_revision,
            "parser_hash": validation.parser_hash,
            "parent_version_id": parent_version_id,
            "parent_version_sha256": parent_version_sha256,
            "profile_id": validation_binding.get("profile_id"),
            "profile_hash": validation_binding.get("profile_hash"),
            "domain_hash": validation_binding.get("domain_hash"),
        }
        return value, validation, report, _digest(binding), source.sha256, parent_version_id

    def resolve_target(self, ctx: OperationContext, resource_ref: ResourceRef, action: str) -> TargetSnapshot:
        if resource_ref.type != "data_import" or action not in {"data_import.commit", "job.retry"}:
            raise DataValidationError("不支持的数据导入确认目标")
        actor_projects = set(ctx.actor.project_ids)
        if not actor_projects:
            raise DataNotFoundError("数据导入不存在")
        with self.session_factory() as session:
            repo = SQLAlchemyDataRepository(session)
            data_import = repo.get_import_any(actor_projects, resource_ref.id)
            if data_import is None:
                raise DataNotFoundError("数据导入不存在")
            retry_job = None
            if action == "job.retry":
                self._authorize(ctx, data_import.project_id, write=True)
                retry_job = session.scalar(select(Job).where(
                    Job.id == data_import.job_id,
                    Job.project_id == data_import.project_id,
                    Job.owner_id == ctx.actor.actor_id,
                    Job.kind == "data_import_commit",
                    Job.status == "FAILED",
                    Job.retryable.is_(True),
                ).with_for_update()) if data_import.job_id else None
                if (
                    data_import.status != "FAILED" or retry_job is None
                    or retry_job.resource_ref != {"type": "data_import", "id": data_import.id}
                    or not isinstance(retry_job.error, dict)
                    or retry_job.error.get("retryable") is not True
                ):
                    raise DataConflictError("只有当前失败且可重试的同一数据导入提交任务可以确认重试")
            else:
                self._authorize(ctx, data_import.project_id, write=False)
            data_import, validation, report, binding_hash, source_sha, parent_version_id = self._binding(
                session, data_import.project_id, resource_ref.id, allow_failed=action == "job.retry",
            )
            if retry_job is not None:
                snapshot = data_import.request_snapshot or {}
                frozen = snapshot.get("commit_binding")
                validation_binding = snapshot.get("validation_binding")
                expected = {
                    "job_id": retry_job.id,
                    "revision": data_import.revision,
                    "config_hash": data_import.config_hash,
                    "source_sha256": source_sha,
                    "validation_id": validation.id,
                    "report_artifact_id": validation.report_artifact_id,
                    "report_sha256": validation_binding.get("report_sha256") if isinstance(validation_binding, dict) else None,
                    "binding_hash": binding_hash,
                    "parent_version_id": parent_version_id,
                    "parent_version_sha256": validation_binding.get("parent_version_sha256") if isinstance(validation_binding, dict) else None,
                    "profile_id": validation_binding.get("profile_id") if isinstance(validation_binding, dict) else None,
                    "profile_hash": validation_binding.get("profile_hash") if isinstance(validation_binding, dict) else None,
                    "domain_hash": validation_binding.get("domain_hash") if isinstance(validation_binding, dict) else None,
                    "parser_hash": validation.parser_hash,
                }
                if not isinstance(frozen, dict) or any(frozen.get(key) != value for key, value in expected.items()):
                    raise DataConflictError("失败任务的冻结输入、解析器或父版本已变化，不能重试原任务")
                retry_config_hash = _digest({
                    "commit_binding_hash": binding_hash,
                    "job_id": retry_job.id,
                    "attempt": int(retry_job.attempt),
                    "etag": retry_job.etag,
                    "revision": data_import.revision,
                })
                return TargetSnapshot(
                    project_id=data_import.project_id,
                    resource_ref=resource_ref,
                    revision=data_import.revision,
                    config_hash=retry_config_hash,
                    etag=retry_job.etag,
                    policy_version=ctx.actor.policy_version,
                    summary=_confirmation_summary(report, source_sha, extra=[
                        {"label": "retry_job_id", "value": retry_job.id},
                        {"label": "attempt", "value": int(retry_job.attempt)},
                    ]),
                    dependencies=_validation_dependency(
                        validation,
                        (data_import.request_snapshot or {}).get("validation_binding", {}).get("report_sha256"),
                    ),
                    impact={
                        "operation": "retry_same_job",
                        "job_id": retry_job.id,
                        "attempt": int(retry_job.attempt),
                        "domain_id": data_import.domain_id,
                        "source_sha256": source_sha,
                        "parent_version_id": parent_version_id,
                    },
                    can_confirm=True,
                )
            return TargetSnapshot(
                project_id=data_import.project_id,
                resource_ref=resource_ref,
                revision=data_import.revision,
                config_hash=binding_hash,
                etag=data_import.etag,
                policy_version=ctx.actor.policy_version,
                summary=_confirmation_summary(report, source_sha),
                dependencies=_validation_dependency(
                    validation,
                    (data_import.request_snapshot or {}).get("validation_binding", {}).get("report_sha256"),
                ),
                impact={"domain_id": data_import.domain_id, "schema_version": data_import.schema_version, "parent_version_id": parent_version_id},
                can_confirm=True,
            )

    def submit_validation(self, ctx: OperationContext, project_id: str, import_id: str, *, if_match: str) -> AsyncAccepted:
        self._authorize(ctx, project_id, write=True)
        with self.session_factory() as session:
            with session.begin():
                session.scalar(select(Project).where(Project.id == project_id).with_for_update())
                observed = session.scalar(select(DataImportRow).where(
                    DataImportRow.id == import_id, DataImportRow.project_id == project_id
                ))
                if observed is None:
                    raise DataNotFoundError("数据导入不存在")
                if observed.etag != if_match:
                    raise DataConflictError("数据导入 ETag 已变化")
                observed_status, observed_job_id = observed.status, observed.job_id
                failed_job = None
                if observed.status == "FAILED":
                    failed_job = session.scalar(select(Job).where(
                        Job.id == observed.job_id,
                        Job.project_id == project_id,
                    ).with_for_update().execution_options(populate_existing=True)) if observed.job_id else None
                    if (
                        failed_job is None
                        or failed_job.status != "FAILED"
                        or failed_job.retryable is not False
                        or not isinstance(failed_job.error, dict)
                        or failed_job.error.get("retryable") is not False
                        or failed_job.kind not in {"data_import_commit", "data_import_validation"}
                        or failed_job.resource_ref != {"type": "data_import", "id": import_id}
                    ):
                        raise DataConflictError("只有当前不可重试的终态数据导入任务可以重新校验")
                    latest_attempt = session.scalar(select(JobAttempt).where(
                        JobAttempt.job_id == failed_job.id,
                        JobAttempt.attempt == failed_job.attempt,
                    ).with_for_update().execution_options(populate_existing=True))
                    if latest_attempt is None or latest_attempt.status != "FAILED":
                        raise DataConflictError("数据导入任务仍有活动或不完整的执行尝试")
                    published_version = session.scalar(select(DataVersionRow.id).where(
                        DataVersionRow.project_id == project_id,
                        DataVersionRow.source_import_id == import_id,
                    ).limit(1))
                    if observed.new_data_version_id is not None or published_version is not None:
                        raise DataConflictError("已发布数据版本的导入不能重新校验")
                elif observed.status not in {"DRAFT", "INVALID", "READY"}:
                    raise DataConflictError("当前状态不可重新验证导入")

                row = session.scalar(select(DataImportRow).where(
                    DataImportRow.id == import_id, DataImportRow.project_id == project_id
                ).with_for_update().execution_options(populate_existing=True))
                if row is None or row.status != observed_status or row.job_id != observed_job_id:
                    raise DataConflictError("数据导入状态在重新校验期间已变化")
                if row.etag != if_match:
                    raise DataConflictError("数据导入 ETag 已变化")
                domain = SQLAlchemyDataDomainRegistry(session).get(row.domain_id, row.schema_version)
                if domain is None:
                    raise DataUnavailableError("受信数据域定义不可用")
                if self.profile_resolver is None or self.profile_resolver(row.domain_id, row.schema_version) is None:
                    raise DataUnavailableError("数据域没有可用的受信导入profile")
                if row.import_mode != "append" or domain.deduplication_policy != "reject" or "append" not in domain.supported_import_modes:
                    raise DataValidationError("当前版本仅支持 append + reject")
                parent = session.scalar(select(DataVersionRow).where(
                    DataVersionRow.project_id == project_id,
                    DataVersionRow.domain_id == row.domain_id,
                    DataVersionRow.status == "READY",
                ).order_by(DataVersionRow.version.desc()).limit(1).with_for_update())
                current_snapshot = dict(row.request_snapshot or {})
                current_snapshot.pop("commit_binding", None)
                current_snapshot.pop("validation_binding", None)
                current_snapshot["validation_parent_version_id"] = parent.id if parent else None
                current_snapshot["validation_parent_version_sha256"] = parent.sha256 if parent else None
                resource = ResourceRef(type="data_import", id=import_id)
                task_repo = SQLAlchemyTaskRepository(lambda: session)
                kwargs = {} if self.id_factory is None else {"job_id": self.id_factory("job")}
                job = add_job_rows(
                    task_repo, session, project_id=project_id, owner_id=ctx.actor.actor_id,
                    kind="data_import_validation", resource_ref=resource,
                    cancelable=True, retryable=False, resume_mode="restart", deadline_at=None,
                    **kwargs,
                )
                row.status = "VALIDATING"
                row.job_id = job.id
                row.validation_ref = None
                row.new_data_version_id = None
                row.request_snapshot = current_snapshot
                row.updated_at = _now()
                session.flush()
                return AsyncAccepted(
                    id=import_id, status="QUEUED", job_id=job.id, resource_ref=resource,
                )

    def submit_commit(self, ctx: OperationContext, project_id: str, import_id: str, confirmation_id: str) -> AsyncAccepted:
        self._authorize(ctx, project_id, write=True)
        with self.session_factory() as session:
            with session.begin():
                session.scalar(select(Project).where(Project.id == project_id).with_for_update())
                row = session.scalar(select(DataImportRow).where(
                    DataImportRow.id == import_id, DataImportRow.project_id == project_id
                ).with_for_update())
                if row is None:
                    raise DataNotFoundError("数据导入不存在")
                data_import, validation, report, binding_hash, source_sha, parent_version_id = self._binding(session, project_id, import_id)
                target = TargetSnapshot(
                    project_id=project_id,
                    resource_ref=ResourceRef(type="data_import", id=import_id),
                    revision=data_import.revision,
                    config_hash=binding_hash,
                    etag=data_import.etag,
                    policy_version=ctx.actor.policy_version,
                    summary=_confirmation_summary(report, source_sha),
                    dependencies=_validation_dependency(
                        validation,
                        (data_import.request_snapshot or {}).get("validation_binding", {}).get("report_sha256"),
                    ),
                    impact={"domain_id": data_import.domain_id, "schema_version": data_import.schema_version, "parent_version_id": parent_version_id},
                )
                self.confirmation_service.consume_for_mutation(
                    ctx, confirmation_id, target=target, action="data_import.commit",
                    result_ref={"type": "data_import", "id": import_id}, session=session,
                )
                task_repo = SQLAlchemyTaskRepository(lambda: session)
                kwargs = {} if self.id_factory is None else {"job_id": self.id_factory("job")}
                job = add_job_rows(
                    task_repo, session, project_id=project_id, owner_id=ctx.actor.actor_id,
                    kind="data_import_commit", resource_ref=target.resource_ref,
                    cancelable=True, retryable=True, resume_mode="restart", deadline_at=None,
                    **kwargs,
                )
                snapshot = dict(data_import.request_snapshot or {})
                snapshot["commit_binding"] = {
                    "revision": data_import.revision,
                    "config_hash": data_import.config_hash,
                    "source_sha256": source_sha,
                    "validation_id": validation.id,
                    "report_artifact_id": validation.report_artifact_id,
                    "report_sha256": (data_import.request_snapshot or {}).get("validation_binding", {}).get("report_sha256"),
                    "binding_hash": binding_hash,
                    "parent_version_id": parent_version_id,
                    "parent_version_sha256": (data_import.request_snapshot or {}).get("validation_parent_version_sha256"),
                    "profile_id": (data_import.request_snapshot or {}).get("validation_binding", {}).get("profile_id"),
                    "profile_hash": (data_import.request_snapshot or {}).get("validation_binding", {}).get("profile_hash"),
                    "domain_hash": (data_import.request_snapshot or {}).get("validation_binding", {}).get("domain_hash"),
                    "parser_hash": validation.parser_hash,
                    "job_id": job.id,
                    "confirmation_id": confirmation_id,
                    "origin_actor_id": ctx.actor.actor_id,
                    "origin_auth_method": ctx.actor.auth_method,
                    "origin_interaction": ctx.interaction,
                    "origin_request_id": ctx.request_id,
                    "origin_agent_run_id": ctx.agent_run_id,
                }
                updated = data_import.model_copy(update={"status": "COMMITTING", "job_id": job.id, "request_snapshot": snapshot, "updated_at": _now()})
                SQLAlchemyDataRepository(session).save_import(updated)
                _add_commit_audit(
                    session, ctx=ctx, project_id=project_id, resource_ref=target.resource_ref,
                    confirmation_id=confirmation_id, binding_hash=binding_hash, job_id=job.id,
                    before_summary={"status": data_import.status, "revision": data_import.revision},
                    after_summary={
                        "phase": "accepted", "status": "COMMITTING", "revision": data_import.revision,
                        "config_hash": data_import.config_hash, "binding_hash": binding_hash,
                        "validation_id": validation.id, "report_artifact_id": validation.report_artifact_id,
                        "report_sha256": (data_import.request_snapshot or {}).get("validation_binding", {}).get("report_sha256"),
                        "source_artifact_id": data_import.artifact_id, "source_sha256": source_sha,
                        "parent_version_id": parent_version_id,
                    },
                )
                return AsyncAccepted(
                    id=import_id, status="QUEUED", job_id=job.id, resource_ref=target.resource_ref,
                )

    def accept_commit(self, ctx: OperationContext, project_id: str, import_id: str, confirmation_id: str) -> AsyncAccepted:
        """Naming alias used by task-core assembly code."""
        return self.submit_commit(ctx, project_id, import_id, confirmation_id)


__all__ = ["SQLAlchemyImportWorkflow"]
