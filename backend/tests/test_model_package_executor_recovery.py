from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Mapping

import pytest

from chrom_platform.modules.model_center import model_package_executor as module
from chrom_platform.modules.model_center.model_package_executor import (
    ControlledModelPackageExecutor,
    _Limits,
)


@pytest.fixture(autouse=True)
def _isolate_local_recovery_blockers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "_UNCONFIRMED_CLEANUPS", [])


def _executor() -> ControlledModelPackageExecutor:
    return ControlledModelPackageExecutor(
        _limits=_Limits(memory_bytes=128 * 1024**2, timeout_seconds=5, output_bytes=8192)
    )


def _verify_request() -> dict[str, object]:
    return {
        "op": "verify_gc_safetensors",
        "project_id": "project",
        "artifact_id": "artifact",
        "task_type": "gc",
        "package_path": str(Path.cwd() / "synthetic.zip"),
        "expected_sha256": "a" * 64,
        "expected_size_bytes": 1,
        "allowed_loader_ids": ["gc-safetensors-v1"],
    }


def _confirmed(*, started: bool = True) -> dict[str, object]:
    return {
        "started": started,
        "never_started": not started,
        "confirmed": True,
        "process_id": 7654 if started else None,
        "creation_filetime": 100 if started else None,
        "exit_filetime": 200 if started else None,
        "exit_code": 1 if started else None,
        "job_active_processes": 0,
        "output_drained": True,
        "cleanup_completed": True,
        "stop_cause": "CHILD_EXIT",
    }


def _result(termination: Mapping[str, object]) -> tuple[dict[str, object], dict[str, object]]:
    return (
        {"probe_case": "synthetic", "_termination": dict(termination)},
        {"readback_verified": True},
    )


def test_package_verification_requires_persistent_lifecycle_hooks_before_os(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        ControlledModelPackageExecutor,
        "_execute_windows",
        lambda *_args: pytest.fail("OS launch path must not run without lifecycle hooks"),
    )

    result = _executor().execute(_verify_request())

    assert result["status"] == "FAILED"
    assert result["error_code"] == "EXECUTION_INTENT_UNAVAILABLE"
    assert "termination" not in result


def test_intent_commit_precedes_launch_and_stop_sink_precedes_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(module, "_UNCONFIRMED_CLEANUPS", [])
    events: list[str] = []

    def before_launch(_ref: str, _sha: str) -> None:
        events.append("intent-committed")

    def execute_windows(*_args: object) -> tuple[dict[str, object], dict[str, object]]:
        events.append("os-launch-and-parent-proof")
        return _result(_confirmed())

    def stop_sink(_ref: str, _sha: str, termination: Mapping[str, object]) -> None:
        assert termination["confirmed"] is True
        events.append("stop-receipt-committed")

    monkeypatch.setattr(ControlledModelPackageExecutor, "_execute_windows", execute_windows)
    result = _executor().execute(
        _verify_request(), before_launch=before_launch, stop_sink=stop_sink
    )

    assert result["status"] == "SUCCEEDED"
    assert events == ["intent-committed", "os-launch-and-parent-proof", "stop-receipt-committed"]


def test_ambiguous_intent_commit_never_launches_and_never_claims_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(module, "_UNCONFIRMED_CLEANUPS", [])
    events: list[str] = []

    def before_launch(_ref: str, _sha: str) -> None:
        events.append("intent-commit-ambiguous")
        raise RuntimeError("private database detail")

    def forbidden_launch(*_args: object) -> object:
        pytest.fail("child must not launch after ambiguous intent commit")

    def forbidden_sink(*_args: object) -> None:
        pytest.fail("ambiguous intent must not emit a clearing stop receipt")

    monkeypatch.setattr(ControlledModelPackageExecutor, "_execute_windows", forbidden_launch)
    result = _executor().execute(
        _verify_request(), before_launch=before_launch, stop_sink=forbidden_sink
    )

    assert result["status"] == "FAILED"
    assert result["error_code"] == "EXECUTION_INTENT_UNAVAILABLE"
    assert "termination" not in result
    assert events == ["intent-commit-ambiguous"]
    assert "private database detail" not in repr(result)


