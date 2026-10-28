"""Deletion through the assembled app and the real shared confirmation UOW."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from chrom_platform.infrastructure.models import AuditEvent, Confirmation, IdempotencyRecord
from chrom_platform.infrastructure.repositories import SQLAlchemyIdempotencyStore
from chrom_platform.modules.assistant.persistence import AgentAttemptRow, AgentRunRow, ConversationRow
from chrom_platform.infrastructure.models import Job, JobAttempt
from test_assistant_conversation_management import ACTOR_ID, CONVERSATION_ID, PROJECT_ID, _confirmed_archive, _make_api, _manifest
from test_assistant_conversation_delete import _seed_delete_graph


def _confirm_delete(client, conversation_id, key="delete-confirm"):
    preview = client.post("/api/v1/confirmations/previews", json={
        "resource_ref": {"type": "conversation", "id": conversation_id},
        "action": "conversation.delete",
    })
    assert preview.status_code == 200, preview.text
    value = preview.json()["data"]
    assert value["can_confirm"] is True
    created = client.post("/api/v1/confirmations", headers={
        "If-Match": value["etag"], "Idempotency-Key": key,
    }, json={k: value[k] for k in ("resource_ref", "action", "revision", "config_hash", "policy_version")})
    assert created.status_code == 201, created.text
    return value["etag"], {"confirmation_id": created.json()["data"]["id"]}


def _delete(client, conversation_id, etag, body, key="delete-once"):
    return client.request("DELETE", f"/api/v1/conversations/{conversation_id}", headers={
        "If-Match": etag, "Idempotency-Key": key,
    }, json=body)


def test_delete_real_confirmation_replay_and_old_title_cannot_be_recovered(tmp_path, monkeypatch):
    engine, factory, app = _make_api(monkeypatch=monkeypatch, manifest_path=_manifest(tmp_path), database_url=f"sqlite:///{tmp_path / 'delete.db'}")
    try:
        client = TestClient(app)
        create_body = {"project_id": PROJECT_ID, "title": "Private unique title", "mode": "business"}
        created = client.post("/api/v1/conversations", headers={"Idempotency-Key": "create-private"}, json=create_body)
        assert created.status_code == 201, created.text
        cid = created.json()["data"]["id"]
        etag, body = _confirm_delete(client, cid)
        assert client.get(f"/api/v1/conversations/{cid}").json()["data"]["etag"] == etag
        deleted = _delete(client, cid, etag, body)
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["data"] == {"id": cid, "deleted": True}
        assert "etag" not in deleted.headers
        replay = _delete(client, cid, etag, body)
        assert replay.status_code == 200, replay.text
        assert replay.headers["Idempotency-Replayed"] == "true"
        assert replay.json()["data"] == deleted.json()["data"]
        changed = _delete(client, cid, '"another-version"', body)
        assert changed.status_code == 409, changed.text
        assert changed.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
        for suffix in ("", "/messages", "/runs", "/tool-calls"):
            response = client.get(f"/api/v1/conversations/{cid}{suffix}")
            assert response.status_code == 404, (suffix, response.text)
        for archived in ("true", "false"):
            listed = client.get(f"/api/v1/conversations?archived={archived}&q=Private")
            assert listed.status_code == 200
            assert listed.json()["data"] == []
        old_create = client.post("/api/v1/conversations", headers={"Idempotency-Key": "create-private"}, json=create_body)
        assert old_create.status_code == 404, old_create.text
        assert "Private unique title" not in old_create.text
        with factory() as db:
            assert db.get(ConversationRow, cid) is None
            assert db.get(Confirmation, body["confirmation_id"]) is None
            assert db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "assistant.conversation.deleted")) == 1
            record = db.scalar(select(IdempotencyRecord).where(IdempotencyRecord.key == "delete-once"))
            assert record.method == "DELETE"
            assert record.result_resource_ref == {"id": cid, "deleted": True}
    finally:
        engine.dispose()


@pytest.mark.parametrize("headers,body,expected", [
    ({"If-Match": "v"}, {"confirmation_id": "c"}, 400),
    ({"Idempotency-Key": "k"}, {"confirmation_id": "c"}, 428),
    ({"Idempotency-Key": "k", "If-Match": "v"}, {"confirmation_id": "c", "cascade": True}, 422),
])
def test_delete_requires_headers_and_strict_body(tmp_path, monkeypatch, headers, body, expected):
    engine, factory, app = _make_api(monkeypatch=monkeypatch, manifest_path=_manifest(tmp_path), database_url=f"sqlite:///{tmp_path / 'delete.db'}")
    try:
        response = TestClient(app).request("DELETE", f"/api/v1/conversations/{CONVERSATION_ID}", headers=headers, json=body)
        assert response.status_code == expected, response.text
        with factory() as db:
            assert db.get(ConversationRow, CONVERSATION_ID) is not None
    finally:
        engine.dispose()


def test_delete_rename_invalidates_version_and_confirmation_without_residue(tmp_path, monkeypatch):
    engine, factory, app = _make_api(monkeypatch=monkeypatch, manifest_path=_manifest(tmp_path), database_url=f"sqlite:///{tmp_path / 'delete.db'}")
    try:
        client = TestClient(app)
        etag, body = _confirm_delete(client, CONVERSATION_ID)
        renamed = client.patch(f"/api/v1/conversations/{CONVERSATION_ID}", headers={
            "If-Match": etag, "Idempotency-Key": "rename-during-delete",
        }, json={"title": "New revision"})
        assert renamed.status_code == 200, renamed.text
        stale = _delete(client, CONVERSATION_ID, etag, body, "stale-delete")
        assert stale.status_code == 412, stale.text
        assert stale.json()["error"]["code"] == "ETAG_MISMATCH"
        invalid = _delete(client, CONVERSATION_ID, renamed.json()["data"]["etag"], body, "invalid-confirm-delete")
        assert invalid.status_code == 409, invalid.text
        with factory() as db:
            assert db.get(ConversationRow, CONVERSATION_ID).title == "New revision"
            assert db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "assistant.conversation.deleted")) == 0
            assert db.scalar(select(func.count()).select_from(IdempotencyRecord).where(IdempotencyRecord.key.in_(["stale-delete", "invalid-confirm-delete"]))) == 0
            assert db.get(Confirmation, body["confirmation_id"]).status == "CONFIRMED"
    finally:
        engine.dispose()


def test_delete_idempotency_completion_failure_rolls_back_resource_and_confirmation(tmp_path, monkeypatch):
    engine, factory, app = _make_api(monkeypatch=monkeypatch, manifest_path=_manifest(tmp_path), database_url=f"sqlite:///{tmp_path / 'delete.db'}")
    try:
        client = TestClient(app, raise_server_exceptions=False)
        etag, body = _confirm_delete(client, CONVERSATION_ID)
        original_complete = SQLAlchemyIdempotencyStore.complete

        def fail_delete_complete(self, scope, *args, **kwargs):
            if scope.method == "DELETE":
                raise RuntimeError("synthetic deletion completion failure")
            return original_complete(self, scope, *args, **kwargs)

        monkeypatch.setattr(SQLAlchemyIdempotencyStore, "complete", fail_delete_complete)
        failed = _delete(client, CONVERSATION_ID, etag, body, "fail-delete-complete")
        assert failed.status_code == 500, failed.text
        with factory() as db:
            assert db.get(ConversationRow, CONVERSATION_ID) is not None
            assert db.get(Confirmation, body["confirmation_id"]).status == "CONFIRMED"
            assert db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "assistant.conversation.deleted")) == 0
            assert db.scalar(select(func.count()).select_from(IdempotencyRecord).where(IdempotencyRecord.key == "fail-delete-complete")) == 0
    finally:
        engine.dispose()


@pytest.mark.parametrize("busy_kind", ["run", "attempt", "assistant-job", "assistant-job-attempt"])
def test_delete_each_individual_busy_source_blocks_without_consuming_confirmation(tmp_path, monkeypatch, busy_kind):
    engine, factory, app = _make_api(monkeypatch=monkeypatch, manifest_path=_manifest(tmp_path), database_url=f"sqlite:///{tmp_path / 'delete.db'}")
    try:
        cid = "qa-delete-isolated-conversation"
        with factory.begin() as db:
            ids = _seed_delete_graph(db, conversation_id=cid, project_id=PROJECT_ID, actor_id=ACTOR_ID, marker="isolated")
        client = TestClient(app)
        etag, body = _confirm_delete(client, cid)
        row_type = {"run": AgentRunRow, "attempt": AgentAttemptRow, "assistant-job": Job, "assistant-job-attempt": JobAttempt}[busy_kind]
        with factory.begin() as db:
            db.get(row_type, ids[busy_kind]).status = "RUNNING"
        response = _delete(client, cid, etag, body, "busy-delete")
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "CONVERSATION_BUSY"
        with factory() as db:
            assert db.get(ConversationRow, cid) is not None
            assert db.get(Confirmation, body["confirmation_id"]).status == "CONFIRMED"
            assert db.get(Job, ids["business-job"]).status == "SUCCEEDED"
            assert db.scalar(select(func.count()).select_from(IdempotencyRecord).where(IdempotencyRecord.key == "busy-delete")) == 0
    finally:
        engine.dispose()


@pytest.mark.parametrize("actor_change", [{"actor_id": "other-owner"}, {"project_ids": ["other-project"]}])
def test_delete_other_owner_or_project_is_invisible(tmp_path, monkeypatch, actor_change):
    engine, factory, app = _make_api(monkeypatch=monkeypatch, manifest_path=_manifest(tmp_path), database_url=f"sqlite:///{tmp_path / 'delete.db'}")
    try:
        client = TestClient(app)
        etag, body = _confirm_delete(client, CONVERSATION_ID)
        app.state.actor_context = app.state.actor_context.model_copy(update=actor_change)
        response = _delete(client, CONVERSATION_ID, etag, body, "invisible-delete")
        assert response.status_code == 404, response.text
        with factory() as db:
            assert db.get(ConversationRow, CONVERSATION_ID) is not None
            assert db.get(Confirmation, body["confirmation_id"]).status == "CONFIRMED"
            assert db.scalar(select(func.count()).select_from(IdempotencyRecord).where(IdempotencyRecord.key == "invisible-delete")) == 0
    finally:
        engine.dispose()


def test_delete_rejects_archive_confirmation_without_consuming_it(tmp_path, monkeypatch):
    engine, factory, app = _make_api(monkeypatch=monkeypatch, manifest_path=_manifest(tmp_path), database_url=f"sqlite:///{tmp_path / 'delete.db'}")
    try:
        client = TestClient(app)
        confirmation_id = _confirmed_archive(client)
        etag = client.get(f"/api/v1/conversations/{CONVERSATION_ID}").json()["data"]["etag"]
        response = _delete(client, CONVERSATION_ID, etag, {"confirmation_id": confirmation_id}, "wrong-action-delete")
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "CONFIRMATION_INVALIDATED"
        with factory() as db:
            assert db.get(ConversationRow, CONVERSATION_ID) is not None
            assert db.get(Confirmation, confirmation_id).status == "CONFIRMED"
    finally:
        engine.dispose()
