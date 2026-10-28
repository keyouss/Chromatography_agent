from __future__ import annotations

from pathlib import Path
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Lock, get_ident
import inspect
import time

import pytest
from fastapi.testclient import TestClient

from chrom_platform.app import create_app, create_app_from_environment
from chrom_platform.core.idempotency import IdempotencyBeginResult, IdempotencyRecord
from chrom_platform.core.projects import ConfiguredProjectReader
from chrom_platform.structure_core.service import StructureService
from chrom_platform.infrastructure.health import DependencyHealthResult, DependencyStatus


ROOT = Path(__file__).resolve().parents[2]


def test_sync_health_probe_does_not_pass_async_database_url_to_sync_engine(monkeypatch) -> None:
    from chrom_platform.app import _InfrastructureDependencyHealthProbe

    monkeypatch.setenv("DATABASE_URL", "mysql+asyncmy://user:pass@localhost/db")
    monkeypatch.setenv("SYNC_DATABASE_URL", "mysql+pymysql://user:pass@localhost/db")
    monkeypatch.delenv("REDIS_URL", raising=False)
    captured = []

    def fake_database_health(url):
        captured.append(url)
        return DependencyHealthResult("database", DependencyStatus.UNAVAILABLE, datetime.now(timezone.utc), "TEST")

    monkeypatch.setattr("chrom_platform.app.database_health_from_url", fake_database_health)
    _InfrastructureDependencyHealthProbe().check()

    assert captured == ["mysql+pymysql://user:pass@localhost/db"]


