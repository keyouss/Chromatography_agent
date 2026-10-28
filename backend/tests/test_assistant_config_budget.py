from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from chrom_platform.modules.assistant.adapter import (
    ProviderRequestBudget,
    ProviderTransportError,
    _OpenAICompatibleClient,
)
from chrom_platform.modules.assistant.production_worker import (
    AssistantLlmConfigurationTestWorker,
    _LlmTestClaim,
    _is_llm_test_auth_failure,
)
from chrom_platform.modules.assistant.runtime import RuntimeConfigSnapshot


def test_provider_request_budget_is_thread_safe_and_deadline_bounded():
    now = [1.0]
    budget = ProviderRequestBudget(deadline_monotonic=10.0, max_requests=5, clock=lambda: now[0])
    from concurrent.futures import ThreadPoolExecutor

    def reserve():
        try:
            budget.reserve_request()
            return True
        except ProviderTransportError:
            return False

    with ThreadPoolExecutor(max_workers=12) as pool:
        accepted = list(pool.map(lambda _: reserve(), range(12)))
    assert sum(accepted) == 5
    assert budget.requests == 5
    with pytest.raises(ProviderTransportError):
        budget.remaining_seconds()

    before_expiry = ProviderRequestBudget(deadline_monotonic=10.0, max_requests=5, clock=lambda: now[0])
    assert before_expiry.remaining_seconds() == 9.0
    now[0] = 10.0
    with pytest.raises(ProviderTransportError, match="预算"):
        before_expiry.reserve_request()


def test_auth_terminal_latch_blocks_dispatch_after_late_callback_without_canceling_sent_request():
    from threading import Event, Thread

    budget = ProviderRequestBudget(deadline_monotonic=20.0, max_requests=5, clock=lambda: 1.0)
    request_started = Event()
    latch_complete = Event()
    sent = []

    class FakeSocket:
        def settimeout(self, _value):
            pass

    class FakeConnection:
        sock = FakeSocket()
        timeout = None

        def request(self, *_args, **_kwargs):
            sent.append("in-flight")
            request_started.set()

    def late_provider_response():
        assert request_started.wait(2)
        budget.mark_auth_terminal(401)
        latch_complete.set()

    callback = Thread(target=late_provider_response)
    callback.start()
    budget.dispatch_request(
        FakeConnection(), method="POST", path="/test", body=b"{}",
        headers={}, timeout=5,
    )
    assert latch_complete.wait(2)
    callback.join(timeout=2)
    assert sent == ["in-flight"]
    assert budget.auth_failed is True
    assert budget.requests == 1

    with pytest.raises(ProviderTransportError) as caught:
        budget.dispatch_request(
            FakeConnection(), method="POST", path="/test", body=b"{}",
            headers={}, timeout=5,
        )
    assert caught.value.http_status == 401
    assert sent == ["in-flight"]


def test_late_dns_result_cannot_start_an_http_request(monkeypatch):
    now = [1.0]
    budget = ProviderRequestBudget(deadline_monotonic=2.0, max_requests=5, clock=lambda: now[0])
    requests = []

    def resolve(_host, _port):
        now[0] = 3.0
        return ["203.0.113.8", "203.0.113.9"]

    monkeypatch.setattr(_OpenAICompatibleClient, "_resolved_public_ips", staticmethod(resolve))
    monkeypatch.setattr(_OpenAICompatibleClient, "_request_once", staticmethod(lambda **kwargs: requests.append(kwargs)))
    client = _OpenAICompatibleClient(
        secret_resolver=lambda _ref: "fake-secret",
        approved_endpoints_resolver=lambda: "https://api.siliconflow.cn/v1",
    )
    config = RuntimeConfigSnapshot(
        "https://api.siliconflow.cn/v1", "model", "dpapi:v1:" + "a" * 32, "revision",
        external_send_policy="approved_endpoint_only",
        approved_endpoints="https://api.siliconflow.cn/v1",
    )
    with pytest.raises(ProviderTransportError):
        client.complete(
            config=config, messages=[{"role": "user", "content": "test"}],
            tools=[], request_budget=budget,
        )
    assert requests == []
    assert budget.requests == 0


def test_request_budget_is_checked_after_tls_before_conn_request(monkeypatch):
    now = [1.0]
    budget = ProviderRequestBudget(deadline_monotonic=2.0, max_requests=5, clock=lambda: now[0])
    calls = []

    class FakeSocket:
        def close(self):
            pass

        def settimeout(self, _timeout):
            pass

    class FakeContext:
        def wrap_socket(self, raw, *, server_hostname):
            now[0] = 2.0
            return raw

    class FakeConnection:
        def __init__(self, *_args, **_kwargs):
            self.sock = None

        def request(self, *_args, **_kwargs):
            calls.append("request")

        def close(self):
            pass

    monkeypatch.setattr("chrom_platform.modules.assistant.adapter.ssl.create_default_context", FakeContext)
    monkeypatch.setattr("chrom_platform.modules.assistant.adapter.socket.create_connection", lambda *_args, **_kwargs: FakeSocket())
    monkeypatch.setattr("chrom_platform.modules.assistant.adapter.http.client.HTTPSConnection", FakeConnection)
    with pytest.raises(ProviderTransportError):
        _OpenAICompatibleClient._request_once(
            hostname="provider.example", address="203.0.113.8", port=443,
            path="/v1/chat/completions", body=b"{}", headers={}, timeout=5,
            request_budget=budget,
        )
    assert calls == []
    assert budget.requests == 0


