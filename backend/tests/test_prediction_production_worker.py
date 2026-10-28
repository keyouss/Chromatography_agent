from __future__ import annotations

from dataclasses import dataclass
import asyncio
from types import SimpleNamespace
import pytest

from chrom_platform.modules.prediction.production_worker import (
    AsyncPredictionExecutionStore,
    PredictionProductionWorker,
    PredictionWorkerError,
    PredictionWorkerMessage,
    ProductionWorkerResult,
    _Claim,
)
from chrom_platform.infrastructure.models import Job, JobAttempt, JobEvent
from chrom_platform.modules.data_center.persistence import PredictionRecordRow
from chrom_platform.modules.prediction.persistence import PredictionExecutionSnapshot, PredictionRow, _etag
from chrom_platform.modules.prediction.schemas import (
    Applicability,
    CcPredictionCreate,
    ModelSnapshot,
    PredictionInputSnapshot,
    PublicErrorSummary,
    SfcSingleColumnExecutionV10,
    SfcSingleColumnPredictionCreate,
    TlcConditionComparisonResult,
    TlcPredictionCreate,
    TlcPredictionResult,
    TlcResultRow,
    TlcReverseResult,
    Warning,
)
from chrom_platform.task_core.state import JobStatus


def _request(kind: str = "sfc"):
    if kind == "cc":
        return CcPredictionCreate.model_validate({
            "project_id": "p1", "chromatography_type": "cc", "tool": "single", "schema_version": "1.0",
            "model_selection": {"mode": "exact", "model_id": "m1", "model_version": "1"},
            "structure_refs": ["s1", "s2"],
            "input": {"eluent_system_id": "pe-ea", "loading_solvent_id": "DCM", "sample_mass_mg": 1.0, "loading_solvent_volume_ul": 10.0, "flow_rate_ml_min": 2.0, "column_spec_id": "4g"},
        })
    if kind == "tlc":
        return TlcPredictionCreate.model_validate({
            "project_id": "p1", "chromatography_type": "tlc", "tool": "single", "schema_version": "1.0",
            "model_selection": {"mode": "exact", "model_id": "m1", "model_version": "1"},
            "structure_refs": ["s1"],
            "input": {"solvent_components": [
                {"solvent_id": "hexane", "fraction": 0.8},
                {"solvent_id": "ethyl_acetate", "fraction": 0.2},
            ]},
        })
    return SfcSingleColumnPredictionCreate.model_validate({
        "project_id": "p1", "chromatography_type": "sfc", "tool": "single_column", "schema_version": "1.1",
        "model_selection": {"mode": "exact", "model_id": "m1", "model_version": "1"},
        "structure_refs": ["s1", "s2"],
        "input": {"modifier_total_fraction": 0.2, "modifier_components": [{"structure_ref": "methanol", "fraction": 1.0}], "candidate_scope_id": "scope", "top_k": 1, "column_id": "c1"},
    })


def _snapshot(kind: str = "sfc") -> PredictionExecutionSnapshot:
    request = _request("sfc")
    schema_version = request.schema_version
    return PredictionExecutionSnapshot(
        prediction_id="pred1", project_id="p1", job_id="job1", attempt=1, status=JobStatus.RUNNING,
        type_id=kind, tool="single_column", schema_version=schema_version, request=request,
        model_snapshot=ModelSnapshot(id="m1", version="1", business_scope_id="scope", artifact_sha256="a" * 64, input_schema_version=schema_version, output_schema_version="1.0"),
        input_snapshot=PredictionInputSnapshot(structure_refs=["s1", "s2"], normalized_structures=["C", "N"], input=request.input),
        dispatch_id="dispatch1", execution_token="exec1", fencing_token=1,
    )


def test_production_worker_lifts_accepted_sfc_v10_snapshot_without_rewriting_row() -> None:
    raw_request = {
        "project_id": "p1", "chromatography_type": "sfc", "tool": "single_column", "schema_version": "1.0",
        "model_selection": {"mode": "champion", "scope_id": "scope"},
        "structure_refs": ["s1", "s2"],
        "input": {"modifier_total_fraction": 0.2, "modifier_components": [{"structure_ref": "methanol", "fraction": 1.0}], "candidate_scope_id": "scope", "top_k": 1, "column_id": "c1"},
    }
    original = repr(raw_request)
    row = SimpleNamespace(
        id="pred1", project_id="p1", job_id="job1", chromatography_type="sfc", tool="single_column",
        schema_version="1.0", request_snapshot=raw_request,
        model_snapshot={"id": "m1", "version": "1", "business_scope_id": "scope", "artifact_sha256": "a" * 64, "input_schema_version": "1.0", "output_schema_version": "1.0"},
        input_snapshot={"structure_refs": ["s1", "s2"], "normalized_structures": ["C", "N"], "input": raw_request["input"], "modifier_structures": []},
    )
    snapshot = AsyncPredictionExecutionStore._snapshot(
        SimpleNamespace(id="job1", attempt=1, status=JobStatus.RUNNING),
        row,
        SimpleNamespace(dispatch_id="dispatch1"),
        SimpleNamespace(execution_token="exec1", fencing_token=1, lease_until=None),
    )
    assert isinstance(snapshot.request, SfcSingleColumnExecutionV10)
    assert snapshot.request.model_selection.model_id == "m1"
    assert raw_request["model_selection"]["mode"] == "champion"
    assert repr(raw_request) == original


