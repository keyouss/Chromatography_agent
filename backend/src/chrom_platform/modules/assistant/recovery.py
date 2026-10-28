"""Crash/restart recovery coordination for assistant runs.

Recovery reads trusted server rows and creates a new Attempt for an
interrupted Run.  It never accepts client supplied framework history, tool
results, or confirmation bindings.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import copy
from threading import RLock
from typing import Any, Callable, Mapping, Protocol
from uuid import uuid4

from .errors import AssistantError


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RecoveryRepository(Protocol):
    def get_run(self, run_id: str) -> object | None: ...
    def get_message(self, message_id: str) -> object | None: ...
    def get_attempt(self, run_id: str, attempt: int) -> object | None: ...
    def list_tool_calls(self, run_id: str) -> list[object]: ...
    def save_attempt(self, attempt: object) -> None: ...
    def save_run(self, run: object) -> None: ...


class ExecutionLedger(Protocol):
    def get(self, operation_key: str) -> object | None: ...


@dataclass(frozen=True, slots=True)
class RecoveryResult:
    run_id: str
    attempt_id: str | None
    attempt: int
    status: str
    replayed: bool = False
    existing_execution: object | None = None
    reason: str = "recovery"


class InMemoryRecoveryRepository:
    """Explicit test-only trusted-row repository."""

    is_test_only = True

    def __init__(self) -> None:
        self.runs: dict[str, object] = {}; self.messages: dict[str, object] = {}; self.attempts: dict[str, object] = {}; self.tool_calls: dict[str, list[object]] = {}
        self.shared_jobs: dict[str, dict[str, Any]] = {}; self.shared_attempts: dict[str, dict[str, Any]] = {}; self.shared_events: list[dict[str, Any]] = []; self.shared_outbox: dict[str, dict[str, Any]] = {}
        self.jobs = self.shared_jobs; self.job_attempts = self.shared_attempts; self.job_events = self.shared_events; self.outbox = self.shared_outbox
        self._lock = RLock()

    def get_run(self, run_id): return self.runs.get(run_id)
    def get_message(self, message_id): return self.messages.get(message_id)
    def get_attempt(self, run_id, attempt):
        for value in self.attempts.values():
            if _get(value, "agent_run_id", None) == run_id and _get(value, "attempt", None) == attempt: return value
        return None
    def list_tool_calls(self, run_id): return list(self.tool_calls.get(run_id, ()))
    def save_attempt(self, attempt):
        run_id, number = _get(attempt, "agent_run_id", None), _get(attempt, "attempt", None)
        if self.get_attempt(run_id, number) is None:
            self.attempts[_get(attempt, "id", str(uuid4()))] = attempt
    def save_run(self, run): self.runs[_get(run, "id")] = run

    def create_shared_recovery(self, run: object, attempt: int, *, status: str = "QUEUED") -> dict[str, Any]:
        """Create the canonical Job/Attempt/Event/Outbox set once."""
        run_id = str(_get(run, "id")); project_id = str(_get(run, "project_id", "assistant")); job_id = f"agent_job_{run_id}"
        job = self.shared_jobs.setdefault(job_id, {"id": job_id, "project_id": project_id, "owner_id": str(_get(run, "owner_id", "assistant")), "kind": "assistant", "resource_ref": {"type": "agent_run", "id": run_id}, "status": status, "attempt": 0, "revision": 1})
        key = f"{job_id}:{attempt}"
        existing = next((value for value in self.shared_attempts.values() if value["job_id"] == job_id and value["attempt"] == attempt), None)
        if existing is not None:
            # Recovery is idempotent, including after a partial write that
            # persisted the attempt/event but lost its execute outbox.
            dispatch_id = next((dispatch for dispatch, item in self.shared_outbox.items() if item.get("aggregate_id", item.get("job_id")) == job_id and item.get("event_type", "job.execute") == "job.execute" and int(item.get("payload", {}).get("attempt", item.get("attempt", -1))) == attempt), None)
            if dispatch_id is None:
                dispatch_id = f"agent_dispatch_{run_id}_{attempt}"
                self.shared_outbox[dispatch_id] = {"dispatch_id": dispatch_id, "aggregate_type": "job", "aggregate_id": job_id, "event_type": "job.execute", "payload": {"job_id": job_id, "attempt": attempt, "dispatch_id": dispatch_id}, "status": "PENDING", "attempts": 0}
            job.update({"status": status, "attempt": attempt})
            return existing
        dispatch_id = f"agent_dispatch_{run_id}_{attempt}"
        attempt_id = f"agent_job_attempt_{run_id}_{attempt}"
        job.update({"status": status, "attempt": attempt, "revision": int(job.get("revision", 1)) + 1})
        self.shared_attempts[attempt_id] = {"id": attempt_id, "job_id": job_id, "attempt": attempt, "status": status, "resume_mode": "restart"}
        seq = max((int(x["seq"]) for x in self.shared_events if x["job_id"] == job_id), default=-1) + 1
        self.shared_events.append({"id": f"agent_job_event_{run_id}_{attempt}", "job_id": job_id, "stream_id": job_id, "seq": seq, "type": "job.queued" if status == "QUEUED" else f"job.{status.lower()}", "data": {"status": status, "attempt": attempt}})
        self.shared_outbox[dispatch_id] = {"dispatch_id": dispatch_id, "aggregate_type": "job", "aggregate_id": job_id, "event_type": "job.execute", "payload": {"job_id": job_id, "attempt": attempt, "dispatch_id": dispatch_id}, "status": "PENDING", "attempts": 0}
        return self.shared_attempts[attempt_id]

    def resume_confirmation_atomic(self, run_id: str, confirmation_id: str, *, now: datetime) -> tuple[object, object, bool]:
        with self._lock:
            run = self.runs.get(run_id)
            if run is None:
                raise AssistantError(404, "RESOURCE_NOT_FOUND", "AgentRun 不存在")
            if _get(run, "status") != "WAITING_CONFIRMATION":
                current = self.get_attempt(run_id, int(_get(run, "attempt", 0)))
                completed = next((
                    value for value in self.list_tool_calls(run_id)
                    if _get(value, "confirmation_id", None) == confirmation_id
                    and _get(value, "status", None) == "SUCCEEDED"
                ), None)
                if completed is not None and current is not None and int(_get(run, "attempt", 0)) > 1:
                    return current, run, True
                raise AssistantError(409, "CONFIRMATION_INVALID", "Run 当前不在等待确认状态")
            if not any(_get(x, "confirmation_id", None) == confirmation_id for x in _get(run, "pending_actions", ())):
                raise AssistantError(409, "CONFIRMATION_INVALID", "确认未匹配服务端 pending action")
            number = int(_get(run, "attempt", 1)) + 1
            existing = self.get_attempt(run_id, number)
            if existing is not None:
                return existing, run, True
            attempt = _copy_attempt(self.get_attempt(run_id, number - 1), run_id, number, now)
            self.save_attempt(attempt)
            attempt = self.get_attempt(run_id, number) or attempt
            self.create_shared_recovery(run, number, status="QUEUED")
            updated = _copy_object(run, attempt=number, status="RUNNING")
            self.save_run(updated)
            return attempt, updated, False


class InMemoryExecutionLedger:
    is_test_only = True
    def __init__(self): self.records: dict[str, object] = {}
    def get(self, operation_key): return self.records.get(operation_key)


class RecoveryCoordinator:
    def __init__(self, repository: RecoveryRepository, *, ledger: ExecutionLedger | None = None, event_store: object | None = None, attempt_factory: Callable[[object, int], object] | None = None, now: Callable[[], datetime] = _now) -> None:
        self.repository, self.ledger, self.event_store, self.attempt_factory, self.now = repository, ledger, event_store, attempt_factory, now
        self._lock = RLock(); self._results: dict[str, RecoveryResult] = {}

    def recover(self, run_id: str, *, reason: str = "recovery") -> RecoveryResult:
        if reason != "recovery":
            raise AssistantError(400, "VALIDATION_FAILED", "恢复 reason 必须为 recovery")
        with self._lock:
            existing = self._results.get(run_id)
            if existing is not None: return replace(existing, replayed=True)
            run = self.repository.get_run(run_id)
            if run is None: raise AssistantError(404, "RESOURCE_NOT_FOUND", "AgentRun 不存在")
            current_attempt = int(_get(run, "attempt", 1))
            current_row = self.repository.get_attempt(run_id, current_attempt)
            if current_attempt > 1 and current_row is not None and _get(current_row, "status", None) in {"QUEUED", "RUNNING", "WAITING_CONFIRMATION"}:
                result = RecoveryResult(run_id, _get(current_row, "id", None), current_attempt, _get(run, "status", "QUEUED"), replayed=True, reason=reason)
                self._results[run_id] = result
                return result
            new_number = current_attempt + 1
            # The persisted attempt is the idempotency record. This also
            # survives a process restart where _results is empty.
            persisted_attempt = self.repository.get_attempt(run_id, new_number)
            if persisted_attempt is not None:
                result = RecoveryResult(run_id, _get(persisted_attempt, "id", None), new_number, _get(persisted_attempt, "status", "QUEUED"), replayed=True, reason=reason)
                self._results[run_id] = result
                return result
            status = _get(run, "status")
            if status != "INTERRUPTED":
                raise AssistantError(409, "RECOVERY_NOT_ALLOWED", f"Run 当前状态 {status} 不可恢复")
            # Read trusted history before creating the attempt.  Accessing the
            # rows is intentional even when the selected handler later fails.
            message_id = _get(run, "input_message_id", None)
            if message_id is not None and self.repository.get_message(message_id) is None:
                raise AssistantError(409, "RECOVERY_STATE_INVALID", "Run 的输入 Message 不存在")
            tool_calls = self.repository.list_tool_calls(run_id)
            # Event rows and the execution ledger are trusted evidence used by
            # the new Attempt; neither is reconstructed from client payloads.
            if self.event_store is not None:
                list_after = getattr(self.event_store, "list_after", None)
                if callable(list_after):
                    last_seq = int(_get(run, "last_event_seq", 0) or 0)
                    list_after("agent_run", run_id, last_seq, 1_000_000)
            existing_execution = None
            if self.ledger is not None:
                lookup = getattr(self.ledger, "get", None)
                for tool_call in tool_calls:
                    operation_key = _get(tool_call, "operation_key", None)
                    if operation_key and callable(lookup):
                        existing_execution = lookup(operation_key)
                        if existing_execution is not None:
                            break
            if self.attempt_factory is not None:
                attempt = self.attempt_factory(run, new_number)
            else:
                attempt = _copy_attempt(self.repository.get_attempt(run_id, current_attempt), run_id, new_number, self.now())
            self.repository.save_attempt(attempt)
            attempt = self.repository.get_attempt(run_id, new_number) or attempt
            shared = getattr(self.repository, "create_shared_recovery", None)
            if callable(shared):
                shared(run, new_number, status="QUEUED")
            updated = _copy_object(run, attempt=new_number, status="QUEUED")
            self.repository.save_run(updated)
            result = RecoveryResult(run_id, _get(attempt, "id", None), new_number, "QUEUED", existing_execution=existing_execution, reason=reason)
            self._results[run_id] = result
            return result

    recover_run = recover

    def resume_confirmation(self, run_id: str, confirmation_id: str) -> RecoveryResult:
        """Resume only a server-persisted pending confirmation binding."""
        if not confirmation_id or not isinstance(confirmation_id, str):
            raise AssistantError(400, "VALIDATION_FAILED", "confirmation_id 无效")
        run = self.repository.get_run(run_id)
        if run is None: raise AssistantError(404, "RESOURCE_NOT_FOUND", "AgentRun 不存在")
        if _get(run, "status") != "WAITING_CONFIRMATION":
            atomic = getattr(self.repository, "resume_confirmation_atomic", None)
            if callable(atomic):
                try:
                    attempt, updated, replayed = atomic(run_id, confirmation_id, now=self.now())
                except AssistantError:
                    replayed = False
                if replayed:
                    return RecoveryResult(run_id, _get(attempt, "id", None), int(_get(updated, "attempt", _get(attempt, "attempt", 1))), "RUNNING", replayed=True, reason="confirmation")
            raise AssistantError(409, "CONFIRMATION_INVALID", "Run 当前不在等待确认状态")
        pending = _get(run, "pending_actions", ())
        matched = [x for x in pending if _get(x, "confirmation_id", None) == confirmation_id]
        if not matched:
            raise AssistantError(409, "CONFIRMATION_INVALID", "确认未匹配服务端 pending action")
        atomic = getattr(self.repository, "resume_confirmation_atomic", None)
        if callable(atomic):
            attempt, updated, replayed = atomic(run_id, confirmation_id, now=self.now())
            return RecoveryResult(run_id, _get(attempt, "id", None), int(_get(updated, "attempt", _get(attempt, "attempt", 1))), "RUNNING", replayed=replayed, reason="confirmation")
        current_attempt = int(_get(run, "attempt", 1)); next_attempt = current_attempt + 1
        attempt = _copy_attempt(self.repository.get_attempt(run_id, current_attempt), run_id, next_attempt, self.now())
        self.repository.save_attempt(attempt)
        shared = getattr(self.repository, "create_shared_recovery", None)
        if callable(shared):
            shared(run, next_attempt, status="QUEUED")
        self.repository.save_run(_copy_object(run, attempt=next_attempt, status="RUNNING"))
        return RecoveryResult(run_id, _get(attempt, "id", None), next_attempt, "RUNNING", reason="confirmation")

    resume = resume_confirmation

    def operation_result(self, operation_key: str) -> object | None:
        """Read the execution ledger so recovery never repeats a side effect."""
        if self.ledger is None: return None
        return self.ledger.get(operation_key)

    def cancel_agent(self, run_id: str, *, reason: str | None = None) -> object:
        """Cancel the AgentRun only; linked business Jobs remain untouched."""
        with self._lock:
            run = self.repository.get_run(run_id)
            if run is None: raise AssistantError(404, "RESOURCE_NOT_FOUND", "AgentRun 不存在")
            updated = _copy_object(run, status="CANCELED", error={"code": "CANCELED", "message": reason or "用户取消", "retryable": False})
            self.repository.save_run(updated)
            return updated

    cancel = cancel_agent


def _get(value: object, name: str, default: Any = ...):
    if isinstance(value, Mapping):
        if name in value: return value[name]
    elif hasattr(value, name): return getattr(value, name)
    if default is not ...: return default
    raise AttributeError(name)


def _copy_object(value: object, **updates: Any) -> object:
    if hasattr(value, "model_copy"):
        return value.model_copy(update=updates)
    if hasattr(value, "_replace"):
        return value._replace(**updates)
    if isinstance(value, Mapping):
        return {**value, **updates}
    try:
        return replace(value, **updates)
    except TypeError:
        clone = copy.copy(value)
        if hasattr(clone, "__dict__"):
            clone.__dict__.update(updates)
        return clone


def _copy_attempt(previous: object | None, run_id: str, attempt: int, now: datetime) -> object:
    if previous is not None:
        return _copy_object(previous, id=f"attempt_{uuid4().hex}", agent_run_id=run_id, attempt=attempt, status="QUEUED", created_at=now, started_at=None, finished_at=None, error=None)
    return {"id": f"attempt_{uuid4().hex}", "agent_run_id": run_id, "attempt": attempt, "status": "QUEUED", "created_at": now}


__all__ = ["ExecutionLedger", "InMemoryExecutionLedger", "InMemoryRecoveryRepository", "RecoveryCoordinator", "RecoveryRepository", "RecoveryResult"]