def test_connect_time_is_removed_from_budget_before_tls_handshake(monkeypatch):
    now = [1.0]
    budget = ProviderRequestBudget(deadline_monotonic=2.0, max_requests=5, clock=lambda: now[0])
    raw_timeouts = []
    handshake_calls = []

    class FakeSocket:
        def close(self):
            pass

        def settimeout(self, value):
            raw_timeouts.append(value)

    class FakeContext:
        def wrap_socket(self, raw, *, server_hostname):
            handshake_calls.append(server_hostname)
            return raw

    class FakeResponse:
        status = 200
        reads = 0

        def read(self, _size):
            self.reads += 1
            return b"ok" if self.reads == 1 else b""

        @staticmethod
        def getheaders():
            return []

    response = FakeResponse()

    class FakeConnection:
        def __init__(self, *_args, **_kwargs):
            self.sock = None

        @staticmethod
        def request(*_args, **_kwargs):
            pass

        @staticmethod
        def getresponse():
            return response

        @staticmethod
        def close():
            pass

    def connect(*_args, **_kwargs):
        now[0] = 1.5
        return FakeSocket()

    monkeypatch.setattr("chrom_platform.modules.assistant.adapter.ssl.create_default_context", FakeContext)
    monkeypatch.setattr("chrom_platform.modules.assistant.adapter.socket.create_connection", connect)
    monkeypatch.setattr("chrom_platform.modules.assistant.adapter.http.client.HTTPSConnection", FakeConnection)
    result = _OpenAICompatibleClient._request_once(
        hostname="provider.example", address="203.0.113.8", port=443,
        path="/v1/chat/completions", body=b"{}", headers={}, timeout=5,
        request_budget=budget,
    )
    assert result == b"ok"
    assert raw_timeouts[0] == pytest.approx(0.5)
    assert handshake_calls == ["provider.example"]


def test_connect_finishing_after_deadline_does_not_start_tls(monkeypatch):
    now = [1.0]
    budget = ProviderRequestBudget(deadline_monotonic=2.0, max_requests=5, clock=lambda: now[0])
    calls = []

    class FakeSocket:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

        def settimeout(self, _value):
            calls.append("socket-timeout")

    raw = FakeSocket()

    class FakeContext:
        def wrap_socket(self, *_args, **_kwargs):
            calls.append("tls")
            return raw

    def connect(*_args, **_kwargs):
        now[0] = 2.0
        return raw

    monkeypatch.setattr("chrom_platform.modules.assistant.adapter.ssl.create_default_context", FakeContext)
    monkeypatch.setattr("chrom_platform.modules.assistant.adapter.socket.create_connection", connect)
    with pytest.raises(ProviderTransportError):
        _OpenAICompatibleClient._request_once(
            hostname="provider.example", address="203.0.113.8", port=443,
            path="/v1/chat/completions", body=b"{}", headers={}, timeout=5,
            request_budget=budget,
        )
    assert calls == []
    assert raw.closed is True


def test_request_time_expiry_does_not_enter_getresponse(monkeypatch):
    now = [1.0]
    budget = ProviderRequestBudget(deadline_monotonic=2.0, max_requests=5, clock=lambda: now[0])
    calls = []

    class FakeSocket:
        def close(self):
            pass

        def settimeout(self, _value):
            pass

    class FakeContext:
        @staticmethod
        def wrap_socket(raw, *, server_hostname):
            return raw

    class FakeConnection:
        def __init__(self, *_args, **_kwargs):
            self.sock = None
            self.timeout = None

        def request(self, *_args, **_kwargs):
            calls.append("request")
            now[0] = 2.0

        @staticmethod
        def getresponse():
            calls.append("getresponse")
            raise AssertionError("must not block after deadline")

        @staticmethod
        def close():
            pass

    monkeypatch.setattr("chrom_platform.modules.assistant.adapter.ssl.create_default_context", FakeContext)
    monkeypatch.setattr("chrom_platform.modules.assistant.adapter.socket.create_connection", lambda *_args, **_kwargs: FakeSocket())
    monkeypatch.setattr("chrom_platform.modules.assistant.adapter.http.client.HTTPSConnection", FakeConnection)
    with pytest.raises(ProviderTransportError):
        _OpenAICompatibleClient._request_once(
            hostname="provider.example", address="203.0.113.8", port=443,
            path="/v1/chat/completions", body=b"{}", headers={}, timeout=5,
            request_budget=budget,
        )
    assert calls == ["request"]


def test_sealed_budget_rejects_conn_request_after_tls(monkeypatch):
    budget = ProviderRequestBudget(deadline_monotonic=20.0, max_requests=5, clock=lambda: 1.0)
    dispatched = []

    class FakeSocket:
        def settimeout(self, _timeout):
            pass

        def close(self):
            pass

    raw = FakeSocket()

    class FakeContext:
        def wrap_socket(self, socket, *, server_hostname):
            budget.seal()
            return socket

    class FakeConnection:
        def __init__(self, *_args, **_kwargs):
            self.sock = None
            self.timeout = None

        def request(self, *_args, **_kwargs):
            dispatched.append("request")

        def close(self):
            pass

    monkeypatch.setattr("chrom_platform.modules.assistant.adapter.ssl.create_default_context", FakeContext)
    monkeypatch.setattr("chrom_platform.modules.assistant.adapter.socket.create_connection", lambda *_args, **_kwargs: raw)
    monkeypatch.setattr("chrom_platform.modules.assistant.adapter.http.client.HTTPSConnection", FakeConnection)
    with pytest.raises(ProviderTransportError) as caught:
        _OpenAICompatibleClient._request_once(
            hostname="provider.example", address="203.0.113.8", port=443,
            path="/v1/chat/completions", body=b"{}", headers={}, timeout=5,
            request_budget=budget,
        )

    assert caught.value.http_status is None
    assert dispatched == []
    assert budget.requests == 0
    assert budget.auth_failed is False


