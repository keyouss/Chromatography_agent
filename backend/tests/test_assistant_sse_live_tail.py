from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import time

import pytest

from chrom_platform.modules.assistant.errors import AssistantError
from chrom_platform.modules.assistant.events import InMemoryAgentEventStore
from chrom_platform.modules.assistant.sse import create_sse_response, stream_live_events


def test_live_tail_observes_later_committed_event_and_heartbeats() -> None:
    store = InMemoryAgentEventStore()
    now = [0.0]
    sleeps = [0]

    def sleep(seconds: float) -> None:
        now[0] += seconds
        sleeps[0] += 1
        if sleeps[0] == 1:
            store.append("agent_run", "run-live", "message.delta", {"message_id": "m1", "block_index": 0, "delta": "hi", "offset": 0})

    stream = stream_live_events(store, "agent_run", "run-live", poll_interval=0.5, heartbeat_interval=1, clock=lambda: now[0], sleep=sleep)
    assert next(stream).startswith("id: 1\nevent: message.delta")

    empty = InMemoryAgentEventStore()
    heartbeat_stream = stream_live_events(empty, "agent_run", "run-empty", poll_interval=0.5, heartbeat_interval=1, clock=lambda: now[0], sleep=sleep)
    assert next(heartbeat_stream) == ": heartbeat\n\n"
    heartbeat_stream.close()


def test_live_tail_resumes_after_cursor_and_deduplicates_seq() -> None:
    store = InMemoryAgentEventStore()
    first = store.append("agent_run", "run-resume", "run.started", {"status": "RUNNING", "attempt": 1})
    second = store.append("agent_run", "run-resume", "warning", {"code": "LATE", "message": "arrived"})

    class DuplicatePageStore(InMemoryAgentEventStore):
        def list_after(self, stream_type: str, stream_id: str, after_seq: int, limit: int = 100):
            rows = super().list_after(stream_type, stream_id, after_seq, limit)
            return [rows[0], rows[0], *rows[1:]] if rows else rows

    duplicated = DuplicatePageStore()
    duplicated.append("agent_run", "run-resume", first.type, first.data)
    duplicated.append("agent_run", "run-resume", second.type, second.data)
    stream = stream_live_events(duplicated, "agent_run", "run-resume", last_event_id="1", heartbeat_interval=None, sleep=lambda _: None)
    assert next(stream).startswith("id: 2\nevent: warning")
    stream.close()


def test_live_tail_closes_only_after_persisted_terminal_and_disconnect_does_not_cancel_worker() -> None:
    store = InMemoryAgentEventStore()
    store.run_states["run-terminal"] = ("SUCCEEDED", 1)
    terminal = store.append("agent_run", "run-terminal", "run.completed", {"status": "SUCCEEDED", "attempt": 1})
    worker = {"cancelled": False}

    stream = stream_live_events(store, "agent_run", "run-terminal", heartbeat_interval=None)
    assert next(stream).startswith(f"id: {terminal.seq}\nevent: run.completed")
    with pytest.raises(StopIteration):
        next(stream)
    stream.close()
    assert worker["cancelled"] is False
    live = stream_live_events(store, "agent_run", "run-terminal", after_seq="1", heartbeat_interval=None)
    live.close()
    assert worker["cancelled"] is False


def test_live_tail_closes_when_cursor_already_covers_authoritative_terminal() -> None:
    store = InMemoryAgentEventStore()
    store.run_states["run-terminal-cursor"] = ("SUCCEEDED", 1)
    terminal = store.append("agent_run", "run-terminal-cursor", "run.completed", {"status": "SUCCEEDED", "attempt": 1})
    stream = stream_live_events(store, "agent_run", "run-terminal-cursor", after_seq=terminal.seq, heartbeat_interval=None)
    with pytest.raises(StopIteration):
        next(stream)


