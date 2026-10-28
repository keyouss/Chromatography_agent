from __future__ import annotations

from datetime import datetime, timezone

from chrom_platform.core.cursor import CursorCodec, CursorError
from chrom_platform.modules.knowledge.schemas import KnowledgeDocument, KnowledgeIndexRun
from chrom_platform.modules.knowledge.worker import KnowledgeWorker


def test_knowledge_cursor_is_opaque_and_scope_bound() -> None:
    codec = CursorCodec()
    token = codec.encode(after_id="doc-2", scope="knowledge-documents:p1::READY")
    assert codec.decode(token, scope="knowledge-documents:p1::READY") == "doc-2"
    try:
        codec.decode(token, scope="knowledge-documents:p2::READY")
    except CursorError:
        pass
    else:
        raise AssertionError("cursor from another project must be rejected")


class _CompensatedIndexRepository:
    def __init__(self) -> None:
        now = datetime.now(timezone.utc)
        self.document = KnowledgeDocument(
            id="doc-1",
            project_id="p1",
            title="Methods",
            artifact_id="artifact-1",
            version=1,
            sha256="a" * 64,
            status="FAILED",
            parser_version="parser-1",
            index_version="1",
            chunk_count=1,
            job_id=None,
            revision=2,
            etag='"failed"',
            created_at=now,
        )
        self.run = KnowledgeIndexRun(
            id="run-1",
            document_id="doc-1",
            status="FAILED",
            strategy_id="paragraph_v1",
            chunk_count=0,
            error={"code": "KNOWLEDGE_INDEX_FAILED"},
            created_at=now,
            finished_at=now,
        )
        self.published = False

    def get_document(self, project_id: str, document_id: str, *, for_update: bool = False):
        return self.document if (project_id, document_id) == ("p1", "doc-1") else None

    def get_index_run(self, project_id: str, run_id: str):
        return self.run if (project_id, run_id) == ("p1", "run-1") else None

    def publish_index(self, *args, **kwargs):
        self.published = True
        raise AssertionError("a compensated worker must not publish")


class _CompensatedIndexStore:
    def __init__(self, item: dict[str, object]) -> None:
        self.item = item
        self.running = False
        self.failed = None

    def load_index(self, value):
        return self.item

    def mark_running(self, item):
        self.running = True
        return True

    def mark_failed(self, item, error):
        self.failed = error

    def mark_succeeded(self, item, result):
        raise AssertionError("a compensated worker must not succeed")


def test_worker_fences_stale_worker_after_enqueue_compensation() -> None:
    item = {
        "project_id": "p1",
        "document_id": "doc-1",
        "index_run_id": "run-1",
        "job_id": "job-1",
        "dispatch_id": "dispatch-1",
        "status": "QUEUED",
        "attempt": 1,
    }
    repository = _CompensatedIndexRepository()
    store = _CompensatedIndexStore(item)

    result = KnowledgeWorker(repository=repository, artifact=None, scanner=None, parser=None, store=store).handle(
        item, attempt=1, dispatch_id="dispatch-1"
    )

    assert result.status == "FAILED"
    assert result.replayed is True
    assert result.error == {"code": "KNOWLEDGE_INDEX_STALE", "message": "知识索引任务已失效", "retryable": False}
    assert store.running is False
    assert store.failed == result.error
    assert repository.published is False
