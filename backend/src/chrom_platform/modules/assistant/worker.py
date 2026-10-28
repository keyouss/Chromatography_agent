"""Agent Worker candidate with queue/persistence-independent boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import inspect
from typing import Any, Mapping

from .errors import AssistantError
from .runtime import AgentRuntime, RuntimeResult


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _attr(value: object, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


@dataclass(frozen=True, slots=True)
class AgentWorkerMessage:
    """The complete queue payload: stable IDs only."""

    run_id: str
    attempt: int
    dispatch_id: str

    def __post_init__(self) -> None:
        if type(self.run_id) is not str or not self.run_id.strip() or type(self.dispatch_id) is not str or not self.dispatch_id.strip() or type(self.attempt) is not int or isinstance(self.attempt, bool) or self.attempt < 1:
            raise ValueError("run_id, positive attempt and dispatch_id are required")

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "AgentWorkerMessage":
        if not isinstance(payload, Mapping) or set(payload) != {"run_id", "attempt", "dispatch_id"}:
            raise ValueError("Agent Worker message may contain only run_id, attempt and dispatch_id")
        if type(payload["run_id"]) is not str or type(payload["dispatch_id"]) is not str or type(payload["attempt"]) is not int or isinstance(payload["attempt"], bool):
            raise ValueError("run_id, attempt and dispatch_id have invalid types")
        return cls(payload["run_id"], payload["attempt"], payload["dispatch_id"])

    def as_dict(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "attempt": self.attempt, "dispatch_id": self.dispatch_id}


WorkerMessage = AgentWorkerMessage


@dataclass(frozen=True, slots=True)
class AgentLease:
    run_id: str
    attempt: int
    dispatch_id: str
    fencing_token: int
    lease_expires_at: datetime | None = None
    event_emitted: bool = False
    execution_token: str | None = None

    def __post_init__(self) -> None:
        if type(self.run_id) is not str or not self.run_id or type(self.dispatch_id) is not str or not self.dispatch_id:
            raise ValueError("lease identifiers are required")
        if type(self.attempt) is not int or self.attempt < 1 or type(self.fencing_token) is not int or self.fencing_token < 1:
            raise ValueError("lease attempt and fencing token must be positive integers")


@dataclass(frozen=True, slots=True)
class AgentWorkerResult:
    status: str
    result: object | None = None
    error: Mapping[str, Any] | None = None
    replayed: bool = False
    fencing_token: int | None = None


WorkerResult = AgentWorkerResult


class AgentWorker:
    """Claim and finish use short transactions; provider calls happen outside."""

    def __init__(self, *, runtime: AgentRuntime, store: object, event_store: object | None = None, lease_seconds: int = 60, clock=_now) -> None:
        self.runtime, self.store, self.event_store, self.lease_seconds, self.clock = runtime, store, event_store, max(1, int(lease_seconds)), clock

    def handle(self, message: AgentWorkerMessage | Mapping[str, Any]) -> AgentWorkerResult:
        if not isinstance(message, AgentWorkerMessage):
            message = AgentWorkerMessage.from_payload(message)
        run = self._load_run(message.run_id)
        if run is None:
            return AgentWorkerResult("FAILED", error={"code": "RESOURCE_NOT_FOUND", "message": "助手 Run 不存在", "retryable": False})
        status = str(_attr(run, "status", "QUEUED"))
        if status in {"SUCCEEDED", "FAILED", "CANCELED", "TIMED_OUT", "INTERRUPTED"}:
            return AgentWorkerResult(status, replayed=True)
        if status == "WAITING_CONFIRMATION":
            # Confirmation is a persisted pause, not a queue retry. Keeping
            # the message pending here would let a redelivery run the LLM or
            # tools again before the user confirms the exact binding.
            return AgentWorkerResult(status, replayed=True)
        persisted_attempt = _attr(run, "attempt")
        persisted_dispatch = _attr(run, "dispatch_id")
        if persisted_attempt is not None and int(persisted_attempt) != message.attempt:
            return AgentWorkerResult(status, replayed=True)
        if persisted_dispatch is not None and str(persisted_dispatch) != message.dispatch_id:
            return AgentWorkerResult(status, replayed=True)
        if self._cancel_requested(message.run_id, run):
            self._cancel_before_claim(message, error={"code": "CANCELED_BEFORE_START", "message": "助手运行已取消", "retryable": False})
            return AgentWorkerResult("CANCELED", replayed=False)
        lease = self._claim(message)
        if lease is None:
            return AgentWorkerResult(status, replayed=True)
        if self._cancel_requested(message.run_id, run):
            self._finish(message, lease.fencing_token, "CANCELED", error={"code": "CANCELED_BEFORE_START", "message": "助手运行已取消", "retryable": False})
            return AgentWorkerResult("CANCELED", fencing_token=lease.fencing_token)
        if not self._event_emitted(lease):
            self._event(message.run_id, "run.started", {"status": "RUNNING", "attempt": message.attempt})
        # The transaction used by _claim has ended before this call. Runtime
        # only loads trusted server history and has no DB Session reference.
        runtime_result = self._run_runtime(message, lease)
        if self._cancel_requested(message.run_id, None):
            finished = self._finish(message, lease.fencing_token, "CANCELED", error={"code": "CANCELED_DURING_RUN", "message": "助手运行已取消", "retryable": False})
            # A lease may have been fenced while cancellation was observed.
            # Do not append a cancellation event for a worker whose state
            # transition was rejected.
            if not self._finish_rejected(finished, expected_status="CANCELED") and not self._event_emitted(finished):
                self._event(message.run_id, "run.canceled", {"attempt": message.attempt})
            return AgentWorkerResult("CANCELED", replayed=False, fencing_token=lease.fencing_token)
        status = runtime_result.status
        error = runtime_result.error
        if status not in {"SUCCEEDED", "WAITING_CONFIRMATION", "FAILED", "TIMED_OUT", "CANCELED"}:
            status = "FAILED"
            error = {"code": "ASSISTANT_RUNTIME_FAILED", "message": "助手运行失败", "retryable": True}
        finished = self._finish(message, lease.fencing_token, status, result=runtime_result if status in {"SUCCEEDED", "WAITING_CONFIRMATION"} else None, error=error)
        if self._finish_rejected(finished, expected_status=status):
            # A lease may have been fenced while the provider was running. Do
            # not emit a terminal success/failure event for a stale owner.
            return AgentWorkerResult("RUNNING", error={"code": "STALE_FENCING_TOKEN", "message": "助手运行租约已被其他 Worker 接管", "retryable": True}, replayed=True, fencing_token=lease.fencing_token)
        event_type = {"SUCCEEDED": "run.completed", "FAILED": "run.failed", "CANCELED": "run.canceled", "TIMED_OUT": "run.interrupted", "INTERRUPTED": "run.interrupted", "WAITING_CONFIRMATION": "run.waiting_confirmation"}.get(status, "run.failed")
        if not self._event_emitted(finished):
            self._event(message.run_id, event_type, {"status": status, "attempt": message.attempt, **({"error": error} if error else {})})
        return AgentWorkerResult(status, runtime_result if status == "SUCCEEDED" else None, error, fencing_token=lease.fencing_token)

    process = handle
    run = handle

    def _load_run(self, run_id: str) -> object | None:
        method = getattr(self.store, "load_run", None) or getattr(self.store, "get_run", None)
        return method(run_id) if callable(method) else None

    def _claim(self, message: AgentWorkerMessage) -> AgentLease | None:
        method = getattr(self.store, "claim", None) or getattr(self.store, "claim_run", None)
        if not callable(method):
            method = getattr(self.store, "mark_running", None)
        if not callable(method):
            return None
        call = {"run_id": message.run_id, "attempt": message.attempt, "dispatch_id": message.dispatch_id, "lease_seconds": self.lease_seconds}
        try:
            signature = inspect.signature(method)
            names = set(signature.parameters)
        except (TypeError, ValueError):
            names = set()
        if names and "run_id" not in names and not any(p.kind is inspect.Parameter.VAR_KEYWORD for p in inspect.signature(method).parameters.values()):
            item = self._load_run(message.run_id)
            result = self._invoke_port(method, {"item": item, "expected_attempt": message.attempt, "expected_dispatch_id": message.dispatch_id}) if item is not None else None
        else:
            result = self._short_call(method, **call)
        if result is False or result is None:
            return None
        if (isinstance(result, Mapping) and result.get("claimed") is False) or _attr(result, "claimed", True) is False:
            return None
        if result is True:
            return AgentLease(message.run_id, message.attempt, message.dispatch_id, 1, self.clock() + timedelta(seconds=self.lease_seconds), False, None)
        token = int(_attr(result, "fencing_token", _attr(result, "fence", 1)) or 1)
        return AgentLease(message.run_id, message.attempt, message.dispatch_id, token, _attr(result, "lease_expires_at", None), bool(_attr(result, "event_emitted", False)), _attr(result, "execution_token", None))

    def _run_runtime(self, message: AgentWorkerMessage, lease: AgentLease | None = None) -> RuntimeResult:
        try:
            method = getattr(self.runtime, "run", None) or getattr(self.runtime, "execute")
            kwargs: dict[str, Any] = {"run_id": message.run_id, "attempt": message.attempt}
            if lease is not None:
                try:
                    signature = inspect.signature(method)
                    accepts_kwargs = any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values())
                    if accepts_kwargs or "execution_token" in signature.parameters:
                        kwargs["execution_token"] = getattr(lease, "execution_token", None)
                    if accepts_kwargs or "fencing_token" in signature.parameters:
                        kwargs["fencing_token"] = lease.fencing_token
                except (TypeError, ValueError):
                    pass
            value = method(**kwargs)
            if inspect.isawaitable(value):
                # The production handler owns the long-lived async loop. This
                # old synchronous candidate must not invent a per-Run loop or
                # treat an unawaited coroutine as a successful runtime result.
                close = getattr(value, "close", None)
                if callable(close):
                    close()
                return RuntimeResult("FAILED", error={"code": "ASYNC_WORKER_REQUIRED", "message": "原生助手需要异步 Worker", "retryable": False})
            return value
        except AssistantError as exc:
            return RuntimeResult("FAILED", error={"code": exc.payload.code, "message": exc.payload.message, "retryable": exc.payload.retryable})
        except Exception:
            return RuntimeResult("FAILED", error={"code": "ASSISTANT_RUNTIME_FAILED", "message": "助手运行失败", "retryable": True})

    def _finish(self, message: AgentWorkerMessage, token: int, status: str, *, result: object | None = None, error: Mapping[str, Any] | None = None) -> object | None:
        method = getattr(self.store, "finish", None) or getattr(self.store, "finish_run", None)
        if not callable(method):
            return None
        return self._short_call(method, run_id=message.run_id, attempt=message.attempt, dispatch_id=message.dispatch_id, fencing_token=token, status=status, result=result, error=dict(error) if error else None)

    def _cancel_before_claim(self, message: AgentWorkerMessage, *, error: Mapping[str, Any]) -> object | None:
        method = getattr(self.store, "cancel_before_start", None)
        if callable(method):
            return self._short_call(method, run_id=message.run_id, attempt=message.attempt, dispatch_id=message.dispatch_id, error=dict(error))
        run = self._load_run(message.run_id)
        token = int(_attr(run, "fencing_token", 1) or 1)
        return self._finish(message, max(1, token), "CANCELED", error=error)

    def _short_call(self, method: Any, **kwargs: Any) -> Any:
        transaction = getattr(self.store, "transaction", None)
        if callable(transaction):
            try:
                manager = transaction()
            except TypeError:
                manager = None
            if manager is not None:
                with manager as session:
                    return self._invoke_port(method, kwargs, session=session)
        return self._invoke_port(method, kwargs)

    @staticmethod
    def _invoke_port(method: Any, kwargs: Mapping[str, Any], *, session: object | None = None) -> Any:
        """Select a port signature without retrying an internal TypeError."""
        supplied = dict(kwargs)
        if session is not None:
            supplied["session"] = session
        try:
            signature = inspect.signature(method)
        except (TypeError, ValueError):
            return method(**supplied)
        params = signature.parameters.values()
        if any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in params):
            return method(**supplied)
        selected = {parameter.name: supplied[parameter.name] for parameter in params if parameter.name in supplied and parameter.kind is not inspect.Parameter.VAR_POSITIONAL}
        return method(**selected)

    @staticmethod
    def _finish_rejected(value: object | None, *, expected_status: str | None = None) -> bool:
        if value is False:
            return True
        if isinstance(value, Mapping):
            if value.get("accepted") is False or value.get("fencing_valid") is False or value.get("stale") is True:
                return True
            return expected_status is not None and value.get("status") is not None and str(value.get("status")) != expected_status
        if getattr(value, "accepted", True) is False or getattr(value, "fencing_valid", True) is False or getattr(value, "stale", False) is True:
            return True
        return expected_status is not None and getattr(value, "status", None) is not None and str(getattr(value, "status")) != expected_status

    @staticmethod
    def _event_emitted(value: object | None) -> bool:
        return bool(value.get("event_emitted")) if isinstance(value, Mapping) else bool(getattr(value, "event_emitted", False))

    def _cancel_requested(self, run_id: str, run: object | None) -> bool:
        method = getattr(self.store, "is_cancel_requested", None)
        if callable(method):
            try:
                return bool(method(run_id))
            except Exception:
                # Cancellation checks fail closed: running a provider/tool
                # call after a lost persistence check could create a side
                # effect that the caller explicitly requested to stop.
                return True
        return str(_attr(run, "status", "")) == "CANCEL_REQUESTED" if run is not None else False

    def _event(self, run_id: str, event_type: str, data: Mapping[str, Any]) -> None:
        append = getattr(self.event_store, "append", None) if self.event_store is not None else None
        if callable(append):
            append("agent_run", run_id, event_type, dict(data), agent_run_id=run_id)


AssistantWorker = AgentWorker
AgentWorkerHandler = AgentWorker


__all__ = ["AgentLease", "AgentWorker", "AgentWorkerHandler", "AgentWorkerMessage", "AgentWorkerResult", "AssistantWorker", "WorkerMessage", "WorkerResult"]
