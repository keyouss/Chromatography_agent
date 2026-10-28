from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from chrom_platform.assembly.profile import _records_router_source
from chrom_platform.core.dto import ActorContext, ResourceRef
from chrom_platform.core.errors import APIError
from chrom_platform.core.idempotency import BeginResult, IdempotencyRecord
from chrom_platform.modules.data_center.router import create_router
from chrom_platform.modules.data_center.schemas import PredictionDraftView, PredictionRecord


class Store:
    def __init__(self):
        self.rows = {}

    def get(self, scope):
        return self.rows.get(scope)

    def begin(self, scope, payload_hash):
        if scope in self.rows:
            return BeginResult(self.rows[scope], False)
        record = IdempotencyRecord(payload_hash=payload_hash, status="IN_PROGRESS")
        self.rows[scope] = record
        return BeginResult(record, True)

    def complete(self, scope, payload_hash, status_code, response):
        self.rows[scope] = IdempotencyRecord(payload_hash, "COMPLETED", status_code, response)

    def fail(self, scope, payload_hash, error_code):
        self.rows[scope] = IdempotencyRecord(payload_hash, "FAILED")


class Records:
    def __init__(self):
        self.values = {key: self._record(key) for key in ("record-1", "record-2", "record-3")}
        self.replay_calls = 0
        self.archive_calls = 0
        self.archive_confirmation_ids = []

    @staticmethod
    def _record(record_id: str) -> PredictionRecord:
        return PredictionRecord(
            id=record_id,
            project_id="project-a",
            source="single_prediction",
            actor_id="actor-a",
            chromatography_type="sfc",
            model_snapshot={"model_id": "model-current", "version": "2"},
            input_snapshot={"tool": "single_column", "schema_version": "1.0", "structure_refs": []},
            warnings=[],
            status="SUCCEEDED",
            created_at=datetime(2026, 10, 4, tzinfo=timezone.utc),
            artifact_refs=[],
            revision=1,
            etag='"revision-1"',
        )

    def get(self, project_id, record_id):
        value = self.values.get(record_id)
        return value if value is not None and value.project_id == project_id else None

    def get_prediction_any(self, project_ids, record_id):
        value = self.values.get(record_id)
        return value if value is not None and value.project_id in project_ids else None

    def list(self, project_id, *, chromatography_type=None, status=None, limit=50, after_id=None):
        values = sorted((v for v in self.values.values() if v.project_id == project_id), key=lambda v: v.id)
        if after_id is not None:
            values = [v for v in values if v.id > after_id]
        return values[:limit]

    def create_replay_draft(self, record):
        self.replay_calls += 1
        return PredictionDraftView(
            id=f"draft-{self.replay_calls}", source_record_id=record.id,
            source_prediction_id="prediction-1",
            project_id=record.project_id, chromatography_type=record.chromatography_type,
            tool="single_column", structure_refs=[ResourceRef(type="structure", id="main-1")], input=record.input_snapshot,
            structure_snapshots=[], modifier_structure_snapshots=[], schema_version="1.0",
            model_selection={"mode": "exact", "model_id": "model-current", "model_version": "2"},
            source_model_snapshot={
                "id": "model-current", "version": "2", "business_scope_id": "sfc",
                "artifact_sha256": "a" * 64, "input_schema_version": "sfc-in-2",
                "output_schema_version": "sfc-out-2",
            }, source_record_revision=1, revision=1,
            etag='"draft-1"', created_at=datetime(2026, 10, 4, tzinfo=timezone.utc),
            updated_at=datetime(2026, 10, 4, tzinfo=timezone.utc),
        )

    def replay_draft(self, *, project_id, record_id, actor_id):
        return self.create_replay_draft(self.get(project_id, record_id))

    def get_draft(self, *, project_id, draft_id):
        return self.create_replay_draft(self.get(project_id, "record-1"))

    def archive_record(self, *, project_id, record_id, request, actor_id):
        self.archive_calls += 1
        self.archive_confirmation_ids.append(request.confirmation_id)
        value = self.get(project_id, record_id)
        updated = value.model_copy(update={"status": "ARCHIVED", "revision": value.revision + 1, "etag": '"revision-2"'})
        self.values[record_id] = updated
        return updated


def _router(profile: bool, records: Records, store: Store):
    kwargs = {"prediction_record_service": records, "prediction_repository": records, "idempotency_store": store}
    if not profile:
        return create_router(**kwargs)
    source = _records_router_source()
    source = source.replace("from .errors import", "from chrom_platform.modules.data_center.errors import")
    source = source.replace("from .schemas import", "from chrom_platform.modules.data_center.schemas import")
    source = source.replace("from .service import", "from chrom_platform.modules.data_center.service import")
    namespace = {"__name__": "chrom_platform.modules.data_center.generated_router"}
    exec(compile(source, "generated_records_router.py", "exec"), namespace)
    return namespace["create_router"](**kwargs)


def _client(profile=False, *, permissions=("*",), projects=("project-a",)):
    app = FastAPI()
    app.state.actor_context = ActorContext(
        actor_id="actor-a", tenant_id=None, project_ids=list(projects),
        auth_method="deployment_default", permissions=list(permissions), policy_version="test",
    )
    records, store = Records(), Store()
    app.add_exception_handler(APIError, lambda request, exc: JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.payload.model_dump(mode="json"), "request_id": "request-test"},
    ))
    app.include_router(_router(profile, records, store), prefix="/api/v1")
    return TestClient(app), records, store


