from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from typing import Callable

import pytest

from chrom_platform.core.dto import ActorContext, CapabilityHealth, ResourceRef
from chrom_platform.core.errors import APIError, InputValidationError
from chrom_platform.adapters.cc.condition_catalog import CcConditionCatalog
from chrom_platform.adapters.cc.preprocessing_profiles import (
    CC_4G_CANONICAL_PROFILE_ID,
    CC_4G_CANONICAL_PROFILE_SHA256,
)
from chrom_platform.modules.prediction.adapters import (
    ModelOption,
    PredictionLimits,
    StructureCount,
    ToolCapability,
)
from chrom_platform.modules.prediction.ports import (
    AtomicPredictionCreateResult,
    IdempotencyLookup,
    StructureSnapshot,
)
from chrom_platform.modules.prediction.schemas import (
    CcPredictionCreate,
    ChiralLcPredictionCreate,
    ModelSnapshot,
    PredictionAccepted,
    SfcSingleColumnPredictionCreate,
)
from chrom_platform.modules.prediction.service import PredictionCreateService, PredictionServiceError, _payload_hash
from chrom_platform.task_core.service import OperationContext
from chrom_platform.task_core.state import JobStatus


ARTIFACT_SHA = "a" * 64


def ctx(*, interaction: str = "human") -> OperationContext:
    return OperationContext(
        ActorContext(
            actor_id="actor-a",
            tenant_id=None,
            project_ids=["project-a"],
            auth_method="deployment_default",
            permissions=["prediction:create"],
            policy_version="policy-1",
        ),
        interaction=interaction,
        conversation_id="conversation-a",
        agent_run_id="run-a",
    )


def sfc_request() -> SfcSingleColumnPredictionCreate:
    return SfcSingleColumnPredictionCreate.model_validate(
        {
            "project_id": "project-a",
            "chromatography_type": "sfc",
            "tool": "single_column",
            "schema_version": "1.1",
            "model_selection": {"mode": "exact", "model_id": "sfc-model", "model_version": "1"},
            "structure_refs": ["structure-a", "structure-b"],
            "input": {
                "modifier_total_fraction": 0.2,
                "modifier_components": [{"structure_ref": "methanol", "fraction": 1.0}],
                "candidate_scope_id": "conditions-a",
                "top_k": 5,
                "column_id": "column-a",
            },
        }
    )


def chiral_request() -> ChiralLcPredictionCreate:
    return ChiralLcPredictionCreate.model_validate(
        {
            "project_id": "project-a",
            "chromatography_type": "chiral_lc",
            "tool": "single",
            "schema_version": "1.0",
            "model_selection": {"mode": "champion", "scope_id": "scope-a"},
            "structure_refs": ["structure-a"],
            "input": {
                "enantiomer_proposal_id": "proposal-a",
                "conditions": {"method_profile_id": "profile-a"},
            },
        }
    )


def cc_request(**overrides) -> CcPredictionCreate:
    input_data = {
        "eluent_system_id": "cc4g-pe-ea-000-100-v1",
        "loading_solvent_id": "cc4g-loading-dcm-v1",
        "sample_mass_mg": 100.0,
        "loading_solvent_volume_ul": 300.0,
        "flow_rate_ml_min": 10.0,
        "column_spec_id": "silica-4g",
    }
    input_data.update(overrides)
    return CcPredictionCreate.model_validate(
        {
            "project_id": "project-a",
            "chromatography_type": "cc",
            "tool": "single",
            "schema_version": "1.0",
            "model_selection": {
                "mode": "exact", "model_id": "qgeognn_silica_4g", "model_version": "cc-qgeognn-1",
                "preprocessing_profile_id": CC_4G_CANONICAL_PROFILE_ID,
                "preprocessing_profile_sha256": CC_4G_CANONICAL_PROFILE_SHA256,
            },
            "structure_refs": ["structure-a", "structure-b"],
            "input": input_data,
        }
    )


