from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import TypeAdapter
from pydantic import ValidationError
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from chrom_platform.core.dto import ActorContext, CapabilityHealth
from chrom_platform.infrastructure.models import Base, Project, Job
from chrom_platform.infrastructure.repositories import SQLAlchemyStructureRepository
from chrom_platform.artifact_core.models import Artifact, Structure
from chrom_platform.modules.prediction.batch import (
    BatchDraft,
    BatchDraftCreate,
    BatchDraftPatch,
    BatchRetryFailed,
    BatchRetryPlanCreate,
    BatchSubmit,
    TlcBatchCompatibleModel,
    TlcBatchModelConstraints,
)
from chrom_platform.modules.prediction.batch_service import (
    BatchPredictionRow,
    BatchPredictionService,
    BatchRowResultRow,
    BatchServiceError,
    BatchValidationItemRow,
    BatchValidationRow,
)
from chrom_platform.modules.prediction.batch_worker import BatchPredictionWorker
from chrom_platform.task_core.service import OperationContext
from chrom_platform.task_core.state import JobStatus
from chrom_platform.structure_core.schemas import NormalizeStructureRequest
from chrom_platform.structure_core.service import StructureService


SCHEMA_VERSION = "tlc-input-test-v1"
MODEL_ID = "tlc-test-model"
MODEL_VERSION = "1"
MODEL_SHA = "a" * 64
SOLVENTS = [
    {"solvent_id": "hexane", "fraction": 0.5},
    {"solvent_id": "ethyl_acetate", "fraction": 0.5},
]


class _ArtifactPort:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def get_artifact(self, artifact_id: str, _actor: ActorContext):
        return SimpleNamespace(
            id=artifact_id, project_id="p1", kind="batch_input", status="AVAILABLE",
            scan_status="PASSED", download_allowed=True, media_type="text/csv",
            file_name="tlc.csv", size_bytes=len(self.content), storage_key="input.csv",
        )

    def read_batch(self, _artifact_id: str, _actor: ActorContext) -> bytes:
        return self.content


class _ConfirmationPort:
    def __init__(self) -> None:
        self.consumed: list[tuple[str, str, int, str]] = []

    def consume_for_mutation(self, _ctx, confirmation_id, *, target, action, session, **_kwargs):
        assert session is not None
        self.consumed.append((confirmation_id, action, target.revision, target.config_hash))


class _Registry:
    def __init__(self, *, fail_structure: str | None = None) -> None:
        self.fail_structure = fail_structure
        self.requests = []
        self.snapshots = []

    def execute_snapshot(self, chromatography_type, request, *, model_snapshot):
        assert chromatography_type == "tlc"
        self.requests.append(request)
        self.snapshots.append(model_snapshot.model_dump(mode="json"))
        if request.structure_refs == [self.fail_structure]:
            raise RuntimeError("synthetic adapter failure")
        return SimpleNamespace(model_dump=lambda mode="json": {
            "task_type": "tlc", "model_id": MODEL_ID,
            "model_version": MODEL_VERSION, "rows": [{"rf": 0.222364}],
        })


