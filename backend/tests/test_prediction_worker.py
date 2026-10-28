from __future__ import annotations

from dataclasses import dataclass

from chrom_platform.core.errors import APIError
from chrom_platform.adapters.sfc.runtime import SfcAssetUnavailable
from chrom_platform.modules.prediction.schemas import (
    CcInput,
    ExactModelSelection,
    ModelSnapshot,
    PredictionInputSnapshot,
    SfcSingleColumnExecutionV10,
    TlcInput,
)
from chrom_platform.modules.prediction.worker import PredictionWorker, _request_from_snapshot
from chrom_platform.task_core.state import JobStatus
from test_prediction_service import sfc_request


@dataclass
class Work:
    prediction_id: str
    project_id: str
    job_id: str
    attempt: int
    status: JobStatus
    type_id: str
    tool: str
    schema_version: str
    model_snapshot: ModelSnapshot
    input_snapshot: PredictionInputSnapshot
    request: object | None = None
    dispatch_id: str | None = None


def _work(status=JobStatus.QUEUED):
    return Work(
        prediction_id="prediction-a", project_id="project-a", job_id="job-a", attempt=1,
        status=status, type_id="tlc", tool="single", schema_version="1.0",
        model_snapshot=ModelSnapshot(
            id="model-a", version="1", business_scope_id="scope-a", artifact_sha256="a" * 64,
            input_schema_version="1.0", output_schema_version="1.0",
        ),
        input_snapshot=PredictionInputSnapshot(
            structure_refs=["structure-a"], normalized_structures=["C"],
            input=TlcInput(solvent_components=[{"solvent_id": "hexane", "fraction": 0.5}, {"solvent_id": "ethyl_acetate", "fraction": 0.5}]),
        ),
    )


class Store:
    def __init__(self, item):
        self.item = item
        self.events = []

    def load_execution(self, _job_id):
        return self.item

    def mark_running(self, item):
        self.events.append("running")
        return True

    def mark_succeeded(self, item, result):
        self.events.append(("succeeded", result))
        item.status = JobStatus.SUCCEEDED

    def mark_failed(self, item, error):
        self.events.append(("failed", error))
        item.status = JobStatus.FAILED


class Registry:
    def __init__(self, *, error=None):
        self.error = error
        self.execute_calls = []
        self.resolve_calls = 0

    def resolve_model(self, *_args, **_kwargs):
        self.resolve_calls += 1
        raise AssertionError("worker resolved Champion")

    def execute_snapshot(self, type_id, request, *, model_snapshot):
        if self.error:
            raise self.error
        self.execute_calls.append((type_id, request, model_snapshot))
        return {"task_type": "tlc"}


def test_worker_uses_persisted_snapshot_and_never_resolves_champion() -> None:
    item, registry = _work(), Registry()
    store = Store(item)
    result = PredictionWorker(registry=registry, store=store).handle("job-a")
    assert result.status is JobStatus.SUCCEEDED
    assert registry.resolve_calls == 0
    assert len(registry.execute_calls) == 1
    _, request, snapshot = registry.execute_calls[0]
    assert snapshot == item.model_snapshot
    assert request.model_selection.mode == "exact"
    assert request.model_selection.model_id == "model-a"
    assert request.input == item.input_snapshot.input


def test_terminal_redelivery_is_idempotent_and_does_not_execute_again() -> None:
    item, registry = _work(JobStatus.SUCCEEDED), Registry()
    store = Store(item)
    result = PredictionWorker(registry=registry, store=store).handle("job-a")
    assert result.replayed is True
    assert registry.execute_calls == [] and store.events == []


def test_worker_persists_safe_public_error_without_exception_details() -> None:
    item, registry = _work(), Registry(error=RuntimeError("SECRET_WEIGHT_PATH"))
    store = Store(item)
    result = PredictionWorker(registry=registry, store=store).handle("job-a")
    assert result.status is JobStatus.FAILED
    assert result.error is not None
    assert result.error.code == "PREDICTION_EXECUTION_FAILED"
    assert "SECRET_WEIGHT_PATH" not in result.error.message
    assert store.events[0] == "running"
    assert store.events[1][0] == "failed"


def test_stale_attempt_redelivery_does_not_execute() -> None:
    item, registry = _work(), Registry()
    store = Store(item)
    result = PredictionWorker(registry=registry, store=store).handle("job-a", attempt=2)
    assert result.replayed is True
    assert result.status is JobStatus.QUEUED
    assert registry.execute_calls == [] and store.events == []


def test_stale_dispatch_redelivery_does_not_execute() -> None:
    item, registry = _work(), Registry()
    item.dispatch_id = "dispatch-current"
    store = Store(item)
    result = PredictionWorker(registry=registry, store=store).handle(
        "job-a", dispatch_id="dispatch-old"
    )
    assert result.replayed is True
    assert registry.execute_calls == [] and store.events == []


