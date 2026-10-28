"""Safe, saved Context Inspector snapshots.

This module intentionally has no provider/runtime dependency.  It records the
two observable boundaries separately and exposes metadata/content permission
checks as ports for the eventual HTTP adapter.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import re
from threading import RLock
from typing import Any, Callable, Mapping, Protocol
from uuid import uuid4

from .errors import AssistantError, public_error
from .schemas import ToolCall

MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024
_SENSITIVE_NAME = re.compile(r"(?:key|api[_-]?key|authorization|cookie|set[_-]?cookie|secret|token|password|credential|credentials|access[_-]?token|refresh[_-]?token)", re.I)
_HIDDEN_NAME = re.compile(r"(?:reasoning|thinking|chain[_-]?of[_-]?thought|hidden|opaque|internal[_-]?recovery)", re.I)
_SECRET_VALUE = re.compile(
    r"(?ix)"
    r"(?:\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]+)"
    r"|(?:\b(?:api[-_ ]?key|access[-_ ]?token|refresh[-_ ]?token|secret|password|authorization|cookie|set[-_ ]?cookie)\s*[=:]\s*[^\s,;]+)"
    r"|(?:\bsk[-_][A-Za-z0-9._-]+)"
    r"|(?:\b[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b)"
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _safe_text(value: str) -> str:
    return _SECRET_VALUE.sub("[REDACTED]", value)


def redact(value: Any, *, _depth: int = 0, _stats: dict[str, int] | None = None) -> Any:
    """Recursively redact secret fields and remove hidden reasoning fields."""
    stats = _stats if _stats is not None else {"secret": 0, "hidden": 0}
    if _depth > 32:
        return "[REDACTED_DEPTH]"
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            # Usage counters and trace coordinates are public telemetry; the
            # substring ``token`` in ``input_tokens`` must not make them look
            # like credentials.
            if key.lower() in {"input_tokens", "output_tokens", "total_tokens", "cached_input_tokens", "max_output_tokens", "request_index", "transport_attempt"}:
                output[key] = redact(raw_value, _depth=_depth + 1, _stats=stats)
                continue
            if _HIDDEN_NAME.search(key):
                stats["hidden"] += 1
                continue
            if _SENSITIVE_NAME.search(key) or key.lower() in {"key", "cookie", "authorization", "set-cookie"}:
                stats["secret"] += 1
                output[key] = "[REDACTED]"
                continue
            output[key] = redact(raw_value, _depth=_depth + 1, _stats=stats)
        return output
    if isinstance(value, (list, tuple)):
        return [redact(item, _depth=_depth + 1, _stats=stats) for item in value]
    if isinstance(value, str):
        return _safe_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


def _bounded(value: Any) -> tuple[Any, bool, int | None]:
    """Bound the serialized redacted payload and mark the cut position."""
    encoded = _canonical(value).encode("utf-8")
    if len(encoded) <= MAX_SNAPSHOT_BYTES:
        return value, False, None
    # Preserve valid JSON and structure by progressively shortening strings;
    # if a single scalar is huge, replace its tail with an explicit marker.
    def trim(node: Any) -> Any:
        if isinstance(node, str):
            budget = max(0, MAX_SNAPSHOT_BYTES // 8)
            if len(node.encode("utf-8")) <= budget:
                return node
            marker = "…[TRUNCATED]"
            end = min(len(node), budget)
            while end and len((node[:end] + marker).encode("utf-8")) > budget:
                end -= 1
            return node[:end] + marker
        if isinstance(node, list):
            return [trim(x) for x in node[:1000]]
        if isinstance(node, dict):
            return {k: trim(v) for k, v in list(node.items())[:1000]}
        return node
    candidate = trim(value)
    # At worst retain a valid marker rather than falsely reporting full data.
    while len(_canonical(candidate).encode("utf-8")) > MAX_SNAPSHOT_BYTES:
        candidate = {"truncated": True, "content": "[TRUNCATED]"}
    return candidate, True, len(encoded)


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class LlmCallDetail:
    id: str
    agent_run_id: str
    agent_attempt_id: str
    request_index: int
    transport_attempt: int
    model_id: str
    project_id: str | None = None
    status: str = "CREATED"
    provider_configuration_id: str | None = None
    provider_configuration_revision: str | None = None
    logical_context_snapshot_id: str | None = None
    provider_request_snapshot_id: str | None = None
    model_parameters_summary: Mapping[str, Any] = field(default_factory=dict)
    usage: Usage = field(default_factory=Usage)
    estimates: Mapping[str, Any] = field(default_factory=dict)
    latency_ms: int | None = None
    response_summary: Mapping[str, Any] | None = None
    error: Mapping[str, Any] | None = None
    trace_id: str | None = None
    span_id: str | None = None
    created_at: datetime = field(default_factory=_now)
    finished_at: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        usage = self.usage if isinstance(self.usage, Mapping) else {
            "input_tokens": self.usage.input_tokens,
            "output_tokens": self.usage.output_tokens,
            "cached_input_tokens": self.usage.cached_input_tokens,
        }
        return {
            "id": self.id, "agent_run_id": self.agent_run_id, "agent_attempt_id": self.agent_attempt_id,
            "request_index": self.request_index, "transport_attempt": self.transport_attempt,
            "provider_configuration_id": self.provider_configuration_id,
            "provider_configuration_revision": self.provider_configuration_revision,
            "model_id": self.model_id, "status": self.status,
            "project_id": self.project_id,
            "logical_context_snapshot_id": self.logical_context_snapshot_id,
            "provider_request_snapshot_id": self.provider_request_snapshot_id,
            "model_parameters_summary": dict(self.model_parameters_summary),
            "usage": dict(usage),
            "estimates": dict(self.estimates), "latency_ms": self.latency_ms,
            "response_summary": dict(self.response_summary) if self.response_summary else None,
            "error": dict(self.error) if self.error else None,
            "trace_id": self.trace_id, "span_id": self.span_id,
            "created_at": self.created_at.isoformat(), "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }


@dataclass(frozen=True, slots=True)
class ContextSnapshot:
    id: str
    llm_call_id: str
    view: str
    capture_state: str
    capture_mode: str
    payload_schema_version: str
    payload: Any = None
    sources: tuple[Mapping[str, Any], ...] = ()
    redactions: Mapping[str, int] = field(default_factory=dict)
    truncated: bool = False
    truncation_offset: int | None = None
    hash_scope: str = "redacted_payload"
    sha256: str | None = None
    created_at: datetime = field(default_factory=_now)
    expires_at: datetime | None = None
    project_id: str = "unknown"

    def metadata(self) -> dict[str, Any]:
        return {
            "id": self.id, "llm_call_id": self.llm_call_id, "project_id": self.project_id, "view": self.view,
            "capture_state": self.capture_state, "capture_mode": self.capture_mode,
            "payload_schema_version": self.payload_schema_version,
            "sources": [redact(dict(x)) for x in self.sources], "redactions": dict(self.redactions),
            "truncated": self.truncated, "truncation_offset": self.truncation_offset,
            "hash_scope": self.hash_scope, "sha256": self.sha256,
            "created_at": self.created_at.isoformat(), "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }

    def content_view(self, now: datetime | None = None) -> dict[str, Any]:
        current = now or _now()
        if self.expires_at and current >= self.expires_at:
            raise AssistantError(410, "TRACE_CONTENT_EXPIRED", "Context 内容已过期", details={"snapshot_id": self.id})
        if self.capture_state == "EXPIRED":
            raise AssistantError(410, "TRACE_CONTENT_EXPIRED", "Context 内容已过期", details={"snapshot_id": self.id})
        return {**self.metadata(), "payload": redact(self.payload) if self.payload is not None else None}


class TraceStore(Protocol):
    def save_snapshot(self, snapshot: ContextSnapshot) -> None: ...
    def get_snapshot(self, snapshot_id: str) -> ContextSnapshot | None: ...
    def save_llm_call(self, call: LlmCallDetail) -> None: ...
    def get_llm_call(self, call_id: str) -> LlmCallDetail | None: ...
    def save_tool_call(self, call: ToolCall, *, execution_token: str | None = None, fencing_token: int | None = None) -> None: ...
    def get_tool_call(self, call_id: str) -> ToolCall | None: ...
    def list_tool_calls(self, *, run_id: str, limit: int = 50) -> list[ToolCall]: ...
    def record_tool_call_event(self, call: ToolCall, event_type: str, data: Mapping[str, Any], *, execution_token: str | None = None, fencing_token: int | None = None) -> None: ...


class InMemoryTraceStore:
    is_test_only = True

    def __init__(self) -> None:
        self._lock = RLock(); self.snapshots: dict[str, ContextSnapshot] = {}; self.calls: dict[str, LlmCallDetail] = {}; self.tool_calls: dict[str, ToolCall] = {}

    def save_snapshot(self, snapshot: ContextSnapshot) -> None:
        with self._lock: self.snapshots[snapshot.id] = snapshot
    def get_snapshot(self, snapshot_id: str) -> ContextSnapshot | None: return self.snapshots.get(snapshot_id)
    def save_llm_call(self, call: LlmCallDetail) -> None:
        with self._lock: self.calls[call.id] = call
    def get_llm_call(self, call_id: str) -> LlmCallDetail | None: return self.calls.get(call_id)
    def save_tool_call(self, call: ToolCall, *, execution_token: str | None = None, fencing_token: int | None = None) -> None:
        with self._lock:
            current = self.tool_calls.get(call.id)
            if current is not None and (current.agent_run_id != call.agent_run_id or current.agent_attempt_id != call.agent_attempt_id):
                raise AssistantError(409, "FENCE_REJECTED", "工具调用不属于当前 Run Attempt")
            terminal = {"SUCCEEDED", "FAILED", "WAITING_CONFIRMATION", "REJECTED", "INTERRUPTED"}
            if current is not None and current.status in terminal:
                if call.status == current.status:
                    return
                if call.status in terminal:
                    raise AssistantError(409, "STATE_CONFLICT", "工具调用终态已确定", retryable=False)
                return
            self.tool_calls[call.id] = call
    def get_tool_call(self, call_id: str) -> ToolCall | None: return self.tool_calls.get(call_id)
    def list_tool_calls(self, *, run_id: str, limit: int = 50) -> list[ToolCall]:
        values = sorted((v for v in self.tool_calls.values() if v.agent_run_id == run_id), key=lambda v: (v.started_at or datetime.min.replace(tzinfo=timezone.utc), v.id), reverse=True)
        return values[: max(1, min(limit, 200))]
    def list_llm_calls(self, *, run_id: str, limit: int = 50, cursor: str | None = None) -> list[LlmCallDetail]:
        values = sorted((v for v in self.calls.values() if v.agent_run_id == run_id), key=lambda v: (v.created_at, v.id), reverse=True)
        if cursor:
            try:
                marker = json.loads(__import__("base64").urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode("utf-8"))["key"]
                values = [v for v in values if (v.created_at.isoformat(), v.id) < tuple(marker)]
            except Exception as exc:
                raise AssistantError(400, "INVALID_CURSOR", "LLM 调用分页 cursor 无效") from exc
        return values[: max(1, min(limit, 200))]


def _require_permission(actor: object | None, permission: str) -> None:
    if actor is None:
        raise AssistantError(403, "PERMISSION_DENIED", f"缺少 {permission} 权限")
    can = getattr(actor, "can", None)
    if not callable(can):
        nested = getattr(actor, "actor", None)
        can = getattr(nested, "can", None)
    if not callable(can) or not can(permission):
        raise AssistantError(403, "PERMISSION_DENIED", f"缺少 {permission} 权限")


class ContextInspector:
    def __init__(self, store: TraceStore, *, mode: str | None = None, retention_days: int = 7, audit: Callable[..., None] | None = None, now: Callable[[], datetime] = _now, scope_checker: Callable[..., None] | None = None) -> None:
        self.store, self.mode, self.retention_days, self.audit, self.now, self.scope_checker = store, mode or os.getenv("TRACE_CONTENT_MODE", "off"), retention_days, audit, now, scope_checker
        if self.mode not in {"off", "redacted"}:
            raise ValueError("TRACE_CONTENT_MODE 只允许 off 或 redacted")

    def capture(self, *, llm_call_id: str, view: str, payload: Any, sources: list[Mapping[str, Any]] | None = None, payload_schema_version: str = "openai_chat_v1", capture_mode: str | None = None, expires_at: datetime | None = None, project_id: str = "unknown") -> ContextSnapshot:
        if view not in {"logical_context", "provider_request"}:
            raise ValueError("view must be logical_context or provider_request")
        active_mode = (capture_mode or self.mode).lower()
        if active_mode == "raw":
            raise AssistantError(400, "TRACE_RAW_FORBIDDEN", "首版禁止 raw trace")
        if active_mode not in {"off", "redacted"}:
            raise AssistantError(400, "VALIDATION_FAILED", "trace capture_mode 只允许 off 或 redacted")
        now = self.now()
        expiry = expires_at or now + timedelta(days=self.retention_days)
        safe_sources = tuple(redact(dict(source)) for source in (sources or ()))
        if active_mode == "off":
            snapshot = ContextSnapshot(f"ctx_{uuid4().hex}", llm_call_id, view, "DISABLED", "off", payload_schema_version, None, safe_sources, {}, False, None, created_at=now, expires_at=expiry, project_id=project_id)
        else:
            stats = {"secret": 0, "hidden": 0}
            clean = redact(payload, _stats=stats)
            clean, truncated, offset = _bounded(clean)
            snapshot = ContextSnapshot(f"ctx_{uuid4().hex}", llm_call_id, view, "AVAILABLE", "redacted", payload_schema_version, clean, safe_sources, stats, truncated, offset, "redacted_payload", _sha(clean), now, expiry, project_id)
        self.store.save_snapshot(snapshot)
        return snapshot

    def capture_pair(self, *, llm_call: LlmCallDetail, logical_context: Any, provider_request: Any, logical_sources: list[Mapping[str, Any]] | None = None, provider_sources: list[Mapping[str, Any]] | None = None, payload_schema_version: str = "openai_chat_v1", project_id: str | None = None) -> tuple[ContextSnapshot, ContextSnapshot]:
        project_id = project_id or llm_call.project_id
        resolver = getattr(self.store, "resolve_project_id", None)
        if not project_id and callable(resolver):
            project_id = resolver(llm_call.id)
        if not project_id:
            raise AssistantError(503, "TRACE_SCOPE_UNAVAILABLE", "Context 快照缺少项目范围", retryable=True)
        # Persist the parent first. SQL foreign keys require the LlmCall row to
        # exist before either snapshot is flushed.
        self.store.save_llm_call(self._safe_call(llm_call))
        logical = self.capture(llm_call_id=llm_call.id, view="logical_context", payload=logical_context, sources=logical_sources, payload_schema_version=payload_schema_version, project_id=project_id)
        provider = self.capture(llm_call_id=llm_call.id, view="provider_request", payload=provider_request, sources=provider_sources, payload_schema_version=payload_schema_version, project_id=project_id)
        self.store.save_llm_call(self._safe_call(replace(llm_call, logical_context_snapshot_id=logical.id, provider_request_snapshot_id=provider.id)))
        return logical, provider

    def save_llm_call(self, call: LlmCallDetail) -> None:
        self.store.save_llm_call(self._safe_call(call))

    def save_tool_call(self, call: ToolCall, *, execution_token: str | None = None, fencing_token: int | None = None) -> None:
        saver = getattr(self.store, "save_tool_call", None)
        if not callable(saver):
            raise AssistantError(503, "DEPENDENCY_UNAVAILABLE", "工具调用账本存储端口未配置", retryable=True)
        saver(self._safe_tool_call(call), execution_token=execution_token, fencing_token=fencing_token)

    def get_tool_call(self, call_id: str) -> ToolCall | None:
        getter = getattr(self.store, "get_tool_call", None)
        if not callable(getter):
            raise AssistantError(503, "DEPENDENCY_UNAVAILABLE", "工具调用账本存储端口未配置", retryable=True)
        return getter(call_id)

    def list_tool_calls(self, run_id: str, *, actor: object | None = None, limit: int = 50) -> list[dict[str, Any]]:
        _require_permission(actor, "trace:read")
        getter = getattr(self.store, "list_tool_calls", None)
        if not callable(getter):
            raise AssistantError(503, "DEPENDENCY_UNAVAILABLE", "工具调用列表存储端口未配置", retryable=True)
        values = getter(run_id=run_id, limit=limit)
        result = []
        for value in values:
            self._scope_run(value.agent_run_id, actor)
            result.append(redact(value.model_dump(mode="json") if hasattr(value, "model_dump") else dict(value)))
        return result

    def record_tool_call_event(self, call: ToolCall, event_type: str, data: Mapping[str, Any], *, execution_token: str | None = None, fencing_token: int | None = None) -> bool:
        recorder = getattr(self.store, "record_tool_call_event", None)
        if callable(recorder):
            recorder(self._safe_tool_call(call), event_type, dict(data), execution_token=execution_token, fencing_token=fencing_token)
            return True
        self.save_tool_call(call, execution_token=execution_token, fencing_token=fencing_token)
        return False

    def metadata(self, snapshot_id: str, *, actor: object | None = None) -> dict[str, Any]:
        _require_permission(actor, "trace:read")
        snapshot = self._get(snapshot_id)
        if self.scope_checker is not None:
            self._scope_snapshot(snapshot, actor)
        self._audit("trace.read", snapshot)
        metadata = snapshot.metadata()
        if snapshot.expires_at and self.now() >= snapshot.expires_at:
            metadata["capture_state"] = "EXPIRED"
        return metadata

    def content(self, snapshot_id: str, *, actor: object | None = None) -> dict[str, Any]:
        _require_permission(actor, "trace:content:read")
        snapshot = self._get(snapshot_id)
        if self.scope_checker is not None:
            self._scope_snapshot(snapshot, actor)
        self._audit("trace.content.read", snapshot)
        return snapshot.content_view(self.now())

    def download(self, snapshot_id: str, *, actor: object | None = None) -> dict[str, Any]:
        return self.content(snapshot_id, actor=actor)

    def llm_call(self, call_id: str, *, actor: object | None = None, permission: str = "trace:read") -> dict[str, Any]:
        _require_permission(actor, permission)
        call = self.store.get_llm_call(call_id)
        if call is None: raise AssistantError(404, "RESOURCE_NOT_FOUND", "LLM 调用不存在")
        self._scope_run(call.agent_run_id, actor)
        self._audit("trace.llm.read", call)
        value = redact(call.as_dict())
        if isinstance(value, dict) and value.get("error") is not None:
            value["error"] = public_error(value["error"])
        return value

    def list_llm_calls(self, run_id: str, *, actor: object | None = None, limit: int = 50, cursor: str | None = None) -> list[dict[str, Any]]:
        _require_permission(actor, "trace:read")
        getter = getattr(self.store, "list_llm_calls", None)
        if not callable(getter):
            raise AssistantError(503, "DEPENDENCY_UNAVAILABLE", "LLM 调用列表存储端口未配置", retryable=True)
        try:
            values = getter(run_id=run_id, limit=limit, cursor=cursor)
        except TypeError:
            # The trimmed M4 assembly's _BoundTraceStore predates the cursor
            # argument.  Reach its repository port when available so a
            # bound SQL session still gets real keyset pagination instead of
            # silently returning page one on every request.
            repository = getattr(self.store, "repository", None)
            session = getattr(self.store, "session", None)
            repository_getter = getattr(repository, "list_llm_calls", None)
            if cursor and callable(repository_getter) and session is not None:
                values = repository_getter(session, run_id=run_id, limit=limit, cursor=cursor)
            else:
                values = getter(run_id=run_id, limit=limit)
        result = []
        for value in values:
            self._scope_run(value.agent_run_id if hasattr(value, "agent_run_id") else run_id, actor)
            projected = redact(value.as_dict() if hasattr(value, "as_dict") else dict(value))
            if isinstance(projected, dict) and projected.get("error") is not None:
                projected["error"] = public_error(projected["error"])
            result.append(projected)
        return result

    def compare(self, left_id: str, right_id: str, *, actor: object | None = None) -> dict[str, Any]:
        _require_permission(actor, "trace:content:read")
        left, right = self._get(left_id), self._get(right_id)
        if self.scope_checker is not None:
            self._scope_snapshot(left, actor)
            self._scope_snapshot(right, actor)
        # content() performs expiry, permission and audit, but does not invoke
        # any provider/runtime callback: compare is strictly saved snapshots.
        a, b = self.content(left_id, actor=actor)["payload"], self.content(right_id, actor=actor)["payload"]
        changes: list[dict[str, Any]] = []
        if a != b: changes.append({"path": "/", "kind": "changed", "before": a, "after": b})
        return {"left_id": left_id, "right_id": right_id, "summary": {"added": 0, "removed": 0, "changed": len(changes)}, "changes": changes, "truncated": left.truncated or right.truncated}

    def snapshot_scope(self, snapshot_id: str, *, actor: object | None = None, permission: str = "trace:read") -> dict[str, str]:
        """Resolve snapshot -> LlmCall -> AgentRun for the HTTP scope gate."""
        _require_permission(actor, permission)
        snapshot = self._get(snapshot_id)
        call = self.store.get_llm_call(snapshot.llm_call_id)
        if call is None:
            raise AssistantError(404, "RESOURCE_NOT_FOUND", "LLM 调用不存在或当前主体不可见")
        if call.project_id and snapshot.project_id not in {"unknown", call.project_id}:
            raise AssistantError(404, "RESOURCE_NOT_FOUND", "Context 快照不存在或当前主体不可见")
        self._scope_run(call.agent_run_id, actor)
        return {"snapshot_id": snapshot.id, "llm_call_id": call.id, "agent_run_id": call.agent_run_id, "project_id": snapshot.project_id}

    def _get(self, snapshot_id: str) -> ContextSnapshot:
        value = self.store.get_snapshot(snapshot_id)
        if value is None: raise AssistantError(404, "RESOURCE_NOT_FOUND", "Context 快照不存在")
        return value

    def _audit(self, action: str, resource: object) -> None:
        if self.audit is not None: self.audit(action=action, resource=resource)

    def _scope(self, llm_call_id: str, actor: object | None) -> None:
        if self.scope_checker is None:
            return
        call = self.store.get_llm_call(llm_call_id)
        if call is None:
            raise AssistantError(404, "RESOURCE_NOT_FOUND", "LLM 调用不存在")
        self._scope_run(call.agent_run_id, actor)

    def _scope_snapshot(self, snapshot: ContextSnapshot, actor: object | None) -> None:
        call = self.store.get_llm_call(snapshot.llm_call_id)
        if call is None:
            raise AssistantError(404, "RESOURCE_NOT_FOUND", "LLM 调用不存在或当前主体不可见")
        if call.project_id and snapshot.project_id not in {"unknown", call.project_id}:
            raise AssistantError(404, "RESOURCE_NOT_FOUND", "Context 快照不存在或当前主体不可见")
        self._scope_run(call.agent_run_id, actor)

    def _scope_run(self, agent_run_id: str, actor: object | None) -> None:
        if self.scope_checker is not None:
            self.scope_checker(actor, agent_run_id)

    @staticmethod
    def _safe_call(call: LlmCallDetail) -> LlmCallDetail:
        return replace(
            call,
            model_parameters_summary=redact(dict(call.model_parameters_summary)),
            estimates=redact(dict(call.estimates)),
            response_summary=redact(dict(call.response_summary)) if call.response_summary is not None else None,
            error=redact(dict(call.error)) if call.error is not None else None,
        )

    @staticmethod
    def _safe_tool_call(call: ToolCall) -> ToolCall:
        return call.model_copy(update={
            "arguments_summary": redact(dict(call.arguments_summary)),
            "result_summary": redact(dict(call.result_summary)) if call.result_summary is not None else None,
            "error": redact(dict(call.error)) if call.error is not None else None,
        })


__all__ = ["ContextInspector", "ContextSnapshot", "InMemoryTraceStore", "LlmCallDetail", "MAX_SNAPSHOT_BYTES", "TraceStore", "Usage", "redact"]
