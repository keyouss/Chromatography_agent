from __future__ import annotations

import os
import time

import pytest

from chrom_platform.modules.model_center import model_package_executor as executor_module
from chrom_platform.modules.model_center.model_package_executor import (
    ControlledModelPackageExecutor,
    _Limits,
)


pytestmark = pytest.mark.skipif(os.name != "nt", reason="requires Windows Job Objects")


def _executor(*, memory_mb: int = 256, timeout: float = 5.0, output_kb: int = 64) -> ControlledModelPackageExecutor:
    return ControlledModelPackageExecutor(
        _limits=_Limits(memory_bytes=memory_mb * 1024 * 1024, timeout_seconds=timeout, output_bytes=output_kb * 1024)
    )


def test_started_and_child_creation_probes_run_with_hard_process_limit() -> None:
    executor = _executor()
    started = executor.execute({"op": "probe", "case": "started"})
    assert started["status"] == "SUCCEEDED"
    assert started["hard_limits"]["active_processes"] == 1
    assert started["hard_limits"]["kill_on_close"] is True
    assert started["hard_limits"]["readback_verified"] is True
    assert started["hard_limits"]["job_limit_flags"] == 0x2308
    assert started["hard_limits"]["memory_bytes"] == 256 * 1024 * 1024
    assert started["hard_limits"]["process_memory_bytes"] == 256 * 1024 * 1024
    assert started["evidence"] == {"probe_case": "started", "started": True}

    child = executor.execute({"op": "probe", "case": "attempt-child"})
    assert child["status"] == "SUCCEEDED"
    assert child["evidence"] == {"probe_case": "attempt-child", "child_blocked": True}


def test_memory_ceiling_refuses_fixed_probe_allocation() -> None:
    result = _executor(memory_mb=96).execute({"op": "probe", "case": "alloc"})
    if result["status"] == "SUCCEEDED":
        assert result["evidence"] == {"probe_case": "alloc", "allocation_refused": True}
    else:
        assert result["error_code"] == "RESOURCE_LIMIT"


def test_timeout_kills_job_and_returns_fixed_error() -> None:
    started = time.monotonic()
    result = _executor(timeout=0.5).execute({"op": "probe", "case": "sleep"})
    assert result["status"] == "FAILED"
    assert result["error_code"] == "TIMEOUT"
    assert time.monotonic() - started < 5.0


def test_output_cap_and_crash_never_return_child_output() -> None:
    output = _executor(output_kb=8).execute({"op": "probe", "case": "output"})
    assert output["status"] == "FAILED"
    assert output["error_code"] == "OUTPUT_LIMIT"
    assert "evidence" not in output
    assert "X" not in repr(output)

    crashed = _executor().execute({"op": "probe", "case": "crash"})
    assert crashed["status"] == "FAILED"
    assert crashed["error_code"] == "CHILD_FAILED"
    assert "evidence" not in crashed


def test_cancellation_and_fence_fail_before_process_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden_api() -> object:
        raise AssertionError("must not create child after cancellation/fence rejection")

    monkeypatch.setattr(executor_module, "_WindowsApi", forbidden_api)
    cancelled = _executor().execute({"op": "probe", "case": "started"}, cancelled=lambda: True)
    assert cancelled["status"] == "CANCELLED"
    assert cancelled["error_code"] == "CANCELLED"
    assert cancelled["termination"]["never_started"] is True
    assert cancelled["termination"]["confirmed"] is True

    def rejected_fence() -> None:
        raise RuntimeError("private fence detail")

    fenced = _executor().execute({"op": "probe", "case": "started"}, check_fence=rejected_fence)
    assert fenced["status"] == "FAILED"
    assert fenced["error_code"] == "FENCE_REJECTED"
    assert "private fence detail" not in repr(fenced)


