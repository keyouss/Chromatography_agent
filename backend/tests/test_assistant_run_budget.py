from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from chrom_platform.modules.assistant import adapter as adapter_module
from chrom_platform.modules.assistant.adapter import ProviderRequestBudget, ProviderTransportError
from chrom_platform.modules.assistant.runtime import AgentRuntime, RuntimeConfigSnapshot


def _config() -> RuntimeConfigSnapshot:
    return RuntimeConfigSnapshot("https://api.openai.com/v1", "model", "key-ref", "revision")


class _Connection:
    sock = None

    def __init__(self, send=None):
        self.send = send
        self.timeout = None

    def request(self, *_args, **_kwargs):
        if self.send:
            self.send()


class _Inspector:
    def __init__(self):
        self.calls = {}

    def capture_pair(self, *, llm_call, **_kwargs):
        self.calls[llm_call.id] = llm_call
        return SimpleNamespace(id="logical"), SimpleNamespace(id="provider")

    def save_llm_call(self, call):
        self.calls[call.id] = call

    def latest(self):
        return max(self.calls.values(), key=lambda call: call.transport_attempt)


def test_run_budget_counts_dispatches_across_retries_and_address_fallbacks():
    dispatched = []

    class Provider:
        def __init__(self):
            self.calls = 0
            self.budgets = []

        def complete(self, *, request_budget, **_kwargs):
            self.calls += 1
            self.budgets.append(request_budget)
            # Fake DNS fallback: 13 addresses per retry. The shared Run budget
            # allows only 36 local dispatch attempts, and the 37th fails.
            for _ in range(13):
                request_budget.dispatch_request(
                    _Connection(lambda: dispatched.append("request")),
                    method="POST", path="/fake", body=b"{}", headers={}, timeout=1,
                )
            if self.calls < 3:
                raise ProviderTransportError("CONNECT", retryable=True)
            return {"text": "would-be-success"}

    provider = Provider()
    inspector = _Inspector()
    runtime = AgentRuntime(provider, _config(), history=[{"role": "user", "content": "hello"}], context_inspector=inspector)
    result = runtime.legacy_run_for_tests(run_id="run-dispatch-limit")

    assert runtime.max_requests == 12 and runtime.max_tool_calls == 24 and runtime.max_retries == 2
    assert result.status == "FAILED"
    assert result.provider_requests == 1  # logical request count
    assert result.transport_attempts == 3  # retry attempts, not wire receipts
    assert provider.calls == 3
    assert len(dispatched) == 36
    assert len({id(budget) for budget in provider.budgets}) == 1
    assert provider.budgets[0].requests == 36
    assert provider.budgets[0].sealed
    snapshot = inspector.latest().model_parameters_summary["provider_budget"]
    assert snapshot == {
        "local_dispatch_attempts": 36,
        "active_provider_calls": 0,
        "transport_state": "UNKNOWN",
        "remote_receipt": "UNKNOWN",
    }
    assert _config().url not in repr(snapshot) and _config().key_reference not in repr(snapshot)


def test_run_budget_deadline_is_shared_across_retry_attempts(monkeypatch):
    budgets = []
    clock = [0.0]
    original = ProviderRequestBudget

    def make_budget(*, deadline_monotonic, max_requests):
        clock[0] = deadline_monotonic - 1
        budget = original(
            deadline_monotonic=deadline_monotonic,
            max_requests=max_requests,
            clock=lambda: clock[0],
        )
        budgets.append(budget)
        return budget

    monkeypatch.setattr(adapter_module, "ProviderRequestBudget", make_budget)

    class Provider:
        calls = 0

        def complete(self, *, request_budget, **_kwargs):
            self.calls += 1
            request_budget.dispatch_request(
                _Connection(), method="POST", path="/fake", body=b"{}", headers={}, timeout=1,
            )
            clock[0] = request_budget.deadline_monotonic + 1
            raise ProviderTransportError("CONNECT", retryable=True)

    provider = Provider()
    result = AgentRuntime(
        provider, _config(), history=[{"role": "user", "content": "hello"}],
    ).legacy_run_for_tests(run_id="run-shared-deadline")

    assert result.status == "FAILED"
    assert result.provider_requests == 1
    assert provider.calls == 1  # deadline stopped the next retry before provider code
    assert budgets[0].requests == 1
    assert budgets[0].sealed


def test_run_deadline_is_clamped_to_600_seconds():
    budgets = []

    class Provider:
        def complete(self, *, request_budget, **_kwargs):
            budgets.append(request_budget)
            return {"text": "ok"}

    started = time.monotonic()
    result = AgentRuntime(
        Provider(), _config(), history=[{"role": "user", "content": "hello"}],
        max_runtime_seconds=900,
    ).legacy_run_for_tests(run_id="run-deadline-clamp")

    assert result.status == "SUCCEEDED"
    assert len(budgets) == 1
    assert 0 < budgets[0].deadline_monotonic - started <= 600


