from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from chrom_platform.assembly.m4 import (
    _BoundTraceStore,
    _WorkerHistory,
    _worker_priority_service,
    agent_worker_message_from_job,
    assemble_m4,
    bind_m4,
)
from chrom_platform.core.dto import ActorContext, CapabilityHealth
from chrom_platform.core.errors import APIError
from chrom_platform.infrastructure.models import Job, JobAttempt, JobEvent, OutboxEvent, Project
from chrom_platform.artifact_core.models import Structure
from chrom_platform.modules.assistant.persistence import AgentAttemptRow, AgentRunRow, AssistantBase, ContextSnapshotRow, ConversationRow, LlmCallRow, LlmConfigurationActiveRow, LlmConfigurationRow, SQLAlchemyAssistantRepository
from chrom_platform.modules.assistant.schemas import ConversationCreate, UserMessageCreate
from chrom_platform.modules.assistant.runtime import AgentRuntime, RuntimeConfigSnapshot
from chrom_platform.modules.assistant.trace import ContextInspector
from chrom_platform.modules.assistant.priority import PRIORITY_TOOL_NAMES, PriorityAssistantToolService, PriorityOperationContext
from chrom_platform.infrastructure.models import Base as PlatformBase
from chrom_platform.modules.prediction.adapters import (
    AdapterManifest,
    AdapterCapability,
    CapabilityRegistry,
    ModelVerificationEvidence,
    ModelOption,
    PredictionLimits,
    StructureCount,
    ToolCapability,
    issue_verified_model_handle,
)
from chrom_platform.modules.prediction.persistence import PredictionRow
from chrom_platform.modules.prediction.schemas import TlcPredictionCreate, TlcPredictionResult
from chrom_platform.adapters.tlc.manifest import (
    TLC_ARTIFACT_ID,
    TLC_ARTIFACT_MANIFEST_SHA256,
    TLC_MODEL_VERSION,
    TLC_SCOPE_ID,
)
from chrom_platform.task_core.service import OperationContext


@pytest.fixture(autouse=True)
def _science_free_default_registry(monkeypatch):
    """Inject before construction; these tests prove wiring, never models."""
    from chrom_platform.assembly import m4 as m4_module
    monkeypatch.setattr(m4_module, "build_prediction_worker_registry", lambda **_kwargs: CapabilityRegistry())


def _ctx(*projects: str) -> OperationContext:
    return OperationContext(
        ActorContext(
            actor_id="actor-a",
            tenant_id=None,
            project_ids=list(projects),
            auth_method="deployment_default",
            permissions=["*"],
            policy_version="policy-1",
        ),
        interaction="human",
    )


def _assembly(*, transport_retries: int = 2):
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _sqlite_mysql_functions(dbapi_connection, _connection_record):
        # The production migration targets MySQL; this keeps the contract
        # checks executable with SQLite without changing application code.
        dbapi_connection.create_function("CHAR_LENGTH", 1, lambda value: len(value) if value is not None else None)

    factory = lambda: Session(engine)
    assembly = assemble_m4(include_m2=False, session_factory=factory, transport_retries=transport_retries)
    assembly.metadata.create_all(engine)
    with factory() as session:
        session.add(Project(id="project-a", slug="project-a", name="Project A", status="ACTIVE", revision=1))
        configuration_hash = "a" * 64
        session.add(LlmConfigurationRow(
            id="llm-config-a", project_id="project-a", revision="config-rev-a",
            url="https://api.siliconflow.cn/v1", model_id="test-model",
            key_reference="TEST_LLM_API_KEY", status="ACTIVE", last_test_id=None,
            settings={"config_hash": configuration_hash, "external_send_policy": "approved_endpoint_only"},
            created_at=datetime.now(timezone.utc),
        ))
        session.add(LlmConfigurationActiveRow(
            project_id="project-a", configuration_id="llm-config-a",
            configuration_revision="config-rev-a", revision=1, etag='"active-1"',
            updated_at=datetime.now(timezone.utc),
        ))
        session.commit()
    return engine, factory, assembly


