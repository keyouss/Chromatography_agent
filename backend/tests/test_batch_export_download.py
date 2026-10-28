from __future__ import annotations

import hashlib
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from chrom_platform.artifact_core.models import Artifact
from chrom_platform.artifact_core.repository import SQLAlchemyArtifactReader
from chrom_platform.artifact_core.service import ArtifactService, ArtifactServiceError
from chrom_platform.artifact_core.storage import LocalArtifactStore
from chrom_platform.core.dto import ActorContext
from chrom_platform.core.errors import APIError
from chrom_platform.infrastructure.db import Base
from chrom_platform.infrastructure.models import Project
from chrom_platform.modules.prediction.batch_router import create_router
from chrom_platform.modules.prediction.batch_service import BatchExportRow, BatchPredictionRow, BatchPredictionService
from chrom_platform.task_core.service import OperationContext


EXPORT_ID = "batch-export-6099"
ARTIFACT_ID = "artifact-export-6099"
CONTENT_SIZE = 6099


def _export_content() -> bytes:
    payload = {
        "format_version": "1.0",
        "generated_at": "2026-10-22T00:00:00+00:00",
        "include": ["result"],
        "source": {"batch_id": "batch-1", "draft_revision": 1, "validation_id": "validation-1", "attempt": 1},
        "records": [{
            "record_id": "row-1", "status": "SUCCEEDED", "source": "batch_prediction",
            "chromatography_type": "gc", "result": {"value": ""},
        }],
    }
    base = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    padding = CONTENT_SIZE - len(base)
    assert padding >= 0
    payload["records"][0]["result"]["value"] = "x" * padding
    content = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    assert len(content) == CONTENT_SIZE
    return content


def _workspace(tmp_path, *, artifact_changes: dict[str, object] | None = None):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    event.listen(engine, "connect", lambda conn, _record: conn.create_function(
        "CHAR_LENGTH", 1, lambda value: len(value) if value is not None else None,
    ))
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    store = LocalArtifactStore(tmp_path / "artifact-store")
    content = _export_content()
    storage_key, size_bytes, digest = store.put(
        ARTIFACT_ID, content, expected_size=len(content), expected_sha256=hashlib.sha256(content).hexdigest(),
    )
    with sessions.begin() as setup:
        setup.add_all([
            Project(id="p1", slug="p1", name="Project 1"),
            Project(id="p2", slug="p2", name="Project 2"),
            Artifact(
                id="input-artifact", project_id="p1", kind="batch_input", title="input.csv",
                media_type="text/csv", size_bytes=1, sha256="b" * 64, storage_key="input/content",
                status="AVAILABLE", scan_status="PASSED", metadata_json={}, created_by="actor-a",
            ),
            BatchPredictionRow(
                id="batch-1", project_id="p1", actor_id="actor-a", chromatography_type="gc", tool="batch",
                name="GC batch", schema_version="1.1", model_selection={"mode": "champion", "scope_id": "gc"},
                input_artifact_id="input-artifact", template_version="gc-batch-1", field_mapping={}, common_input={},
                invalid_row_policy="block", resource_budget={"max_evaluations": 10, "max_runtime_seconds": 60},
                revision=1, config_hash="a" * 64, etag='"batch-r1"', draft_status="READY", validation_id="validation-1",
                counts={}, progress=1.0,
            ),
            BatchExportRow(
                id=EXPORT_ID, project_id="p1", batch_id="batch-1", job_id="job-export", format="json",
                include=["result"], source_snapshot={"source": {"draft_revision": 1, "validation_id": "validation-1", "attempt": 1}},
                source_snapshot_hash="c" * 64, status="SUCCEEDED", artifact_id=ARTIFACT_ID,
            ),
            Artifact(
                id=ARTIFACT_ID, project_id="p1", kind="batch_export", title="batch-export.json",
                media_type="application/json", size_bytes=size_bytes, sha256=digest, storage_key=storage_key,
                status="AVAILABLE", scan_status="PASSED",
                metadata_json={"file_name": "batch-export.json", "source_ref": {"type": "batch_export", "id": EXPORT_ID}},
                created_by="actor-a",
            ),
        ])
    if artifact_changes:
        with sessions.begin() as setup:
            row = setup.get(Artifact, ARTIFACT_ID)
            for key, value in artifact_changes.items():
                setattr(row, key, value)
    artifact_service = ArtifactService(SQLAlchemyArtifactReader(sessions), store)
    batch_session = Session(engine, expire_on_commit=False)
    batch_service = BatchPredictionService(batch_session, artifact=artifact_service, enabled_batch_types={"gc"})
    app = FastAPI()
    app.state.actor_context = ActorContext(
        actor_id="actor-a", project_ids=["p1"], auth_method="service",
        permissions=["batch:read", "artifact:read"], policy_version="test",
    )
    app.include_router(create_router(service=batch_service), prefix="/api/v1")

    @app.exception_handler(APIError)
    def handle_api_error(_request, exc: APIError):
        return JSONResponse(status_code=exc.status_code, content={"code": exc.payload.code})

    return engine, store, artifact_service, app, content, batch_session, sessions


