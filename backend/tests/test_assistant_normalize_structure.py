from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import Session

from chrom_platform.core.dto import ActorContext
from chrom_platform.core.projects import ConfiguredProjectReader
from chrom_platform.core.errors import APIError
from chrom_platform.core.errors import APIError
from chrom_platform.infrastructure.models import AuditEvent, Base, IdempotencyRecord, Project
from chrom_platform.artifact_core.models import Structure
from chrom_platform.infrastructure.structure_uow import SQLAlchemyStructureUnitOfWork, _SQLAlchemyStructureAuditRepository
from chrom_platform.modules.assistant.priority import PriorityAssistantService, PriorityAssistantToolService, PriorityOperationContext
from chrom_platform.modules.assistant.priority import priority_toolset
from chrom_platform.modules.assistant.runtime import AgentRuntime, RuntimeConfigSnapshot
from chrom_platform.modules.assistant.tools import BoundTool, ToolSpec
from chrom_platform.modules.assistant.errors import AssistantError
from chrom_platform.structure_core.router import _normalize_in_transaction
from chrom_platform.structure_core.router import create_router as create_structure_router
from chrom_platform.structure_core.schemas import NormalizeStructureRequest, OriginalInput, StructureView
from chrom_platform.structure_core.service import StructureService
from chrom_platform.core.idempotency import IdempotencyScope


class _Provider:
    def __init__(self, response):
        self.responses = iter(response if isinstance(response, list) else [response])

    def complete(self, **_kwargs):
        return next(self.responses)


class _CaptureToolService:
    def __init__(self):
        self.calls = []

    def validate(self, _ctx, **_kwargs):
        return None

    def execute(self, ctx, **kwargs):
        self.calls.append((getattr(ctx, "tool_call_id", None), getattr(ctx, "tool_call_attempt", None)))
        return {"resource_ref": {"type": "structure", "id": "str-safe"}}


def _actor(*projects: str) -> ActorContext:
    return ActorContext(
        actor_id="actor-a", project_ids=list(projects), auth_method="service",
        permissions=["*"], policy_version="p1", default_project_id=projects[0],
    )


def test_runtime_gives_each_normalize_call_a_stable_independent_call_id():
    service = _CaptureToolService()
    tool = BoundTool(ToolSpec("normalize_structure", "normalize", argument_schema={"type": "object"}), service)
    response = {"tool_calls": [
        {"name": "normalize_structure", "arguments": {"value": "CCO"}},
        {"name": "normalize_structure", "arguments": {"value": "CCO"}},
    ]}
    runtime = AgentRuntime(
        _Provider([response, {"text": "done"}]), RuntimeConfigSnapshot("https://api.openai.com/v1", "model", "KEY", "r1"),
        history=[{"role": "user", "content": "normalize"}], toolset={"normalize_structure": tool}, max_retries=0,
    )
    result = runtime.legacy_run_for_tests(run_id="run-normalize", attempt=2, ctx=SimpleNamespace(actor=_actor("project-a"), agent_run_id="run-normalize", agent_attempt_id="attempt-a"))
    assert result.status == "SUCCEEDED"
    assert len(service.calls) == 2
    assert service.calls[0][0] != service.calls[1][0]
    assert all("CCO" not in str(item[0]) for item in service.calls)

    retry_service = _CaptureToolService()
    retry_runtime = AgentRuntime(
        _Provider({"tool_calls": [{"name": "normalize_structure", "arguments": {"value": "CCO"}}]}),
        RuntimeConfigSnapshot("https://api.openai.com/v1", "model", "KEY", "r1"),
        history=[{"role": "user", "content": "normalize"}],
        toolset={"normalize_structure": BoundTool(ToolSpec("normalize_structure", "normalize", argument_schema={"type": "object"}), retry_service)},
        max_retries=0,
    )
    retry_runtime.legacy_run_for_tests(run_id="run-normalize", attempt=2, ctx=SimpleNamespace(actor=_actor("project-a"), agent_run_id="run-normalize", agent_attempt_id="attempt-a"))
    assert retry_service.calls[0][0] == service.calls[0][0]

    recovered_service = _CaptureToolService()
    recovered_runtime = AgentRuntime(
        _Provider({"tool_calls": [{"id": "provider-call", "name": "normalize_structure", "arguments": {"value": "CCO"}}]}),
        RuntimeConfigSnapshot("https://api.openai.com/v1", "model", "KEY", "r1"),
        history=[{"role": "user", "content": "normalize"}],
        toolset={"normalize_structure": BoundTool(ToolSpec("normalize_structure", "normalize", argument_schema={"type": "object"}), recovered_service)},
        max_retries=0,
    )
    recovered_runtime.legacy_run_for_tests(run_id="run-normalize", attempt=3, ctx=SimpleNamespace(actor=_actor("project-a"), agent_run_id="run-normalize", agent_attempt_id="attempt-b"))
    assert recovered_service.calls[0][0] != service.calls[0][0]


