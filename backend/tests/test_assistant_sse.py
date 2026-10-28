from __future__ import annotations

from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from chrom_platform.modules.assistant.errors import AssistantError
from chrom_platform.modules.assistant.events import InMemoryAgentEventStore, dedupe_events, events_page
from chrom_platform.modules.assistant.sse import encode_sse, resolve_after_seq, stream_events


@pytest.mark.parametrize(("event_type", "expected_code"), [("run.canceled", None), ("run.failed", "INTERNAL_ERROR")])
def test_sse_cancel_without_error_does_not_become_internal_failure(event_type, expected_code):
    event = InMemoryAgentEventStore().append("agent_run", "run-error", event_type, {"error": None})
    payload = json.loads(next(line[6:] for line in encode_sse(event).splitlines() if line.startswith("data: ")))
    error = payload["data"]["error"]
    assert (error["code"] if error else None) == expected_code


def test_event_sequences_are_atomic_and_sse_is_replayable() -> None:
    store = InMemoryAgentEventStore()
    first = store.append("agent_run", "run-1", "run.started", {"status": "RUNNING"})
    second = store.append("agent_run", "run-1", "run.completed", {"status": "SUCCEEDED"})
    assert (first.seq, second.seq) == (1, 2)
    assert list(stream_events(store, "agent_run", "run-1", after_seq="1"))[0].startswith("id: 2\nevent: run.completed")
    assert '"seq":"2"' in encode_sse(second)
    assert events_page(store, "agent_run", "run-1")["next_seq"] == "2"
    assert len(dedupe_events("run-1", [second, second])) == 1


def test_sse_cursor_mismatch_and_expired_window() -> None:
    assert resolve_after_seq("2", "2") == 2
    with pytest.raises(AssistantError) as mismatch:
        resolve_after_seq("2", "3")
    assert mismatch.value.status_code == 400
    clock_value = datetime.now(timezone.utc)
    old = InMemoryAgentEventStore(clock=lambda: clock_value)
    old.append("agent_run", "run-1", "run.started", {})
    old._streams[("agent_run", "run-1")] [0] = old._streams[("agent_run", "run-1")][0].__class__("agent_run", "run-1", 1, "run.started", {}, timestamp=clock_value - timedelta(days=8))
    with pytest.raises(AssistantError) as expired:
        list(stream_events(old, "agent_run", "run-1", after_seq="0"))
    assert expired.value.status_code == 410


def test_concurrent_test_store_sequences_are_unique_and_bigint_json_is_safe() -> None:
    store = InMemoryAgentEventStore()
    with ThreadPoolExecutor(max_workers=8) as pool:
        events = list(pool.map(lambda i: store.append("agent_run", "run-concurrent", "warning", {"i": i}), range(64)))
    assert sorted(event.seq for event in events) == list(range(1, 65))
    assert all(isinstance(event.as_dict()["seq"], str) for event in events)


def test_sse_transport_redacts_event_data_and_emits_keepalive() -> None:
    store = InMemoryAgentEventStore()
    event = store.append("agent_run", "run-1", "warning", {"api_key": "secret", "reasoning": "hidden"})
    chunks = list(stream_events(store, "agent_run", "run-1", heartbeat=True))
    assert "secret" not in encode_sse(event)
    assert "hidden" not in encode_sse(event)
    assert chunks[-1] == ": heartbeat\n\n"


def test_event_projection_preserves_business_provider_fields_across_cursor_replay() -> None:
    store = InMemoryAgentEventStore()
    event = store.append("agent_run", "run-business", "tool.completed", {"category": "result", "provider": "business", "error": "corrupt"})
    page = events_page(store, "agent_run", "run-business", after_seq=0)
    assert page["events"][0]["data"] == {"category": "result", "provider": "business", "error": {"code": "INTERNAL_ERROR", "message": "内部错误", "retryable": False}}
    replay = list(stream_events(store, "agent_run", "run-business", last_event_id=str(event.seq)))
    assert replay == []


