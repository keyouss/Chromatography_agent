from __future__ import annotations

import asyncio
from pathlib import Path
from contextlib import contextmanager
from copy import deepcopy
from threading import get_ident

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import pytest

from chrom_platform.artifact_core.router import _read_bounded_content, create_artifact_router
from chrom_platform.artifact_core.service import ArtifactService
from chrom_platform.artifact_core.service import UploadContentAdmission
from chrom_platform.artifact_core.schemas import UploadCreate
from chrom_platform.artifact_core.storage import LocalArtifactStore
from chrom_platform.core.dto import ActorContext
from chrom_platform.core.errors import APIError
from chrom_platform.core.idempotency import BeginResult, IdempotencyRecord

from test_artifact_service import Audit, Confirmation, JobSubmission, Queue, Repository, Scanner, actor


class StreamRequest:
    def __init__(self, chunks, headers=None):
        self.chunks = list(chunks)
        self.headers = headers or {}
        self.read_count = 0

    async def stream(self):
        for chunk in self.chunks:
            self.read_count += 1
            yield chunk


class Idempotency:
    def __init__(self):
        self.records = {}

    def get(self, scope):
        return self.records.get(scope)

    def begin(self, scope, payload_hash):
        current = self.records.get(scope)
        if current is not None:
            return BeginResult(current, acquired=False)
        record = IdempotencyRecord(payload_hash=payload_hash, status="IN_PROGRESS")
        self.records[scope] = record
        return BeginResult(record, acquired=True)

    def complete(self, scope, payload_hash, status_code, response):
        self.records[scope] = IdempotencyRecord(payload_hash=payload_hash, status="COMPLETED", status_code=status_code, response=response)

    def fail(self, scope, payload_hash, error_code):
        assert error_code in {"VALIDATION_FAILED", "DEPENDENCY_UNAVAILABLE", "STATE_CONFLICT", "OPERATION_TIMEOUT"}
        self.records[scope] = IdempotencyRecord(payload_hash=payload_hash, status="FAILED")


class UnitOfWork:
    def __init__(self, repository, idempotency, jobs=None):
        self.artifacts = repository
        self.idempotency = idempotency
        self.jobs = jobs
        self.commits = 0
        self.rollbacks = 0
        self.thread_ids = []

    def __enter__(self):
        self.thread_ids.append(get_ident())
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is not None:
            self.rollback()

    def commit(self):
        self.thread_ids.append(get_ident())
        self.commits += 1

    def rollback(self):
        self.thread_ids.append(get_ident())
        self.rollbacks += 1

    @contextmanager
    def business_savepoint(self):
        self.thread_ids.append(get_ident())
        snapshot = deepcopy(self.artifacts.rows)
        try:
            yield
        except Exception:
            self.artifacts.rows = snapshot
            raise


class UnitOfWorkFactory:
    def __init__(self, repository, idempotency, jobs=None):
        self.repository = repository
        self.idempotency = idempotency
        self.jobs = jobs
        self.instances = []

    def __call__(self):
        unit = UnitOfWork(self.repository, self.idempotency, self.jobs)
        self.instances.append(unit)
        return unit


