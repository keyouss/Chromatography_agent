from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from chrom_platform.assembly.m2 import assemble_m2, assemble_m2_metadata
from chrom_platform.infrastructure.models import IdempotencyRecord, Project
from chrom_platform.infrastructure.metadata import m2_metadata
from chrom_platform.modules.data_center.persistence import (
    DataCenterBase,
    PredictionRecordRow,
    SQLAlchemyPredictionRecordRepository,
)
from chrom_platform.modules.prediction.persistence import PredictionBase, PredictionRow, SQLAlchemyPredictionPersistence
from chrom_platform.modules.prediction.schemas import ModelSnapshot, PredictionInputSnapshot
from chrom_platform.task_core.state import JobStatus
from test_prediction_service import sfc_request
from test_prediction_persistence import _ctx


def test_m2_metadata_has_one_owner_per_table() -> None:
    assert not set(DataCenterBase.metadata.tables) & set(PredictionBase.metadata.tables)
    metadata = assemble_m2_metadata()
    assert "prediction_record" in metadata.tables
    assert len(metadata.tables) == len(set(metadata.tables))


def test_m2_metadata_matches_prediction_and_record_constraints() -> None:
    metadata = assemble_m2_metadata()
    prediction = metadata.tables["prediction"]
    record = metadata.tables["prediction_record"]
    assert {fk.name for fk in prediction.foreign_key_constraints} == {
        "fk_prediction_project_id_project",
        "fk_prediction_record_id_prediction_record",
        "fk_prediction_job_id_job",
    }
    assert {fk.name for fk in record.foreign_key_constraints} == {
        "fk_prediction_record_project_id_project",
        "fk_prediction_record_prediction_id_prediction",
    }
    assert {constraint.name for constraint in prediction.constraints} >= {
        "ck_prediction_revision_positive",
        "ck_prediction_status",
        "ck_prediction_data_origin",
        "uq_prediction_job_id",
    }
    assert "ix_prediction_project_status_created" in {index.name for index in prediction.indexes}
    assert {constraint.name for constraint in record.constraints} >= {
        "ck_prediction_record_revision_positive",
        "ck_prediction_record_status",
    }


def test_m2_migration_generates_mysql_sql_and_downgrade_order() -> None:
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic" / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", "mysql://user:password@localhost/chrom_platform")
    output = StringIO()
    with redirect_stdout(output):
        command.upgrade(config, "0002_m2_prediction_data", sql=True)
    sql = output.getvalue()
    assert "CREATE TABLE prediction_record" in sql
    assert "CREATE TABLE prediction" in sql
    assert "ENGINE=InnoDB" in sql and "CHARSET=utf8mb4" in sql
    assert sql.index("CREATE TABLE prediction_record (") < sql.index("CREATE TABLE prediction (")
    output = StringIO()
    with redirect_stdout(output):
        command.downgrade(config, "0002_m2_prediction_data:0001_m1_persistence", sql=True)
    down = output.getvalue()
    assert down.index("DROP TABLE prediction") < down.index("DROP TABLE prediction_record")


def _sqlite_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    event.listen(engine, "connect", lambda dbapi, _: dbapi.create_function("CHAR_LENGTH", 1, len))
    m2_metadata().create_all(engine)
    session = Session(engine)
    session.add(Project(id="project-a", slug="project-a", name="Project A", status="ACTIVE", revision=1))
    session.commit()
    return session


