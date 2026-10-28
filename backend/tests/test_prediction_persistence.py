from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from chrom_platform.core.dto import ActorContext, ResourceRef
from chrom_platform.core.errors import APIError
from chrom_platform.infrastructure.metadata import m2_metadata
from chrom_platform.infrastructure.models import AuditEvent, IdempotencyRecord, Job, OutboxEvent, Project
from chrom_platform.modules.data_center.persistence import DataCenterBase, SQLAlchemyPredictionRecordWriter
from chrom_platform.modules.assistant.persistence import AgentRunRow, AssistantBase, ConversationRow
from chrom_platform.modules.prediction.persistence import (
    PredictionRecordRow,
    PredictionRow,
    SQLAlchemyPredictionPersistence,
    _json_result,
    _view,
    _request_from_row,
)
from chrom_platform.modules.prediction.ports import AtomicPredictionCreateResult
from chrom_platform.modules.prediction.schemas import (
    CcPredictionResult,
    ModelSnapshot,
    ModifierStructureSnapshot,
    PredictionAccepted,
    PredictionExecutionInputSnapshot,
    PredictionInputSnapshot,
    SfcSingleColumnExecutionV10,
)
from chrom_platform.task_core.service import OperationContext
from chrom_platform.task_core.state import JobStatus
from test_prediction_service import sfc_request


def test_persistence_execution_loader_lifts_sfc_v10_from_frozen_row_snapshots() -> None:
    created = sfc_request().model_dump(mode="python")
    legacy = {**created, "schema_version": "1.0", "model_selection": {"mode": "champion", "scope_id": "scope-a"}}
    row = SimpleNamespace(
        project_id="project-a", chromatography_type="sfc", tool="single_column", schema_version="1.0",
        request_snapshot=legacy,
        model_snapshot={"id": "sfc-model", "version": "1", "business_scope_id": "scope-a", "artifact_sha256": "a" * 64, "input_schema_version": "1.0", "output_schema_version": "1.0"},
        input_snapshot={"structure_refs": legacy["structure_refs"], "normalized_structures": ["C", "N"], "input": legacy["input"], "modifier_structures": []},
    )
    request = _request_from_row(row)
    assert isinstance(request, SfcSingleColumnExecutionV10)
    assert request.model_selection.model_id == "sfc-model"
    assert legacy["model_selection"]["mode"] == "champion"


@dataclass
class RecordingSession:
    scalar_value: object | None = None
    fail_on_flush: int | None = None
    added: list[object] = field(default_factory=list)
    flushes: int = 0
    commits: int = 0
    closes: int = 0
    rollbacks: int = 0

    def scalar(self, statement):
        if self.scalar_value is None and "prediction_record" in str(statement):
            return next((row for row in self.added if isinstance(row, PredictionRecordRow)), None)
        return self.scalar_value

    def add(self, value):
        self.added.append(value)

    def add_all(self, values):
        self.added.extend(values)

    def flush(self):
        self.flushes += 1
        if self.fail_on_flush == self.flushes:
            raise RuntimeError("write failed")

    @contextmanager
    def begin_nested(self):
        yield

    def commit(self):
        self.commits += 1

    def close(self):
        self.closes += 1

    def rollback(self):
        self.rollbacks += 1
        self.added.clear()


def _ctx(*, projects=("project-a",), permissions=("prediction:create",)) -> OperationContext:
    return OperationContext(ActorContext(
        actor_id="actor-a", tenant_id=None, project_ids=list(projects),
        auth_method="deployment_default", permissions=list(permissions), policy_version="p1",
    ))


def _inputs():
    request = sfc_request()
    model = ModelSnapshot(
        id="model-a", version="1", business_scope_id="scope-a", artifact_sha256="a" * 64,
        input_schema_version="1.0", output_schema_version="1.0",
    )
    snapshot = PredictionInputSnapshot(
        structure_refs=request.structure_refs, normalized_structures=["canonical-a", "canonical-b"], input=request.input
    )
    return request, model, snapshot