def _validate_cc_request(request):
    catalog = CcConditionCatalog()
    try:
        catalog.resolve_eluent_system(request.input.eluent_system_id)
    except KeyError as exc:
        raise InputValidationError("unsupported eluent system") from exc
    try:
        catalog.resolve_loading_solvent(request.input.loading_solvent_id)
    except KeyError as exc:
        raise InputValidationError("unsupported loading solvent") from exc
    try:
        catalog.validate_numeric_inputs(request.input)
    except ValueError as exc:
        raise InputValidationError(str(exc)) from exc
    return request


def tool(*, type_id: str = "sfc", tool_id: str = "single_column", count: int = 2) -> ToolCapability:
    health = CapabilityHealth(status="READY", checked_at=datetime.now(timezone.utc))
    return ToolCapability(
        id=tool_id,
        schema_version="1.1" if type_id == "sfc" else "1.0",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        structure_count=StructureCount(min=count, max=count, semantics="pair" if count == 2 else "single"),
        model_constraints={"type": type_id},
        units={},
        supported_visualizations=[],
        limits=PredictionLimits(max_rows=100, max_candidates=100, max_evaluations=1000),
        health=health,
    )


MODEL = ModelSnapshot(
    id="sfc-model",
    version="1",
    business_scope_id="scope-a",
    artifact_sha256=ARTIFACT_SHA,
    input_schema_version="1.1",
    output_schema_version="1.0",
)
CC_MODEL = ModelSnapshot(
    id="qgeognn_silica_4g",
    version="cc-qgeognn-1",
    business_scope_id="cc-default",
    artifact_sha256="d6c4c113b2d02b4275b7d1cf3a572ac202d0bd8a1927d0158d0e7ab188ee4c99",
    input_schema_version="1.0",
    output_schema_version="cc-qgeognn-1",
    preprocessing_profile_id=CC_4G_CANONICAL_PROFILE_ID,
    preprocessing_profile_sha256=CC_4G_CANONICAL_PROFILE_SHA256,
)


@dataclass
class FakeAccess:
    events: list[str]
    allowed: bool = True

    def ensure_project_access(self, _ctx, _project_id, *, permission: str) -> None:
        self.events.append("access")
        if not self.allowed:
            raise APIError(403, "PERMISSION_DENIED", "denied")


@dataclass
class FakeStructures:
    events: list[str]
    pair_error: bool = False
    proposal_error: bool = False

    def load_many(self, _ctx, _project_id, structure_refs):
        self.events.append("structures")
        return [StructureSnapshot(ref, "project-a", f"canonical-{ref}") for ref in structure_refs]

    def validate_enantiomer_pair(self, _ctx, _project_id, _structures, *, chromatography_type):
        self.events.append("pair")
        if self.pair_error:
            raise ValueError("pair mismatch")

    def validate_chiral_proposal(self, _ctx, _project_id, _structure, *, proposal_id):
        self.events.append("proposal")
        if self.proposal_error:
            raise ValueError("proposal mismatch")


@dataclass
class FakeCapabilities:
    events: list[str]
    current: ToolCapability | None
    validator: Callable[[object], object] | None = None

    def get_tool(self, chromatography_type, tool_id, schema_version):
        self.events.append("capability")
        if self.current is None or self.current.id != tool_id or schema_version != self.current.schema_version:
            raise InputValidationError("unsupported tool")
        return self.current

    def validate_request(self, _type_id, request, *, model_snapshot):
        self.events.append("preflight")
        return self.validator(request) if self.validator is not None else request


@dataclass
class FakeModels:
    events: list[str]
    model: ModelSnapshot | None = field(default_factory=lambda: MODEL)

    def resolve_model(self, _ctx, _request, *, tool):
        self.events.append("model")
        if self.model is None:
            raise APIError(503, "MODEL_UNAVAILABLE", "missing")
        return self.model


@dataclass
class FakeIdempotency:
    events: list[str]
    records: dict[str, IdempotencyLookup] = field(default_factory=dict)

    def lookup(self, _ctx, _project_id, *, key, payload_hash):
        self.events.append("idempotency")
        return self.records.get(key)