def test_batch_export_http_download_uses_internal_artifact_content_port(tmp_path):
    engine, _store, artifact_service, app, expected, batch_session, _sessions = _workspace(tmp_path)
    actor = app.state.actor_context
    try:
        with TestClient(app) as client:
            response = client.get(f"/api/v1/batch-predictions/exports/{EXPORT_ID}/download")
            assert response.status_code == 200
            assert response.content == expected and len(response.content) == CONTENT_SIZE
            assert response.headers["content-type"] == "application/json"
            assert response.headers["content-disposition"] == f'attachment; filename="batch-{EXPORT_ID}.json"'

            app.state.actor_context = ActorContext(
                actor_id="actor-a", project_ids=["p2"], auth_method="service",
                permissions=["batch:read", "artifact:read"], policy_version="test",
            )
            assert client.get(f"/api/v1/batch-predictions/exports/{EXPORT_ID}/download").status_code == 404
            app.state.actor_context = ActorContext(
                actor_id="actor-a", project_ids=["p1"], auth_method="service",
                permissions=["batch:read"], policy_version="test",
            )
            assert client.get(f"/api/v1/batch-predictions/exports/{EXPORT_ID}/download").status_code == 403
            app.state.actor_context = ActorContext(
                actor_id="actor-a", project_ids=["p1"], auth_method="service",
                permissions=["artifact:read"], policy_version="test",
            )
            assert client.get(f"/api/v1/batch-predictions/exports/{EXPORT_ID}/download").status_code == 403

        with pytest.raises(ArtifactServiceError) as public_view:
            artifact_service.get_artifact(ARTIFACT_ID, actor)
        assert public_view.value.status_code == 404
        with pytest.raises(ArtifactServiceError) as public_download:
            artifact_service.download(ARTIFACT_ID, actor)
        assert public_download.value.status_code == 404
        internal_row, internal_bytes = artifact_service.download_content(ARTIFACT_ID, actor)
        assert internal_row.kind == "batch_export" and internal_bytes == expected
    finally:
        batch_session.close()
        engine.dispose()


@pytest.mark.parametrize(
    ("failure", "changes", "tamper_content"),
    [
        ("wrong source ref", {"metadata_json": {"source_ref": {"type": "batch_export", "id": "other"}}}, False),
        ("wrong kind", {"kind": "batch_input"}, False),
        ("unavailable", {"status": "QUARANTINED"}, False),
        ("scan not passed", {"scan_status": "FAILED"}, False),
        ("wrong persisted digest", {"sha256": "0" * 64}, False),
        ("wrong persisted size", {"size_bytes": CONTENT_SIZE + 1}, False),
        ("stored bytes tampered", {}, True),
    ],
)
def test_batch_export_download_rejects_artifact_identity_and_content_mismatches(
    tmp_path, failure: str, changes: dict[str, object], tamper_content: bool,
):
    engine, store, _artifact_service, app, _expected, batch_session, _sessions = _workspace(
        tmp_path, artifact_changes=changes,
    )
    if tamper_content:
        row_key = f"objects/{ARTIFACT_ID}/content"
        store._path_for(row_key).write_bytes(b"tampered")
    try:
        with TestClient(app) as client:
            response = client.get(f"/api/v1/batch-predictions/exports/{EXPORT_ID}/download")
        assert response.status_code == 404, failure
        assert response.json()["code"] == "RESOURCE_NOT_FOUND"
    finally:
        batch_session.close()
        engine.dispose()


def test_public_artifact_download_still_serves_known_kind(tmp_path):
    engine, store, artifact_service, app, _expected, batch_session, sessions = _workspace(tmp_path)
    content = b"ordinary input file\n"
    storage_key, size_bytes, digest = store.put("normal-input", content, expected_size=len(content), expected_sha256=hashlib.sha256(content).hexdigest())
    with sessions.begin() as setup:
        setup.add(Artifact(
            id="normal-input", project_id="p1", kind="batch_input", title="normal.csv", media_type="text/csv",
            size_bytes=size_bytes, sha256=digest, storage_key=storage_key, status="AVAILABLE", scan_status="PASSED",
            metadata_json={"file_name": "normal.csv"}, created_by="actor-a",
        ))
    try:
        view, downloaded = artifact_service.download("normal-input", app.state.actor_context)
        assert view.kind == "batch_input" and downloaded == content
    finally:
        batch_session.close()
        engine.dispose()
