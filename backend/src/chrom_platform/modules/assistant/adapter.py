"""Optional PydanticAI adapter.

The import is intentionally delayed until construction/use.  The base
assistant package remains importable and reports a precise 503 when the
optional dependency is absent; it never fabricates a model response.
"""

from __future__ import annotations

import asyncio
import contextvars
import importlib
import http.client
import ipaddress
import inspect
import json
import os
import re
import socket
import ssl
import threading
import time
import hashlib
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlsplit

from .errors import AssistantError, unavailable
from .ports import LLMProviderAdapter
from .runtime import DEFAULT_MAX_OUTPUT_TOKENS, RuntimeConfigSnapshot
from .security import trusted_provider_url


_PROVIDER_ERROR_CODE_ALLOWLIST = frozenset({
    "20015", "authentication_error", "context_length_exceeded",
    "invalid_request_error", "model_not_found", "permission_denied",
    "rate_limit_exceeded", "server_error", "service_unavailable",
})
_PROVIDER_ERROR_CATEGORIES = frozenset({
    "DNS", "TLS", "CONNECT", "TIMEOUT", "HTTP_4XX", "HTTP_5XX",
    "HTTP_REDIRECT", "RESPONSE_SCHEMA", "RESPONSE_LIMIT",
})
_NATIVE_CLIENTS: dict[tuple[Any, str], tuple[Any, Any, Any]] = {}
_NATIVE_CLIENTS_LOCK = threading.Lock()
_NATIVE_FRAMEWORK = "pydantic-ai"
_NATIVE_SERIALIZATION = "ModelMessagesTypeAdapter"


def _provider_error_code(payload: bytes) -> str | None:
    """Extract only an allowlisted provider code; never retain response text."""
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    candidates: list[Any] = []
    if isinstance(value, Mapping):
        candidates.extend((value.get("code"), value.get("type")))
        nested = value.get("error")
        if isinstance(nested, Mapping):
            candidates.extend((nested.get("code"), nested.get("type")))
    for candidate in candidates:
        if isinstance(candidate, int) and not isinstance(candidate, bool):
            candidate = str(candidate)
        if isinstance(candidate, str) and candidate in _PROVIDER_ERROR_CODE_ALLOWLIST:
            return candidate
    return None


class ProviderTransportError(ConnectionError):
    """Provider failure with bounded, non-secret diagnostics."""

    def __init__(self, category: str, *, http_status: int | None = None,
                 provider_error_code: str | None = None,
                 retryable: bool | None = None,
                 message: str = "模型服务请求失败") -> None:
        if category not in _PROVIDER_ERROR_CATEGORIES:
            category = "CONNECT"
        if http_status is not None and not 100 <= int(http_status) <= 599:
            http_status = None
        if provider_error_code not in _PROVIDER_ERROR_CODE_ALLOWLIST:
            provider_error_code = None
        self.category = category
        self.http_status = int(http_status) if http_status is not None else None
        self.provider_error_code = provider_error_code
        self.retryable = category not in {"HTTP_4XX", "HTTP_REDIRECT", "RESPONSE_SCHEMA"} if retryable is None else bool(retryable)
        if category == "HTTP_4XX" and (self.http_status == 429 or provider_error_code == "rate_limit_exceeded"):
            self.retryable = True
        super().__init__(message)

    def safe_details(self) -> dict[str, Any]:
        details: dict[str, Any] = {"category": self.category}
        if self.http_status is not None:
            details["http_status"] = self.http_status
        if self.provider_error_code is not None:
            details["provider_error_code"] = self.provider_error_code
        return details


