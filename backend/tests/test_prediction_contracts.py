from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import TypeAdapter, ValidationError

from chrom_platform.core.dto import CapabilityHealth
from chrom_platform.modules.prediction.adapters import (
    ModelOption,
    SfcAllColumnDomain,
    SfcDomain,
    SfcSingleColumnDomain,
)
from chrom_platform.modules.prediction.batch import (
    BatchDraftCreate,
    SfcBatchDraftCreate,
)
from chrom_platform.modules.prediction.schemas import (
    CcPredictionCreate,
    CcResultRow,
    ChiralLcPredictionCreate,
    ExactModelSelection,
    HplcPredictionCreate,
    ModelSnapshot,
    PredictionCreate,
    PredictionExecutionInputSnapshot,
    PredictionStoredRead,
    SfcPredictionCreate,
    SfcCandidateScopeCreate,
    SfcFullColumnExecutionV10,
    SfcSingleColumnExecutionV10,
    SfcSingleColumnPredictionReadV10,
    SfcSingleColumnPredictionCreate,
    SfcConditionsOptions,
    SfcAllColumnDomain as SchemaSfcAllColumnDomain,
    SfcSingleColumnDomain as SchemaSfcSingleColumnDomain,
    TlcInput,
    TlcConditionComparisonCreate,
    TlcReverseCreate,
    derive_cc_time_seconds,
    build_prediction_execution_request,
)
from chrom_platform.adapters.hplc.manifest import HPLC_ARTIFACT_ID, HPLC_MODEL_VERSION
from chrom_platform.adapters.hplc.serving_profile import HPLC_SERVING_PROFILE_ID, HPLC_SERVING_PROFILE_SHA256


def test_prediction_inputs_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        TlcInput.model_validate(
            {
                "solvent_components": [
                    {"solvent_id": "hexane", "fraction": 0.8},
                    {"solvent_id": "ethyl_acetate", "fraction": 0.2},
                ],
                "rf": 0.3,
            }
        )


def test_preprocessing_profile_pair_is_optional_for_history_but_never_partial() -> None:
    # Old serialized snapshots remain readable and project as an unbound legacy
    # history record; execution gates decide whether that snapshot is runnable.
    old = ModelSnapshot.model_validate({
        "id": "model-a", "version": "1", "business_scope_id": "scope-a",
        "artifact_sha256": "a" * 64, "input_schema_version": "1.0",
        "output_schema_version": "1.0",
    })
    assert old.preprocessing_profile_id is None
    assert old.preprocessing_profile_sha256 is None

    valid = {
        "mode": "exact", "model_id": "model-a", "model_version": "1",
        "preprocessing_profile_id": "profile-v1", "preprocessing_profile_sha256": "b" * 64,
    }
    assert ExactModelSelection.model_validate(valid).preprocessing_profile_sha256 == "b" * 64
    for invalid in (
        {**valid, "preprocessing_profile_sha256": None},
        {key: value for key, value in valid.items() if key != "preprocessing_profile_sha256"},
        {**valid, "preprocessing_profile_sha256": "B" * 64},
    ):
        with pytest.raises(ValidationError):
            ExactModelSelection.model_validate(invalid)


def test_tlc_http_fractions_require_unit_total_and_supported_solvents() -> None:
    value = TlcInput.model_validate(
        {
            "solvent_components": [
                {"solvent_id": "hexane", "fraction": 0.8},
                {"solvent_id": "ethyl_acetate", "fraction": 0.2},
            ]
        }
    )
    assert [item.fraction for item in value.solvent_components] == [0.8, 0.2]
    for fractions in ([2.0, 0.0], [0.2, 0.2], [1.0, 1e-5]):
        with pytest.raises(ValidationError):
            TlcInput.model_validate(
                {
                    "solvent_components": [
                        {"solvent_id": "hexane", "fraction": fractions[0]},
                        {"solvent_id": "ethyl_acetate", "fraction": fractions[1]},
                    ]
                }
            )
    with pytest.raises(ValidationError):
        TlcInput.model_validate(
            {
                "solvent_components": [
                    {"solvent_id": "petroleum_ether", "fraction": 0.8},
                    {"solvent_id": "ethyl_acetate", "fraction": 0.2},
                ]
            }
        )
    with pytest.raises(ValidationError):
        TlcInput.model_validate(
            {"solvent_components": [{"solvent_id": "hexane", "fraction": 0.0}, {"solvent_id": "ethyl_acetate", "fraction": 0.0}]}
        )


