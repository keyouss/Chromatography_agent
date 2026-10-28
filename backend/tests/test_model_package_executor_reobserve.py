from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from chrom_platform.modules.model_center import model_package_executor as executor_module


def _proof(**changes: object) -> dict[str, object]:
    return {
        "started": True,
        "never_started": False,
        "confirmed": True,
        "process_id": 4812,
        "creation_filetime": 100,
        "exit_filetime": 120,
        "exit_code": 0,
        "job_active_processes": 0,
        "output_drained": True,
        "cleanup_completed": False,
        "stop_cause": "CANCELLED",
        **changes,
    }


class _FakeHandles(executor_module._NativeHandles):
    def __init__(self, *, proof: dict[str, object] | None = None, close_failures: int = 0) -> None:
        self.process_id = 4812
        self.process = 11
        self.job = 22
        self.created = True
        self.proof = proof or _proof()
        self.close_failures = close_failures
        self.confirm_calls = 0
        self.close_calls = 0
        self._close_proof = None

    def confirm_termination(self, *, assigned: bool, stop_cause: str, terminate: bool, timeout_ms: int):
        self.confirm_calls += 1
        assert terminate is False
        self._observed_termination = executor_module._freeze_receipt(self.proof)
        return dict(self.proof)

    def authorize_close(self, proof):
        assert executor_module._physical_exit_confirmed(proof)
        assert proof["output_drained"] is True
        self._close_proof = executor_module._freeze_receipt(proof)

    def close(self):
        self.close_calls += 1
        if self.close_failures:
            self.close_failures -= 1
            raise OSError("fake close failure")


class _FakeReaders(executor_module._Readers):
    def __init__(self, *, drained: bool = True) -> None:
        self._drained = drained
        self.join_calls = 0

    @property
    def drained(self) -> bool:
        return self._drained

    def join(self, timeout: float) -> None:
        self.join_calls += 1


class _FakeWriter(executor_module._RequestWriter):
    def __init__(self, *, alive: bool = False) -> None:
        self.alive = alive
        self.join_calls = 0

    def join(self, timeout: float) -> None:
        self.join_calls += 1

    def is_alive(self) -> bool:
        return self.alive


@pytest.fixture(autouse=True)
def _reset_recovery_state(monkeypatch):
    monkeypatch.setattr(executor_module.shutil, "rmtree", lambda _: None)
    with executor_module._EXECUTION_GATE:
        executor_module._UNCONFIRMED_CLEANUPS.clear()
    yield
    with executor_module._EXECUTION_GATE:
        executor_module._UNCONFIRMED_CLEANUPS.clear()


def _blocker(
    handles: _FakeHandles | None,
    *,
    readers: _FakeReaders | None = None,
    writer: _FakeWriter | None = None,
    sink=None,
    cached_receipt=None,
) -> dict[str, object]:
    item: dict[str, object] = {
        "termination": _proof(),
        "handles": handles,
        "temp_dir": "fake-temp",
        "readers": readers,
        "writer": writer,
        "assigned": handles is not None,
        "stop_sink": sink or (lambda *_: None),
        "request_ref": "ref-1",
        "request_sha256": "a" * 64,
        "cached_receipt": cached_receipt,
        "proof_cache": None,
        "pending": None,
        "last_error": None,
    }
    executor_module._UNCONFIRMED_CLEANUPS.append(item)
    return item


def test_reobserve_caches_receipt_but_keeps_durable_slot_blocked():
    handles = _FakeHandles()
    seen = []
    durable = {"slot": "ACTIVE", "invocation": "UNKNOWN"}

    def sink(*args):
        seen.append(args)
        durable["slot"] = "IDLE"
        durable["invocation"] = "STOP_CONFIRMED"

    item = _blocker(handles, readers=_FakeReaders(), writer=_FakeWriter(), sink=lambda *args: seen.append(args))
    item["stop_sink"] = sink

    with executor_module._EXECUTION_GATE:
        assert executor_module._reobserve_blocker(item) is False
        assert executor_module._reobserve_blocker(item) is False

    assert item in executor_module._UNCONFIRMED_CLEANUPS
    assert item["observation_complete"] is True
    assert durable == {"slot": "ACTIVE", "invocation": "UNKNOWN"}
    assert seen == []
    receipt = item["cached_receipt"]
    assert receipt["cleanup_completed"] is True
    assert set(receipt) == executor_module._TERMINATION_FIELDS | {
        "request_ref", "request_sha256",
    }
    assert receipt["request_ref"] == "ref-1"
    assert receipt["request_sha256"] == "a" * 64
    assert handles.confirm_calls == handles.close_calls == 1