@pytest.mark.parametrize("mode", ["cancel", "fence"])
def test_running_cancellation_and_fence_terminate_job(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    closed_processes: list[bool] = []
    original_wait = executor_module._NativeHandles.wait_process

    def checked_wait(handles: object, timeout_ms: int) -> None:
        original_wait(handles, timeout_ms)  # type: ignore[arg-type]
        process = getattr(handles, "process", None)
        if process:
            closed_processes.append(
                handles.api.kernel32.WaitForSingleObject(process, 0) == 0
            )

    monkeypatch.setattr(executor_module._NativeHandles, "wait_process", checked_wait)
    started = time.monotonic()
    cutoff = started + 0.3
    if mode == "cancel":
        result = _executor(timeout=4).execute(
            {"op": "probe", "case": "sleep"}, cancelled=lambda: time.monotonic() >= cutoff
        )
        expected_code = "CANCELLED"
    else:
        def check_fence() -> None:
            if time.monotonic() >= cutoff:
                raise RuntimeError("private fence detail")

        result = _executor(timeout=4).execute(
            {"op": "probe", "case": "sleep"}, check_fence=check_fence
        )
        expected_code = "FENCE_REJECTED"
    assert result["status"] == ("CANCELLED" if mode == "cancel" else "FAILED")
    assert result["error_code"] == expected_code
    assert time.monotonic() - started < 5.0
    assert closed_processes and all(closed_processes)


def test_attach_failure_terminates_suspended_child_without_resume(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []

    class FakeKernel:
        def AssignProcessToJobObject(self, *_args: object) -> int:
            events.append("assign")
            return 0

    class FakeApi:
        kernel32 = FakeKernel()

    class FakeHandles:
        job = object()
        process = object()

        def __init__(self, _api: object) -> None:
            pass

        def create_job(self, _memory: int) -> None:
            events.append("job-ready")

        def create_pipes(self) -> None:
            pass

        def create_attribute_list(self) -> None:
            pass

        def create_suspended_child(self, _temp: str) -> None:
            events.append("created-suspended")

        def confirm_termination(self, *, assigned: bool, stop_cause: str, terminate: bool, timeout_ms: int) -> dict[str, object]:
            events.append(f"confirm-root-outside-job:{assigned}:{terminate}:{timeout_ms}")
            return {
                "started": False, "never_started": True, "confirmed": True,
                "process_id": 321, "creation_filetime": 10, "exit_filetime": 20,
                "exit_code": 1, "job_active_processes": 0, "output_drained": True,
                "cleanup_completed": False, "stop_cause": stop_cause,
            }

        def close(self) -> None:
            events.append("close-after-confirmed-exit")

    monkeypatch.setattr(executor_module, "_WindowsApi", FakeApi)
    monkeypatch.setattr(executor_module, "_NativeHandles", FakeHandles)
    result = _executor().execute({"op": "probe", "case": "started"})
    assert result["status"] == "FAILED"
    assert result["error_code"] == "HARD_LIMITS_UNAVAILABLE"
    assert result["termination"]["confirmed"] is True
    assert result["termination"]["process_id"] == 321
    assert events == [
        "job-ready", "created-suspended", "assign",
        "confirm-root-outside-job:False:True:5000", "close-after-confirmed-exit",
    ]


class _FakeTerminationKernel:
    def __init__(
        self, wait_result: int = 0, active_processes: int = 0, *,
        exit_code: int = 0xC0000005, creation: tuple[int, int] = (11, 1),
        exit_time: tuple[int, int] = (22, 2),
    ) -> None:
        self.wait_result = wait_result
        self.active_processes = active_processes
        self.exit_code = exit_code
        self.creation = creation
        self.exit_time = exit_time
        self.terminated_job = 0
        self.terminated_process = 0

    def TerminateJobObject(self, *_args: object) -> int:
        self.terminated_job += 1
        return 0  # API failure is not the exit proof.

    def TerminateProcess(self, *_args: object) -> int:
        self.terminated_process += 1
        return 1

    def WaitForSingleObject(self, *_args: object) -> int:
        return self.wait_result

    def GetExitCodeProcess(self, _process: object, output: object) -> int:
        output._obj.value = self.exit_code  # type: ignore[attr-defined]
        return 1

    def GetProcessTimes(self, _process: object, creation: object, exit_time: object, *_args: object) -> int:
        creation._obj.low, creation._obj.high = self.creation  # type: ignore[attr-defined]
        exit_time._obj.low, exit_time._obj.high = self.exit_time  # type: ignore[attr-defined]
        return 1

    def QueryInformationJobObject(self, _job: object, _kind: int, info: object, size: int, returned: object) -> int:
        info._obj.active_processes = self.active_processes  # type: ignore[attr-defined]
        returned._obj.value = size  # type: ignore[attr-defined]
        return 1


def _fake_native_handles(kernel: _FakeTerminationKernel, *, process_id: int = 987) -> executor_module._NativeHandles:
    handles = object.__new__(executor_module._NativeHandles)
    handles.api = type("Api", (), {"kernel32": kernel})()
    handles.job = object()
    handles.process = object()
    handles.process_id = process_id
    handles.resumed = True
    return handles


def test_parent_observation_confirms_even_when_terminate_api_reports_failure() -> None:
    kernel = _FakeTerminationKernel(wait_result=0, active_processes=0)
    handles = _fake_native_handles(kernel)
    observed = handles.confirm_termination(
        assigned=True, stop_cause="CANCELLED", terminate=True, timeout_ms=50
    )
    assert kernel.terminated_job == 1
    assert kernel.terminated_process == 0
    assert observed["confirmed"] is True
    assert observed["process_id"] == 987
    assert observed["creation_filetime"] == (1 << 32) + 11
    assert observed["exit_filetime"] == (2 << 32) + 22
    assert observed["exit_code"] == 0xC0000005
    assert observed["job_active_processes"] == 0


def test_unassigned_root_is_terminated_and_wait_is_required() -> None:
    kernel = _FakeTerminationKernel(wait_result=258, active_processes=0)
    handles = _fake_native_handles(kernel)
    observed = handles.confirm_termination(
        assigned=False, stop_cause="ASSIGN_FAILED", terminate=True, timeout_ms=50
    )
    assert kernel.terminated_process == 1
    assert kernel.terminated_job == 0
    assert observed["confirmed"] is False
    assert observed["exit_code"] is None


def test_job_descendants_must_be_gone_even_when_root_waits() -> None:
    kernel = _FakeTerminationKernel(wait_result=0, active_processes=1)
    handles = _fake_native_handles(kernel)
    observed = handles.confirm_termination(
        assigned=True, stop_cause="FENCE_REJECTED", terminate=True, timeout_ms=20
    )
    assert observed["confirmed"] is False
    assert observed["job_active_processes"] == 1


@pytest.mark.parametrize(
    ("kernel", "process_id"),
    [
        (_FakeTerminationKernel(exit_code=259), 987),
        (_FakeTerminationKernel(creation=(0, 0)), 987),
        (_FakeTerminationKernel(creation=(11, 1), exit_time=(1, 1)), 987),
        (_FakeTerminationKernel(), 0),
    ],
)
def test_invalid_parent_exit_identity_is_unconfirmed(
    kernel: _FakeTerminationKernel, process_id: int
) -> None:
    observed = _fake_native_handles(kernel, process_id=process_id).confirm_termination(
        assigned=True, stop_cause="CANCELLED", terminate=True, timeout_ms=50
    )
    assert observed["confirmed"] is False


def test_executor_keeps_internal_receipt_out_of_public_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    termination = {
        "started": True, "never_started": False, "confirmed": True,
        "process_id": 321, "creation_filetime": 10, "exit_filetime": 20,
        "exit_code": 0, "job_active_processes": 0, "output_drained": True,
        "cleanup_completed": True, "stop_cause": "NATURAL_EXIT",
    }

    def successful(*_args: object) -> tuple[dict[str, object], dict[str, object]]:
        return ({"probe_case": "started", "_termination": termination}, {"readback_verified": True})

    monkeypatch.setattr(ControlledModelPackageExecutor, "_execute_windows", successful)
    result = _executor().execute({"op": "probe", "case": "started"})
    assert result["status"] == "SUCCEEDED"
    assert result["evidence"] == {"probe_case": "started"}
    assert result["termination"]["confirmed"] is True


def test_unconfirmed_termination_is_failed_and_keeps_fixed_receipt(monkeypatch: pytest.MonkeyPatch) -> None:
    termination = {
        "started": True, "never_started": False, "confirmed": False,
        "process_id": 987, "creation_filetime": None, "exit_filetime": None,
        "exit_code": None, "job_active_processes": 1, "output_drained": False,
        "cleanup_completed": False, "stop_cause": "CANCELLED",
    }

    def unconfirmed(*_args: object) -> object:
        raise executor_module._ExecutionFailure("TERMINATION_UNCONFIRMED", termination)

    monkeypatch.setattr(ControlledModelPackageExecutor, "_execute_windows", unconfirmed)
    result = _executor().execute({"op": "probe", "case": "sleep"})
    assert result["status"] == "FAILED"
    assert result["error_code"] == "TERMINATION_UNCONFIRMED"
    assert result["termination"]["confirmed"] is False
    assert result["termination"]["cleanup_completed"] is False
    assert result["termination"]["request_ref"] == result["request_ref"]
    assert result["termination"]["request_sha256"] == result["request_sha256"]


def test_unconfirmed_child_blocks_later_executor_before_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    blocker = {
        "request_ref": "prior-request", "request_sha256": "a" * 64,
        "started": True, "never_started": False, "confirmed": False,
        "process_id": 987, "creation_filetime": 1, "exit_filetime": None,
        "exit_code": None, "job_active_processes": 1, "output_drained": False,
        "cleanup_completed": False, "stop_cause": "CANCELLED",
    }
    monkeypatch.setattr(executor_module, "_UNCONFIRMED_CLEANUPS", [{"termination": blocker}])

    def forbidden_windows(*_args: object) -> object:
        raise AssertionError("must not start while an earlier child exit is unconfirmed")

    monkeypatch.setattr(ControlledModelPackageExecutor, "_execute_windows", forbidden_windows)
    result = _executor().execute({"op": "probe", "case": "started"})
    assert result["status"] == "FAILED"
    assert result["error_code"] == "RECOVERY_REQUIRED"
    assert result["blocking_termination"] == blocker


def test_executor_busy_slot_refuses_concurrent_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    assert executor_module._EXECUTION_GATE.acquire(blocking=False)
    try:
        result = _executor().execute({"op": "probe", "case": "started"})
    finally:
        executor_module._EXECUTION_GATE.release()
    assert result["status"] == "FAILED"
    assert result["error_code"] == "EXECUTOR_BUSY"
    assert result["termination"]["never_started"] is True


def test_rejects_user_commands_and_unknown_request_fields() -> None:
    executor = _executor()
    for request in (
        {"op": "probe", "case": "started", "command": "whoami"},
        {"op": "anything", "module": "evil"},
        {
            "op": "verify_gc_safetensors", "project_id": "p", "artifact_id": "a",
            "task_type": "gc", "package_path": "relative.zip", "expected_sha256": "0" * 64,
            "expected_size_bytes": 1, "allowed_loader_ids": ["gc-safetensors-v1"],
        },
    ):
        result = executor.execute(request)
        assert result["status"] == "FAILED"
        assert result["error_code"] == "INVALID_REQUEST"
