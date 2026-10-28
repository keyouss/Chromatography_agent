"""Assistant runtime boundary for native async agents and platform tools.

The native framework remains behind the adapter. This module owns trusted
history selection, authorization, audit transitions, fenced persistence, and
public result shaping without importing framework types.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field, is_dataclass, replace
from datetime import datetime, timezone
import inspect
import hashlib
import json
import queue
import re
import threading
import time
from typing import Any, Iterable, Mapping
from uuid import uuid4

from chrom_platform.core.dto import ResourceRef
from .errors import AssistantError, unavailable
from .security import trusted_provider_url
from .schemas import PendingConfirmationBinding, ToolCall
from .tools import BoundTool, ToolExecution, ToolPending, ToolSet, normalize_arguments as _normalize_tool_arguments, _validate_schema as _validate_tool_schema


DEFAULT_MAX_OUTPUT_TOKENS = 512

_NATIVE_EXCEPTION_DIAGNOSTICS = {
    "TypeError": ("TypeError", "INTERNAL_TYPE_ERROR"),
    "ProtocolError": ("ProtocolError", "PROTOCOL"),
    "RemoteProtocolError": ("RemoteProtocolError", "PROTOCOL"),
    "UnexpectedModelBehavior": ("UnexpectedModelBehavior", "PROTOCOL"),
    "ConnectError": ("ConnectError", "TRANSPORT"),
    "ReadError": ("ReadError", "TRANSPORT"),
    "WriteError": ("WriteError", "TRANSPORT"),
    "NetworkError": ("NetworkError", "TRANSPORT"),
    "TimeoutError": ("TimeoutError", "TIMEOUT"),
    "TimeoutException": ("TimeoutException", "TIMEOUT"),
    "ConnectTimeout": ("ConnectTimeout", "TIMEOUT"),
    "ReadTimeout": ("ReadTimeout", "TIMEOUT"),
    "WriteTimeout": ("WriteTimeout", "TIMEOUT"),
    "ProviderResponseError": ("ProviderResponseError", "RESPONSE_SCHEMA"),
    "ProviderTransportError": ("ProviderTransportError", "TRANSPORT"),
}
_NATIVE_TRANSPORT_CATEGORIES = frozenset({
    "DNS", "TLS", "CONNECT", "TIMEOUT", "HTTP_4XX", "HTTP_5XX",
    "HTTP_REDIRECT", "RESPONSE_SCHEMA", "RESPONSE_LIMIT",
})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_utc_timestamp(value: Any) -> str | None:
    """Accept only the adapter's fixed-microsecond UTC wire representation."""
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z", value):
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return value if parsed.isoformat(timespec="microseconds").replace("+00:00", "Z") == value else None


@dataclass(frozen=True, slots=True)
class RuntimeConfigSnapshot:
    """Public provider configuration; it deliberately has no secret field."""

    url: str
    model_id: str
    key_reference: str
    config_revision: str
    provider_configuration_id: str | None = None
    provider_allowed_hosts: str | None = None
    configuration_state: str = "CONFIGURED"
    external_send_policy: str | None = None
    approved_endpoints: str | None = field(default=None, repr=False, compare=False)
    # Server-owned hash used to bind private native checkpoints to the
    # configuration row. It is intentionally omitted from public_dict().
    provider_configuration_hash: str | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        for name in ("url", "model_id", "key_reference", "config_revision"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} is required")
            if name == "key_reference" and any(token in value.lower() for token in ("secret=", "token=", "apikey=", "api_key=")):
                raise ValueError("key_reference must be a server-side reference, not a secret")
        if self.configuration_state == "UNAVAILABLE":
            if self.url != "https://unconfigured.invalid/v1":
                raise ValueError("unavailable runtime must use the non-routable sentinel")
            return
        if self.configuration_state != "CONFIGURED":
            raise ValueError("runtime configuration state is invalid")
        if self.external_send_policy is None:
            trusted_provider_url(self.url, allowed_hosts=self.provider_allowed_hosts)
        else:
            trusted_provider_url(
                self.url,
                external_send_policy=self.external_send_policy,
                approved_endpoints=self.approved_endpoints,
            )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RuntimeConfigSnapshot":
        if not isinstance(value, Mapping):
            raise ValueError("runtime configuration must be an object")
        required = {"url", "model_id", "key_reference", "config_revision"}
        allowed = required | {"external_send_policy"}
        if set(value) - allowed:
            raise ValueError("runtime configuration contains unsupported or secret fields")
        if not required <= set(value):
            raise ValueError("runtime configuration is incomplete")
        if any(not isinstance(value[key], str) for key in required):
            raise ValueError("runtime configuration fields must be strings")
        if "external_send_policy" in value and not isinstance(value["external_send_policy"], str):
            raise ValueError("runtime external-send policy must be a string")
        return cls(**{key: value[key] for key in value})

    def public_dict(self) -> dict[str, str]:
        result = {"url": self.url, "model_id": self.model_id, "key_reference": self.key_reference, "config_revision": self.config_revision}
        if self.external_send_policy is not None:
            result["external_send_policy"] = self.external_send_policy
        return result

    as_dict = public_dict


@dataclass(frozen=True, slots=True)
class RuntimeResult:
    status: str
    text: str | None = None
    structured: Any = None
    tool_results: tuple[Any, ...] = ()
    usage: Mapping[str, Any] = field(default_factory=dict)
    provider_requests: int = 0
    tool_calls: int = 0
    transport_attempts: int = 0
    error: Mapping[str, Any] | None = None
    pending_actions: tuple[Mapping[str, Any], ...] = ()


_ASSISTANT_SYSTEM_PROMPT = (
    "你是色谱业务助手，只调用服务端提供的已授权工具。"
    "先确认用户要回答问题、查询资料，还是执行一项业务动作；缺少业务动作必需参数时先询问，不能猜测或写入。"
    "读工具只能提供事实，不能代替写工具或伪造业务结果。"
    "执行预测时，先规范化结构，再按用户要求的色谱类型调用 get_prediction_capabilities 并传入 chromatography_type；"
    "根据该工具返回的 create_prediction 参数 schema 和 READY 模型选项直接准备下一步。"
    "结构成功且用户已提供全部业务条件、且有 READY 模型时应调用 create_prediction；缺少条件或可用模型时明确说明或追问，不要重复读取能力或模型列表。"
    "create_prediction 的返回只表示提交了 prediction/job；只有 get_prediction 返回真实 SUCCEEDED 结果时才能声称预测完成。"
    "如果工具结果含 wait_timeout=true，按其中真实 prediction/job 引用和当前状态报告等待超时；不能重新提交预测。"
    "摘要请求使用对应的 summary 工具；普通知识问答或缺参数补问可以直接结束。"
    "不要把未执行的工具、没有证据的资源、或未完成的预测写成已完成。"
)


