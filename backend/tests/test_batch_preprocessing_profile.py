from __future__ import annotations

from types import SimpleNamespace

import pytest

from chrom_platform.core.dto import ActorContext
from chrom_platform.task_core.service import OperationContext
from chrom_platform.modules.prediction.batch import BatchRetryFailed, BatchRetryPlanCreate, BatchSubmit
from chrom_platform.modules.prediction.batch_service import (
    BatchPredictionService,
    BatchServiceError,
    _batch_config_etag,
    _batch_config_hash,
    default_batch_template_registry,
)


PROFILE_ID = "cc-qgeognn-4g-canonical-v1"
PROFILE_SHA256 = "b" * 64


def _service() -> BatchPredictionService:
    model = SimpleNamespace(
        id="qgeognn_silica_4g", version="cc-qgeognn-1", scope="cc-default",
        preprocessing_profile_id=PROFILE_ID, preprocessing_profile_sha256=PROFILE_SHA256,
        health=SimpleNamespace(status="READY"), artifact_sha256="a" * 64,
        input_schema_version="1.0", output_schema_version="cc-qgeognn-1",
        score_semantics=None, threshold=None,
    )
    capability = SimpleNamespace(health=SimpleNamespace(status="READY"), models=[model])
    capabilities = SimpleNamespace(capability=lambda _type: capability)
    return BatchPredictionService(capabilities=capabilities)


def _payload(*, profile_id: str | None = PROFILE_ID, profile_sha256: str | None = PROFILE_SHA256):
    return SimpleNamespace(
        chromatography_type="cc", schema_version="1.0",
        model_selection=SimpleNamespace(
            mode="exact", model_id="qgeognn_silica_4g", model_version="cc-qgeognn-1",
            scope_id=None, preprocessing_profile_id=profile_id,
            preprocessing_profile_sha256=profile_sha256,
        ),
    )


def test_cc_batch_model_snapshot_carries_exact_profile_pair() -> None:
    snapshot = _service()._resolve_batch_model(None, _payload())

    assert snapshot.preprocessing_profile_id == PROFILE_ID
    assert snapshot.preprocessing_profile_sha256 == PROFILE_SHA256


@pytest.mark.parametrize(
    "payload",
    [
        _payload(profile_id=None, profile_sha256=None),
        _payload(profile_id=PROFILE_ID, profile_sha256="c" * 64),
    ],
)
def test_cc_batch_requires_current_exact_profile(payload) -> None:
    with pytest.raises(BatchServiceError) as exc_info:
        _service()._resolve_batch_model(None, payload)

    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "PREDICTION_INPUT_INVALID"


def test_non_cc_batch_rejects_preprocessing_profile_fields() -> None:
    service = _service()
    payload = _payload()
    payload.chromatography_type = "tlc"

    with pytest.raises(BatchServiceError) as exc_info:
        service._resolve_batch_model(None, payload)

    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "PREDICTION_INPUT_INVALID"


def test_batch_config_hash_and_etag_ignore_only_absent_or_null_default_profile_pair() -> None:
    legacy = {
        "chromatography_type": "tlc",
        "model_selection": {"mode": "exact", "model_id": "tlc-model", "model_version": "1"},
        "ordered_refs": ["structure-a", "structure-b"],
    }
    explicit_null = {
        **legacy,
        "model_selection": {
            **legacy["model_selection"],
            "preprocessing_profile_id": None,
            "preprocessing_profile_sha256": None,
        },
    }
    before = dict(explicit_null["model_selection"])

    assert _batch_config_hash(legacy) == _batch_config_hash(explicit_null)
    assert _batch_config_etag(resource_id="batch-a", revision=1, config=legacy) == _batch_config_etag(
        resource_id="batch-a", revision=1, config=explicit_null
    )
    assert explicit_null["model_selection"] == before
    assert "preprocessing_profile_id" in explicit_null["model_selection"]


def test_batch_config_hash_keeps_real_profile_pair_and_ordered_inputs() -> None:
    base = {
        "chromatography_type": "cc",
        "model_selection": {
            "mode": "exact", "model_id": "cc-model", "model_version": "1",
            "preprocessing_profile_id": PROFILE_ID,
            "preprocessing_profile_sha256": PROFILE_SHA256,
        },
        "ordered_refs": ["structure-a", "structure-b"],
    }
    changed_pair = {
        **base,
        "model_selection": {
            **base["model_selection"],
            "preprocessing_profile_sha256": "c" * 64,
        },
    }
    reordered = {**base, "ordered_refs": ["structure-b", "structure-a"]}

    assert _batch_config_hash(base) != _batch_config_hash(changed_pair)
    assert _batch_config_hash(base) != _batch_config_hash(reordered)