def test_atomic_write_uses_caller_session_without_commit_or_close() -> None:
    session = RecordingSession()
    request, model, snapshot = _inputs()
    result = SQLAlchemyPredictionPersistence(session).create(
        _ctx(), request, model_snapshot=model, input_snapshot=snapshot, source="web",
        idempotency_key="k1", payload_hash="b" * 64,
    )
    assert result.accepted.resource_ref == ResourceRef(type="prediction", id=result.accepted.id)
    assert session.commits == session.closes == 0
    names = {type(row).__name__ for row in session.added}
    assert {"IdempotencyRecord", "PredictionRecordRow", "PredictionRow", "Job", "JobAttempt", "JobEvent", "OutboxEvent"} <= names
    job = next(row for row in session.added if type(row).__name__ == "Job")
    assert job.retryable is False
    assert job.etag and len(job.etag) == 64
    outbox = next(row for row in session.added if type(row).__name__ == "OutboxEvent")
    assert set(outbox.payload) == {"job_id", "attempt", "dispatch_id"}
    record = next(row for row in session.added if isinstance(row, PredictionRecordRow))
    prediction = next(row for row in session.added if isinstance(row, PredictionRow))
    assert record.prediction_id == prediction.id


def test_public_prediction_and_record_reads_strip_internal_modifier_snapshot() -> None:
    _engine, session = _assistant_prediction_session()
    request = sfc_request()
    model = ModelSnapshot(
        id="model-a", version="1", business_scope_id="scope-a", artifact_sha256="a" * 64,
        input_schema_version="1.0", output_schema_version="1.0",
    )
    snapshot = PredictionExecutionInputSnapshot(
        structure_refs=request.structure_refs,
        normalized_structures=["canonical-a", "canonical-b"],
        modifier_structures=[ModifierStructureSnapshot(structure_ref="methanol", normalized_smiles="CO")],
        input=request.input,
    )
    with session.begin():
        accepted = SQLAlchemyPredictionPersistence(session).create(
            _ctx(permissions=("prediction:create", "prediction:read")), request,
            model_snapshot=model, input_snapshot=snapshot, source="web",
            idempotency_key="public-read", payload_hash="b" * 64,
        ).accepted
    prediction = SQLAlchemyPredictionPersistence(session).get(
        _ctx(permissions=("prediction:read",)), accepted.id
    )
    from chrom_platform.modules.data_center.persistence import SQLAlchemyPredictionRecordRepository
    record = SQLAlchemyPredictionRecordRepository(session).get("project-a", accepted.prediction_record_id)
    assert prediction.input_snapshot.model_dump(mode="json") == {
        "structure_refs": ["structure-a", "structure-b"],
        "normalized_structures": ["canonical-a", "canonical-b"],
        "input": request.input.model_dump(mode="json"),
    }
    assert record is not None
    assert record.input_snapshot == prediction.input_snapshot.model_dump(mode="json")
    assert "modifier_structures" not in record.input_snapshot
    with pytest.raises(ValidationError):
        PredictionInputSnapshot.model_validate(snapshot.model_dump(mode="python"))


def test_legacy_hplc_v10_prediction_get_is_readable_without_synthesizing_raw_input() -> None:
    now = datetime.now(timezone.utc)
    row = SimpleNamespace(
        id="prediction-hplc-v10", project_id="project-a", prediction_record_id="record-hplc-v10",
        job_id="job-hplc-v10", chromatography_type="hplc", tool="single", schema_version="1.0",
        status="SUCCEEDED",
        model_snapshot={
            "id": "hplc-model", "version": "v1", "business_scope_id": "hplc-default",
            "artifact_sha256": "a" * 64, "input_schema_version": "1.0", "output_schema_version": "hplc-geognn-1",
        },
        input_snapshot={
            "structure_refs": ["structure-a", "structure-b"],
            "normalized_structures": ["C[C@@H](N)C(=O)O", "C[C@H](N)C(=O)O"],
            "input": {"isopropanol_fraction": 0.1, "flow_rate_ml_min": 1.0, "column_ids": ["ADH"]},
        },
        result=None, warnings=[], error=None, source="web", conversation_id=None,
        agent_run_id=None, created_at=now, completed_at=now, data_origin="production",
        revision=2, etag="etag-hplc-v10", updated_at=now,
    )

    view = _view(row)
    assert view.schema_version == "1.0"
    assert view.input_snapshot.input.model_dump(mode="json") == {
        "isopropanol_fraction": 0.1, "flow_rate_ml_min": 1.0, "column_ids": ["ADH"],
    }
    assert "model_input_smiles" not in view.input_snapshot.input.model_dump(mode="json")


