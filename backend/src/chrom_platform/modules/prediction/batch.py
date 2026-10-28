"""Type-specific batch/template contracts.

This file defines metadata and validation DTOs only.  It intentionally has no
spreadsheet parser, file writer, or worker implementation.
"""

from __future__ import annotations

import io
import re
import zipfile
from xml.sax.saxutils import escape
from datetime import datetime
from typing import Annotated, Literal, TypeAlias

from pydantic import Field, field_validator, model_validator

from chrom_platform.core.dto import CapabilityHealth
from chrom_platform.task_core.state import JobStatus

from .schemas import (
    CcPredictionCreate,
    GcPredictionCreate,
    GcTemperatureProgram,
    HplcPredictionCreate,
    Id,
    ModelSelection,
    ModelSnapshot,
    PredictionStrictModel,
    ModifierComponent,
    SfcCandidateScopeProvenance,
    SfcFullColumnPredictionCreate,
    SfcSingleColumnPredictionCreate,
    TlcCandidateSystem,
    TlcInput,
    TlcSolventComponent,
    TlcSolventOption,
    Warning,
)


TargetFieldId = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,95}$")]
UploadColumnName = Annotated[str, Field(min_length=1, max_length=128)]


class BatchResourceBudget(PredictionStrictModel):
    max_runtime_seconds: int = Field(ge=1, le=86_400)
    max_evaluations: int = Field(ge=1, le=10_000_000)


class TlcBatchCommonInput(PredictionStrictModel):
    solvent_components: list[TlcSolventComponent] | None = Field(default=None, min_length=2, max_length=5)

    @field_validator("solvent_components")
    @classmethod
    def validate_solvents(cls, value: list[TlcSolventComponent] | None) -> list[TlcSolventComponent] | None:
        if value is None:
            return None
        ids = [item.solvent_id for item in value]
        if len(ids) != len(set(ids)) or abs(sum(item.fraction for item in value) - 1.0) > 1e-6:
            raise ValueError("batch solvent components must be unique and sum to 1")
        return value


class CcBatchCommonInput(PredictionStrictModel):
    eluent_system_id: Id | None = None
    loading_solvent_id: Id | None = None
    sample_mass_mg: float | None = Field(default=None, gt=0.0)
    loading_solvent_volume_ul: float | None = Field(default=None, gt=0.0)
    flow_rate_ml_min: float | None = Field(default=None, gt=0.0)
    column_spec_id: Id | None = None


