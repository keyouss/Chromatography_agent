"""Knowledge-owned SQLAlchemy persistence.

The tables are intentionally not imported into the shared metadata yet.  The
application migration/assembly must opt this module in explicitly.  Methods
use the caller's Session and never commit or close it.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Mapping
from uuid import uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, PrimaryKeyConstraint, String, Text, and_, bindparam, func, select, text, update, or_
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from .schemas import (
    Citation,
    CitationSnapshot,
    DocumentVersion,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentCreate,
    KnowledgeIndexRun,
    Locator,
    KnowledgeSearch,
)
from chrom_platform.core.dto import ResourceRef
from chrom_platform.infrastructure.models import Job, JobAttempt, OutboxEvent
from chrom_platform.task_core.repository import SQLAlchemyTaskRepository
from chrom_platform.task_core.service import add_job_rows, sync_job_lifecycle
from chrom_platform.task_core.state import JobStatus
from .ports import JobSubmission


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _etag(document_id: str, revision: int, status: str) -> str:
    raw = f"{document_id}:{revision}:{status}".encode()
    return '"' + hashlib.sha256(raw).hexdigest()[:48] + f"-{revision}" + '"'


def _job_fence(session: Session, *, job_id: str, document_id: str, attempt: int, dispatch_id: str | None = None, lease_owner: str | None = None, lease_token: int | None = None, for_update: bool = True) -> tuple[Job, JobAttempt, OutboxEvent]:
    query = select(Job).where(Job.id == job_id)
    if for_update:
        query = query.with_for_update()
    job = session.scalar(query)
    if job is None or job.attempt != attempt:
        raise LookupError("knowledge job fence mismatch")
    resource = job.resource_ref or {}
    if resource.get("type") != "knowledge_document" or resource.get("id") != document_id:
        raise LookupError("knowledge job resource fence mismatch")
    attempt_row = session.scalar(select(JobAttempt).where(JobAttempt.job_id == job_id, JobAttempt.attempt == attempt).with_for_update() if for_update else select(JobAttempt).where(JobAttempt.job_id == job_id, JobAttempt.attempt == attempt))
    if attempt_row is None:
        raise LookupError("knowledge attempt fence mismatch")
    rows = session.scalars(select(OutboxEvent).where(OutboxEvent.aggregate_type == "job", OutboxEvent.aggregate_id == job_id, OutboxEvent.event_type == "job.execute")).all()
    dispatch = next((row for row in rows if int((row.payload or {}).get("attempt", -1)) == attempt and (dispatch_id is None or row.dispatch_id == dispatch_id)), None)
    if dispatch is None:
        raise LookupError("knowledge dispatch fence mismatch")
    if lease_owner is not None and (dispatch.status != "PROCESSING" or dispatch.lease_owner != lease_owner or int(dispatch.lease_token or 0) != int(lease_token or 0) or dispatch.lease_expires_at is None or dispatch.lease_expires_at <= _now()):
        raise LookupError("knowledge lease fence mismatch")
    return job, attempt_row, dispatch


class KnowledgeBase(DeclarativeBase):
    pass


MYSQL_TABLE_KWARGS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}
MYSQL_NGRAM_FTS_DDL = "CREATE FULLTEXT INDEX ix_knowledge_chunk_content_ft ON knowledge_chunk (content) WITH PARSER ngram"
MYSQL_NGRAM_FTS_DROP_DDL = "DROP INDEX ix_knowledge_chunk_content_ft ON knowledge_chunk"


class KnowledgeDocumentRow(KnowledgeBase):
    __tablename__ = "knowledge_document"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_knowledge_document"),
        CheckConstraint("version >= 1", name="ck_knowledge_document_version_positive"),
        CheckConstraint("chunk_count >= 0", name="ck_knowledge_document_chunk_count_nonnegative"),
        CheckConstraint("revision >= 1", name="ck_knowledge_document_revision_positive"),
        CheckConstraint("status IN ('UPLOADED','INDEXING','READY','FAILED','NEEDS_OCR','ARCHIVED')", name="ck_knowledge_document_status"),
        CheckConstraint("sha256 IS NULL OR CHAR_LENGTH(sha256) = 64", name="ck_knowledge_document_sha256_length"),
        Index("ix_knowledge_document_project_status", "project_id", "status", "id"),
        MYSQL_TABLE_KWARGS,
    )

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    artifact_id: Mapped[str] = mapped_column(String(96), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_description: Mapped[str] = mapped_column(String(2000), nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="UPLOADED")
    parser_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    index_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    retrieval_strategy: Mapped[str] = mapped_column(String(128), nullable=False, default="mysql_ngram_v1")
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    job_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    index_run_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    etag: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DocumentVersionRow(KnowledgeBase):
    __tablename__ = "document_version"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_document_version"),
        CheckConstraint("chunk_count >= 0", name="ck_document_version_chunk_count_nonnegative"),
        CheckConstraint("CHAR_LENGTH(sha256) = 64", name="ck_document_version_sha256_length"),
        Index("ux_document_version", "document_id", "document_version", unique=True),
        MYSQL_TABLE_KWARGS,
    )
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("knowledge_document.id", ondelete="RESTRICT", name="fk_document_version_document_id_knowledge_document"), nullable=False)
    document_version: Mapped[str] = mapped_column(String(64), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(128), nullable=False)
    strategy_id: Mapped[str] = mapped_column(String(128), nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False)
    visible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class KnowledgeChunkRow(KnowledgeBase):
    __tablename__ = "knowledge_chunk"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_knowledge_chunk"),
        Index("ix_knowledge_chunk_document_version_ordinal", "document_id", "document_version", "ordinal", unique=True),
        # Keep metadata DDL aligned with MYSQL_NGRAM_FTS_DDL.  The default
        # parser is not equivalent for CJK keyword retrieval.
        Index("ix_knowledge_chunk_content_ft", "content", mysql_prefix="FULLTEXT", mysql_with_parser="ngram"),
        CheckConstraint("ordinal >= 0", name="ck_knowledge_chunk_ordinal_nonnegative"),
        CheckConstraint("CHAR_LENGTH(content_sha256) = 64", name="ck_knowledge_chunk_content_sha256_length"),
        MYSQL_TABLE_KWARGS,
    )
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("knowledge_document.id", ondelete="RESTRICT", name="fk_knowledge_chunk_document_id_knowledge_document"), nullable=False)
    document_version: Mapped[str] = mapped_column(String(64), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    locator: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class KnowledgeIndexRunRow(KnowledgeBase):
    __tablename__ = "index_run"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_index_run"),
        CheckConstraint("chunk_count >= 0", name="ck_index_run_chunk_count_nonnegative"),
        CheckConstraint("status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')", name="ck_index_run_status"),
        MYSQL_TABLE_KWARGS,
    )
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("knowledge_document.id", ondelete="RESTRICT", name="fk_index_run_document_id_knowledge_document"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    document_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parser_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    strategy_id: Mapped[str] = mapped_column(String(128), nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CitationSnapshotRow(KnowledgeBase):
    __tablename__ = "citation_snapshot"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_citation_snapshot"),
        CheckConstraint("CHAR_LENGTH(chunk_hash) = 64", name="ck_citation_snapshot_chunk_hash_length"),
        CheckConstraint("score >= 0", name="ck_citation_snapshot_score_nonnegative"),
        Index("ix_citation_snapshot_project_document", "project_id", "document_id", "created_at"),
        MYSQL_TABLE_KWARGS,
    )
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    document_id: Mapped[str] = mapped_column(String(96), nullable=False)
    document_version: Mapped[str] = mapped_column(String(64), nullable=False)
    chunk_id: Mapped[str] = mapped_column(String(96), nullable=False)
    chunk_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    locator: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    snippet: Mapped[str] = mapped_column(Text, nullable=False)
    document_status: Mapped[str] = mapped_column(String(32), nullable=False)
    score: Mapped[float] = mapped_column(nullable=False)
    score_type: Mapped[str] = mapped_column(String(64), nullable=False, default="keyword_relevance")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SQLAlchemyKnowledgeRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    @staticmethod
    def _doc(row: KnowledgeDocumentRow) -> KnowledgeDocument:
        return KnowledgeDocument(id=row.id, project_id=row.project_id, title=row.title, artifact_id=row.artifact_id, version=row.version, sha256=row.sha256, status=row.status, parser_version=row.parser_version, index_version=row.index_version, retrieval_strategy=row.retrieval_strategy, chunk_count=row.chunk_count, job_id=row.job_id, error=row.error, revision=row.revision, etag=row.etag, created_at=row.created_at)

    def create_document(self, value: KnowledgeDocumentCreate, *, sha256: str | None = None) -> KnowledgeDocument:
        row = KnowledgeDocumentRow(id=_id("doc"), project_id=value.project_id, title=value.title, artifact_id=value.artifact_id, source_type=value.source_type, source_description=value.source_description, tags=list(value.tags), version=1, sha256=sha256, status="UPLOADED", retrieval_strategy="mysql_ngram_v1", chunk_count=0, revision=1, created_at=_now(), etag="")
        row.etag = _etag(row.id, row.revision, row.status)
        self.session.add(row)
        self.session.flush()
        return self._doc(row)

    def get_document(self, project_id: str, document_id: str, *, for_update: bool = False) -> KnowledgeDocument | None:
        query = select(KnowledgeDocumentRow).where(KnowledgeDocumentRow.project_id == project_id, KnowledgeDocumentRow.id == document_id)
        if for_update:
            query = query.with_for_update()
        row = self.session.execute(query).scalar_one_or_none()
        return self._doc(row) if row else None

    def list_documents(self, project_id: str, *, q: str | None = None, status: str | None = None, limit: int = 50, after_id: str | None = None) -> list[KnowledgeDocument]:
        query = select(KnowledgeDocumentRow).where(KnowledgeDocumentRow.project_id == project_id)
        if status:
            query = query.where(KnowledgeDocumentRow.status == status)
        if q:
            query = query.where(KnowledgeDocumentRow.title.like(f"%{q}%"))
        if after_id:
            boundary_created = None
            boundary_id = after_id
            try:
                boundary = json.loads(after_id)
                if isinstance(boundary, dict) and isinstance(boundary.get("id"), str):
                    boundary_id = boundary["id"]
                    boundary_created = datetime.fromisoformat(str(boundary["created_at"]))
            except (TypeError, ValueError, json.JSONDecodeError):
                boundary_created = None
            if boundary_created is None:
                query = query.where(KnowledgeDocumentRow.id < boundary_id)
            else:
                query = query.where(or_(KnowledgeDocumentRow.created_at < boundary_created, and_(KnowledgeDocumentRow.created_at == boundary_created, KnowledgeDocumentRow.id < boundary_id)))
        rows = self.session.execute(query.order_by(KnowledgeDocumentRow.created_at.desc(), KnowledgeDocumentRow.id.desc()).limit(min(limit, 200))).scalars().all()
        return [self._doc(row) for row in rows]

    def set_indexing(self, project_id: str, document_id: str, *, job_id: str, index_run_id: str, attempt: int = 1, dispatch_id: str | None = None) -> KnowledgeDocument:
        _job_fence(self.session, job_id=job_id, document_id=document_id, attempt=attempt, dispatch_id=dispatch_id)
        row = self.session.execute(select(KnowledgeDocumentRow).where(KnowledgeDocumentRow.project_id == project_id, KnowledgeDocumentRow.id == document_id).with_for_update()).scalar_one_or_none()
        if row is None:
            raise LookupError("knowledge document not found")
        if row.status == "INDEXING" and row.job_id:
            return self._doc(row)
        row.status = "INDEXING"; row.job_id = job_id; row.index_run_id = index_run_id; row.revision += 1; row.etag = _etag(row.id, row.revision, row.status)
        existing_run = self.session.get(KnowledgeIndexRunRow, index_run_id)
        if existing_run is None:
            self.session.add(KnowledgeIndexRunRow(id=index_run_id, document_id=row.id, status="QUEUED", strategy_id="paragraph_v1", chunk_count=0, created_at=_now()))
        self.session.flush()
        return self._doc(row)

    def replace_index_job(self, project_id: str, document_id: str, index_run_id: str, job_id: str, *, attempt: int, dispatch_id: str | None = None) -> KnowledgeDocument:
        _job_fence(self.session, job_id=job_id, document_id=document_id, attempt=attempt, dispatch_id=dispatch_id)
        row = self.session.execute(select(KnowledgeDocumentRow).where(KnowledgeDocumentRow.project_id == project_id, KnowledgeDocumentRow.id == document_id, KnowledgeDocumentRow.index_run_id == index_run_id).with_for_update()).scalar_one_or_none()
        if row is None:
            raise LookupError("knowledge index run not found")
        row.job_id = job_id
        self.session.flush()
        return self._doc(row)

    def cancel_index(self, project_id: str, document_id: str, *, index_run_id: str, job_id: str, attempt: int, dispatch_id: str | None = None, lease_guard: object | None = None) -> KnowledgeDocument:
        claim = getattr(lease_guard, "claim", None)
        _job_fence(self.session, job_id=job_id, document_id=document_id, attempt=attempt, dispatch_id=dispatch_id, lease_owner=getattr(claim, "lease_owner", None), lease_token=getattr(claim, "lease_token", None))
        row = self.session.execute(select(KnowledgeDocumentRow).where(KnowledgeDocumentRow.project_id == project_id, KnowledgeDocumentRow.id == document_id, KnowledgeDocumentRow.index_run_id == index_run_id).with_for_update()).scalar_one_or_none()
        if row is None:
            raise LookupError("knowledge index run not found")
        if row.status != "FAILED" or row.job_id is not None:
            row.status = "FAILED"
            row.job_id = None
            row.error = {"code": "KNOWLEDGE_INDEX_CANCELED", "message": "知识索引已取消", "retryable": True}
            row.revision += 1
            row.etag = _etag(row.id, row.revision, row.status)
        self.session.execute(update(KnowledgeIndexRunRow).where(KnowledgeIndexRunRow.id == index_run_id, KnowledgeIndexRunRow.document_id == document_id).values(status="FAILED", error=row.error, finished_at=_now()))
        self.session.flush()
        return self._doc(row)

    def abort_indexing(self, project_id: str, document_id: str, index_run_id: str) -> KnowledgeDocument:
        row = self.session.execute(select(KnowledgeDocumentRow).where(KnowledgeDocumentRow.project_id == project_id, KnowledgeDocumentRow.id == document_id, KnowledgeDocumentRow.index_run_id == index_run_id).with_for_update()).scalar_one_or_none()
        if row is None:
            raise LookupError("knowledge index run not found")
        row.status = "FAILED"
        row.job_id = None
        row.error = {"code": "KNOWLEDGE_INDEX_FAILED", "message": "知识索引任务未能受理", "retryable": True}
        row.revision += 1
        row.etag = _etag(row.id, row.revision, row.status)
        self.session.execute(
            update(KnowledgeIndexRunRow)
            .where(KnowledgeIndexRunRow.id == index_run_id, KnowledgeIndexRunRow.document_id == document_id)
            .values(status="FAILED", error=row.error, finished_at=_now())
        )
        self.session.flush()
        return self._doc(row)

    def archive_document(self, project_id: str, document_id: str) -> KnowledgeDocument:
        row = self.session.execute(select(KnowledgeDocumentRow).where(KnowledgeDocumentRow.project_id == project_id, KnowledgeDocumentRow.id == document_id).with_for_update()).scalar_one_or_none()
        if row is None:
            raise LookupError("knowledge document not found")
        if row.status != "ARCHIVED":
            row.status = "ARCHIVED"; row.revision += 1; row.etag = _etag(row.id, row.revision, row.status)
            self.session.flush()
        return self._doc(row)

    def list_chunks(self, project_id: str, document_id: str, *, document_version: str | None = None, limit: int = 100, after_ordinal: int | None = None) -> list[KnowledgeChunk]:
        conditions = [KnowledgeDocumentRow.project_id == project_id, KnowledgeChunkRow.document_id == document_id]
        if document_version is None:
            conditions.append(KnowledgeChunkRow.document_version == select(KnowledgeDocumentRow.index_version).where(KnowledgeDocumentRow.id == document_id).scalar_subquery())
        else:
            conditions.append(KnowledgeChunkRow.document_version == document_version)
        if after_ordinal is not None:
            conditions.append(KnowledgeChunkRow.ordinal > after_ordinal)
        rows = self.session.execute(select(KnowledgeChunkRow).join(KnowledgeDocumentRow, KnowledgeDocumentRow.id == KnowledgeChunkRow.document_id).where(and_(*conditions)).order_by(KnowledgeChunkRow.ordinal).limit(min(limit, 200))).scalars().all()
        return [KnowledgeChunk(id=row.id, document_id=row.document_id, document_version=row.document_version, ordinal=row.ordinal, content=row.content, content_sha256=row.content_sha256, locator=row.locator) for row in rows]

    def get_version(self, project_id: str, document_id: str, document_version: str) -> DocumentVersion | None:
        row = self.session.execute(select(DocumentVersionRow).join(KnowledgeDocumentRow, KnowledgeDocumentRow.id == DocumentVersionRow.document_id).where(KnowledgeDocumentRow.project_id == project_id, DocumentVersionRow.document_id == document_id, DocumentVersionRow.document_version == document_version)).scalar_one_or_none()
        return DocumentVersion(id=row.id, document_id=row.document_id, document_version=row.document_version, sha256=row.sha256, parser_version=row.parser_version, strategy_id=row.strategy_id, chunk_count=row.chunk_count, visible=row.visible, created_at=row.created_at) if row else None

    def publish_index(self, project_id: str, document_id: str, run: KnowledgeIndexRun, version: DocumentVersion, chunks: list[KnowledgeChunk] | tuple[KnowledgeChunk, ...], *, lease_guard: object | None = None) -> KnowledgeDocument:
        claim = getattr(lease_guard, "claim", None)
        if claim is not None:
            _job_fence(self.session, job_id=str(getattr(claim, "aggregate_id", "")), document_id=document_id, attempt=int(getattr(claim, "payload", {}).get("attempt", 0)), dispatch_id=str(getattr(claim, "dispatch_id", "")), lease_owner=str(getattr(claim, "lease_owner", "")), lease_token=int(getattr(claim, "lease_token", 0)))
        row = self.session.execute(select(KnowledgeDocumentRow).where(KnowledgeDocumentRow.project_id == project_id, KnowledgeDocumentRow.id == document_id).with_for_update()).scalar_one()
        self.session.execute(update(DocumentVersionRow).where(DocumentVersionRow.document_id == document_id).values(visible=False))
        self.session.add(DocumentVersionRow(id=version.id, document_id=document_id, document_version=version.document_version, sha256=version.sha256, parser_version=version.parser_version, strategy_id=version.strategy_id, chunk_count=version.chunk_count, visible=True, created_at=version.created_at))
        for chunk in chunks:
            self.session.add(KnowledgeChunkRow(id=chunk.id, document_id=document_id, document_version=chunk.document_version, ordinal=chunk.ordinal, content=chunk.content, content_sha256=chunk.content_sha256, locator=chunk.locator.model_dump(mode="json")))
        row.version = int(version.document_version); row.sha256 = version.sha256; row.status = "READY"; row.parser_version = version.parser_version; row.index_version = version.document_version; row.chunk_count = version.chunk_count; row.job_id = None; row.index_run_id = run.id; row.error = None; row.revision += 1; row.etag = _etag(row.id, row.revision, row.status)
        self.session.execute(update(KnowledgeIndexRunRow).where(KnowledgeIndexRunRow.id == run.id).values(status="SUCCEEDED", document_version=run.document_version, parser_version=run.parser_version, chunk_count=run.chunk_count, finished_at=run.finished_at))
        self.session.flush()
        return self._doc(row)

    def fail_index(self, project_id: str, document_id: str, run: KnowledgeIndexRun, *, lease_guard: object | None = None) -> KnowledgeDocument:
        claim = getattr(lease_guard, "claim", None)
        if claim is not None:
            _job_fence(self.session, job_id=str(getattr(claim, "aggregate_id", "")), document_id=document_id, attempt=int(getattr(claim, "payload", {}).get("attempt", 0)), dispatch_id=str(getattr(claim, "dispatch_id", "")), lease_owner=getattr(claim, "lease_owner", None), lease_token=getattr(claim, "lease_token", None))
        row = self.session.execute(select(KnowledgeDocumentRow).where(KnowledgeDocumentRow.project_id == project_id, KnowledgeDocumentRow.id == document_id).with_for_update()).scalar_one()
        row.status = "FAILED"; row.error = run.error; row.job_id = None; row.index_run_id = run.id; row.revision += 1; row.etag = _etag(row.id, row.revision, row.status)
        self.session.execute(update(KnowledgeIndexRunRow).where(KnowledgeIndexRunRow.id == run.id).values(status="FAILED", error=run.error, finished_at=run.finished_at))
        self.session.flush()
        return self._doc(row)

    def get_index_run(self, project_id: str, run_id: str) -> KnowledgeIndexRun | None:
        row = self.session.execute(select(KnowledgeIndexRunRow).join(KnowledgeDocumentRow, KnowledgeDocumentRow.id == KnowledgeIndexRunRow.document_id).where(KnowledgeDocumentRow.project_id == project_id, KnowledgeIndexRunRow.id == run_id)).scalar_one_or_none()
        if row is None: return None
        return KnowledgeIndexRun(id=row.id, document_id=row.document_id, status=row.status, document_version=row.document_version, parser_version=row.parser_version, strategy_id=row.strategy_id, chunk_count=row.chunk_count, error=row.error, created_at=row.created_at, finished_at=row.finished_at)

    def search(self, request: KnowledgeSearch) -> list[Citation]:
        # This is deliberately MySQL-only.  SQLite LIKE is not a valid test or
        # production substitute for the ngram contract.
        match = text("MATCH (kc.content) AGAINST (:knowledge_query IN NATURAL LANGUAGE MODE)").bindparams(bindparam("knowledge_query", request.query))
        score = text("MATCH (kc.content) AGAINST (:knowledge_query IN NATURAL LANGUAGE MODE)").bindparams(bindparam("knowledge_query", request.query)).label("score")
        # FAILED means only the newest attempt failed.  If index_version is
        # still populated, the last successful immutable version remains
        # searchable; ARCHIVED is excluded immediately.
        conditions = [KnowledgeDocumentRow.project_id == request.project_id, KnowledgeDocumentRow.status.in_(["READY", "FAILED"]), KnowledgeDocumentRow.index_version == KnowledgeChunkRow.document_version, DocumentVersionRow.visible.is_(True)]
        if request.document_ids:
            conditions.append(KnowledgeDocumentRow.id.in_(request.document_ids))
        query = select(KnowledgeDocumentRow, KnowledgeChunkRow, score).select_from(KnowledgeChunkRow).join(KnowledgeDocumentRow, KnowledgeDocumentRow.id == KnowledgeChunkRow.document_id).join(DocumentVersionRow, and_(DocumentVersionRow.document_id == KnowledgeChunkRow.document_id, DocumentVersionRow.document_version == KnowledgeChunkRow.document_version)).where(and_(*conditions, match)).order_by(score.desc(), KnowledgeChunkRow.id).limit(request.top_k).params(knowledge_query=request.query)
        rows = self.session.execute(query).all()
        return [Citation(citation_id=_id("cite"), document_id=doc.id, document_version=chunk.document_version, chunk_id=chunk.id, chunk_hash=chunk.content_sha256, title=doc.title, locator=chunk.locator, snippet=chunk.content[:2_000], document_status=doc.status, score=float(value or 0), score_type="keyword_relevance") for doc, chunk, value in rows]

    def save_citation_snapshot(self, project_id: str, citation: Citation) -> CitationSnapshot:
        row = CitationSnapshotRow(id=citation.citation_id, project_id=project_id, document_id=citation.document_id, document_version=citation.document_version, chunk_id=citation.chunk_id, chunk_hash=citation.chunk_hash, title=citation.title, locator=citation.locator.model_dump(mode="json"), snippet=citation.snippet, document_status=citation.document_status, score=citation.score, score_type=citation.score_type, created_at=_now())
        self.session.add(row); self.session.flush()
        return CitationSnapshot(id=row.id, project_id=project_id, citation=citation, created_at=row.created_at)

    def get_citation_snapshot(self, project_id: str, citation_id: str) -> CitationSnapshot | None:
        row = self.session.execute(select(CitationSnapshotRow, KnowledgeDocumentRow.status).join(KnowledgeDocumentRow, and_(KnowledgeDocumentRow.id == CitationSnapshotRow.document_id, KnowledgeDocumentRow.project_id == CitationSnapshotRow.project_id)).where(CitationSnapshotRow.project_id == project_id, CitationSnapshotRow.id == citation_id)).one_or_none()
        if row is None: return None
        snapshot, status = row
        citation = Citation(citation_id=snapshot.id, document_id=snapshot.document_id, document_version=snapshot.document_version, chunk_id=snapshot.chunk_id, chunk_hash=snapshot.chunk_hash, title=snapshot.title, locator=snapshot.locator, snippet=snapshot.snippet, document_status=status, score=snapshot.score, score_type=snapshot.score_type)
        return CitationSnapshot(id=snapshot.id, project_id=project_id, citation=citation, created_at=snapshot.created_at)


class SQLAlchemyKnowledgeJobQueue:
    """Create canonical platform Job rows in the caller's transaction."""

    transactional_outbox = True

    def __init__(self, session: Session) -> None:
        if callable(session) or not all(hasattr(session, name) for name in ("add", "flush", "scalar")):
            raise TypeError("SQLAlchemyKnowledgeJobQueue requires a caller-owned Session")
        self.session = session

    def enqueue_knowledge_index(self, *, project_id: str, document_id: str, index_run_id: str, owner_id: str, job_id: str | None = None) -> JobSubmission:
        if job_id is not None:
            raise ValueError("knowledge Job IDs are allocated by the platform Job producer")
        task_repository = SQLAlchemyTaskRepository(lambda: self.session)
        job = add_job_rows(
            task_repository,
            self.session,
            project_id=project_id,
            owner_id=owner_id,
            kind="knowledge_index",
            resource_ref=ResourceRef(type="knowledge_document", id=document_id),
            cancelable=True,
            retryable=True,
            resume_mode="restart",
            deadline_at=None,
        )
        self.session.flush()
        dispatch = self.session.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_type == "job", OutboxEvent.aggregate_id == job.id, OutboxEvent.event_type == "job.execute"))
        if dispatch is None or set(dispatch.payload or {}) != {"job_id", "attempt", "dispatch_id"}:
            raise RuntimeError("platform job execute outbox was not created")
        return JobSubmission(job_id=job.id, resource_id=document_id, attempt=1, dispatch_id=dispatch.dispatch_id)