@dataclass
class Store:
    snapshot: PredictionExecutionSnapshot
    event_type: str = "job.execute"
    finished: list[tuple[object | None, PublicErrorSummary | None]] | None = None
    canceled: bool = False

    async def claim(self, _message):
        return _Claim(self.snapshot, self.event_type)

    async def finish(self, snapshot, *, result, error):
        assert snapshot is self.snapshot
        if self.finished is None:
            self.finished = []
        self.finished.append((result, error))
        return True

    async def cancel(self, _message):
        self.canceled = True
        return ProductionWorkerResult(status=JobStatus.CANCELED)


class Registry:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def execute_snapshot(self, type_id, request, *, model_snapshot):
        self.calls.append((type_id, request, model_snapshot))
        if self.error:
            raise self.error
        return {"task_type": type_id}


def test_execute_routes_sfc_and_persists_result():
    store, registry = Store(_snapshot()), Registry()
    result = asyncio.run(PredictionProductionWorker(registry=registry, store=store).handle({"job_id": "job1", "attempt": 1, "dispatch_id": "dispatch1"}))
    assert result.status is JobStatus.SUCCEEDED
    assert registry.calls[0][0] == "sfc"
    assert store.finished[0][0] == {"task_type": "sfc"}


def test_execute_routes_cc_without_rewriting_snapshot():
    request = _request("cc")
    snapshot = _snapshot("cc")
    snapshot = PredictionExecutionSnapshot(**{**snapshot.__dict__, "request": request, "input_snapshot": PredictionInputSnapshot(structure_refs=["s1", "s2"], normalized_structures=["C[C]", "N"], input=request.input)})
    store, registry = Store(snapshot), Registry()
    result = asyncio.run(PredictionProductionWorker(registry=registry, store=store).execute({"job_id": "job1", "attempt": 1, "dispatch_id": "dispatch1"}))
    assert result.status is JobStatus.SUCCEEDED
    assert registry.calls[0][0] == "cc"
    assert registry.calls[0][1].input.flow_rate_ml_min == 2.0
    assert snapshot.input_snapshot.normalized_structures == ["C[C]", "N"]


def test_execute_routes_tlc_to_the_verified_adapter():
    request = _request("tlc")
    snapshot = _snapshot("tlc")
    snapshot = PredictionExecutionSnapshot(**{
        **snapshot.__dict__, "request": request, "tool": request.tool,
        "input_snapshot": PredictionInputSnapshot(
            structure_refs=["s1"], normalized_structures=["CCO"], input=request.input
        ),
    })
    store, registry = Store(snapshot), Registry()
    result = asyncio.run(PredictionProductionWorker(registry=registry, store=store).execute(
        {"job_id": "job1", "attempt": 1, "dispatch_id": "dispatch1"}
    ))
    assert result.status is JobStatus.SUCCEEDED
    assert registry.calls[0][0] == "tlc"


def test_missing_asset_fails_closed_without_result():
    store, registry = Store(_snapshot()), Registry(RuntimeError("SECRET_MODEL_PATH"))
    result = asyncio.run(PredictionProductionWorker(registry=registry, store=store).handle(PredictionWorkerMessage("job1", 1, "dispatch1")))
    assert result.status is JobStatus.FAILED
    assert result.error.code == "PREDICTION_EXECUTION_FAILED"
    assert store.finished[0][0] is None
    assert "SECRET_MODEL_PATH" not in result.error.message


@pytest.mark.parametrize("kind", ["hplc", "gc"])
def test_execute_routes_registered_optional_types_to_registry(kind):
    store, registry = Store(_snapshot(kind)), Registry()
    result = asyncio.run(PredictionProductionWorker(registry=registry, store=store).handle(PredictionWorkerMessage("job1", 1, "dispatch1")))
    assert result.status is JobStatus.SUCCEEDED
    assert registry.calls[0][0] == kind