class _ReadyTlcAssemblyAdapter:
    """A contract fixture; production assembly never installs this adapter."""

    type_id = "tlc"
    manifest = None
    verified_handle = None
    request_types = (TlcPredictionCreate,)

    def __init__(self) -> None:
        health = CapabilityHealth(status="READY", checked_at=datetime.now(timezone.utc))
        limits = PredictionLimits(max_rows=100, max_candidates=100, max_evaluations=1000)
        self.manifest = AdapterManifest(
            adapter_id="tlc", model_id=TLC_ARTIFACT_ID, model_version=TLC_MODEL_VERSION,
            business_scope_id=TLC_SCOPE_ID, manifest_version="1", input_schema_version="1.0",
            output_schema_version="1.0", artifact_sha256=TLC_ARTIFACT_MANIFEST_SHA256,
            schema_available=True, assets_available=True, data_origin="production",
        )
        self.verified_handle = issue_verified_model_handle(
            self.manifest,
            ModelVerificationEvidence(
                adapter_id="tlc", model_id=TLC_ARTIFACT_ID, model_version=TLC_MODEL_VERSION,
                business_scope_id=TLC_SCOPE_ID, input_schema_version="1.0", output_schema_version="1.0",
                artifact_sha256=TLC_ARTIFACT_MANIFEST_SHA256, manifest_verified=True,
                schema_verified=True, artifact_sha_verified=True, environment_verified=True,
                golden_sample_verified=True, anomaly_checks_verified=True,
                schema_by_tool={
                    "single": {"input_schema": TlcPredictionCreate.model_json_schema(), "output_schema": TlcPredictionResult.model_json_schema()}
                },
            ),
        )
        self.capability = AdapterCapability(
            id="tlc",
            display_name="TLC",
            tools=[
                ToolCapability(
                    id="single",
                    schema_version="1.0",
                    input_schema=TlcPredictionCreate.model_json_schema(),
                    output_schema=TlcPredictionResult.model_json_schema(),
                    structure_count=StructureCount(min=1, max=1, semantics="single"),
                    model_constraints={"type": "tlc"},
                    units={},
                    supported_visualizations=[],
                    limits=limits,
                    health=health,
                )
            ],
            models=[
                ModelOption(
                    id=TLC_ARTIFACT_ID,
                    version=TLC_MODEL_VERSION,
                    task_type="tlc",
                    business_scope_id=TLC_SCOPE_ID,
                    artifact_sha256=TLC_ARTIFACT_MANIFEST_SHA256,
                    input_schema_version="1.0",
                    output_schema_version="1.0",
                    health=health,
                )
            ],
            limits=limits,
            health=health,
        )

    def validate_request(self, request):
        return request

    def predict(self, _request):
        raise AssertionError("assembly test must stop after formal job creation")


def _tlc_worker_request():
    return TlcPredictionCreate.model_validate(
        {
            "project_id": "project-a",
            "chromatography_type": "tlc",
            "tool": "single",
            "schema_version": "1.0",
            "model_selection": {"mode": "champion", "scope_id": "tlc-default"},
            "structure_refs": ["structure-a"],
            "input": {
                "solvent_components": [
                    {"solvent_id": "hexane", "fraction": 0.8},
                    {"solvent_id": "ethyl_acetate", "fraction": 0.2},
                ]
            },
        }
    )