def test_partial_close_retry_uses_cached_parent_proof_without_requery():
    handles = _FakeHandles(close_failures=1)
    item = _blocker(handles, readers=_FakeReaders(), writer=_FakeWriter())

    with executor_module._EXECUTION_GATE:
        assert executor_module._reobserve_blocker(item) is False
        assert isinstance(item.get("proof_cache"), dict) or item.get("proof_cache") is not None
        assert executor_module._reobserve_blocker(item) is False
        assert executor_module._reobserve_blocker(item) is False

    assert handles.confirm_calls == 1
    assert handles.close_calls == 2
    assert item["observation_complete"] is True


def test_complete_cached_receipt_never_calls_sink_or_clears_slot():
    calls = []
    durable = {"slot": "ACTIVE", "invocation": "UNKNOWN"}

    def sink(ref, digest, receipt):
        calls.append((ref, digest, receipt))
        durable["slot"] = "IDLE"
        durable["invocation"] = "STOP_CONFIRMED"

    receipt = {
        **_proof(cleanup_completed=True),
        "request_ref": "ref-1", "request_sha256": "a" * 64,
    }
    item = _blocker(None, sink=sink, cached_receipt=receipt)
    with executor_module._EXECUTION_GATE:
        assert executor_module._reobserve_blocker(item) is False
        assert executor_module._reobserve_blocker(item) is False

    assert calls == []
    assert durable == {"slot": "ACTIVE", "invocation": "UNKNOWN"}
    assert item["observation_complete"] is True
    assert item in executor_module._UNCONFIRMED_CLEANUPS


@pytest.mark.parametrize("proof", [
    _proof(confirmed=False),
    _proof(job_active_processes=1),
])
def test_unconfirmed_exit_or_active_job_stays_blocked(proof):
    handles = _FakeHandles(proof=proof)
    item = _blocker(handles, readers=_FakeReaders(), writer=_FakeWriter())
    with executor_module._EXECUTION_GATE:
        assert executor_module._reobserve_blocker(item) is False
    assert handles.confirm_calls == 1
    assert handles.close_calls == 0
    assert item.get("sink_done") is not True


def test_reader_error_without_pipe_eof_stays_blocked():
    handles = _FakeHandles()
    item = _blocker(handles, readers=_FakeReaders(drained=False), writer=_FakeWriter())
    with executor_module._EXECUTION_GATE:
        assert executor_module._reobserve_blocker(item) is False
    assert handles.confirm_calls == 1
    assert handles.close_calls == 0


def test_missing_parent_context_stays_blocked():
    item = _blocker(None)
    with executor_module._EXECUTION_GATE:
        assert executor_module._reobserve_blocker(item) is False
    assert item.get("sink_done") is not True


def test_missing_io_or_untrusted_proof_cache_blocks_without_native_reobserve():
    handles = _FakeHandles()
    item = _blocker(handles, readers=None, writer=None)
    with executor_module._EXECUTION_GATE:
        assert executor_module._reobserve_blocker(item) is False
    assert handles.confirm_calls == handles.close_calls == 0

    handles2 = _FakeHandles()
    item2 = _blocker(handles2, readers=_FakeReaders(), writer=_FakeWriter())
    item2["proof_cache"] = dict(_proof())
    with executor_module._EXECUTION_GATE:
        assert executor_module._reobserve_blocker(item2) is False
    assert handles2.confirm_calls == handles2.close_calls == 0


def test_cleanup_failure_route_retains_context_after_partial_sink_succeeds():
    handles = _FakeHandles()
    readers = _FakeReaders()
    writer = _FakeWriter()
    handles._observed_termination = executor_module._freeze_receipt(_proof())
    handles._close_proof = executor_module._freeze_receipt(_proof())
    executor = executor_module.ControlledModelPackageExecutor()
    executor._execute_windows = lambda *args: (_ for _ in ()).throw(
        executor_module._ExecutionFailure(
            "CLEANUP_FAILED", _proof(), handles=handles, temp_dir="fake-temp",
            readers=readers, writer=writer, assigned=True,
        )
    )
    sink_calls = []
    request = {"op": "probe", "case": "started"}
    result = executor._execute_guarded(
        "ref-route", "b" * 64, b"{}", request, None, None,
        lambda *_: None, lambda *args: sink_calls.append(args),
    )
    assert result["error_code"] == "CLEANUP_FAILED"
    assert len(sink_calls) == 1
    assert len(executor_module._UNCONFIRMED_CLEANUPS) == 1
    blocker = executor_module._UNCONFIRMED_CLEANUPS[0]
    assert blocker["handles"] is handles
    assert blocker["readers"] is readers
    assert blocker["writer"] is writer
    assert blocker["temp_dir"] == "fake-temp"
    assert blocker["stop_sink"] is not None


