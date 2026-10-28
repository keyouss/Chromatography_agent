from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from chrom_platform.artifact_core.models import Artifact
from chrom_platform.artifact_core.storage import LocalArtifactStore
from chrom_platform.core.dto import ActorContext, ResourceRef
from chrom_platform.infrastructure.models import Base, Job, JobAttempt, JobEvent, OutboxEvent, Project
from chrom_platform.modules.prediction.batch_service import BatchExportRow, BatchPredictionRow, BatchRowResultRow, BatchValidationItemRow, BatchValidationRow, _hash
import chrom_platform.modules.prediction.batch_worker as batch_worker_module
from chrom_platform.modules.prediction.batch_worker import (
    BatchPredictionProductionWorker,
    BatchPredictionWorker,
    BatchWorkerError,
    _counts,
    _finalize_job_by_ref,
    _public_batch_export_snapshot,
)
from chrom_platform.modules.prediction.schemas import ModelSnapshot
from chrom_platform.task_core.state import JobStatus
from chrom_platform.task_core.repository import SQLAlchemyTaskRepository
from chrom_platform.task_core.service import add_job_attempt_rows, add_job_rows, sync_job_lifecycle

from test_batch_contracts import _batch_db


def _gc_resolved_snapshot(batch: BatchPredictionRow, structure_ref: str, normalized: str) -> dict[str, object]:
    validation = batch.validation_id
    return {
        "chromatography_type": "gc",
        "template_version": "gc-batch-1",
        "project_id": batch.project_id,
        "draft_revision": 1,
        "validation_id": validation,
        "request": {
            "project_id": batch.project_id,
            "schema_version": batch.schema_version,
            "model_selection": batch.model_selection,
            "chromatography_type": "gc",
            "tool": "single",
            "structure_refs": [structure_ref],
            "input": {"temperature_program": {"mode": "linear", "initial_temperature_c": 80.0, "heating_rate_c_per_min": 10.0, "final_temperature_c": 120.0, "initial_hold_min": 0.0}},
        },
        "normalized_structures": [normalized],
        "evaluation_count": 1,
    }


class _AsyncSessionContext:
    def __init__(self, session: Session) -> None:
        self.session = session

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def begin(self):
        return _AsyncTransactionContext(self.session)

    async def run_sync(self, callback):
        return callback(self.session)


class _AsyncTransactionContext:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.transaction = None

    async def __aenter__(self):
        self.transaction = self.session.begin()
        self.transaction.__enter__()
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return self.transaction.__exit__(exc_type, exc, traceback)


class _IndependentAsyncSessionContext:
    """Async worker adapter backed by a fresh SQLite Session per scope."""

    def __init__(self, engine) -> None:
        self.engine = engine
        self.session: Session | None = None

    async def __aenter__(self):
        self.session = Session(self.engine)
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        assert self.session is not None
        self.session.close()
        return False

    def begin(self):
        assert self.session is not None
        return _AsyncTransactionContext(self.session)

    async def run_sync(self, callback):
        assert self.session is not None
        return callback(self.session)


def _claim_dispatch(session: Session, job: Job, *, event_type: str = "job.execute") -> dict[str, object]:
    attempt = session.scalar(select(JobAttempt).where(JobAttempt.job_id == job.id, JobAttempt.attempt == job.attempt))
    assert attempt is not None
    dispatch_id = f"claim-{event_type}-{job.id}"
    payload: dict[str, object] = {"job_id": job.id, "attempt": job.attempt, "dispatch_id": dispatch_id}
    if event_type == "job.cancel":
        attempt.fencing_token = 19
        payload["fencing_token"] = 19
        session.add(OutboxEvent(
            dispatch_id=dispatch_id, aggregate_type="job", aggregate_id=job.id,
            event_type=event_type, payload=payload, status="PROCESSING",
        ))
    else:
        dispatch = session.scalar(select(OutboxEvent).where(
            OutboxEvent.aggregate_type == "job",
            OutboxEvent.aggregate_id == job.id,
            OutboxEvent.event_type == event_type,
        ))
        assert dispatch is not None
        dispatch.status = "PROCESSING"
        dispatch.payload = payload
        dispatch_id = dispatch.dispatch_id
        payload["dispatch_id"] = dispatch_id
    session.commit()
    return payload


def _async_claim_worker(session: Session, *, enabled_batch_types: set[str], registry: object | None = None) -> BatchPredictionProductionWorker:
    return BatchPredictionProductionWorker(
        session_factory=lambda: _AsyncSessionContext(session), registry=registry or object(),
        actor=ActorContext(actor_id="actor-a", project_ids=["p1"], auth_method="service", permissions=["*"], policy_version="test"),
        enabled_batch_types=enabled_batch_types,
    )


def test_batch_worker_payload_is_strictly_fenced() -> None:
    with pytest.raises(BatchWorkerError, match="INVALID_WORKER_PAYLOAD"):
        BatchPredictionProductionWorker._message({
            "job_id": "job-1", "attempt": 1, "dispatch_id": "dispatch-1", "resource": "batch-1",
        })
    assert BatchPredictionProductionWorker._message(
        {"job_id": "job-1", "attempt": 2, "dispatch_id": "dispatch-2"}
    ) == ("job-1", 2, "dispatch-2", None)
    assert BatchPredictionProductionWorker._message(
        {"job_id": "job-1", "attempt": 2, "dispatch_id": "dispatch-2", "fencing_token": 9},
        event_type="job.cancel",
    ) == ("job-1", 2, "dispatch-2", 9)
    with pytest.raises(BatchWorkerError, match="INVALID_WORKER_PAYLOAD"):
        BatchPredictionProductionWorker._message(
            {"job_id": "job-1", "attempt": 2, "dispatch_id": "dispatch-2"},
            event_type="job.cancel",
        )


