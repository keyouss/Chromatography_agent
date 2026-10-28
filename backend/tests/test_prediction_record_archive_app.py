from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from chrom_platform.app import create_app
from chrom_platform.infrastructure.db import Base
from chrom_platform.infrastructure.models import Confirmation, Project
from chrom_platform.modules.data_center.persistence import PredictionRecordRow
from chrom_platform.infrastructure.metadata import m2_metadata


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _archive_app(monkeypatch, tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{tmp_path / 'archive-app.db'}", connect_args={"timeout": 10})
    event.listen(engine, "connect", lambda dbapi, _: dbapi.create_function("CHAR_LENGTH", 1, len))
    Base.metadata.create_all(engine)
    m2_metadata().create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    etag = '"record-a-v1"'
    with sessions.begin() as session:
        session.add(Project(id="project-a", slug="project-a", name="Project A", status="ACTIVE", revision=1))
        session.add(PredictionRecordRow(
            id="record-a", project_id="project-a", prediction_id=None, batch_id=None,
            source="web", actor_id="actor-a", chromatography_type="sfc", model_snapshot={
                "id": "model-a", "version": "1", "business_scope_id": "sfc", "artifact_sha256": "a" * 64,
                "input_schema_version": "1.0", "output_schema_version": "1.0",
            }, input_snapshot={}, result_ref=None, warnings=[], status="SUCCEEDED", archived_at=None,
            created_at=now, artifact_refs=[], revision=1, etag=etag,
        ))
    import chrom_platform.app as app_module
    monkeypatch.setattr(app_module, "_create_session_factory", lambda: sessions)
    monkeypatch.setenv("CHROM_PROJECT_IDS", "project-a")
    monkeypatch.setenv("CHROM_ACTOR_ID", "actor-a")
    app = create_app("builds/universal-v1.json", contract_generation=True)
    return TestClient(app), sessions, engine


def _confirm_archive(client):
    resource_ref = {"type": "prediction_record", "id": "record-a"}
    preview = client.post("/api/v1/confirmations/previews", json={"resource_ref": resource_ref, "action": "resource.archive"})
    assert preview.status_code == 200, preview.text
    data = preview.json()["data"]
    assert data["can_confirm"] is True
    created = client.post(
        "/api/v1/confirmations",
        headers={"If-Match": data["etag"], "Idempotency-Key": "confirm-archive-a"},
        json={
            "resource_ref": resource_ref, "action": "resource.archive", "revision": data["revision"],
            "config_hash": data["config_hash"], "policy_version": data["policy_version"],
        },
    )
    assert created.status_code == 201, created.text
    return created.json()["data"]["id"]


def test_real_app_archive_consumes_confirmation_and_idempotency_once(monkeypatch, tmp_path):
    client, sessions, engine = _archive_app(monkeypatch, tmp_path)
    confirmation_id = _confirm_archive(client)
    endpoint = "/api/v1/prediction-records/record-a/archive?project_id=project-a"
    headers = {"Idempotency-Key": "archive-a"}
    first = client.post(endpoint, headers=headers, json={"confirmation_id": confirmation_id})
    second = client.post(endpoint, headers=headers, json={"confirmation_id": confirmation_id})
    assert first.status_code == second.status_code == 200
    assert first.json()["data"]["status"] == "ARCHIVED"
    assert first.json()["data"]["revision"] == 2
    assert second.headers["Idempotency-Replayed"] == "true"
    consumed = client.get(f"/api/v1/confirmations/{confirmation_id}")
    assert consumed.status_code == 200
    assert consumed.json()["data"]["status"] == "CONSUMED"
    with sessions() as session:
        record = session.get(PredictionRecordRow, "record-a")
        confirmation = session.get(Confirmation, confirmation_id)
        assert record.status == "ARCHIVED" and record.revision == 2
        assert confirmation.status == "CONSUMED" and confirmation.consumed_at is not None
    engine.dispose()


def test_archive_wrong_target_revision_expired_revoked_and_actor_fail_closed(monkeypatch, tmp_path):
    cases = ["wrong-target", "wrong-revision", "expired", "revoked", "wrong-actor", "tampered-binding"]
    for index, case in enumerate(cases):
        client, sessions, engine = _archive_app(monkeypatch, tmp_path / str(index))
        confirmation_id = _confirm_archive(client)
        with sessions.begin() as session:
            confirmation = session.get(Confirmation, confirmation_id)
            if case == "wrong-target":
                confirmation.resource_ref = {"type": "prediction_record", "id": "another-record"}
            elif case == "wrong-revision":
                confirmation.revision = 2
            elif case == "expired":
                confirmation.created_at = datetime.now(timezone.utc) - timedelta(minutes=5)
                confirmation.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            elif case == "revoked":
                confirmation.status = "REVOKED"
                confirmation.revoked_at = datetime.now(timezone.utc)
            elif case == "wrong-actor":
                confirmation.actor_id = "another-actor"
            elif case == "tampered-binding":
                confirmation.binding_hash = "0" * 64
        result = client.post(
            "/api/v1/prediction-records/record-a/archive?project_id=project-a",
            headers={"Idempotency-Key": f"archive-bad-{index}"}, json={"confirmation_id": confirmation_id},
        )
        assert result.status_code in {404, 409}, (case, result.text)
        with sessions() as session:
            record = session.get(PredictionRecordRow, "record-a")
            confirmation = session.get(Confirmation, confirmation_id)
            assert record.status == "SUCCEEDED" and record.revision == 1
            if case == "revoked":
                assert confirmation.status == "REVOKED" and confirmation.revoked_at is not None
            elif case == "expired":
                assert confirmation.status == "CONFIRMED" and confirmation.consumed_at is None
            else:
                assert confirmation.status == "CONFIRMED" and confirmation.consumed_at is None
        engine.dispose()


def test_archive_confirmation_requires_resource_permission_and_rejects_archived_record(monkeypatch, tmp_path):
    client, sessions, engine = _archive_app(monkeypatch, tmp_path)
    actor = client.app.state.actor_context
    client.app.state.actor_context = actor.model_copy(update={"permissions": ["task:confirm", "prediction:read"]})
    resource_ref = {"type": "prediction_record", "id": "record-a"}
    denied = client.post("/api/v1/confirmations/previews", json={"resource_ref": resource_ref, "action": "resource.archive"})
    assert denied.status_code == 403
    denied_create = client.post(
        "/api/v1/confirmations", headers={"If-Match": '"record-a-v1"', "Idempotency-Key": "no-archive-permission"},
        json={"resource_ref": resource_ref, "action": "resource.archive", "revision": 1, "config_hash": "0" * 64, "policy_version": "deployment-default-v1"},
    )
    assert denied_create.status_code == 403
    client.app.state.actor_context = actor
    confirmation_id = _confirm_archive(client)
    with sessions.begin() as session:
        record = session.get(PredictionRecordRow, "record-a")
        record.status = "ARCHIVED"
    archived_preview = client.post("/api/v1/confirmations/previews", json={"resource_ref": resource_ref, "action": "resource.archive"})
    assert archived_preview.status_code == 200
    preview = archived_preview.json()["data"]
    assert preview["can_confirm"] is False
    denied_create = client.post(
        "/api/v1/confirmations", headers={"If-Match": preview["etag"], "Idempotency-Key": "confirm-archived"},
        json={"resource_ref": resource_ref, "action": "resource.archive", "revision": preview["revision"], "config_hash": preview["config_hash"], "policy_version": preview["policy_version"]},
    )
    assert denied_create.status_code == 409
    with sessions() as session:
        assert session.get(Confirmation, confirmation_id).status == "CONFIRMED"
        assert len(session.scalars(select(Confirmation)).all()) == 1
    engine.dispose()


def test_archive_idempotency_completion_failure_rolls_back_confirmation_and_record(monkeypatch, tmp_path):
    client, sessions, engine = _archive_app(monkeypatch, tmp_path)
    confirmation_id = _confirm_archive(client)
    from chrom_platform.infrastructure.repositories import SQLAlchemyIdempotencyStore

    def fail_complete(*_args, **_kwargs):
        raise RuntimeError("simulated idempotency persistence failure")

    monkeypatch.setattr(SQLAlchemyIdempotencyStore, "complete", fail_complete)
    result = client.post(
        "/api/v1/prediction-records/record-a/archive?project_id=project-a",
        headers={"Idempotency-Key": "archive-rollback"}, json={"confirmation_id": confirmation_id},
    )
    assert result.status_code == 503
    with sessions() as session:
        record = session.get(PredictionRecordRow, "record-a")
        confirmation = session.get(Confirmation, confirmation_id)
        assert record.status == "SUCCEEDED" and record.revision == 1
        assert confirmation.status == "CONFIRMED" and confirmation.consumed_at is None
    engine.dispose()
