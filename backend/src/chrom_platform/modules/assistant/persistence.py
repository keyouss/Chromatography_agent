"""SQLAlchemy persistence for the Assistant module.

The application owns the transaction.  This module stages rows and flushes;
it never commits or closes a caller supplied Session. Worker claim/finish use
short transactions and a monotonic fencing token so stale workers cannot
overwrite a newer attempt.
"""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import base64
from threading import RLock
from typing import Any, Callable, Iterator, Mapping
from uuid import uuid4

from sqlalchemy import BigInteger, Boolean, CheckConstraint, ForeignKeyConstraint, Index, Integer, JSON, MetaData, String, UniqueConstraint, delete, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from chrom_platform.infrastructure.db import MYSQL_TABLE_KWARGS, NAMING_CONVENTION, UTCDateTime
from chrom_platform.infrastructure.models import Project as _Project, Job, JobAttempt, JobEvent  # noqa: F401 - registers M1 metadata
from .errors import public_error, unavailable
from .schemas import AgentRun, Attempt, Conversation, Message, PendingConfirmationBinding, ToolCall


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


_SECRET_PARTS = {"password", "secret", "token", "apikey", "api_key", "authorization", "credential", "credentials", "raw_response", "raw_result", "reasoning", "thinking"}


class AssistantBase(DeclarativeBase):
    """Isolated metadata keeps optional Assistant tables out of M1 metadata."""
    # Keep this registry independent from M1 while using the platform's
    # canonical names.  The names are part of the frozen 0006 contract and
    # are needed for Alembic/MySQL schema comparison and downgrade.
    # M1's historical convention prefixes explicit check names a second time.
    # M4 is frozen with the literal ``ck_*`` names in 0006, so retain the
    # shared PK/FK/index/unique rules while preserving those check names.
    _assistant_naming = {**NAMING_CONVENTION, "ck": "%(constraint_name)s"}
    metadata = MetaData(naming_convention=_assistant_naming)


# Compatibility export for callers that expect a module-level Base.
Base = AssistantBase
_PROJECT_ID = _Project.__table__.c.id


def _safe(value: Any, depth: int = 0) -> Any:
    if depth > 12:
        return "[REDACTED_DEPTH]"
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            name = str(key).lower().replace("-", "_")
            if name in _SECRET_PARTS or any(part in name for part in ("api_key", "token", "secret", "password", "reasoning", "thinking", "credential")):
                continue
            result[str(key)] = _safe(item, depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [_safe(item, depth + 1) for item in value[:100]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "model_dump"):
        return _safe(value.model_dump(mode="json"), depth + 1)
    return str(value)


def _json(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _safe(value.model_dump(mode="json"))
    if is_dataclass(value):
        return _safe(asdict(value))
    return _safe(value)


def _contains_any_id(value: Any, identifiers: set[str]) -> bool:
    if isinstance(value, Mapping):
        return any(_contains_any_id(item, identifiers) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_any_id(item, identifiers) for item in value)
    return isinstance(value, str) and value in identifiers


class ConversationRow(AssistantBase):
    __tablename__ = "conversation"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(96), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(96), nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    current_run_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    last_message_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    etag: Mapped[str] = mapped_column(String(128), nullable=False)
    __table_args__ = (ForeignKeyConstraint(("project_id",), (_PROJECT_ID,), name="fk_conversation_project_id_project", ondelete="RESTRICT"), CheckConstraint("mode IN ('knowledge','business')", name="ck_conversation_mode"), CheckConstraint("status IN ('ACTIVE','ARCHIVED')", name="ck_conversation_status"), CheckConstraint("revision >= 1", name="ck_conversation_revision_positive"), Index("ix_conversation_project_owner_updated", "project_id", "owner_id", "updated_at", "id"), MYSQL_TABLE_KWARGS)


class AgentRunRow(AssistantBase):
    __tablename__ = "agent_run"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(96), nullable=False)
    project_id: Mapped[str] = mapped_column(String(96), nullable=False)
    input_message_id: Mapped[str] = mapped_column(String(96), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    dispatch_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    provider_configuration_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    provider_configuration_revision: Mapped[str | None] = mapped_column(String(128), nullable=True)
    provider_configuration_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    toolset_version: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    pending_actions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    related_resources: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    usage: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    last_event_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    etag: Mapped[str] = mapped_column(String(128), nullable=False)
    fencing_token: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    __table_args__ = (ForeignKeyConstraint(("conversation_id",), ("conversation.id",), name="fk_agent_run_conversation_id_conversation", ondelete="RESTRICT"), ForeignKeyConstraint(("project_id",), (_PROJECT_ID,), name="fk_agent_run_project_id_project", ondelete="RESTRICT"), CheckConstraint("mode IN ('knowledge','business')", name="ck_agent_run_mode"), CheckConstraint("attempt >= 1", name="ck_agent_run_attempt_positive"), CheckConstraint("last_event_seq >= 0", name="ck_agent_run_last_event_seq_nonnegative"), CheckConstraint("revision >= 1", name="ck_agent_run_revision_positive"), CheckConstraint("status IN ('QUEUED','RUNNING','WAITING_CONFIRMATION','CANCEL_REQUESTED','SUCCEEDED','FAILED','CANCELED','TIMED_OUT','INTERRUPTED')", name="ck_agent_run_status"), Index("ix_agent_run_conversation_created", "conversation_id", "created_at", "id"), Index("ix_agent_run_project_status", "project_id", "status", "updated_at"), MYSQL_TABLE_KWARGS)


class MessageRow(AssistantBase):
    __tablename__ = "message"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(96), nullable=False)
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    agent_run_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    blocks: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    attachment_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    client_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    __table_args__ = (ForeignKeyConstraint(("conversation_id",), ("conversation.id",), name="fk_message_conversation_id_conversation", ondelete="RESTRICT"), ForeignKeyConstraint(("agent_run_id",), ("agent_run.id",), name="fk_message_agent_run_id_agent_run", ondelete="RESTRICT"), UniqueConstraint("conversation_id", "seq", name="uq_message_conversation_seq"), UniqueConstraint("conversation_id", "client_message_id", name="uq_message_conversation_client_message"), CheckConstraint("seq >= 1", name="ck_message_seq_positive"), CheckConstraint("role IN ('user','assistant')", name="ck_message_role"), CheckConstraint("status IN ('FINAL','PARTIAL','FAILED')", name="ck_message_status"), Index("ix_message_conversation_created", "conversation_id", "created_at", "id"), MYSQL_TABLE_KWARGS)


class AssistantFrameworkStateRow(AssistantBase):
    """Private, trusted PydanticAI history required to resume a Run."""

    __tablename__ = "assistant_framework_state"
    agent_run_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(96), nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(96), nullable=False)
    engine: Mapped[str] = mapped_column(String(32), nullable=False, default="pydantic-ai", server_default="pydantic-ai")
    framework_version: Mapped[str] = mapped_column(String(128), nullable=False)
    serialization_version: Mapped[str] = mapped_column(String(128), nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # This is private framework state.  Keep it out of the public DTO/event
    # sanitizers so trusted message history and tool args/results survive.
    state: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    stream_message_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(("agent_run_id",), ("agent_run.id",), name="fk_assistant_framework_state_agent_run_id_agent_run", ondelete="RESTRICT"),
        ForeignKeyConstraint(("project_id",), (_PROJECT_ID,), name="fk_assistant_framework_state_project_id_project", ondelete="RESTRICT"),
        ForeignKeyConstraint(("conversation_id",), ("conversation.id",), name="fk_assistant_framework_state_conversation_id_conversation", ondelete="RESTRICT"),
        ForeignKeyConstraint(("stream_message_id",), ("message.id",), name="fk_assistant_framework_state_stream_message_id_message", ondelete="RESTRICT"),
        CheckConstraint("engine = 'pydantic-ai'", name="ck_assistant_framework_state_engine"),
        CheckConstraint("CHAR_LENGTH(config_hash) = 64", name="ck_assistant_framework_state_config_hash_length"),
        MYSQL_TABLE_KWARGS,
    )