def test_non_cc_champion_selection_without_profile_attributes_is_supported() -> None:
    model = SimpleNamespace(
        id="tlc-model", version="1", scope="scope-a", preprocessing_profile_id=None,
        preprocessing_profile_sha256=None, health=SimpleNamespace(status="READY"),
        artifact_sha256="a" * 64, input_schema_version="1.0", output_schema_version="1.0",
        score_semantics=None, threshold=None,
    )
    capability = SimpleNamespace(health=SimpleNamespace(status="READY"), models=[model])
    service = BatchPredictionService(
        capabilities=SimpleNamespace(capability=lambda _type: capability)
    )
    payload = SimpleNamespace(
        chromatography_type="tlc", schema_version="1.0",
        model_selection=SimpleNamespace(mode="champion", scope_id="scope-a"),
    )

    snapshot = service._resolve_batch_model(None, payload)

    assert snapshot.id == "tlc-model"
    assert snapshot.preprocessing_profile_id is None
    assert snapshot.preprocessing_profile_sha256 is None


class _NoWriteSession:
    def __init__(self, *, scalar_result=None):
        self.scalar_result = scalar_result
        self.added = []
        self.flush_count = 0

    def scalar(self, *_args, **_kwargs):
        return self.scalar_result

    def add(self, value):
        self.added.append(value)

    def flush(self):
        self.flush_count += 1


def _batch_ctx(*permissions: str) -> OperationContext:
    return OperationContext(ActorContext(
        actor_id="actor-a", project_ids=["project-a"], auth_method="service",
        permissions=list(permissions), policy_version="p1",
    ))