def test_cancel_uses_cancel_port_and_does_not_execute():
    store, registry = Store(_snapshot(), event_type="job.cancel"), Registry()
    result = asyncio.run(PredictionProductionWorker(registry=registry, store=store).handle(PredictionWorkerMessage("job1", 1, "dispatch1")))
    assert result.status is JobStatus.CANCELED
    assert store.canceled and registry.calls == []


def test_three_field_payload_is_strictly_fenced():
    store, registry = Store(_snapshot()), Registry()
    with __import__("pytest").raises(PredictionWorkerError, match="INVALID_WORKER_PAYLOAD"):
        asyncio.run(PredictionProductionWorker(registry=registry, store=store).execute({"job_id": "job1", "attempt": 1, "dispatch_id": "dispatch1", "type_id": "sfc"}))


def test_queued_and_running_cancel_are_separate_stable_paths():
    class CancelStore(Store):
        def __init__(self, status):
            super().__init__(_snapshot(), event_type="job.cancel")
            self.status = status

        async def claim(self, _message):
            return _Claim(None, "job.cancel", status=self.status)

        async def cancel(self, _message):
            self.canceled = True
            return ProductionWorkerResult(status=JobStatus.CANCELED if self.status is JobStatus.QUEUED else JobStatus.CANCEL_REQUESTED)

    for status, expected in ((JobStatus.QUEUED, JobStatus.CANCELED), (JobStatus.RUNNING, JobStatus.CANCEL_REQUESTED)):
        store, registry = CancelStore(status), Registry()
        result = asyncio.run(PredictionProductionWorker(registry=registry, store=store).cancel({"job_id": "job1", "attempt": 1, "dispatch_id": "dispatch1"}))
        assert result.status is expected and store.canceled and registry.calls == []


def test_late_success_is_discarded_after_cancel_request():
    class CancelRaceStore(Store):
        async def finish(self, snapshot, *, result, error):
            self.finished = [(None, None)]
            return JobStatus.CANCELED

    store, registry = CancelRaceStore(_snapshot()), Registry()
    result = asyncio.run(PredictionProductionWorker(registry=registry, store=store).execute({"job_id": "job1", "attempt": 1, "dispatch_id": "dispatch1"}))
    assert result.status is JobStatus.CANCELED and result.replayed is True
    assert store.finished == [(None, None)]


def test_old_retry_attempt_is_replayed_without_execution():
    class OldAttemptStore(Store):
        async def claim(self, _message):
            return _Claim(None, "job.execute", replayed=True, status=JobStatus.QUEUED)

    store, registry = OldAttemptStore(_snapshot()), Registry()
    result = asyncio.run(PredictionProductionWorker(registry=registry, store=store).retry({"job_id": "job1", "attempt": 1, "dispatch_id": "old-dispatch"}))
    assert result.replayed is True and result.status is JobStatus.QUEUED and registry.calls == []


class _QueryResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def first(self):
        return None


class _FinishSession:
    def __init__(self, rows):
        self.rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def begin(self):
        return self

    async def execute(self, statement):
        entity = statement.column_descriptions[0]["entity"]
        if entity is JobEvent.seq:
            return _QueryResult(None)
        return _QueryResult(self.rows.get(entity))

    def add(self, _value):
        pass


def _warning_projection_rows(*, job_status="RUNNING", attempt_status="RUNNING"):
    return {
        Job: SimpleNamespace(
            id="job1", project_id="p1", attempt=1, status=job_status,
            progress={"completed": 0, "total": 1, "unit": "prediction", "stage": "queued"},
            result_ref=None, error=None, finished_at=None, updated_at=None, revision=1, etag="",
        ),
        JobAttempt: SimpleNamespace(
            job_id="job1", attempt=1, status=attempt_status, execution_token="exec1",
            fencing_token=1, lease_until=None, result_ref=None, error=None, finished_at=None,
        ),
        PredictionRow: SimpleNamespace(
            id="pred1", prediction_record_id="record1", status="RUNNING", result=None,
            warnings=[{"code": "EXISTING", "message": "already present", "field": None}],
            error=None, completed_at=None, updated_at=None, revision=1, etag="",
        ),
        PredictionRecordRow: SimpleNamespace(
            id="record1", status="RUNNING", result_ref=None,
            warnings=["EXISTING: already present"], revision=1, etag="",
        ),
    }


def _comparison_result_with_warnings():
    return TlcConditionComparisonResult.model_validate({
        "task_type": "tlc_condition_comparison", "schema_version": "1.0",
        "structure_ref": "structure-a", "model_id": "tlc-model", "model_version": "1.1",
        "artifact_sha256": "a" * 64, "results": [],
        "warnings": [
            "EXPERIMENTAL_CONFIRMATION_REQUIRED: initial condition suggestions require experimental confirmation",
            "NO_INDEPENDENT_GENERALIZATION_EVIDENCE: no independent labeled-set evidence is available",
            "EXPERIMENTAL_CONFIRMATION_REQUIRED: initial condition suggestions require experimental confirmation",
        ],
    })


