from __future__ import annotations

from datetime import datetime, timezone
import csv
import io
from types import SimpleNamespace

import pytest
from pydantic import TypeAdapter
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from chrom_platform.artifact_core.models import Artifact, Structure
from chrom_platform.core.dto import ActorContext, CapabilityHealth, ResourceRef
from chrom_platform.infrastructure.models import Base, Job, Project
from chrom_platform.modules.prediction.batch import BatchCancel, BatchDraftCreate, BatchRetryPlanCreate, BatchSubmit
from chrom_platform.modules.prediction.batch_service import (
    BatchPredictionRow,
    BatchPredictionService,
    BatchRetryPlanRow,
    BatchRowResultRow,
    BatchServiceError,
    BatchValidationItemRow,
    BatchValidationRow,
    MAX_BATCH_ROWS,
    _parse_csv,
    default_batch_template_registry,
)
from chrom_platform.modules.prediction.adapters import PredictionLimits, StructureCount, ToolCapability
from chrom_platform.modules.prediction.ports import AtomicPredictionCreateResult, IdempotencyLookup, StructureSnapshot
from chrom_platform.modules.prediction.schemas import (
    CcPredictionCreate,
    ModelSnapshot,
    PredictionAccepted,
    PredictionExecutionInputSnapshot,
)
from chrom_platform.modules.prediction.service import PredictionCreateService, PredictionPreparation
from chrom_platform.task_core.service import OperationContext
from chrom_platform.task_core.state import JobStatus


PROFILE_ID = "cc-qgeognn-4g-canonical-v1"
PROFILE_SHA256 = "b" * 64


class _Access:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def ensure_project_access(self, _ctx, _project_id, *, permission: str) -> None:
        self.events.append(f"access:{permission}")


class _Structures:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def load_many(self, _ctx, project_id, structure_refs):
        self.events.append("structures")
        return [
            StructureSnapshot(ref, project_id, normalized)
            for ref, normalized in zip(structure_refs, ("C[C@H](O)C", "C[C@@H](O)C"))
        ]


class _Capabilities:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.tool = ToolCapability(
            id="single", schema_version="1.0", input_schema={"type": "object"},
            output_schema={"type": "object"},
            structure_count=StructureCount(min=2, max=2, semantics="pair"),
            model_constraints={}, units={}, supported_visualizations=[],
            limits=PredictionLimits(max_rows=10, max_candidates=10, max_evaluations=10),
            health=CapabilityHealth(status="READY", checked_at=datetime.now(timezone.utc)),
        )

    def get_tool(self, _type, tool_id, schema_version):
        self.events.append("tool")
        assert (tool_id, schema_version) == (self.tool.id, self.tool.schema_version)
        return self.tool

    def validate_request(self, _type, request, *, model_snapshot):
        self.events.append("preflight")
        return request


class _Models:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.snapshot = ModelSnapshot(
            id="qgeognn_silica_4g", version="cc-qgeognn-1", business_scope_id="cc-default",
            artifact_sha256="a" * 64, input_schema_version="1.0", output_schema_version="1.0",
            preprocessing_profile_id=PROFILE_ID, preprocessing_profile_sha256=PROFILE_SHA256,
        )

    def resolve_model(self, _ctx, _request, *, tool):
        self.events.append("model")
        return self.snapshot


class _Idempotency:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def lookup(self, _ctx, _project_id, *, key, payload_hash):
        self.events.append("idempotency")
        return None


class _Atomic:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def create(self, *_args, **_kwargs):
        self.events.append("atomic")
        accepted = PredictionAccepted(
            id="prediction-a", prediction_record_id="record-a", job_id="job-a",
            status=JobStatus.QUEUED, resource_ref=ResourceRef(type="prediction", id="prediction-a"),
        )
        return AtomicPredictionCreateResult(accepted)


def _ctx() -> OperationContext:
    return OperationContext(ActorContext(
        actor_id="actor-a", project_ids=["project-a"], auth_method="service",
        permissions=["prediction:create"], policy_version="p1",
    ))


