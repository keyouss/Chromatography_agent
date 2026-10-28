"""Strict public DTOs for assistant LLM settings."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import ConfigDict, Field, SecretStr, field_validator

from chrom_platform.core.dto import ResourceRef, StrictModel


class LlmConfigurationCreate(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    api_format: Literal["openai_chat"] = "openai_chat"
    base_url: str = Field(min_length=1, max_length=1000)
    model_id: str = Field(min_length=1, max_length=128)
    api_key: SecretStr | None = None
    reuse_secret_from_configuration_id: str | None = Field(default=None, min_length=1, max_length=96)
    timeout_seconds: int = Field(default=90, ge=1, le=600)
    max_retries: int = Field(default=2, ge=0, le=5)
    max_output_tokens: int = Field(default=4096, ge=1, le=32768)
    external_send_policy: Literal["deny_external", "approved_endpoint_only"] = "deny_external"

    model_config = ConfigDict(extra="forbid", populate_by_name=False)

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and (not value.get_secret_value() or len(value.get_secret_value()) > 4096):
            raise ValueError("api_key must contain 1 to 4096 characters")
        return value


class LlmConfigurationView(StrictModel):
    id: str
    name: str
    revision: str
    etag: str
    api_format: Literal["openai_chat"]
    base_url: str
    model_id: str
    has_api_key: bool
    status: Literal["CANDIDATE", "TESTED", "ACTIVE", "RETIRED"]
    limits: dict[str, int]
    external_send_policy: Literal["deny_external", "approved_endpoint_only"]
    last_test_id: str | None = None
    created_at: datetime


class LlmSettingsView(StrictModel):
    active_configuration: LlmConfigurationView | None = None
    active_revision: int = Field(ge=0)
    candidate_configurations: list[LlmConfigurationView]


class LlmTestAccepted(StrictModel):
    id: str
    job_id: str
    status: Literal["QUEUED"]
    resource_ref: ResourceRef


class LlmTestChecks(StrictModel):
    connectivity: Literal["PASSED", "FAILED", "SKIPPED", "NOT_SUPPORTED"]
    streaming: Literal["PASSED", "FAILED", "SKIPPED", "NOT_SUPPORTED"]
    tool_calling: Literal["PASSED", "FAILED", "SKIPPED", "NOT_SUPPORTED"]
    structured_output: Literal["PASSED", "FAILED", "SKIPPED", "NOT_SUPPORTED"]


class LlmTestResult(StrictModel):
    id: str
    configuration_id: str
    configuration_revision: str
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED"]
    checks: LlmTestChecks
    error: dict[str, object] | None = None
    created_at: datetime
    finished_at: datetime | None = None


class LlmConfigurationTestRequest(StrictModel):
    model_config = ConfigDict(extra="forbid")


class LlmConfigurationActivate(StrictModel):
    test_id: str = Field(min_length=1, max_length=96)
    expected_active_revision: int = Field(ge=0)


__all__ = [
    "LlmConfigurationActivate", "LlmConfigurationCreate",
    "LlmConfigurationTestRequest", "LlmConfigurationView", "LlmSettingsView", "LlmTestChecks",
    "LlmTestAccepted", "LlmTestResult",
]
