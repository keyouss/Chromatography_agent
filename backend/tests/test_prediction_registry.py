from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from chrom_platform.core.dto import CapabilityHealth
from chrom_platform.core.errors import InputValidationError
from chrom_platform.modules.prediction.adapters import (
    AdapterCapability,
    AdapterManifest,
    CapabilityRegistry,
    ModelOption,
    ModelUnavailableError,
    ModelVerificationEvidence,
    PredictionContractError,
    PredictionLimits,
    StructureCount,
    ToolCapability,
    VerifiedModelHandle,
    issue_verified_model_handle,
)
from chrom_platform.modules.prediction.schemas import (
    ModelSnapshot,
    SfcFullColumnPredictionCreate,
    SfcSingleColumnPredictionCreate,
)
from chrom_platform.assembly.prediction import build_prediction_registry


ARTIFACT_SHA = "a" * 64


def _capability(type_id: str = "sfc") -> AdapterCapability:
    now = datetime.now(timezone.utc)
    health = CapabilityHealth(status="READY", checked_at=now)
    limits = PredictionLimits(max_rows=100, max_candidates=100, max_evaluations=10_000)
    tool = ToolCapability(
        id="single_column",
        schema_version="1.0",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        structure_count=StructureCount(min=2, max=2, semantics="pair"),
        model_constraints={},
        units={},
        supported_visualizations=[],
        limits=limits,
        health=health,
    )
    model = ModelOption(
        id="sfc-model",
        version="1",
        task_type="sfc",
        scope="scope-a",
        artifact_sha256=ARTIFACT_SHA,
        input_schema_version="1.0",
        output_schema_version="1.0",
        health=health,
    )
    return AdapterCapability(
        id=type_id,
        display_name=type_id.upper(),
        tools=[tool],
        models=[model],
        limits=limits,
        health=health,
    )


def _manifest(*, origin: str = "production") -> AdapterManifest:
    return AdapterManifest(
        adapter_id="sfc",
        model_id="sfc-model",
        model_version="1",
        business_scope_id="scope-a",
        manifest_version="1",
        input_schema_version="1.0",
        output_schema_version="1.0",
        artifact_sha256=ARTIFACT_SHA,
        schema_available=True,
        assets_available=True,
        data_origin=origin,
    )


def _evidence(*, schema_by_tool=None) -> ModelVerificationEvidence:
    return ModelVerificationEvidence(
        adapter_id="sfc",
        model_id="sfc-model",
        model_version="1",
        business_scope_id="scope-a",
        input_schema_version="1.0",
        output_schema_version="1.0",
        artifact_sha256=ARTIFACT_SHA,
        manifest_verified=True,
        schema_verified=True,
        artifact_sha_verified=True,
        environment_verified=True,
        golden_sample_verified=True,
        anomaly_checks_verified=True,
        schema_by_tool=schema_by_tool or {
            "single_column": {
                "input_schema": {"type": "object", "title": "verified"},
                "output_schema": {"type": "object", "title": "verified"},
            }
        },
    )


def _handle() -> VerifiedModelHandle:
    evidence = _evidence()
    return issue_verified_model_handle(_manifest(), evidence)


@dataclass
class _Adapter:
    capability: AdapterCapability
    manifest: AdapterManifest | None = None
    verified_handle: VerifiedModelHandle | None = None
    type_id: str = "sfc"
    request_types: tuple[type, ...] = (SfcSingleColumnPredictionCreate, SfcFullColumnPredictionCreate)
    rebind_scope: str | None = None
    predict_calls: int = 0
    validate_calls: int = 0
    validation_error: Exception | None = None
    preflight_calls: int = 0
    preflight_error: Exception | None = None

    def validate_request(self, request):
        self.validate_calls += 1
        if self.validation_error is not None:
            raise self.validation_error
        if self.rebind_scope is not None:
            return type(request).model_validate(
                request.model_dump(mode="python")
                | {"model_selection": {"mode": "champion", "scope_id": self.rebind_scope}}
            )
        return request

    def predict(self, request):
        self.predict_calls += 1
        return request

    def preflight_request(self, request, *, project_id, model_snapshot):
        self.preflight_calls += 1
        if self.preflight_error is not None:
            raise self.preflight_error
        return request


