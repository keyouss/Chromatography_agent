"""Strict, versioned data-center DTOs.

The DTOs describe the public contract only.  A domain schema is a trusted
deployment contract, not a user supplied database schema.
"""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from chrom_platform.core.dto import ResourceRef, StrictModel


class DataStrictModel(StrictModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        validate_assignment=True,
        populate_by_name=False,
    )


PublicId = str
ChromatographyType = Literal["tlc", "cc", "hplc", "gc", "sfc", "chiral_lc"]
ImportMode = Literal["append", "upsert"]
DataVersionStatus = Literal["READY", "ARCHIVED"]
DataImportStatus = Literal[
    "DRAFT",
    "VALIDATING",
    "INVALID",
    "READY",
    "COMMITTING",
    "COMMITTED",
    "FAILED",
]
ValidationStatus = Literal["PENDING", "VALID", "INVALID", "STALE"]


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PATH_OR_SQL = re.compile(
    r"(?:\.|\[|\]|\$|;|--|/|\\|\b(?:select|insert|update|delete|drop|alter|union|from|where|join|pragma|exec|script)\b)",
    re.IGNORECASE,
)
_UNSAFE_IDENTIFIER_TEXT = re.compile(
    r"(?:\$|;|--|/|\\|\b(?:select|insert|update|delete|drop|alter|union|from|where|join|pragma|exec|script)\b)",
    re.IGNORECASE,
)


def _safe_field_name(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value) or _PATH_OR_SQL.search(value):
        raise ValueError("字段映射只能使用简单字段名，不能包含路径表达式或 SQL")
    return value


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DomainField(DataStrictModel):
    type: Literal["string", "integer", "number", "boolean", "date", "datetime"]
    required: bool = False
    unit: str | None = Field(default=None, max_length=64)