def test_tlc_utility_inputs_reuse_the_same_fraction_validator() -> None:
    base = {
        "project_id": "project-a",
        "schema_version": "1.0",
        "model_selection": {"mode": "champion", "scope_id": "scope-a"},
        "structure_ref": "structure-a",
    }
    comparison = TlcConditionComparisonCreate.model_validate(
        {
            **base,
            "conditions": [{"solvent_components": [{"solvent_id": "hexane", "fraction": 0.8}, {"solvent_id": "ethyl_acetate", "fraction": 0.2}]}],
        }
    )
    assert comparison.conditions[0].solvent_components[0].fraction == 0.8
    reverse = TlcReverseCreate.model_validate(
        {
            **base,
            "target_rf_min": 0.2,
            "target_rf_max": 0.8,
            "candidate_system_ids": ["hexane-ethyl_acetate"],
            "ratio_step": 0.1,
            "max_candidates": 10,
        }
    )
    assert reverse.structure_refs == ["structure-a"]
    for payload in (
        {**base, "conditions": [{"solvent_components": [{"solvent_id": "hexane", "fraction": 2.0}, {"solvent_id": "ethyl_acetate", "fraction": 0.0}]}]},
        {**base, "conditions": [{"solvent_components": [{"solvent_id": "hexane", "fraction": 0.2}, {"solvent_id": "ethyl_acetate", "fraction": 0.2}]}]},
    ):
        with pytest.raises(ValidationError):
            TlcConditionComparisonCreate.model_validate(payload)


def test_hplc_pair_and_chiral_lc_single_are_separate_contracts() -> None:
    base = {
        "project_id": "project-a",
        "schema_version": "1.1",
        "model_selection": {
            "mode": "exact", "model_id": HPLC_ARTIFACT_ID, "model_version": HPLC_MODEL_VERSION,
            "preprocessing_profile_id": HPLC_SERVING_PROFILE_ID,
            "preprocessing_profile_sha256": HPLC_SERVING_PROFILE_SHA256,
        },
        "input": {
            "model_input_smiles": ["N[C@@H](C)C(O)=O", "N[C@H](C)C(O)=O"],
            "isopropanol_fraction": 0.1,
            "flow_rate_ml_min": 1.0,
            "column_ids": ["column-a"],
        },
        "chromatography_type": "hplc",
        "tool": "single",
    }
    HplcPredictionCreate.model_validate({**base, "structure_refs": ["a", "b"]})
    with pytest.raises(ValidationError):
        HplcPredictionCreate.model_validate({**base, "structure_refs": ["a"]})
    with pytest.raises(ValidationError):
        HplcPredictionCreate.model_validate({**base, "structure_refs": ["a", "a"]})

    chiral = {
        "project_id": "project-a",
        "schema_version": "1.0",
        "model_selection": {"mode": "champion", "scope_id": "scope-a"},
        "structure_refs": ["a"],
        "chromatography_type": "chiral_lc",
        "tool": "single",
        "input": {
            "enantiomer_proposal_id": "proposal-a",
            "conditions": {"method_profile_id": "profile-a"},
        },
    }
    ChiralLcPredictionCreate.model_validate(chiral)
    with pytest.raises(ValidationError):
        ChiralLcPredictionCreate.model_validate({**chiral, "structure_refs": ["a", "b"]})

    with pytest.raises(ValidationError):
        ChiralLcPredictionCreate.model_validate(
            {**chiral, "input": {"smiles": "C[C@H](O)F"}}
        )


