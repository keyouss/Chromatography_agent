from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from chrom_platform.infrastructure.metadata import m2_metadata
from chrom_platform.infrastructure.models import Project
from chrom_platform.modules.data_center.persistence import (
    DataCenterBase,
    PredictionRecordRow,
    SQLAlchemyPredictionRecordRepository,
    SQLAlchemyPredictionRecordWriter,
)


def _session() -> tuple[object, Session]:
    engine = create_engine("sqlite:///:memory:")
    event.listen(engine, "connect", lambda dbapi, _: dbapi.create_function("CHAR_LENGTH", 1, len))
    m2_metadata().create_all(engine)
    DataCenterBase.metadata.create_all(engine)
    session = Session(engine)
    session.add(Project(id="project-a", slug="project-a", name="A", status="ACTIVE", revision=1))
    session.commit()
    return engine, session


def _write(writer: SQLAlchemyPredictionRecordWriter, session: Session, *, status: str, warnings: list[str]):
    return writer.write_batch_row_record(
        session=session,
        batch_id="batch-a",
        row_id="row-a",
        project_id="project-a",
        actor_id="actor-a",
        source="web",
        chromatography_type="gc",
        model_snapshot={"id": "model-a", "version": "1", "artifact_sha256": "a" * 64},
        input_snapshot={"normalized_structures": ["CCO"], "temperature_program": []},
        status=status,
        warnings=warnings,
        artifact_refs=[],
    )


def test_batch_row_record_is_stable_across_retry_and_preserves_archive() -> None:
    engine, session = _session()
    writer = SQLAlchemyPredictionRecordWriter()
    try:
        with session.begin():
            first = _write(writer, session, status="FAILED", warnings=["retryable"])
            stable_id = first.id
            assert first.result_ref == {"type": "batch_prediction", "id": "batch-a"}
            assert "row-a" not in first.input_snapshot
            assert first.revision == 1

        with session.begin():
            duplicate = _write(writer, session, status="FAILED", warnings=["retryable"])
            assert duplicate.id == stable_id
            assert duplicate.revision == 1

        with session.begin():
            retried = _write(writer, session, status="SUCCEEDED", warnings=[])
            assert retried.id == stable_id
            assert retried.revision == 2
            assert retried.status == "SUCCEEDED"

        with session.begin():
            SQLAlchemyPredictionRecordRepository(session).archive(
                "project-a", stable_id, confirmation_id="confirm-a", actor=SimpleNamespace(),
                consume_confirmation=lambda *_args: None,
            )

        with session.begin():
            archived_before = session.scalar(
                select(PredictionRecordRow).where(PredictionRecordRow.id == stable_id)
            )
            archived_at = archived_before.archived_at
            archived_revision = archived_before.revision
            preserved = _write(writer, session, status="FAILED", warnings=["late retry"])
            assert preserved.status == "ARCHIVED"
            assert preserved.archived_at == archived_at
            assert preserved.revision == archived_revision
            assert preserved.result_ref == {"type": "batch_prediction", "id": "batch-a"}
    finally:
        session.close()
        engine.dispose()


def test_batch_row_record_rejects_changed_frozen_scientific_identity() -> None:
    engine, session = _session()
    writer = SQLAlchemyPredictionRecordWriter()
    try:
        with session.begin():
            _write(writer, session, status="FAILED", warnings=[])

        with session.begin():
            try:
                writer.write_batch_row_record(
                    session=session,
                    batch_id="batch-a",
                    row_id="row-a",
                    project_id="project-a",
                    actor_id="actor-a",
                    source="web",
                    chromatography_type="gc",
                    model_snapshot={"id": "model-b", "version": "1", "artifact_sha256": "b" * 64},
                    input_snapshot={"normalized_structures": ["CCO"], "temperature_program": []},
                    status="SUCCEEDED",
                    warnings=[],
                    artifact_refs=[],
                )
            except RuntimeError as error:
                assert str(error) == "BATCH_ROW_RECORD_IDENTITY_MISMATCH"
            else:
                raise AssertionError("changed model identity must be rejected")
    finally:
        session.close()
        engine.dispose()
