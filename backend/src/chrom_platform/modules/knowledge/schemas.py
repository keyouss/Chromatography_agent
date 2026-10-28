"""Strict public and worker DTOs for knowledge documents and citations."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, model_validator

from chrom_platform.core.dto import ResourceRef, StrictModel


class KnowledgeStrictModel(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True)


SourceType = Literal["internal", "paper", "standard", "other"]
DocumentStatus = Literal["UPLOADED", "INDEXING", "READY", "FAILED", "NEEDS_OCR", "ARCHIVED"]
IndexStatus = Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED"]
ScoreType = Literal["keyword_relevance"]


class PageLocator(KnowledgeStrictModel):
    kind: Literal["page"]
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)

    @model_validator(mode="after")
    def ordered(self) -> "PageLocator":
        if self.page_end < self.page_start:
            raise ValueError("page_end must not precede page_start")
        return self


class ParagraphLocator(KnowledgeStrictModel):
    kind: Literal["paragraph"]
    heading: str | None = Field(default=None, max_length=500)
    paragraph_start: int = Field(ge=1)
    paragraph_end: int = Field(ge=1)

    @model_validator(mode="after")
    def ordered(self) -> "ParagraphLocator":
        if self.paragraph_end < self.paragraph_start:
            raise ValueError("paragraph_end must not precede paragraph_start")
        return self


class LineLocator(KnowledgeStrictModel):
    kind: Literal["line"]
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)

    @model_validator(mode="after")
    def ordered(self) -> "LineLocator":
        if self.line_end < self.line_start:
            raise ValueError("line_end must not precede line_start")
        return self


Locator = Annotated[PageLocator | ParagraphLocator | LineLocator, Field(discriminator="kind")]


class KnowledgeDocumentCreate(KnowledgeStrictModel):
    project_id: str = Field(min_length=1, max_length=96)
    artifact_id: str = Field(min_length=1, max_length=96)
    title: str = Field(min_length=1, max_length=500)
    source_type: SourceType
    source_description: str = Field(min_length=1, max_length=2_000)
    tags: list[str] = Field(default_factory=list, max_length=50)


class KnowledgeDocument(KnowledgeStrictModel):
    id: str
    project_id: str
    title: str
    artifact_id: str
    version: int = Field(ge=1)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    status: DocumentStatus
    parser_version: str | None = None
    index_version: str | None = None
    retrieval_strategy: str = "mysql_ngram_v1"
    chunk_count: int = Field(ge=0)
    job_id: str | None = None
    error: dict[str, Any] | None = None
    revision: int = Field(ge=1)
    etag: str = Field(min_length=1, max_length=128)
    created_at: datetime

    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True, frozen=True)


class DocumentVersion(KnowledgeStrictModel):
    id: str
    document_id: str
    document_version: str = Field(min_length=1, max_length=64)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parser_version: str = Field(min_length=1, max_length=128)
    strategy_id: str = Field(min_length=1, max_length=128)
    chunk_count: int = Field(ge=0)
    visible: bool
    created_at: datetime

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class KnowledgeChunk(KnowledgeStrictModel):
    id: str
    document_id: str
    document_version: str
    ordinal: int = Field(ge=0)
    content: str = Field(min_length=1, max_length=2_000)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    locator: Locator

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class KnowledgeIndexRun(KnowledgeStrictModel):
    id: str
    document_id: str
    status: IndexStatus
    document_version: str | None = None
    parser_version: str | None = None
    strategy_id: str = "paragraph_v1"
    chunk_count: int = Field(ge=0)
    error: dict[str, Any] | None = None
    created_at: datetime
    finished_at: datetime | None = None

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Citation(KnowledgeStrictModel):
    citation_id: str
    document_id: str
    document_version: str
    chunk_id: str
    chunk_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    title: str
    locator: Locator
    snippet: str = Field(min_length=1, max_length=2_000)
    document_status: DocumentStatus
    score: float
    score_type: ScoreType = "keyword_relevance"


class CitationSnapshot(KnowledgeStrictModel):
    id: str
    project_id: str
    citation: Citation
    created_at: datetime

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class KnowledgeSearch(KnowledgeStrictModel):
    project_id: str = Field(min_length=1, max_length=96)
    query: str = Field(min_length=2, max_length=2_000)
    document_ids: list[str] = Field(default_factory=list, max_length=200)
    top_k: int = Field(default=8, ge=1, le=20)


class KnowledgeSearchResult(KnowledgeStrictModel):
    search_id: str
    retrieval_strategy: str
    results: list[Citation]
    warnings: list[dict[str, Any]] = Field(default_factory=list)


class KnowledgeAccepted(KnowledgeStrictModel):
    id: str
    job_id: str
    status: Literal["QUEUED", "RUNNING", "INDEXING"]
    resource_ref: ResourceRef


class ArchiveRequest(KnowledgeStrictModel):
    confirmation_id: str = Field(min_length=1, max_length=96)


class EmptyRequest(KnowledgeStrictModel):
    pass


class ParsedChunk(KnowledgeStrictModel):
    content: str = Field(min_length=1, max_length=2_000)
    locator: Locator


class ParsedDocument(KnowledgeStrictModel):
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parser_version: str = Field(min_length=1, max_length=128)
    chunks: list[ParsedChunk] = Field(min_length=1)


__all__ = [
    "ArchiveRequest", "Citation", "CitationSnapshot", "DocumentVersion", "DocumentStatus",
    "EmptyRequest", "IndexStatus", "KnowledgeAccepted", "KnowledgeChunk", "KnowledgeDocument",
    "KnowledgeDocumentCreate", "KnowledgeIndexRun", "KnowledgeSearch", "KnowledgeSearchResult",
    "LineLocator", "Locator", "PageLocator", "ParagraphLocator", "ParsedChunk", "ParsedDocument",
]