def test_worker_priority_binds_prediction_port_to_formal_tlc_job_flow() -> None:
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _sqlite_mysql_functions(dbapi_connection, _connection_record):
        dbapi_connection.create_function("CHAR_LENGTH", 1, lambda value: len(value) if value is not None else None)

    factory = lambda: Session(engine)
    assembly = assemble_m4(
        include_m2=True, m3_modules={"model_center"}, session_factory=factory
    )
    assembly.metadata.create_all(engine)
    with factory() as session:
        session.add(Project(id="project-a", slug="project-a", name="Project A", status="ACTIVE", revision=1))
        session.add(
            Structure(
                id="structure-a", project_id="project-a", schema_version="1.0",
                original_input={"smiles": "CCO"}, canonical_smiles="CCO", is_valid=True,
                has_stereochemistry=False, stereochemistry_status="not_applicable",
                stereochemistry_warnings=[], snapshot_hash="b" * 64,
            )
        )
        session.commit()

    registry = CapabilityRegistry()
    registry.register(_ReadyTlcAssemblyAdapter())
    service = _worker_priority_service(factory, model_center_enabled=True, prediction_registry=registry)
    assert service is not None
    assert service.prediction is not None
    capabilities = service.capabilities(_ctx("project-a"))
    tlc = next(item for item in capabilities if item.id == "tlc")
    assert tlc.health.status == "READY"
    assert service.list_models(_ctx("project-a"), "project-a") == []

    accepted = service.create_prediction(
        _ctx("project-a"), _tlc_worker_request(), idempotency_key="assistant-tlc-create-1"
    )
    assert accepted.status_code == 202
    with factory() as session:
        prediction = session.get(PredictionRow, accepted.id)
        job = session.get(Job, accepted.job_id)
        attempt = session.scalar(select(JobAttempt).where(JobAttempt.job_id == accepted.job_id, JobAttempt.attempt == 1))
        outbox = session.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_id == accepted.job_id, OutboxEvent.event_type == "prediction.execute"))
        assert prediction is not None and prediction.status == "QUEUED"
        assert job is not None and job.kind == "prediction" and job.resource_ref == {"type": "prediction", "id": accepted.id}
        assert attempt is not None and attempt.status == "QUEUED"
        assert outbox is not None and outbox.status == "PENDING"
        assert outbox.payload["job_id"] == accepted.job_id and outbox.payload["attempt"] == 1
    readback = service.get_prediction(_ctx("project-a"), accepted.id)
    assert readback.id == accepted.id and readback.job_id == accepted.job_id
    summary = service.prediction_record_summary(_ctx("project-a"), "project-a", chromatography_type="tlc")
    assert accepted.prediction_record_id in summary.source.source_ids


def test_worker_priority_missing_prediction_registry_fails_closed(monkeypatch) -> None:
    import chrom_platform.assembly.m4 as m4

    def _missing_registry(**_kwargs):
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(m4, "build_prediction_worker_registry", _missing_registry)
    service = _worker_priority_service(lambda: Session(create_engine("sqlite:///:memory:")), model_center_enabled=True)
    assert service is not None and service.prediction is None
    with pytest.raises(Exception) as exc_info:
        service.capabilities(_ctx("project-a"))
    assert getattr(exc_info.value, "payload", None).code == "DEPENDENCY_UNAVAILABLE"


def test_worker_priority_factory_injects_every_declared_tool_port() -> None:
    engine = create_engine("sqlite:///:memory:")
    factory = lambda: Session(engine)
    service = _worker_priority_service(factory, model_center_enabled=True, prediction_registry=CapabilityRegistry())
    assert service is not None
    assert PriorityAssistantToolService.executable_tool_names() == frozenset(PRIORITY_TOOL_NAMES)
    port_by_tool = {
        "normalize_structure": service.structures,
        "get_prediction_capabilities": service.capabilities_port,
        "create_prediction": service.prediction,
        "get_prediction": service.prediction,
        "get_data_version_summary": service.data_versions,
        "get_prediction_record_summary": service.records,
        "list_models": service.models,
        "get_model_card": service.models,
        "create_registration_draft": service.models,
        "get_registration_draft": service.models,
    }
    assert set(port_by_tool) == set(PRIORITY_TOOL_NAMES)
    assert all(value is not None for value in port_by_tool.values())


