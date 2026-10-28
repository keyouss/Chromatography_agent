"""Internal, versioned validation report for controlled CSV imports."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CsvImportCounts(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    new: int = Field(ge=0)
    updated: int = Field(ge=0)
    unchanged: int = Field(ge=0)
    invalid: int = Field(ge=0)
    duplicate: int = Field(ge=0)


class CsvImportIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    source_row_number: int = Field(ge=1)
    measurement_id: str | None = Field(default=None, max_length=128)
    field: str | None = Field(default=None, max_length=128)
    raw_value_preview: str = Field(default="", max_length=256)
    raw_value_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    raw_value_truncated: bool = False
    code: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=256)


class CsvImportValidationReport(BaseModel):
    """Private report payload; it is not a public API DTO."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    report_schema_version: Literal["data-import-validation-report-1"] = "data-import-validation-report-1"
    status: Literal["VALID", "INVALID", "UNAVAILABLE", "UNSUPPORTED_IMPORT_MODE"]
    project_id: str = Field(min_length=1, max_length=96)
    import_id: str = Field(min_length=1, max_length=96)
    revision: int = Field(ge=1)
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    domain_id: str = Field(min_length=1, max_length=96)
    domain_schema_version: str = Field(min_length=1, max_length=64)
    source_artifact_id: str = Field(min_length=1, max_length=96)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    encoding: str | None = Field(default=None, max_length=64)
    parser_revision: str = Field(min_length=1, max_length=128)
    row_count: int = Field(ge=0)
    counts: CsvImportCounts
    issues: tuple[CsvImportIssue, ...] = ()
    total_issue_count: int = Field(ge=0)
    issues_truncated: bool = False

    def to_json_bytes(self) -> bytes:
        """Serialize within the report byte limit while preserving truncation evidence."""
        issues = list(self.issues)
        truncated = self.issues_truncated
        while True:
            value = self.model_copy(update={"issues": tuple(issues), "issues_truncated": truncated})
            payload = value.model_dump_json(exclude_none=False).encode("utf-8")
            if len(payload) <= 2 * 1024 * 1024:
                return payload
            if not issues:
                raise ValueError("validation report metadata exceeds the 2 MiB limit")
            issues.pop()
            truncated = True


__all__ = ["CsvImportCounts", "CsvImportIssue", "CsvImportValidationReport"]
