"""HTTP adapter for the artifact service."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from dataclasses import dataclass
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Header, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from fastapi.responses import Response as RawResponse

from chrom_platform.core.errors import APIError
from chrom_platform.core.idempotency import BeginResult, IdempotencyRecord, IdempotencyScope

from .schemas import ArtifactAcceptedEnvelope, ArtifactDeleteEnvelope, ArtifactEnvelope, ArchiveRequest, CompleteUploadRequest, UploadCreate, UploadEnvelope, UploadReceiptEnvelope
from .service import ArtifactService, ArtifactServiceError, UploadContentAdmission
from .uow import ArtifactUnitOfWork


class ArtifactRoute(APIRoute):
    """Translate only a missing required idempotency header to API 400."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def wrapped(request: Request):
            try:
                return await handler(request)
            except RequestValidationError as exc:
                missing_header = any(
                    error.get("type") == "missing"
                    and str(error.get("loc", [""])[-1]).lower() in {"idempotency-key", "idempotency_key"}
                    for error in exc.errors()
                )
                if missing_header:
                    raise APIError(400, "IDEMPOTENCY_KEY_REQUIRED", "缺少 Idempotency-Key") from exc
                raise

        return wrapped


@dataclass(frozen=True)
class _IdempotencyContext:
    store: object
    scope: IdempotencyScope
    payload_hash: str


def _service(request: Request, injected: ArtifactService | None) -> ArtifactService:
    service = injected or getattr(request.app.state, "artifact_service", None)
    if service is None:
        raise APIError(503, "DEPENDENCY_UNAVAILABLE", "制品服务未配置", retryable=True)
    return service


def _actor(request: Request):
    actor = getattr(request.app.state, "actor_context", None)
    if actor is None:
        raise APIError(503, "DEPENDENCY_UNAVAILABLE", "访问上下文未配置", retryable=True)
    return actor


def _raise(exc: ArtifactServiceError) -> None:
    raise APIError(exc.status_code, exc.code, exc.message, retryable=exc.retryable)


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", f"req_{uuid4().hex[:16]}")


