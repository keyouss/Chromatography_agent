"""Durable assistant events and the notification boundary.

The event row is the source of truth.  A notifier is deliberately called only
after the row has been handed to the repository; Redis (when installed) is a
wake-up hint and never the replay store.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
import json
import math
import re
from threading import RLock
from typing import Any, Callable, Iterable, Mapping, Protocol
from uuid import uuid4

from .errors import AssistantError, public_event_data, unavailable

MAX_EVENT_SEQ = (1 << 63) - 1
_TERMINAL_EVENT_TYPES = frozenset({"run.completed", "run.failed", "run.canceled", "run.interrupted"})
_TERMINAL_RUN_STATUSES = frozenset({"SUCCEEDED", "FAILED", "CANCELED", "TIMED_OUT", "INTERRUPTED"})


def _now() -> datetime:
    return datetime.now(timezone.utc)


_EVENT_SENSITIVE_NAME = re.compile(
    r"(?:api[_-]?key|authorization|cookie|credential|password|refresh[_-]?token|secret|token)",
    re.IGNORECASE,
)
_EVENT_PUBLIC_COUNTERS = frozenset({
    "input_tokens", "output_tokens", "total_tokens", "cached_input_tokens", "max_output_tokens",
    "request_index", "transport_attempt",
})


def event_json_safe(value: Any, *, _depth: int = 0) -> Any:
    """Project event data to bounded JSON-safe, redacted values before storage.

    Event rows are durable facts and are written by several paths.  Keeping the
    projection at this boundary prevents Pydantic/dataclass/domain objects or
    credentials from reaching either the JSON column or the public replay
    surfaces.  Unknown objects are represented by a fixed marker rather than
    their potentially sensitive string representation.
    """
    if _depth > 32:
        return "[REDACTED_DEPTH]"
    if hasattr(value, "model_dump") and callable(value.model_dump):
        try:
            value = value.model_dump(mode="json")
        except Exception:
            return "[UNSERIALIZABLE]"
    elif is_dataclass(value) and not isinstance(value, type):
        try:
            value = asdict(value)
        except Exception:
            return "[UNSERIALIZABLE]"
    if isinstance(value, Enum):
        return event_json_safe(value.value, _depth=_depth + 1)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            if key.lower() not in _EVENT_PUBLIC_COUNTERS and _EVENT_SENSITIVE_NAME.search(key):
                result[key] = "[REDACTED]"
            else:
                result[key] = event_json_safe(raw_value, _depth=_depth + 1)
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        return [event_json_safe(item, _depth=_depth + 1) for item in value]
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else "[NONFINITE]"
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "[REDACTED_BINARY]"
    return "[UNSERIALIZABLE]"


@dataclass(frozen=True, slots=True)
class AgentEvent:
    stream_type: str
    stream_id: str
    seq: int
    type: str
    data: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = "1.0"
    timestamp: datetime = field(default_factory=_now)
    agent_run_id: str | None = None
    id: str = field(default_factory=lambda: f"event_{uuid4().hex}")

    def __post_init__(self) -> None:
        if not self.stream_type or not self.stream_id or not self.type:
            raise ValueError("event stream and type are required")
        if not isinstance(self.seq, int) or isinstance(self.seq, bool) or not 0 <= self.seq <= MAX_EVENT_SEQ:
            raise ValueError("event seq must be a BIGINT-safe non-negative integer")

    def as_dict(self) -> dict[str, Any]:
        return {
            "seq": str(self.seq), "type": self.type,
            "schema_version": self.schema_version,
            "timestamp": self.timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            **({"agent_run_id": self.agent_run_id} if self.agent_run_id else {}),
            "data": event_json_safe(self.data),
        }

    model_dump = as_dict


class EventNotificationPort(Protocol):
    def notify(self, event: AgentEvent) -> None: ...


class AgentEventStore(Protocol):
    def append(self, stream_type: str, stream_id: str, event_type: str, data: Mapping[str, Any], *, agent_run_id: str | None = None, schema_version: str = "1.0", timestamp: datetime | None = None) -> AgentEvent: ...
    def list_after(self, stream_type: str, stream_id: str, after_seq: int, limit: int = 100) -> list[AgentEvent]: ...
    def oldest_seq(self, stream_type: str, stream_id: str) -> int | None: ...
    def latest_seq(self, stream_type: str, stream_id: str) -> int | None: ...
    def canonical_tail_cursor_state(self, stream_type: str, stream_id: str) -> tuple[int | None, int | None]: ...


class RedisEventNotificationPort:
    """Small adapter for a Redis-like client; it never reads event payloads."""

    def __init__(self, client: object, *, prefix: str = "assistant:event:") -> None:
        self.client, self.prefix = client, prefix

    def notify(self, event: AgentEvent) -> None:
        publish = getattr(self.client, "publish", None)
        if not callable(publish):
            raise unavailable("Redis event notification 未配置")
        channel = f"{self.prefix}{event.stream_type}:{event.stream_id}"
        publish(channel, str(event.seq))


class InMemoryAgentEventStore:
    """Explicit test-only durable-store substitute.

    ponytail: one process lock keeps sequence allocation atomic; production
    uses a MySQL unique key/transaction rather than this test implementation.
    """

    is_test_only = True

    def __init__(self, *, notifier: EventNotificationPort | None = None, clock=_now, retention_days: int = 7, run_state_loader: Callable[[str], tuple[str, int] | None] | None = None) -> None:
        self._lock = RLock()
        self._streams: dict[tuple[str, str], list[AgentEvent]] = {}
        self.run_states: dict[str, tuple[str, int]] = {}
        self.run_state_loader = run_state_loader
        self.notifier, self.clock, self.retention_days = notifier, clock, retention_days

    def _fresh(self, key: tuple[str, str]) -> list[AgentEvent]:
        cutoff = self.clock() - timedelta(days=self.retention_days)
        return [x for x in self._streams.get(key, ()) if x.timestamp >= cutoff]

    def append(self, stream_type: str, stream_id: str, event_type: str, data: Mapping[str, Any], *, agent_run_id: str | None = None, schema_version: str = "1.0", timestamp: datetime | None = None) -> AgentEvent:
        with self._lock:
            key = (stream_type, stream_id)
            rows = self._streams.setdefault(key, [])
            safe_data = event_json_safe(data)
            event = AgentEvent(stream_type, stream_id, rows[-1].seq + 1 if rows else 1, event_type, safe_data, schema_version, timestamp or self.clock(), agent_run_id)
            rows.append(event)
        if self.notifier is not None:
            self.notifier.notify(event)
        return event

    def list_after(self, stream_type: str, stream_id: str, after_seq: int, limit: int = 100) -> list[AgentEvent]:
        if after_seq < 0 or limit < 1:
            raise ValueError("invalid event cursor")
        state = self.run_state_loader(stream_id) if self.run_state_loader is not None else self.run_states.get(stream_id)
        status, attempt = state if state is not None else (None, None)
        projected = canonical_event_projection(self._fresh((stream_type, stream_id)), authoritative_status=status, authoritative_attempt=attempt)
        return [x for x in projected if x.seq > after_seq][:limit]

    def oldest_seq(self, stream_type: str, stream_id: str) -> int | None:
        key = (stream_type, stream_id)
        rows = self._fresh(key)
        if rows:
            return rows[0].seq
        all_rows = self._streams.get(key, ())
        return (all_rows[-1].seq + 1) if all_rows else None

    def latest_seq(self, stream_type: str, stream_id: str) -> int | None:
        rows = self._streams.get((stream_type, stream_id), ())
        return rows[-1].seq if rows else None

    def canonical_tail_cursor_state(self, stream_type: str, stream_id: str) -> tuple[int | None, int | None]:
        state = self.run_state_loader(stream_id) if self.run_state_loader is not None else self.run_states.get(stream_id)
        status, attempt = state if state is not None else (None, None)
        projected = canonical_event_projection(
            self._fresh((stream_type, stream_id)),
            authoritative_status=status,
            authoritative_attempt=attempt,
        )
        latest_visible = projected[-1].seq if projected else None
        terminal_seq = _current_terminal_seq(projected, state)
        return terminal_seq, latest_visible


class MySQLAgentEventStore:
    """Repository adapter over an injected SQLAlchemy model/session.

    The platform migration owns the model. This adapter only flushes rows;
    the caller owns the outer transaction and therefore commit/close. A
    notification is never emitted from ``append``: only the transaction
    owner can know that the row committed.
    """

    def __init__(self, session: object, row_model: type, *, notifier: EventNotificationPort | None = None, clock=_now, retention_days: int = 7) -> None:
        self.session, self.row_model, self.notifier, self.clock, self.retention_days = session, row_model, notifier, clock, retention_days

    def _run_state(self, stream_type: str, stream_id: str) -> tuple[str, int] | None:
        if stream_type != "agent_run":
            return None
        try:
            from .persistence import AgentRunRow
            row = self.session.get(AgentRunRow, stream_id)
            if row is None:
                return None
            return str(row.status), int(row.attempt or 1)
        except (AttributeError, ImportError):
            return None

    def append(self, stream_type: str, stream_id: str, event_type: str, data: Mapping[str, Any], *, agent_run_id: str | None = None, schema_version: str = "1.0", timestamp: datetime | None = None) -> AgentEvent:
        from sqlalchemy import func, select
        # Lock the stream's latest row in the caller's transaction before
        # allocating the next sequence. The unique key remains the final
        # guard for a first-row race; the outer transaction must retry that
        # insert in a fresh transaction on IntegrityError.
        latest = select(self.row_model.seq).where(
            self.row_model.stream_type == stream_type,
            self.row_model.stream_id == stream_id,
        ).order_by(self.row_model.seq.desc()).limit(1).with_for_update()
        row = self.session.scalar(latest)
        seq = int(row or 0) + 1
        safe_data = event_json_safe(data)
        event = AgentEvent(stream_type, stream_id, seq, event_type, safe_data, schema_version, timestamp or self.clock(), agent_run_id)
        record = self.row_model(id=event.id, stream_type=stream_type, stream_id=stream_id, seq=event.seq, type=event.type, schema_version=event.schema_version, data=safe_data, timestamp=event.timestamp, agent_run_id=agent_run_id)
        self.session.add(record)
        flush = getattr(self.session, "flush", None)
        if callable(flush):
            flush()
        return event

    def notify_committed(self, event: AgentEvent) -> None:
        """Publish a wake-up only after the owning transaction commits."""
        if self.notifier is not None:
            self.notifier.notify(event)

    def list_after(self, stream_type: str, stream_id: str, after_seq: int, limit: int = 100) -> list[AgentEvent]:
        from sqlalchemy import select
        cutoff = self.clock() - timedelta(days=self.retention_days)
        if after_seq < 0 or limit < 1:
            raise ValueError("after_seq must be non-negative and limit must be positive")
        base = (
            self.row_model.stream_type == stream_type,
            self.row_model.stream_id == stream_id,
            self.row_model.timestamp >= cutoff,
        )
        # Ordinary rows (including message deltas) are cursor-paged in SQL.
        # Terminal rows stay a separate, small query so canonical projection
        # can still resolve duplicate/superseding facts across all attempts.
        ordinary_stmt = (
            select(self.row_model)
            .where(*base, self.row_model.seq > after_seq, self.row_model.type.notin_(_TERMINAL_EVENT_TYPES))
            .order_by(self.row_model.seq)
            .limit(limit)
        )
        terminal_stmt = (
            select(self.row_model)
            .where(*base, self.row_model.type.in_(_TERMINAL_EVENT_TYPES))
            .order_by(self.row_model.seq)
        )
        rows = [
            *self.session.scalars(ordinary_stmt).all(),
            *self.session.scalars(terminal_stmt).all(),
        ]
        state = self._run_state(stream_type, stream_id)
        status, attempt = state if state is not None else (None, None)
        projected = canonical_event_projection([_event_from_row(r, stream_type, stream_id) for r in rows], authoritative_status=status, authoritative_attempt=attempt)
        return [x for x in projected if x.seq > after_seq][:limit]

    def oldest_seq(self, stream_type: str, stream_id: str) -> int | None:
        from sqlalchemy import func, select
        cutoff = self.clock() - timedelta(days=self.retention_days)
        oldest = self.session.scalar(select(func.min(self.row_model.seq)).where(self.row_model.stream_type == stream_type, self.row_model.stream_id == stream_id, self.row_model.timestamp >= cutoff))
        if oldest is not None:
            return int(oldest)
        latest = self.latest_seq(stream_type, stream_id)
        return latest + 1 if latest is not None else None

    def latest_seq(self, stream_type: str, stream_id: str) -> int | None:
        from sqlalchemy import func, select
        return self.session.scalar(select(func.max(self.row_model.seq)).where(self.row_model.stream_type == stream_type, self.row_model.stream_id == stream_id))

    def canonical_tail_cursor_state(self, stream_type: str, stream_id: str) -> tuple[int | None, int | None]:
        from sqlalchemy import func, select
        cutoff = self.clock() - timedelta(days=self.retention_days)
        base = (
            self.row_model.stream_type == stream_type,
            self.row_model.stream_id == stream_id,
            self.row_model.timestamp >= cutoff,
        )
        latest_ordinary = self.session.scalar(
            select(func.max(self.row_model.seq)).where(*base, self.row_model.type.notin_(_TERMINAL_EVENT_TYPES))
        )
        terminal_rows = self.session.scalars(
            select(self.row_model).where(*base, self.row_model.type.in_(_TERMINAL_EVENT_TYPES)).order_by(self.row_model.seq)
        ).all()
        state = self._run_state(stream_type, stream_id)
        status, attempt = state if state is not None else (None, None)
        terminal_projection = canonical_event_projection(
            [_event_from_row(row, stream_type, stream_id) for row in terminal_rows],
            authoritative_status=status,
            authoritative_attempt=attempt,
        )
        latest_terminal = terminal_projection[-1].seq if terminal_projection else None
        candidates = [int(value) for value in (latest_ordinary, latest_terminal) if value is not None]
        return _current_terminal_seq(terminal_projection, state), max(candidates) if candidates else None


def _event_from_row(row: object, stream_type: str, stream_id: str) -> AgentEvent:
    return AgentEvent(stream_type, stream_id, int(row.seq), row.type, row.data or {}, row.schema_version, row.timestamp, getattr(row, "agent_run_id", None), getattr(row, "id", f"event_{row.seq}"))


def _current_terminal_seq(events: Iterable[AgentEvent], state: tuple[str, int] | None) -> int | None:
    if state is None:
        return None
    expected_type = {
        "SUCCEEDED": "run.completed", "FAILED": "run.failed", "CANCELED": "run.canceled",
        "TIMED_OUT": "run.interrupted", "INTERRUPTED": "run.interrupted",
    }.get(str(state[0]))
    if expected_type is None:
        return None
    for event in events:
        if event.type != expected_type:
            continue
        try:
            event_attempt = int((event.data or {}).get("attempt"))
        except (TypeError, ValueError):
            continue
        if event_attempt == int(state[1]):
            return event.seq
    return None


def canonical_event_projection(events: Iterable[AgentEvent], *, authoritative_status: str | None = None, authoritative_attempt: int | None = None) -> list[AgentEvent]:
    """Return the replay projection while retaining raw rows in storage.

    Terminal events are facts keyed by run attempt.  A repeated terminal fact
    with the same semantic type keeps its first sequence; if a later terminal
    type supersedes an earlier one, the later fact is canonical.  The rule is
    deterministic, so JSON polling, SSE replay, and recovery observe the same
    cursor-safe stream without deleting forensic rows.
    """
    ordered = sorted(events, key=lambda item: item.seq)
    terminal_by_attempt: dict[int | None, AgentEvent] = {}
    ordinary: list[AgentEvent] = []
    expected_type = {
        "SUCCEEDED": "run.completed", "FAILED": "run.failed", "CANCELED": "run.canceled",
        "TIMED_OUT": "run.interrupted", "INTERRUPTED": "run.interrupted",
    }.get(str(authoritative_status or ""))
    for event in ordered:
        if event.type not in _TERMINAL_EVENT_TYPES:
            ordinary.append(event)
            continue
        raw_attempt = (event.data or {}).get("attempt")
        try:
            attempt = int(raw_attempt) if raw_attempt is not None else None
        except (TypeError, ValueError):
            attempt = None
        if authoritative_attempt is not None and attempt == int(authoritative_attempt):
            # The durable run row is authoritative for its current attempt;
            # contradictory higher-seq terminal rows remain raw evidence but
            # are excluded from every replay surface.
            if expected_type is None or event.type != expected_type:
                continue
        prior = terminal_by_attempt.get(attempt)
        if prior is not None:
            # Same semantic terminal fact: preserve the earliest durable seq.
            if prior.type == event.type:
                continue
            # A different later terminal type is the final semantic state.
            ordinary = [item for item in ordinary if item.seq != prior.seq]
        terminal_by_attempt[attempt] = event
        ordinary.append(event)
    return sorted(ordinary, key=lambda item: item.seq)


def dedupe_events(stream_id: str, events: Iterable[AgentEvent]) -> list[AgentEvent]:
    """Client-side semantic dedupe by `(stream_id, seq)`, preserving order."""
    seen: set[tuple[str, int]] = set()
    result: list[AgentEvent] = []
    for event in events:
        key = (stream_id, event.seq)
        if key not in seen:
            seen.add(key)
            result.append(event)
    return result


def event_stream_status(authoritative_status: str | None) -> str:
    """Map the persisted run status to the polling stream lifecycle."""
    return "TERMINAL" if str(authoritative_status or "") in _TERMINAL_RUN_STATUSES else "OPEN"


def events_page(store: AgentEventStore, stream_type: str, stream_id: str, *, after_seq: int = 0, limit: int = 100, stream_status: str | None = None, authoritative_status: str | None = None, recovery_url: str | None = None) -> dict[str, Any]:
    """Stable JSON shape for polling fallback; it never advances execution."""
    oldest = store.oldest_seq(stream_type, stream_id)
    if oldest is not None and after_seq < oldest - 1:
        raise AssistantError(410, "EVENTS_EXPIRED", "SSE 事件已超出可重放窗口", details={"recovery_url": recovery_url or f"/api/v1/agent-runs/{stream_id}/events"})
    rows = store.list_after(stream_type, stream_id, after_seq, limit + 1)
    visible = rows[:limit]
    status = event_stream_status(authoritative_status) if authoritative_status is not None else (stream_status or "OPEN")
    return {"events": [
        {**event.as_dict(), "data": public_event_data(event.data, event_type=event.type)}
        for event in visible
    ], "next_seq": str(visible[-1].seq) if visible else str(after_seq), "has_more": len(rows) > limit, "stream_status": status}


__all__ = ["AgentEvent", "AgentEventStore", "EventNotificationPort", "InMemoryAgentEventStore", "MySQLAgentEventStore", "RedisEventNotificationPort", "canonical_event_projection", "dedupe_events", "event_json_safe", "event_stream_status", "events_page"]
