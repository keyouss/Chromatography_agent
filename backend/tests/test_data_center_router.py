from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from chrom_platform.core.dto import ActorContext
from chrom_platform.core.errors import APIError
from chrom_platform.core.idempotency import BeginResult, IdempotencyRecord
from chrom_platform.modules.data_center.router import create_router
from chrom_platform.modules.data_center.schemas import (
    DataDomainSchema,
    DataImportValidation,
    DomainField,
    ValidationCounts,
)
from chrom_platform.modules.data_center.service import DataImportService, StaticDataDomainRegistry
from chrom_platform.modules.data_center.testing import InMemoryDataRepository


class Store:
    def __init__(self) -> None:
        self.rows = {}

    def get(self, scope):
        return self.rows.get(scope)

    def begin(self, scope, payload_hash):
        if scope in self.rows:
            return BeginResult(self.rows[scope], False)
        self.rows[scope] = IdempotencyRecord(payload_hash=payload_hash, status="IN_PROGRESS")
        return BeginResult(self.rows[scope], True)

    def complete(self, scope, payload_hash, status_code, response):
        self.rows[scope] = IdempotencyRecord(payload_hash, "COMPLETED", status_code, response)

    def fail(self, scope, payload_hash, error_code):
        self.rows[scope] = IdempotencyRecord(payload_hash, "FAILED")


class Parser:
    parser_revision = "parser-1"
    parser_hash = "a" * 64

    def validate(self, data_import, domain):
        return DataImportValidation(
            id="div-1", import_id=data_import.id, revision=data_import.revision,
            config_hash=data_import.config_hash, status="VALID",
            counts=ValidationCounts(new=1, updated=0, unchanged=0, invalid=0, duplicate=0),
            parser_revision=self.parser_revision, parser_hash=self.parser_hash,
            created_at=datetime.now(timezone.utc),
        )


class AtomicCommit:
    def __init__(self, repository):
        self.repository = repository

    def commit(self, request, confirmation):
        version = self.repository.allocate_and_save_new_data_version(
            __import__("chrom_platform.modules.data_center.schemas", fromlist=["DataVersionDraft"]).DataVersionDraft(
                project_id=request.data_import.project_id, domain_id=request.data_import.domain_id,
                source_import_id=request.data_import.id, snapshot_artifact_id="snapshot-1",
                schema_version=request.data_import.schema_version, sha256="b" * 64, record_count=1,
            )
        )
        self.repository.save_import(request.data_import.model_copy(update={
            "status": "COMMITTED", "new_data_version_id": version.id,
        }))
        return version


def _domain() -> DataDomainSchema:
    return DataDomainSchema(
        id="sfc-experiment", version="1.0", chromatography_type="sfc",
        record_schema={"sample_id": DomainField(type="string")}, unit_definitions={},
        identity_fields=["sample_id"], deduplication_policy="reject", supported_import_modes=["append"],
    )


def _client(*, parser=None, atomic=None, service=None):
    app = FastAPI()
    app.state.actor_context = ActorContext(
        actor_id="actor-1", tenant_id=None, project_ids=["project-a"],
        auth_method="deployment_default", permissions=["*"], policy_version="test",
    )
    app.state.idempotency_store = Store()
    app.add_exception_handler(APIError, lambda request, exc: JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.payload.model_dump(mode="json"), "request_id": "req-test"},
    ))
    domain = _domain()
    repository = InMemoryDataRepository()
    service = service or DataImportService(
        repository, StaticDataDomainRegistry([domain]), validation_port=parser,
        atomic_commit_port=atomic,
    )
    app.include_router(
        create_router(service=service, domain_registry=StaticDataDomainRegistry([domain]), idempotency_store=app.state.idempotency_store),
        prefix="/api/v1",
    )
    return TestClient(app), repository, service


def _body() -> dict:
    return {
        "project_id": "project-a", "domain_id": "sfc-experiment", "artifact_id": "artifact-1",
        "schema_version": "1.0", "import_mode": "append", "field_mapping": {"sample_id": "sample"},
        "source_description": "fixture",
    }


def test_router_missing_dependency_returns_503_and_factory_is_unregistered():
    app = FastAPI()
    app.state.actor_context = ActorContext(
        actor_id="actor-1", tenant_id=None, project_ids=["project-a"],
        auth_method="deployment_default", permissions=["*"], policy_version="test",
    )
    app.add_exception_handler(APIError, lambda request, exc: JSONResponse(status_code=exc.status_code, content={"error": exc.payload.model_dump(mode="json")}))
    app.include_router(create_router(), prefix="/api/v1")
    client = TestClient(app)
    assert client.get("/api/v1/data-domains").status_code == 503
    assert client.get("/api/v1/data-versions?project_id=project-a&domain_id=sfc-experiment").status_code == 503


