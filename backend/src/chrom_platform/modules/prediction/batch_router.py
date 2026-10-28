"""HTTP adapter for the type-specific batch prediction workflow."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import APIRouter, Header, Query, Request, Response, status
from fastapi.responses import Response as RawResponse

from chrom_platform.core.dto import ActorContext, ListEnvelope, PageInfo, SuccessEnvelope
from chrom_platform.core.errors import APIError
from chrom_platform.core.idempotency import BeginResult, IdempotencyScope
from chrom_platform.task_core.service import OperationContext

from .batch import (
    BatchCancel,
    BatchAccepted,
    BatchDraft,
    BatchDraftCreate,
    BatchDraftPatch,
    BatchEmpty,
    BatchExportAccepted,
    BatchExportCreate,
    BatchExportView,
    BatchPrediction,
    BatchRetryFailed,
    BatchRetryPlan,
    BatchRetryPlanCreate,
    BatchRowsPage,
    BatchSubmit,
    BatchValidation,
    BatchValidationAccepted,
    BatchValidationRowsPage,
)
from .batch_service import BatchPredictionService, BatchServiceError


_IF_MATCH = {"in": "header", "name": "If-Match", "required": True, "schema": {"type": "string", "minLength": 1, "maxLength": 256}}
_IDEMPOTENCY = {"in": "header", "name": "Idempotency-Key", "required": True, "schema": {"type": "string", "minLength": 1, "maxLength": 128}}
_ASYNC = {"202": {"description": "异步受理", "headers": {"Location": {"required": True, "schema": {"type": "string", "format": "uri-reference"}}}}}
_CURSOR_QUERY = {"in": "query", "name": "cursor", "required": False, "schema": {"anyOf": [{"type": "string", "minLength": 1, "maxLength": 1024}, {"type": "null"}]}}


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "batch-request")


def _envelope(request: Request, value: Any) -> SuccessEnvelope[Any]:
    return SuccessEnvelope(data=value, warnings=[], request_id=_request_id(request))


def _actor(request: Request) -> ActorContext:
    value = getattr(request.app.state, "actor_context", None)
    if not isinstance(value, ActorContext):
        raise APIError(503, "DEPENDENCY_UNAVAILABLE", "ActorContext 未配置", retryable=True)
    return value


def _ctx(request: Request) -> OperationContext:
    return OperationContext(actor=_actor(request), interaction=getattr(request.app.state, "interaction", "human"), request_id=_request_id(request), conversation_id=getattr(request.state, "conversation_id", None), agent_run_id=getattr(request.state, "agent_run_id", None))


def _cursor_input(cursor: str | None) -> str | None:
    if cursor is not None and (not cursor or len(cursor) > 1024):
        raise APIError(400, "VALIDATION_FAILED", "分页游标无效")
    return cursor


def _header(value: str | None, name: str, *, status_code: int) -> str:
    if value is None:
        raise APIError(status_code, "IF_MATCH_REQUIRED" if name == "If-Match" else "IDEMPOTENCY_KEY_REQUIRED", f"缺少 {name}")
    if not value or len(value) > (256 if name == "If-Match" else 128):
        raise APIError(422, "VALIDATION_FAILED", f"{name} 长度无效")
    return value


def _service(request: Request, injected: BatchPredictionService | None) -> BatchPredictionService:
    bound = getattr(request.state, "m2_assembly", None)
    value = injected or getattr(request.state, "batch_prediction_service", None) or getattr(bound, "batch_prediction_service", None) or getattr(request.app.state, "batch_prediction_service", None)
    if value is None:
        raise APIError(503, "DEPENDENCY_UNAVAILABLE", "批量预测服务未配置", retryable=True)
    return value


def _raise(exc: BatchServiceError) -> None:
    raise APIError(exc.status_code, exc.code, exc.message, retryable=exc.retryable, details=exc.details)


def _reserve(request: Request, *, actor: ActorContext, project_id: str, method: str, route: str, key: str, payload: Any):
    store = getattr(request.state, "idempotency_store", None) or getattr(request.app.state, "idempotency_store", None)
    if store is None:
        raise APIError(503, "DEPENDENCY_UNAVAILABLE", "幂等记录存储未配置", retryable=True)
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
    scope = IdempotencyScope(actor_id=actor.actor_id, project_id=project_id, method=method, route_identity=route, key=key)
    old = store.get(scope)
    if old is not None:
        if old.payload_hash != digest:
            raise APIError(409, "IDEMPOTENCY_KEY_REUSED", "幂等键已用于其他请求")
        if old.status == "IN_PROGRESS":
            raise APIError(409, "OPERATION_IN_PROGRESS", "相同幂等请求正在处理中", retryable=True)
        if old.status == "FAILED":
            # A FAILED record has no successful response to replay.  Re-enter
            # the store's atomic begin path so SQL and in-memory stores can
            # either reset the same semantic request or report a stable retry
            # conflict; never feed an empty response into a Pydantic model.
            result = store.begin(scope, digest)
            if isinstance(result, BeginResult) and result.acquired:
                return store, scope, digest, None
            record = result.record if isinstance(result, BeginResult) else None
            if record is not None and record.status == "IN_PROGRESS":
                raise APIError(409, "OPERATION_IN_PROGRESS", "相同幂等请求正在处理中", retryable=True)
            raise APIError(409, "STATE_CONFLICT", "上次幂等请求失败，请重试", retryable=True)
        return store, scope, digest, old
    result = store.begin(scope, digest)
    if not isinstance(result, BeginResult) or not result.acquired:
        record = result.record if isinstance(result, BeginResult) else None
        if record is None:
            raise APIError(503, "DEPENDENCY_UNAVAILABLE", "幂等存储返回无效结果", retryable=True)
        if record.payload_hash != digest:
            raise APIError(409, "IDEMPOTENCY_KEY_REUSED", "幂等键已用于其他请求")
        if record.status == "IN_PROGRESS":
            raise APIError(409, "OPERATION_IN_PROGRESS", "相同幂等请求正在处理中", retryable=True)
        return store, scope, digest, record
    return store, scope, digest, None


def _complete(store: object, scope: IdempotencyScope, digest: str, result: Any, status_code: int) -> None:
    store.complete(scope, digest, status_code, result.model_dump(mode="json"))


def _fail(store: object | None, scope: IdempotencyScope | None, digest: str | None, code: str) -> None:
    if store is not None and scope is not None and digest is not None:
        try:
            public_code = code if code in {"DEPENDENCY_UNAVAILABLE", "VALIDATION_FAILED", "STATE_CONFLICT", "OPERATION_TIMEOUT"} else "STATE_CONFLICT"
            store.fail(scope, digest, public_code)
        except Exception:
            pass


def _batch_reserve(request: Request, service: BatchPredictionService, ctx: OperationContext, batch_id: str, *, key: str, route: str, payload: Any):
    row = service._get(ctx, batch_id)
    return _reserve(request, actor=ctx.actor, project_id=row.project_id, method="POST", route=route, key=key, payload={"batch_id": batch_id, "request": payload})


def _replayed(response: Response, old: Any, model: Any) -> SuccessEnvelope[Any]:
    response.headers["Idempotency-Replayed"] = "true"
    stored = old.response or {}
    if old.status != "COMPLETED" or not isinstance(stored, dict) or "data" not in stored:
        raise APIError(503, "DEPENDENCY_UNAVAILABLE", "幂等成功响应不可重放", retryable=True)
    return SuccessEnvelope(data=model.model_validate(stored.get("data")), warnings=stored.get("warnings", []), request_id=stored.get("request_id", "batch-replay"))


def create_router(*, service: BatchPredictionService | None = None, template_registry: object | None = None) -> APIRouter:
    router = APIRouter(tags=["batch-prediction"])

    @router.get(
        "/prediction/types/{chromatography_type}/batch-template",
        operation_id="download_batch_template",
        response_class=RawResponse,
        responses={200: {
            "description": "Batch template file",
            "content": {
                "text/csv": {"schema": {"type": "string", "format": "binary"}},
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {
                    "schema": {"type": "string", "format": "binary"},
                },
            },
        }},
    )
    def download_template(chromatography_type: str, request: Request, tool: str = Query("batch", min_length=1, max_length=32), model_id: str | None = Query(None, min_length=1, max_length=96), template_version: str | None = Query(None, min_length=1, max_length=64), format: str = Query("csv", pattern="^(csv|xlsx)$")):
        registry = template_registry or getattr(request.app.state, "batch_template_registry", None)
        if registry is None:
            raise APIError(503, "DEPENDENCY_UNAVAILABLE", "批量模板注册表未配置", retryable=True)
        render = getattr(registry, "render", None)
        if not callable(render):
            raise APIError(503, "DEPENDENCY_UNAVAILABLE", "批量模板制品未配置", retryable=True)
        try:
            kwargs = {"tool": tool, "model_id": model_id, "format": format}
            if template_version is not None:
                kwargs["version"] = template_version
            content, version, digest = render(chromatography_type, **kwargs)
        except (BatchServiceError, KeyError, ValueError) as exc:
            if not isinstance(exc, BatchServiceError):
                raise APIError(422, "BATCH_TEMPLATE_UNSUPPORTED", "批量模板不存在或格式不受支持") from exc
            _raise(exc)
        media = "text/csv; charset=utf-8" if format == "csv" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        return RawResponse(content=content, media_type=media, headers={"X-Template-Version": version, "X-Template-SHA256": digest, "X-Content-Type-Options": "nosniff"})

    @router.post("/batch-predictions/drafts", response_model=SuccessEnvelope[BatchDraft], status_code=status.HTTP_201_CREATED, operation_id="create_batch_prediction_draft", openapi_extra={"parameters": [_IDEMPOTENCY]})
    def create_draft(payload: BatchDraftCreate, request: Request, response: Response, idempotency_key: str | None = Header(None, alias="Idempotency-Key", include_in_schema=False)):
        key = _header(idempotency_key, "Idempotency-Key", status_code=400)
        actor = _actor(request)
        store = scope = digest = None
        try:
            store, scope, digest, old = _reserve(request, actor=actor, project_id=payload.project_id, method="POST", route="/api/v1/batch-predictions/drafts", key=key, payload=payload.model_dump(mode="json"))
            if old is not None:
                response.headers["Idempotency-Replayed"] = "true"
                result = SuccessEnvelope[BatchDraft].model_validate(old.response or {})
                response.headers["ETag"] = result.data.etag
                response.headers["Location"] = f"/api/v1/batch-predictions/{result.data.id}"
                return result
            result = _envelope(request, _service(request, service).create(_ctx(request), payload)); response.headers["ETag"] = result.data.etag; response.headers["Location"] = f"/api/v1/batch-predictions/{result.data.id}"; _complete(store, scope, digest, result, 201); return result
        except BatchServiceError as exc:
            _fail(store, scope, digest, exc.code)
            _raise(exc)

    @router.get("/batch-predictions/{batch_id}", response_model=SuccessEnvelope[BatchDraft], operation_id="get_batch_prediction")
    def get_batch(batch_id: str, request: Request, response: Response):
        try:
            result = _envelope(request, _service(request, service).get(_ctx(request), batch_id)); response.headers["ETag"] = result.data.etag; return result
        except BatchServiceError as exc:
            _raise(exc)

    @router.patch("/batch-predictions/{batch_id}", response_model=SuccessEnvelope[BatchDraft], operation_id="patch_batch_prediction", openapi_extra={"parameters": [_IF_MATCH, _IDEMPOTENCY]})
    def patch_batch(batch_id: str, payload: BatchDraftPatch, request: Request, response: Response, if_match: str | None = Header(None, alias="If-Match", include_in_schema=False), idempotency_key: str | None = Header(None, alias="Idempotency-Key", include_in_schema=False)):
        match, key = _header(if_match, "If-Match", status_code=428), _header(idempotency_key, "Idempotency-Key", status_code=400)
        store = scope = digest = None
        try:
            svc, ctx = _service(request, service), _ctx(request)
            store, scope, digest, old = _batch_reserve(request, svc, ctx, batch_id, key=key, route="/api/v1/batch-predictions/{id}", payload={"payload": payload.model_dump(mode="json"), "if_match": match})
            if old is not None:
                result = _replayed(response, old, BatchDraft)
            else:
                result = _envelope(request, svc.patch(ctx, batch_id, payload, if_match=match)); _complete(store, scope, digest, result, 200)
            response.headers["ETag"] = result.data.etag
            return result
        except BatchServiceError as exc:
            _fail(store, scope, digest, exc.code)
            _raise(exc)

    @router.post("/batch-predictions/{batch_id}/validate", response_model=SuccessEnvelope[BatchValidationAccepted], status_code=status.HTTP_202_ACCEPTED, operation_id="validate_batch_prediction", openapi_extra={"parameters": [_IF_MATCH, _IDEMPOTENCY], "responses": _ASYNC})
    def validate_batch(batch_id: str, payload: BatchEmpty, request: Request, response: Response, if_match: str | None = Header(None, alias="If-Match", include_in_schema=False), idempotency_key: str | None = Header(None, alias="Idempotency-Key", include_in_schema=False)):
        match, key = _header(if_match, "If-Match", status_code=428), _header(idempotency_key, "Idempotency-Key", status_code=400)
        store = scope = digest = None
        try:
            svc, ctx = _service(request, service), _ctx(request)
            store, scope, digest, old = _batch_reserve(request, svc, ctx, batch_id, key=key, route="/api/v1/batch-predictions/{id}/validate", payload={"payload": payload.model_dump(mode="json"), "if_match": match})
            if old is not None:
                result = _replayed(response, old, BatchValidationAccepted)
            else:
                result = _envelope(request, svc.validate(ctx, batch_id, if_match=match)); _complete(store, scope, digest, result, 202)
            response.headers["Location"] = f"/api/v1/batch-predictions/{batch_id}/validation"
            return result
        except BatchServiceError as exc:
            _fail(store, scope, digest, exc.code)
            _raise(exc)

    @router.get("/batch-predictions/{batch_id}/validation", response_model=SuccessEnvelope[BatchValidation], operation_id="get_batch_prediction_validation")
    def get_validation(batch_id: str, request: Request):
        try:
            return _envelope(request, _service(request, service).validation(_ctx(request), batch_id))
        except BatchServiceError as exc:
            _raise(exc)

    @router.get("/batch-predictions/{batch_id}/validation/rows", response_model=SuccessEnvelope[BatchValidationRowsPage], operation_id="list_batch_validation_rows", openapi_extra={"parameters": [_CURSOR_QUERY]})
    def validation_rows(batch_id: str, request: Request, status_filter: str | None = Query(None, alias="status", pattern="^(VALID|INVALID|WARNING)$"), cursor: str | None = Query(None, include_in_schema=False), limit: int = Query(50, ge=1, le=200)):
        try:
            cursor = _cursor_input(cursor)
            return _envelope(request, _service(request, service).validation_rows(_ctx(request), batch_id, status_filter=status_filter, cursor=cursor, limit=limit, cursor_codec=getattr(request.app.state, "batch_cursor_codec", None)))
        except BatchServiceError as exc:
            _raise(exc)

    @router.post("/batch-predictions/{batch_id}/submit", response_model=SuccessEnvelope[BatchAccepted], status_code=status.HTTP_202_ACCEPTED, operation_id="submit_batch_prediction", openapi_extra={"parameters": [_IDEMPOTENCY], "responses": _ASYNC})
    def submit_batch(batch_id: str, payload: BatchSubmit, request: Request, response: Response, idempotency_key: str | None = Header(None, alias="Idempotency-Key", include_in_schema=False)):
        key = _header(idempotency_key, "Idempotency-Key", status_code=400)
        store = scope = digest = None
        try:
            svc, ctx = _service(request, service), _ctx(request)
            store, scope, digest, old = _batch_reserve(request, svc, ctx, batch_id, key=key, route="/api/v1/batch-predictions/{id}/submit", payload=payload.model_dump(mode="json"))
            if old is not None:
                result = _replayed(response, old, BatchAccepted)
            else:
                result = _envelope(request, svc.submit(ctx, batch_id, payload)); _complete(store, scope, digest, result, 202)
            response.headers["Location"] = f"/api/v1/batch-predictions/{batch_id}"
            return result
        except BatchServiceError as exc:
            _fail(store, scope, digest, exc.code)
            _raise(exc)

    @router.get("/batch-predictions/{batch_id}/rows", response_model=SuccessEnvelope[BatchRowsPage], operation_id="list_batch_prediction_rows", openapi_extra={"parameters": [_CURSOR_QUERY]})
    def rows(batch_id: str, request: Request, status_filter: str | None = Query(None, alias="status"), cursor: str | None = Query(None, include_in_schema=False), limit: int = Query(50, ge=1, le=200)):
        try:
            cursor = _cursor_input(cursor)
            return _envelope(request, _service(request, service).rows(_ctx(request), batch_id, status_filter=status_filter, cursor=cursor, limit=limit, cursor_codec=getattr(request.app.state, "batch_cursor_codec", None)))
        except BatchServiceError as exc:
            _raise(exc)

    @router.post("/batch-predictions/{batch_id}/cancel", response_model=SuccessEnvelope[BatchDraft], status_code=status.HTTP_202_ACCEPTED, operation_id="cancel_batch_prediction", openapi_extra={"parameters": [_IF_MATCH, _IDEMPOTENCY], "responses": _ASYNC})
    def cancel_batch(batch_id: str, payload: BatchCancel, request: Request, response: Response, if_match: str | None = Header(None, alias="If-Match", include_in_schema=False), idempotency_key: str | None = Header(None, alias="Idempotency-Key", include_in_schema=False)):
        match, key = _header(if_match, "If-Match", status_code=428), _header(idempotency_key, "Idempotency-Key", status_code=400)
        store = scope = digest = None
        try:
            svc, ctx = _service(request, service), _ctx(request)
            store, scope, digest, old = _batch_reserve(request, svc, ctx, batch_id, key=key, route="/api/v1/batch-predictions/{id}/cancel", payload={"payload": payload.model_dump(mode="json"), "if_match": match})
            if old is not None:
                result = _replayed(response, old, BatchDraft)
            else:
                result = _envelope(request, svc.cancel(ctx, batch_id, payload, if_match=match)); _complete(store, scope, digest, result, 202)
            response.headers["ETag"] = result.data.etag; response.headers["Location"] = f"/api/v1/batch-predictions/{batch_id}"; return result
        except BatchServiceError as exc:
            _fail(store, scope, digest, exc.code)
            _raise(exc)

    @router.post("/batch-predictions/{batch_id}/retry-plans", response_model=SuccessEnvelope[BatchRetryPlan], status_code=status.HTTP_201_CREATED, operation_id="create_batch_retry_plan", openapi_extra={"parameters": [_IF_MATCH, _IDEMPOTENCY]})
    def retry_plan(batch_id: str, payload: BatchRetryPlanCreate, request: Request, response: Response, if_match: str | None = Header(None, alias="If-Match", include_in_schema=False), idempotency_key: str | None = Header(None, alias="Idempotency-Key", include_in_schema=False)):
        match, key = _header(if_match, "If-Match", status_code=428), _header(idempotency_key, "Idempotency-Key", status_code=400)
        store = scope = digest = None
        try:
            svc, ctx = _service(request, service), _ctx(request)
            store, scope, digest, old = _batch_reserve(request, svc, ctx, batch_id, key=key, route="/api/v1/batch-predictions/{id}/retry-plans", payload={"payload": payload.model_dump(mode="json"), "if_match": match})
            if old is not None:
                result = _replayed(response, old, BatchRetryPlan)
            else:
                result = _envelope(request, svc.create_retry_plan(ctx, batch_id, payload, if_match=match)); _complete(store, scope, digest, result, 201)
            response.headers["ETag"] = result.data.etag; return result
        except BatchServiceError as exc:
            _fail(store, scope, digest, exc.code)
            _raise(exc)

    @router.post("/batch-predictions/{batch_id}/retry-failed", response_model=SuccessEnvelope[BatchAccepted], status_code=status.HTTP_202_ACCEPTED, operation_id="retry_failed_batch_prediction", openapi_extra={"parameters": [_IF_MATCH, _IDEMPOTENCY], "responses": _ASYNC})
    def retry_failed(batch_id: str, payload: BatchRetryFailed, request: Request, response: Response, if_match: str | None = Header(None, alias="If-Match", include_in_schema=False), idempotency_key: str | None = Header(None, alias="Idempotency-Key", include_in_schema=False)):
        match, key = _header(if_match, "If-Match", status_code=428), _header(idempotency_key, "Idempotency-Key", status_code=400)
        store = scope = digest = None
        try:
            svc, ctx = _service(request, service), _ctx(request)
            store, scope, digest, old = _batch_reserve(request, svc, ctx, batch_id, key=key, route="/api/v1/batch-predictions/{id}/retry-failed", payload={"payload": payload.model_dump(mode="json"), "if_match": match})
            if old is not None:
                result = _replayed(response, old, BatchAccepted)
            else:
                result = _envelope(request, svc.retry_failed(ctx, batch_id, payload, if_match=match)); _complete(store, scope, digest, result, 202)
            response.headers["Location"] = f"/api/v1/batch-predictions/{batch_id}"; return result
        except BatchServiceError as exc:
            _fail(store, scope, digest, exc.code)
            _raise(exc)

    @router.post("/batch-predictions/{batch_id}/exports", response_model=SuccessEnvelope[BatchExportAccepted], status_code=status.HTTP_202_ACCEPTED, operation_id="export_batch_prediction", openapi_extra={"parameters": [_IF_MATCH, _IDEMPOTENCY], "responses": _ASYNC})
    def export_batch(batch_id: str, payload: BatchExportCreate, request: Request, response: Response, if_match: str | None = Header(None, alias="If-Match", include_in_schema=False), idempotency_key: str | None = Header(None, alias="Idempotency-Key", include_in_schema=False)):
        match, key = _header(if_match, "If-Match", status_code=428), _header(idempotency_key, "Idempotency-Key", status_code=400)
        store = scope = digest = None
        try:
            svc, ctx = _service(request, service), _ctx(request)
            store, scope, digest, old = _batch_reserve(request, svc, ctx, batch_id, key=key, route="/api/v1/batch-predictions/{id}/exports", payload={"payload": payload.model_dump(mode="json"), "if_match": match})
            if old is not None:
                result = _replayed(response, old, BatchExportAccepted)
            else:
                result = _envelope(request, svc.export(ctx, batch_id, payload, if_match=match)); _complete(store, scope, digest, result, 202)
            response.headers["Location"] = f"/api/v1/batch-predictions/exports/{result.data.id}"; return result
        except BatchServiceError as exc:
            _fail(store, scope, digest, exc.code)
            _raise(exc)

    @router.get("/batch-predictions/exports/{export_id}", response_model=SuccessEnvelope[BatchExportView], operation_id="get_batch_prediction_export")
    def get_batch_export(export_id: str, request: Request):
        try:
            return _envelope(request, _service(request, service).get_export(_ctx(request), export_id))
        except BatchServiceError as exc:
            _raise(exc)

    @router.get(
        "/batch-predictions/exports/{export_id}/download",
        operation_id="download_batch_prediction_export",
        response_class=RawResponse,
        responses={200: {
            "description": "Completed batch export file",
            "content": {
                "application/json": {"schema": {"type": "string", "format": "binary"}},
                "text/csv": {"schema": {"type": "string", "format": "binary"}},
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {
                    "schema": {"type": "string", "format": "binary"},
                },
            },
        }},
    )
    def download_batch_export(export_id: str, request: Request):
        try:
            content, media_type, filename = _service(request, service).download_export(_ctx(request), export_id)
            return RawResponse(content=content, media_type=media_type, headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-Content-Type-Options": "nosniff",
            })
        except BatchServiceError as exc:
            _raise(exc)

    return router


__all__ = ["create_router"]
