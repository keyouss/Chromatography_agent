from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import TypeAdapter
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.responses import JSONResponse

from chrom_platform.core.dto import ActorContext
from chrom_platform.core.errors import APIError
from chrom_platform.task_core.service import OperationContext
from chrom_platform.modules.assistant.errors import AssistantError
from chrom_platform.modules.assistant.adapter import ProviderTransportError, _OpenAICompatibleClient, secure_provider_request
import chrom_platform.modules.assistant.priority as priority_module
from chrom_platform.modules.assistant.priority import (
    PRIORITY_TOOL_NAMES,
    PriorityAssistantService,
    PriorityAssistantToolService,
    PriorityOperationContext,
    inspect_provider_config,
    priority_toolset,
    probe_configured_provider,
    provider_config_revision,
    resolve_runtime_config,
)
from chrom_platform.modules.model_center.errors import ModelCenterNotFoundError, ModelCenterPermissionError
from chrom_platform.modules.model_center.schemas import ModelRegistration, ModelRegistrationCreate
from chrom_platform.modules.assistant.priority_router import create_priority_router
from chrom_platform.modules.prediction.schemas import PredictionCreate
from chrom_platform.modules.assistant.runtime import AgentRuntime, RuntimeConfigSnapshot, _model_tool_result, _summary
from chrom_platform.modules.assistant.tools import ToolExecutionLedger, make_toolset


def test_provider_revision_is_stable_and_tracks_effective_configuration() -> None:
    first = provider_config_revision(
        url="HTTPS://api.openai.com/v1/?ignored=secret",
        model_id="model-a",
        key_reference_name="API_KEY",
        source="dotenv",
    )
    same = provider_config_revision(
        url="https://api.openai.com/v1/",
        model_id="model-a",
        key_reference_name="API_KEY",
        source="dotenv",
    )
    changed_model = provider_config_revision(
        url="https://api.openai.com/v1/",
        model_id="model-b",
        key_reference_name="API_KEY",
        source="dotenv",
    )
    changed_source = provider_config_revision(
        url="https://api.openai.com/v1/",
        model_id="model-a",
        key_reference_name="API_KEY",
        source="environment",
    )
    assert first == same
    assert first != changed_model
    assert first != changed_source
    assert first.startswith("sha256:")


def test_runtime_config_generates_revision_without_accepting_secret_as_reference() -> None:
    config = resolve_runtime_config(
        {
            "BASE_URL": "https://api.openai.com/v1",
            "MODEL_ID": "model-a",
            "LLM_KEY_REFERENCE": "API_KEY",
            "API_KEY": "actual-secret-value",
        }
    )
    assert config.key_reference == "API_KEY"
    assert config.config_revision.startswith("sha256:")


def test_missing_provider_is_unavailable_without_networking_to_sentinel(monkeypatch) -> None:
    config = resolve_runtime_config({})
    assert config.configuration_state == "UNAVAILABLE"
    monkeypatch.setattr(_OpenAICompatibleClient, "_resolved_public_ips", staticmethod(lambda *_args: (_ for _ in ()).throw(AssertionError("sentinel was resolved"))))
    with pytest.raises(AssistantError, match="配置不可用"):
        _OpenAICompatibleClient().complete(config=config, messages=[], tools=[])


def test_provider_url_requires_https_and_server_allowlist() -> None:
    with pytest.raises(ValueError):
        resolve_runtime_config({"BASE_URL": "http://api.openai.com/v1", "MODEL_ID": "model-a", "LLM_KEY_REFERENCE": "API_KEY", "API_KEY": "secret"})
    with pytest.raises(ValueError):
        resolve_runtime_config({"BASE_URL": "https://untrusted.example/v1", "MODEL_ID": "model-a", "LLM_KEY_REFERENCE": "API_KEY", "API_KEY": "secret"})
    config = resolve_runtime_config({"BASE_URL": "https://untrusted.example/v1", "MODEL_ID": "model-a", "LLM_KEY_REFERENCE": "API_KEY", "API_KEY": "secret", "LLM_PROVIDER_ALLOWED_HOSTS": "untrusted.example"})
    assert config.url == "https://untrusted.example/v1"


def test_secure_provider_request_rechecks_trusted_url_before_network(monkeypatch) -> None:
    monkeypatch.setattr(_OpenAICompatibleClient, "_resolved_public_ips", staticmethod(lambda *_args: (_ for _ in ()).throw(AssertionError("network must not be reached"))))
    with pytest.raises(ValueError):
        secure_provider_request("http://api.openai.com/v1")
    with pytest.raises(ValueError):
        secure_provider_request("https://untrusted.example/v1")


def test_key_reference_resolves_secret_without_exposing_secret() -> None:
    evidence = inspect_provider_config(
        {
            "LLM_PROVIDER_URL": "http://127.0.0.1:1",
            "LLM_MODEL_ID": "model-a",
            "LLM_KEY_REFERENCE": "API_KEY",
            "API_KEY": "super-secret-value",
        },
        timeout_seconds=0.01,
    )
    assert evidence.key_reference_resolvable is True
    assert "super-secret-value" not in evidence.model_dump_json()


def test_missing_key_reference_fails_closed() -> None:
    evidence = inspect_provider_config(
        {"LLM_PROVIDER_URL": "http://127.0.0.1:1", "LLM_MODEL_ID": "model-a", "LLM_KEY_REFERENCE": "API_KEY"},
        timeout_seconds=0.01,
    )
    assert evidence.status == "BLOCKED"
    assert evidence.key_reference_resolvable is False


def test_readiness_and_probe_use_pinned_secure_transport(monkeypatch) -> None:
    calls = []
    response = b'{"choices":[{"message":{"tool_calls":[{"id":"c1","function":{"name":"get_prediction_capabilities","arguments":"{}"}}]}}]}'
    def secure(url, **kwargs):
        calls.append((url, kwargs.get("method")))
        return 200, {"x-request-id": "req-safe"}, response
    monkeypatch.setattr(priority_module, "secure_provider_request", secure)
    env = {"LLM_PROVIDER_URL": "https://api.openai.com/v1", "LLM_MODEL_ID": "model-a", "LLM_KEY_REFERENCE": "API_KEY", "API_KEY": "secret"}
    readiness = inspect_provider_config(env, timeout_seconds=0.01)
    probe = probe_configured_provider(environ=env, timeout_seconds=0.01)
    assert readiness.status == "READY"
    assert probe.status == "READY"
    assert [method for _, method in calls] == ["GET", "POST", "POST"]


