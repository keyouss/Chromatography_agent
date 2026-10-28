"""Application service for controlled uploads and artifact access."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
from typing import Any, Protocol, get_args
from uuid import uuid4

from chrom_platform.core.dto import ActorContext, ResourceRef, WarningDTO

from .models import Artifact
from .schemas import (
    ArtifactAccepted,
    ArtifactDeleteAccepted,
    ArtifactKind,
    ArtifactView,
    ArchiveRequest,
    UploadCreate,
    UploadReceipt,
    UploadView,
)
from .storage import (
    ArtifactStore,
    StorageContentConflict,
    StorageContentInvalid,
    StorageNotConfigured,
)


DEFAULT_MEDIA_TYPES = {
    "text/plain",
    "text/csv",
    "text/markdown",
    "application/pdf",
    "application/zip",
    "application/json",
    "application/octet-stream",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "chemical/x-mdl-molfile",
    "chemical/x-mdl-sdfile",
    "chemical/x-cdxml",
    "application/x-cdx",
}
_UNSET = object()
_PUBLIC_ARTIFACT_KINDS = frozenset(get_args(ArtifactKind))


class ArtifactServiceError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int = 409, *, retryable: bool = False) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        super().__init__(message)


class ArtifactRepository(Protocol):
    def create(self, row: Artifact) -> Artifact: ...

    def get(self, artifact_id: str, *, for_update: bool = False) -> Artifact | None: ...

    def get_by_upload_id(self, upload_id: str, *, for_update: bool = False) -> Artifact | None: ...

    def save(self, row: Artifact) -> Artifact: ...


class ExportArtifactReadPolicy(Protocol):
    """Resolve current source-resource access for server-generated exports."""

    def can_read_export(self, artifact: Artifact, actor: ActorContext) -> bool: ...

    def validate_export_body(self, artifact: Artifact, actor: ActorContext, content: bytes) -> bool: ...


class ArtifactScanner(Protocol):
    def scan(self, *, storage_key: str, media_type: str, kind: str) -> "ScanResult": ...


@dataclass(frozen=True)
class ScanResult:
    status: str
    warning: dict[str, str] | None = None


@dataclass(frozen=True)
class JobSubmission:
    job_id: str
    resource_ref: ResourceRef


@dataclass(frozen=True)
class UploadContentAdmission:
    """Server-side limits obtained before the request body is consumed."""

    upload_id: str
    artifact_id: str
    declared_size: int
    max_size_bytes: int


class ArtifactJobPort(Protocol):
    """Persist Job + Outbox in the caller's current transaction."""

    @property
    def transactional_outbox(self) -> bool: ...

    def enqueue_validation(self, *, artifact_id: str, project_id: str, kind: str, owner_id: str) -> JobSubmission: ...


class ConfirmationPort(Protocol):
    def consume(
        self,
        confirmation_id: str,
        *,
        actor: ActorContext,
        project_id: str,
        resource_ref: ResourceRef,
        action: str,
    ) -> None: ...


