from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import threading
import time

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from chrom_platform.core.dto import ActorContext, ErrorEnvelope
from chrom_platform.core.errors import APIError
from chrom_platform.infrastructure.db import Base
from chrom_platform.infrastructure.models import Job, JobEvent, Project
from chrom_platform.task_core.policy import DeploymentTaskPolicy
from chrom_platform.task_core.router import create_router
from chrom_platform.task_core.repository import SQLAlchemyTaskRepository
from chrom_platform.task_core.schemas import EventPage, JobEventView
from chrom_platform.task_core.service import JobEventStreamSnapshot, JobService, OperationContext
from chrom_platform.task_core.sse import stream_job_event_frames


class Harness:
    def __init__(self):
        self.engine = create_engine(
            "sqlite+pysqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        event.listen(
            self.engine,
            "connect",
            lambda connection, _record: connection.create_function("CHAR_LENGTH", 1, len),
        )
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.checked_out = 0
        self.checked_in = 0
        event.listen(self.engine, "checkout", lambda *_args: setattr(self, "checked_out", self.checked_out + 1))
        event.listen(self.engine, "checkin", lambda *_args: setattr(self, "checked_in", self.checked_in + 1))
        self.repo = SQLAlchemyTaskRepository(self.session_factory)
        self.service = JobService(self.repo, policy=DeploymentTaskPolicy())
        with self.session_factory.begin() as session:
            for project_id in ("project-a", "project-b"):
                session.add(Project(id=project_id, slug=project_id, name=project_id))

    def add_job(self, job_id="job-a", *, project_id="project-a", owner_id="actor-a", status="RUNNING", event_time=None):
        now = datetime.now(timezone.utc)
        with self.session_factory.begin() as session:
            session.add(Job(
                id=job_id,
                project_id=project_id,
                owner_id=owner_id,
                kind="prediction",
                resource_ref={"type": "prediction", "id": f"prediction-{job_id}"},
                status=status,
                attempt=1,
                progress={"completed": 0, "total": 1, "unit": "rows", "stage": "running"},
                cancelable=True,
                retryable=False,
                resume_mode="none",
                created_at=now,
                updated_at=now,
                revision=1,
                etag="job-etag",
            ))
            session.add(JobEvent(
                id=f"event-{job_id}-0",
                job_id=job_id,
                stream_id=job_id,
                seq=0,
                type="job.queued",
                schema_version="1.0",
                data={"status": "QUEUED", "attempt": 1},
                timestamp=event_time or now,
            ))

    def append_event(self, job_id: str, seq: int, event_type: str, *, status: str | None = None, timestamp=None):
        now = timestamp or datetime.now(timezone.utc)
        with self.session_factory.begin() as session:
            job = session.get(Job, job_id)
            if status is not None:
                job.status = status
                job.updated_at = now
                if status in {"SUCCEEDED", "PARTIAL", "FAILED", "CANCELED", "TIMED_OUT", "INTERRUPTED"}:
                    job.finished_at = now
            session.add(JobEvent(
                id=f"event-{job_id}-{seq}",
                job_id=job_id,
                stream_id=job_id,
                seq=seq,
                type=event_type,
                schema_version="1.0",
                data={"status": status or "RUNNING", "attempt": 1},
                timestamp=now,
            ))

    def close(self):
        self.engine.dispose()


def actor(*, actor_id="actor-a", project_ids=None, permissions=None):
    return ActorContext(
        actor_id=actor_id,
        tenant_id=None,
        project_ids=project_ids if project_ids is not None else ["project-a"],
        auth_method="deployment_default",
        permissions=permissions if permissions is not None else ["task:read"],
        policy_version="test-policy-v1",
    )


def client_for(harness: Harness, *, request_actor=None):
    app = FastAPI()
    app.state.actor_context = request_actor or actor()
    app.include_router(create_router(job_service=harness.service), prefix="/api/v1")

    @app.exception_handler(APIError)
    async def handle_api_error(_request: Request, exc: APIError):
        body = ErrorEnvelope(error=exc.payload, request_id="test-request")
        return JSONResponse(status_code=exc.status_code, content=body.model_dump(mode="json"))

    return TestClient(app)


def read_frame(lines):
    frame = {}
    while True:
        line = next(lines)
        if not line:
            if frame:
                return frame
            continue
        if line.startswith(":"):
            continue
        key, _, value = line.partition(":")
        frame[key] = value.lstrip()


def payload(frame):
    return json.loads(frame["data"])