def test_structure_ref_counts_are_locked_for_baseline_tools() -> None:
    common = {
        "project_id": "project-a",
        "schema_version": "1.0",
        "model_selection": {"mode": "champion", "scope_id": "scope-a"},
    }
    cc = {
        **common,
        "chromatography_type": "cc",
        "tool": "single",
        "structure_refs": ["a", "b"],
        "input": {
            "eluent_system_id": "pe-ea",
            "loading_solvent_id": "petroleum-ether",
            "sample_mass_mg": 10.0,
            "loading_solvent_volume_ul": 100.0,
            "flow_rate_ml_min": 1.0,
            "column_spec_id": "silica-4g",
        },
    }
    CcPredictionCreate.model_validate(cc)
    with pytest.raises(ValidationError):
        CcPredictionCreate.model_validate({**cc, "structure_refs": ["a"]})
    with pytest.raises(ValidationError):
        CcPredictionCreate.model_validate({**cc, "structure_refs": ["a", "a"]})
    with pytest.raises(ValidationError):
        CcPredictionCreate.model_validate({**cc, "structure_refs": ["C[C@H](O)F", "b"]})


def test_sfc_tools_have_non_interchangeable_inputs() -> None:
    common = {
        "project_id": "project-a",
        "schema_version": "1.1",
        "model_selection": {"mode": "exact", "model_id": "sfc-model", "model_version": "1"},
        "chromatography_type": "sfc",
        "structure_refs": ["a", "b"],
    }
    single = {
        **common,
        "tool": "single_column",
        "input": {
            "modifier_total_fraction": 0.2,
            "modifier_components": [{"structure_ref": "methanol", "fraction": 1.0}],
            "candidate_scope_id": "conditions-a",
            "top_k": 5,
            "column_id": "column-a",
        },
    }
    assert isinstance(TypeAdapter(SfcPredictionCreate).validate_python(single), SfcSingleColumnPredictionCreate)
    with pytest.raises(ValidationError):
        TypeAdapter(SfcPredictionCreate).validate_python({**single, "input": {**single["input"], "column_id": None}})

    full = {**common, "tool": "full_column", "input": {key: value for key, value in single["input"].items() if key != "column_id"}}
    assert TypeAdapter(SfcPredictionCreate).validate_python(full).tool == "full_column"
    with pytest.raises(ValidationError):
        TypeAdapter(SfcPredictionCreate).validate_python({**full, "input": {**full["input"], "column_id": "column-a"}})
    with pytest.raises(ValidationError):
        TypeAdapter(SfcPredictionCreate).validate_python({**single, "structure_refs": ["a", "a"]})
    with pytest.raises(ValidationError):
        TypeAdapter(SfcPredictionCreate).validate_python({**full, "structure_refs": ["a", "a"]})


def test_sfc_candidate_scope_requires_exact_model_selection() -> None:
    base = {
        "project_id": "project-a",
        "model_selection": {"mode": "exact", "model_id": "sfc-model", "model_version": "1"},
        "axes": {"column": ["AD"]},
    }
    request = SfcCandidateScopeCreate.model_validate(base)
    assert request.model_selection.model_id == "sfc-model"
    assert request.axes == {"column": ["AD"]}

    invalid_selections = (
        {"mode": "champion", "scope_id": "sfc-business-scope"},
        {"mode": "exact", "model_id": "sfc-model", "model_version": "1", "preprocessing_profile_id": "profile-only"},
        {"mode": "exact", "model_id": "sfc-model"},
    )
    for selection in invalid_selections:
        with pytest.raises(ValidationError):
            SfcCandidateScopeCreate.model_validate({**base, "model_selection": selection})


