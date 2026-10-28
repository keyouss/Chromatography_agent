from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from chrom_platform.artifact_core.models import Artifact, Structure
from chrom_platform.infrastructure.db import Base
from chrom_platform.infrastructure.models import Job, Project
from chrom_platform.modules.data_center.errors import DataUnavailableError
from chrom_platform.modules.data_center.persistence import (
    DataCenterBase,
    PredictionDraftRow,
    PredictionRecordRow,
    SQLAlchemyPredictionRecordRepository,
)
from chrom_platform.modules.prediction.persistence import PredictionBase, PredictionRow, SQLAlchemyPredictionPersistence


def _database() -> tuple[Session, object]:
    engine = create_engine("sqlite:///:memory:")
    event.listen(engine, "connect", lambda dbapi, _: (dbapi.create_function("CHAR_LENGTH", 1, len), dbapi.execute("PRAGMA foreign_keys=ON")))
    for table in (Project.__table__, Artifact.__table__, Job.__table__, Structure.__table__):
        table.create(engine)
    PredictionRecordRow.__table__.create(engine)
    PredictionRow.__table__.create(engine)
    PredictionDraftRow.__table__.create(engine)
    return Session(engine), engine


def _seed(session: Session, *, with_modifier: bool = True) -> None:
    now = datetime.now(timezone.utc)
    session.add(Project(id="p1", slug="p1", name="P1"))
    session.flush()
    session.add(Job(
        id="job-1", project_id="p1", owner_id="actor-1", kind="prediction",
        resource_ref={"type": "prediction", "id": "pred-1"}, status="SUCCEEDED",
        attempt=1, progress={}, cancelable=True, retryable=False, resume_mode="none",
        created_at=now, updated_at=now, revision=1, etag='"job"',
    ))
    session.flush()
    refs = ["main-1", "main-2"] + (["modifier-1"] if with_modifier else [])
    for index, structure_id in enumerate(refs):
        smiles = ("C[C@H](O)F", "C[C@@H](O)F", "CO")[index]
        session.add(Structure(
            id=structure_id, project_id="p1", schema_version="1.0",
            original_input={"input_type": "smiles", "value": smiles}, canonical_smiles=smiles,
            is_valid=True, has_stereochemistry=index < 2,
            stereochemistry_status="specified" if index < 2 else "not_applicable",
            stereochemistry_warnings=[], snapshot_hash=(str(index + 1) * 64),
            source_artifact_id=None, created_at=now,
        ))
    input_value = {
        "modifier_total_fraction": 1.0,
        "modifier_components": ([{"structure_ref": "modifier-1", "fraction": 1.0}] if with_modifier else [{"structure_ref": "missing-modifier", "fraction": 1.0}]),
        "candidate_scope_id": "scope-1", "top_k": 5, "column_id": "column-1",
    }
    session.add(PredictionRecordRow(
        id="record-1", project_id="p1", prediction_id="pred-1", batch_id=None,
        source="web", actor_id="actor-original", chromatography_type="sfc",
        model_snapshot={"id": "model-1", "version": "7", "business_scope_id": "sfc",
            "artifact_sha256": "a" * 64, "input_schema_version": "sfc-v7", "output_schema_version": "sfc-out-7"},
        input_snapshot={"structure_refs": ["main-1", "main-2"], "normalized_structures": ["C[C@H](O)F", "C[C@@H](O)F"], "input": input_value},
        result_ref=None, warnings=[], status="ARCHIVED", archived_at=now, created_at=now,
        artifact_refs=[], revision=2, etag='"record-2"',
    ))
    session.add(PredictionRow(
        id="pred-1", project_id="p1", prediction_record_id="record-1", job_id="job-1",
        actor_id="actor-original", chromatography_type="sfc", tool="single_column", schema_version="sfc-v7",
        status="SUCCEEDED", model_snapshot={"id": "model-1", "version": "7", "business_scope_id": "sfc",
            "artifact_sha256": "a" * 64, "input_schema_version": "sfc-v7", "output_schema_version": "sfc-out-7"},
        input_snapshot={"structure_refs": ["main-1", "main-2"], "normalized_structures": ["C[C@H](O)F", "C[C@@H](O)F"], "input": input_value,
            "modifier_structures": [{"structure_ref": "modifier-1", "normalized_smiles": "CO"}]},
        request_snapshot={"project_id": "p1", "chromatography_type": "sfc", "tool": "single_column", "schema_version": "sfc-v7",
            "model_selection": {"mode": "champion", "scope_id": "sfc"}, "structure_refs": ["main-1", "main-2"], "input": input_value},
        result=None, warnings=[], error=None, source="web", conversation_id=None, agent_run_id=None,
        created_at=now, updated_at=now, completed_at=now, data_origin="production", revision=1, etag='"pred"',
    ))
    session.flush()


