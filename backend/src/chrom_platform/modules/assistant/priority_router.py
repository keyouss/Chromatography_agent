"""HTTP adapter for the priority launch assistant capabilities."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from fastapi import APIRouter, Header, Query, Request, Response, status

from chrom_platform.core.dto import ListEnvelope, SuccessEnvelope
from chrom_platform.core.errors import APIError
from chrom_platform.modules.model_center.schemas import Model, ModelCard, ModelRegistration, ModelRegistrationCreate

from .priority import (
    DataVersionSummary,
    PredictionRecordSummary,
    PriorityAssistantService,
    ProviderReadiness,
    PriorityToolsView,
    priority_toolset,
)
from .settings_service import get_provider_readiness


class PriorityRegistrationDraftRequest(ModelRegistrationCreate):
    """Model registration draft input.

    Draft creation is side-effect free with respect to registration/publish;
    confirmation is supplied only to the model-center register transition.
    """
    pass


def _ctx(request: Request):
    actor = getattr(request.app.state, "actor_context", None)
    if actor is None:
        raise APIError(503, "DEPENDENCY_UNAVAILABLE", "ActorContext 未配置", retryable=True)
    from chrom_platform.task_core.service import OperationContext

    return OperationContext(actor=actor, interaction="assistant", request_id=getattr(request.state, "request_id", "assistant-priority"))


def _service(request: Request, explicit: PriorityAssistantService | None) -> PriorityAssistantService:
    value = explicit or getattr(request.state, "assistant_priority_service", None) or getattr(request.app.state, "assistant_priority_service", None)
    if value is None:
        raise APIError(503, "DEPENDENCY_UNAVAILABLE", "优先助手服务未配置", retryable=True)
    return value


def _project(request: Request, project_id: str) -> None:
    actor = request.app.state.actor_context
    if not actor.can_access_project(project_id):
        raise APIError(404, "RESOURCE_NOT_FOUND", "项目不存在或当前主体不可见")


def _scope(request: Request, project_id: str, permission: str) -> None:
    _project(request, project_id)
    actor = request.app.state.actor_context
    if not actor.can(permission):
        raise APIError(403, "PERMISSION_DENIED", f"缺少 {permission} 权限")


def _envelope(request: Request, value):
    return SuccessEnvelope(data=value, warnings=[], request_id=getattr(request.state, "request_id", "assistant-priority"))


def _model_created_at(value: Any) -> datetime:
    raw = value.get("created_at") if isinstance(value, Mapping) else getattr(value, "created_at", None)
    if isinstance(raw, datetime):
        parsed = raw
    elif isinstance(raw, str):
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            parsed = datetime.min.replace(tzinfo=timezone.utc)
    else:
        parsed = datetime.min.replace(tzinfo=timezone.utc)
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _model_id(value: Any) -> str:
    raw = value.get("id") if isinstance(value, Mapping) else getattr(value, "id", "")
    return str(raw or "")


def _stable_model_order(values: list[Any]) -> list[Any]:
    """Sort already scope/artifact-filtered models before offset pagination."""

    return sorted(values, key=lambda value: (_model_created_at(value), _model_id(value)), reverse=True)


def create_priority_router(*, service: PriorityAssistantService | None = None) -> APIRouter:
    router = APIRouter(prefix="/assistant/priority", tags=["assistant-priority"])

    @router.get("/tools", response_model=PriorityToolsView, operation_id="list_priority_assistant_tools")
    def list_tools():
        return PriorityToolsView(tools=priority_toolset().schemas(), request_id="assistant-priority")

    @router.get("/provider-readiness", response_model=SuccessEnvelope[ProviderReadiness], operation_id="get_assistant_provider_readiness")
    def provider_readiness(request: Request):
        ports = getattr(request.state, "m4_ports", None)
        session = getattr(ports, "session", None)
        if session is None:
            raise APIError(503, "DEPENDENCY_UNAVAILABLE", "assistant持久化未装配", retryable=True)
        actor = getattr(request.app.state, "actor_context", None)
        if actor is None:
            raise APIError(503, "DEPENDENCY_UNAVAILABLE", "ActorContext未配置", retryable=True)
        readiness = get_provider_readiness(session, actor)
        return SuccessEnvelope(data=readiness, warnings=[], request_id=getattr(request.state, "request_id", "assistant-priority"))

    @router.get("/data-version-summary", response_model=SuccessEnvelope[DataVersionSummary], operation_id="get_assistant_data_version_summary")
    def data_version_summary(request: Request, project_id: str = Query(...), domain_id: str = Query(...)):
        _project(request, project_id)
        return _envelope(request, _service(request, service).data_version_summary(_ctx(request), project_id, domain_id=domain_id))

    @router.get("/prediction-record-summary", response_model=SuccessEnvelope[PredictionRecordSummary], operation_id="get_assistant_prediction_record_summary")
    def prediction_record_summary(request: Request, project_id: str = Query(...), chromatography_type: str | None = Query(None)):
        _project(request, project_id)
        return _envelope(request, _service(request, service).prediction_record_summary(_ctx(request), project_id, chromatography_type=chromatography_type))

    @router.get("/models", response_model=ListEnvelope[Model], operation_id="list_assistant_models")
    def list_models(
        request: Request,
        project_id: str = Query(...),
        task_type: str | None = Query(None),
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ):
        _scope(request, project_id, "model:read")
        from chrom_platform.core.dto import PageInfo

        values = _stable_model_order(list(_service(request, service).list_models(_ctx(request), project_id, task_type=task_type)))
        page_values = values[offset:offset + limit]
        has_more = offset + limit < len(values)
        next_cursor = str(offset + limit) if has_more else None
        return ListEnvelope(data=page_values, page=PageInfo(next_cursor=next_cursor, limit=limit, has_more=has_more), warnings=[], request_id=getattr(request.state, "request_id", "assistant-priority"))

    @router.get("/models/{model_id}/card", response_model=SuccessEnvelope[ModelCard], operation_id="get_assistant_model_card")
    def get_model_card(request: Request, model_id: str, project_id: str = Query(...)):
        _scope(request, project_id, "model:read")
        return _envelope(request, _service(request, service).get_model_card(_ctx(request), project_id, model_id))

    @router.post("/model-registration-drafts", response_model=SuccessEnvelope[ModelRegistration], status_code=status.HTTP_201_CREATED, operation_id="create_assistant_model_registration_draft")
    def create_registration_draft(payload: PriorityRegistrationDraftRequest, request: Request, response: Response, idempotency_key: str | None = Header(None, alias="Idempotency-Key")):
        _scope(request, payload.project_id, "model:register")
        if not idempotency_key:
            raise APIError(400, "IDEMPOTENCY_KEY_REQUIRED", "缺少 Idempotency-Key")
        value = _service(request, service).create_registration_draft(_ctx(request), payload, idempotency_key=idempotency_key)
        response.headers["Location"] = f"/api/v1/assistant/priority/model-registration-drafts/{value.id}"
        return _envelope(request, value)

    @router.get("/model-registration-drafts/{registration_id}", response_model=SuccessEnvelope[ModelRegistration], operation_id="get_assistant_model_registration_draft")
    def get_registration_draft(request: Request, registration_id: str, project_id: str = Query(...)):
        _scope(request, project_id, "model:read")
        return _envelope(request, _service(request, service).get_registration_draft(_ctx(request), project_id, registration_id))

    return router


__all__ = ["PriorityRegistrationDraftRequest", "create_priority_router"]
