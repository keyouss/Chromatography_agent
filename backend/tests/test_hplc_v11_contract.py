from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import TypeAdapter, ValidationError

from chrom_platform.adapters.hplc.manifest import (
    HPLC_ARTIFACT_ID, HPLC_BUSINESS_SCOPE_ID, HPLC_INPUT_SCHEMA_VERSION,
    HPLC_MODEL_SHA256, HPLC_MODEL_VERSION, HPLC_OUTPUT_SCHEMA_VERSION,
)
from chrom_platform.adapters.hplc.serving_profile import (
    HPLC_SERVING_PROFILE_ID, HPLC_SERVING_PROFILE_SHA256,
)
from chrom_platform.core.dto import ActorContext, CapabilityHealth
from chrom_platform.core.errors import APIError, InputValidationError
from chrom_platform.modules.prediction.adapters import ModelOption, PredictionLimits, StructureCount, ToolCapability
from chrom_platform.modules.prediction.ports import (
    AtomicPredictionCreateResult, IdempotencyLookup, StructureSnapshot,
)
from chrom_platform.modules.prediction.schemas import (
    HplcInput, HplcPredictionCreate, HplcPredictionReadV10,
    ModelSnapshot, PredictionAccepted, PredictionCreate, PredictionExecutionInputSnapshot,
    PredictionStoredRead, validate_hplc_execution_binding,
)
from chrom_platform.modules.prediction.service import (
    PredictionCreateService, PredictionServiceError, _payload_hash,
)
from chrom_platform.task_core.service import OperationContext
from chrom_platform.task_core.state import JobStatus


RAW = ["N[C@@H](C)C(O)=O", "N[C@H](C)C(O)=O"]


def _canonical_pair() -> list[str]:
    from rdkit import Chem

    return [Chem.MolToSmiles(Chem.MolFromSmiles(item), canonical=True, isomericSmiles=True) for item in RAW]


def _selection(**changes):
    value = {
        "mode": "exact", "model_id": HPLC_ARTIFACT_ID, "model_version": HPLC_MODEL_VERSION,
        "preprocessing_profile_id": HPLC_SERVING_PROFILE_ID,
        "preprocessing_profile_sha256": HPLC_SERVING_PROFILE_SHA256,
    }
    value.update(changes)
    return value


def _request(**changes) -> HplcPredictionCreate:
    value = {
        "project_id": "project-a", "chromatography_type": "hplc", "tool": "single",
        "schema_version": HPLC_INPUT_SCHEMA_VERSION, "model_selection": _selection(),
        "structure_refs": ["structure-a", "structure-b"],
        "input": {
            "model_input_smiles": list(RAW), "isopropanol_fraction": 0.1,
            "flow_rate_ml_min": 1.0, "column_ids": ["ADH", "IA"],
        },
    }
    value.update(changes)
    return HplcPredictionCreate.model_validate(value)


def _model(**changes) -> ModelSnapshot:
    value = {
        "id": HPLC_ARTIFACT_ID, "version": HPLC_MODEL_VERSION,
        "business_scope_id": HPLC_BUSINESS_SCOPE_ID, "artifact_sha256": HPLC_MODEL_SHA256,
        "input_schema_version": HPLC_INPUT_SCHEMA_VERSION,
        "output_schema_version": HPLC_OUTPUT_SCHEMA_VERSION,
        "preprocessing_profile_id": HPLC_SERVING_PROFILE_ID,
        "preprocessing_profile_sha256": HPLC_SERVING_PROFILE_SHA256,
    }
    value.update(changes)
    return ModelSnapshot.model_validate(value)


@pytest.mark.parametrize("raw", [[], [RAW[0]], [RAW[0], ""], [RAW[0], "  \t"], [RAW[0], "x" * 20_001]])
def test_hplc_v11_rejects_missing_blank_and_oversized_raw_entries(raw) -> None:
    with pytest.raises(ValidationError):
        HplcInput.model_validate({
            "model_input_smiles": raw, "isopropanol_fraction": 0.1,
            "flow_rate_ml_min": 1.0, "column_ids": ["ADH"],
        })


