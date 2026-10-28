"""Ports owned by the knowledge vertical slice.

Ports intentionally use IDs and immutable DTOs.  In particular no port accepts
an arbitrary server path, import string, shell command or client supplied SQL.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from chrom_platform.core.dto import ActorContext

from .schemas import (
    Citation,
    CitationSnapshot,
    DocumentVersion,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentCreate,
    KnowledgeIndexRun,
    KnowledgeSearch,
    ParsedDocument,
)


class KnowledgeRepository(Protocol):
    """Knowledge persistence boundary; implementations do not commit/close."""

    def create_document(self, value: KnowledgeDocumentCreate, *, sha256: str | None = None) -> KnowledgeDocument: ...
    def get_document(self, project_id: str, document_id: str, *, for_update: bool = False) -> KnowledgeDocument | None: ...
    def list_documents(self, project_id: str, *, q: str | None = None, status: str | None = None, limit: int = 50, after_id: str | None = None) -> Sequence[KnowledgeDocument]: ...
    def archive_document(self, project_id: str, document_id: str) -> KnowledgeDocument: ...
    def set_indexing(self, project_id: str, document_id: str, *, job_id: str, index_run_id: str) -> KnowledgeDocument: ...
    def list_chunks(self, project_id: str, document_id: str, *, document_version: str | None = None, limit: int = 100, after_ordinal: int | None = None) -> Sequence[KnowledgeChunk]: ...
    def get_version(self, project_id: str, document_id: str, document_version: str) -> DocumentVersion | None: ...
    def publish_index(self, project_id: str, document_id: str, run: KnowledgeIndexRun, version: DocumentVersion, chunks: Sequence[KnowledgeChunk]) -> KnowledgeDocument: ...
    def fail_index(self, project_id: str, document_id: str, run: KnowledgeIndexRun) -> KnowledgeDocument: ...
    def get_index_run(self, project_id: str, run_id: str) -> KnowledgeIndexRun | None: ...
    def search(self, request: KnowledgeSearch) -> Sequence[Citation]: ...
    def save_citation_snapshot(self, project_id: str, citation: Citation) -> CitationSnapshot: ...
    def get_citation_snapshot(self, project_id: str, citation_id: str) -> CitationSnapshot | None: ...


class ArtifactPort(Protocol):
    """Read an already validated artifact by stable ID."""

    def ensure_available(self, project_id: str, artifact_id: str) -> object: ...

    def read_bytes(self, project_id: str, artifact_id: str) -> bytes: ...


class ArtifactScannerPort(Protocol):
    def ensure_scanned(self, project_id: str, artifact_id: str) -> object: ...


class KnowledgeParser(Protocol):
    def parse(self, *, project_id: str, artifact_id: str, content: bytes) -> ParsedDocument: ...


class KnowledgeJobQueue(Protocol):
    transactional_outbox: bool

    def enqueue_knowledge_index(self, *, project_id: str, document_id: str, index_run_id: str, owner_id: str, job_id: str | None = None) -> "JobSubmission": ...


class KnowledgeJobCoordinator(Protocol):
    def on_job_cancel(self, job: object, *, attempt: int, dispatch_id: str, session: object) -> None: ...
    def on_job_retry(self, job: object, *, attempt: int, dispatch_id: str, session: object) -> None: ...


@dataclass(frozen=True)
class JobSubmission:
    job_id: str
    resource_id: str
    status: str = "QUEUED"
    attempt: int = 1
    dispatch_id: str | None = None


class ConfirmationPort(Protocol):
    def consume(self, confirmation_id: str, *, actor: ActorContext, project_id: str, resource_id: str, action: str, revision: int, etag: str) -> object: ...


class AuditPort(Protocol):
    def record(self, *, actor: ActorContext, action: str, project_id: str, resource_id: str, result: str, confirmation_id: str | None = None, before: dict[str, Any] | None = None, after: dict[str, Any] | None = None) -> object: ...


class KnowledgeExecutionStore(Protocol):
    def load_index(self, job_or_id: object) -> object | None: ...
    def mark_running(self, item: object) -> bool | None: ...
    def mark_succeeded(self, item: object, result: object) -> None: ...
    def mark_failed(self, item: object, error: dict[str, Any]) -> None: ...


__all__ = [
    "ArtifactPort", "ArtifactScannerPort", "AuditPort", "ConfirmationPort", "JobSubmission",
    "KnowledgeExecutionStore", "KnowledgeJobCoordinator", "KnowledgeJobQueue", "KnowledgeParser", "KnowledgeRepository",
]
