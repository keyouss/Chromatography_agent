"""Async production handler for Assistant Agent Run Job deliveries."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass, replace
from datetime import datetime, timedelta, timezone
import asyncio
import inspect
import logging
import re
import threading
import time
from typing import Any, Callable, Mapping
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chrom_platform.core.dto import ResourceRef
from chrom_platform.infrastructure.models import AuditEvent, Job, JobAttempt, JobEvent, OutboxEvent
from chrom_platform.task_core.state import JobStatus

from .persistence import AgentAttemptRow, AgentEventRow, AgentRunRow, ConversationRow, LlmCallRow, LlmConfigurationRow, LlmTestResultRow, MessageRow, _run


_TERMINAL = frozenset({"SUCCEEDED", "PARTIAL", "FAILED", "CANCELED", "TIMED_OUT", "INTERRUPTED"})
_EVENTS = frozenset({"job.execute", "job.cancel"})
_LLM_TEST_LOG = logging.getLogger(__name__)
_LLM_TEST_STAGES = frozenset({"endpoint_policy", "connectivity", "tool_calling", "streaming", "worker_execution"})
_LLM_TEST_CATEGORIES = frozenset({
    "ENDPOINT_POLICY_REJECTED",
    "PROVIDER_DNS", "PROVIDER_TIMEOUT", "PROVIDER_CONNECT", "PROVIDER_TLS",
    "PROVIDER_HTTP_4XX", "PROVIDER_HTTP_5XX", "PROVIDER_HTTP_REDIRECT",
    "PROVIDER_RESPONSE_SCHEMA", "PROVIDER_UNAVAILABLE", "PROVIDER_VALIDATION",
    "PROVIDER_FAILURE", "RESPONSE_INVALID", "TOOL_CALL_FAILED", "STREAM_INCOMPLETE",
    "UNEXPECTED_EXCEPTION",
})
_LLM_TEST_AUTH_SKIP_CHECKS = {
    "connectivity": "FAILED",
    "streaming": "SKIPPED",
    "tool_calling": "SKIPPED",
    "structured_output": "SKIPPED",
}
_LLM_TEST_TOOL_AUTH_SKIP_CHECKS = {
    "connectivity": "PASSED",
    "streaming": "SKIPPED",
    "tool_calling": "FAILED",
    "structured_output": "SKIPPED",
}
_LLM_TEST_STREAM_AUTH_CHECKS = {
    "connectivity": "PASSED",
    "streaming": "FAILED",
    "tool_calling": "PASSED",
    "structured_output": "NOT_SUPPORTED",
}
_LLM_TEST_AUTH_ERROR = {
    "code": "PROVIDER_AUTHENTICATION_FAILED",
    "message": "LLM Provider 认证或授权失败",
}


def _is_llm_test_auth_skip(checks: Mapping[str, Any]) -> bool:
    return dict(checks) == _LLM_TEST_AUTH_SKIP_CHECKS


def _is_llm_test_auth_failure(checks: Mapping[str, Any]) -> bool:
    values = dict(checks)
    return values in (_LLM_TEST_AUTH_SKIP_CHECKS, _LLM_TEST_TOOL_AUTH_SKIP_CHECKS, _LLM_TEST_STREAM_AUTH_CHECKS)


def _llm_test_auth_outcome(stage: str | None, checks: Mapping[str, Any]) -> dict[str, Any]:
    values = dict(checks)
    if stage == "connectivity":
        values = dict(_LLM_TEST_AUTH_SKIP_CHECKS)
    elif stage == "tool_calling":
        values = dict(_LLM_TEST_TOOL_AUTH_SKIP_CHECKS)
    elif stage == "streaming" and values.get("connectivity") == "PASSED" and values.get("tool_calling") == "PASSED":
        values = dict(_LLM_TEST_STREAM_AUTH_CHECKS)
    else:
        values = {
            "connectivity": "FAILED", "streaming": "FAILED",
            "tool_calling": "FAILED", "structured_output": "NOT_SUPPORTED",
        }
        return {"status": "FAILED", "checks": values, "error": {"code": "PROVIDER_TEST_FAILED", "message": "LLM 配置能力测试未通过"}}
    return {"status": "FAILED", "checks": values, "error": dict(_LLM_TEST_AUTH_ERROR)}


def _llm_test_inflight_outcome(checks: Mapping[str, Any], stages: frozenset[str]) -> dict[str, Any]:
    values = dict(checks)
    if "connectivity" in stages:
        values["connectivity"] = "FAILED"
    if "tool_calling" in stages:
        values["tool_calling"] = "FAILED"
    if "streaming" in stages or not stages:
        values["streaming"] = "FAILED"
    return {"status": "FAILED", "checks": values, "error": {"code": "PROVIDER_TEST_FAILED", "message": "LLM 配置能力测试未通过"}}


def _safe_llm_test_exception(exc: Exception) -> tuple[str, int | None]:
    from chrom_platform.core.errors import APIError
    from .adapter import ProviderResponseError, ProviderTransportError

    status = None
    if isinstance(exc, ProviderTransportError):
        category = exc.category if exc.category in {"DNS", "TIMEOUT", "CONNECT", "TLS", "HTTP_4XX", "HTTP_5XX", "HTTP_REDIRECT"} else "FAILURE"
        status = exc.http_status if type(exc.http_status) is int and 100 <= exc.http_status <= 599 else None
        return f"PROVIDER_{category}", status
    if isinstance(exc, ProviderResponseError):
        return "PROVIDER_RESPONSE_SCHEMA", None
    if isinstance(exc, APIError):
        status = exc.status_code if type(exc.status_code) is int and 100 <= exc.status_code <= 599 else None
        if exc.payload.code == "UNAVAILABLE":
            return "PROVIDER_UNAVAILABLE", status
        if exc.payload.code == "VALIDATION_FAILED":
            return "PROVIDER_VALIDATION", status
        return "PROVIDER_FAILURE", status
    return "UNEXPECTED_EXCEPTION", None


def _log_llm_test_diagnostic(*, job_id: str | None, test_id: str | None, stage: str, category: str, http_status: int | None = None) -> None:
    safe_stage = stage if stage in _LLM_TEST_STAGES else "worker_execution"
    safe_category = category if category in _LLM_TEST_CATEGORIES else "UNEXPECTED_EXCEPTION"
    safe_status = http_status if type(http_status) is int and 100 <= http_status <= 599 else "none"
    safe_job = job_id if isinstance(job_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,96}", job_id) else "unknown"
    safe_test = test_id if isinstance(test_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,96}", test_id) else "unknown"
    _LLM_TEST_LOG.warning(
        "assistant_llm_configuration_test_diagnostic job_id=%s test_id=%s stage=%s category=%s http_status=%s",
        safe_job, safe_test, safe_stage, safe_category, safe_status,
    )


class AssistantWorkerError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class _AssistantRunCancellationRequested(Exception):
    """Internal signal that a human cancellation should stop provider work."""


@dataclass(frozen=True)
class AssistantWorkerMessage:
    job_id: str
    attempt: int
    dispatch_id: str

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "AssistantWorkerMessage":
        if not isinstance(payload, Mapping) or set(payload) != {"job_id", "attempt", "dispatch_id"}:
            raise AssistantWorkerError("INVALID_WORKER_PAYLOAD")
        if type(payload["job_id"]) is not str or not payload["job_id"] or type(payload["dispatch_id"]) is not str or not payload["dispatch_id"] or type(payload["attempt"]) is not int or payload["attempt"] < 1:
            raise AssistantWorkerError("INVALID_WORKER_PAYLOAD")
        return cls(payload["job_id"], payload["attempt"], payload["dispatch_id"])

    def as_dict(self) -> dict[str, Any]:
        return {"job_id": self.job_id, "attempt": self.attempt, "dispatch_id": self.dispatch_id}


@dataclass(frozen=True)
class AssistantWorkerResult:
    status: JobStatus
    result: object | None = None
    error: Mapping[str, Any] | None = None
    replayed: bool = False


@dataclass(frozen=True)
class _Claim:
    message: AssistantWorkerMessage
    run_id: str
    provider_config: object | None
    execution_token: str
    fencing_token: int
    agent_attempt_id: str


@dataclass(frozen=True)
class _CancelClaim:
    """Durable cancel request fence used after the DB transaction commits."""

    message: AssistantWorkerMessage
    execution_token: str | None
    fencing_token: int


@dataclass(frozen=True)
class AssistantRunScope:
    """Server-resolved scope supplied to a per-job runtime factory."""

    run_id: str
    project_id: str
    conversation_id: str
    actor_id: str
    policy_version: str
    artifact_ids: tuple[str, ...]
    confirmation_id: str | None = None
    mode: str = "business"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json(value: object) -> Any:
    if hasattr(value, "model_dump"):
        return _json(value.model_dump(mode="json"))
    if is_dataclass(value) and not isinstance(value, type):
        return _json(asdict(value))
    if isinstance(value, Mapping):
        hidden = {"authorization", "body", "content", "credential", "credentials", "header", "headers", "key", "password", "prompt", "raw_response", "secret", "token", "url"}
        return {
            str(key): _json(item)
            for key, item in value.items()
            if str(key).lower() not in hidden
        }
    if isinstance(value, (list, tuple)):
        return [_json(item) for item in value[:100]]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return None


_RESULT_KEYS = frozenset({
    "status", "text", "output_text", "structured", "tool_results", "usage",
    "provider_requests", "tool_calls", "transport_attempts", "error",
})


def _runtime_result_json(value: object) -> dict[str, Any] | None:
    projected = _json(value)
    if not isinstance(projected, Mapping):
        return None
    return {str(key): projected[key] for key in _RESULT_KEYS if key in projected}


def _assistant_text_blocks(result: Mapping[str, Any] | None) -> list[dict[str, str]]:
    if not isinstance(result, Mapping):
        return []
    text = result.get("text", result.get("output_text"))
    return [{"type": "text", "text": text}] if isinstance(text, str) and text else []


async def _call(value: Any, *args: Any, **kwargs: Any) -> Any:
    if not callable(value):
        return value
    result = value(*args, **kwargs)
    return await result if inspect.isawaitable(result) else result


async def _call_factory(factory: Callable[..., Any], **kwargs: Any) -> Any:
    """Pass only the parameters declared by a reviewed runtime factory."""
    try:
        signature = inspect.signature(factory)
    except (TypeError, ValueError):
        return await _call(factory, **kwargs)
    parameters = signature.parameters.values()
    if any(item.kind is inspect.Parameter.VAR_KEYWORD for item in parameters):
        return await _call(factory, **kwargs)
    selected = {item.name: kwargs[item.name] for item in parameters if item.name in kwargs}
    return await _call(factory, **selected)


def _safe_error(code: str, message: str = "助手运行失败") -> dict[str, Any]:
    return {"code": code, "message": message, "retryable": True}


class AsyncAssistantExecutionStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], *, lease_seconds: int = 60):
        self.session_factory, self.lease_seconds = session_factory, max(1, int(lease_seconds))

    async def _runtime_rows(self, session: AsyncSession, claim: _Claim):
        rows = await self._rows(session, claim.message)
        job, attempt, run, agent_attempt, _config, _dispatch = rows
        lease_until = getattr(attempt, "lease_until", None)
        lease_valid = isinstance(lease_until, datetime)
        if lease_valid:
            lease_valid = lease_until.replace(tzinfo=timezone.utc) > _now() if lease_until.tzinfo is None else lease_until > _now()
        if (run.id != claim.run_id or attempt.execution_token != claim.execution_token
                or int(attempt.fencing_token or 0) != claim.fencing_token
                or agent_attempt.execution_token != claim.execution_token
                or int(agent_attempt.fencing_token or 0) != claim.fencing_token
                or job.status != "RUNNING" or attempt.status != "RUNNING"
                or run.status != "RUNNING" or run.cancel_requested or not lease_valid):
            raise AssistantWorkerError("WORKER_JOB_FENCE_INVALID")
        return rows

    async def renew_lease(self, claim: _Claim) -> bool:
        """Renew the assistant business lease while the exact claim remains live."""
        async with self.session_factory() as session:
            async with session.begin():
                try:
                    _job, attempt, run, agent_attempt, _config, _dispatch = await self._runtime_rows(session, claim)
                except AssistantWorkerError:
                    return False
                now = _now()
                leases = (attempt.lease_until, run.lease_until, agent_attempt.lease_until)
                if (int(run.fencing_token or 0) != claim.fencing_token
                        or agent_attempt.id != claim.agent_attempt_id
                        or agent_attempt.status != "RUNNING"
                        or run.dispatch_id != claim.message.dispatch_id
                        or agent_attempt.dispatch_id != claim.message.dispatch_id
                        or any(not isinstance(value, datetime)
                               or (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value) <= now
                               for value in leases)):
                    return False
                lease_until = now + timedelta(seconds=self.lease_seconds)
                attempt.lease_until = run.lease_until = agent_attempt.lease_until = lease_until
                return True

    async def is_cancel_requested(self, run_id: str) -> bool:
        async with self.session_factory() as session:
            row = await session.scalar(select(AgentRunRow).where(AgentRunRow.id == run_id))
            return bool(row and (row.cancel_requested or row.status == "CANCEL_REQUESTED"))

    async def framework_state(self, claim: _Claim, state: Mapping[str, Any] | None = None):
        """Private trusted history never passes through public result redaction."""
        from .persistence import AssistantFrameworkStateRow
        import copy
        async with self.session_factory() as session:
            async with session.begin():
                _job, _attempt, run, _agent_attempt, _config, _dispatch = await self._runtime_rows(session, claim)
                row = await session.scalar(select(AssistantFrameworkStateRow).where(
                    AssistantFrameworkStateRow.agent_run_id == run.id,
                ).with_for_update())
                # Only newly stamped Runs may enter the new engine. Historical
                # nonterminal work cannot be reconstructed and replayed safely.
                if (row is None or row.engine != "pydantic-ai"
                        or row.project_id != run.project_id
                        or row.conversation_id != run.conversation_id
                        or row.config_hash != run.provider_configuration_hash):
                    raise AssistantWorkerError("ASSISTANT_ENGINE_STATE_INCOMPATIBLE")
                if state is None:
                    if row.state:
                        return copy.deepcopy(row.state)
                    previous = await session.scalar(select(AssistantFrameworkStateRow).join(
                        AgentRunRow, AgentRunRow.id == AssistantFrameworkStateRow.agent_run_id,
                    ).where(
                        AssistantFrameworkStateRow.project_id == run.project_id,
                        AssistantFrameworkStateRow.conversation_id == run.conversation_id,
                        AssistantFrameworkStateRow.agent_run_id != run.id,
                        AssistantFrameworkStateRow.engine == "pydantic-ai",
                        AgentRunRow.status == "SUCCEEDED",
                    ).order_by(AgentRunRow.finished_at.desc(), AgentRunRow.id.desc()).limit(1))
                    prior = (previous.state or {}).get("history_state") if previous is not None else None
                    if prior:
                        return {"fresh_run": True, "prior_history_state": copy.deepcopy(prior),
                                "config_hash": row.config_hash}
                    return None
                if state.get("config_hash") != row.config_hash:
                    raise AssistantWorkerError("ASSISTANT_ENGINE_STATE_INCOMPATIBLE")
                row.state = copy.deepcopy(dict(state))
                row.framework_version = str(state.get("framework_version", row.framework_version))
                row.serialization_version = str(state.get("serialization_version", row.serialization_version))
                row.stream_message_id = state.get("stream_message_id") or row.stream_message_id
                row.updated_at = _now()
                return True

    async def stream_text(self, claim: _Claim, op: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Commit the Message projection and its SSE event under the same fence."""
        async with self.session_factory() as session:
            async with session.begin():
                _job, _attempt, run, _agent_attempt, _config, _dispatch = await self._runtime_rows(session, claim)
                message = (await session.execute(select(MessageRow).where(
                    MessageRow.conversation_id == run.conversation_id,
                    MessageRow.agent_run_id == run.id, MessageRow.role == "assistant",
                ).with_for_update())).scalars().first()
                if message is None:
                    if op != "ensure":
                        raise AssistantWorkerError("ASSISTANT_STREAM_MESSAGE_MISSING")
                    latest = await session.scalar(select(func.max(MessageRow.seq)).where(
                        MessageRow.conversation_id == run.conversation_id,
                    ))
                    message = MessageRow(id=f"msg_{uuid4().hex}", conversation_id=run.conversation_id,
                        seq=int(latest or 0) + 1, role="assistant", agent_run_id=run.id,
                        status="PARTIAL", blocks=[{"type": "text", "text": ""}], attachment_ids=[],
                        client_message_id=None, payload_hash=None, created_at=_now())
                    session.add(message)
                    await session.flush()
                if message.status != "PARTIAL" or (payload.get("message_id") not in {None, message.id}):
                    raise AssistantWorkerError("ASSISTANT_STREAM_MESSAGE_INVALID")
                blocks = [dict(block) for block in (message.blocks or [])]
                index = payload.get("block_index", 0)
                if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(blocks) or blocks[index].get("type") != "text":
                    raise AssistantWorkerError("ASSISTANT_STREAM_BLOCK_INVALID")
                text = str(blocks[index].get("text", ""))
                if op == "append":
                    delta = payload.get("delta")
                    offset = payload.get("offset")
                    if not isinstance(delta, str) or type(offset) is not int or offset != len(text):
                        raise AssistantWorkerError("ASSISTANT_STREAM_OFFSET_INVALID")
                    blocks[index]["text"] = text + delta
                    message.blocks = blocks
                    await self._agent_event(session, run.id, "message.delta", {
                        "message_id": message.id, "block_index": index, "delta": delta, "offset": len(text),
                    })
                elif op == "replace":
                    replacement = payload.get("text")
                    if not isinstance(replacement, str):
                        raise AssistantWorkerError("ASSISTANT_STREAM_BLOCK_INVALID")
                    blocks[index]["text"] = replacement
                    message.blocks = blocks
                    await session.flush()
                    await self._agent_event(session, run.id, "warning", {
                        "code": "MESSAGE_SNAPSHOT_REPLACED", "message": "消息内容已更新，请读取最新快照",
                        "message_id": message.id, "block_index": index,
                    })
                elif op != "ensure":
                    raise AssistantWorkerError("ASSISTANT_STREAM_OPERATION_INVALID")
                return {"message_id": message.id, "block_index": index,
                        "offset": len(blocks[index].get("text", "")), "text": blocks[index].get("text", "")}

    async def load_scope(self, run_id: str) -> AssistantRunScope:
        async with self.session_factory() as session:
            run = (await session.execute(select(AgentRunRow).where(AgentRunRow.id == run_id))).scalar_one_or_none()
            if run is None:
                raise AssistantWorkerError("WORKER_JOB_RESOURCE_INVALID")
            conversation = (await session.execute(select(ConversationRow).where(ConversationRow.id == run.conversation_id))).scalar_one_or_none()
            if conversation is None or conversation.project_id != run.project_id:
                raise AssistantWorkerError("WORKER_SCOPE_INVALID")
            message = (await session.execute(select(MessageRow).where(MessageRow.id == run.input_message_id, MessageRow.conversation_id == conversation.id))).scalar_one_or_none()
            if message is None or message.agent_run_id != run.id:
                raise AssistantWorkerError("WORKER_SCOPE_INVALID")
            return AssistantRunScope(
                run_id=run.id,
                project_id=run.project_id,
                conversation_id=conversation.id,
                actor_id=conversation.owner_id,
                policy_version=run.policy_version,
                artifact_ids=tuple(str(value) for value in (message.attachment_ids or ())),
                confirmation_id=next((str(item.get("confirmation_id")) for item in (run.pending_actions or []) if isinstance(item, Mapping) and item.get("confirmation_id")), None),
                mode=run.mode,
            )

    async def _rows(self, session: AsyncSession, message: AssistantWorkerMessage):
        job = (await session.execute(select(Job).where(Job.id == message.job_id).with_for_update())).scalar_one_or_none()
        if job is None:
            raise AssistantWorkerError("WORKER_JOB_NOT_FOUND")
        if job.kind != "assistant" or int(job.attempt) != message.attempt:
            raise AssistantWorkerError("WORKER_JOB_FENCE_INVALID")
        try:
            ref = ResourceRef.model_validate(job.resource_ref)
        except ValidationError as exc:
            raise AssistantWorkerError("WORKER_JOB_RESOURCE_INVALID") from exc
        if ref.type != "agent_run":
            raise AssistantWorkerError("WORKER_JOB_RESOURCE_INVALID")
        dispatch = (await session.execute(select(OutboxEvent).where(
            OutboxEvent.dispatch_id == message.dispatch_id,
            OutboxEvent.aggregate_type == "job", OutboxEvent.aggregate_id == job.id,
        ).with_for_update())).scalar_one_or_none()
        if dispatch is None or dispatch.event_type not in _EVENTS or dispatch.status not in {"PENDING", "PROCESSING"} or dict(dispatch.payload or {}) != message.as_dict():
            raise AssistantWorkerError("WORKER_DISPATCH_FENCE_INVALID")
        attempt = (await session.execute(select(JobAttempt).where(JobAttempt.job_id == job.id, JobAttempt.attempt == message.attempt).with_for_update())).scalar_one_or_none()
        run = (await session.execute(select(AgentRunRow).where(AgentRunRow.id == ref.id).with_for_update())).scalar_one_or_none()
        conversation = (await session.execute(select(ConversationRow).where(ConversationRow.id == (run.conversation_id if run is not None else "")).with_for_update())).scalar_one_or_none()
        agent_attempt = (await session.execute(select(AgentAttemptRow).where(AgentAttemptRow.agent_run_id == ref.id, AgentAttemptRow.attempt == message.attempt).with_for_update())).scalar_one_or_none()
        config = None
        if run is not None and run.provider_configuration_id:
            config = (await session.execute(select(LlmConfigurationRow).where(LlmConfigurationRow.id == run.provider_configuration_id, LlmConfigurationRow.project_id == job.project_id).with_for_update())).scalar_one_or_none()
        if attempt is None or run is None or agent_attempt is None or agent_attempt.dispatch_id not in {None, message.dispatch_id}:
            raise AssistantWorkerError("WORKER_JOB_RESOURCE_INVALID")
        if run.project_id != job.project_id or conversation is None or conversation.project_id != job.project_id or run.conversation_id != conversation.id:
            raise AssistantWorkerError("WORKER_PROJECT_FENCE_INVALID")
        if int(run.attempt) != message.attempt:
            raise AssistantWorkerError("WORKER_JOB_FENCE_INVALID")
        # Configuration rows are immutable revisions.  Once a Run has
        # captured one, activation of a newer revision only retires this row;
        # it must not redirect queued/retried work.  Invalid legacy or
        # tampered bindings become an initialization failure after claim so
        # the fenced Job/Run converge to FAILED instead of endlessly
        # redelivering the same outbox message.
        settings = getattr(config, "settings", None)
        config_hash = settings.get("config_hash") if isinstance(settings, Mapping) else None
        if (
            not run.provider_configuration_id
            or not run.provider_configuration_revision
            or not isinstance(run.provider_configuration_hash, str)
            or len(run.provider_configuration_hash) != 64
            or any(character not in "0123456789abcdef" for character in run.provider_configuration_hash)
            or config is None
            or config.status not in {"ACTIVE", "RETIRED"}
            or run.provider_configuration_revision != config.revision
            or config_hash != run.provider_configuration_hash
        ):
            config = None
        return job, attempt, run, agent_attempt, config, dispatch

    async def claim(self, message: AssistantWorkerMessage) -> tuple[_Claim | None, str]:
        async with self.session_factory() as session:
            async with session.begin():
                job, attempt, run, agent_attempt, config, dispatch = await self._rows(session, message)
                status = str(job.status)
                if status in _TERMINAL:
                    return None, status
                if dispatch.event_type == "job.cancel":
                    return None, status
                if status != "QUEUED" or attempt.status != "QUEUED" or run.status != "QUEUED":
                    return None, status
                now = _now(); token, fence = f"exec_{uuid4().hex}", int(attempt.fencing_token or 0) + 1
                job.status = attempt.status = run.status = agent_attempt.status = "RUNNING"
                job.started_at = job.started_at or now; job.updated_at = now; job.revision = int(job.revision or 1) + 1; job.etag = str(job.revision)
                attempt.started_at = attempt.started_at or now; attempt.execution_token = token; attempt.fencing_token = fence; attempt.lease_until = now + timedelta(seconds=self.lease_seconds)
                run.dispatch_id = message.dispatch_id; run.started_at = run.started_at or now; run.updated_at = now; run.fencing_token = fence; run.lease_until = attempt.lease_until
                agent_attempt.dispatch_id = message.dispatch_id; agent_attempt.execution_token = token; agent_attempt.fencing_token = fence; agent_attempt.lease_until = attempt.lease_until; agent_attempt.started_at = agent_attempt.started_at or now
                await self._job_event(session, job.id, "job.running", {"status": "RUNNING", "attempt": message.attempt})
                await self._agent_event(session, run.id, "run.started", {"status": "RUNNING", "attempt": message.attempt})
                return _Claim(message, run.id, config, token, fence, agent_attempt.id), "RUNNING"

    async def cancel(self, message: AssistantWorkerMessage, *, cancel_probe: object | None = None) -> AssistantWorkerResult:
        request: _CancelClaim | None = None
        async with self.session_factory() as session:
            async with session.begin():
                job, attempt, run, agent_attempt, config, dispatch = await self._rows(session, message)
                status = JobStatus(job.status)
                if status in _TERMINAL:
                    return AssistantWorkerResult(status, replayed=True)
                if dispatch.event_type != "job.cancel":
                    raise AssistantWorkerError("WORKER_EVENT_TYPE_INVALID")
                now = _now()
                if status is JobStatus.QUEUED:
                    job.status = attempt.status = run.status = agent_attempt.status = "CANCELED"
                    run.cancel_requested = True; job.finished_at = run.finished_at = agent_attempt.finished_at = now; job.updated_at = run.updated_at = now; job.revision = int(job.revision or 1) + 1; job.etag = str(job.revision)
                    await self._job_event(session, job.id, "job.canceled", {"status": "CANCELED", "attempt": message.attempt})
                    await self._agent_event(session, run.id, "run.canceled", {"status": "CANCELED", "attempt": message.attempt})
                    return AssistantWorkerResult(JobStatus.CANCELED)
                if status is JobStatus.RUNNING:
                    job.status = attempt.status = run.status = agent_attempt.status = "CANCEL_REQUESTED"
                    run.cancel_requested = True; job.updated_at = run.updated_at = now; job.revision = int(job.revision or 1) + 1; job.etag = str(job.revision)
                    await self._job_event(session, job.id, "job.cancel_requested", {"status": "CANCEL_REQUESTED", "attempt": message.attempt})
                    await self._agent_event(session, run.id, "run.cancel_requested", {"status": "CANCEL_REQUESTED", "attempt": message.attempt})
                    request = _CancelClaim(message, attempt.execution_token, int(attempt.fencing_token or 0))
        # Provider cancellation is an external operation.  It must never run
        # while the row locks above are held, and a failed probe leaves the
        # durable request for a later retry.
        confirmed = False
        if request is not None and cancel_probe is not None:
            try:
                confirmed = bool(await _call(cancel_probe, message))
            except Exception:
                confirmed = False
        if request is None or not confirmed:
            return AssistantWorkerResult(JobStatus.CANCEL_REQUESTED)
        async with self.session_factory() as session:
            async with session.begin():
                job, attempt, run, agent_attempt, _config, _dispatch = await self._rows(session, message)
                if (job.status != JobStatus.CANCEL_REQUESTED.value
                        or attempt.execution_token != request.execution_token
                        or int(attempt.fencing_token or 0) != request.fencing_token
                        or run.status != JobStatus.CANCEL_REQUESTED.value):
                    return AssistantWorkerResult(JobStatus.CANCEL_REQUESTED, replayed=True)
                now = _now()
                job.status = attempt.status = run.status = agent_attempt.status = "CANCELED"
                job.finished_at = run.finished_at = agent_attempt.finished_at = now
                job.updated_at = run.updated_at = now
                await self._job_event(session, job.id, "job.canceled", {"status": "CANCELED", "attempt": message.attempt})
                await self._agent_event(session, run.id, "run.canceled", {"status": "CANCELED", "attempt": message.attempt})
                return AssistantWorkerResult(JobStatus.CANCELED)

    async def finish(self, claim: _Claim, *, status: str, result: object | None = None, error: Mapping[str, Any] | None = None, usage: Mapping[str, Any] | None = None) -> bool | JobStatus:
        async with self.session_factory() as session:
            async with session.begin():
                job, attempt, run, agent_attempt, config, dispatch = await self._rows(session, claim.message)
                if run.id != claim.run_id or attempt.execution_token != claim.execution_token or int(attempt.fencing_token or 0) != claim.fencing_token or attempt.status not in {"RUNNING", "CANCEL_REQUESTED"}:
                    return False
                canceled = str(job.status) == "CANCEL_REQUESTED" or bool(run.cancel_requested)
                final = "CANCELED" if canceled else status
                final_error = None if canceled or final == "SUCCEEDED" else (_json(error) if error else None)
                now = _now(); job.status = attempt.status = run.status = agent_attempt.status = final
                public_result_ref = {"type": "agent_run", "id": run.id} if final == "SUCCEEDED" else None
                projected_result = _json(result) if final == "SUCCEEDED" else None
                job.result_ref = public_result_ref; job.error = final_error; job.finished_at = now; job.updated_at = now; job.revision = int(job.revision or 1) + 1; job.etag = str(job.revision)
                attempt.result_ref = public_result_ref; attempt.error = final_error; attempt.finished_at = now; attempt.lease_until = None
                if usage is not None:
                    run.usage = _json(usage)
                if final == "CANCELED":
                    run.pending_actions = []
                run.error = final_error; run.finished_at = now; run.updated_at = now; run.lease_until = None
                agent_attempt.error = final_error; agent_attempt.finished_at = now; agent_attempt.lease_until = None
                if final == "SUCCEEDED":
                    existing_message = (await session.execute(select(MessageRow).where(
                        MessageRow.conversation_id == run.conversation_id,
                        MessageRow.agent_run_id == run.id,
                        MessageRow.role == "assistant",
                    ).with_for_update())).scalars().first()
                    if existing_message is None:
                        latest_seq = await session.scalar(select(func.max(MessageRow.seq)).where(
                            MessageRow.conversation_id == run.conversation_id,
                        ))
                        session.add(MessageRow(
                            id=f"msg_{uuid4().hex}",
                            conversation_id=run.conversation_id,
                            seq=int(latest_seq or 0) + 1,
                            role="assistant",
                            agent_run_id=run.id,
                            status="FINAL",
                            blocks=_assistant_text_blocks(projected_result),
                            attachment_ids=[],
                            client_message_id=None,
                            payload_hash=None,
                            created_at=now,
                        ))
                    else:
                        existing_message.status = "FINAL"
                        existing_message.blocks = _assistant_text_blocks(projected_result)
                else:
                    partial_messages = (await session.execute(select(MessageRow).where(
                        MessageRow.agent_run_id == run.id, MessageRow.role == "assistant",
                        MessageRow.status == "PARTIAL",
                    ).with_for_update())).scalars().all()
                    for partial in partial_messages:
                        partial.status = "FAILED" if final in {"FAILED", "CANCELED", "TIMED_OUT", "INTERRUPTED"} else "PARTIAL"
                await self._job_event(session, job.id, "job.canceled" if canceled else f"job.{final.lower()}", {"status": final, "attempt": claim.message.attempt, "error": final_error})
                await self._agent_event(session, run.id, "run.canceled" if canceled else ("run.completed" if final == "SUCCEEDED" else "run.failed"), {"status": final, "attempt": claim.message.attempt, "error": final_error})
                return JobStatus.CANCELED if canceled else True

    async def reconcile_finalization_failure(
        self,
        *,
        run_id: str,
        job_id: str,
        attempt: int,
        agent_attempt_id: str,
        llm_call_id: str,
        execution_token: str,
        fencing_token: int,
        project_id: str,
        actor_id: str,
        apply: bool = False,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """Inspect or apply one exact no-provider terminal reconcile.

        This is deliberately a narrow maintenance port: every persisted
        identity, project/actor scope, and fence must match, the run must still
        be RUNNING, and the recorded LLM call must already be SUCCEEDED.  A
        dry-run is the default and performs no writes.  Applying never calls a
        provider and never projects success from a model response.
        """
        error = {"code": "FINALIZATION_PERSISTENCE_FAILED", "message": "助手结果终结持久化失败", "retryable": False}
        if not all(isinstance(value, str) and value.strip() for value in (run_id, job_id, agent_attempt_id, llm_call_id, execution_token, project_id, actor_id)):
            return {"matched": False, "applied": False, "reason": "INVALID_EXACT_SCOPE"}
        if type(attempt) is not int or attempt < 1 or type(fencing_token) is not int or fencing_token < 1:
            return {"matched": False, "applied": False, "reason": "INVALID_EXACT_FENCE"}
        request_id = request_id or f"assistant-finalization-reconcile:{run_id}:{attempt}"
        async with self.session_factory() as session:
            async with session.begin():
                lock = lambda statement: statement.with_for_update() if apply else statement
                job = (await session.execute(lock(select(Job).where(Job.id == job_id)))).scalar_one_or_none()
                attempt_row = (await session.execute(lock(select(JobAttempt).where(JobAttempt.job_id == job_id, JobAttempt.attempt == attempt)))).scalar_one_or_none()
                run = (await session.execute(lock(select(AgentRunRow).where(AgentRunRow.id == run_id)))).scalar_one_or_none()
                agent_attempt = (await session.execute(lock(select(AgentAttemptRow).where(AgentAttemptRow.id == agent_attempt_id)))).scalar_one_or_none()
                call = (await session.execute(lock(select(LlmCallRow).where(LlmCallRow.id == llm_call_id)))).scalar_one_or_none()
                conversation = (await session.execute(lock(select(ConversationRow).where(ConversationRow.id == getattr(run, "conversation_id", ""))))).scalar_one_or_none() if run is not None else None
                if any(value is None for value in (job, attempt_row, run, agent_attempt, call, conversation)):
                    return {"matched": False, "applied": False, "reason": "EXACT_ROW_NOT_FOUND"}
                resource_ref = job.resource_ref if isinstance(getattr(job, "resource_ref", None), Mapping) else {}
                identity_ok = not (
                    run.id != run_id or run.attempt != attempt
                    or run.project_id != project_id or int(run.fencing_token or 0) != int(fencing_token)
                    or conversation.project_id != project_id or conversation.owner_id != actor_id
                    or job.id != job_id or job.attempt != attempt or job.project_id != project_id
                    or resource_ref.get("type") != "agent_run" or resource_ref.get("id") != run_id
                    or getattr(attempt_row, "job_id", job_id) != job_id or attempt_row.attempt != attempt
                    or attempt_row.execution_token != execution_token
                    or int(attempt_row.fencing_token or 0) != int(fencing_token)
                    or agent_attempt.id != agent_attempt_id or agent_attempt.agent_run_id != run_id or agent_attempt.attempt != attempt
                    or agent_attempt.execution_token != execution_token or int(agent_attempt.fencing_token or 0) != int(fencing_token)
                    or call.id != llm_call_id or call.agent_run_id != run_id or call.agent_attempt_id != agent_attempt_id
                    or call.provider_configuration_id != getattr(run, "provider_configuration_id", None)
                    or call.provider_configuration_revision != getattr(run, "provider_configuration_revision", None)
                    or call.status != "SUCCEEDED"
                )
                if not identity_ok:
                    return {"matched": False, "applied": False, "reason": "EXACT_SCOPE_OR_FENCE_MISMATCH"}
                statuses = (job.status, attempt_row.status, run.status, agent_attempt.status)
                if statuses == ("FAILED", "FAILED", "FAILED", "FAILED") and all(getattr(row, "error", None) and row.error.get("code") == error["code"] for row in (job, attempt_row, run, agent_attempt)):
                    return {"matched": True, "applied": False, "mode": "APPLY", "idempotent": True, "reason": "ALREADY_RECONCILED"}
                if statuses != ("RUNNING", "RUNNING", "RUNNING", "RUNNING"):
                    return {"matched": False, "applied": False, "reason": "EXACT_STATE_MISMATCH"}
                now = _now()
                usage = dict(run.usage or {})
                call_usage = dict(call.usage or {})
                usage.update({
                    "model_requests": max(1, int(usage.get("model_requests", 0) or 0)),
                    "tool_calls": max(0, int(usage.get("tool_calls", 0) or 0)),
                    "transport_attempts": max(int(usage.get("transport_attempts", 0) or 0), int(call.transport_attempt or 1)),
                })
                for key in ("input_tokens", "output_tokens"):
                    if call_usage.get(key) is not None:
                        usage[key] = int(call_usage[key])
                summary = {
                    "run_id": run_id, "job_id": job_id, "attempt": attempt,
                    "agent_attempt_id": agent_attempt_id, "llm_call_id": llm_call_id,
                    "project_id": project_id, "actor_id": actor_id,
                    "before_status": "RUNNING", "after_status": "FAILED",
                    "error_code": error["code"], "usage": {
                        key: usage[key] for key in ("model_requests", "input_tokens", "output_tokens", "tool_calls", "transport_attempts") if key in usage
                    },
                }
                if not apply:
                    return {"matched": True, "applied": False, "mode": "DRY_RUN", "summary": summary}
                job.status = attempt_row.status = run.status = agent_attempt.status = "FAILED"
                job.error = attempt_row.error = run.error = agent_attempt.error = error
                job.result_ref = attempt_row.result_ref = None
                job.finished_at = run.finished_at = agent_attempt.finished_at = attempt_row.finished_at = now
                job.updated_at = run.updated_at = now
                job.revision = int(job.revision or 1) + 1
                job.etag = str(job.revision)
                run.usage = usage
                run.lease_until = attempt_row.lease_until = agent_attempt.lease_until = None
                session.add(AuditEvent(
                    id=f"audit_{uuid4().hex}", project_id=project_id, actor_id=actor_id,
                    auth_method="service", interaction="service", request_id=request_id,
                    action="assistant.run.reconcile_finalization_failure", resource_type="agent_run",
                    resource_id=run_id, resource_ref={"type": "agent_run", "id": run_id},
                    before_summary={"status": "RUNNING", "attempt": attempt},
                    after_summary={"status": "FAILED", "attempt": attempt, "error_code": error["code"], "usage": summary["usage"]},
                    confirmation_id=None, config_hash=None, job_id=job_id, agent_run_id=run_id,
                    result="SUCCEEDED", timestamp=now,
                ))
                await self._job_event(session, job.id, "job.failed", {"status": "FAILED", "attempt": attempt, "error": error})
                await self._agent_event(session, run.id, "run.failed", {"status": "FAILED", "attempt": attempt, "error": error})
                return {"matched": True, "applied": True, "mode": "APPLY", "summary": summary}

    async def wait_for_confirmation(self, claim: _Claim, result: object) -> bool:
        """Persist assistant waiting state without using an invalid Job state."""
        async with self.session_factory() as session:
            async with session.begin():
                job, attempt, run, agent_attempt, _config, _dispatch = await self._rows(session, claim.message)
                if run.id != claim.run_id or attempt.execution_token != claim.execution_token or int(attempt.fencing_token or 0) != claim.fencing_token:
                    return False
                now = _now()
                # Platform Job/JobAttempt have no WAITING_CONFIRMATION state;
                # the assistant run and attempt carry the durable pause.
                run.status = agent_attempt.status = "WAITING_CONFIRMATION"
                run.pending_actions = _json(getattr(result, "pending_actions", None) or (result.get("pending_actions", []) if isinstance(result, Mapping) else run.pending_actions))
                run.updated_at = now; run.lease_until = None
                attempt.lease_until = None
                agent_attempt.lease_until = None
                await self._job_event(session, job.id, "job.waiting_confirmation", {"status": "RUNNING", "attempt": claim.message.attempt})
                await self._agent_event(session, run.id, "run.waiting_confirmation", {"status": "WAITING_CONFIRMATION", "attempt": claim.message.attempt})
                return True

    @staticmethod
    async def _job_event(session: AsyncSession, job_id: str, event_type: str, data: Mapping[str, Any]) -> None:
        latest = (await session.execute(select(JobEvent.seq).where(JobEvent.job_id == job_id).order_by(JobEvent.seq.desc()).limit(1))).first()
        seq = int(latest[0]) + 1 if latest else 0
        session.add(JobEvent(id=f"event_{uuid4().hex}", job_id=job_id, stream_id=job_id, seq=seq, type=event_type, schema_version="1.0", data=dict(data), timestamp=_now()))

    @staticmethod
    async def _agent_event(session: AsyncSession, run_id: str, event_type: str, data: Mapping[str, Any]) -> None:
        run = (await session.execute(select(AgentRunRow).where(AgentRunRow.id == run_id).with_for_update())).scalar_one_or_none()
        existing_rows = (await session.execute(select(AgentEventRow).where(
            AgentEventRow.stream_type == "agent_run", AgentEventRow.stream_id == run_id,
        ))).scalars().all()
        attempt = int(data.get("attempt", 0) or 0)
        if event_type == "run.failed" and attempt > 0:
            existing = next((item for item in existing_rows if item.type == "run.failed" and int((item.data or {}).get("attempt", 0) or 0) == attempt), None)
            if existing is not None:
                if run is not None:
                    run.last_event_seq = max(int(run.last_event_seq or 0), int(existing.seq))
                return
        latest = max((int(item.seq) for item in existing_rows), default=-1)
        seq = latest + 1
        session.add(AgentEventRow(id=f"assistant_worker_event_{uuid4().hex}", stream_type="agent_run", stream_id=run_id, seq=seq, type=event_type, schema_version="1.0", data=dict(data), timestamp=_now(), agent_run_id=run_id))
        if run is not None:
            run.last_event_seq = max(int(run.last_event_seq or 0), seq)


class AssistantProductionWorker:
    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession], runtime: object | None = None, runtime_factory: Callable[..., Any] | None = None, services_factory: Callable[..., Any] | None = None, actor_context: object | None = None, cancel_probe: object | None = None, lease_seconds: int = 60):
        self.store = AsyncAssistantExecutionStore(session_factory, lease_seconds=lease_seconds)
        self.runtime, self.runtime_factory, self.services_factory, self.actor_context, self.cancel_probe = runtime, runtime_factory, services_factory, actor_context, cancel_probe

    @staticmethod
    def _runtime_ready(runtime: object | None) -> bool:
        if runtime is None:
            return False
        provider = getattr(runtime, "provider", None)
        config = getattr(runtime, "config", None)
        if provider is None or not callable(getattr(provider, "run_agent_async", None)):
            return False
        if config is None:
            return False
        values = [getattr(config, name, None) for name in ("url", "model_id", "key_reference")]
        return all(isinstance(value, str) and value.strip() and "unconfigured" not in value.lower() for value in values)

    @staticmethod
    def _runtime_usage(value: object | None) -> dict[str, Any]:
        """Project bounded runtime counters into the durable run usage DTO."""
        def read(name: str, default: Any = 0) -> Any:
            if isinstance(value, Mapping):
                return value.get(name, default)
            return getattr(value, name, default)

        def nonnegative_int(raw: Any) -> int:
            try:
                return max(0, int(raw))
            except (TypeError, ValueError):
                return 0

        result = {
            "model_requests": nonnegative_int(read("provider_requests")),
            "tool_calls": nonnegative_int(read("tool_calls")),
            "transport_attempts": nonnegative_int(read("transport_attempts")),
        }
        runtime_usage = read("usage", {})
        if isinstance(runtime_usage, Mapping):
            for key in ("input_tokens", "output_tokens", "cached_input_tokens"):
                raw = runtime_usage.get(key)
                if raw is not None:
                    result[key] = nonnegative_int(raw)
        return result

    async def _finish_initialization_failure(self, claim: _Claim, error: Mapping[str, Any]) -> AssistantWorkerResult:
        """Fence and persist failures that occur before runtime execution."""
        # A finish failure must escape to the outbox dispatcher.  Returning a
        # normal FAILED result would make the dispatcher ACK while the fenced
        # rows remain RUNNING; the same delivery then cannot converge them.
        finished = await self.store.finish(
            claim,
            status="FAILED",
            error=error,
            usage={"model_requests": 0, "tool_calls": 0, "transport_attempts": 0},
        )
        if finished is JobStatus.CANCELED:
            return AssistantWorkerResult(JobStatus.CANCELED, replayed=True)
        if not finished:
            return AssistantWorkerResult(JobStatus.RUNNING, replayed=True)
        return AssistantWorkerResult(JobStatus.FAILED, error=dict(error))

    async def _run_with_lease_heartbeat(self, claim: _Claim, operation: Any, lease_guard: object | None) -> Any:
        dispatcher = getattr(lease_guard, "dispatcher", None)
        interval = getattr(dispatcher, "heartbeat_seconds", min(15.0, self.store.lease_seconds / 3))
        if isinstance(interval, bool) or not isinstance(interval, (int, float)) or interval <= 0 or interval >= self.store.lease_seconds:
            close = getattr(operation, "close", None)
            if callable(close):
                close()
            raise AssistantWorkerError("WORKER_LEASE_HEARTBEAT_INVALID")

        async def renew() -> None:
            while True:
                await asyncio.sleep(float(interval))
                if lease_guard is not None and callable(getattr(lease_guard, "check", None)):
                    lease_guard.check()
                if await self.store.is_cancel_requested(claim.run_id):
                    raise _AssistantRunCancellationRequested
                if not await self.store.renew_lease(claim):
                    raise AssistantWorkerError("WORKER_JOB_FENCE_INVALID")

        runtime_task = asyncio.create_task(operation)
        heartbeat_task = asyncio.create_task(renew())
        try:
            done, _pending = await asyncio.wait(
                (runtime_task, heartbeat_task), return_when=asyncio.FIRST_COMPLETED,
            )
            if heartbeat_task in done:
                await heartbeat_task
                raise AssistantWorkerError("WORKER_JOB_FENCE_INVALID")
            return await runtime_task
        finally:
            for task in (heartbeat_task, runtime_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(heartbeat_task, runtime_task, return_exceptions=True)

    async def execute(self, message: AssistantWorkerMessage | Mapping[str, Any], lease_guard: object | None = None) -> AssistantWorkerResult:
        message = message if isinstance(message, AssistantWorkerMessage) else AssistantWorkerMessage.from_payload(message)
        claim, status = await self.store.claim(message)
        if claim is None:
            return AssistantWorkerResult(JobStatus(status), replayed=True)
        try:
            scope = await self.store.load_scope(claim.run_id)
            if claim.provider_config is None or getattr(claim.provider_config, "status", None) not in {"ACTIVE", "RETIRED"}:
                raise AssistantWorkerError("ASSISTANT_LLM_CONFIGURATION_UNAVAILABLE")
            runtime = self.runtime
            if self.runtime_factory:
                runtime = await _call_factory(
                    self.runtime_factory,
                    run_id=claim.run_id,
                    attempt=message.attempt,
                    config=claim.provider_config,
                    scope=scope,
                )
            if self.services_factory is not None and runtime is not None:
                # A runtime template is copied before binding request/job scoped
                # services; no ToolSet or Session is shared across worker jobs.
                import copy
                runtime = copy.copy(runtime)
                services = await _call_factory(self.services_factory, scope=scope, run_id=claim.run_id, attempt=message.attempt, config=claim.provider_config)
                toolset = getattr(runtime, "toolset", None)
                if services and hasattr(toolset, "bind"):
                    runtime.toolset = toolset.bind(services)
            if self.actor_context is None or not self.actor_context.can_access_project(scope.project_id) or self.actor_context.actor_id != scope.actor_id:
                raise AssistantWorkerError("ASSISTANT_ACTOR_SCOPE_UNAVAILABLE")
            if not self._runtime_ready(runtime):
                raise AssistantWorkerError("ASSISTANT_LLM_CONFIGURATION_UNAVAILABLE")
        except AssistantWorkerError as exc:
            return await self._finish_initialization_failure(claim, _safe_error(exc.code))
        except Exception:
            return await self._finish_initialization_failure(claim, _safe_error("ASSISTANT_RUNTIME_FAILED"))
        runtime_usage: dict[str, Any] = {"model_requests": 0, "tool_calls": 0, "transport_attempts": 0}
        try:
            run = getattr(runtime, "run_async", None) or getattr(runtime, "run", None) or getattr(runtime, "execute", None)
            from chrom_platform.modules.assistant.priority import PriorityOperationContext
            actor = self.actor_context.model_copy(update={"project_ids": [scope.project_id], "default_project_id": scope.project_id})
            context = PriorityOperationContext(
                actor=actor, interaction="assistant", request_id=f"worker:{claim.run_id}",
                conversation_id=getattr(scope, "conversation_id", None),
                agent_run_id=claim.run_id, server_project_id=scope.project_id,
                server_artifact_id=scope.artifact_ids[0] if len(scope.artifact_ids) == 1 else None,
                idempotency_key=f"assistant-run:{claim.run_id}:{message.attempt}",
                confirmation_id=scope.confirmation_id, agent_attempt_id=getattr(claim, "agent_attempt_id", None),
                execution_token=claim.execution_token, fencing_token=claim.fencing_token,
            )
            async def stream_sink(_ctx, op, payload):
                return await self.store.stream_text(claim, op, payload)

            async def checkpoint_loader(_ctx):
                return await self.store.framework_state(claim)

            async def checkpoint_sink(_ctx, state):
                return await self.store.framework_state(claim, state)

            runtime.stream_sink = stream_sink
            runtime.checkpoint_loader = checkpoint_loader
            runtime.checkpoint_sink = checkpoint_sink
            async def invoke_runtime():
                if inspect.iscoroutinefunction(run):
                    value = await run(run_id=claim.run_id, attempt=message.attempt, ctx=context)
                else:
                    value = await asyncio.to_thread(run, run_id=claim.run_id, attempt=message.attempt, ctx=context)
                return await value if inspect.isawaitable(value) else value

            value = await self._run_with_lease_heartbeat(claim, invoke_runtime(), lease_guard)
            if lease_guard is not None and callable(getattr(lease_guard, "check", None)):
                lease_guard.check()
            result_status = str(getattr(value, "status", value.get("status") if isinstance(value, Mapping) else "SUCCEEDED"))
            runtime_usage = self._runtime_usage(value)
            if result_status == "WAITING_CONFIRMATION":
                paused = await self.store.wait_for_confirmation(claim, value)
                return AssistantWorkerResult(JobStatus.RUNNING, result=value, replayed=not paused)
            if result_status not in {"SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELED"}:
                result_status = "FAILED"
            error = getattr(value, "error", None) if result_status != "SUCCEEDED" else None
            if isinstance(value, Mapping):
                error = value.get("error") if result_status != "SUCCEEDED" else None
            # Job.result_ref is a JSON column.  RuntimeResult is a dataclass,
            # so project successful results before the transaction rather than
            # letting SQLAlchemy fail after provider success.
            result = _runtime_result_json(value) if result_status == "SUCCEEDED" else None
        except _AssistantRunCancellationRequested:
            result_status, result, error = "CANCELED", None, _safe_error("RUN_CANCELED", "助手运行已取消")
        except Exception:
            result_status, result, error = "FAILED", None, _safe_error("ASSISTANT_RUNTIME_FAILED")
        finished = await self.store.finish(claim, status=result_status, result=result, error=error, usage=runtime_usage)
        if finished is JobStatus.CANCELED:
            return AssistantWorkerResult(JobStatus.CANCELED, replayed=True)
        if not finished:
            return AssistantWorkerResult(JobStatus.RUNNING, replayed=True)
        return AssistantWorkerResult(JobStatus(result_status), result=result, error=error)

    async def cancel(self, message: AssistantWorkerMessage | Mapping[str, Any]) -> AssistantWorkerResult:
        message = message if isinstance(message, AssistantWorkerMessage) else AssistantWorkerMessage.from_payload(message)
        return await self.store.cancel(message, cancel_probe=self.cancel_probe)

    async def retry(self, message: AssistantWorkerMessage | Mapping[str, Any], lease_guard: object | None = None) -> AssistantWorkerResult:
        return await self.execute(message, lease_guard=lease_guard)

    async def handle(self, message: AssistantWorkerMessage | Mapping[str, Any], *, event_type: str | None = None) -> AssistantWorkerResult:
        return await (self.cancel(message) if event_type == "job.cancel" else self.execute(message))


AsyncAssistantWorker = AssistantProductionWorker


@dataclass(frozen=True)
class AssistantLlmTestMessage:
    job_id: str
    attempt: int
    dispatch_id: str

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "AssistantLlmTestMessage":
        if not isinstance(payload, Mapping) or set(payload) != {"job_id", "attempt", "dispatch_id"}:
            raise AssistantWorkerError("INVALID_WORKER_PAYLOAD")
        if type(payload["job_id"]) is not str or not payload["job_id"] or type(payload["dispatch_id"]) is not str or not payload["dispatch_id"] or type(payload["attempt"]) is not int or payload["attempt"] < 1:
            raise AssistantWorkerError("INVALID_WORKER_PAYLOAD")
        return cls(payload["job_id"], payload["attempt"], payload["dispatch_id"])


@dataclass(frozen=True)
class _LlmTestClaim:
    message: AssistantLlmTestMessage
    test_id: str
    config: object
    token: str
    fence: int
    deadline_at: datetime
    request_budget: object | None = None


class AssistantLlmConfigurationTestWorker:
    """Durably test an immutable provider config without exposing business tools."""

    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession],
                 test_runner: Callable[..., Any] | None = None,
                 secret_resolver: Callable[[str], str] | None = None,
                 approved_endpoints_resolver: Callable[[], str | None] | None = None) -> None:
        self.session_factory, self.test_runner = session_factory, test_runner
        self.secret_resolver, self.approved_endpoints_resolver = secret_resolver, approved_endpoints_resolver

    @staticmethod
    async def _append_job_event(session: AsyncSession, job_id: str, event_type: str, data: Mapping[str, Any]) -> None:
        from chrom_platform.infrastructure.models import JobEvent
        seq = await session.scalar(select(func.max(JobEvent.seq)).where(JobEvent.job_id == job_id))
        next_seq = int(seq if seq is not None else -1) + 1
        session.add(JobEvent(
            id=f"assistant_llm_test_event_{uuid4().hex}", job_id=job_id,
            stream_id=job_id, seq=next_seq, type=event_type,
            schema_version="1.0", data=dict(data), timestamp=_now(),
        ))

    async def _claim(self, message: AssistantLlmTestMessage) -> tuple[_LlmTestClaim | None, str]:
        async with self.session_factory() as session:
            async with session.begin():
                job = await session.scalar(select(Job).where(Job.id == message.job_id).with_for_update())
                if job is None or int(job.attempt) != message.attempt or job.kind != "assistant_llm_configuration_test":
                    raise AssistantWorkerError("ASSISTANT_LLM_TEST_JOB_INVALID")
                attempt = await session.scalar(select(JobAttempt).where(
                    JobAttempt.job_id == job.id, JobAttempt.attempt == message.attempt,
                ).with_for_update())
                dispatch = await session.scalar(select(OutboxEvent).where(
                    OutboxEvent.dispatch_id == message.dispatch_id,
                    OutboxEvent.aggregate_type == "job", OutboxEvent.aggregate_id == job.id,
                    OutboxEvent.event_type == "job.execute",
                ).with_for_update())
                expected_payload = {"job_id": job.id, "attempt": message.attempt, "dispatch_id": message.dispatch_id}
                ref = job.resource_ref if isinstance(job.resource_ref, Mapping) else {}
                if attempt is None or dispatch is None or dict(dispatch.payload or {}) != expected_payload or ref.get("type") != "llm_test" or not isinstance(ref.get("id"), str):
                    raise AssistantWorkerError("ASSISTANT_LLM_TEST_FENCE_INVALID")
                test = await session.scalar(select(LlmTestResultRow).where(
                    LlmTestResultRow.id == ref["id"], LlmTestResultRow.job_id == job.id,
                ).with_for_update())
                if test is None:
                    raise AssistantWorkerError("ASSISTANT_LLM_TEST_RESOURCE_INVALID")
                if job.status in _TERMINAL:
                    return None, job.status
                deadline_at = job.deadline_at
                if not isinstance(deadline_at, datetime):
                    deadline_at = _now()
                if deadline_at.tzinfo is None:
                    deadline_at = deadline_at.replace(tzinfo=timezone.utc)
                now = _now()
                if deadline_at <= now:
                    checks = {"connectivity": "FAILED", "streaming": "FAILED", "tool_calling": "FAILED", "structured_output": "NOT_SUPPORTED"}
                    safe_error = {"code": "PROVIDER_TEST_FAILED", "message": "LLM 配置测试超过时间预算"}
                    job.status = attempt.status = test.status = "FAILED"
                    job.error = attempt.error = safe_error
                    test.error, test.checks, test.finished_at = safe_error, checks, now
                    job.finished_at = attempt.finished_at = now
                    job.updated_at = now
                    job.revision = int(job.revision or 1) + 1
                    job.etag = str(job.revision)
                    await self._append_job_event(session, job.id, "job.failed", {"status": "FAILED", "attempt": message.attempt})
                    return None, "FAILED"
                if job.status == "RUNNING" or test.status == "RUNNING":
                    return None, "RUNNING"
                config = await session.scalar(select(LlmConfigurationRow).where(
                    LlmConfigurationRow.id == test.configuration_id,
                    LlmConfigurationRow.project_id == job.project_id,
                ).with_for_update())
                settings = dict(config.settings or {}) if config is not None else {}
                digest = str(settings.get("config_hash", ""))
                if (config is None or config.status not in {"CANDIDATE", "TESTED", "ACTIVE", "RETIRED"}
                        or config.revision != test.configuration_revision
                        or len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest)
                        or test.configuration_hash != digest):
                    raise AssistantWorkerError("ASSISTANT_LLM_TEST_CONFIGURATION_MISMATCH")
                if job.status != "QUEUED" or attempt.status != "QUEUED" or test.status != "QUEUED":
                    return None, job.status
                token, fence = f"llmtest_{uuid4().hex}", int(attempt.fencing_token or 0) + 1
                job.status = attempt.status = test.status = "RUNNING"
                job.started_at = job.started_at or now
                job.updated_at = now
                job.revision = int(job.revision or 1) + 1
                job.etag = str(job.revision)
                attempt.started_at = attempt.started_at or now
                attempt.execution_token, attempt.fencing_token = token, fence
                attempt.lease_until = now + timedelta(minutes=10)
                await self._append_job_event(session, job.id, "job.running", {"status": "RUNNING", "attempt": message.attempt})
                config_snapshot = type("AssistantLlmTestConfig", (), {
                    "id": config.id, "project_id": config.project_id,
                    "revision": config.revision, "url": config.url,
                    "model_id": config.model_id, "key_reference": config.key_reference,
                    "status": config.status, "settings": settings,
                })()
                return _LlmTestClaim(message, test.id, config_snapshot, token, fence, deadline_at), "RUNNING"

    @staticmethod
    async def _test_adapter(config: object, *, secret_resolver=None,
                            approved_endpoints_resolver=None, job_id=None,
                            test_id=None, request_budget=None) -> dict[str, Any]:
        """Use the production native Agent/transport with an in-memory echo."""
        from .adapter import PydanticAIAdapter, ProviderRequestBudget
        from .runtime import RuntimeConfigSnapshot
        from .security import trusted_provider_url
        from .tools import BoundTool, ToolExecutionLedger, ToolSet, ToolSpec
        settings = dict(getattr(config, "settings", {}) or {})
        checks = {"connectivity": "FAILED", "streaming": "FAILED",
                  "tool_calling": "FAILED", "structured_output": "NOT_SUPPORTED"}
        if request_budget is None:
            request_budget = ProviderRequestBudget(deadline_monotonic=time.monotonic() + 90.0, max_requests=5)
        try:
            approved = approved_endpoints_resolver() if approved_endpoints_resolver else None
            policy = str(settings.get("external_send_policy", "deny_external"))
            try:
                trusted_provider_url(str(config.url), external_send_policy=policy, approved_endpoints=approved)
            except ValueError:
                _log_llm_test_diagnostic(job_id=job_id, test_id=test_id, stage="endpoint_policy",
                                         category="ENDPOINT_POLICY_REJECTED")
                return {"status": "FAILED", "checks": checks,
                        "error": {"code": "PROVIDER_TEST_FAILED", "message": "LLM 配置能力测试未通过"}}
            snapshot = RuntimeConfigSnapshot(
                url=str(config.url), model_id=str(config.model_id),
                key_reference=str(config.key_reference), config_revision=str(config.revision),
                provider_configuration_id=str(config.id), external_send_policy=policy,
                provider_configuration_hash=str(settings.get("config_hash", "")),
                approved_endpoints=approved,
            )
            adapter = PydanticAIAdapter(secret_resolver=secret_resolver,
                                       approved_endpoints_resolver=approved_endpoints_resolver)

            class Echo:
                @staticmethod
                def validate(_ctx, **_kwargs):
                    return None

                @staticmethod
                def execute(_ctx, *, arguments, **_kwargs):
                    return {"echo": arguments["value"]}

            spec = ToolSpec(
                "assistant_test_echo", "Return the supplied value for configuration testing only.",
                service="echo", permission="assistant:use", modes=frozenset({"business"}),
                argument_schema={"type": "object", "properties": {"value": {"type": "string"}},
                                 "required": ["value"], "additionalProperties": False},
            )
            ledger = ToolExecutionLedger()
            toolset = ToolSet({spec.name: spec}, build="full", mode="business",
                              permissions=frozenset({"*"}))
            bound = toolset.bind({"echo": Echo()}, ledger=ledger)

            def validate_tool(name, arguments, call_id):
                # Tool.from_schema does not validate arguments. Keep the
                # platform allowlist and the exact echo DTO boundary here.
                if name != spec.name or set(arguments) != {"value"} or not isinstance(arguments.get("value"), str):
                    raise ValueError("invalid configuration-test tool arguments")
                return dict(arguments)

            def execute_tool(name, arguments, call_id):
                validate_tool(name, arguments, call_id)
                execution = bound[name](None, arguments, run_id=f"llm-test:{config.id}")
                return execution.result

            for stage, prompt in (
                ("connectivity", "Reply with the word connected."),
                ("tool_calling", "Call assistant_test_echo once with value chrom-platform-test, then report its result."),
                ("streaming", "Reply with the word streaming."),
            ):
                if not request_budget.can_start_request:
                    break
                text_events = []

                async def on_event(event):
                    if event.get("type") in {"text_start", "text_delta"}:
                        text_events.append(dict(event))

                request_budget.begin_provider_call(stage)
                try:
                    remaining = request_budget.deadline_remaining_seconds()
                    result = await asyncio.wait_for(
                        adapter.run_agent_async(
                            config=snapshot, messages=[{"role": "user", "content": prompt}],
                            tools=toolset.schemas() if stage == "tool_calling" else [],
                            validate_tool=validate_tool, execute_tool=execute_tool,
                            on_event=on_event, history_state=None, request_budget=request_budget,
                            timeout_seconds=min(90.0, float(settings.get("timeout_seconds", 90))),
                            max_turns=3 if stage == "tool_calling" else 1,
                        ),
                        timeout=remaining,
                    )
                    if stage == "tool_calling":
                        results = result.get("tool_results", [])
                        valid = int(result.get("tool_calls", 0)) == 1 and any(
                            isinstance(item, Mapping) and item.get("echo") == "chrom-platform-test"
                            for item in results)
                    else:
                        valid = isinstance(result.get("text"), str) and bool(result["text"].strip())
                        if stage == "streaming":
                            valid = valid and bool(text_events)
                    if result.get("pending") or result.get("finish_reason") in {"error", "failed"}:
                        valid = False
                    checks[stage] = "PASSED" if valid else "FAILED"
                except Exception as exc:
                    category, http_status = _safe_llm_test_exception(exc)
                    _log_llm_test_diagnostic(job_id=job_id, test_id=test_id, stage=stage,
                                             category=category, http_status=http_status)
                    if http_status in {401, 403}:
                        request_budget.mark_auth_terminal(http_status, stage=stage)
                finally:
                    request_budget.end_provider_call(stage)
                auth_failed, auth_stage, active_calls, active_stages = request_budget.provider_call_state
                if auth_failed:
                    return _llm_test_auth_outcome(auth_stage or stage, checks)
                if active_calls:
                    return _llm_test_inflight_outcome(checks, active_stages)
            passed = all(checks[key] == "PASSED" for key in ("connectivity", "streaming", "tool_calling"))
            return {"status": "SUCCEEDED" if passed else "FAILED", "checks": checks,
                    "error": None if passed else {"code": "PROVIDER_TEST_FAILED", "message": "LLM 配置能力测试未通过"}}
        except Exception as exc:
            category, http_status = _safe_llm_test_exception(exc)
            _log_llm_test_diagnostic(job_id=job_id, test_id=test_id, stage="endpoint_policy",
                                     category=category, http_status=http_status)
            return {"status": "FAILED", "checks": checks,
                    "error": {"code": "PROVIDER_TEST_FAILED", "message": "LLM 配置能力测试未通过"}}
        finally:
            request_budget.seal()

    async def _finish(self, claim: _LlmTestClaim, outcome: Mapping[str, Any]) -> bool:
        request_budget = claim.request_budget
        budget_state = request_budget.seal_and_snapshot() if request_budget is not None else None
        async with self.session_factory() as session:
            async with session.begin():
                job = await session.scalar(select(Job).where(Job.id == claim.message.job_id).with_for_update())
                attempt = await session.scalar(select(JobAttempt).where(
                    JobAttempt.job_id == claim.message.job_id, JobAttempt.attempt == claim.message.attempt,
                ).with_for_update())
                test = await session.scalar(select(LlmTestResultRow).where(LlmTestResultRow.id == claim.test_id).with_for_update())
                config = await session.scalar(select(LlmConfigurationRow).where(LlmConfigurationRow.id == getattr(claim.config, "id", None)).with_for_update())
                if (job is None or attempt is None or test is None or config is None
                        or job.status != "RUNNING" or attempt.status != "RUNNING" or test.status != "RUNNING"
                        or attempt.execution_token != claim.token or int(attempt.fencing_token or 0) != claim.fence):
                    return False
                now = _now()
                if budget_state is not None:
                    auth_failed, auth_stage, active_calls, active_stages = budget_state
                    if auth_failed:
                        outcome = _llm_test_auth_outcome(
                            auth_stage,
                            outcome.get("checks") if isinstance(outcome.get("checks"), Mapping) else {},
                        )
                    elif active_calls:
                        outcome = _llm_test_inflight_outcome(
                            outcome.get("checks") if isinstance(outcome.get("checks"), Mapping) else {},
                            active_stages,
                        )
                deadline_at = job.deadline_at
                if not isinstance(deadline_at, datetime):
                    deadline_at = claim.deadline_at
                if deadline_at.tzinfo is None:
                    deadline_at = deadline_at.replace(tzinfo=timezone.utc)
                if deadline_at <= now:
                    outcome = {"status": "FAILED", "checks": {
                        "connectivity": "FAILED", "streaming": "FAILED",
                        "tool_calling": "FAILED", "structured_output": "NOT_SUPPORTED",
                    }, "error": {"code": "PROVIDER_TEST_FAILED", "message": "LLM 配置测试超过时间预算"}}
                hash_matches = (
                    config.project_id == job.project_id
                    and config.revision == test.configuration_revision
                    and str((config.settings or {}).get("config_hash", "")) == test.configuration_hash
                )
                passed = outcome.get("status") == "SUCCEEDED" and hash_matches
                status = "SUCCEEDED" if passed else "FAILED"
                checks = dict(outcome.get("checks") or {})
                allowed = {"connectivity", "streaming", "tool_calling", "structured_output"}
                has_skipped = any(value == "SKIPPED" for value in checks.values())
                if (set(checks) != allowed
                        or any(value not in {"PASSED", "FAILED", "SKIPPED", "NOT_SUPPORTED"} for value in checks.values())
                        or (has_skipped and not _is_llm_test_auth_failure(checks))
                        or (dict(checks) == _LLM_TEST_STREAM_AUTH_CHECKS and not _is_llm_test_auth_failure(checks))):
                    status, passed = "FAILED", False
                    checks = {key: "FAILED" if checks.get(key) == "FAILED" else "NOT_SUPPORTED" for key in allowed}
                if any(checks[key] != "PASSED" for key in ("connectivity", "streaming", "tool_calling")):
                    status, passed = "FAILED", False
                safe_error = None if passed else (
                    dict(_LLM_TEST_AUTH_ERROR) if _is_llm_test_auth_failure(checks) else {
                        "code": "PROVIDER_TEST_FAILED",
                        "message": "LLM 配置能力测试未通过",
                    }
                )
                test.status, test.checks, test.error, test.finished_at = status, checks, safe_error, now
                job.status = attempt.status = status
                job.finished_at = attempt.finished_at = now
                job.updated_at = now
                job.revision = int(job.revision or 1) + 1
                job.etag = str(job.revision)
                job.result_ref = {"type": "llm_test", "id": test.id} if passed else None
                job.error = attempt.error = dict(outcome.get("error") or {}) if not passed else None
                attempt.result_ref = job.result_ref
                current_hash = str((config.settings or {}).get("config_hash", ""))
                if (config.revision == test.configuration_revision and test.configuration_hash == current_hash
                        and config.status in {"CANDIDATE", "TESTED"}):
                    config.last_test_id = test.id
                    if passed:
                        config.status = "TESTED"
                    elif _is_llm_test_auth_failure(checks):
                        config.status = "CANDIDATE"
                if not passed:
                    job.error = attempt.error = dict(safe_error or {})
                await self._append_job_event(session, job.id, "job.completed" if passed else "job.failed", {"status": status, "attempt": claim.message.attempt})
                session.add(AuditEvent(
                    id=f"audit_{uuid4().hex}", project_id=job.project_id, actor_id=job.owner_id,
                    auth_method="worker", interaction="worker", request_id=f"job:{job.id}",
                    action="settings.llm.configuration.test_completed", resource_type="llm_test",
                    resource_id=test.id, resource_ref={"type": "llm_test", "id": test.id},
                    before_summary={"status": "RUNNING"}, after_summary={"status": status, "checks": checks},
                    confirmation_id=None, config_hash=test.configuration_hash, job_id=job.id,
                    agent_run_id=None, result=status, timestamp=now,
                ))
                return True

    async def execute(self, payload: AssistantLlmTestMessage | Mapping[str, Any], lease_guard: object | None = None) -> AssistantWorkerResult:
        message = payload if isinstance(payload, AssistantLlmTestMessage) else AssistantLlmTestMessage.from_payload(payload)
        claim, status = await self._claim(message)
        if claim is None:
            return AssistantWorkerResult(JobStatus(status), replayed=True)
        try:
            if lease_guard is not None and callable(getattr(lease_guard, "check", None)):
                lease_guard.check()
            if self.test_runner is not None:
                value = await _call_factory(self.test_runner, config=claim.config)
            else:
                remaining = (claim.deadline_at - _now()).total_seconds()
                if remaining <= 0:
                    raise TimeoutError("LLM 配置测试超过时间预算")
                from .adapter import ProviderRequestBudget
                request_budget = ProviderRequestBudget(deadline_monotonic=time.monotonic() + remaining, max_requests=5)
                claim = replace(claim, request_budget=request_budget)
                value = await self._test_adapter(
                    claim.config,
                    secret_resolver=self.secret_resolver,
                    approved_endpoints_resolver=self.approved_endpoints_resolver,
                    job_id=claim.message.job_id, test_id=claim.test_id,
                    request_budget=request_budget,
                )
            outcome = dict(value) if isinstance(value, Mapping) else {}
        except Exception as exc:
            category, http_status = _safe_llm_test_exception(exc)
            _log_llm_test_diagnostic(
                job_id=claim.message.job_id, test_id=claim.test_id,
                stage="worker_execution", category=category, http_status=http_status,
            )
            outcome = {"status": "FAILED", "checks": {
                "connectivity": "FAILED", "streaming": "FAILED",
                "tool_calling": "FAILED", "structured_output": "NOT_SUPPORTED",
            }, "error": {"code": "PROVIDER_TEST_FAILED", "message": "LLM 配置能力测试未通过"}}
        expected_checks = {"connectivity", "streaming", "tool_calling", "structured_output"}
        checks = outcome.get("checks") if isinstance(outcome, Mapping) else None
        has_skipped = isinstance(checks, Mapping) and any(value == "SKIPPED" for value in checks.values())
        if (not isinstance(checks, Mapping) or set(checks) != expected_checks
                or any(value not in {"PASSED", "FAILED", "SKIPPED", "NOT_SUPPORTED"} for value in checks.values())
                or (has_skipped and not _is_llm_test_auth_failure(checks))):
            outcome = {"status": "FAILED", "checks": {
                "connectivity": "FAILED", "streaming": "FAILED",
                "tool_calling": "FAILED", "structured_output": "NOT_SUPPORTED",
            }, "error": {"code": "PROVIDER_TEST_FAILED", "message": "LLM 配置能力测试未通过"}}
        elif _is_llm_test_auth_failure(checks):
            outcome = {"status": "FAILED", "checks": dict(checks), "error": dict(_LLM_TEST_AUTH_ERROR)}
        elif outcome.get("status") != "SUCCEEDED" or any(checks[key] != "PASSED" for key in ("connectivity", "streaming", "tool_calling")):
            outcome = {"status": "FAILED", "checks": dict(checks), "error": {"code": "PROVIDER_TEST_FAILED", "message": "LLM 配置能力测试未通过"}}
        else:
            outcome = {"status": "SUCCEEDED", "checks": dict(checks), "error": None}
        if lease_guard is not None and callable(getattr(lease_guard, "check", None)):
            lease_guard.check()
        finished = await self._finish(claim, outcome)
        if not finished:
            return AssistantWorkerResult(JobStatus.RUNNING, replayed=True)
        result_status = JobStatus.SUCCEEDED if outcome.get("status") == "SUCCEEDED" else JobStatus.FAILED
        return AssistantWorkerResult(result_status, result={"checks": outcome.get("checks")}, error=outcome.get("error"))

    async def cancel(self, payload: AssistantLlmTestMessage | Mapping[str, Any]) -> AssistantWorkerResult:
        raise AssistantWorkerError("ASSISTANT_LLM_TEST_CANCEL_UNSUPPORTED")

    async def retry(self, payload: AssistantLlmTestMessage | Mapping[str, Any], lease_guard: object | None = None) -> AssistantWorkerResult:
        return await self.execute(payload, lease_guard=lease_guard)


def build_assistant_worker_handler(*, session_factory: async_sessionmaker[AsyncSession], runtime: object | None = None, runtime_factory: Callable[..., Any] | None = None, services_factory: Callable[..., Any] | None = None, actor_context: object | None = None, cancel_probe: object | None = None, lease_seconds: int = 60) -> AssistantProductionWorker:
    return AssistantProductionWorker(session_factory=session_factory, runtime=runtime, runtime_factory=runtime_factory, services_factory=services_factory, actor_context=actor_context, cancel_probe=cancel_probe, lease_seconds=lease_seconds)


__all__ = ["AssistantProductionWorker", "AssistantRunScope", "AssistantWorkerError", "AssistantWorkerMessage", "AssistantWorkerResult", "AsyncAssistantExecutionStore", "AsyncAssistantWorker", "build_assistant_worker_handler"]