def test_model_option_uses_typed_sfc_domain_without_repurposing_scope_or_threshold() -> None:
    base = {
        "id": "sfc-model",
        "version": "1",
        "task_type": "sfc",
        "scope": "sfc-business-scope",
        "artifact_sha256": "a" * 64,
        "input_schema_version": "1.0",
        "output_schema_version": "1.0",
        "threshold": "1.5",
        "health": {"status": "READY", "checked_at": datetime.now(timezone.utc)},
    }
    single = ModelOption.model_validate({
        **base,
        "sfc_domain": {
            "model_scope": "single_column",
            "group_id": "amylose-coated",
            "group_label": "Amylose coated columns",
            "stationary_phase": "amylose tris (3,5-dimethylphenylcarbamate)",
            "mode": "coated",
            "allowed_columns": ["AD"],
        },
    })
    assert isinstance(single.sfc_domain, SfcSingleColumnDomain)
    assert single.sfc_domain.allowed_columns == ["AD"]
    assert single.sfc_domain.group_id == "amylose-coated"
    assert single.sfc_domain.group_label == "Amylose coated columns"
    assert single.scope == "sfc-business-scope"
    assert single.threshold == "1.5"
    assert single.model_dump(mode="json", by_alias=True)["scope"] == "sfc-business-scope"

    full = ModelOption.model_validate({**base, "sfc_domain": {"model_scope": "all_column"}})
    assert isinstance(full.sfc_domain, SfcAllColumnDomain)
    assert ModelOption.model_validate({**base, "task_type": "gc"}).sfc_domain is None
    assert ModelOption.model_validate({**base, "task_type": "gc", "sfc_domain": None}).sfc_domain is None
    with pytest.raises(ValidationError):
        ModelOption.model_validate({
            **base,
            "task_type": "gc",
            "sfc_domain": {
                "model_scope": "single_column",
                "group_id": "amylose-coated",
                "group_label": "Amylose coated columns",
                "stationary_phase": "amylose tris (3,5-dimethylphenylcarbamate)",
                "mode": "coated",
                "allowed_columns": ["AD"],
            },
        })
    with pytest.raises(ValidationError):
        ModelOption.model_validate({
            **base,
            "sfc_domain": {"model_scope": "single_column", "allowed_columns": ["AD"]},
        })
    with pytest.raises(ValidationError):
        ModelOption.model_validate({
            **base,
            "sfc_domain": {"model_scope": "all_column", "group_id": "amylose-coated"},
        })


def test_sfc_options_reuse_discriminated_domain_schema() -> None:
    assert SfcAllColumnDomain is SchemaSfcAllColumnDomain
    assert SfcSingleColumnDomain is SchemaSfcSingleColumnDomain

    schema = SfcConditionsOptions.model_json_schema()
    domain_schema = schema["properties"]["sfc_domain"]
    assert domain_schema["discriminator"]["propertyName"] == "model_scope"
    assert {item["$ref"].rsplit("/", 1)[-1] for item in domain_schema["oneOf"]} == {
        "SfcAllColumnDomain",
        "SfcSingleColumnDomain",
    }
    assert SfcDomain.__metadata__[0].discriminator == "model_scope"

    base = {
        "scope_id": None,
        "project_id": "project-a",
        "model_id": "model-a",
        "model_version": "1",
        "threshold": "1.2",
        "candidate_count": 65,
        "max_candidates": 1000,
        "fields": [],
    }
    single = {
        "model_scope": "single_column",
        "group_id": "amylose-coated",
        "group_label": "Amylose coated columns",
        "stationary_phase": "amylose tris",
        "mode": "coated",
        "allowed_columns": ["AD"],
    }
    assert SfcConditionsOptions.model_validate({**base, "sfc_domain": single}).sfc_domain.group_id == "amylose-coated"
    all_column = SfcConditionsOptions.model_validate({
        **base,
        "sfc_domain": {"model_scope": "all_column"},
    })
    assert all_column.sfc_domain.model_scope == "all_column"
    for invalid_domain in (
        {"model_scope": "single_column", "allowed_columns": ["AD"]},
        {"model_scope": "all_column", "group_id": "amylose-coated"},
    ):
        with pytest.raises(ValidationError):
            SfcConditionsOptions.model_validate({**base, "sfc_domain": invalid_domain})


