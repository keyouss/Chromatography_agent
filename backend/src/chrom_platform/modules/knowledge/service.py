"""Application service for immutable knowledge indexing and retrieval."""

from __future__ import annotations

from datetime import datetime, timezone
from contextlib import ExitStack
import hashlib
import json
from typing import Any
from uuid import uuid4

from chrom_platform.core.dto import ActorContext, ResourceRef

from .errors import (
    KnowledgeConflictError,
    KnowledgeError,
    KnowledgeNotFoundError,
    KnowledgePreconditionError,
    KnowledgeUnavailableError,
    KnowledgeValidationError,
)
from .ports import (
    ArtifactPort,
    ArtifactScannerPort,
    AuditPort,
    ConfirmationPort,
    JobSubmission,
    KnowledgeJobQueue,
    KnowledgeParser,
    KnowledgeRepository,
)
from .schemas import (
    Citation,
    KnowledgeChunk,
    KnowledgeAccepted,
    KnowledgeDocument,
    KnowledgeDocumentCreate,
    KnowledgeSearch,
    KnowledgeSearchResult,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _authorize(actor: ActorContext | None, project_id: str, permission: str, *, write: bool = False) -> None:
    if actor is None:
        raise KnowledgeUnavailableError("ActorContext 未由服务端配置")
    if not actor.can_access_project(project_id):
        raise KnowledgeConflictError("无权访问当前项目", details={"status_code": 404}) if write else KnowledgeNotFoundError("资源不存在")
    if not actor.can(permission) and not actor.can("*"):
        raise KnowledgeConflictError("缺少知识中心权限", details={"status_code": 403})


def _require(value: object | None, name: str) -> object:
    if value is None:
        raise KnowledgeUnavailableError(f"{name} 未配置")
    return value


def _public_document(value: object) -> KnowledgeDocument:
    if not isinstance(value, KnowledgeDocument):
        raise KnowledgeUnavailableError("知识文档存储端口返回无效 DTO")
    if value.error is None:
        return value
    # Persisted failures may predate the public-error contract.  Keep only a
    # stable code/retry flag and replace arbitrary messages with safe text.
    error = value.error if isinstance(value.error, dict) else {}
    code = error.get("code")
    if code == "KNOWLEDGE_DEPENDENCY_UNAVAILABLE":
        safe = {"code": code, "message": "知识索引依赖暂不可用", "retryable": True}
    else:
        safe = {"code": "KNOWLEDGE_INDEX_FAILED", "message": "知识索引失败", "retryable": False}
    return value.model_copy(update={"error": safe})


class KnowledgeService:
    """Coordinates ports and keeps all business state transitions explicit."""

    def __init__(
        self,
        repository: KnowledgeRepository | None = None,
        *,
        artifact: ArtifactPort | None = None,
        scanner: ArtifactScannerPort | None = None,
        parser: KnowledgeParser | None = None,
        jobs: KnowledgeJobQueue | None = None,
        confirmation: ConfirmationPort | None = None,
        audit: AuditPort | None = None,
    ) -> None:
        self.repository = repository
        self.artifact = artifact
        self.scanner = scanner
        self.parser = parser
        self.jobs = jobs
        self.confirmation = confirmation
        self.audit = audit

    def _repo(self) -> KnowledgeRepository:
        return _require(self.repository, "知识中心存储")  # type: ignore[return-value]

    def _document(self, project_id: str, document_id: str, *, for_update: bool = False) -> KnowledgeDocument:
        updated = None
        run_id = None
        try:
            value = self._repo().get_document(project_id, document_id, for_update=for_update)
        except KnowledgeError:
            raise
        except Exception as exc:
            raise KnowledgeUnavailableError("知识文档存储暂不可用") from exc
        if value is None:
            raise KnowledgeNotFoundError("知识文档不存在")
        return _public_document(value)

    def create(self, request: KnowledgeDocumentCreate, *, actor: ActorContext | None = None) -> KnowledgeDocument:
        _authorize(actor, request.project_id, "knowledge:write", write=True)
        artifact = _require(self.artifact, "制品读取端口")
        scanner = _require(self.scanner, "制品扫描端口")
        try:
            artifact_info = artifact.ensure_available(request.project_id, request.artifact_id)
            if artifact_info is False or scanner.ensure_scanned(request.project_id, request.artifact_id) is False:
                raise KnowledgeUnavailableError("制品当前不可用于知识资料")
        except KnowledgeUnavailableError:
            raise
        except Exception as exc:  # ports must not leak storage details
            raise KnowledgeUnavailableError("制品当前不可用于知识资料") from exc
        audit = _require(self.audit, "审计服务")
        try:
            artifact_sha = getattr(artifact_info, "sha256", None) if artifact_info is not None else None
            if isinstance(artifact_info, dict):
                artifact_sha = artifact_info.get("sha256")
            try:
                value = self._repo().create_document(request, sha256=artifact_sha if isinstance(artifact_sha, str) and len(artifact_sha) == 64 else None)
            except TypeError:
                # Small contract fixtures may expose the original one-argument
                # port; production persistence supports the optional digest.
                value = self._repo().create_document(request)
        except KnowledgeError:
            raise
        except Exception as exc:
            raise KnowledgeUnavailableError("知识文档存储暂不可用") from exc
        value = _public_document(value)
        try:
            audit.record(actor=actor, action="knowledge.document.create", project_id=request.project_id, resource_id=value.id, result="SUCCEEDED", after={"status": value.status, "artifact_id": value.artifact_id})  # type: ignore[union-attr]
        except Exception as exc:
            raise KnowledgeUnavailableError("知识文档审计暂不可用") from exc
        return value

    def get(self, project_id: str, document_id: str, *, actor: ActorContext | None = None) -> KnowledgeDocument:
        _authorize(actor, project_id, "knowledge:read")
        return self._document(project_id, document_id)

    def list(self, project_id: str, *, actor: ActorContext | None = None, q: str | None = None, status: str | None = None, limit: int = 50, after_id: str | None = None) -> list[KnowledgeDocument]:
        _authorize(actor, project_id, "knowledge:read")
        if q is not None and not 1 <= len(q) <= 2_000:
            raise KnowledgeValidationError("搜索词长度必须为 1-2000")
        if status is not None and status not in {"UPLOADED", "INDEXING", "READY", "FAILED", "NEEDS_OCR", "ARCHIVED"}:
            raise KnowledgeValidationError("未知文档状态")
        try:
            return [_public_document(item) for item in self._repo().list_documents(project_id, q=q, status=status, limit=limit, after_id=after_id)]
        except KnowledgeError:
            raise
        except Exception as exc:
            raise KnowledgeUnavailableError("知识文档查询暂不可用") from exc

    def index(self, project_id: str, document_id: str, *, actor: ActorContext | None = None, if_match: str | None = None, session: object | None = None) -> KnowledgeAccepted:
        _authorize(actor, project_id, "knowledge:write", write=True)
        if not if_match:
            raise KnowledgeValidationError("索引请求必须提供 If-Match")
        document = self._document(project_id, document_id, for_update=True)
        if document.etag != if_match:
            raise KnowledgePreconditionError("资源版本已变化")
        if document.status == "ARCHIVED":
            raise KnowledgeConflictError("已归档文档不能重新索引")
        if document.status == "INDEXING" and document.job_id:
            return KnowledgeAccepted(id=document.id, job_id=document.job_id, status="INDEXING", resource_ref=ResourceRef(type="knowledge_document", id=document.id))
        _require(self.artifact, "制品读取端口")
        _require(self.scanner, "制品扫描端口")
        _require(self.parser, "知识解析器")
        jobs = _require(self.jobs, "知识 Worker 队列")
        audit = _require(self.audit, "审计服务")
        if not getattr(jobs, "transactional_outbox", False):
            raise KnowledgeUnavailableError("知识 Worker 事务队列未配置")
        bound_sessions = [getattr(self._repo(), "session", None), getattr(jobs, "session", None)]
        bound_sessions = [value for value in bound_sessions if value is not None]
        if session is not None and any(value is not session for value in bound_sessions):
            raise KnowledgeUnavailableError("知识任务与业务投影未共享同一 Session")
        if len(bound_sessions) == 2 and bound_sessions[0] is not bound_sessions[1]:
            raise KnowledgeUnavailableError("知识任务与业务投影未共享同一 Session")
        updated = None
        run_id = None
        try:
            if self.artifact.ensure_available(project_id, document.artifact_id) is False:  # type: ignore[union-attr]
                raise KnowledgeUnavailableError("制品当前不可用于知识索引")
            if self.scanner.ensure_scanned(project_id, document.artifact_id) is False:  # type: ignore[union-attr]
                raise KnowledgeUnavailableError("制品尚未通过扫描")
            run_id = _id("index")
            enqueue = getattr(jobs, "enqueue_knowledge_index")
            job = enqueue(project_id=project_id, document_id=document.id, index_run_id=run_id, owner_id=actor.actor_id)
            if not isinstance(job, JobSubmission) or not job.job_id:
                raise KnowledgeUnavailableError("知识队列返回无效任务")
            # The queue allocates the canonical platform Job ID.  The document
            # transition and Job rows share the caller-owned transaction.
            try:
                updated = self._repo().set_indexing(project_id, document.id, job_id=job.job_id, index_run_id=run_id, attempt=job.attempt, dispatch_id=job.dispatch_id)
            except TypeError:
                updated = self._repo().set_indexing(project_id, document.id, job_id=job.job_id, index_run_id=run_id)
        except KnowledgeUnavailableError:
            raise
        except Exception as exc:
            raise KnowledgeUnavailableError("知识索引任务暂不可用") from exc
        try:
            audit.record(actor=actor, action="knowledge.document.index", project_id=project_id, resource_id=updated.id, result="SUCCEEDED", after={"status": updated.status, "index_run_id": run_id, "job_id": job.job_id})  # type: ignore[union-attr]
        except Exception as exc:
            raise KnowledgeUnavailableError("知识索引审计暂不可用") from exc
        return KnowledgeAccepted(id=updated.id, job_id=job.job_id, status="INDEXING", resource_ref=ResourceRef(type="knowledge_document", id=updated.id))

    def chunks(self, project_id: str, document_id: str, *, actor: ActorContext | None = None, document_version: str | None = None, limit: int = 100, after_ordinal: int | None = None) -> list[KnowledgeChunk]:
        _authorize(actor, project_id, "knowledge:read")
        self._document(project_id, document_id)
        try:
            values = list(self._repo().list_chunks(project_id, document_id, document_version=document_version, limit=limit, after_ordinal=after_ordinal))
            if not all(isinstance(item, KnowledgeChunk) for item in values):
                raise KnowledgeUnavailableError("知识切片存储端口返回无效 DTO")
            return values
        except KnowledgeError:
            raise
        except Exception as exc:
            raise KnowledgeUnavailableError("知识切片查询暂不可用") from exc

    def archive(self, project_id: str, document_id: str, *, confirmation_id: str, actor: ActorContext | None = None, if_match: str | None = None) -> KnowledgeDocument:
        _authorize(actor, project_id, "knowledge:write", write=True)
        document = self._document(project_id, document_id, for_update=True)
        if if_match and document.etag != if_match:
            raise KnowledgePreconditionError("资源版本已变化")
        if not confirmation_id:
            raise KnowledgeValidationError("归档必须提供 confirmation_id")
        if document.status == "ARCHIVED":
            return document
        confirmation = _require(self.confirmation, "确认服务")
        audit = _require(self.audit, "审计服务")
        try:
            with ExitStack() as stack:
                transaction = getattr(self._repo(), "transaction", None)
                if callable(transaction):
                    stack.enter_context(transaction())
                confirmation_transaction = getattr(confirmation, "transaction", None)
                if callable(confirmation_transaction):
                    stack.enter_context(confirmation_transaction())
                consumed = confirmation.consume(confirmation_id, actor=actor, project_id=project_id, resource_id=document.id, action="resource.archive", revision=document.revision, etag=document.etag)  # type: ignore[union-attr]
                if consumed is False:
                    raise KnowledgeConflictError("确认无效")
                result = self._repo().archive_document(project_id, document.id)
                audit.record(actor=actor, action="resource.archive", project_id=project_id, resource_id=document.id, result="SUCCEEDED", confirmation_id=confirmation_id, before={"status": document.status}, after={"status": result.status})  # type: ignore[union-attr]
                return result
        except (KnowledgeError, KnowledgeConflictError):
            raise
        except Exception as exc:
            raise KnowledgeConflictError("确认无效或归档失败") from exc

    def search(self, request: KnowledgeSearch, *, actor: ActorContext | None = None) -> KnowledgeSearchResult:
        _authorize(actor, request.project_id, "knowledge:read")
        # Repository.search is required to apply project, READY, visible index
        # and archive filters before LIMIT.  The service deliberately does not
        # implement a post-query permission filter.
        try:
            values = list(self._repo().search(request))
        except KnowledgeError:
            raise
        except Exception as exc:
            raise KnowledgeUnavailableError("知识检索暂不可用") from exc
        snapshots: list[Citation] = []
        for value in values[: request.top_k]:
            if not isinstance(value, Citation):
                raise KnowledgeUnavailableError("检索端口返回无效引用")
            try:
                snapshot = self._repo().save_citation_snapshot(request.project_id, value)
            except KnowledgeError:
                raise
            except Exception as exc:
                raise KnowledgeUnavailableError("引用快照暂不可用") from exc
            if not hasattr(snapshot, "citation") or not isinstance(snapshot.citation, Citation):
                raise KnowledgeUnavailableError("检索端口返回无效引用快照")
            snapshots.append(snapshot.citation)
        return KnowledgeSearchResult(search_id=_id("search"), retrieval_strategy="mysql_ngram_v1", results=snapshots)

    def citation(self, project_id: str, citation_id: str, *, actor: ActorContext | None = None) -> Citation:
        _authorize(actor, project_id, "knowledge:read")
        try:
            snapshot = self._repo().get_citation_snapshot(project_id, citation_id)
        except KnowledgeError:
            raise
        except Exception as exc:
            raise KnowledgeUnavailableError("引用存储暂不可用") from exc
        if snapshot is None:
            raise KnowledgeNotFoundError("引用不存在")
        # The repository performs the second project/permission check and
        # returns the snapshot's real document status, including ARCHIVED.
        return snapshot.citation


__all__ = ["KnowledgeService"]