def _cc_request() -> CcPredictionCreate:
    return CcPredictionCreate.model_validate({
        "project_id": "project-a", "chromatography_type": "cc", "tool": "single",
        "schema_version": "1.0",
        "model_selection": {
            "mode": "exact", "model_id": "qgeognn_silica_4g", "model_version": "cc-qgeognn-1",
            "preprocessing_profile_id": PROFILE_ID, "preprocessing_profile_sha256": PROFILE_SHA256,
        },
        "structure_refs": ["structure-a", "structure-b"],
        "input": {
            "eluent_system_id": "system-a", "loading_solvent_id": "solvent-a",
            "sample_mass_mg": 1.0, "loading_solvent_volume_ul": 10.0,
            "flow_rate_ml_min": 1.0, "column_spec_id": "column-a",
        },
    })


def test_prepare_freezes_inputs_without_idempotency_or_atomic_ports() -> None:
    events: list[str] = []
    service = PredictionCreateService(
        access=_Access(events), structures=_Structures(events), capabilities=_Capabilities(events),
        models=_Models(events), idempotency=None, atomic=None,
    )

    prepared = service.prepare(_ctx(), _cc_request())

    assert prepared.normalized_structures == ["C[C@H](O)C", "C[C@@H](O)C"]
    assert prepared.model_snapshot.id == "qgeognn_silica_4g"
    assert events == ["access:prediction:create", "tool", "model", "preflight", "structures"]


def test_create_keeps_access_and_idempotency_before_atomic_creation() -> None:
    events: list[str] = []
    request = _cc_request()
    service = PredictionCreateService(
        access=_Access(events), structures=_Structures(events), capabilities=_Capabilities(events),
        models=_Models(events), idempotency=_Idempotency(events), atomic=_Atomic(events),
    )

    service.create(_ctx(), request, idempotency_key="cc-batch-order")

    assert events == [
        "access:prediction:create", "idempotency", "tool", "model",
        "preflight", "structures", "atomic",
    ]


class _BatchArtifact:
    def __init__(self, session: Session, content: bytes) -> None:
        self.session = session
        self.content = content

    def get_artifact(self, artifact_id, _actor):
        return self.session.get(Artifact, artifact_id)

    def read_batch(self, _artifact_id, _actor):
        return self.content


class _BatchPreparation:
    def __init__(self, snapshot: ModelSnapshot) -> None:
        self.model_snapshot = snapshot
        self.requests = []

    def prepare(self, _ctx, request):
        self.requests.append(request)
        refs = list(request.structure_refs)
        modifiers = [
            {"structure_ref": item.structure_ref, "normalized_smiles": "CO"}
            for item in getattr(request.input, "modifier_components", [])
        ]
        return PredictionPreparation(
            model_snapshot=self.model_snapshot,
            input_snapshot=PredictionExecutionInputSnapshot(
                structure_refs=refs,
                normalized_structures=[f"normalized:{ref}" for ref in refs],
                modifier_structures=modifiers,
                input=request.input,
            ),
        )


class _BatchConfirmation:
    def consume_for_mutation(self, *_args, **_kwargs):
        return None


class _BatchCapabilities:
    def __init__(self, chromatography_type: str, model: object) -> None:
        self.value = SimpleNamespace(
            health=SimpleNamespace(status="READY"), models=[model],
        )
        self.chromatography_type = chromatography_type
        candidates = tuple(
            SimpleNamespace(
                column=f"column-{index}", stationary_phase="silica", mode="normal",
                observed_in_source=True,
            )
            for index in (1, 2)
        )
        catalog = SimpleNamespace(resolve_with_policy=lambda *_args, **_kwargs: {
            "candidates": candidates, "policy": None, "candidate_count": 2,
            "observed_count": 2, "unobserved_count": 0,
        })
        runtime = SimpleNamespace(allowed_tool="full_column")
        binding = SimpleNamespace(candidate_catalog=catalog, runtime=runtime)
        adapter = SimpleNamespace(binding_for=lambda *_args, **_kwargs: binding)
        self._adapter = adapter

    def capability(self, _type):
        return self.value

    def adapter(self, _type):
        return self._adapter


