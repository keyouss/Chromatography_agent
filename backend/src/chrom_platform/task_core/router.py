"""Unregistered FastAPI router factory for task-core endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from chrom_platform.core.dto import ActorContext, ErrorEnvelope, ListEnvelope, PageInfo, SuccessEnvelope
from chrom_platform.core.errors import APIError

from .schemas import (
    ConfirmationCreateRequest,
    ConfirmationPreview,
    ConfirmationPreviewRequest,
    ConfirmationRevokeRequest,
    ConfirmationView,
    EventPage,
    JobAttemptView,
    JobEventView,
    JobCancelRequest,
    JobRetryRequest,
    JobView,
)
from .service import ConfirmationService, JobService, OperationContext, ServiceResult, TaskError
from .sse import check_job_event_replay_window, resolve_job_event_cursor, stream_job_event_frames


def _ctx(request: Request) -> OperationContext:
    actor: ActorContext | None = getattr(request.app.state, "actor_context", None)
    if actor is None:
        raise APIError(503, "DEPENDENCY_UNAVAILABLE", "ActorContext 未由服务端配置", retryable=True)
    return OperationContext(actor=actor, interaction="human", request_id=getattr(request.state, "request_id", "service-request"))


def _raise(exc: TaskError) -> None:
    raise APIError(exc.status_code, exc.code, exc.message, retryable=exc.retryable, details=exc.details)


def _header(request: Request, name: str, *, max_length: int, missing_status: int, missing_code: str) -> str:
    value = request.headers.get(name)
    if value is None:
        raise APIError(missing_status, missing_code, f"缺少 {name}")
    if len(value) > max_length:
        raise APIError(422, "VALIDATION_FAILED", f"{name} 长度无效")
    if value == "":
        raise APIError(422, "VALIDATION_FAILED", f"{name} 长度无效")
    return value


_IF_MATCH_OPENAPI = {"in": "header", "name": "If-Match", "required": True, "schema": {"type": "string", "minLength": 1, "maxLength": 256}}
_IDEMPOTENCY_OPENAPI = {"in": "header", "name": "Idempotency-Key", "required": True, "schema": {"type": "string", "minLength": 1, "maxLength": 128}}
_ASYNC_JOB_RESPONSE_OPENAPI = {
    "202": {
        "description": "任务已接受",
        "headers": {
            "Location": {
                "required": True,
                "schema": {"type": "string", "format": "uri-reference"},
            }
        },
    }
}


def create_router(*, job_service: JobService | None = None, confirmation_service: ConfirmationService | None = None) -> APIRouter:
    """Create the task router without registering it on the application.

    A service passed explicitly is useful for assembly and tests.  Otherwise
    the factory reads server-created services from ``app.state``.
    """

    router = APIRouter(tags=["tasks"])

    def jobs(request: Request) -> JobService:
        service = job_service or getattr(request.app.state, "job_service", None)
        if service is None:
            raise APIError(503, "DEPENDENCY_UNAVAILABLE", "Job 服务未配置", retryable=True)
        return service

    def confirmations(request: Request) -> ConfirmationService:
        service = confirmation_service or getattr(request.app.state, "confirmation_service", None)
        if service is None:
            raise APIError(503, "DEPENDENCY_UNAVAILABLE", "Confirmation 服务未配置", retryable=True)
        return service

    def job_write_dependency(request: Request) -> JobService:
        # Header guards run inside the dependency itself so a missing service
        # cannot mask the public 400/428/422 contract.  The handler repeats
        # the pure checks for direct endpoint calls used by lightweight tests.
        _header(request, "If-Match", max_length=256, missing_status=428, missing_code="IF_MATCH_REQUIRED")
        _header(request, "Idempotency-Key", max_length=128, missing_status=400, missing_code="IDEMPOTENCY_KEY_REQUIRED")
        return jobs(request)

    def confirmation_write_dependency(request: Request) -> ConfirmationService:
        _header(request, "If-Match", max_length=256, missing_status=428, missing_code="IF_MATCH_REQUIRED")
        _header(request, "Idempotency-Key", max_length=128, missing_status=400, missing_code="IDEMPOTENCY_KEY_REQUIRED")
        return confirmations(request)

    def confirmation_idempotency_dependency(request: Request) -> ConfirmationService:
        _header(request, "Idempotency-Key", max_length=128, missing_status=400, missing_code="IDEMPOTENCY_KEY_REQUIRED")
        return confirmations(request)

    def envelope(request: Request, data):
        return SuccessEnvelope(data=data, warnings=[], request_id=getattr(request.state, "request_id", "service-request"))

    @router.get("/jobs", response_model=ListEnvelope[JobView], operation_id="list_jobs")
    def list_jobs(
        request: Request,
        project_id: str = Query(..., min_length=1, max_length=96),
        status_filter: str | None = Query(default=None, alias="status"),
        kind: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
        cursor: str | None = Query(default=None, min_length=1, max_length=512),
        service: JobService = Depends(jobs),
    ):
        try:
            page = service.list_page(_ctx(request), project_id, status=status_filter, kind=kind, limit=limit, cursor=cursor)
        except TaskError as exc:
            _raise(exc)
        return ListEnvelope(data=page.jobs, page=PageInfo(next_cursor=page.next_cursor, limit=limit, has_more=page.has_more), warnings=[], request_id=getattr(request.state, "request_id", "service-request"))

    @router.get("/jobs/{job_id}", response_model=SuccessEnvelope[JobView], operation_id="get_job")
    def get_job(job_id: str, request: Request, service: JobService = Depends(jobs)):
        try:
            result = service.get(_ctx(request), job_id)
        except TaskError as exc:
            _raise(exc)
        return envelope(request, result)

    @router.post("/jobs/{job_id}/cancel", response_model=SuccessEnvelope[JobView], status_code=status.HTTP_202_ACCEPTED, operation_id="cancel_job", openapi_extra={"parameters": [_IF_MATCH_OPENAPI, _IDEMPOTENCY_OPENAPI], "responses": _ASYNC_JOB_RESPONSE_OPENAPI})
    def cancel_job(
        job_id: str,
        payload: JobCancelRequest,
        request: Request,
        response: Response,
        service: JobService = Depends(job_write_dependency),
    ):
        try:
            if_match = _header(request, "If-Match", max_length=256, missing_status=428, missing_code="IF_MATCH_REQUIRED")
            idempotency_key = _header(request, "Idempotency-Key", max_length=128, missing_status=400, missing_code="IDEMPOTENCY_KEY_REQUIRED")
            result = service.cancel(_ctx(request), job_id, if_match=if_match, idempotency_key=idempotency_key, reason=payload.reason)
        except TaskError as exc:
            _raise(exc)
        response.headers["Location"] = f"/api/v1/jobs/{result.data.id}"
        if result.replayed:
            response.headers["Idempotency-Replayed"] = "true"
        return envelope(request, result.data)

    @router.post("/jobs/{job_id}/retry", response_model=SuccessEnvelope[JobView], status_code=status.HTTP_202_ACCEPTED, operation_id="retry_job", openapi_extra={"parameters": [_IF_MATCH_OPENAPI, _IDEMPOTENCY_OPENAPI], "responses": _ASYNC_JOB_RESPONSE_OPENAPI})
    def retry_job(
        job_id: str,
        payload: JobRetryRequest,
        request: Request,
        response: Response,
        service: JobService = Depends(job_write_dependency),
    ):
        try:
            if_match = _header(request, "If-Match", max_length=256, missing_status=428, missing_code="IF_MATCH_REQUIRED")
            idempotency_key = _header(request, "Idempotency-Key", max_length=128, missing_status=400, missing_code="IDEMPOTENCY_KEY_REQUIRED")
            result = service.retry(_ctx(request), job_id, if_match=if_match, idempotency_key=idempotency_key, confirmation_id=payload.confirmation_id)
        except TaskError as exc:
            _raise(exc)
        response.headers["Location"] = f"/api/v1/jobs/{result.data.id}"
        if result.replayed:
            response.headers["Idempotency-Replayed"] = "true"
        return envelope(request, result.data)

    @router.get("/jobs/{job_id}/attempts", response_model=ListEnvelope[JobAttemptView], operation_id="list_job_attempts")
    def list_job_attempts(job_id: str, request: Request, limit: int = Query(default=50, ge=1, le=200), cursor: str | None = Query(default=None, min_length=1, max_length=512), service: JobService = Depends(jobs)):
        try:
            page = service.attempts_page(_ctx(request), job_id, limit=limit, cursor=cursor)
        except TaskError as exc:
            _raise(exc)
        return ListEnvelope(data=page.attempts, page=PageInfo(next_cursor=page.next_cursor, limit=limit, has_more=page.has_more), warnings=[], request_id=getattr(request.state, "request_id", "service-request"))

    @router.get(
        "/jobs/{job_id}/events",
        response_model=SuccessEnvelope[EventPage],
        operation_id="list_job_events",
        openapi_extra={
            "parameters": [{
                "in": "query",
                "name": "after_seq",
                "required": False,
                "description": "最后已消费的 seq；-1 表示从 seq 0 开始，省略时同样从头开始。",
                "schema": {"type": "string", "pattern": "^(-1|0|[1-9][0-9]{0,18})$", "maxLength": 19},
            }]
        },
    )
    def list_job_events(
        job_id: str,
        request: Request,
        limit: int = Query(default=100, ge=1, le=200),
        service: JobService = Depends(jobs),
    ):
        after_seq = request.query_params.get("after_seq")
        try:
            # The paginated API historically accepts -1 as the explicit
            # before-seq-0 sentinel. The SSE endpoint intentionally accepts
            # only nonnegative Last-Event-ID / after_seq values.
            cursor = -1 if after_seq == "-1" else resolve_job_event_cursor(after_seq, None)
            page_result = service.events_page(_ctx(request), job_id, after_seq=cursor, limit=limit)
            job = service.get(_ctx(request), job_id)
        except TaskError as exc:
            _raise(exc)
        terminal = job.status.value in {"SUCCEEDED", "PARTIAL", "FAILED", "CANCELED", "TIMED_OUT", "INTERRUPTED"}
        page = EventPage(
            events=page_result.events,
            next_seq=page_result.next_seq if page_result.has_more else None,
            has_more=page_result.has_more,
            stream_status="TERMINAL" if terminal else "OPEN",
        )
        return envelope(request, page)

    @router.get(
        "/jobs/{job_id}/events/stream",
        response_class=StreamingResponse,
        operation_id="stream_job_events",
        openapi_extra={
            "parameters": [
                {
                    "in": "query",
                    "name": "after_seq",
                    "required": False,
                    "description": "最后已消费的非负十进制 seq；未提供时从最早可用事件开始。",
                    "schema": {"type": "string", "pattern": "^(0|[1-9][0-9]{0,18})$", "maxLength": 19},
                },
                {
                    "in": "header",
                    "name": "Last-Event-ID",
                    "required": False,
                    "description": "断线续传游标；若同时提供 after_seq，二者必须相同。",
                    "schema": {"type": "string", "pattern": "^(0|[1-9][0-9]{0,18})$", "maxLength": 19},
                },
            ]
        },
        responses={
            200: {
                "description": "持久 Job 事件流；事件 id 与 JSON seq 一致，heartbeat 为无业务游标注释。",
                "content": {"text/event-stream": {"schema": {"type": "string"}}},
            },
            400: {"model": ErrorEnvelope, "description": "游标格式错误或 query/header 冲突"},
            401: {"model": ErrorEnvelope, "description": "认证主体无效"},
            403: {"model": ErrorEnvelope, "description": "当前主体无权读取该 Job"},
            404: {"model": ErrorEnvelope, "description": "Job 不存在或当前主体不可见"},
            410: {"model": ErrorEnvelope, "description": "事件游标已超出七日重放窗口；详情含分页 recovery_url"},
            503: {"model": ErrorEnvelope, "description": "Job 权限策略或持久事件存储不可用"},
        },
    )
    def stream_job_events(
        job_id: str,
        request: Request,
        service: JobService = Depends(jobs),
    ) -> StreamingResponse:
        after_seq = request.query_params.get("after_seq")
        try:
            cursor = resolve_job_event_cursor(after_seq, request.headers.get("last-event-id"))
            ctx = _ctx(request)
            initial = service.event_stream_snapshot(ctx, job_id, after_seq=cursor)
            check_job_event_replay_window(initial, cursor, job_id=job_id)
        except TaskError as exc:
            _raise(exc)
        return StreamingResponse(
            stream_job_event_frames(request, service, ctx, job_id, after_seq=cursor, initial_snapshot=initial),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.get("/confirmations/{confirmation_id}", response_model=SuccessEnvelope[ConfirmationView], operation_id="get_confirmation")
    def get_confirmation(confirmation_id: str, request: Request, service: ConfirmationService = Depends(confirmations)):
        try:
            result = service.get(_ctx(request), confirmation_id)
        except TaskError as exc:
            _raise(exc)
        return envelope(request, result)

    @router.post("/confirmations/previews", response_model=SuccessEnvelope[ConfirmationPreview], operation_id="preview_confirmation")
    def preview_confirmation(payload: ConfirmationPreviewRequest, request: Request, service: ConfirmationService = Depends(confirmations)):
        try:
            result = service.preview_from_request(_ctx(request), payload)
        except TaskError as exc:
            _raise(exc)
        return envelope(request, result)

    @router.post("/confirmations", response_model=SuccessEnvelope[ConfirmationView], status_code=status.HTTP_201_CREATED, operation_id="create_confirmation", openapi_extra={"parameters": [_IF_MATCH_OPENAPI, _IDEMPOTENCY_OPENAPI]})
    def create_confirmation(
        payload: ConfirmationCreateRequest,
        request: Request,
        response: Response,
        service: ConfirmationService = Depends(confirmation_write_dependency),
    ):
        try:
            if_match = _header(request, "If-Match", max_length=256, missing_status=428, missing_code="IF_MATCH_REQUIRED")
            idempotency_key = _header(request, "Idempotency-Key", max_length=128, missing_status=400, missing_code="IDEMPOTENCY_KEY_REQUIRED")
            result = service.create_from_request(_ctx(request), payload, if_match=if_match, idempotency_key=idempotency_key)
        except TaskError as exc:
            _raise(exc)
        if result.replayed:
            response.headers["Idempotency-Replayed"] = "true"
        return envelope(request, result.data)

    @router.post("/confirmations/{confirmation_id}/revoke", response_model=SuccessEnvelope[ConfirmationView], operation_id="revoke_confirmation", openapi_extra={"parameters": [_IDEMPOTENCY_OPENAPI]})
    def revoke_confirmation(
        confirmation_id: str,
        payload: ConfirmationRevokeRequest,
        request: Request,
        response: Response,
        service: ConfirmationService = Depends(confirmation_idempotency_dependency),
    ):
        try:
            idempotency_key = _header(request, "Idempotency-Key", max_length=128, missing_status=400, missing_code="IDEMPOTENCY_KEY_REQUIRED")
            result = service.revoke(_ctx(request), confirmation_id, reason=payload.reason, idempotency_key=idempotency_key)
        except TaskError as exc:
            _raise(exc)
        if result.replayed:
            response.headers["Idempotency-Replayed"] = "true"
        return envelope(request, result.data)

    return router


__all__ = ["create_router"]
