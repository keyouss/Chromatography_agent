"""Fixed, fail-closed Windows worker for trusted model-package verification."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any


_CHILD_MODULE = "chrom_platform.modules.model_center.model_package_child"
_MAX_REQUEST_BYTES = 64 * 1024
_MAX_STDIO_BYTES = 64 * 1024
_PRODUCTION_MEMORY_BYTES = 2 * 1024**3  # Initial ceiling; must be validated against the real CPU baseline.
_PRODUCTION_TIMEOUT_SECONDS = 120.0  # Initial ceiling; must be validated against the real CPU baseline.
_ALLOWED_PROBE_CASES = frozenset({"started", "alloc", "sleep", "attempt-child", "output", "crash"})
_UNCONFIRMED_CLEANUPS: list[dict[str, object]] = []
_EXECUTION_GATE = threading.Lock()
_TERMINATION_FIELDS = frozenset({
    "started", "never_started", "confirmed", "process_id", "creation_filetime",
    "exit_filetime", "exit_code", "job_active_processes", "output_drained",
    "cleanup_completed", "stop_cause",
})


@dataclass(frozen=True)
class _Limits:
    memory_bytes: int = _PRODUCTION_MEMORY_BYTES
    timeout_seconds: float = _PRODUCTION_TIMEOUT_SECONDS
    output_bytes: int = _MAX_STDIO_BYTES


_PRODUCTION_LIMITS = _Limits()
_REOBSERVE_BUDGET_SECONDS = 4.0
_ERROR_BROKEN_PIPE = 109
_MAPPING_PROXY_TYPE = type(MappingProxyType({}))


def _canonical_request(request: Mapping[str, object]) -> bytes:
    try:
        payload = json.dumps(
            request, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValueError("INVALID_REQUEST") from exc
    if len(payload) > _MAX_REQUEST_BYTES:
        raise ValueError("INVALID_REQUEST")
    return payload


def _validate_request(request: Mapping[str, object]) -> None:
    if not isinstance(request, Mapping):
        raise ValueError("INVALID_REQUEST")
    op = request.get("op")
    if op == "probe":
        if set(request) != {"op", "case"} or request.get("case") not in _ALLOWED_PROBE_CASES:
            raise ValueError("INVALID_REQUEST")
        return
    if op != "verify_gc_safetensors":
        raise ValueError("INVALID_REQUEST")
    required = {
        "op", "project_id", "artifact_id", "task_type", "package_path",
        "expected_sha256", "expected_size_bytes", "allowed_loader_ids",
    }
    if set(request) != required:
        raise ValueError("INVALID_REQUEST")
    for key in ("project_id", "artifact_id", "task_type", "package_path"):
        value = request.get(key)
        if not isinstance(value, str) or not value or len(value) > 4096:
            raise ValueError("INVALID_REQUEST")
    path = Path(str(request["package_path"]))
    if not path.is_absolute():
        raise ValueError("INVALID_REQUEST")
    digest = request.get("expected_sha256")
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("INVALID_REQUEST")
    size = request.get("expected_size_bytes")
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise ValueError("INVALID_REQUEST")
    loaders = request.get("allowed_loader_ids")
    if (not isinstance(loaders, list) or not loaders or len(loaders) > 16
            or any(not isinstance(item, str) or not item or len(item) > 128 for item in loaders)):
        raise ValueError("INVALID_REQUEST")


def _failure(request_ref: str, request_sha256: str, error_code: str) -> dict[str, object]:
    return {
        "request_ref": request_ref,
        "request_sha256": request_sha256,
        "status": "CANCELLED" if error_code == "CANCELLED" else "FAILED",
        "error_code": error_code,
    }


def _bound_termination(
    termination: object, request_ref: str, request_sha256: str,
) -> dict[str, object] | None:
    if not isinstance(termination, Mapping) or set(termination) != _TERMINATION_FIELDS:
        return None
    return {
        **dict(termination), "request_ref": request_ref, "request_sha256": request_sha256,
    }


def _send_stop_receipt(
    stop_sink: Callable[[str, str, Mapping[str, object]], None] | None,
    request_ref: str,
    request_sha256: str,
    termination: Mapping[str, object],
) -> bool:
    if stop_sink is None:
        return True
    try:
        return stop_sink(request_ref, request_sha256, termination) is None
    except Exception:
        return False


def _parent_exit_confirmed(termination: Mapping[str, object]) -> bool:
    return (
        _physical_exit_confirmed(termination)
        and termination.get("output_drained") is True
        and termination.get("cleanup_completed") is True
    )


def _physical_exit_confirmed(termination: Mapping[str, object]) -> bool:
    process_id = termination.get("process_id")
    creation = termination.get("creation_filetime")
    exit_time = termination.get("exit_filetime")
    exit_code = termination.get("exit_code")
    return (
        termination.get("started") is True
        and termination.get("never_started") is False
        and termination.get("confirmed") is True
        and type(process_id) is int and process_id > 0
        and type(creation) is int and creation > 0
        and type(exit_time) is int and exit_time >= creation
        and type(exit_code) is int and exit_code != 259
        and termination.get("job_active_processes") == 0
    )


def _complete_stop_receipt(termination: Mapping[str, object]) -> bool:
    if _parent_exit_confirmed(termination):
        return True
    return (
        termination.get("started") is False
        and termination.get("never_started") is True
        and termination.get("confirmed") is True
        and termination.get("process_id") is None
        and termination.get("creation_filetime") is None
        and termination.get("exit_filetime") is None
        and termination.get("exit_code") is None
        and termination.get("job_active_processes") == 0
        and termination.get("output_drained") is True
        and termination.get("cleanup_completed") is True
        and isinstance(termination.get("stop_cause"), str)
    )


def _block_local_recovery(
    request_ref: str,
    request_sha256: str,
    termination: Mapping[str, object] | None = None,
    *,
    handles: object | None = None,
    temp_dir: str | None = None,
    readers: object | None = None,
    writer: object | None = None,
    assigned: bool = False,
    stop_sink: Callable[[str, str, Mapping[str, object]], None] | None = None,
    cached_receipt: Mapping[str, object] | None = None,
) -> None:
    blocker = dict(termination or {
        "request_ref": request_ref,
        "request_sha256": request_sha256,
        "started": None,
        "never_started": None,
        "confirmed": False,
        "process_id": None,
        "creation_filetime": None,
        "exit_filetime": None,
        "exit_code": None,
        "job_active_processes": None,
        "output_drained": False,
        "cleanup_completed": False,
        "stop_cause": "RECOVERY_UNKNOWN",
    })
    trusted_proof = (
        getattr(handles, "_close_proof", None)
        if isinstance(handles, _NativeHandles) else None
    )
    proof_cache = None
    observed = getattr(handles, "_observed_termination", None) if handles is not None else None
    if (
        type(trusted_proof) is _MAPPING_PROXY_TYPE
        and isinstance(observed, Mapping)
        and all(trusted_proof.get(key) == observed.get(key) for key in _TERMINATION_FIELDS)
    ):
        proof_cache = _freeze_receipt({
            **trusted_proof, "request_ref": request_ref, "request_sha256": request_sha256,
        })
    bound_receipt = None
    if (
        cached_receipt
        and _complete_stop_receipt(cached_receipt)
        and cached_receipt.get("request_ref") == request_ref
        and cached_receipt.get("request_sha256") == request_sha256
    ):
        bound_receipt = _freeze_receipt(cached_receipt)
    _UNCONFIRMED_CLEANUPS.append({
        "termination": blocker, "handles": handles, "temp_dir": temp_dir,
        "readers": readers, "writer": writer, "assigned": assigned,
        "stop_sink": stop_sink,
        "request_ref": request_ref, "request_sha256": request_sha256,
        "cached_receipt": bound_receipt,
        "proof_cache": proof_cache,
        "pending": None, "last_error": None,
    })


def _freeze_receipt(receipt: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType(dict(receipt))


def _supervise_reobserve(blocker: dict[str, object]) -> None:
    """Run one bounded parent-handle observation and cleanup attempt.

    This background thread never calls the durable stop sink or clears the
    blocker. A complete receipt is only an in-process observation cache; the
    durable slot remains blocked. If I/O or cleanup outlives the budget, the
    pending entry continues to block new work.
    """
    deadline = time.monotonic() + _REOBSERVE_BUDGET_SECONDS
    blocker["last_error"] = None
    try:
        receipt = blocker.get("cached_receipt")
        if receipt is None:
            handles = blocker.get("handles")
            if not _reobserve_context_ready(blocker, handles):
                raise _ReobserveBlocked()
            proof = blocker.get("proof_cache")
            if proof is None:
                readers = blocker.get("readers")
                writer = blocker.get("writer")
                termination = handles.confirm_termination(
                    assigned=bool(blocker.get("assigned")),
                    stop_cause=str(blocker["termination"].get("stop_cause") or "RECOVERY_UNKNOWN"),
                    terminate=False,
                    timeout_ms=max(1, int((_remaining(deadline)) * 1000)),
                )
                if not _physical_exit_confirmed(termination):
                    raise _ReobserveBlocked()
                readers.join(_remaining(deadline))
                if not readers.drained:
                    raise _ReobserveBlocked()
                writer.join(_remaining(deadline))
                if writer.is_alive():
                    raise _ReobserveBlocked()

                # Freeze parent-handle observations before closing any handle.
                handles.authorize_close({
                    **termination, "output_drained": True, "cleanup_completed": False,
                })
                proof = _bind_internal_proof(handles, blocker)
                if proof is None:
                    raise _ReobserveBlocked()
                blocker["proof_cache"] = proof
            handles.close()
            temp_dir = blocker.get("temp_dir")
            if isinstance(temp_dir, str) and temp_dir:
                shutil.rmtree(temp_dir)
                blocker["temp_dir"] = None
            receipt = _freeze_receipt({**proof, "cleanup_completed": True})
            blocker["cached_receipt"] = receipt

        if not _complete_stop_receipt(receipt):
            raise _ReobserveBlocked()
        blocker["observation_complete"] = True
    except Exception:
        blocker["last_error"] = "RECOVERY_REQUIRED"
    finally:
        blocker["pending_done"] = True


def _remaining(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def _reobserve_context_ready(blocker: Mapping[str, object], handles: object) -> bool:
    proof_cache = blocker.get("proof_cache")
    if proof_cache is not None and type(proof_cache) is not _MAPPING_PROXY_TYPE:
        return False
    cleanup_from_proof = type(proof_cache) is _MAPPING_PROXY_TYPE
    if not (
        isinstance(handles, _NativeHandles)
        and type(blocker.get("assigned")) is bool
        and handles.created is True
        and (cleanup_from_proof or handles.job is not None)
        and (cleanup_from_proof or handles.process is not None)
        and isinstance(blocker.get("readers"), _Readers)
        and isinstance(blocker.get("writer"), _RequestWriter)
        and isinstance(blocker.get("temp_dir"), str)
        and bool(blocker.get("temp_dir"))
        and callable(blocker.get("stop_sink"))
        and isinstance(blocker.get("request_ref"), str)
        and bool(blocker.get("request_ref"))
        and isinstance(blocker.get("request_sha256"), str)
        and len(str(blocker.get("request_sha256"))) == 64
    ):
        return False
    if cleanup_from_proof:
        native_proof = handles._close_proof
        return isinstance(native_proof, Mapping) and all(
            proof_cache.get(key) == native_proof.get(key)
            for key in _TERMINATION_FIELDS
        )
    return True


def _bind_internal_proof(
    handles: "_NativeHandles", blocker: Mapping[str, object],
) -> Mapping[str, object] | None:
    proof = handles._close_proof
    observed = handles._observed_termination
    if (
        type(proof) is not _MAPPING_PROXY_TYPE
        or not isinstance(observed, Mapping)
        or any(proof.get(key) != observed.get(key) for key in _TERMINATION_FIELDS)
    ):
        return None
    request_ref = blocker.get("request_ref")
    request_sha256 = blocker.get("request_sha256")
    if not isinstance(request_ref, str) or not isinstance(request_sha256, str):
        return None
    return _freeze_receipt({
        **proof,
        "request_ref": request_ref,
        "request_sha256": request_sha256,
    })


class _ReobserveBlocked(Exception):
    pass


def _reobserve_blocker(blocker: dict[str, object]) -> bool:
    """Observe/cache parent proof; never sink it or authorize blocker removal."""
    pending = blocker.get("pending")
    if isinstance(pending, threading.Thread):
        if pending.is_alive():
            pending.join(_REOBSERVE_BUDGET_SECONDS)
        if pending.is_alive():
            return False
        blocker["pending"] = None
        # Consume this completed attempt only. A later foreground call may
        # retry unfinished observation/cleanup, but this call never starts a
        # second supervisor after waiting for an existing one.
        return False
    if blocker.get("observation_complete") is True:
        return False
    cached = blocker.get("cached_receipt")
    if isinstance(cached, Mapping) and _complete_stop_receipt(cached):
        blocker["observation_complete"] = True
        return False
    pending = threading.Thread(
        target=_supervise_reobserve, args=(blocker,),
        name="model-package-reobserve", daemon=True,
    )
    # Mark pending before start while _EXECUTION_GATE is held.
    blocker["pending"] = pending
    try:
        pending.start()
    except Exception:
        blocker["pending"] = None
        return False
    pending.join(_REOBSERVE_BUDGET_SECONDS)
    if pending.is_alive():
        return False
    blocker["pending"] = None
    return False


class ControlledModelPackageExecutor:
    """Run only the fixed package verifier module inside an attached Windows Job.

    ``_limits`` is private for low-ceiling probe tests. The package request has
    no control over process limits, module, command line, or environment.
    """

    def __init__(self, *, _limits: _Limits = _PRODUCTION_LIMITS) -> None:
        self._limits = _limits

    def execute(
        self,
        request: Mapping[str, object],
        *,
        cancelled: Callable[[], bool] | None = None,
        check_fence: Callable[[], None] | None = None,
        before_launch: Callable[[str, str], None] | None = None,
        stop_sink: Callable[[str, str, Mapping[str, object]], None] | None = None,
    ) -> Mapping[str, object]:
        request_ref = uuid.uuid4().hex
        try:
            _validate_request(request)
            payload = _canonical_request(request)
        except ValueError:
            return _failure(request_ref, "", "INVALID_REQUEST")
        request_sha256 = hashlib.sha256(payload).hexdigest()
        if request.get("op") == "verify_gc_safetensors" and (
            before_launch is None or stop_sink is None
        ):
            return _failure(request_ref, request_sha256, "EXECUTION_INTENT_UNAVAILABLE")
        if os.name != "nt":
            return _failure(request_ref, request_sha256, "HARD_LIMITS_UNAVAILABLE")
        try:
            self._check_control(cancelled, check_fence)
        except _ControlStop as stop:
            result = _failure(request_ref, request_sha256, stop.code)
            result["termination"] = {
                **_never_started_termination(),
                "request_ref": request_ref, "request_sha256": request_sha256,
            }
            return result

        return self._execute_guarded(
            request_ref, request_sha256, payload, request, cancelled, check_fence,
            before_launch, stop_sink,
        )

    def _execute_guarded(
        self,
        request_ref: str,
        request_sha256: str,
        payload: bytes,
        request: Mapping[str, object],
        cancelled: Callable[[], bool] | None,
        check_fence: Callable[[], None] | None,
        before_launch: Callable[[str, str], None] | None,
        stop_sink: Callable[[str, str, Mapping[str, object]], None] | None,
    ) -> Mapping[str, object]:
        if not _EXECUTION_GATE.acquire(blocking=False):
            result = _failure(request_ref, request_sha256, "EXECUTOR_BUSY")
            result["termination"] = {
                **_never_started_termination(),
                "request_ref": request_ref, "request_sha256": request_sha256,
            }
            return result
        try:
            if _UNCONFIRMED_CLEANUPS:
                blocker_entry = _UNCONFIRMED_CLEANUPS[0]
                # Reobserve may cache parent proof, but this executor cannot
                # commit the durable stop receipt or release its global slot.
                _reobserve_blocker(blocker_entry)
                if _UNCONFIRMED_CLEANUPS:
                    blocker = _UNCONFIRMED_CLEANUPS[0]["termination"]
                    result = _failure(request_ref, request_sha256, "RECOVERY_REQUIRED")
                    result["blocking_termination"] = blocker
                    return result
            if before_launch is not None:
                try:
                    if before_launch(request_ref, request_sha256) is not None:
                        _block_local_recovery(request_ref, request_sha256)
                        return _failure(request_ref, request_sha256, "EXECUTION_INTENT_UNAVAILABLE")
                except Exception:
                    # A failed or ambiguous intent commit must never start an OS child.
                    _block_local_recovery(request_ref, request_sha256)
                    return _failure(request_ref, request_sha256, "EXECUTION_INTENT_UNAVAILABLE")
            try:
                self._check_control(cancelled, check_fence)
            except _ControlStop as stop:
                termination = {
                    **_never_started_termination(),
                    "request_ref": request_ref, "request_sha256": request_sha256,
                }
                if before_launch is not None and not _send_stop_receipt(
                    stop_sink, request_ref, request_sha256, termination
                ):
                    _block_local_recovery(request_ref, request_sha256, termination)
                    result = _failure(request_ref, request_sha256, "STOP_RECEIPT_UNAVAILABLE")
                else:
                    result = _failure(request_ref, request_sha256, stop.code)
                result["termination"] = termination
                return result
            try:
                outcome, verified_limits = self._execute_windows(payload, request, cancelled, check_fence)
            except _ExecutionFailure as exc:
                result = _failure(request_ref, request_sha256, exc.code)
                termination = _bound_termination(exc.termination, request_ref, request_sha256)
                if termination is None:
                    _block_local_recovery(
                        request_ref, request_sha256,
                        handles=exc.handles, temp_dir=exc.temp_dir,
                        readers=exc.readers, writer=exc.writer, assigned=exc.assigned,
                        stop_sink=stop_sink,
                    )
                    return _failure(request_ref, request_sha256, "TERMINATION_UNCONFIRMED")
                if before_launch is not None and not _send_stop_receipt(
                    stop_sink, request_ref, request_sha256, termination
                ):
                    result = _failure(request_ref, request_sha256, "STOP_RECEIPT_UNAVAILABLE")
                result["termination"] = termination
                recovery_needed = (
                    exc.code == "TERMINATION_UNCONFIRMED"
                    or result.get("error_code") == "STOP_RECEIPT_UNAVAILABLE"
                    or termination.get("cleanup_completed") is not True
                )
                if recovery_needed:
                    _block_local_recovery(
                        request_ref, request_sha256, termination,
                        handles=exc.handles, temp_dir=exc.temp_dir,
                        readers=exc.readers, writer=exc.writer, assigned=exc.assigned,
                        stop_sink=stop_sink,
                        cached_receipt=(
                            termination
                            if exc.handles is None and _parent_exit_confirmed(termination)
                            else None
                        ),
                    )
                return result
            except Exception:
                # No full parent receipt means the persistent intent must stay blocked.
                _block_local_recovery(request_ref, request_sha256)
                return _failure(request_ref, request_sha256, "EXECUTOR_FAILED")

            termination = outcome.get("_termination")
            complete_termination = _bound_termination(termination, request_ref, request_sha256)
            if complete_termination is None:
                _block_local_recovery(request_ref, request_sha256)
                return _failure(request_ref, request_sha256, "TERMINATION_UNCONFIRMED")
            if before_launch is not None and not _send_stop_receipt(
                stop_sink, request_ref, request_sha256, complete_termination
            ):
                _block_local_recovery(
                    request_ref, request_sha256, complete_termination,
                    handles=outcome.get("_handles"), temp_dir=outcome.get("_temp_dir"),
                    stop_sink=stop_sink, cached_receipt=complete_termination,
                )
                result = _failure(request_ref, request_sha256, "STOP_RECEIPT_UNAVAILABLE")
                result["termination"] = complete_termination
                return result
            if not _parent_exit_confirmed(complete_termination):
                _block_local_recovery(request_ref, request_sha256, complete_termination)
                result = _failure(request_ref, request_sha256, "TERMINATION_UNCONFIRMED")
                result["termination"] = complete_termination
                return result
            return {
                "request_ref": request_ref,
                "request_sha256": request_sha256,
                "status": "SUCCEEDED",
                "termination": {
                    **complete_termination,
                },
                "hard_limits": {
                    **verified_limits,
                    "timeout_seconds": self._limits.timeout_seconds,
                },
                "evidence": {
                    key: value for key, value in outcome.items()
                    if key not in {"_termination", "_handles", "_temp_dir"}
                },
            }
        finally:
            _EXECUTION_GATE.release()

    @staticmethod
    def _check_control(
        cancelled: Callable[[], bool] | None,
        check_fence: Callable[[], None] | None,
    ) -> None:
        if cancelled is not None:
            try:
                if cancelled():
                    raise _ControlStop("CANCELLED")
            except _ControlStop:
                raise
            except Exception as exc:
                raise _ControlStop("CANCELLED") from exc
        if check_fence is not None:
            try:
                check_fence()
            except Exception as exc:
                raise _ControlStop("FENCE_REJECTED") from exc

    def _execute_windows(
        self,
        payload: bytes,
        request: Mapping[str, object],
        cancelled: Callable[[], bool] | None,
        check_fence: Callable[[], None] | None,
    ) -> tuple[Mapping[str, object], Mapping[str, object]]:
        api = _WindowsApi()
        handles = _NativeHandles(api)
        temp_dir = tempfile.mkdtemp(prefix="model-package-exec-")
        readers: _Readers | None = None
        writer: _RequestWriter | None = None
        assigned = False
        resumed = False
        stop_cause = "NATURAL_EXIT"
        primary_error: Exception | None = None
        try:
            handles.create_job(self._limits.memory_bytes)
            handles.create_pipes()
            handles.create_attribute_list()
            started = time.monotonic()
            handles.create_suspended_child(temp_dir)
            if not api.kernel32.AssignProcessToJobObject(handles.job, handles.process):
                raise _HardLimitUnavailable()
            assigned = True
            handles.assigned = True
            self._check_control(cancelled, check_fence)
            if api.kernel32.ResumeThread(handles.thread) == 0xFFFFFFFF:
                raise _HardLimitUnavailable()
            resumed = True
            handles.resumed = True
            handles.close_child_pipe_ends()
            readers = handles.start_readers(self._limits.output_bytes)
            writer = handles.start_request_writer(payload)
            stop_code = self._wait_for_child(
                api, handles, started, readers, writer, cancelled, check_fence
            )
            if stop_code is not None:
                stop_cause = stop_code
                raise _ControlStop(stop_code)
            readers.join(timeout=3.0)
            if not readers.drained:
                stop_cause = "OUTPUT_DRAIN_FAILED"
                raise _ChildFailed("OUTPUT_DRAIN_FAILED")
            if readers.exceeded:
                stop_cause = "OUTPUT_LIMIT"
                raise _OutputLimitExceeded()
            if writer is not None:
                writer.join(timeout=0.2)
                if writer.is_alive() or writer.error:
                    stop_cause = "REQUEST_WRITE"
                    raise _ChildFailed("REQUEST_WRITE")
            exit_code = handles.exit_code()
            if exit_code != 0:
                stop_cause = "CHILD_EXIT"
                if exit_code in {0xC0000017, 0xC000012D}:
                    raise _ChildFailed("RESOURCE_LIMIT")
                raise _ChildFailed("CHILD_FAILED")
            parsed = _parse_child_response(readers.stdout_data)
            primary_error = None
        except Exception as exc:
            primary_error = exc
            if isinstance(exc, _ControlStop):
                stop_cause = exc.code
            elif stop_cause == "NATURAL_EXIT":
                stop_cause = _error_code(exc)

        # A successful API call is not proof of termination. The parent's
        # process handle, FILETIMEs, exit code and Job accounting are the proof.
        try:
            termination = handles.confirm_termination(
                assigned=assigned,
                stop_cause=stop_cause,
                terminate=True,
                timeout_ms=5000,
            )
        except Exception:
            termination = handles._unconfirmed_termination(stop_cause)
        if termination["confirmed"] and readers is not None:
            readers.join(timeout=1.0)
            if writer is not None:
                writer.join(timeout=1.0)
            termination["output_drained"] = (
                readers.drained
                and (writer is None or not writer.is_alive())
            )
        else:
            termination["output_drained"] = readers is None and not handles.created
        if not termination["confirmed"] or not termination["output_drained"]:
            failed = dict(termination)
            failed["cleanup_completed"] = False
            raise _ExecutionFailure(
                "TERMINATION_UNCONFIRMED", failed, handles=handles, temp_dir=temp_dir,
                readers=readers, writer=writer, assigned=assigned,
            ) from primary_error

        try:
            handles.authorize_close(_freeze_receipt(termination))
            handles.close()
        except Exception:
            failed = dict(termination)
            failed["cleanup_completed"] = False
            raise _ExecutionFailure(
                "CLEANUP_FAILED", failed, handles=handles, temp_dir=temp_dir,
                readers=readers, writer=writer, assigned=assigned,
            ) from primary_error
        try:
            shutil.rmtree(temp_dir)
            termination["cleanup_completed"] = True
        except OSError:
            termination["cleanup_completed"] = False
            failed = dict(termination)
            raise _ExecutionFailure(
                "CLEANUP_FAILED", failed, handles=handles, temp_dir=temp_dir,
                readers=readers, writer=writer, assigned=assigned,
            ) from primary_error

        if primary_error is not None:
            if isinstance(primary_error, _ControlStop):
                raise _ExecutionFailure(primary_error.code, termination) from primary_error
            raise _ExecutionFailure(_error_code(primary_error), termination) from primary_error
        return {
            **parsed, "_termination": termination, "_handles": handles, "_temp_dir": None,
        }, handles.verified_limits

    def _wait_for_child(
        self,
        api: "_WindowsApi",
        handles: "_NativeHandles",
        started: float,
        readers: "_Readers",
        writer: "_RequestWriter",
        cancelled: Callable[[], bool] | None,
        check_fence: Callable[[], None] | None,
    ) -> str | None:
        while True:
            try:
                self._check_control(cancelled, check_fence)
            except _ControlStop as stop:
                return stop.code
            if readers.exceeded:
                return "OUTPUT_LIMIT"
            if writer.error:
                return "REQUEST_WRITE"
            remaining = self._limits.timeout_seconds - (time.monotonic() - started)
            if remaining <= 0:
                return "TIMEOUT"
            result = api.kernel32.WaitForSingleObject(handles.process, min(100, max(1, int(remaining * 1000))))
            if result == 0:
                return None
            if result == 0xFFFFFFFF:
                return "WAIT_FAILED"


class _ControlStop(Exception):
    def __init__(self, code: str) -> None:
        self.code = code


class _HardLimitUnavailable(Exception):
    pass


class _OutputLimitExceeded(Exception):
    pass


class _ChildOutputInvalid(Exception):
    pass


class _NativeLaunchFailed(Exception):
    def __init__(self, stage: str) -> None:
        self.stage = stage


class _ChildFailed(Exception):
    def __init__(self, code: str) -> None:
        self.code = code


class _ExecutionFailure(Exception):
    def __init__(
        self, code: str, termination: Mapping[str, object], *,
        handles: "_NativeHandles | None" = None, temp_dir: str | None = None,
        readers: "_Readers | None" = None, writer: "_RequestWriter | None" = None,
        assigned: bool = False,
    ) -> None:
        self.code = code
        self.termination = dict(termination)
        self.handles = handles
        self.temp_dir = temp_dir
        self.readers = readers
        self.writer = writer
        self.assigned = assigned


def _never_started_termination() -> dict[str, object]:
    return {
        "started": False, "never_started": True, "confirmed": True,
        "process_id": None, "creation_filetime": None, "exit_filetime": None,
        "exit_code": None, "job_active_processes": 0, "output_drained": True,
        "cleanup_completed": True, "stop_cause": "PRESTART",
    }


def _error_code(exc: Exception) -> str:
    if isinstance(exc, _HardLimitUnavailable):
        return "HARD_LIMITS_UNAVAILABLE"
    if isinstance(exc, _OutputLimitExceeded):
        return "OUTPUT_LIMIT"
    if isinstance(exc, _ChildOutputInvalid):
        return "CHILD_OUTPUT_INVALID"
    if isinstance(exc, _NativeLaunchFailed):
        return "CHILD_START_FAILED"
    if isinstance(exc, _ChildFailed):
        return exc.code
    return "EXECUTOR_FAILED"


def _parse_child_response(data: bytes) -> Mapping[str, object]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise _ChildOutputInvalid() from exc
    if not isinstance(value, dict) or value.get("status") not in {"SUCCEEDED", "FAILED"}:
        raise _ChildOutputInvalid()
    if value["status"] == "FAILED":
        code = value.get("error_code")
        if not isinstance(code, str) or code not in {"VERIFY_FAILED", "INVALID_REQUEST", "PROBE_FAILED"}:
            raise _ChildOutputInvalid()
        raise _ChildFailed(code)
    evidence = value.get("evidence")
    if not isinstance(evidence, dict):
        raise _ChildOutputInvalid()
    return evidence


class _ReaderState:
    def __init__(self, api: "_WindowsApi", handle: int, limit: int) -> None:
        self.api = api
        self.handle = handle
        self.limit = limit
        self.data = bytearray()
        self.total = 0
        self.exceeded = False
        self.eof = False
        self.read_error: int | str | None = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        buffer = ctypes.create_string_buffer(8192)
        read = ctypes.c_ulong()
        while True:
            try:
                ok = self.api.kernel32.ReadFile(self.handle, buffer, len(buffer), ctypes.byref(read), None)
                if not ok:
                    error = ctypes.get_last_error()
                    if error == _ERROR_BROKEN_PIPE:
                        self.eof = True
                    else:
                        self.read_error = error
                    return
                if not read.value:
                    # A successful zero-byte read is not proof of pipe EOF.
                    self.read_error = "ZERO_BYTE_READ"
                    return
            except Exception:
                self.read_error = "READ_FAILED"
                return
            chunk = buffer.raw[: read.value]
            self.total += len(chunk)
            available = self.limit - len(self.data)
            if available > 0:
                self.data.extend(chunk[:available])
            if self.total > self.limit:
                self.exceeded = True


class _Readers:
    def __init__(self, api: "_WindowsApi", stdout: int, stderr: int, limit: int) -> None:
        self.stdout = _ReaderState(api, stdout, limit)
        self.stderr = _ReaderState(api, stderr, limit)
        self.threads = [self.stdout.thread, self.stderr.thread]

    @property
    def exceeded(self) -> bool:
        return self.stdout.exceeded or self.stderr.exceeded

    @property
    def drained(self) -> bool:
        return all(
            state.eof and state.read_error is None and not state.thread.is_alive()
            for state in (self.stdout, self.stderr)
        )

    @property
    def stdout_data(self) -> bytes:
        return bytes(self.stdout.data)

    @property
    def stderr_data(self) -> bytes:
        return bytes(self.stderr.data)

    def start(self) -> None:
        for thread in self.threads:
            thread.start()

    def join(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        for thread in self.threads:
            thread.join(max(0.0, deadline - time.monotonic()))


class _RequestWriter:
    def __init__(self, handles: "_NativeHandles", payload: bytes) -> None:
        self.handles = handles
        self.payload = payload
        self.error = False
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        try:
            self.handles.write_request(self.payload)
        except Exception:
            self.error = True

    def start(self) -> None:
        self.thread.start()

    def join(self, timeout: float) -> None:
        self.thread.join(timeout)

    def is_alive(self) -> bool:
        return self.thread.is_alive()


class _WindowsApi:
    def __init__(self) -> None:
        if os.name != "nt":
            raise _HardLimitUnavailable()
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._declare()

    def _declare(self) -> None:
        k = self.kernel32
        k.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        k.CreateJobObjectW.restype = ctypes.c_void_p
        k.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong]
        k.SetInformationJobObject.restype = ctypes.c_int
        k.QueryInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]
        k.QueryInformationJobObject.restype = ctypes.c_int
        k.CreatePipe.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.c_ulong]
        k.CreatePipe.restype = ctypes.c_int
        k.SetHandleInformation.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong]
        k.SetHandleInformation.restype = ctypes.c_int
        k.InitializeProcThreadAttributeList.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.POINTER(ctypes.c_size_t)]
        k.InitializeProcThreadAttributeList.restype = ctypes.c_int
        k.UpdateProcThreadAttribute.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p]
        k.UpdateProcThreadAttribute.restype = ctypes.c_int
        k.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
        k.DeleteProcThreadAttributeList.restype = None
        k.CreateProcessW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_void_p]
        k.CreateProcessW.restype = ctypes.c_int
        k.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        k.AssignProcessToJobObject.restype = ctypes.c_int
        k.ResumeThread.argtypes = [ctypes.c_void_p]
        k.ResumeThread.restype = ctypes.c_ulong
        k.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        k.TerminateJobObject.restype = ctypes.c_int
        k.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        k.TerminateProcess.restype = ctypes.c_int
        k.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        k.WaitForSingleObject.restype = ctypes.c_ulong
        k.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        k.GetExitCodeProcess.restype = ctypes.c_int
        k.GetProcessTimes.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(_FileTime), ctypes.POINTER(_FileTime),
            ctypes.POINTER(_FileTime), ctypes.POINTER(_FileTime),
        ]
        k.GetProcessTimes.restype = ctypes.c_int
        k.WriteFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong), ctypes.c_void_p]
        k.WriteFile.restype = ctypes.c_int
        k.ReadFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong), ctypes.c_void_p]
        k.ReadFile.restype = ctypes.c_int
        k.CloseHandle.argtypes = [ctypes.c_void_p]
        k.CloseHandle.restype = ctypes.c_int


class _NativeHandles:
    def __init__(self, api: _WindowsApi) -> None:
        self.api = api
        self.job = None
        self.process = None
        self.thread = None
        self.stdin_read = self.stdin_write = None
        self.stdout_read = self.stdout_write = None
        self.stderr_read = self.stderr_write = None
        self.attribute_list = None
        self.attribute_buffer = None
        self.handle_array = None
        self.startup_info = None
        self._readers: _Readers | None = None
        self.verified_limits: Mapping[str, object] = {}
        self.process_id: int | None = None
        self.thread_id: int | None = None
        self.created = False
        self.assigned = False
        self.resumed = False
        self._writer: _RequestWriter | None = None
        self._observed_termination: Mapping[str, object] | None = None
        self._close_proof: Mapping[str, object] | None = None

    def create_job(self, memory_bytes: int) -> None:
        self.job = self.api.kernel32.CreateJobObjectW(None, None)
        if not self.job:
            raise _HardLimitUnavailable()
        limits = _ExtendedLimitInformation()
        expected_flags = 0x0008 | 0x0100 | 0x0200 | 0x2000
        limits.basic.limit_flags = expected_flags
        limits.basic.active_process_limit = 1
        limits.process_memory_limit = memory_bytes
        limits.job_memory_limit = memory_bytes
        if not self.api.kernel32.SetInformationJobObject(
            self.job, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            raise _HardLimitUnavailable()
        actual = _ExtendedLimitInformation()
        returned = ctypes.c_ulong()
        if not self.api.kernel32.QueryInformationJobObject(
            self.job, 9, ctypes.byref(actual), ctypes.sizeof(actual), ctypes.byref(returned)
        ):
            raise _HardLimitUnavailable()
        if (
            returned.value != ctypes.sizeof(actual)
            or actual.basic.limit_flags != expected_flags
            or actual.basic.active_process_limit != 1
            or actual.process_memory_limit != memory_bytes
            or actual.job_memory_limit != memory_bytes
        ):
            raise _HardLimitUnavailable()
        self.verified_limits = {
            "memory_bytes": int(actual.job_memory_limit),
            "process_memory_bytes": int(actual.process_memory_limit),
            "active_processes": int(actual.basic.active_process_limit),
            "kill_on_close": bool(actual.basic.limit_flags & 0x2000),
            "job_limit_flags": int(actual.basic.limit_flags),
            "readback_verified": True,
        }

    def create_pipes(self) -> None:
        self.stdin_read, self.stdin_write = self._pipe()
        self.stdout_read, self.stdout_write = self._pipe()
        self.stderr_read, self.stderr_write = self._pipe()
        for handle in (self.stdin_write, self.stdout_read, self.stderr_read):
            if not self.api.kernel32.SetHandleInformation(handle, 1, 0):
                raise _NativeLaunchFailed("HANDLE_INHERITANCE")

    def _pipe(self) -> tuple[int, int]:
        read_handle, write_handle = ctypes.c_void_p(), ctypes.c_void_p()
        security = _SecurityAttributes()
        security.length = ctypes.sizeof(security)
        security.inherit_handle = 1
        if not self.api.kernel32.CreatePipe(
            ctypes.byref(read_handle), ctypes.byref(write_handle), ctypes.byref(security), 0
        ):
            raise _NativeLaunchFailed("PIPE_CREATE")
        return read_handle.value, write_handle.value

    def create_attribute_list(self) -> None:
        self.startup_info = subprocess.STARTUPINFO()
        self.startup_info.dwFlags = 0x00000100  # STARTF_USESTDHANDLES
        self.startup_info.hStdInput = self.stdin_read
        self.startup_info.hStdOutput = self.stdout_write
        self.startup_info.hStdError = self.stderr_write
        self.startup_info.lpAttributeList = {
            "handle_list": [self.stdin_read, self.stdout_write, self.stderr_write]
        }

    def create_suspended_child(self, temp_dir: str) -> None:
        import _winapi

        executable = os.path.abspath(sys.executable)
        command = f'"{executable}" -m {_CHILD_MODULE}'
        env_values = _child_environment(temp_dir)
        flags = (
            0x00000004  # CREATE_SUSPENDED
            | 0x00080000  # EXTENDED_STARTUPINFO_PRESENT
            | _winapi.CREATE_NO_WINDOW
            | 0x00000400  # CREATE_UNICODE_ENVIRONMENT
        )
        try:
            self.process, self.thread, self.process_id, self.thread_id = _winapi.CreateProcess(
                executable, command, None, None, 1, flags, env_values, temp_dir,
                self.startup_info,
            )
            self.created = True
        except OSError as exc:
            raise _NativeLaunchFailed("CREATE_PROCESS") from exc

    def close_child_pipe_ends(self) -> None:
        for name in ("stdin_read", "stdout_write", "stderr_write"):
            self._close_name(name)

    def start_readers(self, limit: int) -> _Readers:
        self._readers = _Readers(self.api, self.stdout_read, self.stderr_read, limit)
        self._readers.start()
        return self._readers

    def start_request_writer(self, payload: bytes) -> _RequestWriter:
        writer = _RequestWriter(self, payload)
        self._writer = writer
        writer.start()
        return writer

    def write_request(self, payload: bytes) -> None:
        offset = 0
        while offset < len(payload):
            chunk = payload[offset:offset + 8192]
            buffer = ctypes.create_string_buffer(chunk)
            written = ctypes.c_ulong()
            if not self.api.kernel32.WriteFile(self.stdin_write, buffer, len(chunk), ctypes.byref(written), None):
                raise _NativeLaunchFailed("REQUEST_WRITE")
            if not written.value:
                raise _NativeLaunchFailed("REQUEST_WRITE")
            offset += written.value
        self._close_name("stdin_write")

    def wait_process(self, timeout_ms: int) -> None:
        if self.process:
            return int(self.api.kernel32.WaitForSingleObject(self.process, timeout_ms))
        return 0xFFFFFFFF

    def exit_code(self) -> int:
        value = ctypes.c_ulong()
        if not self.api.kernel32.GetExitCodeProcess(self.process, ctypes.byref(value)):
            raise _ChildFailed("WAIT_FAILED")
        return int(value.value)

    def process_times(self) -> tuple[int, int]:
        creation, exit_time, kernel, user = (_FileTime() for _ in range(4))
        if not self.api.kernel32.GetProcessTimes(
            self.process, ctypes.byref(creation), ctypes.byref(exit_time),
            ctypes.byref(kernel), ctypes.byref(user),
        ):
            raise OSError("GetProcessTimes failed")
        return creation.value, exit_time.value

    def job_active_processes(self) -> int:
        if not self.job:
            return 0
        accounting = _BasicAccountingInformation()
        returned = ctypes.c_ulong()
        if not self.api.kernel32.QueryInformationJobObject(
            self.job, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), ctypes.byref(returned)
        ):
            raise OSError("QueryInformationJobObject failed")
        if returned.value != ctypes.sizeof(accounting):
            raise OSError("Job accounting size mismatch")
        return int(accounting.active_processes)

    def confirm_termination(
        self, *, assigned: bool, stop_cause: str, terminate: bool, timeout_ms: int
    ) -> dict[str, object]:
        if not self.process:
            observed = {**_never_started_termination(), "stop_cause": stop_cause}
            self._observed_termination = _freeze_receipt(observed)
            return observed

        if terminate:
            # This is an attempt only; the checks below provide the evidence.
            try:
                if assigned and self.job:
                    self.api.kernel32.TerminateJobObject(self.job, 1)
                else:
                    self.api.kernel32.TerminateProcess(self.process, 1)
            except Exception:
                pass

        try:
            wait_result = self.wait_process(timeout_ms)
        except Exception:
            return self._unconfirmed_termination(stop_cause)
        if wait_result != 0:  # WAIT_OBJECT_0
            try:
                active = self.job_active_processes() if self.job else 0
            except Exception:
                active = None
            return self._unconfirmed_termination(stop_cause, active=active)
        try:
            exit_code = self.exit_code()
            creation_filetime, exit_filetime = self.process_times()
            if (
                not self.process_id or self.process_id <= 0
                or creation_filetime <= 0
                or exit_filetime < creation_filetime
                or exit_code == 259  # STILL_ACTIVE is not an exit observation.
            ):
                return self._unconfirmed_termination(
                    stop_cause, exit_code, creation_filetime, exit_filetime
                )
            deadline = time.monotonic() + min(2.0, timeout_ms / 1000)
            active = self.job_active_processes() if self.job else 0
            while active != 0 and time.monotonic() < deadline:
                time.sleep(0.025)
                active = self.job_active_processes()
            if active != 0:
                return self._unconfirmed_termination(stop_cause, exit_code, creation_filetime, exit_filetime, active)
            observed = {
                "started": bool(self.created), "never_started": not bool(self.created),
                "confirmed": True, "process_id": int(self.process_id or 0),
                "creation_filetime": creation_filetime, "exit_filetime": exit_filetime,
                "exit_code": exit_code, "job_active_processes": active,
                "output_drained": True, "cleanup_completed": False, "stop_cause": stop_cause,
            }
            self._observed_termination = _freeze_receipt(observed)
            return observed
        except Exception:
            return self._unconfirmed_termination(stop_cause)

    def _unconfirmed_termination(
        self, stop_cause: str, exit_code: int | None = None,
        creation_filetime: int | None = None, exit_filetime: int | None = None,
        active: int | None = None,
    ) -> dict[str, object]:
        return {
            "started": bool(self.created), "never_started": not bool(self.created),
            "confirmed": False, "process_id": int(self.process_id or 0),
            "creation_filetime": creation_filetime, "exit_filetime": exit_filetime,
            "exit_code": exit_code, "job_active_processes": active,
            "output_drained": False, "cleanup_completed": False, "stop_cause": stop_cause,
        }

    def close(self) -> None:
        if self.job is not None:
            proof = self._close_proof
            if proof is None or not (
                self.process is None and self.created is False
                and proof.get("never_started") is True
                and proof.get("confirmed") is True
                or _physical_exit_confirmed(proof)
                and proof.get("output_drained") is True
            ):
                raise OSError("Job close requires cached parent proof")
        if self.attribute_list:
            self.api.kernel32.DeleteProcThreadAttributeList(self.attribute_list)
            self.attribute_list = None
        for name in (
            "thread", "process", "stdin_read", "stdin_write", "stdout_read", "stdout_write",
            "stderr_read", "stderr_write", "job",
        ):
            self._close_name(name)

    def authorize_close(self, proof: Mapping[str, object]) -> None:
        observed = self._observed_termination
        if observed is None or any(
            proof.get(key) != observed.get(key) for key in _TERMINATION_FIELDS
        ):
            raise OSError("No matching parent-handle observation")
        no_child = (
            self.process is None and self.created is False
            and proof.get("never_started") is True and proof.get("confirmed") is True
        )
        if not no_child and (
            not _physical_exit_confirmed(proof) or proof.get("output_drained") is not True
        ):
            raise OSError("Parent termination proof is incomplete")
        if proof.get("process_id") != self.process_id:
            raise OSError("Parent termination identity mismatch")
        self._close_proof = _freeze_receipt(proof)

    def _close_name(self, name: str) -> None:
        value = getattr(self, name)
        if value is not None:
            if not self.api.kernel32.CloseHandle(value):
                raise OSError("CloseHandle failed")
            setattr(self, name, None)


def _child_environment(temp_dir: str) -> dict[str, str]:
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    system32 = str(Path(system_root) / "System32")
    executable_dir = str(Path(sys.executable).parent)
    library_bin = str(Path(sys.prefix) / "Library" / "bin")
    drive, tail = os.path.splitdrive(temp_dir)
    return {
        "SYSTEMROOT": system_root,
        "WINDIR": system_root,
        "HOME": temp_dir,
        "USERPROFILE": temp_dir,
        "HOMEDRIVE": drive,
        "HOMEPATH": tail,
        "PATH": os.pathsep.join((executable_dir, library_bin, system32)),
        "TEMP": temp_dir,
        "TMP": temp_dir,
        "PYTHONPATH": str(Path(__file__).resolve().parents[3]),
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUTF8": "1",
    }


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        "read_operation_count", "write_operation_count", "other_operation_count",
        "read_transfer_count", "write_transfer_count", "other_transfer_count",
    )]


class _FileTime(ctypes.Structure):
    _fields_ = [("low", ctypes.c_ulong), ("high", ctypes.c_ulong)]

    @property
    def value(self) -> int:
        return (int(self.high) << 32) | int(self.low)


class _BasicAccountingInformation(ctypes.Structure):
    _fields_ = [
        ("total_user_time", ctypes.c_longlong),
        ("total_kernel_time", ctypes.c_longlong),
        ("this_period_total_user_time", ctypes.c_longlong),
        ("this_period_total_kernel_time", ctypes.c_longlong),
        ("total_page_fault_count", ctypes.c_ulong),
        ("total_processes", ctypes.c_ulong),
        ("active_processes", ctypes.c_ulong),
        ("total_terminated_processes", ctypes.c_ulong),
    ]


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("per_process_user_time_limit", ctypes.c_longlong),
        ("per_job_user_time_limit", ctypes.c_longlong),
        ("limit_flags", ctypes.c_ulong),
        ("minimum_working_set_size", ctypes.c_size_t),
        ("maximum_working_set_size", ctypes.c_size_t),
        ("active_process_limit", ctypes.c_ulong),
        ("affinity", ctypes.c_size_t),
        ("priority_class", ctypes.c_ulong),
        ("scheduling_class", ctypes.c_ulong),
    ]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("basic", _BasicLimitInformation),
        ("io_counters", _IoCounters),
        ("process_memory_limit", ctypes.c_size_t),
        ("job_memory_limit", ctypes.c_size_t),
        ("peak_process_memory_used", ctypes.c_size_t),
        ("peak_job_memory_used", ctypes.c_size_t),
    ]


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [
        ("length", ctypes.c_ulong),
        ("security_descriptor", ctypes.c_void_p),
        ("inherit_handle", ctypes.c_int),
    ]


class _StartupInfo(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong), ("reserved", ctypes.c_wchar_p),
        ("desktop", ctypes.c_wchar_p), ("title", ctypes.c_wchar_p),
        ("x", ctypes.c_ulong), ("y", ctypes.c_ulong),
        ("x_size", ctypes.c_ulong), ("y_size", ctypes.c_ulong),
        ("x_count_chars", ctypes.c_ulong), ("y_count_chars", ctypes.c_ulong),
        ("fill_attribute", ctypes.c_ulong), ("dw_flags", ctypes.c_ulong),
        ("w_show_window", ctypes.c_ushort), ("cb_reserved2", ctypes.c_ushort),
        ("lp_reserved2", ctypes.c_void_p), ("stdin", ctypes.c_void_p),
        ("stdout", ctypes.c_void_p), ("stderr", ctypes.c_void_p),
    ]


class _StartupInfoEx(ctypes.Structure):
    _fields_ = [("startup", _StartupInfo), ("attribute_list", ctypes.c_void_p)]


class _ProcessInformation(ctypes.Structure):
    _fields_ = [
        ("process", ctypes.c_void_p), ("thread", ctypes.c_void_p),
        ("process_id", ctypes.c_ulong), ("thread_id", ctypes.c_ulong),
    ]


__all__ = ["ControlledModelPackageExecutor"]