def test_cc_persisted_result_round_trips_and_rejects_inconsistent_derived_fields() -> None:
    now = datetime.now(timezone.utc)
    result = CcPredictionResult(
        task_type="cc", schema_version="1.0",
        rows=[{"volume_ml": 2.0, "flow_rate_ml_min": 0.5}],
        applicability={"supported_scope": "verified test scope"},
    )
    persisted = _json_result(result)
    assert persisted["rows"][0]["time_seconds"] == 240.0

    row = SimpleNamespace(
        id="prediction-cc", project_id="project-a", prediction_record_id="record-cc",
        job_id="job-cc", chromatography_type="cc", tool="single", schema_version="1.0",
        status="SUCCEEDED",
        model_snapshot={
            "id": "model-cc", "version": "1", "business_scope_id": "scope-cc",
            "artifact_sha256": "a" * 64, "input_schema_version": "1.0", "output_schema_version": "1.0",
        },
        input_snapshot={
            "structure_refs": ["structure-a", "structure-b"],
            "normalized_structures": ["CCO", "CCN"],
            "input": {
                "eluent_system_id": "eluent-a", "loading_solvent_id": "solvent-a",
                "sample_mass_mg": 10.0, "loading_solvent_volume_ul": 100.0,
                "flow_rate_ml_min": 0.5, "column_spec_id": "column-a",
            },
        },
        result=persisted, warnings=[], error=None, source="web", conversation_id=None,
        agent_run_id=None, created_at=now, completed_at=now, data_origin="production",
        revision=2, etag="etag-2", updated_at=now,
    )

    view = _view(row)
    assert view.result is not None
    assert view.result.rows[0].volume_ml == 2.0
    assert view.result.rows[0].time_seconds == 240.0

    row.result = {**persisted, "rows": [{**persisted["rows"][0], "time_seconds": 241.0}]}
    with pytest.raises(ValidationError):
        _view(row)
    row.result = {**persisted, "rows": [{**persisted["rows"][0], "unknown": "rejected"}]}
    with pytest.raises(ValidationError):
        _view(row)


def _assistant_prediction_session():
    engine = create_engine("sqlite:///:memory:")
    event.listen(engine, "connect", lambda dbapi, _: dbapi.create_function("CHAR_LENGTH", 1, len))
    event.listen(engine, "begin", lambda connection: connection.exec_driver_sql("BEGIN"))
    m2_metadata().create_all(engine)
    AssistantBase.metadata.create_all(engine)
    session = Session(engine)
    now = datetime.now(timezone.utc)
    session.add(Project(id="project-a", slug="project-a", name="A", status="ACTIVE", revision=1))
    session.add(Project(id="project-b", slug="project-b", name="B", status="ACTIVE", revision=1))
    session.add_all([
        ConversationRow(id="conv-a", project_id="project-a", owner_id="actor-a", title="A", mode="knowledge", status="ACTIVE", revision=1, etag="a", created_at=now, updated_at=now),
        ConversationRow(id="conv-b", project_id="project-b", owner_id="actor-a", title="B", mode="knowledge", status="ACTIVE", revision=1, etag="b", created_at=now, updated_at=now),
    ])
    session.add(AgentRunRow(id="run-a", conversation_id="conv-a", project_id="project-a", input_message_id="msg-a", mode="knowledge", status="RUNNING", attempt=1, model_id="m", prompt_version="p", toolset_version="t", policy_version="p", created_at=now, updated_at=now, revision=1, etag="a"))
    session.commit()
    return engine, session