def test_sfc_replay_draft_maps_exact_source_and_ordered_public_structures_atomically() -> None:
    session, _engine = _database()
    _seed(session)
    record = SQLAlchemyPredictionRecordRepository(session).get("p1", "record-1")
    source = SQLAlchemyPredictionPersistence(session).get_replay_source("p1", record)
    assert source is not None
    assert source.tool == "single_column" and source.schema_version == "sfc-v7"
    assert source.structure_refs == ["main-1", "main-2"]
    assert [item["id"] for item in source.structure_snapshots] == source.structure_refs
    assert source.structure_snapshots[0]["canonical_smiles"] == "C[C@H](O)F"
    assert [item["id"] for item in source.modifier_structure_snapshots] == ["modifier-1"]
    assert source.model_selection == {
        "mode": "exact", "model_id": "model-1", "model_version": "7",
        "preprocessing_profile_id": None, "preprocessing_profile_sha256": None,
    }
    assert source.model_snapshot["artifact_sha256"] == "a" * 64
    assert "modifier_structures" not in source.input

    repository = SQLAlchemyPredictionRecordRepository(session, id_factory=lambda _: "draft-1")
    record = repository.get("p1", "record-1")
    draft = repository.create_replay_draft(record, source, actor_id="actor-replay")
    loaded = repository.get_replay_draft("p1", draft.id)
    assert repository.get_replay_draft("p2", draft.id) is None
    assert loaded == draft
    assert [item.id for item in draft.structure_refs] == ["main-1", "main-2"]
    assert [item.type for item in draft.structure_refs] == ["structure", "structure"]
    assert [item.id for item in draft.modifier_structure_snapshots] == ["modifier-1"]
    assert draft.source_record_revision == 2
    assert draft.input["column_id"] == "column-1"
    assert draft.model_selection.model_version == "7"
    assert session.scalar(select(func.count()).select_from(PredictionDraftRow)) == 1
    session.rollback()
    assert session.scalar(select(func.count()).select_from(PredictionDraftRow)) == 0
    session.close()


def test_replay_fails_closed_when_any_referenced_project_structure_is_missing() -> None:
    session, _engine = _database()
    _seed(session, with_modifier=False)
    with pytest.raises(DataUnavailableError, match="结构不存在"):
        SQLAlchemyPredictionPersistence(session).get_replay_source(
            "p1", SQLAlchemyPredictionRecordRepository(session).get("p1", "record-1")
        )
    session.close()


@pytest.mark.parametrize("profile_bound", [False, True])
def test_historical_cc_record_read_and_replay_preserve_profile_boundary(profile_bound: bool) -> None:
    session, _engine = _database()
    _seed(session)
    record = session.get(PredictionRecordRow, "record-1")
    prediction = session.get(PredictionRow, "pred-1")
    model = {
        "id": "qgeognn_silica_4g", "version": "cc-qgeognn-1",
        "business_scope_id": "cc-default", "artifact_sha256": "a" * 64,
        "input_schema_version": "1.0", "output_schema_version": "cc-qgeognn-1",
    }
    if profile_bound:
        model.update({"preprocessing_profile_id": "cc-qgeognn-4g-canonical-v1", "preprocessing_profile_sha256": "b" * 64})
    cc_input = {
        "eluent_system_id": "cc4g-pe-ea-000-100-v1", "loading_solvent_id": "cc4g-loading-dcm-v1",
        "sample_mass_mg": 100.0, "loading_solvent_volume_ul": 300.0,
        "flow_rate_ml_min": 10.0, "column_spec_id": "silica-4g",
    }
    selection = {"mode": "exact", "model_id": model["id"], "model_version": model["version"]}
    if profile_bound:
        selection.update({"preprocessing_profile_id": model["preprocessing_profile_id"], "preprocessing_profile_sha256": model["preprocessing_profile_sha256"]})
    record.chromatography_type = "cc"
    record.model_snapshot = model
    prediction.chromatography_type = "cc"
    prediction.tool = "single"
    prediction.schema_version = "1.0"
    prediction.model_snapshot = model
    prediction.input_snapshot = {
        "structure_refs": ["main-1", "main-2"],
        "normalized_structures": ["C[C@H](O)F", "C[C@@H](O)F"],
        "input": cc_input,
    }
    prediction.request_snapshot = {
        "project_id": "p1", "chromatography_type": "cc", "tool": "single", "schema_version": "1.0",
        "model_selection": selection, "structure_refs": ["main-1", "main-2"], "input": cc_input,
    }
    session.flush()

    readable = SQLAlchemyPredictionRecordRepository(session).get("p1", "record-1")
    assert readable.model_snapshot.get("preprocessing_profile_id") == model.get("preprocessing_profile_id")
    if not profile_bound:
        with pytest.raises(DataUnavailableError, match="不能重放；请重新创建预测"):
            SQLAlchemyPredictionPersistence(session).get_replay_source("p1", readable)
    else:
        source = SQLAlchemyPredictionPersistence(session).get_replay_source("p1", readable)
        assert source is not None
        assert source.model_selection["preprocessing_profile_id"] == model["preprocessing_profile_id"]
        assert source.model_selection["preprocessing_profile_sha256"] == model["preprocessing_profile_sha256"]
        repository = SQLAlchemyPredictionRecordRepository(session, id_factory=lambda _: "cc-draft")
        draft = repository.create_replay_draft(readable, source, actor_id="actor-replay")
        assert draft.model_selection.preprocessing_profile_sha256 == model["preprocessing_profile_sha256"]
        assert draft.source_model_snapshot.preprocessing_profile_sha256 == model["preprocessing_profile_sha256"]
    session.close()


