"""Strict public and domain DTOs for the assistant domain."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from chrom_platform.core.dto import ResourceRef


class AssistantStrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=False)


Id = Annotated[str, Field(min_length=1, max_length=96)]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-fA-F]{64}$")]
Content = Annotated[str, Field(min_length=1, max_length=20_000)]
Mode = Literal["knowledge", "business"]


ConversationStatus = Literal["ACTIVE", "ARCHIVED"]
MessageRole = Literal["user", "assistant"]
MessageStatus = Literal["FINAL", "PARTIAL", "FAILED"]
RunStatus = Literal[
    "QUEUED", "RUNNING", "WAITING_CONFIRMATION", "CANCEL_REQUESTED",
    "SUCCEEDED", "FAILED", "CANCELED", "TIMED_OUT", "INTERRUPTED",
]
AttemptStatus = RunStatus
ToolCallStatus = Literal[
    "PROPOSED", "VALIDATING", "WAITING_CONFIRMATION", "RUNNING",
    "SUCCEEDED", "FAILED", "REJECTED", "INTERRUPTED",
]
AgentRunStatus = RunStatus
AgentAttemptStatus = AttemptStatus


class ContextRef(AssistantStrictModel):
    """A server-resolved context reference; never a client file path."""

    type: Annotated[str, Field(min_length=1, max_length=64)]
    id: Id


class EventPage(AssistantStrictModel):
    events: list["Event"]
    next_seq: str
    has_more: bool
    stream_status: str


class EventData(AssistantStrictModel):
    """Versioned event data envelope.

    Known event fields are typed here while ``extra=allow`` keeps unknown event
    types visible to an upgraded client without turning the public contract into
    an untyped ``dict``.
    """

    model_config = ConfigDict(extra="allow", strict=True, populate_by_name=False)
    status: str | None = None
    attempt: int | None = None
    error: "PublicErrorSummary | None" = None
    message_id: Id | None = None
    block_index: int | None = None
    delta: str | None = None
    offset: int | None = None
    llm_call_id: Id | None = None
    request_index: int | None = None
    tool_call_id: Id | None = None
    tool_name: str | None = None
    resource_ref: ResourceRef | None = None
    job_id: Id | None = None
    confirmation_id: Id | None = None
    outcome: str | None = None
    action: str | None = None
    revision: int | None = None
    config_hash: Sha256 | None = None
    expires_at: datetime | None = None
    kind: str | None = None
    code: str | None = None
    message: str | None = None
    completed: int | None = None
    total: int | None = None
    unit: str | None = None
    stage: str | None = None


class Event(AssistantStrictModel):
    seq: Annotated[str, Field(pattern=r"^[0-9]+$")]
    type: Annotated[str, Field(min_length=1, max_length=128)]
    schema_version: Annotated[str, Field(min_length=1, max_length=32)]
    timestamp: datetime
    agent_run_id: Id | None = None
    data: EventData


class ContextSnapshotCompareRequest(AssistantStrictModel):
    left_id: Id
    right_id: Id


class ConversationCreate(AssistantStrictModel):
    project_id: Id
    title: Annotated[str | None, Field(default=None, max_length=120)] = None
    mode: Mode


class ConversationPatch(AssistantStrictModel):
    title: Annotated[str | None, Field(default=None, max_length=120)] = None


class ConversationArchive(AssistantStrictModel):
    confirmation_id: Id


class ConversationDelete(AssistantStrictModel):
    confirmation_id: Id


class ConversationDeleted(AssistantStrictModel):
    id: Id
    deleted: Literal[True] = True


class UserMessageCreate(AssistantStrictModel):
    """Only user content and controlled references cross the public boundary."""

    content: Content
    attachment_ids: list[Id] = Field(default_factory=list, max_length=10)
    context_refs: list[ContextRef] = Field(default_factory=list, max_length=20)
    client_message_id: Annotated[str, Field(min_length=1, max_length=128)]
    mode: Mode | None = None

    @model_validator(mode="after")
    def unique_refs(self) -> "UserMessageCreate":
        if len(self.attachment_ids) != len(set(self.attachment_ids)):
            raise ValueError("attachment_ids must be unique")
        if len({(r.type, r.id) for r in self.context_refs}) != len(self.context_refs):
            raise ValueError("context_refs must be unique")
        return self


class TextBlock(AssistantStrictModel):
    type: Literal["text"]
    text: Annotated[str, Field(min_length=1, max_length=20_000)]


class CitationBlock(AssistantStrictModel):
    type: Literal["citation"]
    citation_id: Id
    title: Annotated[str, Field(min_length=1, max_length=500)]
    locator: Annotated[str, Field(min_length=1, max_length=500)]


class PredictionResultBlock(AssistantStrictModel):
    type: Literal["prediction_result"]
    prediction_id: Id
    title: Annotated[str, Field(min_length=1, max_length=500)]


class JobBlock(AssistantStrictModel):
    type: Literal["job"]
    job_id: Id
    resource_ref: ResourceRef
    title: Annotated[str, Field(min_length=1, max_length=500)]


class ConfirmationRequestBlock(AssistantStrictModel):
    type: Literal["confirmation_request"]
    agent_run_id: Id
    tool_call_id: Id
    resource_ref: ResourceRef
    action: Annotated[str, Field(min_length=1, max_length=128)]
    revision: int = Field(ge=1)
    config_hash: Sha256


class WarningBlock(AssistantStrictModel):
    type: Literal["warning"]
    code: Annotated[str, Field(min_length=1, max_length=96)]
    message: Annotated[str, Field(min_length=1, max_length=2_000)]


class ArtifactBlock(AssistantStrictModel):
    type: Literal["artifact"]
    artifact_id: Id
    title: Annotated[str, Field(min_length=1, max_length=500)]


class NavigationBlock(AssistantStrictModel):
    type: Literal["navigation"]
    resource_ref: ResourceRef
    label: Annotated[str, Field(min_length=1, max_length=500)]


MessageBlock: TypeAlias = Annotated[
    TextBlock | CitationBlock | PredictionResultBlock | JobBlock
    | ConfirmationRequestBlock | WarningBlock | ArtifactBlock | NavigationBlock,
    Field(discriminator="type"),
]


class Conversation(AssistantStrictModel):
    id: Id
    project_id: Id
    owner_id: Id
    title: Annotated[str, Field(min_length=1, max_length=120)]
    mode: Mode
    status: ConversationStatus
    current_run_id: Id | None = None
    last_message_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    revision: int = Field(ge=1)
    etag: Annotated[str, Field(min_length=1, max_length=128)]


class Message(AssistantStrictModel):
    id: Id
    conversation_id: Id
    seq: Annotated[str, Field(pattern=r"^[0-9]+$")]
    role: MessageRole
    agent_run_id: Id | None = None
    status: MessageStatus
    blocks: list[MessageBlock] = Field(default_factory=list)
    attachment_ids: list[Id] = Field(default_factory=list, max_length=10)
    created_at: datetime


class Usage(AssistantStrictModel):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    model_requests: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)


class PublicErrorSummary(AssistantStrictModel):
    code: Annotated[str, Field(min_length=1, max_length=96)]
    message: Annotated[str, Field(min_length=1, max_length=2_000)]
    retryable: bool = False


class PendingConfirmationBinding(AssistantStrictModel):
    tool_call_id: Id
    confirmation_id: Id | None = None
    resource_ref: ResourceRef
    action: Annotated[str, Field(min_length=1, max_length=128)]
    revision: int = Field(ge=1)
    config_hash: Sha256
    schema_version: Annotated[str, Field(min_length=1, max_length=64)]
    normalized_args: dict[str, object] = Field(default_factory=dict)
    expires_at: datetime | None = None


class AgentRun(AssistantStrictModel):
    id: Id
    conversation_id: Id
    project_id: Id
    input_message_id: Id
    mode: Mode
    status: RunStatus
    attempt: int = Field(ge=1)
    provider_configuration_id: Id | None = None
    provider_configuration_revision: Annotated[str | None, Field(default=None, max_length=128)] = None
    provider_configuration_hash: Sha256 | None = None
    model_id: Annotated[str, Field(min_length=1, max_length=128)]
    prompt_version: Annotated[str, Field(min_length=1, max_length=64)]
    toolset_version: Annotated[str, Field(min_length=1, max_length=64)]
    policy_version: Annotated[str, Field(min_length=1, max_length=64)]
    pending_actions: list[PendingConfirmationBinding] = Field(default_factory=list)
    related_resources: list[ResourceRef] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    last_event_seq: Annotated[str, Field(pattern=r"^[0-9]+$")] = "0"
    error: PublicErrorSummary | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    revision: int = Field(ge=1)
    etag: Annotated[str, Field(min_length=1, max_length=128)]


class Attempt(AssistantStrictModel):
    id: Id
    agent_run_id: Id
    attempt: int = Field(ge=1)
    status: AttemptStatus
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: PublicErrorSummary | None = None


class ToolCall(AssistantStrictModel):
    id: Id
    agent_run_id: Id
    agent_attempt_id: Id
    provider_tool_call_id: Annotated[str, Field(min_length=1, max_length=128)]
    tool_name: Annotated[str, Field(min_length=1, max_length=128)]
    tool_schema_version: Annotated[str, Field(min_length=1, max_length=64)]
    # Server-side integrity marker for the persisted normalized arguments.
    # It is optional for compatibility with older trace rows, but new pending
    # calls always set it before a confirmation can be consumed.
    arguments_hash: Sha256 | None = None
    binding_hash: Sha256 | None = None
    status: ToolCallStatus
    arguments_summary: dict[str, object] = Field(default_factory=dict)
    resource_ref: ResourceRef | None = None
    confirmation_id: Id | None = None
    pending_confirmation: PendingConfirmationBinding | None = None
    result_summary: dict[str, object] | None = None
    job_id: Id | None = None
    error: PublicErrorSummary | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class MessageAccepted(AssistantStrictModel):
    message_id: Id
    agent_run_id: Id
    status: RunStatus
    events_url: Annotated[str, Field(min_length=1, max_length=512)]
    stream_url: Annotated[str, Field(min_length=1, max_length=512)]


class CancelAgentRun(AssistantStrictModel):
    reason: Annotated[str | None, Field(default=None, max_length=500)] = None


CancelAgentRunRequest = CancelAgentRun


class ConfirmationResume(AssistantStrictModel):
    reason: Literal["confirmation"]
    confirmation_id: Id


class RecoveryResume(AssistantStrictModel):
    reason: Literal["recovery"]


ResumeAgentRunPayload: TypeAlias = Annotated[
    ConfirmationResume | RecoveryResume,
    Field(discriminator="reason", title="ResumeAgentRun"),
]


class ResumeAgentRunRequest(ConfirmationResume):
    """Compatibility constructor for service callers.

    HTTP uses ``ResumeAgentRunPayload`` below, which is the strict
    discriminated union. Direct callers should pass ``ConfirmationResume`` or
    ``RecoveryResume`` explicitly.
    """

    reason: Literal["confirmation"] = "confirmation"


class LlmUsageDTO(AssistantStrictModel):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)


class LlmEstimateDTO(AssistantStrictModel):
    input_tokens: int | None = Field(default=None, ge=0)
    method: str | None = None
    estimator_version: str | None = None


class ModelParametersSummaryDTO(AssistantStrictModel):
    temperature: float | None = None
    top_p: float | None = None
    max_output_tokens: int | None = Field(default=None, ge=0)
    stream: bool | None = None


class ResponseSummaryDTO(AssistantStrictModel):
    text_excerpt: str | None = None
    tool_names: list[str] = Field(default_factory=list)
    finish_reason: str | None = None


class LlmCallSummary(AssistantStrictModel):
    id: Id
    agent_run_id: Id
    request_index: int = Field(ge=0)
    transport_attempt: int = Field(ge=1)
    status: Literal["CREATED", "RUNNING", "SUCCEEDED", "FAILED", "INTERRUPTED"]
    model_id: Annotated[str, Field(min_length=1, max_length=128)]
    usage: LlmUsageDTO
    latency_ms: int | None = Field(default=None, ge=0)
    created_at: datetime


class LlmCallDetail(LlmCallSummary):
    agent_attempt_id: Id
    project_id: Id | None = None
    provider_configuration_id: Id | None = None
    provider_configuration_revision: str | None = None
    logical_context_snapshot_id: Id | None = None
    provider_request_snapshot_id: Id | None = None
    model_parameters_summary: ModelParametersSummaryDTO = Field(default_factory=ModelParametersSummaryDTO)
    estimates: LlmEstimateDTO = Field(default_factory=LlmEstimateDTO)
    response_summary: ResponseSummaryDTO | None = None
    error: PublicErrorSummary | None = None
    trace_id: str | None = None
    span_id: str | None = None
    finished_at: datetime | None = None


SnapshotView = Literal["logical_context", "provider_request"]
CaptureState = Literal["AVAILABLE", "DISABLED", "NOT_CAPTURED", "EXPIRED", "FAILED"]
CaptureMode = Literal["off", "redacted"]


class ContextSourceDTO(AssistantStrictModel):
    json_pointer: str
    kind: Literal["system_instruction", "user_message", "history", "tool_definition", "tool_result", "knowledge_chunk", "attachment_excerpt", "runtime_note"]
    source_version: str | None = None
    resource_ref: ResourceRef | None = None


class RedactionDTO(AssistantStrictModel):
    category: Literal["secret", "hidden"]
    count: int = Field(ge=0)


class ContextSnapshotMetadata(AssistantStrictModel):
    id: Id
    llm_call_id: Id
    project_id: Id
    view: SnapshotView
    capture_state: CaptureState
    capture_mode: CaptureMode
    sha256: Sha256 | None = None
    truncated: bool
    created_at: datetime
    expires_at: datetime | None = None


class ContextSnapshotContent(AssistantStrictModel):
    id: Id
    view: SnapshotView
    project_id: Id
    payload_schema_version: str
    payload: JsonValue
    sources: list[ContextSourceDTO] = Field(default_factory=list)
    redactions: list[RedactionDTO] = Field(default_factory=list)
    truncated: bool
    hash_scope: Literal["redacted_payload"]
    sha256: Sha256 | None = None


class ContextSnapshotView(ContextSnapshotContent):
    llm_call_id: Id
    capture_state: CaptureState
    capture_mode: CaptureMode
    created_at: datetime
    expires_at: datetime | None = None


ContextSnapshotViewDTO = ContextSnapshotView


class ContextDiffSummary(AssistantStrictModel):
    added: int = Field(ge=0)
    removed: int = Field(ge=0)
    changed: int = Field(ge=0)


class ContextDiffChange(AssistantStrictModel):
    path: str
    kind: Literal["added", "removed", "changed"]
    before: JsonValue = None
    after: JsonValue = None


class ContextDiff(AssistantStrictModel):
    left_id: Id
    right_id: Id
    summary: ContextDiffSummary
    changes: list[ContextDiffChange] = Field(default_factory=list)
    truncated: bool


class ToolCallDraft(AssistantStrictModel):
    """Server-side DTO; it is intentionally not exposed as a client route."""

    provider_tool_call_id: Annotated[str, Field(min_length=1, max_length=128)]
    tool_name: Annotated[str, Field(min_length=1, max_length=128)]
    schema_version: Annotated[str, Field(min_length=1, max_length=64)]
    normalized_args: dict[str, object] = Field(default_factory=dict)
    resource_ref: ResourceRef | None = None
    action: Annotated[str | None, Field(default=None, max_length=128)] = None
    revision: int | None = Field(default=None, ge=1)
    config_hash: Sha256 | None = None
    requires_confirmation: bool = False

    @model_validator(mode="after")
    def binding_is_complete(self) -> "ToolCallDraft":
        if self.requires_confirmation and (self.resource_ref is None or not self.action or self.revision is None or not self.config_hash):
            raise ValueError("confirmation tool calls require a complete binding")
        return self


# Stable request/view aliases used by application adapters.
ConversationCreateRequest = ConversationCreate
ConversationPatchRequest = ConversationPatch
UserMessageCreateRequest = UserMessageCreate
AgentRunView = AgentRun
AttemptView = Attempt
ToolCallView = ToolCall
MessageView = Message

# Forward references are declared near the public event/page DTOs so OpenAPI
# and response validation resolve them after all DTO definitions exist.
EventData.model_rebuild()
Event.model_rebuild()
EventPage.model_rebuild()

__all__ = [name for name in globals() if not name.startswith("_")]