@dataclass
class FakeAtomic:
    events: list[str]
    result: AtomicPredictionCreateResult | None = None
    calls: list[dict] = field(default_factory=list)

    def create(self, _ctx, request, **kwargs):
        self.events.append("atomic")
        self.calls.append({"request": request, **kwargs})
        if self.result is None:
            self.result = AtomicPredictionCreateResult(
                PredictionAccepted(
                    id="prediction-a",
                    prediction_record_id="record-a",
                    job_id="job-a",
                    status=JobStatus.QUEUED,
                    resource_ref=ResourceRef(type="prediction", id="prediction-a"),
                )
            )
        return self.result


def service(*, events=None, **overrides):
    events = events if events is not None else []
    dependencies = {
        "access": FakeAccess(events),
        "structures": FakeStructures(events),
        "capabilities": FakeCapabilities(events, tool()),
        "models": FakeModels(events),
        "idempotency": FakeIdempotency(events),
        "atomic": FakeAtomic(events),
    }
    dependencies.update(overrides)
    return PredictionCreateService(**dependencies), dependencies


def test_access_is_checked_before_idempotency_replay() -> None:
    events: list[str] = []
    accepted = PredictionAccepted(
        id="prediction-old",
        prediction_record_id="record-old",
        job_id="job-old",
        status=JobStatus.QUEUED,
        resource_ref=ResourceRef(type="prediction", id="prediction-old"),
    )
    request = sfc_request()
    idem = FakeIdempotency(events, {"same": IdempotencyLookup(accepted, _payload_hash(request))})
    service_instance, dependencies = service(events=events, idempotency=idem, structures=None, models=None, atomic=None)
    result = service_instance.create(ctx(), request, idempotency_key="same")
    assert result.replayed is True
    assert result.id == "prediction-old"
    assert events == ["access", "idempotency"]
    assert dependencies["atomic"] is None


def test_model_unavailable_is_503_and_does_not_create_job() -> None:
    events: list[str] = []
    models = FakeModels(events, model=None)
    atomic = FakeAtomic(events)
    service_instance, _ = service(events=events, models=models, atomic=atomic)
    with pytest.raises(APIError) as exc_info:
        service_instance.create(ctx(), sfc_request(), idempotency_key="model-missing")
    assert exc_info.value.status_code == 503
    assert exc_info.value.payload.code == "MODEL_UNAVAILABLE"
    assert "atomic" not in events


@pytest.mark.parametrize("error", [KeyError("catalog unavailable"), ValueError("catalog unavailable")])
def test_native_preflight_dependency_errors_are_not_rewritten_as_422(error: Exception) -> None:
    events: list[str] = []

    def fail_validation(_request):
        raise error

    caps = FakeCapabilities(events, tool(type_id="cc", tool_id="single"), fail_validation)
    service_instance, _ = service(
        events=events, capabilities=caps, models=FakeModels(events, CC_MODEL)
    )

    with pytest.raises(type(error)) as exc_info:
        service_instance.create(ctx(), cc_request(), idempotency_key="catalog-failure")

    assert exc_info.value is error
    assert "structures" not in events and "atomic" not in events


def test_tool_or_schema_mismatch_is_422_before_structure_read() -> None:
    events: list[str] = []
    service_instance, _ = service(events=events, capabilities=FakeCapabilities(events, None))
    with pytest.raises(PredictionServiceError) as exc_info:
        service_instance.create(ctx(), sfc_request(), idempotency_key="bad-tool")
    assert exc_info.value.status_code == 422
    assert exc_info.value.payload.code == "PREDICTION_SCHEMA_UNSUPPORTED"
    assert "structures" not in events


