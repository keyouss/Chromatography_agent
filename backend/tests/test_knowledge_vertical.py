from __future__ import annotations

from datetime import datetime, timezone
import hashlib

import pytest
from fastapi import FastAPI, Request, Response

from chrom_platform.core.dto import ActorContext, ResourceRef
from chrom_platform.modules.knowledge.errors import KnowledgeNotFoundError, KnowledgeUnavailableError
from chrom_platform.modules.knowledge.persistence import KnowledgeChunkRow
from chrom_platform.modules.knowledge.router import create_router
from chrom_platform.core.errors import APIError
from chrom_platform.modules.knowledge.ports import JobSubmission
from chrom_platform.modules.knowledge.schemas import (
    Citation,
    CitationSnapshot,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentCreate,
    KnowledgeIndexRun,
    KnowledgeSearch,
    PageLocator,
    ParsedChunk,
    ParsedDocument,
)
from chrom_platform.modules.knowledge.service import KnowledgeService
from chrom_platform.modules.knowledge.worker import KnowledgeWorker


NOW = datetime.now(timezone.utc)
ACTOR = ActorContext(actor_id="u1", project_ids=["p1"], auth_method="deployment_default", permissions=["knowledge:read", "knowledge:write"], policy_version="p1")


def doc(*, project_id="p1", status="READY", version=1, index_version="1", job_id=None, etag=None):
    return KnowledgeDocument(id="doc1", project_id=project_id, title="Methods", artifact_id="art1", version=version, sha256="a" * 64, status=status, parser_version="parser-1", index_version=index_version, chunk_count=1, job_id=job_id, revision=version, etag=etag or f'"etag-{version}"', created_at=NOW)


class Artifact:
    def __init__(self, content=b"method paragraph"):
        self.content = content

    def ensure_available(self, project_id, artifact_id):
        assert artifact_id == "art1"
        return {"sha256": hashlib.sha256(self.content).hexdigest()}

    def read_bytes(self, project_id, artifact_id):
        return self.content


class Scanner:
    def ensure_scanned(self, project_id, artifact_id):
        return True


class Audit:
    def __init__(self): self.rows = []
    def record(self, **row): self.rows.append(row)


class Confirmation:
    def consume(self, confirmation_id, **kwargs):
        if confirmation_id != "confirm-1": raise ValueError("bad confirmation")
        return True


class Jobs:
    transactional_outbox = True
    def __init__(self): self.calls = []
    def enqueue_knowledge_index(self, **kwargs):
        self.calls.append(kwargs)
        return JobSubmission(job_id="job1", resource_id=kwargs["document_id"])


class Parser:
    def __init__(self, fail=False): self.fail = fail
    def parse(self, **kwargs):
        if self.fail: raise RuntimeError("parser unavailable")
        content = kwargs["content"]
        return ParsedDocument(content_sha256=hashlib.sha256(content).hexdigest(), parser_version="parser-1", chunks=[ParsedChunk(content=content.decode(), locator=PageLocator(kind="page", page_start=1, page_end=1))])


class Repo:
    def __init__(self, row=None):
        self.row = row or doc()
        self.snapshots = {}
        self.published = []
    def create_document(self, value, **kwargs): return self.row
    def get_document(self, project_id, document_id, *, for_update=False):
        return self.row if self.row.project_id == project_id and self.row.id == document_id else None
    def list_documents(self, *args, **kwargs): return [self.row]
    def set_indexing(self, project_id, document_id, *, job_id, index_run_id):
        self.row = self.row.model_copy(update={"status": "INDEXING", "job_id": job_id, "revision": self.row.revision + 1, "etag": '"indexing"'})
        return self.row
    def list_chunks(self, *args, **kwargs): return []
    def archive_document(self, project_id, document_id):
        self.row = self.row.model_copy(update={"status": "ARCHIVED", "revision": self.row.revision + 1, "etag": '"archived"'})
        return self.row
    def publish_index(self, project_id, document_id, run, version, chunks):
        self.published.append((run, version, chunks))
        self.row = self.row.model_copy(update={"status": "READY", "version": int(version.document_version), "index_version": version.document_version, "chunk_count": len(chunks), "job_id": None})
        return self.row
    def fail_index(self, project_id, document_id, run):
        self.row = self.row.model_copy(update={"status": "FAILED", "error": run.error, "job_id": None})
        return self.row
    def search(self, request):
        if self.row.status in {"READY", "FAILED"} and self.row.index_version:
            return [Citation(citation_id="cite1", document_id="doc1", document_version=self.row.index_version, chunk_id="chunk1", chunk_hash="b" * 64, title=self.row.title, locator=PageLocator(kind="page", page_start=1, page_end=1), snippet="method", document_status=self.row.status, score=1.0)]
        return []
    def save_citation_snapshot(self, project_id, citation):
        snapshot = CitationSnapshot(id=citation.citation_id, project_id=project_id, citation=citation, created_at=NOW); self.snapshots[snapshot.id] = snapshot; return snapshot
    def get_citation_snapshot(self, project_id, citation_id):
        snapshot = self.snapshots.get(citation_id)
        if snapshot is None: return None
        return snapshot.model_copy(update={"citation": snapshot.citation.model_copy(update={"document_status": self.row.status})})


