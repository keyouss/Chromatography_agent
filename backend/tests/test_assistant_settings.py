from __future__ import annotations

import os
import re
from pathlib import Path
from types import SimpleNamespace
import tempfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from chrom_platform.core.dto import ActorContext
from chrom_platform.core.errors import APIError
from chrom_platform.infrastructure.models import AuditEvent, IdempotencyRecord, Job, JobAttempt, OutboxEvent, Project
from chrom_platform.infrastructure.repositories import SQLAlchemyIdempotencyStore
from chrom_platform.modules.assistant.persistence import (
    LlmConfigurationActiveRow, LlmConfigurationRow, LlmTestResultRow,
)
from chrom_platform.modules.assistant.schemas import AgentRun
from chrom_platform.modules.assistant.secret_store import SecretStoreError, WindowsDpapiSecretStore, _protect_acl, default_secret_store
from chrom_platform.modules.assistant.security import trusted_provider_url
from chrom_platform.modules.assistant.settings_router import create_router
from chrom_platform.modules.assistant.settings_schemas import LlmConfigurationCreate, LlmSettingsView, LlmTestChecks


SYNTHETIC_KEY = "unit-test-secret-do-not-use"
APPROVED_ENDPOINT = "https://api.siliconflow.cn/v1"


class _TestSecretStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def put(self, value: str) -> str:
        reference = f"dpapi:v1:{len(self.values) + 1:032x}"
        self.values[reference] = value
        return reference

    def get(self, reference: str) -> str:
        if reference not in self.values:
            raise SecretStoreError("missing")
        return self.values[reference]


def _session_factory(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'settings-test.sqlite'}", connect_args={"check_same_thread": False})
    @event.listens_for(engine, "connect")
    def sqlite_mysql_check_compat(dbapi_connection, _connection_record):
        dbapi_connection.create_function("CHAR_LENGTH", 1, lambda value: len(value) if value is not None else None)
        dbapi_connection.create_function("REGEXP", 2, lambda pattern, value: int(bool(re.search(pattern, value or ""))))
    for table in (Project.__table__, AuditEvent.__table__, Job.__table__, JobAttempt.__table__, OutboxEvent.__table__, IdempotencyRecord.__table__):
        table.create(engine, checkfirst=True)
    for table in (LlmConfigurationRow.__table__, LlmConfigurationActiveRow.__table__, LlmTestResultRow.__table__):
        table.create(engine, checkfirst=True)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as session:
        session.add(Project(id="project-settings", slug="settings-test", name="settings test", status="ACTIVE", revision=1))
        session.commit()
    return engine, factory


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    engine, factory = _session_factory(tmp_path)
    app = FastAPI()
    actor = ActorContext(
        actor_id="human-test", project_ids=["project-settings"],
        auth_method="session", permissions=["settings:read", "settings:write"],
        policy_version="test-v1", default_project_id="project-settings",
    )
    app.state.actor_context = actor
    app.state.manifest = SimpleNamespace(build_id="test-build", modules=("assistant",), assistant_features=("business",))
    app.include_router(create_router(secret_store=_TestSecretStore()), prefix="/api/v1")

    @app.exception_handler(APIError)
    async def api_error_handler(request, exc):
        return JSONResponse(status_code=exc.status_code, content={
            "error": exc.payload.model_dump(mode="json"), "request_id": getattr(request.state, "request_id", "test"),
        })

    @app.middleware("http")
    async def bind_session(request, call_next):
        session = factory()
        request.state.request_id = "settings-test-request"
        request.state.m4_ports = SimpleNamespace(session=session)
        request.state.idempotency_store = SQLAlchemyIdempotencyStore(session)
        try:
            response = await call_next(request)
            if response.status_code < 400:
                session.commit()
            else:
                session.rollback()
            return response
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    monkeypatch.setenv("LLM_PROVIDER_APPROVED_ENDPOINTS", APPROVED_ENDPOINT)
    return TestClient(app), factory, engine