def test_upload_router_requires_idempotency_and_returns_controlled_contract(tmp_path: Path) -> None:
    app = FastAPI()
    repository = Repository()
    store = Idempotency()
    service = ArtifactService(repository, LocalArtifactStore(tmp_path), scanner=Scanner())
    app.state.actor_context = actor("project-a")
    app.state.idempotency_store = store
    app.add_exception_handler(APIError, lambda request, exc: __import__("fastapi").responses.JSONResponse(status_code=exc.status_code, content={"error": exc.payload.model_dump(mode="json"), "request_id": "req-test"}))
    app.include_router(create_artifact_router(service, unit_of_work=UnitOfWorkFactory(repository, store, Queue())), prefix="/api/v1")
    client = TestClient(app)
    body = {"project_id": "project-a", "kind": "batch_input", "file_name": "a.csv", "media_type": "text/csv", "size_bytes": 3}
    missing = client.post("/api/v1/artifacts/uploads", json=body)
    assert missing.status_code == 400
    assert missing.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"
    assert client.post("/api/v1/artifacts/uploads", headers={"Idempotency-Key": ""}, json=body).status_code == 422
    assert client.post("/api/v1/artifacts/uploads", headers={"Idempotency-Key": "x" * 129}, json=body).status_code == 422
    response = client.post("/api/v1/artifacts/uploads", headers={"Idempotency-Key": "u-1"}, json=body)
    assert response.status_code == 201
    assert "storage_key" not in response.json()["data"]
    replay = client.post("/api/v1/artifacts/uploads", headers={"Idempotency-Key": "u-1"}, json=body)
    assert replay.status_code == 201
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert replay.json()["data"]["id"] == response.json()["data"]["id"]
    conflict = client.post("/api/v1/artifacts/uploads", headers={"Idempotency-Key": "u-1"}, json={**body, "file_name": "other.csv"})
    assert conflict.status_code == 409
    upload_id = response.json()["data"]["id"]
    received = client.put(f"/api/v1/artifacts/uploads/{upload_id}/content", content=b"abc")
    assert received.status_code == 200
    completed = client.post(f"/api/v1/artifacts/uploads/{upload_id}/complete", headers={"Idempotency-Key": "u-2"}, json={})
    assert completed.status_code == 202
    assert completed.json()["data"]["job_id"] == "job-validation-1"
    assert completed.json()["data"]["resource_ref"] == {"type": "artifact", "id": response.json()["data"]["artifact_id"]}
    assert completed.headers["Location"] == f"/api/v1/artifacts/{response.json()['data']['artifact_id']}"
    complete_replay = client.post(f"/api/v1/artifacts/uploads/{upload_id}/complete", headers={"Idempotency-Key": "u-2"}, json={})
    assert complete_replay.status_code == 202
    assert complete_replay.headers["Idempotency-Replayed"] == "true"
    assert complete_replay.headers["Location"] == f"/api/v1/artifacts/{response.json()['data']['artifact_id']}"
    complete_header = app.openapi()["paths"]["/api/v1/artifacts/uploads/{upload_id}/complete"]["post"]["responses"]["202"]["headers"]["Location"]
    assert complete_header["required"] is True
    assert complete_header["schema"] == {"type": "string", "format": "uri-reference"}
    unknown = client.post(f"/api/v1/artifacts/uploads/{upload_id}/complete", headers={"Idempotency-Key": "u-3"}, json={"unexpected": True})
    assert unknown.status_code == 422


def test_create_replay_checks_current_authorization_and_failure_maps_public_code(tmp_path: Path) -> None:
    app = FastAPI()
    store = Idempotency()
    repository = Repository()
    app.state.actor_context = actor("project-a")
    app.state.idempotency_store = store
    app.add_exception_handler(APIError, lambda request, exc: __import__("fastapi").responses.JSONResponse(status_code=exc.status_code, content={"error": exc.payload.model_dump(mode="json"), "request_id": "req-test"}))
    app.include_router(create_artifact_router(ArtifactService(repository, LocalArtifactStore(tmp_path), scanner=Scanner()), unit_of_work=UnitOfWorkFactory(repository, store)), prefix="/api/v1")
    client = TestClient(app)
    body = {"project_id": "project-a", "kind": "batch_input", "file_name": "a.csv", "media_type": "text/csv", "size_bytes": 3}
    first = client.post("/api/v1/artifacts/uploads", headers={"Idempotency-Key": "auth-1"}, json=body)
    assert first.status_code == 201
    app.state.actor_context = ActorContext(actor_id="actor-1", tenant_id=None, project_ids=["project-a"], auth_method="deployment_default", permissions=["artifact:read"], policy_version="test")
    revoked = client.post("/api/v1/artifacts/uploads", headers={"Idempotency-Key": "auth-1"}, json=body)
    assert revoked.status_code == 403
    app.state.actor_context = actor("project-a")
    invalid = client.post("/api/v1/artifacts/uploads", headers={"Idempotency-Key": "invalid-1"}, json={**body, "media_type": "application/x-unknown"})
    assert invalid.status_code == 422
    failed = next(record for scope, record in store.records.items() if scope.key == "invalid-1")
    assert failed.status == "FAILED"