def test_http_auth_headers_terminate_without_reading_body_or_trying_next_ip(monkeypatch):
    budget = ProviderRequestBudget(deadline_monotonic=20.0, max_requests=5, clock=lambda: 1.0)
    requests = []
    body_reads = []

    class FakeSocket:
        def close(self):
            pass

        def settimeout(self, _timeout):
            pass

    class FakeContext:
        @staticmethod
        def wrap_socket(raw, *, server_hostname):
            return raw

    class FakeResponse:
        status = 403

        def read(self, _size):
            body_reads.append(True)
            raise AssertionError("auth response body must not be read")

    response = FakeResponse()

    class FakeConnection:
        def __init__(self, *_args, **_kwargs):
            self.sock = None

        @staticmethod
        def request(*_args, **_kwargs):
            pass

        @staticmethod
        def getresponse():
            return response

        @staticmethod
        def close():
            pass

    def resolve(_host, _port):
        return ["203.0.113.8", "203.0.113.9"]

    monkeypatch.setattr("chrom_platform.modules.assistant.adapter.ssl.create_default_context", FakeContext)
    monkeypatch.setattr("chrom_platform.modules.assistant.adapter.socket.create_connection", lambda address, **_kwargs: (requests.append(address[0]) or FakeSocket()))
    monkeypatch.setattr("chrom_platform.modules.assistant.adapter.http.client.HTTPSConnection", FakeConnection)
    monkeypatch.setattr(_OpenAICompatibleClient, "_resolved_public_ips", staticmethod(resolve))
    client = _OpenAICompatibleClient(
        secret_resolver=lambda _ref: "fake-secret",
        approved_endpoints_resolver=lambda: "https://api.siliconflow.cn/v1",
    )
    config = RuntimeConfigSnapshot(
        "https://api.siliconflow.cn/v1", "model", "dpapi:v1:" + "c" * 32, "revision",
        external_send_policy="approved_endpoint_only",
        approved_endpoints="https://api.siliconflow.cn/v1",
    )
    with pytest.raises(ProviderTransportError) as caught:
        client.complete(config=config, messages=[{"role": "user", "content": "test"}], tools=[], request_budget=budget)
    assert caught.value.http_status == 403
    assert len(requests) == 1
    assert body_reads == []
    assert budget.auth_failed is True
    with pytest.raises(ProviderTransportError) as blocked:
        client.complete(config=config, messages=[{"role": "user", "content": "next"}], tools=[], request_budget=budget)
    assert blocked.value.http_status == 403
    assert len(requests) == 1


def test_slow_chunked_response_stops_reading_at_deadline(monkeypatch):
    now = [1.0]
    budget = ProviderRequestBudget(deadline_monotonic=2.0, max_requests=5, clock=lambda: now[0])

    class FakeSocket:
        def close(self):
            pass

        def settimeout(self, _timeout):
            pass

    class FakeContext:
        @staticmethod
        def wrap_socket(raw, *, server_hostname):
            return raw

    class FakeResponse:
        status = 200
        reads = 0

        def read(self, _size):
            self.reads += 1
            now[0] = 2.0
            return b"x"

        @staticmethod
        def getheaders():
            return []

    response = FakeResponse()

    class FakeConnection:
        def __init__(self, *_args, **_kwargs):
            self.sock = None

        @staticmethod
        def request(*_args, **_kwargs):
            pass

        @staticmethod
        def getresponse():
            return response

        @staticmethod
        def close():
            pass

    monkeypatch.setattr("chrom_platform.modules.assistant.adapter.ssl.create_default_context", FakeContext)
    monkeypatch.setattr("chrom_platform.modules.assistant.adapter.socket.create_connection", lambda *_args, **_kwargs: FakeSocket())
    monkeypatch.setattr("chrom_platform.modules.assistant.adapter.http.client.HTTPSConnection", FakeConnection)
    with pytest.raises(ProviderTransportError):
        _OpenAICompatibleClient._request_once(
            hostname="provider.example", address="203.0.113.8", port=443,
            path="/v1/chat/completions", body=b"{}", headers={}, timeout=5,
            request_budget=budget,
        )
    assert response.reads == 1
    assert budget.requests == 1