class AuditPort(Protocol):
    def record(
        self,
        *,
        actor: ActorContext,
        action: str,
        project_id: str,
        resource_id: str,
        result: str,
        confirmation_id: str | None = None,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> None: ...


class DeletionQueue(Protocol):
    """Queue a DB-backed cleanup job; assembly must bind Job + Outbox.

    This port must not be implemented by directly pushing an untracked Redis
    message.  The later assembly layer owns the database job/outbox atomicity.
    """

    @property
    def transactional_outbox(self) -> bool: ...

    def enqueue(self, *, artifact_id: str, storage_key: str) -> str: ...


class ArtifactService:
    def __init__(
        self,
        repository: ArtifactRepository | None,
        store: ArtifactStore | None,
        *,
        scanner: ArtifactScanner | None = None,
        confirmation: ConfirmationPort | None = None,
        audit: AuditPort | None = None,
        deletion_queue: DeletionQueue | None = None,
        jobs: ArtifactJobPort | None = None,
        allowed_media_types: set[str] | None = None,
        expires_after: timedelta = timedelta(hours=1),
        export_read_policy: ExportArtifactReadPolicy | None = None,
    ) -> None:
        self.repository = repository
        self.store = store
        self.scanner = scanner
        self.confirmation = confirmation
        self.audit = audit
        self.deletion_queue = deletion_queue
        self.jobs = jobs
        self.allowed_media_types = allowed_media_types or set(DEFAULT_MEDIA_TYPES)
        self.expires_after = expires_after
        self.export_read_policy = export_read_policy

    @property
    def configured(self) -> bool:
        return self.repository is not None and self.store is not None and self.store.configured

    def _require_repository(self) -> ArtifactRepository:
        if self.repository is None:
            raise ArtifactServiceError("DEPENDENCY_UNAVAILABLE", "制品持久化存储未配置", 503, retryable=True)
        return self.repository

    def _require_store(self) -> ArtifactStore:
        if self.store is None or not self.store.configured:
            raise ArtifactServiceError("DEPENDENCY_UNAVAILABLE", "制品存储未配置", 503, retryable=True)
        return self.store

    @staticmethod
    def _authorize(actor: ActorContext, project_id: str, permission: str) -> None:
        if not actor.can_access_project(project_id):
            raise ArtifactServiceError("RESOURCE_NOT_FOUND", "制品不存在或当前主体不可见", 404)
        if not actor.can(permission):
            raise ArtifactServiceError("FORBIDDEN", "当前主体没有制品权限", 403)

    def authorize_project(self, project_id: str, actor: ActorContext, permission: str = "artifact:write") -> None:
        """Check current authorization before an idempotency replay lookup."""

        self._authorize(actor, project_id, permission)

    def with_repository(self, repository: ArtifactRepository, *, jobs: ArtifactJobPort | None | object = _UNSET) -> "ArtifactService":
        """Use the repository bound to the current caller-owned transaction."""

        return ArtifactService(
            repository,
            self.store,
            scanner=self.scanner,
            confirmation=self.confirmation,
            audit=self.audit,
            deletion_queue=self.deletion_queue,
            jobs=self.jobs if jobs is _UNSET else jobs,  # type: ignore[arg-type]
            allowed_media_types=self.allowed_media_types,
            expires_after=self.expires_after,
            export_read_policy=self.export_read_policy,
        )

    def with_export_read_policy(self, policy: ExportArtifactReadPolicy) -> "ArtifactService":
        """Bind an optional source-resource policy without importing its module."""

        return ArtifactService(
            self.repository,
            self.store,
            scanner=self.scanner,
            confirmation=self.confirmation,
            audit=self.audit,
            deletion_queue=self.deletion_queue,
            jobs=self.jobs,
            allowed_media_types=self.allowed_media_types,
            expires_after=self.expires_after,
            export_read_policy=policy,
        )

    def _authorize_source_read(self, row: Artifact, actor: ActorContext) -> None:
        if row.kind != "export_report":
            return
        policy = self.export_read_policy
        if policy is None or not policy.can_read_export(row, actor):
            raise ArtifactServiceError("RESOURCE_NOT_FOUND", "制品不存在或当前主体不可见", 404)

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}_{uuid4().hex}"

    def create_upload(self, payload: UploadCreate, actor: ActorContext) -> UploadView:
        store = self._require_store()
        repository = self._require_repository()
        self._authorize(actor, payload.project_id, "artifact:write")
        if payload.media_type not in self.allowed_media_types:
            raise ArtifactServiceError("MEDIA_TYPE_NOT_ALLOWED", "文件媒体类型不在允许列表", 422)
        if payload.size_bytes > store.max_size_bytes:
            raise ArtifactServiceError("FILE_TOO_LARGE", "文件大小超出限制", 413)
        upload_id = self._new_id("upl")
        artifact_id = self._new_id("art")
        now = datetime.now(timezone.utc)
        metadata = {
            "upload_id": upload_id,
            "file_name": payload.file_name,
            "expected_size": payload.size_bytes,
            "expected_sha256": payload.expected_sha256,
            "received_bytes": 0,
            "warnings": [],
        }
        repository.create(
            Artifact(
                id=artifact_id,
                project_id=payload.project_id,
                kind=payload.kind,
                title=payload.file_name,
                media_type=payload.media_type,
                size_bytes=payload.size_bytes,
                sha256=payload.expected_sha256,
                storage_key=store.key_for(artifact_id),
                status="CREATED",
                scan_status="NOT_CONFIGURED",
                metadata_json=metadata,
                created_by=actor.actor_id,
                created_at=now,
                updated_at=now,
            )
        )
        return self._upload_view(upload_id, artifact_id, "CREATED", payload.size_bytes, now + self.expires_after, store)

    def receive_content(self, upload_id: str, content: bytes, actor: ActorContext) -> UploadReceipt:
        store = self._require_store()
        repository = self._require_repository()
        row = repository.get_by_upload_id(upload_id, for_update=True)
        if row is None:
            raise ArtifactServiceError("RESOURCE_NOT_FOUND", "上传会话不存在", 404)
        self._authorize(actor, row.project_id, "artifact:write")
        if datetime.now(timezone.utc) >= row.created_at + self.expires_after:
            raise ArtifactServiceError("STATE_CONFLICT", "上传会话已过期", 409)
        metadata = dict(row.metadata_json or {})
        expected_size = int(metadata.get("expected_size", row.size_bytes))
        expected_sha256 = metadata.get("expected_sha256")
        if row.status in {"RECEIVED", "VALIDATING", "AVAILABLE", "QUARANTINED", "ARCHIVED", "DELETING", "DELETED"}:
            if row.status == "RECEIVED" and row.sha256:
                digest = row.sha256
                if len(content) == row.size_bytes and __import__("hashlib").sha256(content).hexdigest() == digest:
                    return UploadReceipt(upload_id=upload_id, artifact_id=row.id, received_bytes=row.size_bytes, sha256=digest, received_at=row.updated_at)
            raise ArtifactServiceError("STATE_CONFLICT", "上传内容已接收且不可覆盖", 409)
        try:
            _, received, digest = store.put(
                row.id,
                content,
                expected_size=expected_size,
                expected_sha256=expected_sha256,
            )
        except StorageContentConflict as exc:
            raise ArtifactServiceError("CONTENT_CONFLICT", "上传内容与已接收内容不一致", 409) from exc
        except StorageContentInvalid as exc:
            row.status = "FAILED"
            row.metadata_json = {**metadata, "received_bytes": 0, "error": "UPLOAD_CONTENT_INVALID"}
            repository.save(row)
            raise ArtifactServiceError("UPLOAD_INVALID", "上传内容校验失败", 422) from exc
        except StorageNotConfigured as exc:
            raise ArtifactServiceError("DEPENDENCY_UNAVAILABLE", "制品存储未配置", 503, retryable=True) from exc
        row.sha256 = digest
        row.size_bytes = received
        row.status = "RECEIVED"
        row.scan_status = "NOT_CONFIGURED"
        row.metadata_json = {**metadata, "received_bytes": received}
        repository.save(row)
        return UploadReceipt(
            upload_id=upload_id,
            artifact_id=row.id,
            received_bytes=received,
            sha256=digest,
            received_at=row.updated_at,
        )

    def prepare_content_upload(self, upload_id: str, actor: ActorContext) -> UploadContentAdmission:
        """Authorize and bound an upload before its ASGI body is read."""

        store = self._require_store()
        repository = self._require_repository()
        row = repository.get_by_upload_id(upload_id)
        if row is None:
            raise ArtifactServiceError("RESOURCE_NOT_FOUND", "上传会话不存在", 404)
        self._authorize(actor, row.project_id, "artifact:write")
        if row.status not in {"CREATED", "RECEIVED"}:
            raise ArtifactServiceError("STATE_CONFLICT", "上传会话当前不可接收内容", 409)
        expires_at = row.created_at + self.expires_after
        if datetime.now(timezone.utc) >= expires_at:
            raise ArtifactServiceError("STATE_CONFLICT", "上传会话已过期", 409)
        try:
            declared_size = int((row.metadata_json or {}).get("expected_size", row.size_bytes))
        except (TypeError, ValueError) as exc:
            raise ArtifactServiceError("STATE_CONFLICT", "上传会话大小声明无效", 409) from exc
        if declared_size <= 0 or declared_size > store.max_size_bytes:
            raise ArtifactServiceError("FILE_TOO_LARGE", "文件大小超出限制", 413)
        return UploadContentAdmission(
            upload_id=upload_id,
            artifact_id=row.id,
            declared_size=declared_size,
            max_size_bytes=store.max_size_bytes,
        )

    def get_upload(self, upload_id: str, actor: ActorContext) -> UploadView:
        store = self._require_store()
        repository = self._require_repository()
        row = repository.get_by_upload_id(upload_id)
        if row is None:
            raise ArtifactServiceError("RESOURCE_NOT_FOUND", "上传会话不存在", 404)
        self._authorize(actor, row.project_id, "artifact:read")
        metadata = row.metadata_json or {}
        if row.status == "CREATED":
            upload_status = "CREATED"
        elif row.status == "RECEIVED":
            upload_status = "RECEIVED"
        elif row.status == "EXPIRED":
            upload_status = "EXPIRED"
        elif row.status == "FAILED":
            upload_status = "FAILED"
        elif row.status in {"VALIDATING", "AVAILABLE", "QUARANTINED", "ARCHIVED", "DELETING", "DELETED"}:
            upload_status = "COMPLETED"
        else:  # pragma: no cover - defensive handling for a corrupt row
            upload_status = "FAILED"
        return self._upload_view(
            upload_id,
            row.id,
            upload_status,
            int(metadata.get("expected_size", row.size_bytes)),
            row.created_at + self.expires_after,
            store,
            received_bytes=int(metadata.get("received_bytes", 0)),
        )

    def project_id_for_upload(self, upload_id: str, actor: ActorContext) -> str:
        repository = self._require_repository()
        row = repository.get_by_upload_id(upload_id)
        if row is None:
            raise ArtifactServiceError("RESOURCE_NOT_FOUND", "上传会话不存在", 404)
        self._authorize(actor, row.project_id, "artifact:write")
        return row.project_id

    def project_id_for_artifact(self, artifact_id: str, actor: ActorContext, permission: str = "artifact:read") -> str:
        repository = self._require_repository()
        row = repository.get(artifact_id)
        if row is None:
            raise ArtifactServiceError("RESOURCE_NOT_FOUND", "制品不存在或当前主体不可见", 404)
        self._authorize(actor, row.project_id, permission)
        return row.project_id

    def complete_upload(self, upload_id: str, actor: ActorContext) -> ArtifactAccepted:
        repository = self._require_repository()
        self._require_store()
        row = repository.get_by_upload_id(upload_id, for_update=True)
        if row is None:
            raise ArtifactServiceError("RESOURCE_NOT_FOUND", "上传会话不存在", 404)
        self._authorize(actor, row.project_id, "artifact:write")
        if row.status in {"VALIDATING", "AVAILABLE"}:
            job_id = str((row.metadata_json or {}).get("job_id", ""))
            if job_id:
                return self._accepted(row, JobSubmission(job_id=job_id, resource_ref=ResourceRef(type="artifact", id=row.id)))
            raise ArtifactServiceError("STATE_CONFLICT", "制品任务记录不完整", 409)
        if row.status != "RECEIVED":
            raise ArtifactServiceError("STATE_CONFLICT", "上传会话尚未接收完整内容", 409)
        if self.jobs is None or not getattr(self.jobs, "transactional_outbox", False):
            raise ArtifactServiceError("DEPENDENCY_UNAVAILABLE", "制品任务事务端口未配置", 503, retryable=True)
        row.status = "VALIDATING"
        row.scan_status = "PENDING"
        try:
            job = self.jobs.enqueue_validation(
                artifact_id=row.id, project_id=row.project_id, kind=row.kind, owner_id=actor.actor_id,
            )
        except Exception as exc:
            raise ArtifactServiceError("DEPENDENCY_UNAVAILABLE", "制品任务暂不可受理", 503, retryable=True) from exc
        if (
            not isinstance(job, JobSubmission)
            or not isinstance(job.job_id, str)
            or not job.job_id.strip()
            or not isinstance(job.resource_ref, ResourceRef)
            or job.resource_ref.type != "artifact"
            or job.resource_ref.id != row.id
        ):
            raise ArtifactServiceError("DEPENDENCY_UNAVAILABLE", "制品任务端口返回无效受理结果", 503, retryable=True)
        row.metadata_json = {**(row.metadata_json or {}), "job_id": job.job_id}
        repository.save(row)
        return self._accepted(row, job)

    def get_artifact(self, artifact_id: str, actor: ActorContext) -> ArtifactView:
        repository = self._require_repository()
        row = repository.get(artifact_id)
        if row is None:
            raise ArtifactServiceError("RESOURCE_NOT_FOUND", "制品不存在或当前主体不可见", 404)
        self._authorize(actor, row.project_id, "artifact:read")
        self._authorize_source_read(row, actor)
        if row.kind not in _PUBLIC_ARTIFACT_KINDS or self._is_internal_dataset_snapshot(row):
            raise ArtifactServiceError("RESOURCE_NOT_FOUND", "制品不存在或当前主体不可见", 404)
        downloadable = row.status == "AVAILABLE" and row.scan_status == "PASSED"
        if row.kind == "export_report" and row.status == "AVAILABLE":
            downloadable = self._export_report_is_valid(row, actor)
        return self._view(row, download_allowed=downloadable)

    def download(self, artifact_id: str, actor: ActorContext) -> tuple[ArtifactView, bytes]:
        row, content = self._read_content(artifact_id, actor, public_only=True)
        return self._view(row, download_allowed=True if row.kind == "export_report" else None), content

    def download_content(self, artifact_id: str, actor: ActorContext) -> tuple[Artifact, bytes]:
        """Return verified internal content to trusted Python services only."""

        return self._read_content(artifact_id, actor, public_only=False)

    def _read_content(self, artifact_id: str, actor: ActorContext, *, public_only: bool) -> tuple[Artifact, bytes]:
        store = self._require_store()
        repository = self._require_repository()
        row = repository.get(artifact_id)
        if row is None:
            raise ArtifactServiceError("RESOURCE_NOT_FOUND", "制品不存在或当前主体不可见", 404)
        self._authorize(actor, row.project_id, "artifact:read")
        self._authorize_source_read(row, actor)
        if public_only and (row.kind not in _PUBLIC_ARTIFACT_KINDS or self._is_internal_dataset_snapshot(row)):
            raise ArtifactServiceError("RESOURCE_NOT_FOUND", "制品不存在或当前主体不可见", 404)
        if row.kind in {"context_snapshot", "diagnostic_bundle"} and not actor.can("trace:content:read"):
            raise ArtifactServiceError("FORBIDDEN", "敏感制品需要额外内容权限", 403)
        if row.status == "DELETED":
            raise ArtifactServiceError("ARTIFACT_DELETED", "制品内容已删除", 410)
        if row.status != "AVAILABLE":
            raise ArtifactServiceError("ARTIFACT_NOT_AVAILABLE", "制品当前不可下载", 409)
        try:
            content = store.read(row.storage_key)
        except Exception as exc:
            raise ArtifactServiceError("DEPENDENCY_UNAVAILABLE", "制品内容暂不可用", 503, retryable=True) from exc
        if row.kind == "export_report":
            if not self._valid_export_report_body(row, actor, content):
                raise ArtifactServiceError("RESOURCE_NOT_FOUND", "制品不存在或当前主体不可见", 404)
        return row, content

    def _valid_export_report_body(self, row: Artifact, actor: ActorContext, content: bytes) -> bool:
        if row.scan_status != "PASSED" and not self._is_unscanned_import_validation_report(row):
            return False
        policy = self.export_read_policy
        validate_body = getattr(policy, "validate_export_body", None)
        if (
            len(content) != row.size_bytes
            or not row.sha256
            or hashlib.sha256(content).hexdigest() != row.sha256
            or not callable(validate_body)
        ):
            return False
        try:
            return bool(validate_body(row, actor, content))
        except Exception:
            return False

    @staticmethod
    def _is_unscanned_import_validation_report(row: Artifact) -> bool:
        metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
        source = metadata.get("source_ref")
        return bool(
            row.scan_status == "NOT_CONFIGURED"
            and metadata.get("generation") == "data_import_validation_report_v1"
            and isinstance(source, dict)
            and source.get("type") == "data_import_validation"
            and isinstance(source.get("id"), str)
            and source.get("id")
        )

    @staticmethod
    def _is_internal_dataset_snapshot(row: Artifact) -> bool:
        metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
        source = metadata.get("source_ref")
        return bool(
            row.kind == "dataset_package"
            and metadata.get("generation") == "data_import_snapshot_v1"
            and isinstance(source, dict)
            and source.get("type") == "data_version"
        )

    def _export_report_is_valid(self, row: Artifact, actor: ActorContext) -> bool:
        """Use the download policy and body checks for the browser view flag."""
        store = self.store
        if store is None:
            return False
        try:
            content = store.read(row.storage_key)
        except Exception:
            return False
        return self._valid_export_report_body(row, actor, content)

    def archive(self, artifact_id: str, request: ArchiveRequest, actor: ActorContext) -> ArtifactView:
        repository = self._require_repository()
        row = repository.get(artifact_id, for_update=True)
        if row is None:
            raise ArtifactServiceError("RESOURCE_NOT_FOUND", "制品不存在或当前主体不可见", 404)
        self._authorize(actor, row.project_id, "artifact:read")
        if row.status in {"ARCHIVED", "DELETED", "DELETING"}:
            return self._view(row)
        self._confirm_and_audit(
            request.confirmation_id,
            actor=actor,
            row=row,
            action="resource.archive",
            target="ARCHIVED",
        )
        before = {"status": row.status}
        row.status = "ARCHIVED"
        repository.save(row)
        self._audit(actor, row, "resource.archive", "SUCCEEDED", request.confirmation_id, before, {"status": row.status})
        return self._view(row)

    def delete(self, artifact_id: str, confirmation_id: str, actor: ActorContext) -> ArtifactDeleteAccepted:
        repository = self._require_repository()
        row = repository.get(artifact_id, for_update=True)
        if row is None:
            raise ArtifactServiceError("RESOURCE_NOT_FOUND", "制品不存在或当前主体不可见", 404)
        self._authorize(actor, row.project_id, "artifact:delete")
        if row.status == "DELETED":
            raise ArtifactServiceError("ARTIFACT_DELETED", "制品已删除", 410)
        if row.status == "DELETING":
            job_id = str((row.metadata_json or {}).get("deletion_job_id", "")).strip()
            if not job_id:
                raise ArtifactServiceError("DEPENDENCY_UNAVAILABLE", "制品清理任务记录不完整", 503, retryable=True)
            return ArtifactDeleteAccepted(id=row.id, job_id=job_id, status="DELETING", resource_ref=ResourceRef(type="artifact", id=row.id))
        if self.deletion_queue is None:
            raise ArtifactServiceError("DEPENDENCY_UNAVAILABLE", "制品清理队列未配置", 503, retryable=True)
        if not getattr(self.deletion_queue, "transactional_outbox", False):
            raise ArtifactServiceError("DEPENDENCY_UNAVAILABLE", "制品清理事务队列未配置", 503, retryable=True)
        self._confirm_and_audit(confirmation_id, actor=actor, row=row, action="artifact.delete", target="DELETING")
        before_status = row.status
        job_id = self.deletion_queue.enqueue(artifact_id=row.id, storage_key=row.storage_key)
        if not isinstance(job_id, str) or not job_id.strip():
            raise ArtifactServiceError("DEPENDENCY_UNAVAILABLE", "制品清理任务暂不可用", 503, retryable=True)
        row.status = "DELETING"
        row.metadata_json = {**(row.metadata_json or {}), "deletion_job_id": job_id}
        repository.save(row)
        self._audit(actor, row, "artifact.delete", "SUCCEEDED", confirmation_id, {"status": before_status}, {"status": row.status})
        return ArtifactDeleteAccepted(id=row.id, job_id=job_id, status="DELETING", resource_ref=ResourceRef(type="artifact", id=row.id))

    def mark_deleted(self, artifact_id: str, *, unit_of_work: Any | None = None) -> None:
        if unit_of_work is None or getattr(unit_of_work, "artifacts", None) is None:
            raise ArtifactServiceError("DEPENDENCY_UNAVAILABLE", "制品删除必须绑定事务边界", 503, retryable=True)
        repository = unit_of_work.artifacts
        row = repository.get(artifact_id, for_update=True)
        if row is None:
            return
        row.status = "DELETED"
        repository.save(row)
        if self.store is not None and self.store.configured:
            self.store.remove(row.storage_key)

    def _confirm_and_audit(self, confirmation_id: str, *, actor: ActorContext, row: Artifact, action: str, target: str) -> None:
        if self.confirmation is None or self.audit is None:
            raise ArtifactServiceError("DEPENDENCY_UNAVAILABLE", "确认或审计服务未配置", 503, retryable=True)
        try:
            self.confirmation.consume(
                confirmation_id,
                actor=actor,
                project_id=row.project_id,
                resource_ref=ResourceRef(type="artifact", id=row.id),
                action=action,
            )
        except Exception as exc:
            raise ArtifactServiceError("CONFIRMATION_INVALID", "确认无效或已过期", 409) from exc

    def _audit(self, actor: ActorContext, row: Artifact, action: str, result: str, confirmation_id: str | None, before: dict[str, Any], after: dict[str, Any]) -> None:
        if self.audit is None:
            raise ArtifactServiceError("DEPENDENCY_UNAVAILABLE", "审计服务未配置", 503, retryable=True)
        try:
            self.audit.record(
                actor=actor,
                action=action,
                project_id=row.project_id,
                resource_id=row.id,
                result=result,
                confirmation_id=confirmation_id,
                before=before,
                after=after,
            )
        except Exception as exc:
            raise ArtifactServiceError("DEPENDENCY_UNAVAILABLE", "审计服务暂不可用", 503, retryable=True) from exc

    @staticmethod
    def _upload_view(upload_id: str, artifact_id: str, status: str, max_size: int, expires_at: datetime, store: ArtifactStore, *, received_bytes: int = 0) -> UploadView:
        return UploadView(
            id=upload_id,
            artifact_id=artifact_id,
            status=status,  # type: ignore[arg-type]
            content_url=f"/api/v1/artifacts/uploads/{upload_id}/content",
            allowed_media_types=sorted(DEFAULT_MEDIA_TYPES),
            max_size_bytes=store.max_size_bytes,
            expires_at=expires_at,
            received_bytes=received_bytes,
        )

    @staticmethod
    def _accepted(row: Artifact, job: JobSubmission) -> ArtifactAccepted:
        return ArtifactAccepted(id=row.id, artifact_id=row.id, job_id=job.job_id, status=row.status, resource_ref=ResourceRef(type="artifact", id=row.id))

    @staticmethod
    def _view(row: Artifact, *, download_allowed: bool | None = None) -> ArtifactView:
        metadata = row.metadata_json or {}
        status = row.status
        if status == "CREATED":
            status = "UPLOADING"
        return ArtifactView(
            id=row.id,
            project_id=row.project_id,
            owner_id=row.created_by,
            kind=row.kind,  # type: ignore[arg-type]
            file_name=str(metadata.get("file_name") or row.title or row.id),
            media_type=row.media_type,
            size_bytes=row.size_bytes,
            sha256=row.sha256,
            status=status,  # type: ignore[arg-type]
            scan_status=row.scan_status,  # type: ignore[arg-type]
            validation_report_ref=ResourceRef.model_validate(metadata["validation_report_ref"]) if metadata.get("validation_report_ref") else None,
            source_ref=ResourceRef.model_validate(metadata["source_ref"]) if metadata.get("source_ref") else None,
            retention_until=row.retention_until,
            created_at=row.created_at,
            updated_at=row.updated_at,
            download_allowed=(
                status == "AVAILABLE" and row.scan_status == "PASSED"
                if download_allowed is None else download_allowed
            ),
            warnings=[WarningDTO.model_validate(item) for item in metadata.get("warnings", [])],
        )


__all__ = [
    "ArtifactJobPort",
    "ArtifactScanner",
    "ArtifactService",
    "ArtifactServiceError",
    "AuditPort",
    "ConfirmationPort",
    "DEFAULT_MEDIA_TYPES",
    "DeletionQueue",
    "ScanResult",
    "JobSubmission",
    "UploadContentAdmission",
]
