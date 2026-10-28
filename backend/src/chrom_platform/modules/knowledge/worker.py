"""Controlled knowledge indexing worker.

Queue payloads contain only IDs.  The worker reloads the artifact and the
document from persistence, builds an immutable candidate index, then asks the
repository to atomically publish it.  A parser failure never removes the last
visible index.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from .errors import KnowledgeUnavailableError, KnowledgeValidationError
from .ports import ArtifactPort, ArtifactScannerPort, KnowledgeExecutionStore, KnowledgeParser, KnowledgeRepository
from .schemas import DocumentVersion, KnowledgeChunk, KnowledgeIndexRun, ParsedDocument
from chrom_platform.task_core.outbox_dispatch import LeaseLostError


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _attr(item: object, name: str) -> Any:
    if isinstance(item, dict):
        return item.get(name)
    return getattr(item, name, None)


def _error(exc: BaseException) -> dict[str, Any]:
    # Never persist exception text: parser/storage errors may contain paths,
    # SQL, document excerpts, or credentials.
    if isinstance(exc, KnowledgeUnavailableError):
        return {"code": "KNOWLEDGE_DEPENDENCY_UNAVAILABLE", "message": "知识索引依赖暂不可用", "retryable": True}
    return {"code": "KNOWLEDGE_INDEX_FAILED", "message": "知识索引失败", "retryable": False}


def _stale_error() -> dict[str, Any]:
    return {"code": "KNOWLEDGE_INDEX_STALE", "message": "知识索引任务已失效", "retryable": False}


class _StaleIndexClaim(Exception):
    """The queue item no longer owns the document/index run."""


class _CompletedIndexClaim(Exception):
    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


@dataclass(frozen=True)
class WorkerResult:
    status: str
    result: object | None = None
    error: dict[str, Any] | None = None
    replayed: bool = False


class KnowledgeWorker:
    def __init__(self, *, repository: KnowledgeRepository, artifact: ArtifactPort | None, scanner: ArtifactScannerPort | None, parser: KnowledgeParser | None, store: KnowledgeExecutionStore) -> None:
        self.repository = repository
        self.artifact = artifact
        self.scanner = scanner
        self.parser = parser
        self.store = store

    @staticmethod
    def _check_lease(lease_guard: object | None) -> None:
        if lease_guard is not None:
            check = getattr(lease_guard, "check", None)
            if callable(check):
                check()

    def _claim_document(self, item: object, project_id: str, document_id: str, run_id: str):
        document = self.repository.get_document(project_id, document_id, for_update=False)
        if document is None:
            raise KeyError("knowledge document not found")

        # A compensation/failure fence is meaningful even for repositories
        # that do not expose the internal index_run row through the DTO.
        if str(_attr(document, "status")) in {"FAILED", "ARCHIVED"}:
            raise _StaleIndexClaim()

        item_job_id = _attr(item, "job_id")
        get_run = getattr(self.repository, "get_index_run", None)
        run = get_run(project_id, run_id) if callable(get_run) else None
        durable_claim = item_job_id is not None or callable(get_run)
        if not durable_claim:
            return document
        if run is None:
            raise _StaleIndexClaim()
        if str(_attr(run, "document_id")) != document_id:
            raise _StaleIndexClaim()
        bound_run_id = _attr(document, "index_run_id")
        if bound_run_id is not None and str(bound_run_id) != run_id:
            raise _StaleIndexClaim()
        if str(_attr(run, "status")) == "SUCCEEDED":
            raise _CompletedIndexClaim("SUCCEEDED")
        if str(_attr(run, "status")) not in {"QUEUED", "RUNNING"}:
            raise _StaleIndexClaim()
        if str(_attr(document, "status")) != "INDEXING":
            raise _StaleIndexClaim()
        if item_job_id is not None and str(_attr(document, "job_id")) != str(item_job_id):
            raise _StaleIndexClaim()
        return document

    def handle(self, work: object | str, *, attempt: int | None = None, dispatch_id: str | None = None, lease_guard: object | None = None) -> object:
        item = self.store.load_index(work) if isinstance(work, str) else work
        if item is None:
            raise KeyError("knowledge index job not found")
        current = str(_attr(item, "status") or "QUEUED")
        if current in {"SUCCEEDED", "FAILED", "CANCELED", "TIMED_OUT", "INTERRUPTED"}:
            return WorkerResult(status=current, replayed=True)
        persisted_attempt = _attr(item, "attempt")
        if attempt is not None and persisted_attempt is not None and int(attempt) != int(persisted_attempt):
            return WorkerResult(status=current, replayed=True)
        persisted_dispatch = _attr(item, "dispatch_id")
        if dispatch_id is not None and persisted_dispatch is not None and dispatch_id != str(persisted_dispatch):
            return WorkerResult(status=current, replayed=True)
        project_id = _attr(item, "project_id")
        document_id = _attr(item, "document_id")
        run_id = _attr(item, "index_run_id") or _attr(item, "resource_id")
        if not project_id or not document_id or not run_id:
            error = {"code": "KNOWLEDGE_INDEX_INVALID_JOB", "message": "索引任务参数无效", "retryable": False}
            self.store.mark_failed(item, error)
            return WorkerResult(status="FAILED", error=error)
        run = KnowledgeIndexRun(id=str(run_id), document_id=str(document_id), status="RUNNING", strategy_id="paragraph_v1", chunk_count=0, created_at=_now())
        try:
            document = self._claim_document(item, str(project_id), str(document_id), str(run_id))
        except _CompletedIndexClaim as completed:
            return WorkerResult(status=completed.status, replayed=True)
        except _StaleIndexClaim:
            error = _stale_error()
            self.store.mark_failed(item, error)
            return WorkerResult(status="FAILED", error=error, replayed=True)
        except Exception as exc:
            error = _error(exc)
            self.store.mark_failed(item, error)
            return WorkerResult(status="FAILED", error=error)
        # A real platform job must be fenced by the shared Job/Attempt/Event
        # store.  Legacy unit fixtures without a platform job keep the small
        # in-memory contract, while production jobs fail closed.
        shared_lifecycle = bool(getattr(self.store, "shared_job_lifecycle", False))
        if _attr(item, "job_id") is not None and not shared_lifecycle:
            error = {"code": "KNOWLEDGE_LIFECYCLE_UNAVAILABLE", "message": "知识任务共享生命周期不可用", "retryable": True}
            try:
                self._check_lease(lease_guard)
                try:
                    self.store.mark_failed(item, error, lease_guard=lease_guard)
                except TypeError:
                    if lease_guard is not None:
                        raise
                    self.store.mark_failed(item, error)
            except Exception:
                pass
            return WorkerResult(status="FAILED", error=error)
        if current == "CANCEL_REQUESTED":
            cancel = getattr(self.store, "mark_canceled", None)
            self._check_lease(lease_guard)
            try:
                canceled = cancel(item, lease_guard=lease_guard) if callable(cancel) else False
            except TypeError:
                canceled = cancel(item) if callable(cancel) else False
            if canceled:
                cancel_projection = getattr(self.repository, "cancel_index", None)
                if callable(cancel_projection):
                    self._check_lease(lease_guard)
                    try:
                        cancel_projection(str(project_id), str(document_id), index_run_id=str(run_id), job_id=str(_attr(item, "job_id")), attempt=int(persisted_attempt), dispatch_id=dispatch_id or persisted_dispatch, lease_guard=lease_guard)
                    except TypeError:
                        if lease_guard is not None:
                            raise
                        cancel_projection(str(project_id), str(document_id), index_run_id=str(run_id), job_id=str(_attr(item, "job_id")), attempt=int(persisted_attempt), dispatch_id=dispatch_id or persisted_dispatch)
                return WorkerResult(status="CANCELED")
            return WorkerResult(status="CANCEL_REQUESTED", replayed=True)
        try:
            self._check_lease(lease_guard)
            claimed = self.store.mark_running(item, expected_attempt=persisted_attempt, expected_dispatch_id=dispatch_id or persisted_dispatch, lease_guard=lease_guard)
        except TypeError:
            if lease_guard is not None:
                raise
            claimed = self.store.mark_running(item)
        if claimed is False:
            return {"status": "RUNNING", "replayed": True}
        try:
            self._check_lease(lease_guard)
            if self.artifact is None:
                raise KnowledgeUnavailableError("制品读取端口未配置")
            if self.scanner is None:
                raise KnowledgeUnavailableError("制品扫描端口未配置")
            if self.parser is None:
                raise KnowledgeUnavailableError("知识解析器未配置")
            if self.artifact.ensure_available(str(project_id), document.artifact_id) is False:
                raise KnowledgeUnavailableError("制品当前不可用于知识索引")
            self._check_lease(lease_guard)
            if self.scanner.ensure_scanned(str(project_id), document.artifact_id) is False:
                raise KnowledgeUnavailableError("制品尚未通过扫描")
            self._check_lease(lease_guard)
            content = self.artifact.read_bytes(str(project_id), document.artifact_id)
            self._check_lease(lease_guard)
            parsed = self.parser.parse(project_id=str(project_id), artifact_id=document.artifact_id, content=content)
            self._check_lease(lease_guard)
            if not isinstance(parsed, ParsedDocument):
                parsed = ParsedDocument.model_validate(parsed)
            content_digest = hashlib.sha256(content).hexdigest()
            if parsed.content_sha256 != content_digest:
                raise KnowledgeValidationError("解析内容校验失败")
            # A newly created document has no published version yet; its first
            # successful index is version 1.  Later indexes advance immutably.
            version = str(int(document.version) + 1 if document.index_version else 1)
            chunks: list[KnowledgeChunk] = []
            for ordinal, source in enumerate(parsed.chunks):
                digest = hashlib.sha256(source.content.encode("utf-8")).hexdigest()
                chunks.append(KnowledgeChunk(id=_id("chunk"), document_id=document.id, document_version=version, ordinal=ordinal, content=source.content, content_sha256=digest, locator=source.locator))
            run = run.model_copy(update={"status": "SUCCEEDED", "document_version": version, "parser_version": parsed.parser_version, "chunk_count": len(chunks), "finished_at": _now()})
            immutable = DocumentVersion(id=_id("docver"), document_id=document.id, document_version=version, sha256=parsed.content_sha256, parser_version=parsed.parser_version, strategy_id="paragraph_v1", chunk_count=len(chunks), visible=True, created_at=_now())
            self._check_lease(lease_guard)
            validate_fence = getattr(self.store, "validate_fence", None)
            if callable(validate_fence) and not validate_fence(item, lease_guard=lease_guard):
                return WorkerResult(status="RUNNING", replayed=True)
            try:
                result = self.repository.publish_index(str(project_id), document.id, run, immutable, chunks, lease_guard=lease_guard)
            except TypeError:
                if lease_guard is not None:
                    raise
                result = self.repository.publish_index(str(project_id), document.id, run, immutable, chunks)
            self._check_lease(lease_guard)
            try:
                completed = self.store.mark_succeeded(item, result, lease_guard=lease_guard)
            except TypeError:
                if lease_guard is not None:
                    raise
                completed = self.store.mark_succeeded(item, result)
            if completed is False:
                return WorkerResult(status="RUNNING", replayed=True)
            return WorkerResult(status="SUCCEEDED", result=result)
        except LeaseLostError:
            return WorkerResult(status="RUNNING", replayed=True)
        except Exception as exc:  # every worker failure is made durable
            failed = run.model_copy(update={"status": "FAILED", "error": _error(exc), "finished_at": _now()})
            try:
                validate_fence = getattr(self.store, "validate_fence", None)
                if callable(validate_fence) and not validate_fence(item, lease_guard=lease_guard):
                    return WorkerResult(status="RUNNING", replayed=True)
                try:
                    self.repository.fail_index(str(project_id), str(document_id), failed, lease_guard=lease_guard)
                except TypeError:
                    if lease_guard is not None:
                        raise
                    self.repository.fail_index(str(project_id), str(document_id), failed)
            except Exception:
                # Keep the execution failure durable even if projection repair
                # itself is temporarily unavailable; a coordinator can retry.
                pass
            self._check_lease(lease_guard)
            try:
                self.store.mark_failed(item, _error(exc), lease_guard=lease_guard)
            except TypeError:
                if lease_guard is not None:
                    raise
                self.store.mark_failed(item, _error(exc))
            return WorkerResult(status="FAILED", error=_error(exc))


def handle_knowledge_index(*, repository: KnowledgeRepository, artifact: ArtifactPort | None, scanner: ArtifactScannerPort | None, parser: KnowledgeParser | None, store: KnowledgeExecutionStore, job_id: str, attempt: int | None = None, dispatch_id: str | None = None, lease_guard: object | None = None) -> object:
    return KnowledgeWorker(repository=repository, artifact=artifact, scanner=scanner, parser=parser, store=store).handle(job_id, attempt=attempt, dispatch_id=dispatch_id, lease_guard=lease_guard)


__all__ = ["KnowledgeWorker", "WorkerResult", "handle_knowledge_index"]
