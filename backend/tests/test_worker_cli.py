import json
import hashlib
import os
import asyncio
import threading
import time
import builtins
import importlib.util
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from pathlib import Path

import pytest

from chrom_platform.worker_cli import (
    DramatiqTransport,
    OutboxDispatcher,
    ProfileWorkerPlan,
    ReadyFile,
    StableWorkerMessage,
    WorkerConfig,
    WorkerConfigurationError,
    WorkerRuntime,
    worker_healthcheck,
    _linux_execution_layer_required,
    _validate_database_pair,
    _validate_sync_database_url,
)
from chrom_platform.task_core.outbox_dispatch import OutboxClaim
import chrom_platform.worker_cli as worker_cli


class CoreOutbox:
    def __init__(self, event_type="job.execute"):
        self.state_value = "PENDING"
        self.claim = OutboxClaim(
            dispatch_id="d-1", payload={"job_id": "j-1", "attempt": 1, "dispatch_id": "d-1"},
            event_type=event_type, aggregate_type="job", aggregate_id="j-1", project_id="p-1",
            resource_ref={"type": "prediction", "id": "j-1"}, attempts=1, lease_owner="worker",
            lease_token=1, lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        )

    async def claim_due(self, **_kwargs):
        if self.state_value != "PENDING":
            return None
        self.state_value = "PROCESSING"
        return self.claim

    async def resolve_active(self, _payload, **_kwargs):
        return self.claim

    async def ack(self, _claim, **_kwargs):
        self.state_value = "SENT"
        return True

    async def retry(self, _claim, **_kwargs):
        self.state_value = "PENDING"
        return True


class FakeBroker:
    def __init__(self, fail=False):
        self.fail = fail
        self.messages = []

    def send(self, payload):
        if self.fail:
            raise ConnectionError("secret URL must not escape")
        self.messages.append(dict(payload))


class FakeArtifactStore:
    configured = True
    max_size_bytes = 1024

    def __init__(self):
        self.objects = {}

    def key_for(self, artifact_id):
        return f"objects/{artifact_id}/content"

    def put(self, artifact_id, content, *, expected_size, expected_sha256=None):
        data = bytes(content)
        digest = hashlib.sha256(data).hexdigest()
        if len(data) != expected_size or (expected_sha256 and digest != expected_sha256):
            raise ValueError("invalid test payload")
        key = self.key_for(artifact_id)
        self.objects[key] = data
        return key, len(data), digest

    def read(self, storage_key):
        return self.objects[storage_key]

    def remove(self, storage_key):
        self.objects.pop(storage_key, None)


class UnwritableArtifactStore(FakeArtifactStore):
    def put(self, *_args, **_kwargs):
        raise PermissionError("test root is unwritable")


def test_outbox_sends_stable_ids_and_marks_sent():
    store, broker = CoreOutbox(), FakeBroker()
    dispatcher = OutboxDispatcher(store, broker, {"job.execute": lambda _message: "ok"})
    assert asyncio.run(dispatcher.poll_once(datetime.now(timezone.utc))) is True
    assert broker.messages == [{"job_id": "j-1", "attempt": 1, "dispatch_id": "d-1"}]
    assert store.state_value == "PROCESSING"
    assert dispatcher.consume(broker.messages[0]) == "ok"
    assert store.state_value == "SENT"


def test_same_dispatch_redelivery_is_acked_without_second_handler_execution():
    store, broker = CoreOutbox(), FakeBroker()
    calls = []
    dispatcher = OutboxDispatcher(store, broker, {"job.execute": lambda message: calls.append(message.job_id) or "ok"})
    assert asyncio.run(dispatcher.poll_once(datetime.now(timezone.utc))) is True
    assert dispatcher.consume(broker.messages[0]) == "ok"
    assert dispatcher.consume(broker.messages[0]) == "ok"
    assert calls == ["j-1"]


def test_ack_cas_failure_does_not_cache_result_and_swallow_retry():
    class AckRace(CoreOutbox):
        async def ack(self, _claim, **_kwargs):
            return False

    store, broker = AckRace(), FakeBroker()
    calls = []
    dispatcher = OutboxDispatcher(store, broker, {"job.execute": lambda message: calls.append(message.job_id) or "ok"})
    asyncio.run(dispatcher.poll_once(datetime.now(timezone.utc)))
    with pytest.raises(WorkerConfigurationError, match="STALE_OUTBOX_LEASE"):
        dispatcher.consume(broker.messages[0])
    with pytest.raises(WorkerConfigurationError, match="STALE_OUTBOX_LEASE"):
        dispatcher.consume(broker.messages[0])
    assert calls == ["j-1", "j-1"]


def test_outbox_transport_failure_requeues_same_dispatch():
    store, broker = CoreOutbox(), FakeBroker(fail=True)
    assert asyncio.run(OutboxDispatcher(store, broker, {}).poll_once(datetime.now(timezone.utc))) is False
    assert store.state_value == "PENDING"


def test_handler_failure_requeues_without_ack():
    store, broker = CoreOutbox(), FakeBroker()
    dispatcher = OutboxDispatcher(store, broker, {"job.execute": lambda _message: (_ for _ in ()).throw(RuntimeError("handler failed"))})
    assert asyncio.run(dispatcher.poll_once(datetime.now(timezone.utc))) is True
    with pytest.raises(RuntimeError, match="handler failed"):
        dispatcher.consume(broker.messages[0])
    assert store.state_value == "PENDING"


def test_outbox_cancel_routes_to_cancel_handler_without_execute_handler():
    store, broker = CoreOutbox(event_type="job.cancel"), FakeBroker()
    called = []
    dispatcher = OutboxDispatcher(
        store,
        broker,
        {"job.cancel": lambda message: called.append(message.job_id)},
        allowed_event_types=frozenset({"job.cancel"}),
    )
    assert asyncio.run(dispatcher.poll_once(datetime.now(timezone.utc))) is True
    dispatcher.consume(broker.messages[0])
    assert called == ["j-1"]