class AgentAttemptRow(AssistantBase):
    __tablename__ = "agent_attempt"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    agent_run_id: Mapped[str] = mapped_column(String(96), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    dispatch_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    execution_token: Mapped[str | None] = mapped_column(String(128), nullable=True)
    fencing_token: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    result_ref: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    __table_args__ = (ForeignKeyConstraint(("agent_run_id",), ("agent_run.id",), name="fk_agent_attempt_agent_run_id_agent_run", ondelete="RESTRICT"), UniqueConstraint("agent_run_id", "attempt", name="uq_agent_attempt_run_attempt"), CheckConstraint("attempt >= 1", name="ck_agent_attempt_attempt_positive"), CheckConstraint("status IN ('QUEUED','RUNNING','WAITING_CONFIRMATION','CANCEL_REQUESTED','SUCCEEDED','FAILED','CANCELED','TIMED_OUT','INTERRUPTED')", name="ck_agent_attempt_status"), Index("ix_agent_attempt_lease", "status", "lease_until"), MYSQL_TABLE_KWARGS)


class AgentEventRow(AssistantBase):
    __tablename__ = "agent_event"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    stream_type: Mapped[str] = mapped_column(String(32), nullable=False)
    stream_id: Mapped[str] = mapped_column(String(96), nullable=False)
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    type: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    timestamp: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    agent_run_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    __table_args__ = (UniqueConstraint("stream_type", "stream_id", "seq", name="uq_agent_event_stream_seq"), CheckConstraint("seq >= 0", name="ck_agent_event_seq_nonnegative"), Index("ix_agent_event_stream_timestamp", "stream_type", "stream_id", "timestamp"), MYSQL_TABLE_KWARGS)


class ToolCallRow(AssistantBase):
    __tablename__ = "tool_call"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    agent_run_id: Mapped[str] = mapped_column(String(96), nullable=False)
    agent_attempt_id: Mapped[str] = mapped_column(String(96), nullable=False)
    provider_tool_call_id: Mapped[str] = mapped_column(String(128), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    tool_schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    arguments_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    binding_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    arguments_summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    resource_ref: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    confirmation_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    pending_confirmation: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    result_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    __table_args__ = (ForeignKeyConstraint(("agent_run_id",), ("agent_run.id",), name="fk_tool_call_agent_run_id_agent_run", ondelete="RESTRICT"), ForeignKeyConstraint(("agent_attempt_id",), ("agent_attempt.id",), name="fk_tool_call_agent_attempt_id_agent_attempt", ondelete="RESTRICT"), CheckConstraint("status IN ('PROPOSED','VALIDATING','WAITING_CONFIRMATION','RUNNING','SUCCEEDED','FAILED','REJECTED','INTERRUPTED')", name="ck_tool_call_status"), CheckConstraint("arguments_hash IS NULL OR CHAR_LENGTH(arguments_hash) = 64", name="ck_tool_call_arguments_hash_length"), CheckConstraint("binding_hash IS NULL OR CHAR_LENGTH(binding_hash) = 64", name="ck_tool_call_binding_hash_length"), Index("ix_tool_call_run_started", "agent_run_id", "started_at", "id"), MYSQL_TABLE_KWARGS)


class LlmConfigurationRow(AssistantBase):
    __tablename__ = "llm_configuration"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(96), nullable=False)
    revision: Mapped[str] = mapped_column(String(128), nullable=False)
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    key_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    last_test_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    __table_args__ = (ForeignKeyConstraint(("project_id",), (_PROJECT_ID,), name="fk_llm_configuration_project_id_project", ondelete="RESTRICT"), UniqueConstraint("project_id", "revision", name="uq_llm_configuration_project_revision"), CheckConstraint("status IN ('CANDIDATE','TESTED','ACTIVE','RETIRED')", name="ck_llm_configuration_status"), Index("ix_llm_configuration_project_status", "project_id", "status", "created_at"), MYSQL_TABLE_KWARGS)


class LlmConfigurationActiveRow(AssistantBase):
    __tablename__ = "llm_configuration_active"
    project_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    configuration_id: Mapped[str] = mapped_column(String(96), nullable=False, unique=True)
    configuration_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    etag: Mapped[str] = mapped_column(String(128), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    __table_args__ = (ForeignKeyConstraint(("project_id",), (_PROJECT_ID,), name="fk_llm_configuration_active_project_id_project", ondelete="RESTRICT"), ForeignKeyConstraint(("configuration_id",), ("llm_configuration.id",), name="fk_llm_configuration_active_configuration_id_llm_configuration", ondelete="RESTRICT"), CheckConstraint("revision >= 1", name="ck_llm_configuration_active_revision_positive"), MYSQL_TABLE_KWARGS)


class LlmCallRow(AssistantBase):
    __tablename__ = "llm_call"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    agent_run_id: Mapped[str] = mapped_column(String(96), nullable=False)
    agent_attempt_id: Mapped[str] = mapped_column(String(96), nullable=False)
    request_index: Mapped[int] = mapped_column(Integer, nullable=False)
    transport_attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_configuration_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    provider_configuration_revision: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    model_parameters_summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    usage: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    estimates: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    logical_context_snapshot_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    provider_request_snapshot_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    __table_args__ = (ForeignKeyConstraint(("agent_run_id",), ("agent_run.id",), name="fk_llm_call_agent_run_id_agent_run", ondelete="RESTRICT"), ForeignKeyConstraint(("agent_attempt_id",), ("agent_attempt.id",), name="fk_llm_call_agent_attempt_id_agent_attempt", ondelete="RESTRICT"), CheckConstraint("request_index >= 1", name="ck_llm_call_request_index_positive"), CheckConstraint("transport_attempt >= 1", name="ck_llm_call_transport_attempt_positive"), CheckConstraint("status IN ('CREATED','RUNNING','SUCCEEDED','FAILED','INTERRUPTED')", name="ck_llm_call_status"), Index("ix_llm_call_run_request", "agent_run_id", "request_index", "transport_attempt"), MYSQL_TABLE_KWARGS)


class ContextSnapshotRow(AssistantBase):
    __tablename__ = "context_snapshot"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    llm_call_id: Mapped[str] = mapped_column(String(96), nullable=False)
    project_id: Mapped[str] = mapped_column(String(96), nullable=False)
    view: Mapped[str] = mapped_column(String(32), nullable=False)
    capture_state: Mapped[str] = mapped_column(String(16), nullable=False)
    capture_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_schema_version: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    sources: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    redactions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    hash_scope: Mapped[str] = mapped_column(String(64), nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    __table_args__ = (ForeignKeyConstraint(("llm_call_id",), ("llm_call.id",), name="fk_context_snapshot_llm_call_id_llm_call", ondelete="RESTRICT"), ForeignKeyConstraint(("project_id",), (_PROJECT_ID,), name="fk_context_snapshot_project_id_project", ondelete="RESTRICT"), CheckConstraint("capture_state IN ('AVAILABLE','DISABLED','NOT_CAPTURED','EXPIRED','REDACTED','FAILED')", name="ck_context_snapshot_capture_state"), CheckConstraint("sha256 IS NULL OR CHAR_LENGTH(sha256) = 64", name="ck_context_snapshot_sha256_length"), Index("ix_context_snapshot_project_created", "project_id", "created_at"), MYSQL_TABLE_KWARGS)


class LlmTestResultRow(AssistantBase):
    __tablename__ = "llm_test_result"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    configuration_id: Mapped[str] = mapped_column(String(96), nullable=False)
    configuration_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    configuration_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    checks: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    __table_args__ = (ForeignKeyConstraint(("configuration_id",), ("llm_configuration.id",), name="fk_llm_test_result_configuration_id_llm_configuration", ondelete="RESTRICT"), CheckConstraint("configuration_hash IS NULL OR CHAR_LENGTH(configuration_hash) = 64", name="ck_llm_test_result_configuration_hash_length"), CheckConstraint("status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')", name="ck_llm_test_result_status"), Index("ix_llm_test_result_configuration_created", "configuration_id", "created_at"), Index("ix_llm_test_result_status_created", "status", "created_at"), MYSQL_TABLE_KWARGS)


class PendingConfirmationRow(AssistantBase):
    __tablename__ = "agent_pending_confirmation"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    agent_run_id: Mapped[str] = mapped_column(String(96), nullable=False)
    tool_call_id: Mapped[str] = mapped_column(String(96), nullable=False)
    project_id: Mapped[str] = mapped_column(String(96), nullable=False)
    confirmation_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    resource_ref: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    binding_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    normalized_args: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    __table_args__ = (ForeignKeyConstraint(("agent_run_id",), ("agent_run.id",), name="fk_agent_pending_confirmation_agent_run_id_agent_run", ondelete="RESTRICT"), ForeignKeyConstraint(("tool_call_id",), ("tool_call.id",), name="fk_agent_pending_confirmation_tool_call_id_tool_call", ondelete="RESTRICT"), ForeignKeyConstraint(("project_id",), (_PROJECT_ID,), name="fk_agent_pending_confirmation_project_id_project", ondelete="RESTRICT"), CheckConstraint("revision >= 1", name="ck_agent_pending_confirmation_revision_positive"), CheckConstraint("CHAR_LENGTH(config_hash) = 64", name="ck_agent_pending_confirmation_config_hash_length"), CheckConstraint("CHAR_LENGTH(binding_hash) = 64", name="ck_agent_pending_confirmation_binding_hash_length"), CheckConstraint("status IN ('PENDING','CONFIRMED','CONSUMED','EXPIRED','INVALIDATED')", name="ck_agent_pending_confirmation_status"), Index("ix_agent_pending_confirmation_run_status", "agent_run_id", "status"), MYSQL_TABLE_KWARGS)


def _conversation(row):
    return None if row is None else Conversation(id=row.id, project_id=row.project_id, owner_id=row.owner_id, title=row.title, mode=row.mode, status=row.status, current_run_id=row.current_run_id, last_message_at=_aware(row.last_message_at), created_at=_aware(row.created_at), updated_at=_aware(row.updated_at), revision=row.revision, etag=row.etag)


def _message(row):
    return None if row is None else Message(id=row.id, conversation_id=row.conversation_id, seq=str(row.seq), role=row.role, agent_run_id=row.agent_run_id, status=row.status, blocks=row.blocks or [], attachment_ids=row.attachment_ids or [], created_at=_aware(row.created_at))


def _public_error(value):
    return public_error(value)


def _execution_error(value, status):
    if status == "SUCCEEDED" or (value is None and status in {
        "QUEUED", "RUNNING", "WAITING_CONFIRMATION", "CANCEL_REQUESTED", "CANCELED",
    }):
        return None
    return _public_error(value)


def _public_usage(value):
    """Project persisted usage into the strict public AgentRun shape.

    Worker rows may contain internal accounting fields such as
    ``transport_attempts``.  Those fields stay in storage for diagnostics but
    must not make a failed run unqueryable through the public schema.
    """
    raw = value if isinstance(value, dict) else {}
    return {
        key: raw[key]
        for key in ("input_tokens", "output_tokens", "model_requests", "tool_calls")
        if key in raw
    }


def _run(row):
    if row is None:
        return None
    status = str(row.status)
    public_error_value = _execution_error(row.error, status)
    return AgentRun(id=row.id, conversation_id=row.conversation_id, project_id=row.project_id, input_message_id=row.input_message_id, mode=row.mode, status=row.status, attempt=row.attempt, provider_configuration_id=row.provider_configuration_id, provider_configuration_revision=row.provider_configuration_revision, provider_configuration_hash=row.provider_configuration_hash, model_id=row.model_id, prompt_version=row.prompt_version, toolset_version=row.toolset_version, policy_version=row.policy_version, pending_actions=[PendingConfirmationBinding.model_validate(x) for x in (row.pending_actions or [])], related_resources=row.related_resources or [], usage=_public_usage(row.usage), last_event_seq=str(row.last_event_seq or 0), error=public_error_value, started_at=_aware(row.started_at), finished_at=_aware(row.finished_at), created_at=_aware(row.created_at), updated_at=_aware(row.updated_at), revision=row.revision, etag=row.etag)


def _attempt(row):
    return None if row is None else Attempt(id=row.id, agent_run_id=row.agent_run_id, attempt=row.attempt, status=row.status, created_at=_aware(row.created_at), started_at=_aware(row.started_at), finished_at=_aware(row.finished_at), error=_execution_error(row.error, str(row.status)))


def _tool(row):
    if row is None:
        return None
    from chrom_platform.core.dto import ResourceRef
    pending = PendingConfirmationBinding.model_validate(row.pending_confirmation) if row.pending_confirmation else None
    # ``NULL`` is the durable representation for a ToolCall without an
    # execution error.  Projecting it through ``public_error`` would invent
    # ``INTERNAL_ERROR`` on every successful (and every in-flight) call.
    status = str(getattr(row.status, "value", row.status))
    error = None if status == "SUCCEEDED" or row.error is None and status not in {"FAILED", "INTERRUPTED", "REJECTED"} else _public_error(row.error)
    return ToolCall(id=row.id, agent_run_id=row.agent_run_id, agent_attempt_id=row.agent_attempt_id, provider_tool_call_id=row.provider_tool_call_id, tool_name=row.tool_name, tool_schema_version=row.tool_schema_version, arguments_hash=row.arguments_hash, binding_hash=row.binding_hash, status=row.status, arguments_summary=row.arguments_summary or {}, resource_ref=ResourceRef.model_validate(row.resource_ref) if row.resource_ref else None, confirmation_id=row.confirmation_id, pending_confirmation=pending, result_summary=row.result_summary, job_id=row.job_id, error=error, started_at=_aware(row.started_at), finished_at=_aware(row.finished_at))


class AssistantPersistenceUnavailable(RuntimeError):
    pass


class SessionAssistantUnitOfWork:
    """Caller-owned Session boundary; no implicit commit or close."""
    def __init__(self, session: object | None = None, session_factory: Callable[[], object] | None = None):
        self.session, self.session_factory = session, session_factory

    @contextmanager
    def transaction(self, session: object | None = None) -> Iterator[object]:
        current = session if session is not None else self.session
        if current is not None:
            yield current
            return
        if self.session_factory is None:
            raise unavailable("Assistant SQL Session 未配置")
        db = self.session_factory()
        try:
            manager = db.begin() if callable(getattr(db, "begin", None)) else nullcontext()
            with manager:
                yield db
        except Exception:
            rollback = getattr(db, "rollback", None)
            if callable(rollback):
                rollback()
            raise
        finally:
            close = getattr(db, "close", None)
            if callable(close):
                close()


class SQLAlchemyAssistantRepository:
    is_test_only = False
    def __init__(self, session: Session | None = None, *, session_factory: Callable[[], Session] | None = None, retention_days: int = 7):
        self.session, self.session_factory = session, session_factory
        self.retention_days = max(1, int(retention_days))
        self.uow = SessionAssistantUnitOfWork(session, session_factory)
    def transaction(self, session: Session | None = None): return self.uow.transaction(session)
    @staticmethod
    def _one(session, statement, *, for_update=False, refresh=False):
        if refresh:
            statement = statement.execution_options(populate_existing=True)
        return session.scalar(statement.with_for_update() if for_update else statement)
    @classmethod
    def _row(cls, session, row_cls, ident, *, for_update=False, refresh=False):
        return cls._one(session, select(row_cls).where(row_cls.id == ident), for_update=for_update, refresh=refresh)
    @staticmethod
    def _flush(session):
        if callable(getattr(session, "flush", None)): session.flush()

    def lock_conversation(self, session, conversation_id): return _conversation(self._one(session, select(ConversationRow).where(ConversationRow.id == conversation_id), for_update=True))
    def get_conversation(self, session, conversation_id): return _conversation(self._row(session, ConversationRow, conversation_id))
    def list_conversations(self, session, *, project_id, owner_id, archived, q, limit):
        statement=select(ConversationRow).where(ConversationRow.owner_id == owner_id)
        if project_id is not None: statement=statement.where(ConversationRow.project_id == project_id)
        if archived is not None: statement=statement.where(ConversationRow.status == ("ARCHIVED" if archived else "ACTIVE"))
        if q: statement=statement.where(ConversationRow.title.like(f"%{q}%"))
        rows=session.scalars(statement.order_by(ConversationRow.updated_at.desc(), ConversationRow.id.desc()).limit(max(1,min(limit,200))))
        return [_conversation(row) for row in rows]
    def get_conversation_deletion(self, session, conversation_id):
        from chrom_platform.infrastructure.models import AuditEvent
        return self._one(session, select(AuditEvent).where(
            AuditEvent.resource_type == "conversation",
            AuditEvent.resource_id == conversation_id,
            AuditEvent.action == "assistant.conversation.deleted",
            AuditEvent.result == "SUCCEEDED",
        ).order_by(AuditEvent.timestamp.desc(), AuditEvent.id.desc()).limit(1))
    def lock_conversation_runs(self, session, conversation_id):
        rows = session.scalars(select(AgentRunRow).where(
            AgentRunRow.conversation_id == conversation_id,
        ).order_by(AgentRunRow.id).with_for_update()).all()
        return [_run(row) for row in rows]
    def conversation_message_ids(self, session, conversation_id):
        return list(session.scalars(select(MessageRow.id).where(
            MessageRow.conversation_id == conversation_id,
        ).order_by(MessageRow.id).with_for_update()).all())
    def conversation_delete_busy(self, session, conversation_id):
        terminal_runs = {"SUCCEEDED", "FAILED", "CANCELED", "TIMED_OUT", "INTERRUPTED"}
        terminal_jobs = terminal_runs | {"PARTIAL"}
        # Workers take Job -> JobAttempt -> AgentRun. Keep the same order so a
        # delete racing a worker cannot form the inverse lock cycle.
        snapshots = [_run(row) for row in session.scalars(select(AgentRunRow).where(
            AgentRunRow.conversation_id == conversation_id,
        ).order_by(AgentRunRow.id)).all()]
        job_ids = {}
        for run in snapshots:
            job_id = self._shared_job_id(run, session=session)
            if job_id is not None:
                job_ids[run.id] = job_id
        jobs = {}
        for job_id in sorted(set(job_ids.values())):
            job = self._row(session, Job, job_id, for_update=True, refresh=True)
            if job is not None:
                jobs[job_id] = job
        job_attempts = {}
        for job_id in sorted(jobs):
            job_attempts[job_id] = session.scalars(select(JobAttempt).where(
                JobAttempt.job_id == job_id,
            ).order_by(JobAttempt.id).with_for_update()).all()

        runs = self.lock_conversation_runs(session, conversation_id)
        busy = any(run.status not in terminal_runs for run in runs)
        attempts_by_run = {}
        for run in runs:
            attempts_by_run[run.id] = session.scalars(select(AgentAttemptRow).where(
                AgentAttemptRow.agent_run_id == run.id,
            ).order_by(AgentAttemptRow.id).with_for_update()).all()
        busy = busy or any(
            attempt.status not in terminal_runs
            for attempts in attempts_by_run.values() for attempt in attempts
        )
        for run in runs:
            job_id = job_ids.get(run.id)
            job = jobs.get(job_id)
            if job is None or job.kind != "assistant" or str(job.project_id) != str(run.project_id) or dict(job.resource_ref or {}) != {"type": "agent_run", "id": run.id}:
                continue
            if str(job.status) not in terminal_jobs or any(
                str(attempt.status) not in terminal_jobs for attempt in job_attempts.get(job_id, ())
            ):
                busy = True
        return busy
    def delete_conversation_data(self, session, conversation_id, *, run_ids, message_ids, actor_id, project_id):
        """Erase private Assistant rows while retaining audit and shared job history."""
        from sqlalchemy import or_, update
        from chrom_platform.infrastructure.models import Confirmation, IdempotencyRecord

        runs = session.scalars(select(AgentRunRow).where(
            AgentRunRow.conversation_id == conversation_id,
        ).order_by(AgentRunRow.id).with_for_update()).all()
        actual_run_ids = {str(row.id) for row in runs}
        if actual_run_ids != {str(value) for value in run_ids}:
            raise ValueError("conversation run graph changed during deletion")
        messages = session.scalars(select(MessageRow).where(
            MessageRow.conversation_id == conversation_id,
        ).order_by(MessageRow.id).with_for_update()).all()
        actual_message_ids = {str(row.id) for row in messages}
        if actual_message_ids != {str(value) for value in message_ids}:
            raise ValueError("conversation message graph changed during deletion")

        # The frozen 0006 migration has two cyclic FKs absent from ORM metadata.
        # Break only nullable edges; input_message_id remains NOT NULL until its Run is deleted.
        session.execute(update(ConversationRow).where(
            ConversationRow.id == conversation_id,
        ).values(current_run_id=None))
        session.execute(update(MessageRow).where(
            MessageRow.conversation_id == conversation_id,
        ).values(agent_run_id=None))

        private_ids = actual_run_ids | actual_message_ids | {str(conversation_id)}
        confirmation_rows = session.scalars(select(Confirmation).where(
            Confirmation.project_id == project_id,
            Confirmation.resource_ref["type"].as_string() == "conversation",
            Confirmation.resource_ref["id"].as_string() == str(conversation_id),
        ).order_by(Confirmation.id).with_for_update()).all()
        target_ref = {"type": "conversation", "id": str(conversation_id)}
        removed_confirmation_ids = {str(row.id) for row in confirmation_rows if dict(row.resource_ref or {}) == target_ref}
        private_ids.update(removed_confirmation_ids)
        if actual_run_ids:
            private_ids.update(str(value) for value in session.scalars(select(ToolCallRow.id).where(
                ToolCallRow.agent_run_id.in_(actual_run_ids),
            )).all())
            llm_call_ids = list(session.scalars(select(LlmCallRow.id).where(
                LlmCallRow.agent_run_id.in_(actual_run_ids),
            )).all())
            private_ids.update(str(value) for value in llm_call_ids)
            if llm_call_ids:
                private_ids.update(str(value) for value in session.scalars(select(ContextSnapshotRow.id).where(
                    ContextSnapshotRow.llm_call_id.in_(llm_call_ids),
                )).all())

        assistant_routes = {
            "/api/v1/conversations",
            "/api/v1/conversations/{conversation_id}",
            "/api/v1/conversations/{conversation_id}/archive",
            "/api/v1/conversations/{conversation_id}/messages",
            "/api/v1/agent-runs/{run_id}/cancel",
            "/api/v1/agent-runs/{run_id}/resume",
        }
        idem_rows = session.scalars(select(IdempotencyRecord).where(
            IdempotencyRecord.actor_id == actor_id,
            IdempotencyRecord.project_id == project_id,
            IdempotencyRecord.status == "COMPLETED",
            IdempotencyRecord.result_resource_ref.is_not(None),
            or_(
                IdempotencyRecord.route_identity.in_(assistant_routes),
                IdempotencyRecord.route_identity.like("/api/v1/confirmations%"),
            ),
        ).order_by(IdempotencyRecord.id).with_for_update()).all()
        marker = {"_deleted_resource": {"type": "conversation", "id": str(conversation_id)}}
        for row in idem_rows:
            if _contains_any_id(row.result_resource_ref, private_ids):
                row.result_resource_ref = marker
                row.result_status_code = 404
                row.completed_at = _now()

        for row in confirmation_rows:
            if str(row.id) in removed_confirmation_ids:
                session.delete(row)

        if actual_run_ids:
            llm_call_ids = list(session.scalars(select(LlmCallRow.id).where(
                LlmCallRow.agent_run_id.in_(actual_run_ids),
            ).with_for_update()).all())
            if llm_call_ids:
                session.execute(delete(ContextSnapshotRow).where(ContextSnapshotRow.llm_call_id.in_(llm_call_ids)))
                session.execute(delete(LlmCallRow).where(LlmCallRow.id.in_(llm_call_ids)))
            session.execute(delete(AssistantFrameworkStateRow).where(
                AssistantFrameworkStateRow.agent_run_id.in_(actual_run_ids),
            ))
            session.execute(delete(PendingConfirmationRow).where(
                PendingConfirmationRow.agent_run_id.in_(actual_run_ids),
            ))
            session.execute(delete(ToolCallRow).where(ToolCallRow.agent_run_id.in_(actual_run_ids)))
            session.execute(delete(AgentEventRow).where(or_(
                AgentEventRow.agent_run_id.in_(actual_run_ids),
                (AgentEventRow.stream_type == "agent_run") & AgentEventRow.stream_id.in_(actual_run_ids),
            )))
            session.execute(delete(AgentAttemptRow).where(AgentAttemptRow.agent_run_id.in_(actual_run_ids)))

        # Preserve the platform Job and its status-only JobEvents as a safe tombstone.
        for run in runs:
            value = _run(run)
            job_id = self._shared_job_id(value, session=session)
            if not job_id:
                continue
            job = self._row(session, Job, job_id, for_update=True, refresh=True)
            if (job is None or job.kind != "assistant" or str(job.project_id) != str(project_id)
                    or dict(job.resource_ref or {}) != {"type": "agent_run", "id": value.id}):
                continue
            # Keep a registered, reference-only shape so normal Job readers can
            # still project the preserved lifecycle tombstone. The Run itself
            # is gone, so following this ref correctly returns 404.
            job.resource_ref = {"type": "agent_run", "id": value.id}
            job.retryable = False
            job.resume_mode = "none"
            job.cancelable = False
            job.progress = {}
            job.result_ref = None
            job.error = None
            attempts = session.scalars(select(JobAttempt).where(
                JobAttempt.job_id == job_id,
            ).order_by(JobAttempt.id).with_for_update()).all()
            for attempt in attempts:
                attempt.result_ref = None
                attempt.error = None
                attempt.checkpoint_ref = None
                attempt.execution_token = None
                attempt.lease_until = None
            job_events = session.scalars(select(JobEvent).where(
                JobEvent.job_id == job_id,
            ).order_by(JobEvent.seq).with_for_update()).all()
            for event in job_events:
                data = event.data if isinstance(event.data, Mapping) else {}
                event.data = {key: data[key] for key in ("status", "attempt") if key in data}

        if actual_run_ids:
            session.execute(delete(AgentRunRow).where(AgentRunRow.id.in_(actual_run_ids)))
        if actual_message_ids:
            session.execute(delete(MessageRow).where(MessageRow.id.in_(actual_message_ids)))
        session.execute(delete(ConversationRow).where(ConversationRow.id == conversation_id))

        replay_cache = getattr(self, "_assistant_write_replays", {})
        if isinstance(replay_cache, dict):
            for identity, previous in list(replay_cache.items()):
                if (isinstance(identity, tuple) and len(identity) >= 2
                        and identity[0] == actor_id
                        and identity[1] in {"conversation.create", "conversation.patch", "conversation.archive", "message.send", "run.cancel", "run.resume"}
                        and isinstance(previous, tuple) and len(previous) == 2
                        and _contains_any_id(_json(previous[1]), private_ids)):
                    replay_cache[identity] = (previous[0], marker)
        self._flush(session)
    def save_conversation(self, session, value):
        row=self._row(session,ConversationRow,value.id,for_update=True)
        if row is None: row=ConversationRow(id=value.id); session.add(row)
        for name in ("project_id","owner_id","title","mode","status","current_run_id","last_message_at","created_at","updated_at","revision","etag"): setattr(row,name,getattr(value,name))
        self._flush(session)
    def get_message_by_client_id(self, session, conversation_id, client_message_id): return _message(self._one(session,select(MessageRow).where(MessageRow.conversation_id==conversation_id,MessageRow.client_message_id==client_message_id)))
    def list_messages(self, session, conversation_id, *, limit, before=None):
        statement=select(MessageRow).where(MessageRow.conversation_id==conversation_id)
        if before is not None: statement=statement.where(MessageRow.seq<int(before))
        rows=list(session.scalars(statement.order_by(MessageRow.seq.desc()).limit(max(1,min(limit,201)))))
        return [_message(row) for row in reversed(rows)]
    def save_message(self, session, value):
        row=self._row(session,MessageRow,value.id,for_update=True)
        if row is None: row=MessageRow(id=value.id); session.add(row)
        for name,val in {"conversation_id":value.conversation_id,"seq":int(value.seq),"role":value.role,"agent_run_id":value.agent_run_id,"status":value.status,"blocks":_json(value.blocks),"attachment_ids":list(value.attachment_ids),"created_at":value.created_at}.items(): setattr(row,name,val)
        self._flush(session)
    def fail_partial_messages_for_run(self, session, run_id):
        rows = session.scalars(select(MessageRow).where(
            MessageRow.agent_run_id == run_id,
            MessageRow.role == "assistant",
            MessageRow.status == "PARTIAL",
        ).with_for_update()).all()
        for row in rows:
            row.status = "FAILED"
        self._flush(session)
    def save_client_message_id(self, session, conversation_id, client_message_id, message_id):
        row=self._row(session,MessageRow,message_id,for_update=True)
        if row is not None and row.conversation_id==conversation_id: row.client_message_id=client_message_id; self._flush(session)
    def save_message_payload_hash(self, session, message_id, payload_hash):
        row=self._row(session,MessageRow,message_id,for_update=True)
        if row is not None: row.payload_hash=payload_hash; self._flush(session)
    def get_message_payload_hash(self, session, message_id):
        row=self._row(session,MessageRow,message_id); return row.payload_hash if row is not None else None
    def save_run(self, session, value):
        row=self._row(session,AgentRunRow,value.id,for_update=True)
        if row is None: row=AgentRunRow(id=value.id); session.add(row)
        fields={"conversation_id":value.conversation_id,"project_id":value.project_id,"input_message_id":value.input_message_id,"mode":value.mode,"status":value.status,"attempt":value.attempt,"provider_configuration_id":value.provider_configuration_id,"provider_configuration_revision":value.provider_configuration_revision,"provider_configuration_hash":value.provider_configuration_hash,"model_id":value.model_id,"prompt_version":value.prompt_version,"toolset_version":value.toolset_version,"policy_version":value.policy_version,"pending_actions":_json(value.pending_actions),"related_resources":_json(value.related_resources),"usage":_json(value.usage),"last_event_seq":int(value.last_event_seq),"error":_json(value.error),"started_at":value.started_at,"finished_at":value.finished_at,"created_at":value.created_at,"updated_at":value.updated_at,"revision":value.revision,"etag":value.etag}
        for name,val in fields.items(): setattr(row,name,val)
        self._flush(session)
        self._sync_shared_job(session, value)
    def get_run(self, session, run_id): return _run(self._row(session,AgentRunRow,run_id))
    def lock_run_for_cancel(self, session, run_id):
        """Lock the AgentRun's own shared Job before its attempt and run rows."""
        initial = self._row(session, AgentRunRow, run_id)
        if initial is None:
            return None
        snapshot = _run(initial)
        job_id = self._shared_job_id(snapshot, session=session)
        if job_id is not None:
            self._row(session, Job, job_id, for_update=True, refresh=True)
            self._one(session, select(JobAttempt).where(
                JobAttempt.job_id == job_id, JobAttempt.attempt == snapshot.attempt,
            ), for_update=True, refresh=True)
        row = self._row(session, AgentRunRow, run_id, for_update=True, refresh=True)
        if row is None:
            return None
        self._one(session, select(AgentAttemptRow).where(
            AgentAttemptRow.agent_run_id == run_id, AgentAttemptRow.attempt == row.attempt,
        ), for_update=True, refresh=True)
        return _run(row)
    def set_cancel_requested(self, session, run_id, *, requested=True):
        row = self._row(session, AgentRunRow, run_id, for_update=True, refresh=True)
        if row is None:
            return False
        row.cancel_requested = bool(requested)
        self._flush(session)
        return True

    @staticmethod
    def _shared_job_ids(value: object) -> list[str]:
        refs = _json(getattr(value, "related_resources", None)) or []
        return [str(ref.get("id")) for ref in refs if isinstance(ref, dict) and ref.get("type") == "job" and ref.get("id")]

    @classmethod
    def _shared_job_id(cls, value: object, *, session=None) -> str | None:
        run_id = str(getattr(value, "id", ""))
        for job_id in cls._shared_job_ids(value):
            if session is None:
                return job_id
            job = cls._row(session, Job, job_id, refresh=True)
            if (job is not None and job.kind == "assistant"
                    and str(job.project_id) == str(getattr(value, "project_id", ""))
                    and dict(job.resource_ref or {}) == {"type": "agent_run", "id": run_id}):
                return job_id
        return None

    def create_shared_recovery(self, run: object, attempt: int, *, status: str = "QUEUED", session=None) -> object:
        """Recovery hook for callers that only hold the assistant repository."""
        from chrom_platform.core.dto import ResourceRef
        from chrom_platform.task_core.repository import SQLAlchemyTaskRepository
        from chrom_platform.task_core.service import add_job_attempt_rows, add_job_rows
        from chrom_platform.task_core.state import JobStatus
        with self.transaction(session) as db:
            run_id = getattr(run, "id", None) or (run.get("id") if isinstance(run, dict) else None)
            project_id = getattr(run, "project_id", None) or (run.get("project_id") if isinstance(run, dict) else None)
            owner_id = getattr(run, "owner_id", None) or (run.get("owner_id") if isinstance(run, dict) else None) or "assistant"
            job_id = self._shared_job_id(run, session=db) or f"assistant_job_{run_id}"
            job = self._row(db, Job, job_id, for_update=True)
            if job is None:
                job = add_job_rows(SQLAlchemyTaskRepository(lambda: db), db, project_id=str(project_id), owner_id=str(owner_id), kind="assistant", resource_ref=ResourceRef(type="agent_run", id=str(run_id)), cancelable=True, retryable=True, resume_mode="restart", deadline_at=None, job_id=job_id)
            add_job_attempt_rows(SQLAlchemyTaskRepository(lambda: db), db, job_id=job.id, attempt=attempt, status=JobStatus.QUEUED)
            self._flush(db)
            return job

    def resume_confirmation_atomic(self, run_id: str, confirmation_id: str, *, now=None, session=None):
        with self.transaction(session) as db:
            row = self._row(db, AgentRunRow, run_id, for_update=True)
            if row is None:
                raise unavailable("AgentRun 不存在")
            # A retried request must see the committed result of the first
            # confirmation resume, rather than treating the terminal Run as a
            # new state conflict.  The ToolCall confirmation id is the durable
            # replay key; the attempt and Run are locked by this transaction.
            if row.status != "WAITING_CONFIRMATION":
                completed = self._one(
                    db,
                    select(ToolCallRow).where(
                        ToolCallRow.agent_run_id == run_id,
                        ToolCallRow.confirmation_id == confirmation_id,
                        ToolCallRow.status == "SUCCEEDED",
                    ).with_for_update(),
                )
                current = self._one(
                    db,
                    select(AgentAttemptRow).where(
                        AgentAttemptRow.agent_run_id == run_id,
                        AgentAttemptRow.attempt == row.attempt,
                    ).with_for_update(),
                )
                if completed is not None and current is not None and row.attempt > 1:
                    return _attempt(current), _run(row), True
                raise unavailable("Run 当前不在等待确认状态")
            pending = row.pending_actions or []
            if not any((item or {}).get("confirmation_id") in (None, confirmation_id) for item in pending):
                raise unavailable("确认未匹配服务端 pending action")
            next_number = int(row.attempt) + 1
            existing = self._one(db, select(AgentAttemptRow).where(AgentAttemptRow.agent_run_id == run_id, AgentAttemptRow.attempt == next_number).with_for_update())
            if existing is not None:
                return _attempt(existing), _run(row), True
            stamp = now or _now()
            attempt = AgentAttemptRow(id=f"assistant_attempt_{uuid4().hex}", agent_run_id=run_id, attempt=next_number, status="QUEUED", created_at=stamp)
            db.add(attempt)
            row.attempt = next_number; row.status = "RUNNING"; row.updated_at = stamp; row.revision = int(row.revision or 1) + 1
            self.create_shared_recovery(_run(row), next_number, status="QUEUED", session=db)
            self._flush(db)
            return _attempt(attempt), _run(row), False

    def _sync_shared_job(self, session, value: object, *, force: bool = False) -> None:
        job_id = self._shared_job_id(value, session=session)
        if not job_id:
            return
        job = self._row(session, Job, job_id, for_update=True, refresh=True)
        if job is None:
            return
        status = "RUNNING" if str(value.status) == "WAITING_CONFIRMATION" else str(value.status)
        old = str(job.status)
        job.status = status
        job.attempt = int(value.attempt)
        job.updated_at = _now()
        if value.started_at is not None:
            job.started_at = value.started_at
        if value.finished_at is not None:
            job.finished_at = value.finished_at
        job.error = None if status == "SUCCEEDED" else _json(value.error)
        if status == "SUCCEEDED":
            job.result_ref = {"type": "agent_run", "id": str(value.id)}
        if old != status or force:
            job.revision = int(job.revision or 1) + 1
            job.etag = f'"{job.revision}"'
            attempt = self._one(session, select(JobAttempt).where(JobAttempt.job_id == job_id, JobAttempt.attempt == int(value.attempt)).with_for_update())
            if attempt is not None:
                attempt.status = status
                attempt.started_at = value.started_at or attempt.started_at
                attempt.finished_at = value.finished_at or attempt.finished_at
                attempt.error = None if status == "SUCCEEDED" else _json(value.error)
                if status == "SUCCEEDED":
                    attempt.result_ref = {"type": "agent_run", "id": str(value.id)}
            seq = self._one(session, select(JobEvent.seq).where(JobEvent.job_id == job_id).order_by(JobEvent.seq.desc()).limit(1))
            session.add(JobEvent(id=f"assistant_event_{uuid4().hex}", job_id=job_id, stream_id=job_id, seq=int(seq if seq is not None else -1) + 1, type=f"job.{status.lower()}", schema_version="1.0", data={"status": status, "attempt": int(value.attempt)}, timestamp=_now()))
            self._flush(session)
    def list_runs(self, session, conversation_id, *, limit):
        rows=session.scalars(select(AgentRunRow).where(AgentRunRow.conversation_id==conversation_id).order_by(AgentRunRow.created_at.desc(),AgentRunRow.id.desc()).limit(max(1,min(limit,200))))
        return [_run(row) for row in rows]
    def save_attempt(self, session, value):
        row=self._row(session,AgentAttemptRow,value.id,for_update=True)
        if row is None: row=AgentAttemptRow(id=value.id); session.add(row)
        for name,val in {"agent_run_id":value.agent_run_id,"attempt":value.attempt,"status":value.status,"created_at":value.created_at,"started_at":value.started_at,"finished_at":value.finished_at,"error":_json(value.error)}.items(): setattr(row,name,val)
        self._flush(session)
    def get_attempt(self, session, agent_run_id, attempt): return _attempt(self._one(session,select(AgentAttemptRow).where(AgentAttemptRow.agent_run_id==agent_run_id,AgentAttemptRow.attempt==attempt)))
    def save_tool_call(self, session, value):
        row=self._row(session,ToolCallRow,value.id,for_update=True)
        if row is None: row=ToolCallRow(id=value.id); session.add(row)
        fields={"agent_run_id":value.agent_run_id,"agent_attempt_id":value.agent_attempt_id,"provider_tool_call_id":value.provider_tool_call_id,"tool_name":value.tool_name,"tool_schema_version":value.tool_schema_version,"arguments_hash":value.arguments_hash,"binding_hash":value.binding_hash,"status":value.status,"arguments_summary":_json(value.arguments_summary),"resource_ref":_json(value.resource_ref),"confirmation_id":value.confirmation_id,"pending_confirmation":_json(value.pending_confirmation),"result_summary":_json(value.result_summary),"job_id":value.job_id,"error":_json(value.error),"started_at":value.started_at,"finished_at":value.finished_at}
        for name,val in fields.items(): setattr(row,name,val)
        self._flush(session)
    def get_tool_call(self, session, tool_call_id): return _tool(self._row(session,ToolCallRow,tool_call_id))
    def list_tool_calls(self, session, *, run_id=None, conversation_id=None, limit):
        statement=select(ToolCallRow)
        if run_id is not None: statement=statement.where(ToolCallRow.agent_run_id==run_id)
        if conversation_id is not None: statement=statement.join(AgentRunRow,AgentRunRow.id==ToolCallRow.agent_run_id).where(AgentRunRow.conversation_id==conversation_id)
        rows=session.scalars(statement.order_by(ToolCallRow.started_at.desc(),ToolCallRow.id.desc()).limit(max(1,min(limit,200))))
        return [_tool(row) for row in rows]

    # Worker AgentExecutionStore. All mutating calls lock and flush only.
    def load_run(self, run_id, *, session=None):
        with self.transaction(session) as db: return self.get_run(db,run_id)
    def claim(self, *, run_id, attempt, dispatch_id, lease_seconds, session=None):
        with self.transaction(session) as db:
            row=self._row(db,AgentRunRow,run_id,for_update=True)
            if row is None or row.attempt!=attempt or row.cancel_requested or row.status in {"SUCCEEDED","FAILED","CANCELED","TIMED_OUT","INTERRUPTED","WAITING_CONFIRMATION"}: return None
            if row.dispatch_id is not None and row.dispatch_id!=dispatch_id: return None
            now=_now(); active=row.status=="RUNNING" and row.lease_until is not None and _aware(row.lease_until)>now
            if active: return None
            row.dispatch_id=dispatch_id; row.status="RUNNING"; row.started_at=row.started_at or now; row.updated_at=now; row.fencing_token=int(row.fencing_token or 0)+1; row.lease_until=now+timedelta(seconds=max(1,int(lease_seconds)))
            execution_token = f"exec_{uuid4().hex}"
            attempt_row=self._one(db,select(AgentAttemptRow).where(AgentAttemptRow.agent_run_id==run_id,AgentAttemptRow.attempt==attempt).with_for_update())
            if attempt_row is not None: attempt_row.status="RUNNING"; attempt_row.dispatch_id=dispatch_id; attempt_row.execution_token=execution_token; attempt_row.fencing_token=row.fencing_token; attempt_row.lease_until=row.lease_until; attempt_row.started_at=attempt_row.started_at or now
            self._sync_shared_job(db, _run(row), force=False)
            self._append_worker_event(db, run_id, "run.started", {"status": "RUNNING", "attempt": attempt})
            self._flush(db)
            return {"run_id":run_id,"attempt":attempt,"dispatch_id":dispatch_id,"execution_token":execution_token,"fencing_token":row.fencing_token,"lease_expires_at":row.lease_until,"claimed":True,"event_emitted":True}
    def is_cancel_requested(self, run_id, *, session=None):
        with self.transaction(session) as db:
            row=self._row(db,AgentRunRow,run_id); return bool(row and (row.cancel_requested or row.status=="CANCEL_REQUESTED"))
    def finish(self, *, run_id, attempt, dispatch_id, fencing_token, status, result=None, error=None, session=None):
        with self.transaction(session) as db:
            row=self._row(db,AgentRunRow,run_id,for_update=True)
            valid=bool(row and row.attempt==attempt and row.dispatch_id==dispatch_id and int(row.fencing_token or 0)==int(fencing_token) and row.status in {"RUNNING","CANCEL_REQUESTED"} and not (row.status == "CANCEL_REQUESTED" and status not in {"CANCELED","FAILED","INTERRUPTED"}))
            if not valid: return {"accepted":False,"fencing_valid":False,"stale":True,"status":row.status if row else None}
            now=_now(); row.status=status; row.updated_at=now; row.finished_at=now if status in {"SUCCEEDED","FAILED","CANCELED","TIMED_OUT","INTERRUPTED"} else None; row.lease_until=None; row.error=_json(error); row.revision=int(row.revision or 1)+1
            if status == "WAITING_CONFIRMATION" and result is not None:
                pending = getattr(result, "pending_actions", None)
                if pending is None and isinstance(result, Mapping):
                    pending = result.get("pending_actions")
                if pending:
                    row.pending_actions = _json(pending)
            attempt_row=self._one(db,select(AgentAttemptRow).where(AgentAttemptRow.agent_run_id==run_id,AgentAttemptRow.attempt==attempt).with_for_update())
            if attempt_row is not None: attempt_row.status=status; attempt_row.finished_at=row.finished_at; attempt_row.lease_until=None; attempt_row.error=_json(error); attempt_row.result_ref=_json(result) if result is not None else None
            self._sync_shared_job(db, _run(row), force=False)
            event_type = {"SUCCEEDED": "run.completed", "FAILED": "run.failed", "CANCELED": "run.canceled", "TIMED_OUT": "run.interrupted", "INTERRUPTED": "run.interrupted", "WAITING_CONFIRMATION": "run.waiting_confirmation"}.get(status, "run.failed")
            self._append_worker_event(db, run_id, event_type, {"status": status, "attempt": attempt, **({"error": _json(error)} if error else {})})
            self._flush(db); return {"accepted":True,"fencing_valid":True,"stale":False,"status":status,"fencing_token":fencing_token,"event_emitted":True}

    def cancel_before_start(self, *, run_id, attempt, dispatch_id, error=None, session=None):
        with self.transaction(session) as db:
            row = self._row(db, AgentRunRow, run_id, for_update=True)
            if row is None or row.attempt != attempt or (row.dispatch_id is not None and row.dispatch_id != dispatch_id) or row.status in {"SUCCEEDED", "FAILED", "CANCELED", "TIMED_OUT", "INTERRUPTED"}:
                return {"accepted": False, "stale": True, "status": row.status if row else None}
            now = _now(); row.dispatch_id = dispatch_id; row.status = "CANCELED"; row.cancel_requested = True; row.updated_at = now; row.finished_at = now; row.error = _json(error); row.revision = int(row.revision or 1) + 1
            attempt_row = self._one(db, select(AgentAttemptRow).where(AgentAttemptRow.agent_run_id == run_id, AgentAttemptRow.attempt == attempt).with_for_update())
            if attempt_row is not None:
                attempt_row.status = "CANCELED"; attempt_row.finished_at = now; attempt_row.error = _json(error)
            self._sync_shared_job(db, _run(row), force=False)
            self._append_worker_event(db, run_id, "run.canceled", {"status": "CANCELED", "attempt": attempt, "error": _json(error) if error else None})
            self._flush(db)
            return {"accepted": True, "stale": False, "status": "CANCELED", "event_emitted": True}

    def _append_worker_event(self, session, run_id, event_type, data):
        latest = self._one(session, select(AgentEventRow.seq).where(AgentEventRow.stream_type == "agent_run", AgentEventRow.stream_id == run_id).order_by(AgentEventRow.seq.desc()).limit(1), for_update=True)
        seq = int(latest or 0) + 1
        session.add(AgentEventRow(id=f"assistant_worker_event_{uuid4().hex}", stream_type="agent_run", stream_id=run_id, seq=seq, type=event_type, schema_version="1.0", data=_safe(data), timestamp=_now(), agent_run_id=run_id))

    def append_event(self, stream_type, stream_id, event_type, data, *, agent_run_id=None, schema_version="1.0", timestamp=None, session=None):
        from .events import AgentEvent
        with self.transaction(session) as db:
            latest=self._one(db,select(AgentEventRow.seq).where(AgentEventRow.stream_type==stream_type,AgentEventRow.stream_id==stream_id).order_by(AgentEventRow.seq.desc()).limit(1).with_for_update()); seq=int(latest or 0)+1; event=AgentEvent(stream_type,stream_id,seq,event_type,_safe(data),schema_version,timestamp or _now(),agent_run_id); db.add(AgentEventRow(id=event.id,stream_type=stream_type,stream_id=stream_id,seq=seq,type=event_type,schema_version=schema_version,data=_safe(data),timestamp=event.timestamp,agent_run_id=agent_run_id)); self._flush(db); return event
    def list_events(self, session, stream_type, stream_id, after_seq=0, limit=100):
        from .events import _event_from_row
        cutoff = _now() - timedelta(days=self.retention_days)
        rows=session.scalars(select(AgentEventRow).where(AgentEventRow.stream_type==stream_type,AgentEventRow.stream_id==stream_id,AgentEventRow.seq>after_seq,AgentEventRow.timestamp>=cutoff).order_by(AgentEventRow.seq).limit(limit)); return [_event_from_row(row,stream_type,stream_id) for row in rows]
    def oldest_event_seq(self, session, stream_type, stream_id):
        cutoff = _now() - timedelta(days=self.retention_days)
        value = self._one(session,select(AgentEventRow.seq).where(AgentEventRow.stream_type==stream_type,AgentEventRow.stream_id==stream_id,AgentEventRow.timestamp>=cutoff).order_by(AgentEventRow.seq).limit(1))
        if value is not None:
            return int(value)
        latest = self.latest_event_seq(session, stream_type, stream_id)
        return int(latest) + 1 if latest is not None else None
    def latest_event_seq(self, session, stream_type, stream_id): return self._one(session,select(AgentEventRow.seq).where(AgentEventRow.stream_type==stream_type,AgentEventRow.stream_id==stream_id).order_by(AgentEventRow.seq.desc()).limit(1))

    # Immutable configuration and trace snapshots. These helpers intentionally
    # accept dictionaries instead of provider objects so no secret-bearing
    # client is accidentally persisted.
    def save_llm_configuration(self, session, *, id, project_id, revision, url, model_id, key_reference, status="CANDIDATE", settings=None, created_at=None):
        if any(secret in str(key_reference).lower() for secret in ("secret=", "token=", "api_key=", "apikey=")):
            raise ValueError("key_reference must be a server-side reference")
        row = self._row(session, LlmConfigurationRow, id, for_update=True)
        normalized_settings = _json(settings or {})
        if row is not None:
            immutable = {"project_id": project_id, "revision": revision, "url": url, "model_id": model_id, "key_reference": key_reference, "settings": normalized_settings}
            if any(getattr(row, name) != value for name, value in immutable.items()):
                raise ValueError("LLM configuration revisions are immutable")
            # Status transitions are the deployment pointer's concern; the
            # immutable revision remains the same snapshot.
            row.status = status
            self._flush(session); return row
        if row is None:
            row = LlmConfigurationRow(id=id); session.add(row)
        for name, value in {"project_id":project_id,"revision":revision,"url":url,"model_id":model_id,"key_reference":key_reference,"status":status,"settings":normalized_settings,"created_at":created_at or _now()}.items():
            setattr(row, name, value)
        self._flush(session); return row

    def get_llm_configuration(self, session, configuration_id, *, for_update=False):
        return self._row(session, LlmConfigurationRow, configuration_id, for_update=for_update)

    def save_llm_call(self, session, value):
        row = self._row(session, LlmCallRow, value.id, for_update=True)
        if row is None: row = LlmCallRow(id=value.id); session.add(row)
        json_fields = {"model_parameters_summary", "usage", "estimates", "response_summary", "error"}
        for name in LlmCallRow.__table__.columns.keys():
            if name != "id" and hasattr(value, name): setattr(row, name, _json(getattr(value, name)) if name in json_fields else getattr(value, name))
        self._flush(session); return row

    def save_context_snapshot(self, session, value):
        project_id = getattr(value, "project_id", None)
        if not project_id or project_id == "unknown":
            raise ValueError("context snapshot project_id is required for SQL persistence")
        row = self._row(session, ContextSnapshotRow, value.id, for_update=True)
        if row is None: row = ContextSnapshotRow(id=value.id); session.add(row)
        for name in ContextSnapshotRow.__table__.columns.keys():
            if name != "id" and hasattr(value, name): setattr(row, name, _json(getattr(value, name)) if name in {"payload", "sources", "redactions"} else getattr(value, name))
        self._flush(session); return row

    def get_llm_call(self, call_id, *, session=None):
        from .trace import LlmCallDetail, Usage
        with self.transaction(session) as db:
            row = self._row(db, LlmCallRow, call_id)
            if row is None:
                return None
            run_row = self._row(db, AgentRunRow, row.agent_run_id)
            usage = row.usage or {}
            return LlmCallDetail(
                id=row.id,
                agent_run_id=row.agent_run_id,
                agent_attempt_id=row.agent_attempt_id,
                request_index=row.request_index,
                transport_attempt=row.transport_attempt,
                project_id=run_row.project_id if run_row is not None else None,
                model_id=row.model_id,
                status=row.status,
                provider_configuration_id=row.provider_configuration_id,
                provider_configuration_revision=row.provider_configuration_revision,
                logical_context_snapshot_id=row.logical_context_snapshot_id,
                provider_request_snapshot_id=row.provider_request_snapshot_id,
                model_parameters_summary=row.model_parameters_summary or {},
                usage=Usage(
                    input_tokens=usage.get("input_tokens"),
                    output_tokens=usage.get("output_tokens"),
                    cached_input_tokens=usage.get("cached_input_tokens"),
                ),
                estimates=row.estimates or {},
                latency_ms=row.latency_ms,
                response_summary=row.response_summary,
                error=row.error,
                created_at=_aware(row.created_at) or _now(),
                finished_at=_aware(row.finished_at),
            )

    def list_llm_calls(self, session, *, run_id: str, limit: int = 50, cursor: str | None = None):
        from datetime import datetime
        from sqlalchemy import and_, or_
        statement = select(LlmCallRow).where(LlmCallRow.agent_run_id == run_id)
        if cursor:
            try:
                padded = cursor + "=" * (-len(cursor) % 4)
                marker = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))["key"]
                marker_time = datetime.fromisoformat(str(marker[0]))
                statement = statement.where(or_(LlmCallRow.created_at < marker_time, and_(LlmCallRow.created_at == marker_time, LlmCallRow.id < str(marker[1]))))
            except Exception as exc:
                raise ValueError("invalid llm call cursor") from exc
        rows = session.scalars(
            statement.order_by(LlmCallRow.created_at.desc(), LlmCallRow.id.desc()).limit(max(1, min(int(limit), 200)))
        ).all()
        return [self.get_llm_call(row.id, session=session) for row in rows]

    def get_context_snapshot(self, snapshot_id, *, session=None):
        from .trace import ContextSnapshot
        with self.transaction(session) as db:
            row = self._row(db, ContextSnapshotRow, snapshot_id)
            if row is None:
                return None
            return ContextSnapshot(
                id=row.id,
                llm_call_id=row.llm_call_id,
                project_id=row.project_id,
                view=row.view,
                capture_state=row.capture_state,
                capture_mode=row.capture_mode,
                payload_schema_version=row.payload_schema_version,
                payload=row.payload,
                sources=tuple(row.sources or ()),
                redactions=row.redactions or {},
                truncated=bool(row.truncated),
                hash_scope=row.hash_scope,
                sha256=row.sha256,
                created_at=_aware(row.created_at) or _now(),
                expires_at=_aware(row.expires_at),
            )


class InMemoryAssistantRepository:
    is_test_only=True
    def __init__(self):
        self._lock=RLock(); self.conversations={}; self.messages={}; self.runs={}; self.attempts={}; self.tool_calls={}; self.message_payload_hashes={}; self.client_message_ids={}; self.shared_jobs={}; self.shared_attempts={}; self.shared_events=[]; self.shared_outbox={}; self.cancel_requested={}; self.events=[]; self.conversation_deletions={}
    @contextmanager
    def transaction(self,session=None):
        with self._lock: yield self
    def lock_conversation(self,session,conversation_id): return self.conversations.get(conversation_id)
    def get_conversation(self,session,conversation_id): return self.conversations.get(conversation_id)
    def list_conversations(self,session,*,project_id,owner_id,archived,q,limit):
        values=[v for v in self.conversations.values() if v.owner_id==owner_id]; values=[v for v in values if project_id is None or v.project_id==project_id]; values=[v for v in values if archived is None or (v.status=="ARCHIVED")==archived]; values=[v for v in values if not q or q.lower() in v.title.lower()]; return sorted(values,key=lambda v:(v.updated_at,v.id),reverse=True)[:limit]
    def get_conversation_deletion(self,session,conversation_id): return self.conversation_deletions.get(conversation_id)
    def record_conversation_deletion(self,session,audit): self.conversation_deletions[audit.resource_id]=audit
    def lock_conversation_runs(self,session,conversation_id): return sorted((run for run in self.runs.values() if run.conversation_id==conversation_id),key=lambda run:run.id)
    def conversation_message_ids(self,session,conversation_id): return sorted(message.id for message in self.messages.values() if message.conversation_id==conversation_id)
    def conversation_delete_busy(self,session,conversation_id):
        terminal_runs={"SUCCEEDED","FAILED","CANCELED","TIMED_OUT","INTERRUPTED"}; terminal_jobs=terminal_runs|{"PARTIAL"}
        for run in self.lock_conversation_runs(session,conversation_id):
            if run.status not in terminal_runs: return True
            if any(attempt.agent_run_id==run.id and attempt.status not in terminal_runs for attempt in self.attempts.values()): return True
            job_ref=next((ref for ref in run.related_resources if getattr(ref,"type",None)=="job"),None)
            job_id=getattr(job_ref,"id",None) if job_ref is not None else None
            job=self.shared_jobs.get(job_id) if job_id is not None else None
            if job is not None:
                get=lambda key: job.get(key) if isinstance(job,Mapping) else getattr(job,key,None)
                if get("kind")!="assistant" or str(get("project_id"))!=str(run.project_id) or get("resource_ref")!={"type":"agent_run","id":run.id}: continue
                if str(get("status")) not in terminal_jobs: return True
                for item in self.shared_attempts.values():
                    if item.get("job_id")==job_id and str(item.get("status")) not in terminal_jobs: return True
        return False
    def delete_conversation_data(self,session,conversation_id,*,run_ids,message_ids,actor_id,project_id):
        actual_runs={run.id for run in self.lock_conversation_runs(session,conversation_id)}
        actual_messages=set(self.conversation_message_ids(session,conversation_id))
        if actual_runs!={str(value) for value in run_ids} or actual_messages!={str(value) for value in message_ids}: raise ValueError("conversation graph changed during deletion")
        private_ids=actual_runs|actual_messages|{str(conversation_id)}
        assistant_job_ids=set()
        for run_id in actual_runs:
            run=self.runs[run_id]
            for ref in run.related_resources:
                if getattr(ref,"type",None)!="job": continue
                job_id=str(getattr(ref,"id",""))
                job=self.shared_jobs.get(job_id)
                get=lambda key: job.get(key) if isinstance(job,Mapping) else getattr(job,key,None)
                if job is not None and get("kind")=="assistant" and str(get("project_id"))==str(project_id) and get("resource_ref")=={"type":"agent_run","id":run_id}:
                    assistant_job_ids.add(job_id)
                    if isinstance(job,dict):
                        job["resource_ref"]={"type":"agent_run","id":run_id}; job["retryable"]=False; job["resume_mode"]="none"; job["cancelable"]=False; job["progress"]={}; job["result_ref"]=None; job["error"]=None
                    else:
                        job.resource_ref={"type":"agent_run","id":run_id}; job.retryable=False; job.resume_mode="none"; job.cancelable=False; job.progress={}; job.result_ref=None; job.error=None
        for item in self.shared_attempts.values():
            get=lambda key: item.get(key) if isinstance(item,Mapping) else getattr(item,key,None)
            if get("job_id") in assistant_job_ids:
                if isinstance(item,dict):
                    item.update(result_ref=None,error=None,checkpoint_ref=None,execution_token=None,lease_until=None)
                else:
                    item.result_ref=None; item.error=None; item.checkpoint_ref=None; item.execution_token=None; item.lease_until=None
        for event in self.shared_events:
            if event.get("job_id") in assistant_job_ids:
                data=event.get("data") if isinstance(event.get("data"),Mapping) else {}
                event["data"]={key:data[key] for key in ("status","attempt") if key in data}
        marker={"_deleted_resource":{"type":"conversation","id":str(conversation_id)}}
        for identity,previous in list(getattr(self,"_assistant_write_replays",{}).items()):
            if (isinstance(identity,tuple) and len(identity)>=2 and identity[0]==actor_id
                    and identity[1] in {"conversation.create","conversation.patch","conversation.archive","message.send","run.cancel","run.resume"}
                    and isinstance(previous,tuple) and len(previous)==2 and _contains_any_id(_json(previous[1]),private_ids)):
                self._assistant_write_replays[identity]=(previous[0],marker)
        for call_id,call in list(self.tool_calls.items()):
            if call.agent_run_id in actual_runs: self.tool_calls.pop(call_id,None)
        for run_id in actual_runs:
            self.runs.pop(run_id,None); self.cancel_requested.pop(run_id,None)
        for attempt_id,attempt in list(self.attempts.items()):
            if attempt.agent_run_id in actual_runs: self.attempts.pop(attempt_id,None)
        for message_id in actual_messages:
            self.messages.pop(message_id,None); self.message_payload_hashes.pop(message_id,None)
        for key in list(self.client_message_ids):
            if key[0]==conversation_id: self.client_message_ids.pop(key,None)
        self.events[:]=[event for event in self.events if str(event.get("agent_run_id") or event.get("stream_id")) not in actual_runs]
        self.conversations.pop(conversation_id,None)
    def save_conversation(self,session,value): self.conversations[value.id]=value
    def get_message_by_client_id(self,session,conversation_id,client_message_id):
        mid=self.client_message_ids.get((conversation_id,client_message_id)); return self.messages.get(mid) if mid else None
    def list_messages(self,session,conversation_id,*,limit,before=None):
        values=sorted([m for m in self.messages.values() if m.conversation_id==conversation_id],key=lambda m:int(m.seq)); return [m for m in values if before is None or int(m.seq)<int(before)][-limit:]
    def save_message(self,session,value): self.messages[value.id]=value
    def fail_partial_messages_for_run(self,session,run_id):
        for message in self.messages.values():
            if message.agent_run_id == run_id and message.role == "assistant" and message.status == "PARTIAL":
                message.status = "FAILED"
    def save_client_message_id(self,session,conversation_id,client_message_id,message_id): self.client_message_ids[(conversation_id,client_message_id)]=message_id
    def save_message_payload_hash(self,session,message_id,payload_hash): self.message_payload_hashes[message_id]=payload_hash
    def get_message_payload_hash(self,session,message_id): return self.message_payload_hashes.get(message_id)
    def save_run(self,session,value): self.runs[value.id]=value
    def get_run(self,session,run_id): return self.runs.get(run_id)
    def lock_run_for_cancel(self,session,run_id): return self.runs.get(run_id)
    def set_cancel_requested(self,session,run_id,*,requested=True):
        if run_id not in self.runs: return False
        self.cancel_requested[run_id]=bool(requested)
        return True
    def append_event(self,stream_type,stream_id,event_type,data,*,agent_run_id=None,schema_version="1.0",timestamp=None,session=None):
        from types import SimpleNamespace
        seq = 1 + max((item["seq"] for item in self.events if item["stream_type"] == stream_type and item["stream_id"] == stream_id), default=0)
        event = SimpleNamespace(stream_type=stream_type,stream_id=stream_id,seq=seq,type=event_type,data=_safe(data),schema_version=schema_version,timestamp=timestamp or _now(),agent_run_id=agent_run_id)
        self.events.append({"stream_type":stream_type,"stream_id":stream_id,"seq":seq,"type":event_type,"data":_safe(data),"agent_run_id":agent_run_id})
        return event
    def list_runs(self,session,conversation_id,*,limit): return sorted([v for v in self.runs.values() if v.conversation_id==conversation_id],key=lambda v:(v.created_at,v.id),reverse=True)[:limit]
    def save_attempt(self,session,value): self.attempts[value.id]=value
    def get_attempt(self,session,agent_run_id,attempt): return next((v for v in self.attempts.values() if v.agent_run_id==agent_run_id and v.attempt==attempt),None)
    def save_tool_call(self,session,value): self.tool_calls[value.id]=value
    def get_tool_call(self,session,tool_call_id): return self.tool_calls.get(tool_call_id)
    def resume_confirmation_atomic(self, run_id, confirmation_id, *, now=None, session=None):
        with self._lock:
            run = self.runs.get(run_id)
            if run is None or run.status != "WAITING_CONFIRMATION":
                completed = next((
                    value for value in self.tool_calls.values()
                    if value.agent_run_id == run_id
                    and value.confirmation_id == confirmation_id
                    and value.status == "SUCCEEDED"
                ), None)
                current = self.get_attempt(None, run_id, int(getattr(run, "attempt", 0))) if run is not None else None
                if completed is not None and current is not None and int(getattr(run, "attempt", 0)) > 1:
                    return current, run, True
                raise ValueError("confirmation resume is not allowed")
            if not any(item.confirmation_id in (None, confirmation_id) for item in run.pending_actions):
                raise ValueError("confirmation binding is invalid")
            number = int(run.attempt) + 1
            existing = self.get_attempt(None, run_id, number)
            if existing is not None:
                return existing, run, True
            stamp = now or _now()
            previous = self.get_attempt(None, run_id, run.attempt)
            attempt = Attempt(id=f"attempt_{uuid4().hex}", agent_run_id=run_id, attempt=number, status="QUEUED", created_at=stamp, started_at=None, finished_at=None)
            self.attempts[attempt.id] = attempt
            job_id = next((str(getattr(x, "id", "")) for x in run.related_resources if getattr(x, "type", None) == "job"), f"assistant_job_{run_id}")
            dispatch_id = f"assistant_dispatch_{run_id}_{number}"
            self.shared_attempts[f"{job_id}:{number}"] = {"job_id": job_id, "attempt": number, "status": "QUEUED"}
            self.shared_events.append({"job_id": job_id, "attempt": number, "type": "job.queued"})
            self.shared_outbox[dispatch_id] = {"job_id": job_id, "attempt": number, "dispatch_id": dispatch_id, "status": "PENDING"}
            updated = run.model_copy(update={
                "attempt": number,
                "status": "RUNNING",
                "revision": int(getattr(run, "revision", 1)) + 1,
                "updated_at": stamp,
            })
            self.runs[run_id] = updated
            return attempt, updated, False
    def list_tool_calls(self,session,*,run_id=None,conversation_id=None,limit=50):
        values=list(self.tool_calls.values()); values=[v for v in values if run_id is None or v.agent_run_id==run_id]; run_ids={v.id for v in self.runs.values() if conversation_id is not None and v.conversation_id==conversation_id}; values=[v for v in values if conversation_id is None or v.agent_run_id in run_ids]; return sorted(values,key=lambda v:(v.started_at or datetime.min.replace(tzinfo=timezone.utc),v.id),reverse=True)[:limit]


SQLAlchemyAssistantUoW=SessionAssistantUnitOfWork
__all__=["AgentAttemptRow","AgentEventRow","AgentRunRow","AssistantBase","AssistantFrameworkStateRow","AssistantPersistenceUnavailable","ContextSnapshotRow","ConversationRow","InMemoryAssistantRepository","LlmCallRow","LlmConfigurationActiveRow","LlmConfigurationRow","LlmTestResultRow","MessageRow","PendingConfirmationRow","SQLAlchemyAssistantRepository","SessionAssistantUnitOfWork","SQLAlchemyAssistantUoW","ToolCallRow"]
