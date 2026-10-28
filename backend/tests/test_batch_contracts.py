from __future__ import annotations

import hashlib
import io
import zipfile
from types import SimpleNamespace

import pytest
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from chrom_platform.core.dto import ActorContext
from chrom_platform.core.dto import ResourceRef
from chrom_platform.core.idempotency import BeginResult, IdempotencyRecord
from chrom_platform.artifact_core.service import ArtifactServiceError
from chrom_platform.infrastructure.models import Base, Job, JobAttempt, JobEvent, OutboxEvent, Project
from chrom_platform.artifact_core.models import Artifact
from chrom_platform.task_core.service import OperationContext, TaskError
from chrom_platform.task_core.repository import SQLAlchemyTaskRepository
from chrom_platform.task_core.service import add_job_rows, sync_job_lifecycle
from chrom_platform.task_core.state import JobStatus
from chrom_platform.modules.prediction.batch import BatchDraftCreate, BatchSubmit, CcBatchRow, GcBatchRow, HplcBatchRow, SfcBatchRow, TlcBatchRow
from chrom_platform.modules.prediction.batch_service import BatchExportRow, BatchPredictionRow, BatchPredictionService, BatchRetryPlanRow, BatchRowResultRow, BatchServiceError, BatchValidationRow, _etag, _hash, _parse_csv, _parse_xlsx, build_batch_template_metadata, default_batch_template_registry
from chrom_platform.modules.prediction.batch import BatchCancel, BatchRetryFailed, BatchRetryPlanCreate


def test_five_type_templates_are_explicit_and_renderable() -> None:
    registry = default_batch_template_registry()
    assert [item.chromatography_type for item in registry.list()] == ["cc", "gc", "hplc", "sfc", "tlc", "tlc"]
    tlc_templates = [item for item in registry.list("tlc")]
    assert [item.version for item in tlc_templates] == ["tlc-batch-1", "tlc-batch-2"]
    assert tlc_templates[0].fields == ["structure_ref", "solvent_components"]
    assert tlc_templates[1].fields == ["smiles", "solvent_1", "fraction_1", "solvent_2", "fraction_2"]
    for item in registry.list():
        content, version, digest = registry.render(item.chromatography_type, version=item.version)
        assert content.startswith(item.fields[0].encode())
        assert version == item.version
        assert len(digest) == 64


def test_tlc_batch_v2_row_is_flat_and_does_not_accept_v1_shape() -> None:
    from chrom_platform.modules.prediction.batch import TlcBatchRowV2

    row = TlcBatchRowV2.model_validate({
        "smiles": "C[C@H](O)F", "solvent_1": "hexane", "fraction_1": 0.7,
        "solvent_2": "ethyl_acetate", "fraction_2": 0.3,
    })
    assert row.smiles == "C[C@H](O)F"
    with pytest.raises(ValidationError):
        TlcBatchRowV2.model_validate({"structure_ref": "s1", "solvent_components": [
            {"solvent_id": "hexane", "fraction": 0.5},
            {"solvent_id": "ethyl_acetate", "fraction": 0.5},
        ]})
    with pytest.raises(ValidationError):
        TlcBatchRowV2.model_validate({
            "smiles": "C[C@H](O)F", "solvent_1": "hexane", "fraction_1": 0.8,
            "solvent_2": "ethyl_acetate", "fraction_2": 0.3,
        })


def test_tlc_batch_template_metadata_is_typed_and_capability_derived() -> None:
    constraints = {
        "solvent_order": ["hexane", "ethyl_acetate", "dichloromethane", "methanol", "diethyl_ether"],
        "allowed_systems": [
            ["hexane", "ethyl_acetate"],
            ["dichloromethane", "methanol"],
            ["hexane", "diethyl_ether"],
        ],
    }
    capability = SimpleNamespace(tools=[SimpleNamespace(id="single", model_constraints=constraints)])
    metadata = build_batch_template_metadata(SimpleNamespace(capability=lambda type_id: capability))
    templates = [item for item in metadata if item.chromatography_type == "tlc"]
    assert [item.version for item in templates] == ["tlc-batch-1", "tlc-batch-2"]
    v1, v2 = templates
    assert v1.fields == ["structure_ref", "solvent_components"]
    assert v1.tlc_options is None
    assert v2.fields == ["smiles", "solvent_1", "fraction_1", "solvent_2", "fraction_2"]
    assert v2.limits.max_runtime_seconds == 3600 and v2.limits.max_evaluations == 100_000
    assert v2.health.status == "UNKNOWN" and v2.health.reason == "MODEL_CHECK_DEFERRED"
    assert [item.id for item in v2.tlc_options.solvent_options] == constraints["solvent_order"]
    assert [item.solvent_ids for item in v2.tlc_options.allowed_systems] == constraints["allowed_systems"]
    assert default_batch_template_registry().get("tlc", "batch", "tlc-batch-2").tlc_options is None