class DataDomainSchema(DataStrictModel):
    """A trusted immutable schema registered per chromatography domain."""

    id: PublicId = Field(min_length=1, max_length=96)
    version: str = Field(min_length=1, max_length=64)
    chromatography_type: ChromatographyType
    record_schema: dict[str, DomainField]
    unit_definitions: dict[str, str]
    identity_fields: list[str] = Field(min_length=1)
    deduplication_policy: Literal["reject", "update_existing"]
    supported_import_modes: list[ImportMode] = Field(min_length=1)

    @field_validator("id", "version")
    @classmethod
    def no_expression(cls, value: str) -> str:
        # Schema versions commonly contain dots (for example ``1.0``); the
        # path/SQL restriction applies to field mappings, while metadata only
        # needs to reject executable/path-like separators.
        if _UNSAFE_IDENTIFIER_TEXT.search(value):
            raise ValueError("schema 标识不能包含路径表达式或 SQL")
        return value

    @field_validator("record_schema", "unit_definitions")
    @classmethod
    def safe_schema_keys(cls, value: dict[str, Any]) -> dict[str, Any]:
        for key in value:
            _safe_field_name(key)
        return value

    @field_validator("identity_fields")
    @classmethod
    def safe_identity_fields(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("identity_fields 不能重复")
        for item in value:
            _safe_field_name(item)
        return value

    @model_validator(mode="after")
    def validate_contract(self) -> "DataDomainSchema":
        unknown = set(self.identity_fields) - set(self.record_schema)
        if unknown:
            raise ValueError("identity_fields 必须来自 record_schema")
        if len(set(self.supported_import_modes)) != len(self.supported_import_modes):
            raise ValueError("supported_import_modes 不能重复")
        return self


class DataDomainView(DataDomainSchema):
    """Read DTO; no user-provided schema is accepted by import DTOs."""


class DataImportCreate(DataStrictModel):
    project_id: PublicId = Field(min_length=1, max_length=96)
    domain_id: PublicId = Field(min_length=1, max_length=96)
    artifact_id: PublicId = Field(min_length=1, max_length=96)
    schema_version: str = Field(min_length=1, max_length=64)
    import_mode: ImportMode
    field_mapping: dict[str, str]
    source_description: str = Field(min_length=1, max_length=2_000)

    @field_validator("field_mapping")
    @classmethod
    def safe_mapping(cls, value: dict[str, str]) -> dict[str, str]:
        if not value:
            raise ValueError("field_mapping 不能为空")
        # Contract direction: trusted target field id -> uploaded column.
        for target, source in value.items():
            _safe_field_name(target)
            if (
                not 1 <= len(source) <= 255
                or not source.strip()
                or any(ord(char) < 32 or ord(char) == 127 for char in source)
            ):
                raise ValueError("上传文件列名必须为 1-255 个非控制字符")
        if len(set(value.values())) != len(value.values()):
            raise ValueError("field_mapping 不能重复使用上传文件列名")
        return value


class DataImportPatch(DataStrictModel):
    schema_version: str | None = Field(default=None, min_length=1, max_length=64)
    import_mode: ImportMode | None = None
    field_mapping: dict[str, str] | None = None
    source_description: str | None = Field(default=None, min_length=1, max_length=2_000)

    @field_validator("field_mapping")
    @classmethod
    def safe_mapping(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if value is not None:
            DataImportCreate.safe_mapping(value)
        return value

    @model_validator(mode="after")
    def non_empty(self) -> "DataImportPatch":
        if all(getattr(self, field) is None for field in ("schema_version", "import_mode", "field_mapping", "source_description")):
            raise ValueError("至少提供一个可修改字段")
        return self


class DataImport(DataStrictModel):
    id: PublicId
    project_id: PublicId
    domain_id: PublicId
    artifact_id: PublicId
    schema_version: str
    import_mode: ImportMode
    field_mapping: dict[str, str]
    source_description: str
    revision: int = Field(ge=1)
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    etag: str = Field(min_length=1, max_length=128)
    status: DataImportStatus
    job_id: PublicId | None = None
    validation_ref: PublicId | None = None
    new_data_version_id: PublicId | None = None
    request_snapshot: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(
        extra="forbid", strict=True, validate_assignment=True, frozen=True
    )


class ValidationCounts(DataStrictModel):
    new: int = Field(ge=0)
    updated: int = Field(ge=0)
    unchanged: int = Field(ge=0)
    invalid: int = Field(ge=0)
    duplicate: int = Field(ge=0)


class DataImportValidation(DataStrictModel):
    id: PublicId
    import_id: PublicId
    revision: int = Field(ge=1)
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: ValidationStatus
    counts: ValidationCounts
    report_artifact_id: PublicId | None = None
    warnings: list[str] = Field(default_factory=list)
    # The parser contract is versioned independently from the import config.
    # A validation result carrying an old parser revision/hash is stale even
    # when the user-facing import fields have not changed.
    parser_revision: str | None = Field(default=None, max_length=128)
    parser_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    created_at: datetime

    model_config = ConfigDict(
        extra="forbid", strict=True, validate_assignment=True, frozen=True
    )


class DataVersion(DataStrictModel):
    id: PublicId
    project_id: PublicId
    domain_id: PublicId
    version: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: DataVersionStatus
    record_count: int = Field(ge=0)
    source_import_id: PublicId
    snapshot_artifact_id: PublicId
    schema_version: str
    quality_report_ref: ResourceRef | None = None
    created_at: datetime
    referenced_by_count: int = Field(ge=0)

    model_config = ConfigDict(
        extra="forbid", strict=True, validate_assignment=True, frozen=True
    )


class AsyncAccepted(DataStrictModel):
    """Stable envelope for data validation/commit task acceptance."""

    id: PublicId
    status: str = Field(min_length=1, max_length=32)
    job_id: PublicId
    resource_ref: ResourceRef


class DataVersionDraft(DataStrictModel):
    """Metadata handed to a repository that atomically allocates a version."""

    project_id: PublicId
    domain_id: PublicId
    source_import_id: PublicId
    snapshot_artifact_id: PublicId
    schema_version: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    record_count: int = Field(ge=0)
    quality_report_ref: ResourceRef | None = None


class FeatureVersion(DataStrictModel):
    id: PublicId
    version: str = Field(min_length=1, max_length=64)
    task_types: list[str] = Field(min_length=1)
    implementation_version: str = Field(min_length=1, max_length=64)
    schema_definition: dict[str, Any] = Field(alias="schema")
    dependency_lock_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime

    model_config = ConfigDict(
        extra="forbid", strict=True, validate_assignment=True, frozen=True, populate_by_name=False
    )


class MoleculeCreate(DataStrictModel):
    project_id: PublicId
    structure_id: PublicId
    display_name: str | None = Field(default=None, max_length=255)
    external_reference: str | None = Field(default=None, max_length=255)
    source: str = Field(min_length=1, max_length=2_000)


class Molecule(DataStrictModel):
    id: PublicId
    project_id: PublicId
    structure_id: PublicId
    canonical_smiles: str
    inchikey: str | None = None
    display_name: str | None = None
    external_reference: str | None = None
    chromatography_types: list[ChromatographyType] = Field(default_factory=list)
    record_count: int = Field(ge=0)
    source: str
    quality_status: Literal["UNKNOWN", "VALID", "WARNING", "INVALID"]
    created_at: datetime


class ExperimentRecord(DataStrictModel):
    id: PublicId
    project_id: PublicId
    molecule_id: PublicId
    domain_id: PublicId
    chromatography_type: ChromatographyType
    data_version_id: PublicId
    values: dict[str, Any]
    created_at: datetime


class PredictionRecord(DataStrictModel):
    id: PublicId
    project_id: PublicId
    prediction_id: PublicId | None = None
    batch_id: PublicId | None = None
    source: str
    actor_id: PublicId
    chromatography_type: ChromatographyType
    model_snapshot: dict[str, Any]
    input_snapshot: dict[str, Any]
    result_ref: ResourceRef | None = None
    warnings: list[str] = Field(default_factory=list)
    status: str
    archived_at: datetime | None = None
    created_at: datetime
    artifact_refs: list[ResourceRef] = Field(default_factory=list)
    revision: int = Field(ge=1)
    etag: str


class PredictionDraftExactModelSelection(DataStrictModel):
    mode: Literal["exact"]
    model_id: PublicId
    model_version: str = Field(min_length=1, max_length=128)
    preprocessing_profile_id: PublicId | None = Field(
        default=None, description="CC 重放来源的预处理版本标识；必须与 SHA-256 字段成对提供。"
    )
    preprocessing_profile_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$",
        description="CC 重放来源的预处理版本 SHA-256；必须与标识字段成对提供。",
    )

    @model_validator(mode="after")
    def validate_preprocessing_profile_pair(self) -> "PredictionDraftExactModelSelection":
        if (self.preprocessing_profile_id is None) != (self.preprocessing_profile_sha256 is None):
            raise ValueError("preprocessing profile id and sha256 must be supplied together")
        return self


class PredictionDraftStructureSnapshot(DataStrictModel):
    id: PublicId
    project_id: PublicId
    canonical_smiles: str = Field(min_length=1, max_length=20_000)
    stereochemistry_status: Literal["specified", "unspecified", "not_applicable"]


class PredictionDraftSourceModelSnapshot(DataStrictModel):
    id: PublicId
    version: str = Field(min_length=1, max_length=128)
    business_scope_id: str = Field(min_length=1, max_length=160)
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_schema_version: str = Field(min_length=1, max_length=64)
    output_schema_version: str = Field(min_length=1, max_length=64)
    preprocessing_profile_id: PublicId | None = Field(
        default=None, description="源模型的 CC 预处理版本标识；必须与 SHA-256 字段成对提供。"
    )
    preprocessing_profile_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$",
        description="源模型的 CC 预处理版本 SHA-256；必须与标识字段成对提供。",
    )
    score_semantics: str | None = Field(default=None, max_length=96)
    threshold: str | None = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def validate_preprocessing_profile_pair(self) -> "PredictionDraftSourceModelSnapshot":
        if (self.preprocessing_profile_id is None) != (self.preprocessing_profile_sha256 is None):
            raise ValueError("preprocessing profile id and sha256 must be supplied together")
        return self