class ProviderRequestBudget:
    """Thread-safe cooperative deadline and local dispatch allowance per operation."""

    def __init__(self, *, deadline_monotonic: float, max_requests: int = 5,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.deadline_monotonic = float(deadline_monotonic)
        self.max_requests = max(0, int(max_requests))
        self._clock = clock
        self._lock = threading.Lock()
        self._active_calls_lock = threading.Lock()
        # Orders seal-intent publication against a new local dispatch commit.
        # This lock is released before conn.request(), so seal can publish while
        # already-committed synchronous I/O is still in progress.
        self._dispatch_gate_lock = threading.Lock()
        self._requests = 0
        self._auth_status: int | None = None
        self._auth_stage: str | None = None
        self._active_provider_calls: dict[str, int] = {}
        self._sealed = False
        self._seal_requested = threading.Event()

    @property
    def requests(self) -> int:
        # A plain integer read keeps timeout diagnostics available while the
        # state lock is held across an in-flight synchronous send.
        return self._requests

    @property
    def deadline_expired(self) -> bool:
        with self._lock:
            return self._clock() >= self.deadline_monotonic

    def cooperative_remaining_seconds(self) -> float:
        """Read the immutable Run deadline without waiting on an active send."""
        return max(0.0, self.deadline_monotonic - self._clock())

    @property
    def can_start_request(self) -> bool:
        with self._lock:
            with self._dispatch_gate_lock:
                return not self._seal_requested.is_set() and not self._sealed and self._auth_status is None and self._requests < self.max_requests and self._clock() < self.deadline_monotonic

    @property
    def exhausted(self) -> bool:
        return not self.can_start_request

    @property
    def sealed(self) -> bool:
        if self._seal_requested.is_set():
            return True
        with self._lock:
            return self._sealed or self._seal_requested.is_set()

    def remaining_seconds(self) -> float:
        with self._lock:
            if self._sealed or self._seal_requested.is_set():
                raise self._sealed_error()
            if self._auth_status is not None:
                raise self._auth_error()
            remaining = self.deadline_monotonic - self._clock()
            if remaining <= 0 or self._requests >= self.max_requests:
                raise ProviderTransportError("TIMEOUT", retryable=False, message="LLM 配置测试超过预算")
            return remaining

    def deadline_remaining_seconds(self) -> float:
        """Return time left for an already-started request, ignoring quota."""
        with self._lock:
            remaining = self.deadline_monotonic - self._clock()
            if remaining <= 0:
                raise ProviderTransportError("TIMEOUT", retryable=False, message="LLM 配置测试超过预算")
            return remaining

    def timeout(self, maximum: float) -> float:
        return min(float(maximum), self.remaining_seconds())

    def reserve_request(self) -> None:
        """Atomically gate and count one request immediately before conn.request."""
        with self._lock:
            with self._dispatch_gate_lock:
                if self._sealed or self._seal_requested.is_set():
                    raise self._sealed_error()
                if self._auth_status is not None:
                    raise self._auth_error()
                if self._clock() >= self.deadline_monotonic or self._requests >= self.max_requests:
                    raise ProviderTransportError("TIMEOUT", retryable=False, message="LLM 配置测试超过预算")
                self._requests += 1

    def mark_auth_terminal(self, http_status: int, *, stage: str | None = None) -> None:
        if http_status not in {401, 403}:
            return
        with self._lock:
            with self._active_calls_lock:
                if self._auth_status is None:
                    self._auth_status = http_status
                    self._auth_stage = stage
                elif self._auth_stage is None and self._auth_status == http_status and stage is not None:
                    self._auth_stage = stage

    def begin_provider_call(self, stage: str) -> None:
        with self._lock:
            with self._dispatch_gate_lock:
                if self._sealed or self._seal_requested.is_set():
                    raise self._sealed_error()
                if self._auth_status is not None:
                    raise self._auth_error()
                if self._clock() >= self.deadline_monotonic:
                    raise ProviderTransportError("TIMEOUT", retryable=False, message="LLM 配置测试超过预算")
                with self._active_calls_lock:
                    self._active_provider_calls[stage] = self._active_provider_calls.get(stage, 0) + 1

    def end_provider_call(self, stage: str) -> None:
        with self._active_calls_lock:
            active = self._active_provider_calls.get(stage, 0)
            if active <= 1:
                self._active_provider_calls.pop(stage, None)
            else:
                self._active_provider_calls[stage] = active - 1

    @property
    def active_provider_calls(self) -> int:
        with self._active_calls_lock:
            return sum(self._active_provider_calls.values())

    @property
    def active_provider_stages(self) -> frozenset[str]:
        with self._active_calls_lock:
            return frozenset(stage for stage, count in self._active_provider_calls.items() if count)

    @property
    def auth_stage(self) -> str | None:
        with self._active_calls_lock:
            if self._auth_stage is not None:
                return self._auth_stage
            active_stages = [stage for stage, count in self._active_provider_calls.items() if count]
            return active_stages[0] if len(active_stages) == 1 else None

    @property
    def provider_call_state(self) -> tuple[bool, str | None, int, frozenset[str]]:
        """Snapshot unfinished invocations without waiting on synchronous I/O."""
        with self._active_calls_lock:
            stages = frozenset(stage for stage, count in self._active_provider_calls.items() if count)
            auth_stage = self._auth_stage
            if auth_stage is None and len(stages) == 1:
                auth_stage = next(iter(stages))
            return self._auth_status is not None, auth_stage, sum(self._active_provider_calls.values()), stages

    def seal(self) -> None:
        """Publish a nonblocking seal intent; committed synchronous I/O may finish."""
        # Publish intent before waiting on dispatch_request's lock. A request
        # already past its locked gate is committed; queued callers see this
        # event as soon as the current conn.request releases the lock.
        with self._dispatch_gate_lock:
            self._seal_requested.set()

    def seal_and_snapshot(self) -> tuple[bool, str | None, int, frozenset[str]]:
        """Atomically close dispatch and snapshot unfinished calls/auth state."""
        with self._dispatch_gate_lock:
            self._seal_requested.set()
        with self._lock:
            self._sealed = True
            with self._active_calls_lock:
                stages = frozenset(stage for stage, count in self._active_provider_calls.items() if count)
                auth_stage = self._auth_stage
                if auth_stage is None and len(stages) == 1:
                    auth_stage = next(iter(stages))
                return self._auth_status is not None, auth_stage, sum(self._active_provider_calls.values()), stages

    @property
    def auth_failed(self) -> bool:
        with self._lock:
            return self._auth_status is not None

    def _auth_error(self) -> ProviderTransportError:
        return ProviderTransportError(
            "HTTP_4XX", http_status=self._auth_status,
            retryable=False, message="模型服务认证或授权失败",
        )

    @staticmethod
    def _sealed_error() -> ProviderTransportError:
        return ProviderTransportError(
            "TIMEOUT", retryable=False, message="LLM 配置测试已终结",
        )

    def dispatch_request(self, connection: http.client.HTTPSConnection, *,
                         method: str, path: str, body: bytes,
                         headers: Mapping[str, str], timeout: float) -> None:
        """Gate and enter the bounded send step atomically with auth latching."""
        with self._lock:
            with self._dispatch_gate_lock:
                if self._sealed or self._seal_requested.is_set():
                    raise self._sealed_error()
                if self._auth_status is not None:
                    raise self._auth_error()
                remaining = self.deadline_monotonic - self._clock()
                if remaining <= 0 or self._requests >= self.max_requests:
                    raise ProviderTransportError("TIMEOUT", retryable=False, message="LLM 配置测试超过预算")
                step_timeout = min(float(timeout), remaining)
                connection.timeout = step_timeout
                if connection.sock is not None:
                    connection.sock.settimeout(step_timeout)
                # This count is a conservative local commitment/attempt, not
                # evidence that the remote provider received the request.
                self._requests += 1
            # The gate has been released; retain state/auth ordering while
            # seal intent can publish during this synchronous send.
            connection.request(method, path, body=body, headers=dict(headers))


class ProviderResponseError(RuntimeError):
    """The provider returned a response outside the supported schema."""

    def __init__(self, message: str, *, category: str = "RESPONSE_SCHEMA") -> None:
        self.category = category if category in {"RESPONSE_SCHEMA", "RESPONSE_LIMIT"} else "RESPONSE_SCHEMA"
        super().__init__(message)


class _OpenAICompatibleClient:
    """Small stdlib client for the configured OpenAI-compatible endpoint.

    The runtime config deliberately contains only a server-side secret
    reference. Legacy environment-variable names remain supported; DPAPI
    references require an explicitly injected resolver and never fall back to
    environment lookup.
    """

    def __init__(self, *, secret_resolver: Callable[[str], str] | None = None,
                 approved_endpoints_resolver: Callable[[], str | None] | None = None) -> None:
        self.secret_resolver = secret_resolver
        self.approved_endpoints_resolver = approved_endpoints_resolver

    @staticmethod
    def _tools(value: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for item in value:
            name = item.get("name")
            if not isinstance(name, str) or not name:
                continue
            result.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": str(item.get("description") or ""),
                    "parameters": item.get("parameters") if isinstance(item.get("parameters"), Mapping) else {"type": "object", "properties": {}},
                },
            })
        return result

    @staticmethod
    def _endpoint(url: str) -> str:
        endpoint = url.rstrip("/")
        return endpoint if endpoint.endswith("/chat/completions") else endpoint + "/chat/completions"

    def _key(self, config: RuntimeConfigSnapshot) -> str:
        reference = config.key_reference.strip()
        if reference.startswith("dpapi:v1:"):
            if self.secret_resolver is None:
                raise unavailable("LLM Provider 密钥不可用")
            try:
                value = self.secret_resolver(reference)
            except Exception as exc:
                raise unavailable("LLM Provider 密钥不可用") from exc
            if not isinstance(value, str) or not value.strip():
                raise unavailable("LLM Provider 密钥不可用")
            return value
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", reference):
            raise unavailable("LLM Provider 密钥引用无效")
        value = os.environ.get(reference, "").strip()
        if not value:
            raise unavailable("LLM Provider 密钥不可用")
        return value

    def _trusted_url(self, config: RuntimeConfigSnapshot) -> str:
        if config.external_send_policy is None:
            return trusted_provider_url(config.url)
        if self.approved_endpoints_resolver is None:
            raise unavailable("LLM Provider 外发策略检查未装配")
        approved = self.approved_endpoints_resolver()
        return trusted_provider_url(
            config.url,
            external_send_policy=config.external_send_policy,
            approved_endpoints=approved,
        )

    @staticmethod
    def _resolved_public_ips(host: str, port: int) -> list[str]:
        """Resolve once and reject a mixed/public-private DNS answer.

        The request is subsequently connected to one of these exact addresses;
        this prevents a second resolver lookup from being used for the socket.
        """
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise ProviderTransportError("DNS", message="模型服务地址解析失败") from exc
        addresses: list[str] = []
        for _family, _kind, _proto, _canonname, sockaddr in infos:
            address = str(sockaddr[0])
            try:
                parsed = ipaddress.ip_address(address)
            except ValueError as exc:
                raise ProviderTransportError("DNS", message="模型服务地址解析无效") from exc
            if (parsed.is_private or parsed.is_loopback or parsed.is_link_local
                    or parsed.is_reserved or parsed.is_unspecified):
                raise ProviderTransportError("DNS", message="模型服务地址解析到非公网上址")
            if address not in addresses:
                addresses.append(address)
        if not addresses:
            raise ProviderTransportError("DNS", message="模型服务地址解析为空")
        return addresses

    @staticmethod
    def _request_once(*, hostname: str, address: str, port: int, path: str,
                      body: bytes, headers: Mapping[str, str], timeout: float,
                      method: str = "POST", allow_error_status: bool = False,
                      return_metadata: bool = False,
                      request_budget: ProviderRequestBudget | None = None) -> bytes | tuple[int, dict[str, str], bytes]:
        context = ssl.create_default_context()
        if request_budget is not None:
            timeout = request_budget.timeout(timeout)
        try:
            raw = socket.create_connection((address, port), timeout=timeout)
        except TimeoutError as exc:
            raise ProviderTransportError("TIMEOUT", message="模型服务连接超时") from exc
        except OSError as exc:
            raise ProviderTransportError("CONNECT", message="模型服务连接失败") from exc
        try:
            if request_budget is not None:
                try:
                    step_timeout = min(timeout, request_budget.remaining_seconds())
                except ProviderTransportError:
                    raw.close()
                    raise
                raw.settimeout(step_timeout)
            wrapped = context.wrap_socket(raw, server_hostname=hostname)
        except ssl.SSLError as exc:
            raw.close()
            text = str(exc).lower()
            certificate_failure = isinstance(exc, (ssl.SSLCertVerificationError, ssl.CertificateError)) or any(
                marker in text for marker in ("certificate", "hostname", "verify failed", "unknown ca", "bad certificate")
            )
            raise ProviderTransportError("TLS", retryable=not certificate_failure, message="模型服务 TLS 握手失败") from exc
        except OSError as exc:
            raw.close()
            raise ProviderTransportError("TLS", message="模型服务 TLS 握手失败") from exc
        conn = http.client.HTTPSConnection(hostname, port=port, timeout=timeout, context=context)
        conn.sock = wrapped
        try:
            try:
                if request_budget is not None:
                    request_budget.dispatch_request(
                        conn, method=method, path=path, body=body,
                        headers=headers, timeout=timeout,
                    )
                else:
                    conn.request(method, path, body=body, headers=dict(headers))
                if request_budget is not None:
                    conn.timeout = min(timeout, request_budget.deadline_remaining_seconds())
                    if conn.sock is not None:
                        conn.sock.settimeout(conn.timeout)
                response = conn.getresponse()
            except ProviderTransportError:
                raise
            except TimeoutError as exc:
                raise ProviderTransportError("TIMEOUT", message="模型服务请求超时") from exc
            except OSError as exc:
                raise ProviderTransportError("CONNECT", message="模型服务请求失败") from exc
            if response.status in {401, 403}:
                if request_budget is not None:
                    request_budget.mark_auth_terminal(response.status)
                raise ProviderTransportError(
                    "HTTP_4XX", http_status=response.status,
                    message="模型服务认证或授权失败",
                )
            if 300 <= response.status < 400:
                raise ProviderTransportError("HTTP_REDIRECT", http_status=response.status, message="模型服务禁止重定向")
            try:
                chunks: list[bytes] = []
                size = 0
                while True:
                    if request_budget is not None:
                        conn.sock.settimeout(min(timeout, request_budget.deadline_remaining_seconds()))
                    chunk = response.read(min(32 * 1024, 256 * 1024 + 1 - size))
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > 256 * 1024:
                        raise ProviderResponseError("模型服务响应超过大小限制")
                    chunks.append(chunk)
                payload = b"".join(chunks)
            except TimeoutError as exc:
                raise ProviderTransportError("TIMEOUT", message="模型服务响应超时") from exc
            except OSError as exc:
                raise ProviderTransportError("CONNECT", message="模型服务响应读取失败") from exc
            if response.status >= 400 and not allow_error_status:
                category = "HTTP_4XX" if response.status < 500 else "HTTP_5XX"
                raise ProviderTransportError(category, http_status=response.status,
                                              provider_error_code=_provider_error_code(payload))
            if return_metadata:
                return response.status, {str(k): str(v) for k, v in response.getheaders()}, payload
            return payload
        finally:
            conn.close()

    def complete(self, *, config: RuntimeConfigSnapshot, messages: list[Mapping[str, Any]], tools: list[Mapping[str, Any]], response_schema: Mapping[str, Any] | None = None, timeout_seconds: float | None = None, request_budget: ProviderRequestBudget | None = None) -> dict[str, Any]:
        del response_schema
        if config.configuration_state != "CONFIGURED":
            raise unavailable("LLM Provider 配置不可用")
        payload: dict[str, Any] = {"model": config.model_id, "messages": messages, "max_tokens": DEFAULT_MAX_OUTPUT_TOKENS}
        openai_tools = self._tools(tools)
        if openai_tools:
            payload["tools"] = openai_tools
            payload["tool_choice"] = "auto"
        endpoint = urlsplit(self._endpoint(self._trusted_url(config)))
        hostname = endpoint.hostname or ""
        port = endpoint.port or 443
        path = endpoint.path or "/"
        if endpoint.query or endpoint.fragment:
            raise ConnectionError("模型服务 URL 不允许 query/fragment")
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self._key(config)}"}
        timeout = timeout_seconds or 90.0
        if request_budget is not None:
            request_budget.remaining_seconds()
        raw = None
        last_error: Exception | None = None
        addresses = self._resolved_public_ips(hostname, port)
        if request_budget is not None:
            request_budget.remaining_seconds()
        for address in addresses:
            try:
                raw = self._request_once(hostname=hostname, address=address, port=port, path=path,
                                         body=body, headers=headers, timeout=timeout, request_budget=request_budget)
                break
            except (ConnectionError, TimeoutError, OSError) as exc:
                if isinstance(exc, ProviderTransportError) and (
                    exc.http_status in {401, 403} or (request_budget is not None and request_budget.sealed)
                ):
                    raise
                last_error = exc
        if raw is None:
            if isinstance(last_error, ProviderTransportError):
                raise last_error
            raise ProviderTransportError("CONNECT") from last_error
        try:
            document = json.loads(raw.decode("utf-8"))
            choices = document.get("choices") if isinstance(document, Mapping) else None
            first = choices[0] if isinstance(choices, list) and choices else None
            message = first.get("message") if isinstance(first, Mapping) else None
            if not isinstance(message, Mapping):
                raise TypeError("message schema")
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderResponseError("模型服务响应格式无效") from exc
        calls: list[dict[str, Any]] = []
        for item in message.get("tool_calls") or ():
            function = item.get("function") if isinstance(item, Mapping) else None
            if not isinstance(function, Mapping) or not isinstance(function.get("name"), str):
                raise ProviderResponseError("模型服务工具调用格式无效")
            arguments = function.get("arguments", {})
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments) if arguments.strip() else {}
                except ValueError as exc:
                    raise ProviderResponseError("模型服务工具参数格式无效") from exc
            if not isinstance(arguments, Mapping):
                raise ProviderResponseError("模型服务工具参数格式无效") from None
            calls.append({"id": str(item.get("id") or function["name"]), "name": function["name"], "arguments": dict(arguments)})
        usage = document.get("usage") if isinstance(document, Mapping) else {}
        usage = usage if isinstance(usage, Mapping) else {}
        structured = message.get("structured", message.get("output"))
        return {
            "text": message.get("content"),
            "structured": structured,
            "tool_calls": calls,
            "finish_reason": first.get("finish_reason"),
            "usage": {
                "input_tokens": usage.get("prompt_tokens"),
                "output_tokens": usage.get("completion_tokens"),
                "total_tokens": usage.get("total_tokens"),
            },
        }

    def stream(self, *, config: RuntimeConfigSnapshot, messages: list[Mapping[str, Any]], tools: list[Mapping[str, Any]], response_schema: Mapping[str, Any] | None = None, timeout_seconds: float | None = None, request_budget: ProviderRequestBudget | None = None) -> Iterable[dict[str, Any]]:
        """Consume one bounded OpenAI-compatible SSE response.

        The transport remains pinned to the resolved public addresses and
        redirects are rejected by ``_request_once``.  Parsing is bounded and
        accumulates fragmented tool-call fields before yielding one call.
        """
        del response_schema
        if config.configuration_state != "CONFIGURED":
            raise unavailable("LLM Provider 配置不可用")
        payload: dict[str, Any] = {
            "model": config.model_id, "messages": messages,
            "max_tokens": DEFAULT_MAX_OUTPUT_TOKENS, "stream": True,
        }
        openai_tools = self._tools(tools)
        if openai_tools:
            payload["tools"] = openai_tools
            payload["tool_choice"] = "auto"
        endpoint = urlsplit(self._endpoint(self._trusted_url(config)))
        hostname, port, path = endpoint.hostname or "", endpoint.port or 443, endpoint.path or "/"
        if endpoint.query or endpoint.fragment:
            raise ConnectionError("模型服务 URL 不允许 query/fragment")
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self._key(config)}", "Accept": "text/event-stream"}
        timeout = timeout_seconds or 90.0
        if request_budget is not None:
            request_budget.remaining_seconds()
        raw: bytes | None = None
        last_error: Exception | None = None
        addresses = self._resolved_public_ips(hostname, port)
        if request_budget is not None:
            request_budget.remaining_seconds()
        for address in addresses:
            try:
                raw = self._request_once(hostname=hostname, address=address, port=port, path=path,
                                         body=body, headers=headers, timeout=timeout, request_budget=request_budget)
                break
            except (ConnectionError, TimeoutError, OSError) as exc:
                if isinstance(exc, ProviderTransportError) and (
                    exc.http_status in {401, 403} or (request_budget is not None and request_budget.sealed)
                ):
                    raise
                last_error = exc
        if raw is None:
            if isinstance(last_error, ProviderTransportError):
                raise last_error
            raise ProviderTransportError("CONNECT") from last_error

        tool_calls: dict[int, dict[str, str]] = {}
        finish_reason: str | None = None
        usage: Mapping[str, Any] = {}
        done_marker = False
        for line in raw.decode("utf-8", errors="strict").splitlines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                done_marker = True
                continue
            if not data:
                continue
            try:
                event = json.loads(data)
            except ValueError as exc:
                raise ProviderResponseError("模型服务流响应格式无效") from exc
            choices = event.get("choices") if isinstance(event, Mapping) else None
            choice = choices[0] if isinstance(choices, list) and choices else None
            delta = choice.get("delta") if isinstance(choice, Mapping) else None
            if isinstance(delta, Mapping):
                content = delta.get("content")
                if isinstance(content, str) and content:
                    yield {"text": content}
                for call in delta.get("tool_calls") or ():
                    if not isinstance(call, Mapping):
                        continue
                    index = call.get("index")
                    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
                        continue
                    bucket = tool_calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                    if isinstance(call.get("id"), str):
                        bucket["id"] += call["id"]
                    function = call.get("function")
                    if isinstance(function, Mapping):
                        if isinstance(function.get("name"), str):
                            bucket["name"] += function["name"]
                        if isinstance(function.get("arguments"), str):
                            bucket["arguments"] += function["arguments"]
            if isinstance(choice, Mapping) and isinstance(choice.get("finish_reason"), str):
                finish_reason = choice["finish_reason"]
            if isinstance(event, Mapping) and isinstance(event.get("usage"), Mapping):
                usage = event["usage"]

        calls: list[dict[str, Any]] = []
        for _, value in sorted(tool_calls.items()):
            try:
                arguments = json.loads(value["arguments"] or "{}")
            except ValueError as exc:
                raise ProviderResponseError("模型服务工具参数格式无效") from exc
            if not isinstance(arguments, Mapping) or not value["name"]:
                raise ProviderResponseError("模型服务工具调用格式无效")
            calls.append({"id": value["id"] or value["name"], "name": value["name"], "arguments": dict(arguments)})
        yield {
            "tool_calls": calls,
            "finish_reason": finish_reason,
            "stream_complete": done_marker or finish_reason is not None,
            "usage": {
                "input_tokens": usage.get("prompt_tokens"),
                "output_tokens": usage.get("completion_tokens"),
                "total_tokens": usage.get("total_tokens"),
            },
        }