class AgentRuntime:
    """Small model/tool loop with bounded requests and safe failure output."""

    def __init__(
        self,
        provider: object,
        config: RuntimeConfigSnapshot,
        *,
        toolset: ToolSet | Mapping[str, BoundTool] | None = None,
        services: Mapping[str, object] | None = None,
        ledger: object | None = None,
        authorization: object | None = None,
        history: object | None = None,
        history_loader: object | None = None,
        event_store: object | None = None,
        max_requests: int = 12,
        max_tool_calls: int = 24,
        timeout_seconds: float = 90.0,
        max_runtime_seconds: float = 600.0,
        max_retries: int = 2,
        response_schema: Mapping[str, Any] | None = None,
        stream: bool = False,
        context_inspector: object | None = None,
        trace_inspector: object | None = None,
        project_id: str | None = None,
        trace_metadata_only: bool = False,
        no_progress_limit: int = 3,
        stream_sink: object | None = None,
        checkpoint_loader: object | None = None,
        checkpoint_sink: object | None = None,
    ) -> None:
        self.provider = provider
        self.config = config if isinstance(config, RuntimeConfigSnapshot) else RuntimeConfigSnapshot.from_mapping(config) if isinstance(config, Mapping) else config
        if not isinstance(self.config, RuntimeConfigSnapshot):
            raise ValueError("runtime config must be a RuntimeConfigSnapshot")
        # Keep the declared schema visible when the worker has no request
        # service ports; execution still fails closed at the tool boundary.
        self.toolset = toolset.bind(services or {}, ledger=ledger, authorization=authorization) if isinstance(toolset, ToolSet) and services else toolset
        self.history, self.history_loader, self.event_store = history, history_loader, event_store
        self.context_inspector = context_inspector or trace_inspector
        self.project_id = project_id
        self.trace_metadata_only = bool(trace_metadata_only)
        self.stream_sink = stream_sink
        self.checkpoint_loader = checkpoint_loader
        self.checkpoint_sink = checkpoint_sink
        self._llm_calls: dict[str, object] = {}
        self._transport_attempts = 0
        self.max_requests, self.max_tool_calls = max(1, int(max_requests)), max(1, int(max_tool_calls))
        self.timeout_seconds, self.max_runtime_seconds, self.max_retries, self.response_schema, self.stream = max(0.001, float(timeout_seconds)), max(0.001, float(max_runtime_seconds)), min(5, max(0, int(max_retries))), response_schema, stream
        self.no_progress_limit = max(2, min(5, int(no_progress_limit)))

    def run(self, *, run_id: str, attempt: int = 1, ctx: object | None = None, messages: Iterable[Mapping[str, Any]] | None = None, **kwargs: Any) -> Any:
        """Return the production async runtime coroutine.

        Callers that own an event loop must await this method (or `run_async`).
        The legacy provider/tool loop is deliberately available only through
        `legacy_run_for_tests` and is never selected as a production fallback.
        """
        return self.run_async(run_id=run_id, attempt=attempt, ctx=ctx, messages=messages, **kwargs)

    async def run_async(
        self,
        *,
        run_id: str,
        attempt: int = 1,
        ctx: object | None = None,
        messages: Iterable[Mapping[str, Any]] | None = None,
    ) -> RuntimeResult:
        """Run the native async agent and keep framework state behind this boundary."""
        method = getattr(self.provider, "run_agent_async", None)
        if not callable(method):
            return RuntimeResult(
                "FAILED",
                error={"code": "DEPENDENCY_UNAVAILABLE", "message": "助手原生运行适配器未配置", "retryable": False},
            )

        from .adapter import ProviderRequestBudget

        loaded = self._load_history(run_id, attempt=attempt, supplied=messages)
        safe_messages = [{"role": "system", "content": _ASSISTANT_SYSTEM_PROMPT}]
        safe_messages.extend(self._message(item) for item in loaded)
        schemas = self._schemas()
        started = time.monotonic()
        deadline = started + min(600.0, self.max_runtime_seconds)
        request_budget = ProviderRequestBudget(deadline_monotonic=deadline, max_requests=36)
        self._transport_attempts = 0
        request_count = 0
        tool_count = 0
        usage: dict[str, Any] = {}
        message_state: dict[str, Any] | None = None
        text_parts: list[str] = []
        text_buffer: list[str] = []
        text_buffer_length = 0
        flush_task: asyncio.Task[None] | None = None
        flush_lock = asyncio.Lock()
        tool_rows: dict[str, dict[str, Any]] = {}
        tool_results: list[Any] = []
        pending_actions: list[Mapping[str, Any]] = []
        llm_calls_by_request: dict[tuple[int, int], str] = {}
        llm_started_by_request: dict[tuple[int, int], float] = {}
        latest_native_call_id: str | None = None

        async def call_sink(op: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
            callback = self.stream_sink
            if callback is None:
                raise AssistantError(503, "STREAM_PERSISTENCE_UNAVAILABLE", "助手流式持久化端口未配置", retryable=True)
            if not callable(callback):
                callback = getattr(callback, "stream_sink", None)
            if not callable(callback):
                raise AssistantError(503, "STREAM_PERSISTENCE_UNAVAILABLE", "助手流式持久化端口未配置", retryable=True)
            try:
                value = callback(ctx, op, dict(payload))
                if inspect.isawaitable(value):
                    value = await value
            except AssistantError:
                raise
            except Exception as exc:
                raise AssistantError(503, "STALE_FENCING_TOKEN", "助手运行租约已失效", retryable=True) from exc
            if value is False:
                raise AssistantError(409, "STALE_FENCING_TOKEN", "助手运行租约已失效", retryable=True)
            if not isinstance(value, Mapping):
                raise AssistantError(503, "STREAM_PERSISTENCE_FAILED", "助手消息持久化失败", retryable=True)
            return value

        async def ensure_message() -> dict[str, Any]:
            nonlocal message_state
            if message_state is None:
                value = await call_sink("ensure", {})
                if not isinstance(value.get("message_id"), str) or type(value.get("block_index")) is not int or type(value.get("offset")) is not int or not isinstance(value.get("text"), str) or value["offset"] != len(value["text"]):
                    raise AssistantError(503, "STREAM_PERSISTENCE_FAILED", "助手消息持久化返回无效状态", retryable=True)
                message_state = dict(value)
                text_parts[:] = [str(value["text"])] if value["text"] else []
            return message_state

        async def flush_text() -> None:
            nonlocal text_buffer_length, message_state
            async with flush_lock:
                if not text_buffer:
                    return
                state = await ensure_message()
                delta = "".join(text_buffer)
                text_buffer.clear()
                text_buffer_length = 0
                offset = int(state["offset"])
                value = await call_sink("append", {
                    "message_id": state["message_id"],
                    "block_index": state["block_index"],
                    "delta": delta,
                    "offset": offset,
                })
                current_text = str(state["text"]) + delta
                if value.get("message_id", state["message_id"]) != state["message_id"] or value.get("block_index", state["block_index"]) != state["block_index"]:
                    raise AssistantError(503, "STREAM_PERSISTENCE_FAILED", "助手消息标识状态无效", retryable=True)
                # append atomically returns the persisted text metadata. If a
                # sink omits a field, derive only from its validated prior text.
                state.update({
                    "message_id": value.get("message_id", state["message_id"]),
                    "block_index": value.get("block_index", state["block_index"]),
                    "offset": value.get("offset", len(current_text)),
                    "text": value.get("text", current_text),
                })
                if int(state["offset"]) != len(str(state["text"])):
                    raise AssistantError(503, "STREAM_PERSISTENCE_FAILED", "助手消息偏移状态无效", retryable=True)
                text_parts[:] = [str(state["text"])] if state["text"] else []

        async def flush_after_interval() -> None:
            try:
                await asyncio.sleep(0.1)
                await flush_text()
            except asyncio.CancelledError:
                raise

        def schedule_flush() -> None:
            nonlocal flush_task
            if flush_task is not None and flush_task.done():
                failure = flush_task.exception()
                if failure is not None:
                    raise failure
            if flush_task is None or flush_task.done():
                flush_task = asyncio.create_task(flush_after_interval())

        async def flush_before_sync_io() -> None:
            """Release any async stream transaction before sync persistence runs."""
            await flush_text()
            task = flush_task
            if task is not None and task.done():
                failure = task.exception()
                if failure is not None:
                    raise failure

        async def stop_flush_task(*, suppress_errors: bool) -> None:
            task = flush_task
            if task is None:
                return
            if not task.done():
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                if not suppress_errors:
                    raise

        async def add_text_chunk(chunk: Any) -> None:
            nonlocal text_buffer_length
            if not isinstance(chunk, str) or not chunk:
                return
            await ensure_message()
            text_buffer.append(chunk)
            text_buffer_length += len(chunk)
            schedule_flush()
            if text_buffer_length >= 256:
                await flush_text()

        async def on_event(event: Mapping[str, Any]) -> None:
            nonlocal request_count, tool_count
            nonlocal latest_native_call_id
            if not isinstance(event, Mapping):
                raise AssistantError(502, "PROVIDER_RESPONSE_INVALID", "模型服务响应格式无效", retryable=False)
            event_type = event.get("type")
            if event_type == "text_start":
                await add_text_chunk(event.get("text"))
                return
            if event_type == "text_delta":
                await add_text_chunk(event.get("text"))
                return
            if event_type == "text_end":
                await flush_text()
                return
            if event_type == "replacement" or event.get("replace") is True:
                replacement = event.get("text")
                if not isinstance(replacement, str):
                    raise AssistantError(502, "PROVIDER_RESPONSE_INVALID", "模型服务响应格式无效", retryable=False)
                await flush_text()
                state = await ensure_message()
                value = await call_sink("replace", {
                    "message_id": state["message_id"],
                    "block_index": state["block_index"],
                    "text": replacement,
                })
                state.update({
                    "message_id": value.get("message_id", state["message_id"]),
                    "block_index": value.get("block_index", state["block_index"]),
                    "offset": value.get("offset", len(replacement)),
                    "text": value.get("text", replacement),
                })
                if int(state["offset"]) != len(str(state["text"])):
                    raise AssistantError(503, "STREAM_PERSISTENCE_FAILED", "助手替换快照状态无效", retryable=True)
                text_parts[:] = [str(state["text"])] if state["text"] else []
                return
            if event_type == "model_request":
                await flush_before_sync_io()
                request_index = _int_or_none(event.get("request_index")) or request_count + 1
                if request_index > self.max_requests:
                    raise AssistantError(429, "RESOURCE_LIMIT_EXCEEDED", "助手模型请求次数超出限制", retryable=False)
                request_count = max(request_count, request_index)
                self._transport_attempts += 1
                transport_attempt = max(1, _int_or_none(event.get("transport_attempt")) or 1)
                call_key = (request_index, transport_attempt)
                llm_call_id = self._capture_native_llm_context(
                    run_id, attempt, request_index, event,
                    project_id=(self.project_id or getattr(ctx, "project_id", None) or getattr(ctx, "server_project_id", None)),
                    agent_attempt_id=getattr(ctx, "agent_attempt_id", None),
                    request_budget=request_budget,
                )
                llm_calls_by_request[call_key] = llm_call_id
                latest_native_call_id = llm_call_id
                llm_started_by_request[call_key] = time.monotonic()
                self._event(run_id, "llm.started", {"llm_call_id": llm_call_id, "request_index": request_index, "attempt": transport_attempt})
                return
            if event_type == "model_response":
                await flush_before_sync_io()
                request_index = _int_or_none(event.get("request_index")) or request_count
                transport_attempt = max(1, _int_or_none(event.get("transport_attempt")) or 1)
                call_key = (request_index, transport_attempt)
                llm_call_id = llm_calls_by_request.get(call_key)
                raw_usage = event.get("usage") if isinstance(event.get("usage"), Mapping) else {}
                usage.update(_usage(raw_usage))
                if llm_call_id:
                    failed = bool(event.get("error")) or str(event.get("status", "SUCCEEDED")).upper() in {"FAILED", "ERROR"}
                    error = _native_error(event.get("error")) if failed else None
                    error_diagnostic = _native_error_diagnostic(event.get("error")) if failed else None
                    response_end_kind = event.get("response_end_kind")
                    receive_complete_at = (
                        _canonical_utc_timestamp(event.get("receive_complete_at"))
                        if response_end_kind == "EOF" else None
                    )
                    response_view = {"text": None, "tool_calls": [], "usage": dict(raw_usage), "finish_reason": event.get("finish_reason")}
                    self._finish_llm_call(
                        llm_call_id,
                        status="FAILED" if failed else "SUCCEEDED",
                        usage=raw_usage,
                        latency_ms=_elapsed_ms(llm_started_by_request.get(call_key, time.monotonic())),
                        response=response_view if not failed else None,
                        error=error,
                        error_diagnostic=error_diagnostic,
                        metadata_only_response=True,
                        request_budget=request_budget,
                        provider_response_receive_complete_at=receive_complete_at,
                        provider_response_end_kind=response_end_kind,
                    )
                    self._event(run_id, "llm.failed" if failed else "llm.completed", {
                        "llm_call_id": llm_call_id,
                        "request_index": request_index,
                        "attempt": _int_or_none(event.get("transport_attempt")) or 1,
                        **({"error": error} if failed else {"usage": _usage(raw_usage)}),
                    })
                return
            if event_type == "tool_call":
                await flush_before_sync_io()
                provider_id = str(event.get("tool_call_id") or "")
                name, args = event.get("name"), event.get("arguments")
                if not provider_id or not isinstance(name, str) or not name or not isinstance(args, Mapping):
                    raise AssistantError(400, "VALIDATION_FAILED", "模型返回的工具调用无效", retryable=False)
                if provider_id in tool_rows:
                    raise AssistantError(409, "RECOVERY_STATE_INVALID", "模型返回了重复的工具调用标识", retryable=False)
                tool_count += 1
                if tool_count > self.max_tool_calls:
                    raise AssistantError(429, "RESOURCE_LIMIT_EXCEEDED", "助手工具调用次数超出限制", retryable=False)
                call_id = _namespaced_tool_call_id(run_id, attempt, ctx, provider_id)
                call = self._new_tool_call(run_id, attempt, call_id, name, args, ctx).model_copy(
                    update={"provider_tool_call_id": provider_id}
                )
                self._record_tool_transition(call, "tool.proposed", {"tool_call_id": call.id, "tool_name": name, "status": "PROPOSED"}, ctx=ctx)
                running = call.model_copy(update={"status": "RUNNING", "started_at": _now()})
                self._record_tool_transition(running, "tool.started", {"tool_call_id": call.id, "tool_name": name, "status": "RUNNING"}, ctx=ctx)
                tool_rows[provider_id] = {"call": running, "started_at": running.started_at, "name": name, "arguments": dict(args), "finished": False}
                return
            if event_type == "error":
                return
            # Adapter events that do not cross a public persistence boundary
            # are intentionally ignored; framework objects never escape.

        async def validate_tool(name: str, arguments: dict[str, Any], provider_tool_call_id: str) -> dict[str, Any]:
            await flush_before_sync_io()
            row = tool_rows.get(provider_tool_call_id)
            if row is None or row["finished"] or row["name"] != name:
                raise AssistantError(409, "RECOVERY_STATE_INVALID", "模型工具调用关联无效", retryable=False)
            if not isinstance(arguments, Mapping) or dict(arguments) != row["arguments"]:
                raise AssistantError(400, "VALIDATION_FAILED", "模型工具参数关联无效", retryable=False)
            tool = self.toolset.get(name) if isinstance(self.toolset, Mapping) else None
            if tool is None or not callable(tool):
                raise AssistantError(403, "PERMISSION_DENIED", "模型请求的工具不在允许列表中", retryable=False)
            try:
                normalized = _normalize_tool_arguments(arguments)
                spec = getattr(tool, "spec", None)
                _validate_tool_schema(normalized, getattr(spec, "argument_schema", {}) or {})
                if isinstance(tool, BoundTool):
                    tool._authorize(_tool_call_context(ctx, run_id=run_id, attempt=attempt, tool_call_id=row["call"].id))
                return normalized
            except Exception as exc:
                if not row["finished"]:
                    failure = self._tool_failure(row["call"], exc, row["started_at"])
                    self._record_tool_transition_best_effort(failure, "tool.failed", {"tool_call_id": row["call"].id, "tool_name": name, "status": "FAILED", "error": failure.error}, ctx=ctx)
                    row["finished"] = True
                raise

        async def execute_tool(name: str, arguments: dict[str, Any], provider_tool_call_id: str) -> Any:
            await flush_before_sync_io()
            row = tool_rows.get(provider_tool_call_id)
            if row is None or row["finished"] or row["name"] != name:
                raise AssistantError(409, "RECOVERY_STATE_INVALID", "模型工具调用关联无效", retryable=False)
            if pending_actions:
                raise AssistantError(409, "CONFIRMATION_STATE_INVALID", "存在待确认工具，不能继续执行其他工具", retryable=False)
            call = row["call"]
            try:
                result = self._execute_tool(name, arguments, ctx=ctx, run_id=run_id, attempt=attempt, tool_call_id=call.id)
                if isinstance(result, ToolPending):
                    confirmation_id = getattr(ctx, "confirmation_id", None) or f"confirm_{uuid4().hex}"
                    binding = {
                        "tool_call_id": call.id,
                        "provider_tool_call_id": provider_tool_call_id,
                        "tool_name": name,
                        "confirmation_id": confirmation_id,
                        "resource_ref": _resource_ref_value(result.resource_ref),
                        "action": result.action,
                        "revision": result.revision or 1,
                        "config_hash": result.config_hash or result.target_revision_hash,
                        "target_revision_hash": result.target_revision_hash,
                        "schema_version": self._tool_schema(name),
                        "normalized_args": dict(result.normalized_args),
                    }
                    public_binding = _pending_confirmation_dto(binding)
                    # Keep the exact confirmation hashes used by AssistantService
                    # so its transactional resume validation can consume this
                    # native proposal without widening the public DTO.
                    from .service import _binding_hash, _hash
                    pending = call.model_copy(update={
                        "status": "WAITING_CONFIRMATION",
                        "confirmation_id": confirmation_id,
                        "pending_confirmation": public_binding,
                        "arguments_hash": _hash(public_binding.normalized_args),
                        "binding_hash": _binding_hash(public_binding),
                        "finished_at": _now(),
                    })
                    self._record_tool_transition(pending, "confirmation.required", {
                        "tool_call_id": call.id,
                        "confirmation_id": confirmation_id,
                        "resource_ref": binding["resource_ref"],
                        "action": result.action,
                        "revision": binding["revision"],
                        "config_hash": binding["config_hash"],
                        "status": "WAITING_CONFIRMATION",
                    }, ctx=ctx)
                    pending_actions.append(binding)
                    tool_results.append(result)
                    row["finished"] = True
                    return {"status": "WAITING_CONFIRMATION", "tool_call_id": provider_tool_call_id, "pending_action": _private_json_snapshot(binding)}
                value = result.result if isinstance(result, ToolExecution) else result
                if name in {"create_prediction", "get_prediction"}:
                    call = call.model_copy(update={
                        "job_id": _result_job_id(value),
                        "resource_ref": _resource_ref_value(_result_resource_ref(value)),
                    })
                    row["call"] = call
                    self._save_tool_call(call, ctx=ctx)
                value = await self._wait_for_prediction(
                    name, value, arguments=arguments, ctx=ctx, run_id=run_id,
                    attempt=attempt, tool_call_id=call.id, deadline=deadline - self.timeout_seconds,
                )
                public = _public_value(value)
                done = call.model_copy(update={
                    "status": "SUCCEEDED",
                    "result_summary": _tool_result_summary(value),
                    "resource_ref": _resource_ref_value(_result_resource_ref(value)),
                    "job_id": _result_job_id(value),
                    "finished_at": _now(),
                })
                self._record_tool_transition(done, "tool.completed", {"tool_call_id": call.id, "tool_name": name, "status": "SUCCEEDED"}, ctx=ctx)
                row["finished"] = True
                tool_results.append(public)
                return _summary(_model_tool_result(name, public))
            except Exception as exc:
                if not row["finished"]:
                    failure = self._tool_failure(call, exc, row["started_at"])
                    self._record_tool_transition_best_effort(failure, "tool.failed", {"tool_call_id": call.id, "tool_name": name, "status": "FAILED", "error": failure.error}, ctx=ctx)
                    row["finished"] = True
                raise

        async def persist_checkpoint(
            *,
            native_history_state: Mapping[str, Any],
            state_usage: Mapping[str, Any] | None,
            pending: list[Mapping[str, Any]],
        ) -> dict[str, Any]:
            state = {
                "engine": "pydantic-ai",
                "framework_version": str(native_history_state.get("framework_version") or "2.56.0"),
                "serialization_version": str(native_history_state.get("serialization_version") or "ModelMessagesTypeAdapter"),
                "history_state": _private_json_snapshot(native_history_state),
                "usage": _usage(state_usage or {}),
                "pending_actions": _private_json_snapshot(pending),
                "config_hash": self._provider_configuration_hash(),
                "stream_message_id": message_state.get("message_id") if message_state else None,
            }
            callback = self.checkpoint_sink
            if callback is None:
                raise AssistantError(503, "CHECKPOINT_PERSISTENCE_UNAVAILABLE", "助手私有历史持久化端口未配置", retryable=True)
            if not callable(callback):
                callback = getattr(callback, "checkpoint_sink", None)
            if not callable(callback):
                raise AssistantError(503, "CHECKPOINT_PERSISTENCE_UNAVAILABLE", "助手私有历史持久化端口未配置", retryable=True)
            try:
                value = callback(ctx, state)
                if inspect.isawaitable(value):
                    value = await value
            except Exception as exc:
                raise AssistantError(409, "STALE_FENCING_TOKEN", "助手运行租约已失效", retryable=True) from exc
            if value is False:
                raise AssistantError(409, "STALE_FENCING_TOKEN", "助手运行租约已失效", retryable=True)
            return state

        def fail_open_tool_calls(error: Mapping[str, Any]) -> None:
            failure_exc = AssistantError(
                409,
                str(error.get("code") or "PROVIDER_ERROR")[:96],
                str(error.get("message") or "模型服务调用失败")[:2000],
                retryable=bool(error.get("retryable", False)),
            )
            for row in tool_rows.values():
                if row.get("finished"):
                    continue
                failed = self._tool_failure(row["call"], failure_exc, row["started_at"])
                self._record_tool_transition_best_effort(failed, "tool.failed", {
                    "tool_call_id": row["call"].id,
                    "tool_name": row["name"],
                    "status": "FAILED",
                    "error": failed.error,
                }, ctx=ctx)
                row["finished"] = True

        try:
            history_state = await self._load_native_checkpoint(ctx)
            self._validate_native_checkpoint(history_state)
            if isinstance(history_state, Mapping) and history_state.get("fresh_run") is not True:
                saved_pending = history_state.get("pending_actions")
                if saved_pending:
                    if not isinstance(saved_pending, (list, tuple)) or len(saved_pending) != 1 or not isinstance(saved_pending[0], Mapping):
                        raise AssistantError(409, "CONFIRMATION_STATE_INVALID", "助手待确认状态无效", retryable=False)
                    # Confirmation is executed exactly once by the platform's
                    # confirmation service. A queued AgentRuntime invocation
                    # must never run the tool or resume the Agent again.
                    if getattr(ctx, "confirmation_id", None):
                        raise AssistantError(409, "CONFIRMATION_STATE_INVALID", "确认操作必须由平台确认服务执行", retryable=False)
                    return RuntimeResult(
                        "WAITING_CONFIRMATION",
                        usage=_usage(history_state.get("usage", {})) if isinstance(history_state.get("usage"), Mapping) else {},
                        pending_actions=tuple(_public_pending_action(item) for item in saved_pending),
                    )
                if getattr(ctx, "confirmation_id", None):
                    raise AssistantError(409, "CONFIRMATION_STATE_INVALID", "确认操作与助手可信待确认状态不匹配", retryable=False)
            remaining = max(0.001, deadline - time.monotonic())
            result = await asyncio.wait_for(
                method(
                    config=self.config,
                    messages=safe_messages,
                    tools=schemas,
                    execute_tool=execute_tool,
                    validate_tool=validate_tool,
                    on_event=on_event,
                    history_state=history_state,
                    request_budget=request_budget,
                    timeout_seconds=self.timeout_seconds,
                    max_turns=self.max_requests,
                    max_retries=self.max_retries,
                ),
                timeout=remaining,
            )
            await flush_text()
            if not isinstance(result, Mapping):
                raise AssistantError(502, "PROVIDER_RESPONSE_INVALID", "模型服务响应格式无效", retryable=False)
            result_usage = _usage(result.get("usage") if isinstance(result.get("usage"), Mapping) else usage)
            adapter_pending = result.get("pending_actions")
            if adapter_pending is not None and not isinstance(adapter_pending, (list, tuple)):
                raise AssistantError(502, "PROVIDER_RESPONSE_INVALID", "模型确认状态无效", retryable=False)
            if adapter_pending is not None and len(adapter_pending) > 1:
                raise AssistantError(409, "CONFIRMATION_STATE_INVALID", "同时存在多个待确认工具", retryable=False)
            # The platform callback is the sole source of confirmation bindings.
            # Never persist provider-echoed pending metadata as authoritative.
            if bool(result.get("pending")) != bool(pending_actions) and result.get("pending") is not None:
                raise AssistantError(409, "CONFIRMATION_STATE_INVALID", "模型确认状态与平台账本不匹配", retryable=False)
            if adapter_pending and pending_actions:
                adapter_id = adapter_pending[0].get("tool_call_id") if isinstance(adapter_pending[0], Mapping) else None
                if adapter_id not in {pending_actions[0].get("provider_tool_call_id"), pending_actions[0].get("tool_call_id")}:
                    raise AssistantError(409, "CONFIRMATION_STATE_INVALID", "模型确认状态与平台账本不匹配", retryable=False)
            raw_pending = pending_actions
            if len(raw_pending) > 1:
                raise AssistantError(409, "CONFIRMATION_STATE_INVALID", "同时存在多个待确认工具", retryable=False)
            finish_reason = result.get("finish_reason")
            if _is_truncated_finish_reason(finish_reason):
                raise AssistantError(502, "PROVIDER_OUTPUT_TRUNCATED", "模型服务输出达到长度上限，结果未提交", retryable=False)
            native_history_state = result.get("history_state")
            if not isinstance(native_history_state, Mapping):
                raise AssistantError(502, "PROVIDER_RESPONSE_INVALID", "原生助手历史状态无效", retryable=False)
            final_text = result.get("text")
            if isinstance(final_text, str):
                current_text = str(message_state.get("text", "")) if message_state else ""
                if current_text != final_text:
                    state = await ensure_message()
                    if final_text.startswith(current_text):
                        suffix = final_text[len(current_text):]
                        if suffix:
                            value = await call_sink("append", {
                                "message_id": state["message_id"],
                                "block_index": state["block_index"],
                                "delta": suffix,
                                "offset": len(current_text),
                            })
                            state.update({
                                "message_id": value.get("message_id", state["message_id"]),
                                "block_index": value.get("block_index", state["block_index"]),
                                "offset": value.get("offset", len(final_text)),
                                "text": value.get("text", final_text),
                            })
                    else:
                        value = await call_sink("replace", {
                            "message_id": state["message_id"],
                            "block_index": state["block_index"],
                            "text": final_text,
                        })
                        state.update({
                            "message_id": value.get("message_id", state["message_id"]),
                            "block_index": value.get("block_index", state["block_index"]),
                            "offset": value.get("offset", len(final_text)),
                            "text": value.get("text", final_text),
                        })
                    if int(state["offset"]) != len(str(state["text"])):
                        raise AssistantError(503, "STREAM_PERSISTENCE_FAILED", "助手最终消息快照状态无效", retryable=True)
                    text_parts[:] = [str(state["text"])] if state["text"] else []
            await persist_checkpoint(native_history_state=native_history_state, state_usage=result_usage, pending=list(raw_pending))
            text = result.get("text")
            if text is None and text_parts:
                text = text_parts[-1]
            status = "WAITING_CONFIRMATION" if raw_pending else "SUCCEEDED"
            return RuntimeResult(
                status,
                _public_text(text),
                _public_value(result.get("structured")),
                tuple(_public_value(item) for item in tool_results),
                result_usage,
                _int_or_none(result.get("provider_requests")) or request_count,
                _int_or_none(result.get("tool_calls")) or tool_count,
                self._transport_attempts,
                pending_actions=tuple(_public_pending_action(item) for item in raw_pending),
            )
        except asyncio.TimeoutError:
            request_budget.seal()
            error = {"code": "PROVIDER_TIMEOUT", "message": "模型服务调用超时", "retryable": True}
            await stop_flush_task(suppress_errors=True)
            fail_open_tool_calls(error)
            self._fail_native_llm_calls(llm_calls_by_request.values(), error, diagnostic=_native_error_diagnostic(asyncio.TimeoutError()), diagnostic_call_id=latest_native_call_id)
            return RuntimeResult("FAILED", usage=usage, error=error, provider_requests=request_count, tool_calls=tool_count, transport_attempts=self._transport_attempts)
        except AssistantError as exc:
            error = {"code": exc.payload.code, "message": exc.payload.message, "retryable": exc.payload.retryable}
            await stop_flush_task(suppress_errors=True)
            fail_open_tool_calls(error)
            self._fail_native_llm_calls(llm_calls_by_request.values(), error, diagnostic=_native_error_diagnostic(exc), diagnostic_call_id=latest_native_call_id)
            return RuntimeResult("FAILED", usage=usage, error=error, provider_requests=request_count, tool_calls=tool_count, transport_attempts=self._transport_attempts)
        except Exception as exc:
            error = {"code": "PROVIDER_ERROR", "message": "模型服务调用失败", "retryable": True}
            await stop_flush_task(suppress_errors=True)
            fail_open_tool_calls(error)
            self._fail_native_llm_calls(llm_calls_by_request.values(), error, diagnostic=_native_error_diagnostic(exc), diagnostic_call_id=latest_native_call_id)
            return RuntimeResult("FAILED", usage=usage, error=error, provider_requests=request_count, tool_calls=tool_count, transport_attempts=self._transport_attempts)
        finally:
            await stop_flush_task(suppress_errors=True)
            request_budget.seal()

    async def _load_native_checkpoint(self, ctx: object | None) -> Mapping[str, Any] | None:
        callback = self.checkpoint_loader
        if callback is None:
            return None
        if not callable(callback):
            callback = getattr(callback, "checkpoint_loader", None)
        if not callable(callback):
            raise unavailable("助手私有历史读取端口未配置")
        try:
            value = callback(ctx)
            if inspect.isawaitable(value):
                value = await value
        except AssistantError:
            raise
        except Exception as exc:
            raise AssistantError(409, "STALE_FENCING_TOKEN", "助手运行租约已失效", retryable=True) from exc
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise AssistantError(409, "RECOVERY_STATE_INVALID", "助手可信私有历史无效", retryable=False)
        return dict(value)

    def _validate_native_checkpoint(self, state: Mapping[str, Any] | None) -> None:
        if state is None:
            return
        expected_hash = self._provider_configuration_hash()
        if state.get("fresh_run") is True:
            if state.get("config_hash") != expected_hash:
                raise AssistantError(409, "RECOVERY_STATE_INVALID", "助手配置已变更，请重新开始运行", retryable=False)
            prior = state.get("prior_history_state")
            if prior is not None and not isinstance(prior, Mapping):
                raise AssistantError(409, "RECOVERY_STATE_INVALID", "助手可信历史无效", retryable=False)
            return
        if (
            state.get("engine") != "pydantic-ai"
            or state.get("config_hash") != expected_hash
            or not isinstance(state.get("framework_version"), str)
            or not isinstance(state.get("serialization_version"), str)
            or not isinstance(state.get("history_state"), Mapping)
        ):
            raise AssistantError(409, "RECOVERY_STATE_INVALID", "原生助手历史与运行配置不匹配", retryable=False)

    def _provider_configuration_hash(self) -> str:
        value = getattr(self.config, "provider_configuration_hash", None)
        if isinstance(value, str) and value:
            return value
        raise AssistantError(409, "RECOVERY_STATE_INVALID", "LLM 配置缺少私有历史绑定", retryable=False)

    def _capture_native_llm_context(
        self,
        run_id: str,
        attempt: int,
        request_index: int,
        event: Mapping[str, Any],
        *,
        project_id: str | None,
        agent_attempt_id: str | None,
        request_budget: object,
    ) -> str:
        call_id = f"llm_{uuid4().hex}"
        inspector = self.context_inspector
        if inspector is None:
            return call_id
        from .trace import LlmCallDetail, Usage

        tool_names = event.get("tool_names") if isinstance(event.get("tool_names"), (list, tuple)) else []
        metadata = {
            "metadata_only": True,
            "request_index": request_index,
            "model_id": str(event.get("model_id") or self.config.model_id),
            "message_count": max(0, _int_or_none(event.get("message_count")) or 0),
            "tool_names": [str(name)[:100] for name in tool_names[:100]],
            "request_content_sha256": str(event.get("request_content_sha256") or event.get("request_sha256") or "")[:64],
        }
        call = LlmCallDetail(
            id=call_id,
            agent_run_id=run_id,
            agent_attempt_id=agent_attempt_id or f"attempt_{attempt}",
            request_index=request_index,
            transport_attempt=max(1, _int_or_none(event.get("transport_attempt")) or 1),
            model_id=self.config.model_id,
            project_id=project_id,
            status="RUNNING",
            provider_configuration_id=getattr(self.config, "provider_configuration_id", None),
            provider_configuration_revision=self.config.config_revision,
            usage=Usage(),
            model_parameters_summary={
                "max_output_tokens": DEFAULT_MAX_OUTPUT_TOKENS,
                "stream": True,
                "provider_budget": self._provider_budget_summary(request_budget, transport_state="ATTEMPT_NOT_STARTED"),
            },
        )
        capture_pair = getattr(inspector, "capture_pair", None)
        if not callable(capture_pair):
            raise unavailable("Context trace capture 端口未配置")
        try:
            logical, provider = capture_pair(
                llm_call=call,
                logical_context=metadata,
                provider_request=metadata,
                project_id=project_id,
            )
        except AssistantError as exc:
            self._save_failed_trace_call(call, exc)
            raise
        except Exception as exc:
            failure = AssistantError(503, "TRACE_PERSISTENCE_FAILED", "Context 快照保存失败", retryable=True)
            self._save_failed_trace_call(call, failure)
            raise failure from exc
        self._llm_calls[call_id] = replace(call, logical_context_snapshot_id=logical.id, provider_request_snapshot_id=provider.id)
        return call_id

    def _fail_native_llm_calls(self, call_ids: Iterable[str], error: Mapping[str, Any], *, diagnostic: Mapping[str, str] | None = None, diagnostic_call_id: str | None = None) -> None:
        for call_id in call_ids:
            value = self._llm_calls.get(call_id)
            if value is None:
                continue
            try:
                if call_id == diagnostic_call_id and diagnostic is not None and getattr(value, "status", None) in {"RUNNING", "FAILED"}:
                    self._record_native_error_diagnostic(call_id, error, diagnostic)
                elif getattr(value, "status", None) == "RUNNING":
                    self._finish_llm_call(call_id, status="FAILED", error=error)
            except Exception:
                continue

    def _record_native_error_diagnostic(self, call_id: str, error: Mapping[str, Any], diagnostic: Mapping[str, str]) -> None:
        from .trace import LlmCallDetail

        call = self._llm_calls.get(call_id)
        if not isinstance(call, LlmCallDetail) or self.context_inspector is None:
            return
        parameters = dict(call.model_parameters_summary or {})
        existing_diagnostic = parameters.get("provider_error_diagnostic")
        if (
            not isinstance(existing_diagnostic, Mapping)
            or (existing_diagnostic.get("exception_class") == "Unknown" and diagnostic.get("exception_class") != "Unknown")
        ):
            updated_diagnostic = dict(diagnostic)
            if isinstance(existing_diagnostic, Mapping) and existing_diagnostic.get("reason_category") in _NATIVE_TRANSPORT_CATEGORIES:
                # A reported response category survives a later cleanup exception.
                updated_diagnostic["reason_category"] = existing_diagnostic["reason_category"]
            parameters["provider_error_diagnostic"] = updated_diagnostic
        failed = replace(
            call,
            status="FAILED",
            model_parameters_summary=parameters,
            error=call.error if call.status == "FAILED" else dict(error),
            finished_at=call.finished_at if call.status == "FAILED" else _now(),
        )
        self._llm_calls[call_id] = failed
        saver = getattr(self.context_inspector, "save_llm_call", None)
        if callable(saver):
            saver(failed)

    async def complete_deferred_history(
        self,
        *,
        ctx: object,
        tool_call_id: str,
        trusted_result: Any,
    ) -> Mapping[str, Any]:
        """Append the server-confirmed result to native history without model execution."""
        state = await self._load_native_checkpoint(ctx)
        self._validate_native_checkpoint(state)
        if state is None or state.get("fresh_run") is True:
            raise AssistantError(409, "RECOVERY_STATE_INVALID", "待确认的原生助手历史不存在", retryable=False)
        pending = state.get("pending_actions")
        matching = [
            item for item in pending
            if isinstance(item, Mapping)
            and tool_call_id in {item.get("tool_call_id"), item.get("provider_tool_call_id")}
        ] if isinstance(pending, (list, tuple)) else []
        if len(matching) != 1:
            raise AssistantError(409, "CONFIRMATION_STATE_INVALID", "待确认工具与原生历史不匹配", retryable=False)
        method = getattr(self.provider, "complete_deferred_history", None)
        if not callable(method):
            raise unavailable("助手原生确认历史适配器未配置")
        raw_history = state.get("history_state")
        adapter_tool_call_id = str(matching[0].get("provider_tool_call_id") or tool_call_id)
        updated_history = method(history_state=raw_history, tool_call_id=adapter_tool_call_id, trusted_result=trusted_result)
        if inspect.isawaitable(updated_history):
            updated_history = await updated_history
        if not isinstance(updated_history, Mapping):
            raise AssistantError(409, "RECOVERY_STATE_INVALID", "确认后的原生助手历史无效", retryable=False)
        remaining = [item for item in pending if not isinstance(item, Mapping) or item.get("tool_call_id") != tool_call_id]
        updated = dict(state)
        updated.update({"history_state": _private_json_snapshot(updated_history), "pending_actions": _private_json_snapshot(remaining)})
        callback = self.checkpoint_sink
        if not callable(callback):
            callback = getattr(callback, "checkpoint_sink", None)
        if not callable(callback):
            raise unavailable("助手私有历史写入端口未配置")
        try:
            value = callback(ctx, updated)
            if inspect.isawaitable(value):
                value = await value
        except AssistantError:
            raise
        except Exception as exc:
            raise AssistantError(409, "STALE_FENCING_TOKEN", "助手运行租约已失效", retryable=True) from exc
        if value is False:
            raise AssistantError(409, "STALE_FENCING_TOKEN", "助手运行租约已失效", retryable=True)
        return updated

    def legacy_run_for_tests(self, *, run_id: str, attempt: int = 1, ctx: object | None = None, messages: Iterable[Mapping[str, Any]] | None = None) -> RuntimeResult:
        """Explicit synchronous compatibility entry point for legacy unit tests only."""
        from .adapter import ProviderRequestBudget

        loaded = self._load_history(run_id, attempt=attempt, supplied=messages)
        safe_messages = [{"role": "system", "content": _ASSISTANT_SYSTEM_PROMPT}]
        safe_messages.extend(self._message(x) for x in loaded)
        tools = self._schemas()
        request_count = 0
        tool_count = 0
        started_monotonic = time.monotonic()
        run_deadline = started_monotonic + min(600.0, self.max_runtime_seconds)
        request_budget = ProviderRequestBudget(
            deadline_monotonic=run_deadline,
            max_requests=36,
        )
        self._transport_attempts = 0
        tool_results: list[Any] = []
        last_response: Any = None
        repeated_results: dict[tuple[str, str, str], int] = {}
        failed_usage: dict[str, Any] = {}
        try:
            while request_count < self.max_requests:
                if time.monotonic() >= run_deadline:
                    raise AssistantError(504, "RUN_TIMEOUT", "助手运行超过时间预算", retryable=True)
                request_count += 1
                # PriorityOperationContext binds the server-authorized project
                # as ``server_project_id``.  Keep request-scoped project_id
                # first, then use that binding for production async workers so
                # metadata-only trace capture has the same scope as the run.
                run_project_id = (
                    self.project_id
                    or getattr(ctx, "project_id", None)
                    or getattr(ctx, "server_project_id", None)
                )
                run_agent_attempt_id = getattr(ctx, "agent_attempt_id", None)
                try:
                    response = self._provider_call(safe_messages, tools, run_id=run_id, attempt=attempt, request_index=request_count, project_id=run_project_id, agent_attempt_id=run_agent_attempt_id, request_budget=request_budget)
                except AssistantError as exc:
                    raise
                except Exception:
                    raise
                last_response = response
                normalized = _response(response)
                if _is_truncated_finish_reason(normalized["finish_reason"]):
                    failed_usage = _usage(normalized["usage"])
                    raise AssistantError(502, "PROVIDER_OUTPUT_TRUNCATED", "模型服务输出达到长度上限，结果未提交", retryable=False)
                calls = normalized["tool_calls"]
                if not calls:
                    result = RuntimeResult("SUCCEEDED", _public_text(normalized["text"]), _public_value(normalized["structured"]), tuple(_public_value(item) for item in tool_results), _usage(normalized["usage"]), request_count, tool_count, self._transport_attempts)
                    # The execution store owns the terminal run event.  The
                    # runtime may be retried or resumed before that store
                    # commits; emitting a second terminal event here makes
                    # one attempt appear to finish twice.
                    return result
                for call in calls:
                    tool_count += 1
                    if tool_count > self.max_tool_calls:
                        raise AssistantError(429, "RESOURCE_LIMIT_EXCEEDED", "助手工具调用次数超出限制", retryable=False)
                    name, args, provider_id = _tool_call(call)
                    wire_provider_id = provider_id
                    if not provider_id:
                        # Some providers omit an id.  The ordinal makes two
                        # equal calls in one response independent; run/name/
                        # arguments make the fallback stable on a retry.
                        provider_id = f"{run_id}:{tool_count}:{name}:{_sha256_json(args)}"
                        wire_provider_id = provider_id
                    provider_id = _namespaced_tool_call_id(run_id, attempt, ctx, provider_id)
                    started_at = _now()
                    tool_call = self._new_tool_call(run_id, attempt, provider_id, name, args, ctx)
                    durable_started = False
                    try:
                        self._record_tool_transition(tool_call, "tool.proposed", {"tool_call_id": provider_id, "tool_name": name, "status": "PROPOSED"}, ctx=ctx)
                        durable_started = True
                        tool_call = tool_call.model_copy(update={"status": "RUNNING", "started_at": started_at})
                        self._record_tool_transition(tool_call, "tool.started", {"tool_call_id": provider_id, "tool_name": name, "status": "RUNNING"}, ctx=ctx)
                        result = self._execute_tool(name, args, ctx=ctx, run_id=run_id, attempt=attempt, tool_call_id=provider_id)
                    except Exception as exc:
                        if not durable_started:
                            raise
                        failure = self._tool_failure(tool_call, exc, started_at)
                        self._record_tool_transition_best_effort(failure, "tool.failed", {"tool_call_id": provider_id, "tool_name": name, "status": "FAILED", "error": failure.error}, ctx=ctx)
                        raise
                    if isinstance(result, ToolPending):
                        confirmation_id = getattr(ctx, "confirmation_id", None) or f"confirm_{uuid4().hex}"
                        pending_binding = {"tool_call_id": provider_id, "confirmation_id": confirmation_id, "resource_ref": _resource_ref_value(result.resource_ref), "action": result.action, "revision": result.revision or 1, "config_hash": result.config_hash or result.target_revision_hash, "schema_version": self._tool_schema(name), "normalized_args": dict(tool_call.arguments_summary)}
                        pending = tool_call.model_copy(update={"status": "WAITING_CONFIRMATION", "confirmation_id": confirmation_id, "pending_confirmation": pending_binding})
                        try:
                            self._record_tool_transition(pending, "confirmation.required", {"tool_call_id": provider_id, "confirmation_id": confirmation_id, "resource_ref": result.resource_ref, "action": result.action, "revision": result.revision or 1, "config_hash": result.config_hash or result.target_revision_hash, "status": "WAITING_CONFIRMATION"}, ctx=ctx)
                        except Exception as exc:
                            failure = self._tool_failure(tool_call, exc, started_at)
                            self._record_tool_transition_best_effort(failure, "tool.failed", {"tool_call_id": provider_id, "tool_name": name, "status": "FAILED", "error": failure.error}, ctx=ctx)
                            raise
                        return RuntimeResult("WAITING_CONFIRMATION", _public_text(normalized["text"]), _public_value(normalized["structured"]), tuple(_public_value(item) for item in tool_results + [result]), _usage(normalized["usage"]), request_count, tool_count, self._transport_attempts, pending_actions=(pending_binding,))
                    if isinstance(result, ToolExecution):
                        tool_value = result.result
                    else:
                        tool_value = result
                    progress_key = (name, _sha256_json(args), _sha256_json(tool_value))
                    # Polling a prediction that is still QUEUED/RUNNING is a
                    # legal operation. It is bounded by max_requests rather
                    # than treated as a no-progress terminal loop. A changed
                    # status changes the fingerprint and therefore counts as
                    # progress automatically.
                    pending_prediction = name == "get_prediction" and _prediction_is_pending(tool_value)
                    repeat_count = 0
                    if not pending_prediction:
                        repeated_results[progress_key] = repeated_results.get(progress_key, 0) + 1
                        repeat_count = repeated_results[progress_key]
                    tool_results.append(tool_value)
                    completed = tool_call.model_copy(update={"status": "SUCCEEDED", "result_summary": _tool_result_summary(tool_value), "resource_ref": _resource_ref_value(_result_resource_ref(tool_value)), "job_id": _result_job_id(tool_value), "finished_at": _now()})
                    try:
                        self._record_tool_transition(completed, "tool.completed", {"tool_call_id": provider_id, "tool_name": name, "status": "SUCCEEDED"}, ctx=ctx)
                    except Exception as exc:
                        failure = self._tool_failure(tool_call, exc, started_at)
                        self._record_tool_transition_best_effort(failure, "tool.failed", {"tool_call_id": provider_id, "tool_name": name, "status": "FAILED", "error": failure.error}, ctx=ctx)
                        raise
                    if not pending_prediction and repeated_results[progress_key] >= self.no_progress_limit:
                        raise AssistantError(429, "RESOURCE_LIMIT_EXCEEDED", "tool calls repeated without resource or status progress; run stopped", retryable=False)
                    safe_messages.extend((
                        # Keep the provider's wire id for protocol
                        # correlation; the durable ToolCall/provider id above
                        # remains opaque and attempt-scoped.
                        {"role": "assistant", "tool_call_id": wire_provider_id, "tool_name": name},
                        {"role": "tool", "tool_call_id": wire_provider_id, "content": _summary(tool_value)},
                    ))
                    if not pending_prediction and repeat_count == 2:
                        safe_messages.append({
                            "role": "system",
                            "content": (
                                "运行时检测到刚才的工具名、参数和结果都与此前一次完全相同。"
                                "请不要再次调用该工具；使用已有结果执行下一项已授权且参数齐全的动作，"
                                "或如实说明阻塞/询问缺失信息。不得声称未执行的动作已成功。"
                            ),
                        })
            raise AssistantError(429, "RESOURCE_LIMIT_EXCEEDED", "助手模型请求次数超出限制", retryable=False)
        except AssistantError as exc:
            error = {"code": exc.payload.code, "message": exc.payload.message, "retryable": exc.payload.retryable}
            usage = failed_usage if exc.payload.code == "PROVIDER_OUTPUT_TRUNCATED" else {}
            return RuntimeResult("FAILED", usage=usage, error=error, provider_requests=request_count, tool_calls=tool_count, transport_attempts=self._transport_attempts)
        except Exception as exc:  # provider text is never persisted
            error = {"code": "PROVIDER_ERROR", "message": "模型服务调用失败", "retryable": True}
            return RuntimeResult("FAILED", error=error, provider_requests=request_count, tool_calls=tool_count, transport_attempts=self._transport_attempts)
        finally:
            request_budget.seal()

    execute = run

    def _load_history(self, run_id: str, *, attempt: int, supplied: Iterable[Mapping[str, Any]] | None) -> list[Mapping[str, Any]]:
        if self.history_loader is not None:
            loader = getattr(self.history_loader, "load_history", None)
            if not callable(loader) and callable(self.history_loader):
                loader = self.history_loader
            if not callable(loader):
                raise unavailable("助手可信历史端口未配置")
            try:
                signature = inspect.signature(loader)
            except (TypeError, ValueError):
                signature = None
            if signature is None or "attempt" in signature.parameters or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values()):
                value = loader(run_id, attempt=attempt)
            else:
                value = loader(run_id)
            return list(value or ())
        if self.history is not None:
            value = getattr(self.history, "load_history", None)
            if callable(value):
                try:
                    signature = inspect.signature(value)
                except (TypeError, ValueError):
                    signature = None
                if signature is None or "attempt" in signature.parameters or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values()):
                    return list(value(run_id, attempt=attempt) or ())
                return list(value(run_id) or ())
            if isinstance(self.history, Mapping):
                return list(self.history.get(run_id, ()) or ())
            return list(self.history)
        if supplied is not None:
            raise unavailable("禁止使用客户端提供的助手历史")
        raise unavailable("助手可信历史端口未配置")

    def _provider_call(self, messages: list[Mapping[str, Any]], tools: list[Mapping[str, Any]], *, run_id: str, attempt: int, request_index: int, project_id: str | None, agent_attempt_id: str | None = None, request_budget: object | None = None) -> Any:
        # Import lazily to keep the dependency-free runtime importable while
        # still preserving the adapter's bounded transport diagnostics.
        from .adapter import ProviderResponseError, ProviderTransportError
        method = getattr(self.provider, "stream", None) if self.stream else None
        method = method or getattr(self.provider, "complete", None) or getattr(self.provider, "run", None)
        if not callable(method):
            raise unavailable("LLM Provider 端口未配置")
        last: BaseException | None = None
        for retry in range(self.max_retries + 1):
            transport_attempt = retry + 1
            self._transport_attempts += 1
            llm_call_id = f"llm_{uuid4().hex}"
            self._event(run_id, "llm.started", {"llm_call_id": llm_call_id, "request_index": request_index, "attempt": transport_attempt})
            try:
                llm_call_id = self._capture_llm_context(run_id, attempt, transport_attempt, request_index, messages, tools, project_id, agent_attempt_id=agent_attempt_id, call_id=llm_call_id, request_budget=request_budget)
            except AssistantError as exc:
                error = {"code": exc.payload.code, "message": exc.payload.message, "retryable": exc.payload.retryable}
                self._event(run_id, "llm.failed", {"llm_call_id": llm_call_id, "request_index": request_index, "attempt": transport_attempt, "error": error})
                raise
            started = time.monotonic()
            try:
                response = self._call_with_timeout(method, messages, tools, request_budget=request_budget)
                normalized = _response(response)
                if _is_truncated_finish_reason(normalized["finish_reason"]):
                    error = {
                        "code": "PROVIDER_OUTPUT_TRUNCATED",
                        "message": "模型服务输出达到长度上限，结果未提交",
                        "retryable": False,
                    }
                    self._finish_llm_call(
                        llm_call_id,
                        status="FAILED",
                        usage=normalized["usage"],
                        latency_ms=_elapsed_ms(started),
                        response=normalized,
                        error=error,
                        metadata_only_response=True,
                        request_budget=request_budget,
                    )
                    self._event(run_id, "llm.failed", {
                        "llm_call_id": llm_call_id,
                        "request_index": request_index,
                        "attempt": transport_attempt,
                        "usage": _usage(normalized["usage"]),
                        "error": error,
                    })
                    return response
                self._finish_llm_call(llm_call_id, status="SUCCEEDED", usage=normalized["usage"], latency_ms=_elapsed_ms(started), response=normalized, request_budget=request_budget)
                self._event(run_id, "llm.completed", {"llm_call_id": llm_call_id, "request_index": request_index, "attempt": transport_attempt, "usage": _usage(normalized["usage"])})
                return response
            except TimeoutError as exc:
                last = exc
                error = {"code": "PROVIDER_TIMEOUT", "message": "模型服务调用超时", "retryable": True}
                budget_snapshot = None
                if request_budget is not None:
                    budget_snapshot = self._provider_budget_summary(
                        request_budget, transport_state="UNKNOWN"
                    )
                    error["provider"] = budget_snapshot
                self._finish_llm_call(llm_call_id, status="FAILED", latency_ms=_elapsed_ms(started), error=error, request_budget=request_budget, provider_budget_snapshot=budget_snapshot)
                self._event(run_id, "llm.failed", {"llm_call_id": llm_call_id, "request_index": request_index, "attempt": transport_attempt, "error": error})
                # Python cannot cancel the daemon invocation.  Its budget was
                # sealed when the queue timed out; do not dispatch a retry
                # while the earlier invocation may still be in flight.
                raise AssistantError(504, "PROVIDER_TIMEOUT", "模型服务调用超时", retryable=True) from exc
            except AssistantError as exc:
                error = {"code": exc.payload.code, "message": exc.payload.message, "retryable": exc.payload.retryable}
                call = self._llm_calls.get(llm_call_id)
                if getattr(call, "status", None) != "FAILED":
                    self._finish_llm_call(llm_call_id, status="FAILED", latency_ms=_elapsed_ms(started), error=error, request_budget=request_budget)
                self._event(run_id, "llm.failed", {"llm_call_id": llm_call_id, "request_index": request_index, "attempt": transport_attempt, "error": error})
                raise
            except ProviderTransportError as exc:
                details = exc.safe_details()
                retryable = bool(exc.retryable)
                if exc.category == "HTTP_4XX" and retryable:
                    code, message, status = "PROVIDER_RATE_LIMITED", "模型服务请求受限，稍后重试", 503
                elif exc.category == "HTTP_4XX":
                    code, message, status = "PROVIDER_REQUEST_REJECTED", "模型服务拒绝了请求", 502
                elif exc.category == "HTTP_REDIRECT":
                    code, message, status = "PROVIDER_TRANSPORT_REJECTED", "模型服务重定向被拒绝", 502
                else:
                    code, message, status = "PROVIDER_UNAVAILABLE", "模型服务暂不可用", 503
                error = {"code": code, "message": message, "retryable": retryable, "provider": details}
                self._finish_llm_call(llm_call_id, status="FAILED", latency_ms=_elapsed_ms(started), error=error, request_budget=request_budget)
                self._event(run_id, "llm.failed", {"llm_call_id": llm_call_id, "request_index": request_index, "attempt": transport_attempt, "error": {"code": code, "message": message, "retryable": retryable}})
                if retry >= self.max_retries or not retryable:
                    raise AssistantError(status, code, message, retryable=retryable, details={"provider": details}) from exc
                time.sleep(min(1.0, 0.1 * (2 ** retry)))
            except ProviderResponseError as exc:
                details = {"category": exc.category}
                error = {"code": "PROVIDER_RESPONSE_INVALID", "message": "模型服务响应格式无效", "retryable": False, "provider": details}
                self._finish_llm_call(llm_call_id, status="FAILED", latency_ms=_elapsed_ms(started), error=error, request_budget=request_budget)
                self._event(run_id, "llm.failed", {"llm_call_id": llm_call_id, "request_index": request_index, "attempt": transport_attempt, "error": {"code": "PROVIDER_RESPONSE_INVALID", "message": "模型服务响应格式无效", "retryable": False}})
                raise AssistantError(502, "PROVIDER_RESPONSE_INVALID", "模型服务响应格式无效", retryable=False, details={"provider": details}) from exc
            except (TimeoutError, ConnectionError, OSError) as exc:
                last = exc
                error = {"code": "PROVIDER_UNAVAILABLE", "message": "模型服务暂不可用", "retryable": True}
                self._finish_llm_call(llm_call_id, status="FAILED", latency_ms=_elapsed_ms(started), error=error, request_budget=request_budget)
                self._event(run_id, "llm.failed", {"llm_call_id": llm_call_id, "request_index": request_index, "attempt": transport_attempt, "error": error})
                if retry >= self.max_retries:
                    raise AssistantError(503, "PROVIDER_UNAVAILABLE", "模型服务暂不可用", retryable=True) from exc
            except Exception as exc:
                error = {"code": "PROVIDER_ERROR", "message": "模型服务调用失败", "retryable": True}
                self._finish_llm_call(llm_call_id, status="FAILED", latency_ms=_elapsed_ms(started), error=error, request_budget=request_budget)
                self._event(run_id, "llm.failed", {"llm_call_id": llm_call_id, "request_index": request_index, "attempt": transport_attempt, "error": error})
                raise
        raise AssistantError(503, "PROVIDER_UNAVAILABLE", "模型服务暂不可用", retryable=True) from last

    def _call_with_timeout(self, method: Any, messages: list[Mapping[str, Any]], tools: list[Mapping[str, Any]], *, request_budget: object | None = None) -> Any:
        """Call and consume a provider stream under one bounded timeout."""
        wait_seconds = self.timeout_seconds
        if request_budget is not None:
            wait_seconds = min(wait_seconds, request_budget.cooperative_remaining_seconds())
        if wait_seconds <= 0:
            if request_budget is not None:
                request_budget.seal()
            raise TimeoutError
        result_queue: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

        def invoke() -> None:
            began = False
            outcome: tuple[bool, Any]
            try:
                if request_budget is not None:
                    request_budget.begin_provider_call("run")
                    began = True
                value = self._call_method(method, messages, tools, request_budget=request_budget)
                if self.stream and not isinstance(value, Mapping) and not isinstance(value, (str, bytes)):
                    merged: dict[str, Any] = {"text": "", "tool_calls": [], "structured": None, "usage": {}, "finish_reason": None}
                    for item in value:
                        part = _response(item)
                        merged["text"] += part["text"] or ""
                        merged["tool_calls"].extend(part["tool_calls"])
                        if part["structured"] is not None:
                            merged["structured"] = part["structured"]
                        merged["usage"].update(part["usage"])
                        if _has_finish_reason(part["finish_reason"]):
                            merged["finish_reason"] = part["finish_reason"]
                    value = merged
                outcome = (True, value)
            except BaseException as exc:
                outcome = (False, exc)
            finally:
                if began:
                    request_budget.end_provider_call("run")
            # Publish only after invocation accounting is settled so a caller
            # receiving this outcome cannot snapshot a completed call as live.
            result_queue.put(outcome)

        # A provider cannot be safely interrupted by Python. A daemon thread
        # prevents a stalled transport from keeping the worker process alive.
        thread = threading.Thread(target=invoke, name="assistant-provider-call", daemon=True)
        thread.start()
        try:
            ok, value = result_queue.get(timeout=wait_seconds)
        except queue.Empty as exc:
            if request_budget is not None:
                request_budget.seal()
            raise TimeoutError from exc
        if ok:
            return value
        if isinstance(value, BaseException):
            raise value
        raise RuntimeError("provider call failed")

    def _call_method(self, method: Any, messages: list[Mapping[str, Any]], tools: list[Mapping[str, Any]], *, request_budget: object | None = None) -> Any:
        kwargs = {"config": self.config, "messages": messages, "tools": tools, "response_schema": self.response_schema, "timeout_seconds": self.timeout_seconds}
        # Never retry after catching TypeError from inside the provider: it
        # may have accepted the request and performed a side effect already.
        try:
            signature = inspect.signature(method)
        except (TypeError, ValueError):
            return method(**kwargs)
        parameters = signature.parameters.values()
        if any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters):
            if request_budget is not None:
                kwargs["request_budget"] = request_budget
            return method(**kwargs)
        if request_budget is not None and "request_budget" in signature.parameters:
            kwargs["request_budget"] = request_budget
        positional: list[Any] = []
        selected: dict[str, Any] = {}
        for parameter in parameters:
            if parameter.name not in kwargs or parameter.kind is inspect.Parameter.VAR_POSITIONAL:
                continue
            if parameter.kind is inspect.Parameter.POSITIONAL_ONLY:
                positional.append(kwargs[parameter.name])
            elif parameter.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY):
                selected[parameter.name] = kwargs[parameter.name]
        return method(*positional, **selected)

    async def _wait_for_prediction(self, name: str, value: Any, *, arguments: Mapping[str, Any], ctx: object | None, run_id: str, attempt: int, tool_call_id: str, deadline: float) -> Any:
        """Wait in this Run while the worker advances the submitted prediction."""
        if name not in {"create_prediction", "get_prediction"}:
            return value
        accepted = _public_value(value)
        if name == "create_prediction" and isinstance(accepted, Mapping) and isinstance(accepted.get("data"), Mapping):
            accepted = accepted["data"]
        if not isinstance(accepted, Mapping):
            raise AssistantError(502, "PREDICTION_RESULT_INVALID", "预测受理结果无效", retryable=False)
        prediction_id = accepted.get("id") or arguments.get("prediction_id")
        job_id = accepted.get("job_id")
        if (not isinstance(prediction_id, str) or not prediction_id
                or (name == "get_prediction" and accepted.get("id") != prediction_id)
                or not isinstance(job_id, str) or not job_id):
            raise AssistantError(502, "PREDICTION_RESULT_INVALID", "预测任务引用无效", retryable=False)
        if not _prediction_is_pending(accepted):
            if accepted.get("status") not in {"SUCCEEDED", "FAILED", "CANCELED", "TIMED_OUT", "INTERRUPTED"}:
                raise AssistantError(502, "PREDICTION_RESULT_INVALID", "预测终态无效", retryable=False)
            if accepted["status"] == "SUCCEEDED" and not accepted.get("result"):
                raise AssistantError(502, "PREDICTION_RESULT_INVALID", "预测成功结果缺失", retryable=False)
            return value
        current: Mapping[str, Any] = accepted
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return {**accepted, **current, "wait_timeout": True}
            result = self._execute_tool(
                "get_prediction", {"prediction_id": prediction_id}, ctx=ctx,
                run_id=run_id, attempt=attempt, tool_call_id=tool_call_id,
            )
            current = _public_value(result.result if isinstance(result, ToolExecution) else result)
            if (not isinstance(current, Mapping) or current.get("id") != prediction_id
                    or (accepted.get("job_id") and current.get("job_id") != accepted["job_id"])):
                raise AssistantError(502, "PREDICTION_RESULT_INVALID", "预测结果与已提交任务不匹配", retryable=False)
            if not _prediction_is_pending(current):
                if current.get("status") not in {"SUCCEEDED", "FAILED", "CANCELED", "TIMED_OUT", "INTERRUPTED"} or (current["status"] == "SUCCEEDED" and not current.get("result")):
                    raise AssistantError(502, "PREDICTION_RESULT_INVALID", "预测终态或结果无效", retryable=False)
                return {**accepted, **current}
            await asyncio.sleep(min(.5, remaining))

    def _execute_tool(self, name: str, args: Mapping[str, Any], *, ctx: object | None, run_id: str | None = None, attempt: int = 1, tool_call_id: str | None = None) -> Any:
        tool: BoundTool | None
        tool = self.toolset.get(name) if isinstance(self.toolset, Mapping) else None
        if tool is None or not callable(tool):
            raise AssistantError(403, "PERMISSION_DENIED", "模型请求的工具不在允许列表中")
        # Runtime receives a pre-bound closure in production. ToolSet.bind is
        # intentionally not allowed to discover application services here.
        tool_ctx = _tool_call_context(ctx, run_id=run_id, attempt=attempt, tool_call_id=tool_call_id)
        if isinstance(tool, BoundTool):
            if name == "get_prediction" and tool.ledger is not None:
                # A mutable status read must reach the authorized service each time.
                tool = BoundTool(tool.spec, tool.service, authorization=tool.authorization)
            return tool(tool_ctx, args, run_id=run_id, confirmation_id=getattr(tool_ctx, "confirmation_id", None))
        return tool(tool_ctx, args)

    def _tool_schema(self, name: str) -> str:
        tool = self.toolset.get(name) if isinstance(self.toolset, Mapping) else None
        return str(getattr(getattr(tool, "spec", None), "schema_version", "1.0"))

    def _new_tool_call(self, run_id: str, attempt: int, provider_id: str, name: str, args: Mapping[str, Any], ctx: object | None) -> ToolCall:
        project_id = self.project_id or getattr(ctx, "project_id", None) or getattr(ctx, "server_project_id", None) or "unknown"
        actor = getattr(ctx, "actor", None)
        actor_id = getattr(actor, "actor_id", None) or getattr(ctx, "actor_id", None)
        spec = getattr(self.toolset.get(name) if isinstance(self.toolset, Mapping) else None, "spec", None)
        permission = str(getattr(spec, "permission", "assistant:use"))
        schema_version = str(getattr(spec, "schema_version", "1.0"))
        agent_attempt_id = str(getattr(ctx, "agent_attempt_id", None) or f"attempt_{attempt}")
        binding = {"project_id": project_id, "actor_id": actor_id, "permission": permission, "schema_version": schema_version, "run_id": run_id, "attempt": attempt}
        argument_keys = sorted(str(key) for key in args)[:100]
        return ToolCall(id=provider_id, agent_run_id=run_id, agent_attempt_id=agent_attempt_id, provider_tool_call_id=provider_id, tool_name=name, tool_schema_version=schema_version, arguments_hash=_sha256_json(args), binding_hash=_sha256_json(binding), status="PROPOSED", arguments_summary={"metadata_only": True, "project_id": project_id, "actor_id": actor_id, "permission": permission, "schema_version": schema_version, "argument_count": len(args), "argument_keys": argument_keys, "argument_types": {key: type(args[key]).__name__ for key in argument_keys if key in args}}, started_at=None)

    def _save_tool_call(self, call: ToolCall, *, ctx: object | None = None) -> None:
        saver = getattr(self.context_inspector, "save_tool_call", None) if self.context_inspector is not None else None
        if not callable(saver):
            return
        try:
            saver(call, execution_token=getattr(ctx, "execution_token", None), fencing_token=getattr(ctx, "fencing_token", None))
        except AssistantError:
            raise
        except Exception as exc:
            raise AssistantError(503, "TRACE_PERSISTENCE_FAILED", "工具调用账本保存失败", retryable=True) from exc

    def _record_tool_transition(self, call: ToolCall, event_type: str, data: Mapping[str, Any], *, ctx: object | None = None) -> None:
        recorder = getattr(self.context_inspector, "record_tool_call_event", None) if self.context_inspector is not None else None
        if callable(recorder):
            try:
                combined = recorder(call, event_type, data, execution_token=getattr(ctx, "execution_token", None), fencing_token=getattr(ctx, "fencing_token", None))
                if combined is False:
                    self._event(call.agent_run_id, event_type, data)
                return
            except AssistantError:
                raise
            except Exception as exc:
                raise AssistantError(503, "TRACE_PERSISTENCE_FAILED", "工具调用及事件保存失败", retryable=True) from exc
        self._save_tool_call(call, ctx=ctx)
        self._event(call.agent_run_id, event_type, data)

    def _record_tool_transition_best_effort(self, call: ToolCall, event_type: str, data: Mapping[str, Any], *, ctx: object | None = None) -> None:
        try:
            self._record_tool_transition(call, event_type, data, ctx=ctx)
        except Exception:
            return

    def _save_tool_call_best_effort(self, call: ToolCall, *, ctx: object | None = None) -> None:
        try:
            self._save_tool_call(call, ctx=ctx)
        except Exception:
            return

    def _tool_failure(self, call: ToolCall, exc: Exception, started_at: datetime) -> ToolCall:
        if isinstance(exc, AssistantError):
            error = {"code": exc.payload.code, "message": exc.payload.message, "retryable": exc.payload.retryable}
        else:
            error = {"code": "TOOL_EXECUTION_FAILED", "message": "工具执行失败", "retryable": True}
        return call.model_copy(update={"status": "FAILED", "error": error, "finished_at": _now(), "result_summary": {"metadata_only": True, "latency_ms": max(0, int((_now() - started_at).total_seconds() * 1000))}})

    def _schemas(self) -> list[Mapping[str, Any]]:
        if isinstance(self.toolset, ToolSet):
            return self.toolset.schemas()
        if isinstance(self.toolset, Mapping):
            return [getattr(value, "spec").public_schema() for value in self.toolset.values() if hasattr(value, "spec")]
        return []

    def _event(self, run_id: str, event_type: str, data: Mapping[str, Any]) -> None:
        if self.event_store is None:
            return
        append = getattr(self.event_store, "append", None)
        if callable(append):
            append("agent_run", run_id, event_type, dict(data), agent_run_id=run_id)

    def _capture_llm_context(self, run_id: str, attempt: int, transport_attempt: int, request_index: int, messages: list[Mapping[str, Any]], tools: list[Mapping[str, Any]], project_id: str | None, *, agent_attempt_id: str | None = None, call_id: str | None = None, request_budget: object | None = None) -> str:
        call_id = call_id or f"llm_{uuid4().hex}"
        inspector = self.context_inspector
        if inspector is None:
            return call_id
        from .trace import LlmCallDetail, Usage
        call = LlmCallDetail(id=call_id, agent_run_id=run_id, agent_attempt_id=agent_attempt_id or f"attempt_{attempt}", request_index=request_index, transport_attempt=transport_attempt, model_id=self.config.model_id, project_id=project_id, status="RUNNING", provider_configuration_id=getattr(self.config, "provider_configuration_id", None), provider_configuration_revision=self.config.config_revision, usage=Usage())
        if self.trace_metadata_only:
            provider_request = _trace_request_payload(
                self.config,
                messages,
                tools,
                response_schema=self.response_schema,
                timeout_seconds=self.timeout_seconds,
                metadata_only=True,
            )
            logical_context = provider_request
        else:
            provider_request = {"config": self.config.public_dict(), "messages": messages, "tools": tools, "response_schema": self.response_schema, "timeout_seconds": self.timeout_seconds}
            logical_context = {"messages": messages, "tools": tools}
        parameters = {"max_output_tokens": DEFAULT_MAX_OUTPUT_TOKENS, "stream": self.stream}
        if request_budget is not None:
            parameters["provider_budget"] = self._provider_budget_summary(
                request_budget, transport_state="ATTEMPT_NOT_STARTED"
            )
        call = replace(call, model_parameters_summary=parameters)
        capture_pair = getattr(inspector, "capture_pair", None)
        if not callable(capture_pair):
            raise unavailable("Context trace capture 端口未配置")
        try:
            logical, provider = capture_pair(llm_call=call, logical_context=logical_context, provider_request=provider_request, project_id=project_id)
        except AssistantError as exc:
            self._save_failed_trace_call(call, exc)
            raise
        except Exception as exc:
            failure = AssistantError(503, "TRACE_PERSISTENCE_FAILED", "Context 快照保存失败", retryable=True)
            self._save_failed_trace_call(call, failure)
            raise failure from exc
        self._llm_calls[call_id] = replace(call, logical_context_snapshot_id=logical.id, provider_request_snapshot_id=provider.id)
        return call_id

    def _save_failed_trace_call(self, call: object, error: AssistantError) -> None:
        """Best-effort terminal trace row after capture failure."""
        if self.context_inspector is None:
            return
        from .trace import LlmCallDetail
        if not isinstance(call, LlmCallDetail):
            return
        failed = replace(
            call,
            status="FAILED",
            error={"code": error.payload.code, "message": error.payload.message, "retryable": error.payload.retryable},
            finished_at=_now(),
        )
        self._llm_calls[call.id] = failed
        saver = getattr(self.context_inspector, "save_llm_call", None)
        if callable(saver):
            try:
                saver(failed)
            except Exception:
                # The worker's durable run failure remains authoritative if
                # the trace store itself is unavailable.
                return

    @staticmethod
    def _provider_budget_summary(request_budget: object, *, transport_state: str) -> dict[str, Any]:
        """Return bounded local counters only; never infer remote receipt/cancel."""
        _auth_failed, _stage, active_calls, _stages = request_budget.provider_call_state
        return {
            "local_dispatch_attempts": int(request_budget.requests),
            "active_provider_calls": int(active_calls),
            "transport_state": transport_state,
            "remote_receipt": "UNKNOWN",
        }

    def _finish_llm_call(self, call_id: str, *, status: str, usage: Mapping[str, Any] | None = None, latency_ms: int | None = None, response: Mapping[str, Any] | None = None, error: Mapping[str, Any] | None = None, error_diagnostic: Mapping[str, str] | None = None, metadata_only_response: bool = False, request_budget: object | None = None, provider_budget_snapshot: Mapping[str, Any] | None = None, provider_response_receive_complete_at: str | None = None, provider_response_end_kind: str | None = None) -> None:
        call = self._llm_calls.get(call_id)
        if call is None or self.context_inspector is None:
            return
        from dataclasses import replace
        from .trace import Usage
        raw_usage = usage or {}
        parameters = dict(getattr(call, "model_parameters_summary", {}) or {})
        if provider_budget_snapshot is not None:
            parameters["provider_budget"] = dict(provider_budget_snapshot)
        elif request_budget is not None:
            parameters["provider_budget"] = self._provider_budget_summary(
                request_budget, transport_state="CALL_RETURNED" if status == "SUCCEEDED" else "UNKNOWN"
            )
        if provider_response_end_kind in {"EOF", "ERROR", "CLOSED_EARLY"}:
            parameters["provider_response_end_kind"] = provider_response_end_kind
            if provider_response_end_kind == "EOF":
                timestamp = _canonical_utc_timestamp(provider_response_receive_complete_at)
                if timestamp is not None:
                    parameters["provider_response_receive_complete_at"] = timestamp
        if error_diagnostic is not None:
            parameters.setdefault("provider_error_diagnostic", dict(error_diagnostic))
        updated = replace(call, status=status, model_parameters_summary=parameters, usage=Usage(input_tokens=_int_or_none(raw_usage.get("input_tokens")), output_tokens=_int_or_none(raw_usage.get("output_tokens")), cached_input_tokens=_int_or_none(raw_usage.get("cached_input_tokens"))), estimates={"method": "provider" if raw_usage else "unavailable"}, latency_ms=latency_ms, response_summary=_response_summary(response, metadata_only=self.trace_metadata_only or metadata_only_response) if response else None, error=error, finished_at=_now())
        self._llm_calls[call_id] = updated
        saver = getattr(self.context_inspector, "save_llm_call", None)
        if not callable(saver):
            raise unavailable("Context trace persistence 端口未配置")
        try:
            saver(updated)
        except AssistantError:
            self._save_terminal_trace_fallback(saver, updated)
            raise
        except Exception as exc:
            self._save_terminal_trace_fallback(saver, updated)
            raise AssistantError(503, "TRACE_PERSISTENCE_FAILED", "LLM 调用账本保存失败", retryable=True) from exc

    def _save_terminal_trace_fallback(self, saver: Any, call: object) -> None:
        """Best-effort FAILED upsert when the normal trace write fails."""
        from .trace import LlmCallDetail
        if not isinstance(call, LlmCallDetail):
            return
        failed = replace(
            call,
            status="FAILED",
            response_summary=None,
            error={"code": "TRACE_PERSISTENCE_FAILED", "message": "LLM 调用账本保存失败", "retryable": True},
            finished_at=_now(),
        )
        self._llm_calls[failed.id] = failed
        try:
            saver(failed)
        except Exception:
            return

    @staticmethod
    def _message(value: Mapping[str, Any]) -> Mapping[str, Any]:
        if not isinstance(value, Mapping) or set(value) - {"role", "content", "name", "tool_call_id", "tool_name"}:
            raise AssistantError(409, "RECOVERY_STATE_INVALID", "服务端历史 schema 无效")
        return dict(value)