def test_worker_priority_structure_tool_reuses_persisted_structure_and_prediction_flow() -> None:
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _sqlite_mysql_functions(dbapi_connection, _connection_record):
        dbapi_connection.create_function("CHAR_LENGTH", 1, lambda value: len(value) if value is not None else None)

    factory = lambda: Session(engine)
    assembly = assemble_m4(include_m2=True, session_factory=factory)
    assembly.metadata.create_all(engine)
    with factory() as session:
        session.add_all([
            Project(id="project-a", slug="project-a", name="Project A", status="ACTIVE", revision=1),
            Project(id="project-b", slug="project-b", name="Project B", status="ACTIVE", revision=1),
        ])
        session.commit()

    registry = CapabilityRegistry()
    registry.register(_ReadyTlcAssemblyAdapter())
    service = _worker_priority_service(factory, model_center_enabled=False, prediction_registry=registry)
    assert service is not None
    tools = PriorityAssistantToolService(service)
    actor = ActorContext(actor_id="actor-a", project_ids=["project-a"], auth_method="service", permissions=["*"], policy_version="p")
    ctx = PriorityOperationContext(actor=actor, server_project_id="project-a", idempotency_key="assistant-structure-chain")
    structure_args = {"input_type": "smiles", "value": "CCO", "preserve_stereochemistry": True}
    tools.validate(ctx, tool_name="normalize_structure", arguments=structure_args)
    normalized = tools.execute(ctx, tool_name="normalize_structure", arguments=structure_args)
    replay = tools.execute(ctx, tool_name="normalize_structure", arguments=structure_args)
    assert normalized.structure_ref.id == replay.structure_ref.id
    assert normalized.resource_ref == normalized.structure_ref

    prediction_args = _tlc_worker_request().model_dump(mode="json")
    prediction_args["project_id"] = "project-a"
    prediction_args["structure_refs"] = [normalized.structure_ref.id]
    tools.validate(ctx, tool_name="create_prediction", arguments=prediction_args)
    accepted = tools.execute(ctx, tool_name="create_prediction", arguments=prediction_args)
    readback = tools.execute(ctx, tool_name="get_prediction", arguments={"prediction_id": accepted.id})
    assert readback.id == accepted.id
    with factory() as session:
        structure = session.get(Structure, normalized.structure_ref.id)
        prediction = session.get(PredictionRow, accepted.id)
        job = session.get(Job, accepted.job_id)
        attempt = session.scalar(select(JobAttempt).where(JobAttempt.job_id == accepted.job_id, JobAttempt.attempt == 1))
        outbox = session.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_id == accepted.job_id, OutboxEvent.event_type == "prediction.execute"))
        assert structure is not None and structure.project_id == "project-a" and structure.canonical_smiles == "CCO"
        assert prediction is not None and prediction.project_id == "project-a"
        assert job is not None and attempt is not None and outbox is not None

    stereo_ctx = ctx.__class__(**{**ctx.__dict__, "idempotency_key": "assistant-structure-stereo"})
    stereo = tools.execute(stereo_ctx, tool_name="normalize_structure", arguments={**structure_args, "value": "C[C@H](O)c1ccccc1"})
    assert stereo.has_stereochemistry and stereo.stereochemistry_status == "specified"
    second = tools.execute(ctx.__class__(**{**ctx.__dict__, "idempotency_key": "assistant-structure-second"}), tool_name="normalize_structure", arguments={**structure_args, "value": "CC"})
    assert second.structure_ref.id != normalized.structure_ref.id
    with factory() as session:
        rows = session.scalars(select(Structure).where(Structure.project_id == "project-a")).all()
        assert len(rows) == 3
    with pytest.raises(APIError) as invalid:
        tools.execute(ctx.__class__(**{**ctx.__dict__, "idempotency_key": "assistant-structure-invalid"}), tool_name="normalize_structure", arguments={**structure_args, "value": "not a smiles"})
    assert invalid.value.payload.code == "VALIDATION_FAILED"

    other_actor = ActorContext(actor_id="actor-b", project_ids=["project-b"], auth_method="service", permissions=["*"], policy_version="p")
    other_ctx = PriorityOperationContext(actor=other_actor, server_project_id="project-b", idempotency_key="assistant-cross-project")
    cross_args = {**prediction_args, "project_id": "project-a"}
    with pytest.raises(Exception):
        tools.validate(other_ctx, tool_name="create_prediction", arguments=cross_args)


