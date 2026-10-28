from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import ValidationError

from chrom_platform.core.dto import ActorContext
from chrom_platform.core.errors import APIError
from chrom_platform.modules.assistant.errors import AssistantError
from chrom_platform.modules.assistant.router import create_router
from chrom_platform.modules.assistant.schemas import ContextSnapshotCompareRequest
from chrom_platform.modules.assistant.priority_router import create_priority_router


def test_trace_and_event_routes_have_typed_success_contracts() -> None:
    app = FastAPI()
    app.include_router(create_router(trace_enabled=True), prefix="/api/v1")
    spec = app.openapi()

    typed = (
        "/api/v1/agent-runs/{run_id}/events",
        "/api/v1/agent-runs/{run_id}/llm-calls",
        "/api/v1/llm-calls/{call_id}",
        "/api/v1/llm-calls/{call_id}/context",
        "/api/v1/context-snapshots/{snapshot_id}",
        "/api/v1/context-snapshots/{snapshot_id}/content",
        "/api/v1/context-snapshots/compare",
    )
    for path in typed:
        operation = next(value for method, value in spec["paths"][path].items() if method in {"get", "post"})
        assert operation["responses"]["200"]["content"]
        assert all(media.get("schema") for media in operation["responses"]["200"]["content"].values())

    stream = spec["paths"]["/api/v1/agent-runs/{run_id}/events/stream"]["get"]
    assert stream["responses"]["200"]["content"]["text/event-stream"]["schema"] == {"type": "string"}


def test_context_snapshot_compare_rejects_unknown_fields() -> None:
    try:
        ContextSnapshotCompareRequest.model_validate({"left_id": "left", "right_id": "right", "extra": True})
    except ValidationError:
        return
    raise AssertionError("unknown compare request fields must be rejected")


def test_priority_tools_route_serializes_all_declared_tools_including_normalize_structure() -> None:
    app = FastAPI()
    app.include_router(create_priority_router())
    response = TestClient(app).get("/assistant/priority/tools")
    assert response.status_code == 200
    names = [item["name"] for item in response.json()["tools"]]
    assert names == [
        "normalize_structure",
        "get_prediction_capabilities",
        "create_prediction",
        "get_prediction",
        "get_data_version_summary",
        "get_prediction_record_summary",
        "list_models",
        "get_model_card",
        "create_registration_draft",
        "get_registration_draft",
    ]


class _HeaderProbeAssistant:
    def patch_conversation(self, *args, **kwargs):
        raise AssistantError(412, "VERSION_CONFLICT", "会话版本已变化")


def test_write_header_http_boundaries_run_before_assistant_dependency() -> None:
    app = FastAPI()
    app.state.actor_context = ActorContext(
        actor_id="actor-a", tenant_id=None, project_ids=["project-a"],
        auth_method="deployment_default", permissions=["*"], policy_version="test",
    )
    app.add_exception_handler(APIError, lambda request, exc: JSONResponse(status_code=exc.status_code, content={"error": exc.payload.model_dump(mode="json")}))
    app.include_router(create_router(assistant_service=_HeaderProbeAssistant()), prefix="/api/v1")
    client = TestClient(app)
    create_body = {"project_id": "project-a", "mode": "business"}
    patch_body = {"title": "updated"}
    assert client.post("/api/v1/conversations", json=create_body).status_code == 400
    assert client.post("/api/v1/conversations", headers={"Idempotency-Key": ""}, json=create_body).status_code == 422
    assert client.post("/api/v1/conversations", headers={"Idempotency-Key": "x" * 129}, json=create_body).status_code == 422
    endpoint = "/api/v1/conversations/c-1"
    assert client.patch(endpoint, headers={"Idempotency-Key": "k"}, json=patch_body).status_code == 428
    assert client.patch(endpoint, headers={"Idempotency-Key": "k", "If-Match": ""}, json=patch_body).status_code == 422
    assert client.patch(endpoint, headers={"Idempotency-Key": "k", "If-Match": "x" * 257}, json=patch_body).status_code == 422
    assert client.patch(endpoint, headers={"Idempotency-Key": "k", "If-Match": "stale"}, json=patch_body).status_code == 412
