from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from chrom_platform.assembly.m3 import assemble_m3, bind_m3, m3_metadata
from chrom_platform.artifact_core.repository import SQLAlchemyProjectArtifactReader
from chrom_platform.core.errors import APIError
from chrom_platform.modules.model_center.persistence import ModelCenterBase
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session


def _alembic() -> Config:
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic" / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", "mysql://user:password@localhost/chrom_platform")
    return config


def test_m3_metadata_has_unique_module_owned_tables_and_cross_fks() -> None:
    metadata = m3_metadata()
    assert len(metadata.tables) == len(set(metadata.tables))
    assert "audit_event" in metadata.tables  # M1 generic audit
    assert "model_center_audit_event" in metadata.tables
    assert {fk.name for fk in metadata.tables["training_job"].foreign_key_constraints} >= {
        "fk_training_job_project_id_project",
        "fk_training_job_experiment_id_training_experiment",
    }
    assert "fk_evaluation_protocol_data_version_id_data_version" in {
        fk.name for fk in metadata.tables["evaluation_protocol"].foreign_key_constraints
    }
    assert "fk_training_experiment_data_version_id_data_version" in {
        fk.name for fk in metadata.tables["training_experiment"].foreign_key_constraints
    }
    assert {
        c.name for c in metadata.tables["knowledge_document"].constraints
        if c.__class__.__name__ == "CheckConstraint"
    } >= {"ck_knowledge_document_status", "ck_knowledge_document_revision_positive"}
    assert "ix_knowledge_chunk_content_ft" in {
        index.name for index in metadata.tables["knowledge_chunk"].indexes
    }
    assert {
        metadata.tables[table].primary_key.name
        for table in (
            "knowledge_document",
            "document_version",
            "knowledge_chunk",
            "index_run",
            "citation_snapshot",
        )
    } >= {
        "pk_knowledge_document",
        "pk_document_version",
        "pk_knowledge_chunk",
        "pk_index_run",
        "pk_citation_snapshot",
    }


def test_m3_migrations_generate_mysql_sql_and_reverse_in_dependency_order() -> None:
    output = StringIO()
    with redirect_stdout(output):
        # Keep this schema-order check within M3; later full-head migrations
        # include the online-only 0019 batch-export data repair.
        command.upgrade(_alembic(), "0005_m3_model_center", sql=True)
    sql = output.getvalue()
    assert sql.index("CREATE TABLE knowledge_document") < sql.index("CREATE TABLE training_template") < sql.index("CREATE TABLE registration")
    assert "WITH PARSER ngram" in sql
    assert "ENGINE=InnoDB" in sql and "CHARSET=utf8mb4" in sql

    output = StringIO()
    with redirect_stdout(output):
        command.downgrade(_alembic(), "0005_m3_model_center:0002_m2_prediction_data", sql=True)
    down = output.getvalue()
    assert down.index("DROP TABLE model") < down.index("DROP TABLE registration")
    assert "DROP INDEX ix_knowledge_chunk_content_ft ON knowledge_chunk" in down


def test_m3_assembly_has_no_bound_session_and_unconfigured_routes_are_503() -> None:
    assembly = assemble_m3()
    assert "session" not in assembly.__dataclass_fields__
    assert all(router.routes for router in assembly.routers)

    app = FastAPI()
    app.add_exception_handler(APIError, lambda request, exc: JSONResponse(status_code=exc.status_code, content={"error": exc.payload.model_dump(mode="json")}))
    for router in assembly.routers:
        app.include_router(router, prefix="/api/v1")
    client = TestClient(app)
    assert client.get("/api/v1/knowledge/documents", params={"project_id": "p1"}).status_code == 503
    assert client.get("/api/v1/training/templates").status_code == 503
    assert client.get("/api/v1/models", params={"project_id": "p1"}).status_code == 503


def test_bind_m3_uses_caller_session_and_keeps_project_scope() -> None:
    engine = create_engine("sqlite:///:memory:")
    event.listen(engine, "connect", lambda dbapi, _: dbapi.execute("PRAGMA foreign_keys=ON"))
    ModelCenterBase.metadata.create_all(engine)
    with Session(engine) as session:
        ports = bind_m3(assemble_m3(modules={"model_center"}), session)
        assert ports.session is session
        assert ports.model_center_repository is not None
        assert ports.model_center_service is not None
        assert isinstance(ports.model_center_service.artifacts, SQLAlchemyProjectArtifactReader)
        assert ports.model_center_service.artifacts._session is session
        assert ports.knowledge_repository is None
        assert ports.model_center_repository.get_model("project-a", "missing") is None


def test_module_selection_does_not_expose_unselected_management_routes() -> None:
    assembly = assemble_m3(modules={"knowledge"})
    assert assembly.routers[0].routes
    assert not assembly.routers[1].routes
    assert not assembly.routers[2].routes
    assert "knowledge_document" in assembly.metadata.tables
    assert "training_experiment" not in assembly.metadata.tables


def test_m3_rejects_unknown_modules_and_does_not_store_session_factory() -> None:
    try:
        assemble_m3(modules={"knowledge", "future_module"})
    except ValueError as exc:
        assert "future_module" in str(exc)
    else:
        raise AssertionError("unknown M3 modules must fail closed")
    assert "session_factory" not in assemble_m3().__dataclass_fields__
