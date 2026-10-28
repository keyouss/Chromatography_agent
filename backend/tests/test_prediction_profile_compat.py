from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from chrom_platform.app import create_app
from chrom_platform.core.dto import ActorContext
from chrom_platform.core.errors import APIError
from chrom_platform.modules.prediction.router import create_router


ROOT = Path(__file__).resolve().parents[2]


def _document(build_id: str) -> dict:
    return create_app(ROOT / "builds" / f"{build_id}.json", contract_generation=True).openapi()


def _refs(schema: dict) -> list[str]:
    return [item["$ref"].rsplit("/", 1)[-1] for item in schema["anyOf"]]


def test_profile_prediction_response_unions_follow_built_types() -> None:
    universal = _document("universal-v1")["components"]["schemas"]
    prediction = universal["Prediction"]
    assert prediction["properties"]["chromatography_type"]["enum"] == ["cc", "gc", "hplc", "sfc", "tlc"]
    assert _refs(universal["PredictionInputSnapshot"]["properties"]["input"]) == [
        "CcInput", "GcInput", "HplcInput", "HplcInputV10", "SfcFullColumnInput", "SfcSingleColumnInput", "TlcInput", "_SfcConditionInput"
    ]
    result_union = prediction["properties"]["result"]["anyOf"][0]
    assert [item["$ref"].rsplit("/", 1)[-1] for item in result_union["oneOf"]] == [
        "CcPredictionResult", "GcPredictionResult", "HplcPredictionResult", "SfcPredictionResult", "TlcPredictionResult"
    ]
    assert "ChiralLcInput" not in universal
    assert "ChiralLcPredictionResult" not in universal

    sfc = _document("sfc-v1")["components"]["schemas"]
    assert sfc["Prediction"]["properties"]["chromatography_type"]["const"] == "sfc"
    assert _refs(sfc["PredictionInputSnapshot"]["properties"]["input"]) == [
        "SfcFullColumnInput", "SfcSingleColumnInput", "_SfcConditionInput"
    ]
    axes = sfc["SfcCandidateScopeCreate"]["properties"]["axes"]
    assert axes["propertyNames"]["enum"] == [
        "column", "stationary_phase", "mode", "length_mm", "inner_diameter_mm",
        "particle_size_um", "column_temperature_c", "flow_rate_ml_min",
    ]
    assert {"conditions", "score_semantics"} <= set(sfc["SfcResultRow"]["required"])

    cc = _document("cc-v1")["components"]["schemas"]
    assert cc["Prediction"]["properties"]["chromatography_type"]["const"] == "cc"
    assert cc["PredictionInputSnapshot"]["properties"]["input"]["$ref"].endswith("/CcInput")
    assert "SfcCandidateScopeCreate" not in cc
    assert "SfcCandidateScopeCreate" not in _document("minimal-structure-v1")["components"]["schemas"]

    sfc_paths = _document("sfc-v1")["paths"]
    cc_paths = _document("cc-v1")["paths"]
    assert "/api/v1/sfc/conditions/options" in sfc_paths
    assert "/api/v1/sfc/candidate-scopes" in sfc_paths
    assert "/api/v1/sfc/conditions/options" not in cc_paths
    assert "/api/v1/sfc/candidate-scopes" not in cc_paths