def _reverse_result_with_warnings():
    return TlcReverseResult.model_validate({
        "task_type": "tlc_reverse_solvent", "schema_version": "1.0",
        "structure_ref": "structure-a", "model_id": "tlc-model", "model_version": "1.1",
        "artifact_sha256": "a" * 64, "candidates": [], "evaluated": 2, "failed": 1,
        "target_reachable": False,
        "warnings": [
            "EXPERIMENTAL_CONFIRMATION_REQUIRED: initial condition suggestions require experimental confirmation",
            "NO_INDEPENDENT_GENERALIZATION_EVIDENCE: no independent labeled-set evidence is available",
            "PARTIAL_FAILURE: one or more real model evaluations failed",
            "TARGET_UNREACHABLE: no directly predicted solvent point reaches target Rf",
        ],
    })


def _single_result_with_warning():
    return TlcPredictionResult.model_validate({
        "task_type": "tlc", "schema_version": "1.0", "model_id": "tlc-model",
        "model_version": "1.1", "artifact_sha256": "a" * 64,
        "ensemble_weights": {"ensemble": 1.0},
        "mobile_phase_original": {"hexane": 0.8, "ethyl_acetate": 0.2},
        "mobile_phase_normalized": {"hexane": 0.8, "ethyl_acetate": 0.2},
        "metrics": [], "rows":[TlcResultRow(rf=0.5)],
        "applicability": Applicability(
            supported_scope="validated binary systems",
            warnings=[Warning(
                code="EXPERIMENTAL_CONFIRMATION_REQUIRED",
                message="initial condition suggestions require experimental confirmation",
                field="input.solvent_components",
            )],
        ),
    })


@pytest.mark.parametrize(
    ("result", "expected_codes"),
    [
        (_comparison_result_with_warnings(), {"EXPERIMENTAL_CONFIRMATION_REQUIRED", "NO_INDEPENDENT_GENERALIZATION_EVIDENCE"}),
        (_reverse_result_with_warnings(), {
            "EXPERIMENTAL_CONFIRMATION_REQUIRED", "NO_INDEPENDENT_GENERALIZATION_EVIDENCE",
            "PARTIAL_FAILURE", "TARGET_UNREACHABLE",
        }),
        (_single_result_with_warning(), {"EXPERIMENTAL_CONFIRMATION_REQUIRED"}),
    ],
)
def test_successful_finish_projects_result_warnings_to_prediction_and_record(result, expected_codes):
    rows = _warning_projection_rows()
    store = AsyncPredictionExecutionStore(lambda: _FinishSession(rows))

    assert asyncio.run(store.finish(_snapshot("tlc"), result=result, error=None)) is True

    prediction_warnings = rows[PredictionRow].warnings
    assert prediction_warnings[0] == {"code": "EXISTING", "message": "already present"}
    assert {warning["code"] for warning in prediction_warnings[1:]} == expected_codes
    assert len(prediction_warnings) == len(expected_codes) + 1
    record_warnings = rows[PredictionRecordRow].warnings
    assert record_warnings[0] == "EXISTING: already present"
    assert {warning.split(":", 1)[0] for warning in record_warnings[1:]} == expected_codes
    assert len(record_warnings) == len(expected_codes) + 1
    if isinstance(result, TlcPredictionResult):
        assert prediction_warnings[1]["field"] == "input.solvent_components"


@pytest.mark.parametrize(
    ("job_status", "attempt_status", "error", "expected_status"),
    [
        ("CANCEL_REQUESTED", "CANCEL_REQUESTED", None, JobStatus.CANCELED),
        ("RUNNING", "RUNNING", PublicErrorSummary(code="FAILED", message="safe failure"), True),
    ],
)
def test_failed_or_canceled_finish_does_not_publish_result_warnings(
    job_status, attempt_status, error, expected_status,
):
    rows = _warning_projection_rows(job_status=job_status, attempt_status=attempt_status)
    store = AsyncPredictionExecutionStore(lambda: _FinishSession(rows))
    original_prediction_warnings = list(rows[PredictionRow].warnings)
    original_record_warnings = list(rows[PredictionRecordRow].warnings)

    outcome = asyncio.run(store.finish(_snapshot("tlc"), result=_reverse_result_with_warnings(), error=error))

    assert outcome is expected_status
    assert rows[PredictionRow].status == ("CANCELED" if expected_status is JobStatus.CANCELED else "FAILED")
    assert rows[PredictionRow].warnings == original_prediction_warnings
    assert rows[PredictionRecordRow].warnings == original_record_warnings
