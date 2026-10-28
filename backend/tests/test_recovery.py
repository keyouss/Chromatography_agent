from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from chrom_platform.modules.assistant.errors import AssistantError
from chrom_platform.modules.assistant.recovery import InMemoryRecoveryRepository, RecoveryCoordinator


def test_recovery_creates_one_new_attempt_and_is_idempotent() -> None:
    repo = InMemoryRecoveryRepository()
    old = SimpleNamespace(id="a1", agent_run_id="run-1", attempt=1, status="INTERRUPTED", created_at=datetime.now(timezone.utc))
    run = SimpleNamespace(id="run-1", input_message_id="msg-1", attempt=1, status="INTERRUPTED", pending_actions=[])
    repo.runs[run.id] = run; repo.messages["msg-1"] = object(); repo.attempts[old.id] = old
    coordinator = RecoveryCoordinator(repo)
    first = coordinator.recover("run-1")
    second = coordinator.recover("run-1")
    assert first.attempt == second.attempt == 2
    assert second.replayed
    assert len(repo.attempts) == 2
    shared_job = next(iter(repo.shared_jobs.values()))
    assert shared_job["attempt"] == 2 and shared_job["status"] == "QUEUED"
    assert any(value["attempt"] == 2 and value["status"] == "QUEUED" for value in repo.shared_attempts.values())
    assert any(item.get("data", {}).get("attempt") == 2 for item in repo.shared_events)
    assert any(item.get("payload", {}).get("attempt") == 2 for item in repo.shared_outbox.values())


def test_confirmation_resume_uses_only_server_pending_binding_and_cancel_keeps_job_external() -> None:
    repo = InMemoryRecoveryRepository()
    run = SimpleNamespace(id="run-1", input_message_id="msg-1", attempt=1, status="WAITING_CONFIRMATION", pending_actions=[SimpleNamespace(confirmation_id="confirm-1")])
    repo.runs[run.id] = run; repo.messages["msg-1"] = object()
    coordinator = RecoveryCoordinator(repo)
    assert coordinator.resume_confirmation("run-1", "confirm-1").reason == "confirmation"
    with pytest.raises(AssistantError):
        coordinator.resume_confirmation("run-1", "confirm-2")
    canceled = coordinator.cancel_agent("run-1", reason="stop")
    assert canceled.status == "CANCELED"
    assert not hasattr(canceled, "job_status")


def test_recovery_is_idempotent_from_persisted_attempt_after_coordinator_restart() -> None:
    repo = InMemoryRecoveryRepository()
    old = {"id": "a1", "agent_run_id": "run-1", "attempt": 1, "status": "INTERRUPTED"}
    run = {"id": "run-1", "input_message_id": None, "attempt": 1, "status": "INTERRUPTED", "pending_actions": []}
    repo.runs[run["id"]] = run
    repo.attempts[old["id"]] = old
    first = RecoveryCoordinator(repo).recover("run-1")
    replay = RecoveryCoordinator(repo).recover("run-1")
    assert replay.replayed and replay.attempt_id == first.attempt_id
    assert len(repo.attempts) == 2


def test_recovery_repairs_missing_execute_outbox_without_repeating_attempt_or_event() -> None:
    repo = InMemoryRecoveryRepository()
    run = SimpleNamespace(id="run-repair", project_id="p1", owner_id="assistant")
    first = repo.create_shared_recovery(run, 1)
    event_count = len(repo.shared_events)
    repo.shared_outbox.clear()
    repaired = repo.create_shared_recovery(run, 1)
    assert repaired is first
    assert len(repo.shared_events) == event_count
    matching = [item for item in repo.shared_outbox.values() if item["aggregate_id"] == "agent_job_run-repair" and item["payload"]["attempt"] == 1]
    assert len(matching) == 1
