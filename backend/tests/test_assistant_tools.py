from __future__ import annotations

import pytest

from chrom_platform.modules.assistant.errors import AssistantError
from chrom_platform.modules.assistant.tools import ToolExecution, ToolExecutionLedger, ToolPending, ToolSpec, make_toolset


class BusinessPort:
    def __init__(self):
        self.validated = 0
        self.executed = 0

    def validate(self, _ctx, **_kwargs):
        self.validated += 1

    def execute(self, _ctx, **kwargs):
        self.executed += 1
        return {"job_id": "job-1", "echo": kwargs["arguments"]}


def test_allowlist_and_confirmation_closed_over_injected_service():
    port = BusinessPort()
    ledger = ToolExecutionLedger()
    tools = make_toolset(mode="business").bind({"training": port}, ledger=ledger)

    pending = tools["submit_training"](None, {"experiment_id": "e1"}, target_revision={"revision": 2, "config_hash": "a" * 64})
    assert isinstance(pending, ToolPending)
    assert port.executed == 0

    first = tools["submit_training"](None, {"experiment_id": "e1"}, target_revision={"revision": 2, "config_hash": "a" * 64}, confirmation_id="c1")
    second = tools["submit_training"](None, {"experiment_id": "e1"}, target_revision={"revision": 2, "config_hash": "a" * 64}, confirmation_id="c1")
    assert isinstance(first, ToolExecution) and second.replayed
    assert port.executed == 1


def test_forbidden_capabilities_cannot_be_registered():
    for name in ("shell", "exec", "python", "sql", "http_fetch", "confirmation:create", "publish", "rollback"):
        with pytest.raises(ValueError):
            ToolSpec(name, "forbidden")
    names = {spec.name for spec in make_toolset().specs}
    assert not names.intersection({"shell", "exec", "python", "sql", "publish", "rollback", "approve"})


def test_ledger_key_includes_target_revision_and_normalizes_arguments():
    ledger = ToolExecutionLedger()
    calls = []
    first = ledger.execute(tool_name="get_prediction", normalized_args={"b": 2, "a": 1}, target_revision={"revision": 3}, fn=lambda: calls.append(1) or {"ok": True})
    replay = ledger.execute(tool_name="get_prediction", normalized_args={"a": 1, "b": 2}, target_revision={"revision": 3}, fn=lambda: calls.append(2) or {"ok": False})
    changed = ledger.execute(tool_name="get_prediction", normalized_args={"a": 1, "b": 2}, target_revision={"revision": 4}, fn=lambda: calls.append(3) or {"ok": "new"})
    assert not first.replayed and replay.replayed and not changed.replayed
    assert calls == [1, 3]