def test_capture_failure_keeps_cumulative_budget_snapshot_without_starting_attempt():
    class FailingSecondCapture(_Inspector):
        captures = 0

        def capture_pair(self, *, llm_call, **_kwargs):
            self.captures += 1
            if self.captures == 2:
                raise RuntimeError("fake trace store failure")
            return super().capture_pair(llm_call=llm_call)

    class Provider:
        calls = 0

        def complete(self, *, request_budget, **_kwargs):
            self.calls += 1
            request_budget.dispatch_request(
                _Connection(), method="POST", path="/fake", body=b"{}", headers={}, timeout=1,
            )
            raise ProviderTransportError("CONNECT", retryable=True)

    provider = Provider()
    inspector = FailingSecondCapture()
    result = AgentRuntime(
        provider, _config(), history=[{"role": "user", "content": "hello"}],
        context_inspector=inspector,
    ).legacy_run_for_tests(run_id="run-capture-failure-budget")

    assert result.status == "FAILED"
    assert provider.calls == 1
    second_attempt = max(inspector.calls.values(), key=lambda call: call.transport_attempt)
    assert second_attempt.status == "FAILED"
    assert second_attempt.model_parameters_summary["provider_budget"] == {
        "local_dispatch_attempts": 1,
        "active_provider_calls": 0,
        "transport_state": "ATTEMPT_NOT_STARTED",
        "remote_receipt": "UNKNOWN",
    }


def test_provider_wait_uses_remaining_run_deadline_and_expired_budget_starts_no_thread():
    started = threading.Event()
    release = threading.Event()
    inspector = _Inspector()

    class Provider:
        calls = 0

        def complete(self, **_kwargs):
            self.calls += 1
            started.set()
            release.wait(2)
            return {"text": "late"}

    provider = Provider()
    begin = time.monotonic()
    result = AgentRuntime(
        provider, _config(), history=[{"role": "user", "content": "hello"}],
        timeout_seconds=5, max_runtime_seconds=0.05, context_inspector=inspector,
    ).legacy_run_for_tests(run_id="run-remaining-deadline")
    elapsed = time.monotonic() - begin

    assert result.status == "FAILED"
    assert elapsed < 0.5
    assert started.is_set() and provider.calls == 1
    assert inspector.latest().model_parameters_summary["provider_budget"] == {
        "local_dispatch_attempts": 0,
        "active_provider_calls": 1,
        "transport_state": "UNKNOWN",
        "remote_receipt": "UNKNOWN",
    }

    release.set()

    expired = ProviderRequestBudget(deadline_monotonic=time.monotonic() - 1, max_requests=1)
    invoked = threading.Event()
    runtime = AgentRuntime(Provider(), _config(), history=[{"role": "user", "content": "hello"}])
    with pytest.raises(TimeoutError):
        runtime._call_with_timeout(
            lambda **_kwargs: invoked.set(), [], [], request_budget=expired,
        )
    assert expired.sealed and not invoked.is_set()


def test_auth_terminal_stops_retries_and_run_seals_budget():
    class Provider:
        calls = 0
        budget = None

        def complete(self, *, request_budget, **_kwargs):
            self.calls += 1
            self.budget = request_budget
            request_budget.dispatch_request(
                _Connection(), method="POST", path="/fake", body=b"{}", headers={}, timeout=1,
            )
            request_budget.mark_auth_terminal(403, stage="run")
            with pytest.raises(ProviderTransportError):
                request_budget.dispatch_request(
                    _Connection(), method="POST", path="/fake", body=b"{}", headers={}, timeout=1,
                )
            raise ProviderTransportError("HTTP_4XX", http_status=403, retryable=False)

    provider = Provider()
    result = AgentRuntime(provider, _config(), history=[{"role": "user", "content": "hello"}]).legacy_run_for_tests(
        run_id="run-auth-terminal"
    )
    assert result.status == "FAILED"
    assert provider.calls == 1
    assert provider.budget.requests == 1
    assert provider.budget.auth_failed and provider.budget.sealed


def test_reused_runtime_gets_an_independent_budget_for_each_run():
    class Provider:
        budgets = []

        def complete(self, *, request_budget, **_kwargs):
            self.budgets.append(request_budget)
            request_budget.dispatch_request(
                _Connection(), method="POST", path="/fake", body=b"{}", headers={}, timeout=1,
            )
            return {"text": "ok"}

    provider = Provider()
    inspector = _Inspector()
    runtime = AgentRuntime(provider, _config(), history=[{"role": "user", "content": "hello"}], context_inspector=inspector)
    first = runtime.legacy_run_for_tests(run_id="run-one")
    second = runtime.legacy_run_for_tests(run_id="run-two")

    assert first.status == second.status == "SUCCEEDED"
    assert len(provider.budgets) == 2
    assert provider.budgets[0] is not provider.budgets[1]
    assert all(budget.requests == 1 and budget.sealed for budget in provider.budgets)
    snapshots = [call.model_parameters_summary["provider_budget"] for call in inspector.calls.values()]
    assert len(snapshots) == 2
    assert all(snapshot == {
        "local_dispatch_attempts": 1,
        "active_provider_calls": 0,
        "transport_state": "CALL_RETURNED",
        "remote_receipt": "UNKNOWN",
    } for snapshot in snapshots)