def test_worker_normalizes_persisted_json_snapshots_before_adapter_call() -> None:
    item, registry = _work(), Registry()
    item.model_snapshot = item.model_snapshot.model_dump(mode="json")
    item.input_snapshot = item.input_snapshot.model_dump(mode="json")
    store = Store(item)
    result = PredictionWorker(registry=registry, store=store).handle("job-a")
    assert result.status is JobStatus.SUCCEEDED
    _, request, model = registry.execute_calls[0]
    assert model.id == "model-a"
    assert request.model_selection.mode == "exact"


def test_worker_rehydrates_cc_preprocessing_profile_from_immutable_snapshot() -> None:
    profile_id = "cc-qgeognn-4g-canonical-v1"
    profile_sha256 = "b" * 64
    item = _work()
    item.type_id = "cc"
    item.model_snapshot = ModelSnapshot(
        id="qgeognn_silica_4g", version="cc-qgeognn-1", business_scope_id="cc-default",
        artifact_sha256="a" * 64, input_schema_version="1.0", output_schema_version="cc-qgeognn-1",
        preprocessing_profile_id=profile_id, preprocessing_profile_sha256=profile_sha256,
    )
    item.input_snapshot = PredictionInputSnapshot(
        structure_refs=["structure-a", "structure-b"], normalized_structures=["CC", "CCC"],
        input=CcInput(
            eluent_system_id="cc4g-pe-ea-000-100-v1", loading_solvent_id="cc4g-loading-dcm-v1",
            sample_mass_mg=100.0, loading_solvent_volume_ul=300.0, flow_rate_ml_min=10.0,
            column_spec_id="silica-4g",
        ),
    )
    store, registry = Store(item), Registry()

    result = PredictionWorker(registry=registry, store=store).handle("job-a")

    assert result.status is JobStatus.SUCCEEDED
    _, request, snapshot = registry.execute_calls[0]
    assert snapshot.preprocessing_profile_id == profile_id
    assert snapshot.preprocessing_profile_sha256 == profile_sha256
    assert request.model_selection.preprocessing_profile_id == profile_id
    assert request.model_selection.preprocessing_profile_sha256 == profile_sha256


def test_worker_old_sfc_snapshot_fails_closed_and_redelivery_is_idempotent() -> None:
    request = sfc_request()
    item = _work()
    item.type_id = "sfc"
    item.tool = "single_column"
    item.input_snapshot = PredictionInputSnapshot(
        structure_refs=request.structure_refs,
        normalized_structures=["C[C@H](O)F", "C[C@@H](O)F"],
        input=request.input,
    )

    class SfcRegistry(Registry):
        def execute_snapshot(
            self, type_id, request, *, model_snapshot, normalized_structures,
            normalized_modifier_structures,
        ):
            self.execute_calls.append((type_id, request, normalized_modifier_structures))
            assert normalized_structures == ["C[C@H](O)F", "C[C@@H](O)F"]
            assert normalized_modifier_structures == {}
            raise SfcAssetUnavailable("SFC_INPUT_SNAPSHOT_MISSING")

    registry = SfcRegistry()
    store = Store(item)
    first = PredictionWorker(registry=registry, store=store).handle("job-a")
    assert first.status is JobStatus.FAILED
    assert first.error is not None and first.error.code == "PREDICTION_EXECUTION_FAILED"
    assert len(registry.execute_calls) == 1
    second = PredictionWorker(registry=registry, store=store).handle("job-a")
    assert second.replayed is True
    assert len(registry.execute_calls) == 1


def test_normal_worker_lifts_accepted_sfc_v10_champion_using_only_frozen_snapshot() -> None:
    request = sfc_request()
    stored = request.model_dump(mode="python")
    stored["schema_version"] = "1.0"
    stored["model_selection"] = {"mode": "champion", "scope_id": "scope-a"}
    item = _work()
    item.type_id = "sfc"
    item.tool = "single_column"
    item.schema_version = "1.0"
    item.request = stored
    item.model_snapshot = ModelSnapshot(
        id="sfc-model", version="1", business_scope_id="scope-a", artifact_sha256="a" * 64,
        input_schema_version="1.0", output_schema_version="1.0",
    )
    item.input_snapshot = PredictionInputSnapshot(
        structure_refs=request.structure_refs, normalized_structures=["C", "N"], input=request.input,
    )
    lifted = _request_from_snapshot(item)
    assert isinstance(lifted, SfcSingleColumnExecutionV10)
    assert isinstance(lifted.model_selection, ExactModelSelection)
    assert lifted.model_selection.model_id == item.model_snapshot.id
    assert stored["model_selection"]["mode"] == "champion"
