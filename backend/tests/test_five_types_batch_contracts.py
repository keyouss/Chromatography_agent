from __future__ import annotations

from datetime import datetime, timezone
import pytest
from pydantic import TypeAdapter, ValidationError
from types import SimpleNamespace

from chrom_platform.adapters.cc.adapter import (
    CC_4G_MODEL_ID,
    CC_INPUT_SCHEMA_VERSION,
    CC_MODEL_HASH_BY_ID,
    CC_MODEL_VERSION,
    CC_OUTPUT_SCHEMA_VERSION,
    CcPredictionAdapter,
    _tool_schemas,
)
from chrom_platform.adapters.cc.condition_catalog import CcConditionCatalog
from chrom_platform.adapters.cc.preprocessing_profiles import (
    CC_4G_CANONICAL_PROFILE_ID,
    CC_4G_CANONICAL_PROFILE_SHA256,
)
from chrom_platform.adapters.cc.runtime import CC_SCOPE_ID
from chrom_platform.adapters.gc.adapter import GcPredictionAdapter, _gc_input_schema
from chrom_platform.adapters.gc.manifest import (
    GC_ARTIFACT_ID,
    GC_BUSINESS_SCOPE_ID,
    GC_INPUT_SCHEMA_VERSION,
    GC_MODEL_SHA256,
    GC_MODEL_VERSION,
    GC_OUTPUT_SCHEMA_VERSION,
)
from chrom_platform.adapters.gc.serving_profile import GC_SERVING_PROFILE_ID, GC_SERVING_PROFILE_SHA256
from chrom_platform.core.dto import CapabilityHealth
from chrom_platform.modules.prediction.adapters import (
    AdapterManifest,
    CapabilityRegistry,
    ModelVerificationEvidence,
    issue_verified_model_handle,
)
from chrom_platform.modules.prediction.batch import (
    BatchResourceBudget,
    BatchResolvedInput,
    BatchTemplateDefinition,
    CcBatchResolvedInputV2,
    CcBatchRowV2,
    GcBatchResolvedInputV1,
    HplcBatchResolvedInputV2,
    HplcBatchRowV2,
    SfcBatchResolvedInputV2,
    SfcBatchRowV2,
)
from chrom_platform.modules.prediction.batch_health import build_non_tlc_batch_model_constraints
from chrom_platform.modules.prediction.batch_service import build_batch_template_metadata
from chrom_platform.modules.prediction.schemas import GcPredictionResult, SfcSingleColumnPredictionCreate


def _exact_model(*, profile: bool = False) -> dict[str, object]:
    selection: dict[str, object] = {"mode": "exact", "model_id": "model-v1", "model_version": "1"}
    if profile:
        selection.update(preprocessing_profile_id="profile-v1", preprocessing_profile_sha256="a" * 64)
    return selection


def test_new_batch_rows_preserve_pair_and_explicit_sfc_tool() -> None:
    with pytest.raises(ValidationError):
        CcBatchRowV2.model_validate({
            "structure_ref_a": "same", "structure_ref_b": "same", "eluent_system_id": "eluent",
            "loading_solvent_id": "solvent", "sample_mass_mg": 1, "loading_solvent_volume_ul": 10,
            "flow_rate_ml_min": 1, "column_spec_id": "column",
        })
    hplc = HplcBatchRowV2.model_validate({
        "smiles_a": "N[C@@H](C)O", "smiles_b": "N[C@H](C)O", "isopropanol_fraction": 0.2,
        "flow_rate_ml_min": 1, "column_ids": ["column-a", "column-b"],
    })
    assert (hplc.smiles_a, hplc.smiles_b) == ("N[C@@H](C)O", "N[C@H](C)O")
    base = {
        "structure_ref_a": "a", "structure_ref_b": "b", "modifier_total_fraction": 0.2,
        "modifier_components": [{"structure_ref": "modifier", "fraction": 1}],
        "candidate_scope_id": "scope", "top_k": 1,
    }
    with pytest.raises(ValidationError):
        SfcBatchRowV2.model_validate({**base, "tool": "single_column"})
    with pytest.raises(ValidationError):
        SfcBatchRowV2.model_validate({**base, "tool": "full_column", "column_id": "column"})
    assert SfcBatchRowV2.model_validate({**base, "tool": "single_column", "column_id": "column"}).tool == "single_column"
    assert SfcBatchRowV2.model_validate({**base, "tool": "full_column"}).tool == "full_column"


