from __future__ import annotations

from datetime import datetime, timezone
import json
import base64
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker

from chrom_platform.app import create_app
from chrom_platform.core.dto import ActorContext
from chrom_platform.infrastructure.models import AuditEvent, Base as PlatformBase, Confirmation, IdempotencyRecord, Job, Project
from chrom_platform.modules.assistant.persistence import AgentAttemptRow, AgentRunRow, AssistantBase, ConversationRow, MessageRow, SQLAlchemyAssistantRepository


CONVERSATION_ID = "conversation-a"
PROJECT_ID = "project-a"
ACTOR_ID = "actor-a"


def _make_api(*, monkeypatch, manifest_path: Path, session_class=Session, database_url="sqlite://"):
    engine = create_engine(database_url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _sqlite_functions(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")
        dbapi_connection.create_function("CHAR_LENGTH", 1, lambda value: len(value) if value is not None else None)
        dbapi_connection.isolation_level = None

    @event.listens_for(engine, "begin")
    def _sqlite_begin(connection):
        connection.exec_driver_sql("BEGIN")

    factory = sessionmaker(bind=engine, class_=session_class, expire_on_commit=False)
    PlatformBase.metadata.create_all(engine)
    AssistantBase.metadata.create_all(engine)
    actor = ActorContext(
        actor_id=ACTOR_ID, tenant_id=None, project_ids=[PROJECT_ID],
        auth_method="deployment_default", permissions=["assistant:use", "task:confirm"],
        policy_version="deployment-default-v1",
    )
    with factory.begin() as db:
        db.add(Project(id=PROJECT_ID, slug=PROJECT_ID, name="Project A"))
        db.add(ConversationRow(
            id=CONVERSATION_ID, project_id=PROJECT_ID, owner_id=ACTOR_ID,
            title="Original title", mode="business", status="ACTIVE", revision=1,
            etag='"conversation-1"', created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
        ))

    from chrom_platform import app as app_module
    monkeypatch.setattr(app_module, "_create_session_factory", lambda: factory)
    monkeypatch.setattr(app_module, "_actor_context_from_environment", lambda: actor)
    app = create_app(manifest_path, contract_generation=False)
    assert app.state.m4_assembly.confirmation_service is app.state.confirmation_service
    return engine, factory, app


def _confirmed_archive(client: TestClient, *, key: str = "confirmation-create") -> str:
    preview_response = client.post("/api/v1/confirmations/previews", json={
        "resource_ref": {"type": "conversation", "id": CONVERSATION_ID},
        "action": "resource.archive",
    })
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()["data"]
    assert preview["revision"] == 1
    assert preview["can_confirm"] is True
    created = client.post(
        "/api/v1/confirmations",
        headers={"If-Match": preview["etag"], "Idempotency-Key": key},
        json={
            "resource_ref": preview["resource_ref"], "action": preview["action"],
            "revision": preview["revision"], "config_hash": preview["config_hash"],
            "policy_version": preview["policy_version"],
        },
    )
    assert created.status_code == 201, created.text
    return created.json()["data"]["id"]


def _manifest(tmp_path: Path) -> Path:
    path = tmp_path / "assistant-manifest.json"
    path.write_text(json.dumps({
        "manifest_version": "1.0", "build_id": "assistant-conversation-test",
        "modules": ["assistant"], "assistant_features": ["business"],
        "chromatography_types": [], "prediction_tools": {}, "utilities": [],
        "export_formats": [], "auth_provider": "deployment_default",
    }), encoding="utf-8")
    return path


def _cursor(payload: object) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode("ascii").rstrip("=")


def test_m4_assembled_conversation_confirmation_archive_and_replay_are_audited_once(tmp_path, monkeypatch):
    engine, factory, app = _make_api(monkeypatch=monkeypatch, manifest_path=_manifest(tmp_path), database_url=f"sqlite:///{tmp_path / 'assistant.db'}")
    try:
        client = TestClient(app)
        confirmation_id = _confirmed_archive(client)
        request = client.post(
            f"/api/v1/conversations/{CONVERSATION_ID}/archive",
            headers={"Idempotency-Key": "archive-once"},
            json={"confirmation_id": confirmation_id},
        )
        assert request.status_code == 200, request.text
        assert "Idempotency-Replayed" not in request.headers
        archived = request.json()["data"]
        assert archived["status"] == "ARCHIVED"
        assert archived["revision"] == 2

        replay = client.post(
            f"/api/v1/conversations/{CONVERSATION_ID}/archive",
            headers={"Idempotency-Key": "archive-once"},
            json={"confirmation_id": confirmation_id},
        )
        assert replay.status_code == 200, replay.text
        assert replay.headers["Idempotency-Replayed"] == "true"
        assert replay.json()["data"]["revision"] == 2
        blocked = client.post(
            f"/api/v1/conversations/{CONVERSATION_ID}/messages",
            headers={"Idempotency-Key": "archived-new-message"},
            json={"content": "Must not create a Run", "client_message_id": "archived-message"},
        )
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["error"]["code"] == "STATE_CONFLICT"

        with factory() as db:
            rows = list(db.scalars(select(AuditEvent).where(AuditEvent.action == "assistant.conversation.archived")))
            stored_confirmation = db.get(Confirmation, confirmation_id)
            assert len(rows) == 1
            assert rows[0].confirmation_id == confirmation_id
            assert rows[0].resource_ref == {"type": "conversation", "id": CONVERSATION_ID}
            assert stored_confirmation.status == "CONSUMED"
            assert db.scalar(select(func.count()).select_from(IdempotencyRecord).where(IdempotencyRecord.key == "archive-once")) == 1
            assert db.scalar(select(func.count()).select_from(IdempotencyRecord).where(IdempotencyRecord.key == "archived-new-message")) == 0
            assert db.scalar(select(func.count()).select_from(MessageRow)) == 0
            assert db.scalar(select(func.count()).select_from(Job)) == 0
    finally:
        engine.dispose()


def test_confirmation_is_invalidated_by_rename_and_owner_scope_is_checked(tmp_path, monkeypatch):
    engine, factory, app = _make_api(monkeypatch=monkeypatch, manifest_path=_manifest(tmp_path), database_url=f"sqlite:///{tmp_path / 'assistant.db'}")
    try:
        client = TestClient(app)
        confirmation_id = _confirmed_archive(client, key="confirmation-stale")
        detail = client.get(f"/api/v1/conversations/{CONVERSATION_ID}")
        assert detail.status_code == 200
        renamed = client.patch(
            f"/api/v1/conversations/{CONVERSATION_ID}",
            headers={"If-Match": detail.json()["data"]["etag"], "Idempotency-Key": "rename-before-archive"},
            json={"title": "Renamed"},
        )
        assert renamed.status_code == 200, renamed.text
        assert "Idempotency-Replayed" not in renamed.headers
        rename_replay = client.patch(
            f"/api/v1/conversations/{CONVERSATION_ID}",
            headers={"If-Match": detail.json()["data"]["etag"], "Idempotency-Key": "rename-before-archive"},
            json={"title": "Renamed"},
        )
        assert rename_replay.status_code == 200, rename_replay.text
        assert rename_replay.headers["Idempotency-Replayed"] == "true"
        assert rename_replay.json()["data"] == renamed.json()["data"]
        stale = client.post(
            f"/api/v1/conversations/{CONVERSATION_ID}/archive",
            headers={"Idempotency-Key": "archive-stale"}, json={"confirmation_id": confirmation_id},
        )
        assert stale.status_code == 409, stale.text
        assert stale.json()["error"]["code"] == "CONFIRMATION_INVALID"
        with factory() as db:
            assert db.scalar(select(func.count()).select_from(IdempotencyRecord).where(IdempotencyRecord.key == "archive-stale")) == 0

        app.state.actor_context = ActorContext(
            actor_id="other-actor", tenant_id=None, project_ids=[PROJECT_ID],
            auth_method="deployment_default", permissions=["assistant:use", "task:confirm"],
            policy_version="deployment-default-v1",
        )
        hidden = client.get(f"/api/v1/conversations/{CONVERSATION_ID}")
        assert hidden.status_code == 404
        hidden_preview = client.post("/api/v1/confirmations/previews", json={
            "resource_ref": {"type": "conversation", "id": CONVERSATION_ID},
            "action": "resource.archive",
        })
        assert hidden_preview.status_code == 404
    finally:
        engine.dispose()


def test_malformed_message_cursors_return_400_and_foreign_filters_are_rejected(tmp_path, monkeypatch):
    engine, _factory, app = _make_api(monkeypatch=monkeypatch, manifest_path=_manifest(tmp_path), database_url=f"sqlite:///{tmp_path / 'assistant.db'}")
    try:
        client = TestClient(app)
        wrong_shape = client.get(f"/api/v1/conversations/{CONVERSATION_ID}/messages?cursor=W10")
        assert wrong_shape.status_code == 400, wrong_shape.text
        assert wrong_shape.json()["error"]["code"] == "INVALID_CURSOR"

        non_numeric_key = _cursor({
            "v": 1, "scope": "messages", "key": "not-a-sequence",
            "filters": {"conversation_id": CONVERSATION_ID},
        })
        wrong_type = client.get(f"/api/v1/conversations/{CONVERSATION_ID}/messages?cursor={non_numeric_key}")
        assert wrong_type.status_code == 400, wrong_type.text
        assert wrong_type.json()["error"]["code"] == "INVALID_CURSOR"

        foreign_filter = _cursor({
            "v": 1, "scope": "messages", "key": 1,
            "filters": {"conversation_id": "another-conversation"},
        })
        mismatched = client.get(f"/api/v1/conversations/{CONVERSATION_ID}/messages?cursor={foreign_filter}")
        assert mismatched.status_code == 400, mismatched.text
        assert mismatched.json()["error"]["code"] == "INVALID_CURSOR"

        oversized = client.get(f"/api/v1/conversations/{CONVERSATION_ID}/messages?cursor={'a' * 513}")
        assert oversized.status_code == 422, oversized.text
    finally:
        engine.dispose()


def test_stale_rename_etag_is_rejected_without_revision_change(tmp_path, monkeypatch):
    engine, factory, app = _make_api(monkeypatch=monkeypatch, manifest_path=_manifest(tmp_path), database_url=f"sqlite:///{tmp_path / 'assistant.db'}")
    try:
        client = TestClient(app)
        stale = client.patch(
            f"/api/v1/conversations/{CONVERSATION_ID}",
            headers={"If-Match": '"old"', "Idempotency-Key": "rename-stale"},
            json={"title": "Should not win"},
        )
        assert stale.status_code == 412
        assert stale.json()["error"]["code"] == "VERSION_CONFLICT"
        with factory() as db:
            stored = db.get(ConversationRow, CONVERSATION_ID)
            assert stored.title == "Original title"
            assert stored.revision == 1
            assert db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "assistant.conversation.renamed")) == 0
    finally:
        engine.dispose()


