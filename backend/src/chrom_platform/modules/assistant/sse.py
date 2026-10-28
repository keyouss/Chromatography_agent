"""Replayable SSE transport for assistant event streams."""

from __future__ import annotations

import json
from collections.abc import Iterator
import time
from typing import Any, Callable

from fastapi.responses import StreamingResponse

from .errors import AssistantError, public_event_data
from .events import AgentEvent, AgentEventStore
from .trace import redact


def _cursor(value: str | int | None, *, name: str) -> int | None:
    if value is None or value == "":
        return None
    text = str(value)
    if not text.isdecimal():
        raise AssistantError(400, "VALIDATION_FAILED", f"{name} 必须是十进制 seq")
    return int(text)


def _public_error(error: BaseException) -> dict[str, Any]:
    if isinstance(error, AssistantError):
        result = {"code": error.payload.code, "message": error.payload.message, "retryable": error.payload.retryable}
        if error.payload.code == "EVENTS_EXPIRED":
            details = getattr(error.payload, "details", None)
            recovery_url = details.get("recovery_url") if isinstance(details, dict) else None
            if isinstance(recovery_url, str) and recovery_url.startswith("/api/"):
                result["recovery_url"] = recovery_url
        return result
    return {"code": "INTERNAL_ERROR", "message": "SSE stream unavailable", "retryable": True}


def resolve_after_seq(after_seq: str | int | None = None, last_event_id: str | None = None) -> int:
    """Resolve URL cursor and Last-Event-ID, rejecting contradictory cursors."""
    url_value, header_value = _cursor(after_seq, name="after_seq"), _cursor(last_event_id, name="Last-Event-ID")
    if url_value is not None and header_value is not None and url_value != header_value:
        raise AssistantError(400, "CURSOR_MISMATCH", "after_seq 与 Last-Event-ID 不一致")
    return url_value if url_value is not None else header_value if header_value is not None else 0


def check_replay_window(store: AgentEventStore, stream_type: str, stream_id: str, after_seq: int, *, recovery_url: str | None = None) -> None:
    oldest = store.oldest_seq(stream_type, stream_id)
    if oldest is not None and after_seq < oldest - 1:
        raise AssistantError(410, "EVENTS_EXPIRED", "SSE 事件已超出可重放窗口", details={"recovery_url": recovery_url or f"/api/v1/agent-runs/{stream_id}"})


def encode_sse(event: AgentEvent) -> str:
    # Events are persisted facts, but transport output still crosses a trust
    # boundary. Apply the same recursive policy used by Context Inspector so
    # a malformed producer cannot leak keys, cookies, or hidden reasoning.
    payload = redact(event.as_dict())
    if isinstance(payload, dict) and payload.get("data") is not None:
        payload["data"] = public_event_data(payload["data"], event_type=event.type)
    return f"id: {event.seq}\nevent: {event.type}\ndata: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"


def encode_sse_error(error: BaseException) -> str:
    return f"event: error\ndata: {json.dumps({'error': _public_error(error)}, ensure_ascii=False, separators=(',', ':'))}\n\n"


def stream_events(store: AgentEventStore, stream_type: str, stream_id: str, *, after_seq: str | int | None = None, last_event_id: str | None = None, limit: int = 100, recovery_url: str | None = None, heartbeat: bool = False) -> Iterator[str]:
    if limit < 1:
        raise AssistantError(400, "VALIDATION_FAILED", "limit 必须大于0")
    cursor = resolve_after_seq(after_seq, last_event_id)
    check_replay_window(store, stream_type, stream_id, cursor, recovery_url=recovery_url)
    try:
        events = store.list_after(stream_type, stream_id, cursor, limit)
    except Exception as exc:
        yield encode_sse_error(exc)
        return
    for event in events:
        yield encode_sse(event)
    if heartbeat:
        yield ": heartbeat\n\n"


def _close_reader(reader: AgentEventStore, *, owned: bool) -> None:
    if not owned:
        return
    close = getattr(getattr(reader, "session", None), "close", None)
    if callable(close):
        close()


