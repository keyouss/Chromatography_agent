from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from chrom_platform.core.dto import ResourceRef


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


PublicId = Annotated[str, Field(min_length=1, max_length=96)]


class WarningView(StrictModel):
    code: str
    message: str
    resource_ref: ResourceRef | None = None
    field: str | None = None


class NormalizeStructureRequest(StrictModel):
    # The server binds an omitted project to the verified default/selection;
    # a supplied value is still checked against the same server-side scope.
    project_id: PublicId | None = None
    input_type: Literal["smiles"]
    value: Annotated[str, Field(min_length=1, max_length=20_000)]
    preserve_stereochemistry: Literal[True]


class OriginalInput(StrictModel):
    input_type: Literal["smiles"]
    value: str
    source_artifact_id: PublicId | None = None


class StructureView(StrictModel):
    id: PublicId
    project_id: PublicId
    schema_version: str
    original_input: OriginalInput
    canonical_smiles: str
    is_valid: Literal[True]
    has_stereochemistry: bool
    stereochemistry_status: Literal["specified", "unspecified", "not_applicable"]
    stereochemistry_warnings: list[WarningView]
    snapshot_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    created_at: datetime


class StructureEnvelope(StrictModel):
    data: StructureView
    warnings: list[WarningView]
    request_id: PublicId