def test_probe_reports_safe_transport_diagnostics(monkeypatch) -> None:
    def secure(_url, **_kwargs):
        raise ProviderTransportError("HTTP_4XX", http_status=429, provider_error_code="rate_limit_exceeded")
    monkeypatch.setattr(priority_module, "secure_provider_request", secure)
    env = {"LLM_PROVIDER_URL": "https://api.openai.com/v1", "LLM_MODEL_ID": "model-a", "LLM_KEY_REFERENCE": "API_KEY", "API_KEY": "secret"}
    probe = probe_configured_provider(environ=env, timeout_seconds=0.01)
    assert probe.status == "BLOCKED"
    assert probe.error_category == "HTTP_4XX"
    assert {item.get("provider_error_code") for item in probe.variants.values()} == {"rate_limit_exceeded"}


def test_registration_draft_does_not_require_confirmation() -> None:
    class Artifacts:
        def get_artifact(self, artifact_id, actor):
            return type("Artifact", (), {"kind": "model_package", "project_id": "project-1", "status": "AVAILABLE", "scan_status": "PASSED"})()

    class Models:
        def create_registration(self, request, *, idempotency_key, actor):
            return type("Draft", (), {"id": "registration-1", "config_hash": "a" * 64, "_idempotency_replayed": False})()

    class Confirmation:
        def validate(self, *args, **kwargs):
            raise AssertionError("draft creation must not validate confirmation")

    actor = ActorContext(actor_id="actor", project_ids=["project-1"], auth_method="service", permissions=["*"], policy_version="p")
    ctx = OperationContext(actor=actor)
    request = ModelRegistrationCreate.model_validate({"project_id": "project-1", "source": {"kind": "external_package", "artifact_id": "artifact-1"}, "name": "model", "task_type": "sfc"})
    value = PriorityAssistantService(models=Models(), artifacts=Artifacts(), confirmation=Confirmation()).create_registration_draft(ctx, request, idempotency_key="draft-1")
    assert value.id == "registration-1"