class _NativeCloseKernel:
    def __init__(self) -> None:
        self.fail_stdout_read_once = True
        self.closed = []

    def WaitForSingleObject(self, handle, timeout):
        return 0

    def GetExitCodeProcess(self, handle, output):
        output._obj.value = 0
        return 1

    def GetProcessTimes(self, handle, creation, exit_time, kernel, user):
        for pointer, value in ((creation, 100), (exit_time, 120), (kernel, 0), (user, 0)):
            pointer._obj.low = value
            pointer._obj.high = 0
        return 1

    def QueryInformationJobObject(self, job, info_class, output, size, returned):
        if info_class != 1:
            return 0
        output._obj.active_processes = 0
        returned._obj.value = size
        return 1

    def CloseHandle(self, handle):
        if handle == 15 and self.fail_stdout_read_once:
            self.fail_stdout_read_once = False
            return 0
        self.closed.append(handle)
        return 1

    def DeleteProcThreadAttributeList(self, _):
        return None


def test_native_partial_close_keeps_failed_handle_and_retries_from_cached_proof(monkeypatch):
    kernel = _NativeCloseKernel()
    handles = executor_module._NativeHandles.__new__(executor_module._NativeHandles)
    handles.api = SimpleNamespace(kernel32=kernel)
    handles.job = 99
    handles.process = 11
    handles.thread = 12
    handles.stdin_read = 13
    handles.stdin_write = 14
    handles.stdout_read = 15
    handles.stdout_write = 16
    handles.stderr_read = 17
    handles.stderr_write = 18
    handles.attribute_list = None
    handles._observed_termination = None
    handles._close_proof = None
    handles._readers = None
    handles._writer = None
    handles.created = True
    handles.assigned = True
    handles.resumed = True
    handles.process_id = 4812
    handles.thread_id = 4813
    readers = _FakeReaders()
    writer = _FakeWriter()

    termination = handles.confirm_termination(
        assigned=True, stop_cause="CANCELLED", terminate=False, timeout_ms=50,
    )
    termination["output_drained"] = True
    handles.authorize_close(termination)
    with pytest.raises(OSError, match="CloseHandle failed"):
        handles.close()
    assert handles.process is None
    assert handles.stdout_read == 15

    blocker = executor_module._block_local_recovery(
        "ref-native", "c" * 64, termination, handles=handles, temp_dir="fake-temp",
        readers=readers, writer=writer, assigned=True, stop_sink=lambda *_: None,
    )
    entry = executor_module._UNCONFIRMED_CLEANUPS[-1]
    assert blocker is None
    assert entry["proof_cache"]["request_ref"] == "ref-native"
    monkeypatch.setattr(
        handles, "confirm_termination",
        lambda **_: (_ for _ in ()).throw(AssertionError("must use cached proof")),
    )
    with executor_module._EXECUTION_GATE:
        assert executor_module._reobserve_blocker(entry) is False
    assert handles.stdout_read is None
    assert 15 in kernel.closed
    assert entry["cached_receipt"]["request_ref"] == "ref-native"
    assert entry["cached_receipt"]["request_sha256"] == "c" * 64