def _valid_request() -> SfcSingleColumnPredictionCreate:
    return SfcSingleColumnPredictionCreate.model_validate(
        {
            "project_id": "project-a",
            "chromatography_type": "sfc",
            "tool": "single_column",
            "schema_version": "1.0",
            "model_selection": {"mode": "champion", "scope_id": "scope-a"},
            "structure_refs": ["a", "b"],
            "input": {
                "modifier_total_fraction": 0.2,
                "modifier_components": [{"structure_ref": "methanol", "fraction": 1.0}],
                "candidate_scope_id": "conditions-a",
                "top_k": 5,
                "column_id": "column-a",
            },
        }
    )


def test_registry_exposes_only_explicitly_registered_adapters() -> None:
    registry = CapabilityRegistry()
    assert registry.registered_types() == ()
    registry.register(_Adapter(capability=_capability()))
    assert registry.registered_types() == ("sfc",)
    assert [item.id for item in registry.capabilities()] == ["sfc"]


def test_universal_assembly_discovers_local_audited_optional_bundles() -> None:
    registry = build_prediction_registry()
    assert {"cc", "gc", "hplc"} <= set(registry.registered_types())
    cc = registry.capability("cc")
    if cc.health.status == "READY":
        assert cc.models[0].id == "qgeognn_silica_4g"
        assert cc.tools[0].model_constraints["eluent_system_ids"] == ["cc4g-pe-ea-000-100-v1"]
        assert cc.tools[0].model_constraints["loading_solvent_ids"] == ["cc4g-loading-dcm-v1"]
        assert cc.tools[0].model_constraints["column_spec_ids"] == ["silica-4g"]
        assert cc.tools[0].model_constraints["numeric_input_ranges"] == {
            "sample_mass_mg": {"minimum": 30.0, "maximum": 327.3, "unit": "mg"},
            "loading_solvent_volume_ul": {"minimum": 200.0, "maximum": 2000.0, "unit": "uL"},
            "flow_rate_ml_min": {"minimum": 5.0, "maximum": 10.0, "unit": "mL/min"},
        }
        assert "not verified joint support or model generalization" in cc.tools[0].model_constraints["numeric_input_range_semantics"]
    else:
        assert cc.health.status == "UNAVAILABLE"
        assert cc.health.reason == "MODEL_VERIFICATION_REQUIRED"
    assert all(registry.capability(item).health.status == "UNAVAILABLE" for item in ("gc", "hplc"))


def test_scope_binding_is_required_and_non_blank_across_public_snapshots() -> None:
    with pytest.raises(ValidationError):
        ModelOption(
            id="sfc-model", version="1", task_type="sfc", scope="",
            artifact_sha256=ARTIFACT_SHA, input_schema_version="1.0",
            output_schema_version="1.0", health=CapabilityHealth(
                status="READY", checked_at=datetime.now(timezone.utc)
            ),
        )
    with pytest.raises(ValidationError):
        ModelOption(
            id="sfc-model", version="1", task_type="sfc", business_scope_id=" ",
            artifact_sha256=ARTIFACT_SHA, input_schema_version="1.0",
            output_schema_version="1.0", health=CapabilityHealth(
                status="READY", checked_at=datetime.now(timezone.utc)
            ),
        )
    with pytest.raises(ValidationError):
        ModelSnapshot(
            id="sfc-model", version="1", business_scope_id=" ",
            artifact_sha256=ARTIFACT_SHA, input_schema_version="1.0",
            output_schema_version="1.0",
        )


def test_missing_manifest_is_unavailable_and_execution_has_safe_503() -> None:
    registry = CapabilityRegistry()
    registry.register(_Adapter(capability=_capability()))
    capability = registry.capability("sfc")
    assert capability.health.status == "UNAVAILABLE"
    assert capability.health.reason == "MODEL_CONTRACT_MISSING"
    with pytest.raises(ModelUnavailableError) as exc_info:
        registry.execute("sfc", _valid_request())
    assert exc_info.value.status_code == 503
    assert exc_info.value.payload.code == "MODEL_UNAVAILABLE"


