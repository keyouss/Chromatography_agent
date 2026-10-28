"""FastAPI adapter for the knowledge vertical slice.

The factory is deliberately unregistered: application assembly decides whether
the knowledge module is in a build and supplies real ports through arguments or
``app.state``.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from fastapi import APIRouter, Header, Query, Request, Response, status

from chrom_platform.core.dto import ActorContext, ListEnvelope, PageInfo, SuccessEnvelope
from chrom_platform.core.cursor import CursorCodec, CursorError
from chrom_platform.core.errors import APIError
from chrom_platform.core.idempotency import BeginResult, IdempotencyRecord, IdempotencyScope

from .errors import KnowledgeError
from .schemas import (
    ArchiveRequest,
    Citation,
    EmptyRequest,
    KnowledgeAccepted,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentCreate,
    KnowledgeSearch,
    KnowledgeSearchResult,
)
from .service import KnowledgeService


_ASYNC_RESPONSES = {"202": {"description": "异步受理", "headers": {"Location": {"required": True, "schema": {"type": "string", "format": "uri-reference"}}}}}
_IF_MATCH = {"in": "header", "name": "If-Match", "required": True, "schema": {"type": "string", "minLength": 1, "maxLength": 256}}
_IDEMPOTENCY = {"in": "header", "name": "Idempotency-Key", "required": True, "schema": {"type": "string", "minLength": 1, "maxLength": 128}}


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "service-request")


def _envelope(request: Request, value: Any) -> SuccessEnvelope[Any]:
    return SuccessEnvelope(data=value, warnings=[], request_id=_request_id(request))


def _actor(request: Request) -> ActorContext:
    value = getattr(request.app.state, "actor_context", None)
    if not isinstance(value, ActorContext):
        raise APIError(503, "DEPENDENCY_UNAVAILABLE", "ActorContext 未由服务端配置", retryable=True)
    return value


def _service(request: Request, explicit: KnowledgeService | None) -> KnowledgeService:
    value = explicit or getattr(request.state, "knowledge_service", None) or getattr(request.app.state, "knowledge_service", None)
    # Keep the adapter open to a deployment proxy that implements the stable
    # service protocol; only absence is an unavailable dependency.
    if value is None:
        raise APIError(503, "DEPENDENCY_UNAVAILABLE", "知识中心服务未配置", retryable=True)
    return value  # type: ignore[return-value]


def _scope(actor: ActorContext, project_id: str, *, permission: str, write: bool = False) -> None:
    """Check request scope before touching idempotency or business storage."""

    if not actor.can_access_project(project_id):
        raise APIError(404, "RESOURCE_NOT_FOUND", "资源不存在")
    if not actor.can(permission) and not actor.can("*"):
        raise APIError(403, "PERMISSION_DENIED", "缺少知识中心权限")


def _raise(exc: KnowledgeError) -> None:
    code = exc.code
    code_status = int(exc.details.get("status_code", exc.status_code)) if isinstance(exc.details, dict) else exc.status_code
    raise APIError(code_status, code, exc.message, retryable=exc.retryable, details=exc.details)


def _header(value: str | None, name: str, *, missing_status: int = 400) -> str:
    if value is None:
        raise APIError(missing_status, "HEADER_REQUIRED", f"缺少 {name}")
    if value == "" or len(value) > (128 if name == "Idempotency-Key" else 256):
        raise APIError(422, "VALIDATION_FAILED", f"{name} 长度无效")
    return value


def _cursor(value: str | None, scope: str) -> str | None:
    if not value:
        return None
    try:
        return CursorCodec().decode(value, scope=scope)
    except CursorError:
        raise APIError(400, "VALIDATION_FAILED", "cursor 无效或与筛选条件不匹配") from None


def _digest(route: str, payload: Any) -> str:
    raw = json.dumps({"route": route, "payload": payload}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _reserve(request: Request, explicit: object | None, *, actor: ActorContext, project_id: str, method: str, route: str, key: str, payload: Any) -> tuple[object, IdempotencyScope, str, IdempotencyRecord | None]:
    store = explicit or getattr(request.state, "idempotency_store", None) or getattr(request.app.state, "idempotency_store", None)
    if store is None:
        raise APIError(503, "DEPENDENCY_UNAVAILABLE", "幂等记录存储未配置", retryable=True)
    digest = _digest(route, payload)
    scope = IdempotencyScope(actor_id=actor.actor_id, project_id=project_id, method=method, route_identity=route, key=key)
    try:
        old = store.get(scope)
        if old is not None:
            if old.payload_hash != digest:
                raise APIError(409, "IDEMPOTENCY_KEY_REUSED", "幂等键已用于其他请求")
            if old.status == "IN_PROGRESS":
                raise APIError(409, "OPERATION_IN_PROGRESS", "相同幂等请求正在处理中", retryable=True)
            return store, scope, digest, old
        begin = store.begin(scope, digest)
    except APIError:
        raise
    except Exception as exc:
        raise APIError(503, "DEPENDENCY_UNAVAILABLE", "幂等存储暂不可用", retryable=True) from exc
    if not isinstance(begin, BeginResult) or not begin.acquired:
        record = begin.record if isinstance(begin, BeginResult) else None
        if record is None:
            raise APIError(503, "DEPENDENCY_UNAVAILABLE", "幂等存储返回无效结果", retryable=True)
        if record.payload_hash != digest:
            raise APIError(409, "IDEMPOTENCY_KEY_REUSED", "幂等键已用于其他请求")
        if record.status == "IN_PROGRESS":
            raise APIError(409, "OPERATION_IN_PROGRESS", "相同幂等请求正在处理中", retryable=True)
        return store, scope, digest, record
    return store, scope, digest, None


def _finish(store: object, scope: IdempotencyScope, digest: str, result: Any, status_code: int) -> None:
    try:
        store.complete(scope, digest, status_code, result.model_dump(mode="json"))
    except Exception as exc:
        raise APIError(503, "DEPENDENCY_UNAVAILABLE", "幂等记录暂不可用", retryable=True) from exc


def _replay(record: IdempotencyRecord, model: Any) -> Any:
    if not record.response:
        raise APIError(409, "STATE_CONFLICT", "幂等记录缺少响应")
    return model.model_validate_json(json.dumps(record.response, ensure_ascii=False))


def create_router(*, service: KnowledgeService | None = None, knowledge_service: KnowledgeService | None = None, idempotency_store: object | None = None) -> APIRouter:
    app_service = service or knowledge_service
    router = APIRouter(prefix="", tags=["knowledge"])

    @router.get("/knowledge/documents", response_model=ListEnvelope[KnowledgeDocument], operation_id="list_knowledge_documents")
    def list_documents(request: Request, project_id: str = Query(..., min_length=1, max_length=96), q: str | None = Query(None, min_length=1, max_length=2_000), status_filter: Literal["UPLOADED", "INDEXING", "READY", "FAILED", "NEEDS_OCR", "ARCHIVED"] | None = Query(None, alias="status"), limit: int = Query(50, ge=1, le=200), cursor: str | None = Query(None, min_length=1, max_length=512)) -> ListEnvelope[KnowledgeDocument]:
        try:
            scope = f"knowledge-documents:{project_id}:{q or ''}:{status_filter or ''}"
            cursor_value = _cursor(cursor, scope)
            after_id = cursor_value
            boundary_id = cursor_value
            if cursor_value:
                try:
                    boundary = json.loads(cursor_value)
                    if isinstance(boundary, dict) and isinstance(boundary.get("id"), str):
                        boundary_id = boundary["id"]
                except (TypeError, ValueError, json.JSONDecodeError):
                    pass
            values = _service(request, app_service).list(project_id, actor=_actor(request), q=q, status=status_filter, limit=limit + 1, after_id=after_id)
        except KnowledgeError as exc:
            _raise(exc)
        values = sorted(values, key=lambda item: (item.created_at, item.id), reverse=True)
        if after_id is not None:
            boundary = next((index for index, item in enumerate(values) if item.id == boundary_id), None)
            if boundary is not None:
                values = values[boundary + 1:]
        window = values[:limit]
        has_more = len(values) > limit
        next_cursor = None
        if has_more and window:
            boundary = json.dumps({"created_at": window[-1].created_at.isoformat(), "id": window[-1].id}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            next_cursor = CursorCodec().encode(after_id=boundary, scope=scope)
        return ListEnvelope(data=window, page=PageInfo(next_cursor=next_cursor, limit=limit, has_more=has_more), warnings=[], request_id=_request_id(request))

    @router.post("/knowledge/documents", response_model=SuccessEnvelope[KnowledgeDocument], status_code=status.HTTP_201_CREATED, operation_id="create_knowledge_document", openapi_extra={"parameters": [_IDEMPOTENCY]})
    def create_document(payload: KnowledgeDocumentCreate, request: Request, response: Response, idempotency_key: str | None = Header(None, alias="Idempotency-Key", include_in_schema=False)) -> SuccessEnvelope[KnowledgeDocument]:
        key = _header(idempotency_key, "Idempotency-Key")
        actor = _actor(request)
        _scope(actor, payload.project_id, permission="knowledge:write", write=True)
        store, scope, digest, old = _reserve(request, idempotency_store, actor=actor, project_id=payload.project_id, method="POST", route="/api/v1/knowledge/documents", key=key, payload=payload.model_dump(mode="json"))
        if old is not None:
            response.headers["Idempotency-Replayed"] = "true"
            replay = _replay(old, SuccessEnvelope[KnowledgeDocument])
            response.headers["ETag"] = replay.data.etag
            return replay
        try:
            result = _envelope(request, _service(request, app_service).create(payload, actor=actor)); response.headers["ETag"] = result.data.etag; _finish(store, scope, digest, result, 201); return result
        except KnowledgeError as exc:
            try: store.fail(scope, digest, exc.code)
            except Exception: pass
            _raise(exc)

    @router.get("/knowledge/documents/{document_id}", response_model=SuccessEnvelope[KnowledgeDocument], operation_id="get_knowledge_document")
    def get_document(document_id: str, request: Request, response: Response, project_id: str = Query(..., min_length=1, max_length=96)) -> SuccessEnvelope[KnowledgeDocument]:
        try:
            result = _envelope(request, _service(request, app_service).get(project_id, document_id, actor=_actor(request)))
        except KnowledgeError as exc: _raise(exc)
        response.headers["ETag"] = result.data.etag
        return result

    @router.post("/knowledge/documents/{document_id}/index", response_model=SuccessEnvelope[KnowledgeAccepted], status_code=status.HTTP_202_ACCEPTED, operation_id="index_knowledge_document", openapi_extra={"parameters": [_IF_MATCH, _IDEMPOTENCY], "responses": _ASYNC_RESPONSES})
    def index_document(document_id: str, payload: EmptyRequest, request: Request, response: Response, project_id: str = Query(..., min_length=1, max_length=96), if_match: str | None = Header(None, alias="If-Match", include_in_schema=False), idempotency_key: str | None = Header(None, alias="Idempotency-Key", include_in_schema=False)) -> SuccessEnvelope[KnowledgeAccepted]:
        match = _header(if_match, "If-Match", missing_status=428); key = _header(idempotency_key, "Idempotency-Key"); actor = _actor(request); _scope(actor, project_id, permission="knowledge:write", write=True)
        store, scope, digest, old = _reserve(request, idempotency_store, actor=actor, project_id=project_id, method="POST", route="/api/v1/knowledge/documents/{id}/index", key=key, payload={"document_id": document_id, "project_id": project_id, "if_match": match})
        if old is not None:
            response.headers["Idempotency-Replayed"] = "true"; replay = _replay(old, SuccessEnvelope[KnowledgeAccepted]); response.headers["Location"] = f"/api/v1/knowledge/documents/{document_id}"; return replay
        try:
            result = _envelope(request, _service(request, app_service).index(project_id, document_id, actor=actor, if_match=match)); response.headers["Location"] = f"/api/v1/knowledge/documents/{document_id}"; _finish(store, scope, digest, result, 202); return result
        except KnowledgeError as exc:
            try: store.fail(scope, digest, exc.code)
            except Exception: pass
            _raise(exc)

    @router.get("/knowledge/documents/{document_id}/chunks", response_model=ListEnvelope[KnowledgeChunk], operation_id="list_knowledge_chunks")
    def list_chunks(document_id: str, request: Request, project_id: str = Query(..., min_length=1, max_length=96), document_version: str | None = Query(None, min_length=1, max_length=64), limit: int = Query(100, ge=1, le=200), cursor: str | None = Query(None, min_length=1, max_length=512)) -> ListEnvelope[KnowledgeChunk]:
        try:
            scope = f"knowledge-chunks:{project_id}:{document_id}:{document_version or ''}"
            after = _cursor(cursor, scope)
            values = _service(request, app_service).chunks(project_id, document_id, actor=_actor(request), document_version=document_version, limit=limit + 1, after_ordinal=int(after) if after is not None else None)
        except KnowledgeError as exc: _raise(exc)
        window = values[:limit]
        more = len(values) > limit
        next_cursor = CursorCodec().encode(after_id=str(window[-1].ordinal), scope=scope) if more and window else None
        return ListEnvelope(data=window, page=PageInfo(next_cursor=next_cursor, limit=limit, has_more=more), warnings=[], request_id=_request_id(request))

    @router.post("/knowledge/documents/{document_id}/archive", response_model=SuccessEnvelope[KnowledgeDocument], operation_id="archive_knowledge_document", openapi_extra={"parameters": [_IDEMPOTENCY]})
    def archive_document(document_id: str, payload: ArchiveRequest, request: Request, response: Response, project_id: str = Query(..., min_length=1, max_length=96), idempotency_key: str | None = Header(None, alias="Idempotency-Key", include_in_schema=False)) -> SuccessEnvelope[KnowledgeDocument]:
        key = _header(idempotency_key, "Idempotency-Key"); actor = _actor(request); _scope(actor, project_id, permission="knowledge:write", write=True)
        store, scope, digest, old = _reserve(request, idempotency_store, actor=actor, project_id=project_id, method="POST", route="/api/v1/knowledge/documents/{id}/archive", key=key, payload={"document_id": document_id, "project_id": project_id, **payload.model_dump(mode="json")})
        if old is not None:
            response.headers["Idempotency-Replayed"] = "true"; replay = _replay(old, SuccessEnvelope[KnowledgeDocument]); response.headers["ETag"] = replay.data.etag; return replay
        try:
            result = _envelope(request, _service(request, app_service).archive(project_id, document_id, confirmation_id=payload.confirmation_id, actor=actor)); response.headers["ETag"] = result.data.etag; _finish(store, scope, digest, result, 200); return result
        except KnowledgeError as exc:
            try: store.fail(scope, digest, exc.code)
            except Exception: pass
            _raise(exc)

    @router.post("/knowledge/search", response_model=SuccessEnvelope[KnowledgeSearchResult], operation_id="search_knowledge")
    def search(payload: KnowledgeSearch, request: Request) -> SuccessEnvelope[KnowledgeSearchResult]:
        try: return _envelope(request, _service(request, app_service).search(payload, actor=_actor(request)))
        except KnowledgeError as exc: _raise(exc)

    @router.get("/knowledge/citations/{citation_id}", response_model=SuccessEnvelope[Citation], operation_id="get_knowledge_citation")
    def get_citation(citation_id: str, request: Request, project_id: str = Query(..., min_length=1, max_length=96)) -> SuccessEnvelope[Citation]:
        try: return _envelope(request, _service(request, app_service).citation(project_id, citation_id, actor=_actor(request)))
        except KnowledgeError as exc: _raise(exc)

    return router


create_knowledge_router = create_router

__all__ = ["create_knowledge_router", "create_router"]