def test_prediction_create_schema_and_sfc_union_are_openapi_safe() -> None:
    schema = TypeAdapter(PredictionCreate).json_schema()
    branches = {
        schema["$defs"][ref["$ref"].rsplit("/", 1)[-1]]["properties"]["tool"]["const"]:
        schema["$defs"][ref["$ref"].rsplit("/", 1)[-1]]
        for ref in schema["oneOf"]
        if "$ref" in ref and "Sfc" in ref["$ref"]
    }
    assert set(branches) == {"single_column", "full_column"}
    assert branches["single_column"]["properties"]["input"] == {
        "$ref": "#/$defs/SfcSingleColumnInput"
    }
    assert branches["full_column"]["properties"]["input"] == {
        "$ref": "#/$defs/SfcFullColumnInput"
    }
    for branch in branches.values():
        refs_schema = branch["properties"]["structure_refs"]
        assert refs_schema["minItems"] == refs_schema["maxItems"] == 2
    single = {
        "project_id": "project-a",
        "schema_version": "1.1",
        "model_selection": {"mode": "exact", "model_id": "sfc-model", "model_version": "1"},
        "structure_refs": ["a", "b"],
        "chromatography_type": "sfc",
        "tool": "single_column",
        "input": {
            "modifier_total_fraction": 0.2,
            "modifier_components": [{"structure_ref": "methanol", "fraction": 1.0}],
            "candidate_scope_id": "conditions-a",
            "top_k": 5,
            "column_id": "column-a",
        },
    }
    assert TypeAdapter(PredictionCreate).validate_python(single).tool == "single_column"
    for invalid in (
        {**single, "schema_version": "1.0"},
        {**single, "model_selection": {"mode": "champion", "scope_id": "sfc-scope"}},
    ):
        with pytest.raises(ValidationError):
            TypeAdapter(PredictionCreate).validate_python(invalid)
    assert SfcSingleColumnPredictionReadV10.model_validate({**single, "schema_version": "1.0", "model_selection": {"mode": "champion", "scope_id": "sfc-scope"}}).schema_version == "1.0"


def test_sfc_v10_stored_request_lifts_only_against_frozen_snapshots() -> None:
    raw = {
        "project_id": "project-a", "schema_version": "1.0", "chromatography_type": "sfc",
        "tool": "single_column", "structure_refs": ["structure-a", "structure-b"],
        "model_selection": {"mode": "champion", "scope_id": "scope-a"},
        "input": {
            "modifier_total_fraction": 0.2,
            "modifier_components": [{"structure_ref": "methanol", "fraction": 1.0}],
            "candidate_scope_id": "conditions-a", "top_k": 5, "column_id": "column-a",
        },
    }
    model = ModelSnapshot.model_validate({
        "id": "sfc-model", "version": "1", "business_scope_id": "scope-a", "artifact_sha256": "a" * 64,
        "input_schema_version": "1.0", "output_schema_version": "1.0",
    })
    frozen_input = PredictionExecutionInputSnapshot.model_validate({
        "structure_refs": raw["structure_refs"], "normalized_structures": ["C", "CC"], "input": raw["input"],
    })
    lifted = build_prediction_execution_request(
        raw, project_id="project-a", expected_type="sfc", expected_tool="single_column",
        expected_schema_version="1.0", model_snapshot=model, input_snapshot=frozen_input,
    )
    assert isinstance(lifted, SfcSingleColumnExecutionV10)
    assert lifted.model_selection.model_id == model.id
    assert lifted.schema_version == "1.0"
    assert raw["model_selection"]["mode"] == "champion"  # persisted request remains untouched
    for altered_raw, altered_model, altered_input in (
        (raw, model.model_copy(update={"business_scope_id": "other-scope"}), frozen_input),
        (raw, model, frozen_input.model_copy(update={"structure_refs": ["structure-b", "structure-a"]})),
        ({**raw, "tool": "full_column"}, model, frozen_input),
    ):
        with pytest.raises(ValueError):
            build_prediction_execution_request(
                altered_raw, project_id="project-a", expected_type="sfc", expected_tool="single_column",
                expected_schema_version="1.0", model_snapshot=altered_model, input_snapshot=altered_input,
            )