def test_nonbatch_cancel_transport_does_not_accept_batch_fencing_field():
    with pytest.raises(WorkerConfigurationError, match="INVALID_WORKER_PAYLOAD"):
        StableWorkerMessage.from_payload(
            {"job_id": "j-1", "attempt": 1, "dispatch_id": "d-1", "fencing_token": 3},
            event_type="job.cancel",
        )
    assert StableWorkerMessage.from_payload(
        {"job_id": "j-1", "attempt": 1, "dispatch_id": "d-1", "fencing_token": 3},
        event_type="job.cancel", allow_cancel_fencing=True,
    ).dispatch_id == "d-1"


def test_profile_plan_exposes_static_kinds_and_events():
    root = Path(__file__).resolve().parents[2]
    sfc = ProfileWorkerPlan.from_manifest(root / "builds" / "sfc-v1.json")
    universal = ProfileWorkerPlan.from_manifest(root / "builds" / "universal-v1.json")
    assert sfc.allowed_job_kinds == frozenset({"artifact_validation", "prediction", "export"})
    assert {"prediction.execute", "job.execute", "job.cancel"} <= sfc.allowed_event_types
    assert {"artifact_validation", "prediction", "knowledge_index", "training", "assistant", "assistant_llm_configuration_test", "model_deployment"} <= universal.allowed_job_kinds


def test_default_assistant_handler_binds_server_history_and_event_ports(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("LLM_PROVIDER_APPROVED_ENDPOINTS", "https://api.openai.com/v1")
    monkeypatch.setenv("LLM_MODEL_ID", "model-1")
    monkeypatch.setenv("LLM_KEY_REFERENCE", "API_KEY")
    monkeypatch.setenv("API_KEY", "process-only-secret")
    plan = ProfileWorkerPlan(
        build_id="universal-v1",
        modules=("assistant",),
        worker_modules=("assistant",),
        allowed_job_kinds=frozenset({"assistant"}),
        allowed_event_types=frozenset({"job.execute", "job.cancel"}),
    )
    captured = {}
    from chrom_platform.assembly import assistant_worker as assistant_worker_assembly

    def fake_build(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            execute=lambda *_args, **_kwargs: None,
            cancel=lambda *_args, **_kwargs: None,
            retry=lambda *_args, **_kwargs: None,
        )

    monkeypatch.setattr(assistant_worker_assembly, "build_assistant_worker", fake_build)
    worker_cli.build_production_handlers(
        plan,
        async_session_factory=lambda: object(),
        sync_session_factory=lambda: None,
    )
    assert captured["runtime"] is None
    factory = captured["runtime_factory"]
    runtime = factory(config=SimpleNamespace(
        id="cfg-1", status="RETIRED", url="https://api.openai.com/v1",
        model_id="model-1", key_reference="API_KEY", revision="rev-1",
            settings={"external_send_policy": "approved_endpoint_only", "config_hash": "a" * 64},
    ))
    assert runtime.history_loader is not None
    assert runtime.event_store is not None
    assert callable(runtime.provider.complete)
    assert runtime.config.config_revision == "rev-1"
    knowledge_runtime = factory(
        config=SimpleNamespace(
            id="cfg-1", status="RETIRED", url="https://api.openai.com/v1",
            model_id="model-1", key_reference="API_KEY", revision="rev-1",
            settings={"external_send_policy": "approved_endpoint_only", "config_hash": "a" * 64},
        ),
        scope=SimpleNamespace(mode="knowledge"),
    )
    knowledge_tools = {spec.name for spec in knowledge_runtime.toolset.specs}
    assert "create_prediction" not in knowledge_tools
    assert "normalize_structure" not in knowledge_tools
    assert "create_registration_draft" not in knowledge_tools
    assert "get_prediction" in knowledge_tools
    services = captured["services_factory"](scope=SimpleNamespace())
    # An assistant-only profile must not rebuild a cropped prediction module.
    assert services["priority"].service.prediction is None
    assert services["priority"].service.capabilities_port is None
    assert services["priority"].service.models is not None


def test_assistant_secret_resolver_requires_protected_absolute_root(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("CHROM_ASSISTANT_SECRET_ROOT", raising=False)
    with pytest.raises(WorkerConfigurationError):
        worker_cli._assistant_secret_resolver("dpapi:v1:" + "a" * 32)
    monkeypatch.setenv("CHROM_ASSISTANT_SECRET_ROOT", "relative\\assistant-llm-secrets")
    with pytest.raises(WorkerConfigurationError):
        worker_cli._assistant_secret_resolver("dpapi:v1:" + "a" * 32)

    calls = []
    class FakeStore:
        def __init__(self, root):
            calls.append(root)

        def get(self, reference):
            calls.append(reference)
            return "decrypted-for-test"

    from chrom_platform.modules.assistant import secret_store
    monkeypatch.setattr(secret_store, "WindowsDpapiSecretStore", FakeStore)
    root = tmp_path / "protected-runtime" / "ChromatographyPlatform" / "assistant-llm-secrets"
    monkeypatch.setenv("CHROM_ASSISTANT_SECRET_ROOT", str(root))
    assert worker_cli._assistant_secret_resolver("dpapi:v1:" + "a" * 32) == "decrypted-for-test"
    assert calls == [root, "dpapi:v1:" + "a" * 32]


def test_assistant_configuration_test_job_routes_to_separate_worker(monkeypatch):
    class RouteSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_args):
            return None
        async def scalar(self, _query):
            return "assistant_llm_configuration_test"

    seen = []
    class FakeTestWorker:
        def __init__(self, **_kwargs):
            pass
        async def execute(self, payload, lease_guard=None):
            seen.append((payload, lease_guard))
            return "configuration-tested"
        async def cancel(self, payload):
            return payload
        async def retry(self, payload, lease_guard=None):
            return await self.execute(payload, lease_guard=lease_guard)

    from chrom_platform.modules.assistant import production_worker
    monkeypatch.setattr(production_worker, "AssistantLlmConfigurationTestWorker", FakeTestWorker, raising=False)
    from chrom_platform.assembly import assistant_worker as assistant_worker_assembly
    monkeypatch.setattr(assistant_worker_assembly, "build_assistant_worker", lambda **_kwargs: SimpleNamespace(
        execute=lambda *_args, **_kwargs: None,
        cancel=lambda *_args, **_kwargs: None,
        retry=lambda *_args, **_kwargs: None,
    ))
    plan = ProfileWorkerPlan(
        build_id="universal-v1", modules=("assistant",), worker_modules=("assistant",),
        allowed_job_kinds=frozenset({"assistant", "assistant_llm_configuration_test"}),
        allowed_event_types=frozenset({"job.execute", "job.cancel"}),
    )
    handlers = worker_cli.build_production_handlers(
        plan, async_session_factory=lambda: RouteSession(), sync_session_factory=lambda: None,
    )
    payload = {"job_id": "job-1", "attempt": 1, "dispatch_id": "dispatch-1"}
    assert asyncio.run(handlers["job.execute"](payload)) == "configuration-tested"
    assert seen == [(payload, None)]


def test_batch_profile_registers_and_dispatches_real_production_handler(monkeypatch):
    class RouteSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_args):
            return None
        async def scalar(self, _query):
            return "batch_prediction"

    called = []
    from chrom_platform.modules.prediction.batch_worker import BatchPredictionProductionWorker

    async def fake_execute(self, payload, *, lease_guard=None):
        called.append((payload, lease_guard))
        return "batch-dispatched"

    monkeypatch.setattr(BatchPredictionProductionWorker, "execute", fake_execute)
    plan = ProfileWorkerPlan(
        build_id="future-batch-v1",
        modules=("prediction",),
        worker_modules=("prediction",),
        allowed_job_kinds=frozenset({"prediction", "batch_prediction"}),
        allowed_event_types=frozenset({"job.execute", "job.cancel"}),
        batch_enabled=True,
    )
    artifact_store = FakeArtifactStore()
    handlers = worker_cli.build_production_handlers(
        plan,
        async_session_factory=lambda: RouteSession(),
        sync_session_factory=lambda: None,
        dependencies=worker_cli.ProductionWorkerDependencies(prediction_registry=object(), artifact_store=artifact_store),
    )
    assert artifact_store.objects == {}  # startup probe removed its generated key
    assert set(handlers) == {"job.execute", "job.cancel"}
    payload = {"job_id": "job-1", "attempt": 1, "dispatch_id": "dispatch-1"}
    assert asyncio.run(handlers["job.execute"](payload)) == "batch-dispatched"
    assert asyncio.run(handlers["job.execute"](StableWorkerMessage.from_payload(payload, event_type="job.execute"))) == "batch-dispatched"
    assert asyncio.run(worker_cli._invoke_handler(
        handlers["job.execute"], StableWorkerMessage.from_payload(payload, event_type="job.execute"), object()
    )) == "batch-dispatched"
    assert called == [(payload, None), (payload, None), (payload, None)]

    cancel_payload = {**payload, "fencing_token": 7}
    async def fake_cancel(self, payload, *, lease_guard=None):
        called.append((payload, lease_guard))
        return "batch-canceled"
    monkeypatch.setattr(BatchPredictionProductionWorker, "cancel", fake_cancel)
    cancel_message = StableWorkerMessage.from_payload(cancel_payload, event_type="job.cancel", allow_cancel_fencing=True)
    assert asyncio.run(handlers["job.cancel"](cancel_message)) == "batch-canceled"
    assert called[-1] == (cancel_payload, None)