def test_timeout_with_default_retries_seals_before_late_thread_and_keeps_unknown_diagnostic():
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    dispatched = []
    budgets = []

    class Provider:
        calls = 0

        def complete(self, *, request_budget, **_kwargs):
            self.calls += 1
            budgets.append(request_budget)
            started.set()
            release.wait(2)
            try:
                request_budget.dispatch_request(
                    _Connection(lambda: dispatched.append("request")),
                    method="POST", path="/late", body=b"{}", headers={}, timeout=1,
                )
            finally:
                finished.set()
            return {"text": "late"}

    provider = Provider()
    inspector = _Inspector()
    runtime = AgentRuntime(
        provider, _config(), history=[{"role": "user", "content": "hello"}],
        timeout_seconds=0.02, context_inspector=inspector,
    )
    result = runtime.legacy_run_for_tests(run_id="run-late-timeout")

    assert started.wait(1)
    budget = budgets[0]
    assert result.status == "FAILED"
    assert provider.calls == 1
    assert budget.sealed
    assert budget.provider_call_state[2] == 1
    assert inspector.latest().error["provider"] == {
        "transport_state": "UNKNOWN",
        "local_dispatch_attempts": 0,
        "active_provider_calls": 1,
        "remote_receipt": "UNKNOWN",
    }
    assert inspector.latest().model_parameters_summary["provider_budget"] == inspector.latest().error["provider"]

    release.set()
    assert finished.wait(1)
    assert dispatched == []
    assert budget.requests == 0
    assert budget.provider_call_state[2] == 0
    assert result.status == "FAILED"  # the late thread cannot rewrite the result
    assert provider.calls == 1
    assert inspector.latest().error["provider"]["transport_state"] == "UNKNOWN"


def test_timeout_during_committed_send_returns_unknown_without_waiting_for_io():
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    dispatched = []
    provider_calls = []

    class BlockingConnection(_Connection):
        def request(self, *_args, **_kwargs):
            dispatched.append("committed")
            entered.set()
            release.wait(3)
            finished.set()

    class Provider:
        def complete(self, *, request_budget, **_kwargs):
            provider_calls.append("invoked")
            request_budget.dispatch_request(
                BlockingConnection(), method="POST", path="/fake", body=b"{}", headers={}, timeout=5,
            )
            return {"text": "late"}

    inspector = _Inspector()
    result = AgentRuntime(
        Provider(), _config(), history=[{"role": "user", "content": "hello"}],
        timeout_seconds=0.03, context_inspector=inspector,
    ).legacy_run_for_tests(run_id="run-committed-timeout")

    assert entered.is_set()
    assert result.status == "FAILED"
    assert provider_calls == ["invoked"]
    assert dispatched == ["committed"]
    assert inspector.latest().error["provider"] == {
        "transport_state": "UNKNOWN",
        "local_dispatch_attempts": 1,
        "active_provider_calls": 1,
        "remote_receipt": "UNKNOWN",
    }
    assert inspector.latest().model_parameters_summary["provider_budget"] == inspector.latest().error["provider"]
    # The provider thread still owns the in-flight request; Run completion
    # only seals future dispatch and does not claim to cancel that I/O.
    release.set()
    assert finished.wait(1)
    assert result.status == "FAILED"
    assert provider_calls == ["invoked"]
    assert inspector.latest().error["provider"]["transport_state"] == "UNKNOWN"


def test_seal_intent_blocks_waiting_dispatch_but_does_not_cancel_committed_send():
    budget = ProviderRequestBudget(deadline_monotonic=time.monotonic() + 20, max_requests=5)
    entered = threading.Event()
    release = threading.Event()
    second_started = threading.Event()
    seal_returned = threading.Event()
    requests = []
    second_errors = []

    def blocking_send():
        requests.append("committed")
        entered.set()
        assert release.wait(5)

    def dispatch(label, send=None):
        budget.dispatch_request(
            _Connection(send), method="POST", path=f"/{label}", body=b"{}", headers={}, timeout=5,
        )

    first = threading.Thread(target=dispatch, args=("first", blocking_send))
    first.start()
    assert entered.wait(1)
    assert budget._requests == 1  # avoid reacquiring the lock held by conn.request

    def late_dispatch():
        second_started.set()
        try:
            dispatch("second", lambda: requests.append("late"))
        except ProviderTransportError as exc:
            second_errors.append(exc)

    second = threading.Thread(target=late_dispatch)
    second.start()
    assert second_started.wait(1)
    time.sleep(0.02)  # let the second thread queue behind the committed send

    def seal_after_timeout():
        budget.seal()
        seal_returned.set()

    sealer = threading.Thread(target=seal_after_timeout)
    sealer.start()
    assert budget._seal_requested.wait(1)
    assert seal_returned.wait(1)  # intent publishes without waiting for committed I/O
    assert requests == ["committed"]

    release.set()
    for thread in (first, second, sealer):
        thread.join(1)
        assert not thread.is_alive()

    assert requests == ["committed"]
    assert len(second_errors) == 1 and second_errors[0].category == "TIMEOUT"
    assert budget.requests == 1
    assert budget.sealed
