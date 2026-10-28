from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chrom_platform.core.dto import ActorContext
from chrom_platform.modules.assistant.persistence import LlmConfigurationActiveRow
from chrom_platform.modules.assistant.priority_router import create_priority_router
from chrom_platform.modules.assistant.settings_service import get_provider_readiness


APPROVED_ENDPOINT = "https://api.siliconflow.cn/v1"
SYNTHETIC_SECRET = "readiness-test-secret"


class _Session:
    def __init__(self, pointer=None, configuration=None):
        self.pointer = pointer
        self.configuration = configuration

    def get(self, model, key):
        assert model is LlmConfigurationActiveRow
        assert key == "project-readiness"
        return self.pointer

    def scalar(self, _statement):
        return self.configuration


class _SecretStore:
    def get(self, reference):
        assert reference == "dpapi:v1:readiness-test"
        return SYNTHETIC_SECRET


def _actor():
    return ActorContext(
        actor_id="readiness-test", project_ids=["project-readiness"],
        auth_method="session", permissions=["settings:read"],
        policy_version="test-v1", default_project_id="project-readiness",
    )


def test_provider_readiness_does_not_use_dotenv_or_call_provider(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "BASE_URL=https://api.siliconflow.cn/v1\nMODEL_ID=legacy-model\nAPI_KEY=legacy-secret\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    app = FastAPI()
    app.state.actor_context = _actor()
    app.include_router(create_priority_router(), prefix="/api/v1")

    class NoActiveConfiguration:
        def get(self, _model, _key):
            return None

    @app.middleware("http")
    async def bind_ports(request, call_next):
        request.state.m4_ports = SimpleNamespace(session=NoActiveConfiguration())
        request.state.request_id = "readiness-test"
        return await call_next(request)

    from chrom_platform.modules.assistant import priority as priority_module

    def no_network(*_args, **_kwargs):
        raise AssertionError("provider-readiness must not make network requests")

    monkeypatch.setattr(priority_module, "secure_provider_request", no_network)
    response = TestClient(app).get("/api/v1/assistant/priority/provider-readiness")

    assert response.status_code == 200
    readiness = response.json()["data"]
    assert readiness["status"] == "BLOCKED"
    assert readiness["blocked_reason"] == "LLM_CONFIGURATION_NOT_ACTIVATED"
    assert readiness["url_reachable"] is None
    assert readiness["model_id_valid"] is False
    assert readiness["key_reference_resolvable"] is False
    assert all(item["status"] == "not_activated" for item in readiness["checks"])
    assert "legacy-secret" not in response.text


def test_active_provider_readiness_uses_exact_policy_and_dpapi_reference(monkeypatch):
    pointer = SimpleNamespace(
        project_id="project-readiness", configuration_id="config-readiness",
        configuration_revision="7",
    )
    configuration = SimpleNamespace(
        id="config-readiness", project_id="project-readiness", revision="7",
        url=APPROVED_ENDPOINT, model_id="test-model", key_reference="dpapi:v1:readiness-test",
        settings={
            "external_send_policy": "approved_endpoint_only",
            "config_hash": "a" * 64,
        },
    )
    session = _Session(pointer, configuration)
    monkeypatch.setenv("LLM_PROVIDER_APPROVED_ENDPOINTS", APPROVED_ENDPOINT)

    result = get_provider_readiness(session, _actor(), secret_store=_SecretStore())

    assert result.status == "READY"
    assert result.url_reachable is None
    assert result.model_id_valid is True
    assert result.key_reference_resolvable is True
    assert result.config_revision == f"sha256:{'a' * 64}"
    assert SYNTHETIC_SECRET not in result.model_dump_json()

    monkeypatch.setenv("LLM_PROVIDER_APPROVED_ENDPOINTS", "https://api.siliconflow.cn/v1/extra")
    rejected = get_provider_readiness(session, _actor(), secret_store=_SecretStore())
    assert rejected.status == "BLOCKED"
    assert rejected.blocked_reason == "LLM_PROVIDER_ENDPOINT_NOT_APPROVED"