def _create_body(**overrides):
    value = {
        "name": "test provider", "api_format": "openai_chat", "base_url": APPROVED_ENDPOINT,
        "model_id": "synthetic-model", "api_key": SYNTHETIC_KEY,
        "timeout_seconds": 15, "max_retries": 0, "max_output_tokens": 32,
        "external_send_policy": "approved_endpoint_only",
    }
    value.update(overrides)
    return value


def test_settings_dtos_are_strict_and_secret_is_not_serialized():
    with pytest.raises(ValueError):
        LlmConfigurationCreate.model_validate({**_create_body(), "unexpected": True})
    with pytest.raises(ValueError):
        LlmConfigurationCreate.model_validate(_create_body(api_key=""))
    parsed = LlmConfigurationCreate.model_validate(_create_body())
    assert SYNTHETIC_KEY not in str(parsed.model_dump(mode="json"))
    checks = LlmTestChecks(connectivity="PASSED", streaming="NOT_SUPPORTED", tool_calling="FAILED", structured_output="NOT_SUPPORTED")
    assert set(checks.model_dump()) == {"connectivity", "streaming", "tool_calling", "structured_output"}
    settings = LlmSettingsView(active_revision=0, candidate_configurations=[])
    assert set(settings.model_dump()) == {"active_configuration", "active_revision", "candidate_configurations"}


def test_external_send_policy_requires_an_exact_approved_endpoint():
    assert trusted_provider_url(
        APPROVED_ENDPOINT + "/", external_send_policy="approved_endpoint_only",
        approved_endpoints=APPROVED_ENDPOINT,
    ) == APPROVED_ENDPOINT
    for rejected in (
        "https://api.siliconflow.cn:444/v1",
        "https://api.siliconflow.cn/v1/extra",
        "https://api.siliconflow.cn/v1?redirect=https://elsewhere.invalid",
    ):
        with pytest.raises(ValueError):
            trusted_provider_url(rejected, external_send_policy="approved_endpoint_only", approved_endpoints=APPROVED_ENDPOINT)
    for policy, approved in (("deny_external", APPROVED_ENDPOINT), ("approved_endpoint_only", None)):
        with pytest.raises(ValueError):
            trusted_provider_url(APPROVED_ENDPOINT, external_send_policy=policy, approved_endpoints=approved)


def test_settings_openapi_declares_unique_required_precondition_headers():
    app = FastAPI()
    app.include_router(create_router(), prefix="/api/v1")
    schema = app.openapi()
    for path, required in (
        ("/api/v1/settings/llm/configurations", {"Idempotency-Key"}),
        ("/api/v1/settings/llm/configurations/{configuration_id}/test", {"Idempotency-Key"}),
        ("/api/v1/settings/llm/configurations/{configuration_id}/activate", {"Idempotency-Key", "If-Match"}),
    ):
        operation = schema["paths"][path]["post"]
        headers = [item for item in operation["parameters"] if item["in"] == "header"]
        assert len(headers) == len(required)
        assert {item["name"] for item in headers} == required
        assert all(item["required"] for item in headers)
    accepted_headers = schema["paths"]["/api/v1/settings/llm/configurations/{configuration_id}/test"]["post"]["responses"]["202"]["headers"]
    assert "Location" in accepted_headers

    operations = (
        ("/api/v1/settings/llm", "get", "200", {"ETag"}),
        ("/api/v1/settings/llm/configurations", "post", "201", {"ETag", "Location"}),
        ("/api/v1/settings/llm/configurations/{configuration_id}/activate", "post", "200", {"ETag"}),
    )
    for path, method, code, expected in operations:
        headers = schema["paths"][path][method]["responses"][code]["headers"]
        assert set(headers) == expected
        assert all(headers[name]["required"] for name in expected)
        assert all(headers[name]["schema"]["type"] == "string" for name in expected)
    created_headers = schema["paths"]["/api/v1/settings/llm/configurations"]["post"]["responses"]["201"]["headers"]
    assert created_headers["Location"]["schema"]["format"] == "uri-reference"