def test_fifth_reserved_request_can_still_read_its_response(monkeypatch):
    budget = ProviderRequestBudget(deadline_monotonic=20.0, max_requests=5, clock=lambda: 1.0)
    for _ in range(4):
        budget.reserve_request()
    socket_timeouts = []

    class FakeSocket:
        def close(self):
            pass

        def settimeout(self, value):
            socket_timeouts.append(value)

    class FakeContext:
        @staticmethod
        def wrap_socket(raw, *, server_hostname):
            return raw

    class FakeResponse:
        status = 200
        reads = 0

        def read(self, _size):
            self.reads += 1
            return b"ok" if self.reads == 1 else b""

    response = FakeResponse()

    class FakeConnection:
        def __init__(self, *_args, **_kwargs):
            self.sock = None

        @staticmethod
        def request(*_args, **_kwargs):
            pass

        @staticmethod
        def getresponse():
            return response

        @staticmethod
        def close():
            pass

    monkeypatch.setattr("chrom_platform.modules.assistant.adapter.ssl.create_default_context", FakeContext)
    monkeypatch.setattr("chrom_platform.modules.assistant.adapter.socket.create_connection", lambda *_args, **_kwargs: FakeSocket())
    monkeypatch.setattr("chrom_platform.modules.assistant.adapter.http.client.HTTPSConnection", FakeConnection)
    payload = _OpenAICompatibleClient._request_once(
        hostname="provider.example", address="203.0.113.8", port=443,
        path="/v1/chat/completions", body=b"{}", headers={}, timeout=5,
        request_budget=budget,
    )
    assert payload == b"ok"
    assert budget.requests == 5
    assert len(socket_timeouts) >= 2


def test_multi_ip_fallback_calls_never_dispatch_a_sixth_conn_request(monkeypatch):
    budget = ProviderRequestBudget(deadline_monotonic=50.0, max_requests=5, clock=lambda: 1.0)
    request_dispatches = []

    class FakeSocket:
        def __init__(self, address):
            self.address = address

        def close(self):
            pass

        def settimeout(self, _value):
            pass

    class FakeContext:
        @staticmethod
        def wrap_socket(raw, *, server_hostname):
            return raw

    class FakeResponse:
        status = 200

        def __init__(self):
            self.reads = 0

        def read(self, _size):
            self.reads += 1
            if self.reads == 1:
                return b'{"choices":[{"message":{"content":"ok"},"finish_reason":"stop"}]}'
            return b""

        @staticmethod
        def getheaders():
            return []

    class FakeConnection:
        def __init__(self, *_args, **_kwargs):
            self.sock = None

        def request(self, *_args, **_kwargs):
            request_dispatches.append(self.sock.address)
            if self.sock.address.endswith(".8"):
                raise OSError("simulated connection reset")

        @staticmethod
        def getresponse():
            return FakeResponse()

        @staticmethod
        def close():
            pass

    monkeypatch.setattr("chrom_platform.modules.assistant.adapter.ssl.create_default_context", FakeContext)
    monkeypatch.setattr("chrom_platform.modules.assistant.adapter.socket.create_connection", lambda address, **_kwargs: FakeSocket(address[0]))
    monkeypatch.setattr("chrom_platform.modules.assistant.adapter.http.client.HTTPSConnection", FakeConnection)
    monkeypatch.setattr(
        _OpenAICompatibleClient, "_resolved_public_ips",
        staticmethod(lambda _host, _port: ["203.0.113.8", "203.0.113.9"]),
    )
    client = _OpenAICompatibleClient(
        secret_resolver=lambda _ref: "fake-secret",
        approved_endpoints_resolver=lambda: "https://api.siliconflow.cn/v1",
    )
    config = RuntimeConfigSnapshot(
        "https://api.siliconflow.cn/v1", "model", "dpapi:v1:" + "e" * 32, "revision",
        external_send_policy="approved_endpoint_only",
        approved_endpoints="https://api.siliconflow.cn/v1",
    )
    call = lambda: client.complete(
        config=config, messages=[{"role": "user", "content": "test"}],
        tools=[], timeout_seconds=5, request_budget=budget,
    )
    assert call()["text"] == "ok"
    assert call()["text"] == "ok"
    with pytest.raises(ProviderTransportError):
        call()
    assert budget.requests == 5
    assert len(request_dispatches) == 5


def _test_config():
    return SimpleNamespace(
        id="cfg-budget", revision="revision-budget", url="https://api.siliconflow.cn/v1",
        model_id="model", key_reference="dpapi:v1:" + "b" * 32,
        settings={"external_send_policy": "approved_endpoint_only", "timeout_seconds": 2, "max_retries": 0},
    )


def _native_echo_result(execute_tool):
    return {
        "text": "echo checked",
        "tool_calls": 1,
        "tool_results": [execute_tool("assistant_test_echo", {"value": "chrom-platform-test"}, "echo-1")],
    }


async def _native_stream_event(on_event):
    await on_event({"type": "text_delta", "delta": "streamed"})


def test_tool_auth_is_terminal_and_skips_streaming(monkeypatch):
    from chrom_platform.modules.assistant import adapter as adapter_module

    class FakeAdapter:
        stream_calls = 0

        def __init__(self, **_kwargs):
            pass

        async def run_agent_async(self, *, tools, messages, execute_tool, **kwargs):
            prompt = messages[-1]["content"]
            if "connected" in prompt:
                return {"text": "connected"}
            if tools:
                raise ProviderTransportError("HTTP_4XX", http_status=401, message="private provider body")
            self.stream_calls += 1
            await _native_stream_event(kwargs["on_event"])
            return {"text": "should not happen"}

    monkeypatch.setattr(adapter_module, "PydanticAIAdapter", FakeAdapter)
    result = asyncio.run(AssistantLlmConfigurationTestWorker._test_adapter(
        _test_config(), secret_resolver=lambda _ref: "fake-secret",
        approved_endpoints_resolver=lambda: "https://api.siliconflow.cn/v1",
    ))
    assert result["status"] == "FAILED"
    assert result["checks"] == {
        "connectivity": "PASSED", "streaming": "SKIPPED",
        "tool_calling": "FAILED", "structured_output": "SKIPPED",
    }
    assert result["error"]["code"] == "PROVIDER_AUTHENTICATION_FAILED"
    assert "private provider body" not in str(result)


