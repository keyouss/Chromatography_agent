"""Fault injection for shutdown of the existing Worker-owned event loop."""
import asyncio
import sys
from types import SimpleNamespace

import pytest

from chrom_platform.worker_cli import WorkerRuntime


def test_native_client_close_failure_still_disposes_engines_and_loop(monkeypatch):
    calls = []

    async def close_clients():
        calls.append("clients")
        raise RuntimeError("injected-client-close")

    async def dispose_async():
        calls.append("async-engine")

    loop = asyncio.new_event_loop()
    runtime = SimpleNamespace(loop=loop,
        async_engine=SimpleNamespace(dispose=dispose_async),
        sync_engine=SimpleNamespace(dispose=lambda: calls.append("sync-engine")))
    monkeypatch.setitem(sys.modules, "chrom_platform.modules.assistant.adapter",
                        SimpleNamespace(close_native_clients=close_clients))
    with pytest.raises(RuntimeError, match="injected-client-close"):
        WorkerRuntime._close_async_resources(runtime)
    assert calls == ["clients", "async-engine", "sync-engine"]
    assert loop.is_closed()
    assert runtime.loop is runtime.async_engine is runtime.sync_engine is None


def test_transport_stop_failure_still_removes_ready_and_closes_resources():
    calls = []

    def stop():
        calls.append("worker")
        raise RuntimeError("injected-worker-stop")

    runtime = SimpleNamespace(stop_event=SimpleNamespace(set=lambda: calls.append("stop")),
        poll_thread=None, config=SimpleNamespace(lease_seconds=60),
        transport=SimpleNamespace(worker=SimpleNamespace(stop=stop),
                                  broker=SimpleNamespace(close=lambda: calls.append("broker"))),
        ready_file=SimpleNamespace(remove=lambda: calls.append("ready")),
        _close_async_resources=lambda: calls.append("resources"))
    with pytest.raises(RuntimeError, match="injected-worker-stop"):
        WorkerRuntime.shutdown(runtime)
    assert calls == ["stop", "worker", "broker", "ready", "resources"]