@pytest.mark.parametrize(
    ("slot_state", "invocation_state"),
    [("ACTIVE", "STARTED"), ("UNKNOWN", "UNKNOWN")],
)
def test_slow_reobserve_never_sinks_or_relaunches_even_after_receipt_is_cached(
    monkeypatch, slot_state, invocation_state,
):
    handles = _FakeHandles()
    entered = threading.Event()
    release = threading.Event()
    sink_calls = []
    launch_calls = []
    durable = {"slot": slot_state, "invocation": invocation_state}

    class _SlowReaders(_FakeReaders):
        def join(self, timeout: float) -> None:
            self.join_calls += 1
            entered.set()
            release.wait(3)

    def sink(*args):
        sink_calls.append(args)
        durable["slot"] = "IDLE"
        durable["invocation"] = "STOP_CONFIRMED"

    item = _blocker(handles, readers=_SlowReaders(), writer=_FakeWriter(), sink=sink)
    monkeypatch.setattr(executor_module, "_REOBSERVE_BUDGET_SECONDS", 0.05)
    executor = executor_module.ControlledModelPackageExecutor()
    before_launch = lambda *_: launch_calls.append("launch")

    def call_execute(ref):
        return executor._execute_guarded(
            ref, "d" * 64, b"{}", {"op": "probe", "case": "started"},
            None, None, before_launch, sink,
        )

    try:
        first = call_execute("new-ref-1")
        assert entered.wait(2)
        assert first["error_code"] == "RECOVERY_REQUIRED"
        assert call_execute("new-ref-2")["error_code"] == "RECOVERY_REQUIRED"
        assert sink_calls == []
        assert launch_calls == []
        assert durable == {"slot": slot_state, "invocation": invocation_state}
    finally:
        release.set()
    pending = item["pending"]
    pending.join(2)
    assert not pending.is_alive()
    assert item["observation_complete"] is True
    assert item["cached_receipt"]["request_ref"] == "ref-1"
    assert item["cached_receipt"]["request_sha256"] == "a" * 64
    assert handles.confirm_calls == handles.close_calls == 1
    assert sink_calls == []
    assert launch_calls == []
    assert durable == {"slot": slot_state, "invocation": invocation_state}
    assert call_execute("new-ref-3")["error_code"] == "RECOVERY_REQUIRED"
    assert sink_calls == []
    assert launch_calls == []
    assert durable == {"slot": slot_state, "invocation": invocation_state}
    with executor_module._EXECUTION_GATE:
        assert executor_module._reobserve_blocker(item) is False
    assert item in executor_module._UNCONFIRMED_CLEANUPS


def test_cached_complete_receipt_keeps_before_launch_and_sink_disabled():
    calls = []
    receipt = {
        **_proof(cleanup_completed=True),
        "request_ref": "ref-cached", "request_sha256": "e" * 64,
    }
    item = _blocker(None, cached_receipt=receipt, sink=lambda *args: calls.append(args))
    executor = executor_module.ControlledModelPackageExecutor()
    result = executor._execute_guarded(
        "next-ref", "f" * 64, b"{}", {"op": "probe", "case": "started"},
        None, None, lambda *_: calls.append("before_launch"),
        lambda *args: calls.append(args),
    )
    assert result["error_code"] == "RECOVERY_REQUIRED"
    assert calls == []
    assert item in executor_module._UNCONFIRMED_CLEANUPS


def _under_gate(call):
    with executor_module._EXECUTION_GATE:
        return call()


class _ReadApi:
    def __init__(self, result: bool, bytes_read: int = 0) -> None:
        self.result = result
        self.bytes_read = bytes_read

    def ReadFile(self, handle, buffer, size, count, overlapped):
        count._obj.value = self.bytes_read
        return self.result


def test_reader_only_accepts_broken_pipe_as_eof(monkeypatch):
    monkeypatch.setattr(executor_module.ctypes, "get_last_error", lambda: 109, raising=False)
    eof = executor_module._ReaderState(SimpleNamespace(kernel32=_ReadApi(False)), 1, 32)
    eof.thread.run()
    assert eof.eof is True
    assert eof.read_error is None

    empty_success = executor_module._ReaderState(SimpleNamespace(kernel32=_ReadApi(True, 0)), 1, 32)
    empty_success.thread.run()
    assert empty_success.eof is False
    assert empty_success.read_error == "ZERO_BYTE_READ"


def test_createprocess_failure_can_close_fresh_job_with_native_never_started_proof():
    closed = []
    kernel = SimpleNamespace(
        CloseHandle=lambda handle: closed.append(handle) or 1,
        DeleteProcThreadAttributeList=lambda _: None,
    )
    handles = executor_module._NativeHandles.__new__(executor_module._NativeHandles)
    handles.api = SimpleNamespace(kernel32=kernel)
    handles.job = 77
    handles.process = None
    handles.process_id = None
    handles.created = False
    handles._observed_termination = None
    handles._close_proof = None
    handles.attribute_list = None
    handles.thread = None
    handles.stdin_read = handles.stdin_write = None
    handles.stdout_read = handles.stdout_write = None
    handles.stderr_read = handles.stderr_write = None
    with pytest.raises(OSError):
        handles.authorize_close(_proof())
    proof = handles.confirm_termination(
        assigned=False, stop_cause="CHILD_START_FAILED", terminate=False, timeout_ms=1,
    )
    proof["output_drained"] = True
    handles.authorize_close(proof)
    handles.close()
    assert closed == [77]