class PredictionDraftView(DataStrictModel):
    id: PublicId
    source_record_id: PublicId
    source_prediction_id: PublicId
    project_id: PublicId
    chromatography_type: ChromatographyType
    tool: str
    structure_refs: list[ResourceRef] = Field(default_factory=list)
    structure_snapshots: list[PredictionDraftStructureSnapshot]
    modifier_structure_snapshots: list[PredictionDraftStructureSnapshot] = Field(default_factory=list)
    input: dict[str, Any]
    schema_version: str
    model_selection: PredictionDraftExactModelSelection
    source_model_snapshot: PredictionDraftSourceModelSnapshot
    source_record_revision: int = Field(ge=1)
    revision: int = Field(ge=1)
    etag: str = Field(min_length=1, max_length=128)
    created_at: datetime
    updated_at: datetime


class ExportCreate(DataStrictModel):
    project_id: PublicId
    resource_refs: list[ResourceRef] = Field(min_length=1)
    format: Literal["json", "csv", "xlsx"]
    include: list[Literal["input_snapshot", "result", "model_version", "warnings"]] = Field(min_length=1)


class ExportAccepted(DataStrictModel):
    id: PublicId
    job_id: PublicId
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED"]
    resource_ref: ResourceRef


class ExportView(DataStrictModel):
    id: PublicId
    job_id: PublicId
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELED", "TIMED_OUT", "INTERRUPTED"]
    format: Literal["json", "csv", "xlsx"]
    format_version: str
    source_snapshots: dict[str, Any]
    artifact_id: PublicId | None = None
    error: dict[str, Any] | None = None
    created_at: datetime
    completed_at: datetime | None = None