def test_message_pages_return_latest_then_older_without_loss_and_preserve_text(tmp_path, monkeypatch):
    engine, factory, app = _make_api(monkeypatch=monkeypatch, manifest_path=_manifest(tmp_path), database_url=f"sqlite:///{tmp_path / 'assistant.db'}")
    original = "  第一行\n第二行  \n"
    try:
        with factory.begin() as db:
            db.add_all([
                MessageRow(id=f"message-{seq}", conversation_id=CONVERSATION_ID, seq=seq,
                           role="user", status="FINAL", blocks=[{"type": "text", "text": original}],
                           attachment_ids=[], created_at=datetime.now(timezone.utc))
                for seq in range(1, 202)
            ])
        client = TestClient(app)
        path = f"/api/v1/conversations/{CONVERSATION_ID}/messages"
        first_response = client.get(path, params={"limit": 200})
        assert first_response.status_code == 200, first_response.text
        first = first_response.json()
        assert [int(item["seq"]) for item in first["data"]] == list(range(2, 202))
        assert all(item["blocks"][0]["text"] == original for item in first["data"])
        assert first["page"]["has_more"] is True
        second_response = client.get(path, params={"limit": 200, "cursor": first["page"]["next_cursor"]})
        assert second_response.status_code == 200, second_response.text
        second = second_response.json()
        assert [int(item["seq"]) for item in second["data"] + first["data"]] == list(range(1, 202))
        assert second["page"]["has_more"] is False
        assert second["page"]["next_cursor"] is None
        exact = client.get(path, params={"limit": 200, "before": "201"}).json()
        assert [int(item["seq"]) for item in exact["data"]] == list(range(1, 201))
        assert exact["page"]["has_more"] is False
        assert exact["page"]["next_cursor"] is None
        default = client.get(path).json()
        assert [int(item["seq"]) for item in default["data"]] == list(range(152, 202))
        conflict = client.get(path, params={"before": "201", "cursor": first["page"]["next_cursor"]})
        assert conflict.status_code == 400
        assert conflict.json()["error"]["code"] == "INVALID_CURSOR"
    finally:
        engine.dispose()


