"""Unregistered HTTP adapter for the data-center vertical slice.

The factory never creates repositories, parsers, scanners or in-memory
success stores.  Assembly supplies those ports through arguments or
``app.state``; a missing production port is reported as 503.
"""

from __future__ import annotations

import hashlib
import json
import base64
from typing import Any, Literal

from fastapi import APIRouter, Header, Query, Request, Response, status

from chrom_platform.core.dto import ActorContext, ListEnvelope, PageInfo, SuccessEnvelope
from chrom_platform.core.cursor import CursorCodec, CursorError
from chrom_platform.core.errors import APIError
from chrom_platform.core.idempotency import BeginResult, IdempotencyRecord, IdempotencyScope
from pydantic import ConfigDict, Field
from chrom_platform.task_core.service import OperationContext, TaskError

from .errors import DataCenterError
from .schemas import (
    ArchiveRequest,
    AsyncAccepted,
    DataDomainView,
    DataImport,
    DataImportCreate,
    DataImportPatch,
    DataImportValidation,
    DataVersion,
    ExportAccepted,
    ExportCreate,
    ExportView,
    PredictionDraftView,
    PredictionRecord,
)
from .service import DataImportService, DataVersionService, ExportService, PredictionRecordService


class _EmptyPayload(DataImportCreate.__base__):
    model_config = ConfigDict(extra="forbid", strict=True)


_IF_MATCH_OPENAPI = {
    "in": "header", "name": "If-Match", "required": True,
    "schema": {"type": "string", "minLength": 1, "maxLength": 256},
}
_IDEMPOTENCY_OPENAPI = {
    "in": "header", "name": "Idempotency-Key", "required": True,
    "schema": {"type": "string", "minLength": 1, "maxLength": 128},
}
_ASYNC_RESPONSES = {
    "202": {"description": "异步受理", "headers": {"Location": {
        "required": True, "schema": {"type": "string", "format": "uri-reference"}
    }}}
}


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "service-request")


def _envelope(request: Request, data: Any) -> SuccessEnvelope:
    return SuccessEnvelope(data=data, warnings=[], request_id=_request_id(request))


def _raise(exc: DataCenterError) -> None:
    raise APIError(exc.status_code, exc.code, exc.message, details=exc.details)


def _actor(request: Request) -> ActorContext:
    actor = getattr(request.app.state, "actor_context", None)
    if actor is None:
        raise APIError(503, "DEPENDENCY_UNAVAILABLE", "ActorContext 未由服务端配置", retryable=True)
    return actor


def _dependency(request: Request, explicit: object | None, *names: str) -> object:
    candidate = explicit
    if candidate is None:
        bound = getattr(request.state, "m2_assembly", None)
        for name in names:
            candidate = getattr(bound, name, None) if bound is not None else None
            if candidate is not None:
                break
    if candidate is None:
        for name in names:
            candidate = getattr(request.app.state, name, None)
            if candidate is not None:
                break
    if candidate is None:
        raise APIError(503, "DEPENDENCY_UNAVAILABLE", "数据中心依赖未配置", retryable=True)
    return candidate


def _header(request: Request, name: str, *, status_code: int, code: str, max_length: int) -> str:
    value = request.headers.get(name)
    if value is None:
        raise APIError(status_code, code, f"缺少 {name}")
    if value == "" or len(value) > max_length:
        raise APIError(422, "VALIDATION_FAILED", f"{name} 长度无效")
    return value


def _project(actor: ActorContext, project_id: str, *, write: bool = False) -> None:
    if not actor.can_access_project(project_id):
        # Reads intentionally do not reveal whether another project owns a
        # resource.  Writes use the conventional permission failure.
        if write:
            raise APIError(403, "PERMISSION_DENIED", "无权访问当前项目")
        raise APIError(404, "RESOURCE_NOT_FOUND", "资源不存在")
    if write and not actor.can("dataset:write") and not actor.can("dataset:import") and not actor.can("*"):
        raise APIError(403, "PERMISSION_DENIED", "缺少数据写入权限")


