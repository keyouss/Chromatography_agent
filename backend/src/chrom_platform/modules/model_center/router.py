"""Typed HTTP adapter for model-center operations."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from typing import TypeVar

from fastapi import APIRouter, Header, Query, Request, Response

from chrom_platform.core.dto import ActorContext, ListEnvelope, PageInfo, SuccessEnvelope
from chrom_platform.core.errors import APIError

from .errors import ModelCenterError
from .schemas import (
    ApprovalDecision, ApprovalRequest, ApprovalRequestCreate, Artifact, ComparisonCreate,
    ConfirmationRequest, Deployment, DeploymentAccepted, DeploymentDraft,
    DeploymentDraftCreate, EmptyRequest, EvaluationAccepted, EvaluationCreate,
    EvaluationProtocol, Model, ModelComparison, ModelDetail, ModelEvaluation,
    ModelRegistration, ModelRegistrationCreate, RegistrationAccepted,
    RollbackDraftCreate,
)
from .service import ModelCenterService


_IF_MATCH = {"in": "header", "name": "If-Match", "required": True, "schema": {"type": "string", "minLength": 1, "maxLength": 256}}
_IDEMPOTENCY = {"in": "header", "name": "Idempotency-Key", "required": True, "schema": {"type": "string", "minLength": 1, "maxLength": 128}}
_LOCATION = {"headers": {"Location": {"description": "受理资源详情地址", "schema": {"type": "string"}}}}
T = TypeVar("T")
_CURSOR_SECRET = b"model-center-cursor-v1"


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "model-center-request")


def _envelope(request: Request, value: T, response: Response | None = None, *, location: str | None = None) -> SuccessEnvelope[T]:
    if response is not None:
        etag = getattr(value, "etag", None) or getattr(getattr(value, "model", None), "etag", None)
        if etag:
            response.headers["ETag"] = etag
        if getattr(value, "_idempotency_replayed", False):
            response.headers["Idempotency-Replayed"] = "true"
        if location:
            response.headers["Location"] = location
    return SuccessEnvelope(data=value, warnings=[], request_id=_request_id(request))


def _actor(request: Request) -> ActorContext:
    value = getattr(request.app.state, "actor_context", None)
    if value is None:
        raise APIError(503, "DEPENDENCY_UNAVAILABLE", "ActorContext 未配置", retryable=True)
    return value


def _service(request: Request, explicit: ModelCenterService | None) -> ModelCenterService:
    value = explicit or getattr(request.state, "model_center_service", None) or getattr(request.app.state, "model_center_service", None)
    if value is None:
        raise APIError(503, "DEPENDENCY_UNAVAILABLE", "模型中心服务未配置", retryable=True)
    return value


def _scope(actor: ActorContext, project_id: str, *, write: bool = False, permission: str | None = None) -> None:
    if not actor.can_access_project(project_id):
        raise APIError(403 if write else 404, "PERMISSION_DENIED" if write else "RESOURCE_NOT_FOUND", "无权访问当前项目")
    if permission and not actor.can(permission):
        raise APIError(403, "PERMISSION_DENIED", f"缺少 {permission} 权限")


def _raise(exc: ModelCenterError) -> None:
    raise APIError(exc.status_code, exc.code, exc.message, details=exc.details)


def _header(value: str | None, name: str, status_code: int = 428) -> str:
    if value is None:
        raise APIError(status_code, "IF_MATCH_REQUIRED" if name == "If-Match" else "IDEMPOTENCY_KEY_REQUIRED", f"缺少 {name}")
    limit = 256 if name == "If-Match" else 128
    if value == "" or len(value) > limit:
        raise APIError(422, "VALIDATION_FAILED", f"{name} 长度无效")
    return value


def _cursor_payload(cursor: str | None, *, context: dict[str, object]) -> int:
    if not cursor:
        return 0
    try:
        raw = cursor + "=" * (-len(cursor) % 4)
        encoded = base64.urlsafe_b64decode(raw.encode())
        payload_raw, signature = encoded.rsplit(b".", 1)
        expected = hmac.new(_CURSOR_SECRET, payload_raw, hashlib.sha256).hexdigest().encode()
        payload = json.loads(payload_raw.decode())
        if not hmac.compare_digest(signature, expected) or payload.get("context") != context:
            raise ValueError("cursor context mismatch")
        value = int(payload["offset"])
    except (ValueError, KeyError, TypeError, UnicodeError, binascii.Error, json.JSONDecodeError) as exc:
        raise APIError(422, "VALIDATION_FAILED", "cursor 无效或与当前筛选条件不匹配") from exc
    if value < 0:
        raise APIError(422, "VALIDATION_FAILED", "cursor 无效")
    return value


def _cursor(offset: int, *, context: dict[str, object]) -> str:
    payload_raw = json.dumps({"offset": offset, "context": context}, sort_keys=True, separators=(",", ":")).encode()
    signature = hmac.new(_CURSOR_SECRET, payload_raw, hashlib.sha256).hexdigest().encode()
    return base64.urlsafe_b64encode(payload_raw + b"." + signature).decode().rstrip("=")


def _page(values: list[T], cursor: str | None, limit: int, *, context: dict[str, object]) -> tuple[list[T], PageInfo]:
    start = _cursor_payload(cursor, context=context)
    items = values[start:start + limit]
    has_more = start + len(items) < len(values)
    return items, PageInfo(next_cursor=_cursor(start + len(items), context=context) if has_more else None, limit=limit, has_more=has_more)


def create_router(*, service: ModelCenterService | None = None) -> APIRouter:
    router = APIRouter(tags=["model-center"])

    @router.get("/models", response_model=ListEnvelope[Model], operation_id="list_models")
    def list_models(request: Request, project_id: str = Query(...), task_type: str | None = Query(None), lifecycle: str | None = Query(None), scope_id: str | None = Query(None), cursor: str | None = Query(None), limit: int = Query(50, ge=1, le=200)):
        _scope(_actor(request), project_id, permission="model:read")
        try:
            values = _service(request, service).list_models(project_id, task_type=task_type, lifecycle=lifecycle, scope_id=scope_id)
            items, page = _page(list(values), cursor, limit, context={"actor_id": _actor(request).actor_id, "project_id": project_id, "filters": {"task_type": task_type, "lifecycle": lifecycle, "scope_id": scope_id}, "sort": "created_at.desc,id.desc"})
            return ListEnvelope(data=items, page=page, warnings=[], request_id=_request_id(request))
        except ModelCenterError as exc:
            _raise(exc)

    @router.post("/models/registrations", response_model=SuccessEnvelope[ModelRegistration], status_code=201, openapi_extra={"parameters": [_IDEMPOTENCY]}, operation_id="create_model_registration")
    def create_registration(payload: ModelRegistrationCreate, request: Request, response: Response, idempotency_key: str | None = Header(None, alias="Idempotency-Key", include_in_schema=False)):
        key = _header(idempotency_key, "Idempotency-Key", 400)
        actor = _actor(request); _scope(actor, payload.project_id, write=True, permission="model:register")
        try:
            value = _service(request, service).create_registration(payload, idempotency_key=key, actor=actor)
            return _envelope(request, value, response, location=f"/api/v1/models/registrations/{value.id}")
        except ModelCenterError as exc:
            _raise(exc)

    @router.get("/models/registrations/{registration_id}", response_model=SuccessEnvelope[ModelRegistration], operation_id="get_model_registration")
    def get_registration(registration_id: str, request: Request, response: Response, project_id: str = Query(...)):
        _scope(_actor(request), project_id, permission="model:register")
        try:
            return _envelope(request, _service(request, service).get_registration(project_id, registration_id), response)
        except ModelCenterError as exc:
            _raise(exc)

    @router.post("/models/registrations/{registration_id}/validate", response_model=SuccessEnvelope[RegistrationAccepted], status_code=202, responses={202: _LOCATION}, openapi_extra={"parameters": [_IF_MATCH, _IDEMPOTENCY]}, operation_id="validate_model_registration")
    def validate_registration(registration_id: str, payload: EmptyRequest, request: Request, response: Response, project_id: str = Query(...), if_match: str | None = Header(None, alias="If-Match", include_in_schema=False), idempotency_key: str | None = Header(None, alias="Idempotency-Key", include_in_schema=False)):
        match = _header(if_match, "If-Match"); key = _header(idempotency_key, "Idempotency-Key", 400)
        _scope(_actor(request), project_id, write=True, permission="model:register")
        try:
            value = _service(request, service).queue_registration_validation(project_id, registration_id, idempotency_key=key, if_match=match, actor=_actor(request))
            return _envelope(request, value, response, location=f"/api/v1/models/registrations/{registration_id}")
        except ModelCenterError as exc:
            _raise(exc)

    @router.post("/models/registrations/{registration_id}/register", response_model=SuccessEnvelope[Model], status_code=201, openapi_extra={"parameters": [_IDEMPOTENCY]}, operation_id="register_model_candidate")
    def register_model(registration_id: str, payload: ConfirmationRequest, request: Request, response: Response, idempotency_key: str | None = Header(None, alias="Idempotency-Key", include_in_schema=False), project_id: str = Query(...)):
        key = _header(idempotency_key, "Idempotency-Key", 400)
        actor = _actor(request); _scope(actor, project_id, write=True, permission="model:register")
        try:
            value = _service(request, service).register(project_id, registration_id, confirmation_id=payload.confirmation_id, actor=actor, idempotency_key=key)
            return _envelope(request, value, response, location=f"/api/v1/models/{value.id}")
        except ModelCenterError as exc:
            _raise(exc)

    @router.get("/models/{model_id}", response_model=SuccessEnvelope[ModelDetail], operation_id="get_model_detail")
    def get_model(model_id: str, request: Request, response: Response, project_id: str = Query(...), scope_id: str | None = Query(None)):
        _scope(_actor(request), project_id, permission="model:read")
        try:
            return _envelope(request, _service(request, service).get_model(project_id, model_id, scope_id=scope_id), response)
        except ModelCenterError as exc:
            _raise(exc)

    @router.get("/models/{model_id}/artifacts", response_model=ListEnvelope[Artifact], operation_id="list_model_artifacts")
    def list_model_artifacts(model_id: str, request: Request, project_id: str = Query(...), cursor: str | None = Query(None), limit: int = Query(50, ge=1, le=200)):
        _scope(_actor(request), project_id, permission="model:read")
        try:
            values = _service(request, service).list_model_artifacts(project_id, model_id)
            items, page = _page(list(values), cursor, limit, context={"actor_id": _actor(request).actor_id, "project_id": project_id, "filters": {"model_id": model_id}, "sort": "created_at.desc,id.desc"})
            return ListEnvelope(data=items, page=page, warnings=[], request_id=_request_id(request))
        except ModelCenterError as exc:
            _raise(exc)

    @router.get("/evaluation-protocols", response_model=ListEnvelope[EvaluationProtocol], operation_id="list_evaluation_protocols")
    def list_protocols(request: Request, task_type: str | None = Query(None), cursor: str | None = Query(None), limit: int = Query(50, ge=1, le=200)):
        if not _actor(request).can("model:evaluate"):
            raise APIError(403, "PERMISSION_DENIED", "缺少 model:evaluate 权限")
        try:
            values = _service(request, service).list_protocols(task_type)
            items, page = _page(list(values), cursor, limit, context={"actor_id": _actor(request).actor_id, "project_id": None, "filters": {"task_type": task_type}, "sort": "id.asc,version.asc"})
            return ListEnvelope(data=items, page=page, warnings=[], request_id=_request_id(request))
        except ModelCenterError as exc:
            _raise(exc)

    @router.post("/model-evaluations", response_model=SuccessEnvelope[EvaluationAccepted], status_code=202, responses={202: _LOCATION}, openapi_extra={"parameters": [_IDEMPOTENCY]}, operation_id="create_model_evaluation")
    def create_evaluation(payload: EvaluationCreate, request: Request, response: Response, idempotency_key: str | None = Header(None, alias="Idempotency-Key", include_in_schema=False)):
        key = _header(idempotency_key, "Idempotency-Key", 400)
        actor = _actor(request); _scope(actor, payload.project_id, write=True, permission="model:evaluate")
        try:
            value = _service(request, service).queue_evaluation(payload, actor=actor, idempotency_key=key)
            return _envelope(request, value, response, location=f"/api/v1/model-evaluations/{value.id}")
        except ModelCenterError as exc:
            _raise(exc)

    @router.get("/model-evaluations/{evaluation_id}", response_model=SuccessEnvelope[ModelEvaluation], operation_id="get_model_evaluation")
    def get_evaluation(evaluation_id: str, request: Request, response: Response, project_id: str = Query(...)):
        _scope(_actor(request), project_id, permission="model:evaluate")
        try:
            return _envelope(request, _service(request, service).get_evaluation(project_id, evaluation_id), response)
        except ModelCenterError as exc:
            _raise(exc)

    @router.post("/model-comparisons", response_model=SuccessEnvelope[ModelComparison], operation_id="compare_models")
    def compare_models(payload: ComparisonCreate, request: Request, response: Response):
        actor = _actor(request); _scope(actor, payload.project_id, write=True, permission="model:evaluate")
        try:
            value = _service(request, service).compare(payload, actor=actor)
            return _envelope(request, value, response, location=f"/api/v1/model-comparisons/{value.id}")
        except ModelCenterError as exc:
            _raise(exc)

    @router.post("/models/{model_id}/approval-requests", response_model=SuccessEnvelope[ApprovalRequest], status_code=201, openapi_extra={"parameters": [_IDEMPOTENCY]}, operation_id="create_model_approval_request")
    def create_approval(model_id: str, payload: ApprovalRequestCreate, request: Request, response: Response, project_id: str = Query(...), idempotency_key: str | None = Header(None, alias="Idempotency-Key", include_in_schema=False)):
        key = _header(idempotency_key, "Idempotency-Key", 400)
        actor = _actor(request); _scope(actor, project_id, write=True, permission="model:approve")
        try:
            value = _service(request, service).create_approval_request(project_id, model_id, payload, actor=actor, idempotency_key=key)
            return _envelope(request, value, response, location=f"/api/v1/approval-requests/{value.id}")
        except ModelCenterError as exc:
            _raise(exc)

    @router.get("/approval-requests/{approval_id}", response_model=SuccessEnvelope[ApprovalRequest], operation_id="get_model_approval")
    def get_approval(approval_id: str, request: Request, response: Response, project_id: str = Query(...)):
        _scope(_actor(request), project_id, permission="model:approve")
        try:
            return _envelope(request, _service(request, service).get_approval_request(project_id, approval_id), response)
        except ModelCenterError as exc:
            _raise(exc)

    def decision(approval_id: str, payload: ApprovalDecision, request: Request, response: Response, *, approve: bool, project_id: str, if_match: str | None, idempotency_key: str | None):
        match = _header(if_match, "If-Match"); key = _header(idempotency_key, "Idempotency-Key", 400)
        actor = _actor(request); _scope(actor, project_id, write=True, permission="model:approve")
        try:
            value = _service(request, service).decide_approval(project_id, approval_id, payload, approve=approve, actor=actor, idempotency_key=key, if_match=match)
            return _envelope(request, value, response, location=f"/api/v1/approval-requests/{approval_id}")
        except ModelCenterError as exc:
            _raise(exc)

    @router.post("/approval-requests/{approval_id}/approve", response_model=SuccessEnvelope[ApprovalRequest], operation_id="approve_model", openapi_extra={"parameters": [_IF_MATCH, _IDEMPOTENCY]})
    def approve(approval_id: str, payload: ApprovalDecision, request: Request, response: Response, project_id: str = Query(...), if_match: str | None = Header(None, alias="If-Match", include_in_schema=False), idempotency_key: str | None = Header(None, alias="Idempotency-Key", include_in_schema=False)):
        return decision(approval_id, payload, request, response, approve=True, project_id=project_id, if_match=if_match, idempotency_key=idempotency_key)

    @router.post("/approval-requests/{approval_id}/reject", response_model=SuccessEnvelope[ApprovalRequest], operation_id="reject_model", openapi_extra={"parameters": [_IF_MATCH, _IDEMPOTENCY]})
    def reject(approval_id: str, payload: ApprovalDecision, request: Request, response: Response, project_id: str = Query(...), if_match: str | None = Header(None, alias="If-Match", include_in_schema=False), idempotency_key: str | None = Header(None, alias="Idempotency-Key", include_in_schema=False)):
        return decision(approval_id, payload, request, response, approve=False, project_id=project_id, if_match=if_match, idempotency_key=idempotency_key)

    @router.post("/model-deployment-drafts", response_model=SuccessEnvelope[DeploymentDraft], status_code=201, openapi_extra={"parameters": [_IDEMPOTENCY]}, operation_id="create_model_deployment_draft")
    def create_draft(payload: DeploymentDraftCreate, request: Request, response: Response, idempotency_key: str | None = Header(None, alias="Idempotency-Key", include_in_schema=False)):
        key = _header(idempotency_key, "Idempotency-Key", 400); actor = _actor(request); _scope(actor, payload.project_id, write=True, permission="model:deploy")
        try:
            value = _service(request, service).create_deployment_draft(payload, actor=actor, idempotency_key=key)
            return _envelope(request, value, response, location=f"/api/v1/model-deployment-drafts/{value.id}")
        except ModelCenterError as exc:
            _raise(exc)

    @router.get("/model-deployment-drafts/{draft_id}", response_model=SuccessEnvelope[DeploymentDraft], operation_id="get_model_deployment_draft")
    def get_draft(draft_id: str, request: Request, response: Response, project_id: str = Query(...)):
        _scope(_actor(request), project_id, permission="model:deploy")
        try:
            return _envelope(request, _service(request, service).get_draft(project_id, draft_id), response)
        except ModelCenterError as exc:
            _raise(exc)

    @router.post("/model-deployment-drafts/{draft_id}/validate", response_model=SuccessEnvelope[DeploymentDraft], openapi_extra={"parameters": [_IF_MATCH, _IDEMPOTENCY]}, operation_id="validate_model_deployment_draft")
    def validate_draft(draft_id: str, payload: EmptyRequest, request: Request, response: Response, project_id: str = Query(...), if_match: str | None = Header(None, alias="If-Match", include_in_schema=False), idempotency_key: str | None = Header(None, alias="Idempotency-Key", include_in_schema=False)):
        match = _header(if_match, "If-Match"); key = _header(idempotency_key, "Idempotency-Key", 400)
        _scope(_actor(request), project_id, write=True, permission="model:deploy")
        try:
            value = _service(request, service).validate_deployment_request(project_id, draft_id, if_match=match, idempotency_key=key, actor=_actor(request))
            return _envelope(request, value, response, location=f"/api/v1/model-deployment-drafts/{draft_id}")
        except ModelCenterError as exc:
            _raise(exc)

    @router.post("/model-deployment-drafts/{draft_id}/submit", response_model=SuccessEnvelope[DeploymentAccepted], status_code=202, responses={202: _LOCATION}, openapi_extra={"parameters": [_IDEMPOTENCY]}, operation_id="submit_model_deployment")
    def submit_draft(draft_id: str, payload: ConfirmationRequest, request: Request, response: Response, project_id: str = Query(...), idempotency_key: str | None = Header(None, alias="Idempotency-Key", include_in_schema=False)):
        key = _header(idempotency_key, "Idempotency-Key", 400); actor = _actor(request); _scope(actor, project_id, write=True, permission="model:deploy")
        try:
            value = _service(request, service).submit_deployment(project_id, draft_id, confirmation_id=payload.confirmation_id, actor=actor, idempotency_key=key)
            return _envelope(request, value, response, location=f"/api/v1/model-deployments/{value.id}")
        except ModelCenterError as exc:
            _raise(exc)

    @router.get("/model-deployments", response_model=ListEnvelope[Deployment], operation_id="list_model_deployments")
    def list_deployments(request: Request, project_id: str = Query(...), scope_id: str | None = Query(None), cursor: str | None = Query(None), limit: int = Query(50, ge=1, le=200)):
        _scope(_actor(request), project_id, permission="model:deploy")
        try:
            values = _service(request, service).list_deployments(project_id, scope_id)
            items, page = _page(list(values), cursor, limit, context={"actor_id": _actor(request).actor_id, "project_id": project_id, "filters": {"scope_id": scope_id}, "sort": "created_at.desc,id.desc"})
            return ListEnvelope(data=items, page=page, warnings=[], request_id=_request_id(request))
        except ModelCenterError as exc:
            _raise(exc)

    @router.get("/model-deployments/{deployment_id}", response_model=SuccessEnvelope[Deployment], operation_id="get_model_deployment")
    def get_deployment(deployment_id: str, request: Request, response: Response, project_id: str = Query(...)):
        _scope(_actor(request), project_id, permission="model:deploy")
        try:
            return _envelope(request, _service(request, service).get_deployment(project_id, deployment_id), response)
        except ModelCenterError as exc:
            _raise(exc)

    @router.post("/model-deployments/{deployment_id}/rollback-drafts", response_model=SuccessEnvelope[DeploymentDraft], status_code=201, openapi_extra={"parameters": [_IDEMPOTENCY]}, operation_id="create_model_rollback_draft")
    def rollback_draft(deployment_id: str, payload: RollbackDraftCreate, request: Request, response: Response, project_id: str = Query(...), idempotency_key: str | None = Header(None, alias="Idempotency-Key", include_in_schema=False)):
        key = _header(idempotency_key, "Idempotency-Key", 400); actor = _actor(request); _scope(actor, project_id, write=True, permission="model:deploy")
        try:
            value = _service(request, service).create_rollback_draft(project_id, deployment_id, payload, actor=actor, idempotency_key=key)
            return _envelope(request, value, response, location=f"/api/v1/model-deployment-drafts/{value.id}")
        except ModelCenterError as exc:
            _raise(exc)

    return router


__all__ = ["create_router"]