def test_resolved_batch_input_union_keeps_type_specific_frozen_requests() -> None:
    context = {"project_id": "project-a", "draft_revision": 2, "validation_id": "validation-a"}
    cc = {
        **context, "chromatography_type": "cc", "template_version": "cc-batch-2",
        "request": {
            "project_id": "project-a", "chromatography_type": "cc", "tool": "single", "schema_version": "1.1",
            "model_selection": _exact_model(profile=True), "structure_refs": ["a", "b"],
            "input": {"eluent_system_id": "eluent", "loading_solvent_id": "solvent", "sample_mass_mg": 1,
                      "loading_solvent_volume_ul": 10, "flow_rate_ml_min": 1, "column_spec_id": "column"},
        },
        "normalized_structures": ["C", "N"], "evaluation_count": 2,
    }
    hplc = {
        **context, "chromatography_type": "hplc", "template_version": "hplc-batch-2",
        "request": {
            "project_id": "project-a", "chromatography_type": "hplc", "tool": "single", "schema_version": "1.1",
            "model_selection": _exact_model(profile=True), "structure_refs": ["a", "b"],
            "input": {"model_input_smiles": ["N[C@@H](C)O", "N[C@H](C)O"], "isopropanol_fraction": 0.2,
                      "flow_rate_ml_min": 1, "column_ids": ["column-a", "column-b"]},
        },
        "normalized_structures": ["N[C@@H](C)O", "N[C@H](C)O"], "evaluation_count": 2,
    }
    sfc = {
        **context, "chromatography_type": "sfc", "template_version": "sfc-batch-2",
        "request": {
            "project_id": "project-a", "chromatography_type": "sfc", "tool": "single_column", "schema_version": "1.1",
            "model_selection": _exact_model(), "structure_refs": ["a", "b"],
            "input": {"modifier_total_fraction": 0.2, "modifier_components": [{"structure_ref": "modifier", "fraction": 1}],
                      "candidate_scope_id": "scope", "top_k": 1, "column_id": "column"},
        },
        "normalized_structures": ["C", "N"], "normalized_modifier_structures": {"modifier": "CO"},
        "scope_provenance": {"scope_id": "scope", "candidate_count": 3, "observed_count": 3, "unobserved_count": 0,
                             "policy": {"expansion_mode": "coupled_discrete_conditions", "policy_version": "1",
                                        "candidate_source_sha256": "b" * 64}},
        "evaluation_count": 2,
    }
    gc = {
        **context, "chromatography_type": "gc", "template_version": "gc-batch-1",
        "request": {
            "project_id": "project-a", "chromatography_type": "gc", "tool": "single", "schema_version": "1.1",
            "model_selection": _exact_model(profile=True), "structure_refs": ["a"],
            "input": {"temperature_program": {"mode": "linear", "initial_temperature_c": 80,
                      "final_temperature_c": 120, "heating_rate_c_per_min": 10, "initial_hold_min": 0}},
        },
        "normalized_structures": ["C"], "evaluation_count": 1,
    }
    parsed = [TypeAdapter(BatchResolvedInput).validate_python(value) for value in (cc, hplc, sfc, gc)]
    assert isinstance(parsed[0], CcBatchResolvedInputV2)
    assert isinstance(parsed[1], HplcBatchResolvedInputV2)
    assert parsed[1].request.input.model_input_smiles == ["N[C@@H](C)O", "N[C@H](C)O"]
    assert isinstance(parsed[2], SfcBatchResolvedInputV2)
    assert parsed[2].request.tool == "single_column"
    assert parsed[2].scope_provenance.candidate_count == 3
    assert parsed[2].evaluation_count == 2
    assert isinstance(parsed[3], GcBatchResolvedInputV1)


def test_sfc_capability_keeps_only_each_verified_leaf_tool() -> None:
    ready = CapabilityHealth(status="READY", checked_at=datetime.now(timezone.utc))
    schema = SfcSingleColumnPredictionCreate.model_json_schema()
    output_schema = {"type": "object"}
    model = SimpleNamespace(
        id="sfc-model", version="1", scope="sfc-scope", input_schema_version="1.1",
        output_schema_version="1.0", artifact_sha256="c" * 64,
        preprocessing_profile_id=None, preprocessing_profile_sha256=None, health=ready,
    )
    leaf = SimpleNamespace(
        manifest=SimpleNamespace(
            adapter_id="sfc", data_origin="production", schema_available=True, assets_available=True,
            model_id="sfc-model", model_version="1", input_schema_version="1.1", output_schema_version="1.0",
            artifact_sha256="c" * 64, preprocessing_profile_id=None, preprocessing_profile_sha256=None,
        ),
        verified_handle=SimpleNamespace(
            adapter_id="sfc", model_id="sfc-model", model_version="1", business_scope_id="sfc-scope",
            input_schema_version="1.1", output_schema_version="1.0", artifact_sha256="c" * 64,
            preprocessing_profile_id=None, preprocessing_profile_sha256=None,
            schema_for=lambda tool: {"input_schema": schema, "output_schema": output_schema} if tool == "single_column" else None,
        ),
        runtime=SimpleNamespace(allowed_tool="single_column"),
        capability=SimpleNamespace(tools=[SimpleNamespace(
            id="single_column", health=ready, schema_version="1.1", input_schema=schema, output_schema=output_schema,
        )]),
    )
    composite = SimpleNamespace(binding_for=lambda model_id, version: leaf if (model_id, version) == ("sfc-model", "1") else None)
    registry = CapabilityRegistry()
    registry._capabilities["sfc"] = SimpleNamespace(models=[model], health=ready)
    registry._adapters["sfc"] = composite
    template = BatchTemplateDefinition(
        id="sfc_batch", chromatography_type="sfc", tool="batch", version="sfc-batch-2",
        row_schema="SfcBatchRowV2",
        fields=["structure_ref_a", "structure_ref_b", "modifier_total_fraction", "modifier_components", "candidate_scope_id", "top_k", "tool", "column_id"],
        limits=BatchResourceBudget(max_runtime_seconds=3600, max_evaluations=100_000),
        health=CapabilityHealth(status="UNKNOWN", reason="MODEL_CHECK_DEFERRED", checked_at=datetime.now(timezone.utc)),
    )
    constraints = build_non_tlc_batch_model_constraints("sfc", registry, [template])
    assert len(constraints.compatible_models) == 1
    assert constraints.compatible_models[0].tools == ["single_column"]


