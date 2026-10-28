"""Pydantic contracts for chromatography prediction.

These are contracts only.  They do not contain scientific defaults and do not
pretend that a fixture is a production model.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Annotated, Literal, Mapping, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    Tag,
    computed_field,
    field_validator,
    model_validator,
)

from chrom_platform.core.dto import CapabilityHealth, ResourceRef
from chrom_platform.task_core.state import JobStatus


class PredictionStrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=False)


Id = Annotated[str, Field(min_length=1, max_length=96)]
BusinessScopeId = Annotated[
    str,
    Field(min_length=1, max_length=160, pattern=r"^\S(?:.*\S)?$"),
]
StructureRefId = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_.:-]{0,95}$")]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class Warning(PredictionStrictModel):
    code: Annotated[str, Field(min_length=1, max_length=96)]
    message: Annotated[str, Field(min_length=1, max_length=2_000)]
    field: str | None = None


WarningDTO = Warning


class PublicErrorSummary(PredictionStrictModel):
    code: Annotated[str, Field(min_length=1, max_length=96)]
    message: Annotated[str, Field(min_length=1, max_length=2_000)]
    retryable: bool = False


PredictionError = PublicErrorSummary


class ModelSnapshot(PredictionStrictModel):
    id: Id
    version: Annotated[str, Field(min_length=1, max_length=128)]
    business_scope_id: BusinessScopeId
    artifact_sha256: Sha256
    input_schema_version: Annotated[str, Field(min_length=1, max_length=64)]
    output_schema_version: Annotated[str, Field(min_length=1, max_length=64)]
    preprocessing_profile_id: Id | None = Field(
        default=None,
        description="需精确选择预处理版本的模型使用该字段；必须与 SHA-256 成对提供。",
    )
    preprocessing_profile_sha256: Sha256 | None = Field(
        default=None,
        description="需精确选择预处理版本的模型使用该字段；必须与标识成对提供。",
    )
    score_semantics: str | None = Field(default=None, max_length=96)
    threshold: str | None = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def validate_preprocessing_profile_pair(self) -> "ModelSnapshot":
        if (self.preprocessing_profile_id is None) != (self.preprocessing_profile_sha256 is None):
            raise ValueError("preprocessing profile id and sha256 must be supplied together")
        return self


class ChampionModelSelection(PredictionStrictModel):
    mode: Literal["champion"]
    scope_id: Id


class ExactModelSelection(PredictionStrictModel):
    mode: Literal["exact"]
    model_id: Id
    model_version: Annotated[str, Field(min_length=1, max_length=128)]
    preprocessing_profile_id: Id | None = Field(
        default=None,
        description="能力声明要求的精确预处理版本标识；必须与 SHA-256 字段同时出现。",
    )
    preprocessing_profile_sha256: Sha256 | None = Field(
        default=None,
        description="能力声明要求的精确预处理版本 SHA-256；必须与标识字段同时出现。",
    )

    @model_validator(mode="after")
    def validate_preprocessing_profile_pair(self) -> "ExactModelSelection":
        if (self.preprocessing_profile_id is None) != (self.preprocessing_profile_sha256 is None):
            raise ValueError("preprocessing profile id and sha256 must be supplied together")
        return self


class GcExactProfileModelSelection(ExactModelSelection):
    """GC v1.1 requires a complete observed model/profile pin in the request."""

    preprocessing_profile_id: Id
    preprocessing_profile_sha256: Sha256


ModelSelection: TypeAlias = Annotated[
    ChampionModelSelection | ExactModelSelection,
    Field(discriminator="mode"),
]


class SolventComponent(PredictionStrictModel):
    solvent_id: Annotated[str, Field(min_length=1, max_length=96)]
    fraction: float = Field(ge=0.0, le=1.0)


class TlcSolventComponent(PredictionStrictModel):
    """A public TLC solvent fraction bound to the verified model vocabulary.

    The HTTP contract accepts fractions only after the caller has explicitly
    converted any UI ratio (for example, ``8:2``) to values in ``[0, 1]``
    whose total is one.  Internal model feature construction may still use a
    separate ratio normalization boundary, but it must never repair this DTO.
    """

    solvent_id: Literal[
        "hexane",
        "ethyl_acetate",
        "dichloromethane",
        "methanol",
        "diethyl_ether",
    ]
    fraction: float = Field(ge=0.0, le=1.0)


class TlcInput(PredictionStrictModel):
    solvent_components: list[TlcSolventComponent] = Field(min_length=2, max_length=5)

    @field_validator("solvent_components")
    @classmethod
    def validate_solvents(cls, value: list[TlcSolventComponent]) -> list[TlcSolventComponent]:
        ids = [item.solvent_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("solvent_id values must be unique")
        if abs(sum(item.fraction for item in value) - 1.0) > 1e-6:
            raise ValueError("TLC solvent fractions must sum to 1 within 1e-6; ratios must be normalized by the caller")
        return value


class TlcSolventOption(PredictionStrictModel):
    """One solvent exposed by the verified TLC capability."""

    id: Id


class TlcCandidateSystem(PredictionStrictModel):
    """A model-declared solvent system, identified from its solvent IDs."""

    id: Id
    solvent_ids: list[Id] = Field(min_length=2, max_length=5)

    @field_validator("solvent_ids")
    @classmethod
    def validate_solvent_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("solvent_ids must be unique")
        return value


class TlcRange(PredictionStrictModel):
    min: float
    max: float
    min_inclusive: bool = True
    max_inclusive: bool = True

    @model_validator(mode="after")
    def validate_order(self) -> "TlcRange":
        if self.min > self.max:
            raise ValueError("range.min must be <= range.max")
        return self


class TlcReverseLimits(PredictionStrictModel):
    target_rf: TlcRange
    ratio_step: TlcRange
    max_candidates: int = Field(ge=1)
    max_evaluations: int = Field(ge=1)


class TlcConditionOptions(PredictionStrictModel):
    """Typed projection of the verified TLC adapter capability."""

    schema_version: Annotated[str, Field(min_length=1, max_length=64)]
    model_id: Id
    model_version: Annotated[str, Field(min_length=1, max_length=128)]
    artifact_sha256: Sha256
    solvent_options: list[TlcSolventOption] = Field(min_length=1)
    candidate_systems: list[TlcCandidateSystem] = Field(min_length=1)
    ratio: TlcRange
    reverse: TlcReverseLimits


class CcInput(PredictionStrictModel):
    eluent_system_id: Id
    loading_solvent_id: Id
    sample_mass_mg: float = Field(gt=0.0)
    loading_solvent_volume_ul: float = Field(gt=0.0)
    flow_rate_ml_min: float = Field(gt=0.0)
    column_spec_id: Id


class HplcInputV10(PredictionStrictModel):
    """Read-only decoder for canonical-only historical HPLC snapshots."""

    isopropanol_fraction: float = Field(ge=0.0, le=1.0)
    flow_rate_ml_min: float = Field(gt=0.0)
    column_ids: list[Id] = Field(min_length=1)

    @field_validator("column_ids")
    @classmethod
    def validate_columns(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("column_ids must be unique")
        return value


class HplcInput(PredictionStrictModel):
    """HPLC schema 1.1 input; raw strings are the author's actual inputs."""

    model_input_smiles: list[Annotated[str, Field(min_length=1, max_length=20_000)]] = Field(
        min_length=2, max_length=2,
    )
    isopropanol_fraction: float = Field(ge=0.0, le=1.0)
    flow_rate_ml_min: float = Field(gt=0.0)
    column_ids: list[Id] = Field(min_length=1)

    @field_validator("column_ids")
    @classmethod
    def validate_columns(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("column_ids must be unique")
        return value

    @field_validator("model_input_smiles")
    @classmethod
    def reject_blank_model_inputs(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("model_input_smiles entries must not be blank")
        return value


class ChiralLcConditions(PredictionStrictModel):
    method_profile_id: Id


class ChiralLcInput(PredictionStrictModel):
    enantiomer_proposal_id: Id
    conditions: ChiralLcConditions


class ModifierComponent(PredictionStrictModel):
    structure_ref: StructureRefId
    fraction: float = Field(ge=0.0, le=1.0)


class _SfcConditionInput(PredictionStrictModel):
    modifier_total_fraction: float = Field(ge=0.0, le=1.0)
    modifier_components: list[ModifierComponent] = Field(min_length=1, max_length=20)
    candidate_scope_id: Id
    top_k: int = Field(ge=1, le=200)

    @field_validator("modifier_components")
    @classmethod
    def validate_modifiers(cls, value: list[ModifierComponent]) -> list[ModifierComponent]:
        ids = [item.structure_ref for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("modifier structure_ref values must be unique")
        if abs(sum(item.fraction for item in value) - 1.0) > 1e-6:
            raise ValueError("modifier fractions must sum to 1")
        return value


class SfcSingleColumnInput(_SfcConditionInput):
    column_id: Id


class SfcFullColumnInput(_SfcConditionInput):
    """Full-column selection uses a server-declared candidate scope."""


class GcLinearTemperatureProgram(PredictionStrictModel):
    mode: Literal["linear"]
    initial_temperature_c: float
    final_temperature_c: float
    heating_rate_c_per_min: float = Field(gt=0.0)
    initial_hold_min: float = Field(ge=0.0)

    @model_validator(mode="after")
    def validate_range(self) -> "GcLinearTemperatureProgram":
        if self.final_temperature_c < self.initial_temperature_c:
            raise ValueError("final temperature must be >= initial temperature")
        return self


class GcSeriesTemperatureProgram(PredictionStrictModel):
    mode: Literal["series"]
    temperature_series_c: list[float] = Field(min_length=1, max_length=10_000)


GcTemperatureProgram: TypeAlias = Annotated[
    GcLinearTemperatureProgram | GcSeriesTemperatureProgram,
    Field(discriminator="mode"),
]


class GcInput(PredictionStrictModel):
    temperature_program: GcTemperatureProgram


class ModifierStructureSnapshot(PredictionStrictModel):
    structure_ref: StructureRefId
    normalized_smiles: Annotated[str, Field(min_length=1, max_length=20_000)]


class PredictionInputSnapshot(PredictionStrictModel):
    structure_refs: list[StructureRefId] = Field(min_length=1, max_length=2)
    normalized_structures: list[Annotated[str, Field(min_length=1, max_length=20_000)]] = Field(
        min_length=1, max_length=2
    )
    input: TlcInput | TlcConditionComparisonCreate | TlcReverseCreate | CcInput | HplcInputV10 | HplcInput | ChiralLcInput | SfcSingleColumnInput | SfcFullColumnInput | _SfcConditionInput | GcInput

class PredictionExecutionInputSnapshot(PredictionInputSnapshot):
    """Private execution snapshot; never use this as an API response model."""

    modifier_structures: list[ModifierStructureSnapshot] = Field(default_factory=list, max_length=20)


def to_public_prediction_input_snapshot(
    value: PredictionExecutionInputSnapshot | Mapping[str, object],
) -> PredictionInputSnapshot:
    """Map the private persisted snapshot to the exact public DTO fields."""

    internal = (
        value
        if isinstance(value, PredictionExecutionInputSnapshot)
        else PredictionExecutionInputSnapshot.model_validate(value)
    )
    return PredictionInputSnapshot(
        structure_refs=list(internal.structure_refs),
        normalized_structures=list(internal.normalized_structures),
        input=internal.input,
    )


class PredictionCreateBase(PredictionStrictModel):
    # Bound by the server's access-context when omitted by the client.
    project_id: Id | None = None
    schema_version: Annotated[str, Field(min_length=1, max_length=64)]
    model_selection: ModelSelection
    structure_refs: list[StructureRefId] = Field(min_length=1, max_length=2)


def _require_distinct_pair(value: list[str], label: str) -> list[str]:
    if len(value) != 2 or value[0] == value[1]:
        raise ValueError(f"{label} requires exactly two distinct structure refs")
    return value


class TlcPredictionCreate(PredictionCreateBase):
    chromatography_type: Literal["tlc"]
    tool: Literal["single"]
    input: TlcInput

    @field_validator("structure_refs")
    @classmethod
    def require_single(cls, value: list[str]) -> list[str]:
        if len(value) != 1:
            raise ValueError("TLC single requires exactly one structure ref")
        return value


class TlcConditionComparisonCreate(PredictionStrictModel):
    """Persisted TLC comparison job input; HTTP may omit discriminator defaults."""

    project_id: Id | None = None
    schema_version: Annotated[str, Field(min_length=1, max_length=64)]
    model_selection: ModelSelection
    chromatography_type: Literal["tlc"] = "tlc"
    tool: Literal["condition_compare"] = "condition_compare"
    structure_ref: StructureRefId
    structure_refs: list[StructureRefId] | None = None
    conditions: list[TlcInput] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def bind_structure(self) -> "TlcConditionComparisonCreate":
        if self.structure_refs is None:
            object.__setattr__(self, "structure_refs", [self.structure_ref])
        elif self.structure_refs != [self.structure_ref]:
            raise ValueError("TLC comparison structure_refs must bind structure_ref")
        return self


class TlcReverseCreate(PredictionStrictModel):
    """Persisted TLC reverse-solvent job input; all points use the real model."""

    project_id: Id | None = None
    schema_version: Annotated[str, Field(min_length=1, max_length=64)]
    model_selection: ModelSelection
    chromatography_type: Literal["tlc"] = "tlc"
    tool: Literal["reverse_solvent"] = "reverse_solvent"
    structure_ref: StructureRefId
    structure_refs: list[StructureRefId] | None = None
    target_rf_min: float = Field(ge=0.0, le=1.0)
    target_rf_max: float = Field(ge=0.0, le=1.0)
    candidate_system_ids: list[Annotated[str, Field(min_length=1, max_length=96)]] = Field(min_length=1, max_length=3)
    ratio_step: float = Field(gt=0.0, le=1.0)
    max_candidates: int = Field(ge=1, le=200)

    @model_validator(mode="after")
    def validate_target(self) -> "TlcReverseCreate":
        if self.target_rf_min > self.target_rf_max:
            raise ValueError("target_rf_min must be <= target_rf_max")
        if self.structure_refs is None:
            object.__setattr__(self, "structure_refs", [self.structure_ref])
        elif self.structure_refs != [self.structure_ref]:
            raise ValueError("TLC reverse structure_refs must bind structure_ref")
        if len(self.candidate_system_ids) != len(set(self.candidate_system_ids)):
            raise ValueError("candidate_system_ids must be unique")
        return self


class CcPredictionCreate(PredictionCreateBase):
    chromatography_type: Literal["cc"]
    tool: Literal["single"]
    input: CcInput
    structure_refs: list[StructureRefId] = Field(
        min_length=2,
        max_length=2,
        description="Exactly two distinct structures to separate; no enantiomer claim is implied.",
    )

    @field_validator("structure_refs")
    @classmethod
    def require_pair(cls, value: list[str]) -> list[str]:
        return _require_distinct_pair(value, "CC")


class HplcPredictionCreate(PredictionCreateBase):
    chromatography_type: Literal["hplc"]
    tool: Literal["single"]
    schema_version: Literal["1.1"]
    model_selection: ExactModelSelection
    structure_refs: list[StructureRefId] = Field(
        min_length=2,
        max_length=2,
        description="Exactly two distinct structures bound in order to the raw HPLC SMILES pair.",
    )
    input: HplcInput

    @field_validator("structure_refs")
    @classmethod
    def require_pair(cls, value: list[str]) -> list[str]:
        return _require_distinct_pair(value, "HPLC")


def validate_hplc_execution_binding(
    *, request: BaseModel, model_snapshot: ModelSnapshot,
    input_snapshot: "PredictionExecutionInputSnapshot",
) -> None:
    """Reject missing or divergent HPLC v1.1 facts before worker execution."""
    if getattr(request, "chromatography_type", None) != "hplc":
        return
    if not isinstance(request, HplcPredictionCreate):
        raise ValueError("legacy HPLC request cannot enter the execution path")
    if not isinstance(input_snapshot.input, HplcInput):
        raise ValueError("HPLC v1.1 execution input snapshot is incomplete")
    if (
        list(request.structure_refs) != list(input_snapshot.structure_refs)
        or request.input.model_dump(mode="json") != input_snapshot.input.model_dump(mode="json")
        or len(input_snapshot.normalized_structures) != 2
        or request.model_selection.mode != "exact"
        or request.model_selection.model_id != model_snapshot.id
        or request.model_selection.model_version != model_snapshot.version
        or request.model_selection.preprocessing_profile_id != model_snapshot.preprocessing_profile_id
        or request.model_selection.preprocessing_profile_sha256 != model_snapshot.preprocessing_profile_sha256
        or model_snapshot.preprocessing_profile_id is None
        or model_snapshot.preprocessing_profile_sha256 is None
        or model_snapshot.artifact_sha256 is None
    ):
        raise ValueError("HPLC v1.1 request and frozen snapshots do not match")


def validate_gc_execution_binding(
    *, request: BaseModel, model_snapshot: ModelSnapshot,
    input_snapshot: "PredictionExecutionInputSnapshot",
) -> None:
    """Reject legacy or divergent GC v1.1 facts before worker execution."""
    if getattr(request, "chromatography_type", None) != "gc":
        return
    if not isinstance(request, GcPredictionCreate):
        raise ValueError("legacy GC request cannot enter the execution path")
    selection = request.model_selection
    normalized = input_snapshot.normalized_structures
    if (
        not isinstance(input_snapshot.input, GcInput)
        or list(request.structure_refs) != list(input_snapshot.structure_refs)
        or request.input.model_dump(mode="json") != input_snapshot.input.model_dump(mode="json")
        or isinstance(normalized, (str, bytes))
        or not isinstance(normalized, list)
        or len(normalized) != 1
        or not isinstance(normalized[0], str)
        or not normalized[0].strip()
        or selection.mode != "exact"
        or selection.model_id != model_snapshot.id
        or selection.model_version != model_snapshot.version
        or selection.preprocessing_profile_id != model_snapshot.preprocessing_profile_id
        or selection.preprocessing_profile_sha256 != model_snapshot.preprocessing_profile_sha256
        or model_snapshot.input_schema_version != request.schema_version
        or model_snapshot.preprocessing_profile_id is None
        or model_snapshot.preprocessing_profile_sha256 is None
        or model_snapshot.artifact_sha256 is None
    ):
        raise ValueError("GC v1.1 request and frozen snapshots do not match")


class ChiralLcPredictionCreate(PredictionCreateBase):
    chromatography_type: Literal["chiral_lc"]
    tool: Literal["single"]
    input: ChiralLcInput

    @field_validator("structure_refs")
    @classmethod
    def require_single(cls, value: list[str]) -> list[str]:
        if len(value) != 1:
            raise ValueError("chiral_lc requires exactly one stereospecified structure ref")
        return value


class SfcSingleColumnPredictionCreate(PredictionCreateBase):
    chromatography_type: Literal["sfc"]
    tool: Literal["single_column"]
    schema_version: Literal["1.1"]
    model_selection: ExactModelSelection
    structure_refs: list[StructureRefId] = Field(min_length=2, max_length=2)
    input: SfcSingleColumnInput

    @field_validator("structure_refs")
    @classmethod
    def require_pair(cls, value: list[str]) -> list[str]:
        return _require_distinct_pair(value, "SFC")



class SfcFullColumnPredictionCreate(PredictionCreateBase):
    chromatography_type: Literal["sfc"]
    tool: Literal["full_column"]
    schema_version: Literal["1.1"]
    model_selection: ExactModelSelection
    structure_refs: list[StructureRefId] = Field(min_length=2, max_length=2)
    input: SfcFullColumnInput

    @field_validator("structure_refs")
    @classmethod
    def require_pair(cls, value: list[str]) -> list[str]:
        return _require_distinct_pair(value, "SFC")


def _prediction_discriminator(value: object) -> str | None:
    data = value if isinstance(value, dict) else getattr(value, "__dict__", {})
    chromatography_type = data.get("chromatography_type")
    if chromatography_type in {"sfc", "tlc"}:
        return f"{chromatography_type}_{data.get('tool')}"
    return chromatography_type


def _sfc_discriminator(value: object) -> str | None:
    data = value if isinstance(value, dict) else getattr(value, "__dict__", {})
    return f"sfc_{data.get('tool')}"


SfcPredictionCreate: TypeAlias = Annotated[
    Annotated[SfcSingleColumnPredictionCreate, Tag("sfc_single_column")]
    | Annotated[SfcFullColumnPredictionCreate, Tag("sfc_full_column")],
    Discriminator(_sfc_discriminator),
]


class SfcSingleColumnPredictionReadV10(PredictionStrictModel):
    """Stored SFC v1.0 request decoder; excluded from new prediction create."""

    project_id: Id | None = None
    schema_version: Literal["1.0"]
    model_selection: ModelSelection
    chromatography_type: Literal["sfc"]
    tool: Literal["single_column"]
    structure_refs: list[StructureRefId] = Field(min_length=2, max_length=2)
    input: SfcSingleColumnInput

    @field_validator("structure_refs")
    @classmethod
    def require_pair(cls, value: list[str]) -> list[str]:
        return _require_distinct_pair(value, "SFC")


class SfcFullColumnPredictionReadV10(PredictionStrictModel):
    """Stored SFC v1.0 request decoder; excluded from new prediction create."""

    project_id: Id | None = None
    schema_version: Literal["1.0"]
    model_selection: ModelSelection
    chromatography_type: Literal["sfc"]
    tool: Literal["full_column"]
    structure_refs: list[StructureRefId] = Field(min_length=2, max_length=2)
    input: SfcFullColumnInput

    @field_validator("structure_refs")
    @classmethod
    def require_pair(cls, value: list[str]) -> list[str]:
        return _require_distinct_pair(value, "SFC")


class SfcSingleColumnExecutionV10(PredictionStrictModel):
    """Internal-only SFC v1.0 execution request built from frozen snapshots."""

    project_id: Id | None = None
    schema_version: Literal["1.0"]
    model_selection: ExactModelSelection
    chromatography_type: Literal["sfc"]
    tool: Literal["single_column"]
    structure_refs: list[StructureRefId] = Field(min_length=2, max_length=2)
    input: SfcSingleColumnInput

    @field_validator("structure_refs")
    @classmethod
    def require_pair(cls, value: list[str]) -> list[str]:
        return _require_distinct_pair(value, "SFC")


class SfcFullColumnExecutionV10(PredictionStrictModel):
    """Internal-only SFC v1.0 execution request built from frozen snapshots."""

    project_id: Id | None = None
    schema_version: Literal["1.0"]
    model_selection: ExactModelSelection
    chromatography_type: Literal["sfc"]
    tool: Literal["full_column"]
    structure_refs: list[StructureRefId] = Field(min_length=2, max_length=2)
    input: SfcFullColumnInput

    @field_validator("structure_refs")
    @classmethod
    def require_pair(cls, value: list[str]) -> list[str]:
        return _require_distinct_pair(value, "SFC")


def _sfc_execution_discriminator(value: object) -> str | None:
    data = value if isinstance(value, dict) else getattr(value, "__dict__", {})
    return f"sfc_{data.get('tool')}_v10_execution"


SfcPredictionExecutionV10: TypeAlias = Annotated[
    Annotated[SfcSingleColumnExecutionV10, Tag("sfc_single_column_v10_execution")]
    | Annotated[SfcFullColumnExecutionV10, Tag("sfc_full_column_v10_execution")],
    Discriminator(_sfc_execution_discriminator),
]


class GcPredictionCreate(PredictionCreateBase):
    chromatography_type: Literal["gc"]
    tool: Literal["single"]
    schema_version: Literal["1.1"]
    model_selection: GcExactProfileModelSelection
    structure_refs: list[StructureRefId] = Field(min_length=1, max_length=1)
    input: GcInput

    @field_validator("structure_refs")
    @classmethod
    def require_single(cls, value: list[str]) -> list[str]:
        if len(value) != 1:
            raise ValueError("GC single requires exactly one structure ref")
        return value

    @model_validator(mode="after")
    def require_profile_selection(self) -> "GcPredictionCreate":
        if (
            self.model_selection.preprocessing_profile_id is None
            or self.model_selection.preprocessing_profile_sha256 is None
        ):
            raise ValueError("GC v1.1 requires an exact model and preprocessing profile")
        return self


class GcPredictionReadV10(PredictionStrictModel):
    """Stored GC v1.0 request decoder; read/export only, never executable."""

    project_id: Id | None = None
    chromatography_type: Literal["gc"]
    tool: Literal["single"]
    schema_version: Literal["1.0"]
    model_selection: ModelSelection
    structure_refs: list[StructureRefId] = Field(min_length=1, max_length=1)
    input: GcInput


PredictionCreate: TypeAlias = Annotated[
    Annotated[TlcPredictionCreate, Tag("tlc_single")]
    | Annotated[TlcConditionComparisonCreate, Tag("tlc_condition_compare")]
    | Annotated[TlcReverseCreate, Tag("tlc_reverse_solvent")]
    | Annotated[CcPredictionCreate, Tag("cc")]
    | Annotated[HplcPredictionCreate, Tag("hplc")]
    | Annotated[ChiralLcPredictionCreate, Tag("chiral_lc")]
    | Annotated[SfcSingleColumnPredictionCreate, Tag("sfc_single_column")]
    | Annotated[SfcFullColumnPredictionCreate, Tag("sfc_full_column")]
    | Annotated[GcPredictionCreate, Tag("gc")],
    Discriminator(_prediction_discriminator),
]


class HplcPredictionReadV10(PredictionStrictModel):
    """Stored HPLC v1.0 request decoder; never accepted by create or workers."""

    project_id: Id | None = None
    chromatography_type: Literal["hplc"]
    tool: Literal["single"]
    schema_version: Literal["1.0"]
    model_selection: ModelSelection
    structure_refs: list[StructureRefId] = Field(min_length=2, max_length=2)
    input: HplcInputV10

    @field_validator("structure_refs")
    @classmethod
    def require_pair(cls, value: list[str]) -> list[str]:
        return _require_distinct_pair(value, "HPLC")


def _stored_prediction_discriminator(value: object) -> str | None:
    data = value if isinstance(value, dict) else getattr(value, "__dict__", {})
    if data.get("chromatography_type") == "hplc":
        return "hplc_v10" if data.get("schema_version") == "1.0" else "hplc"
    if data.get("chromatography_type") == "gc":
        return "gc_v10" if data.get("schema_version") == "1.0" else "gc"
    if data.get("chromatography_type") == "sfc" and data.get("schema_version") == "1.0":
        return f"sfc_{data.get('tool')}_v10"
    return _prediction_discriminator(value)


PredictionStoredRead: TypeAlias = Annotated[
    Annotated[TlcPredictionCreate, Tag("tlc_single")]
    | Annotated[TlcConditionComparisonCreate, Tag("tlc_condition_compare")]
    | Annotated[TlcReverseCreate, Tag("tlc_reverse_solvent")]
    | Annotated[CcPredictionCreate, Tag("cc")]
    | Annotated[HplcPredictionCreate, Tag("hplc")]
    | Annotated[HplcPredictionReadV10, Tag("hplc_v10")]
    | Annotated[ChiralLcPredictionCreate, Tag("chiral_lc")]
    | Annotated[SfcSingleColumnPredictionCreate, Tag("sfc_single_column")]
    | Annotated[SfcFullColumnPredictionCreate, Tag("sfc_full_column")]
    | Annotated[SfcSingleColumnPredictionReadV10, Tag("sfc_single_column_v10")]
    | Annotated[SfcFullColumnPredictionReadV10, Tag("sfc_full_column_v10")]
    | Annotated[GcPredictionCreate, Tag("gc")]
    | Annotated[GcPredictionReadV10, Tag("gc_v10")],
    Discriminator(_stored_prediction_discriminator),
]


PredictionExecutionRequest: TypeAlias = (
    PredictionCreate | SfcSingleColumnExecutionV10 | SfcFullColumnExecutionV10
)


def build_prediction_execution_request(
    value: object,
    *,
    project_id: str,
    expected_type: str,
    expected_tool: str,
    expected_schema_version: str,
    model_snapshot: ModelSnapshot,
    input_snapshot: PredictionExecutionInputSnapshot,
) -> PredictionExecutionRequest:
    """Bind one persisted request to its frozen model/input snapshots.

    SFC v1.0 is accepted only from stored rows. Its former Champion choice is
    lifted to the already-persisted model snapshot for this execution attempt;
    no active default or Champion pointer is consulted.
    """
    from pydantic import TypeAdapter

    raw = value.model_dump(mode="python") if isinstance(value, BaseModel) else value
    if not isinstance(raw, Mapping):
        raise ValueError("persisted prediction request is invalid")
    if (
        raw.get("chromatography_type") != expected_type
        or raw.get("tool") != expected_tool
        or raw.get("schema_version") != expected_schema_version
    ):
        raise ValueError("persisted prediction row and request binding do not match")
    is_sfc = raw.get("chromatography_type") == "sfc"
    is_sfc_v10 = is_sfc and raw.get("schema_version") == "1.0"
    if is_sfc_v10:
        request = TypeAdapter(PredictionStoredRead).validate_python(raw)
        if not isinstance(request, (SfcSingleColumnPredictionReadV10, SfcFullColumnPredictionReadV10)):
            raise ValueError("stored SFC v1.0 request is invalid")
    else:
        request = TypeAdapter(PredictionCreate).validate_python(raw)

    if not is_sfc:
        return request
    if (
        request.project_id not in (None, project_id)
        or request.schema_version != model_snapshot.input_schema_version
        or request.chromatography_type != "sfc"
        or len(input_snapshot.structure_refs) != 2
        or list(request.structure_refs) != list(input_snapshot.structure_refs)
        or request.input.model_dump(mode="json") != input_snapshot.input.model_dump(mode="json")
        or model_snapshot.artifact_sha256 is None
        or model_snapshot.output_schema_version != "1.0"
        or model_snapshot.preprocessing_profile_id is not None
        or model_snapshot.preprocessing_profile_sha256 is not None
    ):
        raise ValueError("SFC request and frozen snapshots do not match")
    selection = request.model_selection
    if selection.mode == "exact":
        if (
            selection.model_id != model_snapshot.id
            or selection.model_version != model_snapshot.version
            or selection.preprocessing_profile_id is not None
            or selection.preprocessing_profile_sha256 is not None
        ):
            raise ValueError("SFC exact selection does not match frozen model")
    elif selection.mode == "champion":
        if selection.scope_id != model_snapshot.business_scope_id:
            raise ValueError("SFC Champion scope does not match frozen model")
    else:  # pragma: no cover - strict DTOs reject this already
        raise ValueError("SFC model selection is invalid")

    exact = ExactModelSelection(
        mode="exact",
        model_id=model_snapshot.id,
        model_version=model_snapshot.version,
    )
    payload = {
        **request.model_dump(mode="python"),
        "model_selection": exact.model_dump(mode="python"),
    }
    if isinstance(request, SfcSingleColumnPredictionReadV10):
        return SfcSingleColumnExecutionV10.model_validate(payload)
    if isinstance(request, SfcFullColumnPredictionReadV10):
        return SfcFullColumnExecutionV10.model_validate(payload)
    return type(request).model_validate(payload)

# Stable naming aliases for callers that use request/result terminology.  The
# aliases do not merge any schemas; each concrete class above remains strict.
TlcPredictionRequest = TlcPredictionCreate
CcPredictionRequest = CcPredictionCreate
HplcPredictionRequest = HplcPredictionCreate
ChiralLcPredictionRequest = ChiralLcPredictionCreate
SfcPredictionRequest = SfcPredictionCreate
GcPredictionRequest = GcPredictionCreate
PredictionRequest = PredictionCreate


class Metric(PredictionStrictModel):
    key: Annotated[str, Field(min_length=1, max_length=96)]
    label: Annotated[str, Field(min_length=1, max_length=200)]
    value: float | str | None
    unit: str | None = None
    origin: Literal["model", "derived"]


class Visualization(PredictionStrictModel):
    kind: Annotated[str, Field(min_length=1, max_length=96)]
    schema_version: Annotated[str, Field(min_length=1, max_length=64)]
    data: dict[str, object]
    origin: Literal["predicted", "simulated", "measured"]


class Applicability(PredictionStrictModel):
    supported_scope: Annotated[str, Field(min_length=1, max_length=500)]
    warnings: list[Warning] = Field(default_factory=list)


class TlcResultRow(PredictionStrictModel):
    rf: float = Field(ge=0.0, le=1.0)


def derive_cc_time_seconds(*, volume_ml: float, flow_rate_ml_min: float) -> float:
    if volume_ml <= 0 or flow_rate_ml_min <= 0:
        raise ValueError("volume_ml and flow_rate_ml_min must be > 0")
    return volume_ml * 60.0 / flow_rate_ml_min


class CcResultRow(PredictionStrictModel):
    volume_ml: float = Field(gt=0.0)
    flow_rate_ml_min: float = Field(gt=0.0)

    @model_validator(mode="before")
    @classmethod
    def accept_verified_persisted_time_seconds(cls, value: object) -> object:
        """Read prior JSON rows that persisted the computed response field.

        ``time_seconds`` is emitted by ``model_dump`` because it is a computed
        field. Older prediction rows therefore contain it even though it is
        not an input field. Accept only this exact known field and verify it
        against the unchanged volume/flow calculation before Pydantic applies
        the normal strict, extra-forbid validation.
        """
        if not isinstance(value, Mapping) or "time_seconds" not in value:
            return value

        supplied = value["time_seconds"]
        if isinstance(supplied, bool) or not isinstance(supplied, (int, float)) or not math.isfinite(supplied):
            raise ValueError("persisted CC time_seconds is invalid")
        try:
            expected = derive_cc_time_seconds(
                volume_ml=float(value["volume_ml"]),
                flow_rate_ml_min=float(value["flow_rate_ml_min"]),
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            raise ValueError("persisted CC time_seconds cannot be verified") from None
        if not math.isclose(float(supplied), expected, rel_tol=1e-12, abs_tol=1e-9):
            raise ValueError("persisted CC time_seconds does not match volume and flow")
        return {key: item for key, item in value.items() if key != "time_seconds"}

    @computed_field  # type: ignore[prop-decorator]
    @property
    def time_seconds(self) -> float:
        return derive_cc_time_seconds(volume_ml=self.volume_ml, flow_rate_ml_min=self.flow_rate_ml_min)


class HplcResultRow(PredictionStrictModel):
    candidate_id: Id
    separation: float | None = None


class ChiralLcResultRow(PredictionStrictModel):
    peak_id: Id
    retention_time_min: float = Field(ge=0.0)
    separation: float | None = None


class SfcCandidateConditions(PredictionStrictModel):
    column: Annotated[str, Field(min_length=1, max_length=160)]
    stationary_phase: Annotated[str, Field(min_length=1, max_length=255)]
    mode: Annotated[str, Field(min_length=1, max_length=64)]
    length_mm: float = Field(gt=0.0)
    inner_diameter_mm: float = Field(gt=0.0)
    particle_size_um: float = Field(gt=0.0)
    column_temperature_c: float
    flow_rate_ml_min: float = Field(gt=0.0)
    observed_in_source: bool | None = None


class SfcCandidateScopePolicy(PredictionStrictModel):
    expansion_mode: Literal["coupled_discrete_conditions"]
    policy_version: Literal["1"]
    candidate_source_sha256: Sha256


class SfcCandidateScopeProvenance(PredictionStrictModel):
    scope_id: Id
    policy: SfcCandidateScopePolicy
    candidate_count: int = Field(ge=1, le=100_000)
    observed_count: int = Field(ge=0, le=100_000)
    unobserved_count: int = Field(ge=0, le=100_000)


class SfcCandidateScopeView(PredictionStrictModel):
    id: Id
    project_id: Id
    model_id: Id
    model_version: Annotated[str, Field(min_length=1, max_length=128)]
    candidate_count: int = Field(ge=1, le=100_000)
    max_candidates: int = Field(ge=1, le=100_000)
    expansion: Literal[
        "server_cartesian_or_historical_distinct_rows",
        "observed_distinct_rows",
        "coupled_discrete_conditions",
    ]
    expansion_mode: Literal["observed_distinct_rows", "coupled_discrete_conditions"] = "observed_distinct_rows"
    policy: SfcCandidateScopePolicy | None = None
    observed_count: int | None = Field(default=None, ge=0, le=100_000)
    unobserved_count: int | None = Field(default=None, ge=0, le=100_000)
    conditions: list[SfcCandidateConditions] = Field(max_length=500)
    truncated: bool


class SfcConditionOption(PredictionStrictModel):
    key: Id
    label: Annotated[str, Field(min_length=1, max_length=160)]
    unit: str | None = None
    values: list[str | float]


class SfcAllColumnDomain(PredictionStrictModel):
    model_scope: Literal["all_column"]


class SfcSingleColumnDomain(PredictionStrictModel):
    model_scope: Literal["single_column"]
    group_id: Id
    group_label: str = Field(min_length=1, max_length=160)
    stationary_phase: str = Field(min_length=1, max_length=255)
    mode: str = Field(min_length=1, max_length=64)
    allowed_columns: list[str] = Field(min_length=1, max_length=200)


SfcDomain: TypeAlias = Annotated[
    SfcAllColumnDomain | SfcSingleColumnDomain,
    Field(discriminator="model_scope"),
]


class SfcConditionsOptions(PredictionStrictModel):
    scope_id: Id | None
    project_id: Id
    model_id: Id
    model_version: Annotated[str, Field(min_length=1, max_length=128)]
    threshold: str | None = None
    sfc_domain: SfcDomain
    candidate_count: int = Field(ge=1, le=100_000)
    max_candidates: int = Field(ge=1, le=100_000)
    scope_provenance: SfcCandidateScopeProvenance | None = None
    supported_expansion_modes: list[Literal["observed_distinct_rows", "coupled_discrete_conditions"]] = Field(
        default_factory=lambda: ["observed_distinct_rows"]
    )
    fields: list[SfcConditionOption]


SfcCandidateAxis: TypeAlias = Literal[
    "column", "stationary_phase", "mode", "length_mm", "inner_diameter_mm",
    "particle_size_um", "column_temperature_c", "flow_rate_ml_min",
]


class SfcCandidateScopeCreate(PredictionStrictModel):
    """Axis values supplied by a client; candidates are expanded server-side."""
    project_id: Id | None = None
    model_selection: ExactModelSelection
    axes: dict[SfcCandidateAxis, list[str | float]] = Field(min_length=1, max_length=8)
    max_candidates: int = Field(default=100_000, ge=1, le=100_000)
    expansion_mode: Literal["observed_distinct_rows", "coupled_discrete_conditions"] = "observed_distinct_rows"

    @model_validator(mode="after")
    def validate_axes(self) -> "SfcCandidateScopeCreate":
        allowed = {
            "column", "stationary_phase", "mode", "length_mm", "inner_diameter_mm",
            "particle_size_um", "column_temperature_c", "flow_rate_ml_min",
        }
        if set(self.axes) - allowed or not all(self.axes.values()):
            raise ValueError("SFC axes are empty or unsupported")
        return self


class SfcCandidateScopePreview(PredictionStrictModel):
    project_id: Id
    model_id: Id
    model_version: Annotated[str, Field(min_length=1, max_length=128)]
    matched_count: int = Field(ge=0)
    max_candidates: int = Field(ge=1, le=100_000)
    expansion_mode: Literal["observed_distinct_rows", "coupled_discrete_conditions"]
    policy: SfcCandidateScopePolicy | None = None
    observed_count: int | None = Field(default=None, ge=0)
    unobserved_count: int | None = Field(default=None, ge=0)
    status: Literal["READY", "EMPTY", "OVER_LIMIT"]


class SfcResultRow(PredictionStrictModel):
    candidate_id: Id
    rank: int = Field(ge=1)
    separation: float | None = None
    conditions: SfcCandidateConditions
    score_semantics: Literal["positive_class_probability"]


class GcResultRow(PredictionStrictModel):
    retention_time_min: float = Field(ge=0.0)
    quantiles: dict[str, float] = Field(default_factory=dict)


class TlcPredictionResult(PredictionStrictModel):
    task_type: Literal["tlc"]
    schema_version: Annotated[str, Field(min_length=1, max_length=64)]
    model_id: Id
    model_version: Annotated[str, Field(min_length=1, max_length=128)]
    artifact_sha256: Sha256
    ensemble_weights: dict[str, float]
    mobile_phase_original: dict[str, float]
    mobile_phase_normalized: dict[str, float]
    metrics: list[Metric] = Field(default_factory=list)
    rows: list[TlcResultRow]
    visualizations: list[Visualization] = Field(default_factory=list)
    applicability: Applicability


class TlcConditionComparisonResult(PredictionStrictModel):
    task_type: Literal["tlc_condition_comparison"]
    schema_version: Annotated[str, Field(min_length=1, max_length=64)]
    structure_ref: StructureRefId
    model_id: Id
    model_version: Annotated[str, Field(min_length=1, max_length=128)]
    artifact_sha256: Sha256
    results: list[dict[str, object]]
    warnings: list[str] = Field(default_factory=list)


class TlcReverseResult(PredictionStrictModel):
    task_type: Literal["tlc_reverse_solvent"]
    schema_version: Annotated[str, Field(min_length=1, max_length=64)]
    structure_ref: StructureRefId
    model_id: Id
    model_version: Annotated[str, Field(min_length=1, max_length=128)]
    artifact_sha256: Sha256
    candidates: list[dict[str, object]]
    evaluated: int = Field(ge=0)
    failed: int = Field(ge=0)
    target_reachable: bool
    warnings: list[str]


class CcPredictionResult(PredictionStrictModel):
    task_type: Literal["cc"]
    schema_version: Annotated[str, Field(min_length=1, max_length=64)]
    metrics: list[Metric] = Field(default_factory=list)
    rows: list[CcResultRow]
    visualizations: list[Visualization] = Field(default_factory=list)
    applicability: Applicability


class HplcPredictionResult(PredictionStrictModel):
    task_type: Literal["hplc"]
    schema_version: Annotated[str, Field(min_length=1, max_length=64)]
    metrics: list[Metric] = Field(default_factory=list)
    rows: list[HplcResultRow]
    visualizations: list[Visualization] = Field(default_factory=list)
    applicability: Applicability


class ChiralLcPredictionResult(PredictionStrictModel):
    task_type: Literal["chiral_lc"]
    schema_version: Annotated[str, Field(min_length=1, max_length=64)]
    metrics: list[Metric] = Field(default_factory=list)
    rows: list[ChiralLcResultRow]
    visualizations: list[Visualization] = Field(default_factory=list)
    applicability: Applicability


class SfcPredictionResult(PredictionStrictModel):
    task_type: Literal["sfc"]
    schema_version: Annotated[str, Field(min_length=1, max_length=64)]
    metrics: list[Metric] = Field(default_factory=list)
    evaluated_candidate_count: int | None = Field(
        default=None, ge=1, le=100_000,
        description="实际评分候选数，单柱过滤后、Top-k 截断前；旧结果未知时为 null。",
    )
    rows: list[SfcResultRow]
    visualizations: list[Visualization] = Field(default_factory=list)
    applicability: Applicability
    candidate_scope_provenance: SfcCandidateScopeProvenance | None = None
    score_semantics: Literal["positive_class_probability"] = "positive_class_probability"


class GcPredictionResult(PredictionStrictModel):
    task_type: Literal["gc"]
    schema_version: Annotated[str, Field(min_length=1, max_length=64)]
    metrics: list[Metric] = Field(default_factory=list)
    rows: list[GcResultRow]
    visualizations: list[Visualization] = Field(default_factory=list)
    applicability: Applicability


TypedPredictionResult: TypeAlias = Annotated[
    TlcPredictionResult
    | TlcConditionComparisonResult
    | TlcReverseResult
    | CcPredictionResult
    | HplcPredictionResult
    | ChiralLcPredictionResult
    | SfcPredictionResult
    | GcPredictionResult,
    Field(discriminator="task_type"),
]
PredictionResult = TypedPredictionResult


CCInput = CcInput
HPLCInput = HplcInput
SFCInput = SfcSingleColumnInput | SfcFullColumnInput
GCInput = GcInput
ChiralLCInput = ChiralLcInput
HplcPredictionInput = HplcInput
ChiralLcPredictionInput = ChiralLcInput
Error = PublicErrorSummary


class Prediction(PredictionStrictModel):
    id: Id
    project_id: Id
    prediction_record_id: Id
    job_id: Id
    chromatography_type: Literal["tlc", "cc", "hplc", "sfc", "gc", "chiral_lc"]
    tool: Annotated[str, Field(min_length=1, max_length=96)]
    schema_version: Annotated[str, Field(min_length=1, max_length=64)]
    status: JobStatus
    model: ModelSnapshot
    input_snapshot: PredictionInputSnapshot
    result: TypedPredictionResult | None = None
    warnings: list[Warning] = Field(default_factory=list)
    error: PublicErrorSummary | None = None
    source: Literal["web", "assistant", "service"]
    conversation_id: Id | None = None
    agent_run_id: Id | None = None
    created_at: datetime
    completed_at: datetime | None = None
    data_origin: Literal["production", "fixture"]
    revision: int = Field(ge=1)
    etag: Annotated[str, Field(min_length=1, max_length=128)]
    updated_at: datetime


class PredictionAccepted(PredictionStrictModel):
    id: Id
    prediction_record_id: Id
    job_id: Id
    status: JobStatus
    resource_ref: ResourceRef


PredictionExecutionInputSnapshot.model_rebuild()


class BatchAccepted(PredictionStrictModel):
    id: Id
    job_id: Id
    attempt: int = Field(ge=1)
    execution_status: JobStatus
    resource_ref: ResourceRef


__all__ = [
    "Applicability",
    "BatchAccepted",
    "BusinessScopeId",
    "CcInput",
    "CcPredictionCreate",
    "CcPredictionRequest",
    "CcPredictionResult",
    "CcResultRow",
    "ChampionModelSelection",
    "ChiralLcConditions",
    "ChiralLcInput",
    "ChiralLcPredictionCreate",
    "ChiralLcPredictionInput",
    "ChiralLcPredictionResult",
    "ChiralLcResultRow",
    "ExactModelSelection",
    "GcExactProfileModelSelection",
    "GcInput",
    "GcLinearTemperatureProgram",
    "GcPredictionCreate",
    "GcPredictionReadV10",
    "GcPredictionRequest",
    "GcPredictionResult",
    "GcResultRow",
    "GcSeriesTemperatureProgram",
    "HplcInput",
    "HplcInputV10",
    "HplcPredictionCreate",
    "HplcPredictionReadV10",
    "HplcPredictionInput",
    "HplcPredictionRequest",
    "HplcPredictionResult",
    "HplcResultRow",
    "Metric",
    "ModelSelection",
    "ModelSnapshot",
    "Prediction",
    "PredictionAccepted",
    "PredictionCreate",
    "PredictionExecutionRequest",
    "PredictionStoredRead",
    "PredictionRequest",
    "PredictionResult",
    "PredictionError",
    "PredictionInputSnapshot",
    "PredictionStrictModel",
    "PublicErrorSummary",
    "Error",
    "CCInput",
    "HPLCInput",
    "SFCInput",
    "GCInput",
    "ChiralLCInput",
    "SfcFullColumnInput",
    "SfcFullColumnExecutionV10",
    "SfcFullColumnPredictionCreate",
    "SfcFullColumnPredictionReadV10",
    "SfcSingleColumnInput",
    "SfcSingleColumnExecutionV10",
    "SfcSingleColumnPredictionCreate",
    "SfcSingleColumnPredictionReadV10",
    "SfcPredictionCreate",
    "SfcPredictionResult",
    "SfcCandidateConditions",
    "SfcCandidateScopePolicy",
    "SfcCandidateScopeProvenance",
    "SfcCandidateScopeView",
    "SfcCandidateScopeCreate",
    "SfcCandidateScopePreview",
    "SfcConditionOption",
    "SfcConditionsOptions",
    "SfcResultRow",
    "SolventComponent",
    "StructureRefId",
    "TlcInput",
    "TlcSolventOption",
    "TlcCandidateSystem",
    "TlcRange",
    "TlcReverseLimits",
    "TlcConditionOptions",
    "TlcConditionComparisonCreate",
    "TlcConditionComparisonResult",
    "TlcReverseCreate",
    "TlcReverseResult",
    "TlcSolventComponent",
    "TlcPredictionCreate",
    "TlcPredictionRequest",
    "TlcPredictionResult",
    "TlcResultRow",
    "TypedPredictionResult",
    "Visualization",
    "Warning",
    "WarningDTO",
    "derive_cc_time_seconds",
    "validate_hplc_execution_binding",
    "validate_gc_execution_binding",
    "build_prediction_execution_request",
]