def test_old_export_lease_cleanup_cannot_remove_new_attempt_artifact(tmp_path) -> None:
    database = tmp_path / "batch-export-race.sqlite"
    engine = create_engine(f"sqlite:///{database}", connect_args={"timeout": 10})

    def configure_sqlite(connection, _record) -> None:
        connection.create_function("CHAR_LENGTH", 1, lambda value: len(value) if value is not None else None)
        connection.execute("PRAGMA journal_mode=WAL")

    event.listen(engine, "connect", configure_sqlite)
    Base.metadata.create_all(engine)
    export_id = "export-lease-race"
    source = {
        "source": {"draft_revision": 1, "validation_id": "validation-race", "attempt": 1},
        "include": ["result"],
        "records": [{"record_id": "row-1", "result": {"prediction": 3.5}}],
    }
    with Session(engine) as seed:
        seed.add(Project(id="p1", slug="p1", name="P1"))
        seed.add(Artifact(
            id="artifact-input", project_id="p1", kind="batch_input", media_type="text/csv",
            size_bytes=1, storage_key="batch/input.csv", status="AVAILABLE", scan_status="PASSED",
            created_by="actor-a",
        ))
        repo = SQLAlchemyTaskRepository(lambda: seed)
        job = add_job_rows(
            repo, seed, project_id="p1", owner_id="actor-a", kind="batch_prediction",
            resource_ref=ResourceRef(type="batch_export", id=export_id), cancelable=True,
            retryable=True, resume_mode="restart", deadline_at=None,
        )
        first_attempt = seed.scalar(select(JobAttempt).where(JobAttempt.job_id == job.id, JobAttempt.attempt == 1))
        assert first_attempt is not None
        job.status = JobStatus.RUNNING.value
        first_attempt.status = JobStatus.RUNNING.value
        first_attempt.fencing_token = 11
        first_attempt.started_at = datetime.now(timezone.utc)
        seed.add(BatchPredictionRow(
            id="batch-1", project_id="p1", actor_id="actor-a", chromatography_type="gc", tool="batch",
            name="batch", schema_version="gc-input-v1", model_selection={"mode": "champion", "scope_id": "scope"},
            input_artifact_id="artifact-input", template_version="gc-batch-1",
            field_mapping={"structure_ref": "structure_ref", "temperature_program": "temperature_program"},
            common_input={}, invalid_row_policy="block", resource_budget={"max_runtime_seconds": 60, "max_evaluations": 10},
            revision=1, config_hash="a" * 64, etag='"batch-v1"', draft_status="READY", counts={"total": 1},
        ))
        seed.add(BatchExportRow(
            id=export_id, project_id="p1", batch_id="batch-1", job_id=job.id,
            format="json", include=["result"], source_snapshot=source,
            source_snapshot_hash=_hash(source), status="QUEUED",
        ))
        seed.commit()
        job_id = job.id

    store = LocalArtifactStore(tmp_path / "artifacts")

    class Guard:
        expired = False

        def check(self):
            if self.expired:
                raise RuntimeError("lease expired")

    guard = Guard()
    old_key: str | None = None
    new_key: str | None = None
    removed: list[str] = []

    class InterleavingStore:
        configured = True

        def key_for(self, artifact_id: str) -> str:
            return store.key_for(artifact_id)

        def put(self, artifact_id: str, content: bytes, *, expected_size: int, expected_sha256=None):
            nonlocal old_key, new_key
            if old_key is None:
                old_key = store.key_for(artifact_id)
                guard.expired = True  # lease expires before the retry publishes
                with Session(engine) as retry_session:
                    retry_repo = SQLAlchemyTaskRepository(lambda: retry_session)
                    old_job = retry_session.get(Job, job_id)
                    assert old_job is not None
                    sync_job_lifecycle(
                        retry_repo, retry_session, job_id=job_id,
                        status=JobStatus.FAILED, attempt=1, expected_attempt=1,
                        fencing_token=11,
                    )
                    add_job_attempt_rows(retry_repo, retry_session, job_id=job_id, attempt=2)
                    retry_attempt = retry_session.scalar(select(JobAttempt).where(
                        JobAttempt.job_id == job_id, JobAttempt.attempt == 2,
                    ))
                    assert retry_attempt is not None
                    retry_attempt.fencing_token = 29
                    sync_job_lifecycle(
                        retry_repo, retry_session, job_id=job_id,
                        status=JobStatus.RUNNING, attempt=2, expected_attempt=2,
                        fencing_token=29, started_at=datetime.now(timezone.utc),
                    )
                    retry_session.commit()
                with Session(engine) as retry_session:
                    retry_worker = BatchPredictionProductionWorker(
                        session_factory=object(), registry=object(),
                        actor=ActorContext(actor_id="actor-a", project_ids=["p1"], auth_method="service", permissions=["*"], policy_version="test"),
                        artifact_store=store, enabled_batch_types={"gc"},
                    )
                    assert retry_worker._execute_export(retry_session, export_id, 2, 29, None) is JobStatus.SUCCEEDED
                with Session(engine) as verify:
                    final_artifact_id = verify.scalar(select(BatchExportRow.artifact_id).where(BatchExportRow.id == export_id))
                    artifact = verify.get(Artifact, final_artifact_id)
                    assert artifact is not None
                    new_key = artifact.storage_key
            return store.put(artifact_id, content, expected_size=expected_size, expected_sha256=expected_sha256)

        def remove(self, storage_key: str) -> None:
            removed.append(storage_key)
            store.remove(storage_key)

    old_worker = BatchPredictionProductionWorker(
        session_factory=object(), registry=object(),
        actor=ActorContext(actor_id="actor-a", project_ids=["p1"], auth_method="service", permissions=["*"], policy_version="test"),
        artifact_store=InterleavingStore(), enabled_batch_types={"gc"},
    )
    with Session(engine) as old_session:
        with pytest.raises(RuntimeError, match="lease expired"):
            old_worker._execute_export(old_session, export_id, 1, 11, guard)

    assert old_key is not None and new_key is not None and old_key != new_key
    assert removed == [old_key]
    with Session(engine) as verify:
        export = verify.get(BatchExportRow, export_id)
        assert export is not None and export.status == JobStatus.SUCCEEDED.value
        artifact = verify.get(Artifact, export.artifact_id)
        assert artifact is not None and artifact.status == "AVAILABLE"
        content = store.read(new_key)
        assert hashlib.sha256(content).hexdigest() == artifact.sha256
        assert len(content) == artifact.size_bytes
    assert (tmp_path / "artifacts" / old_key).exists() is False
    assert (tmp_path / "artifacts" / new_key).is_file()


def _batch_export_input_case(chromatography_type: str):
    selection = {
        "mode": "exact", "model_id": "m1", "model_version": "1",
        "preprocessing_profile_id": "profile-1", "preprocessing_profile_sha256": "d" * 64,
    }
    common = {"project_id": "p1", "schema_version": "1.1", "model_selection": selection}
    if chromatography_type == "tlc":
        batch = SimpleNamespace(
            project_id="p1", chromatography_type="tlc", template_version="tlc-batch-2",
            schema_version="1.1", model_selection=selection,
        )
        resolved = {
            "chromatography_type": "tlc", "template_version": "tlc-batch-2",
            "project_id": "p1", "draft_revision": 2, "validation_id": "validation-1",
            "structure_ref": "structure-a", "normalized_smiles": "CC",
            "solvent_conditions": {"solvent_components": [
                {"solvent_id": "hexane", "fraction": 0.7},
                {"solvent_id": "ethyl_acetate", "fraction": 0.3},
            ]},
        }
    elif chromatography_type == "cc":
        batch = SimpleNamespace(
            project_id="p1", chromatography_type="cc", template_version="cc-batch-2",
            schema_version="1.1", model_selection=selection,
        )
        resolved = {
            "chromatography_type": "cc", "template_version": "cc-batch-2",
            "project_id": "p1", "draft_revision": 2, "validation_id": "validation-1",
            "request": {**common, "chromatography_type": "cc", "tool": "single",
                         "structure_refs": ["structure-a", "structure-b"],
                         "input": {"eluent_system_id": "eluent-1", "loading_solvent_id": "solvent-1",
                                   "sample_mass_mg": 12.5, "loading_solvent_volume_ul": 240.0,
                                   "flow_rate_ml_min": 3.0, "column_spec_id": "column-1"}},
            "normalized_structures": ["CC", "N"], "evaluation_count": 2,
        }
    elif chromatography_type == "hplc":
        batch = SimpleNamespace(
            project_id="p1", chromatography_type="hplc", template_version="hplc-batch-2",
            schema_version="1.1", model_selection=selection,
        )
        resolved = {
            "chromatography_type": "hplc", "template_version": "hplc-batch-2",
            "project_id": "p1", "draft_revision": 2, "validation_id": "validation-1",
            "request": {**common, "chromatography_type": "hplc", "tool": "single",
                         "structure_refs": ["structure-a", "structure-b"],
                         "input": {"model_input_smiles": ["CC", "N"], "isopropanol_fraction": 0.2,
                                   "flow_rate_ml_min": 1.0, "column_ids": ["column-1"]}},
            "normalized_structures": ["CC", "N"], "evaluation_count": 1,
        }
    elif chromatography_type == "sfc":
        batch = SimpleNamespace(
            project_id="p1", chromatography_type="sfc", template_version="sfc-batch-2",
            schema_version="1.1", model_selection=selection,
        )
        resolved = {
            "chromatography_type": "sfc", "template_version": "sfc-batch-2",
            "project_id": "p1", "draft_revision": 2, "validation_id": "validation-1",
            "request": {**common, "chromatography_type": "sfc", "tool": "single_column",
                         "structure_refs": ["structure-a", "structure-b"],
                         "input": {"modifier_total_fraction": 0.2,
                                   "modifier_components": [{"structure_ref": "modifier-1", "fraction": 1.0}],
                                   "candidate_scope_id": "scope-1", "top_k": 5, "column_id": "column-1"}},
            "normalized_structures": ["CC", "N"],
            "normalized_modifier_structures": {"modifier-1": "O"},
            "scope_provenance": None, "evaluation_count": 5,
        }
    else:
        batch = SimpleNamespace(
            project_id="p1", chromatography_type="gc", template_version="gc-batch-1",
            schema_version="1.1", model_selection=selection,
        )
        resolved = {
            "chromatography_type": "gc", "template_version": "gc-batch-1",
            "project_id": "p1", "draft_revision": 2, "validation_id": "validation-1",
            "request": {**common, "chromatography_type": "gc", "tool": "single",
                         "structure_refs": ["structure-a"],
                         "input": {"temperature_program": {"mode": "linear",
                                   "initial_temperature_c": 80.0, "heating_rate_c_per_min": 10.0,
                                   "final_temperature_c": 120.0, "initial_hold_min": 0.0}}},
            "normalized_structures": ["CC"], "evaluation_count": 1,
        }
    return batch, SimpleNamespace(id="validation-1", draft_revision=2), resolved