def test_rows_reject_unknown_fields_and_tlc_ratio_values() -> None:
    with pytest.raises(ValidationError):
        TlcBatchRow.model_validate({"structure_ref": "s1", "solvent_components": [{"solvent_id": "hexane", "fraction": 0.5}, {"solvent_id": "ethyl_acetate", "fraction": 0.5}], "extra": 1})
    with pytest.raises(ValidationError):
        TlcBatchRow.model_validate({"structure_ref": "s1", "solvent_components": [{"solvent_id": "hexane", "fraction": 8.0}, {"solvent_id": "ethyl_acetate", "fraction": 2.0}]})


def test_hplc_and_sfc_keep_two_structure_boundary() -> None:
    with pytest.raises(ValidationError):
        HplcBatchRow.model_validate({"structure_ref_a": "a", "structure_ref_b": "a", "isopropanol_fraction": 0.2, "flow_rate_ml_min": 1.0, "column_ids": ["c"]})
    with pytest.raises(ValidationError):
        SfcBatchRow.model_validate({"structure_ref_a": "a", "structure_ref_b": "b", "modifier_total_fraction": 0.2, "modifier_components": [{"structure_ref": "m", "fraction": 1.0}], "candidate_scope_id": "scope", "top_k": 1, "extra": "reject"})


def test_decode_error_isolated_to_one_csv_row_and_expected_fields_are_enforced() -> None:
    fields = ["structure_ref", "temperature_program"]
    content = b'structure_ref,temperature_program\ns1,"{""mode"":""series"",""temperature_series_c"":[100.0]}"\ns2,not-json\n'
    rows = _parse_csv(content, field_mapping={field: field for field in fields}, row_model=GcBatchRow, expected_fields=fields)
    assert rows[0][1]["structure_ref"] == "s1" and rows[0][2] is None
    assert rows[1][1] is None and rows[1][2]["code"] == "BATCH_ROW_INVALID"
    with pytest.raises(BatchServiceError) as exc:
        _parse_csv(content, field_mapping={"structure_ref": "structure_ref"}, row_model=GcBatchRow, expected_fields=fields)
    assert exc.value.code == "UNKNOWN_BATCH_FIELDS"


def test_common_input_is_presence_aware_and_identity_remains_row_scoped() -> None:
    fields = ["structure_ref", "temperature_program"]
    content = (
        b'structure_ref,temperature_program\n'
        b's1,\n'
        b's2,"{""mode"":""series"",""temperature_series_c"":[100.0]}"\n'
        b's3,null\n'
    )
    rows = _parse_csv(
        content, field_mapping={"structure_ref": "structure_ref", "temperature_program": "temperature_program"},
        row_model=GcBatchRow, expected_fields=fields,
        common_input={"temperature_program": {"mode": "linear", "initial_temperature_c": 80, "heating_rate_c_per_min": 10, "final_temperature_c": 120, "initial_hold_min": 0}},
    )
    assert rows[0][1]["temperature_program"]["mode"] == "linear"
    assert rows[1][1]["temperature_program"]["mode"] == "series"
    assert rows[2][1] is None and rows[2][2]["code"] == "BATCH_ROW_INVALID"
    with pytest.raises(BatchServiceError) as identity:
        _parse_csv(content, field_mapping={"structure_ref": "structure_ref", "temperature_program": "temperature_program"}, row_model=GcBatchRow, expected_fields=fields, common_input={"structure_ref": "must-not-override"})
    assert identity.value.code == "BATCH_COMMON_INPUT_INVALID"