def test_sync_health_probe_ignores_async_only_database_url(monkeypatch) -> None:
    from chrom_platform.app import _InfrastructureDependencyHealthProbe

    monkeypatch.delenv("SYNC_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "mysql+asyncmy://user:pass@localhost/db")
    monkeypatch.delenv("REDIS_URL", raising=False)
    captured = []

    def fake_database_health(url):
        captured.append(url)
        return DependencyHealthResult("database", DependencyStatus.UNAVAILABLE, datetime.now(timezone.utc), "TEST")

    monkeypatch.setattr("chrom_platform.app.database_health_from_url", fake_database_health)
    _InfrastructureDependencyHealthProbe().check()

    assert captured == [None]


def test_readiness_does_not_use_environment_presence_as_ready(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "mysql+pymysql://user:pass@localhost/db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    app = create_app(ROOT / "builds" / "minimal-structure-v1.json", contract_generation=True)
    response = TestClient(app).get("/api/v1/health/ready")
    assert response.status_code == 503
    assert response.json()["data"]["core_ready"] is False


def test_environment_factory_requires_explicit_manifest(monkeypatch) -> None:
    monkeypatch.delenv("CHROM_BUILD_MANIFEST", raising=False)
    with pytest.raises(FileNotFoundError):
        create_app_from_environment()


def test_system_capabilities_reports_manifest_export_formats() -> None:
    cases = (
        ("universal-v1.json", ["json", "csv", "xlsx"]),
        ("minimal-structure-v1.json", []),
    )
    for manifest_name, expected_formats in cases:
        app = create_app(ROOT / "builds" / manifest_name, contract_generation=True)
        response = TestClient(app).get("/api/v1/system/capabilities")
        assert response.status_code == 200
        assert response.json()["data"]["export_formats"] == expected_formats


def test_public_settings_route_is_core_in_every_profile_and_llm_settings_are_optional() -> None:
    from fastapi import FastAPI

    from chrom_platform.assembly.manifest import load_manifest
    from chrom_platform.core.dto import ActorContext
    from chrom_platform.core.public_settings import create_router as create_public_settings_router

    for manifest_name in (
        "universal-v1.json", "sfc-v1.json", "cc-v1.json", "minimal-structure-v1.json",
    ):
        manifest = load_manifest(ROOT / "builds" / manifest_name)
        app = FastAPI()
        app.state.manifest = manifest
        app.state.actor_context = ActorContext(
            actor_id="test", project_ids=[], auth_method="service", permissions=[],
            policy_version="test-v1",
        )
        app.include_router(create_public_settings_router(), prefix="/api/v1")
        if "assistant" in manifest.modules:
            from chrom_platform.modules.assistant.settings_router import create_router as create_assistant_settings_router

            app.include_router(create_assistant_settings_router(), prefix="/api/v1")
        client = TestClient(app)
        response = client.get("/api/v1/settings/public")
        assert response.status_code == 200, manifest_name
        data = response.json()["data"]
        assert data["build_id"] == manifest.build_id
        assert data["product_name"] == "Chromatography Platform"
        assert data["built_modules"] == manifest.modules
        assert data["allowed_modes"] == list(manifest.assistant_features or [])
        paths = app.openapi()["paths"]
        assert "/api/v1/settings/public" in paths
        assert ("/api/v1/settings/llm" in paths) == ("assistant" in manifest.modules)

    # The real app registers this common route outside the optional assistant
    # branch; the assistant's M4 router remains the only LLM route owner.
    minimal = create_app(ROOT / "builds" / "minimal-structure-v1.json", contract_generation=True)
    assert "/api/v1/settings/public" in minimal.openapi()["paths"]
    assert "/api/v1/settings/llm" not in minimal.openapi()["paths"]


def test_projects_require_a_reader_and_do_not_use_actor_ids_as_resources(monkeypatch) -> None:
    monkeypatch.delenv("CHROM_PROJECT_IDS", raising=False)
    app = create_app(ROOT / "builds" / "minimal-structure-v1.json", contract_generation=True)
    response = TestClient(app).get("/api/v1/projects")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"


def test_projects_are_read_from_injected_reader(monkeypatch) -> None:
    monkeypatch.setenv("CHROM_PROJECT_IDS", "project-a,project-b")
    app = create_app(ROOT / "builds" / "minimal-structure-v1.json", contract_generation=True)

    class Reader:
        def list_accessible(self, actor, *, limit, after_id):
            from chrom_platform.core.dto import ProjectView

            assert actor.project_ids == ["project-a", "project-b"]
            assert limit == 51
            assert after_id is None
            return [ProjectView(id="project-a", name="Persisted A", status="ARCHIVED")]

    app.state.project_reader = Reader()
    response = TestClient(app).get("/api/v1/projects")
    assert response.status_code == 200
    assert response.json()["data"] == [{"id": "project-a", "name": "Persisted A", "status": "ARCHIVED"}]


def test_projects_cursor_pages_first_middle_last_and_rejects_forgery(monkeypatch) -> None:
    project_ids = [f"project-{i:03d}" for i in range(205)] + ["project-199a"]
    monkeypatch.setenv("CHROM_PROJECT_IDS", ",".join(project_ids))
    app = create_app(ROOT / "builds" / "minimal-structure-v1.json", contract_generation=True)

    class Reader:
        rows = [item for item in project_ids if item != "project-199a"]
        calls = []

        def list_accessible(self, actor, *, limit, after_id):
            from chrom_platform.core.dto import ProjectView

            self.calls.append((limit, after_id))
            assert set(self.rows).issubset(set(actor.project_ids))
            rows = sorted(item for item in self.rows if after_id is None or item > after_id)
            return [ProjectView(id=item, name=item, status="ACTIVE") for item in rows[:limit]]

    app.state.project_reader = Reader()
    client = TestClient(app)
    first = client.get("/api/v1/projects?limit=200")
    assert first.status_code == 200
    assert len(first.json()["data"]) == 200
    assert first.json()["data"][0]["id"] == "project-000"
    assert first.json()["data"][-1]["id"] == "project-199"
    first_page = first.json()["page"]
    assert first_page["has_more"] is True and first_page["next_cursor"]
    assert app.state.project_reader.calls == [(201, None)]

    # An insertion before the continuation point is skipped and a deletion
    # after it is harmless; no item from the first page is repeated.
    app.state.project_reader.rows.insert(200, "project-199a")
    app.state.project_reader.rows.remove("project-200")
    last = client.get("/api/v1/projects?limit=200&cursor=" + first_page["next_cursor"])
    assert [item["id"] for item in last.json()["data"]] == ["project-199a", *[f"project-{i:03d}" for i in range(201, 205)]]
    assert app.state.project_reader.calls[-1] == (201, "project-199")
    assert last.json()["page"]["has_more"] is False
    assert last.json()["page"]["next_cursor"] is None
    forged = client.get("/api/v1/projects?limit=2&cursor=forged.cursor")
    assert forged.status_code == 400


def test_health_routes_run_as_sync_threadpool_endpoints_and_do_not_reprobe_capabilities(monkeypatch) -> None:
    app = create_app(ROOT / "builds" / "minimal-structure-v1.json", contract_generation=True)

    class Probe:
        def __init__(self):
            self.calls = 0
            self.thread_ids = []
            self.lock = Lock()

        def check(self):
            with self.lock:
                self.calls += 1
                self.thread_ids.append(get_ident())
            time.sleep(0.01)
            now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
            return [
                DependencyHealthResult("database", DependencyStatus.READY, now),
                DependencyHealthResult("queue", DependencyStatus.READY, now),
            ]

    probe = Probe()
    app.state.dependency_health_probe = probe
    with TestClient(app) as client:
        response = client.get("/api/v1/health/ready")
        assert response.status_code == 200
        assert probe.calls == 1
        response = client.get("/api/v1/system/capabilities")
        assert response.status_code == 200
        assert probe.calls == 2
    for path in ("/api/v1/system/capabilities", "/api/v1/system/dependencies", "/api/v1/health/ready", "/api/v1/projects"):
        route = next(route for route in app.routes if getattr(route, "path", None) == path)
        assert not inspect.iscoroutinefunction(route.endpoint)


def test_concurrent_health_requests_probe_once_per_request(monkeypatch) -> None:
    app = create_app(ROOT / "builds" / "minimal-structure-v1.json", contract_generation=True)

    class Probe:
        def __init__(self):
            self.calls = 0
            self.lock = Lock()

        def check(self):
            with self.lock:
                self.calls += 1
            return []

    probe = Probe()
    app.state.dependency_health_probe = probe

    def request(_):
        return TestClient(app).get("/api/v1/system/dependencies")

    with ThreadPoolExecutor(max_workers=4) as executor:
        responses = list(executor.map(request, range(4)))
    assert all(response.status_code == 200 for response in responses)
    assert probe.calls == 4


def test_universal_system_requests_do_not_build_assistant_worker_concurrently(monkeypatch) -> None:
    """HTTP binding must not reload the assistant prediction registry per request."""

    monkeypatch.setenv("SYNC_DATABASE_URL", "sqlite://")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("CHROM_PROJECT_IDS", "project-a")
    app = create_app(ROOT / "builds" / "universal-v1.json", contract_generation=True)
    app.state.project_reader = ConfiguredProjectReader(["project-a"])

    class Probe:
        def check(self):
            return []

    app.state.dependency_health_probe = Probe()

    import chrom_platform.assembly.m4 as m4

    calls = []

    def fail_if_registry_built(*_args, **_kwargs):
        calls.append(True)
        raise AssertionError("HTTP request binding must not build the assistant prediction registry")

    monkeypatch.setattr(m4, "build_prediction_worker_registry", fail_if_registry_built)

    paths = [
        "/api/v1/health/live",
        "/api/v1/health/ready",
        "/api/v1/system/access-context",
        "/api/v1/system/capabilities",
    ]

    def request(path):
        return path, client.get(path)

    with TestClient(app) as client:
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=4) as executor:
            responses = list(executor.map(request, paths))
        elapsed = time.perf_counter() - started

    assert not calls
    assert elapsed < 3.0
    assert {path: response.status_code for path, response in responses} == {
        "/api/v1/health/live": 200,
        "/api/v1/health/ready": 503,
        "/api/v1/system/access-context": 200,
        "/api/v1/system/capabilities": 200,
    }


def test_structure_unknown_fields_use_contract_error(monkeypatch) -> None:
    monkeypatch.setenv("CHROM_PROJECT_IDS", "project-a")
    app = create_app(ROOT / "builds" / "minimal-structure-v1.json", contract_generation=True)
    response = TestClient(app).post(
        "/api/v1/structures/normalize",
        headers={"Idempotency-Key": "test-key"},
        json={
            "project_id": "project-a",
            "input_type": "smiles",
            "value": "CCO",
            "preserve_stereochemistry": True,
            "tenantId": "forbidden",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"


def test_missing_idempotency_header_is_400(monkeypatch) -> None:
    monkeypatch.setenv("CHROM_PROJECT_IDS", "project-a")
    app = create_app(ROOT / "builds" / "minimal-structure-v1.json", contract_generation=True)
    response = TestClient(app).post(
        "/api/v1/structures/normalize",
        json={"project_id": "project-a", "input_type": "smiles", "value": "CCO", "preserve_stereochemistry": True},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"


class _StructureRepository:
    def __init__(self):
        self.saved = []
        self.thread_ids = []

    def save(self, structure):
        self.thread_ids.append(get_ident())
        self.saved.append(structure)
        return structure


class _IdempotencyStore:
    def __init__(self):
        self.records = {}
        self.thread_ids = []

    def get(self, scope):
        self.thread_ids.append(get_ident())
        return self.records.get(scope)

    def begin(self, scope, payload_hash):
        self.thread_ids.append(get_ident())
        current = self.records.get(scope)
        if current is not None:
            return IdempotencyBeginResult(current, acquired=False)
        record = IdempotencyRecord(payload_hash=payload_hash, status="IN_PROGRESS")
        self.records[scope] = record
        return IdempotencyBeginResult(record, acquired=True)

    def complete(self, scope, payload_hash, status_code, response):
        self.thread_ids.append(get_ident())
        self.records[scope] = IdempotencyRecord(
            payload_hash=payload_hash, status="COMPLETED", status_code=status_code, response=response
        )

    def fail(self, scope, payload_hash, error_code):
        self.thread_ids.append(get_ident())
        record = self.records[scope]
        assert record.payload_hash == payload_hash
        self.records[scope] = IdempotencyRecord(payload_hash=payload_hash, status="FAILED")


class _Transaction:
    def __init__(self, structures, idempotency):
        self.structures = structures
        self.idempotency = idempotency
        self.thread_id = get_ident()


class _StructureUow:
    def __init__(self):
        self.transaction_count = 0
        self.committed = 0
        self.rolled_back = 0
        self.structures = _StructureRepository()
        self.idempotency = _IdempotencyStore()
        self.last_transaction = None
        self.thread_ids = []

    @contextmanager
    def transaction(self):
        self.thread_ids.append(get_ident())
        self.transaction_count += 1
        records = dict(self.idempotency.records)
        saved = list(self.structures.saved)
        transaction = _Transaction(self.structures, self.idempotency)
        self.last_transaction = transaction
        try:
            yield transaction
        except Exception:
            self.idempotency.records = records
            self.structures.saved = saved
            self.rolled_back += 1
            raise
        else:
            self.committed += 1


def _app_with_fake_writes(monkeypatch):
    monkeypatch.setenv("CHROM_PROJECT_IDS", "project-a")
    app = create_app(ROOT / "builds" / "minimal-structure-v1.json", contract_generation=True)
    uow = _StructureUow()
    app.state.structure_service = StructureService(uow.structures)
    app.state.structure_uow = uow
    return app


def test_idempotency_first_request_replay_conflict_and_in_progress(monkeypatch) -> None:
    app = _app_with_fake_writes(monkeypatch)
    client = TestClient(app)
    body = {"project_id": "project-a", "input_type": "smiles", "value": "CCO", "preserve_stereochemistry": True}
    first = client.post("/api/v1/structures/normalize", headers={"Idempotency-Key": "key-1"}, json=body)
    assert first.status_code == 201
    assert app.state.structure_uow.last_transaction.structures is app.state.structure_uow.structures
    assert app.state.structure_uow.last_transaction.idempotency is app.state.structure_uow.idempotency
    assert len(app.state.structure_uow.structures.saved) == 1
    assert app.state.structure_uow.committed == 1
    first_thread = app.state.structure_uow.last_transaction.thread_id
    assert app.state.structure_uow.thread_ids[0] == first_thread
    assert set(app.state.structure_uow.idempotency.thread_ids[:2]) == {first_thread}
    assert app.state.structure_uow.structures.thread_ids == [first_thread]
    replay = client.post("/api/v1/structures/normalize", headers={"Idempotency-Key": "key-1"}, json=body)
    assert replay.status_code == 201
    assert replay.json()["data"]["id"] == first.json()["data"]["id"]
    assert replay.headers["Idempotency-Replayed"] == "true"
    different = {**body, "value": "CCN"}
    conflict = client.post("/api/v1/structures/normalize", headers={"Idempotency-Key": "key-1"}, json=different)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"

    scope = next(iter(app.state.structure_uow.idempotency.records))
    existing = app.state.structure_uow.idempotency.records[scope]
    app.state.structure_uow.idempotency.records[scope] = IdempotencyRecord(
        payload_hash=existing.payload_hash, status="IN_PROGRESS"
    )
    in_progress = client.post("/api/v1/structures/normalize", headers={"Idempotency-Key": "key-1"}, json=body)
    assert in_progress.status_code == 409
    assert in_progress.json()["error"]["code"] == "OPERATION_IN_PROGRESS"


def test_idempotency_store_missing_is_explicit_503(monkeypatch) -> None:
    app = _app_with_fake_writes(monkeypatch)
    app.state.structure_uow = None
    response = TestClient(app).post(
        "/api/v1/structures/normalize",
        headers={"Idempotency-Key": "key-2"},
        json={"project_id": "project-a", "input_type": "smiles", "value": "CCO", "preserve_stereochemistry": True},
    )
    assert response.status_code == 503


def test_failed_structure_request_does_not_remain_in_progress(monkeypatch) -> None:
    app = _app_with_fake_writes(monkeypatch)

    class FailingService(StructureService):
        def normalize(self, request, structure_id, repository=None):
            raise RuntimeError("database unavailable")

    app.state.structure_service = FailingService(app.state.structure_uow.structures)
    response = TestClient(app).post(
        "/api/v1/structures/normalize",
        headers={"Idempotency-Key": "failed-key"},
        json={"project_id": "project-a", "input_type": "smiles", "value": "CCO", "preserve_stereochemistry": True},
    )
    assert response.status_code == 503
    assert app.state.structure_uow.rolled_back == 1
    assert app.state.structure_uow.committed == 0
    assert app.state.structure_uow.idempotency.records == {}