def _batch_csv(headers: list[str], rows: list[list[object]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(headers)
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def _non_tlc_validation(chromatography_type: str, *, policy: str = "block", rows=None, max_evaluations=None):
    if chromatography_type == "hplc":
        profile_id, profile_sha = "hplc-profile-v1", "c" * 64
        schema_version, template_version = "1.1", "hplc-batch-2"
        headers = ["smiles_a", "smiles_b", "isopropanol_fraction", "flow_rate_ml_min", "column_ids"]
        rows = rows if rows is not None else [["C[C@H](O)F", "C[C@@H](O)F", 0.4, 1.0, '["column-a","column-b"]']]
        fields = headers
        eval_budget = 10
    else:
        profile_id = profile_sha = None
        schema_version, template_version = "1.1", "sfc-batch-2"
        headers = ["structure_ref_a", "structure_ref_b", "modifier_total_fraction", "modifier_components", "candidate_scope_id", "top_k", "tool", "column_id"]
        rows = rows if rows is not None else [["structure-a", "structure-b", 0.2, '[{"structure_ref":"modifier-a","fraction":1.0}]', "scope-a", 1, "full_column", ""]]
        fields = headers
        eval_budget = 2
    content = _batch_csv(headers, rows)
    engine = create_engine("sqlite:///:memory:")
    event.listen(engine, "connect", lambda connection, _record: connection.create_function(
        "CHAR_LENGTH", 1, lambda value: len(value) if value is not None else None,
    ))
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(Project(id="project-a", slug="project-a", name="batch test project"))
    session.add(Artifact(
        id="artifact-batch", project_id="project-a", kind="batch_input", media_type="text/csv",
        size_bytes=len(content), storage_key="batch.csv", status="AVAILABLE", scan_status="PASSED",
        created_by="actor-a",
    ))
    session.flush()
    model = SimpleNamespace(
        id=f"{chromatography_type}-model", version="1", scope=f"{chromatography_type}-scope",
        artifact_sha256="a" * 64, input_schema_version=schema_version, output_schema_version="1.1",
        preprocessing_profile_id=profile_id, preprocessing_profile_sha256=profile_sha,
        score_semantics=None, threshold=None, health=SimpleNamespace(status="READY"),
    )
    snapshot = ModelSnapshot(
        id=model.id, version=model.version, business_scope_id=model.scope,
        artifact_sha256=model.artifact_sha256, input_schema_version=schema_version,
        output_schema_version=model.output_schema_version,
        preprocessing_profile_id=profile_id, preprocessing_profile_sha256=profile_sha,
    )
    capabilities = _BatchCapabilities(chromatography_type, model)
    preparation = _BatchPreparation(snapshot)
    actor = ActorContext(
        actor_id="actor-a", project_ids=["project-a"], auth_method="service",
        permissions=["*"], policy_version="p1",
    )
    ctx = OperationContext(actor)
    service = BatchPredictionService(
        session, artifact=_BatchArtifact(session, content), capabilities=capabilities,
        prediction_create_service=preparation, enabled_batch_types={chromatography_type},
    )
    payload = TypeAdapter(BatchDraftCreate).validate_python({
        "project_id": "project-a", "name": "batch test", "chromatography_type": chromatography_type,
        "tool": "batch", "schema_version": schema_version,
        "model_selection": {
            "mode": "exact", "model_id": model.id, "model_version": model.version,
            **({"preprocessing_profile_id": profile_id, "preprocessing_profile_sha256": profile_sha} if profile_id else {}),
        },
        "input_artifact_id": "artifact-batch", "template_version": template_version,
        "field_mapping": {name: name for name in fields}, "invalid_row_policy": policy,
        "resource_budget": {"max_runtime_seconds": 60, "max_evaluations": eval_budget if max_evaluations is None else max_evaluations},
    })
    draft = service.create(ctx, payload)
    accepted = service.validate(ctx, draft.id, if_match=draft.etag)
    validation = service.run_validation(ctx, accepted.id)
    return engine, session, ctx, service, preparation, draft, validation


def test_hplc_v2_validation_freezes_ordered_raw_pair_and_column_evaluations() -> None:
    engine, session, _ctx_value, _service, preparation, _draft, validation = _non_tlc_validation("hplc")
    try:
        item = session.scalar(select(BatchValidationItemRow).where(
            BatchValidationItemRow.validation_id == validation.id,
            BatchValidationItemRow.status == "VALID",
        ))
        assert validation.status == "READY"
        assert validation.total_evaluations == 2
        assert item is not None
        frozen = item.resolved_input_snapshot
        structure_refs = frozen["request"]["structure_refs"]
        assert len(structure_refs) == 2 and structure_refs[0] != structure_refs[1]
        assert frozen["request"]["input"]["model_input_smiles"] == ["C[C@H](O)F", "C[C@@H](O)F"]
        assert frozen["normalized_structures"] == [f"normalized:{ref}" for ref in structure_refs]
        structures = {
            structure.id: structure
            for structure in session.scalars(select(Structure).where(Structure.id.in_(structure_refs)))
        }
        assert [structures[ref].original_input["value"] for ref in structure_refs] == [
            "C[C@H](O)F", "C[C@@H](O)F",
        ]
        assert len(preparation.requests) == 1
    finally:
        session.close()
        engine.dispose()


def test_sfc_retry_plan_uses_frozen_scope_count_instead_of_top_k() -> None:
    engine, session, ctx, service, _preparation, draft, validation = _non_tlc_validation("sfc")
    try:
        assert validation.status == "READY"
        assert validation.total_evaluations == 2
        item = session.scalar(select(BatchValidationItemRow).where(
            BatchValidationItemRow.validation_id == validation.id,
            BatchValidationItemRow.status == "VALID",
        ))
        assert item is not None
        assert item.resolved_input_snapshot["request"]["input"]["top_k"] == 1
        service.confirmation = _BatchConfirmation()
        service.submit(ctx, draft.id, BatchSubmit(confirmation_id="confirmed"))
        result = session.scalar(select(BatchRowResultRow).where(BatchRowResultRow.row_id == item.row_id))
        assert result is not None
        result.status = "FAILED"
        batch = session.get(BatchPredictionRow, draft.id)
        assert batch is not None and batch.job_id
        job = session.get(Job, batch.job_id)
        assert job is not None
        job.status = JobStatus.FAILED.value
        session.flush()
        current = service.get(ctx, draft.id)
        plan = service.create_retry_plan(
            ctx, draft.id, BatchRetryPlanCreate(row_ids=[item.row_id]), if_match=current.etag,
        )
        assert plan.row_ids == [item.row_id]
        assert plan.estimated_evaluations == 2
        assert batch.resource_budget["max_evaluations"] == 2
    finally:
        session.close()
        engine.dispose()


def test_all_invalid_exclude_batch_is_invalid_and_cannot_submit() -> None:
    engine, session, ctx, service, _preparation, draft, validation = _non_tlc_validation(
        "hplc", policy="exclude_invalid",
        rows=[["C[C@H](O)F", "C[C@@H](O)F", 0.4, 1.0, "[]"]],
    )
    try:
        assert validation.status == "INVALID"
        assert validation.valid_rows == 0
        assert validation.invalid_rows == 1
        assert validation.excluded_rows == 1
        saved = service.get(ctx, draft.id)
        assert saved.counts.failed == 0
        assert saved.counts.skipped == 1
        with pytest.raises(BatchServiceError) as exc_info:
            service.submit(ctx, draft.id, BatchSubmit(confirmation_id="unused"))
        assert exc_info.value.code == "BATCH_NOT_READY"
    finally:
        session.close()
        engine.dispose()


def test_submit_counts_excluded_rows_as_skipped_not_failed() -> None:
    engine, session, ctx, service, _preparation, draft, validation = _non_tlc_validation(
        "hplc", policy="exclude_invalid",
        rows=[
            ["C[C@H](O)F", "C[C@@H](O)F", 0.4, 1.0, '["column-a","column-b"]'],
            ["C[C@H](O)F", "C[C@@H](O)F", 0.4, 1.0, "[]"],
        ],
    )
    try:
        assert validation.status == "READY"
        assert validation.valid_rows == 1 and validation.excluded_rows == 1
        service.confirmation = _BatchConfirmation()
        service.submit(ctx, draft.id, BatchSubmit(confirmation_id="confirmed"))
        saved = session.get(BatchPredictionRow, draft.id)
        assert saved is not None
        projected = service.get(ctx, draft.id)
        assert projected.counts.failed == 0
        assert projected.counts.skipped == 1
        canceled = service.cancel(ctx, draft.id, BatchCancel(reason="cancel queued batch"), if_match=projected.etag)
        assert canceled.counts.total == 2
        assert canceled.counts.canceled == 1
        assert canceled.counts.skipped == 1
    finally:
        session.close()
        engine.dispose()


def test_exclude_policy_budget_rejection_keeps_row_and_count_states_consistent() -> None:
    valid = ["structure-a", "structure-b", 0.2, '[{"structure_ref":"modifier-a","fraction":1.0}]', "scope-a", 1, "full_column", ""]
    engine, session, ctx, service, _preparation, draft, validation = _non_tlc_validation(
        "sfc", policy="exclude_invalid", rows=[valid, valid], max_evaluations=3,
    )
    try:
        assert validation.status == "INVALID"
        assert validation.total_evaluations == 4
        projected = service.get(ctx, draft.id)
        assert projected.counts.total == projected.counts.skipped == 2
        assert projected.counts.queued == projected.counts.running == 0
        assert list(session.scalars(select(BatchRowResultRow.status))) == ["SKIPPED", "SKIPPED"]
    finally:
        session.close()
        engine.dispose()


def test_tlc_v1_template_is_rejected_for_new_draft() -> None:
    service = BatchPredictionService(enabled_batch_types={"tlc"})
    ctx = OperationContext(ActorContext(
        actor_id="actor-a", project_ids=["project-a"], auth_method="service",
        permissions=["*"], policy_version="p1",
    ))
    payload = TypeAdapter(BatchDraftCreate).validate_python({
        "project_id": "project-a", "name": "legacy", "chromatography_type": "tlc", "tool": "batch",
        "schema_version": "1.0", "model_selection": {"mode": "champion", "scope_id": "tlc-default"},
        "input_artifact_id": "artifact-a", "template_version": "tlc-batch-1",
        "field_mapping": {"structure_ref": "structure_ref", "solvent_components": "solvent_components"},
        "invalid_row_policy": "block", "resource_budget": {"max_runtime_seconds": 60, "max_evaluations": 10},
    })
    with pytest.raises(BatchServiceError) as exc_info:
        service.create(ctx, payload)
    assert exc_info.value.code == "BATCH_TEMPLATE_UNSUPPORTED"
    legacy = next(item for item in default_batch_template_registry().list("tlc") if item.version == "tlc-batch-1")
    assert legacy.health.status == "UNAVAILABLE"
    assert legacy.health.reason == "LEGACY_BATCH_TEMPLATE_READ_ONLY"


def test_csv_batch_row_limit_is_enforced_before_materializing_all_rows() -> None:
    headers = ["smiles_a", "smiles_b", "isopropanol_fraction", "flow_rate_ml_min", "column_ids"]
    content = _batch_csv(headers, [["C", "CC", 0.5, 1.0, '["column-a"]']] * (MAX_BATCH_ROWS + 1))
    with pytest.raises(BatchServiceError) as exc_info:
        _parse_csv(
            content, field_mapping={name: name for name in headers}, row_model=object,
            expected_fields=headers,
        )
    assert exc_info.value.code == "BATCH_ROW_LIMIT_EXCEEDED"