@pytest.mark.parametrize("chromatography_type", ["tlc", "cc", "hplc", "gc", "sfc"])
def test_batch_export_projection_renders_all_five_typed_inputs_and_dict_warnings(chromatography_type) -> None:
    from chrom_platform.modules.data_center.exports import render_export

    batch, validation, resolved = _batch_export_input_case(chromatography_type)
    source = {
        "format_version": "1.0", "generated_at": "2026-10-22T00:00:00+00:00",
        "include": ["input_snapshot", "model_version", "warnings"],
        "records": [{"record_id": "row-1", "status": "SUCCEEDED", "source": "batch_prediction",
                     "chromatography_type": chromatography_type, "model_version": "1",
                     "input_snapshot": resolved,
                     "warnings": [{"code": "OUT_OF_DOMAIN", "message": "适用范围警告"}]}],
    }
    projected = _public_batch_export_snapshot(source, batch, validation)
    body, _media_type, _extension = render_export("csv", projected)
    exported = next(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))
    public_input = json.loads(exported["input_snapshot"])
    assert exported["model_version"] == "1"
    assert "OUT_OF_DOMAIN: 适用范围警告" in exported["warnings"]
    assert public_input["normalized_structures"]
    if chromatography_type == "tlc":
        assert public_input["normalized_structures"] == ["CC"]
        assert public_input["input"]["solvent_components"][0]["fraction"] == 0.7
    elif chromatography_type == "cc":
        assert public_input["input"]["loading_solvent_volume_ul"] == 240.0
        assert public_input["input"]["flow_rate_ml_min"] == 3.0
    elif chromatography_type == "hplc":
        assert public_input["structure_refs"] == ["structure-a", "structure-b"]
        assert public_input["input"]["model_input_smiles"] == ["CC", "N"]
    elif chromatography_type == "sfc":
        assert public_input["input"]["candidate_scope_id"] == "scope-1"
        assert public_input["input"]["column_id"] == "column-1"
    else:
        assert public_input["input"]["temperature_program"]["heating_rate_c_per_min"] == 10.0


@pytest.mark.parametrize("input_case", ["valid", "malformed_current", "non_mapping"])
def test_production_export_persists_tlc_rows_and_safe_public_csv(tmp_path, input_case) -> None:
    from chrom_platform.modules.prediction.batch import BatchExportCreate
    from chrom_platform.modules.prediction.batch_service import BatchPredictionService
    from chrom_platform.task_core.service import OperationContext

    database = tmp_path / "batch-export-tlc.sqlite"
    engine = create_engine(f"sqlite:///{database}", connect_args={"timeout": 10})
    event.listen(engine, "connect", lambda dbapi, _: dbapi.create_function(
        "CHAR_LENGTH", 1, lambda value: len(value) if value is not None else None,
    ))
    Base.metadata.create_all(engine)
    actor = ActorContext(
        actor_id="actor-a", project_ids=["p1"], auth_method="service",
        permissions=["*"], policy_version="test",
    )
    selection = {
        "mode": "exact", "model_id": "m1", "model_version": "1",
        "preprocessing_profile_id": "profile-1", "preprocessing_profile_sha256": "d" * 64,
    }
    model_snapshot = {
        "id": "m1", "version": "1", "business_scope_id": "tlc",
        "artifact_sha256": "a" * 64, "input_schema_version": "1.1",
        "output_schema_version": "1",
    }
    batch_id, validation_id = "batch-export-tlc", "validation-export-tlc"
    resolved = {
        "chromatography_type": "tlc", "template_version": "tlc-batch-2",
        "project_id": "p1", "draft_revision": 1, "validation_id": validation_id,
        "structure_ref": "structure-a", "normalized_smiles": "CC",
        "solvent_conditions": {"solvent_components": [
            {"solvent_id": "hexane", "fraction": 0.7},
            {"solvent_id": "ethyl_acetate", "fraction": 0.3},
        ]},
    }
    result = {
        "rf": 0.42, "unit": "dimensionless", "warning_source": "synthetic",
    }
    with Session(engine) as seed:
        seed.add(Project(id="p1", slug="p1", name="P1"))
        seed.add(Artifact(
            id="artifact-input", project_id="p1", kind="batch_input", media_type="text/csv",
            size_bytes=1, storage_key="batch/input.csv", status="AVAILABLE", scan_status="PASSED",
            created_by="actor-a",
        ))
        repo = SQLAlchemyTaskRepository(lambda: seed)
        parent_job = add_job_rows(
            repo, seed, project_id="p1", owner_id="actor-a", kind="batch_prediction",
            resource_ref=ResourceRef(type="batch_prediction", id=batch_id), cancelable=True,
            retryable=True, resume_mode="restart", deadline_at=None,
        )
        batch = BatchPredictionRow(
            id=batch_id, project_id="p1", actor_id="actor-a", chromatography_type="tlc", tool="batch",
            name="TLC export", schema_version="1.1", model_selection=selection,
            model_snapshot=model_snapshot, input_artifact_id="artifact-input", template_version="tlc-batch-2",
            field_mapping={"structure_ref": "structure_ref"}, common_input={}, invalid_row_policy="exclude",
            resource_budget={"max_runtime_seconds": 60, "max_evaluations": 10}, revision=1,
            config_hash="b" * 64, etag='"batch-ready"', draft_status="READY", validation_id=validation_id,
            job_id=parent_job.id, attempt=1, execution_status="SUCCEEDED", submitted_at=datetime.now(timezone.utc),
            counts={"total": 3, "succeeded": 2, "skipped": 1},
        )
        validation = BatchValidationRow(
            id=validation_id, project_id="p1", batch_id=batch_id, draft_revision=1,
            config_hash=batch.config_hash, status="READY", template_version="tlc-batch-2",
            model_snapshot=model_snapshot, total_rows=3, valid_rows=2, invalid_rows=1, excluded_rows=1,
            total_evaluations=2, resource_estimate={"max_runtime_seconds": 60, "max_evaluations": 10},
        )
        valid_items = [
            BatchValidationItemRow(
                id=f"validation-item-{number}", project_id="p1", validation_id=validation_id,
                row_id=f"row-{number}", source_row_number=number, status="VALID",
                normalized_input_hash=_hash(resolved), resolved_input_snapshot=resolved,
                resolved_input_hash=_hash(resolved), field_errors=[], warnings=[],
            )
            for number in (1, 2)
        ]
        invalid_item = BatchValidationItemRow(
            id="validation-item-3", project_id="p1", validation_id=validation_id,
            row_id="row-3", source_row_number=3, status="INVALID",
            normalized_input_hash=_hash({"invalid": True}), resolved_input_snapshot=None,
            resolved_input_hash=None, field_errors=[{"code": "ROW_INVALID", "message": "结构无效"}], warnings=[],
        )
        validation.resolved_input_hash = _hash([
            {"row_id": item.row_id, "input_hash": item.resolved_input_hash} for item in valid_items
        ])
        first_result_input = resolved
        if input_case == "malformed_current":
            first_result_input = {key: value for key, value in resolved.items() if key != "normalized_smiles"}
        rows = [
            BatchRowResultRow(
                id=f"result-{number}", project_id="p1", batch_id=batch_id, validation_id=validation_id,
                row_id=f"row-{number}", source_row_number=number,
                input_snapshot=first_result_input if number == 1 else resolved,
                result=result, status="SUCCEEDED", attempt=1,
                warnings=[{"code": "OUT_OF_DOMAIN", "message": "适用范围警告"}],
            )
            for number in (1, 2)
        ]
        rows.append(BatchRowResultRow(
            id="result-3", project_id="p1", batch_id=batch_id, validation_id=validation_id,
            row_id="row-3", source_row_number=3, input_snapshot={}, result=None,
            error={"code": "ROW_INVALID", "message": "结构无效"}, warnings=[], attempt=1, status="SKIPPED",
        ))
        parent_job.status = JobStatus.SUCCEEDED.value
        seed.add_all([batch, validation, *valid_items, invalid_item, *rows])
        seed.flush()

        accepted = BatchPredictionService(seed, enabled_batch_types={"tlc"}).export(
            OperationContext(actor), batch_id,
            BatchExportCreate(format="csv", include=["input_snapshot", "result", "model_version", "warnings"]),
            if_match=batch.etag,
        )
        export_job = seed.get(Job, accepted.job_id)
        assert export_job is not None
        payload = _claim_dispatch(seed, export_job)
        export = seed.get(BatchExportRow, accepted.id)
        assert export is not None and export.source_snapshot is not None
        # Exercise the renderer's allowlist against a secret-looking field in
        # a hash-bound persisted snapshot, without changing any business row.
        modified_source = dict(export.source_snapshot)
        modified_records = [dict(item) for item in modified_source["records"]]
        modified_records[0]["cancel_token"] = "synthetic-secret-token"
        if input_case == "non_mapping":
            modified_records[0]["input_snapshot"] = "corrupt current input"
        modified_source["records"] = modified_records
        export.source_snapshot = modified_source
        export.source_snapshot_hash = _hash(modified_source)
        frozen_source_hash = export.source_snapshot_hash
        seed.commit()
        job_id, export_id = export_job.id, export.id

    store = LocalArtifactStore(tmp_path / "artifact-store")
    worker = BatchPredictionProductionWorker(
        session_factory=lambda: _IndependentAsyncSessionContext(engine),
        sync_session_factory=lambda: Session(engine), registry=object(), actor=actor,
        artifact_store=store, enabled_batch_types={"tlc"},
    )
    expected_status = JobStatus.SUCCEEDED if input_case == "valid" else JobStatus.FAILED
    assert asyncio.run(worker.handle(payload)) is expected_status

    with Session(engine) as verify:
        export = verify.get(BatchExportRow, export_id)
        job = verify.get(Job, job_id)
        attempt = verify.scalar(select(JobAttempt).where(JobAttempt.job_id == job_id, JobAttempt.attempt == 1))
        event_type = "job.succeeded" if input_case == "valid" else "job.failed"
        event_row = verify.scalar(select(JobEvent).where(JobEvent.job_id == job_id, JobEvent.type == event_type))
        assert export is not None and job is not None and attempt is not None and event_row is not None
        assert export.status == job.status == attempt.status == expected_status.value
        assert export.source_snapshot_hash == frozen_source_hash
        assert _hash(export.source_snapshot) == frozen_source_hash
        if input_case != "valid":
            assert export.error == {"code": "BATCH_EXPORT_GENERATION_FAILED", "message": "批量导出生成失败"}
            assert job.result_ref is None and export.artifact_id is None
            assert verify.scalar(select(Artifact.id).where(Artifact.kind == "batch_export")) is None
            return
        assert job.result_ref == {"type": "artifact", "id": export.artifact_id}
        artifact = verify.get(Artifact, export.artifact_id)
        assert artifact is not None and artifact.kind == "batch_export" and artifact.status == "AVAILABLE"
        content = store.read(artifact.storage_key).decode("utf-8-sig")
        records = list(csv.DictReader(io.StringIO(content)))
        assert [row["status"] for row in records] == ["SUCCEEDED", "SUCCEEDED", "SKIPPED"]
        assert all(row["model_version"] == "1" for row in records)
        for row in records[:2]:
            public_input = json.loads(row["input_snapshot"])
            public_result = json.loads(row["result"])
            assert public_input["normalized_structures"] == ["CC"]
            assert public_input["input"]["solvent_components"][0]["fraction"] == 0.7
            assert public_result["rf"] == 0.42 and public_result["unit"] == "dimensionless"
            assert "OUT_OF_DOMAIN: 适用范围警告" in row["warnings"]
            assert "BATCH_SOURCE_ROW:" in row["warnings"]
        skipped = records[2]
        assert skipped["input_snapshot"] == ""
        assert "ROW_INVALID: 结构无效" in skipped["warnings"]
        assert "BATCH_SOURCE_ROW: 源数据第 3 行" in skipped["warnings"]
        assert "synthetic-secret-token" not in content
        assert store.read(artifact.storage_key).decode("utf-8-sig") == content