def test_live_tail_uses_canonical_terminal_cursor_and_replays_visible_tail_first() -> None:
    store = InMemoryAgentEventStore()
    store.run_states["run-terminal-duplicate"] = ("SUCCEEDED", 1)
    first = store.append("agent_run", "run-terminal-duplicate", "run.completed", {"status": "SUCCEEDED", "attempt": 1})
    store.append("agent_run", "run-terminal-duplicate", "run.completed", {"status": "SUCCEEDED", "attempt": 1})
    closed = stream_live_events(
        store, "agent_run", "run-terminal-duplicate", after_seq=first.seq,
        heartbeat_interval=None, sleep=lambda _: pytest.fail("canonical terminal cursor must close without polling"),
    )
    with pytest.raises(StopIteration):
        next(closed)

    with_tail = InMemoryAgentEventStore()
    with_tail.run_states["run-terminal-tail"] = ("SUCCEEDED", 1)
    terminal = with_tail.append("agent_run", "run-terminal-tail", "run.completed", {"status": "SUCCEEDED", "attempt": 1})
    with_tail.append("agent_run", "run-terminal-tail", "run.completed", {"status": "SUCCEEDED", "attempt": 1})
    warning = with_tail.append("agent_run", "run-terminal-tail", "warning", {"code": "AFTER_TERMINAL", "message": "replay me"})
    stream = stream_live_events(with_tail, "agent_run", "run-terminal-tail", after_seq=terminal.seq, heartbeat_interval=None)
    assert next(stream).startswith(f"id: {warning.seq}\nevent: warning")
    with pytest.raises(StopIteration):
        next(stream)


def test_live_tail_does_not_close_on_old_attempt_terminal() -> None:
    store = InMemoryAgentEventStore()
    store.run_states["run-resumed"] = ("RUNNING", 2)
    old = store.append("agent_run", "run-resumed", "run.failed", {"status": "FAILED", "attempt": 1})
    started = store.append("agent_run", "run-resumed", "run.started", {"status": "RUNNING", "attempt": 2})
    stream = stream_live_events(store, "agent_run", "run-resumed", heartbeat_interval=None)
    assert next(stream).startswith(f"id: {old.seq}\nevent: run.failed")
    assert next(stream).startswith(f"id: {started.seq}\nevent: run.started")
    stream.close()


def test_factory_path_uses_only_fresh_short_lived_stores_and_includes_recovery_url() -> None:
    class ClosedSession:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class Store(InMemoryAgentEventStore):
        def __init__(self, session: ClosedSession) -> None:
            super().__init__()
            self.session = session

    class ForbiddenStore:
        def __getattr__(self, name: str):
            raise AssertionError(f"request-scoped store accessed after factory was configured: {name}")

    sessions: list[ClosedSession] = []
    backing = InMemoryAgentEventStore()
    backing.append("agent_run", "factory-run", "warning", {"code": "LATE", "message": "ok"})

    def factory() -> Store:
        session = ClosedSession()
        sessions.append(session)
        current = Store(session)
        current._streams = backing._streams
        current.run_states = backing.run_states
        return current

    response = create_sse_response(ForbiddenStore(), "agent_run", "factory-run", store_factory=factory, heartbeat=False)
    assert sessions[0].closed is True  # preflight replay-window check

    async def read_one() -> bytes:
        try:
            return await anext(response.body_iterator)
        finally:
            await response.body_iterator.aclose()

    body = asyncio.run(read_one())
    assert "event: warning" in body
    assert len(sessions) == 2 and all(session.closed for session in sessions)


def test_live_tail_midstream_expiry_keeps_recovery_url_public() -> None:
    current = datetime.now(timezone.utc)
    store = InMemoryAgentEventStore(clock=lambda: current)
    first = store.append("agent_run", "run-expired-midstream", "run.started", {"attempt": 1})
    stream = stream_live_events(
        store, "agent_run", "run-expired-midstream", recovery_url="/api/v1/agent-runs/run-expired-midstream/events",
        heartbeat_interval=None,
    )
    assert next(stream).startswith(f"id: {first.seq}")
    second = store.append("agent_run", "run-expired-midstream", "warning", {"code": "NEXT", "message": "next"})
    store._streams[("agent_run", "run-expired-midstream")][0] = first.__class__(
        first.stream_type, first.stream_id, first.seq, first.type, first.data, timestamp=current - timedelta(days=8),
    )
    store._streams[("agent_run", "run-expired-midstream")][1] = second.__class__(
        second.stream_type, second.stream_id, second.seq, second.type, second.data, timestamp=current - timedelta(days=8),
    )
    chunk = next(stream)
    assert '"code":"EVENTS_EXPIRED"' in chunk
    assert '"recovery_url":"/api/v1/agent-runs/run-expired-midstream/events"' in chunk
    stream.close()