def test_xlsx_upload_is_parsed_without_openpyxl() -> None:
    xml = (
        '<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>structure_ref</t></is></c>'
        '<c r="B1" t="inlineStr"><is><t>temperature_program</t></is></c></row>'
        '<row r="2"><c r="A2" t="inlineStr"><is><t>s1</t></is></c>'
        '<c r="B2" t="inlineStr"><is><t>{&quot;mode&quot;:&quot;series&quot;,&quot;temperature_series_c&quot;:[100]}</t></is></c></row>'
        '</sheetData></worksheet>'
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/worksheets/sheet1.xml", xml)
    rows = _parse_xlsx(output.getvalue(), field_mapping={"structure_ref": "structure_ref", "temperature_program": "temperature_program"}, row_model=GcBatchRow, expected_fields=["structure_ref", "temperature_program"])
    assert rows[0][1]["structure_ref"] == "s1" and rows[0][2] is None


def test_chiral_lc_batch_is_not_a_public_dto() -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(BatchDraftCreate).validate_python({
            "project_id": "project-a", "name": "chiral", "chromatography_type": "chiral_lc", "tool": "batch",
            "schema_version": "1.0", "model_selection": {"mode": "champion", "scope_id": "scope"},
            "input_artifact_id": "artifact-a", "template_version": "chiral-batch-1",
            "field_mapping": {"structure_ref": "structure_ref"}, "invalid_row_policy": "block",
            "resource_budget": {"max_runtime_seconds": 60, "max_evaluations": 1}, "common_input": {"method_profile_id": "m"},
        })


def test_submit_consumes_confirmation_on_the_caller_session(monkeypatch: pytest.MonkeyPatch) -> None:
    database = SimpleNamespace(flush=lambda: None)
    row = SimpleNamespace(id="batch-1", project_id="project-a", draft_status="READY", revision=1, config_hash="a" * 64, etag="etag", model_selection={})
    confirmation = SimpleNamespace(session=None)

    def consume(*_args, **kwargs):
        confirmation.session = kwargs.get("session")

    confirmation.consume_for_mutation = consume
    service = BatchPredictionService(database, confirmation=confirmation, enabled_batch_types={"gc"})
    monkeypatch.setattr(service, "_get", lambda *_args, **_kwargs: row)
    monkeypatch.setattr(service, "resolve_confirmation_target", lambda *_args, **_kwargs: SimpleNamespace())
    monkeypatch.setattr(service, "validation", lambda *_args, **_kwargs: SimpleNamespace(status="READY", total_rows=1, valid_rows=1, invalid_rows=0, excluded_rows=0))
    monkeypatch.setattr(service, "_audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("chrom_platform.modules.prediction.batch_service.add_job_rows", lambda *_args, **_kwargs: SimpleNamespace(id="job-1", attempt=1, status="QUEUED"))
    ctx = OperationContext(ActorContext(actor_id="actor-a", project_ids=["project-a"], auth_method="service", permissions=["batch:submit"], policy_version="p1"))
    accepted = service.submit(ctx, "batch-1", BatchSubmit(confirmation_id="confirmation-1"))
    assert accepted.job_id == "job-1"
    assert confirmation.session is database


def test_shared_confirmation_resolver_registers_batch_kinds_and_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from chrom_platform.assembly.m2 import assemble_m2

    class Session:
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return None

    confirmation = SimpleNamespace(resolver=None, policy=None)
    calls = []
    monkeypatch.setattr(
        BatchPredictionService, "resolve_confirmation_target",
        lambda _self, _ctx, resource_ref, action: calls.append((resource_ref.type, action)) or "resolved",
    )
    assemble_m2(session_factory=Session, confirmation=confirmation, batch_enabled=True)
    ctx = OperationContext(ActorContext(
        actor_id="actor-a", project_ids=["project-a"], auth_method="service",
        permissions=["*"], policy_version="p1",
    ))
    assert confirmation.resolver(ctx, ResourceRef(type="batch_prediction", id="b1"), "batch.submit") == "resolved"
    assert confirmation.resolver(ctx, ResourceRef(type="batch_retry_plan", id="r1"), "batch.retry_failed") == "resolved"
    assert calls == [("batch_prediction", "batch.submit"), ("batch_retry_plan", "batch.retry_failed")]
    with pytest.raises(TaskError) as exc:
        confirmation.resolver(ctx, ResourceRef(type="unknown_target", id="x1"), "batch.submit")
    assert exc.value.code == "POLICY_UNAVAILABLE"


def _batch_db() -> tuple[object, Session, OperationContext, BatchPredictionRow, Job]:
    engine = create_engine("sqlite:///:memory:")
    event.listen(engine, "connect", lambda dbapi, _: dbapi.create_function("CHAR_LENGTH", 1, lambda value: len(value) if value is not None else None))
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(Project(id="p1", slug="p1", name="P1"))
    session.add(Artifact(id="a1", project_id="p1", kind="dataset", media_type="text/csv", size_bytes=1, storage_key="a1", status="AVAILABLE", scan_status="PASSED", created_by="actor-a"))
    repo = SQLAlchemyTaskRepository(lambda: session)
    job = add_job_rows(repo, session, project_id="p1", owner_id="actor-a", kind="batch_prediction", resource_ref=ResourceRef(type="batch_prediction", id="batch-1"), cancelable=True, retryable=True, resume_mode="restart", deadline_at=None)
    model_snapshot = {"id": "m1", "version": "1", "business_scope_id": "scope", "artifact_sha256": "a" * 64, "input_schema_version": "1", "output_schema_version": "1"}
    row = BatchPredictionRow(id="batch-1", project_id="p1", actor_id="actor-a", chromatography_type="gc", tool="batch", name="b", schema_version="1", model_selection={"mode": "champion", "scope_id": "scope"}, input_artifact_id="a1", template_version="gc-batch-1", field_mapping={"structure_ref": "structure_ref", "temperature_program": "temperature_program"}, common_input={}, invalid_row_policy="block", resource_budget={"max_runtime_seconds": 60, "max_evaluations": 10}, revision=1, config_hash="a" * 64, etag="etag-1", draft_status="READY", job_id=job.id, validation_id="validation-ready", submitted_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc), model_snapshot=model_snapshot, attempt=1, execution_status="QUEUED", counts={"total": 1, "queued": 1, "running": 0, "succeeded": 0, "failed": 0, "skipped": 0, "canceled": 0})
    session.add(row)
    session.add(BatchValidationRow(id="validation-ready", project_id="p1", batch_id=row.id, draft_revision=1, config_hash=row.config_hash, status="READY", template_version="gc-batch-1", model_snapshot=model_snapshot, total_rows=1, valid_rows=1, invalid_rows=0, excluded_rows=0, total_evaluations=1, resource_estimate={}, resolved_input_hash="c" * 64))
    session.flush()
    return engine, session, OperationContext(ActorContext(actor_id="actor-a", project_ids=["p1"], auth_method="service", permissions=["*"], policy_version="p1")), row, job


def test_cancel_uses_shared_lifecycle_outbox_and_deduplicates_request() -> None:
    _engine, session, ctx, row, job = _batch_db()
    service = BatchPredictionService(session, enabled_batch_types={"gc"})
    first = service.cancel(ctx, row.id, BatchCancel(reason="stop"), if_match=row.etag)
    assert first.execution_status == "CANCELED"
    assert session.get(Job, job.id).status == "CANCELED"
    assert len(session.scalars(select(OutboxEvent).where(OutboxEvent.aggregate_id == job.id, OutboxEvent.event_type == "job.cancel")).all()) == 1
    assert len(session.scalars(select(JobEvent).where(JobEvent.job_id == job.id, JobEvent.type == "job.canceled")).all()) == 1
    second = service.cancel(ctx, row.id, BatchCancel(reason="stop-again"), if_match=first.etag)
    assert second.execution_status == "CANCELED"
    assert len(session.scalars(select(JobEvent).where(JobEvent.job_id == job.id, JobEvent.type == "job.canceled")).all()) == 1


def test_batch_service_defaults_to_empty_selected_type_scope() -> None:
    _engine, session, ctx, row, _job = _batch_db()
    with pytest.raises(BatchServiceError) as exc:
        BatchPredictionService(session).get(ctx, row.id)
    assert (exc.value.status_code, exc.value.code) == (503, "BATCH_TYPE_NOT_ENABLED")


def test_running_cancel_stays_requested_and_carries_fencing_token() -> None:
    _engine, session, ctx, row, job = _batch_db()
    repo = SQLAlchemyTaskRepository(lambda: session)
    sync_job_lifecycle(repo, session, job_id=job.id, status=JobStatus.RUNNING, attempt=1)
    attempt = session.scalar(select(JobAttempt).where(JobAttempt.job_id == job.id, JobAttempt.attempt == 1))
    attempt.fencing_token = 9
    row.execution_status = "RUNNING"
    session.flush()
    result = BatchPredictionService(session, enabled_batch_types={"gc"}).cancel(ctx, row.id, BatchCancel(reason="stop"), if_match=row.etag)
    assert result.execution_status == "CANCEL_REQUESTED"
    outbox = session.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_id == job.id, OutboxEvent.event_type == "job.cancel"))
    assert outbox.payload["fencing_token"] == 9