def test_assistant_prediction_persists_conversation_source_audit_and_atomic_job_graph():
    engine, session = _assistant_prediction_session()
    request, model, snapshot = _inputs()
    ctx = OperationContext(
        _ctx().actor, interaction="assistant", request_id="assistant-request",
        conversation_id="conv-a", agent_run_id="run-a",
    )
    result = SQLAlchemyPredictionPersistence(session).create(
        ctx, request, model_snapshot=model, input_snapshot=snapshot, source="assistant",
        idempotency_key="assistant-key", payload_hash="d" * 64,
    )
    prediction = session.scalar(select(PredictionRow).where(PredictionRow.id == result.accepted.id))
    audit = session.scalar(select(AuditEvent).where(AuditEvent.resource_id == result.accepted.id))
    job = session.scalar(select(Job).where(Job.id == result.accepted.job_id))
    outbox = session.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_id == result.accepted.job_id))
    assert prediction is not None and prediction.conversation_id == "conv-a" and prediction.source == "assistant"
    assert audit is not None and audit.agent_run_id == "run-a" and audit.job_id == result.accepted.job_id
    assert audit.resource_ref == {"type": "prediction", "id": result.accepted.id}
    assert audit.after_summary["source"] == "assistant" and "normalized_structures" not in str(audit.after_summary)
    assert job is not None and outbox is not None and outbox.payload["job_id"] == job.id


def test_prediction_rejects_cross_project_conversation_before_any_write():
    _engine, session = _assistant_prediction_session()
    request, model, snapshot = _inputs()
    ctx = OperationContext(
        _ctx().actor, interaction="assistant", request_id="cross-project",
        conversation_id="conv-b", agent_run_id=None,
    )
    with pytest.raises(APIError) as error:
        SQLAlchemyPredictionPersistence(session).create(
            ctx, request, model_snapshot=model, input_snapshot=snapshot, source="assistant",
            idempotency_key="cross-key", payload_hash="e" * 64,
        )
    assert error.value.status_code == 404
    session.rollback()
    assert session.scalar(select(PredictionRow)) is None
    assert session.scalar(select(Job)) is None
    assert session.scalar(select(OutboxEvent)) is None


def test_assistant_prediction_requires_conversation_before_any_write():
    _engine, session = _assistant_prediction_session()
    request, model, snapshot = _inputs()
    ctx = OperationContext(_ctx().actor, interaction="assistant", request_id="missing-conversation")
    with pytest.raises(APIError) as error:
        SQLAlchemyPredictionPersistence(session).create(
            ctx, request, model_snapshot=model, input_snapshot=snapshot, source="assistant",
            idempotency_key="missing-conversation-key", payload_hash="f" * 64,
        )
    assert error.value.status_code == 422 and error.value.payload.code == "VALIDATION_FAILED"
    session.rollback()
    assert session.scalar(select(IdempotencyRecord)) is None
    assert session.scalar(select(PredictionRow)) is None
    assert session.scalar(select(Job)) is None
    assert session.scalar(select(OutboxEvent)) is None
    assert session.scalar(select(AuditEvent)) is None


def test_caller_rollback_can_remove_all_staged_writes() -> None:
    session = RecordingSession(fail_on_flush=2)
    request, model, snapshot = _inputs()
    with pytest.raises(RuntimeError):
        SQLAlchemyPredictionPersistence(session).create(
            _ctx(), request, model_snapshot=model, input_snapshot=snapshot, source="web",
            idempotency_key="k1", payload_hash="b" * 64,
        )
    session.rollback()
    assert session.added == []
    assert session.commits == session.closes == 0