def test_batch_profile_fails_closed_without_artifact_store(monkeypatch):
    plan = ProfileWorkerPlan(
        build_id="future-batch-v1",
        modules=("prediction",),
        worker_modules=("prediction",),
        allowed_job_kinds=frozenset({"prediction", "batch_prediction"}),
        allowed_event_types=frozenset({"job.execute", "job.cancel"}),
        batch_enabled=True,
    )
    monkeypatch.delenv("ARTIFACT_ROOT", raising=False)
    with pytest.raises(WorkerConfigurationError, match="BATCH_ARTIFACT_STORE_UNAVAILABLE"):
        worker_cli.build_production_handlers(
            plan,
            async_session_factory=lambda: object(),
            sync_session_factory=lambda: object(),
            dependencies=worker_cli.ProductionWorkerDependencies(prediction_registry=object()),
        )


def test_batch_profile_rejects_unreviewed_or_unconfigured_store_before_registering_handlers(monkeypatch):
    plan = ProfileWorkerPlan(
        build_id="future-batch-v1",
        modules=("prediction",),
        worker_modules=("prediction",),
        allowed_job_kinds=frozenset({"prediction", "batch_prediction"}),
        allowed_event_types=frozenset({"job.execute", "job.cancel"}),
        batch_enabled=True,
    )
    monkeypatch.delenv("ARTIFACT_ROOT", raising=False)
    with pytest.raises(WorkerConfigurationError, match="BATCH_ARTIFACT_STORE_UNAVAILABLE"):
        worker_cli.build_production_handlers(
            plan,
            async_session_factory=lambda: object(),
            sync_session_factory=lambda: object(),
            dependencies=worker_cli.ProductionWorkerDependencies(
                prediction_registry=object(), artifact_store=object(),
            ),
        )
    with pytest.raises(WorkerConfigurationError, match="BATCH_ARTIFACT_STORE_UNAVAILABLE"):
        worker_cli.build_production_handlers(
            plan,
            async_session_factory=lambda: object(),
            sync_session_factory=lambda: object(),
            dependencies=worker_cli.ProductionWorkerDependencies(
                prediction_registry=object(), artifact_store=UnwritableArtifactStore(),
            ),
        )

    from chrom_platform.artifact_core.storage import LocalArtifactStore
    with pytest.raises(WorkerConfigurationError, match="BATCH_ARTIFACT_STORE_UNAVAILABLE"):
        worker_cli._validate_batch_artifact_store(LocalArtifactStore(None))


def test_local_artifact_store_probe_checks_real_writability_only_in_test_root(tmp_path):
    from chrom_platform.artifact_core.storage import LocalArtifactStore

    root = tmp_path / "approved-test-artifacts"
    store = LocalArtifactStore(root)
    worker_cli._validate_batch_artifact_store(store)
    assert not [path for path in root.rglob("*") if path.is_file()]


