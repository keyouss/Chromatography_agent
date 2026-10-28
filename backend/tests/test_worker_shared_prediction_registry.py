from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from types import SimpleNamespace

from chrom_platform.assembly import m4
from chrom_platform.worker_cli import ProfileWorkerPlan, ProductionWorkerDependencies, build_production_handlers


class _Registry:
    def __init__(self):
        self._capabilities = ("fake-capability",)

    def capabilities(self):
        return list(self._capabilities)


def _plan(*modules: str) -> ProfileWorkerPlan:
    return ProfileWorkerPlan(
        build_id="universal-v1",
        modules=modules,
        worker_modules=modules,
        allowed_job_kinds=frozenset({"assistant", "prediction"}),
        allowed_event_types=frozenset({"job.execute", "job.cancel"}),
    )


def _stub_workers(monkeypatch, captured):
    from chrom_platform.assembly import assistant_worker, prediction_worker

    def build_assistant(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(execute=lambda *_args, **_kwargs: None,
                               cancel=lambda *_args, **_kwargs: None,
                               retry=lambda *_args, **_kwargs: None)

    def build_prediction(**kwargs):
        captured["prediction_worker_registry"] = kwargs["registry"]
        return SimpleNamespace(execute=lambda *_args, **_kwargs: None,
                               cancel=lambda *_args, **_kwargs: None)

    monkeypatch.setattr(assistant_worker, "build_assistant_worker", build_assistant)
    monkeypatch.setattr(prediction_worker, "build_prediction_worker", build_prediction)


def test_worker_builds_registry_once_and_shares_it_with_assistant_and_prediction(monkeypatch):
    from chrom_platform.assembly import prediction

    registry = _Registry()
    builder_calls = []
    monkeypatch.setattr(
        prediction, "build_prediction_worker_registry",
        lambda **_kwargs: builder_calls.append("boot") or registry,
    )
    captured = {}
    _stub_workers(monkeypatch, captured)
    handlers = build_production_handlers(
        _plan("prediction", "assistant"),
        async_session_factory=lambda: object(),
        sync_session_factory=lambda: object(),
    )

    assert set(handlers) >= {"job.execute", "job.cancel"}
    assert builder_calls == ["boot"]
    assert captured["prediction_worker_registry"] is registry

    services_factory = captured["services_factory"]
    first = services_factory(scope=SimpleNamespace())["priority"].service
    second = services_factory(scope=SimpleNamespace())["priority"].service
    assert first.prediction is not second.prediction  # per-Run ports
    assert first.prediction.registry is registry
    assert second.prediction.registry is registry
    assert builder_calls == ["boot"]  # no per-Run rebuild

    # The boot-built registry is read concurrently after publication. Its
    # registration dictionaries are not mutated by worker service creation.
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _index: first.prediction.capabilities(), range(32)))
    assert results == [["fake-capability"]] * 32


def test_assistant_only_worker_does_not_fallback_build_prediction_registry(monkeypatch):
    from chrom_platform.assembly import prediction

    builder_calls = []

    def unexpected_build(**_kwargs):
        builder_calls.append("called")
        raise AssertionError("a profile without prediction must not assemble it")

    monkeypatch.setattr(prediction, "build_prediction_worker_registry", unexpected_build)
    captured = {}
    _stub_workers(monkeypatch, captured)
    build_production_handlers(
        _plan("assistant"),
        async_session_factory=lambda: object(),
        sync_session_factory=lambda: object(),
        dependencies=ProductionWorkerDependencies(),
    )

    services = captured["services_factory"](scope=SimpleNamespace())
    assert services["priority"].service.prediction is None
    assert builder_calls == []


def test_shared_registry_does_not_share_run_sessions_or_actor_context(monkeypatch):
    sessions = []
    sessions_lock = Lock()
    registry = _Registry()

    class FakeSession:
        def __init__(self):
            self.closed = False

        def begin(self):
            return self

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def close(self):
            self.closed = True

    def session_factory():
        value = FakeSession()
        with sessions_lock:
            sessions.append(value)
        return value

    class FakeCreateService:
        def __init__(self, **kwargs):
            self.registry = kwargs["capabilities"]
            assert kwargs["models"] is self.registry

        def create(self, ctx, request, *, idempotency_key):
            return (ctx.actor.actor_id, request, idempotency_key, self.registry)

    monkeypatch.setattr(m4, "SQLAlchemyPredictionPersistence", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(m4, "SQLAlchemyPredictionRecordWriter", lambda: object())
    monkeypatch.setattr(m4, "SQLAlchemyPredictionStructurePort", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(m4, "PredictionCreateService", FakeCreateService)

    port = m4._WorkerPredictionPort(session_factory, registry)

    def create(actor_id):
        ctx = SimpleNamespace(actor=SimpleNamespace(actor_id=actor_id))
        return port.create(ctx, f"request-{actor_id}", idempotency_key=f"idem-{actor_id}")

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(create, ("actor-a", "actor-b"))

    assert first[:3] == ("actor-a", "request-actor-a", "idem-actor-a")
    assert second[:3] == ("actor-b", "request-actor-b", "idem-actor-b")
    assert first[3] is second[3] is registry
    assert len(sessions) == 2 and sessions[0] is not sessions[1]
    assert all(session.closed for session in sessions)