class Store:
    def __init__(self, item): self.item = item; self.failed = None; self.succeeded = None
    def load_index(self, value): return self.item
    def mark_running(self, item): return True
    def mark_succeeded(self, item, result): self.succeeded = result
    def mark_failed(self, item, error): self.failed = error


def service(repo=None, **kwargs):
    return KnowledgeService(repo or Repo(), artifact=Artifact(), scanner=Scanner(), audit=Audit(), **kwargs)


def test_unknown_fields_are_rejected_and_artifact_is_an_id_only():
    with pytest.raises(ValueError): KnowledgeDocumentCreate(project_id="p1", artifact_id="art1", title="x", source_type="internal", source_description="x", path="C:/secret")


def test_cross_project_is_checked_before_repository_query():
    with pytest.raises(KnowledgeNotFoundError): service().get("p2", "doc1", actor=ACTOR)


def test_index_requires_real_parser_scanner_and_queue():
    row = doc(status="UPLOADED", index_version=None)
    svc = KnowledgeService(Repo(row), artifact=Artifact(), scanner=Scanner(), parser=None, jobs=Jobs(), audit=Audit())
    with pytest.raises(KnowledgeUnavailableError): svc.index("p1", "doc1", actor=ACTOR, if_match='"etag-1"')


@pytest.mark.parametrize("missing", ["artifact", "scanner", "jobs", "repository"])
def test_missing_real_dependency_is_503(missing):
    kwargs = {"artifact": Artifact(), "scanner": Scanner(), "parser": Parser(), "jobs": Jobs(), "audit": Audit()}
    if missing == "repository":
        svc = KnowledgeService(None, **kwargs)
    else:
        kwargs[missing] = None
        svc = KnowledgeService(Repo(doc(status="UPLOADED", index_version=None)), **kwargs)
    with pytest.raises(KnowledgeUnavailableError): svc.index("p1", "doc1", actor=ACTOR, if_match='"etag-1"')


def test_worker_failure_keeps_previous_index_visible():
    repo = Repo(doc(status="READY", index_version="1")); item = {"project_id": "p1", "document_id": "doc1", "index_run_id": "run1", "status": "QUEUED", "attempt": 1}
    store = Store(item)
    result = KnowledgeWorker(repository=repo, artifact=Artifact(), scanner=Scanner(), parser=Parser(fail=True), store=store).handle(item, attempt=1)
    assert result.status == "FAILED" and store.failed is not None
    assert repo.row.index_version == "1" and repo.row.status == "FAILED"


def test_worker_success_publishes_a_new_immutable_version_atomically():
    repo = Repo(doc(status="READY", index_version="1")); item = {"project_id": "p1", "document_id": "doc1", "index_run_id": "run1", "status": "QUEUED", "attempt": 1}; store = Store(item)
    result = KnowledgeWorker(repository=repo, artifact=Artifact(), scanner=Scanner(), parser=Parser(), store=store).handle(item, attempt=1)
    assert result.status == "SUCCEEDED" and repo.published[0][1].document_version == "2"
    assert repo.row.index_version == "2"


def test_search_snapshot_is_stable_and_archive_is_visible_in_history():
    repo = Repo(); svc = service(repo, confirmation=Confirmation())
    result = svc.search(KnowledgeSearch(project_id="p1", query="method"), actor=ACTOR)
    assert result.results[0].document_version == "1"
    archived = svc.archive("p1", "doc1", confirmation_id="confirm-1", actor=ACTOR, if_match='"etag-1"')
    assert archived.status == "ARCHIVED"
    historical = svc.citation("p1", "cite1", actor=ACTOR)
    assert historical.document_status == "ARCHIVED"  # history reports the current real document state


def test_worker_redelivery_of_terminal_job_is_a_replay():
    repo = Repo(); item = {"project_id": "p1", "document_id": "doc1", "index_run_id": "run1", "status": "SUCCEEDED", "attempt": 1}; store = Store(item)
    result = KnowledgeWorker(repository=repo, artifact=Artifact(), scanner=Scanner(), parser=Parser(), store=store).handle(item, attempt=1)
    assert result.status == "SUCCEEDED" and result.replayed is True