def test_prediction_and_record_stage_in_one_session_and_rollback_together() -> None:
    session = _sqlite_session()
    request = sfc_request()
    model = ModelSnapshot(
        id="model-a", version="1", business_scope_id="scope-a", artifact_sha256="a" * 64,
        input_schema_version="1.0", output_schema_version="1.0",
    )
    snapshot = PredictionInputSnapshot(
        structure_refs=request.structure_refs, normalized_structures=["A", "B"], input=request.input,
    )
    adapter = SQLAlchemyPredictionPersistence(session)
    with pytest.raises(RuntimeError):
        with session.begin():
            accepted = adapter.create(
                _ctx(), request, model_snapshot=model, input_snapshot=snapshot,
                source="web", idempotency_key="m2-k1", payload_hash="b" * 64,
            ).accepted
            assert session.scalar(select(PredictionRow).where(PredictionRow.id == accepted.id)) is not None
            assert session.scalar(select(PredictionRecordRow).where(PredictionRecordRow.id == accepted.prediction_record_id)) is not None
            raise RuntimeError("rollback")
    assert session.scalar(select(PredictionRow)) is None
    assert session.scalar(select(PredictionRecordRow)) is None
    assert session.scalar(select(IdempotencyRecord)) is None


def test_stale_dispatch_claim_is_a_noop() -> None:
    session = _sqlite_session()
    request = sfc_request()
    model = ModelSnapshot(
        id="model-a", version="1", business_scope_id="scope-a", artifact_sha256="a" * 64,
        input_schema_version="1.0", output_schema_version="1.0",
    )
    snapshot = PredictionInputSnapshot(
        structure_refs=request.structure_refs, normalized_structures=["A", "B"], input=request.input,
    )
    adapter = SQLAlchemyPredictionPersistence(session)
    with session.begin():
        accepted = adapter.create(
            _ctx(), request, model_snapshot=model, input_snapshot=snapshot,
            source="web", idempotency_key="stale-dispatch", payload_hash="c" * 64,
        ).accepted
    item = adapter.load_execution(accepted.job_id)
    assert item is not None and item.dispatch_id
    assert adapter.mark_running(item, expected_dispatch_id="dispatch-old") is False
    assert session.scalar(select(PredictionRow).where(PredictionRow.id == accepted.id)).status == JobStatus.QUEUED.value


def test_cross_module_record_reader_uses_canonical_table() -> None:
    session = _sqlite_session()
    assert SQLAlchemyPredictionRecordRepository(session).get("project-a", "missing") is None


def test_sqlite_duplicate_key_replays_without_second_record() -> None:
    session = _sqlite_session()
    request = sfc_request()
    model = ModelSnapshot(
        id="model-a", version="1", business_scope_id="scope-a", artifact_sha256="a" * 64,
        input_schema_version="1.0", output_schema_version="1.0",
    )
    snapshot = PredictionInputSnapshot(structure_refs=request.structure_refs, normalized_structures=["A", "B"], input=request.input)
    adapter = SQLAlchemyPredictionPersistence(session)
    with session.begin():
        first = adapter.create(_ctx(), request, model_snapshot=model, input_snapshot=snapshot, source="web", idempotency_key="dup", payload_hash="b" * 64).accepted
    with session.begin():
        replay = adapter.create(_ctx(), request, model_snapshot=model, input_snapshot=snapshot, source="web", idempotency_key="dup", payload_hash="b" * 64)
    assert replay.replayed is True and replay.accepted.id == first.id
    assert session.query(PredictionRow).count() == 1


def test_m2_candidate_routers_keep_missing_ports_as_503() -> None:
    prediction_router, data_router = assemble_m2().routers
    app = FastAPI()
    from chrom_platform.core.errors import APIError

    app.add_exception_handler(APIError, lambda request, exc: JSONResponse(status_code=exc.status_code, content={"error": exc.payload.model_dump(mode="json")}))
    app.include_router(prediction_router, prefix="/api/v1")
    app.include_router(data_router, prefix="/api/v1")
    client = TestClient(app)
    assert client.get("/api/v1/prediction/types").status_code == 503
    assert client.get("/api/v1/data-domains").status_code == 503


def test_m2_import_does_not_change_frozen_m1_app_openapi() -> None:
    from chrom_platform.app import create_app

    profile = Path(__file__).resolve().parents[2] / "builds" / "minimal-structure-v1.json"
    before = set(create_app(profile).openapi()["paths"])
    import chrom_platform.assembly.m2  # noqa: F401

    after = set(create_app(profile).openapi()["paths"])
    assert before == after