def test_hplc_preserves_exact_raw_strings_and_other_types_forbid_them() -> None:
    unusual_spacing = [" N[C@@H](C)C(O)=O", "N[C@H](C)C(O)=O\n"]
    request = _request(input={
        "model_input_smiles": unusual_spacing, "isopropanol_fraction": 0.1,
        "flow_rate_ml_min": 1.0, "column_ids": ["ADH"],
    })
    assert request.input.model_input_smiles == unusual_spacing

    cc_payload = {
        "project_id": "project-a", "chromatography_type": "cc", "tool": "single",
        "schema_version": "1.0", "model_selection": {"mode": "champion", "scope_id": "scope-a"},
        "structure_refs": ["a", "b"], "input": {
            "model_input_smiles": RAW, "eluent_system_id": "e", "loading_solvent_id": "l",
            "sample_mass_mg": 1.0, "loading_solvent_volume_ul": 1.0,
            "flow_rate_ml_min": 1.0, "column_spec_id": "c",
        },
    }
    with pytest.raises(ValidationError):
        TypeAdapter(PredictionCreate).validate_python(cc_payload)


def test_create_union_accepts_only_hplc_v11_and_history_read_accepts_v10() -> None:
    legacy = {
        "project_id": "project-a", "chromatography_type": "hplc", "tool": "single",
        "schema_version": "1.0", "model_selection": {"mode": "champion", "scope_id": HPLC_BUSINESS_SCOPE_ID},
        "structure_refs": ["structure-a", "structure-b"],
        "input": {"isopropanol_fraction": 0.1, "flow_rate_ml_min": 1.0, "column_ids": ["ADH"]},
    }
    assert isinstance(TypeAdapter(PredictionStoredRead).validate_python(legacy), HplcPredictionReadV10)
    with pytest.raises(ValidationError):
        TypeAdapter(PredictionCreate).validate_python(legacy)


def test_hplc_v11_schema_exposes_required_bounded_raw_array():
    schema = HplcPredictionCreate.model_json_schema()
    hplc_input = schema["$defs"]["HplcInput"]
    raw = hplc_input["properties"]["model_input_smiles"]
    assert "model_input_smiles" in hplc_input["required"]
    assert raw["minItems"] == raw["maxItems"] == 2
    assert raw["items"]["minLength"] == 1
    assert raw["items"]["maxLength"] == 20_000
    assert hplc_input["additionalProperties"] is False

    request_refs = schema["properties"]["structure_refs"]
    assert request_refs["minItems"] == request_refs["maxItems"] == 2
    assert schema["properties"]["model_selection"]["$ref"] == "#/$defs/ExactModelSelection"


@pytest.mark.parametrize("changes", [
    {"structure_refs": ["structure-a"]},
    {"structure_refs": ["structure-a", "structure-b", "structure-c"]},
    {"model_selection": {"mode": "champion", "scope_id": HPLC_BUSINESS_SCOPE_ID}},
])
def test_hplc_v11_dto_rejects_non_pair_refs_and_champion_selection(changes) -> None:
    payload = {
        "project_id": "project-a", "chromatography_type": "hplc", "tool": "single",
        "schema_version": HPLC_INPUT_SCHEMA_VERSION, "model_selection": _selection(),
        "structure_refs": ["structure-a", "structure-b"],
        "input": {
            "model_input_smiles": list(RAW), "isopropanol_fraction": 0.1,
            "flow_rate_ml_min": 1.0, "column_ids": ["ADH"],
        },
    }
    payload.update(changes)
    with pytest.raises(ValidationError):
        HplcPredictionCreate.model_validate(payload)


def test_hplc_hash_binds_raw_order_refs_and_profile_pair() -> None:
    request = _request()
    base = _payload_hash(request)
    assert _payload_hash(request.model_copy(update={
        "input": request.input.model_copy(update={"model_input_smiles": [RAW[1], RAW[0]]}),
    })) != base
    assert _payload_hash(request.model_copy(update={"structure_refs": ["structure-b", "structure-a"]})) != base
    assert _payload_hash(request.model_copy(update={
        "model_selection": request.model_selection.model_copy(update={"preprocessing_profile_sha256": "c" * 64}),
    })) != base


def test_hplc_execution_binding_rejects_snapshot_drift() -> None:
    model = _model()
    request = _request()
    canonical = _canonical_pair()
    snapshot = PredictionExecutionInputSnapshot(
        structure_refs=request.structure_refs, normalized_structures=canonical,
        input=request.input,
    )
    validate_hplc_execution_binding(request=request, model_snapshot=model, input_snapshot=snapshot)
    changed_input = request.input.model_copy(update={"model_input_smiles": [RAW[1], RAW[0]]})
    with pytest.raises(ValueError, match="do not match"):
        validate_hplc_execution_binding(
            request=request, model_snapshot=model,
            input_snapshot=snapshot.model_copy(update={"input": changed_input}),
        )


class _Access:
    def ensure_project_access(self, _ctx, _project, *, permission):
        if permission != "prediction:create":
            raise AssertionError(permission)