def test_retry_appends_attempt_and_preserves_failed_row_history() -> None:
    _engine, session, ctx, row, job = _batch_db()
    repo = SQLAlchemyTaskRepository(lambda: session)
    sync_job_lifecycle(repo, session, job_id=job.id, status=JobStatus.RUNNING, attempt=1)
    sync_job_lifecycle(repo, session, job_id=job.id, status=JobStatus.FAILED, attempt=1, error={"code": "ADAPTER_FAILED", "message": "bad"}, finished_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc))
    failed = BatchRowResultRow(id="result-1", project_id="p1", batch_id=row.id, validation_id=row.validation_id, row_id="row-1", source_row_number=2, input_snapshot={"structure_ref": "s1"}, result={"old": 1}, error={"code": "ADAPTER_FAILED", "message": "bad"}, warnings=[], attempt=1, status="FAILED")
    session.add(failed)
    from chrom_platform.modules.prediction.batch_service import BatchValidationItemRow, _hash
    session.add(BatchValidationItemRow(id="item-row-1", project_id="p1", validation_id=row.validation_id, row_id="row-1", source_row_number=2, status="VALID", normalized_input_hash=_hash(failed.input_snapshot), resolved_input_snapshot=failed.input_snapshot, resolved_input_hash=_hash(failed.input_snapshot), field_errors=[], warnings=[]))
    row.execution_status, row.attempt, row.etag = "FAILED", 1, _etag({"id": row.id, "revision": row.revision, "status": "FAILED"})
    session.flush()
    confirmation = SimpleNamespace(consume_for_mutation=lambda *_args, **_kwargs: None)
    service = BatchPredictionService(session, confirmation=confirmation, enabled_batch_types={"gc"})
    plan = service.create_retry_plan(ctx, row.id, BatchRetryPlanCreate(row_ids=["row-1"]), if_match=row.etag)
    accepted = service.retry_failed(ctx, row.id, BatchRetryFailed(retry_plan_id=plan.id, confirmation_id="c1"), if_match=row.etag)
    assert accepted.attempt == 2
    results = list(session.scalars(select(BatchRowResultRow).where(BatchRowResultRow.row_id == "row-1").order_by(BatchRowResultRow.attempt)))
    assert len(results) == 2 and results[0].status == "FAILED" and results[0].error["code"] == "ADAPTER_FAILED"
    assert results[1].attempt == 2 and results[1].status == "QUEUED"