class ArchiveRequest(DataStrictModel):
    confirmation_id: PublicId


class ConfirmationConsumeRequest(DataStrictModel):
    confirmation_id: PublicId
    project_id: PublicId
    # The actor is resolved by the server.  It is optional for internal
    # worker adapters and legacy unit fixtures, but when supplied it is part
    # of the confirmation binding and must match the persisted row.
    actor_id: PublicId | None = None
    resource_ref: ResourceRef
    action: Literal["data_import.commit", "resource.archive"]
    revision: int = Field(ge=1)
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class DataCommitRequest(DataStrictModel):
    data_import: DataImport
    validation: DataImportValidation
    domain: DataDomainSchema


# Names used by the API prose and by application adapters.
DataVersionView = DataVersion
FeatureVersionView = FeatureVersion
DataImportView = DataImport
MoleculeView = Molecule


__all__ = [
    "ArchiveRequest",
    "ChromatographyType",
    "ConfirmationConsumeRequest",
    "DataCommitRequest",
    "DataDomainSchema",
    "DataDomainView",
    "DataImport",
    "DataImportCreate",
    "DataImportPatch",
    "DataImportValidation",
    "DataImportView",
    "DataVersion",
    "AsyncAccepted",
    "DataVersionDraft",
    "DataVersionView",
    "DomainField",
    "ExperimentRecord",
    "ExportAccepted",
    "ExportCreate",
    "ExportView",
    "FeatureVersion",
    "FeatureVersionView",
    "ImportMode",
    "Molecule",
    "MoleculeCreate",
    "MoleculeView",
    "PredictionDraftView",
    "PredictionRecord",
    "ValidationCounts",
]