def test_batch_worker_partial_failure_projects_terminal_counts() -> None:
    _engine, session, ctx, batch, job = _batch_db()
    snapshot = ModelSnapshot(
        id="m1", version="1", business_scope_id="scope", artifact_sha256="a" * 64,
        input_schema_version="1.1", output_schema_version="1",
        preprocessing_profile_id="gc-test-profile", preprocessing_profile_sha256="b" * 64,
    ).model_dump(mode="json")
    batch.schema_version = "1.1"
    batch.model_selection = {
        "mode": "exact", "model_id": "m1", "model_version": "1",
        "preprocessing_profile_id": "gc-test-profile", "preprocessing_profile_sha256": "b" * 64,
    }
    batch.model_snapshot = snapshot
    resolved_rows = [
        _gc_resolved_snapshot(batch, "s1", "C"),
        _gc_resolved_snapshot(batch, "s2", "N"),
    ]
    rows = [
        BatchRowResultRow(
            id=f"r{index}", project_id="p1", batch_id=batch.id, row_id=f"row-{index}", source_row_number=index,
            input_snapshot=snapshot, status="QUEUED", attempt=1, warnings=[], validation_id=batch.validation_id,
        )
        for index, snapshot in enumerate(resolved_rows, start=1)
    ]
    session.add_all(rows)
    validation = session.get(BatchValidationRow, batch.validation_id)
    assert validation is not None
    validation.model_snapshot = snapshot
    validation.total_rows = validation.valid_rows = 2
    validation.resolved_input_hash = _hash([
        {"row_id": item.row_id, "input_hash": _hash(item.input_snapshot)} for item in rows
    ])
    session.add_all([BatchValidationItemRow(id=f"item-{item.row_id}", project_id="p1", validation_id=batch.validation_id, row_id=item.row_id, source_row_number=item.source_row_number, status="VALID", normalized_input_hash=_hash(item.input_snapshot), resolved_input_snapshot=item.input_snapshot, resolved_input_hash=_hash(item.input_snapshot), field_errors=[], warnings=[]) for item in rows])
    session.flush()
    class Registry:
        normalized = []
        def execute_snapshot(self, _kind, request, *, model_snapshot, normalized_structures=None):
            self.normalized.append(normalized_structures)
            if request.structure_refs == ["s2"]:
                raise RuntimeError("adapter failed")
            return SimpleNamespace(model_dump=lambda mode="json": {
                "task_type": "gc",
                "metrics": [{"key": "retention", "value": 12.5, "unit": "s"}],
                "applicability": {
                    "supported_scope": "verified range",
                    "warnings": [{"code": "OUT_OF_DOMAIN", "message": "预测超出已验证适用范围"}],
                },
            })

    class RecordWriter:
        calls = []

        def write_batch_row_record(self, **kwargs):
            self.calls.append(kwargs)

    record_writer = RecordWriter()
    result = BatchPredictionWorker(
        session, Registry(), enabled_batch_types={"gc"}, record_writer=record_writer,
    ).execute(batch.id, actor=ctx.actor, expected_attempt=job.attempt)
    assert result is JobStatus.PARTIAL
    succeeded = session.get(BatchRowResultRow, "r1")
    assert succeeded.status == "SUCCEEDED"
    assert succeeded.result == {
        "task_type": "gc",
        "metrics": [{"key": "retention", "value": 12.5, "unit": "s"}],
        "applicability": {
            "supported_scope": "verified range",
            "warnings": [{"code": "OUT_OF_DOMAIN", "message": "预测超出已验证适用范围"}],
        },
    }
    assert succeeded.warnings == [{"code": "OUT_OF_DOMAIN", "message": "预测超出已验证适用范围"}]
    assert session.get(BatchRowResultRow, "r2").status == "FAILED"
    assert Registry.normalized == [["C"], ["N"]]
    assert session.get(Job, job.id).progress["completed"] == 2
    assert [call["status"] for call in record_writer.calls] == ["SUCCEEDED", "FAILED"]
    assert all(call["session"] is session for call in record_writer.calls)
    assert record_writer.calls[0]["input_snapshot"] == {
        "structure_refs": ["s1"],
        "normalized_structures": ["C"],
        "input": resolved_rows[0]["request"]["input"],
    }
    assert "row_id" not in record_writer.calls[0]["input_snapshot"]
    assert record_writer.calls[0]["source"] == "service"
    assert record_writer.calls[0]["warnings"] == [
        f"{warning['code']}: {warning['message']}" for warning in succeeded.warnings
    ]
    assert record_writer.calls[0]["artifact_refs"] == [{"type": "artifact", "id": batch.input_artifact_id}]