def test_caller_rollback_removes_writes_when_record_backfill_flush_fails() -> None:
    session = RecordingSession(fail_on_flush=3)
    request, model, snapshot = _inputs()
    with pytest.raises(RuntimeError):
        SQLAlchemyPredictionPersistence(session).create(
            _ctx(), request, model_snapshot=model, input_snapshot=snapshot, source="web",
            idempotency_key="k1", payload_hash="b" * 64,
        )
    session.rollback()
    assert session.added == []
    assert session.commits == session.closes == 0


def test_same_key_replays_and_different_payload_conflicts() -> None:
    accepted = PredictionAccepted(
        id="prediction-a", prediction_record_id="record-a", job_id="job-a", status=JobStatus.QUEUED,
        resource_ref=ResourceRef(type="prediction", id="prediction-a"),
    )
    row = IdempotencyRecord(
        id="idem-a", actor_id="actor-a", project_id="project-a", method="POST",
        route_identity="/api/v1/predictions", key="k1", payload_hash="b" * 64,
        status="COMPLETED", result_resource_ref=accepted.model_dump(mode="json"),
    )
    request, model, snapshot = _inputs()
    persistence = SQLAlchemyPredictionPersistence(RecordingSession(scalar_value=row))
    replay = persistence.create(_ctx(), request, model_snapshot=model, input_snapshot=snapshot, source="web", idempotency_key="k1", payload_hash="b" * 64)
    assert replay.replayed is True and replay.accepted.id == "prediction-a"
    with pytest.raises(APIError) as error:
        persistence.create(_ctx(), request, model_snapshot=model, input_snapshot=snapshot, source="web", idempotency_key="k1", payload_hash="c" * 64)
    assert error.value.status_code == 409 and error.value.payload.code == "IDEMPOTENCY_KEY_REUSED"


def test_expired_idempotency_record_is_reclaimed_in_place() -> None:
    from datetime import datetime, timedelta, timezone

    row = IdempotencyRecord(
        id="idem-expired", actor_id="actor-a", project_id="project-a", method="POST",
        route_identity="/api/v1/predictions", key="k1", payload_hash="b" * 64,
        status="COMPLETED", result_resource_ref={"id": "old"}, result_status_code=202,
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    session = RecordingSession(scalar_value=row)
    request, model, snapshot = _inputs()
    result = SQLAlchemyPredictionPersistence(session).create(
        _ctx(), request, model_snapshot=model, input_snapshot=snapshot, source="web",
        idempotency_key="k1", payload_hash="b" * 64,
    )
    assert result.replayed is False
    assert row.status == "COMPLETED"
    assert row.result_resource_ref["id"] == result.accepted.id


def test_prediction_record_mapping_matches_data_center_table_shape() -> None:
    from chrom_platform.modules.prediction.persistence import PredictionRecordRow

    assert set(PredictionRecordRow.__table__.columns.keys()) == set(
        DataCenterBase.metadata.tables["prediction_record"].columns.keys()
    )


def test_prediction_record_update_missing_row_raises_for_transaction_rollback() -> None:
    session = RecordingSession()
    with pytest.raises(RuntimeError, match="PREDICTION_RECORD_NOT_FOUND"):
        SQLAlchemyPredictionRecordWriter().update_prediction_record(
            "missing-record", status=JobStatus.FAILED.value, session=session,
        )
    assert session.flushes == 0 and session.added == []


def test_prediction_service_checks_project_access_before_replay() -> None:
    from chrom_platform.modules.prediction.service import PredictionCreateService

    class Deny:
        def ensure_project_access(self, *_args, **_kwargs):
            raise APIError(403, "PERMISSION_DENIED", "denied")

    class ShouldNotLookup:
        def lookup(self, *_args, **_kwargs):
            raise AssertionError("replay lookup happened before access")

    with pytest.raises(APIError) as error:
        PredictionCreateService(
            access=Deny(), structures=None, capabilities=None, models=None,
            idempotency=ShouldNotLookup(), atomic=None,
        ).create(_ctx(), sfc_request(), idempotency_key="k1")
    assert error.value.status_code == 403