def test_non_none_intent_result_fails_closed_without_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(module, "_UNCONFIRMED_CLEANUPS", [])
    monkeypatch.setattr(
        ControlledModelPackageExecutor,
        "_execute_windows",
        lambda *_args: pytest.fail("unexpected OS launch"),
    )
    result = _executor().execute(
        _verify_request(), before_launch=lambda *_args: False, stop_sink=lambda *_args: None
    )
    assert result["error_code"] == "EXECUTION_INTENT_UNAVAILABLE"
    assert "termination" not in result


@pytest.mark.parametrize("mode", ["cancel", "fence"])
def test_control_change_after_intent_but_before_native_launch_closes_intent(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    monkeypatch.setattr(module, "os", SimpleNamespace(name="nt"))
    events: list[str] = []
    calls = 0

    def before_launch(_ref: str, _sha: str) -> None:
        events.append("intent-committed")

    def cancelled() -> bool:
        nonlocal calls
        calls += 1
        return calls >= 2

    def check_fence() -> None:
        nonlocal calls
        calls += 1
        if calls >= 2:
            raise RuntimeError("private fence detail")

    def forbidden_launch(*_args: object) -> object:
        pytest.fail("control recheck must precede all OS launch calls")

    def stop_sink(_ref: str, _sha: str, termination: Mapping[str, object]) -> None:
        assert termination["never_started"] is True
        assert termination["confirmed"] is True
        events.append("no-child-receipt-committed")

    monkeypatch.setattr(ControlledModelPackageExecutor, "_execute_windows", forbidden_launch)
    result = _executor().execute(
        _verify_request(),
        cancelled=cancelled if mode == "cancel" else None,
        check_fence=check_fence if mode == "fence" else None,
        before_launch=before_launch,
        stop_sink=stop_sink,
    )

    assert result["error_code"] == ("CANCELLED" if mode == "cancel" else "FENCE_REJECTED")
    assert result["termination"]["never_started"] is True
    assert events == ["intent-committed", "no-child-receipt-committed"]


def test_unconfirmed_parent_receipt_is_sent_to_sink_and_keeps_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(module, "_UNCONFIRMED_CLEANUPS", [])
    retained_handles = object()
    retained_temp_dir = r"C:\private-temp"
    observed: list[Mapping[str, object]] = []
    termination = {
        "started": True,
        "never_started": False,
        "confirmed": False,
        "process_id": 7654,
        "creation_filetime": 100,
        "exit_filetime": None,
        "exit_code": None,
        "job_active_processes": 1,
        "output_drained": False,
        "cleanup_completed": False,
        "stop_cause": "CANCELLED",
    }

    def before_launch(_ref: str, _sha: str) -> None:
        return None

    def execute_windows(*_args: object) -> object:
        raise module._ExecutionFailure(
            "TERMINATION_UNCONFIRMED", termination,
            handles=retained_handles, temp_dir=retained_temp_dir,
        )

    def stop_sink(_ref: str, _sha: str, receipt: Mapping[str, object]) -> None:
        observed.append(dict(receipt))

    monkeypatch.setattr(ControlledModelPackageExecutor, "_execute_windows", execute_windows)
    result = _executor().execute(
        _verify_request(), before_launch=before_launch, stop_sink=stop_sink
    )

    assert result["error_code"] == "TERMINATION_UNCONFIRMED"
    assert result["termination"]["confirmed"] is False
    assert len(observed) == 1
    assert observed[0]["request_ref"] == result["request_ref"]
    assert observed[0]["request_sha256"] == result["request_sha256"]
    blocker = module._UNCONFIRMED_CLEANUPS[0]
    assert blocker["handles"] is retained_handles
    assert blocker["temp_dir"] == retained_temp_dir


def test_sink_failure_returns_failed_with_original_parent_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(module, "_UNCONFIRMED_CLEANUPS", [])
    monkeypatch.setattr(module, "_UNCONFIRMED_CLEANUPS", [])
    termination = _confirmed()
    monkeypatch.setattr(
        ControlledModelPackageExecutor,
        "_execute_windows",
        lambda *_args: _result(termination),
    )

    def stop_sink(*_args: object) -> None:
        raise RuntimeError("private storage detail")

    result = _executor().execute(
        _verify_request(), before_launch=lambda *_args: None, stop_sink=stop_sink
    )

    assert result["status"] == "FAILED"
    assert result["error_code"] == "STOP_RECEIPT_UNAVAILABLE"
    assert result["termination"]["confirmed"] is True
    assert result["termination"]["process_id"] == 7654
    assert "private storage detail" not in repr(result)
    assert module._UNCONFIRMED_CLEANUPS


def test_success_without_parent_termination_is_blocked_not_fabricated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(module, "_UNCONFIRMED_CLEANUPS", [])
    monkeypatch.setattr(
        ControlledModelPackageExecutor,
        "_execute_windows",
        lambda *_args: ({"probe_case": "missing-receipt"}, {"readback_verified": True}),
    )

    result = _executor().execute(
        _verify_request(),
        before_launch=lambda *_args: None,
        stop_sink=lambda *_args: pytest.fail("must not invent a stop receipt"),
    )

    assert result["status"] == "FAILED"
    assert result["error_code"] == "TERMINATION_UNCONFIRMED"
    assert "termination" not in result
    assert module._UNCONFIRMED_CLEANUPS


@pytest.mark.parametrize("error", [module._ControlStop("CANCELLED"), module._HardLimitUnavailable()])
def test_unexpected_preproof_exception_never_fabricates_never_started(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    monkeypatch.setattr(module, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(module, "_UNCONFIRMED_CLEANUPS", [])
    monkeypatch.setattr(
        ControlledModelPackageExecutor,
        "_execute_windows",
        lambda *_args: (_ for _ in ()).throw(error),
    )

    result = _executor().execute(
        _verify_request(),
        before_launch=lambda *_args: None,
        stop_sink=lambda *_args: pytest.fail("no parent receipt exists"),
    )

    assert result["error_code"] == "EXECUTOR_FAILED"
    assert "termination" not in result
    assert module._UNCONFIRMED_CLEANUPS


def test_malformed_parent_receipt_still_retains_handle_and_tempdir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "os", SimpleNamespace(name="nt"))
    retained_handles = object()
    retained_temp_dir = r"C:\recovery-evidence"
    monkeypatch.setattr(
        ControlledModelPackageExecutor,
        "_execute_windows",
        lambda *_args: (_ for _ in ()).throw(module._ExecutionFailure(
            "TERMINATION_UNCONFIRMED", {"missing": "allowlist"},
            handles=retained_handles, temp_dir=retained_temp_dir,
        )),
    )

    result = _executor().execute(
        _verify_request(), before_launch=lambda *_args: None,
        stop_sink=lambda *_args: pytest.fail("malformed parent receipt must not reach sink"),
    )

    assert result["error_code"] == "TERMINATION_UNCONFIRMED"
    assert "termination" not in result
    blocker = module._UNCONFIRMED_CLEANUPS[0]
    assert blocker["handles"] is retained_handles
    assert blocker["temp_dir"] == retained_temp_dir


class _ParentKernel:
    def TerminateProcess(self, *_args: object) -> int:
        return 1

    def WaitForSingleObject(self, *_args: object) -> int:
        return 0

    def GetExitCodeProcess(self, _process: object, output: object) -> int:
        output._obj.value = 1  # type: ignore[attr-defined]
        return 1

    def GetProcessTimes(self, _process: object, creation: object, exit_time: object, *_args: object) -> int:
        creation._obj.low, creation._obj.high = 100, 0  # type: ignore[attr-defined]
        exit_time._obj.low, exit_time._obj.high = 200, 0  # type: ignore[attr-defined]
        return 1

    def QueryInformationJobObject(self, _job: object, _kind: int, info: object, size: int, returned: object) -> int:
        info._obj.active_processes = 0  # type: ignore[attr-defined]
        returned._obj.value = size  # type: ignore[attr-defined]
        return 1


def test_created_suspended_process_is_not_misreported_never_started() -> None:
    handles = object.__new__(module._NativeHandles)
    handles.api = SimpleNamespace(kernel32=_ParentKernel())
    handles.job = object()
    handles.process = object()
    handles.process_id = 7654
    handles.created = True
    handles.resumed = False

    receipt = handles.confirm_termination(
        assigned=False, stop_cause="ASSIGN_FAILED", terminate=True, timeout_ms=50
    )

    assert receipt["confirmed"] is True
    assert receipt["started"] is True
    assert receipt["never_started"] is False
    assert receipt["process_id"] == 7654