def test_pair_validation_and_chiral_proposal_validation_are_service_boundaries() -> None:
    events: list[str] = []
    structures = FakeStructures(events, pair_error=True)
    service_instance, _ = service(events=events, structures=structures)
    with pytest.raises(PredictionServiceError) as pair_error:
        service_instance.create(ctx(), sfc_request(), idempotency_key="bad-pair")
    assert pair_error.value.payload.code == "STRUCTURE_PAIR_INVALID"

    events = []
    chiral_structures = FakeStructures(events, proposal_error=True)
    chiral_caps = FakeCapabilities(events, tool(type_id="chiral_lc", tool_id="single", count=1))
    chiral_service, _ = service(events=events, structures=chiral_structures, capabilities=chiral_caps)
    with pytest.raises(PredictionServiceError) as proposal_error:
        chiral_service.create(ctx(), chiral_request(), idempotency_key="bad-proposal")
    assert proposal_error.value.payload.code == "CHIRAL_PROPOSAL_INVALID"


def test_cc_accepts_two_project_structures_without_enantiomer_validation() -> None:
    events: list[str] = []
    service_instance, _ = service(
        events=events,
        capabilities=FakeCapabilities(events, tool(type_id="cc", tool_id="single"), _validate_cc_request),
        models=FakeModels(events, CC_MODEL),
    )

    result = service_instance.create(ctx(), cc_request(), idempotency_key="cc-distinct-structures")

    assert result.status_code == 202
    assert "structures" in events
    assert "pair" not in events


def test_cc_exact_preprocessing_pair_must_match_returned_model_snapshot() -> None:
    events: list[str] = []
    changed = CC_MODEL.model_copy(update={"preprocessing_profile_sha256": "c" * 64})
    service_instance, _ = service(events=events, models=FakeModels(events, changed))

    with pytest.raises(PredictionServiceError) as exc_info:
        service_instance._resolve_model(ctx(), cc_request(), tool(type_id="cc", tool_id="single"))

    assert exc_info.value.status_code == 422
    assert exc_info.value.payload.code == "PREDICTION_INPUT_INVALID"


def test_non_cc_exact_selection_cannot_smuggle_preprocessing_profile() -> None:
    request_data = sfc_request().model_dump(mode="python")
    request_data["model_selection"] = {
        "mode": "exact", "model_id": MODEL.id, "model_version": MODEL.version,
        "preprocessing_profile_id": CC_4G_CANONICAL_PROFILE_ID,
        "preprocessing_profile_sha256": CC_4G_CANONICAL_PROFILE_SHA256,
    }
    request = SfcSingleColumnPredictionCreate.model_validate(request_data)
    service_instance, _ = service()

    with pytest.raises(PredictionServiceError) as exc_info:
        service_instance._resolve_model(ctx(), request, tool(type_id="sfc", tool_id="single_column"))

    assert exc_info.value.status_code == 422
    assert exc_info.value.payload.code == "PREDICTION_INPUT_INVALID"


def test_atomic_port_receives_bound_snapshots_and_server_source() -> None:
    events: list[str] = []
    atomic = FakeAtomic(events)
    service_instance, _ = service(events=events, atomic=atomic)
    result = service_instance.create(ctx(interaction="assistant"), sfc_request(), idempotency_key="new")
    assert result.status_code == 202
    call = atomic.calls[0]
    assert call["model_snapshot"] == MODEL
    assert call["input_snapshot"].normalized_structures == ["canonical-structure-a", "canonical-structure-b"]
    assert call["input_snapshot"].structure_refs == ["structure-a", "structure-b"]
    assert [item.structure_ref for item in call["input_snapshot"].modifier_structures] == ["methanol"]
    assert call["source"] == "assistant"
    assert call["idempotency_key"] == "new"
    assert events.index("model") < events.index("structures") < events.index("preflight") < events.index("atomic")


