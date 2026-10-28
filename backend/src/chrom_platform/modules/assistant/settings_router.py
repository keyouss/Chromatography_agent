"""HTTP routes for public settings and the assistant LLM configuration lifecycle."""

from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, Request, Response, status

from chrom_platform.core.dto import SuccessEnvelope
from chrom_platform.core.errors import APIError
from chrom_platform.core.idempotency import IdempotencyScope
from .persistence import LlmConfigurationActiveRow

from .secret_store import WindowsDpapiSecretStore
from .settings_schemas import (
    LlmConfigurationActivate, LlmConfigurationCreate, LlmConfigurationTestRequest,
    LlmConfigurationView, LlmSettingsView, LlmTestAccepted, LlmTestResult,
)
from .settings_service import (
    activate_configuration, create_configuration, get_settings, get_test,
    resolve_project, submit_test,
)


_IDEMPOTENCY_PARAMETER = {"in": "header", "name": "Idempotency-Key", "required": True, "schema": {"type": "string", "minLength": 1, "maxLength": 128}}
_IF_MATCH_PARAMETER = {"in": "header", "name": "If-Match", "required": True, "schema": {"type": "string", "minLength": 1, "maxLength": 256}}
_ETAG_RESPONSE_HEADER = {"ETag": {"required": True, "schema": {"type": "string"}}}
_LOCATION_RESPONSE_HEADER = {"Location": {"required": True, "schema": {"type": "string", "format": "uri-reference"}}}


def _session(request: Request):
    ports = getattr(request.state, "m4_ports", None)
    session = getattr(ports, "session", None)
    if session is None:
        raise APIError(503, "DEPENDENCY_UNAVAILABLE", "assistant持久化未装配", retryable=True)
    return session


def _actor(request: Request):
    actor = getattr(request.app.state, "actor_context", None)
    if actor is None:
        raise APIError(503, "DEPENDENCY_UNAVAILABLE", "ActorContext未装配", retryable=True)
    return actor


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", "settings-request"))


def _key(value: str | None) -> str:
    if value is None:
        raise APIError(400, "IDEMPOTENCY_KEY_REQUIRED", "缺少 Idempotency-Key")
    if not value or len(value) > 128:
        raise APIError(422, "VALIDATION_FAILED", "Idempotency-Key 长度无效")
    return value


def _if_match(value: str | None) -> str:
    if value is None:
        raise APIError(428, "IF_MATCH_REQUIRED", "缺少 If-Match")
    if not value or len(value) > 256:
        raise APIError(422, "VALIDATION_FAILED", "If-Match 长度无效")
    return value