def secure_provider_request(url: str, *, method: str = "GET", body: bytes = b"",
                            headers: Mapping[str, str] | None = None,
                            timeout_seconds: float = 10.0,
                            allowed_hosts: str | None = None,
                            external_send_policy: str | None = None,
                            approved_endpoints: str | None = None) -> tuple[int, dict[str, str], bytes]:
    """Perform one pinned-IP provider request with no redirect handling."""
    trusted = trusted_provider_url(
        str(url), allowed_hosts=allowed_hosts,
        external_send_policy=external_send_policy,
        approved_endpoints=approved_endpoints,
    )
    endpoint = urlsplit(trusted.rstrip("/"))
    hostname = endpoint.hostname or ""
    port = endpoint.port or 443
    path = endpoint.path or "/"
    if endpoint.query or endpoint.fragment:
        raise ConnectionError("模型服务 URL 不允许 query/fragment")
    last_error: Exception | None = None
    for address in _OpenAICompatibleClient._resolved_public_ips(hostname, port):
        try:
            result = _OpenAICompatibleClient._request_once(
                hostname=hostname, address=address, port=port, path=path,
                body=body, headers=headers or {}, timeout=timeout_seconds,
                method=method, allow_error_status=True, return_metadata=True,
            )
            return result
        except (ConnectionError, TimeoutError, OSError) as exc:
            last_error = exc
    if isinstance(last_error, ProviderTransportError):
        raise last_error
    raise ProviderTransportError("CONNECT") from last_error