@pytest.mark.parametrize(("status", "stored_error", "expected_code"), [
    ("CANCELED", None, None),
    ("FAILED", None, "INTERNAL_ERROR"),
    ("FAILED", {"code": "PROVIDER_TIMEOUT", "message": "timeout", "retryable": True}, "PROVIDER_TIMEOUT"),
])
def test_run_and_attempt_public_errors_distinguish_cancel_from_failure(tmp_path, monkeypatch, status, stored_error, expected_code):
    engine, factory, app = _make_api(monkeypatch=monkeypatch, manifest_path=_manifest(tmp_path), database_url=f"sqlite:///{tmp_path / 'assistant.db'}")
    try:
        now = datetime.now(timezone.utc)
        with factory.begin() as db:
            db.add(AgentRunRow(
                id="error-projection-run", conversation_id=CONVERSATION_ID, project_id=PROJECT_ID,
                input_message_id="error-input", mode="business", status=status, attempt=1,
                model_id="test-model", prompt_version="1", toolset_version="1", policy_version="deployment-default-v1",
                error=stored_error, revision=1, etag='"error-run"', created_at=now, updated_at=now,
            ))
            db.flush()
            db.add(AgentAttemptRow(id="error-projection-attempt", agent_run_id="error-projection-run",
                                   attempt=1, status=status, error=stored_error, created_at=now))
        response = TestClient(app).get("/api/v1/agent-runs/error-projection-run")
        assert response.status_code == 200, response.text
        actual = response.json()["data"]["error"]
        assert (actual["code"] if actual else None) == expected_code
        with factory() as db:
            attempt = SQLAlchemyAssistantRepository(db).get_attempt(db, "error-projection-run", 1)
            assert (attempt.error.code if attempt.error else None) == expected_code
    finally:
        engine.dispose()