def test_create_and_test_are_durable_idempotent_and_redacted(tmp_path, monkeypatch):
    client, factory, engine = _client(tmp_path, monkeypatch)
    try:
        initial_settings = client.get("/api/v1/settings/llm")
        assert initial_settings.status_code == 200
        assert initial_settings.headers["ETag"] == '"active-0"'
        assert set(initial_settings.json()["data"]) == {"active_configuration", "active_revision", "candidate_configurations"}
        created = client.post("/api/v1/settings/llm/configurations", json=_create_body(), headers={"Idempotency-Key": "create-1"})
        assert created.status_code == 201
        config = created.json()["data"]
        assert created.headers["ETag"] == config["etag"]
        assert created.headers["Location"] == "/api/v1/settings/llm"
        assert config["has_api_key"] is True
        assert SYNTHETIC_KEY not in created.text

        replay = client.post("/api/v1/settings/llm/configurations", json=_create_body(), headers={"Idempotency-Key": "create-1"})
        assert replay.status_code == 201
        assert replay.headers["Idempotency-Replayed"] == "true"
        assert replay.json()["data"]["id"] == config["id"]
        conflict = client.post("/api/v1/settings/llm/configurations", json=_create_body(model_id="other-model"), headers={"Idempotency-Key": "create-1"})
        assert conflict.status_code == 409

        accepted_response = client.post(
            f"/api/v1/settings/llm/configurations/{config['id']}/test", json={},
            headers={"Idempotency-Key": "test-1"},
        )
        assert accepted_response.status_code == 202
        accepted = accepted_response.json()["data"]
        assert set(accepted) == {"id", "job_id", "status", "resource_ref"}
        assert accepted_response.headers["Location"].endswith(accepted["id"])
        replay_test = client.post(
            f"/api/v1/settings/llm/configurations/{config['id']}/test", json={},
            headers={"Idempotency-Key": "test-1"},
        )
        assert replay_test.headers["Idempotency-Replayed"] == "true"
        assert replay_test.json()["data"]["id"] == accepted["id"]

        with factory() as session:
            assert session.get(Job, accepted["job_id"]).status == "QUEUED"
            assert session.scalar(select(JobAttempt).where(JobAttempt.job_id == accepted["job_id"])) is not None
            outbox = session.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_id == accepted["job_id"]))
            assert outbox.payload == {"job_id": accepted["job_id"], "attempt": 1, "dispatch_id": outbox.dispatch_id}
            assert session.get(LlmTestResultRow, accepted["id"]).status == "QUEUED"
            assert session.get(LlmConfigurationRow, config["id"]).key_reference.startswith("dpapi:v1:")
            persisted = " ".join(str(row.after_summary) for row in session.scalars(select(AuditEvent)))
            persisted += " ".join(str(row.result_resource_ref) for row in session.scalars(select(IdempotencyRecord)))
            assert SYNTHETIC_KEY not in persisted

        result = client.get(f"/api/v1/settings/llm/tests/{accepted['id']}")
        assert result.status_code == 200
        assert result.json()["data"]["status"] == "QUEUED"
        assert set(result.json()["data"]["checks"]) == {"connectivity", "streaming", "tool_calling", "structured_output"}
        assert set(result.json()["data"]["checks"].values()) == {"NOT_SUPPORTED"}
    finally:
        engine.dispose()