class _RetryingProvider:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            raise ConnectionError("temporary provider failure")
        return {"text": "worker-ok", "usage": {"input_tokens": 4, "output_tokens": 2}}


def test_m4_binds_assistant_run_to_platform_job_and_outbox() -> None:
    engine, factory, assembly = _assembly()
    ctx = _ctx("project-a")
    with factory() as session:
        session.begin()
        ports = bind_m4(assembly, session)
        conversation = ports.service.create_conversation(
            ctx,
            ConversationCreate(project_id="project-a", mode="knowledge"),
            idempotency_key="conversation-key",
            session=session,
        )
        accepted = ports.service.send_message(
            ctx,
            conversation.id,
            UserMessageCreate(content="hello", client_message_id="client-1"),
            idempotency_key="message-key",
            session=session,
        )
        assert ports.worker is not None
        job = session.scalar(select(Job).where(Job.kind == "assistant", Job.owner_id == "actor-a"))
        assert job is not None
        assert job.project_id == "project-a"
        assert job.resource_ref == {"type": "agent_run", "id": accepted.agent_run_id}
        assert session.scalar(select(JobAttempt).where(JobAttempt.job_id == job.id, JobAttempt.attempt == 1)) is not None
        assert session.scalar(select(JobEvent).where(JobEvent.job_id == job.id, JobEvent.type == "job.queued")) is not None
        outbox = session.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_id == job.id, OutboxEvent.event_type == "job.execute"))
        assert outbox is not None
        assert outbox.aggregate_type == "job"
        assert set(outbox.payload) == {"job_id", "attempt", "dispatch_id"}
        assert outbox.payload["job_id"] == job.id and outbox.payload["attempt"] == 1 and outbox.payload["dispatch_id"] == outbox.dispatch_id
        assert session.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_type == "agent_run")) is None
        run_row = session.scalar(select(AgentRunRow).where(AgentRunRow.id == accepted.agent_run_id))
        assert run_row.related_resources == [{"type": "job", "id": job.id}]
        session.commit()

    with factory() as session:
        session.begin()
        ports = bind_m4(assembly, session)
        replay = ports.service.send_message(
            ctx,
            conversation.id,
            UserMessageCreate(content="hello", client_message_id="client-1"),
            idempotency_key="message-key",
            session=session,
        )
        assert replay.agent_run_id == accepted.agent_run_id
        session.rollback()


def test_m4_worker_handles_platform_job_without_owning_outbox() -> None:
    _engine, factory, assembly = _assembly()
    ctx = _ctx("project-a")
    with factory() as session:
        session.begin()
        ports = bind_m4(assembly, session)
        conversation = ports.service.create_conversation(
            ctx,
            ConversationCreate(project_id="project-a", mode="knowledge"),
            idempotency_key="conversation-key",
            session=session,
        )
        accepted = ports.service.send_message(
            ctx,
            conversation.id,
            UserMessageCreate(content="hello", client_message_id="client-1"),
            idempotency_key="message-key",
            session=session,
        )
        session.commit()

    with factory() as session:
        session.begin()
        ports = bind_m4(assembly, session)
        job = session.scalar(select(Job).where(Job.kind == "assistant", Job.owner_id == "actor-a"))
        job_id = job.id
        outbox = session.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_id == job.id, OutboxEvent.event_type == "job.execute"))
        result = ports.worker.handle_job(job, dict(outbox.payload))
        assert result.status == "FAILED"
        assert result.error and result.error["code"] == "ASYNC_WORKER_REQUIRED"
        session.commit()

    with factory() as session:
        assert session.scalar(select(AgentRunRow.status).where(AgentRunRow.id == accepted.agent_run_id)) == "FAILED"
        assert session.scalar(select(OutboxEvent.status).where(OutboxEvent.aggregate_id == job_id)) == "PENDING"