class _StructurePort:
    def __init__(self):
        self.keys = []

    def normalize(self, _ctx, request, *, idempotency_key):
        self.keys.append(idempotency_key)
        now = datetime.now(timezone.utc)
        return StructureView(
            id="str-1", project_id=request.project_id, schema_version="1.0",
            original_input=OriginalInput(input_type="smiles", value=request.value), canonical_smiles="CCO",
            is_valid=True, has_stereochemistry=False, stereochemistry_status="not_applicable",
            stereochemistry_warnings=[], snapshot_hash="a" * 64, created_at=now,
        )


def test_priority_normalize_key_is_per_tool_call_and_retry_stable():
    structures = _StructurePort()
    service = PriorityAssistantToolService(PriorityAssistantService(structures=structures))
    ctx = PriorityOperationContext(actor=_actor("project-a"), server_project_id="project-a", agent_run_id="run-a", tool_call_id="call-a", tool_call_attempt=1)
    args = {"project_id": "project-a", "input_type": "smiles", "value": "C[C@H](O)N", "preserve_stereochemistry": True}
    service.execute(ctx, tool_name="normalize_structure", arguments=args)
    service.execute(ctx, tool_name="normalize_structure", arguments=args)
    service.execute(replace(ctx, tool_call_id="call-b"), tool_name="normalize_structure", arguments=args)
    assert structures.keys[0] == structures.keys[1]
    assert structures.keys[0] != structures.keys[2]
    assert "C[C" not in structures.keys[0]


def test_priority_normalize_rejects_cross_project_structure():
    service = PriorityAssistantToolService(PriorityAssistantService(structures=_StructurePort()))
    ctx = PriorityOperationContext(actor=_actor("project-a"), server_project_id="project-a", agent_run_id="run-a", tool_call_id="call-a")
    with pytest.raises(AssistantError) as error:
        service.execute(ctx, tool_name="normalize_structure", arguments={
            "project_id": "project-b", "input_type": "smiles", "value": "CCO", "preserve_stereochemistry": True,
        })
    assert error.value.payload.code == "RESOURCE_NOT_FOUND"


def test_priority_normalize_rejects_read_only_actor_before_persistence():
    structures = _StructurePort()
    service = PriorityAssistantToolService(PriorityAssistantService(structures=structures))
    bound = priority_toolset().bind({"priority": service})
    actor = ActorContext(
        actor_id="actor-a", project_ids=["project-a"], auth_method="deployment_default",
        permissions=["prediction:read"], policy_version="p1",
    )
    ctx = PriorityOperationContext(actor=actor, server_project_id="project-a", tool_call_id="normalize-read-only")

    with pytest.raises(AssistantError) as error:
        bound["normalize_structure"](ctx, {
            "project_id": "project-a", "input_type": "smiles", "value": "CCO", "preserve_stereochemistry": True,
        })

    assert error.value.payload.code == "PERMISSION_DENIED"
    assert structures.keys == []


def test_priority_normalize_service_rejects_read_only_actor_before_persistence():
    structures = _StructurePort()
    service = PriorityAssistantService(structures=structures)
    actor = ActorContext(
        actor_id="actor-a", project_ids=["project-a"], auth_method="deployment_default",
        permissions=["prediction:read"], policy_version="p1",
    )
    request = NormalizeStructureRequest(
        project_id="project-a", input_type="smiles", value="CCO", preserve_stereochemistry=True,
    )
    ctx = PriorityOperationContext(actor=actor, server_project_id="project-a")

    with pytest.raises(AssistantError) as error:
        service.normalize_structure(ctx, request, idempotency_key="read-only")

    assert error.value.payload.code == "PERMISSION_DENIED"
    assert structures.keys == []