def test_sfc_foreign_modifier_is_rejected_before_atomic_create() -> None:
    events: list[str] = []

    class ForeignModifierStructures(FakeStructures):
        def load_many(self, _ctx, _project_id, structure_refs):
            self.events.append("structures")
            return [
                StructureSnapshot(
                    ref,
                    "project-b" if ref == "methanol" else "project-a",
                    "CO" if ref == "methanol" else f"canonical-{ref}",
                )
                for ref in structure_refs
            ]

    atomic = FakeAtomic(events)
    service_instance, _ = service(
        events=events,
        structures=ForeignModifierStructures(events),
        atomic=atomic,
    )
    with pytest.raises(PredictionServiceError) as exc:
        service_instance.create(ctx(), sfc_request(), idempotency_key="foreign-modifier")
    assert exc.value.status_code == 404
    assert exc.value.payload.code == "STRUCTURE_NOT_FOUND"
    assert atomic.calls == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sample_mass_mg", 29.9), ("sample_mass_mg", 327.4),
        ("loading_solvent_volume_ul", 199.0), ("loading_solvent_volume_ul", 2001.0),
        ("flow_rate_ml_min", 4.9), ("flow_rate_ml_min", 10.1),
    ],
)
def test_cc_preflight_rejects_numeric_domain_before_structure_or_atomic(field: str, value: float) -> None:
    events: list[str] = []
    caps = FakeCapabilities(events, tool(type_id="cc", tool_id="single"), _validate_cc_request)
    atomic = FakeAtomic(events)
    idem = FakeIdempotency(events)
    service_instance, _ = service(
        events=events, capabilities=caps, models=FakeModels(events, CC_MODEL), atomic=atomic, idempotency=idem
    )

    with pytest.raises(PredictionServiceError) as exc_info:
        service_instance.create(ctx(), cc_request(**{field: value}), idempotency_key="invalid-cc")

    assert exc_info.value.status_code == 422
    assert exc_info.value.payload.code == "PREDICTION_INPUT_INVALID"
    assert events[-1] == "preflight"
    assert "structures" not in events and "atomic" not in events
    assert not atomic.calls and not idem.records


@pytest.mark.parametrize(
    ("field", "value"),
    [("eluent_system_id", "unknown-system"), ("loading_solvent_id", "unknown-solvent")],
)
def test_cc_preflight_rejects_unknown_condition_ids_before_creation(field: str, value: str) -> None:
    events: list[str] = []
    caps = FakeCapabilities(events, tool(type_id="cc", tool_id="single"), _validate_cc_request)
    atomic = FakeAtomic(events)
    idem = FakeIdempotency(events)
    service_instance, _ = service(
        events=events, capabilities=caps, models=FakeModels(events, CC_MODEL), atomic=atomic, idempotency=idem
    )

    with pytest.raises(PredictionServiceError) as exc_info:
        service_instance.create(ctx(), cc_request(**{field: value}), idempotency_key="unknown-cc")

    assert exc_info.value.status_code == 422
    assert exc_info.value.payload.code == "PREDICTION_INPUT_INVALID"
    assert events[-1] == "preflight"
    assert "structures" not in events and "atomic" not in events
    assert not atomic.calls and not idem.records


def test_cc_unsupported_schema_is_public_input_422_before_side_effects() -> None:
    events: list[str] = []
    atomic = FakeAtomic(events)
    idem = FakeIdempotency(events)
    service_instance, _ = service(
        events=events,
        capabilities=FakeCapabilities(events, tool(type_id="cc", tool_id="single")),
        models=FakeModels(events, CC_MODEL),
        atomic=atomic,
        idempotency=idem,
    )

    with pytest.raises(PredictionServiceError) as exc_info:
        service_instance.create(
            ctx(), cc_request().model_copy(update={"schema_version": "2.0"}), idempotency_key="bad-schema"
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.payload.code == "PREDICTION_INPUT_INVALID"
    assert "structures" not in events and "atomic" not in events
    assert not atomic.calls and not idem.records


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sample_mass_mg", 30.0), ("sample_mass_mg", 327.3),
        ("loading_solvent_volume_ul", 200.0), ("loading_solvent_volume_ul", 2000.0),
        ("flow_rate_ml_min", 5.0), ("flow_rate_ml_min", 10.0),
    ],
)
def test_cc_preflight_accepts_each_numeric_endpoint(field: str, value: float) -> None:
    events: list[str] = []
    caps = FakeCapabilities(events, tool(type_id="cc", tool_id="single"), _validate_cc_request)
    atomic = FakeAtomic(events)
    service_instance, _ = service(
        events=events, capabilities=caps, models=FakeModels(events, CC_MODEL), atomic=atomic
    )

    result = service_instance.create(ctx(), cc_request(**{field: value}), idempotency_key=f"edge-{field}-{value}")

    assert result.status_code == 202
    assert len(atomic.calls) == 1
    assert events.index("model") < events.index("preflight") < events.index("structures") < events.index("atomic")