def test_registration_tools_attach_resource_ref_and_preserve_requery_fields() -> None:
    class Draft:
        id = "registration-1"
        project_id = "project-1"
        revision = 1
        config_hash = "a" * 64
        etag = '"1"'
        status = "DRAFT"
        source = {"kind": "external_package", "artifact_id": "artifact-1"}
        name = "model"
        task_type = "tlc"

        def model_dump(self, mode="json"):
            return {key: getattr(self, key) for key in ("id", "project_id", "revision", "config_hash", "etag", "status", "source", "name", "task_type")}

    class Artifacts:
        def get_artifact(self, _artifact_id, _actor):
            return type("Artifact", (), {"kind": "model_package", "project_id": "project-1", "status": "AVAILABLE", "scan_status": "PASSED"})()

    class Models:
        def __init__(self):
            self.lookups = []

        def create_registration(self, _request, *, idempotency_key, actor):
            return Draft()

        def get_registration(self, project_id, registration_id):
            self.lookups.append((project_id, registration_id))
            if registration_id != Draft.id:
                raise APIError(404, "RESOURCE_NOT_FOUND", "登记草稿不可见")
            return Draft()

    class Inspector:
        def __init__(self):
            self.calls = []

        def capture_pair(self, **_kwargs):
            return type("Snapshot", (), {"id": "snapshot"})(), type("Snapshot", (), {"id": "snapshot"})()

        def save_llm_call(self, _call):
            pass

        def save_tool_call(self, call, **_kwargs):
            self.calls.append(call)

    models = Models()
    service = PriorityAssistantToolService(PriorityAssistantService(models=models, artifacts=Artifacts()))
    bound = priority_toolset().bind({"priority": service})
    actor = ActorContext(actor_id="actor", project_ids=["project-1"], auth_method="service", permissions=["*"], policy_version="p")
    ctx = PriorityOperationContext(actor=actor, server_project_id="project-1", server_artifact_id="artifact-1")
    create_args = {
        "project_id": "project-1",
        "source": {"kind": "external_package", "artifact_id": "artifact-1"},
        "name": "model",
        "task_type": "tlc",
    }
    created = bound["create_registration_draft"](ctx, create_args).result
    assert created["id"] == "registration-1"
    assert created["status"] == "DRAFT"
    assert created["resource_ref"] == {"type": "model_registration", "id": "registration-1"}

    loaded = bound["get_registration_draft"](ctx, {"registration_id": "registration-1"}).result
    assert models.lookups == [("project-1", "registration-1")]
    assert loaded["resource_ref"] == created["resource_ref"]
    assert loaded["source"] == create_args["source"]

    # The runtime projects the helper field into ToolCall.resource_ref on both
    # create and requery while preserving the DTO result for the next model turn.
    inspector = Inspector()

    class Provider:
        def __init__(self):
            self.calls = 0

        def complete(self, *, messages, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return {"tool_calls": [{"id": "call-create", "name": "create_registration_draft", "arguments": create_args}]}
            if self.calls == 2:
                tool_message = next(item for item in reversed(messages) if item.get("role") == "tool")
                returned = json.loads(tool_message["content"])
                return {"tool_calls": [{"id": "call-get", "name": "get_registration_draft", "arguments": {"registration_id": returned["id"]}}]}
            return {"text": "草稿已创建并重新查询。"}

    provider = Provider()
    result = AgentRuntime(
        provider, RuntimeConfigSnapshot("https://api.openai.com/v1", "model", "API_KEY", "rev"),
        history=[{"role": "user", "content": "创建并查询模型登记草稿"}],
        toolset=bound, context_inspector=inspector, max_requests=3,
    ).legacy_run_for_tests(run_id="run-registration-ref", ctx=ctx)
    assert result.status == "SUCCEEDED", result.error
    succeeded = {call.tool_name: call for call in inspector.calls if call.status == "SUCCEEDED"}
    assert succeeded["create_registration_draft"].resource_ref.model_dump() == created["resource_ref"]
    assert succeeded["get_registration_draft"].resource_ref.model_dump() == created["resource_ref"]
    assert models.lookups[-1] == ("project-1", created["id"])

    missing_inspector = Inspector()
    missing_provider = type("Provider", (), {
        "complete": lambda self, **_kwargs: {"tool_calls": [{
            "id": "call-get-missing", "name": "get_registration_draft",
            "arguments": {"registration_id": "missing-registration"},
        }]},
    })()
    missing_result = AgentRuntime(
        missing_provider, RuntimeConfigSnapshot("https://api.openai.com/v1", "model", "API_KEY", "rev"),
        history=[{"role": "user", "content": "查询不存在的登记草稿"}],
        toolset=bound, context_inspector=missing_inspector, max_requests=2,
    ).legacy_run_for_tests(run_id="run-registration-ref-missing", ctx=ctx)
    assert missing_result.status == "FAILED"
    failed_get = [call for call in missing_inspector.calls if call.tool_name == "get_registration_draft"]
    assert failed_get and failed_get[-1].status == "FAILED"
    assert all(call.resource_ref is None for call in failed_get)


def test_priority_models_empty_inventory_returns_valid_empty_page() -> None:
    class Models:
        def list_models(self, _project_id, *, task_type=None):
            assert task_type is None
            return []

    class Artifacts:
        def get_artifact(self, _artifact_id, _actor):
            raise AssertionError("empty model inventory must not inspect artifacts")

    actor = ActorContext(
        actor_id="actor",
        project_ids=["priority-local"],
        auth_method="deployment_default",
        permissions=["*"],
        policy_version="p",
    )
    app = FastAPI()
    app.state.actor_context = actor
    app.include_router(
        create_priority_router(service=PriorityAssistantService(models=Models(), artifacts=Artifacts())),
        prefix="/api/v1",
    )

    response = TestClient(app).get("/api/v1/assistant/priority/models?project_id=priority-local")

    assert response.status_code == 200
    body = response.json()
    assert body["data"] == []
    assert body["page"] == {"next_cursor": None, "limit": 50, "has_more": False}


def test_priority_models_page_metadata_is_bounded_for_zero_one_and_large_inventory() -> None:
    now = datetime.now(timezone.utc)

    def model(index: int) -> dict:
        return {
            "id": f"model-{index}", "project_id": "priority-local", "name": f"Model {index}",
            "version": "1", "task_type": "sfc", "artifact_id": f"artifact-{index}",
            "sha256": "a" * 64, "lifecycle_status": "APPROVED",
            "input_schema_version": "in-1", "output_schema_version": "out-1",
            "source": {}, "created_at": now, "updated_at": now,
        }

    class Models:
        def __init__(self, count: int):
            self.values = [model(index) for index in range(count)]

        def list_models(self, _project_id, *, task_type=None):
            return self.values

    class Artifacts:
        def get_artifact(self, artifact_id, _actor):
            return type("Artifact", (), {"kind": "model_package", "project_id": "priority-local", "status": "AVAILABLE", "scan_status": "PASSED"})()

    actor = ActorContext(actor_id="actor", project_ids=["priority-local"], auth_method="deployment_default", permissions=["*"], policy_version="p")
    for count, expected_size, expected_more in ((0, 0, False), (1, 1, False), (201, 50, True)):
        app = FastAPI()
        app.state.actor_context = actor
        app.include_router(create_priority_router(service=PriorityAssistantService(models=Models(count), artifacts=Artifacts())), prefix="/api/v1")
        response = TestClient(app).get("/api/v1/assistant/priority/models?project_id=priority-local")
        assert response.status_code == 200
        body = response.json()
        assert len(body["data"]) == expected_size
        assert body["page"]["limit"] == 50
        assert body["page"]["has_more"] is expected_more
        assert body["page"]["next_cursor"] == ("50" if expected_more else None)


def test_priority_models_sort_after_visibility_filter_before_middle_end_and_out_of_range_pages() -> None:
    shared = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def model(model_id: str, created_at, artifact_id: str) -> dict:
        return {
            "id": model_id, "project_id": "priority-local", "name": model_id, "version": "1", "task_type": "sfc",
            "artifact_id": artifact_id, "sha256": "a" * 64, "lifecycle_status": "APPROVED",
            "input_schema_version": "in-1", "output_schema_version": "out-1", "source": {}, "created_at": created_at, "updated_at": created_at,
        }

    values = [
        model("model-b", shared.isoformat().replace("+00:00", "Z"), "artifact-b"),
        model("model-a", shared, "artifact-a"),
        model("model-new", "2026-02-01T00:00:00Z", "artifact-new"),
        model("model-hidden", "2026-03-01T00:00:00Z", "artifact-hidden"),
    ]

    class Models:
        def list_models(self, _project_id, *, task_type=None):
            return list(values)

    class Artifacts:
        def get_artifact(self, artifact_id, _actor):
            status = "REJECTED" if artifact_id == "artifact-hidden" else "AVAILABLE"
            scan_status = "FAILED" if artifact_id == "artifact-hidden" else "PASSED"
            return type("Artifact", (), {"kind": "model_package", "project_id": "priority-local", "status": status, "scan_status": scan_status})()

    actor = ActorContext(actor_id="actor", project_ids=["priority-local"], auth_method="deployment_default", permissions=["*"], policy_version="p")
    app = FastAPI()
    app.state.actor_context = actor
    app.include_router(create_priority_router(service=PriorityAssistantService(models=Models(), artifacts=Artifacts())), prefix="/api/v1")
    client = TestClient(app)

    middle = client.get("/api/v1/assistant/priority/models?project_id=priority-local&limit=1&offset=1")
    assert middle.status_code == 200
    assert [item["id"] for item in middle.json()["data"]] == ["model-b"]
    assert middle.json()["page"] == {"next_cursor": "2", "limit": 1, "has_more": True}

    end = client.get("/api/v1/assistant/priority/models?project_id=priority-local&limit=2&offset=1")
    assert [item["id"] for item in end.json()["data"]] == ["model-b", "model-a"]
    assert end.json()["page"] == {"next_cursor": None, "limit": 2, "has_more": False}

    out_of_range = client.get("/api/v1/assistant/priority/models?project_id=priority-local&limit=1&offset=9")
    assert out_of_range.status_code == 200
    assert out_of_range.json()["data"] == []
    assert out_of_range.json()["page"] == {"next_cursor": None, "limit": 1, "has_more": False}


def test_registration_draft_rejects_non_model_or_cross_project_artifacts() -> None:
    actor = ActorContext(actor_id="actor", project_ids=["project-1"], auth_method="service", permissions=["*"], policy_version="p")
    ctx = OperationContext(actor=actor)
    request = ModelRegistrationCreate.model_validate({"project_id": "project-1", "source": {"kind": "external_package", "artifact_id": "artifact-1"}, "name": "model", "task_type": "sfc"})

    class Artifacts:
        def __init__(self, kind="dataset_package", project_id="project-1"):
            self.kind, self.project_id = kind, project_id

        def get_artifact(self, _artifact_id, _actor):
            return type("Artifact", (), {"kind": self.kind, "project_id": self.project_id, "status": "AVAILABLE", "scan_status": "PASSED"})()

    service = PriorityAssistantService(artifacts=Artifacts())
    with pytest.raises(AssistantError, match="ARTIFACT_NOT_VERIFIED|制品"):
        service.create_registration_draft(ctx, request, idempotency_key="draft-invalid-kind")
    service.artifacts = Artifacts(kind="model_package", project_id="other-project")
    with pytest.raises(AssistantError, match="ARTIFACT_NOT_VERIFIED|制品"):
        service.create_registration_draft(ctx, request, idempotency_key="draft-invalid-project")


def test_priority_model_routes_require_model_permissions_and_project_scope() -> None:
    actor = ActorContext(actor_id="actor", project_ids=["project-1"], auth_method="service", permissions=["prediction:read"], policy_version="p")
    app = FastAPI()
    app.state.actor_context = actor
    app.include_router(create_priority_router(service=PriorityAssistantService()), prefix="/api/v1")

    @app.exception_handler(APIError)
    async def api_error_handler(_request, exc):
        return JSONResponse(status_code=exc.status_code, content=exc.payload.model_dump(mode="json"))

    client = TestClient(app)
    assert client.get("/api/v1/assistant/priority/models?project_id=project-1").status_code == 403
    assert client.get("/api/v1/assistant/priority/models/model-1/card?project_id=project-1").status_code == 403
    assert client.get("/api/v1/assistant/priority/model-registration-drafts/reg-1?project_id=project-1").status_code == 403
    assert client.post(
        "/api/v1/assistant/priority/model-registration-drafts",
        headers={"Idempotency-Key": "draft-permission"},
        json={"project_id": "project-1", "source": {"kind": "external_package", "artifact_id": "artifact-1"}, "name": "model", "task_type": "sfc"},
    ).status_code == 403
    assert client.get("/api/v1/assistant/priority/models?project_id=project-2").status_code == 404


def test_registration_draft_get_maps_missing_model_center_resource_to_safe_404() -> None:
    class Models:
        def get_registration(self, project_id, registration_id):
            assert project_id == "priority-local"
            assert registration_id == "registration_missing_acceptance_20261007"
            raise ModelCenterNotFoundError("internal DSN=do-not-return")

    actor = ActorContext(
        actor_id="actor",
        project_ids=["priority-local"],
        auth_method="deployment_default",
        permissions=["model:read"],
        policy_version="p",
    )
    app = FastAPI()
    app.state.actor_context = actor
    app.include_router(create_priority_router(service=PriorityAssistantService(models=Models())), prefix="/api/v1")

    @app.exception_handler(APIError)
    async def api_error_handler(_request, exc):
        return JSONResponse(status_code=exc.status_code, content=exc.payload.model_dump(mode="json"))

    response = TestClient(app).get(
        "/api/v1/assistant/priority/model-registration-drafts/registration_missing_acceptance_20261007?project_id=priority-local"
    )

    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "RESOURCE_NOT_FOUND"
    assert body["message"] == "登记草稿不存在或当前主体不可见"
    assert "DSN" not in response.text
    assert "internal" not in response.text


def test_registration_draft_get_maps_repository_none_to_safe_404() -> None:
    class Models:
        def get_registration(self, project_id, registration_id):
            assert (project_id, registration_id) == ("priority-local", "registration_missing_repository_none")
            return None

    class Artifacts:
        def get_artifact(self, *_args, **_kwargs):
            raise AssertionError("missing registration must not inspect an artifact")

    actor = ActorContext(
        actor_id="actor",
        project_ids=["priority-local"],
        auth_method="deployment_default",
        permissions=["model:read"],
        policy_version="p",
    )
    app = FastAPI()
    app.state.actor_context = actor
    app.include_router(
        create_priority_router(service=PriorityAssistantService(models=Models(), artifacts=Artifacts())),
        prefix="/api/v1",
    )

    @app.exception_handler(APIError)
    async def api_error_handler(_request, exc):
        return JSONResponse(status_code=exc.status_code, content=exc.payload.model_dump(mode="json"))

    response = TestClient(app).get(
        "/api/v1/assistant/priority/model-registration-drafts/registration_missing_repository_none?project_id=priority-local"
    )

    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "RESOURCE_NOT_FOUND"
    assert body["message"] == "登记草稿不存在或当前主体不可见"
    assert "registration_missing_repository_none" not in response.text


def test_registration_draft_get_returns_valid_draft_and_keeps_project_scope() -> None:
    registration = ModelRegistration.model_validate(
        {
            "id": "registration-valid",
            "project_id": "priority-local",
            "revision": 1,
            "config_hash": "a" * 64,
            "etag": '"1"',
            "status": "DRAFT",
            "source": {"kind": "external_package", "artifact_id": "artifact-1"},
            "name": "validated-model",
            "task_type": "sfc",
            "created_at": "2026-10-07T00:00:00Z",
            "updated_at": "2026-10-07T00:00:00Z",
        }
    )

    class Models:
        def get_registration(self, project_id, registration_id):
            assert (project_id, registration_id) == ("priority-local", "registration-valid")
            return registration

    class Artifacts:
        def get_artifact(self, artifact_id, actor):
            assert artifact_id == "artifact-1"
            return type(
                "Artifact",
                (),
                {
                    "kind": "model_package",
                    "project_id": "priority-local",
                    "status": "AVAILABLE",
                    "scan_status": "PASSED",
                },
            )()

    actor = ActorContext(
        actor_id="actor",
        project_ids=["priority-local"],
        auth_method="deployment_default",
        permissions=["model:read"],
        policy_version="p",
    )
    app = FastAPI()
    app.state.actor_context = actor
    app.include_router(
        create_priority_router(service=PriorityAssistantService(models=Models(), artifacts=Artifacts())),
        prefix="/api/v1",
    )
    response = TestClient(app).get(
        "/api/v1/assistant/priority/model-registration-drafts/registration-valid?project_id=priority-local"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["id"] == "registration-valid"
    assert body["data"]["project_id"] == "priority-local"
    assert body["data"]["status"] == "DRAFT"


def test_registration_draft_get_rejects_cross_project_and_model_center_permission() -> None:
    class Models:
        def get_registration(self, _project_id, _registration_id):
            raise ModelCenterPermissionError("internal permission details")

    def make_client(*, projects, permissions):
        actor = ActorContext(
            actor_id="actor",
            project_ids=projects,
            auth_method="deployment_default",
            permissions=permissions,
            policy_version="p",
        )
        app = FastAPI()
        app.state.actor_context = actor
        app.include_router(create_priority_router(service=PriorityAssistantService(models=Models())), prefix="/api/v1")

        @app.exception_handler(APIError)
        async def api_error_handler(_request, exc):
            return JSONResponse(status_code=exc.status_code, content=exc.payload.model_dump(mode="json"))

        return TestClient(app)

    cross_project = make_client(projects=["priority-local"], permissions=["model:read"]).get(
        "/api/v1/assistant/priority/model-registration-drafts/registration-valid?project_id=other-project"
    )
    assert cross_project.status_code == 404
    assert cross_project.json()["code"] == "RESOURCE_NOT_FOUND"

    permission_error = make_client(projects=["priority-local"], permissions=["model:read"]).get(
        "/api/v1/assistant/priority/model-registration-drafts/registration-valid?project_id=priority-local"
    )
    assert permission_error.status_code == 403
    assert permission_error.json()["code"] == "PERMISSION_DENIED"
    assert permission_error.json()["message"] == "当前主体无权访问登记草稿"
    assert "internal permission details" not in permission_error.text


def test_model_list_and_card_reject_wrong_artifact_kind_or_project() -> None:
    actor = ActorContext(actor_id="actor", project_ids=["project-1"], auth_method="service", permissions=["*"], policy_version="p")
    model = {"id": "model-1", "project_id": "project-1", "name": "Model", "version": "1", "task_type": "sfc", "artifact_id": "artifact-1", "sha256": "a" * 64, "lifecycle_status": "APPROVED", "input_schema_version": "in-1", "output_schema_version": "out-1", "source": {}}

    class Models:
        def list_models(self, _project_id, *, task_type=None):
            return [model]

        def get_model(self, _project_id, _model_id):
            return model

    class Artifacts:
        def __init__(self, *, kind, project_id):
            self.kind, self.project_id = kind, project_id

        def get_artifact(self, _artifact_id, _actor):
            return type("Artifact", (), {"kind": self.kind, "project_id": self.project_id, "status": "AVAILABLE", "scan_status": "PASSED"})()

    ctx = OperationContext(actor=actor)
    for kind, project_id in (("dataset_package", "project-1"), ("model_package", "project-2")):
        service = PriorityAssistantService(models=Models(), artifacts=Artifacts(kind=kind, project_id=project_id))
        assert service.list_models(ctx, "project-1") == []
        with pytest.raises(AssistantError) as error:
            service.get_model_card(ctx, "project-1", "model-1")
        assert error.value.payload.code == "ARTIFACT_NOT_VERIFIED"


def test_runtime_tool_uses_server_bound_scope_without_ctx_project_id() -> None:
    tool = PriorityAssistantToolService(object(), project_id="server-project", artifact_id="server-artifact")
    ctx = OperationContext(actor=ActorContext(actor_id="actor", project_ids=["server-project"], auth_method="service", permissions=["*"], policy_version="p"))
    arguments = {"project_id": "server-project", "source": {"kind": "external_package", "artifact_id": "server-artifact"}, "name": "test", "task_type": "sfc"}
    tool.validate(ctx, tool_name="create_registration_draft", arguments=arguments)
    with pytest.raises(AssistantError):
        tool.validate(ctx, tool_name="create_registration_draft", arguments={**arguments, "project_id": "other-project"})


def test_runtime_tool_resolves_only_server_scope_and_rejects_source_tampering() -> None:
    tool = PriorityAssistantToolService(object())
    ctx = type("WorkerContext", (), {
        "server_project_id": "server-project",
        "server_artifact_id": "server-artifact",
    })()
    arguments = {"project_id": "server-project", "source": {"kind": "external_package", "artifact_id": "server-artifact"}, "name": "test", "task_type": "sfc"}
    tool.validate(ctx, tool_name="create_registration_draft", arguments=arguments)
    with pytest.raises(AssistantError):
        tool.validate(ctx, tool_name="create_registration_draft", arguments={**arguments, "source": {"kind": "external_package", "artifact_id": "other-artifact"}})
    with pytest.raises(AssistantError):
        tool.validate(type("WorkerContext", (), {"server_project_id": "other-project", "server_artifact_id": "server-artifact"})(), tool_name="create_registration_draft", arguments=arguments)


def test_priority_worker_registry_matches_all_declared_tools_and_dispatches_prediction_chain() -> None:
    class Prediction:
        def __init__(self):
            self.created = None

        def capabilities(self):
            return {"capabilities": ["tlc"]}

        def create(self, ctx, request, *, idempotency_key):
            self.created = (ctx, request, idempotency_key)
            return {"prediction_id": "pred-1"}

    prediction = Prediction()
    service = PriorityAssistantToolService(PriorityAssistantService(prediction=prediction))
    toolset = priority_toolset()
    assert {spec.name for spec in toolset.specs} == set(PRIORITY_TOOL_NAMES)
    assert service.executable_tool_names() == frozenset(PRIORITY_TOOL_NAMES)
    bound = toolset.bind({"priority": service})
    assert set(bound) == set(PRIORITY_TOOL_NAMES)

    actor = ActorContext(actor_id="actor", project_ids=["project-1"], auth_method="service", permissions=["*"], policy_version="p")
    ctx = PriorityOperationContext(actor=actor, server_project_id="project-1", idempotency_key="assistant-key")
    assert bound["get_prediction_capabilities"](ctx, {}).result == {"capabilities": ["tlc"]}
    request = {
        "schema_version": "1.0",
        "model_selection": {"mode": "exact", "model_id": "model-1", "model_version": "1"},
        "structure_refs": ["structure-1"],
        "chromatography_type": "tlc",
        "tool": "single",
        "input": {"solvent_components": [{"solvent_id": "hexane", "fraction": 0.8}, {"solvent_id": "ethyl_acetate", "fraction": 0.2}]},
    }
    result = bound["create_prediction"](ctx, request)
    assert result.result == {"prediction_id": "pred-1"}
    assert prediction.created[1].project_id == "project-1"
    assert prediction.created[2] == "assistant-key"


def test_capability_tool_keeps_real_ready_model_with_oversized_duplicate_schema(monkeypatch) -> None:
    # Redacted model metadata reflects the actual 2026-10-26 TLC capability; only tool metadata is synthetic.
    tlc = {
        "id": "tlc",
        "health": {"status": "READY"},
        "models": [{
            "id": "tlc-author-ensemble-lgb-xgb-rf-ann",
            "version": "v1.1-author-ensemble",
            "health": {"status": "READY"},
            "preprocessing_profile_id": None,
            "preprocessing_profile_sha256": None,
        }],
    }

    class Prediction:
        def capabilities(self):
            # The observed capability evidence did not carry this tool projection.
            return [{**tlc, "display_name": "TLC Rf", "tools": [{"id": "single", "schema_version": "1.0", "health": {"status": "READY"}}]}]

    actor = ActorContext(actor_id="actor", project_ids=["project-1"], auth_method="service", permissions=["*"], policy_version="p")
    ctx = PriorityOperationContext(actor=actor, server_project_id="project-1")
    service = PriorityAssistantToolService(PriorityAssistantService(prediction=Prediction()))
    toolset = priority_toolset()
    create_prediction_spec = next(spec for spec in toolset.specs if spec.name == "create_prediction")
    monkeypatch.setattr(priority_module, "_raw_prediction_arguments_schema", lambda *_args: {"description": "x" * 25_000})
    bound = toolset.bind({"priority": service})
    result = bound["get_prediction_capabilities"](ctx, {"chromatography_type": "tlc"}).result

    assert result["requested_type"] == "tlc"
    assert result["ready_models"][0]["id"] == tlc["models"][0]["id"]
    assert result["ready_models"][0]["version"] == tlc["models"][0]["version"]
    assert result["ready_models"][0]["health"]["status"] == "READY"
    assert result["ready_models"][0]["preprocessing_profile_id"] is None
    assert result["ready_models"][0]["preprocessing_profile_sha256"] is None
    assert result["create_prediction"]["tool_name"] == "create_prediction"
    assert result["create_prediction"]["schema_version"] == "1.0"
    assert result["create_prediction"]["tool_schema_versions"] == {"single": "1.0"}
    assert "arguments_schema" not in result["create_prediction"]
    assert create_prediction_spec.argument_schema["oneOf"]
    assert any(
        branch["properties"]["chromatography_type"].get("const") == "tlc"
        for branch in create_prediction_spec.argument_schema["oneOf"]
    )
    wire = json.loads(_summary(_model_tool_result("get_prediction_capabilities", result)))
    assert wire.get("truncated") is not True
    assert len(json.dumps(wire, ensure_ascii=False).encode("utf-8")) <= 20_000
    assert wire["ready_models"][0]["id"] == tlc["models"][0]["id"]
    assert wire["ready_models"][0]["version"] == tlc["models"][0]["version"]
    assert wire["ready_models"][0]["health"]["status"] == "READY"
    assert wire["capability"]["tool_ids"] == ["single"]
    assert wire["create_prediction"]["tool_schema_versions"] == {"single": "1.0"}
    assert "完整业务 input" in wire["next_step"]

    request = {
        "schema_version": result["create_prediction"]["schema_version"],
        "model_selection": {"mode": "exact", "model_id": tlc["models"][0]["id"], "model_version": tlc["models"][0]["version"]},
        "structure_refs": ["structure-1"],
        "chromatography_type": "tlc",
        "tool": "single",
        "input": {"solvent_components": [
            {"solvent_id": "hexane", "fraction": 0.6},
            {"solvent_id": "methanol", "fraction": 0.4},
        ]},
    }
    assert TypeAdapter(PredictionCreate).validate_python(request).structure_refs == ["structure-1"]


def test_existing_data_summary_tool_is_read_only_and_does_not_create_prediction() -> None:
    class DataVersions:
        def __init__(self):
            self.reads = []

        def list(self, project_id, domain_id):
            self.reads.append((project_id, domain_id))
            return []

    class Prediction:
        def __init__(self):
            self.creates = 0

        def create(self, *_args, **_kwargs):
            self.creates += 1
            raise AssertionError("a summary request must not create a prediction")

    versions, prediction = DataVersions(), Prediction()
    service = PriorityAssistantToolService(PriorityAssistantService(data_versions=versions, prediction=prediction))
    bound = priority_toolset().bind({"priority": service})
    actor = ActorContext(actor_id="actor", project_ids=["project-1"], auth_method="service", permissions=["*"], policy_version="p")
    ctx = PriorityOperationContext(actor=actor, server_project_id="project-1")

    value = bound["get_data_version_summary"](ctx, {"domain_id": "domain-1"}).result

    assert versions.reads == [("project-1", "domain-1")]
    assert prediction.creates == 0
    assert value.project_id == "project-1"


def test_capability_guidance_keeps_unknown_and_unready_requests_closed() -> None:
    from chrom_platform.modules.assistant.priority import _prediction_capability_guidance

    rows = [{
        "id": "tlc", "display_name": "TLC", "health": {"status": "READY"},
        "tools": [{"id": "single", "schema_version": "1.0", "health": {"status": "READY"}}],
        "models": [{"id": "model-blocked", "version": "v1", "health": {"status": "BLOCKED"}}],
    }]
    unknown = _prediction_capability_guidance(rows, "sfc")
    assert "create_prediction" not in unknown

    unavailable = _prediction_capability_guidance(rows, "tlc")
    assert unavailable["ready_models"] == []
    assert "create_prediction" not in unavailable


def test_capability_guidance_closes_registered_but_unsupported_type() -> None:
    from chrom_platform.modules.assistant.priority import _prediction_capability_guidance

    rows = [{
        "id": "vendor_x", "display_name": "Vendor X", "health": {"status": "READY"},
        "tools": [{"id": "single", "schema_version": "9.0", "health": {"status": "READY"}}],
        "models": [{"id": "model-x", "version": "v1", "health": {"status": "READY"}}],
    }]
    value = _prediction_capability_guidance(rows, "vendor_x")

    assert value["requested_type"] == "vendor_x"
    assert "create_prediction" not in value
    assert "不要调用 create_prediction" in value["next_step"]


def test_capability_guidance_requires_ready_single_tool_and_keeps_actual_version() -> None:
    from chrom_platform.modules.assistant.priority import _prediction_capability_guidance

    rows = [{
        "id": "tlc", "display_name": "TLC", "health": {"status": "READY"},
        "tools": [
            {"id": "single", "schema_version": "1.7", "health": {"status": "READY"}},
            {"id": "reverse_solvent", "schema_version": "2.0", "health": {"status": "BLOCKED"}},
        ],
        "models": [{"id": "model-tlc", "version": "v1", "health": {"status": "READY"}}],
    }]
    value = _prediction_capability_guidance(rows, "tlc")
    assert value["create_prediction"]["schema_version"] == "1.7"
    assert value["create_prediction"]["tool_schema_versions"] == {"single": "1.7"}


def test_sfc_capability_guidance_preserves_tool_scopes_and_candidate_scope_prompt() -> None:
    from chrom_platform.modules.assistant.priority import _prediction_capability_guidance

    rows = [{
        "id": "sfc", "display_name": "SFC", "health": {"status": "READY"},
        "tools": [
            {"id": "single_column", "schema_version": "1.1", "health": {"status": "READY"}},
            {"id": "full_column", "schema_version": "1.2", "health": {"status": "READY"}},
        ],
        "models": [
            {"id": "sfc-single", "version": "v1", "task_type": "sfc", "scope": "single_column", "health": {"status": "READY"}},
            {"id": "sfc-all", "version": "v2", "task_type": "sfc", "scope": "all_column", "health": {"status": "READY"}},
        ],
    }]

    value = _prediction_capability_guidance(rows, "sfc")

    assert value["create_prediction"]["tool_schema_versions"] == {"single_column": "1.1", "full_column": "1.2"}
    assert {(model["id"], model["scope"]) for model in value["ready_models"]} == {
        ("sfc-single", "single_column"), ("sfc-all", "all_column"),
    }
    assert "tool" in value["next_step"]
    assert "candidate_scope_id" in value["next_step"]
    wire = json.loads(_summary(_model_tool_result("get_prediction_capabilities", value)))
    assert {(model["id"], model["scope"]) for model in wire["ready_models"]} == {
        ("sfc-single", "single_column"), ("sfc-all", "all_column"),
    }
    assert wire["create_prediction"]["tool_schema_versions"] == {"single_column": "1.1", "full_column": "1.2"}


def test_cc_capability_guidance_preserves_preprocessing_profile_binding() -> None:
    import json
    from chrom_platform.modules.assistant.priority import _prediction_capability_guidance

    profile_id = "cc-qgeognn-4g-canonical-v1"
    profile_sha256 = "b" * 64
    rows = [{
        "id": "cc", "display_name": "CC", "health": {"status": "READY"},
        "tools": [{"id": "single", "schema_version": "1.0", "health": {"status": "READY"}}],
        "models": [{
            "id": "qgeognn_silica_4g", "version": "cc-qgeognn-1", "task_type": "cc",
            "scope": "cc-default", "health": {"status": "READY"},
            "preprocessing_profile_id": profile_id,
            "preprocessing_profile_sha256": profile_sha256,
        }],
    }]

    value = _prediction_capability_guidance(rows, "cc")

    assert value["ready_models"][0]["preprocessing_profile_id"] == profile_id
    assert value["ready_models"][0]["preprocessing_profile_sha256"] == profile_sha256
    wire = json.loads(_summary(_model_tool_result("get_prediction_capabilities", value)))
    assert wire["ready_models"][0]["scope"] == "cc-default"
    assert wire["ready_models"][0]["preprocessing_profile_id"] == profile_id
    assert wire["ready_models"][0]["preprocessing_profile_sha256"] == profile_sha256
    schema_json = json.dumps(next(
        spec.argument_schema for spec in priority_toolset().specs if spec.name == "create_prediction"
    ))
    assert '"preprocessing_profile_id"' in schema_json
    assert '"preprocessing_profile_sha256"' in schema_json


def test_priority_worker_binding_fails_closed_without_service_and_keeps_permission_boundary() -> None:
    toolset = priority_toolset()
    assert toolset.bind({}) == {}
    service = PriorityAssistantToolService(PriorityAssistantService(prediction=object()))
    bound = toolset.bind({"priority": service})
    denied = ActorContext(actor_id="actor", project_ids=["project-1"], auth_method="service", permissions=[], policy_version="p")
    ctx = PriorityOperationContext(actor=denied, server_project_id="project-1")
    with pytest.raises(AssistantError, match="无权"):
        bound["get_prediction_capabilities"](ctx, {})


def test_priority_data_and_model_tools_fail_closed_when_ports_are_missing() -> None:
    toolset = priority_toolset()
    service = PriorityAssistantToolService(PriorityAssistantService(prediction=object()))
    bound = toolset.bind({"priority": service})
    actor = ActorContext(actor_id="actor", project_ids=["project-1"], auth_method="service", permissions=["*"], policy_version="p")
    ctx = PriorityOperationContext(actor=actor, server_project_id="project-1", server_artifact_id="artifact-1")
    cases = {
        "get_data_version_summary": {"domain_id": "domain-1"},
        "get_prediction_record_summary": {},
        "list_models": {},
        "get_model_card": {"model_id": "missing"},
        "get_registration_draft": {"registration_id": "missing"},
        "create_registration_draft": {
            "project_id": "project-1",
            "source": {"kind": "external_package", "artifact_id": "artifact-1"},
            "name": "draft",
            "task_type": "tlc",
        },
    }
    for name, arguments in cases.items():
        with pytest.raises(AssistantError) as exc_info:
            bound[name](ctx, arguments)
        assert exc_info.value.payload.code == "DEPENDENCY_UNAVAILABLE", name


def test_priority_read_only_actor_cannot_execute_prediction_or_registration_writes() -> None:
    class Prediction:
        def __init__(self):
            self.calls = 0

        def create(self, *_args, **_kwargs):
            self.calls += 1
            return {"prediction_id": "prediction-1"}

    class Artifacts:
        def get_artifact(self, *_args, **_kwargs):
            return SimpleNamespace(kind="model_package", project_id="project-1", status="AVAILABLE", scan_status="PASSED")

    class Models:
        def __init__(self):
            self.calls = 0

        def create_registration(self, *_args, **_kwargs):
            self.calls += 1
            return SimpleNamespace(id="registration-1", config_hash="a" * 64, _idempotency_replayed=False)

    prediction, models = Prediction(), Models()
    service = PriorityAssistantToolService(PriorityAssistantService(prediction=prediction, models=models, artifacts=Artifacts()))
    bound = priority_toolset().bind({"priority": service})
    actor = ActorContext(
        actor_id="actor", project_ids=["project-1"], auth_method="deployment_default",
        permissions=["prediction:read", "model:read", "structure:read"], policy_version="p",
    )
    ctx = PriorityOperationContext(actor=actor, server_project_id="project-1", server_artifact_id="artifact-1")
    prediction_args = {
        "schema_version": "1.0",
        "model_selection": {"mode": "exact", "model_id": "model-1", "model_version": "1"},
        "structure_refs": ["structure-1"], "chromatography_type": "tlc", "tool": "single",
        "input": {"solvent_components": [{"solvent_id": "hexane", "fraction": 0.8}, {"solvent_id": "ethyl_acetate", "fraction": 0.2}]},
    }
    registration_args = {
        "project_id": "project-1", "source": {"kind": "external_package", "artifact_id": "artifact-1"},
        "name": "model", "task_type": "sfc",
    }

    with pytest.raises(AssistantError) as prediction_error:
        bound["create_prediction"](ctx, prediction_args)
    with pytest.raises(AssistantError) as registration_error:
        bound["create_registration_draft"](ctx, registration_args)

    assert prediction_error.value.payload.code == "PERMISSION_DENIED"
    assert registration_error.value.payload.code == "PERMISSION_DENIED"
    assert prediction.calls == 0
    assert models.calls == 0


def test_priority_legal_actor_can_create_prediction_and_registration_draft_with_audit() -> None:
    class Prediction:
        def __init__(self):
            self.created = []

        def create(self, ctx, request, *, idempotency_key):
            self.created.append((ctx, request, idempotency_key))
            return {"prediction_id": "prediction-1"}

    class Artifacts:
        def get_artifact(self, *_args, **_kwargs):
            return SimpleNamespace(kind="model_package", project_id="project-1", status="AVAILABLE", scan_status="PASSED")

    class Draft:
        id = "registration-1"
        config_hash = "a" * 64

        def model_dump(self, mode="json"):
            return {"id": self.id, "config_hash": self.config_hash, "status": "DRAFT"}

    class Models:
        def create_registration(self, request, *, idempotency_key, actor):
            assert request.project_id == "project-1"
            assert idempotency_key
            assert actor.actor_id == "actor"
            return Draft()

    class Audit:
        def __init__(self):
            self.calls = []

        def record(self, **kwargs):
            self.calls.append(kwargs)

    prediction, audit = Prediction(), Audit()
    service = PriorityAssistantToolService(PriorityAssistantService(prediction=prediction, models=Models(), artifacts=Artifacts(), audit=audit))
    bound = priority_toolset().bind({"priority": service})
    actor = ActorContext(
        actor_id="actor", project_ids=["project-1"], auth_method="deployment_default",
        permissions=["prediction:create", "model:register"], policy_version="p",
    )
    ctx = PriorityOperationContext(actor=actor, server_project_id="project-1", server_artifact_id="artifact-1", idempotency_key="assistant-key")
    prediction_args = {
        "schema_version": "1.0",
        "model_selection": {"mode": "exact", "model_id": "model-1", "model_version": "1"},
        "structure_refs": ["structure-1"], "chromatography_type": "tlc", "tool": "single",
        "input": {"solvent_components": [{"solvent_id": "hexane", "fraction": 0.8}, {"solvent_id": "ethyl_acetate", "fraction": 0.2}]},
    }
    registration_args = {
        "project_id": "project-1", "source": {"kind": "external_package", "artifact_id": "artifact-1"},
        "name": "model", "task_type": "sfc",
    }

    prediction_result = bound["create_prediction"](ctx, prediction_args)
    registration_result = bound["create_registration_draft"](ctx, registration_args)

    assert prediction_result.result == {"prediction_id": "prediction-1"}
    assert prediction.created[0][1].project_id == "project-1"
    assert registration_result.result["resource_ref"] == {"type": "model_registration", "id": "registration-1"}
    assert len(audit.calls) == 1
    assert audit.calls[0]["action"] == "model_registration.draft"
    assert audit.calls[0]["project_id"] == "project-1"


def test_priority_write_replay_does_not_repeat_prediction_or_registration_side_effect() -> None:
    class Prediction:
        def __init__(self):
            self.calls = 0

        def create(self, *_args, **_kwargs):
            self.calls += 1
            return {"prediction_id": "prediction-1"}

    class Artifacts:
        def get_artifact(self, *_args, **_kwargs):
            return SimpleNamespace(kind="model_package", project_id="project-1", status="AVAILABLE", scan_status="PASSED")

    class Draft:
        id = "registration-1"
        config_hash = "a" * 64

        def model_dump(self, mode="json"):
            return {"id": self.id, "config_hash": self.config_hash, "status": "DRAFT"}

    class Models:
        def __init__(self):
            self.calls = 0

        def create_registration(self, *_args, **_kwargs):
            self.calls += 1
            return Draft()

    class Audit:
        def __init__(self):
            self.calls = []

        def record(self, **kwargs):
            self.calls.append(kwargs)

    prediction, models, audit = Prediction(), Models(), Audit()
    service = PriorityAssistantToolService(PriorityAssistantService(prediction=prediction, models=models, artifacts=Artifacts(), audit=audit))
    bound = priority_toolset().bind({"priority": service}, ledger=ToolExecutionLedger())
    actor = ActorContext(
        actor_id="actor", project_ids=["project-1"], auth_method="deployment_default",
        permissions=["prediction:create", "model:register"], policy_version="p",
    )
    ctx = PriorityOperationContext(actor=actor, server_project_id="project-1", server_artifact_id="artifact-1")
    prediction_args = {
        "schema_version": "1.0",
        "model_selection": {"mode": "exact", "model_id": "model-1", "model_version": "1"},
        "structure_refs": ["structure-1"], "chromatography_type": "tlc", "tool": "single",
        "input": {"solvent_components": [{"solvent_id": "hexane", "fraction": 0.8}, {"solvent_id": "ethyl_acetate", "fraction": 0.2}]},
    }
    registration_args = {
        "project_id": "project-1", "source": {"kind": "external_package", "artifact_id": "artifact-1"},
        "name": "model", "task_type": "sfc",
    }

    first_prediction = bound["create_prediction"](ctx, prediction_args)
    replay_prediction = bound["create_prediction"](ctx, prediction_args)
    first_registration = bound["create_registration_draft"](ctx, registration_args)
    replay_registration = bound["create_registration_draft"](ctx, registration_args)

    assert first_prediction.replayed is False
    assert replay_prediction.replayed is True
    assert first_registration.replayed is False
    assert replay_registration.replayed is True
    assert prediction.calls == 1
    assert models.calls == 1
    assert len(audit.calls) == 1


def test_assistant_mode_boundary_keeps_knowledge_read_only_and_business_tools_controlled() -> None:
    knowledge_names = {spec.name for spec in make_toolset(build="universal-v1", mode="knowledge").specs}
    business_names = {spec.name for spec in make_toolset(build="universal-v1", mode="business").specs}

    assert "normalize_structure" not in knowledge_names
    assert "search_knowledge" in knowledge_names
    assert "read_citation" in knowledge_names
    assert "get_prediction_capabilities" not in knowledge_names
    assert "create_prediction" not in knowledge_names
    assert "create_registration_draft" not in knowledge_names
    assert {"normalize_structure", "get_prediction_capabilities", "create_prediction", "get_prediction"}.issubset(business_names)
    assert {"get_model_card", "create_registration_draft", "validate_registration"}.issubset(business_names)


def test_priority_toolset_uses_run_mode_to_hide_persistent_business_tools():
    knowledge_names = {spec.name for spec in priority_toolset(mode="knowledge").specs}
    business_names = {spec.name for spec in priority_toolset(mode="business").specs}

    assert "normalize_structure" not in knowledge_names
    assert "create_prediction" not in knowledge_names
    assert "create_registration_draft" not in knowledge_names
    assert {"get_prediction_capabilities", "get_prediction", "get_data_version_summary"}.issubset(knowledge_names)
    assert {"normalize_structure", "create_prediction", "create_registration_draft"}.issubset(business_names)