def test_job_stream_route_declares_sse_oas_and_string_cursors():
    harness = Harness()
    try:
        spec = client_for(harness).get("/openapi.json").json()
        stream = spec["paths"]["/api/v1/jobs/{job_id}/events/stream"]["get"]
        assert stream["operationId"] == "stream_job_events"
        assert stream["responses"]["200"]["content"]["text/event-stream"]["schema"] == {"type": "string"}
        params = {(x["in"], x["name"]): x for x in stream["parameters"]}
        for key in (("query", "after_seq"), ("header", "Last-Event-ID")):
            assert params[key]["schema"]["type"] == "string"
            assert params[key]["schema"]["maxLength"] == 19
        page_seq = spec["components"]["schemas"]["JobEventView"]["properties"]["seq"]
        page_schema = spec["components"]["schemas"]["EventPage"]
        page_next = page_schema["properties"]["next_seq"]
        assert page_seq["type"] == "string"
        assert page_next["anyOf"][0]["type"] == "string"
        assert "next_seq" in page_schema["required"]
        page_params = {(item["in"], item["name"]): item for item in spec["paths"]["/api/v1/jobs/{job_id}/events"]["get"]["parameters"]}
        page_cursor = page_params[("query", "after_seq")]
        assert page_cursor["required"] is False
        assert page_cursor["schema"]["pattern"] == "^(-1|0|[1-9][0-9]{0,18})$"
        assert "default" not in page_cursor["schema"]
    finally:
        harness.close()


def test_http_sse_replays_then_reads_new_committed_events_and_closes_terminal():
    harness = Harness()
    harness.add_job()
    client = client_for(harness)
    def commit_after_open():
        time.sleep(0.1)
        harness.append_event("job-a", 1, "job.started", status="RUNNING")
        time.sleep(0.1)
        harness.append_event("job-a", 2, "job.succeeded", status="SUCCEEDED")
    writer = threading.Thread(target=commit_after_open, daemon=True)
    writer.start()
    try:
        with client.stream("GET", "/api/v1/jobs/job-a/events/stream") as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            assert response.headers["cache-control"] == "no-cache"
            assert response.headers["x-accel-buffering"] == "no"
            assert response.headers["x-content-type-options"] == "nosniff"
            body = "".join(response.iter_text())
        assert writer.is_alive() is False
        frames = []
        for block in body.strip().split("\n\n"):
            frame = {}
            for line in block.splitlines():
                key, _, value = line.partition(":")
                frame[key] = value.lstrip()
            frames.append(frame)
        assert [frame["id"] for frame in frames] == ["0", "1", "2"]
        assert [frame["event"] for frame in frames] == ["job.queued", "job.started", "job.succeeded"]
        assert [payload(frame)["seq"] for frame in frames] == ["0", "1", "2"]
    finally:
        writer.join(timeout=2)
        client.close()
        harness.close()


def test_paging_string_seq_preserves_numeric_order_bigints_and_empty_null_cursor():
    harness = Harness()
    harness.add_job(status="SUCCEEDED")
    harness.append_event("job-a", 9, "job.progress")
    harness.append_event("job-a", 10, "job.progress")
    huge_a, huge_b = 9_007_199_254_740_992, 9_007_199_254_740_993
    harness.append_event("job-a", huge_a, "job.progress")
    harness.append_event("job-a", huge_b, "job.succeeded", status="SUCCEEDED")
    client = client_for(harness)
    try:
        page = client.get("/api/v1/jobs/job-a/events?after_seq=8&limit=2").json()["data"]
        assert [row["seq"] for row in page["events"]] == ["9", "10"]
        assert page["next_seq"] == "10"
        assert page["has_more"] is True
        huge_page = client.get(f"/api/v1/jobs/job-a/events?after_seq=10&limit=2").json()["data"]
        assert [row["seq"] for row in huge_page["events"]] == [str(huge_a), str(huge_b)]
        assert huge_page["next_seq"] is None
        assert huge_page["has_more"] is False
        assert huge_page["stream_status"] == "TERMINAL"
        first_page = client.get("/api/v1/jobs/job-a/events?limit=2").json()["data"]
        sentinel_page = client.get("/api/v1/jobs/job-a/events?after_seq=-1&limit=2").json()["data"]
        assert [row["seq"] for row in first_page["events"]] == ["0", "9"]
        assert sentinel_page == first_page
        for invalid_page_cursor in ("-2", "-01", str(1 << 63)):
            assert client.get(f"/api/v1/jobs/job-a/events?after_seq={invalid_page_cursor}").status_code == 400
        empty = client.get(f"/api/v1/jobs/job-a/events?after_seq={huge_b}").json()["data"]
        assert empty["events"] == []
        assert empty["next_seq"] is None
        assert empty["has_more"] is False
        assert empty["stream_status"] == "TERMINAL"
    finally:
        client.close()
        harness.close()