def test_batch_worker_records_cancellation_after_science_execution() -> None:
    _engine, session, ctx, batch, job = _batch_db()
    snapshot = ModelSnapshot(
        id="m1", version="1", business_scope_id="scope", artifact_sha256="a" * 64,
        input_schema_version="1.1", output_schema_version="1",
        preprocessing_profile_id="gc-test-profile", preprocessing_profile_sha256="b" * 64,
    ).model_dump(mode="json")
    batch.schema_version = "1.1"
    batch.model_selection = {
        "mode": "exact", "model_id": "m1", "model_version": "1",
        "preprocessing_profile_id": "gc-test-profile", "preprocessing_profile_sha256": "b" * 64,
    }
    batch.model_snapshot = snapshot
    resolved = _gc_resolved_snapshot(batch, "s1", "C")
    row = BatchRowResultRow(
        id="cancel-after-science", project_id=batch.project_id, batch_id=batch.id,
        row_id="logical-cancel", source_row_number=1, input_snapshot=resolved,
        status="QUEUED", attempt=1, warnings=[{"code": "PREVIOUS_ATTEMPT", "message": "previous warning"}],
        validation_id=batch.validation_id,
    )
    session.add(row)
    validation = session.get(BatchValidationRow, batch.validation_id)
    assert validation is not None
    validation.model_snapshot = snapshot
    validation.total_rows = validation.valid_rows = 1
    validation.resolved_input_hash = _hash([{"row_id": row.row_id, "input_hash": _hash(resolved)}])
    session.add(BatchValidationItemRow(
        id="item-cancel-after-science", project_id=batch.project_id, validation_id=batch.validation_id,
        row_id=row.row_id, source_row_number=1, status="VALID", normalized_input_hash=_hash(resolved),
        resolved_input_snapshot=resolved, resolved_input_hash=_hash(resolved), field_errors=[], warnings=[],
    ))
    session.flush()

    class Registry:
        def execute_snapshot(self, *_args, **_kwargs):
            active_job = session.get(Job, job.id)
            assert active_job is not None
            active_job.status = JobStatus.CANCEL_REQUESTED.value
            session.commit()
            return SimpleNamespace(model_dump=lambda mode="json": {
                "task_type": "gc",
                "metrics": [{"key": "retention", "value": 12.5, "unit": "s"}],
                "applicability": {"warnings": [{"code": "OUT_OF_DOMAIN", "message": "do not publish after cancel"}]},
            })

    class RecordWriter:
        calls = []

        def write_batch_row_record(self, **kwargs):
            self.calls.append(kwargs)

    record_writer = RecordWriter()
    status = BatchPredictionWorker(
        session, Registry(), enabled_batch_types={"gc"}, record_writer=record_writer,
    ).execute(batch.id, actor=ctx.actor, expected_attempt=job.attempt)

    assert status is JobStatus.CANCELED
    assert row.status == "CANCELED"
    assert row.result is None
    assert row.warnings == [{"code": "PREVIOUS_ATTEMPT", "message": "previous warning"}]
    assert len(record_writer.calls) == 1
    record = record_writer.calls[0]
    assert record["session"] is session
    assert record["status"] == "CANCELED"
    assert record["warnings"] == []
    assert record["input_snapshot"]["normalized_structures"] == ["C"]


def test_batch_worker_uses_frozen_sfc_tool_and_modifier_structures() -> None:
    model_selection = {
        "mode": "exact", "model_id": "sfc-model", "model_version": "3",
        "preprocessing_profile_id": "sfc-profile", "preprocessing_profile_sha256": "b" * 64,
    }
    request = {
        "project_id": "p1", "schema_version": "1.1", "model_selection": model_selection,
        "chromatography_type": "sfc", "tool": "single_column", "structure_refs": ["a", "b"],
        "input": {
            "modifier_total_fraction": 0.2,
            "modifier_components": [{"structure_ref": "modifier-1", "fraction": 1.0}],
            "candidate_scope_id": "scope-1", "top_k": 5, "column_id": "column-1",
        },
    }
    row = SimpleNamespace(input_snapshot={
        "chromatography_type": "sfc", "template_version": "sfc-batch-2", "project_id": "p1",
        "draft_revision": 2, "validation_id": "validation-1", "request": request,
        "normalized_structures": ["C", "N"],
        "normalized_modifier_structures": {"modifier-1": "O"},
        "scope_provenance": None, "evaluation_count": 17,
    })
    batch = SimpleNamespace(
        project_id="p1", chromatography_type="sfc", template_version="sfc-batch-2",
        schema_version="1.1", model_selection=model_selection,
    )
    validation = SimpleNamespace(id="validation-1", draft_revision=2)

    frozen_request, normalized, modifiers = BatchPredictionWorker._execution_input(batch, validation, row)

    assert frozen_request.tool == "single_column"
    assert frozen_request.input.column_id == "column-1"
    assert normalized == ["C", "N"]
    assert modifiers == {"modifier-1": "O"}
    public_snapshot = BatchPredictionWorker._public_input_snapshot(frozen_request, normalized)
    assert public_snapshot["normalized_structures"] == ["C", "N"]
    assert "modifier_structures" not in public_snapshot


def test_batch_worker_rejects_reference_only_tlc_v1_execution() -> None:
    batch = SimpleNamespace(
        project_id="p1", chromatography_type="tlc", template_version="tlc-batch-1",
        schema_version="1.0", model_selection={"mode": "exact", "model_id": "m1", "model_version": "1"},
    )
    validation = SimpleNamespace(id="validation-1", draft_revision=1)
    row = SimpleNamespace(input_snapshot={
        "chromatography_type": "tlc", "template_version": "tlc-batch-1", "project_id": "p1",
        "draft_revision": 1, "validation_id": "validation-1", "structure_ref": "structure-1",
        "solvent_conditions": {"solvent_components": [
            {"solvent_id": "hexane", "fraction": 0.7},
            {"solvent_id": "ethyl_acetate", "fraction": 0.3},
        ]},
    })

    with pytest.raises(BatchWorkerError, match="BATCH_LEGACY_DATA_REQUIRES_REVALIDATION"):
        BatchPredictionWorker._execution_input(batch, validation, row)


def test_batch_worker_default_empty_type_scope_blocks_before_adapter_dispatch() -> None:
    _engine, session, ctx, batch, job = _batch_db()
    calls = []

    class Registry:
        def execute_snapshot(self, *_args, **_kwargs):
            calls.append("called")
            return {"unexpected": "success"}

    with pytest.raises(BatchWorkerError, match="BATCH_TYPE_NOT_ENABLED"):
        BatchPredictionWorker(session, Registry()).execute(
            batch.id, actor=ctx.actor, expected_attempt=job.attempt,
        )
    assert calls == []
    assert session.get(Job, job.id).status == JobStatus.QUEUED.value
    assert session.get(BatchPredictionRow, batch.id).execution_status == "QUEUED"


@pytest.mark.parametrize(
    "event_type,terminal",
    [("job.execute", False), ("job.cancel", False), ("job.execute", True), ("job.cancel", True)],
)
@pytest.mark.parametrize("denied_scope", ["type", "project"])
def test_production_claim_rejects_unselected_batch_before_any_mutation(event_type: str, terminal: bool, denied_scope: str) -> None:
    _engine, session, _ctx, batch, job = _batch_db()
    row = BatchRowResultRow(
        id="claim-row", project_id=batch.project_id, batch_id=batch.id,
        validation_id=batch.validation_id, row_id="row-claim", source_row_number=1,
        input_snapshot={}, warnings=[], attempt=1, status="QUEUED",
    )
    session.add(row)
    if terminal:
        job.status = JobStatus.SUCCEEDED.value
        batch.execution_status = JobStatus.SUCCEEDED.value
        row.status = "SUCCEEDED"
        attempt = session.scalar(select(JobAttempt).where(JobAttempt.job_id == job.id, JobAttempt.attempt == job.attempt))
        assert attempt is not None
        attempt.status = JobStatus.SUCCEEDED.value
    payload = _claim_dispatch(session, job, event_type=event_type)
    attempt = session.scalar(select(JobAttempt).where(JobAttempt.job_id == job.id, JobAttempt.attempt == job.attempt))
    assert attempt is not None
    before = (job.status, job.attempt, attempt.status, attempt.fencing_token, batch.execution_status, row.status)
    session.commit()

    class Registry:
        calls = 0

        def execute_snapshot(self, *_args, **_kwargs):
            self.calls += 1

    registry = Registry()
    worker = _async_claim_worker(session, enabled_batch_types={"tlc"} if denied_scope == "type" else {"gc"}, registry=registry)
    if denied_scope == "project":
        worker.actor = worker.actor.model_copy(update={"project_ids": ["p2"]})
    expected = "BATCH_TYPE_NOT_ENABLED" if denied_scope == "type" else "WORKER_JOB_RESOURCE_INVALID"
    with pytest.raises(BatchWorkerError, match=expected):
        asyncio.run(worker.handle(payload, event_type="job.cancel" if event_type == "job.cancel" else None))

    session.expire_all()
    stored_job = session.get(Job, job.id)
    stored_attempt = session.scalar(select(JobAttempt).where(JobAttempt.job_id == job.id, JobAttempt.attempt == 1))
    stored_batch = session.get(BatchPredictionRow, batch.id)
    stored_row = session.get(BatchRowResultRow, row.id)
    assert stored_job is not None and stored_attempt is not None and stored_batch is not None and stored_row is not None
    assert (stored_job.status, stored_job.attempt, stored_attempt.status, stored_attempt.fencing_token,
            stored_batch.execution_status, stored_row.status) == before
    assert registry.calls == 0