def test_stream_auth_is_terminal_and_preserves_prior_passed_checks(monkeypatch):
    from chrom_platform.modules.assistant import adapter as adapter_module

    class FakeAdapter:
        def __init__(self, **_kwargs):
            self.tool_turn = 0

        async def run_agent_async(self, *, tools, messages, execute_tool, **kwargs):
            prompt = messages[-1]["content"]
            if "connected" in prompt:
                return {"text": "connected"}
            if tools:
                self.tool_turn += 1
                return _native_echo_result(execute_tool)
            await _native_stream_event(kwargs["on_event"])
            raise ProviderTransportError("HTTP_4XX", http_status=403, message="private body")

    monkeypatch.setattr(adapter_module, "PydanticAIAdapter", FakeAdapter)
    result = asyncio.run(AssistantLlmConfigurationTestWorker._test_adapter(
        _test_config(), secret_resolver=lambda _ref: "fake-secret",
        approved_endpoints_resolver=lambda: "https://api.siliconflow.cn/v1",
    ))
    assert result["status"] == "FAILED"
    assert result["checks"] == {
        "connectivity": "PASSED", "tool_calling": "PASSED",
        "streaming": "FAILED", "structured_output": "NOT_SUPPORTED",
    }
    assert result["error"]["code"] == "PROVIDER_AUTHENTICATION_FAILED"
    assert "private body" not in str(result)


def test_shared_five_request_budget_covers_connectivity_runtime_and_stream(monkeypatch):
    from chrom_platform.modules.assistant import adapter as adapter_module

    class FakeAdapter:
        def __init__(self, **_kwargs):
            self.tool_turn = 0

        async def run_agent_async(self, *, messages, tools, request_budget, execute_tool, on_event, **_kwargs):
            prompt = messages[-1]["content"]
            if "connected" in prompt:
                request_budget.reserve_request()
                return {"text": "connected"}
            if tools:
                self.tool_turn += 1
                count = 3
                for _ in range(count):
                    request_budget.reserve_request()
                return _native_echo_result(execute_tool)
            request_budget.reserve_request()
            await _native_stream_event(on_event)
            return {"text": "streamed"}

        # Connectivity, the native tool turn (including the post-tool model
        # continuation), and streaming share the same five-request budget.

    monkeypatch.setattr(adapter_module, "PydanticAIAdapter", FakeAdapter)
    now = [1.0]
    budget = ProviderRequestBudget(deadline_monotonic=20.0, max_requests=5, clock=lambda: now[0])
    result = asyncio.run(AssistantLlmConfigurationTestWorker._test_adapter(
        _test_config(), secret_resolver=lambda _ref: "fake-secret",
        approved_endpoints_resolver=lambda: "https://api.siliconflow.cn/v1",
        request_budget=budget,
    ))
    assert result["status"] == "SUCCEEDED"
    assert budget.requests == 5


def test_late_finish_cannot_test_configuration_or_overwrite_deadline_failure():
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    job = SimpleNamespace(
        id="job-budget", project_id="project-budget", owner_id="owner",
        status="RUNNING", attempt=1, deadline_at=now - timedelta(seconds=1),
        revision=1, etag="1", updated_at=now, finished_at=None,
        result_ref=None, error=None,
    )
    attempt = SimpleNamespace(
        job_id=job.id, attempt=1, status="RUNNING", execution_token="token",
        fencing_token=2, finished_at=None, result_ref=None, error=None,
    )
    test = SimpleNamespace(
        id="test-budget", job_id=job.id, status="RUNNING", checks={}, error=None,
        finished_at=None, configuration_revision="revision", configuration_hash="a" * 64,
    )
    config = SimpleNamespace(
        id="cfg-budget", project_id=job.project_id, revision="revision",
        settings={"config_hash": "a" * 64}, status="CANDIDATE", last_test_id=None,
    )

    class FakeSession:
        def __init__(self):
            self.rows = iter((job, attempt, test, config, None))

        async def scalar(self, _statement):
            return next(self.rows)

        def add(self, _value):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def begin(self):
            return self

    claim = _LlmTestClaim(
        message=SimpleNamespace(job_id=job.id, attempt=1, dispatch_id="dispatch"),
        test_id=test.id, config=config, token="token", fence=2,
        deadline_at=now - timedelta(seconds=1),
    )
    worker = AssistantLlmConfigurationTestWorker(session_factory=FakeSession)
    finished = asyncio.run(worker._finish(claim, {
        "status": "SUCCEEDED",
        "checks": {"connectivity": "PASSED", "streaming": "PASSED", "tool_calling": "PASSED", "structured_output": "NOT_SUPPORTED"},
        "error": None,
    }))
    assert finished is True
    assert job.status == attempt.status == test.status == "FAILED"
    assert job.result_ref is None
    assert config.status == "CANDIDATE"
    repeated = asyncio.run(worker._finish(claim, {
        "status": "SUCCEEDED",
        "checks": {"connectivity": "PASSED", "streaming": "PASSED", "tool_calling": "PASSED", "structured_output": "NOT_SUPPORTED"},
        "error": None,
    }))
    assert repeated is False
    assert job.status == test.status == "FAILED"
    assert _is_llm_test_auth_failure({
        "connectivity": "PASSED", "streaming": "SKIPPED",
        "tool_calling": "FAILED", "structured_output": "SKIPPED",
    })
    assert not _is_llm_test_auth_failure({
        "connectivity": "PASSED", "streaming": "SKIPPED",
        "tool_calling": "PASSED", "structured_output": "SKIPPED",
    })