def test_worker_first_successful_index_starts_at_version_one():
    repo = Repo(doc(status="UPLOADED", index_version=None, version=1)); item = {"project_id": "p1", "document_id": "doc1", "index_run_id": "run1", "status": "QUEUED", "attempt": 1}; store = Store(item)
    result = KnowledgeWorker(repository=repo, artifact=Artifact(), scanner=Scanner(), parser=Parser(), store=store).handle(item, attempt=1)
    assert result.status == "SUCCEEDED" and repo.published[0][1].document_version == "1"


def test_worker_rejects_stale_dispatch_without_running():
    repo = Repo(); item = {"project_id": "p1", "document_id": "doc1", "index_run_id": "run1", "dispatch_id": "dispatch-current", "status": "QUEUED", "attempt": 1}; store = Store(item)
    result = KnowledgeWorker(repository=repo, artifact=Artifact(), scanner=Scanner(), parser=Parser(), store=store).handle(item, attempt=1, dispatch_id="dispatch-old")
    assert result.replayed is True and result.status == "QUEUED" and store.succeeded is None


def test_worker_error_does_not_persist_dependency_exception_text():
    class LeakingParser(Parser):
        def parse(self, **kwargs):
            raise KnowledgeUnavailableError("C:/private/customer-secret.pdf")

    repo = Repo(); item = {"project_id": "p1", "document_id": "doc1", "index_run_id": "run1", "status": "QUEUED", "attempt": 1}; store = Store(item)
    result = KnowledgeWorker(repository=repo, artifact=Artifact(), scanner=Scanner(), parser=LeakingParser(), store=store).handle(item, attempt=1)
    assert result.error is not None and "customer-secret" not in str(result.error) and result.error["code"] == "KNOWLEDGE_DEPENDENCY_UNAVAILABLE"


def test_worker_rejects_parser_content_hash_mismatch():
    class BadParser(Parser):
        def parse(self, **kwargs):
            parsed = super().parse(**kwargs)
            return parsed.model_copy(update={"content_sha256": "f" * 64})

    repo = Repo(); item = {"project_id": "p1", "document_id": "doc1", "index_run_id": "run1", "status": "QUEUED", "attempt": 1}; store = Store(item)
    result = KnowledgeWorker(repository=repo, artifact=Artifact(), scanner=Scanner(), parser=BadParser(), store=store).handle(item, attempt=1)
    assert result.status == "FAILED" and repo.row.index_version == "1"


def test_document_error_dto_redacts_legacy_storage_details():
    row = doc(status="FAILED").model_copy(update={"error": {"code": "INTERNAL", "message": "C:/private/secret.sql", "traceback": "SELECT ..."}})
    result = service(Repo(row)).get("p1", "doc1", actor=ACTOR)
    assert result.error == {"code": "KNOWLEDGE_INDEX_FAILED", "message": "知识索引失败", "retryable": False}


def test_mysql_chunk_index_declares_ngram_parser():
    index = next(index for index in KnowledgeChunkRow.__table__.indexes if index.name == "ix_knowledge_chunk_content_ft")
    assert index.dialect_options["mysql"]["prefix"] == "FULLTEXT"
    assert index.dialect_options["mysql"]["with_parser"] == "ngram"
    ordinal = next(index for index in KnowledgeChunkRow.__table__.indexes if index.name == "ix_knowledge_chunk_document_version_ordinal")
    assert ordinal.unique is True


def test_knowledge_router_exposes_required_write_headers_once_and_typed_results():
    app = FastAPI()
    app.include_router(create_router())
    operation = app.openapi()["paths"]["/knowledge/documents/{document_id}/index"]["post"]
    headers = [parameter for parameter in operation["parameters"] if parameter["in"] == "header"]
    assert [(parameter["name"], parameter["required"]) for parameter in headers] == [("If-Match", True), ("Idempotency-Key", True)]
    assert "KnowledgeAccepted" in str(operation["responses"]["202"])


def test_router_checks_project_scope_before_idempotency_lookup():
    class Probe:
        def __init__(self): self.lookups = 0
        def get(self, scope): self.lookups += 1; return None

    probe = Probe()
    app = FastAPI()
    app.state.actor_context = ACTOR
    app.include_router(create_router(idempotency_store=probe))
    route = next(route for route in app.routes if getattr(route, "path", None) == "/knowledge/documents" and "POST" in route.methods)
    request = Request({"type": "http", "method": "POST", "path": "/knowledge/documents", "headers": [], "app": app})
    payload = KnowledgeDocumentCreate(project_id="p2", artifact_id="art1", title="x", source_type="internal", source_description="x")
    with pytest.raises(APIError) as error:
        route.endpoint(payload=payload, request=request, response=Response(), idempotency_key="probe")
    assert error.value.status_code == 404 and probe.lookups == 0