def test_archive_audit_failure_rolls_back_confirmation_conversation_and_idempotency(tmp_path, monkeypatch):
    class FailArchiveAuditSession(Session):
        fail_archive_audit = False

    def _fail_archive_audit(session, _flush_context, _instances):
        if session.fail_archive_audit and any(
            isinstance(row, AuditEvent) and row.action == "assistant.conversation.archived"
            for row in session.new
        ):
            raise RuntimeError("injected archive audit failure")

    event.listen(FailArchiveAuditSession, "before_flush", _fail_archive_audit)
    engine, factory, app = _make_api(monkeypatch=monkeypatch, manifest_path=_manifest(tmp_path), session_class=FailArchiveAuditSession, database_url=f"sqlite:///{tmp_path / 'assistant.db'}")
    try:
        client = TestClient(app, raise_server_exceptions=False)
        confirmation_id = _confirmed_archive(client, key="confirmation-rollback")
        FailArchiveAuditSession.fail_archive_audit = True
        response = client.post(
            f"/api/v1/conversations/{CONVERSATION_ID}/archive",
            headers={"Idempotency-Key": "archive-rollback"},
            json={"confirmation_id": confirmation_id},
        )
        assert response.status_code == 500
        FailArchiveAuditSession.fail_archive_audit = False
        with factory() as db:
            conversation = db.get(ConversationRow, CONVERSATION_ID)
            confirmation = db.get(Confirmation, confirmation_id)
            assert conversation.status == "ACTIVE"
            assert conversation.revision == 1
            assert confirmation.status == "CONFIRMED"
            assert db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "assistant.conversation.archived")) == 0
            assert db.scalar(select(func.count()).select_from(IdempotencyRecord).where(IdempotencyRecord.key == "archive-rollback")) == 0
    finally:
        event.remove(FailArchiveAuditSession, "before_flush", _fail_archive_audit)
        engine.dispose()
