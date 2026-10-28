from __future__ import annotations

import asyncio
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from types import ModuleType, SimpleNamespace

import pytest

import chrom_platform.worker_cli as worker_cli


def test_outbox_routes_assistant_resources_to_assistant_slot():
    class Store:
        async def claim_due(self, **_kwargs):
            return SimpleNamespace(
                payload={"job_id": "job-a", "attempt": 1, "dispatch_id": "dispatch-a"},
                event_type="job.execute",
                resource_ref={"type": "agent_run", "id": "run-a"},
                attempts=1,
            )

        async def retry(self, *_args, **_kwargs):
            raise AssertionError("successful dispatch must not retry")

    class Broker:
        def __init__(self):
            self.routes = []

        def send_routed(self, payload, *, assistant):
            self.routes.append((payload, assistant))

    broker = Broker()
    dispatcher = worker_cli.OutboxDispatcher(Store(), broker, {})

    assert asyncio.run(dispatcher.poll_once()) is True
    assert broker.routes == [({"job_id": "job-a", "attempt": 1, "dispatch_id": "dispatch-a"}, True)]


def test_waiting_assistant_yields_to_science_consumer():
    loop = asyncio.new_event_loop()
    loop_ready = threading.Event()

    def run_loop():
        asyncio.set_event_loop(loop)
        loop_ready.set()
        loop.run_forever()

    loop_thread = threading.Thread(target=run_loop, daemon=True)
    loop_thread.start()
    assert loop_ready.wait(2)

    runtime = object.__new__(worker_cli.WorkerRuntime)
    runtime.loop = loop
    runtime._loop_ready = threading.Event()
    runtime._loop_ready.set()
    assistant_waiting = threading.Event()
    claims = {
        name: SimpleNamespace(
            payload={"job_id": name, "attempt": 1, "dispatch_id": name},
            event_type="job.execute", dispatch_id=name,
        )
        for name in ("assistant", "science")
    }

    class Guard:
        async def stop(self):
            return None

        def check(self):
            return None

    class Store:
        async def resolve_active(self, payload, **_kwargs):
            return claims[payload["dispatch_id"]]

        async def start_heartbeat(self, _claim):
            return Guard()

        async def ack(self, _claim):
            return True

    science_active = 0
    science_max_active = 0
    science_finished = asyncio.Event()

    async def handler(message):
        nonlocal science_active, science_max_active
        if message.job_id == "assistant":
            assistant_waiting.set()
            await science_finished.wait()
            return "assistant-finished"
        science_active += 1
        science_max_active = max(science_max_active, science_active)
        await asyncio.sleep(0.01)
        science_active -= 1
        science_finished.set()
        return "science-finished"

    dispatcher = worker_cli.OutboxDispatcher(
        Store(), SimpleNamespace(send=lambda _payload: None),
        {"job.execute": handler}, async_runner=runtime._run_async_from_consumer,
    )
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            assistant = pool.submit(dispatcher.consume, claims["assistant"].payload)
            assert assistant_waiting.wait(2)
            science = pool.submit(dispatcher.consume, claims["science"].payload)
            assert science.result(timeout=2) == "science-finished"
            assert assistant.result(timeout=2) == "assistant-finished"
        assert science_max_active == 1
    finally:
        loop.call_soon_threadsafe(loop.stop)
        loop_thread.join(timeout=2)
        loop.close()


@pytest.mark.parametrize("assistant_enabled", [False, True])
def test_transport_builds_selected_single_threaded_queues_and_routes_messages(monkeypatch, assistant_enabled):
    created_actors = []
    created_workers = []

    class FakeWorker:
        def __init__(self, _broker, **kwargs):
            self.kwargs = kwargs
            self.started = self.stopped = 0
            created_workers.append(self)

        def start(self):
            self.started += 1

        def stop(self):
            self.stopped += 1

    class FakeBroker:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    dramatiq = ModuleType("dramatiq")
    dramatiq.set_broker = lambda _broker: None

    def actor(_consume, **kwargs):
        result = SimpleNamespace(kwargs=kwargs, sent=[])
        result.consume = _consume
        result.send = lambda payload: result.sent.append(payload)
        created_actors.append(result)
        return result

    dramatiq.actor = actor
    brokers = ModuleType("dramatiq.brokers")
    redis = ModuleType("dramatiq.brokers.redis")
    redis.RedisBroker = FakeBroker
    worker_module = ModuleType("dramatiq.worker")
    worker_module.Worker = FakeWorker
    monkeypatch.setitem(sys.modules, "dramatiq", dramatiq)
    monkeypatch.setitem(sys.modules, "dramatiq.brokers", brokers)
    monkeypatch.setitem(sys.modules, "dramatiq.brokers.redis", redis)
    monkeypatch.setitem(sys.modules, "dramatiq.worker", worker_module)

    transport = worker_cli.DramatiqTransport.create(
        "redis://localhost", "chrom", lambda _payload: None,
        assistant_enabled=assistant_enabled,
    )
    expected_workers = [{"queues": ["chrom"], "worker_threads": 1}]
    if assistant_enabled:
        expected_workers.append({"queues": ["chrom-assistant"], "worker_threads": 1})
    assert [worker.kwargs for worker in created_workers] == expected_workers
    transport.send_routed({"job_id": "science"}, assistant=False)
    assert created_actors[0].sent == [{"job_id": "science"}]
    if assistant_enabled:
        transport.send_routed({"job_id": "assistant"}, assistant=True)
        assert created_actors[1].sent == [{"job_id": "assistant"}]

    transport.worker.start()
    transport.worker.stop()
    assert [worker.started for worker in created_workers] == [1] * len(expected_workers)
    assert [worker.stopped for worker in created_workers] == [1] * len(expected_workers)


def test_worker_group_stops_already_started_worker_if_startup_fails():
    calls = []

    class Worker:
        def __init__(self, name, fail=False):
            self.name, self.fail = name, fail

        def start(self):
            calls.append(("start", self.name))
            if self.fail:
                raise RuntimeError("start failed")

        def stop(self):
            calls.append(("stop", self.name))

    group = worker_cli._WorkerGroup([Worker("science"), Worker("assistant", fail=True)])
    try:
        group.start()
    except RuntimeError as exc:
        assert str(exc) == "start failed"
    else:
        raise AssertionError("worker startup failure must propagate")
    assert calls == [("start", "science"), ("start", "assistant"), ("stop", "science")]