def test_streaming_response_disconnect_stops_further_tail_reads_promptly() -> None:
    store = InMemoryAgentEventStore()
    store.append("agent_run", "run-disconnect", "warning", {"code": "FIRST", "message": "first"})
    created: list[object] = []
    disconnect = asyncio.Event()

    def factory() -> InMemoryAgentEventStore:
        created.append(object())
        return store

    response = create_sse_response(store, "agent_run", "run-disconnect", store_factory=factory, heartbeat=False)
    received = False
    bodies: list[bytes] = []

    async def receive():
        nonlocal received
        if not received:
            received = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body" and message.get("body"):
            bodies.append(message["body"])
            disconnect.set()

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "scheme": "http", "path": "/events", "raw_path": b"/events", "query_string": b"", "headers": [], "server": ("testserver", 80), "client": ("testclient", 123), "root_path": ""}
    started = time.monotonic()
    asyncio.run(response(scope, receive, send))
    elapsed = time.monotonic() - started
    assert bodies and b"event: warning" in bodies[0]
    assert elapsed < 1.0
    # One preflight plus one active read is allowed; a detached poll loop is not.
    assert len(created) <= 2


def test_mysql_live_read_pushes_cursor_and_limit_into_sql() -> None:
    from sqlalchemy.dialects import mysql

    from chrom_platform.modules.assistant.events import MySQLAgentEventStore
    from chrom_platform.modules.assistant.persistence import AgentEventRow

    class EmptyRows:
        def all(self):
            return []

    class Session:
        def __init__(self) -> None:
            self.statements = []

        def scalars(self, statement):
            self.statements.append(statement)
            return EmptyRows()

        def scalar(self, statement):
            return None

        def get(self, model, key):
            return None

    session = Session()
    store = MySQLAgentEventStore(session, AgentEventRow)
    assert store.list_after("agent_run", "run-sql", 123, 7) == []
    assert len(session.statements) == 2
    ordinary_sql = str(session.statements[0].compile(dialect=mysql.dialect()))
    terminal_sql = str(session.statements[1].compile(dialect=mysql.dialect()))
    assert "agent_event.seq > %s" in ordinary_sql
    assert "LIMIT %s" in ordinary_sql
    assert "agent_event.type NOT IN" in ordinary_sql
    assert "agent_event.type IN" in terminal_sql


def test_mysql_canonical_tail_cursor_ignores_duplicate_terminal_rows() -> None:
    from sqlalchemy.dialects import mysql

    from chrom_platform.modules.assistant.events import MySQLAgentEventStore
    from chrom_platform.modules.assistant.persistence import AgentEventRow, AgentRunRow

    now = datetime.now(timezone.utc)
    duplicate_rows = [
        type("TerminalRow", (), {
            "id": f"event-{seq}", "seq": seq, "type": "run.completed",
            "schema_version": "1.0", "timestamp": now, "data": {"attempt": 1},
            "agent_run_id": "run-mysql-tail",
        })()
        for seq in (1, 2)
    ]

    class Rows:
        def __init__(self, values):
            self.values = values

        def all(self):
            return self.values

    class Session:
        def scalar(self, _statement):
            return None

        def scalars(self, statement):
            sql = str(statement.compile(dialect=mysql.dialect()))
            return Rows(duplicate_rows if "agent_event.type IN" in sql else [])

        def get(self, model, key):
            return type("RunRow", (), {"status": "SUCCEEDED", "attempt": 1})() if model is AgentRunRow and key == "run-mysql-tail" else None

    store = MySQLAgentEventStore(Session(), AgentEventRow)
    assert store.canonical_tail_cursor_state("agent_run", "run-mysql-tail") == (1, 1)



def test_live_tail_reports_expired_replay_window_before_http_200() -> None:
    current = datetime.now(timezone.utc)
    store = InMemoryAgentEventStore(clock=lambda: current)
    old = store.append("agent_run", "run-expired", "run.started", {"status": "RUNNING", "attempt": 1})
    store._streams[("agent_run", "run-expired")][0] = old.__class__(
        old.stream_type,
        old.stream_id,
        old.seq,
        old.type,
        old.data,
        timestamp=current - timedelta(days=8),
    )

    with pytest.raises(AssistantError) as expired:
        create_sse_response(store, "agent_run", "run-expired", after_seq="0")
    assert expired.value.status_code == 410