class PydanticAIAdapter:
    """Thin provider port adapter with an injectable client for tests."""

    dependency = "pydantic_ai"

    def __init__(self, *, client: object | None = None, import_name: str | None = None,
                 secret_resolver: Callable[[str], str] | None = None,
                 approved_endpoints_resolver: Callable[[], str | None] | None = None) -> None:
        self.client, self.import_name, self.secret_resolver = client, import_name, secret_resolver
        self.approved_endpoints_resolver = approved_endpoints_resolver
        self._module: object | None = None

    def _require_dependency(self) -> object:
        if self.client is not None:
            return self.client
        try:
            self._module = importlib.import_module(self.import_name)
        except ModuleNotFoundError as exc:
            raise unavailable(f"PydanticAI adapter unavailable: optional dependency {self.import_name!r} is not installed") from exc
        except Exception as exc:
            raise unavailable(f"PydanticAI adapter unavailable: cannot load {self.import_name!r}") from exc
        return self._module

    @staticmethod
    def _safe_config(config: RuntimeConfigSnapshot | object) -> RuntimeConfigSnapshot:
        if isinstance(config, RuntimeConfigSnapshot):
            return config
        if isinstance(config, Mapping):
            return RuntimeConfigSnapshot.from_mapping(config)
        try:
            values = {name: getattr(config, name) for name in ("url", "model_id", "key_reference", "config_revision")}
            if hasattr(config, "configuration_state"):
                values["configuration_state"] = getattr(config, "configuration_state")
            if hasattr(config, "provider_configuration_hash"):
                values["provider_configuration_hash"] = getattr(config, "provider_configuration_hash")
            if any(not isinstance(value, str) for value in values.values()):
                raise ValueError("LLM 配置字段必须为字符串")
            return RuntimeConfigSnapshot(**values)
        except Exception as exc:
            raise AssistantError(400, "VALIDATION_FAILED", "LLM 配置快照无效") from exc

    def complete(self, *, config: RuntimeConfigSnapshot | object, messages: Iterable[Mapping[str, Any]], tools: Iterable[Mapping[str, Any]] = (), response_schema: Mapping[str, Any] | None = None, timeout_seconds: float | None = None, request_budget: ProviderRequestBudget | None = None) -> object:
        snapshot = self._safe_config(config)
        client = self.client
        if client is None:
            if self.import_name is not None:
                self._require_dependency()
            client = _OpenAICompatibleClient(
                secret_resolver=self.secret_resolver,
                approved_endpoints_resolver=self.approved_endpoints_resolver,
            )
        elif snapshot.external_send_policy is not None:
            if self.approved_endpoints_resolver is None:
                raise unavailable("LLM Provider 外发策略检查未装配")
            approved = self.approved_endpoints_resolver()
            trusted_provider_url(snapshot.url, external_send_policy=snapshot.external_send_policy, approved_endpoints=approved)
        method = getattr(client, "complete", None) or getattr(client, "run", None)
        if callable(method):
            kwargs = dict(config=snapshot, messages=list(messages), tools=list(tools), response_schema=response_schema, timeout_seconds=timeout_seconds)
            if request_budget is not None:
                kwargs["request_budget"] = request_budget
            return method(**kwargs)
        # A real PydanticAI integration is deliberately kept behind the
        # optional package and an injected factory. Never silently use a mock.
        raise unavailable("PydanticAI adapter loaded but no supported provider client is configured")

    def stream(self, *, config: RuntimeConfigSnapshot | object, messages: Iterable[Mapping[str, Any]], tools: Iterable[Mapping[str, Any]] = (), response_schema: Mapping[str, Any] | None = None, timeout_seconds: float | None = None, request_budget: ProviderRequestBudget | None = None) -> Iterable[object]:
        snapshot = self._safe_config(config)
        client = self.client
        if client is None:
            if self.import_name is not None:
                self._require_dependency()
            client = _OpenAICompatibleClient(
                secret_resolver=self.secret_resolver,
                approved_endpoints_resolver=self.approved_endpoints_resolver,
            )
        elif snapshot.external_send_policy is not None:
            if self.approved_endpoints_resolver is None:
                raise unavailable("LLM Provider 外发策略检查未装配")
            approved = self.approved_endpoints_resolver()
            trusted_provider_url(snapshot.url, external_send_policy=snapshot.external_send_policy, approved_endpoints=approved)
        method = getattr(client, "stream", None)
        if callable(method):
            kwargs = dict(config=snapshot, messages=list(messages), tools=list(tools), response_schema=response_schema, timeout_seconds=timeout_seconds)
            if request_budget is not None:
                kwargs["request_budget"] = request_budget
            return method(**kwargs)
        raise unavailable("PydanticAI adapter loaded but streaming is unavailable")

    async def run_agent_async(self, *, config: RuntimeConfigSnapshot | object,
                              messages: Iterable[Mapping[str, Any]],
                              tools: Iterable[Mapping[str, Any] | object] = (),
                              execute_tool: Callable[..., Any],
                              validate_tool: Callable[..., Any] | None,
                              on_event: Callable[[Mapping[str, Any]], Any] | None,
                              history_state: Mapping[str, Any] | None,
                              request_budget: ProviderRequestBudget,
                              timeout_seconds: float = 90.0,
                              max_turns: int = 12, max_retries: int = 2) -> dict[str, Any]:
        """Run the native async Agent, keeping framework objects inside this boundary."""
        return await _run_native_agent(
            self, config=config, messages=messages, tools=tools,
            execute_tool=execute_tool, validate_tool=validate_tool,
            on_event=on_event, history_state=history_state,
            request_budget=request_budget, timeout_seconds=timeout_seconds,
            max_turns=max_turns, max_retries=max_retries,
        )

    def complete_deferred_history(self, *, history_state: Mapping[str, Any],
                                  tool_call_id: str, trusted_result: Any) -> dict[str, Any]:
        """Attach one platform-confirmed result to native history without resuming the Agent."""
        return complete_deferred_history(history_state, tool_call_id, trusted_result)

    async def close_async_clients(self) -> None:
        """Close cached native clients for this adapter's current event loop."""
        await close_native_clients()


def _plain(value: Any, *, depth: int = 0) -> Any:
    """Convert framework/platform values to bounded JSON-compatible values."""
    if depth > 16:
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _plain(item, depth=depth + 1) for key, item in value.items() if isinstance(key, (str, int))}
    if isinstance(value, (list, tuple)):
        return [_plain(item, depth=depth + 1) for item in value]
    if is_dataclass(value):
        return _plain(asdict(value), depth=depth + 1)
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return _plain(dump(mode="json"), depth=depth + 1)
    return str(value)[:4096]


def _sse_usage_summary(payload: bytes) -> tuple[dict[str, Any], str | None]:
    usage: dict[str, Any] = {}
    finish_reason: str | None = None
    for line in payload.decode("utf-8", errors="replace").splitlines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            event = json.loads(data)
        except ValueError:
            continue
        if isinstance(event, Mapping):
            raw_usage = event.get("usage")
            if isinstance(raw_usage, Mapping):
                usage = {
                    "input_tokens": raw_usage.get("prompt_tokens"),
                    "output_tokens": raw_usage.get("completion_tokens"),
                    "total_tokens": raw_usage.get("total_tokens"),
                }
                prompt_details = raw_usage.get("prompt_tokens_details")
                if isinstance(prompt_details, Mapping) and isinstance(prompt_details.get("cached_tokens"), int):
                    usage["cached_input_tokens"] = prompt_details["cached_tokens"]
            choices = event.get("choices")
            choice = choices[0] if isinstance(choices, list) and choices else None
            if isinstance(choice, Mapping) and isinstance(choice.get("finish_reason"), str):
                finish_reason = choice["finish_reason"]
    return usage, finish_reason


class _SSEStreamLimits:
    """Bound raw SSE bytes, one event, and decoded streamed answer fields."""

    wire_limit = 2 * 1024 * 1024
    event_limit = 256 * 1024
    content_limit = 256 * 1024

    def __init__(self) -> None:
        self.wire_size = 0
        self.event_size = 0
        self.content_size = 0
        self.line = bytearray()
        self.data_lines: list[bytes] = []
        self.pending_cr = False
        self.pending_cr_event_size = 0

    @staticmethod
    def _field_size(value: Any) -> int:
        return len(value.encode("utf-8")) if isinstance(value, str) else 0

    def _count_event_content(self) -> None:
        data = b"\n".join(self.data_lines)
        if not data or data == b"[DONE]":
            self.data_lines.clear()
            return
        try:
            event = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self.data_lines.clear()
            return
        if isinstance(event, Mapping):
            choices = event.get("choices")
            if isinstance(choices, list):
                for choice in choices:
                    if not isinstance(choice, Mapping):
                        continue
                    delta = choice.get("delta")
                    if not isinstance(delta, Mapping):
                        continue
                    size = sum(self._field_size(delta.get(key)) for key in (
                        "content", "reasoning", "reasoning_content", "refusal",
                    ))
                    for call in delta.get("tool_calls", ()) if isinstance(delta.get("tool_calls"), list) else ():
                        if not isinstance(call, Mapping):
                            continue
                        function = call.get("function")
                        if isinstance(function, Mapping):
                            size += self._field_size(function.get("name"))
                            size += self._field_size(function.get("arguments"))
                    function_call = delta.get("function_call")
                    if isinstance(function_call, Mapping):
                        size += self._field_size(function_call.get("name"))
                        size += self._field_size(function_call.get("arguments"))
                    self.content_size += size
                    if self.content_size > self.content_limit:
                        raise ProviderResponseError("模型服务响应超过大小限制", category="RESPONSE_LIMIT")
        self.data_lines.clear()

    def _finish_line(self) -> None:
        line = bytes(self.line)
        self.line.clear()
        if not line:
            self._count_event_content()
            self.event_size = 0
        elif line.startswith(b"data:"):
            value = line[5:]
            self.data_lines.append(value[1:] if value.startswith(b" ") else value)

    def feed(self, chunk: bytes) -> None:
        self.wire_size += len(chunk)
        if self.wire_size > self.wire_limit:
            raise ProviderResponseError("模型服务响应超过大小限制", category="RESPONSE_LIMIT")
        for value in chunk:
            if self.pending_cr and value == 10:
                # A CR may already have dispatched/reset the event. Its paired
                # LF still belongs to that event's raw byte limit.
                if self.pending_cr_event_size + 1 > self.event_limit:
                    raise ProviderResponseError("模型服务响应超过大小限制", category="RESPONSE_LIMIT")
                if self.event_size:
                    self.event_size += 1
                self.pending_cr = False
                continue
            self.pending_cr = False
            self.event_size += 1
            if self.event_size > self.event_limit:
                raise ProviderResponseError("模型服务响应超过大小限制", category="RESPONSE_LIMIT")
            if value == 13:
                self.pending_cr_event_size = self.event_size
                self._finish_line()
                self.pending_cr = True
            elif value == 10:
                self._finish_line()
            else:
                self.line.append(value)

    def finish(self) -> None:
        if self.line or self.data_lines:
            self._finish_line()
            self._count_event_content()


