from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from chrom_platform.core.dto import ActorContext
from chrom_platform.modules.assistant.errors import AssistantError
from chrom_platform.modules.assistant.trace import ContextInspector, InMemoryTraceStore


def actor(*permissions: str) -> ActorContext:
    return ActorContext(actor_id="a", tenant_id=None, project_ids=["p"], auth_method="deployment_default", permissions=list(permissions), policy_version="test")


def test_trace_off_has_metadata_without_content_and_raw_is_forbidden() -> None:
    store = InMemoryTraceStore()
    inspector = ContextInspector(store, mode="off")
    snapshot = inspector.capture(llm_call_id="llm-1", view="logical_context", payload={"token": "secret"})
    assert snapshot.capture_state == "DISABLED"
    assert inspector.metadata(snapshot.id, actor=actor("trace:read"))["capture_state"] == "DISABLED"
    assert inspector.content(snapshot.id, actor=actor("trace:content:read"))["payload"] is None
    with pytest.raises(AssistantError) as exc:
        inspector.capture(llm_call_id="llm-1", view="provider_request", payload={}, capture_mode="raw")
    assert exc.value.status_code == 400


def test_redaction_hash_truncation_expiry_permissions_and_saved_compare() -> None:
    store = InMemoryTraceStore()
    now = datetime.now(timezone.utc)
    inspector = ContextInspector(store, mode="redacted", now=lambda: now)
    payload = {"api_key": "key", "Authorization": "Bearer abc", "thinking": "private", "body": "x" * (3 * 1024 * 1024)}
    left = inspector.capture(llm_call_id="l", view="logical_context", payload=payload)
    right = inspector.capture(llm_call_id="l", view="provider_request", payload={"body": "ok"})
    assert left.truncated and left.sha256 and len(left.sha256) == 64
    with pytest.raises(AssistantError) as denied:
        inspector.content(left.id, actor=actor("trace:read"))
    assert denied.value.status_code == 403
    content = inspector.content(left.id, actor=actor("trace:content:read"))
    text = str(content)
    assert "Bearer abc" not in text and "private" not in text
    compared = inspector.compare(left.id, right.id, actor=actor("trace:content:read"))
    assert compared["changes"]
    expired = inspector.capture(llm_call_id="l", view="logical_context", payload={"ok": 1}, expires_at=now - timedelta(seconds=1))
    assert inspector.metadata(expired.id, actor=actor("trace:read"))["capture_state"] == "EXPIRED"
    with pytest.raises(AssistantError) as gone:
        inspector.content(expired.id, actor=actor("trace:content:read"))
    assert gone.value.status_code == 410


def test_trace_redacts_nested_value_forms_sources_and_requires_content_permission() -> None:
    store = InMemoryTraceStore()
    inspector = ContextInspector(store, mode="redacted")
    snapshot = inspector.capture(
        llm_call_id="llm-1",
        view="provider_request",
        payload={"nested": [{"key": "value", "text": "Authorization: Bearer abc.def"}], "safe": "ok"},
        sources=[{"kind": "runtime_note", "cookie": "session=secret"}],
    )
    rendered = inspector.content(snapshot.id, actor=actor("trace:content:read"))
    assert "value" not in str(rendered)
    assert "Bearer abc.def" not in str(rendered)
    assert "session=secret" not in str(rendered)
    with pytest.raises(AssistantError) as denied:
        inspector.metadata(snapshot.id)
    assert denied.value.status_code == 403