@pytest.mark.parametrize("profile", [False, True], ids=["universal", "sfc-profile-router"])
def test_records_permissions_project_scope_and_cursor(profile):
    client, _, _ = _client(profile, permissions=("prediction:read",))
    first = client.get("/api/v1/prediction-records?project_id=project-a&limit=1")
    assert first.status_code == 200
    assert [item["id"] for item in first.json()["data"]] == ["record-1"]
    cursor = first.json()["page"]["next_cursor"]
    second = client.get(f"/api/v1/prediction-records?project_id=project-a&limit=1&cursor={cursor}")
    assert [item["id"] for item in second.json()["data"]] == ["record-2"]

    client.app.state.actor_context.project_ids = []
    assert client.get("/api/v1/prediction-records/record-1?project_id=project-a").status_code == 404
    client.app.state.actor_context.permissions = ["*"]
    assert client.get("/api/v1/prediction-records/record-1?project_id=project-a").status_code == 404
    client.app.state.actor_context.project_ids = ["project-a"]
    client.app.state.actor_context.permissions = []
    assert client.get("/api/v1/prediction-records?project_id=project-a").status_code == 403
    assert client.get("/api/v1/prediction-records/record-1?project_id=project-a").status_code == 403


@pytest.mark.parametrize("profile", [False, True], ids=["universal", "sfc-profile-router"])
def test_replay_requires_read_create_empty_body_and_replays_idempotently(profile):
    client, records, _ = _client(profile, permissions=("prediction:read", "prediction:create"))
    endpoint = "/api/v1/prediction-records/record-1/replay-draft?project_id=project-a"
    headers = {"Idempotency-Key": "replay-1"}
    client.app.state.actor_context.permissions = ["prediction:create"]
    assert client.post(endpoint, headers=headers, json={}).status_code == 403
    client.app.state.actor_context.permissions = ["prediction:read"]
    assert client.post(endpoint, headers={"Idempotency-Key": "replay-2"}, json={"unknown": True}).status_code == 422
    assert client.post(endpoint, headers={"Idempotency-Key": "replay-empty-body"}).status_code == 422
    assert client.post(endpoint, json={}).status_code == 400
    client.app.state.actor_context.permissions = ["prediction:read", "prediction:create"]
    first = client.post(endpoint, headers=headers, json={})
    second = client.post(endpoint, headers=headers, json={})
    assert first.status_code == second.status_code == 201
    assert first.json()["data"]["id"] == second.json()["data"]["id"] == "draft-1"
    assert second.headers["Idempotency-Replayed"] == "true"
    assert records.replay_calls == 1
    changed_target = client.post(endpoint.replace("record-1", "record-2"), headers=headers, json={})
    assert changed_target.status_code == 409


@pytest.mark.parametrize("profile", [False, True], ids=["universal", "sfc-profile-router"])
def test_get_replay_draft_uses_read_permission_and_project_scope(profile):
    client, _, _ = _client(profile, permissions=("prediction:read",))
    result = client.get("/api/v1/prediction-drafts/draft-1?project_id=project-a")
    assert result.status_code == 200
    assert result.headers["ETag"] == '"draft-1"'
    client.app.state.actor_context.permissions = ["prediction:create"]
    assert client.get("/api/v1/prediction-drafts/draft-1?project_id=project-a").status_code == 403
    client.app.state.actor_context.permissions = ["prediction:read"]
    client.app.state.actor_context.project_ids = []
    assert client.get("/api/v1/prediction-drafts/draft-1?project_id=project-a").status_code == 404


@pytest.mark.parametrize("profile", [False, True], ids=["universal", "sfc-profile-router"])
def test_archive_requires_read_and_archive_permission_and_replays_confirmation_result(profile):
    endpoint = "/api/v1/prediction-records/record-1/archive?project_id=project-a"
    body = {"confirmation_id": "confirm-1"}
    client, records, _ = _client(profile, permissions=("prediction:read", "dataset:write"))
    assert client.post(endpoint, headers={"Idempotency-Key": "archive-1"}, json=body).status_code == 403
    client.app.state.actor_context.permissions = ["prediction:read", "prediction:create"]
    assert client.post(endpoint, headers={"Idempotency-Key": "archive-create"}, json=body).status_code == 403
    client.app.state.actor_context.permissions = ["prediction:archive"]
    assert client.post(endpoint, headers={"Idempotency-Key": "archive-only"}, json=body).status_code == 403
    client.app.state.actor_context.permissions = ["prediction:read", "prediction:archive"]
    headers = {"Idempotency-Key": "archive-2"}
    first = client.post(endpoint, headers=headers, json=body)
    second = client.post(endpoint, headers=headers, json=body)
    assert first.status_code == second.status_code == 200
    assert first.json()["data"]["status"] == second.json()["data"]["status"] == "ARCHIVED"
    assert first.json()["data"]["revision"] == second.json()["data"]["revision"] == 2
    assert second.headers["Idempotency-Replayed"] == "true"
    assert records.archive_calls == 1
    assert records.archive_confirmation_ids == ["confirm-1"]


def test_sfc_records_openapi_matches_universal_request_contracts():
    client, _, _ = _client(profile=True)
    paths = client.app.openapi()["paths"]
    item = paths["/api/v1/prediction-records/{record_id}"]["get"]["parameters"]
    project = next(value for value in item if value["name"] == "project_id")
    assert project["required"] is False
    replay = paths["/api/v1/prediction-records/{record_id}/replay-draft"]["post"]
    assert any(value["name"] == "Idempotency-Key" and value["required"] for value in replay["parameters"])
    assert replay["requestBody"]["required"] is True
    archive = paths["/api/v1/prediction-records/{record_id}/archive"]["post"]
    assert any(value["name"] == "Idempotency-Key" and value["required"] for value in archive["parameters"])
    listing = paths["/api/v1/prediction-records"]["get"]["parameters"]
    assert any(value["name"] == "cursor" and not value["required"] for value in listing)