def test_bare_manifest_never_becomes_ready() -> None:
    registry = CapabilityRegistry()
    registry.register(_Adapter(capability=_capability(), manifest=_manifest()))
    assert registry.capability("sfc").health.status == "UNAVAILABLE"
    assert registry.capability("sfc").health.reason == "MODEL_VERIFICATION_REQUIRED"


@pytest.mark.parametrize("field", ["schema_available", "assets_available"])
def test_incomplete_manifest_never_becomes_ready(field: str) -> None:
    manifest = _manifest().model_copy(update={field: False})
    registry = CapabilityRegistry()
    registry.register(_Adapter(capability=_capability(), manifest=manifest, verified_handle=_handle()))
    assert registry.capability("sfc").health.status == "UNAVAILABLE"
    assert registry.capability("sfc").health.reason == "MODEL_CONTRACT_INCOMPLETE"


def test_fixture_manifest_cannot_issue_a_verified_handle() -> None:
    with pytest.raises(ValueError):
        issue_verified_model_handle(
            _manifest(origin="fixture"),
            ModelVerificationEvidence(
                adapter_id="sfc",
                model_id="sfc-model",
                model_version="1",
                input_schema_version="1.0",
                output_schema_version="1.0",
                artifact_sha256=ARTIFACT_SHA,
                manifest_verified=True,
                schema_verified=True,
                artifact_sha_verified=True,
                environment_verified=True,
                golden_sample_verified=True,
                anomaly_checks_verified=True,
                schema_by_tool={
                    "single_column": {"input_schema": {}, "output_schema": {}}
                },
            ),
        )


def test_verified_handle_binds_schema_and_execute_validates_request() -> None:
    registry = CapabilityRegistry()
    registry.register(_Adapter(capability=_capability(), manifest=_manifest(), verified_handle=_handle()))
    capability = registry.capability("sfc")
    assert capability.health.status == "READY"
    assert capability.tools[0].input_schema["title"] == "verified"
    assert registry.execute("sfc", _valid_request()).tool == "single_column"
    with pytest.raises(PredictionContractError) as exc_info:
        registry.execute("sfc", object())
    assert exc_info.value.status_code == 422

    for invalid in (
        _valid_request().model_copy(update={"schema_version": "2.0"}),
        _valid_request().model_copy(update={"tool": "full_column"}),
        _valid_request().model_copy(update={"chromatography_type": "gc"}),
    ):
        with pytest.raises(PredictionContractError):
            registry.execute("sfc", invalid)


def test_preflight_reuses_execution_binding_without_scientific_prediction() -> None:
    adapter = _Adapter(capability=_capability(), manifest=_manifest(), verified_handle=_handle())
    registry = CapabilityRegistry()
    registry.register(adapter)
    request = _valid_request()
    snapshot = registry.resolve_model(None, request, tool=registry.get_tool("sfc", "single_column", "1.0"))

    assert registry.validate_request("sfc", request, model_snapshot=snapshot) is request
    assert adapter.validate_calls == 1
    assert adapter.predict_calls == 0


def test_preflight_rejects_tool_schema_mismatch_before_adapter_science() -> None:
    adapter = _Adapter(capability=_capability(), manifest=_manifest(), verified_handle=_handle())
    registry = CapabilityRegistry()
    registry.register(adapter)
    request = _valid_request().model_copy(update={"schema_version": "2.0"})
    snapshot = registry.resolve_model(None, _valid_request(), tool=registry.get_tool("sfc", "single_column", "1.0"))

    with pytest.raises(PredictionContractError) as exc_info:
        registry.validate_request("sfc", request, model_snapshot=snapshot)

    assert exc_info.value.status_code == 422
    assert exc_info.value.payload.code == "PREDICTION_INPUT_INVALID"
    assert adapter.validate_calls == 0
    assert adapter.predict_calls == 0