def test_service_defensively_rejects_duplicate_pair_refs() -> None:
    service_instance, dependencies = service()
    request = sfc_request().model_copy(update={"structure_refs": ["structure-a", "structure-a"]})
    with pytest.raises(PredictionServiceError) as exc_info:
        service_instance.create(ctx(), request, idempotency_key="duplicate-pair")
    assert exc_info.value.status_code == 422
    assert exc_info.value.payload.code == "STRUCTURE_PAIR_INVALID"
    assert "structures" not in dependencies["structures"].events


@pytest.mark.parametrize(
    "accepted",
    [
        PredictionAccepted(
            id="prediction-a",
            prediction_record_id="record-a",
            job_id="job-a",
            status=JobStatus.QUEUED,
            resource_ref=ResourceRef(type="job", id="prediction-a"),
        ),
        PredictionAccepted(
            id="prediction-a",
            prediction_record_id="record-a",
            job_id="job-a",
            status=JobStatus.QUEUED,
            resource_ref=ResourceRef(type="prediction", id="other-prediction"),
        ),
        PredictionAccepted(
            id="prediction-a",
            prediction_record_id="record-a",
            job_id="job-a",
            status=JobStatus.SUCCEEDED,
            resource_ref=ResourceRef(type="prediction", id="prediction-a"),
        ),
        PredictionAccepted(
            id="prediction-a",
            prediction_record_id="record-a",
            job_id="job-a",
            status=JobStatus.FAILED,
            resource_ref=ResourceRef(type="prediction", id="prediction-a"),
        ),
    ],
)
def test_invalid_atomic_accepted_binding_is_503(accepted: PredictionAccepted) -> None:
    atomic = FakeAtomic([], AtomicPredictionCreateResult(accepted))
    service_instance, _ = service(atomic=atomic)
    with pytest.raises(PredictionServiceError) as exc_info:
        service_instance.create(ctx(), sfc_request(), idempotency_key="bad-accepted")
    assert exc_info.value.status_code == 503
    assert exc_info.value.payload.code == "PREDICTION_DEPENDENCY_UNAVAILABLE"


def test_idempotency_conflict_and_replay() -> None:
    request = sfc_request()
    accepted = PredictionAccepted(
        id="prediction-old",
        prediction_record_id="record-old",
        job_id="job-old",
        status=JobStatus.QUEUED,
        resource_ref=ResourceRef(type="prediction", id="prediction-old"),
    )
    events: list[str] = []
    idem = FakeIdempotency(events, {"same": IdempotencyLookup(accepted, "b" * 64)})
    service_instance, _ = service(events=events, idempotency=idem, structures=None, models=None, atomic=None)
    with pytest.raises(PredictionServiceError) as conflict:
        service_instance.create(ctx(), request, idempotency_key="same")
    assert conflict.value.status_code == 409
    assert conflict.value.payload.code == "IDEMPOTENCY_KEY_REUSED"