def _payload_hash(path: str, payload: object) -> str:
    return hashlib.sha256(json.dumps({"path": path, "payload": payload}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _reserve(request: Request, *, actor_id: str, project_id: str, method: str, route: str, key: str, payload: object, idempotency_store: object | None = None) -> tuple[_IdempotencyContext, IdempotencyRecord | None]:
    store = idempotency_store or getattr(request.app.state, "idempotency_store", None)
    if store is None:
        raise APIError(503, "DEPENDENCY_UNAVAILABLE", "幂等记录存储未配置", retryable=True)
    payload_hash = _payload_hash(route, payload)
    scope = IdempotencyScope(actor_id=actor_id, project_id=project_id, method=method, route_identity=route, key=key)
    try:
        record = store.get(scope)
    except Exception as exc:
        raise APIError(503, "DEPENDENCY_UNAVAILABLE", "幂等存储暂不可用", retryable=True) from exc
    if record is not None:
        if record.payload_hash != payload_hash:
            raise APIError(409, "IDEMPOTENCY_KEY_REUSED", "幂等键已用于其他请求")
        if record.status == "IN_PROGRESS":
            raise APIError(409, "OPERATION_IN_PROGRESS", "相同幂等请求正在处理中", retryable=True)
        if record.status == "COMPLETED":
            return _IdempotencyContext(store, scope, payload_hash), record
    try:
        reservation = store.begin(scope, payload_hash)
    except Exception as exc:
        raise APIError(503, "DEPENDENCY_UNAVAILABLE", "幂等存储暂不可用", retryable=True) from exc
    if not isinstance(reservation, BeginResult):
        raise APIError(500, "STATE_CONFLICT", "幂等存储返回无效结果")
    if not reservation.acquired and reservation.record.payload_hash != payload_hash:
        raise APIError(409, "IDEMPOTENCY_KEY_REUSED", "幂等键已用于其他请求")
    if not reservation.acquired and reservation.record.status == "IN_PROGRESS":
        raise APIError(409, "OPERATION_IN_PROGRESS", "相同幂等请求正在处理中", retryable=True)
    if not reservation.acquired and reservation.record.status == "FAILED":
        raise APIError(503, "OPERATION_RETRY_REQUIRED", "上一次操作失败，可稍后重试", retryable=True)
    if not reservation.acquired:
        return _IdempotencyContext(store, scope, payload_hash), reservation.record
    return _IdempotencyContext(store, scope, payload_hash), None


def _complete(context: _IdempotencyContext, status_code: int, response: object) -> None:
    try:
        context.store.complete(context.scope, context.payload_hash, status_code, response.model_dump(mode="json"))
    except Exception as exc:
        _fail(context, "DEPENDENCY_UNAVAILABLE")
        raise APIError(503, "DEPENDENCY_UNAVAILABLE", "幂等存储暂不可用", retryable=True) from exc


_PUBLIC_FAILURE_CODES = {"VALIDATION_FAILED", "DEPENDENCY_UNAVAILABLE", "STATE_CONFLICT", "OPERATION_TIMEOUT"}


def _public_failure_code(error_code: str) -> str:
    if error_code in _PUBLIC_FAILURE_CODES:
        return error_code
    if error_code in {"MEDIA_TYPE_NOT_ALLOWED", "FILE_TOO_LARGE", "UPLOAD_INVALID", "CONTENT_CONFLICT"}:
        return "VALIDATION_FAILED"
    if error_code in {"RESOURCE_NOT_FOUND", "FORBIDDEN", "CONFIRMATION_INVALID", "ARTIFACT_NOT_AVAILABLE", "ARTIFACT_DELETED"}:
        return "STATE_CONFLICT"
    if error_code in {"SCANNER_NOT_CONFIGURED", "SCAN_FAILED"}:
        return "DEPENDENCY_UNAVAILABLE"
    return "STATE_CONFLICT"


def _fail(context: _IdempotencyContext, error_code: str) -> None:
    fail = getattr(context.store, "fail", None)
    if not callable(fail):
        raise APIError(503, "DEPENDENCY_UNAVAILABLE", "幂等失败记录接口未配置", retryable=True)
    try:
        fail(context.scope, context.payload_hash, _public_failure_code(error_code))
    except Exception as exc:
        raise APIError(503, "DEPENDENCY_UNAVAILABLE", "幂等失败记录暂不可用", retryable=True) from exc


def _replay(record: IdempotencyRecord, request: Request, model):
    if record.response is None:
        raise APIError(409, "STATE_CONFLICT", "幂等记录缺少响应")
    result = model.model_validate(record.response)
    result.request_id = _request_id(request)
    return result


def _create_upload_work(request: Request, service: ArtifactService, unit_of_work: object | None, payload: UploadCreate, actor, key: str):
    with _transaction(request, unit_of_work) as transaction:
        service_instance = service.with_repository(transaction.artifacts, jobs=transaction.jobs)
        try:
            service_instance.authorize_project(payload.project_id, actor, "artifact:write")
        except ArtifactServiceError as exc:
            _raise(exc)
        context, replay = _reserve(request, actor_id=actor.actor_id, project_id=payload.project_id, method="POST", route="/api/v1/artifacts/uploads", key=key, payload=payload.model_dump(mode="json"), idempotency_store=transaction.idempotency)
        if replay is not None:
            return _replay(replay, request, UploadEnvelope), True
        try:
            with transaction.business_savepoint():
                data = service_instance.create_upload(payload, actor)
        except ArtifactServiceError as exc:
            _fail(context, exc.code)
            transaction.commit()
            _raise(exc)
        result = UploadEnvelope(data=data, warnings=[], request_id=_request_id(request))
        _complete(context, status.HTTP_201_CREATED, result)
        transaction.commit()
        return result, False


def _receive_content_work(request: Request, service: ArtifactService, unit_of_work: object | None, upload_id: str, content: bytes, actor):
    with _transaction(request, unit_of_work) as transaction:
        service_instance = service.with_repository(transaction.artifacts, jobs=transaction.jobs)
        try:
            with transaction.business_savepoint():
                data = service_instance.receive_content(upload_id, content, actor)
        except ArtifactServiceError as exc:
            transaction.rollback()
            _raise(exc)
        transaction.commit()
        return UploadReceiptEnvelope(data=data, warnings=[], request_id=_request_id(request))


def _prepare_content_work(request: Request, service: ArtifactService, unit_of_work: object | None, upload_id: str, actor) -> UploadContentAdmission:
    """Run the short authorization/read transaction before consuming ASGI input."""

    with _transaction(request, unit_of_work) as transaction:
        service_instance = service.with_repository(transaction.artifacts, jobs=transaction.jobs)
        try:
            admission = service_instance.prepare_content_upload(upload_id, actor)
            transaction.rollback()
            return admission
        except ArtifactServiceError as exc:
            _raise(exc)


async def _read_bounded_content(request: Request, admission: UploadContentAdmission) -> bytes:
    """Read at most the declared server-side size without using Request.body()."""

    limit = min(admission.declared_size, admission.max_size_bytes)
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            announced = int(content_length)
        except (TypeError, ValueError) as exc:
            raise APIError(413, "FILE_TOO_LARGE", "上传长度不符合会话声明") from exc
        if announced <= 0 or announced != admission.declared_size or announced > admission.max_size_bytes:
            raise APIError(413, "FILE_TOO_LARGE", "上传长度不符合会话声明")

    content = bytearray()
    total = 0
    async for chunk in request.stream():
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise APIError(422, "UPLOAD_INVALID", "上传内容必须是二进制")
        if not chunk:
            continue
        next_total = total + len(chunk)
        if next_total > limit:
            raise APIError(413, "FILE_TOO_LARGE", "文件大小超出限制")
        content.extend(chunk)
        total = next_total
    if total != admission.declared_size:
        raise APIError(422, "UPLOAD_INVALID", "上传内容大小与声明不一致")
    return bytes(content)


def _complete_upload_work(request: Request, service: ArtifactService, unit_of_work: object | None, upload_id: str, actor, key: str):
    with _transaction(request, unit_of_work) as transaction:
        service_instance = service.with_repository(transaction.artifacts, jobs=transaction.jobs)
        try:
            project_id = service_instance.project_id_for_upload(upload_id, actor)
        except ArtifactServiceError as exc:
            _raise(exc)
        context, replay = _reserve(request, actor_id=actor.actor_id, project_id=project_id, method="POST", route="/api/v1/artifacts/uploads/{id}/complete", key=key, payload={"upload_id": upload_id}, idempotency_store=transaction.idempotency)
        if replay is not None:
            return _replay(replay, request, ArtifactAcceptedEnvelope), True
        try:
            with transaction.business_savepoint():
                data = service_instance.complete_upload(upload_id, actor)
        except ArtifactServiceError as exc:
            _fail(context, exc.code)
            transaction.commit()
            _raise(exc)
        result = ArtifactAcceptedEnvelope(data=data, warnings=[], request_id=_request_id(request))
        _complete(context, status.HTTP_202_ACCEPTED, result)
        transaction.commit()
        return result, False


def _archive_work(request: Request, service: ArtifactService, unit_of_work: object | None, artifact_id: str, payload: ArchiveRequest, actor, key: str):
    with _transaction(request, unit_of_work) as transaction:
        service_instance = service.with_repository(transaction.artifacts, jobs=transaction.jobs)
        try:
            project_id = service_instance.project_id_for_artifact(artifact_id, actor, "artifact:read")
        except ArtifactServiceError as exc:
            _raise(exc)
        context, replay = _reserve(request, actor_id=actor.actor_id, project_id=project_id, method="POST", route="/api/v1/artifacts/{id}/archive", key=key, payload={"artifact_id": artifact_id, **payload.model_dump(mode="json")}, idempotency_store=transaction.idempotency)
        if replay is not None:
            return _replay(replay, request, ArtifactEnvelope), True
        try:
            with transaction.business_savepoint():
                data = service_instance.archive(artifact_id, payload, actor)
        except ArtifactServiceError as exc:
            _fail(context, exc.code)
            transaction.commit()
            _raise(exc)
        result = ArtifactEnvelope(data=data, warnings=[], request_id=_request_id(request))
        _complete(context, status.HTTP_200_OK, result)
        transaction.commit()
        return result, False


def _delete_work(request: Request, service: ArtifactService, unit_of_work: object | None, artifact_id: str, confirmation_id: str, actor, key: str):
    with _transaction(request, unit_of_work) as transaction:
        service_instance = service.with_repository(transaction.artifacts, jobs=transaction.jobs)
        try:
            project_id = service_instance.project_id_for_artifact(artifact_id, actor, "artifact:delete")
        except ArtifactServiceError as exc:
            _raise(exc)
        context, replay = _reserve(request, actor_id=actor.actor_id, project_id=project_id, method="DELETE", route="/api/v1/artifacts/{id}", key=key, payload={"artifact_id": artifact_id, "confirmation_id": confirmation_id}, idempotency_store=transaction.idempotency)
        if replay is not None:
            return _replay(replay, request, ArtifactDeleteEnvelope), True
        try:
            with transaction.business_savepoint():
                data = service_instance.delete(artifact_id, confirmation_id, actor)
        except ArtifactServiceError as exc:
            _fail(context, exc.code)
            transaction.commit()
            _raise(exc)
        result = ArtifactDeleteEnvelope(data=data, warnings=[], request_id=_request_id(request))
        _complete(context, status.HTTP_202_ACCEPTED, result)
        transaction.commit()
        return result, False


def _new_uow(request: Request, injected: object | None) -> ArtifactUnitOfWork:
    candidate = injected or getattr(request.app.state, "artifact_uow_factory", None) or getattr(request.app.state, "artifact_uow", None)
    if candidate is None:
        raise APIError(503, "DEPENDENCY_UNAVAILABLE", "制品事务边界未配置", retryable=True)
    unit = candidate() if callable(candidate) and not hasattr(candidate, "__enter__") else candidate
    if not hasattr(unit, "__enter__") or not hasattr(unit, "commit") or not hasattr(unit, "business_savepoint"):
        raise APIError(503, "DEPENDENCY_UNAVAILABLE", "制品事务边界未配置", retryable=True)
    return unit


@contextmanager
def _transaction(request: Request, injected: object | None):
    unit = _new_uow(request, injected)
    with unit as active:
        if getattr(active, "artifacts", None) is None or getattr(active, "idempotency", None) is None:
            raise APIError(503, "DEPENDENCY_UNAVAILABLE", "制品事务端口未配置", retryable=True)
        yield active


def create_artifact_router(service: ArtifactService | None = None, *, unit_of_work: object | None = None) -> APIRouter:
    router = APIRouter(prefix="/artifacts", tags=["artifacts"], route_class=ArtifactRoute)

    @router.post("/uploads", response_model=UploadEnvelope, status_code=status.HTTP_201_CREATED, operation_id="create_artifact_upload")
    async def create_upload(payload: UploadCreate, request: Request, response: Response, idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=128)) -> UploadEnvelope:
        actor = _actor(request)
        result, replayed = await run_in_threadpool(_create_upload_work, request, _service(request, service), unit_of_work, payload, actor, idempotency_key)
        if replayed:
            response.headers["Idempotency-Replayed"] = "true"
        return result

    @router.put(
        "/uploads/{upload_id}/content",
        response_model=UploadReceiptEnvelope,
        operation_id="receive_artifact_upload_content",
        openapi_extra={"requestBody": {"required": True, "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}}},
    )
    async def receive_content(upload_id: str, request: Request) -> UploadReceiptEnvelope:
        actor = _actor(request)
        service_instance = _service(request, service)
        admission = await run_in_threadpool(_prepare_content_work, request, service_instance, unit_of_work, upload_id, actor)
        content = await _read_bounded_content(request, admission)
        return await run_in_threadpool(_receive_content_work, request, service_instance, unit_of_work, upload_id, content, actor)

    @router.post(
        "/uploads/{upload_id}/complete",
        response_model=ArtifactAcceptedEnvelope,
        status_code=status.HTTP_202_ACCEPTED,
        operation_id="complete_artifact_upload",
        responses={202: {"description": "异步受理", "headers": {"Location": {"description": "受理制品资源地址", "required": True, "schema": {"type": "string", "format": "uri-reference"}}}}},
    )
    async def complete_upload(upload_id: str, payload: CompleteUploadRequest, request: Request, response: Response, idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=128)) -> ArtifactAcceptedEnvelope:
        actor = _actor(request)
        result, replayed = await run_in_threadpool(_complete_upload_work, request, _service(request, service), unit_of_work, upload_id, actor, idempotency_key)
        if replayed:
            response.headers["Idempotency-Replayed"] = "true"
        response.headers["Location"] = f"/api/v1/artifacts/{result.data.artifact_id}"
        return result

    @router.get("/uploads/{upload_id}", response_model=UploadEnvelope, operation_id="get_artifact_upload")
    async def get_upload(upload_id: str, request: Request) -> UploadEnvelope:
        try:
            data = await run_in_threadpool(_service(request, service).get_upload, upload_id, _actor(request))
        except ArtifactServiceError as exc:
            _raise(exc)
        return UploadEnvelope(data=data, warnings=[], request_id=_request_id(request))

    @router.get("/{artifact_id}", response_model=ArtifactEnvelope, operation_id="get_artifact")
    async def get_artifact(artifact_id: str, request: Request) -> ArtifactEnvelope:
        try:
            data = await run_in_threadpool(_service(request, service).get_artifact, artifact_id, _actor(request))
        except ArtifactServiceError as exc:
            _raise(exc)
        return ArtifactEnvelope(data=data, warnings=[], request_id=_request_id(request))

    @router.get(
        "/{artifact_id}/download",
        response_class=RawResponse,
        operation_id="download_artifact",
        responses={200: {"description": "制品二进制内容", "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}}},
    )
    async def download_artifact(artifact_id: str, request: Request) -> RawResponse:
        try:
            view, content = await run_in_threadpool(_service(request, service).download, artifact_id, _actor(request))
        except ArtifactServiceError as exc:
            _raise(exc)
        return RawResponse(content=content, media_type="application/octet-stream", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(view.file_name, safe='')}", "X-Content-Type-Options": "nosniff"})

    @router.post("/{artifact_id}/archive", response_model=ArtifactEnvelope, operation_id="archive_artifact")
    async def archive_artifact(artifact_id: str, payload: ArchiveRequest, request: Request, response: Response, idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=128)) -> ArtifactEnvelope:
        actor = _actor(request)
        result, replayed = await run_in_threadpool(_archive_work, request, _service(request, service), unit_of_work, artifact_id, payload, actor, idempotency_key)
        if replayed:
            response.headers["Idempotency-Replayed"] = "true"
        return result

    @router.delete(
        "/{artifact_id}",
        response_model=ArtifactDeleteEnvelope,
        status_code=status.HTTP_202_ACCEPTED,
        operation_id="delete_artifact",
        responses={202: {"description": "异步受理", "headers": {"Location": {"description": "受理制品资源地址", "required": True, "schema": {"type": "string", "format": "uri-reference"}}}}},
    )
    async def delete_artifact(artifact_id: str, request: Request, response: Response, confirmation_id: str, idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=128)) -> ArtifactDeleteEnvelope:
        actor = _actor(request)
        result, replayed = await run_in_threadpool(_delete_work, request, _service(request, service), unit_of_work, artifact_id, confirmation_id, actor, idempotency_key)
        if replayed:
            response.headers["Idempotency-Replayed"] = "true"
        response.headers["Location"] = f"/api/v1/artifacts/{result.data.id}"
        return result

    return router


create_router = create_artifact_router

__all__ = ["create_artifact_router", "create_router"]