def test_required_idempotency_header_is_openapi_required_but_missing_is_400(tmp_path: Path) -> None:
    app = FastAPI()
    app.state.actor_context = actor("project-a")
    store = Idempotency()
    repository = Repository()
    app.state.idempotency_store = store
    app.add_exception_handler(APIError, lambda request, exc: __import__("fastapi").responses.JSONResponse(status_code=exc.status_code, content={"error": exc.payload.model_dump(mode="json"), "request_id": "req-test"}))
    app.include_router(create_artifact_router(ArtifactService(repository, LocalArtifactStore(tmp_path), scanner=Scanner()), unit_of_work=UnitOfWorkFactory(repository, store)), prefix="/api/v1")
    parameter = next(item for item in app.openapi()["paths"]["/api/v1/artifacts/uploads"]["post"]["parameters"] if item["name"] == "Idempotency-Key")
    assert parameter["required"] is True
    assert parameter["schema"]["minLength"] == 1
    assert parameter["schema"]["maxLength"] == 128
    response = TestClient(app).post("/api/v1/artifacts/uploads", json={"project_id": "project-a", "kind": "batch_input", "file_name": "a.csv", "media_type": "text/csv", "size_bytes": 3})
    assert response.status_code == 400


def test_binary_upload_and_download_are_declared_and_served_as_bytes(tmp_path: Path) -> None:
    app = FastAPI()
    repository = Repository()
    store = Idempotency()
    queue = Queue()
    app.state.actor_context = actor("project-a")
    app.state.idempotency_store = store
    app.add_exception_handler(APIError, lambda request, exc: __import__("fastapi").responses.JSONResponse(status_code=exc.status_code, content={"error": exc.payload.model_dump(mode="json"), "request_id": "req-test"}))
    app.include_router(create_artifact_router(ArtifactService(repository, LocalArtifactStore(tmp_path), scanner=Scanner()), unit_of_work=UnitOfWorkFactory(repository, store, queue)), prefix="/api/v1")
    openapi = app.openapi()
    upload_body = openapi["paths"]["/api/v1/artifacts/uploads/{upload_id}/content"]["put"]["requestBody"]
    assert upload_body["content"]["application/octet-stream"]["schema"] == {"type": "string", "format": "binary"}
    download_response = openapi["paths"]["/api/v1/artifacts/{artifact_id}/download"]["get"]["responses"]["200"]
    assert download_response["content"]["application/octet-stream"]["schema"] == {"type": "string", "format": "binary"}
    assert "application/json" not in download_response.get("content", {})

    client = TestClient(app)
    body = {"project_id": "project-a", "kind": "batch_input", "file_name": "a.csv", "media_type": "text/csv", "size_bytes": 3}
    created = client.post("/api/v1/artifacts/uploads", headers={"Idempotency-Key": "binary-1"}, json=body)
    upload_id = created.json()["data"]["id"]
    artifact_id = created.json()["data"]["artifact_id"]
    assert client.put(f"/api/v1/artifacts/uploads/{upload_id}/content", content=b"abc").status_code == 200
    accepted = client.post(f"/api/v1/artifacts/uploads/{upload_id}/complete", headers={"Idempotency-Key": "binary-2"}, json={})
    assert accepted.status_code == 202
    repository.rows[artifact_id].status = "AVAILABLE"
    repository.rows[artifact_id].scan_status = "PASSED"
    downloaded = client.get(f"/api/v1/artifacts/{artifact_id}/download")
    assert downloaded.status_code == 200
    assert downloaded.content == b"abc"
    assert downloaded.headers["content-type"] == "application/octet-stream"


def test_delete_location_header_is_declared_and_returned(tmp_path: Path) -> None:
    app = FastAPI()
    repository = Repository()
    store = Idempotency()
    queue = Queue()
    app.state.actor_context = actor("project-a")
    app.state.idempotency_store = store
    app.add_exception_handler(APIError, lambda request, exc: __import__("fastapi").responses.JSONResponse(status_code=exc.status_code, content={"error": exc.payload.model_dump(mode="json"), "request_id": "req-test"}))
    app.include_router(
        create_artifact_router(
            ArtifactService(repository, LocalArtifactStore(tmp_path), scanner=Scanner(), confirmation=Confirmation(), audit=Audit(), deletion_queue=queue),
            unit_of_work=UnitOfWorkFactory(repository, store, queue),
        ),
        prefix="/api/v1",
    )
    delete_header = app.openapi()["paths"]["/api/v1/artifacts/{artifact_id}"]["delete"]["responses"]["202"]["headers"]["Location"]
    assert delete_header["required"] is True
    assert delete_header["schema"] == {"type": "string", "format": "uri-reference"}
    client = TestClient(app)
    body = {"project_id": "project-a", "kind": "batch_input", "file_name": "a.csv", "media_type": "text/csv", "size_bytes": 3}
    created = client.post("/api/v1/artifacts/uploads", headers={"Idempotency-Key": "delete-create"}, json=body)
    artifact_id = created.json()["data"]["artifact_id"]
    upload_id = created.json()["data"]["id"]
    assert client.put(f"/api/v1/artifacts/uploads/{upload_id}/content", content=b"abc").status_code == 200
    repository.rows[artifact_id].status = "AVAILABLE"
    repository.rows[artifact_id].scan_status = "PASSED"
    deleted = client.delete(f"/api/v1/artifacts/{artifact_id}?confirmation_id=confirm-1", headers={"Idempotency-Key": "delete-1"})
    assert deleted.status_code == 202
    assert deleted.headers["Location"] == f"/api/v1/artifacts/{artifact_id}"


