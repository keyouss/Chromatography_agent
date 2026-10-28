from __future__ import annotations

import asyncio
import json

import pytest

from chrom_platform.modules.assistant import reconcile_cli


def _argv(*extra: str) -> list[str]:
    return [
        "--run-id", "arun_exact",
        "--job-id", "job_exact",
        "--attempt", "1",
        "--agent-attempt-id", "attempt_exact",
        "--llm-call-id", "llm_exact",
        "--execution-token", "exec_exact",
        "--fencing-token", "7",
        "--project-id", "project_exact",
        "--actor-id", "actor_exact",
        *extra,
    ]


def test_reconcile_cli_is_dry_run_by_default_and_has_no_batch_selector():
    namespace = reconcile_cli.build_parser().parse_args(_argv())
    assert namespace.apply is False
    with pytest.raises(SystemExit):
        reconcile_cli.build_parser().parse_args(_argv("--all"))


def test_reconcile_cli_apply_is_explicit():
    namespace = reconcile_cli.build_parser().parse_args(_argv("--apply"))
    assert namespace.apply is True


def test_run_once_passes_exact_scope_and_dry_run_without_provider(monkeypatch):
    captured = {}

    class Engine:
        async def dispose(self):
            return None

    class Store:
        def __init__(self, _factory):
            pass

        async def reconcile_finalization_failure(self, **kwargs):
            captured.update(kwargs)
            return {"matched": True, "applied": False, "mode": "DRY_RUN"}

    monkeypatch.setattr(reconcile_cli, "_database_url", lambda: "mysql+asyncmy://redacted")
    monkeypatch.setattr(reconcile_cli, "create_async_engine", lambda *_args, **_kwargs: Engine())
    monkeypatch.setattr(reconcile_cli, "async_sessionmaker", lambda **_kwargs: object())
    monkeypatch.setattr(reconcile_cli, "AsyncAssistantExecutionStore", Store)
    namespace = reconcile_cli.build_parser().parse_args(_argv())
    output = asyncio.run(reconcile_cli.run_once(namespace))
    assert output["result"]["applied"] is False
    assert captured["apply"] is False
    assert captured["run_id"] == "arun_exact"
    assert captured["project_id"] == "project_exact"
    assert captured["actor_id"] == "actor_exact"


def test_cli_output_never_echoes_execution_token(monkeypatch, capsys):
    async def fake_run_once(namespace):
        return {"ok": True, "result": {"matched": True, "applied": False, "mode": "DRY_RUN"}}

    monkeypatch.setattr(reconcile_cli, "run_once", fake_run_once)
    assert reconcile_cli.main(_argv("--execution-token", "exec_secret")) == 0
    assert "exec_secret" not in capsys.readouterr().out

