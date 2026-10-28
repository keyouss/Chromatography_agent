"""Non-sensitive settings shared by every build profile."""

from __future__ import annotations

from fastapi import APIRouter, Request

from .dto import ActorContext, StrictModel, SuccessEnvelope
from .errors import APIError


class PublicSettingsView(StrictModel):
    product_name: str
    build_id: str
    default_project_id: str | None = None
    input_limits: dict[str, int]
    export_limits: dict[str, int]
    allowed_modes: list[str]
    built_modules: list[str]


def _public_settings(manifest: object, actor: ActorContext) -> PublicSettingsView:
    return PublicSettingsView(
        product_name="Chromatography Platform",
        build_id=str(getattr(manifest, "build_id", "unknown")),
        default_project_id=actor.default_project_id,
        input_limits={"message_characters": 20000, "page_size": 200},
        export_limits={"rows": 10000},
        allowed_modes=list(getattr(manifest, "assistant_features", None) or []),
        built_modules=list(getattr(manifest, "modules", ()) or ()),
    )


def create_router() -> APIRouter:
    router = APIRouter(prefix="/settings", tags=["settings"])

    @router.get("/public", response_model=SuccessEnvelope[PublicSettingsView], operation_id="get_public_settings")
    def get_public(request: Request):
        actor = getattr(request.app.state, "actor_context", None)
        if actor is None:
            raise APIError(503, "DEPENDENCY_UNAVAILABLE", "ActorContext未装配", retryable=True)
        manifest = getattr(request.app.state, "manifest", None)
        if manifest is None:
            raise APIError(503, "DEPENDENCY_UNAVAILABLE", "构建配置未装配", retryable=True)
        return SuccessEnvelope(
            data=_public_settings(manifest, actor), warnings=[],
            request_id=str(getattr(request.state, "request_id", "settings-request")),
        )

    return router


__all__ = ["PublicSettingsView", "create_router"]