def test_failed_idempotency_is_restarted_by_store_instead_of_replayed() -> None:
    from chrom_platform.modules.prediction.batch_router import _reserve

    class Store:
        def __init__(self):
            digest = __import__("hashlib").sha256(__import__("json").dumps({"x": 1}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
            self.record = IdempotencyRecord(payload_hash=digest, status="FAILED")
            self.begun = False
        def get(self, _scope):
            return self.record
        def begin(self, _scope, digest):
            self.begun = True
            return BeginResult(IdempotencyRecord(payload_hash=digest, status="IN_PROGRESS"), True)
    store = Store()
    request = SimpleNamespace(state=SimpleNamespace(idempotency_store=store), app=SimpleNamespace(state=SimpleNamespace()))
    actor = ActorContext(actor_id="a", project_ids=["p"], auth_method="service", permissions=["*"], policy_version="p1")
    _store, _scope, _digest, old = _reserve(request, actor=actor, project_id="p", method="POST", route="/batch", key="k", payload={"x": 1})
    assert store.begun is True and old is None


@pytest.mark.parametrize("reader_name", ["read_batch", "download"])
def test_all_artifact_read_ports_are_guarded_by_service_metadata_validation(reader_name: str) -> None:
    ctx = OperationContext(ActorContext(actor_id="actor-a", project_ids=["p1"], auth_method="service", permissions=["*"], policy_version="p1"))
    artifact = SimpleNamespace(
        get_artifact=lambda *_args: SimpleNamespace(project_id="p1", kind="batch_input", status="ARCHIVED", scan_status="PASSED", media_type="text/csv", download_allowed=True),
        **{reader_name: lambda *_args: b"structure_ref,temperature_program\ns1,{}"},
    )
    with pytest.raises(BatchServiceError) as exc:
        BatchPredictionService(artifact=artifact)._artifact_content(ctx, "a1", project_id="p1")
    assert exc.value.code == "ARTIFACT_NOT_AVAILABLE"


def test_batch_export_status_and_download_are_project_scoped_and_use_batch_artifact():
    _engine, session, ctx, batch, job = _batch_db()
    source = {
        "format_version": "1.0",
        "source": {"draft_revision": 1, "validation_id": batch.validation_id, "attempt": 1},
        "records": [],
    }
    source_hash = _hash(source)
    export = BatchExportRow(
        id="export-1", project_id="p1", batch_id=batch.id, job_id=job.id,
        format="csv", include=["result"], source_snapshot=source,
        source_snapshot_hash=source_hash, status="SUCCEEDED", artifact_id="artifact-export",
    )
    session.add(export)
    body = b"record_id,result\nrow-1,ok\n"
    digest = hashlib.sha256(body).hexdigest()
    session.add(Artifact(
        id="artifact-export", project_id="p1", kind="batch_export",
        media_type="text/csv", size_bytes=len(body), sha256=digest, storage_key="batch/export-1",
        status="AVAILABLE", scan_status="PASSED", created_by="actor-a",
        metadata_json={"source_ref": {"type": "batch_export", "id": "export-1"}},
    ))
    session.flush()
    artifact_port = SimpleNamespace(download=lambda artifact_id, _actor: (
        SimpleNamespace(
            id=artifact_id, project_id="p1", kind="batch_export", status="AVAILABLE",
            scan_status="PASSED", size_bytes=len(body), sha256=digest,
        ), body,
    ))
    service = BatchPredictionService(session, artifact=artifact_port, enabled_batch_types={"gc"})

    view = service.get_export(ctx, export.id)
    content, media_type, filename = service.download_export(ctx, export.id)
    assert view.source_snapshot_hash == source_hash
    assert view.artifact_id == "artifact-export"
    assert (content, media_type, filename) == (body, "text/csv; charset=utf-8", "batch-export-1.csv")

    other_project = OperationContext(ActorContext(
        actor_id="other", project_ids=["p2"], auth_method="service",
        permissions=["*"], policy_version="p1",
    ))
    with pytest.raises(BatchServiceError) as exc:
        service.get_export(other_project, export.id)
    assert exc.value.code == "RESOURCE_NOT_FOUND"


@pytest.mark.parametrize(
    ("failure", "expected_status", "expected_code"),
    [
        (ArtifactServiceError("FORBIDDEN", "forbidden", 403), 403, "FORBIDDEN"),
        (ArtifactServiceError("RESOURCE_NOT_FOUND", "missing", 404), 404, "RESOURCE_NOT_FOUND"),
        (ArtifactServiceError("STATE_CONFLICT", "conflict", 409), 409, "STATE_CONFLICT"),
        (RuntimeError("dependency down"), 503, "DEPENDENCY_UNAVAILABLE"),
    ],
)
def test_batch_export_download_preserves_artifact_errors_and_maps_unknown_failures(
    failure: Exception, expected_status: int, expected_code: str,
) -> None:
    _engine, session, ctx, batch, job = _batch_db()
    source = {
        "format_version": "1.0",
        "source": {"draft_revision": 1, "validation_id": batch.validation_id, "attempt": 1},
        "records": [],
    }
    export = BatchExportRow(
        id="export-errors", project_id="p1", batch_id=batch.id, job_id=job.id,
        format="csv", include=["result"], source_snapshot=source,
        source_snapshot_hash=_hash(source), status="SUCCEEDED", artifact_id="artifact-errors",
    )
    session.add(export)
    session.add(Artifact(
        id="artifact-errors", project_id="p1", kind="batch_export",
        media_type="text/csv", size_bytes=1, storage_key="batch/errors",
        status="AVAILABLE", scan_status="PASSED", created_by="actor-a",
        metadata_json={"source_ref": {"type": "batch_export", "id": export.id}},
    ))
    session.flush()

    def fail_download(_artifact_id: str, _actor: ActorContext) -> object:
        raise failure

    service = BatchPredictionService(session, artifact=SimpleNamespace(download=fail_download), enabled_batch_types={"gc"})
    with pytest.raises(BatchServiceError) as exc:
        service.download_export(ctx, export.id)
    assert (exc.value.status_code, exc.value.code) == (expected_status, expected_code)