def test_cc_time_is_a_derived_value_and_does_not_change_volume() -> None:
    assert derive_cc_time_seconds(volume_ml=2.0, flow_rate_ml_min=0.5) == 240.0
    row = CcResultRow(volume_ml=2.0, flow_rate_ml_min=0.5)
    assert row.volume_ml == 2.0
    assert row.time_seconds == 240.0


def test_cc_result_row_accepts_only_a_verified_persisted_computed_time() -> None:
    row = CcResultRow(volume_ml=2.0, flow_rate_ml_min=0.5)
    persisted = row.model_dump(mode="json")

    restored = CcResultRow.model_validate(persisted)
    assert restored.volume_ml == 2.0
    assert restored.flow_rate_ml_min == 0.5
    assert restored.time_seconds == 240.0

    with pytest.raises(ValidationError):
        CcResultRow.model_validate({**persisted, "time_seconds": 241.0})
    with pytest.raises(ValidationError):
        CcResultRow.model_validate({**persisted, "unknown": "rejected"})


def test_batch_union_keeps_sfc_input_type_specific() -> None:
    request = {
        "project_id": "project-a",
        "name": "SFC batch",
        "schema_version": "1.0",
        "model_selection": {"mode": "champion", "scope_id": "scope-a"},
        "input_artifact_id": "artifact-a",
        "template_version": "sfc-batch-1",
        "field_mapping": {"compound_a": "sample name.v1"},
        "invalid_row_policy": "block",
        "resource_budget": {"max_runtime_seconds": 60, "max_evaluations": 10},
        "chromatography_type": "sfc",
        "tool": "batch",
        "common_input": {
            "candidate_scope_id": "conditions-a",
            "top_k": 5,
        },
    }
    parsed = TypeAdapter(BatchDraftCreate).validate_python(request)
    assert isinstance(parsed, SfcBatchDraftCreate)
    with pytest.raises(ValidationError):
        TypeAdapter(BatchDraftCreate).validate_python({**request, "common_input": {"top_k": 5}})
    row_scoped = {
        **request,
        "field_mapping": {
            "structure_ref_a": "enantiomer_a",
            "structure_ref_b": "enantiomer_b",
            "candidate_scope_id": "candidate_scope",
        },
        "common_input": {"top_k": 5},
    }
    assert isinstance(TypeAdapter(BatchDraftCreate).validate_python(row_scoped), SfcBatchDraftCreate)


@pytest.mark.parametrize("column", [" ", "sample\nname", "../smiles", "name;drop table", "name = value"])
def test_batch_mapping_rejects_unsafe_upload_column_names(column: str) -> None:
    request = {
        "project_id": "project-a",
        "name": "SFC batch",
        "schema_version": "1.0",
        "model_selection": {"mode": "champion", "scope_id": "scope-a"},
        "input_artifact_id": "artifact-a",
        "template_version": "sfc-batch-1",
        "field_mapping": {"compound_a": column},
        "invalid_row_policy": "block",
        "resource_budget": {"max_runtime_seconds": 60, "max_evaluations": 10},
        "chromatography_type": "sfc",
        "tool": "batch",
        "common_input": {"candidate_scope_id": "conditions-a", "top_k": 5},
    }
    with pytest.raises(ValidationError):
        TypeAdapter(BatchDraftCreate).validate_python(request)


@pytest.mark.parametrize("column", ["select", "[x]", "sample name.v1", "a/b"])
def test_batch_mapping_preserves_literal_upload_column_names(column: str) -> None:
    request = {
        "project_id": "project-a",
        "name": "SFC batch",
        "schema_version": "1.0",
        "model_selection": {"mode": "champion", "scope_id": "scope-a"},
        "input_artifact_id": "artifact-a",
        "template_version": "sfc-batch-1",
        "field_mapping": {"compound_a": column},
        "invalid_row_policy": "block",
        "resource_budget": {"max_runtime_seconds": 60, "max_evaluations": 10},
        "chromatography_type": "sfc",
        "tool": "batch",
        "common_input": {"candidate_scope_id": "conditions-a", "top_k": 5},
    }
    parsed = TypeAdapter(BatchDraftCreate).validate_python(request)
    assert parsed.field_mapping["compound_a"] == column