def test_preflight_maps_explicit_adapter_input_error_to_public_422_without_prediction() -> None:
    adapter = _Adapter(
        capability=_capability(), manifest=_manifest(), verified_handle=_handle(),
        validation_error=InputValidationError("outside range"),
    )
    registry = CapabilityRegistry()
    registry.register(adapter)
    request = _valid_request()
    snapshot = registry.resolve_model(None, request, tool=registry.get_tool("sfc", "single_column", "1.0"))

    with pytest.raises(PredictionContractError) as exc_info:
        registry.validate_request("sfc", request, model_snapshot=snapshot)

    assert exc_info.value.status_code == 422
    assert exc_info.value.payload.code == "PREDICTION_INPUT_INVALID"
    assert adapter.validate_calls == 1
    assert adapter.predict_calls == 0


def test_explicit_preflight_preserves_dependency_errors() -> None:
    error = KeyError("catalog backend unavailable")
    adapter = _Adapter(
        capability=_capability(), manifest=_manifest(), verified_handle=_handle(), preflight_error=error
    )
    registry = CapabilityRegistry()
    registry.register(adapter)
    request = _valid_request()
    snapshot = registry.resolve_model(None, request, tool=registry.get_tool("sfc", "single_column", "1.0"))

    with pytest.raises(KeyError) as exc_info:
        registry.validate_request("sfc", request, model_snapshot=snapshot)

    assert exc_info.value is error
    assert adapter.preflight_calls == 1
    assert adapter.predict_calls == 0


@pytest.mark.parametrize("error", [KeyError("dependency unavailable"), ValueError("dependency unavailable"), TypeError("bug")])
def test_preflight_preserves_native_adapter_errors(error: Exception) -> None:
    adapter = _Adapter(
        capability=_capability(), manifest=_manifest(), verified_handle=_handle(), validation_error=error
    )
    registry = CapabilityRegistry()
    registry.register(adapter)
    request = _valid_request()
    snapshot = registry.resolve_model(None, request, tool=registry.get_tool("sfc", "single_column", "1.0"))

    with pytest.raises(type(error)) as exc_info:
        registry.validate_request("sfc", request, model_snapshot=snapshot)

    assert exc_info.value is error
    assert adapter.predict_calls == 0


def test_rebuilt_request_cannot_change_scope_before_predict() -> None:
    adapter = _Adapter(
        capability=_capability(), manifest=_manifest(), verified_handle=_handle(), rebind_scope="other-scope"
    )
    registry = CapabilityRegistry()
    registry.register(adapter)
    with pytest.raises(PredictionContractError):
        registry.execute("sfc", _valid_request())
    assert adapter.predict_calls == 0


def test_verified_handle_is_immutable_and_schema_isolation_is_recursive() -> None:
    handle = _handle()
    schema = handle.schema_for("single_column")
    assert schema is not None
    schema["input_schema"]["nested"] = {"items": ["changed"]}
    schema["output_schema"]["nested"] = {"items": ["changed"]}
    assert handle.schema_for("single_column")["input_schema"] == {"type": "object", "title": "verified"}
    with pytest.raises(TypeError):
        handle._schema_by_tool["single_column"] = {}
    with pytest.raises(TypeError):
        handle._schema_by_tool["single_column"]["input_schema"] = b"changed"
    with pytest.raises(AttributeError):
        handle.business_scope_id = "other-scope"
    with pytest.raises(AttributeError):
        object.__setattr__(handle, "business_scope_id", "other-scope")
    with pytest.raises(AttributeError):
        object.__setattr__(handle, "_schema_by_tool", {})
    assert handle.business_scope_id == "scope-a"