class HplcBatchCommonInput(PredictionStrictModel):
    isopropanol_fraction: float | None = Field(default=None, ge=0.0, le=1.0)
    flow_rate_ml_min: float | None = Field(default=None, gt=0.0)
    column_ids: list[Id] | None = Field(default=None, min_length=1)

    @field_validator("column_ids")
    @classmethod
    def validate_columns(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        if len(value) != len(set(value)):
            raise ValueError("batch column_ids must be unique")
        return value


class SfcBatchCommonInput(PredictionStrictModel):
    modifier_total_fraction: float | None = Field(default=None, ge=0.0, le=1.0)
    modifier_components: list[ModifierComponent] | None = Field(default=None, min_length=1, max_length=20)
    candidate_scope_id: Id | None = None
    top_k: int | None = Field(default=None, ge=1, le=200)

    @field_validator("modifier_components")
    @classmethod
    def validate_modifiers(cls, value: list[ModifierComponent] | None) -> list[ModifierComponent] | None:
        if value is None:
            return None
        ids = [item.structure_ref for item in value]
        if len(ids) != len(set(ids)) or abs(sum(item.fraction for item in value) - 1.0) > 1e-6:
            raise ValueError("batch modifier fractions must sum to 1")
        return value


class GcBatchCommonInput(PredictionStrictModel):
    temperature_program: GcTemperatureProgram | None = None


class TlcBatchRow(PredictionStrictModel):
    """One TLC row; fractions are strict normalized values, never ratios."""

    structure_ref: Id
    solvent_components: list[TlcSolventComponent] = Field(min_length=2, max_length=5)

    @field_validator("solvent_components")
    @classmethod
    def validate_solvents(cls, value: list[TlcSolventComponent]) -> list[TlcSolventComponent]:
        ids = [item.solvent_id for item in value]
        if len(ids) != len(set(ids)) or abs(sum(item.fraction for item in value) - 1.0) > 1e-6:
            raise ValueError("TLC batch solvent fractions must sum to 1")
        return value


class TlcBatchRowV2(PredictionStrictModel):
    """TLC batch row with raw structure input and flat binary solvent fields."""

    smiles: Annotated[str, Field(min_length=1, max_length=20_000)]
    solvent_1: Literal["hexane", "ethyl_acetate", "dichloromethane", "methanol", "diethyl_ether"]
    fraction_1: float = Field(ge=0.0, le=1.0)
    solvent_2: Literal["hexane", "ethyl_acetate", "dichloromethane", "methanol", "diethyl_ether"]
    fraction_2: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_binary_solvent_pair(self) -> "TlcBatchRowV2":
        if self.solvent_1 == self.solvent_2:
            raise ValueError("TLC batch requires two different solvents")
        if abs(self.fraction_1 + self.fraction_2 - 1.0) > 1e-6:
            raise ValueError("TLC solvent fractions must sum to 1")
        return self


class TlcBatchResolvedInputV1(PredictionStrictModel):
    """Explicit projection for the legacy structure-reference TLC template."""

    chromatography_type: Literal["tlc"] = "tlc"
    template_version: Literal["tlc-batch-1"]
    project_id: Id
    draft_revision: int = Field(ge=1)
    validation_id: Id
    structure_ref: Id
    solvent_conditions: TlcInput


class TlcBatchResolvedInputV2(PredictionStrictModel):
    """Typed preview for raw-SMILES TLC rows after trusted normalization."""

    chromatography_type: Literal["tlc"] = "tlc"
    template_version: Literal["tlc-batch-2"]
    project_id: Id
    draft_revision: int = Field(ge=1)
    validation_id: Id
    structure_ref: Id
    normalized_smiles: Annotated[str, Field(min_length=1, max_length=20_000)]
    solvent_conditions: TlcInput


TlcBatchResolvedInput = Annotated[
    TlcBatchResolvedInputV1 | TlcBatchResolvedInputV2,
    Field(discriminator="template_version"),
]


class CcBatchRow(PredictionStrictModel):
    structure_ref: Id
    eluent_system_id: Id
    loading_solvent_id: Id
    sample_mass_mg: float = Field(gt=0.0)
    loading_solvent_volume_ul: float = Field(gt=0.0)
    flow_rate_ml_min: float = Field(gt=0.0)
    column_spec_id: Id


class HplcBatchRow(PredictionStrictModel):
    """HPLC keeps the ordered two-structure boundary used by its adapter."""

    structure_ref_a: Id
    structure_ref_b: Id
    isopropanol_fraction: float = Field(ge=0.0, le=1.0)
    flow_rate_ml_min: float = Field(gt=0.0)
    column_ids: list[Id] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_pair(self) -> "HplcBatchRow":
        if self.structure_ref_a == self.structure_ref_b:
            raise ValueError("HPLC requires two distinct structures")
        return self

    @field_validator("column_ids")
    @classmethod
    def validate_columns(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("batch column_ids must be unique")
        return value


class SfcBatchRow(PredictionStrictModel):
    """SFC is always an enantiomer pair; a column selects the single-column tool."""

    structure_ref_a: Id
    structure_ref_b: Id
    modifier_total_fraction: float = Field(ge=0.0, le=1.0)
    modifier_components: list[ModifierComponent] = Field(min_length=1, max_length=20)
    candidate_scope_id: Id
    top_k: int = Field(ge=1, le=200)
    column_id: Id | None = None

    @model_validator(mode="after")
    def validate_pair(self) -> "SfcBatchRow":
        if self.structure_ref_a == self.structure_ref_b:
            raise ValueError("SFC requires two distinct structures")
        return self

    @field_validator("modifier_components")
    @classmethod
    def validate_modifiers(cls, value: list[ModifierComponent]) -> list[ModifierComponent]:
        ids = [item.structure_ref for item in value]
        if len(ids) != len(set(ids)) or abs(sum(item.fraction for item in value) - 1.0) > 1e-6:
            raise ValueError("batch modifier fractions must sum to 1")
        return value


class GcBatchRow(PredictionStrictModel):
    structure_ref: Id
    temperature_program: GcTemperatureProgram


BatchRow: TypeAlias = TlcBatchRow | CcBatchRow | HplcBatchRow | SfcBatchRow | GcBatchRow


class CcBatchRowV2(PredictionStrictModel):
    structure_ref_a: Id
    structure_ref_b: Id
    eluent_system_id: Id
    loading_solvent_id: Id
    sample_mass_mg: float = Field(gt=0.0)
    loading_solvent_volume_ul: float = Field(gt=0.0)
    flow_rate_ml_min: float = Field(gt=0.0)
    column_spec_id: Id

    @model_validator(mode="after")
    def validate_pair(self) -> "CcBatchRowV2":
        if self.structure_ref_a == self.structure_ref_b:
            raise ValueError("CC requires two distinct structures")
        return self


class HplcBatchRowV2(PredictionStrictModel):
    smiles_a: Annotated[str, Field(min_length=1, max_length=20_000)]
    smiles_b: Annotated[str, Field(min_length=1, max_length=20_000)]
    isopropanol_fraction: float = Field(ge=0.0, le=1.0)
    flow_rate_ml_min: float = Field(gt=0.0)
    column_ids: list[Id] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_pair(self) -> "HplcBatchRowV2":
        if self.smiles_a == self.smiles_b:
            raise ValueError("HPLC requires two distinct ordered structures")
        return self

    @field_validator("column_ids")
    @classmethod
    def validate_columns(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("batch column_ids must be unique")
        return value


class SfcBatchRowV2(PredictionStrictModel):
    structure_ref_a: Id
    structure_ref_b: Id
    modifier_total_fraction: float = Field(ge=0.0, le=1.0)
    modifier_components: list[ModifierComponent] = Field(min_length=1, max_length=20)
    candidate_scope_id: Id
    top_k: int = Field(ge=1, le=200)
    tool: Literal["single_column", "full_column"]
    column_id: Id | None = None

    @model_validator(mode="after")
    def validate_row(self) -> "SfcBatchRowV2":
        if self.structure_ref_a == self.structure_ref_b:
            raise ValueError("SFC requires two distinct structures")
        if (self.tool == "single_column") != (self.column_id is not None):
            raise ValueError("SFC single_column requires column_id and full_column forbids it")
        ids = [item.structure_ref for item in self.modifier_components]
        if len(ids) != len(set(ids)) or abs(sum(item.fraction for item in self.modifier_components) - 1.0) > 1e-6:
            raise ValueError("SFC modifier fractions must sum to 1 with unique structures")
        return self


class CcBatchResolvedInputV2(PredictionStrictModel):
    chromatography_type: Literal["cc"] = "cc"
    template_version: Literal["cc-batch-2"]
    project_id: Id
    draft_revision: int = Field(ge=1)
    validation_id: Id
    request: CcPredictionCreate
    normalized_structures: list[Annotated[str, Field(min_length=1, max_length=20_000)]] = Field(min_length=2, max_length=2)
    evaluation_count: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_frozen_request(self) -> "CcBatchResolvedInputV2":
        if (self.request.project_id != self.project_id or self.request.model_selection.mode != "exact"
                or len(self.request.structure_refs) != len(self.normalized_structures)):
            raise ValueError("CC batch request must match its frozen row context")
        if self.evaluation_count != 2:
            raise ValueError("CC batch evaluates two structures per row")
        return self


class HplcBatchResolvedInputV2(PredictionStrictModel):
    chromatography_type: Literal["hplc"] = "hplc"
    template_version: Literal["hplc-batch-2"]
    project_id: Id
    draft_revision: int = Field(ge=1)
    validation_id: Id
    request: HplcPredictionCreate
    normalized_structures: list[Annotated[str, Field(min_length=1, max_length=20_000)]] = Field(min_length=2, max_length=2)
    evaluation_count: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_frozen_request(self) -> "HplcBatchResolvedInputV2":
        if (self.request.project_id != self.project_id
                or self.request.model_selection.preprocessing_profile_id is None
                or self.request.model_selection.preprocessing_profile_sha256 is None
                or len(self.request.input.model_input_smiles) != 2):
            raise ValueError("HPLC batch request must preserve its ordered raw pair and project")
        if self.evaluation_count != len(self.request.input.column_ids):
            raise ValueError("HPLC batch evaluation_count must equal the selected column count")
        return self


class SfcBatchResolvedInputV2(PredictionStrictModel):
    chromatography_type: Literal["sfc"] = "sfc"
    template_version: Literal["sfc-batch-2"]
    project_id: Id
    draft_revision: int = Field(ge=1)
    validation_id: Id
    request: SfcSingleColumnPredictionCreate | SfcFullColumnPredictionCreate
    normalized_structures: list[Annotated[str, Field(min_length=1, max_length=20_000)]] = Field(min_length=2, max_length=2)
    normalized_modifier_structures: dict[Id, Annotated[str, Field(min_length=1, max_length=20_000)]] = Field(min_length=1, max_length=20)
    scope_provenance: SfcCandidateScopeProvenance | None = None
    evaluation_count: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_frozen_request(self) -> "SfcBatchResolvedInputV2":
        if self.request.project_id != self.project_id or self.request.model_selection.mode != "exact":
            raise ValueError("SFC batch request must match its frozen project")
        if self.scope_provenance is not None and self.scope_provenance.scope_id != self.request.input.candidate_scope_id:
            raise ValueError("SFC batch scope provenance must match the typed request")
        refs = {item.structure_ref for item in self.request.input.modifier_components}
        if set(self.normalized_modifier_structures) != refs:
            raise ValueError("SFC batch modifier structures must match the typed request")
        return self


class GcBatchResolvedInputV1(PredictionStrictModel):
    chromatography_type: Literal["gc"] = "gc"
    template_version: Literal["gc-batch-1"]
    project_id: Id
    draft_revision: int = Field(ge=1)
    validation_id: Id
    request: GcPredictionCreate
    normalized_structures: list[Annotated[str, Field(min_length=1, max_length=20_000)]] = Field(min_length=1, max_length=1)
    evaluation_count: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_frozen_request(self) -> "GcBatchResolvedInputV1":
        if self.request.project_id != self.project_id or self.evaluation_count != 1:
            raise ValueError("GC batch request must match one frozen structure and project")
        return self


NonTlcBatchResolvedInput: TypeAlias = Annotated[
    CcBatchResolvedInputV2 | HplcBatchResolvedInputV2 | SfcBatchResolvedInputV2 | GcBatchResolvedInputV1,
    Field(discriminator="chromatography_type"),
]
BatchResolvedInput: TypeAlias = TlcBatchResolvedInput | NonTlcBatchResolvedInput


class TlcBatchTemplateOptions(PredictionStrictModel):
    """Actual TLC solvent vocabulary and binary systems from tool metadata."""

    solvent_options: list[TlcSolventOption] = Field(min_length=1)
    allowed_systems: list[TlcCandidateSystem] = Field(min_length=1)


class BatchTemplateDefinition(PredictionStrictModel):
    """Build-owned template contract; fields are the only accepted upload columns."""

    id: Id
    chromatography_type: Literal["tlc", "cc", "hplc", "sfc", "gc"]
    tool: Literal["batch"]
    version: Annotated[str, Field(min_length=1, max_length=64)]
    row_schema: Annotated[str, Field(min_length=1, max_length=128)]
    fields: list[TargetFieldId] = Field(min_length=1)
    limits: BatchResourceBudget
    health: CapabilityHealth
    tlc_options: TlcBatchTemplateOptions | None = None


class TlcBatchCompatibleModel(PredictionStrictModel):
    """One exact verified TLC model binding and its supported batch templates."""

    model_id: Id
    model_version: Annotated[str, Field(min_length=1, max_length=128)]
    input_schema_version: Annotated[str, Field(min_length=1, max_length=64)]
    template_versions: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(min_length=1)
    health: CapabilityHealth

    @field_validator("template_versions")
    @classmethod
    def validate_unique_templates(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("compatible model template versions must be unique")
        return value


class TlcBatchModelConstraints(PredictionStrictModel):
    """TLC-only model/template compatibility contract; an empty list is fail-closed."""

    template_versions: list[Annotated[str, Field(min_length=1, max_length=64)]]
    compatible_models: list[TlcBatchCompatibleModel]

    @model_validator(mode="after")
    def validate_bindings(self) -> "TlcBatchModelConstraints":
        if len(self.template_versions) != len(set(self.template_versions)):
            raise ValueError("TLC batch template versions must be unique")
        keys = [
            (item.model_id, item.model_version, item.input_schema_version)
            for item in self.compatible_models
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("TLC batch model binding keys must be unique")
        allowed = set(self.template_versions)
        if any(not set(item.template_versions) <= allowed for item in self.compatible_models):
            raise ValueError("compatible model templates must be declared by the batch capability")
        return self


class NonTlcBatchCompatibleModel(PredictionStrictModel):
    model_id: Id
    model_version: Annotated[str, Field(min_length=1, max_length=128)]
    input_schema_version: Annotated[str, Field(min_length=1, max_length=64)]
    preprocessing_profile_id: Id | None = None
    preprocessing_profile_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None
    tools: list[Literal["single", "single_column", "full_column"]] = Field(min_length=1)
    template_versions: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(min_length=1)
    health: CapabilityHealth

    @field_validator("template_versions")
    @classmethod
    def validate_unique_templates(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("compatible model template versions must be unique")
        return value

    @model_validator(mode="after")
    def validate_profile_pair(self) -> "NonTlcBatchCompatibleModel":
        if (self.preprocessing_profile_id is None) != (self.preprocessing_profile_sha256 is None):
            raise ValueError("preprocessing profile id and sha256 must be supplied together")
        if len(self.tools) != len(set(self.tools)):
            raise ValueError("compatible model tools must be unique")
        return self


class NonTlcBatchModelConstraints(PredictionStrictModel):
    template_versions: list[Annotated[str, Field(min_length=1, max_length=64)]]
    compatible_models: list[NonTlcBatchCompatibleModel]

    @model_validator(mode="after")
    def validate_bindings(self) -> "NonTlcBatchModelConstraints":
        if len(self.template_versions) != len(set(self.template_versions)):
            raise ValueError("batch template versions must be unique")
        keys = [(item.model_id, item.model_version, item.input_schema_version, item.preprocessing_profile_id, item.preprocessing_profile_sha256) for item in self.compatible_models]
        if len(keys) != len(set(keys)):
            raise ValueError("batch exact model/profile bindings must be unique")
        allowed = set(self.template_versions)
        if any(not set(item.template_versions) <= allowed for item in self.compatible_models):
            raise ValueError("compatible model templates must be declared by the batch capability")
        return self


class BatchDraftBase(PredictionStrictModel):
    project_id: Id
    name: Annotated[str, Field(min_length=1, max_length=200)]
    schema_version: Annotated[str, Field(min_length=1, max_length=64)]
    model_selection: ModelSelection
    input_artifact_id: Id
    template_version: Annotated[str, Field(min_length=1, max_length=64)]
    field_mapping: dict[TargetFieldId, UploadColumnName] = Field(min_length=1)
    invalid_row_policy: Literal["block", "exclude_invalid"]
    resource_budget: BatchResourceBudget

    @field_validator("field_mapping")
    @classmethod
    def validate_field_mapping(cls, value: dict[str, str]) -> dict[str, str]:
        sql_expression = re.compile(
            r"(?:\bselect\b.+\bfrom\b|\b(?:insert|update|delete|drop|union|join|exec|pragma)\b\s+)",
            re.I,
        )
        for column in value.values():
            if not column.strip() or any(ord(char) < 32 or ord(char) == 127 for char in column):
                raise ValueError("upload column names must be visible non-whitespace text")
            if (
                ";" in column
                or "--" in column
                or "/*" in column
                or "*/" in column
                or "=" in column
                or sql_expression.search(column)
                or re.search(r"(?:^|[/\\])\.\.(?:$|[/\\])", column)
                or re.match(r"^(?:[A-Za-z]:[/\\]|[/\\])", column)
            ):
                raise ValueError("upload column name contains an unsafe expression or path")
        return value


class TlcBatchDraftCreate(BatchDraftBase):
    chromatography_type: Literal["tlc"]
    tool: Literal["batch"]
    common_input: TlcBatchCommonInput = Field(default_factory=TlcBatchCommonInput)


class CcBatchDraftCreate(BatchDraftBase):
    chromatography_type: Literal["cc"]
    tool: Literal["batch"]
    common_input: CcBatchCommonInput = Field(default_factory=CcBatchCommonInput)


class HplcBatchDraftCreate(BatchDraftBase):
    chromatography_type: Literal["hplc"]
    tool: Literal["batch"]
    common_input: HplcBatchCommonInput = Field(default_factory=HplcBatchCommonInput)


class SfcBatchDraftCreate(BatchDraftBase):
    chromatography_type: Literal["sfc"]
    tool: Literal["batch"]
    common_input: SfcBatchCommonInput = Field(default_factory=SfcBatchCommonInput)

    @model_validator(mode="after")
    def require_candidate_scope_source(self) -> "SfcBatchDraftCreate":
        # SFC single/full-column requests both bind to a project-owned
        # candidate scope. Batch may take it from shared defaults or a mapped
        # row column, but must not accept a draft with no source for it.
        if (self.common_input.candidate_scope_id is None
                and "candidate_scope_id" not in self.field_mapping):
            raise ValueError("SFC batch requires candidate_scope_id in common_input or field_mapping")
        return self


class GcBatchDraftCreate(BatchDraftBase):
    chromatography_type: Literal["gc"]
    tool: Literal["batch"]
    common_input: GcBatchCommonInput = Field(default_factory=GcBatchCommonInput)


BatchDraftCreate: TypeAlias = Annotated[
    TlcBatchDraftCreate
    | CcBatchDraftCreate
    | HplcBatchDraftCreate
    | SfcBatchDraftCreate
    | GcBatchDraftCreate,
    Field(discriminator="chromatography_type"),
]


class BatchDraftState(PredictionStrictModel):
    id: Id
    revision: int = Field(ge=1)
    config_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    etag: Annotated[str, Field(min_length=1, max_length=128)]
    status: Literal["DRAFT", "VALIDATING", "INVALID", "READY"]


class BatchValidation(PredictionStrictModel):
    id: Id
    draft_revision: int = Field(ge=1)
    config_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    status: Literal["QUEUED", "RUNNING", "READY", "INVALID", "STALE", "FAILED"]
    template_version: Annotated[str, Field(min_length=1, max_length=64)]
    model_snapshot: ModelSnapshot
    total_rows: int = Field(ge=0)
    valid_rows: int = Field(ge=0)
    invalid_rows: int = Field(ge=0)
    excluded_rows: int = Field(ge=0)
    total_evaluations: int = Field(ge=0)
    resource_estimate: BatchResourceBudget
    resolved_input_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None
    errors_report_artifact_id: Id | None = None
    warnings: list[Warning] = Field(default_factory=list)


class RowValidation(PredictionStrictModel):
    row_id: Id
    source_row_number: int = Field(ge=1)
    status: Literal["VALID", "INVALID", "WARNING"]
    normalized_input_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    resolved_input_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None
    resolved_input_snapshot: BatchResolvedInput | None = None
    field_errors: list[dict[str, str]] = Field(default_factory=list)
    warnings: list[Warning] = Field(default_factory=list)


class BatchCounts(PredictionStrictModel):
    total: int = Field(ge=0)
    queued: int = Field(ge=0)
    running: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    failed: int = Field(ge=0)
    skipped: int = Field(ge=0)
    canceled: int = Field(ge=0)


class BatchPrediction(PredictionStrictModel):
    id: Id
    project_id: Id
    chromatography_type: Literal["tlc", "cc", "hplc", "sfc", "gc"]
    tool: Literal["batch"]
    config_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    draft_status: Literal["DRAFT", "VALIDATING", "INVALID", "READY"]
    execution_status: JobStatus | None = None
    job_id: Id | None = None
    attempt: int | None = Field(default=None, ge=1)
    counts: BatchCounts
    progress: float = Field(ge=0.0, le=1.0)
    revision: int = Field(ge=1)
    etag: Annotated[str, Field(min_length=1, max_length=128)]


class BatchDraftPatch(PredictionStrictModel):
    """Mutable draft fields; absent fields retain the current snapshot."""

    name: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    schema_version: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    model_selection: ModelSelection | None = None
    input_artifact_id: Id | None = None
    template_version: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    field_mapping: dict[TargetFieldId, UploadColumnName] | None = None
    common_input: dict[str, object] | None = None
    invalid_row_policy: Literal["block", "exclude_invalid"] | None = None
    resource_budget: BatchResourceBudget | None = None

    @field_validator("field_mapping")
    @classmethod
    def validate_patch_mapping(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if value is None:
            return None
        # Reuse the create contract's path/expression guard without accepting
        # a partial object as a draft create request.
        return BatchDraftBase.validate_field_mapping(value)


class BatchDraft(BatchPrediction):
    """Saved draft projection returned by create/patch/get."""

    name: Annotated[str, Field(min_length=1, max_length=200)]
    schema_version: Annotated[str, Field(min_length=1, max_length=64)]
    model_selection: ModelSelection
    input_artifact_id: Id
    template_version: Annotated[str, Field(min_length=1, max_length=64)]
    field_mapping: dict[TargetFieldId, UploadColumnName] = Field(min_length=1)
    common_input: dict[str, object]
    invalid_row_policy: Literal["block", "exclude_invalid"]
    resource_budget: BatchResourceBudget
    validation_id: Id | None = None


class BatchValidationAccepted(PredictionStrictModel):
    id: Id
    status: Literal["QUEUED", "RUNNING"]
    job_id: Id
    resource_ref: dict[str, str]


class BatchAccepted(PredictionStrictModel):
    id: Id
    job_id: Id
    attempt: int = Field(ge=1)
    execution_status: Literal["QUEUED", "RUNNING", "CANCEL_REQUESTED", "SUCCEEDED", "PARTIAL", "FAILED", "CANCELED", "TIMED_OUT", "INTERRUPTED"]
    resource_ref: dict[str, str]


class BatchRowResult(PredictionStrictModel):
    row_id: Id
    source_row_number: int = Field(ge=1)
    input_snapshot: dict[str, object]
    result: dict[str, object] | None = None
    error: dict[str, str] | None = None
    warnings: list[Warning] = Field(default_factory=list)
    attempt: int = Field(ge=1)
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "SKIPPED", "CANCELED"]


class BatchRetryPlanCreate(PredictionStrictModel):
    row_ids: list[Id] = Field(min_length=1, max_length=100_000)


class BatchRetryPlan(PredictionStrictModel):
    id: Id
    revision: int = Field(ge=1)
    config_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    etag: Annotated[str, Field(min_length=1, max_length=128)]
    row_ids: list[Id] = Field(min_length=1)
    parent_batch_id: Id
    expected_attempt: int = Field(ge=1)
    estimated_evaluations: int = Field(ge=0)
    validation_id: Id | None = None
    model_snapshot_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None
    resolved_input_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None


class BatchRetryFailed(PredictionStrictModel):
    retry_plan_id: Id
    confirmation_id: Id


class BatchSubmit(PredictionStrictModel):
    confirmation_id: Id


class BatchCancel(PredictionStrictModel):
    reason: Annotated[str, Field(max_length=2_000)] | None = None


class BatchEmpty(PredictionStrictModel):
    """Explicit empty object used by asynchronous validate."""

    pass


class BatchExportCreate(PredictionStrictModel):
    format: Literal["json", "csv", "xlsx"]
    include: list[Literal["input_snapshot", "result", "model_version", "warnings"]] = Field(min_length=1)


class BatchExportAccepted(PredictionStrictModel):
    id: Id
    job_id: Id
    status: Literal["QUEUED", "RUNNING"]
    resource_ref: dict[str, str]


class BatchExportView(PredictionStrictModel):
    id: Id
    batch_id: Id
    job_id: Id
    format: Literal["json", "csv", "xlsx"]
    include: list[Literal["input_snapshot", "result", "model_version", "warnings"]]
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELED"]
    source_revision: int = Field(ge=1)
    validation_id: Id
    source_attempt: int = Field(ge=1)
    source_snapshot_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    artifact_id: Id | None = None
    error: dict[str, str] | None = None
    created_at: datetime
    completed_at: datetime | None = None


class BatchRowsPage(PredictionStrictModel):
    rows: list[BatchRowResult]
    next_cursor: str | None = None
    has_more: bool


class BatchValidationRowsPage(PredictionStrictModel):
    rows: list[RowValidation]
    next_cursor: str | None = None
    has_more: bool


class BatchTemplate(PredictionStrictModel):
    id: Id
    chromatography_type: Literal["tlc", "cc", "hplc", "sfc", "gc"]
    tool: Literal["batch"]
    version: Annotated[str, Field(min_length=1, max_length=64)]
    row_schema: Annotated[str, Field(min_length=1, max_length=128)] = ""
    fields: list[TargetFieldId] = Field(default_factory=list)
    limits: BatchResourceBudget
    health: CapabilityHealth
    tlc_options: TlcBatchTemplateOptions | None = None
    tlc_options: TlcBatchTemplateOptions | None = None


class BatchTemplateRegistry:
    """Explicit build registration; no template discovery or execution."""

    def __init__(self) -> None:
        self._templates: dict[tuple[str, str, str], BatchTemplate] = {}
        self._row_models: dict[tuple[str, str], type[PredictionStrictModel]] = {}

    def register(self, template: BatchTemplate, *, row_model: type[PredictionStrictModel] | None = None) -> None:
        key = (template.chromatography_type, template.tool, template.version)
        if key in self._templates:
            raise ValueError(f"batch template already registered: {key}")
        self._templates[key] = template
        if row_model is not None:
            self._row_models[(template.chromatography_type, template.version)] = row_model

    def list(self, chromatography_type: str | None = None) -> list[BatchTemplate]:
        values = self._templates.values()
        if chromatography_type is not None:
            values = (item for item in values if item.chromatography_type == chromatography_type)
        return sorted(values, key=lambda item: (item.chromatography_type, item.version))

    def get(self, chromatography_type: str, tool: str, version: str) -> BatchTemplate:
        try:
            return self._templates[(chromatography_type, tool, version)]
        except KeyError as exc:
            raise KeyError(f"batch template is not built: {chromatography_type}/{tool}/{version}") from exc

    def row_model(self, chromatography_type: str, version: str | None = None) -> type[PredictionStrictModel]:
        try:
            if version is not None:
                return self._row_models[(chromatography_type, version)]
            matches = sorted(
                ((version_id, model) for (type_id, version_id), model in self._row_models.items() if type_id == chromatography_type),
                key=lambda pair: pair[0],
            )
            return matches[-1][1]
        except KeyError as exc:
            raise KeyError(f"batch row schema is not built: {chromatography_type}") from exc

    def render(self, chromatography_type: str, *, tool: str = "batch", model_id: str | None = None, format: str = "csv", version: str | None = None) -> tuple[bytes, str, str]:
        import csv
        import hashlib

        matches = [item for item in self.list(chromatography_type) if item.tool == tool]
        template = next((item for item in matches if item.version == version), None) if version else (matches[-1] if matches else None)
        if template is None:
            raise KeyError(f"batch template is not built: {chromatography_type}/{tool}")
        if template.health.status == "UNAVAILABLE":
            raise ValueError("batch template is not executable; select the current version")
        if format == "csv":
            stream = io.StringIO(newline="")
            csv.writer(stream, lineterminator="\n").writerow(template.fields)
            content = stream.getvalue().encode("utf-8")
        elif format == "xlsx":
            headers = "".join(
                f'<c r="{chr(65 + index)}1" t="inlineStr"><is><t>{escape(field)}</t></is></c>'
                for index, field in enumerate(template.fields)
            )
            files = {
                "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
                "_rels/.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
                "xl/workbook.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="batch" sheetId="1" r:id="rId1"/></sheets></workbook>',
                "xl/_rels/workbook.xml.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
                "xl/worksheets/sheet1.xml": f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1">{headers}</row></sheetData></worksheet>',
            }
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, value in files.items():
                    archive.writestr(name, value)
            content = output.getvalue()
        else:
            raise ValueError("unsupported batch template format")
        return content, template.version, hashlib.sha256(content).hexdigest()


__all__ = [
    "BatchCancel",
    "BatchAccepted",
    "BatchCounts",
    "BatchDraft",
    "BatchDraftCreate",
    "BatchDraftPatch",
    "BatchEmpty",
    "BatchExportAccepted",
    "BatchExportCreate",
    "BatchRetryFailed",
    "BatchRetryPlan",
    "BatchRetryPlanCreate",
    "BatchDraftState",
    "BatchPrediction",
    "BatchResourceBudget",
    "BatchRowResult",
    "BatchRowsPage",
    "BatchSubmit",
    "BatchTemplate",
    "BatchTemplateRegistry",
    "BatchTemplateDefinition",
    "TlcBatchTemplateOptions",
    "CcBatchRow",
    "GcBatchRow",
    "HplcBatchRow",
    "SfcBatchRow",
    "TlcBatchRow",
    "BatchValidationAccepted",
    "BatchValidationRowsPage",
    "BatchValidation",
    "CcBatchCommonInput",
    "CcBatchDraftCreate",
    "GcBatchCommonInput",
    "GcBatchDraftCreate",
    "HplcBatchCommonInput",
    "HplcBatchDraftCreate",
    "RowValidation",
    "SfcBatchCommonInput",
    "SfcBatchDraftCreate",
    "TlcBatchCommonInput",
    "TlcBatchDraftCreate",
]