def test_write_path_without_uow_is_explicitly_unavailable(tmp_path: Path) -> None:
    app = FastAPI()
    app.state.actor_context = actor("project-a")
    app.state.idempotency_store = Idempotency()
    app.add_exception_handler(APIError, lambda request, exc: __import__("fastapi").responses.JSONResponse(status_code=exc.status_code, content={"error": exc.payload.model_dump(mode="json"), "request_id": "req-test"}))
    app.include_router(create_artifact_router(ArtifactService(Repository(), LocalArtifactStore(tmp_path), scanner=Scanner())), prefix="/api/v1")
    response = TestClient(app).post("/api/v1/artifacts/uploads", headers={"Idempotency-Key": "without-uow"}, json={"project_id": "project-a", "kind": "batch_input", "file_name": "a.csv", "media_type": "text/csv", "size_bytes": 3})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"


def test_business_failure_commits_failed_idempotency_record(tmp_path: Path) -> None:
    app = FastAPI()
    store = Idempotency()
    repository = Repository()
    factory = UnitOfWorkFactory(repository, store)
    app.state.actor_context = actor("project-a")
    app.state.idempotency_store = store
    app.add_exception_handler(APIError, lambda request, exc: __import__("fastapi").responses.JSONResponse(status_code=exc.status_code, content={"error": exc.payload.model_dump(mode="json"), "request_id": "req-test"}))
    app.include_router(create_artifact_router(ArtifactService(repository, LocalArtifactStore(tmp_path), scanner=Scanner()), unit_of_work=factory), prefix="/api/v1")
    response = TestClient(app).post("/api/v1/artifacts/uploads", headers={"Idempotency-Key": "failed-uow"}, json={"project_id": "project-a", "kind": "batch_input", "file_name": "a.csv", "media_type": "application/x-invalid", "size_bytes": 3})
    assert response.status_code == 422
    record = next(item for scope, item in store.records.items() if scope.key == "failed-uow")
    assert record.status == "FAILED"
    assert factory.instances[-1].commits == 1


def test_content_failure_rolls_back_artifact_mutation(tmp_path: Path) -> None:
    repository = Repository()
    store = Idempotency()
    factory = UnitOfWorkFactory(repository, store)
    service = ArtifactService(repository, LocalArtifactStore(tmp_path), scanner=Scanner())
    app = FastAPI()
    app.state.actor_context = actor("project-a")
    app.state.idempotency_store = store
    app.add_exception_handler(APIError, lambda request, exc: __import__("fastapi").responses.JSONResponse(status_code=exc.status_code, content={"error": exc.payload.model_dump(mode="json"), "request_id": "req-test"}))
    app.include_router(create_artifact_router(service, unit_of_work=factory), prefix="/api/v1")
    client = TestClient(app)
    body = {"project_id": "project-a", "kind": "batch_input", "file_name": "a.csv", "media_type": "text/csv", "size_bytes": 3, "expected_sha256": "0" * 64}
    created = client.post("/api/v1/artifacts/uploads", headers={"Idempotency-Key": "rollback-create"}, json=body)
    assert created.status_code == 201
    upload_id = created.json()["data"]["id"]
    response = client.put(f"/api/v1/artifacts/uploads/{upload_id}/content", content=b"abc")
    assert response.status_code == 422
    artifact_id = created.json()["data"]["artifact_id"]
    assert repository.rows[artifact_id].status == "CREATED"
    assert factory.instances[-1].rollbacks >= 1


