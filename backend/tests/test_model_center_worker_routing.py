from __future__ import annotations

import asyncio
import faulthandler
import threading

import pytest

import chrom_platform.worker_cli as worker_cli


def test_model_registration_thread_keeps_heartbeat_and_supervises_cancel(monkeypatch):
    faulthandler.dump_traceback_later(8)
    started = threading.Event()
    release = threading.Event()
    observed = {}
    main_thread = threading.get_ident()
    guard = object()

    class OwnedSession:
        def __init__(self):
            self.owner = threading.get_ident()
            observed["session_owner"] = self.owner

        def commit(self):
            observed["commit_owner"] = threading.get_ident()

        def rollback(self):
            observed["rollback_owner"] = threading.get_ident()

        def close(self):
            observed["close_owner"] = threading.get_ident()

    def session_factory():
        observed["factory_owner"] = threading.get_ident()
        return OwnedSession()

    def fake_dispatch(session, message, *, event_type, deps, fresh_session_factory):
        observed["dispatch_owner"] = threading.get_ident()
        observed["guard"] = worker_cli.get_current_lease_guard()
        observed["message"] = (message.job_id, message.attempt, message.dispatch_id)
        observed["event_type"] = event_type
        observed["fresh_factory"] = fresh_session_factory
        started.set()
        assert release.wait(3)
        return "done"

    monkeypatch.setattr(worker_cli, "_dispatch_database_sync", fake_dispatch)
    payload = {"job_id": "job-1", "attempt": 1, "dispatch_id": "dispatch-1"}

    async def scenario():
        slot = asyncio.Semaphore(1)
        ticks = 0
        running = True

        async def heartbeat():
            nonlocal ticks
            while running:
                ticks += 1
                await asyncio.sleep(0.002)

        heartbeat_task = asyncio.create_task(heartbeat())
        token = worker_cli._CURRENT_LEASE_GUARD.set(guard)
        try:
            task = asyncio.create_task(worker_cli._dispatch_model_registration_thread(
                session_factory, payload,
                deps=worker_cli.ProductionWorkerDependencies(),
                slot=slot,
            ))
            assert await asyncio.to_thread(started.wait, 2)
            await asyncio.sleep(0.02)
            assert ticks >= 3
            assert observed["dispatch_owner"] != main_thread
            assert observed["guard"] is guard
            assert observed["fresh_factory"] is session_factory

            task.cancel()
            await asyncio.sleep(0.02)
            assert not task.done(), "cancellation must not release the supervised thread early"
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            release.set()
            running = False
            await heartbeat_task
            worker_cli._CURRENT_LEASE_GUARD.reset(token)

        assert observed["factory_owner"] == observed["session_owner"] == observed["dispatch_owner"]
        assert observed["commit_owner"] == observed["close_owner"] == observed["session_owner"]
        assert observed["message"] == ("job-1", 1, "dispatch-1")
        assert observed["event_type"] == "job.execute"

    try:
        asyncio.run(asyncio.wait_for(scenario(), timeout=20))
    finally:
        faulthandler.cancel_dump_traceback_later()


def test_model_registration_thread_requires_sync_session_factory():
    async def scenario():
        with pytest.raises(
            worker_cli.WorkerConfigurationError,
            match="MODEL_REGISTRATION_SYNC_SESSION_FACTORY_UNAVAILABLE",
        ):
            await worker_cli._dispatch_model_registration_thread(
                None,
                {"job_id": "job-1", "attempt": 1, "dispatch_id": "dispatch-1"},
                deps=worker_cli.ProductionWorkerDependencies(),
                slot=asyncio.Semaphore(1),
            )

    asyncio.run(scenario())


def test_sync_session_rolls_back_and_closes_on_dispatch_failure(monkeypatch):
    observed = []

    class Session:
        def commit(self):
            observed.append(("commit", threading.get_ident()))

        def rollback(self):
            observed.append(("rollback", threading.get_ident()))

        def close(self):
            observed.append(("close", threading.get_ident()))

    def fail_dispatch(*_args, **_kwargs):
        raise RuntimeError("synthetic routing failure")

    monkeypatch.setattr(worker_cli, "_dispatch_database_sync", fail_dispatch)
    with pytest.raises(RuntimeError, match="synthetic routing failure"):
        worker_cli._dispatch_model_registration_sync(
            Session,
            {"job_id": "job-1", "attempt": 1, "dispatch_id": "dispatch-1"},
            event_type="job.execute",
            deps=worker_cli.ProductionWorkerDependencies(),
        )
    assert [name for name, _ in observed] == ["rollback", "close"]
    assert len({owner for _, owner in observed}) == 1