def _request_hash(route: str, payload: dict[str, object]) -> str:
    raw = json.dumps({"route": route, "payload": payload}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _reserve(request: Request, *, actor, project_id: str, route: str, key: str, payload: dict[str, object]):
    store = getattr(request.state, "idempotency_store", None)
    if store is None:
        raise APIError(503, "DEPENDENCY_UNAVAILABLE", "幂等记录存储未配置", retryable=True)
    digest = _request_hash(route, payload)
    scope = IdempotencyScope(actor_id=actor.actor_id, project_id=project_id, method="POST", route_identity=route, key=key)
    existing = store.get(scope)
    if existing is not None:
        if existing.payload_hash != digest:
            raise APIError(409, "IDEMPOTENCY_KEY_REUSED", "幂等键已用于其他请求")
        if existing.status == "IN_PROGRESS":
            raise APIError(409, "OPERATION_IN_PROGRESS", "相同幂等请求正在处理中", retryable=True)
        if existing.status == "COMPLETED":
            return store, scope, digest, existing
        raise APIError(503, "OPERATION_RETRY_REQUIRED", "上一次操作未完成，可使用新幂等键重试", retryable=True)
    reservation = store.begin(scope, digest)
    if not getattr(reservation, "acquired", False):
        record = getattr(reservation, "record", None)
        if record is None or record.payload_hash != digest:
            raise APIError(409, "IDEMPOTENCY_KEY_REUSED", "幂等键已用于其他请求")
        if record.status == "COMPLETED":
            return store, scope, digest, record
        raise APIError(409, "OPERATION_IN_PROGRESS", "相同幂等请求正在处理中", retryable=True)
    return store, scope, digest, None


def _finish(store, scope, digest: str, response: SuccessEnvelope, status_code: int) -> None:
    store.complete(scope, digest, status_code, response.model_dump(mode="json"))


def _replay(record, model, request: Request, response: Response):
    value = model.model_validate(record.response)
    value.request_id = _request_id(request)
    response.headers["Idempotency-Replayed"] = "true"
    return value


def _config_location() -> str:
    return "/api/v1/settings/llm"


def _test_location(test_id: str) -> str:
    return f"/api/v1/settings/llm/tests/{test_id}"


def create_router(*, secret_store: WindowsDpapiSecretStore | None = None) -> APIRouter:
    router = APIRouter(prefix="/settings", tags=["settings"])

    @router.get("/llm", response_model=SuccessEnvelope[LlmSettingsView], operation_id="get_llm_settings", openapi_extra={"responses": {"200": {"headers": _ETAG_RESPONSE_HEADER}}})
    def get_llm(request: Request, response: Response):
        session, actor = _session(request), _actor(request)
        value = get_settings(session, actor)
        pointer = session.get(LlmConfigurationActiveRow, resolve_project(actor))
        response.headers["ETag"] = pointer.etag if pointer is not None else '"active-0"'
        return SuccessEnvelope(data=value, warnings=[], request_id=_request_id(request))

    @router.post("/llm/configurations", response_model=SuccessEnvelope[LlmConfigurationView], status_code=status.HTTP_201_CREATED, operation_id="create_llm_configuration", openapi_extra={"parameters": [_IDEMPOTENCY_PARAMETER], "responses": {"201": {"headers": {**_ETAG_RESPONSE_HEADER, **_LOCATION_RESPONSE_HEADER}}}})
    def create_llm(payload: LlmConfigurationCreate, request: Request, response: Response):
        actor = _actor(request)
        key = _key(request.headers.get("Idempotency-Key"))
        project_id = resolve_project(actor)
        payload_for_hash = payload.model_dump(mode="json", exclude={"api_key"})
        if payload.api_key is not None:
            # The only persisted representation is the final request hash; the
            # raw value exists only in this transient canonicalization input.
            payload_for_hash["api_key"] = payload.api_key.get_secret_value()
        store, scope, digest, replay = _reserve(request, actor=actor, project_id=project_id, route="settings.llm.configurations.create", key=key, payload=payload_for_hash)
        if replay is not None:
            value = _replay(replay, SuccessEnvelope[LlmConfigurationView], request, response)
            response.headers["ETag"] = value.data.etag
            response.headers["Location"] = _config_location()
            return value
        value = create_configuration(session=_session(request), actor=actor, request=payload, secret_store=secret_store, request_id=_request_id(request))
        result = SuccessEnvelope(data=value, warnings=[], request_id=_request_id(request))
        _finish(store, scope, digest, result, 201)
        response.headers["ETag"] = value.etag
        response.headers["Location"] = _config_location()
        return result

    @router.post("/llm/configurations/{configuration_id}/test", response_model=SuccessEnvelope[LlmTestAccepted], status_code=status.HTTP_202_ACCEPTED, operation_id="test_llm_configuration", openapi_extra={"parameters": [_IDEMPOTENCY_PARAMETER], "responses": {"202": {"description": "测试任务已接受", "headers": {"Location": {"required": True, "schema": {"type": "string", "format": "uri-reference"}}}}}})
    def test_llm(configuration_id: str, payload: LlmConfigurationTestRequest, request: Request, response: Response):
        actor = _actor(request)
        key = _key(request.headers.get("Idempotency-Key"))
        project_id = resolve_project(actor)
        route = f"settings.llm.configurations.{configuration_id}.test"
        store, scope, digest, replay = _reserve(request, actor=actor, project_id=project_id, route=route, key=key, payload={})
        if replay is not None:
            value = _replay(replay, SuccessEnvelope[LlmTestAccepted], request, response)
            response.headers["Location"] = _test_location(value.data.id)
            return value
        value = submit_test(_session(request), actor, configuration_id, request_id=_request_id(request))
        result = SuccessEnvelope(data=value, warnings=[], request_id=_request_id(request))
        _finish(store, scope, digest, result, 202)
        response.headers["Location"] = _test_location(value.id)
        return result

    @router.get("/llm/tests/{test_id}", response_model=SuccessEnvelope[LlmTestResult], operation_id="get_llm_test")
    def get_llm_test(test_id: str, request: Request):
        value = get_test(_session(request), _actor(request), test_id)
        return SuccessEnvelope(data=value, warnings=[], request_id=_request_id(request))

    @router.post("/llm/configurations/{configuration_id}/activate", response_model=SuccessEnvelope[LlmSettingsView], operation_id="activate_llm_configuration", openapi_extra={"parameters": [_IDEMPOTENCY_PARAMETER, _IF_MATCH_PARAMETER], "responses": {"200": {"headers": _ETAG_RESPONSE_HEADER}}})
    def activate_llm(configuration_id: str, payload: LlmConfigurationActivate, request: Request, response: Response):
        actor = _actor(request)
        key = _key(request.headers.get("Idempotency-Key"))
        etag = _if_match(request.headers.get("If-Match"))
        project_id = resolve_project(actor)
        route = f"settings.llm.configurations.{configuration_id}.activate"
        request_payload = {**payload.model_dump(mode="json"), "if_match": etag}
        store, scope, digest, replay = _reserve(request, actor=actor, project_id=project_id, route=route, key=key, payload=request_payload)
        if replay is not None:
            value = _replay(replay, SuccessEnvelope[LlmSettingsView], request, response)
            response.headers["ETag"] = f'"active-{value.data.active_revision}"'
            return value
        value = activate_configuration(
            _session(request), actor, configuration_id, test_id=payload.test_id,
            expected_active_revision=payload.expected_active_revision,
            if_match=etag, request_id=_request_id(request),
        )
        result = SuccessEnvelope(data=value, warnings=[], request_id=_request_id(request))
        _finish(store, scope, digest, result, 200)
        pointer = _session(request).get(LlmConfigurationActiveRow, project_id)
        response.headers["ETag"] = pointer.etag if pointer is not None else '"active-0"'
        return result

    return router


__all__ = ["create_router"]