def test_test_job_has_fixed_deadline_and_cannot_be_retried_with_fresh_budget(monkeypatch):
    from datetime import timedelta
    from chrom_platform.core.dto import ActorContext
    from chrom_platform.modules.assistant import settings_service

    endpoint = "https://api.siliconflow.cn/v1"
    monkeypatch.setenv("LLM_PROVIDER_APPROVED_ENDPOINTS", endpoint)
    config = SimpleNamespace(
        id="cfg-submit", project_id="project-submit", revision="rev-submit",
        url=endpoint, status="CANDIDATE", model_id="model", key_reference="dpapi:v1:" + "d" * 32,
        settings={"config_hash": "d" * 64, "external_send_policy": "approved_endpoint_only"},
    )

    class FakeSession:
        def __init__(self):
            self.rows = []

        def scalar(self, _statement):
            return config

        def add(self, row):
            self.rows.append(row)

        def add_all(self, rows):
            self.rows.extend(rows)

        @staticmethod
        def flush():
            pass

    session = FakeSession()
    actor = ActorContext(
        actor_id="actor-submit", project_ids=["project-submit"], auth_method="session",
        permissions=["settings:write"], policy_version="test", default_project_id="project-submit",
    )
    settings_service.submit_test(session, actor, config.id, request_id="request-submit")
    job = next(row for row in session.rows if getattr(row, "kind", None) == "assistant_llm_configuration_test")
    assert job.retryable is False
    assert job.deadline_at - job.created_at == timedelta(seconds=90)


def test_timed_out_tool_daemon_still_active_prevents_stream_and_success(monkeypatch):
    import threading
    from chrom_platform.modules.assistant import adapter as adapter_module

    tool_started = threading.Event()
    release_tool = threading.Event()
    tool_finished = threading.Event()
    stream_calls = []
    errors = []

    class FakeAdapter:
        def __init__(self, **_kwargs):
            pass

        async def run_agent_async(self, *, tools, messages, request_budget, execute_tool, **_kwargs):
            prompt = messages[-1]["content"]
            if "connected" in prompt:
                return {"text": "connected"}
            if tools:
                request_budget.begin_provider_call("tool_calling")

                def old_tool_call():
                    tool_started.set()
                    try:
                        assert release_tool.wait(2)
                        error = ProviderTransportError("HTTP_4XX", http_status=401, message="private response")
                        errors.append(error)
                        request_budget.mark_auth_terminal(401, stage="tool_calling")
                    finally:
                        request_budget.end_provider_call("tool_calling")
                        tool_finished.set()

                threading.Thread(target=old_tool_call, daemon=True).start()
                assert tool_started.wait(2)
                return _native_echo_result(execute_tool)
            stream_calls.append(True)
            await _native_stream_event(_kwargs["on_event"])
            return {"text": "streamed"}

    monkeypatch.setattr(adapter_module, "PydanticAIAdapter", FakeAdapter)
    budget = ProviderRequestBudget(deadline_monotonic=100.0, max_requests=5, clock=lambda: 1.0)
    result = asyncio.run(AssistantLlmConfigurationTestWorker._test_adapter(
        _test_config(), secret_resolver=lambda _ref: "fake-secret",
        approved_endpoints_resolver=lambda: "https://api.siliconflow.cn/v1",
        request_budget=budget,
    ))

    assert result["status"] == "FAILED"
    assert result["checks"]["connectivity"] == "PASSED"
    assert result["checks"]["tool_calling"] == "FAILED"
    assert result["checks"]["streaming"] == "FAILED"
    assert stream_calls == []
    assert budget.active_provider_calls == 1
    release_tool.set()
    assert tool_finished.wait(2)
    assert len(errors) == 1 and errors[0].http_status == 401
    assert budget.active_provider_calls == 0
    assert budget.auth_failed is True
    assert budget.auth_stage == "tool_calling"
    assert "private response" not in str(result)