def test_batch_profile_injects_and_probes_configured_local_artifact_root(tmp_path, monkeypatch):
    from chrom_platform.artifact_core.storage import LocalArtifactStore

    class RouteSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def scalar(self, _query):
            return "batch_prediction"

    root = tmp_path / "profile-artifact-root"
    monkeypatch.setenv("ARTIFACT_ROOT", str(root))
    plan = ProfileWorkerPlan(
        build_id="future-batch-v1",
        modules=("prediction",),
        worker_modules=("prediction",),
        allowed_job_kinds=frozenset({"prediction", "batch_prediction"}),
        allowed_event_types=frozenset({"job.execute", "job.cancel"}),
        batch_enabled=True,
    )
    handlers = worker_cli.build_production_handlers(
        plan,
        async_session_factory=lambda: RouteSession(),
        sync_session_factory=lambda: None,
        dependencies=worker_cli.ProductionWorkerDependencies(prediction_registry=object()),
    )
    assert set(handlers) == {"job.execute", "job.cancel"}
    assert root.is_dir()
    assert not [path for path in root.rglob("*") if path.is_file()]


def test_batch_profile_assembles_default_readonly_artifact_service(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from chrom_platform.artifact_core.models import Artifact
    from chrom_platform.artifact_core.repository import SQLAlchemyArtifactReader
    from chrom_platform.artifact_core.service import ArtifactService, ArtifactServiceError
    from chrom_platform.artifact_core.storage import LocalArtifactStore
    from chrom_platform.core.dto import ActorContext
    from chrom_platform.infrastructure.db import Base
    from chrom_platform.infrastructure.models import Project
    from chrom_platform.modules.prediction.batch_worker import BatchPredictionProductionWorker

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    event.listen(engine, "connect", lambda conn, _record: conn.create_function(
        "CHAR_LENGTH", 1, lambda value: len(value) if value is not None else None,
    ))
    Base.metadata.create_all(engine)
    sync_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with sync_factory.begin() as session:
        session.add_all([
            Project(id="p1", slug="p1", name="Project 1"),
            Project(id="p2", slug="p2", name="Project 2"),
        ])

    store = LocalArtifactStore(tmp_path / "worker-artifacts")
    captured = {}
    original_init = BatchPredictionProductionWorker.__init__

    def capture_init(self, **kwargs):
        original_init(self, **kwargs)
        captured["worker"] = self

    monkeypatch.setattr(BatchPredictionProductionWorker, "__init__", capture_init)
    plan = ProfileWorkerPlan(
        build_id="future-batch-v1", modules=("prediction",), worker_modules=("prediction",),
        allowed_job_kinds=frozenset({"prediction", "batch_prediction"}),
        allowed_event_types=frozenset({"job.execute", "job.cancel"}), batch_enabled=True,
    )

    class RouteSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    try:
        worker_cli.build_production_handlers(
            plan,
            async_session_factory=lambda: RouteSession(),
            sync_session_factory=sync_factory,
            dependencies=worker_cli.ProductionWorkerDependencies(
                prediction_registry=object(), artifact_store=store,
            ),
        )
        artifact_service = captured["worker"].artifact
        assert isinstance(artifact_service, ArtifactService)
        assert isinstance(artifact_service.repository, SQLAlchemyArtifactReader)

        available_content = b"smiles_a,smiles_b\nC,CC\n"
        unavailable_content = b"smiles_a,smiles_b\nN,NN\n"
        rows = []
        for artifact_id, project_id, content, status, scan_status in (
            ("available-p1", "p1", available_content, "AVAILABLE", "PASSED"),
            ("available-p2", "p2", available_content, "AVAILABLE", "PASSED"),
            ("validating-p1", "p1", unavailable_content, "VALIDATING", "PENDING"),
        ):
            key, size, digest = store.put(
                artifact_id, content, expected_size=len(content),
                expected_sha256=hashlib.sha256(content).hexdigest(),
            )
            rows.append(Artifact(
                id=artifact_id, project_id=project_id, kind="batch_input",
                title="input.csv", media_type="text/csv", size_bytes=size, sha256=digest,
                storage_key=key, status=status, scan_status=scan_status,
                metadata_json={"file_name": "input.csv"}, created_by="owner",
            ))
        with sync_factory.begin() as session:
            session.add_all(rows)

        actor = ActorContext(
            actor_id="reader", tenant_id=None, project_ids=["p1"],
            auth_method="deployment_default", permissions=["artifact:read"], policy_version="test",
        )
        view = artifact_service.get_artifact("available-p1", actor)
        assert view.download_allowed is True
        downloaded_view, content = artifact_service.download("available-p1", actor)
        assert downloaded_view.id == view.id and content == available_content

        with pytest.raises(ArtifactServiceError) as missing_get:
            artifact_service.get_artifact("available-p2", actor)
        assert missing_get.value.code == "RESOURCE_NOT_FOUND"
        with pytest.raises(ArtifactServiceError) as missing_download:
            artifact_service.download("available-p2", actor)
        assert missing_download.value.code == "RESOURCE_NOT_FOUND"
        denied = actor.model_copy(update={"permissions": []})
        with pytest.raises(ArtifactServiceError) as forbidden:
            artifact_service.get_artifact("available-p1", denied)
        assert forbidden.value.code == "FORBIDDEN"
        with pytest.raises(ArtifactServiceError) as unavailable:
            artifact_service.download("validating-p1", actor)
        assert unavailable.value.code == "ARTIFACT_NOT_AVAILABLE"

        explicit_port = object()
        worker_cli.build_production_handlers(
            plan,
            async_session_factory=lambda: RouteSession(),
            sync_session_factory=sync_factory,
            dependencies=worker_cli.ProductionWorkerDependencies(
                prediction_registry=object(), artifact_store=store, artifact=explicit_port,
            ),
        )
        assert captured["worker"].artifact is explicit_port
    finally:
        engine.dispose()


def test_consumer_does_not_replay_a_coroutine_when_worker_operation_fails() -> None:
    runtime = WorkerRuntime(WorkerConfig(
        Path("build-manifest.json"), "mysql+asyncmy://user:pass@mysql:3306/db",
        "redis://redis:6379/0", Path("worker.ready"), profile="sfc-v1",
    ), {})
    runtime.loop = asyncio.new_event_loop()
    runtime._loop_ready.set()
    thread = threading.Thread(target=runtime.loop.run_forever)
    thread.start()

    async def fail() -> None:
        raise RuntimeError("operation failed")

    try:
        with pytest.raises(RuntimeError, match="operation failed"):
            runtime._run_async_from_consumer(fail())
    finally:
        runtime.loop.call_soon_threadsafe(runtime.loop.stop)
        thread.join(timeout=2)
        runtime.loop.close()


def test_static_job_resource_matrix_covers_producers_and_injection_seams():
    assert worker_cli._RESOURCE_TYPES["knowledge_index"] == "knowledge_document"
    assert worker_cli._RESOURCE_TYPES["training"] == "experiment"
    assert worker_cli._RESOURCE_TYPES["assistant"] == "agent_run"
    assert worker_cli._RESOURCE_TYPES["assistant_llm_configuration_test"] == "llm_test"
    deps = worker_cli.ProductionWorkerDependencies(training_execute=lambda _: None, assistant_cancel=lambda _: None)
    assert deps.training_execute is not None and deps.assistant_cancel is not None


def test_sync_database_url_requires_pymysql_and_same_endpoint_without_exposing_secret():
    async_url = "mysql+asyncmy://user:async-secret@db.example:3306/chrom"
    sync_url = "mysql+pymysql://user:sync-secret@db.example:3306/chrom"
    _validate_sync_database_url(sync_url)
    _validate_database_pair(async_url, sync_url)
    with pytest.raises(WorkerConfigurationError, match="SYNC_DATABASE_URL_MUST_USE_MYSQL_PYMYSQL"):
        _validate_sync_database_url("mysql+asyncmy://user:secret@db.example/chrom")
    with pytest.raises(WorkerConfigurationError, match="DATABASE_URL_SYNC_DATABASE_URL_MISMATCH"):
        _validate_database_pair(async_url, "mysql+pymysql://user:sync-secret@other.example:3306/chrom")
    assert "secret" not in "DATABASE_URL_SYNC_DATABASE_URL_MISMATCH"


def test_payload_rejects_dynamic_fields_and_requires_fencing_ids():
    with pytest.raises(WorkerConfigurationError, match="INVALID_WORKER_PAYLOAD"):
        StableWorkerMessage.from_payload({"job_id": "j", "attempt": 1, "dispatch_id": "d", "module": "os"})


def test_ready_file_is_removed_on_exit(tmp_path):
    path = tmp_path / "worker.ready"
    with ReadyFile(path) as ready:
        ready.mark_ready()
        marker = json.loads(path.read_text(encoding="utf-8"))
        assert marker["ready"] is True
        assert marker["pid"] == os.getpid()
        assert marker["instance_id"] == ready.instance_id
        assert set(marker) == {"ready", "pid", "instance_id", "boot_id", "process_starttime"}
    assert not path.exists()


def test_old_instance_cannot_remove_new_ready_marker(tmp_path):
    path = tmp_path / "worker.ready"
    boot = lambda: "boot-test"
    start = lambda _pid: "123"
    old = ReadyFile(path, instance_id="old-instance", boot_id_reader=boot, process_start_reader=start)
    new = ReadyFile(path, instance_id="new-instance", boot_id_reader=boot, process_start_reader=start)
    old.mark_ready()
    new.mark_ready()
    old.remove()
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["instance_id"] == "new-instance"
    new.created = True
    new.remove()
    assert not path.exists()


def test_startup_does_not_remove_live_other_instance_marker(tmp_path):
    path = tmp_path / "worker.ready"
    boot = lambda: "boot-test"
    start = lambda _pid: "123"
    owner = ReadyFile(path, pid=41001, instance_id="live", pid_alive=lambda pid: True, boot_id_reader=boot, process_start_reader=start)
    owner.mark_ready()
    contender = ReadyFile(path, pid=41002, instance_id="contender", pid_alive=lambda pid: pid == 41001, boot_id_reader=boot, process_start_reader=start)
    with pytest.raises(WorkerConfigurationError, match="WORKER_ALREADY_RUNNING"):
        contender.clear_existing()
    assert json.loads(path.read_text(encoding="utf-8"))["instance_id"] == "live"


def test_clear_existing_preserves_marker_without_cleanup_race(tmp_path):
    path = tmp_path / "worker.ready"
    old_marker = {"ready": True, "pid": 41001, "instance_id": "old-marker", "boot_id": "boot", "process_starttime": "1"}
    path.write_text(json.dumps(old_marker), encoding="utf-8")
    ready = ReadyFile(path, pid=41003, instance_id="cleaner", pid_alive=lambda _pid: False, boot_id_reader=lambda: "boot", process_start_reader=lambda _pid: "3")
    ready.clear_existing()
    assert json.loads(path.read_text(encoding="utf-8")) == old_marker


def test_readyfile_active_pid_second_instance_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(worker_cli.sys, "platform", "win32")
    path = tmp_path / "worker.ready"
    owner = ReadyFile(path, pid=41001, instance_id="owner-01", pid_alive=lambda pid: pid == 41001, process_start_reader=lambda _pid: "owner-start")
    owner.mark_ready()
    contender = ReadyFile(path, pid=41002, instance_id="contender-01", pid_alive=lambda pid: pid == 41001, process_start_reader=lambda _pid: "owner-start")
    with pytest.raises(WorkerConfigurationError, match="WORKER_ALREADY_RUNNING"):
        contender.clear_existing()
    assert json.loads(path.read_text(encoding="utf-8"))["instance_id"] == "owner-01"
    owner.remove()


def test_readyfile_concurrent_replacement_cannot_overwrite_active_marker(monkeypatch, tmp_path):
    monkeypatch.setattr(worker_cli.sys, "platform", "win32")
    path = tmp_path / "worker.ready"
    first = ReadyFile(path, pid=42001, instance_id="first-001", pid_alive=lambda _pid: False, process_start_reader=lambda _pid: "first-start")
    second = ReadyFile(path, pid=42002, instance_id="second-001", pid_alive=lambda _pid: False, process_start_reader=lambda _pid: "second-start")
    first.clear_existing()
    second.clear_existing()
    first.mark_ready()
    expected = json.loads(path.read_text(encoding="utf-8"))
    second.mark_ready()
    assert second.startup_conflict is True
    assert json.loads(path.read_text(encoding="utf-8")) == expected
    first.remove()


def test_readyfile_stale_or_invalid_marker_is_replaced_atomically(monkeypatch, tmp_path):
    monkeypatch.setattr(worker_cli.sys, "platform", "win32")
    path = tmp_path / "worker.ready"
    path.write_text(json.dumps({"ready": True, "pid": 43001, "instance_id": "stale-001", "boot_id": None, "process_starttime": "old-start"}), encoding="utf-8")
    recovered = ReadyFile(path, pid=43001, instance_id="recovered-1", pid_alive=lambda _pid: True, process_start_reader=lambda _pid: "new-start")
    recovered.clear_existing()
    recovered.mark_ready()
    assert json.loads(path.read_text(encoding="utf-8"))["instance_id"] == "recovered-1"
    recovered.remove()

    path.write_text("{invalid", encoding="utf-8")
    recovered.mark_ready()
    assert json.loads(path.read_text(encoding="utf-8"))["instance_id"] == "recovered-1"
    recovered.remove()


def test_readyfile_pid_reuse_requires_creation_time_match(monkeypatch, tmp_path):
    monkeypatch.setattr(worker_cli.sys, "platform", "win32")
    path = tmp_path / "worker.ready"
    path.write_text(json.dumps({"ready": True, "pid": 44001, "instance_id": "old-001", "boot_id": None, "process_starttime": "old-start"}), encoding="utf-8")
    reused = ReadyFile(path, pid=44001, instance_id="new-001", pid_alive=lambda _pid: True, process_start_reader=lambda _pid: "new-start")
    reused.clear_existing()
    reused.mark_ready()
    marker = json.loads(path.read_text(encoding="utf-8"))
    assert marker["instance_id"] == "new-001"
    assert marker["process_starttime"] == "new-start"
    reused.remove()


def test_readyfile_mark_failure_cleanup_does_not_remove_other_marker(monkeypatch, tmp_path):
    monkeypatch.setattr(worker_cli.sys, "platform", "win32")
    path = tmp_path / "worker.ready"
    old = {"ready": True, "pid": 45001, "instance_id": "other-001", "boot_id": None, "process_starttime": "old-start"}
    fresh = {"ready": True, "pid": 45002, "instance_id": "other-002", "boot_id": None, "process_starttime": "fresh-start"}
    path.write_text(json.dumps(old), encoding="utf-8")
    owner = ReadyFile(path, pid=45003, instance_id="owner-001", pid_alive=lambda _pid: False, process_start_reader=lambda _pid: "owner-start")
    def replace_then_fail(_source, destination):
        destination.write_text(json.dumps(fresh), encoding="utf-8")
        raise OSError("replacement raced")
    monkeypatch.setattr(worker_cli.os, "replace", replace_then_fail)
    with pytest.raises(OSError, match="replacement raced"):
        owner.mark_ready()
    assert json.loads(path.read_text(encoding="utf-8")) == fresh
    assert not owner.path.with_name(f".{path.name}.{owner.pid}.{owner.instance_id}.tmp").exists()


def test_worker_healthcheck_is_read_only(monkeypatch, tmp_path):
    monkeypatch.setattr(worker_cli.sys, "platform", "win32")
    path = tmp_path / "worker.ready"
    path.write_text(json.dumps({"ready": True, "pid": 46001, "instance_id": "health-001", "boot_id": None, "process_starttime": "health-start"}), encoding="utf-8")
    monkeypatch.setattr(Path, "write_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("healthcheck wrote marker")))
    monkeypatch.setattr(Path, "unlink", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("healthcheck removed marker")))
    assert worker_healthcheck(path, pid_alive=lambda _pid: True, process_start_reader=lambda _pid: "health-start", platform="win32") == 0


def test_healthcheck_rejects_missing_file_and_legacy_or_malformed_marker(monkeypatch, tmp_path):
    monkeypatch.delenv("WORKER_READY_FILE", raising=False)
    assert worker_healthcheck(platform="win32") != 0
    path = tmp_path / "worker.ready"
    path.write_text("ready\n", encoding="utf-8")
    assert worker_healthcheck(path, platform="win32") != 0
    path.write_text("{not-json", encoding="utf-8")
    assert worker_healthcheck(path, platform="win32") != 0


def test_healthcheck_rejects_dead_pid_and_wrong_identity(tmp_path):
    path = tmp_path / "worker.ready"
    marker = {"ready": True, "pid": 12345, "instance_id": "valid-instance", "boot_id": None, "process_starttime": None}
    path.write_text(json.dumps(marker), encoding="utf-8")
    assert worker_healthcheck(path, pid_alive=lambda _pid: False, platform="win32") != 0
    marker["pid"] = os.getpid()
    marker["instance_id"] = "bad space"
    path.write_text(json.dumps(marker), encoding="utf-8")
    assert worker_healthcheck(path, pid_alive=lambda _pid: True, platform="win32") != 0


def test_healthcheck_validates_linux_identity_chain_without_db_initialization(tmp_path, monkeypatch):
    path = tmp_path / "worker.ready"
    marker = {"ready": True, "pid": 321, "instance_id": "valid-instance", "boot_id": "boot-a", "process_starttime": "77"}
    path.write_text(json.dumps(marker), encoding="utf-8")
    monkeypatch.setattr(worker_cli, "WorkerRuntime", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("runtime must not initialize")))
    assert worker_cli.main(["--healthcheck"]) != 0  # env is intentionally absent
    monkeypatch.setenv("WORKER_READY_FILE", str(path))
    assert worker_cli.main(["--healthcheck"]) != 0  # host is Windows; injected readers are tested below
    assert worker_healthcheck(path, pid_alive=lambda _pid: True, boot_id_reader=lambda: "boot-a", process_start_reader=lambda _pid: "77", cmdline_reader=lambda _pid: "python -m chrom_platform.worker_cli --healthcheck", platform="linux") == 0


