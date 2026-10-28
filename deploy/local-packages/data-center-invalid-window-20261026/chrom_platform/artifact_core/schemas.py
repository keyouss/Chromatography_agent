"""Strict public contracts for the controlled artifact service."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, field_validator

from chrom_platform.core.dto import ResourceRef, StrictModel, WarningDTO


PublicId = Annotated[str, Field(min_length=1, max_length=96)]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

UploadKind = Literal[
    "structure_input",
    "dataset_package",
    "knowledge_document",
    "model_package",
    "assistant_attachment",
]
ArtifactKind = Literal[
    "structure_input",
    "dataset_package",
    "knowledge_document",
    "model_package",
    "assistant_attachment",
    "context_snapshot",
    "diagnostic_bundle",
    "export_report",
]
UploadStatus = Literal["CREATED", "RECEIVED", "COMPLETED", "EXPIRED", "FAILED"]
ArtifactStatus = Literal[
    "UPLOADING",
    "VALIDATING",
    "AVAILABLE",
    "QUARANTINED",
    "FAILED",
    "ARCHIVED",
    "DELETING",
    "DELETED",
]
ScanStatus = Literal["PENDING", "PASSED", "REJECTED", "NOT_CONFIGURED", "FAILED"]


class UploadCreate(StrictModel):
    project_id: PublicId
    kind: UploadKind
    file_name: Annotated[str, Field(min_length=1, max_length=255)]
    media_type: Annotated[str, Field(min_length=1, max_length=255)]
    size_bytes: Annotated[int, Field(gt=0)]
    expected_sha256: Sha256 | None = None

    @field_validator("file_name")
    @classmethod
    def safe_file_name(cls, value: str) -> str:
        if "\x00" in value or "/" in value or "\\" in value or value in {".", ".."}:
            raise ValueError("file_name 只能是单个文件名")
        if any(ord(char) < 32 for char in value):
            raise ValueError("file_name 含有控制字符")
        return value


class UploadView(StrictModel):
    id: PublicId
    artifact_id: PublicId
    status: UploadStatus
    content_url: str
    allowed_media_types: list[str]
    max_size_bytes: int
    expires_at: datetime
    received_bytes: int


class UploadReceipt(StrictModel):
    upload_id: PublicId
    artifact_id: PublicId
    received_bytes: int
    sha256: Sha256
    received_at: datetime


class ArtifactView(StrictModel):
    id: PublicId
    project_id: PublicId
    owner_id: PublicId
    kind: ArtifactKind
    file_name: str
    media_type: str
    size_bytes: int
    sha256: Sha256 | None
    status: ArtifactStatus
    scan_status: ScanStatus
    validation_report_ref: ResourceRef | None = None
    source_ref: ResourceRef | None = None
    retention_until: datetime | None = None
    created_at: datetime
    updated_at: datetime
    download_allowed: bool
    warnings: list[WarningDTO] = Field(default_factory=list)


class ArtifactAccepted(StrictModel):
    id: PublicId
    artifact_id: PublicId
    job_id: PublicId
    status: ArtifactStatus
    resource_ref: ResourceRef


class ArchiveRequest(StrictModel):
    confirmation_id: PublicId


class ArtifactDeleteAccepted(StrictModel):
    id: PublicId
    job_id: PublicId
    status: Literal["DELETING"]
    resource_ref: ResourceRef


class CompleteUploadRequest(StrictModel):
    """The complete endpoint intentionally accepts an empty JSON object only."""

    pass


class ArtifactEnvelope(StrictModel):
    data: ArtifactView
    warnings: list[WarningDTO] = Field(default_factory=list)
    request_id: PublicId


class UploadEnvelope(StrictModel):
    data: UploadView
    warnings: list[WarningDTO] = Field(default_factory=list)
    request_id: PublicId


class UploadReceiptEnvelope(StrictModel):
    data: UploadReceipt
    warnings: list[WarningDTO] = Field(default_factory=list)
    request_id: PublicId


class ArtifactAcceptedEnvelope(StrictModel):
    data: ArtifactAccepted
    warnings: list[WarningDTO] = Field(default_factory=list)
    request_id: PublicId


class ArtifactDeleteEnvelope(StrictModel):
    data: ArtifactDeleteAccepted
    warnings: list[WarningDTO] = Field(default_factory=list)
    request_id: PublicId


__all__ = [
    "ArtifactAccepted",
    "ArtifactDeleteAccepted",
    "ArtifactDeleteEnvelope",
    "ArtifactEnvelope",
    "ArtifactKind",
    "ArtifactStatus",
    "ArtifactView",
    "ArchiveRequest",
    "CompleteUploadRequest",
    "ScanStatus",
    "UploadCreate",
    "UploadKind",
    "UploadEnvelope",
    "UploadReceipt",
    "UploadReceiptEnvelope",
    "UploadStatus",
    "UploadView",
]