def test_late_tool_auth_after_stream_result_cannot_mark_configuration_tested():
    import threading
    import time
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    job = SimpleNamespace(
        id="job-late-auth", project_id="project-late-auth", owner_id="owner",
        status="RUNNING", attempt=1, deadline_at=now + timedelta(seconds=30),
        revision=1, etag="1", updated_at=now, finished_at=None,
        result_ref=None, error=None,
    )
    attempt = SimpleNamespace(
        job_id=job.id, attempt=1, status="RUNNING", execution_token="token",
        fencing_token=2, finished_at=None, result_ref=None, error=None,
    )
    test = SimpleNamespace(
        id="test-late-auth", job_id=job.id, status="RUNNING", checks={}, error=None,
        finished_at=None, configuration_revision="revision", configuration_hash="a" * 64,
    )
    config = SimpleNamespace(
        id="cfg-late-auth", project_id=job.project_id, revision="revision",
        settings={"config_hash": "a" * 64}, status="CANDIDATE", last_test_id=None,
    )

    class FakeSession:
        def __init__(self):
            self.rows = iter((job, attempt, test, config))

        async def scalar(self, _statement):
            return next(self.rows, None)

        def add(self, _value):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def begin(self):
            return self

    budget = ProviderRequestBudget(deadline_monotonic=time.monotonic() + 30, max_requests=5)
    budget.begin_provider_call("tool_calling")
    release_auth = threading.Event()
    auth_finished = threading.Event()

    def late_auth():
        assert release_auth.wait(2)
        budget.mark_auth_terminal(401, stage="tool_calling")
        budget.end_provider_call("tool_calling")
        auth_finished.set()

    threading.Thread(target=late_auth, daemon=True).start()
    claim = _LlmTestClaim(
        message=SimpleNamespace(job_id=job.id, attempt=1, dispatch_id="dispatch"),
        test_id=test.id, config=config, token="token", fence=2,
        deadline_at=job.deadline_at, request_budget=budget,
    )
    # Model a completed streaming result followed by the old tool daemon's 401.
    outcome = {
        "status": "SUCCEEDED",
        "checks": {"connectivity": "PASSED", "streaming": "PASSED", "tool_calling": "PASSED", "structured_output": "NOT_SUPPORTED"},
        "error": None,
    }
    release_auth.set()
    assert auth_finished.wait(2)
    worker = AssistantLlmConfigurationTestWorker(session_factory=FakeSession)
    finished = asyncio.run(worker._finish(claim, outcome))

    assert finished is True
    assert job.status == attempt.status == test.status == "FAILED"
    assert test.checks == {
        "connectivity": "PASSED", "streaming": "SKIPPED",
        "tool_calling": "FAILED", "structured_output": "SKIPPED",
    }
    assert test.error["code"] == "PROVIDER_AUTHENTICATION_FAILED"
    assert config.status == "CANDIDATE"
    assert job.result_ref is None


def test_failed_finish_seals_budget_against_late_non_auth_request(monkeypatch):
    import threading
    from datetime import datetime, timedelta, timezone
    from chrom_platform.modules.assistant import adapter as adapter_module

    started = threading.Event()
    release = threading.Event()
    finished_call = threading.Event()
    late_errors = []
    late_dispatches = []
    stream_calls = []

    class FakeConnection:
        sock = None

        def request(self, *_args, **_kwargs):
            late_dispatches.append("request")

    class FakeAdapter:
        def __init__(self, **_kwargs):
            pass

        async def run_agent_async(self, *, tools, messages, request_budget, execute_tool, on_event, **_kwargs):
            prompt = messages[-1]["content"]
            if "connected" in prompt:
                request_budget.reserve_request()
                return {"text": "connected"}

            if tools:
                request_budget.reserve_request()
                request_budget.begin_provider_call("tool_calling")

                def old_call():
                    started.set()
                    try:
                        assert release.wait(2)
                        request_budget.dispatch_request(
                            FakeConnection(), method="POST", path="/next-ip",
                            body=b"{}", headers={}, timeout=1,
                        )
                    except ProviderTransportError as exc:
                        late_errors.append(exc)
                    finally:
                        request_budget.end_provider_call("tool_calling")
                        finished_call.set()

                threading.Thread(target=old_call, daemon=True).start()
                assert started.wait(2)
                # The current turn completes while its already-started daemon
                # remains active; finish must seal the shared budget first.
                request_budget.reserve_request()
                return _native_echo_result(execute_tool)

            stream_calls.append(True)
            request_budget.reserve_request()
            await _native_stream_event(on_event)
            return {"text": "streamed"}

    monkeypatch.setattr(adapter_module, "PydanticAIAdapter", FakeAdapter)
    budget = ProviderRequestBudget(deadline_monotonic=100.0, max_requests=5, clock=lambda: 1.0)
    outcome = asyncio.run(AssistantLlmConfigurationTestWorker._test_adapter(
        _test_config(), secret_resolver=lambda _ref: "fake-secret",
        approved_endpoints_resolver=lambda: "https://api.siliconflow.cn/v1",
        request_budget=budget,
    ))
    assert outcome["status"] == "FAILED"
    assert budget.requests == 3
    assert stream_calls == []

    now = datetime.now(timezone.utc)
    job = SimpleNamespace(
        id="job-sealed", project_id="project-sealed", owner_id="owner", status="RUNNING",
        attempt=1, deadline_at=now + timedelta(seconds=30), revision=1, etag="1",
        updated_at=now, finished_at=None, result_ref=None, error=None,
    )
    attempt = SimpleNamespace(
        job_id=job.id, attempt=1, status="RUNNING", execution_token="token", fencing_token=2,
        finished_at=None, result_ref=None, error=None,
    )
    test = SimpleNamespace(
        id="test-sealed", job_id=job.id, status="RUNNING", checks={}, error=None, finished_at=None,
        configuration_revision="revision", configuration_hash="a" * 64,
    )
    config = SimpleNamespace(
        id="cfg-sealed", project_id=job.project_id, revision="revision",
        settings={"config_hash": "a" * 64}, status="CANDIDATE", last_test_id=None,
    )

    class FakeSession:
        def __init__(self):
            self.rows = iter((job, attempt, test, config))

        async def scalar(self, _statement):
            return next(self.rows, None)

        def add(self, _value):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def begin(self):
            return self

    claim = _LlmTestClaim(
        message=SimpleNamespace(job_id=job.id, attempt=1, dispatch_id="dispatch"),
        test_id=test.id, config=config, token="token", fence=2,
        deadline_at=job.deadline_at, request_budget=budget,
    )
    worker = AssistantLlmConfigurationTestWorker(session_factory=FakeSession)
    assert asyncio.run(worker._finish(claim, outcome)) is True
    assert job.status == attempt.status == test.status == "FAILED"
    assert config.status == "CANDIDATE"

    release.set()
    assert finished_call.wait(2)
    assert late_errors and late_errors[0].category == "TIMEOUT"
    assert late_dispatches == []
    assert budget.auth_failed is False
    assert budget.requests == 3