def _cc_batch_row(**overrides):
    values = {
        "id": "batch-cc", "project_id": "project-a", "chromatography_type": "cc",
        "tool": "batch", "template_version": "cc-batch-1", "etag": '"batch-v1"',
        "submitted_at": None, "draft_status": "READY", "revision": 1,
        "config_hash": "a" * 64, "job_id": "job-existing", "validation_id": "validation-ready",
        "attempt": 1, "execution_status": "FAILED",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _assert_cc_batch_unavailable(call) -> None:
    with pytest.raises(BatchServiceError) as exc_info:
        call()
    assert (exc_info.value.status_code, exc_info.value.code) == (503, "MODEL_UNAVAILABLE")


def test_cc_batch_template_is_unavailable_without_downgrading_other_templates() -> None:
    templates = {item.chromatography_type: item for item in default_batch_template_registry().list()}

    assert templates["cc"].health.status == "UNAVAILABLE"
    assert templates["cc"].health.reason == "CC_BATCH_EXECUTION_UNVERIFIED"
    assert templates["hplc"].health.status == "UNAVAILABLE"
    assert templates["hplc"].health.reason == "HPLC_BATCH_RAW_INPUT_UNAVAILABLE"
    assert all(templates[name].health.status == "UNKNOWN" for name in ("tlc", "sfc", "gc"))


def test_cc_validate_gate_runs_after_permission_and_etag_before_any_job_write(monkeypatch) -> None:
    row = _cc_batch_row()
    db = _NoWriteSession()
    service = BatchPredictionService(db)
    monkeypatch.setattr(service, "_get", lambda *_args, **_kwargs: row)
    monkeypatch.setattr(service, "_payload_from_row", lambda *_args: pytest.fail("gate must precede validation work"))

    _assert_cc_batch_unavailable(lambda: service.validate(_batch_ctx("batch:validate"), row.id, if_match=row.etag))
    assert db.added == [] and db.flush_count == 0

    with pytest.raises(BatchServiceError) as denied:
        service.validate(_batch_ctx(), row.id, if_match=row.etag)
    assert (denied.value.status_code, denied.value.code) == (403, "PERMISSION_DENIED")
    with pytest.raises(BatchServiceError) as stale:
        service.validate(_batch_ctx("batch:validate"), row.id, if_match='"stale"')
    assert (stale.value.status_code, stale.value.code) == (412, "ETAG_MISMATCH")


def test_cc_submit_gate_does_not_consume_confirmation_or_create_job(monkeypatch) -> None:
    row = _cc_batch_row()
    db = _NoWriteSession()
    consumed = []
    confirmation = SimpleNamespace(consume_for_mutation=lambda *args, **kwargs: consumed.append((args, kwargs)))
    service = BatchPredictionService(db, confirmation=confirmation)
    monkeypatch.setattr(service, "_get", lambda *_args, **_kwargs: row)
    monkeypatch.setattr(service, "validation", lambda *_args, **_kwargs: SimpleNamespace(
        status="READY", total_rows=1, valid_rows=1, invalid_rows=0, excluded_rows=0,
    ))
    monkeypatch.setattr(service, "resolve_confirmation_target", lambda *_args, **_kwargs: pytest.fail("confirmation target must not be resolved"))
    monkeypatch.setattr("chrom_platform.modules.prediction.batch_service.add_job_rows", lambda *_args, **_kwargs: pytest.fail("job must not be created"))

    _assert_cc_batch_unavailable(lambda: service.submit(
        _batch_ctx("batch:submit"), row.id, BatchSubmit(confirmation_id="confirmation-1")
    ))
    assert consumed == [] and db.added == [] and db.flush_count == 0


def test_cc_worker_validation_gate_blocks_preexisting_queued_validation(monkeypatch) -> None:
    row = _cc_batch_row(draft_status="VALIDATING")
    validation = SimpleNamespace(
        id="validation-queued", batch_id=row.id, draft_revision=1,
        config_hash=row.config_hash, status="QUEUED",
    )
    db = _NoWriteSession(scalar_result=validation)
    service = BatchPredictionService(db, artifact=SimpleNamespace(get_artifact=lambda *_: pytest.fail("artifact must not be read")))
    monkeypatch.setattr(service, "_get", lambda *_args, **_kwargs: row)

    _assert_cc_batch_unavailable(lambda: service.run_validation(_batch_ctx("*"), validation.id))
    assert validation.status == "QUEUED"
    assert db.added == [] and db.flush_count == 0


def test_cc_retry_plan_and_retry_gate_before_plan_or_confirmation_side_effects(monkeypatch) -> None:
    row = _cc_batch_row()
    plan = SimpleNamespace(id="retry-plan", revision=1, config_hash="b" * 64, etag='"plan-v1"')
    db = _NoWriteSession(scalar_result=plan)
    consumed = []
    confirmation = SimpleNamespace(consume_for_mutation=lambda *args, **kwargs: consumed.append((args, kwargs)))
    service = BatchPredictionService(db, confirmation=confirmation)
    monkeypatch.setattr(service, "_get", lambda *_args, **_kwargs: row)
    monkeypatch.setattr(service, "resolve_confirmation_target", lambda *_args, **_kwargs: pytest.fail("confirmation target must not be resolved"))
    monkeypatch.setattr("chrom_platform.modules.prediction.batch_service.add_job_attempt_rows", lambda *_args, **_kwargs: pytest.fail("job attempt must not be created"))

    _assert_cc_batch_unavailable(lambda: service.create_retry_plan(
        _batch_ctx("batch:write"), row.id, BatchRetryPlanCreate(row_ids=["row-1"]), if_match=row.etag
    ))
    _assert_cc_batch_unavailable(lambda: service.retry_failed(
        _batch_ctx("batch:submit"), row.id,
        BatchRetryFailed(retry_plan_id=plan.id, confirmation_id="confirmation-1"), if_match=row.etag,
    ))
    assert consumed == [] and db.added == [] and db.flush_count == 0


def test_non_cc_batch_execution_health_is_unchanged() -> None:
    service = BatchPredictionService()
    service._ensure_batch_available(SimpleNamespace(
        chromatography_type="gc", tool="batch", template_version="gc-batch-1",
    ))


def test_hplc_batch_without_raw_inputs_is_fail_closed() -> None:
    templates = {item.chromatography_type: item for item in default_batch_template_registry().list()}
    assert templates["hplc"].fields == [
        "structure_ref_a", "structure_ref_b", "isopropanol_fraction", "flow_rate_ml_min", "column_ids",
    ]
    with pytest.raises(BatchServiceError) as exc_info:
        BatchPredictionService()._ensure_batch_available(SimpleNamespace(
            chromatography_type="hplc", tool="batch", template_version="hplc-batch-1",
        ))
    assert (exc_info.value.status_code, exc_info.value.code) == (503, "MODEL_UNAVAILABLE")