def test_profile_plan_rejects_profiles_without_workers(tmp_path):
    manifest = {
        "manifest_version": "1.0", "build_id": "minimal-structure-v1", "modules": ["utilities"],
        "chromatography_types": [], "prediction_tools": {}, "utilities": ["structure_converter"],
        "export_formats": [], "auth_provider": "deployment_default",
    }
    path = tmp_path / "build-manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(WorkerConfigurationError, match="NO_WORKER_MODULES_SELECTED"):
        ProfileWorkerPlan.from_manifest(path)


def test_profile_plan_rejects_unknown_build_profile(tmp_path):
    manifest = json.loads((__import__("pathlib").Path(__file__).resolve().parents[2] / "builds" / "sfc-v1.json").read_text(encoding="utf-8"))
    manifest["build_id"] = "unapproved-v1"
    path = tmp_path / "build-manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(WorkerConfigurationError, match="PROFILE_UNSUPPORTED"):
        ProfileWorkerPlan.from_manifest(path)


def test_worker_config_rejects_unknown_profile_before_runtime(monkeypatch, tmp_path):
    manifest = tmp_path / "build-manifest.json"
    manifest.write_text((__import__("pathlib").Path(__file__).resolve().parents[2] / "builds" / "sfc-v1.json").read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("CHROM_BUILD_MANIFEST", str(manifest))
    monkeypatch.setenv("DATABASE_URL", "mysql+asyncmy://user:pass@mysql:3306/db")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("WORKER_READY_FILE", str(tmp_path / "worker.ready"))
    monkeypatch.setenv("BUILD_PROFILE", "unapproved-v1")
    with pytest.raises(WorkerConfigurationError, match="PROFILE_UNSUPPORTED"):
        WorkerConfig.from_env()


def test_dramatiq_import_is_deferred_and_missing_dependency_is_explicit(monkeypatch, tmp_path):
    original_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == "dramatiq":
            raise ImportError("dramatiq intentionally absent")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    with pytest.raises(WorkerConfigurationError, match="DRAMATIQ_DEPENDENCY_UNAVAILABLE"):
        DramatiqTransport.create("redis://redis:6379/0", "chrom-platform", lambda _: None)
    assert not (tmp_path / "worker.ready").exists()


def test_runtime_initialization_failure_preserves_stale_ready_until_successful_init(tmp_path):
    manifest = Path(__file__).resolve().parents[2] / "builds" / "sfc-v1.json"
    ready = tmp_path / "worker.ready"
    ready.write_text("ready\n", encoding="utf-8")
    config = WorkerConfig(manifest, "mysql+asyncmy://user:pass@mysql:3306/db", "redis://redis:6379/0", ready, profile="sfc-v1", sync_database_url="mysql+pymysql://user:pass@mysql:3306/db")
    runtime = WorkerRuntime(config, {"job.execute": lambda _: None}, transport_factory=lambda *_: (_ for _ in ()).throw(WorkerConfigurationError("DRAMATIQ_DEPENDENCY_UNAVAILABLE")))
    class FakeEngine:
        async def dispose(self):
            return None

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(worker_cli, "_linux_execution_layer_required", lambda: None)
    monkeypatch.setattr(worker_cli, "create_async_engine", lambda *_args, **_kwargs: FakeEngine())
    monkeypatch.setattr(worker_cli, "create_engine", lambda *_args, **_kwargs: SimpleNamespace(dispose=lambda: None))
    monkeypatch.setattr(runtime, "_check_database_health", lambda: asyncio.sleep(0, result=(True, None)))
    try:
        with pytest.raises(WorkerConfigurationError, match="DRAMATIQ_DEPENDENCY_UNAVAILABLE"):
            runtime.initialize()
    finally:
        monkeypatch.undo()
    assert ready.exists()


def test_default_runtime_binds_production_handlers_before_transport(tmp_path, monkeypatch):
    manifest = Path(__file__).resolve().parents[2] / "builds" / "sfc-v1.json"
    ready = tmp_path / "worker.ready"
    config = WorkerConfig(manifest, "mysql+asyncmy://user:pass@mysql:3306/db", "redis://redis:6379/0", ready, profile="sfc-v1", sync_database_url="mysql+pymysql://user:pass@mysql:3306/db")
    runtime = WorkerRuntime(config)
    monkeypatch.setattr(worker_cli, "_linux_execution_layer_required", lambda: None)
    class FakeEngine:
        async def dispose(self):
            return None

    monkeypatch.setattr(worker_cli, "create_async_engine", lambda *_args, **_kwargs: FakeEngine())
    monkeypatch.setattr(worker_cli, "create_engine", lambda *_args, **_kwargs: SimpleNamespace(dispose=lambda: None))
    monkeypatch.setattr(runtime, "_check_database_health", lambda: asyncio.sleep(0, result=(True, None)))
    if importlib.util.find_spec("dramatiq") is None:
        with pytest.raises(WorkerConfigurationError, match="DRAMATIQ_DEPENDENCY_UNAVAILABLE"):
            runtime.initialize()
    else:
        runtime.initialize()
        assert runtime.handlers
        runtime.shutdown()
    assert not ready.exists()


def _mock_dataset_runtime_init(monkeypatch, tmp_path, *, sync_database_url):
    plan = SimpleNamespace(
        worker_modules=("datasets",),
        allowed_event_types=frozenset({"job.execute", "job.cancel"}),
        allowed_job_kinds=frozenset({"data_import_validation", "data_import_commit"}),
        import_worker_types=lambda: None,
    )
    monkeypatch.setattr(
        worker_cli.ProfileWorkerPlan, "from_manifest",
        classmethod(lambda _cls, *_args, **_kwargs: plan),
    )

    class FakeReadyFile:
        instance_id = "dataset-worker-fixture"
        def __init__(self, _path):
            pass
        def clear_existing(self):
            return None

    class FakeAsyncEngine:
        async def dispose(self):
            return None

    class FakeSyncEngine:
        def dispose(self):
            return None

    class FakeLoop:
        closed = False
        def run_until_complete(self, coroutine):
            try:
                coroutine.send(None)
            except StopIteration as result:
                return result.value
            raise AssertionError("pure worker fixture coroutine unexpectedly suspended")
        def is_closed(self):
            return self.closed
        def close(self):
            self.closed = True

    class FakeAsyncDispatcher:
        def __init__(self, *_args, **_kwargs):
            pass

    class FakeOutboxDispatcher:
        def __init__(self, _core, broker, handlers, **_kwargs):
            self.broker = broker
            self.handlers = handlers
        def consume(self, *_args, **_kwargs):
            return None

    async_engine = FakeAsyncEngine()
    sync_engine = FakeSyncEngine()
    sync_engine_calls = []
    captured = {}
    sync_session_factory = lambda: None
    monkeypatch.setattr(worker_cli, "ReadyFile", FakeReadyFile)
    monkeypatch.setattr(worker_cli, "_linux_execution_layer_required", lambda: None)
    monkeypatch.setattr(worker_cli.asyncio, "new_event_loop", FakeLoop)
    monkeypatch.setitem(worker_cli.sys.modules, "chrom_platform.modules.assistant.adapter", None)
    monkeypatch.setattr(worker_cli, "create_async_engine", lambda *_args, **_kwargs: async_engine)
    monkeypatch.setattr(
        worker_cli, "create_engine",
        lambda url, **_kwargs: sync_engine_calls.append(url) or sync_engine,
    )
    monkeypatch.setattr(worker_cli, "async_sessionmaker", lambda **_kwargs: "async-session-factory")
    monkeypatch.setattr(
        worker_cli, "sessionmaker",
        lambda **_kwargs: sync_session_factory,
    )
    monkeypatch.setattr(worker_cli, "AsyncOutboxDispatcher", FakeAsyncDispatcher)
    monkeypatch.setattr(worker_cli, "OutboxDispatcher", FakeOutboxDispatcher)

    async def healthy(_self):
        return True, None

    monkeypatch.setattr(worker_cli.WorkerRuntime, "_check_database_health", healthy)

    def handler_factory(_plan, **kwargs):
        captured.update(kwargs)
        return {"job.execute": lambda _payload: None}

    runtime = WorkerRuntime(
        WorkerConfig(
            tmp_path / "dataset-manifest.json",
            "mysql+asyncmy://fixture:fixture@127.0.0.1:3306/dataset_fixture",
            "redis://127.0.0.1:6379/15",
            tmp_path / "dataset-worker.ready",
            sync_database_url=sync_database_url,
        ),
        transport_factory=lambda *_args: SimpleNamespace(),
        handler_factory=handler_factory,
    )
    return runtime, captured, sync_engine_calls, sync_session_factory


def test_dataset_runtime_initialization_builds_sync_factory_for_handlers(tmp_path, monkeypatch):
    runtime, captured, sync_engine_calls, sync_session_factory = _mock_dataset_runtime_init(
        monkeypatch, tmp_path,
        sync_database_url="mysql+pymysql://fixture:fixture@127.0.0.1:3306/dataset_fixture",
    )
    try:
        runtime.initialize()
        assert sync_engine_calls == [runtime.config.sync_database_url]
        assert captured["async_session_factory"] == "async-session-factory"
        assert captured["sync_session_factory"] is sync_session_factory
        assert callable(captured["sync_session_factory"])
    finally:
        runtime._close_async_resources()


def test_dataset_runtime_still_requires_sync_database_url(tmp_path, monkeypatch):
    runtime, _captured, sync_engine_calls, _sync_session_factory = _mock_dataset_runtime_init(
        monkeypatch, tmp_path, sync_database_url=None,
    )
    with pytest.raises(WorkerConfigurationError, match="MISSING_SYNC_DATABASE_URL"):
        runtime.initialize()
    assert sync_engine_calls == []


def test_injected_runtime_runner_marks_ready_only_after_init_and_cleans_on_exit(tmp_path):
    ready = tmp_path / "worker.ready"
    config = WorkerConfig(tmp_path / "build-manifest.json", "mysql+asyncmy://user:pass@mysql:3306/db", "redis://redis:6379/0", ready, poll_seconds=0.01, lease_seconds=1, profile="sfc-v1")

    class FakeWorker:
        def start(self):
            self.started = True

        def stop(self):
            self.stopped = True

    class FakeBroker:
        def close(self):
            self.closed = True

    runtime = WorkerRuntime(config, {"job.execute": lambda _: None})
    runtime.loop = asyncio.new_event_loop()
    runtime.transport = SimpleNamespace(worker=FakeWorker(), broker=FakeBroker())
    async def poll_once():
        return False

    runtime.dispatcher = SimpleNamespace(poll_once=poll_once)
    thread = threading.Thread(target=runtime.run)
    thread.start()
    deadline = time.monotonic() + 2
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    marker = json.loads(ready.read_text(encoding="utf-8"))
    assert marker["ready"] is True and marker["pid"] == os.getpid()
    runtime.stop_event.set()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert not ready.exists()
    assert runtime.transport.worker.stopped is True


def test_poll_failure_cleans_ready_and_exits_nonzero(tmp_path):
    ready = tmp_path / "worker.ready"
    config = WorkerConfig(tmp_path / "build-manifest.json", "mysql+asyncmy://user:pass@mysql:3306/db", "redis://redis:6379/0", ready, poll_seconds=0.01, lease_seconds=1, profile="sfc-v1")

    class FakeWorker:
        def start(self):
            return None

        def stop(self):
            return None

    class FakeBroker:
        def close(self):
            return None

    async def poll_once():
        raise ConnectionError("broker unavailable")

    runtime = WorkerRuntime(config, {"job.execute": lambda _: None})
    runtime.loop = asyncio.new_event_loop()
    runtime.transport = SimpleNamespace(worker=FakeWorker(), broker=FakeBroker())
    runtime.dispatcher = SimpleNamespace(poll_once=poll_once)
    with pytest.raises(WorkerConfigurationError, match="WORKER_POLL_FAILED_CONNECTIONERROR"):
        runtime.run()
    assert not ready.exists()


def test_main_signal_handler_requests_graceful_shutdown(monkeypatch):
    signals = {}

    class FakeRuntime:
        def __init__(self, _config):
            self.stop_event = threading.Event()
            self.initialized = False
            self.shutdown_called = False

        def initialize(self):
            self.initialized = True

        def run(self):
            assert self.initialized
            signals[worker_cli.signal.SIGTERM](worker_cli.signal.SIGTERM, None)
            assert self.stop_event.is_set()

        def shutdown(self):
            self.shutdown_called = True

    monkeypatch.setattr(worker_cli.WorkerConfig, "from_env", classmethod(lambda cls: object()))
    monkeypatch.setattr(worker_cli, "WorkerRuntime", FakeRuntime)
    monkeypatch.setattr(worker_cli.signal, "signal", lambda signum, handler: signals.__setitem__(signum, handler))
    assert worker_cli.main([]) == 0


def test_windows_local_worker_is_supported(monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    _linux_execution_layer_required()