def _transport_error_summary(error: Exception) -> dict[str, Any]:
    if isinstance(error, ProviderTransportError):
        return error.safe_details()
    if isinstance(error, ProviderResponseError):
        return {"category": error.category}
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
        return {"category": "TIMEOUT"}
    if isinstance(error, ssl.SSLError):
        return {"category": "TLS"}
    if isinstance(error, ConnectionError):
        return {"category": "CONNECT"}
    try:
        import httpx

        if isinstance(error, httpx.TimeoutException):
            return {"category": "TIMEOUT"}
        if isinstance(error, httpx.TransportError):
            return {"category": "CONNECT"}
    except ImportError:
        pass
    return {"category": "UNKNOWN"}


def _utc_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


async def _emit(callback: Callable[[Mapping[str, Any]], Any] | None,
                event: Mapping[str, Any]) -> Any:
    if callback is None:
        return None
    result = await _maybe_await(callback(dict(event)))
    if result is False:
        raise AssistantError(409, "RUN_FENCED", "助手运行已失效")
    return result


def _tool_specs(tools: Iterable[Mapping[str, Any] | object]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for tool in tools:
        if isinstance(tool, Mapping):
            name = tool.get("name")
            description = tool.get("description") or ""
            schema = tool.get("parameters", tool.get("argument_schema", {}))
        else:
            name = getattr(tool, "name", None)
            description = getattr(tool, "description", "")
            schema = getattr(tool, "argument_schema", {})
        if not isinstance(name, str) or not name:
            continue
        if not isinstance(schema, Mapping):
            schema = {"type": "object", "properties": {}}
        result.append({"name": name, "description": str(description), "parameters": _plain(schema)})
    return result


def _endpoint_binding(snapshot: RuntimeConfigSnapshot) -> str:
    endpoint = str(snapshot.url).strip().rstrip("/")
    if endpoint.endswith("/chat/completions"):
        endpoint = endpoint[:-len("/chat/completions")]
    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()


def _history_state(messages: Any, *, snapshot: RuntimeConfigSnapshot,
                   framework_version: str, pending_actions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    from pydantic_ai.messages import ModelMessagesTypeAdapter

    provider_hash = getattr(snapshot, "provider_configuration_hash", None)
    binding = {
        "model_id": snapshot.model_id,
        "config_revision": snapshot.config_revision,
        "endpoint_sha256": _endpoint_binding(snapshot),
    }
    if isinstance(provider_hash, str) and provider_hash:
        binding["provider_configuration_hash"] = provider_hash
    return {
        "framework": "pydantic-ai",
        "framework_version": framework_version,
        "serialization": "ModelMessagesTypeAdapter",
        "config_binding": binding,
        "messages": ModelMessagesTypeAdapter.dump_python(list(messages), mode="json"),
        "pending_actions": _plain(pending_actions or []),
    }


def _completed_history_messages(prior_state: Mapping[str, Any]) -> list[Any]:
    """Import only completed conversational evidence from another config's trusted native history."""
    try:
        from pydantic_ai.messages import (
            ModelMessagesTypeAdapter, ModelRequest, ModelResponse, TextPart,
            ToolCallPart, ToolReturnPart, UserPromptPart,
        )
        validated = validate_native_history_state(prior_state)
        native = ModelMessagesTypeAdapter.validate_python(validated["messages"])
    except Exception as exc:
        raise AssistantError(409, "CONFLICT", "助手会话历史无效") from exc
    call_ids = {
        part.tool_call_id
        for message in native if isinstance(message, ModelResponse)
        for part in message.parts if isinstance(part, ToolCallPart)
    }
    return_ids = {
        part.tool_call_id
        for message in native if isinstance(message, ModelRequest)
        for part in message.parts if isinstance(part, ToolReturnPart)
    }
    paired_ids = call_ids & return_ids
    imported: list[Any] = []
    for message in native:
        if isinstance(message, ModelRequest):
            parts = [
                part for part in message.parts
                if isinstance(part, UserPromptPart)
                or (isinstance(part, ToolReturnPart) and part.tool_call_id in paired_ids)
            ]
            if parts:
                imported.append(ModelRequest(parts=parts))
        elif isinstance(message, ModelResponse):
            parts = [
                part for part in message.parts
                if isinstance(part, TextPart)
                or (isinstance(part, ToolCallPart) and part.tool_call_id in paired_ids)
            ]
            if parts:
                imported.append(ModelResponse(parts=parts))
    return imported


async def _run_native_agent(adapter: PydanticAIAdapter, *, config: RuntimeConfigSnapshot | object,
                            messages: Iterable[Mapping[str, Any]],
                            tools: Iterable[Mapping[str, Any] | object],
                            execute_tool: Callable[..., Any],
                            validate_tool: Callable[..., Any] | None,
                            on_event: Callable[[Mapping[str, Any]], Any] | None,
                            history_state: Mapping[str, Any] | None,
                            request_budget: ProviderRequestBudget,
                            timeout_seconds: float, max_turns: int,
                            max_retries: int) -> dict[str, Any]:
    try:
        module = importlib.import_module("pydantic_ai")
        messages_module = importlib.import_module("pydantic_ai.messages")
        toolsets_module = importlib.import_module("pydantic_ai.toolsets")
        tools_module = importlib.import_module("pydantic_ai.tools")
        from pydantic_core import SchemaValidator, core_schema
    except Exception as exc:
        raise unavailable("PydanticAI adapter unavailable") from exc

    snapshot = adapter._safe_config(config)
    if snapshot.configuration_state != "CONFIGURED":
        raise unavailable("LLM Provider 配置不可用")
    if not callable(execute_tool):
        raise AssistantError(400, "VALIDATION_FAILED", "助手工具执行端口未配置")
    specs = _tool_specs(tools)
    if specs and not callable(validate_tool):
        raise AssistantError(400, "VALIDATION_FAILED", "助手工具校验端口未配置")
    input_messages = [dict(item) for item in messages if isinstance(item, Mapping)]
    user_prompts = [str(item.get("content") or "") for item in input_messages if item.get("role") == "user"]
    if not user_prompts:
        raise AssistantError(400, "VALIDATION_FAILED", "助手运行缺少用户消息")
    user_prompt = user_prompts[-1]
    current_user_index = max(index for index, item in enumerate(input_messages) if item.get("role") == "user")
    instructions = [str(item.get("content") or "") for item in input_messages if item.get("role") == "system"]
    if not instructions:
        instructions = ["你是色谱业务助手，只调用服务端提供的已授权工具；不得声称未完成的业务操作已完成。"]

    expected_binding = {
        "model_id": snapshot.model_id,
        "config_revision": snapshot.config_revision,
        "endpoint_sha256": _endpoint_binding(snapshot),
    }
    provider_hash = getattr(snapshot, "provider_configuration_hash", None)
    if isinstance(provider_hash, str) and provider_hash:
        expected_binding["provider_configuration_hash"] = provider_hash
    previous_native_messages: list[Any] = []
    if history_state is not None:
        state_wrapper = history_state if isinstance(history_state, Mapping) else {}
        fresh_context = state_wrapper.get("fresh_run") is True
        if fresh_context:
            prior = state_wrapper.get("prior_history_state")
            if isinstance(prior, Mapping):
                previous_native_messages = _completed_history_messages(prior)
        else:
            if "history_state" in state_wrapper and "engine" in state_wrapper:
                wrapper_hash = state_wrapper.get("config_hash")
                if isinstance(provider_hash, str) and provider_hash and wrapper_hash != provider_hash:
                    raise AssistantError(409, "CONFLICT", "助手运行检查点与当前LLM配置不匹配")
                inner_state = state_wrapper.get("history_state")
            else:
                inner_state = state_wrapper
            try:
                inner_state = validate_native_history_state(inner_state)
            except AssistantError as exc:
                raise AssistantError(409, "CONFLICT", "助手原生历史与当前LLM配置不匹配") from exc
            binding = inner_state.get("config_binding")
            if dict(binding) != expected_binding:
                raise AssistantError(409, "CONFLICT", "助手原生历史与当前LLM配置不匹配")
            previous_native_messages = messages_module.ModelMessagesTypeAdapter.validate_python(inner_state["messages"])
    else:
        for item in input_messages[:current_user_index]:
            role = item.get("role")
            content = item.get("content")
            if role == "system":
                continue
            if role == "user":
                previous_native_messages.append(messages_module.ModelRequest(
                    parts=[messages_module.UserPromptPart(content=str(content or ""))],
                ))
            elif role == "assistant":
                parts: list[Any] = []
                if isinstance(content, str) and content:
                    parts.append(messages_module.TextPart(content=content))
                for call in item.get("tool_calls", ()) if isinstance(item.get("tool_calls", ()), (list, tuple)) else ():
                    if not isinstance(call, Mapping):
                        continue
                    function = call.get("function") if isinstance(call.get("function"), Mapping) else call
                    args = function.get("arguments", {})
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except ValueError:
                            continue
                    if isinstance(function.get("name"), str) and isinstance(args, Mapping):
                        parts.append(messages_module.ToolCallPart(
                            tool_name=function["name"], args=dict(args),
                            tool_call_id=str(call.get("id") or function["name"]),
                        ))
                if parts:
                    previous_native_messages.append(messages_module.ModelResponse(parts=parts))
            elif role == "tool":
                name = item.get("name")
                call_id = item.get("tool_call_id")
                if isinstance(name, str) and isinstance(call_id, str):
                    previous_native_messages.append(messages_module.ModelRequest(parts=[
                        messages_module.ToolReturnPart(
                            tool_name=name, tool_call_id=call_id,
                            content=str(content or ""),
                        ),
                    ]))

    pending_actions: list[dict[str, Any]] = []
    tool_results: list[Any] = []
    pending_unsupported = False
    tool_call_index = 0

    class _NativeToolset(toolsets_module.AbstractToolset):
        @property
        def id(self) -> str:
            return "chrom-platform"

        async def get_tools(self, _ctx: Any) -> dict[str, Any]:
            return {
                spec["name"]: toolsets_module.ToolsetTool(
                    toolset=self,
                    tool_def=tools_module.ToolDefinition(
                        name=spec["name"], description=spec["description"],
                        parameters_json_schema=spec["parameters"], sequential=True,
                    ),
                    max_retries=0,
                    args_validator=SchemaValidator(core_schema.dict_schema()),
                )
                for spec in specs
            }

        async def call_tool(self, name: str, tool_args: dict[str, Any], ctx: Any, _tool: Any) -> Any:
            nonlocal pending_unsupported, tool_call_index
            tool_call_index += 1
            args = _plain(tool_args)
            provider_id = getattr(ctx, "tool_call_id", None)
            if not isinstance(provider_id, str) or not provider_id:
                provider_id = f"pydantic-ai:{tool_call_index}:{hashlib.sha256(json.dumps([name, args], sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20]}"
            await _emit(on_event, {"type": "tool_call", "tool_call_id": provider_id, "name": name, "arguments": args})
            if pending_actions:
                pending_unsupported = True
                raise module.CallDeferred(metadata={"unsupported_multiple_pending": True, "tool_call_id": provider_id})
            try:
                validated = await _maybe_await(validate_tool(name, args, provider_id)) if validate_tool else args
                if isinstance(validated, Mapping):
                    args = _plain(validated)
                result = await _maybe_await(execute_tool(name, args, provider_id))
            except Exception as exc:
                await _emit(on_event, {"type": "error", "code": getattr(exc, "code", "TOOL_EXECUTION_FAILED"), "tool_call_id": provider_id, "name": name})
                raise
            from .tools import ToolExecution, ToolPending
            pending_value = isinstance(result, ToolPending) or (
                isinstance(result, Mapping) and result.get("status") == "WAITING_CONFIRMATION"
            )
            if pending_value:
                pending_result = result if isinstance(result, ToolPending) else result.get("pending_action", result.get("pending", result))
                if not isinstance(pending_result, (ToolPending, Mapping)):
                    raise AssistantError(502, "PROVIDER_PROTOCOL_ERROR", "待确认工具结果格式无效")
                if isinstance(pending_result, ToolPending):
                    pending = {
                        "tool_call_id": provider_id,
                        "tool_name": pending_result.tool_name,
                        "action": pending_result.action,
                        "revision": pending_result.revision or 1,
                        "config_hash": pending_result.config_hash or pending_result.target_revision_hash,
                        "resource_ref": _plain(pending_result.resource_ref),
                        "normalized_args": _plain(pending_result.normalized_args),
                        "target_revision_hash": pending_result.target_revision_hash,
                    }
                else:
                    pending = _plain(pending_result)
                    if pending.get("tool_call_id") not in (None, provider_id):
                        pending.setdefault("platform_tool_call_id", pending["tool_call_id"])
                    pending["tool_call_id"] = provider_id
                pending_actions.append(pending)
                tool_results.append({"status": "WAITING_CONFIRMATION", "pending": pending})
                raise module.CallDeferred(metadata={"pending_action": pending})
            tool_value = result.result if isinstance(result, ToolExecution) else result
            safe_result = _plain(tool_value)
            tool_results.append(safe_result)
            await _emit(on_event, {"type": "tool_result", "tool_call_id": provider_id, "name": name, "result": safe_result})
            return safe_result

    transport, openai_client = _native_openai_client(adapter, snapshot)
    observed_usage: dict[int, dict[str, Any]] = {}

    async def observe_transport_event(event: Mapping[str, Any]) -> Any:
        if (event.get("type") == "model_response" and event.get("response_end_kind") == "EOF"
                and event.get("error") is None and isinstance(event.get("request_index"), int)):
            raw_usage = event.get("usage")
            usage = raw_usage if isinstance(raw_usage, Mapping) else {}
            observed_usage[event["request_index"]] = {
                key: usage.get(key) if isinstance(usage.get(key), int) and not isinstance(usage.get(key), bool) else None
                for key in ("input_tokens", "output_tokens", "total_tokens", "cached_input_tokens")
            }
        return await _emit(on_event, event)

    run_context_token = transport.activate(request_budget, observe_transport_event, timeout_seconds, max_retries)
    try:
        provider = importlib.import_module("pydantic_ai.providers.openai").OpenAIProvider(openai_client=openai_client)
        openai_models = importlib.import_module("pydantic_ai.models.openai")

        class _SnapshotOpenAIStreamedResponse(openai_models.OpenAIStreamedResponse):
            def _map_usage(self, response: Any) -> Any:
                usage = super()._map_usage(response)
                if getattr(response, "usage", None) is not None:
                    self._usage = type(usage)()
                return usage

        class _SnapshotOpenAIChatModel(openai_models.OpenAIChatModel):
            @property
            def _streamed_response_cls(self) -> type[Any]:
                return _SnapshotOpenAIStreamedResponse

        model = _SnapshotOpenAIChatModel(snapshot.model_id, provider=provider)
        agent = module.Agent(
            model, output_type=[str, module.DeferredToolRequests],
            instructions=instructions, toolsets=[_NativeToolset()],
            retries=0, max_concurrency=1,
            model_settings={"max_tokens": DEFAULT_MAX_OUTPUT_TOKENS},
        )
        committed_text = ""
        response_text_parts: dict[int, str] = {}
        open_text_indices: set[int] = set()
        run_result = None
        before_dispatches = request_budget.requests

        def visible_text() -> str:
            return committed_text + "".join(response_text_parts[index] for index in sorted(response_text_parts))

        async def publish_text_update(previous: str, current: str, *, start: bool) -> None:
            if current == previous:
                if start:
                    await _emit(on_event, {"type": "text_start", "text": ""})
                return
            if current.startswith(previous):
                suffix = current[len(previous):]
                await _emit(on_event, {"type": "text_start" if start else "text_delta", "text": suffix})
            else:
                await _emit(on_event, {
                    "type": "replacement", "replace": True, "text": current,
                    "warning": "same_response_text_part_replaced",
                })

        async def finish_response_text() -> None:
            nonlocal committed_text
            if open_text_indices:
                open_text_indices.clear()
                await _emit(on_event, {"type": "text_end"})
            committed_text = visible_text()
            response_text_parts.clear()

        async with agent.run_stream_events(
            user_prompt=user_prompt,
            message_history=previous_native_messages,
            usage_limits=module.UsageLimits(request_limit=max(1, int(max_turns))),
        ) as event_stream:
            async for event in event_stream:
                event_name = getattr(event, "event_kind", "")
                if event_name == "agent_run_result":
                    run_result = event.result
                    continue
                if event_name in {"function_tool_call", "output_tool_call"}:
                    # Tool calls separate model responses. Part indexes are local
                    # to one response; PartEnd/FinalResultEvent are not boundaries.
                    await finish_response_text()
                    continue
                if event_name == "part_start":
                    part_index = int(getattr(event, "index", 0))
                    part = getattr(event, "part", None)
                    previous = visible_text()
                    if isinstance(part, messages_module.TextPart):
                        is_replacement = part_index in response_text_parts
                        response_text_parts[part_index] = part.content
                        open_text_indices.add(part_index)
                        await publish_text_update(previous, visible_text(), start=not is_replacement)
                    elif part_index in response_text_parts:
                        # A part replacement can change TextPart to a non-text
                        # response part; never expose its arguments as user text.
                        response_text_parts.pop(part_index, None)
                        open_text_indices.discard(part_index)
                        await publish_text_update(previous, visible_text(), start=False)
                elif event_name == "part_delta" and isinstance(getattr(event, "delta", None), messages_module.TextPartDelta):
                    part_index = int(getattr(event, "index", 0))
                    previous = visible_text()
                    response_text_parts[part_index] = response_text_parts.get(part_index, "") + event.delta.content_delta
                    await publish_text_update(previous, visible_text(), start=False)
                elif event_name == "part_end" and isinstance(getattr(event, "part", None), messages_module.TextPart):
                    open_text_indices.discard(int(getattr(event, "index", 0)))
                    if not open_text_indices:
                        await _emit(on_event, {"type": "text_end"})
        if run_result is None:
            raise AssistantError(502, "PROVIDER_PROTOCOL_ERROR", "模型服务未返回Agent终态事件")
        if pending_unsupported or len(pending_actions) > 1:
            raise AssistantError(400, "UNSUPPORTED_MULTIPLE_PENDING", "一次助手运行不支持多个待确认操作")
        output = run_result.output
        deferred = isinstance(output, module.DeferredToolRequests)
        text = None if deferred else output if isinstance(output, str) else None
        if isinstance(text, str):
            # Agent output is response-local; preserve earlier responses that
            # were sealed at tool-call boundaries without prefix guessing.
            final_text = committed_text + text
            if final_text != visible_text():
                await _emit(on_event, {"type": "replacement", "replace": True, "text": final_text})
            text = final_text
        if open_text_indices:
            await _emit(on_event, {"type": "text_end"})
        usage_obj = run_result.usage
        usage = {
            "requests": getattr(usage_obj, "requests", None),
            "input_tokens": getattr(usage_obj, "input_tokens", None),
            "output_tokens": getattr(usage_obj, "output_tokens", None),
            "total_tokens": getattr(usage_obj, "total_tokens", None),
            "cached_input_tokens": getattr(usage_obj, "cache_read_tokens", None),
        }
        if observed_usage:
            snapshots = list(observed_usage.values())
            for key in ("input_tokens", "output_tokens", "total_tokens", "cached_input_tokens"):
                values = [item[key] for item in snapshots]
                usage[key] = sum(values) if all(value is not None for value in values) else None
        native_messages = run_result.all_messages()
        state = _history_state(native_messages, snapshot=snapshot,
                               framework_version=getattr(module, "__version__", "2.56.0"),
                               pending_actions=pending_actions)
        return {
            "text": text,
            "structured": None,
            "tool_results": tool_results,
            "usage": usage,
            "provider_requests": usage.get("requests") or 0,
            "tool_calls": tool_call_index,
            "transport_attempts": max(0, request_budget.requests - before_dispatches),
            "framework_history": "pydantic-ai",
            "history_state": state,
            "finish_reason": getattr(run_result, "finish_reason", None),
            "pending": bool(deferred or pending_actions),
            "pending_actions": pending_actions,
        }
    finally:
        transport.deactivate(run_context_token)


def _native_openai_client(adapter: PydanticAIAdapter,
                          snapshot: RuntimeConfigSnapshot) -> tuple[Any, Any]:
    """Get a loop/config-isolated SDK client with pinned-IP async HTTP transport."""
    try:
        import httpcore
        import httpx
        from openai import AsyncOpenAI
    except Exception as exc:
        raise unavailable("PydanticAI OpenAI transport unavailable") from exc

    secure = _OpenAICompatibleClient(
        secret_resolver=adapter.secret_resolver,
        approved_endpoints_resolver=adapter.approved_endpoints_resolver,
    )
    trusted = secure._trusted_url(snapshot)
    endpoint = urlsplit(trusted.rstrip("/"))
    hostname, endpoint_port = endpoint.hostname or "", endpoint.port or 443
    if endpoint.query or endpoint.fragment:
        raise AssistantError(400, "VALIDATION_FAILED", "LLM服务地址不允许query或fragment")
    addresses = secure._resolved_public_ips(hostname, endpoint_port)
    endpoint_path = endpoint.path or ""
    if endpoint_path.endswith("/chat/completions"):
        base_url = trusted[: -len("/chat/completions")]
    else:
        base_url = trusted.rstrip("/")
    key = secure._key(snapshot)
    loop = asyncio.get_running_loop()
    config_digest = hashlib.sha256(json.dumps({
        "model_id": snapshot.model_id,
        "config_revision": snapshot.config_revision,
        "endpoint_sha256": _endpoint_binding(snapshot),
        "key_sha256": hashlib.sha256(key.encode("utf-8")).hexdigest(),
        "external_send_policy": snapshot.external_send_policy,
    }, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    registry_key = (loop, config_digest)
    with _NATIVE_CLIENTS_LOCK:
        existing = _NATIVE_CLIENTS.get(registry_key)
        if existing is not None and not getattr(existing[0], "closed", False):
            return existing[0], existing[1]
    ssl_context = ssl.create_default_context()
    io_backend = httpcore.AnyIOBackend()
    current_run: contextvars.ContextVar[Any] = contextvars.ContextVar(
        f"chrom_assistant_run_{id(loop)}_{config_digest[:12]}", default=None,
    )

    def run_context() -> tuple[ProviderRequestBudget, Callable[..., Any] | None, float, int, dict[str, int]]:
        value = current_run.get()
        if value is None:
            raise ProviderTransportError("TIMEOUT", retryable=False, message="模型服务运行上下文已结束")
        return value

    class _PinnedStream:
        def __init__(self, inner: Any) -> None:
            self.inner = inner

        async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
            return await self.inner.read(max_bytes, timeout=timeout)

        async def write(self, buffer: bytes, timeout: float | None = None) -> None:
            first_line = buffer.split(b"\r\n", 1)[0]
            first_line_parts = first_line.split(b" ")
            if len(first_line_parts) == 3 and first_line_parts[2].startswith(b"HTTP/"):
                budget, callback, _seconds, _retries, counter = run_context()
                dispatch_event = counter.get("dispatch_event")
                if isinstance(dispatch_event, dict) and not dispatch_event.get("emitted"):
                    await _emit(callback, dispatch_event["payload"])
                    dispatch_event["emitted"] = True
                    # No await may occur between committing the budget gate and
                    # starting the socket write. A seal published while the
                    # request trace is being persisted therefore prevents send.
                    budget.reserve_request()
            await self.inner.write(buffer, timeout=timeout)

        async def aclose(self) -> None:
            await self.inner.aclose()

        async def start_tls(self, ssl_context: Any, server_hostname: str | None = None,
                            timeout: float | None = None) -> "_PinnedStream":
            return _PinnedStream(await self.inner.start_tls(
                ssl_context, server_hostname=server_hostname, timeout=timeout,
            ))

        def get_extra_info(self, info: str) -> Any:
            return self.inner.get_extra_info(info)

    class _PinnedBackend:
        async def connect_tcp(self, host: str, port: int, timeout: float | None = None,
                              local_address: str | None = None,
                              socket_options: Any = None) -> Any:
            if host.lower() != hostname.lower() or port != endpoint_port:
                raise ProviderTransportError("CONNECT", retryable=False, message="模型服务目标与已批准地址不一致")
            last: Exception | None = None
            for address in addresses:
                try:
                    stream = await io_backend.connect_tcp(address, endpoint_port, timeout=timeout,
                                                          local_address=local_address,
                                                          socket_options=socket_options)
                    return _PinnedStream(stream)
                except Exception as exc:
                    last = exc
            raise ProviderTransportError("CONNECT", message="模型服务连接失败") from last

        async def connect_unix_socket(self, path: str, timeout: float | None = None,
                                      socket_options: Any = None) -> Any:
            raise ProviderTransportError("CONNECT", retryable=False, message="模型服务不支持Unix socket")

        async def sleep(self, seconds: float) -> None:
            await asyncio.sleep(seconds)

    pool = httpcore.AsyncConnectionPool(
        ssl_context=ssl_context, max_connections=1, max_keepalive_connections=1,
        http1=True, http2=False, retries=0, network_backend=_PinnedBackend(),
    )

    class _CoreStream(httpx.AsyncByteStream):
        def __init__(self, response: Any, *, on_close: Callable[[], None],
                     on_complete: Callable[[bytes | None, Mapping[str, Any] | None, str | None, str], Any]) -> None:
            self.response, self.on_close, self.on_complete, self.size = response, on_close, on_complete, 0
            self.closed = False
            self.reported = False
            self.chunks: list[bytes] = []
            self.limits = _SSEStreamLimits()

        async def __aiter__(self):
            try:
                async for chunk in self.response.stream:
                    self.limits.feed(chunk)
                    self.size = self.limits.wire_size
                    self.chunks.append(chunk)
                    yield chunk
                self.limits.finish()
                receive_complete_at = _utc_iso_now()
                await self._report(b"".join(self.chunks), None, receive_complete_at, "EOF")
            except GeneratorExit:
                await self._report(None, {"category": "STREAM_INTERRUPTED"}, None, "CLOSED_EARLY")
                raise
            except BaseException as exc:
                await self._report(None, _transport_error_summary(exc), None, "ERROR")
                raise
            finally:
                await self.aclose()

        async def _report(self, payload: bytes | None, error: Mapping[str, Any] | None,
                          receive_complete_at: str | None, response_end_kind: str) -> None:
            if self.reported:
                return
            self.reported = True
            await _maybe_await(self.on_complete(payload, error, receive_complete_at, response_end_kind))

        async def aclose(self) -> None:
            if self.closed:
                return
            self.closed = True
            try:
                if not self.reported:
                    await self._report(None, {"category": "STREAM_INTERRUPTED"}, None, "CLOSED_EARLY")
            finally:
                try:
                    await self.response.aclose()
                finally:
                    self.on_close()

    class _PinnedTransport(httpx.AsyncBaseTransport):
        def __init__(self) -> None:
            self.closed = False

        async def handle_async_request(self, request: Any) -> Any:
            budget, callback, timeout_seconds, max_retries, counter = run_context()
            counter["requests"] = counter.get("requests", 0) + 1
            request_index = counter["requests"]
            raw = await request.aread()
            try:
                decoded = json.loads(raw.decode("utf-8"))
            except Exception:
                decoded = {}
            core_request = httpcore.Request(
                method=request.method,
                url=httpcore.URL(scheme=request.url.scheme, host=request.url.host.encode("ascii"),
                                 port=request.url.port, target=request.url.raw_path),
                headers=request.headers.raw, content=raw,
            )
            last_error: Exception | None = None
            retry_limit = max(0, min(2, int(max_retries)))
            for network_try in range(retry_limit + 1):
                attempt_number = network_try + 1
                active_phases = budget.active_provider_stages
                stage = next(
                    (candidate for candidate in ("connectivity", "tool_calling", "streaming") if candidate in active_phases),
                    f"agent:{request_index}:{attempt_number}",
                )
                budget.begin_provider_call(stage)
                active = True
                response_reported = False
                dispatch_event = {"emitted": False, "payload": {
                    "type": "model_request", "request_index": request_index,
                    "transport_attempt": attempt_number, "model_id": snapshot.model_id,
                    "message_count": len(decoded.get("messages", [])) if isinstance(decoded, Mapping) else 0,
                    "tool_names": [item.get("function", {}).get("name") for item in decoded.get("tools", []) if isinstance(item, Mapping)] if isinstance(decoded, Mapping) else [],
                    "request_content_sha256": hashlib.sha256(raw).hexdigest(),
                }}
                counter["dispatch_event"] = dispatch_event
                try:
                    remaining = budget.remaining_seconds()
                    timeout = min(timeout_seconds, remaining)
                    core_request.extensions["timeout"] = {"connect": timeout, "read": timeout, "write": timeout, "pool": timeout}
                    response = await pool.handle_async_request(core_request)
                    if response.status in {401, 403}:
                        budget.mark_auth_terminal(response.status, stage=stage)
                        await response.aclose()
                        await _emit(callback, {
                            "type": "model_response", "request_index": request_index,
                            "transport_attempt": attempt_number, "status": response.status,
                            "error": {"category": "HTTP_4XX", "http_status": response.status},
                        })
                        response_reported = True
                        raise ProviderTransportError("HTTP_4XX", http_status=response.status,
                                                     message="模型服务认证或授权失败")
                    if 300 <= response.status < 400:
                        await response.aclose()
                        await _emit(callback, {
                            "type": "model_response", "request_index": request_index,
                            "transport_attempt": attempt_number, "status": response.status,
                            "error": {"category": "HTTP_REDIRECT", "http_status": response.status},
                        })
                        response_reported = True
                        raise ProviderTransportError("HTTP_REDIRECT", http_status=response.status,
                                                     retryable=False, message="模型服务禁止重定向")
                    if response.status >= 400:
                        chunks: list[bytes] = []
                        size = 0
                        async for chunk in response.stream:
                            size += len(chunk)
                            if size > 256 * 1024:
                                raise ProviderResponseError("模型服务响应超过大小限制")
                            chunks.append(chunk)
                        await response.aclose()
                        category = "HTTP_4XX" if response.status < 500 else "HTTP_5XX"
                        last_error = ProviderTransportError(category, http_status=response.status,
                                                            provider_error_code=_provider_error_code(b"".join(chunks)))
                        await _emit(callback, {
                            "type": "model_response", "request_index": request_index,
                            "transport_attempt": attempt_number, "status": response.status,
                            "error": last_error.safe_details(),
                        })
                        response_reported = True
                        if not last_error.retryable or network_try >= retry_limit:
                            raise last_error
                        budget.end_provider_call(stage)
                        active = False
                        continue

                    async def report_response(payload: bytes | None, error: Mapping[str, Any] | None,
                                              receive_complete_at: str | None, response_end_kind: str) -> None:
                        usage, finish_reason = _sse_usage_summary(payload or b"")
                        await _emit(callback, {
                            "type": "model_response", "request_index": request_index,
                            "transport_attempt": attempt_number, "status": response.status,
                            "error": dict(error) if isinstance(error, Mapping) else None,
                            "usage": usage, "finish_reason": finish_reason,
                            "receive_complete_at": receive_complete_at,
                            "response_end_kind": response_end_kind,
                        })

                    def closed() -> None:
                        budget.end_provider_call(stage)

                    return httpx.Response(
                        status_code=response.status, headers=response.headers,
                        stream=_CoreStream(response, on_close=closed, on_complete=report_response), request=request,
                    )
                except Exception as exc:
                    if active:
                        budget.end_provider_call(stage)
                        active = False
                    if isinstance(exc, (ProviderTransportError, ProviderResponseError, AssistantError)):
                        last_error = exc
                    else:
                        category = _transport_error_summary(exc).get("category")
                        last_error = (
                            ProviderTransportError(category, message="模型服务请求失败")
                            if category in {"TIMEOUT", "TLS", "CONNECT"}
                            else exc
                        )
                    if dispatch_event["emitted"] and not response_reported:
                        await _emit(callback, {
                            "type": "model_response", "request_index": request_index,
                            "transport_attempt": attempt_number,
                            "status": getattr(last_error, "http_status", None),
                            "error": _transport_error_summary(last_error),
                        })
                    if isinstance(last_error, ProviderTransportError) and last_error.retryable and network_try < retry_limit:
                        continue
                    raise last_error from exc
            raise last_error or ProviderTransportError("CONNECT")

        def activate(self, budget: ProviderRequestBudget, callback: Callable[..., Any] | None,
                     timeout: float, max_retries: int) -> Any:
            return current_run.set((budget, callback, max(0.001, float(timeout)), max_retries, {"requests": 0}))

        def deactivate(self, token: Any) -> None:
            current_run.reset(token)

        async def aclose(self) -> None:
            if not self.closed:
                self.closed = True
                await pool.aclose()

    transport = _PinnedTransport()
    # The per-run timeout is installed on each httpcore request from the active
    # context; this value is only the SDK's defensive default.
    http_client = httpx.AsyncClient(
        transport=transport, timeout=90.0, follow_redirects=False, trust_env=False,
    )
    sdk_client = AsyncOpenAI(api_key=key, base_url=base_url, http_client=http_client, max_retries=0)
    with _NATIVE_CLIENTS_LOCK:
        _NATIVE_CLIENTS[registry_key] = (transport, sdk_client, http_client)
    return transport, sdk_client


async def close_native_clients(*, loop: Any = None) -> None:
    """Close all cached PydanticAI/OpenAI clients for a loop, or all loops."""
    target_loop = loop or asyncio.get_running_loop()
    with _NATIVE_CLIENTS_LOCK:
        selected = [(key, value) for key, value in _NATIVE_CLIENTS.items()
                    if target_loop is None or key[0] is target_loop]
        for key, _value in selected:
            _NATIVE_CLIENTS.pop(key, None)
    for _key, (_transport, sdk_client, _http_client) in selected:
        await sdk_client.close()


def validate_native_history_state(history_state: Mapping[str, Any], *,
                                  expected_config_binding: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Validate and normalize the private native checkpoint without running an Agent or network call."""
    if not isinstance(history_state, Mapping) or history_state.get("framework") != _NATIVE_FRAMEWORK:
        raise AssistantError(409, "CONFLICT", "助手原生历史不可用")
    if history_state.get("serialization") != _NATIVE_SERIALIZATION:
        raise AssistantError(409, "CONFLICT", "助手原生历史序列化版本不支持")
    try:
        pydantic_ai = importlib.import_module("pydantic_ai")
        expected_version = str(getattr(pydantic_ai, "__version__", ""))
    except Exception as exc:
        raise unavailable("PydanticAI adapter unavailable") from exc
    if not expected_version or history_state.get("framework_version") != expected_version:
        raise AssistantError(409, "CONFLICT", "助手原生历史框架版本不支持")
    binding = history_state.get("config_binding")
    if not isinstance(binding, Mapping):
        raise AssistantError(409, "CONFLICT", "助手原生历史配置绑定无效")
    allowed_binding_keys = {"model_id", "config_revision", "endpoint_sha256", "provider_configuration_hash"}
    if set(binding) - allowed_binding_keys:
        raise AssistantError(409, "CONFLICT", "助手原生历史配置绑定无效")
    if (not isinstance(binding.get("model_id"), str) or not binding["model_id"]
            or not isinstance(binding.get("config_revision"), str) or not binding["config_revision"]
            or not isinstance(binding.get("endpoint_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", binding["endpoint_sha256"])):
        raise AssistantError(409, "CONFLICT", "助手原生历史配置绑定无效")
    provider_hash = binding.get("provider_configuration_hash")
    if provider_hash is not None and (not isinstance(provider_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", provider_hash)):
        raise AssistantError(409, "CONFLICT", "助手原生历史配置绑定无效")
    if expected_config_binding is not None and dict(binding) != dict(expected_config_binding):
        raise AssistantError(409, "CONFLICT", "助手原生历史与当前LLM配置不匹配")
    pending = history_state.get("pending_actions", [])
    if (not isinstance(pending, list) or len(pending) > 1
            or any(not isinstance(action, Mapping) for action in pending)):
        raise AssistantError(409, "CONFLICT", "助手原生历史待确认状态无效")
    if pending and (not isinstance(pending[0].get("tool_call_id"), str) or not pending[0]["tool_call_id"]):
        raise AssistantError(409, "CONFLICT", "助手原生历史待确认状态无效")
    try:
        messages_adapter = importlib.import_module("pydantic_ai.messages").ModelMessagesTypeAdapter
        native_messages = messages_adapter.validate_python(history_state.get("messages", []))
        serialized_messages = messages_adapter.dump_python(native_messages, mode="json")
    except Exception as exc:
        raise AssistantError(409, "CONFLICT", "助手原生历史无效") from exc
    return {
        "framework": _NATIVE_FRAMEWORK,
        "framework_version": expected_version,
        "serialization": _NATIVE_SERIALIZATION,
        "config_binding": _plain(binding),
        "messages": _plain(serialized_messages),
        "pending_actions": _plain(pending),
    }


def complete_deferred_history(history_state: Mapping[str, Any], tool_call_id: str,
                              trusted_result: Any) -> dict[str, Any]:
    """Append one trusted platform result to a deferred native tool call, without rerunning an agent."""
    if not isinstance(tool_call_id, str) or not tool_call_id:
        raise AssistantError(400, "VALIDATION_FAILED", "工具调用标识无效")
    try:
        from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart
        validated = validate_native_history_state(history_state)
        native = ModelMessagesTypeAdapter.validate_python(validated["messages"])
    except AssistantError:
        raise
    except Exception as exc:
        raise AssistantError(409, "CONFLICT", "助手原生历史无效") from exc
    matches = [part for message in native if isinstance(message, ModelResponse)
               for part in message.parts if isinstance(part, ToolCallPart) and part.tool_call_id == tool_call_id]
    if len(matches) != 1:
        raise AssistantError(409, "CONFLICT", "待确认工具调用与助手原生历史不匹配")
    existing_returns = [part for message in native if isinstance(message, ModelRequest)
                        for part in message.parts if isinstance(part, ToolReturnPart) and part.tool_call_id == tool_call_id]
    pending_actions = validated["pending_actions"]
    pending_matches = [action for action in pending_actions if action.get("tool_call_id") == tool_call_id]
    if existing_returns or len(pending_actions) != 1 or len(pending_matches) != 1:
        raise AssistantError(409, "CONFLICT", "待确认工具调用状态已完成或不匹配")
    call = matches[0]
    native.append(ModelRequest(parts=[ToolReturnPart(
        tool_name=call.tool_name, tool_call_id=tool_call_id,
        content=json.dumps(_plain(trusted_result), ensure_ascii=False, separators=(",", ":")),
    )]))
    updated = dict(validated)
    updated["messages"] = ModelMessagesTypeAdapter.dump_python(native, mode="json")
    updated["pending_actions"] = [action for action in validated.get("pending_actions", [])
                                  if not isinstance(action, Mapping) or action.get("tool_call_id") != tool_call_id]
    return _plain(updated)


AgentRuntimeAdapter = PydanticAIAdapter


__all__ = ["AgentRuntimeAdapter", "LLMProviderAdapter", "PydanticAIAdapter", "ProviderRequestBudget", "ProviderResponseError", "ProviderTransportError", "close_native_clients", "complete_deferred_history", "secure_provider_request"]