def test_priority_normalize_allows_prediction_creator_to_prepare_structure():
    structures = _StructurePort()
    service = PriorityAssistantToolService(PriorityAssistantService(structures=structures))
    bound = priority_toolset().bind({"priority": service})
    actor = ActorContext(
        actor_id="actor-a", project_ids=["project-a"], auth_method="deployment_default",
        permissions=["prediction:create"], policy_version="p1",
    )
    ctx = PriorityOperationContext(actor=actor, server_project_id="project-a", tool_call_id="normalize-for-prediction")

    result = bound["normalize_structure"](ctx, {
        "project_id": "project-a", "input_type": "smiles", "value": "CCO", "preserve_stereochemistry": True,
    })

    assert result.result.structure_ref.id == "str-1"
    expected_key = "assistant-tool:" + hashlib.sha256("unknown|normalize-for-prediction".encode("utf-8")).hexdigest()
    assert structures.keys == [expected_key]


def test_tool_specific_permission_is_not_bypassed_by_assistant_use():
    service = _CaptureToolService()
    tool = BoundTool(ToolSpec("create_prediction", "create", permission="prediction:create", argument_schema={"type": "object"}), service)
    actor = ActorContext(
        actor_id="actor-a", project_ids=["project-a"], auth_method="deployment_default",
        permissions=["assistant:use"], policy_version="p1",
    )

    with pytest.raises(AssistantError) as error:
        tool(SimpleNamespace(actor=actor), {})

    assert error.value.payload.code == "PERMISSION_DENIED"
    assert service.calls == []


def _structure_route_client(actor: ActorContext):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    event.listen(engine, "connect", lambda dbapi, _: dbapi.create_function("CHAR_LENGTH", 1, len))
    event.listen(engine, "begin", lambda connection: connection.exec_driver_sql("BEGIN"))
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Project(id="project-a", slug="project-a", name="A", status="ACTIVE", revision=1))
        session.commit()
    app = FastAPI()
    app.state.actor_context = actor
    app.state.project_reader = ConfiguredProjectReader(actor.project_ids)
    app.state.structure_service = StructureService(None)
    app.state.structure_uow = SQLAlchemyStructureUnitOfWork(lambda: Session(engine))
    app.state.raise_api_error = lambda status_code, code, message, **kwargs: (_ for _ in ()).throw(APIError(status_code, code, message, retryable=kwargs.get("retryable", False)))

    @app.middleware("http")
    async def _request_id(request, call_next):
        request.state.request_id = "test-structure-normalize"
        return await call_next(request)

    app.include_router(create_structure_router(), prefix="/api/v1")

    @app.exception_handler(APIError)
    async def _api_error_handler(_request, exc):
        return JSONResponse(status_code=exc.status_code, content=exc.payload.model_dump(mode="json"))

    return app, engine


def test_structure_normalize_route_rejects_read_only_actor_before_persistence():
    actor = ActorContext(
        actor_id="actor-a", project_ids=["project-a"], auth_method="deployment_default",
        permissions=["prediction:read"], policy_version="p1",
    )
    app, engine = _structure_route_client(actor)
    try:
        response = TestClient(app).post(
            "/api/v1/structures/normalize", headers={"Idempotency-Key": "read-only"},
            json={"project_id": "project-a", "input_type": "smiles", "value": "CCO", "preserve_stereochemistry": True},
        )
        assert response.status_code == 403
        if "error" in response.json():
            assert response.json()["error"]["code"] == "PERMISSION_DENIED"
        with Session(engine) as session:
            assert session.scalar(select(Structure)) is None
    finally:
        engine.dispose()


def test_structure_normalize_route_accepts_prediction_creator():
    actor = ActorContext(
        actor_id="actor-a", project_ids=["project-a"], auth_method="deployment_default",
        permissions=["prediction:create"], policy_version="p1",
    )
    app, engine = _structure_route_client(actor)
    try:
        response = TestClient(app).post(
            "/api/v1/structures/normalize", headers={"Idempotency-Key": "prediction-create"},
            json={"project_id": "project-a", "input_type": "smiles", "value": "CCO", "preserve_stereochemistry": True},
        )
        assert response.status_code == 201
        assert response.json()["data"]["project_id"] == "project-a"
        with Session(engine) as session:
            assert session.scalar(select(Structure)) is not None
    finally:
        engine.dispose()


def _sqlite_engine():
    engine = create_engine("sqlite:///:memory:")
    event.listen(engine, "connect", lambda dbapi, _: dbapi.create_function("CHAR_LENGTH", 1, len))
    event.listen(engine, "begin", lambda connection: connection.exec_driver_sql("BEGIN"))
    Base.metadata.create_all(engine)
    return engine