def test_replay_fails_closed_on_request_row_tool_schema_mismatch() -> None:
    session, _engine = _database()
    _seed(session)
    row = session.get(PredictionRow, "pred-1")
    row.tool = "full_column"
    session.flush()
    with pytest.raises(DataUnavailableError, match="原始预测请求、模型与预测记录绑定不一致"):
        SQLAlchemyPredictionPersistence(session).get_replay_source(
            "p1", SQLAlchemyPredictionRecordRepository(session).get("p1", "record-1")
        )
    session.close()


@pytest.mark.parametrize(
    "mutation, message",
    [
        ("record_link", "绑定不一致"),
        ("record_model", "绑定不一致"),
        ("prediction_model", "绑定不一致"),
        ("champion_scope", "Champion 范围"),
        ("exact_model", "精确模型选择"),
        ("schema_version", "绑定不一致"),
    ],
)
def test_replay_rejects_source_record_model_and_schema_cross_links(mutation, message) -> None:
    session, _engine = _database()
    _seed(session)
    record = session.get(PredictionRecordRow, "record-1")
    prediction = session.get(PredictionRow, "pred-1")
    if mutation == "record_link":
        prediction.prediction_record_id = "record-other"
    elif mutation == "record_model":
        record.model_snapshot = {**record.model_snapshot, "version": "other"}
    elif mutation == "prediction_model":
        prediction.model_snapshot = {**prediction.model_snapshot, "artifact_sha256": "b" * 64}
    elif mutation == "champion_scope":
        prediction.request_snapshot = {**prediction.request_snapshot, "model_selection": {"mode": "champion", "scope_id": "other-scope"}}
    elif mutation == "exact_model":
        prediction.request_snapshot = {**prediction.request_snapshot, "model_selection": {"mode": "exact", "model_id": "other-model", "model_version": "7"}}
    elif mutation == "schema_version":
        prediction.model_snapshot = {**prediction.model_snapshot, "input_schema_version": "other-schema"}
    session.flush()
    with pytest.raises(DataUnavailableError, match=message):
        SQLAlchemyPredictionPersistence(session).get_replay_source("p1", SQLAlchemyPredictionRecordRepository(session).get("p1", "record-1"))
    session.close()


def test_prediction_draft_restricts_source_record_deletion() -> None:
    session, _engine = _database()
    _seed(session)
    record = SQLAlchemyPredictionRecordRepository(session).get("p1", "record-1")
    source = SQLAlchemyPredictionPersistence(session).get_replay_source("p1", record)
    repository = SQLAlchemyPredictionRecordRepository(session, id_factory=lambda _: "draft-1")
    repository.create_replay_draft(repository.get("p1", "record-1"), source, actor_id="actor-replay")
    session.flush()
    with pytest.raises(IntegrityError):
        with session.begin_nested():
            session.delete(session.get(PredictionRecordRow, "record-1"))
            session.flush()
    session.rollback()
    session.close()