def stream_live_events(
    store: AgentEventStore,
    stream_type: str,
    stream_id: str,
    *,
    after_seq: str | int | None = None,
    last_event_id: str | None = None,
    limit: int = 100,
    recovery_url: str | None = None,
    store_factory: Callable[[], AgentEventStore] | None = None,
    poll_interval: float = 0.25,
    heartbeat_interval: float | None = 15.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Iterator[str]:
    """Tail committed event rows until a persisted terminal event is observed.

    ``stream_events`` remains the finite replay helper. A factory lets HTTP
    routes use one fresh synchronous DB session per read batch; the generator
    closes only those read sessions and has no ownership of Worker lifecycle.
    """
    if limit < 1:
        raise AssistantError(400, "VALIDATION_FAILED", "limit 必须大于0")
    cursor = resolve_after_seq(after_seq, last_event_id)
    last_heartbeat = clock()

    while True:
        reader = None
        terminal_seq = None
        latest_visible_seq = None
        try:
            reader = store_factory() if store_factory is not None else store
            check_replay_window(reader, stream_type, stream_id, cursor, recovery_url=recovery_url)
            tail_cursor_state = getattr(reader, "canonical_tail_cursor_state", None)
            if callable(tail_cursor_state):
                terminal_seq, latest_visible_seq = tail_cursor_state(stream_type, stream_id)
            else:
                # External store adapters predating canonical tail metadata can
                # still replay events, but a raw high-water mark alone must
                # never prove that a terminal fact is visible.
                latest_seq = getattr(reader, "latest_seq", None)
                latest_visible_seq = latest_seq(stream_type, stream_id) if callable(latest_seq) else None
            events = reader.list_after(stream_type, stream_id, cursor, limit)
        except Exception as exc:
            yield encode_sse_error(exc)
            return
        finally:
            if reader is not None:
                _close_reader(reader, owned=store_factory is not None)

        sent = False
        for event in events:
            if event.seq <= cursor:
                continue
            cursor = event.seq
            sent = True
            yield encode_sse(event)
        if (
            terminal_seq is not None
            and latest_visible_seq is not None
            and cursor >= terminal_seq
            and cursor >= latest_visible_seq
        ):
            return
        if sent:
            continue

        now = clock()
        if heartbeat_interval is not None and now - last_heartbeat >= heartbeat_interval:
            yield ": heartbeat\n\n"
            last_heartbeat = now
        delay = max(0.0, poll_interval)
        if heartbeat_interval is not None and heartbeat_interval > 0:
            delay = min(delay, max(0.0, heartbeat_interval - (clock() - last_heartbeat)))
        sleep(delay)


def create_sse_response(store: AgentEventStore, stream_type: str, stream_id: str, *, after_seq: str | int | None = None, last_event_id: str | None = None, limit: int = 100, recovery_url: str | None = None, heartbeat: bool = True, store_factory: Callable[[], AgentEventStore] | None = None) -> StreamingResponse:
    # Constructing the generator does not execute it, so validate before the
    # response is returned and let APIError middleware produce 400/410.
    cursor = resolve_after_seq(after_seq, last_event_id)
    if limit < 1:
        raise AssistantError(400, "VALIDATION_FAILED", "limit 必须大于0")
    reader = store_factory() if store_factory is not None else store
    try:
        check_replay_window(reader, stream_type, stream_id, cursor, recovery_url=recovery_url)
    finally:
        _close_reader(reader, owned=store_factory is not None)
    return StreamingResponse(
        stream_live_events(store, stream_type, stream_id, after_seq=cursor, limit=limit, recovery_url=recovery_url, heartbeat_interval=15.0 if heartbeat else None, store_factory=store_factory),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Content-Type-Options": "nosniff"},
    )


def create_event_stream_factory(store: AgentEventStore, *, stream_type: str = "agent_run", authorize: Callable[[str], None] | None = None) -> Callable[..., StreamingResponse]:
    """Factory suitable for a router dependency without owning run lifecycle."""
    def factory(stream_id: str, *, after_seq: str | int | None = None, last_event_id: str | None = None, limit: int = 100, recovery_url: str | None = None, heartbeat: bool = True) -> StreamingResponse:
        if authorize is not None:
            authorize(stream_id)
        return create_sse_response(store, stream_type, stream_id, after_seq=after_seq, last_event_id=last_event_id, limit=limit, recovery_url=recovery_url, heartbeat=heartbeat)
    return factory


__all__ = ["check_replay_window", "create_event_stream_factory", "create_sse_response", "encode_sse", "encode_sse_error", "resolve_after_seq", "stream_events", "stream_live_events"]
