"""Durable, replayable SSE transport for shared Job events.

This module stays in task_core: the task substrate must not import the
optional assistant feature. MySQL/SQLAlchemy remains the event source of truth;
the stream periodically re-reads committed rows and treats disconnect as a
transport concern only.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import re
from time import monotonic
from typing import AsyncIterator

from fastapi import Request
from starlette.concurrency import run_in_threadpool

from .schemas import JobEventView
from .service import JobEventStreamSnapshot, JobService, OperationContext, TaskError

MAX_JOB_EVENT_SEQ = (1 << 63) - 1
JOB_EVENT_REPLAY_WINDOW = timedelta(days=7)
_CURSOR = re.compile(r"^(0|[1-9][0-9]{0,18})$")


def _parse_cursor(value: str | None, *, name: str) -> int | None:
    if value is None:
        return None
    if not _CURSOR.fullmatch(value):
        raise TaskError(400, "VALIDATION_FAILED", f"{name} 必须是非负十进制 seq")
    parsed = int(value)
    if parsed > MAX_JOB_EVENT_SEQ:
        raise TaskError(400, "VALIDATION_FAILED", f"{name} 超出 MySQL BIGINT 范围")
    return parsed


def resolve_job_event_cursor(after_seq: str | None, last_event_id: str | None) -> int:
    """Return the last consumed seq; -1 is the internal before-seq-0 sentinel."""
    url_cursor = _parse_cursor(after_seq, name="after_seq")
    header_cursor = _parse_cursor(last_event_id, name="Last-Event-ID")
    if url_cursor is not None and header_cursor is not None and url_cursor != header_cursor:
        raise TaskError(400, "CURSOR_MISMATCH", "after_seq 与 Last-Event-ID 不一致")
    if url_cursor is not None:
        return url_cursor
    if header_cursor is not None:
        return header_cursor
    # Job creation persists seq 0, so start before it to include the first fact.
    return -1


def check_job_event_replay_window(
    snapshot: JobEventStreamSnapshot,
    after_seq: int,
    *,
    job_id: str,
    now: datetime | None = None,
) -> None:
    """Reject unavailable/expired history before response headers are sent."""
    oldest = snapshot.oldest_event
    if oldest is None:
        return
    oldest_seq = int(oldest.seq)
    if after_seq < oldest_seq - 1:
        raise TaskError(
            410,
            "EVENTS_EXPIRED",
            "任务事件已超出可重放窗口",
            details={"recovery_url": f"/api/v1/jobs/{job_id}/events"},
        )
    current_time = now or datetime.now(timezone.utc)
    cutoff = current_time.astimezone(timezone.utc) - JOB_EVENT_REPLAY_WINDOW
    for event in snapshot.events:
        if int(event.seq) <= after_seq:
            continue
        timestamp = event.timestamp
        timestamp = timestamp.replace(tzinfo=timezone.utc) if timestamp.tzinfo is None else timestamp.astimezone(timezone.utc)
        if timestamp < cutoff:
            raise TaskError(
                410,
                "EVENTS_EXPIRED",
                "任务事件已超出可重放窗口",
                details={"recovery_url": f"/api/v1/jobs/{job_id}/events"},
            )


def encode_job_event(event: JobEventView) -> str:
    payload = event.model_dump(mode="json")
    payload["seq"] = str(event.seq)
    return (
        f"id: {event.seq}\n"
        f"event: {event.type}\n"
        f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"
    )


async def stream_job_event_frames(
    request: Request,
    service: JobService,
    ctx: OperationContext,
    job_id: str,
    *,
    after_seq: int,
    initial_snapshot: JobEventStreamSnapshot,
    poll_interval_seconds: float = 1.0,
    heartbeat_seconds: float = 15.0,
) -> AsyncIterator[str]:
    """Replay then poll committed event pages until terminal/disconnected.

    Each service call owns and closes its short DB transaction before this
    generator yields or sleeps. Polling from the durable job seq avoids a
    subscribe-after-query gap and needs no in-memory event state.
    """
    cursor = after_seq
    snapshot = initial_snapshot
    last_heartbeat = monotonic()
    while True:
        if await request.is_disconnected():
            return
        try:
            check_job_event_replay_window(snapshot, cursor, job_id=job_id)
        except TaskError:
            # Once headers are sent, the only safe recovery is to close this
            # transport; the next authorized request receives the HTTP 410.
            return
        for event in snapshot.events:
            event_seq = int(event.seq)
            if event_seq <= cursor:
                continue
            if await request.is_disconnected():
                return
            yield encode_job_event(event)
            cursor = event_seq
        if snapshot.terminal and not snapshot.has_more:
            return
        if snapshot.has_more:
            snapshot = await run_in_threadpool(
                service.event_stream_snapshot,
                ctx,
                job_id,
                after_seq=cursor,
            )
            continue

        elapsed = monotonic() - last_heartbeat
        if elapsed >= heartbeat_seconds:
            yield ": heartbeat\n\n"
            last_heartbeat = monotonic()
        await asyncio.sleep(min(poll_interval_seconds, max(0.01, heartbeat_seconds - (monotonic() - last_heartbeat))))
        if await request.is_disconnected():
            return
        try:
            snapshot = await run_in_threadpool(
                service.event_stream_snapshot,
                ctx,
                job_id,
                after_seq=cursor,
            )
        except TaskError:
            # The response is already open. End without disclosing whether
            # a revoked job exists; a reconnect gets the normal HTTP error.
            return


__all__ = [
    "JOB_EVENT_REPLAY_WINDOW",
    "MAX_JOB_EVENT_SEQ",
    "check_job_event_replay_window",
    "encode_job_event",
    "resolve_job_event_cursor",
    "stream_job_event_frames",
]