def test_verified_handle_canonicalizes_legal_json_and_rejects_invalid_values() -> None:
    schemas = {
        "single_column": {
            "input_schema": {
                "title": "含 Unicode",
                "required": ["value"],
                "nullable": None,
                "nested": {"enabled": True, "values": [1, 2.5]},
            },
            "output_schema": {"type": "object"},
        }
    }
    handle = issue_verified_model_handle(_manifest(), _evidence(schema_by_tool=schemas))
    assert handle.schema_for("single_column")["input_schema"] == schemas["single_column"]["input_schema"]

    for invalid in (float("nan"), object()):
        with pytest.raises(ValueError):
            issue_verified_model_handle(
                _manifest(),
                _evidence(
                    schema_by_tool={
                        "single_column": {
                            "input_schema": {"invalid": invalid},
                            "output_schema": {"type": "object"},
                        }
                    }
                ),
            )


def test_snapshot_scope_and_execute_binding_are_central() -> None:
    registry = CapabilityRegistry()
    registry.register(_Adapter(capability=_capability(), manifest=_manifest(), verified_handle=_handle()))
    request = _valid_request()
    snapshot = registry.resolve_model(None, request, tool=registry.get_tool("sfc", "single_column", "1.0"))
    assert snapshot.business_scope_id == "scope-a"
    wrong = type(request).model_validate(
        request.model_dump(mode="python")
        | {"model_selection": {"mode": "champion", "scope_id": "other-scope"}}
    )
    with pytest.raises(PredictionContractError):
        registry.execute_snapshot("sfc", wrong, model_snapshot=snapshot)


def test_same_model_binding_with_a_different_scope_is_unavailable() -> None:
    model = _capability().models[0]
    conflicting = _capability().model_copy(
        update={"models": [model.model_copy(update={"scope": "other-scope"})]}
    )
    registry = CapabilityRegistry()
    registry.register(_Adapter(capability=conflicting, manifest=_manifest(), verified_handle=_handle()))
    assert registry.capability("sfc").health.status == "UNAVAILABLE"


def test_duplicate_champion_scope_is_unavailable() -> None:
    model = _capability().models[0]
    duplicate = model.model_copy(update={"id": "other-model"})
    registry = CapabilityRegistry()
    registry.register(
        _Adapter(
            capability=_capability().model_copy(update={"models": [model, duplicate]}),
            manifest=_manifest(),
            verified_handle=_handle(),
        )
    )
    assert registry.capability("sfc").health.status == "UNAVAILABLE"
    assert registry.capability("sfc").health.reason == "MODEL_SCOPE_NOT_UNIQUE"


def test_exact_id_and_version_with_multiple_scopes_is_rejected_as_ambiguous() -> None:
    capability = _capability()
    original = capability.models[0]
    alternate = original.model_copy(update={"scope": "scope-b"})
    registry = CapabilityRegistry()
    registry.register(
        _Adapter(
            capability=capability.model_copy(update={"models": [original, alternate]}),
            manifest=_manifest(),
            verified_handle=_handle(),
        )
    )
    assert registry.capability("sfc").health.status == "UNAVAILABLE"
    request = type(_valid_request()).model_validate(
        _valid_request().model_dump(mode="python")
        | {"model_selection": {"mode": "exact", "model_id": "sfc-model", "model_version": "1"}}
    )
    with pytest.raises(ModelUnavailableError):
        registry.execute("sfc", request)


def test_verified_handle_mismatch_keeps_adapter_unavailable() -> None:
    registry = CapabilityRegistry()
    evidence = ModelVerificationEvidence(
        adapter_id="sfc",
            model_id="other-model",
            model_version="1",
            business_scope_id="scope-a",
        input_schema_version="1.0",
        output_schema_version="1.0",
        artifact_sha256=ARTIFACT_SHA,
        manifest_verified=True,
        schema_verified=True,
        artifact_sha_verified=True,
        environment_verified=True,
        golden_sample_verified=True,
        anomaly_checks_verified=True,
        schema_by_tool={"single_column": {"input_schema": {}, "output_schema": {}}},
    )
    mismatched_manifest = _manifest()
    mismatched_manifest = mismatched_manifest.model_copy(update={"model_id": "other-model"})
    handle = issue_verified_model_handle(mismatched_manifest, evidence)
    registry.register(_Adapter(capability=_capability(), manifest=_manifest(), verified_handle=handle))
    assert registry.capability("sfc").health.status == "UNAVAILABLE"