def test_worker_history_reads_persisted_text_blocks() -> None:
    _engine, factory, assembly = _assembly()
    ctx = _ctx("project-a")
    with factory() as session:
        session.begin()
        ports = bind_m4(assembly, session)
        conversation = ports.service.create_conversation(
            ctx,
            ConversationCreate(project_id="project-a", mode="knowledge"),
            idempotency_key="history-conversation-key",
            session=session,
        )
        accepted = ports.service.send_message(
            ctx,
            conversation.id,
            UserMessageCreate(content="synthetic history message", client_message_id="history-client-1"),
            idempotency_key="history-message-key",
            session=session,
        )
        session.commit()

    history = _WorkerHistory(SQLAlchemyAssistantRepository(session_factory=factory)).load_history(accepted.agent_run_id)
    assert history == [{"role": "user", "content": "synthetic history message"}]


def test_m4_sync_worker_rejects_native_runtime_without_provider_dispatch() -> None:
    _engine, factory, assembly = _assembly(transport_retries=1)
    ctx = _ctx("project-a")
    with factory() as session:
        session.begin()
        ports = bind_m4(assembly, session)
        conversation = ports.service.create_conversation(
            ctx,
            ConversationCreate(project_id="project-a", mode="knowledge"),
            idempotency_key="conversation-key",
            session=session,
        )
        accepted = ports.service.send_message(
            ctx,
            conversation.id,
            UserMessageCreate(content="hello", client_message_id="client-1"),
            idempotency_key="message-key",
            session=session,
        )
        session.commit()

    with factory() as session:
        session.begin()
        ports = bind_m4(assembly, session)
        provider = _RetryingProvider()
        ports.worker.worker.runtime.provider = provider
        job = session.scalar(select(Job).where(Job.kind == "assistant", Job.owner_id == "actor-a"))
        outbox = session.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_id == job.id, OutboxEvent.event_type == "job.execute"))
        result = ports.worker.handle_job(job, dict(outbox.payload))
        # The synchronous candidate must never manufacture a native Agent
        # loop or fall back to the old Provider.complete protocol.
        assert result.status == "FAILED"
        assert result.error["code"] == "ASYNC_WORKER_REQUIRED"
        assert provider.calls == 0
        session.commit()

    with factory() as session:
        run = session.scalar(select(AgentRunRow).where(AgentRunRow.id == accepted.agent_run_id))
        calls = list(session.scalars(select(LlmCallRow).where(LlmCallRow.agent_run_id == accepted.agent_run_id).order_by(LlmCallRow.transport_attempt)))
        snapshots = list(session.scalars(select(ContextSnapshotRow).where(ContextSnapshotRow.llm_call_id.in_([call.id for call in calls]))))
        assert run is not None and run.status == "FAILED"
        assert calls == []
        assert snapshots == []


def test_m4_trace_scope_binds_run_project_and_owner() -> None:
    _engine, factory, assembly = _assembly()
    ctx = _ctx("project-a")
    with factory() as session:
        session.begin()
        ports = bind_m4(assembly, session)
        conversation = ports.service.create_conversation(
            ctx,
            ConversationCreate(project_id="project-a", mode="knowledge"),
            idempotency_key="conversation-key",
            session=session,
        )
        accepted = ports.service.send_message(
            ctx,
            conversation.id,
            UserMessageCreate(content="hello", client_message_id="client-1"),
            idempotency_key="message-key",
            session=session,
        )
        checker = ports.trace_inspector.scope_checker
        checker(ctx.actor, accepted.agent_run_id)
        try:
            checker(_ctx("project-other").actor, accepted.agent_run_id)
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 404
        else:
            raise AssertionError("trace scope accepted an inaccessible project")
        session.rollback()