def _verified_cc_adapter() -> CcPredictionAdapter:
    catalog = CcConditionCatalog()
    input_schema, output_schema = _tool_schemas(catalog)
    manifest = AdapterManifest(
        adapter_id="cc", model_id=CC_4G_MODEL_ID, model_version=CC_MODEL_VERSION,
        business_scope_id=CC_SCOPE_ID, manifest_version=CC_OUTPUT_SCHEMA_VERSION,
        input_schema_version=CC_INPUT_SCHEMA_VERSION, output_schema_version=CC_OUTPUT_SCHEMA_VERSION,
        artifact_sha256=CC_MODEL_HASH_BY_ID[CC_4G_MODEL_ID],
        preprocessing_profile_id=CC_4G_CANONICAL_PROFILE_ID,
        preprocessing_profile_sha256=CC_4G_CANONICAL_PROFILE_SHA256,
        schema_available=True, assets_available=True, data_origin="production",
    )
    evidence = ModelVerificationEvidence(
        adapter_id="cc", model_id=manifest.model_id, model_version=manifest.model_version,
        business_scope_id=manifest.business_scope_id,
        input_schema_version=manifest.input_schema_version, output_schema_version=manifest.output_schema_version,
        artifact_sha256=manifest.artifact_sha256,
        preprocessing_profile_id=manifest.preprocessing_profile_id,
        preprocessing_profile_sha256=manifest.preprocessing_profile_sha256,
        manifest_verified=True, schema_verified=True, artifact_sha_verified=True,
        environment_verified=True, golden_sample_verified=True, anomaly_checks_verified=True,
        schema_by_tool={"single": {"input_schema": input_schema, "output_schema": output_schema}},
    )
    return CcPredictionAdapter(
        manifest=manifest,
        verified_handle=issue_verified_model_handle(manifest, evidence),
        artifact_sha256=manifest.artifact_sha256,
        condition_catalog=catalog,
    )


def _verified_gc_adapter() -> GcPredictionAdapter:
    manifest = AdapterManifest(
        adapter_id="gc", model_id=GC_ARTIFACT_ID, model_version=GC_MODEL_VERSION,
        business_scope_id=GC_BUSINESS_SCOPE_ID, manifest_version=GC_OUTPUT_SCHEMA_VERSION,
        input_schema_version=GC_INPUT_SCHEMA_VERSION, output_schema_version=GC_OUTPUT_SCHEMA_VERSION,
        artifact_sha256=GC_MODEL_SHA256,
        preprocessing_profile_id=GC_SERVING_PROFILE_ID,
        preprocessing_profile_sha256=GC_SERVING_PROFILE_SHA256,
        schema_available=True, assets_available=True, data_origin="production",
    )
    evidence = ModelVerificationEvidence(
        adapter_id="gc", model_id=manifest.model_id, model_version=manifest.model_version,
        business_scope_id=manifest.business_scope_id,
        input_schema_version=manifest.input_schema_version, output_schema_version=manifest.output_schema_version,
        artifact_sha256=manifest.artifact_sha256,
        preprocessing_profile_id=manifest.preprocessing_profile_id,
        preprocessing_profile_sha256=manifest.preprocessing_profile_sha256,
        manifest_verified=True, schema_verified=True, artifact_sha_verified=True,
        environment_verified=True, golden_sample_verified=True, anomaly_checks_verified=True,
        schema_by_tool={"single": {
            "input_schema": _gc_input_schema(),
            "output_schema": GcPredictionResult.model_json_schema(),
        }},
    )
    return GcPredictionAdapter(
        structure_resolver={}, manifest=manifest,
        verified_handle=issue_verified_model_handle(manifest, evidence),
    )


@pytest.mark.parametrize(
    ("type_id", "adapter_factory"),
    [("cc", _verified_cc_adapter), ("gc", _verified_gc_adapter)],
)
def test_cc_gc_batch_constraints_reuse_real_adapter_schema_generators(type_id, adapter_factory) -> None:
    registry = CapabilityRegistry()
    registry.register(adapter_factory())
    templates = build_batch_template_metadata(chromatography_type=type_id)

    constraints = build_non_tlc_batch_model_constraints(type_id, registry, templates)

    assert len(constraints.compatible_models) == 1
    binding = constraints.compatible_models[0]
    assert binding.health.status == "READY"
    assert binding.template_versions == (["cc-batch-2"] if type_id == "cc" else ["gc-batch-1"])