def test_sync_uow_work_stays_on_one_worker_thread(tmp_path: Path) -> None:
    repository = Repository()
    store = Idempotency()
    factory = UnitOfWorkFactory(repository, store)
    app = FastAPI()
    app.state.actor_context = actor("project-a")
    app.state.idempotency_store = store
    app.add_exception_handler(APIError, lambda request, exc: __import__("fastapi").responses.JSONResponse(status_code=exc.status_code, content={"error": exc.payload.model_dump(mode="json"), "request_id": "req-test"}))
    app.include_router(create_artifact_router(ArtifactService(repository, LocalArtifactStore(tmp_path), scanner=Scanner()), unit_of_work=factory), prefix="/api/v1")
    response = TestClient(app).post("/api/v1/artifacts/uploads", headers={"Idempotency-Key": "thread-1"}, json={"project_id": "project-a", "kind": "batch_input", "file_name": "a.csv", "media_type": "text/csv", "size_bytes": 3})
    assert response.status_code == 201
    assert len(set(factory.instances[-1].thread_ids)) == 1


def _admission(size: int = 3, max_size: int = 10) -> UploadContentAdmission:
    return UploadContentAdmission(upload_id="upl-1", artifact_id="art-1", declared_size=size, max_size_bytes=max_size)


def test_bounded_stream_stops_before_consuming_an_oversized_chunk() -> None:
    request = StreamRequest([b"abcd", b"must-not-be-read"], headers={})
    with pytest.raises(APIError) as error:
        asyncio.run(_read_bounded_content(request, _admission()))
    assert error.value.status_code == 413
    assert request.read_count == 1


def test_missing_content_length_uses_bounded_stream_and_exact_size() -> None:
    request = StreamRequest([b"ab", b"c"], headers={})
    assert asyncio.run(_read_bounded_content(request, _admission())) == b"abc"
    too_long = StreamRequest([b"abc", b"d"], headers={})
    with pytest.raises(APIError) as error:
        asyncio.run(_read_bounded_content(too_long, _admission()))
    assert error.value.status_code == 413
    assert too_long.read_count == 2


def test_bounded_stream_rejects_short_body_and_invalid_content_length() -> None:
    short = StreamRequest([b"ab"], headers={})
    with pytest.raises(APIError) as error:
        asyncio.run(_read_bounded_content(short, _admission()))
    assert error.value.status_code == 422
    forged = StreamRequest([b"abc"], headers={"content-length": "2"})
    with pytest.raises(APIError) as error:
        asyncio.run(_read_bounded_content(forged, _admission()))
    assert error.value.status_code == 413
    assert forged.read_count == 0


def test_unauthorized_upload_does_not_consume_request_body(tmp_path: Path) -> None:
    app = FastAPI()
    repository = Repository()
    idempotency = Idempotency()
    service = ArtifactService(repository, LocalArtifactStore(tmp_path), scanner=Scanner())
    upload = service.create_upload(
        UploadCreate(project_id="project-a", kind="batch_input", file_name="a.csv", media_type="text/csv", size_bytes=3),
        actor("project-a"),
    )
    app.state.actor_context = actor("project-b")
    app.state.idempotency_store = idempotency
    app.include_router(create_artifact_router(service, unit_of_work=UnitOfWorkFactory(repository, idempotency)), prefix="/api/v1")
    route = next(item for item in app.routes if getattr(item, "path", None) == "/api/v1/artifacts/uploads/{upload_id}/content")
    consumed = False

    async def receive():
        nonlocal consumed
        consumed = True
        return {"type": "http.request", "body": b"secret", "more_body": False}

    from starlette.requests import Request

    request = Request(
        {
            "type": "http",
            "method": "PUT",
            "path": f"/api/v1/artifacts/uploads/{upload.id}/content",
            "raw_path": f"/api/v1/artifacts/uploads/{upload.id}/content".encode(),
            "query_string": b"",
            "headers": [],
            "client": ("test", 1),
            "server": ("test", 80),
            "scheme": "http",
            "app": app,
        },
        receive,
    )
    with pytest.raises(APIError) as error:
        asyncio.run(route.endpoint(upload.id, request))
    assert error.value.status_code == 404
    assert consumed is False