def _csv(rows: list[tuple[str, object]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["structure_ref", "solvent_components"])
    for structure_ref, solvents in rows:
        encoded = json.dumps(solvents, separators=(",", ":")) if not isinstance(solvents, str) else solvents
        writer.writerow([structure_ref, encoded])
    return output.getvalue().encode("utf-8")


def _fake_tlc_constraints_provider(capability, templates):
    """Component-test binding only; it does not represent verified production health."""
    capability = capability.capability("tlc")
    versions = [item.version for item in templates]
    model = next(item for item in capability.models if item.id == MODEL_ID and item.version == MODEL_VERSION)
    return TlcBatchModelConstraints(
        template_versions=versions,
        compatible_models=[TlcBatchCompatibleModel(
            model_id=MODEL_ID,
            model_version=MODEL_VERSION,
            input_schema_version=model.input_schema_version,
            template_versions=versions,
            health=CapabilityHealth(status="READY", checked_at=datetime.now(timezone.utc)),
        )],
    )


def _empty_tlc_constraints_provider(_capability, templates):
    return TlcBatchModelConstraints(
        template_versions=[item.version for item in templates],
        compatible_models=[],
    )


def _make_flow(*, rows: list[tuple[str, object]], policy: str = "block", constraints_provider=_fake_tlc_constraints_provider):
    content = _csv(rows)
    engine = create_engine("sqlite:///:memory:")
    event.listen(engine, "connect", lambda connection, _record: connection.create_function(
        "CHAR_LENGTH", 1, lambda value: len(value) if value is not None else None,
    ))
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(Project(id="p1", slug="p1", name="TLC test project"))
    session.add(Artifact(
        id="artifact-tlc", project_id="p1", kind="batch_input", media_type="text/csv",
        size_bytes=len(content), storage_key="artifact-tlc.csv", status="AVAILABLE",
        scan_status="PASSED", created_by="actor-a",
    ))
    session.flush()
    actor = ActorContext(
        actor_id="actor-a", project_ids=["p1"], auth_method="service",
        permissions=["*"], policy_version="test",
    )
    ctx = OperationContext(actor)
    model = SimpleNamespace(
        id=MODEL_ID, version=MODEL_VERSION, scope="tlc-scope", artifact_sha256=MODEL_SHA,
        input_schema_version=SCHEMA_VERSION, output_schema_version="tlc-output-test-v1",
        preprocessing_profile_id=None, preprocessing_profile_sha256=None,
        score_semantics=None, threshold=None, health=SimpleNamespace(status="READY"),
    )
    capability = SimpleNamespace(health=SimpleNamespace(status="READY"), models=[model])
    capabilities = SimpleNamespace(capability=lambda _type: capability)
    confirmation = _ConfirmationPort()
    service = BatchPredictionService(
        session, artifact=_ArtifactPort(content), confirmation=confirmation,
        capabilities=capabilities, enabled_batch_types={"tlc"},
        tlc_batch_constraints_provider=constraints_provider,
    )
    payload = TypeAdapter(BatchDraftCreate).validate_python({
        "project_id": "p1", "name": "one-row TLC batch", "chromatography_type": "tlc",
        "tool": "batch", "schema_version": SCHEMA_VERSION,
        "model_selection": {"mode": "exact", "model_id": MODEL_ID, "model_version": MODEL_VERSION},
        "input_artifact_id": "artifact-tlc", "template_version": "tlc-batch-1",
        "field_mapping": {"structure_ref": "structure_ref", "solvent_components": "solvent_components"},
        "invalid_row_policy": policy,
        "resource_budget": {"max_runtime_seconds": 60, "max_evaluations": 10},
    })
    draft = service.create(ctx, payload)
    accepted = service.validate(ctx, draft.id, if_match=draft.etag)
    validation = service.run_validation(ctx, accepted.id)
    return engine, session, ctx, service, confirmation, draft, validation, content


def _make_v2_flow(*, smiles: str = "C[C@H](O)F", solvent_1: str = "hexane", fraction_1: float = 0.7,
                  solvent_2: str = "ethyl_acetate", fraction_2: float = 0.3, policy: str = "block",
                  rows: list[tuple[str, str, float, str, float]] | None = None,
                  permissions: list[str] | None = None, auto_validate: bool = True,
                  constraints_provider=_fake_tlc_constraints_provider):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    headers = ["smiles", "solvent_1", "fraction_1", "solvent_2", "fraction_2"]
    writer.writerow(headers)
    for item in rows or [(smiles, solvent_1, fraction_1, solvent_2, fraction_2)]:
        writer.writerow(item)
    content = output.getvalue().encode("utf-8")
    engine = create_engine("sqlite:///:memory:")
    event.listen(engine, "connect", lambda connection, _record: connection.create_function(
        "CHAR_LENGTH", 1, lambda value: len(value) if value is not None else None,
    ))
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(Project(id="p1", slug="p1", name="TLC test project"))
    session.add(Project(id="p2", slug="p2", name="Second TLC test project"))
    session.add(Artifact(
        id="artifact-tlc", project_id="p1", kind="batch_input", media_type="text/csv",
        size_bytes=len(content), storage_key="artifact-tlc.csv", status="AVAILABLE",
        scan_status="PASSED", created_by="actor-a",
    ))
    session.flush()
    actor = ActorContext(
        actor_id="actor-a", project_ids=["p1"], auth_method="service",
        permissions=permissions or ["*"], policy_version="test",
    )
    ctx = OperationContext(actor)
    model = SimpleNamespace(
        id=MODEL_ID, version=MODEL_VERSION, scope="tlc-scope", artifact_sha256=MODEL_SHA,
        input_schema_version=SCHEMA_VERSION, output_schema_version="tlc-output-test-v1",
        preprocessing_profile_id=None, preprocessing_profile_sha256=None,
        score_semantics=None, threshold=None, health=SimpleNamespace(status="READY"),
    )
    tool = SimpleNamespace(
        id="single", health=SimpleNamespace(status="READY"),
        model_constraints={"allowed_systems": [
            ["hexane", "ethyl_acetate"],
            ["dichloromethane", "methanol"],
            ["hexane", "diethyl_ether"],
        ]},
    )
    capability = SimpleNamespace(health=SimpleNamespace(status="READY"), models=[model], tools=[tool])
    service = BatchPredictionService(
        session, artifact=_ArtifactPort(content), confirmation=_ConfirmationPort(),
        capabilities=SimpleNamespace(capability=lambda _type: capability), enabled_batch_types={"tlc"},
        tlc_batch_constraints_provider=constraints_provider,
    )
    payload = TypeAdapter(BatchDraftCreate).validate_python({
        "project_id": "p1", "name": "raw TLC batch", "chromatography_type": "tlc",
        "tool": "batch", "schema_version": SCHEMA_VERSION,
        "model_selection": {"mode": "exact", "model_id": MODEL_ID, "model_version": MODEL_VERSION},
        "input_artifact_id": "artifact-tlc", "template_version": "tlc-batch-2",
        "field_mapping": {field: field for field in headers},
        "invalid_row_policy": policy,
        "resource_budget": {"max_runtime_seconds": 60, "max_evaluations": 10},
    })
    draft = service.create(ctx, payload)
    if auto_validate:
        accepted = service.validate(ctx, draft.id, if_match=draft.etag)
        validation = service.run_validation(ctx, accepted.id)
    else:
        validation = None
    return engine, session, ctx, service, draft, validation


def _submit(service, ctx, draft_id: str):
    return service.submit(ctx, draft_id, BatchSubmit(confirmation_id="confirm-tlc"))


def test_tlc_batch_binds_artifact_validation_confirmation_and_worker_snapshot() -> None:
    engine, session, ctx, service, confirmation, draft, validation, _content = _make_flow(
        rows=[("structure-1", SOLVENTS)],
    )
    assert validation.status == "READY"
    assert validation.total_rows == validation.valid_rows == 1
    assert validation.invalid_rows == validation.excluded_rows == 0
    assert validation.model_snapshot.id == MODEL_ID
    assert validation.model_snapshot.version == MODEL_VERSION
    assert validation.model_snapshot.artifact_sha256 == MODEL_SHA
    assert draft.config_hash == validation.config_hash
    preview_page = service.validation_rows(ctx, draft.id, status_filter=None, cursor=None, limit=10)
    assert len(preview_page.rows) == 1
    assert preview_page.rows[0].resolved_input_snapshot is not None
    assert preview_page.rows[0].resolved_input_snapshot.template_version == "tlc-batch-1"

    original_hash = draft.config_hash
    current = service.get(ctx, draft.id)
    assert current.config_hash == original_hash
    patched = service.patch(
        ctx, draft.id, BatchDraftPatch(name="renamed TLC batch"), if_match=current.etag,
    )
    assert patched.revision == draft.revision + 1
    assert patched.config_hash != original_hash
    with pytest.raises(ValidationError):
        BatchDraft.model_validate({**patched.model_dump(mode="json"), "config_hash": "invalid"})
    validation_accepted = service.validate(ctx, draft.id, if_match=patched.etag)
    validation = service.run_validation(ctx, validation_accepted.id)
    assert validation.draft_revision == patched.revision
    assert validation.config_hash == patched.config_hash
    draft = patched

    accepted = _submit(service, ctx, draft.id)
    assert len(confirmation.consumed) == 1
    confirmation_id, action, revision, config_hash = confirmation.consumed[0]
    assert (confirmation_id, action, revision) == ("confirm-tlc", "batch.submit", draft.revision)
    assert len(config_hash) == 64
    with pytest.raises(BatchServiceError) as repeated:
        _submit(service, ctx, draft.id)
    assert repeated.value.code == "CONFIRMATION_STALE"
    assert len(confirmation.consumed) == 1

    registry = _Registry()
    final = BatchPredictionWorker(session, registry, enabled_batch_types={"tlc"}).execute(
        accepted.id, actor=ctx.actor, expected_attempt=accepted.attempt,
    )
    assert final is JobStatus.SUCCEEDED
    assert len(registry.requests) == 1
    request = registry.requests[0]
    assert request.structure_refs == ["structure-1"]
    assert request.tool == "single"
    assert [item.model_dump(mode="json") for item in request.input.solvent_components] == SOLVENTS
    assert registry.snapshots[0]["artifact_sha256"] == MODEL_SHA
    results = list(session.scalars(select(BatchRowResultRow).where(
        BatchRowResultRow.batch_id == draft.id,
        BatchRowResultRow.validation_id == validation.id,
    )))
    assert len(results) == 1 and results[0].status == "SUCCEEDED"
    assert results[0].result["rows"] == [{"rf": 0.222364}]
    job = session.get(Job, accepted.job_id)
    assert job is not None and job.status == JobStatus.SUCCEEDED.value
    session.close()
    engine.dispose()


def test_tlc_batch_validation_fails_closed_without_exact_template_binding() -> None:
    engine, session, ctx, service, draft, validation = _make_v2_flow(
        auto_validate=False,
        constraints_provider=_empty_tlc_constraints_provider,
    )
    assert validation is None
    initial_jobs = len(list(session.scalars(select(Job))))
    with pytest.raises(BatchServiceError) as unavailable:
        service.validate(ctx, draft.id, if_match=draft.etag)
    assert unavailable.value.code == "MODEL_UNAVAILABLE"
    assert len(list(session.scalars(select(Job)))) == initial_jobs
    assert service.get(ctx, draft.id).draft_status == "DRAFT"
    session.close()
    engine.dispose()


@pytest.mark.parametrize(
    ("drift", "expected_code"),
    [("artifact", "BATCH_MODEL_BINDING_STALE"), ("schema", "MODEL_UNAVAILABLE")],
)
def test_tlc_batch_submit_rejects_changed_binding_before_confirmation_or_job(drift, expected_code) -> None:
    engine, session, ctx, service, confirmation, draft, validation, _content = _make_flow(
        rows=[("structure-1", SOLVENTS)],
    )
    assert validation.status == "READY"
    jobs_before = len(list(session.scalars(select(Job))))
    capability = service.capabilities.capability("tlc")
    if drift == "artifact":
        capability.models[0].artifact_sha256 = "b" * 64
    else:
        capability.models[0].input_schema_version = "tlc-input-changed-v2"

    with pytest.raises(BatchServiceError) as stale:
        _submit(service, ctx, draft.id)

    assert stale.value.code == expected_code
    assert confirmation.consumed == []
    assert len(list(session.scalars(select(Job)))) == jobs_before
    batch = session.get(BatchPredictionRow, draft.id)
    assert batch is not None and batch.submitted_at is None and batch.job_id is not None
    session.close()
    engine.dispose()


def test_tlc_batch_retry_rejects_changed_artifact_before_confirmation_or_attempt() -> None:
    engine, session, ctx, service, confirmation, draft, validation, _content = _make_flow(
        rows=[("structure-1", SOLVENTS)],
    )
    accepted = _submit(service, ctx, draft.id)
    failure = BatchPredictionWorker(
        session, _Registry(fail_structure="structure-1"), enabled_batch_types={"tlc"},
    ).execute(accepted.id, actor=ctx.actor, expected_attempt=accepted.attempt)
    assert failure is JobStatus.FAILED
    failed_row = session.scalar(select(BatchRowResultRow).where(
        BatchRowResultRow.batch_id == draft.id,
        BatchRowResultRow.validation_id == validation.id,
        BatchRowResultRow.status == "FAILED",
    ))
    assert failed_row is not None
    current = service.get(ctx, draft.id)
    plan = service.create_retry_plan(
        ctx, draft.id, BatchRetryPlanCreate(row_ids=[failed_row.row_id]), if_match=current.etag,
    )
    job_count = len(list(session.scalars(select(Job))))
    capability = service.capabilities.capability("tlc")
    capability.models[0].artifact_sha256 = "c" * 64

    with pytest.raises(BatchServiceError) as stale:
        service.retry_failed(
            ctx, draft.id,
            BatchRetryFailed(retry_plan_id=plan.id, confirmation_id="confirm-retry"),
            if_match=current.etag,
        )

    assert stale.value.code == "BATCH_MODEL_BINDING_STALE"
    assert len(confirmation.consumed) == 1
    assert len(list(session.scalars(select(Job)))) == job_count
    batch = session.get(BatchPredictionRow, draft.id)
    assert batch is not None and batch.attempt == accepted.attempt
    session.close()
    engine.dispose()


def test_tlc_batch_draft_revision_change_invalidates_confirmation_target() -> None:
    engine, session, ctx, service, confirmation, draft, validation, _content = _make_flow(
        rows=[("structure-1", SOLVENTS)],
    )
    assert validation.status == "READY"
    from chrom_platform.core.dto import ResourceRef

    old_target = service.resolve_confirmation_target(
        ctx, ResourceRef(type="batch_prediction", id=draft.id), "batch.submit",
    )
    current = service.get(ctx, draft.id)
    patched = service.patch(
        ctx, draft.id, BatchDraftPatch(name="revised batch"), if_match=current.etag,
    )
    assert patched.revision == old_target.revision + 1
    with pytest.raises(BatchServiceError) as stale:
        service.resolve_confirmation_target(
            ctx, ResourceRef(type="batch_prediction", id=draft.id), "batch.submit",
        )
    assert stale.value.code == "CONFIRMATION_STALE"
    assert confirmation.consumed == []
    session.close()
    engine.dispose()


@pytest.mark.parametrize(
    ("policy", "expected_validation", "expected_worker_status", "expected_counts"),
    [
        ("block", "INVALID", None, {"valid": 1, "invalid": 1, "excluded": 0}),
        ("exclude_invalid", "READY", JobStatus.SUCCEEDED, {"valid": 1, "invalid": 1, "excluded": 1}),
    ],
)
def test_tlc_batch_invalid_rows_block_or_remain_skipped(
    policy: str, expected_validation: str, expected_worker_status: JobStatus | None,
    expected_counts: dict[str, int],
) -> None:
    engine, session, ctx, service, confirmation, draft, validation, _content = _make_flow(
        rows=[("structure-ok", SOLVENTS), ("structure-bad", "not-json")], policy=policy,
    )
    assert validation.status == expected_validation
    assert (validation.valid_rows, validation.invalid_rows, validation.excluded_rows) == (
        expected_counts["valid"], expected_counts["invalid"], expected_counts["excluded"],
    )
    if policy == "block":
        stored_rows = list(session.scalars(select(BatchRowResultRow).where(
            BatchRowResultRow.batch_id == draft.id,
            BatchRowResultRow.validation_id == validation.id,
        )))
        assert sorted(row.status for row in stored_rows) == ["SKIPPED", "SKIPPED"]
        batch = session.get(BatchPredictionRow, draft.id)
        assert batch is not None and batch.counts["failed"] == 0 and batch.counts["skipped"] == 2
        initial_job_count = len(list(session.scalars(select(Job))))
        with pytest.raises(BatchServiceError) as exc:
            _submit(service, ctx, draft.id)
        assert exc.value.code == "BATCH_NOT_READY"
        assert confirmation.consumed == []
        assert len(list(session.scalars(select(Job)))) == initial_job_count
    else:
        accepted = _submit(service, ctx, draft.id)
        registry = _Registry()
        final = BatchPredictionWorker(session, registry, enabled_batch_types={"tlc"}).execute(
            accepted.id, actor=ctx.actor, expected_attempt=accepted.attempt,
        )
        assert final is expected_worker_status
        assert len(registry.requests) == 1
        rows = list(session.scalars(select(BatchRowResultRow).where(
            BatchRowResultRow.batch_id == draft.id,
        )))
        assert sorted(row.status for row in rows) == ["SKIPPED", "SUCCEEDED"]
        batch = session.get(BatchPredictionRow, draft.id)
        assert batch is not None
        assert batch.counts["failed"] == 0 and batch.counts["skipped"] == 1
        assert final is not JobStatus.PARTIAL
    session.close()
    engine.dispose()


def test_tlc_batch_v2_freezes_real_structure_ref_and_typed_current_preview() -> None:
    engine, session, ctx, service, draft, validation = _make_v2_flow()
    assert validation.status == "READY" and validation.valid_rows == 1
    structures = list(session.scalars(select(Structure).where(Structure.project_id == "p1")))
    assert len(structures) == 1
    assert structures[0].canonical_smiles == "C[C@H](O)F"
    assert structures[0].original_input == {"input_type": "smiles", "value": "C[C@H](O)F", "source_artifact_id": None}
    page = service.validation_rows(ctx, draft.id, status_filter=None, cursor=None, limit=10)
    preview = page.rows[0].resolved_input_snapshot
    assert preview is not None
    assert preview.template_version == "tlc-batch-2"
    assert preview.project_id == "p1" and preview.draft_revision == draft.revision
    assert preview.validation_id == validation.id
    assert preview.normalized_smiles == structures[0].canonical_smiles
    assert preview.structure_ref == structures[0].id
    assert [item.model_dump(mode="json") for item in preview.solvent_conditions.solvent_components] == [
        {"solvent_id": "hexane", "fraction": 0.7},
        {"solvent_id": "ethyl_acetate", "fraction": 0.3},
    ]
    validation_item = session.scalar(select(BatchRowResultRow).where(BatchRowResultRow.batch_id == draft.id))
    assert validation_item is not None and validation_item.status == "QUEUED"
    accepted = _submit(service, ctx, draft.id)
    registry = _Registry()
    final = BatchPredictionWorker(session, registry, enabled_batch_types={"tlc"}).execute(
        accepted.id, actor=ctx.actor, expected_attempt=accepted.attempt,
    )
    assert final is JobStatus.SUCCEEDED
    assert registry.requests[0].structure_refs == [structures[0].id]
    assert [item.model_dump(mode="json") for item in registry.requests[0].input.solvent_components] == [
        {"solvent_id": "hexane", "fraction": 0.7},
        {"solvent_id": "ethyl_acetate", "fraction": 0.3},
    ]
    session.close()
    engine.dispose()


@pytest.mark.parametrize(
    ("smiles", "solvent_1", "solvent_2", "expected_field"),
    [
        ("not-a-smiles", "hexane", "ethyl_acetate", "smiles"),
        ("C[C@H](O)F", "hexane", "methanol", "solvent_1"),
    ],
)
def test_tlc_batch_v2_invalid_rows_have_validation_errors_no_preview_or_model_failure(
    smiles: str, solvent_1: str, solvent_2: str, expected_field: str,
) -> None:
    engine, session, ctx, service, draft, validation = _make_v2_flow(
        smiles=smiles, solvent_1=solvent_1, solvent_2=solvent_2,
    )
    assert validation.status == "INVALID"
    assert validation.valid_rows == 0 and validation.invalid_rows == 1
    page = service.validation_rows(ctx, draft.id, status_filter=None, cursor=None, limit=10)
    assert page.rows[0].status == "INVALID"
    assert page.rows[0].resolved_input_snapshot is None
    assert page.rows[0].field_errors[0]["stage"] == "VALIDATION"
    assert page.rows[0].field_errors[0]["field"] == expected_field
    results = list(session.scalars(select(BatchRowResultRow).where(BatchRowResultRow.batch_id == draft.id)))
    assert len(results) == 1 and results[0].status == "SKIPPED"
    assert results[0].error["stage"] == "VALIDATION"
    batch = session.get(BatchPredictionRow, draft.id)
    assert batch is not None
    assert batch.counts["failed"] == 0 and batch.counts["skipped"] == 1
    assert list(session.scalars(select(Structure).where(Structure.project_id == "p1"))) == []
    session.close()
    engine.dispose()


@pytest.mark.parametrize("fractions", [(0.0, 1.0), (1.0, 0.0)])
def test_tlc_batch_v2_unary_mobile_phase_is_invalid_before_structure_write(fractions) -> None:
    engine, session, ctx, service, draft, validation = _make_v2_flow(
        fraction_1=fractions[0], fraction_2=fractions[1],
    )
    assert validation.status == "INVALID"
    assert (validation.valid_rows, validation.invalid_rows) == (0, 1)
    assert list(session.scalars(select(Structure).where(Structure.project_id == "p1"))) == []
    preview = service.validation_rows(ctx, draft.id, status_filter=None, cursor=None, limit=10)
    assert preview.rows[0].resolved_input_snapshot is None
    assert preview.rows[0].field_errors[0]["stage"] == "VALIDATION"
    assert preview.rows[0].field_errors[0]["field"] == "solvent_1"
    results = list(session.scalars(select(BatchRowResultRow).where(BatchRowResultRow.batch_id == draft.id)))
    assert len(results) == 1 and results[0].status == "SKIPPED"
    assert results[0].error["stage"] == "VALIDATION"
    assert session.get(BatchPredictionRow, draft.id).counts["failed"] == 0
    session.close()
    engine.dispose()


def test_tlc_batch_v2_canonical_dedup_preserves_stereo_and_project_isolation() -> None:
    engine, session, ctx, service, draft, validation = _make_v2_flow(rows=[
        ("C[C@H](O)F", "hexane", 0.7, "ethyl_acetate", 0.3),
        ("F[C@@H](O)C", "hexane", 0.7, "ethyl_acetate", 0.3),
        ("C[C@@H](O)F", "hexane", 0.7, "ethyl_acetate", 0.3),
    ])
    assert validation.status == "READY" and validation.valid_rows == 3
    previews = service.validation_rows(ctx, draft.id, status_filter=None, cursor=None, limit=10).rows
    assert previews[0].resolved_input_snapshot.structure_ref == previews[1].resolved_input_snapshot.structure_ref
    assert previews[2].resolved_input_snapshot.structure_ref != previews[0].resolved_input_snapshot.structure_ref
    project_structures = list(session.scalars(select(Structure).where(Structure.project_id == "p1")))
    assert len(project_structures) == 2
    assert {item.canonical_smiles for item in project_structures} == {
        previews[0].resolved_input_snapshot.normalized_smiles,
        previews[2].resolved_input_snapshot.normalized_smiles,
    }

    other_project = StructureService(None).normalize(
        NormalizeStructureRequest(project_id="p2", input_type="smiles", value="C[C@H](O)F", preserve_stereochemistry=True),
        structure_id="structure-p2",
        repository=SQLAlchemyStructureRepository(session),
    )
    assert other_project.project_id == "p2"
    assert other_project.id != previews[0].resolved_input_snapshot.structure_ref
    assert len(list(session.scalars(select(Structure).where(Structure.project_id == "p2")))) == 1
    session.close()
    engine.dispose()


def test_tlc_batch_v2_requires_prediction_create_before_validation_job_or_structure_write() -> None:
    engine, session, ctx, service, draft, _validation = _make_v2_flow(
        permissions=["batch:create", "batch:validate"], auto_validate=False,
    )
    assert list(session.scalars(select(Structure).where(Structure.project_id == "p1"))) == []
    jobs_before = list(session.scalars(select(Job)))
    with pytest.raises(BatchServiceError) as exc:
        service.validate(ctx, draft.id, if_match=draft.etag)
    assert (exc.value.status_code, exc.value.code) == (403, "PERMISSION_DENIED")
    assert list(session.scalars(select(Job))) == jobs_before == []
    assert list(session.scalars(select(Structure).where(Structure.project_id == "p1"))) == []
    session.close()
    engine.dispose()


@pytest.mark.parametrize("unselected_type", ["gc", "sfc"])
def test_batch_create_rejects_types_outside_manifest_scope(unselected_type: str) -> None:
    service = BatchPredictionService(enabled_batch_types={"tlc"})
    ctx = OperationContext(ActorContext(
        actor_id="actor-a", project_ids=["p1"], auth_method="service",
        permissions=["batch:create"], policy_version="test",
    ))
    payload = SimpleNamespace(project_id="p1", chromatography_type=unselected_type)
    with pytest.raises(BatchServiceError) as exc:
        service.create(ctx, payload)  # type: ignore[arg-type]
    assert (exc.value.status_code, exc.value.code) == (503, "BATCH_TYPE_NOT_ENABLED")


def test_tlc_batch_v2_old_revision_validation_is_stale_and_preview_is_hidden() -> None:
    engine, session, ctx, service, draft, old_validation = _make_v2_flow()
    old_item = session.scalar(select(BatchValidationItemRow).where(
        BatchValidationItemRow.validation_id == old_validation.id,
    ))
    assert old_item is not None and old_item.resolved_input_snapshot is not None
    current = service.get(ctx, draft.id)
    patched = service.patch(ctx, draft.id, BatchDraftPatch(name="new revision"), if_match=current.etag)
    stale = service.run_validation(ctx, old_validation.id)
    stored_validation = session.get(BatchValidationRow, old_validation.id)
    current_batch = session.get(BatchPredictionRow, draft.id)
    assert stale.status == "STALE"
    assert stored_validation is not None and stored_validation.status == "STALE"
    assert current_batch is not None and current_batch.revision == patched.revision
    assert BatchPredictionService._row_preview(current_batch, stored_validation, old_item) is None
    session.close()
    engine.dispose()


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        ({"project_id": "p1", "kind": "dataset_package", "scan_status": "PASSED"}, "ARTIFACT_KIND_INVALID"),
        ({"project_id": "p1", "kind": "batch_input"}, "ARTIFACT_NOT_AVAILABLE"),
        ({"project_id": "p1", "kind": "batch_input", "scan_status": "PASSED"}, "ARTIFACT_NOT_AVAILABLE"),
        ({"project_id": "p1", "kind": "batch_input", "status": None, "scan_status": "PASSED"}, "ARTIFACT_NOT_AVAILABLE"),
        ({"project_id": "p1", "kind": "batch_input", "status": "AVAILABLE"}, "ARTIFACT_NOT_AVAILABLE"),
        ({"kind": "batch_input", "scan_status": "PASSED"}, "RESOURCE_NOT_FOUND"),
        ({"project_id": "p2", "kind": "batch_input", "scan_status": "PASSED"}, "RESOURCE_NOT_FOUND"),
    ],
)
def test_batch_artifact_requires_owned_batch_kind_and_passed_scan(metadata, expected) -> None:
    ctx = OperationContext(ActorContext(
        actor_id="actor-a", project_ids=["p1", "p2"], auth_method="service", permissions=["*"], policy_version="test",
    ))
    with pytest.raises(BatchServiceError) as exc:
        BatchPredictionService._validate_artifact_value(ctx, SimpleNamespace(**metadata), project_id="p1")
    assert exc.value.code == expected


def test_tlc_batch_adapter_failure_is_row_scoped_and_projects_partial() -> None:
    engine, session, ctx, service, _confirmation, draft, validation, _content = _make_flow(
        rows=[("structure-ok", SOLVENTS), ("structure-fails", SOLVENTS)],
    )
    assert validation.status == "READY" and validation.valid_rows == 2
    accepted = _submit(service, ctx, draft.id)
    registry = _Registry(fail_structure="structure-fails")
    final = BatchPredictionWorker(session, registry, enabled_batch_types={"tlc"}).execute(
        accepted.id, actor=ctx.actor, expected_attempt=accepted.attempt,
    )
    assert final is JobStatus.PARTIAL
    rows = list(session.scalars(select(BatchRowResultRow).where(
        BatchRowResultRow.batch_id == draft.id,
    ).order_by(BatchRowResultRow.source_row_number)))
    assert [row.status for row in rows] == ["SUCCEEDED", "FAILED"]
    assert rows[1].error is not None and rows[1].error["code"] == "PREDICTION_EXECUTION_FAILED"
    batch = session.get(BatchPredictionRow, draft.id)
    assert batch is not None and batch.counts["succeeded"] == 1 and batch.counts["failed"] == 1
    session.close()
    engine.dispose()