def test_event_projection_preserves_explicit_null_error_on_success_event() -> None:
    store = InMemoryAgentEventStore()
    store.append("agent_run", "run-success", "run.completed", {"status": "SUCCEEDED", "error": None})
    page = events_page(store, "agent_run", "run-success", after_seq=0)
    assert page["events"][0]["data"] == {"status": "SUCCEEDED", "error": None}


def test_event_projection_keeps_safe_fallback_for_failed_event_without_error() -> None:
    store = InMemoryAgentEventStore()
    store.append("agent_run", "run-failed", "run.failed", {"status": "FAILED", "error": None})
    page = events_page(store, "agent_run", "run-failed", after_seq=0)
    assert page["events"][0]["data"]["error"] == {
        "code": "INTERNAL_ERROR", "message": "内部错误", "retryable": False,
    }


def test_canonical_terminal_projection_skips_raw_duplicate_without_losing_cursor_tail() -> None:
    store = InMemoryAgentEventStore()
    store.append("agent_run", "run-duplicate", "run.started", {"attempt": 1})
    store.append("agent_run", "run-duplicate", "run.failed", {"attempt": 1, "error": {"code": "A"}})
    store.append("agent_run", "run-duplicate", "run.failed", {"attempt": 1, "error": {"code": "B"}})
    store.append("agent_run", "run-duplicate", "warning", {"detail": "tail"})
    page = events_page(store, "agent_run", "run-duplicate", after_seq=0)
    assert [item["seq"] for item in page["events"]] == ["1", "2", "4"]
    assert len([item for item in page["events"] if item["type"] == "run.failed"]) == 1
    assert list(stream_events(store, "agent_run", "run-duplicate", last_event_id="2"))[0].startswith("id: 4\nevent: warning")
    assert list(stream_events(store, "agent_run", "run-duplicate", last_event_id="3"))[0].startswith("id: 4\nevent: warning")


def test_canonical_projection_uses_persisted_run_state_over_higher_terminal_seq() -> None:
    completed = InMemoryAgentEventStore()
    completed.run_states["run-completed"] = ("SUCCEEDED", 1)
    completed.append("agent_run", "run-completed", "run.started", {"attempt": 1})
    completed.append("agent_run", "run-completed", "run.completed", {"attempt": 1})
    completed.append("agent_run", "run-completed", "run.failed", {"attempt": 1, "error": {"code": "late"}})
    completed.append("agent_run", "run-completed", "warning", {"detail": "tail"})
    page = events_page(completed, "agent_run", "run-completed")
    assert [(item["seq"], item["type"]) for item in page["events"]] == [("1", "run.started"), ("2", "run.completed"), ("4", "warning")]
    assert [event.seq for event in completed.list_after("agent_run", "run-completed", 2)] == [4]

    failed = InMemoryAgentEventStore()
    failed.run_states["run-failed"] = ("FAILED", 1)
    failed.append("agent_run", "run-failed", "run.started", {"attempt": 1})
    failed.append("agent_run", "run-failed", "run.failed", {"attempt": 1})
    failed.append("agent_run", "run-failed", "run.completed", {"attempt": 1})
    failed.append("agent_run", "run-failed", "run.canceled", {"attempt": 1})
    assert [event.type for event in failed.list_after("agent_run", "run-failed", 0)] == ["run.started", "run.failed"]

    canceled = InMemoryAgentEventStore()
    canceled.run_states["run-canceled"] = ("CANCELED", 1)
    canceled.append("agent_run", "run-canceled", "run.started", {"attempt": 1})
    canceled.append("agent_run", "run-canceled", "run.canceled", {"attempt": 1})
    canceled.append("agent_run", "run-canceled", "run.failed", {"attempt": 1})
    assert [event.type for event in canceled.list_after("agent_run", "run-canceled", 0)] == ["run.started", "run.canceled"]