class _Structures:
    def load_many(self, _ctx, project_id, refs):
        canonical = dict(zip(("structure-a", "structure-b"), _canonical_pair()))
        return [StructureSnapshot(ref, project_id, canonical[ref]) for ref in refs]

    def validate_enantiomer_pair(self, _ctx, _project, _structures, *, chromatography_type):
        assert chromatography_type == "hplc"


class _Capabilities:
    def __init__(self):
        self.health = CapabilityHealth(status="READY", checked_at=datetime.now(timezone.utc))
        self.tool = ToolCapability(
            id="single", schema_version="1.1", input_schema={}, output_schema={},
            structure_count=StructureCount(min=2, max=2, semantics="pair"),
            model_constraints={}, units={}, supported_visualizations=[],
            limits=PredictionLimits(max_rows=25, max_candidates=25, max_evaluations=25), health=self.health,
        )

    def get_tool(self, *_args):
        return self.tool

    def validate_request(self, _type, request, *, model_snapshot):
        assert model_snapshot.preprocessing_profile_sha256 == HPLC_SERVING_PROFILE_SHA256
        return request


class _Models:
    def __init__(self, model=None):
        self.model = model or _model()

    def resolve_model(self, _ctx, request, *, tool):
        assert request.schema_version == tool.schema_version
        return self.model


class _Idempotency:
    def lookup(self, *_args, **_kwargs):
        return None


class _Atomic:
    def __init__(self):
        self.call = None

    def create(self, _ctx, request, **kwargs):
        self.call = (request, kwargs)
        return AtomicPredictionCreateResult(PredictionAccepted(
            id="prediction-a", prediction_record_id="record-a", job_id="job-a",
            status=JobStatus.QUEUED, resource_ref={"type": "prediction", "id": "prediction-a"},
        ))


def _context() -> OperationContext:
    return OperationContext(ActorContext(
        actor_id="actor-a", project_ids=["project-a"], auth_method="deployment_default",
        permissions=["prediction:create"], policy_version="policy-1",
    ))


def test_hplc_service_binds_ordered_raw_pair_to_authorized_refs_and_snapshot() -> None:
    atomic = _Atomic()
    service = PredictionCreateService(
        access=_Access(), structures=_Structures(), capabilities=_Capabilities(), models=_Models(),
        idempotency=_Idempotency(), atomic=atomic,
    )
    request = _request()
    service.create(_context(), request, idempotency_key="hplc-v11")
    stored_request, kwargs = atomic.call
    snapshot = kwargs["input_snapshot"]
    assert stored_request.input.model_input_smiles == RAW
    assert snapshot.input.model_input_smiles == RAW
    assert snapshot.structure_refs == ["structure-a", "structure-b"]
    assert snapshot.normalized_structures == _canonical_pair()
    assert snapshot.input.model_input_smiles == request.input.model_input_smiles


def test_hplc_service_rejects_wrong_slot_identity_and_preserves_access_errors() -> None:
    service = PredictionCreateService(
        access=_Access(), structures=_Structures(), capabilities=_Capabilities(), models=_Models(),
        idempotency=_Idempotency(), atomic=_Atomic(),
    )
    request = _request(structure_refs=["structure-b", "structure-a"])
    with pytest.raises(PredictionServiceError) as exc:
        service.create(_context(), request, idempotency_key="wrong-order")
    assert exc.value.payload.code == "HPLC_MODEL_INPUT_IDENTITY_MISMATCH"

    class _Denied(_Access):
        def ensure_project_access(self, *_args, **_kwargs):
            raise APIError(403, "PERMISSION_DENIED", "denied")

    denied = PredictionCreateService(
        access=_Denied(), structures=_Structures(), capabilities=_Capabilities(), models=_Models(),
        idempotency=_Idempotency(), atomic=_Atomic(),
    )
    with pytest.raises(APIError) as denied_error:
        denied.create(_context(), _request(), idempotency_key="denied")
    assert denied_error.value.status_code == 403


def test_hplc_service_rejects_stale_profile_selection() -> None:
    stale = _model(preprocessing_profile_sha256="c" * 64)
    service = PredictionCreateService(
        access=_Access(), structures=_Structures(), capabilities=_Capabilities(), models=_Models(stale),
        idempotency=_Idempotency(), atomic=_Atomic(),
    )
    with pytest.raises(PredictionServiceError) as exc:
        service.create(_context(), _request(), idempotency_key="stale-profile")
    assert exc.value.status_code == 422
    assert exc.value.payload.code == "PREDICTION_INPUT_INVALID"