def test_activation_cas_and_etag_are_bound_to_active_pointer(tmp_path, monkeypatch):
    client, factory, engine = _client(tmp_path, monkeypatch)
    try:
        created = client.post("/api/v1/settings/llm/configurations", json=_create_body(), headers={"Idempotency-Key": "create-activate"})
        config = created.json()["data"]
        accepted = client.post(
            f"/api/v1/settings/llm/configurations/{config['id']}/test", json={},
            headers={"Idempotency-Key": "test-activate"},
        ).json()["data"]
        with factory() as session:
            row = session.get(LlmConfigurationRow, config["id"])
            row.status = "TESTED"
            test_row = session.get(LlmTestResultRow, accepted["id"])
            test_row.status = "SUCCEEDED"
            test_row.configuration_hash = row.settings["config_hash"]
            test_row.checks = {"connectivity": "PASSED", "streaming": "PASSED", "tool_calling": "PASSED", "structured_output": "NOT_SUPPORTED"}
            test_row.finished_at = row.created_at
            session.commit()
        activated = client.post(
            f"/api/v1/settings/llm/configurations/{config['id']}/activate",
            json={"test_id": accepted["id"], "expected_active_revision": 0},
            headers={"Idempotency-Key": "activate-1", "If-Match": '"active-0"'},
        )
        assert activated.status_code == 200
        assert activated.headers["ETag"] == '"active-1"'
        assert activated.json()["data"]["active_revision"] == 1
        assert activated.json()["data"]["active_configuration"]["id"] == config["id"]
        current = client.get("/api/v1/settings/llm")
        assert current.headers["ETag"] == '"active-1"'

        # This second contender carries the same stale snapshot as a request
        # that raced the winner; the compare-and-swap rejects it.
        loser = client.post(
            f"/api/v1/settings/llm/configurations/{config['id']}/activate",
            json={"test_id": accepted["id"], "expected_active_revision": 0},
            headers={"Idempotency-Key": "activate-stale", "If-Match": '"active-0"'},
        )
        assert loser.status_code == 409
        with factory() as session:
            pointer = session.get(LlmConfigurationActiveRow, "project-settings")
            assert pointer.configuration_id == config["id"]
            assert pointer.revision == 1
    finally:
        engine.dispose()


def test_bad_secret_reference_fails_closed_and_dpapi_round_trips_same_identity(tmp_path):
    if os.name != "nt":
        pytest.skip("DPAPI CurrentUser validation runs only on Windows")
    runtime_base = Path(os.environ["LOCALAPPDATA"]) / "ChromatographyPlatform"
    runtime_base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="assistant-secret-test-", dir=runtime_base) as runtime_dir:
        protected_runtime = Path(runtime_dir)
        _protect_acl(protected_runtime, directory=True)
        store = WindowsDpapiSecretStore(protected_runtime / "assistant-llm-secrets")
        with pytest.raises(SecretStoreError):
            store.get("dpapi:v1:malformed")
        reference = store.put("synthetic-dpapi-test-value")
        assert store.get(reference) == "synthetic-dpapi-test-value"
        target = protected_runtime / "assistant-llm-secrets" / f"{reference.removeprefix(store.PREFIX)}.dpapi"
        target.write_bytes(b"invalid protected payload")
        with pytest.raises(SecretStoreError):
            store.get(reference)


def test_secret_root_resolver_rejects_missing_relative_and_escaping_paths(tmp_path, monkeypatch):
    with pytest.raises(SecretStoreError):
        WindowsDpapiSecretStore(Path("relative") / "assistant-llm-secrets")
    if os.name != "nt":
        assert default_secret_store() is None
        return
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("CHROM_ASSISTANT_SECRET_ROOT", raising=False)
    assert default_secret_store() is None
    monkeypatch.setenv("CHROM_ASSISTANT_SECRET_ROOT", "relative\\assistant-llm-secrets")
    assert default_secret_store() is None
    monkeypatch.setenv("CHROM_ASSISTANT_SECRET_ROOT", str(tmp_path / "outside" / "assistant-llm-secrets"))
    assert default_secret_store() is None
    approved = tmp_path / "ChromatographyPlatform" / "isolated-runtime" / "assistant-llm-secrets"
    monkeypatch.setenv("CHROM_ASSISTANT_SECRET_ROOT", str(approved))
    assert isinstance(default_secret_store(), WindowsDpapiSecretStore)


def test_provider_configuration_hash_field_is_optional_for_history():
    from datetime import datetime, timezone

    value = AgentRun(
        id="run-1", conversation_id="conv-1", project_id="project-1", input_message_id="msg-1",
        mode="business", status="QUEUED", attempt=1, model_id="model",
        prompt_version="1", toolset_version="1", policy_version="1",
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc), revision=1, etag='"1"',
    )
    assert value.provider_configuration_hash is None