def test_last_event_id_resume_and_conflicting_or_invalid_cursors():
    harness = Harness()
    harness.add_job(status="SUCCEEDED")
    harness.append_event("job-a", 1, "job.started")
    harness.append_event("job-a", 2, "job.succeeded", status="SUCCEEDED")
    huge_a, huge_b = 9_007_199_254_740_992, 9_007_199_254_740_993
    harness.append_event("job-a", huge_a, "job.progress")
    harness.append_event("job-a", huge_b, "job.succeeded", status="SUCCEEDED")
    client = client_for(harness)
    try:
        response = client.get("/api/v1/jobs/job-a/events/stream", headers={"Last-Event-ID": "1"})
        assert response.status_code == 200
        assert "id: 2\nevent: job.succeeded\ndata: " in response.text
        frames = [line for line in response.text.splitlines() if line.startswith("data: ")]
        assert [json.loads(line[6:])["seq"] for line in frames] == ["2", str(huge_a), str(huge_b)]
        precise_resume = client.get(
            "/api/v1/jobs/job-a/events/stream",
            headers={"Last-Event-ID": str(huge_a)},
        )
        assert precise_resume.status_code == 200
        assert f"id: {huge_b}\nevent: job.succeeded\ndata: " in precise_resume.text
        precise_payload = next(line[6:] for line in precise_resume.text.splitlines() if line.startswith("data: "))
        assert json.loads(precise_payload)["seq"] == str(huge_b)
        conflict = client.get(
            "/api/v1/jobs/job-a/events/stream?after_seq=1",
            headers={"Last-Event-ID": "2"},
        )
        assert conflict.status_code == 400
        for value in ("", "01", "-1", str(1 << 63), "not-a-seq"):
            invalid = client.get(f"/api/v1/jobs/job-a/events/stream?after_seq={value}")
            assert invalid.status_code == 400
        assert client.get(
            "/api/v1/jobs/job-a/events/stream",
            headers={"Last-Event-ID": "-1"},
        ).status_code == 400
    finally:
        client.close()
        harness.close()


def test_expired_cursor_has_paginated_recovery_and_access_is_checked_before_stream():
    harness = Harness()
    old = datetime.now(timezone.utc) - timedelta(days=8)
    harness.add_job(event_time=old)
    client = client_for(harness)
    try:
        expired = client.get("/api/v1/jobs/job-a/events/stream")
        assert expired.status_code == 410
        assert expired.json()["error"]["details"]["recovery_url"] == "/api/v1/jobs/job-a/events"
        assert client.get("/api/v1/jobs/missing/events/stream").status_code == 404
        assert client.get("/api/v1/jobs/job-a/events/stream", headers={"Last-Event-ID": str(1 << 63)}).status_code == 400
    finally:
        client.close()

    unauthorized = client_for(harness, request_actor=actor(actor_id="actor-b", project_ids=["project-a"]))
    try:
        assert unauthorized.get("/api/v1/jobs/job-a/events/stream").status_code == 403
    finally:
        unauthorized.close()
        harness.close()


def test_cross_project_job_is_not_enumerable_on_stream_or_paging():
    harness = Harness()
    harness.add_job("job-b", project_id="project-b", owner_id="actor-b")
    client = client_for(harness)
    try:
        assert client.get("/api/v1/jobs/job-b/events/stream").status_code == 404
        assert client.get("/api/v1/jobs/job-b/events").status_code == 404
    finally:
        client.close()
        harness.close()


def test_stream_generator_stops_after_disconnect_without_holding_a_db_session():
    harness = Harness()
    harness.add_job()
    ctx = OperationContext(actor=actor())
    snapshot = harness.service.event_stream_snapshot(ctx, "job-a", after_seq=-1)

    class DisconnectAfterFirstFrame:
        disconnected = False

        async def is_disconnected(self):
            return self.disconnected

    request = DisconnectAfterFirstFrame()

    async def consume():
        generator = stream_job_event_frames(
            request,
            harness.service,
            ctx,
            "job-a",
            after_seq=-1,
            initial_snapshot=snapshot,
            poll_interval_seconds=0.001,
        )
        first = await anext(generator)
        request.disconnected = True
        try:
            next_value = await anext(generator)
        except StopAsyncIteration:
            next_value = None
        await generator.aclose()
        return first, next_value

    try:
        first, next_value = asyncio.run(consume())
        assert "id: 0" in first
        assert next_value is None
        assert harness.checked_out == harness.checked_in
    finally:
        harness.close()


def test_job_event_public_schemas_reject_noncanonical_or_out_of_range_seq():
    base = {
        "id": "event-1",
        "job_id": "job-a",
        "stream_id": "job-a",
        "type": "job.progress",
        "schema_version": "1.0",
        "data": {},
        "timestamp": datetime.now(timezone.utc),
    }
    for value in ("01", "-1", str(1 << 63)):
        try:
            JobEventView.model_validate({**base, "seq": value})
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted invalid seq: {value}")
    assert EventPage.model_validate({"events": [], "next_seq": None, "has_more": False, "stream_status": "OPEN"}).next_seq is None
    try:
        EventPage.model_validate({"events": [], "has_more": False, "stream_status": "OPEN"})
    except ValueError:
        pass
    else:
        raise AssertionError("accepted missing required nullable next_seq")