def test_successful_test_result_seals_budget_against_new_request(monkeypatch):
    from chrom_platform.modules.assistant import adapter as adapter_module

    class FakeAdapter:
        def __init__(self, **_kwargs):
            pass

        async def run_agent_async(self, *, request_budget, tools, messages, execute_tool, on_event, **_kwargs):
            prompt = messages[-1]["content"]
            if "connected" in prompt:
                request_budget.reserve_request()
                return {"text": "connected"}
            if tools:
                request_budget.reserve_request()
                request_budget.reserve_request()
                return _native_echo_result(execute_tool)
            request_budget.reserve_request()
            await _native_stream_event(on_event)
            return {"text": "streamed"}

    monkeypatch.setattr(adapter_module, "PydanticAIAdapter", FakeAdapter)
    budget = ProviderRequestBudget(deadline_monotonic=100.0, max_requests=5, clock=lambda: 1.0)
    result = asyncio.run(AssistantLlmConfigurationTestWorker._test_adapter(
        _test_config(), secret_resolver=lambda _ref: "fake-secret",
        approved_endpoints_resolver=lambda: "https://api.siliconflow.cn/v1",
        request_budget=budget,
    ))
    assert result["status"] == "SUCCEEDED"
    assert budget.requests == 4
    with pytest.raises(ProviderTransportError) as caught:
        budget.reserve_request()
    assert caught.value.http_status is None


def test_successful_finish_seals_budget_before_persisting_tested():
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    job = SimpleNamespace(
        id="job-finish-seal", project_id="project-finish-seal", owner_id="owner", status="RUNNING",
        attempt=1, deadline_at=now + timedelta(seconds=30), revision=1, etag="1",
        updated_at=now, finished_at=None, result_ref=None, error=None,
    )
    attempt = SimpleNamespace(
        job_id=job.id, attempt=1, status="RUNNING", execution_token="token", fencing_token=2,
        finished_at=None, result_ref=None, error=None,
    )
    test = SimpleNamespace(
        id="test-finish-seal", job_id=job.id, status="RUNNING", checks={}, error=None, finished_at=None,
        configuration_revision="revision", configuration_hash="a" * 64,
    )
    config = SimpleNamespace(
        id="cfg-finish-seal", project_id=job.project_id, revision="revision",
        settings={"config_hash": "a" * 64}, status="CANDIDATE", last_test_id=None,
    )

    class FakeSession:
        def __init__(self):
            self.rows = iter((job, attempt, test, config))

        async def scalar(self, _statement):
            return next(self.rows, None)

        def add(self, _value):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def begin(self):
            return self

    budget = ProviderRequestBudget(deadline_monotonic=100.0, max_requests=5, clock=lambda: 1.0)
    claim = _LlmTestClaim(
        message=SimpleNamespace(job_id=job.id, attempt=1, dispatch_id="dispatch"),
        test_id=test.id, config=config, token="token", fence=2,
        deadline_at=job.deadline_at, request_budget=budget,
    )
    outcome = {
        "status": "SUCCEEDED",
        "checks": {"connectivity": "PASSED", "streaming": "PASSED", "tool_calling": "PASSED", "structured_output": "NOT_SUPPORTED"},
        "error": None,
    }
    worker = AssistantLlmConfigurationTestWorker(session_factory=FakeSession)
    assert asyncio.run(worker._finish(claim, outcome)) is True
    assert job.status == attempt.status == test.status == "SUCCEEDED"
    assert config.status == "TESTED"
    with pytest.raises(ProviderTransportError) as caught:
        budget.reserve_request()
    assert caught.value.http_status is None


def test_outbox_redelivery_of_running_test_does_not_claim_a_second_execution():
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    job = SimpleNamespace(
        id="job-redelivery", attempt=1, kind="assistant_llm_configuration_test",
        status="RUNNING", resource_ref={"type": "llm_test", "id": "test-redelivery"},
        project_id="project-redelivery", deadline_at=now + timedelta(seconds=30),
    )
    attempt = SimpleNamespace(status="RUNNING")
    dispatch_id = "dispatch-redelivery"
    dispatch = SimpleNamespace(payload={"job_id": job.id, "attempt": 1, "dispatch_id": dispatch_id})
    test = SimpleNamespace(id="test-redelivery", job_id=job.id, status="RUNNING")

    class FakeSession:
        def __init__(self):
            self.rows = iter((job, attempt, dispatch, test))

        async def scalar(self, _statement):
            return next(self.rows)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def begin(self):
            return self

    worker = AssistantLlmConfigurationTestWorker(session_factory=FakeSession)
    claim, status = asyncio.run(worker._claim(SimpleNamespace(
        job_id=job.id, attempt=1, dispatch_id=dispatch_id,
    )))
    assert claim is None
    assert status == "RUNNING"