@pytest.mark.parametrize("resource_type", ["batch_prediction", "batch_validation", "batch_export"])
def test_production_claim_resolves_enabled_parent_without_breaking_stale_validation(resource_type: str) -> None:
    _engine, session, _ctx, batch, job = _batch_db()
    validation = session.get(BatchValidationRow, batch.validation_id)
    assert validation is not None
    if resource_type == "batch_validation":
        # Old queued validation work still reaches run_validation, which owns
        # materializing STALE when the draft has advanced.
        batch.validation_id = "newer-validation"
        batch.job_id = None
        job.resource_ref = {"type": "batch_validation", "id": validation.id}
    elif resource_type == "batch_export":
        batch.job_id = None  # Export jobs have their own job id.
        export = BatchExportRow(
            id="claim-export", project_id=batch.project_id, batch_id=batch.id,
            job_id=job.id, format="json", include=["result"], status="QUEUED",
        )
        session.add(export)
        job.resource_ref = {"type": "batch_export", "id": export.id}
    else:
        job.resource_ref = {"type": "batch_prediction", "id": batch.id}
    payload = _claim_dispatch(session, job)

    worker = _async_claim_worker(session, enabled_batch_types={"gc"})
    job_id, attempt_no, token, ref, terminal = asyncio.run(worker._claim(payload, event_type=None, lease_guard=None))

    assert (job_id, attempt_no, ref["type"], terminal) == (job.id, 1, resource_type, None)
    assert token == 1
    assert session.get(Job, job.id).status == JobStatus.RUNNING.value


@pytest.mark.parametrize("resource_type", ["batch_prediction", "batch_validation", "batch_export"])
def test_production_claim_rejects_mismatched_parent_binding_before_start(resource_type: str) -> None:
    _engine, session, _ctx, batch, job = _batch_db()
    session.add(Project(id="p2", slug="p2", name="P2"))
    validation = session.get(BatchValidationRow, batch.validation_id)
    assert validation is not None
    if resource_type == "batch_prediction":
        batch.job_id = None
        job.resource_ref = {"type": "batch_prediction", "id": batch.id}
    elif resource_type == "batch_validation":
        job.project_id = "p2"
        job.resource_ref = {"type": "batch_validation", "id": validation.id}
    else:
        export = BatchExportRow(
            id="claim-export-mismatch", project_id=batch.project_id, batch_id=batch.id,
            job_id="another-job", format="json", include=["result"], status="QUEUED",
        )
        session.add(export)
        job.resource_ref = {"type": "batch_export", "id": export.id}
    payload = _claim_dispatch(session, job)
    before_status, before_attempt = job.status, job.attempt
    attempt = session.scalar(select(JobAttempt).where(JobAttempt.job_id == job.id, JobAttempt.attempt == job.attempt))
    assert attempt is not None
    before_fence = attempt.fencing_token
    session.commit()

    worker = _async_claim_worker(session, enabled_batch_types={"gc"})
    with pytest.raises(BatchWorkerError, match="WORKER_JOB_RESOURCE_INVALID"):
        asyncio.run(worker.handle(payload))

    session.expire_all()
    stored_job = session.get(Job, job.id)
    stored_attempt = session.scalar(select(JobAttempt).where(JobAttempt.job_id == job.id, JobAttempt.attempt == 1))
    assert stored_job is not None and stored_attempt is not None
    assert (stored_job.status, stored_job.attempt, stored_attempt.status, stored_attempt.fencing_token) == (
        before_status, before_attempt, "QUEUED", before_fence,
    )


def test_production_duplicate_delivery_does_not_execute_batch_twice() -> None:
    _engine, session, _ctx, _batch, job = _batch_db()
    payload = _claim_dispatch(session, job)

    class Registry:
        calls = 0

        def execute_snapshot(self, *_args, **_kwargs):
            self.calls += 1

    registry = Registry()
    worker = _async_claim_worker(session, enabled_batch_types={"gc"}, registry=registry)
    execution_calls = 0

    async def run_sync(callback, *args):
        nonlocal execution_calls
        assert callback.__name__ == "_execute_batch"
        execution_calls += 1
        current_job = session.get(Job, job.id)
        current_attempt = session.scalar(select(JobAttempt).where(JobAttempt.job_id == job.id, JobAttempt.attempt == 1))
        assert current_job is not None and current_attempt is not None
        current_job.status = JobStatus.SUCCEEDED.value
        current_attempt.status = JobStatus.SUCCEEDED.value
        session.commit()
        return JobStatus.SUCCEEDED

    worker._run_sync = run_sync
    first = asyncio.run(worker.handle(payload))
    second = asyncio.run(worker.handle(payload))

    assert first is JobStatus.SUCCEEDED and second is JobStatus.SUCCEEDED
    assert execution_calls == 1
    assert registry.calls == 0


def test_batch_worker_counts_only_latest_retry_attempt() -> None:
    _engine, session, _ctx, batch, _job = _batch_db()
    session.add_all([
        BatchRowResultRow(id="old", project_id="p1", batch_id=batch.id, validation_id=batch.validation_id, row_id="logical-1", source_row_number=1, input_snapshot={}, status="FAILED", attempt=1, warnings=[]),
        BatchRowResultRow(id="new", project_id="p1", batch_id=batch.id, validation_id=batch.validation_id, row_id="logical-1", source_row_number=1, input_snapshot={}, status="SUCCEEDED", attempt=2, warnings=[]),
    ])
    session.flush()
    assert _counts(session, batch.id) == {"total": 1, "queued": 0, "running": 0, "succeeded": 1, "failed": 0, "skipped": 0, "canceled": 0}


def test_batch_validation_success_finalizes_shared_job_lifecycle() -> None:
    _engine, session, _ctx, batch, job = _batch_db()
    validation = BatchValidationRow(
        id="validation-1", project_id="p1", batch_id=batch.id, draft_revision=1,
        config_hash="b" * 64, status="VALID", template_version="gc-batch-1",
        model_snapshot={}, resource_estimate={},
    )
    session.add(validation)
    job.resource_ref = {"type": "batch_validation", "id": validation.id}
    attempt = session.scalar(select(JobAttempt).where(JobAttempt.job_id == job.id, JobAttempt.attempt == 1))
    attempt.fencing_token = 3
    job.status = JobStatus.RUNNING.value
    attempt.status = JobStatus.RUNNING.value
    session.flush()
    result = _finalize_job_by_ref(
        session, job.id, job.resource_ref, JobStatus.SUCCEEDED,
        expected_attempt=1, expected_fencing_token=3,
    )
    assert result is JobStatus.SUCCEEDED
    assert session.get(Job, job.id).status == JobStatus.SUCCEEDED.value
    assert session.get(BatchPredictionRow, batch.id).execution_status == JobStatus.SUCCEEDED.value
    assert session.scalar(select(JobEvent).where(JobEvent.job_id == job.id, JobEvent.type == "job.succeeded")) is not None


def test_batch_validation_invalid_is_failed_not_succeeded() -> None:
    _engine, session, _ctx, batch, job = _batch_db()
    validation = BatchValidationRow(
        id="validation-invalid", project_id="p1", batch_id=batch.id, draft_revision=1,
        config_hash="b" * 64, status="INVALID", template_version="gc-batch-1",
        model_snapshot={}, resource_estimate={},
    )
    session.add(validation)
    job.resource_ref = {"type": "batch_validation", "id": validation.id}
    attempt = session.scalar(select(JobAttempt).where(JobAttempt.job_id == job.id, JobAttempt.attempt == 1))
    attempt.fencing_token = 4
    job.status = JobStatus.RUNNING.value
    attempt.status = JobStatus.RUNNING.value
    session.flush()
    result = _finalize_job_by_ref(
        session, job.id, job.resource_ref, JobStatus.FAILED,
        expected_attempt=1, expected_fencing_token=4,
        error={"code": "BATCH_VALIDATION_INVALID", "message": "校验未通过"},
    )
    assert result is JobStatus.FAILED
    assert session.get(Job, job.id).status == JobStatus.FAILED.value
    assert session.get(BatchPredictionRow, batch.id).execution_status == JobStatus.FAILED.value
    assert session.get(BatchValidationRow, validation.id).status == "INVALID"
    assert session.scalar(select(JobEvent).where(JobEvent.job_id == job.id, JobEvent.type == "job.failed")) is not None