class SQLAlchemyKnowledgeExecutionStore:
    """Shared Job lifecycle adapter for the knowledge worker.

    It uses the caller-owned session, so projection and Job rows are committed
    by one outer worker transaction.
    """

    shared_job_lifecycle = True

    def __init__(self, session: Session) -> None:
        self.session = session
        self.tasks = SQLAlchemyTaskRepository(lambda: session)

    def load_index(self, job_or_id: object) -> object | None:
        job_id = str(job_or_id) if isinstance(job_or_id, str) else getattr(job_or_id, "job_id", None) or (job_or_id.get("job_id") if isinstance(job_or_id, dict) else None)
        if not job_id:
            return None
        job = self.session.scalar(select(Job).where(Job.id == job_id))
        if job is None:
            return None
        ref = job.resource_ref or {}
        document_id = ref.get("id")
        document = self.session.scalar(select(KnowledgeDocumentRow).where(KnowledgeDocumentRow.project_id == job.project_id, KnowledgeDocumentRow.id == document_id))
        if document is None or not document.index_run_id:
            return None
        attempt = self.session.scalar(select(JobAttempt).where(JobAttempt.job_id == job.id, JobAttempt.attempt == job.attempt))
        dispatches = self.session.scalars(select(OutboxEvent).where(OutboxEvent.aggregate_id == job.id, OutboxEvent.event_type == "job.execute")).all()
        dispatch = next((row for row in dispatches if int((row.payload or {}).get("attempt", -1)) == int(job.attempt)), None)
        return {"job_id": job.id, "project_id": job.project_id, "document_id": document.id, "index_run_id": document.index_run_id, "status": job.status, "attempt": int(job.attempt), "dispatch_id": dispatch.dispatch_id if dispatch else None, "fencing_token": attempt.fencing_token if attempt else None}

    def validate_fence(self, item: object, *, for_update: bool = True, lease_guard: object | None = None) -> bool:
        job_id = _value(item, "job_id")
        attempt = _value(item, "attempt")
        document_id = _value(item, "document_id")
        dispatch_id = _value(item, "dispatch_id")
        if not job_id or attempt is None or not document_id:
            return False
        try:
            claim = getattr(lease_guard, "claim", None)
            _job_fence(self.session, job_id=str(job_id), document_id=str(document_id), attempt=int(attempt), dispatch_id=str(dispatch_id) if dispatch_id else None, lease_owner=getattr(claim, "lease_owner", None), lease_token=getattr(claim, "lease_token", None), for_update=for_update)
        except LookupError:
            return False
        return True

    def _sync(self, item: object, status: JobStatus | str, *, error=None, result_ref=None, lease_guard: object | None = None) -> bool:
        job_id = _value(item, "job_id")
        attempt = _value(item, "attempt")
        resource_ref = {"type": "knowledge_document", "id": _value(item, "document_id")}
        if not job_id:
            return False
        if lease_guard is not None and not self.validate_fence(item, lease_guard=lease_guard):
            return False
        value = sync_job_lifecycle(self.tasks, self.session, job_id=str(job_id), status=status, attempt=int(attempt) if attempt is not None else None, expected_attempt=int(attempt) if attempt is not None else None, dispatch_id=_value(item, "dispatch_id"), resource_ref=resource_ref, error=error, result_ref=result_ref)
        return value is not None

    def mark_running(self, item: object, *, expected_attempt=None, expected_dispatch_id=None, lease_guard: object | None = None) -> bool:
        if expected_dispatch_id and _value(item, "dispatch_id") != expected_dispatch_id:
            return False
        if not self.validate_fence(item, lease_guard=lease_guard):
            return False
        if not self._sync(item, JobStatus.RUNNING, lease_guard=lease_guard):
            return False
        run_id = _value(item, "index_run_id")
        self.session.execute(update(KnowledgeIndexRunRow).where(KnowledgeIndexRunRow.id == run_id, KnowledgeIndexRunRow.status == "QUEUED").values(status="RUNNING"))
        return True

    def mark_succeeded(self, item: object, result: object, *, lease_guard: object | None = None) -> bool:
        if not self.validate_fence(item, lease_guard=lease_guard):
            return False
        return self._sync(item, JobStatus.SUCCEEDED, lease_guard=lease_guard, result_ref={"type": "knowledge_document", "id": _value(item, "document_id")})

    def mark_failed(self, item: object, error: dict[str, Any], *, lease_guard: object | None = None) -> bool:
        if not self.validate_fence(item, lease_guard=lease_guard):
            return False
        return self._sync(item, JobStatus.FAILED, lease_guard=lease_guard, error=error)

    def mark_canceled(self, item: object, *, lease_guard: object | None = None) -> bool:
        if not self.validate_fence(item, lease_guard=lease_guard):
            return False
        return self._sync(item, JobStatus.CANCELED, lease_guard=lease_guard, error={"code": "KNOWLEDGE_INDEX_CANCELED", "message": "知识索引已取消", "retryable": True})