def test_profileless_exact_hash_is_compatible_with_legacy_absent_pair_and_replays() -> None:
    request_data = sfc_request().model_dump(mode="json")
    request_data["model_selection"] = {
        "mode": "exact", "model_id": "sfc-model", "model_version": "1"
    }
    request = SfcSingleColumnPredictionCreate.model_validate(request_data)
    legacy_payload = request.model_dump(mode="json")
    legacy_selection = legacy_payload["model_selection"]
    assert legacy_selection["preprocessing_profile_id"] is None
    assert legacy_selection["preprocessing_profile_sha256"] is None
    legacy_selection.pop("preprocessing_profile_id")
    legacy_selection.pop("preprocessing_profile_sha256")
    legacy_hash = hashlib.sha256(
        json.dumps(
            legacy_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    assert _payload_hash(request) == legacy_hash

    accepted = PredictionAccepted(
        id="prediction-legacy",
        prediction_record_id="record-legacy",
        job_id="job-legacy",
        status=JobStatus.QUEUED,
        resource_ref=ResourceRef(type="prediction", id="prediction-legacy"),
    )
    events: list[str] = []
    idem = FakeIdempotency(events, {"same-legacy-input": IdempotencyLookup(accepted, legacy_hash)})
    service_instance, _ = service(
        events=events, idempotency=idem, structures=None, models=None, atomic=None
    )
    result = service_instance.create(ctx(), request, idempotency_key="same-legacy-input")
    assert result.replayed is True
    assert result.id == "prediction-legacy"
    assert events == ["access", "idempotency"]
    current_payload = request.model_dump(mode="json")["model_selection"]
    assert current_payload["preprocessing_profile_id"] is None
    assert current_payload["preprocessing_profile_sha256"] is None


def test_idempotency_hash_keeps_real_profile_raw_inputs_and_structure_order() -> None:
    request = cc_request()
    baseline = _payload_hash(request)
    changed_profile = request.model_copy(
        update={
            "model_selection": request.model_selection.model_copy(
                update={"preprocessing_profile_sha256": "c" * 64}
            )
        }
    )
    changed_input = request.model_copy(
        update={"input": request.input.model_copy(update={"sample_mass_mg": 101.0})}
    )
    reordered = request.model_copy(update={"structure_refs": ["structure-b", "structure-a"]})
    assert _payload_hash(changed_profile) != baseline
    assert _payload_hash(changed_input) != baseline
    assert _payload_hash(reordered) != baseline


def test_changed_profile_pair_with_reused_idempotency_key_is_conflict() -> None:
    request = cc_request()
    changed = request.model_copy(
        update={
            "model_selection": request.model_selection.model_copy(
                update={"preprocessing_profile_sha256": "c" * 64}
            )
        }
    )
    accepted = PredictionAccepted(
        id="prediction-profile-old",
        prediction_record_id="record-profile-old",
        job_id="job-profile-old",
        status=JobStatus.QUEUED,
        resource_ref=ResourceRef(type="prediction", id="prediction-profile-old"),
    )
    events: list[str] = []
    idem = FakeIdempotency(
        events,
        {"same-key": IdempotencyLookup(accepted, _payload_hash(request))},
    )
    service_instance, _ = service(
        events=events, idempotency=idem, structures=None, models=None, atomic=None
    )

    with pytest.raises(PredictionServiceError) as exc_info:
        service_instance.create(ctx(), changed, idempotency_key="same-key")

    assert exc_info.value.status_code == 409
    assert exc_info.value.payload.code == "IDEMPOTENCY_KEY_REUSED"


def test_sfc_exact_idempotency_hash_keeps_profileless_selection_shape() -> None:
    request = sfc_request()
    selection = request.model_dump(mode="json")["model_selection"]
    assert selection["mode"] == "exact"
    assert selection["preprocessing_profile_id"] is None
    assert selection["preprocessing_profile_sha256"] is None
    assert _payload_hash(request)


def test_missing_atomic_port_is_503_without_fake_job() -> None:
    service_instance, _ = service(atomic=None)
    with pytest.raises(PredictionServiceError) as exc_info:
        service_instance.create(ctx(), sfc_request(), idempotency_key="no-atomic")
    assert exc_info.value.status_code == 503
    assert exc_info.value.payload.code == "PREDICTION_DEPENDENCY_UNAVAILABLE"