def test_sfc_records_openapi_keeps_runtime_cursor_idempotency_and_empty_body_contract() -> None:
    paths = _document("sfc-v1")["paths"]
    listing = paths["/api/v1/prediction-records"]["get"]["parameters"]
    assert any(item["name"] == "cursor" and item["required"] is False for item in listing)

    get_params = paths["/api/v1/prediction-records/{record_id}"]["get"]["parameters"]
    project_id = next(item for item in get_params if item["name"] == "project_id")
    assert project_id["required"] is False

    for suffix in ("archive", "replay-draft"):
        operation = paths[f"/api/v1/prediction-records/{{record_id}}/{suffix}"]["post"]
        assert any(
            item["name"] == "Idempotency-Key" and item["required"] is True
            for item in operation["parameters"]
        )

    replay = paths["/api/v1/prediction-records/{record_id}/replay-draft"]["post"]
    assert replay["requestBody"]["required"] is True
    assert replay["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("/_EmptyPayload")

    draft_get = paths["/api/v1/prediction-drafts/{draft_id}"]["get"]
    draft_project = next(item for item in draft_get["parameters"] if item["name"] == "project_id")
    assert draft_project["required"] is False
    draft_ref = draft_get["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[-1]
    draft_envelope = _document("sfc-v1")["components"]["schemas"][draft_ref]
    assert draft_envelope["properties"]["data"]["$ref"].endswith("/PredictionDraftView")
    view = _document("sfc-v1")["components"]["schemas"]["PredictionDraftView"]
    assert {"source_prediction_id", "source_model_snapshot", "source_record_revision", "structure_snapshots", "revision", "etag", "created_at", "updated_at"} <= set(view["required"])
    assert view["properties"]["structure_refs"]["items"]["$ref"].endswith("/ResourceRef")
    assert view["properties"]["structure_snapshots"]["items"]["$ref"].endswith("/PredictionDraftStructureSnapshot")
    assert view["properties"]["modifier_structure_snapshots"]["items"]["$ref"].endswith("/PredictionDraftStructureSnapshot")
    exact = _document("sfc-v1")["components"]["schemas"]["PredictionDraftExactModelSelection"]
    assert exact["properties"]["mode"]["const"] == "exact"


def test_trimmed_sfc_and_cc_packages_generate_openapi_in_isolated_process(tmp_path: Path) -> None:
    from chrom_platform.assembly.profile import assemble_profile
    from chrom_platform.generate_openapi import generate_package

    sfc_package = assemble_profile(ROOT / "builds" / "sfc-v1.json", tmp_path / "packages")
    cc_package = assemble_profile(ROOT / "builds" / "cc-v1.json", tmp_path / "packages")
    sfc_document = json.loads(generate_package(sfc_package, tmp_path / "openapi").read_text(encoding="utf-8"))
    cc_document = json.loads(generate_package(cc_package, tmp_path / "openapi").read_text(encoding="utf-8"))

    assert "/api/v1/sfc/conditions/options" in sfc_document["paths"]
    assert "/api/v1/sfc/candidate-scopes" in sfc_document["paths"]
    assert "/api/v1/prediction-drafts/{draft_id}" in sfc_document["paths"]
    sfc_schemas = sfc_document["components"]["schemas"]
    assert "SfcSingleColumnPredictionReadV10" not in sfc_schemas
    assert "SfcFullColumnPredictionReadV10" not in sfc_schemas
    assert "/api/v1/sfc/conditions/options" not in cc_document["paths"]
    assert "/api/v1/sfc/candidate-scopes" not in cc_document["paths"]
    assert "/api/v1/prediction-drafts/{draft_id}" in cc_document["paths"]
    for package in (sfc_package, cc_package):
        worker_cli = package / "chrom_platform" / "worker_cli.py"
        worker_source = worker_cli.read_text(encoding="utf-8")
        assert '"batch_prediction"' not in worker_source
        assert '"batch_validation"' not in worker_source
        assert '"batch_export"' not in worker_source
        assert "_validate_batch_artifact_store" not in worker_source
        assert "batch_enabled" not in worker_source
    assert not any(path.name == "__pycache__" or path.suffix == ".pyc" for path in sfc_package.rglob("*"))
    assert not any(path.name == "__pycache__" or path.suffix == ".pyc" for path in cc_package.rglob("*"))

    execution_check = r'''
import sys
from pathlib import Path
from pydantic import ValidationError
from pydantic import TypeAdapter
sys.path.insert(0, str(Path.cwd()))
from chrom_platform.modules.prediction.schemas import (
    ModelSnapshot, PredictionCreate, PredictionExecutionInputSnapshot,
    SfcSingleColumnExecutionV10, build_prediction_execution_request,
)
raw = {
    "project_id": "project-a", "schema_version": "1.0", "chromatography_type": "sfc",
    "tool": "single_column", "structure_refs": ["structure-a", "structure-b"],
    "model_selection": {"mode": "champion", "scope_id": "scope-a"},
    "input": {
        "modifier_total_fraction": 0.2,
        "modifier_components": [{"structure_ref": "methanol", "fraction": 1.0}],
        "candidate_scope_id": "conditions-a", "top_k": 5, "column_id": "AD",
    },
}
model = ModelSnapshot.model_validate({
    "id": "model-a", "version": "1", "business_scope_id": "scope-a",
    "artifact_sha256": "a" * 64, "input_schema_version": "1.0", "output_schema_version": "1.0",
})
frozen_input = PredictionExecutionInputSnapshot.model_validate({
    "structure_refs": raw["structure_refs"], "normalized_structures": ["C", "CC"], "input": raw["input"],
})
execution = build_prediction_execution_request(
    raw, project_id="project-a", expected_type="sfc", expected_tool="single_column",
    expected_schema_version="1.0", model_snapshot=model, input_snapshot=frozen_input,
)
assert isinstance(execution, SfcSingleColumnExecutionV10)
assert execution.model_selection.model_id == model.id
assert raw["model_selection"]["mode"] == "champion"
try:
    TypeAdapter(PredictionCreate).validate_python(raw)
except ValidationError:
    pass
else:
    raise AssertionError("stored SFC v1.0 must not enter the create union")
'''
    child_env = os.environ.copy()
    child_env.pop("PYTHONPATH", None)
    child_env.pop("CHROM_BUILD_MANIFEST", None)
    subprocess.run(
        [sys.executable, "-B", "-I", "-c", execution_check],
        cwd=sfc_package,
        env=child_env,
        capture_output=True,
        text=True,
        check=True,
    )


def test_trimmed_cc_prediction_schema_keeps_validator_dependency_closure(tmp_path: Path) -> None:
    from chrom_platform.assembly.profile import assemble_profile

    package = assemble_profile(ROOT / "builds" / "cc-v1.json", tmp_path / "packages")
    source_root = package
    script = r'''
import sys
sys.path.insert(0, sys.argv[1])
from pydantic import TypeAdapter, ValidationError
from chrom_platform.modules.prediction.schemas import PredictionCreate
valid = {
  "chromatography_type": "cc", "tool": "single", "schema_version": "1.0",
  "model_selection": {"mode": "exact", "model_id": "model-a", "model_version": "1"},
  "structure_refs": ["structure-a", "structure-b"],
  "input": {"eluent_system_id": "eluent-a", "loading_solvent_id": "solvent-a", "sample_mass_mg": 1.0,
            "loading_solvent_volume_ul": 10.0, "flow_rate_ml_min": 1.0, "column_spec_id": "column-a"}
}
adapter = TypeAdapter(PredictionCreate)
assert adapter.validate_python(valid).chromatography_type == "cc"
invalid = {**valid, "structure_refs": ["structure-a", "structure-a"]}
try:
    adapter.validate_python(invalid)
except ValidationError:
    pass
else:
    raise AssertionError("duplicate CC structure refs were accepted")
'''
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, str(source_root)], cwd=ROOT,
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_physical_profile_prediction_dtos_are_isolated_and_validated(tmp_path: Path) -> None:
    from chrom_platform.assembly.profile import assemble_profile

    script = r'''
import sys
from pydantic import TypeAdapter, ValidationError
from chrom_platform.modules.prediction.schemas import PredictionCreate

cc = {
  "chromatography_type": "cc", "tool": "single", "schema_version": "1.0",
  "model_selection": {"mode": "exact", "model_id": "model-a", "model_version": "1"},
  "structure_refs": ["structure-a", "structure-b"],
  "input": {"eluent_system_id": "eluent-a", "loading_solvent_id": "solvent-a", "sample_mass_mg": 1.0,
            "loading_solvent_volume_ul": 10.0, "flow_rate_ml_min": 1.0, "column_spec_id": "column-a"}
}
sfc = {
  "chromatography_type": "sfc", "tool": "single_column", "schema_version": "1.0",
  "model_selection": {"mode": "exact", "model_id": "model-a", "model_version": "1"},
  "structure_refs": ["structure-a", "structure-b"],
  "input": {"modifier_total_fraction": 0.2, "modifier_components": [{"structure_ref": "methanol", "fraction": 1.0}],
            "candidate_scope_id": "conditions-a", "top_k": 5, "column_id": "column-a"}
}
adapter = TypeAdapter(PredictionCreate)
profile = sys.argv[2]
if profile == "cc-v1":
    assert adapter.validate_python(cc).chromatography_type == "cc"
    bad = {**cc, "structure_refs": ["structure-a", "structure-a"]}
    try:
        adapter.validate_python(bad)
    except ValidationError:
        pass
    else:
        raise AssertionError("CC duplicate pair accepted")
elif profile == "sfc-v1":
    assert adapter.validate_python(sfc).chromatography_type == "sfc"
    bad = {**sfc, "input": {**sfc["input"], "unknown_axis": 1}}
    try:
        adapter.validate_python(bad)
    except ValidationError:
        pass
    else:
        raise AssertionError("SFC unknown input field accepted")
    from chrom_platform.modules.prediction.schemas import SfcCandidateScopeCreate
    try:
        SfcCandidateScopeCreate.model_validate({"project_id": "p", "name": "n", "axes": {"unknown_axis": ["x"]}})
    except ValidationError:
        pass
    else:
        raise AssertionError("SFC unknown scope axis accepted")
else:
    assert profile == "universal-v1"
    assert adapter.validate_python(cc).chromatography_type == "cc"
    assert adapter.validate_python(sfc).chromatography_type == "sfc"
    bad = {**cc, "structure_refs": ["structure-a", "structure-a"]}
    try:
        adapter.validate_python(bad)
    except ValidationError:
        pass
    else:
        raise AssertionError("universal CC duplicate pair accepted")
'''
    for build_id in ("sfc-v1", "cc-v1", "universal-v1"):
        package = assemble_profile(ROOT / "builds" / f"{build_id}.json", tmp_path / "packages")
        result = subprocess.run(
            [sys.executable, "-B", "-c", script, str(package), build_id],
            cwd=ROOT,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": str(package), "PYTHONDONTWRITEBYTECODE": "1"},
        )
        assert result.returncode == 0, f"{build_id}: {result.stdout}{result.stderr}"

    minimal = assemble_profile(ROOT / "builds" / "minimal-structure-v1.json", tmp_path / "packages")
    assert not (minimal / "chrom_platform" / "modules" / "prediction" / "schemas.py").exists()


def test_physical_profile_archive_confirmation_chain_reaches_routes(tmp_path: Path) -> None:
    from chrom_platform.assembly.profile import assemble_profile

    script = r'''
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, sys.argv[1])
build_id, db_path = sys.argv[2], sys.argv[3]
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from chrom_platform.app import create_app

if build_id == "minimal-structure-v1":
    app = create_app(Path("build-manifest.json"), contract_generation=True)
    assert "/api/v1/prediction-records" not in app.openapi()["paths"]
    assert "/api/v1/prediction-records/{record_id}/archive" not in app.openapi()["paths"]
    with TestClient(app) as client:
        missing = client.post("/api/v1/prediction-records/record-a/archive?project_id=project-a", json={})
        assert missing.status_code == 404, (missing.status_code, missing.text)
    raise SystemExit(0)

from chrom_platform.infrastructure.db import Base
from chrom_platform.infrastructure.models import Project
from chrom_platform.infrastructure.metadata import m2_metadata
from chrom_platform.modules.data_center.persistence import PredictionRecordRow

engine = create_engine("sqlite:///" + db_path, connect_args={"timeout": 10})
event.listen(engine, "connect", lambda dbapi, _: dbapi.create_function("CHAR_LENGTH", 1, len))
Base.metadata.create_all(engine)
m2_metadata().create_all(engine)
sessions = sessionmaker(engine, expire_on_commit=False)
record_type = {"sfc-v1": "sfc", "cc-v1": "cc", "universal-v1": "sfc"}[build_id]
now = datetime.now(timezone.utc)
with sessions.begin() as session:
    session.add(Project(id="project-a", slug="project-a", name="Project A", status="ACTIVE", revision=1))
    session.add(PredictionRecordRow(
        id="record-a", project_id="project-a", prediction_id=None, batch_id=None,
        source="web", actor_id="actor-a", chromatography_type=record_type,
        model_snapshot={"id": "model-a", "version": "1", "business_scope_id": record_type,
                        "artifact_sha256": "a" * 64, "input_schema_version": "1.0",
                        "output_schema_version": "1.0"},
        input_snapshot={}, result_ref=None, warnings=[], status="SUCCEEDED", archived_at=None,
        created_at=now, artifact_refs=[], revision=1, etag='"record-a-v1"',
    ))

import chrom_platform.app as app_module
app_module._create_session_factory = lambda: sessions
os.environ["CHROM_PROJECT_IDS"] = "project-a"
os.environ["CHROM_ACTOR_ID"] = "actor-a"
app = create_app(Path("build-manifest.json"), contract_generation=True)
with TestClient(app) as client:
    ref = {"type": "prediction_record", "id": "record-a"}
    preview_response = client.post("/api/v1/confirmations/previews", json={"resource_ref": ref, "action": "resource.archive"})
    assert preview_response.status_code == 200, (build_id, preview_response.text)
    preview = preview_response.json()["data"]
    assert preview["can_confirm"] is True
    created = client.post("/api/v1/confirmations", headers={"If-Match": preview["etag"], "Idempotency-Key": "confirm-a"}, json={
        "resource_ref": ref, "action": "resource.archive", "revision": preview["revision"],
        "config_hash": preview["config_hash"], "policy_version": preview["policy_version"],
    })
    assert created.status_code == 201, (build_id, created.text)
    confirmation_id = created.json()["data"]["id"]
    endpoint = "/api/v1/prediction-records/record-a/archive?project_id=project-a"
    headers = {"Idempotency-Key": "archive-a"}
    archived = client.post(endpoint, headers=headers, json={"confirmation_id": confirmation_id})
    replayed = client.post(endpoint, headers=headers, json={"confirmation_id": confirmation_id})
    assert archived.status_code == 200, (build_id, archived.text)
    assert replayed.status_code == 200 and replayed.headers.get("Idempotency-Replayed") == "true"
    assert archived.json()["data"]["status"] == "ARCHIVED"
engine.dispose()
'''
    for build_id in ("sfc-v1", "cc-v1", "universal-v1", "minimal-structure-v1"):
        package = assemble_profile(ROOT / "builds" / f"{build_id}.json", tmp_path / "packages")
        assembly_source = package / "chrom_platform" / "assembly" / "m2.py"
        worker_source = package / "chrom_platform" / "worker_cli.py"
        if build_id == "minimal-structure-v1":
            assert not assembly_source.exists()
            assert not worker_source.exists()
        else:
            m2_text = assembly_source.read_text(encoding="utf-8")
            assert "consume_prediction_record_archive" in m2_text
            assert "resolve_prediction_record_confirmation" in m2_text
            assert "resolve_batch_confirmation" not in m2_text
            assert "BatchPredictionService" not in m2_text
            assert "batch_prediction_service" not in m2_text
            if worker_source.is_file():
                worker_text = worker_source.read_text(encoding="utf-8")
                assert '"batch_prediction"' not in worker_text
                assert "_validate_batch_artifact_store" not in worker_text
        result = subprocess.run(
            [sys.executable, "-B", "-I", "-c", script, str(package), build_id, str(tmp_path / f"{build_id}.db")],
            cwd=package,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        assert result.returncode == 0, f"{build_id}: {result.stdout}{result.stderr}"


def test_physical_profile_replay_source_draft_and_record_staging_are_persistent(tmp_path: Path) -> None:
    from chrom_platform.assembly.profile import assemble_profile

    script = r'''
import sys
from dataclasses import is_dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, sys.argv[1])
build_id, db_path = sys.argv[2], sys.argv[3]
if build_id == "minimal-structure-v1":
    from chrom_platform.app import create_app
    app = create_app(Path("build-manifest.json"), contract_generation=True)
    assert "/api/v1/prediction-drafts/{draft_id}" not in app.openapi()["paths"]
    assert not (Path("chrom_platform") / "modules" / "data_center" / "ports.py").exists()
    raise SystemExit(0)

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from chrom_platform.artifact_core.models import Structure
from chrom_platform.core.dto import ResourceRef
from chrom_platform.infrastructure.db import Base
from chrom_platform.infrastructure.models import Job, Project
from chrom_platform.infrastructure.metadata import m2_metadata
from chrom_platform.modules.data_center.errors import DataUnavailableError
from chrom_platform.modules.data_center.persistence import (
    PredictionDraftRow, PredictionRecordRow, SQLAlchemyPredictionRecordRepository,
    SQLAlchemyPredictionRecordWriter,
)
from chrom_platform.modules.data_center.ports import PredictionRecordDraft, PredictionReplaySourceData
from chrom_platform.modules.prediction.persistence import PredictionBase, PredictionRow, SQLAlchemyPredictionPersistence

assert is_dataclass(PredictionRecordDraft)
assert is_dataclass(PredictionReplaySourceData)
engine = create_engine("sqlite:///" + db_path, connect_args={"timeout": 10})
event.listen(engine, "connect", lambda dbapi, _: (dbapi.create_function("CHAR_LENGTH", 1, len), dbapi.execute("PRAGMA foreign_keys=ON")))
Base.metadata.create_all(engine)
m2_metadata().create_all(engine)
PredictionBase.metadata.create_all(engine)
sessions = sessionmaker(engine, expire_on_commit=False)
kind = "cc" if build_id == "cc-v1" else "sfc"
tool = "single" if kind == "cc" else "single_column"
schema_version = "cc-v1" if kind == "cc" else "sfc-v7"
now = datetime.now(timezone.utc)
main_refs = ["main-1", "main-2"]
modifier_refs = ["modifier-1"] if kind == "sfc" else []
all_refs = main_refs + modifier_refs
normalized = ["C[C@H](O)F", "C[C@@H](O)F"]
if kind == "cc":
    input_value = {"eluent_system_id": "pe-ea", "loading_solvent_id": "petroleum-ether", "sample_mass_mg": 10.0,
                   "loading_solvent_volume_ul": 100.0, "flow_rate_ml_min": 1.0, "column_spec_id": "silica-4g"}
else:
    input_value = {"modifier_total_fraction": 1.0, "modifier_components": [{"structure_ref": "modifier-1", "fraction": 1.0}],
                   "candidate_scope_id": "scope-1", "top_k": 5, "column_id": "column-1"}
model = {"id": "model-1", "version": "7", "business_scope_id": kind, "artifact_sha256": "a" * 64,
         "input_schema_version": schema_version, "output_schema_version": "out-v1"}
selection = {"mode": "champion", "scope_id": kind}
if kind == "cc":
    model.update(preprocessing_profile_id="cc-fixture-profile", preprocessing_profile_sha256="b" * 64)
    selection = {"mode": "exact", "model_id": "model-1", "model_version": "7",
                 "preprocessing_profile_id": "cc-fixture-profile", "preprocessing_profile_sha256": "b" * 64}
request = {"project_id": "p1", "chromatography_type": kind, "tool": tool, "schema_version": schema_version,
           "model_selection": selection, "structure_refs": main_refs, "input": input_value}
record_input = {"structure_refs": main_refs, "normalized_structures": normalized, "input": input_value}
execution_input = {**record_input}
if kind == "sfc": execution_input["modifier_structures"] = [{"structure_ref": "modifier-1", "normalized_smiles": "CO"}]
with sessions.begin() as session:
    session.add(Project(id="p1", slug="p1", name="P1", status="ACTIVE", revision=1))
    session.flush()
    session.add(Job(id="job-1", project_id="p1", owner_id="actor-original", kind="prediction",
        resource_ref={"type": "prediction", "id": "pred-1"}, status="SUCCEEDED", attempt=1,
        progress={}, cancelable=True, retryable=False, resume_mode="none", created_at=now, updated_at=now,
        revision=1, etag='"job"'))
    for index, sid in enumerate(all_refs):
        smiles = normalized[index] if index < 2 else "CO"
        session.add(Structure(id=sid, project_id="p1", schema_version="1.0",
            original_input={"input_type": "smiles", "value": smiles}, canonical_smiles=smiles,
            is_valid=True, has_stereochemistry=index < 2,
            stereochemistry_status="specified" if index < 2 else "not_applicable",
            stereochemistry_warnings=[], snapshot_hash=str(index + 1) * 64,
            source_artifact_id=None, created_at=now))
    session.flush()
    record_row = PredictionRecordRow(id="record-1", project_id="p1", prediction_id=None, batch_id=None,
        source="web", actor_id="actor-original", chromatography_type=kind, model_snapshot=model,
        input_snapshot=record_input, result_ref=None, warnings=[], status="ARCHIVED", archived_at=now,
        created_at=now, artifact_refs=[], revision=2, etag='"record-2"')
    session.add(record_row)
    session.flush()
    session.add(PredictionRow(id="pred-1", project_id="p1", prediction_record_id="record-1", job_id="job-1",
        actor_id="actor-original", chromatography_type=kind, tool=tool, schema_version=schema_version,
        status="SUCCEEDED", model_snapshot=model, input_snapshot=execution_input, request_snapshot=request,
        result=None, warnings=[], error=None, source="web", conversation_id=None, agent_run_id=None,
        created_at=now, updated_at=now, completed_at=now, data_origin="production", revision=1, etag='"pred"'))
    session.flush()
    record_row.prediction_id = "pred-1"
    session.flush()

with sessions.begin() as session:
    record_repo = SQLAlchemyPredictionRecordRepository(session, id_factory=lambda _: "draft-1")
    record = record_repo.get("p1", "record-1")
    source = SQLAlchemyPredictionPersistence(session).get_replay_source("p1", record)
    assert isinstance(source, PredictionReplaySourceData)
    assert source.model_selection == {"mode": "exact", "model_id": "model-1", "model_version": "7",
        "preprocessing_profile_id": model.get("preprocessing_profile_id"),
        "preprocessing_profile_sha256": model.get("preprocessing_profile_sha256")}
    assert source.structure_refs == main_refs
    draft = record_repo.create_replay_draft(record, source, actor_id="actor-replay")
    assert draft.structure_refs == [ResourceRef(type="structure", id=value) for value in main_refs]
    assert draft.source_record_revision == 2
    assert draft.model_selection.model_version == "7"

with sessions() as session:
    repo = SQLAlchemyPredictionRecordRepository(session)
    restored = repo.get_replay_draft("p1", "draft-1")
    assert restored is not None and restored.id == "draft-1"
    assert restored.input["column_id" if kind == "sfc" else "column_spec_id"] in {"column-1", "silica-4g"}
    assert session.scalar(select(func.count()).select_from(PredictionDraftRow)) == 1

# Exercise the physical package's public GET route against the same persisted
# draft and caller-owned SQLite database, not only the repository getter.
import os
import chrom_platform.app as app_module
from fastapi.testclient import TestClient
from chrom_platform.app import create_app
app_module._create_session_factory = lambda: sessions
os.environ["CHROM_PROJECT_IDS"] = "p1"
os.environ["CHROM_ACTOR_ID"] = "actor-replay"
app = create_app(Path("build-manifest.json"), contract_generation=True)
with TestClient(app) as client:
    response = client.get("/api/v1/prediction-drafts/draft-1?project_id=p1")
    assert response.status_code == 200, (build_id, response.status_code, response.text)
    assert response.json()["data"]["id"] == "draft-1"

# The retained PredictionRecordDraft decorator is used by the prediction-owned
# staging port to create a real row with the same caller-owned SQLite session.
staging = PredictionRecordDraft(id="record-staging", prediction_id=None, batch_id=None, project_id="p1",
    source="web", actor_id="actor-staging", chromatography_type=kind, model_snapshot=model,
    input_snapshot=record_input, result_ref=None, warnings=[], status="QUEUED", created_at=now,
    artifact_refs=[], revision=1, etag='"staging"')
with sessions.begin() as session:
    SQLAlchemyPredictionRecordWriter().stage_prediction_record(staging, session=session)
    session.flush()
    assert session.get(PredictionRecordRow, "record-staging") is not None

negative_cases = {
  "record-link": lambda row: setattr(row, "prediction_record_id", "other-record"),
  "record-model-id": lambda row: setattr(row, "model_snapshot", {**row.model_snapshot, "id": "other-model"}),
  "record-model-version": lambda row: setattr(row, "model_snapshot", {**row.model_snapshot, "version": "8"}),
  "prediction-model-id": lambda row: setattr(row, "model_snapshot", {**row.model_snapshot, "id": "other-model"}),
  "prediction-model-version": lambda row: setattr(row, "model_snapshot", {**row.model_snapshot, "version": "8"}),
  "exact-model-id": lambda row: setattr(row, "request_snapshot", {**row.request_snapshot, "model_selection": {"mode": "exact", "model_id": "other-model", "model_version": "7"}}),
  "exact-model-version": lambda row: setattr(row, "request_snapshot", {**row.request_snapshot, "model_selection": {"mode": "exact", "model_id": "model-1", "model_version": "8"}}),
  "schema": lambda row: setattr(row, "schema_version", "other-schema"),
  "tool": lambda row: setattr(row, "tool", "other-tool"),
  "type": lambda row: setattr(row, "chromatography_type", "gc"),
}
for name, mutate in negative_cases.items():
    try:
        with sessions.begin() as session:
            prediction = session.get(PredictionRow, "pred-1")
            if name == "record-link":
                # Keep the FK valid while testing a semantic mismatch: the
                # prediction row points at a different, same-project record.
                session.add(PredictionRecordRow(id="other-record", project_id="p1", prediction_id=None,
                    batch_id=None, source="web", actor_id="actor-original", chromatography_type=kind,
                    model_snapshot=model, input_snapshot=record_input, result_ref=None, warnings=[],
                    status="SUCCEEDED", archived_at=None, created_at=now, artifact_refs=[], revision=1,
                    etag='"other-record-v1"'))
                session.flush()
            mutate(prediction)
            record = SQLAlchemyPredictionRecordRepository(session).get("p1", "record-1")
            SQLAlchemyPredictionPersistence(session).get_replay_source("p1", record)
            raise AssertionError(f"negative replay source case accepted: {name}")
    except DataUnavailableError:
        pass
with sessions() as session:
    assert session.scalar(select(func.count()).select_from(PredictionDraftRow)) == 1
    try:
        with session.begin_nested():
            session.delete(session.get(PredictionRecordRow, "record-1"))
            session.flush()
    except IntegrityError:
        pass
    else:
        raise AssertionError("source record deletion was not restricted")
engine.dispose()
'''
    for build_id in ("sfc-v1", "cc-v1", "universal-v1", "minimal-structure-v1"):
        package = assemble_profile(ROOT / "builds" / f"{build_id}.json", tmp_path / "packages")
        result = subprocess.run(
            [sys.executable, "-B", "-I", "-c", script, str(package), build_id, str(tmp_path / f"{build_id}-replay.db")],
            cwd=package,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        assert result.returncode == 0, f"{build_id}: {result.stdout}{result.stderr}"


def test_profile_packages_are_hash_seed_deterministic(tmp_path: Path) -> None:
    script = """
import sys
from pathlib import Path
from chrom_platform.assembly.profile import assemble_profile
assemble_profile(Path(sys.argv[1]), Path(sys.argv[2]))
"""
    for build_id in ("sfc-v1", "cc-v1", "universal-v1", "minimal-structure-v1"):
        file_maps: list[dict[str, str]] = []
        for seed in ("1", "987654"):
            output_root = tmp_path / f"seed-{seed}" / build_id
            env = os.environ.copy()
            env["PYTHONPATH"] = str(ROOT / "backend" / "src")
            env["PYTHONHASHSEED"] = seed
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            subprocess.run(
                [sys.executable, "-B", "-c", script, str(ROOT / "builds" / f"{build_id}.json"), str(output_root)],
                cwd=ROOT,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            package = output_root / build_id
            file_maps.append({
                path.relative_to(package).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest().upper()
                for path in package.rglob("*")
                if path.is_file()
            })
            assert not any(path.name == "__pycache__" or path.suffix == ".pyc" for path in package.rglob("*"))
            worker_cli = package / "chrom_platform" / "worker_cli.py"
            assert worker_cli.is_file() is (build_id != "minimal-structure-v1")
        assert file_maps[0] == file_maps[1]


def test_task_sse_is_trimmed_only_from_minimal_profile(tmp_path: Path) -> None:
    from chrom_platform.assembly.profile import assemble_profile

    script = r'''
import json
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from chrom_platform.app import create_app

app = create_app(Path(sys.argv[1]) / "build-manifest.json", contract_generation=True)
print(json.dumps({
    "paths": len(app.openapi()["paths"]),
    "stream": "/api/v1/jobs/{job_id}/events/stream" in app.openapi()["paths"],
}))
'''
    for build_id in ("sfc-v1", "cc-v1", "universal-v1", "minimal-structure-v1"):
        package = assemble_profile(ROOT / "builds" / f"{build_id}.json", tmp_path / "packages")
        sse = package / "chrom_platform" / "task_core" / "sse.py"
        router = package / "chrom_platform" / "task_core" / "router.py"
        result = subprocess.run(
            [sys.executable, "-B", "-I", "-c", script, str(package)],
            cwd=package,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        assert result.returncode == 0, f"{build_id}: {result.stdout}{result.stderr}"
        observed = json.loads(result.stdout)
        if build_id == "minimal-structure-v1":
            assert not sse.exists()
            assert '"/jobs/{job_id}/events/stream"' not in router.read_text(encoding="utf-8")
            assert observed["paths"] > 0
            assert observed["stream"] is False
        else:
            assert sse.is_file()
            assert observed["stream"] is True


class _UnsupportedPredictionReader:
    def get(self, _ctx, _prediction_id):
        return SimpleNamespace(chromatography_type="chiral_lc")


def test_prediction_read_rejects_dto_outside_manifest() -> None:
    app = FastAPI()
    app.state.actor_context = ActorContext(
        actor_id="actor-a", tenant_id=None, project_ids=["project-a"],
        auth_method="deployment_default", permissions=["prediction:read"], policy_version="p1",
    )

    @app.exception_handler(APIError)
    async def api_error(_request, exc: APIError):
        return JSONResponse(status_code=exc.status_code, content={"error": exc.payload.model_dump(mode="json")})

    app.include_router(
        create_router(read_service=_UnsupportedPredictionReader(), enabled_types={"sfc"}),
        prefix="/api/v1",
    )
    response = TestClient(app, raise_server_exceptions=False).get("/api/v1/predictions/prediction-a")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "PREDICTION_TYPE_UNAVAILABLE"