class SQLAlchemyKnowledgeJobCoordinator:
    """Knowledge projection hooks for the shared JobService transaction."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def on_job_cancel(self, job: Job, *, attempt: int, dispatch_id: str, session: Session | None = None) -> None:
        db = session or self.session
        document_id = str((job.resource_ref or {}).get("id", ""))
        if not document_id or (job.resource_ref or {}).get("type") != "knowledge_document":
            return
        _job_fence(db, job_id=job.id, document_id=document_id, attempt=attempt, dispatch_id=dispatch_id)
        if job.status == JobStatus.CANCELED.value:
            run_id = db.scalar(select(KnowledgeDocumentRow.index_run_id).where(KnowledgeDocumentRow.id == document_id, KnowledgeDocumentRow.project_id == job.project_id))
            if run_id:
                SQLAlchemyKnowledgeRepository(db).cancel_index(job.project_id, document_id, index_run_id=str(run_id), job_id=job.id, attempt=attempt, dispatch_id=dispatch_id)

    def on_job_retry(self, job: Job, *, attempt: int, dispatch_id: str, session: Session | None = None) -> None:
        db = session or self.session
        document_id = str((job.resource_ref or {}).get("id", ""))
        if not document_id or (job.resource_ref or {}).get("type") != "knowledge_document":
            return
        _job_fence(db, job_id=job.id, document_id=document_id, attempt=attempt, dispatch_id=dispatch_id)
        row = db.scalar(select(KnowledgeDocumentRow).where(KnowledgeDocumentRow.project_id == job.project_id, KnowledgeDocumentRow.id == document_id).with_for_update())
        if row is None or row.job_id != job.id:
            raise LookupError("knowledge retry document binding mismatch")
        run_id = _id("index")
        db.add(KnowledgeIndexRunRow(id=run_id, document_id=document_id, status="QUEUED", strategy_id="paragraph_v1", chunk_count=0, created_at=_now()))
        row.status = "INDEXING"
        row.index_run_id = run_id
        row.job_id = job.id
        row.error = None
        row.revision += 1
        row.etag = _etag(row.id, row.revision, row.status)
        db.flush()


def _value(item: object, name: str):
    return item.get(name) if isinstance(item, dict) else getattr(item, name, None)


__all__ = ["CitationSnapshotRow", "DocumentVersionRow", "KnowledgeBase", "KnowledgeChunkRow", "KnowledgeDocumentRow", "KnowledgeIndexRunRow", "MYSQL_NGRAM_FTS_DDL", "MYSQL_NGRAM_FTS_DROP_DDL", "SQLAlchemyKnowledgeJobQueue", "SQLAlchemyKnowledgeExecutionStore", "SQLAlchemyKnowledgeJobCoordinator", "SQLAlchemyKnowledgeRepository"]