def test_sql_trace_uses_persisted_attempt_id_with_foreign_keys_enabled() -> None:
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _sqlite_constraints(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")
        dbapi_connection.create_function("CHAR_LENGTH", 1, lambda value: len(value) if value is not None else None)

    PlatformBase.metadata.create_all(engine)
    AssistantBase.metadata.create_all(engine)
    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        session.add(Project(id="project-a", slug="project-a", name="Project A", status="ACTIVE", revision=1))
        session.flush()
        session.add(ConversationRow(id="conv-trace", project_id="project-a", owner_id="actor-a", title="Trace", mode="knowledge", status="ACTIVE", revision=1, etag="e", created_at=now, updated_at=now))
        session.flush()
        session.add(AgentRunRow(id="run-trace", conversation_id="conv-trace", project_id="project-a", input_message_id="msg-trace", mode="knowledge", status="RUNNING", attempt=1, model_id="m", prompt_version="p", toolset_version="t", policy_version="policy", created_at=now, updated_at=now, revision=1, etag="e"))
        session.flush()
        attempt = AgentAttemptRow(id="attempt-persisted", agent_run_id="run-trace", attempt=1, status="RUNNING", created_at=now)
        session.add(attempt)
        session.flush()
        repository = SQLAlchemyAssistantRepository(session)
        inspector = ContextInspector(_BoundTraceStore(repository, session), mode="redacted")

        class Provider:
            def complete(self, **_kwargs):
                return {"text": "ok", "usage": {}}

        runtime = AgentRuntime(
            Provider(),
            RuntimeConfigSnapshot("https://api.openai.com/v1", "model", "key-ref", "rev", provider_configuration_id="cfg"),
            history=[{"role": "user", "content": "hello"}],
            context_inspector=inspector,
            trace_metadata_only=True,
            max_retries=0,
        )
        result = runtime.legacy_run_for_tests(run_id="run-trace", attempt=1, ctx=type("Ctx", (), {"project_id": "project-a", "agent_attempt_id": attempt.id})())
        session.flush()
        call = session.scalar(select(LlmCallRow).where(LlmCallRow.agent_run_id == "run-trace"))
        snapshots = list(session.scalars(select(ContextSnapshotRow).where(ContextSnapshotRow.llm_call_id == call.id)))
        assert result.status == "SUCCEEDED"
        assert call is not None and call.agent_attempt_id == attempt.id
        assert len(snapshots) == 2


def test_m4_execution_ledger_persists_unknown_fence() -> None:
    _engine, factory, assembly = _assembly()
    with factory() as session:
        session.begin()
        ports = bind_m4(assembly, session)
        with pytest.raises(RuntimeError):
            ports.execution_ledger.execute(
                tool_name="create_prediction",
                normalized_args={"x": 1},
                target_revision={"revision": 1},
                run_id="run-ledger",
                fn=lambda: (_ for _ in ()).throw(RuntimeError("provider lost after submit")),
            )
        key = ports.execution_ledger.operation_key(
            tool_name="create_prediction",
            normalized_args={"x": 1},
            target_revision={"revision": 1},
            run_id="run-ledger",
        )
        assert ports.execution_ledger.get(key).status == "UNKNOWN"
        session.rollback()


def test_platform_job_adapter_requires_exact_job_route_metadata() -> None:
    job = type("Job", (), {"id": "job-1", "kind": "assistant", "attempt": 1, "resource_ref": {"type": "agent_run", "id": "run-1"}})()
    message = agent_worker_message_from_job(job, {"job_id": "job-1", "attempt": 1, "dispatch_id": "dispatch-1"})
    assert message.as_dict() == {"run_id": "run-1", "attempt": 1, "dispatch_id": "dispatch-1"}
    with pytest.raises(ValueError):
        agent_worker_message_from_job(job, {"run_id": "run-1", "attempt": 1, "dispatch_id": "dispatch-1"})
    with pytest.raises(ValueError):
        agent_worker_message_from_job(type("Job", (), {"id": "job-1", "kind": "training", "attempt": 1, "resource_ref": {"type": "agent_run", "id": "run-1"}})(), {"job_id": "job-1", "attempt": 1, "dispatch_id": "dispatch-1"})