def _validation_handle_fixture(tmp_path):
    database = tmp_path / "batch-validation-handle.sqlite"
    engine = create_engine(f"sqlite:///{database}", connect_args={"timeout": 10})
    event.listen(engine, "connect", lambda dbapi, _: dbapi.create_function("CHAR_LENGTH", 1, lambda value: len(value) if value is not None else None))
    Base.metadata.create_all(engine)
    validation_id = "validation-handle"
    with Session(engine) as session:
        session.add(Project(id="p1", slug="p1", name="P1"))
        session.add(Artifact(
            id="artifact-validation", project_id="p1", kind="batch_input",
            media_type="text/csv", size_bytes=1, storage_key="batch/input.csv",
            status="AVAILABLE", scan_status="PASSED", created_by="actor-a",
        ))
        repo = SQLAlchemyTaskRepository(lambda: session)
        job = add_job_rows(
            repo, session, project_id="p1", owner_id="actor-a", kind="batch_prediction",
            resource_ref=ResourceRef(type="batch_validation", id=validation_id),
            cancelable=True, retryable=True, resume_mode="restart", deadline_at=None,
        )
        batch = BatchPredictionRow(
            id="batch-validation-handle", project_id="p1", actor_id="actor-a",
            chromatography_type="gc", tool="batch", name="batch", schema_version="gc-input-v1",
            model_selection={"mode": "champion", "scope_id": "scope"},
            input_artifact_id="artifact-validation", template_version="gc-batch-1",
            field_mapping={"structure_ref": "structure_ref", "temperature_program": "temperature_program"},
            common_input={}, invalid_row_policy="block",
            resource_budget={"max_runtime_seconds": 60, "max_evaluations": 10},
            revision=3, config_hash="a" * 64, etag='"batch-v3"', draft_status="VALIDATING",
            job_id=job.id, validation_id=validation_id, execution_status="QUEUED",
        )
        validation = BatchValidationRow(
            id=validation_id, project_id="p1", batch_id=batch.id, draft_revision=3,
            config_hash=batch.config_hash, status="QUEUED", template_version="gc-batch-1",
            model_snapshot={
                "id": "m1", "version": "1", "business_scope_id": "scope",
                "artifact_sha256": "a" * 64, "input_schema_version": "1",
                "output_schema_version": "1",
            },
            resource_estimate={"max_runtime_seconds": 60, "max_evaluations": 10},
        )
        session.add_all([batch, validation])
        session.commit()
        payload = _claim_dispatch(session, job)
        job_id, batch_id = job.id, batch.id
    actor = ActorContext(
        actor_id="actor-a", project_ids=["p1"], auth_method="service",
        permissions=["*"], policy_version="test",
    )
    return engine, actor, job_id, batch_id, validation_id, payload


def _validation_production_worker(engine, actor: ActorContext) -> BatchPredictionProductionWorker:
    return BatchPredictionProductionWorker(
        session_factory=lambda: _IndependentAsyncSessionContext(engine),
        sync_session_factory=lambda: Session(engine), registry=object(), actor=actor,
        artifact=object(), enabled_batch_types={"gc"},
    )


@pytest.mark.parametrize(
    ("outcome", "job_status", "validation_status", "expected_revision", "event_type"),
    [
        ("READY", JobStatus.SUCCEEDED, "READY", 3, "job.succeeded"),
        ("INVALID", JobStatus.FAILED, "INVALID", 4, "job.failed"),
    ],
)
def test_validation_handle_commits_terminal_projection_in_independent_sessions(
    tmp_path, monkeypatch, outcome, job_status, validation_status, expected_revision, event_type,
) -> None:
    engine, actor, job_id, batch_id, validation_id, payload = _validation_handle_fixture(tmp_path)

    class ValidationService:
        def __init__(self, session, **_kwargs):
            self.session = session

        def run_validation(self, _ctx, requested_id):
            assert requested_id == validation_id
            validation = self.session.get(BatchValidationRow, validation_id)
            batch = self.session.get(BatchPredictionRow, batch_id)
            assert validation is not None and batch is not None
            validation.status = outcome
            batch.draft_status = "READY" if outcome == "READY" else "INVALID"
            return SimpleNamespace(status=outcome)

    monkeypatch.setattr(batch_worker_module, "BatchPredictionService", ValidationService)
    result = asyncio.run(_validation_production_worker(engine, actor).handle(payload))
    assert result.status == outcome

    with Session(engine) as verify:
        job = verify.get(Job, job_id)
        attempt = verify.scalar(select(JobAttempt).where(JobAttempt.job_id == job_id, JobAttempt.attempt == 1))
        batch = verify.get(BatchPredictionRow, batch_id)
        validation = verify.get(BatchValidationRow, validation_id)
        event_row = verify.scalar(select(JobEvent).where(JobEvent.job_id == job_id, JobEvent.type == event_type))
        assert job is not None and attempt is not None and batch is not None and validation is not None
        assert job.status == job_status.value and attempt.status == job_status.value
        assert job.result_ref == {"type": "batch_validation", "id": validation_id}
        assert batch.execution_status == job_status.value
        assert batch.revision == expected_revision
        assert validation.status == validation_status
        assert event_row is not None
        if job_status is JobStatus.SUCCEEDED:
            assert batch.draft_status == "READY"
            assert batch.revision == validation.draft_revision
            assert batch.etag != '"batch-v3"'
        else:
            assert batch.draft_status == "INVALID"


def test_validation_cancel_and_legacy_status_read_keep_public_dto_valid(tmp_path) -> None:
    from chrom_platform.modules.prediction.batch import BatchValidation
    from chrom_platform.modules.prediction.batch_service import BatchPredictionService
    from chrom_platform.task_core.service import OperationContext

    engine, actor, job_id, batch_id, validation_id, _payload = _validation_handle_fixture(tmp_path)
    dispatch_id = f"cancel-validation-{job_id}"
    with Session(engine) as session:
        job = session.get(Job, job_id)
        attempt = session.scalar(select(JobAttempt).where(JobAttempt.job_id == job_id, JobAttempt.attempt == 1))
        batch = session.get(BatchPredictionRow, batch_id)
        validation = session.get(BatchValidationRow, validation_id)
        assert job is not None and attempt is not None and batch is not None and validation is not None
        job.status = attempt.status = JobStatus.RUNNING.value
        attempt.fencing_token = 7
        batch.execution_status = "RUNNING"
        validation.status = "RUNNING"
        cancel_payload = {"job_id": job_id, "attempt": 1, "dispatch_id": dispatch_id, "fencing_token": 7}
        session.add(OutboxEvent(
            dispatch_id=dispatch_id, aggregate_type="job", aggregate_id=job_id,
            event_type="job.cancel", payload=cancel_payload, status="PROCESSING",
        ))
        session.commit()

    canceled = asyncio.run(_validation_production_worker(engine, actor).cancel(cancel_payload))
    assert canceled is JobStatus.CANCELED
    with Session(engine) as verify:
        job = verify.get(Job, job_id)
        attempt = verify.scalar(select(JobAttempt).where(JobAttempt.job_id == job_id, JobAttempt.attempt == 1))
        batch = verify.get(BatchPredictionRow, batch_id)
        validation = verify.get(BatchValidationRow, validation_id)
        assert job is not None and attempt is not None and batch is not None and validation is not None
        assert job.status == attempt.status == batch.execution_status == "CANCELED"
        assert validation.status == "FAILED"
        assert {warning["code"] for warning in validation.warnings} == {"BATCH_VALIDATION_CANCELED"}

    context = OperationContext(actor)
    with Session(engine) as legacy:
        validation = legacy.get(BatchValidationRow, validation_id)
        assert validation is not None
        validation.status = "CANCELED"  # Existing persisted rows from the old projection.
        validation.warnings = []
        legacy.commit()
    with Session(engine) as read_session:
        stored = read_session.get(BatchValidationRow, validation_id)
        projected = BatchPredictionService(read_session, enabled_batch_types={"gc"}).validation(context, batch_id)
        assert stored is not None and stored.status == "CANCELED" and stored.warnings == []
        assert isinstance(projected, BatchValidation)
        assert projected.status == "FAILED"
        assert projected.warnings[0].code == "BATCH_VALIDATION_CANCELED"