def test_import_rejects_unknown_fields_and_requires_idempotency_key():
    client = _client()[0]
    assert client.post("/api/v1/data-imports", json=_body()).status_code == 400
    assert client.post("/api/v1/data-imports", headers={"Idempotency-Key": ""}, json=_body()).status_code == 422
    assert client.post("/api/v1/data-imports", headers={"Idempotency-Key": "x" * 129}, json=_body()).status_code == 422
    response = client.post("/api/v1/data-imports", headers={"Idempotency-Key": "create-1"}, json={**_body(), "unknown": True})
    assert response.status_code == 422


def test_import_create_replay_etag_patch_if_match_and_validate_location():
    client, repository, service = _client(parser=Parser())
    first = client.post("/api/v1/data-imports", headers={"Idempotency-Key": "create-1"}, json=_body())
    assert first.status_code == 201
    assert first.headers["ETag"] == first.json()["data"]["etag"]
    replay = client.post("/api/v1/data-imports", headers={"Idempotency-Key": "create-1"}, json=_body())
    assert replay.status_code == 201
    assert replay.headers["Idempotency-Replayed"] == "true"
    import_id = first.json()["data"]["id"]
    stale = client.patch(
        f"/api/v1/data-imports/{import_id}",
        headers={"If-Match": '"stale"', "Idempotency-Key": "patch-1"},
        json={"source_description": "changed"},
    )
    assert stale.status_code == 412
    current_etag = first.headers["ETag"]
    patched = client.patch(
        f"/api/v1/data-imports/{import_id}",
        headers={"If-Match": current_etag, "Idempotency-Key": "patch-2"},
        json={"source_description": "changed"},
    )
    assert patched.status_code == 200
    valid = client.post(
        f"/api/v1/data-imports/{import_id}/validate",
        headers={"If-Match": patched.headers["ETag"], "Idempotency-Key": "validate-1"},
        json={},
    )
    assert valid.status_code == 202
    assert valid.headers["Location"] == f"/api/v1/data-imports/{import_id}/validation"


def test_commit_is_accepted_with_location_and_missing_parser_or_commit_is_503():
    client, repository, service = _client(parser=None)
    created = client.post("/api/v1/data-imports", headers={"Idempotency-Key": "create-1"}, json=_body()).json()["data"]
    unavailable = client.post(
        f"/api/v1/data-imports/{created['id']}/validate",
        headers={"If-Match": created["etag"], "Idempotency-Key": "validate-1"}, json={},
    )
    assert unavailable.status_code == 503

    parser = Parser()
    repository = InMemoryDataRepository()
    service = DataImportService(
        repository, StaticDataDomainRegistry([_domain()]), validation_port=parser,
        atomic_commit_port=AtomicCommit(repository),
    )
    client, _, _ = _client(parser=parser, atomic=AtomicCommit(repository), service=service)
    created = client.post("/api/v1/data-imports", headers={"Idempotency-Key": "create-2"}, json=_body()).json()["data"]
    checked = client.post(
        f"/api/v1/data-imports/{created['id']}/validate",
        headers={"If-Match": created["etag"], "Idempotency-Key": "validate-2"}, json={},
    ).json()["data"]
    accepted = client.post(
        f"/api/v1/data-imports/{created['id']}/commit",
        headers={"If-Match": created["etag"], "Idempotency-Key": "commit-1"},
        json={"confirmation_id": "confirmation-1"},
    )
    assert accepted.status_code == 202
    assert accepted.headers["Location"].startswith("/api/v1/data-versions/")


def test_validate_rejects_empty_or_overlong_concurrency_headers_before_dependencies():
    client, _, _ = _client(parser=None)
    created = client.post("/api/v1/data-imports", headers={"Idempotency-Key": "create-headers"}, json=_body()).json()["data"]
    endpoint = f"/api/v1/data-imports/{created['id']}/validate"
    assert client.post(endpoint, headers={"Idempotency-Key": "validate-empty", "If-Match": ""}, json={}).status_code == 422
    assert client.post(endpoint, headers={"Idempotency-Key": "validate-long", "If-Match": "x" * 257}, json={}).status_code == 422
    assert client.post(endpoint, headers={"Idempotency-Key": "", "If-Match": created["etag"]}, json={}).status_code == 422
    assert client.post(endpoint, headers={"Idempotency-Key": "x" * 129, "If-Match": created["etag"]}, json={}).status_code == 422