def _payload_hash(route: str, payload: Any) -> str:
    raw = json.dumps({"route": route, "payload": payload}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _reserve(request: Request, store: object | None, *, actor_id: str, project_id: str, method: str, route: str, key: str, payload: Any) -> tuple[object, IdempotencyScope, str, IdempotencyRecord | None]:
    store = store or getattr(request.state, "idempotency_store", None) or getattr(request.app.state, "idempotency_store", None)
    if store is None:
        raise APIError(503, "DEPENDENCY_UNAVAILABLE", "幂等记录存储未配置", retryable=True)
    digest = _payload_hash(route, payload)
    scope = IdempotencyScope(actor_id=actor_id, project_id=project_id, method=method, route_identity=route, key=key)
    try:
        existing = store.get(scope)
        if existing is not None:
            if existing.payload_hash != digest:
                raise APIError(409, "IDEMPOTENCY_KEY_REUSED", "幂等键已用于其他请求")
            if existing.status == "IN_PROGRESS":
                raise APIError(409, "OPERATION_IN_PROGRESS", "相同幂等请求正在处理中", retryable=True)
            if existing.status == "COMPLETED":
                return store, scope, digest, existing
        reservation = store.begin(scope, digest)
    except APIError:
        raise
    except Exception as exc:
        raise APIError(503, "DEPENDENCY_UNAVAILABLE", "幂等存储暂不可用", retryable=True) from exc
    if not isinstance(reservation, BeginResult):
        raise APIError(500, "STATE_CONFLICT", "幂等存储返回无效结果")
    if not reservation.acquired:
        if reservation.record.payload_hash != digest:
            raise APIError(409, "IDEMPOTENCY_KEY_REUSED", "幂等键已用于其他请求")
        if reservation.record.status == "IN_PROGRESS":
            raise APIError(409, "OPERATION_IN_PROGRESS", "相同幂等请求正在处理中", retryable=True)
        if reservation.record.status == "FAILED":
            raise APIError(503, "OPERATION_RETRY_REQUIRED", "上一次操作失败，可稍后重试", retryable=True)
        return store, scope, digest, reservation.record
    return store, scope, digest, None


def _finish(store: object, scope: IdempotencyScope, digest: str, result: Any, status_code: int) -> None:
    try:
        store.complete(scope, digest, status_code, result.model_dump(mode="json"))
    except Exception as exc:
        raise APIError(503, "DEPENDENCY_UNAVAILABLE", "幂等记录存储暂不可用", retryable=True) from exc


def _fail(store: object, scope: IdempotencyScope, digest: str, code: str) -> None:
    try:
        store.fail(scope, digest, code)
    except Exception as exc:
        raise APIError(503, "DEPENDENCY_UNAVAILABLE", "幂等失败记录暂不可用", retryable=True) from exc


def _replay(record: IdempotencyRecord, model: Any, request: Request | None = None) -> Any:
    if record.response is None:
        raise APIError(409, "STATE_CONFLICT", "幂等记录缺少响应")
    # Idempotency stores persist the JSON representation; strict DTOs still
    # need JSON parsing to reconstruct datetime fields on replay.
    result = model.model_validate_json(json.dumps(record.response, ensure_ascii=False))
    # ``request_id`` is transport metadata, not the idempotency result.  A
    # replay therefore carries the current request id while preserving the
    # exact business result that was stored by the first request.
    if request is not None and hasattr(result, "request_id"):
        result.request_id = _request_id(request)
    return result


def _resource_project(request: Request, supplied: str | None, resource_id: str, repository: object | None, *, kind: str) -> str:
    actor = _actor(request)
    if supplied:
        _project(actor, supplied)
        return supplied
    if len(actor.project_ids) == 1:
        return actor.project_ids[0]
    finder = getattr(repository, f"get_{kind}_any", None) if repository else None
    if callable(finder):
        value = finder(actor.project_ids, resource_id)
        if value is not None:
            return value.project_id
    raise APIError(400, "VALIDATION_FAILED", "多项目访问时必须提供 project_id")


def _page(values: list[Any], *, limit: int, cursor: str | None, scope: str) -> tuple[list[Any], PageInfo]:
    codec = CursorCodec()
    after = None
    if cursor:
        try:
            after = codec.decode(cursor, scope=scope)
        except CursorError:
            raise APIError(400, "VALIDATION_FAILED", "cursor 无效或已失效") from None
    if after is not None:
        index = next((i for i, item in enumerate(values) if str(getattr(item, "id", "")) == after), None)
        # A SQL repository may already apply the keyset predicate.  In that
        # case the boundary row is absent; retain the bounded result window.
        if index is not None:
            values = values[index + 1:]
    window = values[:limit]
    has_more = len(values) > limit
    next_cursor = codec.encode(after_id=str(window[-1].id), scope=scope) if has_more and window else None
    return window, PageInfo(next_cursor=next_cursor, limit=limit, has_more=has_more)


def _after_cursor(cursor: str | None, scope: str) -> str | None:
    if not cursor:
        return None
    try:
        return CursorCodec().decode(cursor, scope=scope)
    except CursorError:
        raise APIError(400, "VALIDATION_FAILED", "cursor 无效或已失效") from None


def _assert_import_etag(service_obj: object, project_id: str, import_id: str, expected: str) -> None:
    getter = getattr(service_obj, "get", None)
    if not callable(getter):
        return
    try:
        current = getter(project_id, import_id)
    except DataCenterError as exc:
        _raise(exc)
    if current is None:
        raise APIError(404, "RESOURCE_NOT_FOUND", "数据导入不存在")
    if current.etag != expected:
        raise APIError(412, "VERSION_CONFLICT", "资源版本已变化")


def create_router(
    *,
    service: DataImportService | None = None,
    data_import_service: DataImportService | None = None,
    data_version_service: DataVersionService | None = None,
    domain_registry: object | None = None,
    prediction_record_service: PredictionRecordService | None = None,
    export_service: ExportService | None = None,
    data_repository: object | None = None,
    prediction_repository: object | None = None,
    idempotency_store: object | None = None,
    import_workflow: object | None = None,
) -> APIRouter:
    """Build the data-center routes without registering them on an app."""

    imports = data_import_service or service
    router = APIRouter(prefix="", tags=["data-center"])

    def domains(request: Request):
        return _dependency(request, domain_registry, "data_domain_registry", "data_center_domain_registry")

    def import_service(request: Request):
        return _dependency(request, imports, "data_import_service", "data_center_import_service")

    def import_workflow_service(request: Request):
        configured = import_workflow or getattr(request.state, "data_import_workflow", None) or getattr(request.app.state, "data_import_workflow", None)
        if configured is None:
            raise APIError(503, "DEPENDENCY_UNAVAILABLE", "数据导入任务工作流未配置", retryable=True)
        return configured

    def version_service(request: Request):
        return _dependency(request, data_version_service, "data_version_service", "data_center_version_service")

    def prediction_service(request: Request):
        return _dependency(request, prediction_record_service, "prediction_record_service", "data_center_prediction_record_service")

    def exports(request: Request):
        return _dependency(request, export_service, "export_service", "data_center_export_service")

    def import_repository(request: Request) -> object | None:
        if data_repository is not None:
            return data_repository
        configured = imports or getattr(request.app.state, "data_import_service", None) or getattr(request.app.state, "data_center_import_service", None)
        return getattr(configured, "_repository", None)

    def version_repository(request: Request) -> object | None:
        if data_repository is not None:
            return data_repository
        configured = data_version_service or getattr(request.app.state, "data_version_service", None) or getattr(request.app.state, "data_center_version_service", None)
        return getattr(configured, "_repository", None)

    def prediction_reader(request: Request) -> object | None:
        if prediction_repository is not None:
            return prediction_repository
        configured = prediction_record_service or getattr(request.app.state, "prediction_record_service", None) or getattr(request.app.state, "data_center_prediction_record_service", None)
        return getattr(configured, "_records", None)

    @router.get("/data-domains", response_model=ListEnvelope[DataDomainView], operation_id="list_data_domains")
    def list_domains(request: Request):
        try:
            values = list(domains(request).list())
        except DataCenterError as exc:
            _raise(exc)
        return ListEnvelope(data=values, page=PageInfo(next_cursor=None, limit=max(1, len(values)), has_more=False), warnings=[], request_id=_request_id(request))

    @router.get("/data-domains/{domain_id}/schema", response_model=SuccessEnvelope[DataDomainView], operation_id="get_data_domain_schema")
    def get_domain_schema(domain_id: str, request: Request, schema_version: str | None = Query(None, min_length=1, max_length=64)):
        value = domains(request).get(domain_id, schema_version)
        if value is None:
            raise APIError(404, "RESOURCE_NOT_FOUND", "数据域 schema 不存在")
        return _envelope(request, value)

    @router.get("/data-versions", response_model=ListEnvelope[DataVersion], operation_id="list_data_versions")
    def list_versions(request: Request, project_id: str = Query(..., min_length=1, max_length=96), domain_id: str = Query(..., min_length=1, max_length=96), status_filter: Literal["READY", "ARCHIVED"] | None = Query(None, alias="status"), limit: int = Query(50, ge=1, le=200), cursor: str | None = Query(None, min_length=1, max_length=512)):
        _project(_actor(request), project_id)
        try:
            after_id = _after_cursor(cursor, f"data-versions:{project_id}:{domain_id}:{status_filter or ''}")
            values = list(version_service(request).list(project_id, domain_id, status=status_filter, limit=limit + 1, after_id=after_id)) if hasattr(version_service(request), "list") else list(version_service(request)._repository.list_data_versions(project_id, domain_id, status=status_filter, limit=limit + 1, after_id=after_id))
        except DataCenterError as exc:
            _raise(exc)
        values, page = _page(values, limit=limit, cursor=cursor, scope=f"data-versions:{project_id}:{domain_id}:{status_filter or ''}")
        return ListEnvelope(data=values, page=page, warnings=[], request_id=_request_id(request))

    @router.get("/data-versions/{version_id}", response_model=SuccessEnvelope[DataVersion], operation_id="get_data_version")
    def get_version(version_id: str, request: Request, project_id: str | None = Query(None, min_length=1, max_length=96)):
        project = _resource_project(request, project_id, version_id, version_repository(request), kind="data_version")
        try:
            value = version_service(request).get(project, version_id)
        except DataCenterError as exc:
            _raise(exc)
        return _envelope(request, value)

    @router.post("/data-versions/{version_id}/archive", response_model=SuccessEnvelope[DataVersion], operation_id="archive_data_version", openapi_extra={"parameters": [_IDEMPOTENCY_OPENAPI]})
    def archive_version(version_id: str, payload: ArchiveRequest, request: Request, response: Response, project_id: str | None = Query(None), idempotency_key: str | None = Header(None, alias="Idempotency-Key")):
        key = _header(request, "Idempotency-Key", status_code=400, code="IDEMPOTENCY_KEY_REQUIRED", max_length=128)
        project = _resource_project(request, project_id, version_id, version_repository(request), kind="data_version")
        actor = _actor(request)
        _project(actor, project, write=True)
        store, scope, digest, replay = _reserve(request, idempotency_store, actor_id=actor.actor_id, project_id=project, method="POST", route="/api/v1/data-versions/{id}/archive", key=key, payload={"version_id": version_id, **payload.model_dump(mode="json")})
        if replay is not None:
            response.headers["Idempotency-Replayed"] = "true"
            return _replay(replay, SuccessEnvelope[DataVersion], request)
        try:
            value = version_service(request).archive(project, version_id)
            result = _envelope(request, value)
            _finish(store, scope, digest, result, 200)
            return result
        except DataCenterError as exc:
            _fail(store, scope, digest, exc.code)
            _raise(exc)

    @router.post("/data-imports", response_model=SuccessEnvelope[DataImport], status_code=status.HTTP_201_CREATED, operation_id="create_data_import", openapi_extra={"parameters": [_IDEMPOTENCY_OPENAPI]})
    def create_import(payload: DataImportCreate, request: Request, response: Response, idempotency_key: str | None = Header(None, alias="Idempotency-Key")):
        key = _header(request, "Idempotency-Key", status_code=400, code="IDEMPOTENCY_KEY_REQUIRED", max_length=128)
        actor = _actor(request)
        _project(actor, payload.project_id, write=True)
        store, scope, digest, replay = _reserve(request, idempotency_store, actor_id=actor.actor_id, project_id=payload.project_id, method="POST", route="/api/v1/data-imports", key=key, payload=payload.model_dump(mode="json"))
        if replay is not None:
            response.headers["Idempotency-Replayed"] = "true"
            result = _replay(replay, SuccessEnvelope[DataImport], request)
            response.headers["ETag"] = result.data.etag
            return result
        try:
            result = _envelope(request, import_service(request).create(payload))
            response.headers["ETag"] = result.data.etag
            _finish(store, scope, digest, result, 201)
            return result
        except DataCenterError as exc:
            _fail(store, scope, digest, exc.code)
            _raise(exc)

    @router.get("/data-imports/{import_id}", response_model=SuccessEnvelope[DataImport], operation_id="get_data_import")
    def get_import(import_id: str, request: Request, response: Response, project_id: str | None = Query(None)):
        project = _resource_project(request, project_id, import_id, import_repository(request), kind="import")
        try:
            result = _envelope(request, import_service(request).get(project, import_id))
        except DataCenterError as exc:
            _raise(exc)
        response.headers["ETag"] = result.data.etag
        return result

    @router.patch("/data-imports/{import_id}", response_model=SuccessEnvelope[DataImport], operation_id="patch_data_import", openapi_extra={"parameters": [_IF_MATCH_OPENAPI, _IDEMPOTENCY_OPENAPI]})
    def patch_import(import_id: str, payload: DataImportPatch, request: Request, response: Response, project_id: str | None = Query(None), if_match: str | None = Header(None, alias="If-Match"), idempotency_key: str | None = Header(None, alias="Idempotency-Key")):
        match = _header(request, "If-Match", status_code=428, code="IF_MATCH_REQUIRED", max_length=256)
        key = _header(request, "Idempotency-Key", status_code=400, code="IDEMPOTENCY_KEY_REQUIRED", max_length=128)
        project = _resource_project(request, project_id, import_id, import_repository(request), kind="import")
        actor = _actor(request)
        _project(actor, project, write=True)
        store, scope, digest, replay = _reserve(request, idempotency_store, actor_id=actor.actor_id, project_id=project, method="PATCH", route="/api/v1/data-imports/{id}", key=key, payload={"import_id": import_id, **payload.model_dump(mode="json"), "if_match": match})
        if replay is not None:
            response.headers["Idempotency-Replayed"] = "true"
            result = _replay(replay, SuccessEnvelope[DataImport], request); response.headers["ETag"] = result.data.etag; return result
        _assert_import_etag(import_service(request), project, import_id, match)
        try:
            result = _envelope(request, import_service(request).patch(project, import_id, payload, if_match=match))
            response.headers["ETag"] = result.data.etag; _finish(store, scope, digest, result, 200); return result
        except DataCenterError as exc:
            _fail(store, scope, digest, exc.code); _raise(exc)

    @router.post("/data-imports/{import_id}/validate", response_model=SuccessEnvelope[AsyncAccepted], status_code=status.HTTP_202_ACCEPTED, operation_id="validate_data_import", openapi_extra={"parameters": [_IF_MATCH_OPENAPI, _IDEMPOTENCY_OPENAPI], "responses": _ASYNC_RESPONSES})
    def validate_import(import_id: str, payload: _EmptyPayload, request: Request, response: Response, project_id: str | None = Query(None), if_match: str | None = Header(None, alias="If-Match"), idempotency_key: str | None = Header(None, alias="Idempotency-Key")):
        match = _header(request, "If-Match", status_code=428, code="IF_MATCH_REQUIRED", max_length=256)
        key = _header(request, "Idempotency-Key", status_code=400, code="IDEMPOTENCY_KEY_REQUIRED", max_length=128)
        project = _resource_project(request, project_id, import_id, import_repository(request), kind="import")
        actor = _actor(request)
        _project(actor, project, write=True)
        store, scope, digest, replay = _reserve(request, idempotency_store, actor_id=actor.actor_id, project_id=project, method="POST", route="/api/v1/data-imports/{id}/validate", key=key, payload={"import_id": import_id, "if_match": match})
        if replay is not None:
            replayed = _replay(replay, SuccessEnvelope[AsyncAccepted], request)
            response.headers["Idempotency-Replayed"] = "true"
            response.headers["Location"] = f"/api/v1/data-imports/{import_id}"
            return replayed
        try:
            _assert_import_etag(import_service(request), project, import_id, match)
            value = import_workflow_service(request).submit_validation(
                OperationContext(actor=actor, request_id=_request_id(request)), project, import_id, if_match=match,
            )
            result = _envelope(request, value); response.headers["Location"] = f"/api/v1/data-imports/{import_id}"; _finish(store, scope, digest, result, 202); return result
        except DataCenterError as exc:
            _fail(store, scope, digest, exc.code); _raise(exc)
        except TaskError as exc:
            _fail(store, scope, digest, exc.code)
            raise APIError(exc.status_code, exc.code, exc.message, retryable=exc.retryable) from exc

    @router.get("/data-imports/{import_id}/validation", response_model=SuccessEnvelope[DataImportValidation], operation_id="get_data_import_validation")
    def get_validation(import_id: str, request: Request, project_id: str | None = Query(None)):
        project = _resource_project(request, project_id, import_id, import_repository(request), kind="import")
        try:
            return _envelope(request, import_service(request).validation(project, import_id))
        except DataCenterError as exc:
            _raise(exc)

    @router.post("/data-imports/{import_id}/commit", response_model=SuccessEnvelope[AsyncAccepted], status_code=status.HTTP_202_ACCEPTED, operation_id="commit_data_import", openapi_extra={"parameters": [_IDEMPOTENCY_OPENAPI], "responses": _ASYNC_RESPONSES})
    def commit_import(import_id: str, payload: ArchiveRequest, request: Request, response: Response, project_id: str | None = Query(None), idempotency_key: str | None = Header(None, alias="Idempotency-Key")):
        key = _header(request, "Idempotency-Key", status_code=400, code="IDEMPOTENCY_KEY_REQUIRED", max_length=128)
        project = _resource_project(request, project_id, import_id, import_repository(request), kind="import")
        actor = _actor(request)
        _project(actor, project, write=True)
        # Commit binds Confirmation to the authoritative current revision;
        # If-Match is intentionally not part of this operation's contract.
        store, scope, digest, replay = _reserve(request, idempotency_store, actor_id=actor.actor_id, project_id=project, method="POST", route="/api/v1/data-imports/{id}/commit", key=key, payload={"import_id": import_id, **payload.model_dump(mode="json")})
        if replay is not None:
            result = _replay(replay, SuccessEnvelope[AsyncAccepted], request); response.headers["Idempotency-Replayed"] = "true"; response.headers["Location"] = f"/api/v1/data-imports/{import_id}"; return result
        try:
            value = import_workflow_service(request).submit_commit(
                OperationContext(actor=actor, request_id=_request_id(request)), project, import_id, payload.confirmation_id,
            )
            result = _envelope(request, value); response.headers["Location"] = f"/api/v1/data-imports/{import_id}"; _finish(store, scope, digest, result, 202); return result
        except DataCenterError as exc:
            _fail(store, scope, digest, exc.code); _raise(exc)
        except TaskError as exc:
            _fail(store, scope, digest, exc.code)
            raise APIError(exc.status_code, exc.code, exc.message, retryable=exc.retryable) from exc

    @router.get("/prediction-records", response_model=ListEnvelope[PredictionRecord], operation_id="list_prediction_records")
    def list_prediction_records(request: Request, project_id: str = Query(..., min_length=1, max_length=96), chromatography_type: str | None = Query(None), status_filter: str | None = Query(None, alias="status"), limit: int = Query(50, ge=1, le=200), cursor: str | None = Query(None, min_length=1, max_length=512)):
        actor = _actor(request)
        if not actor.can("prediction:read"):
            raise APIError(403, "PERMISSION_DENIED", "当前主体无权读取预测记录")
        _project(actor, project_id)
        service_obj = prediction_service(request)
        repository = prediction_repository or getattr(request.app.state, "prediction_record_repository", None)
        try:
            if hasattr(service_obj, "list"):
                values = list(service_obj.list(project_id, chromatography_type=chromatography_type, status=status_filter, limit=limit + 1, after_id=_after_cursor(cursor, f"prediction-records:{project_id}:{chromatography_type or ''}:{status_filter or ''}")))
            elif repository is not None and hasattr(repository, "list"):
                values = list(repository.list(project_id, chromatography_type=chromatography_type, status=status_filter))
            else:
                raise APIError(503, "DEPENDENCY_UNAVAILABLE", "预测记录读取端口未配置", retryable=True)
        except DataCenterError as exc:
            _raise(exc)
        values, page = _page(values, limit=limit, cursor=cursor, scope=f"prediction-records:{project_id}:{chromatography_type or ''}:{status_filter or ''}")
        return ListEnvelope(data=values, page=page, warnings=[], request_id=_request_id(request))

    @router.get("/prediction-records/{record_id}", response_model=SuccessEnvelope[PredictionRecord], operation_id="get_prediction_record")
    def get_prediction_record(record_id: str, request: Request, response: Response, project_id: str | None = Query(None)):
        actor = _actor(request)
        if not actor.can("prediction:read"):
            raise APIError(403, "PERMISSION_DENIED", "当前主体无权读取预测记录")
        project = _resource_project(request, project_id, record_id, prediction_reader(request), kind="prediction")
        try:
            service_obj = prediction_service(request)
            if hasattr(service_obj, "get"):
                value = service_obj.get(project, record_id)
            else:
                value = service_obj._records.get(project, record_id)
        except DataCenterError as exc:
            _raise(exc)
        if value is None:
            raise APIError(404, "RESOURCE_NOT_FOUND", "预测记录不存在")
        result = _envelope(request, value)
        response.headers["ETag"] = value.etag
        return result

    @router.get("/prediction-drafts/{draft_id}", response_model=SuccessEnvelope[PredictionDraftView], operation_id="get_prediction_draft")
    def get_prediction_draft(draft_id: str, request: Request, response: Response, project_id: str | None = Query(None)):
        actor = _actor(request)
        if not actor.can("prediction:read"):
            raise APIError(403, "PERMISSION_DENIED", "当前主体无权读取预测草稿")
        project = _resource_project(request, project_id, draft_id, prediction_reader(request), kind="replay_draft")
        try:
            value = prediction_service(request).get_draft(project_id=project, draft_id=draft_id)
        except DataCenterError as exc:
            _raise(exc)
        response.headers["ETag"] = value.etag
        return _envelope(request, value)

    @router.post("/prediction-records/{record_id}/replay-draft", response_model=SuccessEnvelope[PredictionDraftView], status_code=status.HTTP_201_CREATED, operation_id="replay_prediction_record", openapi_extra={"parameters": [_IDEMPOTENCY_OPENAPI]})
    def replay_prediction_record(record_id: str, payload: _EmptyPayload, request: Request, response: Response, project_id: str | None = Query(None), idempotency_key: str | None = Header(None, alias="Idempotency-Key")):
        key = _header(request, "Idempotency-Key", status_code=400, code="IDEMPOTENCY_KEY_REQUIRED", max_length=128)
        actor = _actor(request)
        if not actor.can("prediction:read"):
            raise APIError(403, "PERMISSION_DENIED", "当前主体无权读取预测记录")
        if not actor.can("prediction:create"):
            raise APIError(403, "PERMISSION_DENIED", "当前主体无权创建预测草稿")
        project = _resource_project(request, project_id, record_id, prediction_reader(request), kind="prediction")
        store, scope, digest, replay = _reserve(request, idempotency_store, actor_id=actor.actor_id, project_id=project, method="POST", route="/api/v1/prediction-records/{id}/replay-draft", key=key, payload={"record_id": record_id})
        if replay is not None:
            response.headers["Idempotency-Replayed"] = "true"; return _replay(replay, SuccessEnvelope[PredictionDraftView], request)
        try:
            result = _envelope(request, prediction_service(request).replay_draft(project_id=project, record_id=record_id, actor_id=actor.actor_id)); _finish(store, scope, digest, result, 201); return result
        except DataCenterError as exc:
            _fail(store, scope, digest, exc.code); _raise(exc)

    @router.post("/prediction-records/{record_id}/archive", response_model=SuccessEnvelope[PredictionRecord], operation_id="archive_prediction_record", openapi_extra={"parameters": [_IDEMPOTENCY_OPENAPI]})
    def archive_prediction_record(record_id: str, payload: ArchiveRequest, request: Request, response: Response, project_id: str | None = Query(None), idempotency_key: str | None = Header(None, alias="Idempotency-Key")):
        key = _header(request, "Idempotency-Key", status_code=400, code="IDEMPOTENCY_KEY_REQUIRED", max_length=128)
        actor = _actor(request)
        if not actor.can("prediction:read"):
            raise APIError(403, "PERMISSION_DENIED", "当前主体无权读取预测记录")
        if not actor.can("prediction:archive"):
            raise APIError(403, "PERMISSION_DENIED", "当前主体无权归档预测记录")
        project = _resource_project(request, project_id, record_id, prediction_reader(request), kind="prediction")
        store, scope, digest, replay = _reserve(request, idempotency_store, actor_id=actor.actor_id, project_id=project, method="POST", route="/api/v1/prediction-records/{id}/archive", key=key, payload={"record_id": record_id, **payload.model_dump(mode="json")})
        if replay is not None:
            result = _replay(replay, SuccessEnvelope[PredictionRecord], request)
            response.headers["Idempotency-Replayed"] = "true"
            response.headers["ETag"] = result.data.etag
            return result
        try:
            result = _envelope(request, prediction_service(request).archive_record(project_id=project, record_id=record_id, request=payload, actor=actor)); response.headers["ETag"] = result.data.etag; _finish(store, scope, digest, result, 200); return result
        except DataCenterError as exc:
            _fail(store, scope, digest, exc.code); _raise(exc)
        except Exception as exc:
            from chrom_platform.task_core.service import TaskError
            if isinstance(exc, TaskError):
                ledger_code = exc.code if exc.code in {"DEPENDENCY_UNAVAILABLE", "VALIDATION_FAILED", "STATE_CONFLICT", "OPERATION_TIMEOUT", "CONFIRMATION_REQUIRED", "DATA_CONFLICT", "DATA_VALIDATION_FAILED", "RESOURCE_NOT_FOUND", "DATA_CAPABILITY_UNAVAILABLE"} else "CONFIRMATION_REQUIRED"
                _fail(store, scope, digest, ledger_code)
                raise APIError(exc.status_code, exc.code, exc.message, retryable=exc.retryable) from exc
            raise

    @router.post("/exports", response_model=SuccessEnvelope[ExportAccepted], status_code=status.HTTP_202_ACCEPTED, operation_id="create_prediction_record_export", openapi_extra={"parameters": [_IDEMPOTENCY_OPENAPI], "responses": _ASYNC_RESPONSES})
    def create_export(payload: ExportCreate, request: Request, response: Response, idempotency_key: str | None = Header(None, alias="Idempotency-Key", include_in_schema=False)):
        actor = _actor(request)
        _project(actor, payload.project_id)
        if not actor.can("prediction:read"):
            raise APIError(403, "PERMISSION_DENIED", "当前主体无权导出预测记录")
        key = _header(request, "Idempotency-Key", status_code=400, code="IDEMPOTENCY_KEY_REQUIRED", max_length=128)
        store, scope, digest, replay = _reserve(
            request, idempotency_store, actor_id=actor.actor_id, project_id=payload.project_id,
            method="POST", route="/api/v1/exports", key=key,
            payload=payload.model_dump(mode="json"),
        )
        if replay is not None:
            result = _replay(replay, SuccessEnvelope[ExportAccepted], request)
            response.headers["Idempotency-Replayed"] = "true"
            response.headers["Location"] = f"/api/v1/exports/{result.data.id}"
            return result
        try:
            value = exports(request).create(payload, actor=actor, request_id=_request_id(request))
            result = _envelope(request, value)
            response.headers["Location"] = f"/api/v1/exports/{value.id}"
            _finish(store, scope, digest, result, 202)
            return result
        except DataCenterError as exc:
            _fail(store, scope, digest, exc.code)
            _raise(exc)

    @router.get("/exports/{id}", response_model=SuccessEnvelope[ExportView], operation_id="get_prediction_record_export")
    def get_export(id: str, request: Request):
        try:
            value = exports(request).get(id, actor=_actor(request))
        except DataCenterError as exc:
            _raise(exc)
        return _envelope(request, value)

    return router


create_data_center_router = create_router

__all__ = ["VersionReferenceRequest", "create_router", "create_data_center_router"]