def test_sqlalchemy_normalize_writes_audit_in_same_transaction_without_raw_smiles():
    engine = _sqlite_engine()
    with Session(engine) as session:
        session.add(Project(id="project-a", slug="project-a", name="A", status="ACTIVE", revision=1))
        session.commit()
    uow = SQLAlchemyStructureUnitOfWork(lambda: Session(engine))
    scope = IdempotencyScope("actor-a", "project-a", "POST", "/api/v1/structures/normalize", "assistant-tool:key")
    payload = NormalizeStructureRequest(project_id="project-a", input_type="smiles", value="C[C@H](O)N", preserve_stereochemistry=True)
    envelope, replayed = _normalize_in_transaction(
        service=StructureService(None),
        uow=uow, payload=payload, scope=scope, payload_hash="b" * 64, structure_id="str-a",
        request_id="worker:run-a:1", agent_run_id="run-a", attempt=2,
    )
    assert replayed is False and envelope.data.id == "str-a"
    with Session(engine) as session:
        audit = session.scalar(select(AuditEvent).where(AuditEvent.agent_run_id == "run-a"))
        assert audit is not None
        assert audit.actor_id == "actor-a" and audit.project_id == "project-a"
        assert audit.action == "structure.normalize" and audit.result == "SUCCEEDED"
        assert audit.resource_ref == {"type": "structure", "id": "str-a"}
        assert audit.after_summary["attempt"] == 2
        assert "C[C" not in str(audit.after_summary)
        assert session.scalar(select(Structure).where(Structure.id == "str-a")) is not None


def test_sqlalchemy_normalize_audit_failure_rolls_back_structure_and_idempotency(monkeypatch):
    engine = _sqlite_engine()
    with Session(engine) as session:
        session.add(Project(id="project-a", slug="project-a", name="A", status="ACTIVE", revision=1))
        session.commit()
    def fail(_self, _event):
        raise RuntimeError("audit unavailable")
    monkeypatch.setattr(_SQLAlchemyStructureAuditRepository, "record", fail)
    with pytest.raises(APIError) as error:
        _normalize_in_transaction(
            service=StructureService(None),
            uow=SQLAlchemyStructureUnitOfWork(lambda: Session(engine)),
            payload=NormalizeStructureRequest(project_id="project-a", input_type="smiles", value="CCO", preserve_stereochemistry=True),
            scope=IdempotencyScope("actor-a", "project-a", "POST", "/api/v1/structures/normalize", "audit-fail"),
            payload_hash="c" * 64, structure_id="str-fail", request_id="worker:run-fail:1",
        )
    assert error.value.status_code == 503
    with Session(engine) as session:
        assert session.scalar(select(Structure).where(Structure.id == "str-fail")) is None
        assert session.scalar(select(IdempotencyRecord)) is None
        assert session.scalar(select(AuditEvent)) is None


def test_sqlalchemy_normalize_replay_does_not_repeat_structure_or_audit():
    engine = _sqlite_engine()
    with Session(engine) as session:
        session.add(Project(id="project-a", slug="project-a", name="A", status="ACTIVE", revision=1))
        session.commit()
    uow = SQLAlchemyStructureUnitOfWork(lambda: Session(engine))
    scope = IdempotencyScope("actor-a", "project-a", "POST", "/api/v1/structures/normalize", "replay-key")
    payload = NormalizeStructureRequest(project_id="project-a", input_type="smiles", value="CCO", preserve_stereochemistry=True)

    first, first_replayed = _normalize_in_transaction(
        service=StructureService(None), uow=uow, payload=payload, scope=scope, payload_hash="d" * 64,
        structure_id="str-first", request_id="worker:run-replay:1", agent_run_id="run-replay", attempt=1,
    )
    second, second_replayed = _normalize_in_transaction(
        service=StructureService(None), uow=uow, payload=payload, scope=scope, payload_hash="d" * 64,
        structure_id="str-second", request_id="worker:run-replay:2", agent_run_id="run-replay", attempt=2,
    )

    assert first_replayed is False
    assert second_replayed is True
    assert first.data.id == second.data.id == "str-first"
    with Session(engine) as session:
        assert len(session.scalars(select(Structure)).all()) == 1
        assert len(session.scalars(select(AuditEvent)).all()) == 1
        assert len(session.scalars(select(IdempotencyRecord)).all()) == 1