def _trace_request_payload(
    config: RuntimeConfigSnapshot,
    messages: list[Mapping[str, Any]],
    tools: list[Mapping[str, Any]],
    *,
    response_schema: Mapping[str, Any] | None,
    timeout_seconds: float,
    metadata_only: bool,
) -> dict[str, Any]:
    """Return a trace-safe request view, never the provider body.

    Production workers persist only this view.  The exact runtime and wire
    envelopes are hashed in memory so trace readers can correlate a failed
    request without receiving prompt text, tool arguments, URL details, or
    credentials.
    """
    runtime_envelope = {
        "config": config.public_dict(),
        "messages": messages,
        "response_schema": response_schema,
        "timeout_seconds": timeout_seconds,
        "tools": tools,
    }
    from .adapter import _OpenAICompatibleClient
    wire_envelope = {
        "model": config.model_id,
        "messages": messages,
        "max_tokens": DEFAULT_MAX_OUTPUT_TOKENS,
        "tools": _OpenAICompatibleClient._tools(tools),
        "tool_choice": "auto",
    }

    def canonical(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

    def digest(value: Any) -> str:
        return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()

    def message_shape(message: Mapping[str, Any]) -> dict[str, Any]:
        content = message.get("content")
        content_text = content if isinstance(content, str) else canonical(content)
        return {
            "role": str(message.get("role", "")),
            "content_type": type(content).__name__,
            "content_length": len(content_text),
            "content_sha256": hashlib.sha256(content_text.encode("utf-8")).hexdigest(),
        }

    roles = [str(message.get("role", "")) for message in messages]
    message_shapes = [message_shape(message) for message in messages]
    user_hashes = [item["content_sha256"] for item, role in zip(message_shapes, roles) if role == "user"]
    repeated_current = 0
    if user_hashes:
        repeated_current = sum(1 for value in user_hashes if value == user_hashes[-1])
    schema_text = canonical(tools)
    wire_tools = wire_envelope["tools"]
    return {
        "metadata_only": bool(metadata_only),
        "runtime_envelope_sha256": digest(runtime_envelope),
        "wire_envelope_sha256": digest(wire_envelope),
        "messages": {
            "count": len(messages),
            "roles": roles,
            "system_prompt_present": "system" in roles,
            "tool_message_count": roles.count("tool"),
            "repeated_current_user_message_count": repeated_current,
            "items": message_shapes,
        },
        "tools": {
            "count": len(tools),
            "names": [str(item.get("name", "")) for item in tools],
            "order_sha256": digest([item.get("name") for item in tools]),
            "schema_sha256": hashlib.sha256(schema_text.encode("utf-8")).hexdigest(),
            "schema_keyword_counts": {key: schema_text.count(key) for key in ("$defs", "$ref", "anyOf", "oneOf", "allOf", "required")},
            "wire_count": len(wire_tools),
        },
        "request": {
            "model_id": config.model_id,
            "max_tokens": DEFAULT_MAX_OUTPUT_TOKENS,
            "tool_choice": "auto",
            "response_schema_present": response_schema is not None,
            "timeout_seconds": timeout_seconds,
        },
        "configuration": {
            "provider_configuration_id": config.provider_configuration_id,
            "config_revision": config.config_revision,
            "model_id": config.model_id,
            "key_reference": config.key_reference,
        },
    }


def _response(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {"text": value.get("text", value.get("content")), "structured": value.get("structured", value.get("output")), "tool_calls": list(value.get("tool_calls") or value.get("tools") or ()), "usage": dict(value.get("usage") or {}), "finish_reason": value.get("finish_reason")}
    return {"text": getattr(value, "text", getattr(value, "content", None)), "structured": getattr(value, "structured", getattr(value, "output", None)), "tool_calls": list(getattr(value, "tool_calls", ()) or ()), "usage": dict(getattr(value, "usage", {}) or {}), "finish_reason": getattr(value, "finish_reason", None)}


def _has_finish_reason(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_truncated_finish_reason(value: Any) -> bool:
    return _has_finish_reason(value) and value.strip().casefold() in {"length", "max_tokens"}


def _tool_call(value: Any) -> tuple[str, Mapping[str, Any], str]:
    if isinstance(value, Mapping):
        name = value.get("name") or value.get("tool_name")
        args = value.get("arguments", value.get("args", {}))
        call_id = value.get("id") or value.get("provider_tool_call_id") or ""
    else:
        name = getattr(value, "name", getattr(value, "tool_name", None)); args = getattr(value, "arguments", getattr(value, "args", {})); call_id = getattr(value, "id", None) or getattr(value, "provider_tool_call_id", None) or ""
    if not isinstance(name, str) or not name:
        raise AssistantError(400, "VALIDATION_FAILED", "模型返回的工具名称无效")
    if not isinstance(args, Mapping):
        raise AssistantError(400, "VALIDATION_FAILED", "模型返回的工具参数无效")
    return name, args, str(call_id)


def _stable_tool_call_id(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return "tool_" + hashlib.sha256(b"missing-provider-tool-call-id").hexdigest()[:32]
    if len(value) <= 96:
        return value
    return f"tool_{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _namespaced_tool_call_id(run_id: str, attempt: int, ctx: object | None, provider_id: str) -> str:
    """Opaque ToolCall identity scoped to one fenced agent attempt.

    The provider id is only an input to the digest.  This prevents a reused
    provider id in a recovered attempt from colliding with the old ToolCall,
    while a transport retry in the same attempt produces the same identity.
    """

    agent_attempt_id = getattr(ctx, "agent_attempt_id", None) or f"attempt_{attempt}"
    material = f"{run_id}|{agent_attempt_id}|{provider_id}"
    return "tool_" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _tool_call_context(ctx: object | None, *, run_id: str | None, attempt: int, tool_call_id: str | None) -> object | None:
    """Return a per-call context without mutating the run's fenced context."""

    if ctx is None:
        return None
    values = {"agent_run_id": run_id, "tool_call_id": tool_call_id, "tool_call_attempt": attempt}
    if is_dataclass(ctx):
        try:
            return replace(ctx, **{key: value for key, value in values.items() if hasattr(ctx, key)})
        except (TypeError, ValueError):
            pass
    try:
        copied = replace(ctx) if is_dataclass(ctx) else __import__("copy").copy(ctx)
        for key, value in values.items():
            try:
                setattr(copied, key, value)
            except (AttributeError, TypeError):
                continue
        return copied
    except Exception:
        return ctx


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(json.dumps(_public_value(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _resource_ref_value(value: Any) -> ResourceRef | None:
    if value is None:
        return None
    raw = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
    if isinstance(raw, Mapping) and isinstance(raw.get("type"), str) and isinstance(raw.get("id"), str):
        try:
            return ResourceRef.model_validate({"type": raw["type"], "id": raw["id"]})
        except Exception:
            return None
    return None


def _result_resource_ref(value: Any) -> Any:
    if isinstance(value, Mapping):
        return value.get("resource_ref")
    return getattr(value, "resource_ref", None)


def _result_job_id(value: Any) -> str | None:
    raw = value.get("job_id") if isinstance(value, Mapping) else getattr(value, "job_id", None)
    return str(raw) if isinstance(raw, str) and raw else None


def _tool_result_summary(value: Any) -> dict[str, Any]:
    safe = _public_value(value)
    metadata: dict[str, Any] = {
        "metadata_only": True,
        "type": type(value).__name__ if value is not None else "NoneType",
        "result_hash": _sha256_json(safe),
    }
    if isinstance(safe, Mapping):
        metadata["count"] = len(safe)
        metadata["keys"] = sorted(str(key) for key in safe)[:100]
        for key in ("resource_ref", "resource_refs", "job_id", "status", "error", "schema", "schema_version"):
            if key in safe:
                metadata[key] = safe[key]
    elif isinstance(safe, (list, tuple)):
        metadata["count"] = len(safe)
    else:
        metadata["count"] = 1 if safe is not None else 0
    return metadata


def _prediction_is_pending(value: Any) -> bool:
    status = value.get("status") if isinstance(value, Mapping) else getattr(value, "status", None)
    normalized = str(getattr(status, "value", status) or "").upper()
    return normalized in {"QUEUED", "PENDING", "RUNNING", "IN_PROGRESS", "SUBMITTED"}


def _usage(value: Mapping[str, Any]) -> dict[str, Any]:
    result = {}
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        if value.get(key) is not None:
            try:
                result[key] = int(value[key])
            except (TypeError, ValueError):
                pass
    return result


def _native_error(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        code = value.get("code") if isinstance(value.get("code"), str) else "PROVIDER_ERROR"
        message = value.get("message") if isinstance(value.get("message"), str) else "模型服务调用失败"
        retryable = value.get("retryable") if isinstance(value.get("retryable"), bool) else True
        return {"code": code[:96], "message": _public_text(message) or "模型服务调用失败", "retryable": retryable}
    return {"code": "PROVIDER_ERROR", "message": "模型服务调用失败", "retryable": True}


def _native_error_diagnostic(value: Any) -> dict[str, str]:
    if isinstance(value, BaseException):
        name = type(value).__name__
    elif isinstance(value, Mapping):
        raw_name = next((value.get(key) for key in ("exception_class", "type", "class") if isinstance(value.get(key), str)), "")
        name = raw_name[:128].rsplit(".", 1)[-1]
    else:
        name = ""

    entry = _NATIVE_EXCEPTION_DIAGNOSTICS.get(name)
    if entry is None and isinstance(value, TypeError):
        entry = _NATIVE_EXCEPTION_DIAGNOSTICS["TypeError"]
    if entry is None and isinstance(value, TimeoutError):
        entry = _NATIVE_EXCEPTION_DIAGNOSTICS["TimeoutError"]
    if entry is None and isinstance(value, ConnectionError):
        entry = ("TransportError", "TRANSPORT")
    if entry is None:
        if isinstance(value, Mapping):
            supplied = value.get("category")
            if isinstance(supplied, str) and supplied in _NATIVE_TRANSPORT_CATEGORIES:
                return {"exception_class": "Unknown", "reason_category": supplied}
        return {"exception_class": "Unknown", "reason_category": "UNKNOWN"}

    label, category = entry
    if label == "ProviderTransportError":
        details: Mapping[str, Any] = {}
        if isinstance(value, BaseException):
            try:
                from .adapter import ProviderTransportError

                if isinstance(value, ProviderTransportError):
                    details = value.safe_details()
            except Exception:
                details = {}
        elif isinstance(value, Mapping) and name == "ProviderTransportError":
            details = value
        supplied = details.get("category")
        if isinstance(supplied, str) and supplied in _NATIVE_TRANSPORT_CATEGORIES:
            category = supplied
    return {"exception_class": label, "reason_category": category}


def _private_json_snapshot(value: Any) -> Any:
    """Copy native framework state as bounded JSON without altering trusted history."""
    def default(item: Any) -> Any:
        if is_dataclass(item):
            return asdict(item)
        dump = getattr(item, "model_dump", None)
        if callable(dump):
            return dump(mode="json")
        as_dict = getattr(item, "as_dict", None)
        if callable(as_dict):
            return as_dict()
        raise TypeError("private assistant checkpoint is not JSON serializable")

    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False, default=default)
        if len(encoded.encode("utf-8")) > _MAX_PRIVATE_HISTORY_BYTES:
            raise ValueError("private assistant checkpoint exceeds the size limit")
        return json.loads(encoded)
    except (TypeError, ValueError, OverflowError) as exc:
        raise AssistantError(409, "RECOVERY_STATE_INVALID", "助手可信私有历史无效", retryable=False) from exc


def _public_pending_action(value: Mapping[str, Any]) -> Any:
    """Keep platform confirmation details while hiding provider wire IDs."""
    public_fields = {
        "tool_call_id", "confirmation_id", "resource_ref", "action", "revision",
        "config_hash", "schema_version", "normalized_args", "expires_at",
    }
    return _public_value({key: item for key, item in value.items() if key in public_fields})


def _pending_confirmation_dto(value: Mapping[str, Any]) -> PendingConfirmationBinding:
    """Project private deferred metadata into the stable strict platform DTO."""
    public_fields = {
        "tool_call_id", "confirmation_id", "resource_ref", "action", "revision",
        "config_hash", "schema_version", "normalized_args", "expires_at",
    }
    try:
        return PendingConfirmationBinding.model_validate({key: item for key, item in value.items() if key in public_fields})
    except Exception as exc:
        raise AssistantError(409, "CONFIRMATION_STATE_INVALID", "工具确认绑定无效", retryable=False) from exc


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _int_or_none(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _response_summary(value: Mapping[str, Any] | None, *, metadata_only: bool = False) -> dict[str, Any] | None:
    if value is None:
        return None
    tool_names = [str(item.get("name") or item.get("tool_name")) for item in value.get("tool_calls", ()) if isinstance(item, Mapping)]
    if metadata_only:
        return {"text_excerpt": None, "tool_names": tool_names, "finish_reason": value.get("finish_reason")}
    return {
        "text_excerpt": str(value.get("text"))[:2000] if value.get("text") is not None else None,
        "tool_names": tool_names,
        "finish_reason": value.get("finish_reason"),
    }


_MAX_TOOL_RESULT_BYTES = 20_000
_MAX_PRIVATE_HISTORY_BYTES = 4_000_000
_WIRE_RESULT_FIELDS = ("resource_ref", "resource_refs", "structure_ref", "canonical_smiles", "snapshot_hash", "job_id", "status", "error", "schema", "schema_version")


def _model_tool_result(name: str, value: Any) -> Any:
    """Keep a compact model-facing view while audits retain the full DTO."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return value
    if not isinstance(value, Mapping):
        return value
    if name == "normalize_structure":
        return {key: value[key] for key in ("structure_ref", "canonical_smiles", "stereochemistry_status", "warnings") if key in value}
    if name not in {"create_prediction", "get_prediction"}:
        return value
    if isinstance(value.get("data"), Mapping):
        value = value["data"]
    if "status" not in value:
        return value
    compact = {key: value[key] for key in ("id", "job_id", "resource_ref", "status", "wait_timeout", "chromatography_type", "source", "data_origin", "warnings", "error") if key in value}
    model = value.get("model") or value.get("model_snapshot")
    if isinstance(model, Mapping):
        compact["model"] = {key: model[key] for key in (
            "id", "version", "model_id", "model_version", "business_scope_id",
            "artifact_sha256", "input_schema_version", "output_schema_version",
            "preprocessing_profile_id", "preprocessing_profile_sha256",
            "score_semantics", "threshold",
        ) if key in model}
    result = value.get("result")
    if isinstance(result, Mapping):
        compact["result"] = {key: result[key] for key in ("task_type", "metrics", "rows", "results", "candidates", "applicability", "warnings", "score_semantics", "candidate_scope_provenance") if key in result}
    else:
        compact["result"] = result
    return compact


def _summary(value: Any) -> str:
    """Serialize a safe tool result without sending unbounded provider data."""
    safe = _public_value(value)
    try:
        encoded = json.dumps(safe, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        encoded = json.dumps({"result_hash": _sha256_json(safe), "serialization_error": True}, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) <= _MAX_TOOL_RESULT_BYTES:
        return encoded
    compact: dict[str, Any] = {"truncated": True, "result_hash": _sha256_json(safe)}
    if isinstance(safe, Mapping):
        compact.update({key: safe[key] for key in _WIRE_RESULT_FIELDS if key in safe})
        compact["field_count"] = len(safe)
    elif isinstance(safe, (list, tuple)):
        compact["item_count"] = len(safe)
    encoded = json.dumps(compact, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return encoded if len(encoded.encode("utf-8")) <= _MAX_TOOL_RESULT_BYTES else json.dumps({"truncated": True, "result_hash": compact["result_hash"]}, separators=(",", ":"))


_SENSITIVE_FIELDS = re.compile(r"(?:key|api[_-]?key|authorization|cookie|secret|token|password|credential|reasoning|thinking|chain[_-]?of[_-]?thought|raw[_-]?response)", re.I)
_SENSITIVE_TEXT = re.compile(r"(?ix)(?:\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]+)|(?:\b(?:api[-_ ]?key|access[-_ ]?token|refresh[-_ ]?token|secret|password|authorization|cookie)\s*[=:]\s*[^\s,;]+)|(?:\bsk[-_][A-Za-z0-9._-]+)")


def _public_value(value: Any, *, depth: int = 0) -> Any:
    """Recursively remove secrets/hidden reasoning before persistence or replay."""
    if depth > 16:
        return "[REDACTED_DEPTH]"
    if is_dataclass(value):
        return _public_value(asdict(value), depth=depth + 1)
    if hasattr(value, "model_dump"):
        return _public_value(value.model_dump(mode="json"), depth=depth + 1)
    if hasattr(value, "as_dict"):
        return _public_value(value.as_dict(), depth=depth + 1)
    if isinstance(value, Mapping):
        return {str(key): _public_value(item, depth=depth + 1) for key, item in value.items() if not _SENSITIVE_FIELDS.search(str(key))}
    if isinstance(value, (list, tuple)):
        return [_public_value(item, depth=depth + 1) for item in value[:100]]
    if isinstance(value, str):
        return _SENSITIVE_TEXT.sub("[REDACTED]", value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


def _public_text(value: Any) -> str | None:
    if value is None:
        return None
    return str(_public_value(value))[:20_000]


AssistantRuntime = AgentRuntime
Runtime = AgentRuntime


__all__ = ["AgentRuntime", "AssistantRuntime", "Runtime", "RuntimeConfigSnapshot", "RuntimeResult"]