def test_validation_finalizer_rejects_changed_fencing_token_without_terminal_writes(tmp_path, monkeypatch) -> None:
    engine, actor, job_id, batch_id, validation_id, payload = _validation_handle_fixture(tmp_path)

    class RacingValidationService:
        def __init__(self, session, **_kwargs):
            self.session = session

        def run_validation(self, _ctx, _requested_id):
            validation = self.session.get(BatchValidationRow, validation_id)
            attempt = self.session.scalar(select(JobAttempt).where(JobAttempt.job_id == job_id, JobAttempt.attempt == 1))
            assert validation is not None and attempt is not None
            validation.status = "READY"
            attempt.fencing_token = 2  # A newer lease owner wins before finalization.
            return SimpleNamespace(status="READY")

    monkeypatch.setattr(batch_worker_module, "BatchPredictionService", RacingValidationService)
    with pytest.raises(BatchWorkerError, match="BATCH_FENCING_INVALID"):
        asyncio.run(_validation_production_worker(engine, actor).handle(payload))

    with Session(engine) as verify:
        job = verify.get(Job, job_id)
        attempt = verify.scalar(select(JobAttempt).where(JobAttempt.job_id == job_id, JobAttempt.attempt == 1))
        batch = verify.get(BatchPredictionRow, batch_id)
        events = list(verify.scalars(select(JobEvent).where(
            JobEvent.job_id == job_id, JobEvent.type.in_(("job.succeeded", "job.failed")),
        )))
        assert job is not None and attempt is not None and batch is not None
        assert job.status == attempt.status == "RUNNING"
        assert attempt.fencing_token == 2
        assert job.result_ref is None
        assert batch.execution_status == "QUEUED" and batch.revision == 3
        assert events == []


def test_batch_worker_does_not_publish_result_after_lease_check_fails() -> None:
    _engine, session, ctx, batch, job = _batch_db()
    from chrom_platform.modules.prediction.schemas import ModelSnapshot
    batch.schema_version = "1.1"
    batch.model_selection = {
        "mode": "exact", "model_id": "m1", "model_version": "1",
        "preprocessing_profile_id": "gc-test-profile", "preprocessing_profile_sha256": "b" * 64,
    }
    batch.model_snapshot = ModelSnapshot(
        id="m1", version="1", business_scope_id="scope", artifact_sha256="a" * 64,
        input_schema_version="1.1", output_schema_version="1",
        preprocessing_profile_id="gc-test-profile", preprocessing_profile_sha256="b" * 64,
    ).model_dump(mode="json")
    validation = session.get(BatchValidationRow, batch.validation_id)
    assert validation is not None
    validation.model_snapshot = batch.model_snapshot
    resolved = _gc_resolved_snapshot(batch, "s1", "C")
    row = BatchRowResultRow(
        id="lease-row", project_id="p1", batch_id=batch.id, row_id="lease-row", source_row_number=1,
        input_snapshot=resolved,
        status="QUEUED", attempt=1, warnings=[], validation_id=batch.validation_id,
    )
    session.add(row)
    validation.total_rows = validation.valid_rows = 1
    validation.resolved_input_hash = _hash([{"row_id": row.row_id, "input_hash": _hash(row.input_snapshot)}])
    session.add(BatchValidationItemRow(id="item-lease-row", project_id="p1", validation_id=batch.validation_id, row_id=row.row_id, source_row_number=1, status="VALID", normalized_input_hash=_hash(row.input_snapshot), resolved_input_snapshot=row.input_snapshot, resolved_input_hash=_hash(row.input_snapshot), field_errors=[], warnings=[]))

    class Guard:
        calls = 0
        def check(self):
            self.calls += 1
            if self.calls >= 2:
                raise RuntimeError("lease expired")

    class Registry:
        def execute_snapshot(self, _kind, _request, *, model_snapshot):
            return SimpleNamespace(model_dump=lambda mode="json": {"task_type": "gc"})

    with pytest.raises(RuntimeError, match="lease expired"):
        BatchPredictionWorker(session, Registry(), enabled_batch_types={"gc"}).execute(batch.id, actor=ctx.actor, expected_attempt=job.attempt, lease_guard=Guard())
    stored = session.get(BatchRowResultRow, row.id)
    assert stored.status == "RUNNING" and stored.result is None
    _finalize_job_by_ref(
        session, job.id, {"type": "batch_prediction", "id": batch.id}, JobStatus.FAILED,
        expected_attempt=job.attempt, expected_fencing_token=None,
    )
    assert stored.status == "FAILED" and stored.result is None
    assert session.get(Job, job.id).status == JobStatus.FAILED.value


def test_0013_legacy_batch_rows_are_preserved_but_cannot_succeed_without_resolved_snapshot() -> None:
    _engine, session, ctx, batch, job = _batch_db()
    validation = session.get(BatchValidationRow, batch.validation_id)
    assert validation is not None
    # Simulate a row carried forward by 0014: the added columns are NULL and
    # pre-0014 result rows cannot be associated to a validation snapshot.
    validation.total_rows = validation.valid_rows = 1
    validation.resolved_input_hash = None
    legacy = BatchRowResultRow(
        id="legacy-0013-row", project_id="p1", batch_id=batch.id, validation_id=None,
        row_id="legacy-row", source_row_number=1,
        input_snapshot={"structure_ref": "legacy-structure"}, status="QUEUED", attempt=1, warnings=[],
    )
    session.add(legacy)
    session.flush()
    calls = []

    class Registry:
        def execute_snapshot(self, *_args, **_kwargs):
            calls.append("executed")
            return {"unexpected": "success"}

    with pytest.raises(BatchWorkerError, match="BATCH_LEGACY_DATA_REQUIRES_REVALIDATION"):
        BatchPredictionWorker(session, Registry(), enabled_batch_types={"gc"}).execute(
            batch.id, actor=ctx.actor, expected_attempt=job.attempt,
        )

    assert calls == []
    assert session.get(BatchRowResultRow, legacy.id).status == "QUEUED"
    assert session.get(BatchRowResultRow, legacy.id).input_snapshot == {"structure_ref": "legacy-structure"}
    assert session.get(Job, job.id).status == JobStatus.QUEUED.value
    assert session.get(BatchPredictionRow, batch.id).execution_status == "QUEUED"


def test_batch_cancel_marks_queued_rows_and_fences_dispatch() -> None:
    _engine, session, _ctx, batch, job = _batch_db()
    row = BatchRowResultRow(
        id="r-cancel", project_id="p1", batch_id=batch.id, row_id="row-cancel", source_row_number=1,
        input_snapshot={}, status="QUEUED", attempt=1, warnings=[], validation_id=batch.validation_id,
    )
    session.add(row)
    attempt = session.scalar(select(JobAttempt).where(JobAttempt.job_id == job.id, JobAttempt.attempt == 1))
    attempt.fencing_token = 7
    dispatch = OutboxEvent(
        dispatch_id="dispatch-cancel", aggregate_type="job", aggregate_id=job.id,
        event_type="job.cancel", payload={"job_id": job.id, "attempt": 1, "dispatch_id": "dispatch-cancel", "fencing_token": 7},
        status="PROCESSING",
    )
    session.add(dispatch)
    session.flush()
    locked = BatchPredictionProductionWorker._locked(session, job.id, 1, dispatch.dispatch_id, "job.cancel")
    status = BatchPredictionProductionWorker._cancel_locked(session, locked[0], locked[1], locked[3])
    assert status is JobStatus.CANCELED
    assert row.status == "CANCELED"
    assert job.status == JobStatus.CANCELED.value


def test_batch_cancel_payload_must_match_attempt_fencing_token() -> None:
    _engine, session, _ctx, _batch, job = _batch_db()
    attempt = session.scalar(select(JobAttempt).where(JobAttempt.job_id == job.id, JobAttempt.attempt == 1))
    attempt.fencing_token = 11
    dispatch = OutboxEvent(
        dispatch_id="dispatch-wrong-fence", aggregate_type="job", aggregate_id=job.id,
        event_type="job.cancel", payload={"job_id": job.id, "attempt": 1, "dispatch_id": "dispatch-wrong-fence", "fencing_token": 10},
        status="PROCESSING",
    )
    session.add(dispatch)
    session.flush()
    with pytest.raises(BatchWorkerError, match="WORKER_DISPATCH_FENCE_INVALID"):
        BatchPredictionProductionWorker._locked(session, job.id, 1, dispatch.dispatch_id, "job.cancel")
    with pytest.raises(BatchWorkerError, match="WORKER_DISPATCH_FENCE_INVALID"):
        BatchPredictionProductionWorker._locked(session, job.id, 1, dispatch.dispatch_id, "job.cancel", inbound_fencing_token=12)

