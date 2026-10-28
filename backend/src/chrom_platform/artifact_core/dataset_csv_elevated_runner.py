"""One fixed, one-shot Defender runner for the isolated dataset CSV window."""

from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import hashlib
import json
import logging
import os
from pathlib import Path
import secrets
import subprocess
import struct
import sys
import threading
import time
from datetime import datetime, timezone
from contextvars import ContextVar, Token
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .scanner_defender import (
    DEFENDER_EXE,
    MAX_OUTPUT_BYTES,
    SCAN_TIMEOUT_SECONDS,
    ProcessResult,
    _run_bounded_process,
)


BUILD_ID = "data-center-invalid-window-20261026"
PROJECT_ID = "project_data_accept_20261026"
PROFILE_ID = "dataset-csv-tlc-header-v1-20261026"
INPUT_SHA256 = "95d934f576bd1ec2ff6c6b496b4e85543b36ccee96c9ddfebdd33561aa98154f"
INPUT_SIZE_BYTES = 247
RUNTIME_ROOT = Path(r"C:\Users\13459\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0\LocalCache\Local\ChromatographyPlatform\data-accept-20261026")
ARTIFACT_ROOT = RUNTIME_ROOT / "artifacts"
STATE_ROOT = RUNTIME_ROOT / "state"
PACKAGE_ROOT = Path(__file__).resolve().parents[2]
REQUEST_PATH = STATE_ROOT / "dataset-csv-scan-v1-audit02-20261027.request.json"
LEDGER_PATH = STATE_ROOT / "dataset-csv-scan-v1-audit02-20261027.used"
HELPER_MODULE = "chrom_platform.artifact_core.dataset_csv_elevated_runner"
PYTHON_EXE = Path(r"D:\Anaconda\envs\SFC\python.exe")
MAX_FRAME_BYTES = 100_000
_LEDGER_STARTED_FIELDS = frozenset({
    "schema", "status", "request_sha256", "build_id", "project_id", "profile_id",
    "artifact_id", "attempt_id", "input_sha256", "size_bytes", "source_manifest_sha256",
    "started_at_utc",
})
_LEDGER_COMPLETE_FIELDS = _LEDGER_STARTED_FIELDS | frozenset({
    "scan_completed", "completed_at_utc", "helper_pid", "helper_creation_id", "scan_returncode",
})
ERROR_TIMEOUT = "SCAN_TIMEOUT_COMPLETION_UNKNOWN"
DEFENDER_EXE_SHA256 = "a4291c5b4325895d13d32e232bf50c1b41356306803ac5dc75bba9aecb021141"
DEFENDER_PRODUCT_VERSION = "4.18.25080.5"
DEFENDER_ENGINE_VERSION = "1.1.26100.4"
DEFENDER_SIGNER = "CN=Microsoft Windows, O=Microsoft Corporation, L=Redmond, S=Washington, C=US"
SEE_MASK_NOCLOSEPROCESS = 0x00000040
SEE_MASK_NOASYNC = 0x00000100
_SOURCE_PIN_REASONS = frozenset({
    "SOURCE_MANIFEST_PIN_MISSING", "SOURCE_MANIFEST_PIN_MISMATCH",
    "HELPER_PACKAGE_ROOT_MISMATCH", "HELPER_SOURCE_MISMATCH",
})
_TARGET_REASONS = frozenset({
    "FIXED_BINDING_MISMATCH", "PATH_OUTSIDE_FIXED_RUNTIME", "RUNTIME_REPARSE_REJECTED",
    "TARGET_PATH_MISMATCH", "TARGET_REPARSE_REJECTED", "TARGET_IDENTITY_INVALID",
    "TARGET_BYTES_MISMATCH", "TARGET_IDENTITY_CHANGED",
})
_NATIVE_STAGES = frozenset({
    "UAC_WAIT", "SHELLEXECUTE", "HELPER_IDENTITY", "PIPE_CONNECT",
    "PIPE_READ_HEADER", "PIPE_READ_BODY", "HELPER_EXIT", "TRUSTED_RESULT",
})
_NATIVE_REASONS = frozenset({
    "UAC_WAIT_TIMEOUT", "UAC_CANCELLED", "SHELLEXECUTE_FAILED",
    "SHELLEXECUTE_LATE_HANDLE", "HELPER_IDENTITY_CAPTURED",
    "HELPER_IDENTITY_QUERY_FAILED", "PIPE_CONNECT_TIMEOUT", "PIPE_CONNECT_FAILED",
    "PIPE_READ_TIMEOUT", "PIPE_READ_FAILED", "HELPER_EXIT_TIMEOUT",
    "HELPER_EXIT_FAILED", "DEFENDER_TIMEOUT", "TRUSTED_RESULT",
    "NATIVE_TRANSPORT_FAILED",
})


def _fixed_exception_reason(exc: Exception, allowed: frozenset[str], fallback: str) -> str:
    reason = exc.args[0] if isinstance(exc, ValueError) and len(exc.args) == 1 else None
    return reason if type(reason) is str and reason in allowed else fallback


def _log_binding_value(value: Any) -> str:
    if (not isinstance(value, str) or not value or len(value) > 128
            or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for char in value)):
        return "invalid"
    return value


def _log_preflight_rejection(stage: str, reason: str, binding: DatasetScanBinding) -> None:
    try:
        sha256 = binding.sha256
        sha_summary = sha256[:12] if isinstance(sha256, str) and len(sha256) == 64 and all(c in "0123456789abcdef" for c in sha256.lower()) else "invalid"
        identity = binding.identity
        identity_hash = (
            hashlib.sha256(f"{identity[0]}:{identity[1]}".encode("ascii")).hexdigest()[:16]
            if isinstance(identity, tuple) and len(identity) == 2 and all(type(part) is int for part in identity)
            else "unavailable"
        )
        size = binding.size_bytes if type(binding.size_bytes) is int and binding.size_bytes >= 0 else -1
        logging.getLogger(__name__).warning(
            "DATASET_SCAN_PREFLIGHT_REJECTED stage=%s reason=%s artifact_id=%s project_id=%s "
            "profile_id=%s sha256_prefix=%s size_bytes=%d identity_sha256=%s",
            stage, reason, _log_binding_value(binding.artifact_id),
            _log_binding_value(binding.project_id), _log_binding_value(binding.profile_id),
            sha_summary, size, identity_hash,
        )
    except Exception:
        # Logging is diagnostic only and must never affect the fail-closed result.
        pass


def _log_native_observation(
    stage: str,
    reason: str,
    launch_state: str,
    *,
    launched_pid: int = 0,
    creation_id: int = 0,
    image_matches: bool = False,
    peer_authenticated: bool = False,
    request_sha256: str = "",
) -> None:
    """Persist only fixed diagnostic enums and non-secret process identity evidence."""
    try:
        if (not isinstance(stage, str) or stage not in _NATIVE_STAGES
                or not isinstance(reason, str) or reason not in _NATIVE_REASONS
                or not isinstance(launch_state, str)
                or launch_state not in {"NOT_STARTED", "STARTED", "UNKNOWN"}):
            stage, reason, launch_state = "SHELLEXECUTE", "NATIVE_TRANSPORT_FAILED", "UNKNOWN"
        request_prefix = (
            request_sha256[:12]
            if isinstance(request_sha256, str) and len(request_sha256) == 64
            and all(char in "0123456789abcdef" for char in request_sha256.lower())
            else "unavailable"
        )
        logging.getLogger(__name__).warning(
            "DATASET_SCAN_NATIVE at_utc=%s request_sha256_prefix=%s stage=%s reason=%s launch_state=%s launched_pid=%d "
            "creation_id=%d image_matches=%s peer_authenticated=%s",
            datetime.now(timezone.utc).isoformat(), request_prefix, stage, reason, launch_state,
            launched_pid if type(launched_pid) is int and launched_pid > 0 else 0,
            creation_id if type(creation_id) is int and creation_id > 0 else 0,
            bool(image_matches), bool(peer_authenticated),
        )
    except Exception:
        # Diagnostics must never affect the fail-closed result or handle cleanup.
        pass


def _capture_launch_identity(kernel32: Any, process_handle: Any) -> tuple[int, int, bool]:
    """Capture redacted launch identity before pipe waits; authentication remains later."""
    pid = creation_id = 0
    image_matches = False
    try:
        kernel32.GetProcessId.argtypes = [wintypes.HANDLE]
        kernel32.GetProcessId.restype = wintypes.DWORD
        pid = int(kernel32.GetProcessId(process_handle))
    except Exception:
        pass
    try:
        creation_id = _process_creation_id(kernel32, process_handle)
    except Exception:
        pass
    try:
        image = ctypes.create_unicode_buffer(32768)
        image_size = wintypes.DWORD(len(image))
        if kernel32.QueryFullProcessImageNameW(process_handle, 0, image, ctypes.byref(image_size)):
            image_matches = os.path.normcase(os.path.normpath(image.value)) == os.path.normcase(os.path.normpath(str(PYTHON_EXE)))
    except Exception:
        pass
    return pid, creation_id, image_matches


def _observe_launch_handle(
    kernel32: Any,
    process_handle: Any,
    *,
    request_sha256: str = "",
    late: bool = False,
    close: Callable[[Any], Any] | None = None,
) -> tuple[int, int, bool]:
    try:
        pid, creation_id, image_matches = _capture_launch_identity(kernel32, process_handle)
        try:
            _log_native_observation(
                "SHELLEXECUTE" if late else "HELPER_IDENTITY",
                "SHELLEXECUTE_LATE_HANDLE" if late else (
                    "HELPER_IDENTITY_CAPTURED" if pid > 0 and creation_id > 0 else "HELPER_IDENTITY_QUERY_FAILED"
                ),
                "STARTED", launched_pid=pid, creation_id=creation_id,
                image_matches=image_matches, request_sha256=request_sha256,
            )
        except Exception:
            pass
        return pid, creation_id, image_matches
    finally:
        if late and close is not None:
            close(process_handle)


def _native_timeout_reason(stage: str, code: Any) -> str:
    known = {
        ("UAC_WAIT", "UAC_WAIT_TIMEOUT"): "UAC_WAIT_TIMEOUT",
        ("PIPE_CONNECT", "PIPE_CONNECT_TIMEOUT"): "PIPE_CONNECT_TIMEOUT",
        ("PIPE_READ_HEADER", "PIPE_READ_TIMEOUT"): "PIPE_READ_TIMEOUT",
        ("PIPE_READ_BODY", "PIPE_READ_TIMEOUT"): "PIPE_READ_TIMEOUT",
        ("HELPER_EXIT", "HELPER_EXIT_UNKNOWN"): "HELPER_EXIT_TIMEOUT",
        ("HELPER_EXIT", "ELEVATED_SCAN_DEADLINE_EXPIRED"): "HELPER_EXIT_TIMEOUT",
    }
    return known.get((stage, code), "NATIVE_TRANSPORT_FAILED")


def _fixed_shell_execute_mask() -> int:
    return SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC


@dataclass(frozen=True)
class DatasetScanBinding:
    path: Path
    identity: tuple[int, int]
    project_id: str
    profile_id: str
    artifact_id: str
    attempt_id: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class ElevatedProcessResult:
    process: ProcessResult
    launched_pid: int
    client_pid: int
    elevated: bool
    image_path: str
    creation_id: int
    user_sid: str
    request_sha256: str
    nonce: str


class DatasetCsvElevatedScanner:
    """Adds claim binding to the existing scanner without changing its classifier."""

    def __init__(self, scanner: Any, runner: DatasetCsvElevatedRunner):
        self._scanner = scanner
        self._runner = runner

    def scan_dataset(
        self,
        *,
        path: Path,
        artifact_id: str,
        input_sha256: str,
        input_size_bytes: int,
        scan_attempt_id: str,
        project_id: str,
        profile_id: str,
        target_identity: tuple[int, int],
    ) -> Any:
        binding = DatasetScanBinding(
            path=path,
            identity=target_identity,
            project_id=project_id,
            profile_id=profile_id,
            artifact_id=artifact_id,
            attempt_id=scan_attempt_id,
            sha256=input_sha256,
            size_bytes=input_size_bytes,
        )
        with self._runner.bind(binding):
            return self._scanner.scan(
                path=path,
                artifact_id=artifact_id,
                input_sha256=input_sha256,
                input_size_bytes=input_size_bytes,
                scan_attempt_id=scan_attempt_id,
            )


def _file_identity(path: Path) -> tuple[int, int]:
    info = path.stat(follow_symlinks=False)
    if not info.st_ino or path.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError("TARGET_IDENTITY_INVALID")
    return int(info.st_dev), int(info.st_ino)


def _digest(path: Path) -> tuple[int, str]:
    h = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            size += len(block)
            h.update(block)
    return size, h.hexdigest()


def _binding_payload(binding: DatasetScanBinding) -> dict[str, Any]:
    return {
        "build_id": BUILD_ID,
        "project_id": binding.project_id,
        "profile_id": binding.profile_id,
        "artifact_id": binding.artifact_id,
        "attempt_id": binding.attempt_id,
        "input_sha256": binding.sha256,
        "size_bytes": binding.size_bytes,
        "identity": list(binding.identity),
        "path": str(binding.path),
    }


def _reject_reparse_path(path: Path) -> None:
    """Require an existing path under the fixed runtime with no reparse component."""
    root = Path(os.path.abspath(RUNTIME_ROOT))
    candidate = Path(os.path.abspath(path))
    for checked in (root, candidate):
        current = Path(checked.anchor)
        for component in checked.parts[1:]:
            current = current / component
            info = current.lstat()
            if current.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("RUNTIME_REPARSE_REJECTED")
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("PATH_OUTSIDE_FIXED_RUNTIME") from exc


def _check_target(binding: Mapping[str, Any]) -> tuple[Path, tuple[int, int]]:
    if (
        binding.get("build_id") != BUILD_ID
        or binding.get("project_id") != PROJECT_ID
        or binding.get("profile_id") != PROFILE_ID
        or binding.get("input_sha256") != INPUT_SHA256
        or binding.get("size_bytes") != INPUT_SIZE_BYTES
        or not isinstance(binding.get("artifact_id"), str)
        or not binding["artifact_id"]
        or any(c in binding["artifact_id"] for c in "/\\.:\x00")
        or not isinstance(binding.get("attempt_id"), str)
        or not binding["attempt_id"]
    ):
        raise ValueError("FIXED_BINDING_MISMATCH")
    expected_path = ARTIFACT_ROOT / "objects" / binding["artifact_id"] / "content"
    _reject_reparse_path(expected_path)
    expected = Path(os.path.abspath(expected_path))
    supplied = Path(os.path.abspath(str(binding.get("path", ""))))
    if os.path.normcase(str(supplied)) != os.path.normcase(str(expected)):
        raise ValueError("TARGET_PATH_MISMATCH")
    current = ARTIFACT_ROOT
    for component in expected.relative_to(Path(os.path.abspath(ARTIFACT_ROOT))).parts:
        current = current / component
        info = current.lstat()
        if current.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("TARGET_REPARSE_REJECTED")
    identity = _file_identity(expected)
    size, digest = _digest(expected)
    if size != INPUT_SIZE_BYTES or digest != INPUT_SHA256:
        raise ValueError("TARGET_BYTES_MISMATCH")
    if binding.get("identity") != list(identity):
        raise ValueError("TARGET_IDENTITY_CHANGED")
    return expected, identity


def _read_runtime_manifest_pin() -> str:
    runtime_file = RUNTIME_ROOT / "runtime.json"
    _reject_reparse_path(runtime_file)
    runtime = json.loads(runtime_file.read_text(encoding="utf-8-sig"))
    raw = runtime.get("CHROM_BACKEND_SOURCE_MANIFEST_SHA256", "") if isinstance(runtime, dict) else ""
    if len(raw) != 64 or any(c not in "0123456789abcdef" for c in raw.lower()):
        raise ValueError("SOURCE_MANIFEST_PIN_MISSING")
    return raw.lower()


def _source_manifest_pin() -> str:
    raw = _read_runtime_manifest_pin()
    package_root = Path(__file__).resolve().parents[2]
    if package_root != PACKAGE_ROOT.resolve():
        raise ValueError("HELPER_PACKAGE_ROOT_MISMATCH")
    manifest_path = package_root / "SOURCE_MANIFEST.json"
    manifest = manifest_path.read_bytes()
    if hashlib.sha256(manifest).hexdigest() != raw.lower():
        raise ValueError("SOURCE_MANIFEST_PIN_MISMATCH")
    document = json.loads(manifest)
    relative = "chrom_platform/artifact_core/dataset_csv_elevated_runner.py"
    expected = next((item.get("sha256") for item in document.get("files", []) if item.get("path") == relative), None)
    if expected != hashlib.sha256(Path(__file__).read_bytes()).hexdigest():
        raise ValueError("HELPER_SOURCE_MISMATCH")
    return raw


class DatasetCsvElevatedRunner:
    """Callable runner; context is supplied only by the dataset worker branch."""

    def __init__(self, *, source_manifest_sha256: str, transport: Callable[..., ElevatedProcessResult] | None = None):
        self.source_manifest_sha256 = source_manifest_sha256.lower()
        self._transport = transport or _windows_named_pipe_transport
        self._binding: ContextVar[DatasetScanBinding | None] = ContextVar("dataset_csv_scan_binding", default=None)

    @contextmanager
    def bind(self, binding: DatasetScanBinding):
        token: Token = self._binding.set(binding)
        try:
            yield
        finally:
            self._binding.reset(token)

    def __call__(self, argv: list[str], *, timeout: float, max_output_bytes: int) -> ProcessResult:
        binding = self._binding.get()
        if not isinstance(binding, DatasetScanBinding):
            return ProcessResult(None, b"", error_code="DATASET_SCAN_BINDING_REQUIRED")
        return self.run(binding, argv, timeout=timeout, max_output_bytes=max_output_bytes)

    def run(self, binding: DatasetScanBinding, argv: list[str], *, timeout: float, max_output_bytes: int) -> ProcessResult:
        if (
            max_output_bytes != MAX_OUTPUT_BYTES
            or not 0 < timeout <= SCAN_TIMEOUT_SECONDS
            or argv != [str(DEFENDER_EXE), "-Scan", "-ScanType", "3", "-File", str(binding.path), "-DisableRemediation"]
        ):
            return ProcessResult(None, b"", error_code="FIXED_SCAN_COMMAND_REJECTED")
        try:
            source_pin = _source_manifest_pin()
        except Exception as exc:
            reason = _fixed_exception_reason(exc, _SOURCE_PIN_REASONS, "SOURCE_MANIFEST_CHECK_FAILED")
            _log_preflight_rejection("source_manifest", reason, binding)
            return ProcessResult(None, b"", error_code=reason)
        if self.source_manifest_sha256 != source_pin:
            _log_preflight_rejection("source_manifest", "PIN_MISMATCH", binding)
            return ProcessResult(None, b"", error_code="SOURCE_MANIFEST_PIN_MISMATCH")
        try:
            _check_target(_binding_payload(binding))
        except Exception as exc:
            reason = _fixed_exception_reason(exc, _TARGET_REASONS, "TARGET_INVALID")
            _log_preflight_rejection("target", reason, binding)
            error_code = "DATASET_SCAN_TARGET_INVALID" if reason == "TARGET_INVALID" else f"DATASET_SCAN_{reason}"
            return ProcessResult(None, b"", error_code=error_code)
        try:
            caller_is_elevated = _current_token_elevated()
        except Exception:
            reason = "CALLER_TOKEN_ELEVATION_QUERY_FAILED"
            _log_preflight_rejection("caller_token_elevation", reason, binding)
            return ProcessResult(None, b"", error_code=reason)
        if caller_is_elevated:
            _log_preflight_rejection("caller_token_elevation", "CALLER_IS_ELEVATED", binding)
            return ProcessResult(None, b"", error_code="CALLER_MUST_BE_UNELEVATED")
        nonce = secrets.token_hex(16)
        try:
            caller_sid = _current_user_sid()
        except Exception:
            return ProcessResult(None, b"", error_code="CALLER_TOKEN_QUERY_FAILED")
        request = {
            "schema": "dataset-csv-elevated-scan-v1",
            "nonce": nonce,
            "pipe_name": rf"\\.\pipe\ChromPlatform.DatasetCsvScan.{nonce}",
            "parent_pid": os.getpid(),
            "deadline_utc": time.time() + timeout,
            "deadline_monotonic": time.monotonic() + timeout,
            "source_manifest_sha256": self.source_manifest_sha256,
            "caller_sid": caller_sid,
            "build_id": BUILD_ID,
            "project_id": binding.project_id,
            "profile_id": binding.profile_id,
            "artifact_id": binding.artifact_id,
            "attempt_id": binding.attempt_id,
            "input_sha256": binding.sha256,
            "size_bytes": binding.size_bytes,
            "identity": list(binding.identity),
            "path": str(binding.path),
        }
        request_bytes = json.dumps(request, sort_keys=True, separators=(",", ":")).encode("utf-8")
        request_hash = hashlib.sha256(request_bytes).hexdigest()
        if not self._reserve_once(request_bytes):
            return ProcessResult(None, b"", error_code="DATASET_SCAN_QUOTA_USED")
        try:
            _write_exclusive(REQUEST_PATH, request_bytes)
        except Exception:
            return ProcessResult(None, b"", error_code="DATASET_SCAN_REQUEST_UNAVAILABLE")
        try:
            result = self._transport(
                request=request,
                request_sha256=request_hash,
                timeout=timeout,
                max_output_bytes=max_output_bytes,
            )
        except PermissionError:
            return ProcessResult(None, b"", error_code="UAC_CANCELLED")
        except TimeoutError:
            return ProcessResult(None, b"", timed_out=True, error_code=ERROR_TIMEOUT)
        except Exception:
            return ProcessResult(None, b"", reader_incomplete=True, error_code="ELEVATED_SCAN_TRANSPORT_FAILED")
        if (
            result.nonce != nonce
            or result.request_sha256 != request_hash
            or result.launched_pid <= 0
            or result.client_pid != result.launched_pid
            or not result.elevated
            or result.user_sid != caller_sid
            or os.path.normcase(os.path.normpath(result.image_path)) != os.path.normcase(os.path.normpath(str(PYTHON_EXE)))
            or result.creation_id <= 0
        ):
            return ProcessResult(None, b"", reader_incomplete=True, error_code="ELEVATED_SCAN_PEER_UNTRUSTED")
        process = result.process
        if (
            not isinstance(process, ProcessResult)
            or not isinstance(process.output, bytes)
            or len(process.output) > max_output_bytes
            or (process.returncode is not None and type(process.returncode) is not int)
        ):
            return ProcessResult(None, b"", reader_incomplete=True, error_code="ELEVATED_SCAN_RESULT_INVALID")
        if (type(process.returncode) is int and not process.timed_out
                and not process.reader_incomplete and not process.output_truncated
                and process.error_code is None):
            if not self._complete_ledger(request_hash, result):
                return ProcessResult(None, b"", reader_incomplete=True, error_code="DATASET_SCAN_LEDGER_COMPLETION_UNCERTAIN")
        return process

    @staticmethod
    def _reserve_once(request: bytes) -> bool:
        try:
            if not STATE_ROOT.is_dir():
                return False
            _reject_reparse_path(STATE_ROOT)
            document = json.loads(request)
            ledger = {
                "schema": "dataset-csv-scan-ledger-v1",
                "status": "STARTED",
                "request_sha256": hashlib.sha256(request).hexdigest(),
                **{key: document[key] for key in (
                    "build_id", "project_id", "profile_id", "artifact_id", "attempt_id", "input_sha256", "size_bytes",
                )},
                "source_manifest_sha256": document["source_manifest_sha256"],
                "started_at_utc": datetime.now(timezone.utc).isoformat(),
            }
            _write_exclusive(LEDGER_PATH, json.dumps(ledger, sort_keys=True, separators=(",", ":")).encode("utf-8"))
            return True
        except FileExistsError:
            return False
        except Exception:
            return False

    @staticmethod
    def _complete_ledger(request_hash: str, result: ElevatedProcessResult) -> bool:
        try:
            _reject_reparse_path(LEDGER_PATH)
            ledger = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
            if (not isinstance(ledger, dict) or ledger.get("schema") != "dataset-csv-scan-ledger-v1"
                    or set(ledger) != _LEDGER_STARTED_FIELDS
                    or ledger.get("status") != "STARTED" or ledger.get("request_sha256") != request_hash):
                return False
            ledger.update({
                "status": "COMPLETE",
                "scan_completed": True,
                "completed_at_utc": datetime.now(timezone.utc).isoformat(),
                "helper_pid": result.launched_pid,
                "helper_creation_id": result.creation_id,
                "scan_returncode": result.process.returncode,
            })
            with LEDGER_PATH.open("wb") as stream:
                stream.write(json.dumps(ledger, sort_keys=True, separators=(",", ":")).encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            return True
        except Exception:
            return False


def _write_exclusive(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def ledger_processes_idle(path: str | Path | None = None) -> bool:
    """Read-only gate: absent ledger is idle; only a fixed, complete claim permits idle."""
    marker = LEDGER_PATH if path is None else Path(path)
    try:
        _reject_reparse_path(marker.parent)
        marker_info = marker.lstat()
    except FileNotFoundError:
        return True
    except Exception:
        return False
    if marker.is_symlink() or getattr(marker_info, "st_file_attributes", 0) & 0x400:
        return False
    try:
        ledger = json.loads(marker.read_text(encoding="utf-8"))
        if (not isinstance(ledger, dict)
                or set(ledger) != _LEDGER_COMPLETE_FIELDS
                or ledger.get("schema") != "dataset-csv-scan-ledger-v1"
                or ledger.get("status") != "COMPLETE"
                or ledger.get("scan_completed") is not True
                or ledger.get("build_id") != BUILD_ID
                or ledger.get("project_id") != PROJECT_ID
                or ledger.get("profile_id") != PROFILE_ID
                or not isinstance(ledger.get("request_sha256"), str)
                or len(ledger["request_sha256"]) != 64
                or any(c not in "0123456789abcdef" for c in ledger["request_sha256"])):
            return False
        if (ledger.get("input_sha256") != INPUT_SHA256
                or type(ledger.get("size_bytes")) is not int or ledger["size_bytes"] != INPUT_SIZE_BYTES
                or not isinstance(ledger.get("source_manifest_sha256"), str)
                or len(ledger["source_manifest_sha256"]) != 64
                or any(c not in "0123456789abcdef" for c in ledger["source_manifest_sha256"])
                or ledger["source_manifest_sha256"] != _read_runtime_manifest_pin()):
            return False
        for key in ("artifact_id", "attempt_id"):
            value = ledger.get(key)
            if (not isinstance(value, str) or not value
                    or any(c in value for c in "/\\:\x00")):
                return False
        for key in ("helper_pid", "helper_creation_id", "scan_returncode"):
            if type(ledger.get(key)) is not int:
                return False
        if ledger["helper_pid"] <= 0 or ledger["helper_creation_id"] <= 0 or ledger["scan_returncode"] < 0:
            return False
        started = ledger.get("started_at_utc")
        completed = ledger.get("completed_at_utc")
        if not isinstance(started, str) or not isinstance(completed, str):
            return False
        started_at = datetime.fromisoformat(started)
        completed_at = datetime.fromisoformat(completed)
        if started_at.tzinfo is None or completed_at.tzinfo is None or completed_at < started_at:
            return False
        return True
    except Exception:
        return False


def _helper_result(request: Mapping[str, Any], request_hash: str, timeout: float) -> dict[str, Any]:
    if request.get("schema") != "dataset-csv-elevated-scan-v1":
        raise ValueError("REQUEST_SCHEMA_INVALID")
    if request.get("source_manifest_sha256") != _source_manifest_pin():
        raise ValueError("REQUEST_SOURCE_PIN_INVALID")
    if os.name != "nt" or request.get("caller_sid") != _current_user_sid():
        raise ValueError("HELPER_USER_SID_MISMATCH")
    if (not _current_token_elevated()
            or os.path.normcase(os.path.normpath(sys.executable)) != os.path.normcase(os.path.normpath(str(PYTHON_EXE)))):
        raise ValueError("HELPER_EXECUTION_IDENTITY_INVALID")
    path, identity = _check_target(request)
    target_fd = _lock_target_windows(path, identity)
    try:
        return _helper_result_locked(request, request_hash, timeout, path, identity)
    finally:
        if target_fd is not None:
            os.close(target_fd)


def _helper_result_locked(request: Mapping[str, Any], request_hash: str, timeout: float, path: Path, identity: tuple[int, int]) -> dict[str, Any]:
    from .scanner_defender import _read_status, _sha256_file, _valid_status
    status = _read_status()
    initial_size, initial_sha256 = _digest(path)
    if (
        not _valid_status(status)
        or status.product_version != DEFENDER_PRODUCT_VERSION
        or status.engine_version != DEFENDER_ENGINE_VERSION
        or status.signer_subject != DEFENDER_SIGNER
        or status.exe_sha256 != DEFENDER_EXE_SHA256
        or _sha256_file(DEFENDER_EXE) != DEFENDER_EXE_SHA256
        or initial_size != INPUT_SIZE_BYTES
        or initial_sha256 != INPUT_SHA256
        or _file_identity(path) != identity
    ):
        raise ValueError("DEFENDER_IDENTITY_INVALID")
    profile = request.get("profile_id")
    attempt = request.get("attempt_id")
    artifact = request.get("artifact_id")
    if not all(isinstance(v, str) and v for v in (profile, attempt, artifact)):
        raise ValueError("REQUEST_BINDING_INVALID")
    remaining = float(request.get("deadline_monotonic", 0)) - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("SCAN_DEADLINE_EXPIRED")
    scan = _run_bounded_process(
        [str(DEFENDER_EXE), "-Scan", "-ScanType", "3", "-File", str(path), "-DisableRemediation"],
        timeout=min(remaining, float(timeout), SCAN_TIMEOUT_SECONDS),
        max_output_bytes=MAX_OUTPUT_BYTES,
    )
    after = _read_status()
    if (
        not _valid_status(after)
        or after.product_version != status.product_version
        or after.engine_version != status.engine_version
        or after.signer_subject != status.signer_subject
        or after.exe_sha256 != DEFENDER_EXE_SHA256
        or _sha256_file(DEFENDER_EXE) != DEFENDER_EXE_SHA256
    ):
        raise ValueError("DEFENDER_IDENTITY_CHANGED")
    size, digest = _digest(path)
    if identity != _file_identity(path) or size != INPUT_SIZE_BYTES or digest != INPUT_SHA256:
        raise ValueError("TARGET_CHANGED_DURING_SCAN")
    return {
        "schema": "dataset-csv-elevated-result-v1",
        "nonce": request["nonce"],
        "request_sha256": request_hash,
        "project_id": PROJECT_ID,
        "profile_id": PROFILE_ID,
        "artifact_id": artifact,
        "attempt_id": attempt,
        "input_sha256": digest,
        "size_bytes": size,
        "identity": list(identity),
        "returncode": scan.returncode,
        "output_b64": base64.b64encode(scan.output[:MAX_OUTPUT_BYTES]).decode("ascii"),
        "timed_out": scan.timed_out,
        "output_truncated": scan.output_truncated,
        "reader_incomplete": scan.reader_incomplete,
        "error_code": scan.error_code,
    }


def _helper_main() -> int:
    try:
        _reject_reparse_path(REQUEST_PATH)
        if REQUEST_PATH.stat().st_size > 16_384:
            return 2
        request_bytes = REQUEST_PATH.read_bytes()
        request = json.loads(request_bytes)
        if not isinstance(request, dict):
            return 2
        nonce = request.get("nonce")
        if not isinstance(nonce, str) or len(nonce) != 32 or any(c not in "0123456789abcdef" for c in nonce):
            return 2
        if request.get("pipe_name") != rf"\\.\pipe\ChromPlatform.DatasetCsvScan.{nonce}":
            return 2
        request_hash = hashlib.sha256(request_bytes).hexdigest()
        return _serve_request(request, request_hash, timeout=SCAN_TIMEOUT_SECONDS)
    except TimeoutError:
        return 3
    except Exception:
        return 4


def _serve_request(
    request: Mapping[str, Any],
    request_hash: str,
    *,
    timeout: float,
    connect: Callable[[Mapping[str, Any]], tuple[Any, int]] | None = None,
    send: Callable[[Any, Mapping[str, Any]], int] | None = None,
    close: Callable[[Any], None] | None = None,
) -> int:
    pipe_handle, server_pid = (connect or _connect_helper_pipe)(request)
    if server_pid != request.get("parent_pid") or type(server_pid) is not int:
        (close or _close_pipe_handle)(pipe_handle)
        raise ValueError("PIPE_SERVER_PID_MISMATCH")
    try:
        result = _helper_result(request, request_hash, timeout)
        return (send or _write_pipe_result)(pipe_handle, result)
    finally:
        (close or _close_pipe_handle)(pipe_handle)


def _connect_helper_pipe(request: Mapping[str, Any]) -> tuple[Any, int]:
    pipe_name = request.get("pipe_name")
    if os.name != "nt" or not isinstance(pipe_name, str) or not pipe_name.startswith(r"\\.\pipe\ChromPlatform.DatasetCsvScan."):
        raise OSError("PIPE_NAME_INVALID")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.CreateFileW(pipe_name, 0x40000000, 0, None, 3, 0, None)
    if handle in (0, wintypes.HANDLE(-1).value):
        raise OSError("PIPE_CONNECT_FAILED")
    server_pid = wintypes.ULONG()
    kernel32.GetNamedPipeServerProcessId.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG)]
    kernel32.GetNamedPipeServerProcessId.restype = wintypes.BOOL
    if not kernel32.GetNamedPipeServerProcessId(handle, ctypes.byref(server_pid)):
        kernel32.CloseHandle(handle)
        raise OSError("PIPE_SERVER_PID_QUERY_FAILED")
    return handle, int(server_pid.value)


def _write_pipe_result(handle: Any, result: Mapping[str, Any]) -> int:
    if os.name != "nt":
        return 5
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    body = json.dumps(result, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_FRAME_BYTES:
        return 7
    frame = struct.pack("<I", len(body)) + body
    buffer = ctypes.create_string_buffer(frame)
    written = wintypes.DWORD()
    kernel32.WriteFile.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
    kernel32.WriteFile.restype = wintypes.BOOL
    kernel32.FlushFileBuffers.argtypes = [wintypes.HANDLE]
    kernel32.FlushFileBuffers.restype = wintypes.BOOL
    if not kernel32.WriteFile(handle, buffer, len(frame), ctypes.byref(written), None) or written.value != len(frame):
        return 8
    if not kernel32.FlushFileBuffers(handle):
        return 9
    return 0


def _close_pipe_handle(handle: Any) -> None:
    if os.name == "nt":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle(handle)


def _windows_named_pipe_transport(*, request: Mapping[str, Any], request_sha256: str, timeout: float, max_output_bytes: int) -> ElevatedProcessResult:
    """Native implementation is intentionally Windows-only and one-shot."""
    if os.name != "nt" or max_output_bytes != MAX_OUTPUT_BYTES:
        raise OSError("NATIVE_ELEVATION_UNAVAILABLE")
    return _run_win32_pipe(request, request_sha256, timeout)


def _run_win32_pipe(request: Mapping[str, Any], request_sha256: str, timeout: float) -> ElevatedProcessResult:
    """Use a SID-scoped local pipe and authenticate its client against ShellExecuteEx."""
    if os.name != "nt":
        raise OSError("WINDOWS_ELEVATION_REQUIRED")
    deadline = float(request.get("deadline_monotonic", 0))
    if deadline <= time.monotonic():
        raise TimeoutError("ELEVATED_SCAN_DEADLINE_EXPIRED")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.LPWSTR)]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.DWORD)]
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    kernel32.CreateNamedPipeW.restype = wintypes.HANDLE
    kernel32.CreateNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID]
    kernel32.ConnectNamedPipe.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
    kernel32.ConnectNamedPipe.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetNamedPipeClientProcessId.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG)]
    kernel32.GetNamedPipeClientProcessId.restype = wintypes.BOOL
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL

    class SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Sid", wintypes.LPVOID), ("Attributes", wintypes.DWORD)]

    class TOKEN_USER(ctypes.Structure):
        _fields_ = [("User", SID_AND_ATTRIBUTES)]

    class SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("nLength", wintypes.DWORD), ("lpSecurityDescriptor", wintypes.LPVOID), ("bInheritHandle", wintypes.BOOL)]

    class SHELLEXECUTEINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD), ("fMask", wintypes.ULONG), ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", wintypes.LPVOID), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE),
        ]

    def remaining_ms() -> int:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("ELEVATED_SCAN_DEADLINE_EXPIRED")
        return max(1, int(remaining * 1000))

    # Build an explicit current-user/SYSTEM DACL for the one-instance local pipe.
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise OSError("TOKEN_QUERY_FAILED")
    sid_buffer = ctypes.create_string_buffer(4096)
    needed = wintypes.DWORD()
    if (not advapi32.GetTokenInformation(token, 1, sid_buffer, len(sid_buffer), ctypes.byref(needed))
            or not needed.value or needed.value > len(sid_buffer)):
        kernel32.CloseHandle(token)
        raise OSError("TOKEN_USER_QUERY_FAILED")
    user_sid = ctypes.cast(sid_buffer, ctypes.POINTER(TOKEN_USER)).contents.User.Sid
    sid_text = wintypes.LPWSTR()
    if not advapi32.ConvertSidToStringSidW(user_sid, ctypes.byref(sid_text)):
        kernel32.CloseHandle(token)
        raise OSError("TOKEN_SID_QUERY_FAILED")
    caller_sid = str(sid_text.value)
    sddl = f"D:P(A;;GA;;;SY)(A;;GA;;;{sid_text.value})"
    security_descriptor = wintypes.LPVOID()
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(security_descriptor), None):
        kernel32.LocalFree(sid_text)
        kernel32.CloseHandle(token)
        raise OSError("PIPE_DACL_CREATE_FAILED")
    sa = SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), security_descriptor, False)
    name = str(request.get("pipe_name", ""))
    if not name.startswith(r"\\.\pipe\ChromPlatform.DatasetCsvScan."):
        raise OSError("PIPE_NAME_INVALID")
    kernel32.CreateNamedPipeW.restype = wintypes.HANDLE
    # One instance, reject remote clients, and use synchronous nonblocking I/O.
    pipe = kernel32.CreateNamedPipeW(name, 0x00080003, 0x00000009, 1, 0, MAX_FRAME_BYTES + 4, 0, ctypes.byref(sa))
    kernel32.LocalFree(security_descriptor)
    kernel32.LocalFree(sid_text)
    kernel32.CloseHandle(token)
    if pipe in (0, wintypes.HANDLE(-1).value):
        raise OSError("PIPE_CREATE_FAILED")
    process_handle: Any = None
    launch_done = threading.Event()
    launch_box: dict[str, Any] = {}
    launch_abandoned = threading.Event()

    def launch() -> None:
        info = None
        try:
            info = SHELLEXECUTEINFO()
            info.cbSize = ctypes.sizeof(info)
            info.fMask = _fixed_shell_execute_mask()
            info.lpVerb = "runas"
            info.lpFile = str(PYTHON_EXE)
            info.lpParameters = subprocess.list2cmdline(_fixed_elevation_arguments())
            info.lpDirectory = str(PACKAGE_ROOT)
            info.nShow = 0
            launch_box["ok"], launch_box["error"] = _shell_execute_sta(
                ole32, shell32, info, SHELLEXECUTEINFO,
            )
        except BaseException:
            launch_box["ok"] = False
            launch_box["error"] = -1
            launch_box["launch_error"] = "UAC_STA_INITIALIZATION_OR_EXECUTION_FAILED"
        finally:
            try:
                if info is not None:
                    launch_box["info"] = info
                    if launch_abandoned.is_set() and info.hProcess:
                        _observe_launch_handle(
                            kernel32, info.hProcess, request_sha256=request_sha256,
                            late=True, close=kernel32.CloseHandle,
                        )
                        info.hProcess = None
            finally:
                launch_done.set()

    launcher = threading.Thread(target=launch, name="dataset-csv-uac-once", daemon=True)
    launcher.start()
    stage = "UAC_WAIT"
    launch_state = "UNKNOWN"
    launched_pid = creation_id = 0
    image_matches = False
    peer_authenticated = False
    try:
        while not launch_done.wait(min(0.05, max(0.001, deadline - time.monotonic()))):
            if time.monotonic() >= deadline:
                launch_abandoned.set()
                raise TimeoutError("UAC_WAIT_TIMEOUT")
        info = launch_box.get("info")
        if info is None or not info.hProcess:
            if launch_box.get("error") == 1223 and not launch_box.get("launch_error"):
                stage, launch_state = "SHELLEXECUTE", "NOT_STARTED"
                _log_native_observation("SHELLEXECUTE", "UAC_CANCELLED", "NOT_STARTED", request_sha256=request_sha256)
                raise PermissionError("UAC_CANCELLED")
            if launch_box.get("ok") is False and not launch_box.get("launch_error"):
                stage, launch_state = "SHELLEXECUTE", "NOT_STARTED"
                _log_native_observation("SHELLEXECUTE", "SHELLEXECUTE_FAILED", "NOT_STARTED", request_sha256=request_sha256)
            else:
                stage, launch_state = "SHELLEXECUTE", "UNKNOWN"
                _log_native_observation("SHELLEXECUTE", "NATIVE_TRANSPORT_FAILED", "UNKNOWN", request_sha256=request_sha256)
            raise OSError("SHELLEXECUTE_FAILED")
        process_handle = info.hProcess
        launch_state = "STARTED"
        stage = "HELPER_IDENTITY"
        launched_pid, creation_id, image_matches = _observe_launch_handle(
            kernel32, process_handle, request_sha256=request_sha256,
        )
        if not launch_box.get("ok"):
            stage = "SHELLEXECUTE"
            raise OSError("SHELLEXECUTE_FAILED")
        kernel32.GetProcessId.argtypes = [wintypes.HANDLE]
        kernel32.GetProcessId.restype = wintypes.DWORD
        launched_pid = int(kernel32.GetProcessId(process_handle))
        if launched_pid <= 0:
            raise OSError("LAUNCHED_PID_MISMATCH")

        stage = "PIPE_CONNECT"
        _connect_named_pipe_until_connected(kernel32, pipe, process_handle, deadline)
        client_pid = wintypes.ULONG()
        if not kernel32.GetNamedPipeClientProcessId(pipe, ctypes.byref(client_pid)) or client_pid.value != launched_pid:
            raise OSError("PIPE_CLIENT_PID_MISMATCH")
        process_token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(process_handle, 0x0008, ctypes.byref(process_token)):
            raise OSError("HELPER_TOKEN_QUERY_FAILED")
        elevation = wintypes.DWORD()
        returned = wintypes.DWORD()
        if not advapi32.GetTokenInformation(process_token, 20, ctypes.byref(elevation), ctypes.sizeof(elevation), ctypes.byref(returned)) or elevation.value != 1:
            kernel32.CloseHandle(process_token)
            raise OSError("HELPER_NOT_ELEVATED")
        helper_sid = _token_user_sid(advapi32, kernel32, process_token)
        if helper_sid != caller_sid or helper_sid != request.get("caller_sid"):
            kernel32.CloseHandle(process_token)
            raise OSError("HELPER_USER_SID_MISMATCH")
        kernel32.CloseHandle(process_token)
        image = ctypes.create_unicode_buffer(32768)
        image_size = wintypes.DWORD(len(image))
        if not kernel32.QueryFullProcessImageNameW(process_handle, 0, image, ctypes.byref(image_size)):
            raise OSError("HELPER_IMAGE_QUERY_FAILED")
        if os.path.normcase(os.path.normpath(image.value)) != os.path.normcase(os.path.normpath(str(PYTHON_EXE))):
            raise OSError("HELPER_IMAGE_MISMATCH")
        creation = _process_creation_id(kernel32, process_handle)
        creation_id = creation
        image_matches = True
        peer_authenticated = creation > 0
        _log_native_observation(
            "HELPER_IDENTITY", "HELPER_IDENTITY_CAPTURED", launch_state,
            launched_pid=launched_pid, creation_id=creation,
            image_matches=image_matches, peer_authenticated=peer_authenticated,
            request_sha256=request_sha256,
        )

        stage = "PIPE_READ_HEADER"
        header = _read_pipe_exact(kernel32, pipe, 4, deadline)
        frame_size = struct.unpack("<I", header)[0]
        if frame_size <= 0 or frame_size > MAX_FRAME_BYTES:
            raise OSError("PIPE_FRAME_SIZE_INVALID")
        stage = "PIPE_READ_BODY"
        raw = _read_pipe_exact(kernel32, pipe, frame_size, deadline)
        expected = {
            "schema": "dataset-csv-elevated-result-v1", "nonce": request["nonce"],
            "request_sha256": request_sha256, "project_id": request["project_id"],
            "profile_id": request["profile_id"], "artifact_id": request["artifact_id"],
            "attempt_id": request["attempt_id"], "input_sha256": request["input_sha256"],
            "size_bytes": request["size_bytes"], "identity": request["identity"],
        }
        result = _decode_helper_result_body(raw, expected)
        stage = "HELPER_EXIT"
        wait_result = kernel32.WaitForSingleObject(process_handle, remaining_ms())
        if wait_result != 0:
            raise TimeoutError("HELPER_EXIT_UNKNOWN")
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(process_handle, ctypes.byref(exit_code)) or exit_code.value != 0:
            raise OSError("HELPER_EXIT_NONZERO")
        stage = "TRUSTED_RESULT"
        _log_native_observation(
            stage, "DEFENDER_TIMEOUT" if result.timed_out else "TRUSTED_RESULT", launch_state,
            launched_pid=launched_pid, creation_id=creation,
            image_matches=image_matches, peer_authenticated=peer_authenticated,
            request_sha256=request_sha256,
        )
        return ElevatedProcessResult(result, launched_pid, int(client_pid.value), True, image.value, creation, helper_sid, request_sha256, str(request["nonce"]))
    except TimeoutError as exc:
        known = exc.args[0] if exc.args and isinstance(exc.args[0], str) else ""
        reason = _native_timeout_reason(stage, known)
        _log_native_observation(
            stage, reason, launch_state, launched_pid=launched_pid,
            creation_id=creation_id, image_matches=image_matches,
            peer_authenticated=peer_authenticated, request_sha256=request_sha256,
        )
        raise
    except Exception:
        reason = {
            "SHELLEXECUTE": "SHELLEXECUTE_FAILED",
            "HELPER_IDENTITY": "HELPER_IDENTITY_QUERY_FAILED",
            "PIPE_CONNECT": "PIPE_CONNECT_FAILED",
            "PIPE_READ_HEADER": "PIPE_READ_FAILED",
            "PIPE_READ_BODY": "PIPE_READ_FAILED",
            "HELPER_EXIT": "HELPER_EXIT_FAILED",
        }.get(stage, "NATIVE_TRANSPORT_FAILED")
        _log_native_observation(
            stage, reason, launch_state, launched_pid=launched_pid,
            creation_id=creation_id, image_matches=image_matches,
            peer_authenticated=peer_authenticated, request_sha256=request_sha256,
        )
        raise
    finally:
        if not launch_done.is_set():
            launch_abandoned.set()
        kernel32.CloseHandle(pipe)
        if process_handle:
            kernel32.CloseHandle(process_handle)


def _connect_named_pipe_until_connected(
    kernel32: Any,
    pipe: Any,
    process_handle: Any,
    deadline: float,
    *,
    get_error: Callable[[], int] = ctypes.get_last_error,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    while True:
        if kernel32.ConnectNamedPipe(pipe, None):
            return
        error = get_error()
        if error == 535:  # ERROR_PIPE_CONNECTED: client won the create/connect race.
            return
        if error != 536:  # ERROR_PIPE_LISTENING: no client yet on PIPE_NOWAIT.
            raise OSError(f"PIPE_CONNECT_FAILED_{error}")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("PIPE_CONNECT_TIMEOUT")
        process_state = kernel32.WaitForSingleObject(process_handle, 0)
        if process_state == 0:
            raise OSError("HELPER_EXITED_BEFORE_PIPE")
        if process_state != 0x102:
            raise OSError("HELPER_PROCESS_STATE_UNKNOWN")
        sleep(min(0.01, remaining))


def _read_pipe_exact(
    kernel32: Any,
    pipe: Any,
    size: int,
    deadline: float,
    *,
    get_error: Callable[[], int] = ctypes.get_last_error,
    sleep: Callable[[float], None] = time.sleep,
) -> bytes:
    result = bytearray()
    kernel32.PeekNamedPipe.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD, wintypes.LPVOID, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
    kernel32.PeekNamedPipe.restype = wintypes.BOOL
    kernel32.ReadFile.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
    kernel32.ReadFile.restype = wintypes.BOOL
    while len(result) < size:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("PIPE_READ_TIMEOUT")
        available = wintypes.DWORD()
        if not kernel32.PeekNamedPipe(pipe, None, 0, None, ctypes.byref(available), None):
            raise OSError("PIPE_READ_FAILED")
        if not available.value:
            sleep(min(0.01, remaining))
            continue
        count = min(size - len(result), available.value)
        buffer = ctypes.create_string_buffer(count)
        read = wintypes.DWORD()
        if not kernel32.ReadFile(pipe, buffer, count, ctypes.byref(read), None):
            error = get_error()
            if error == 232:  # ERROR_NO_DATA: nonblocking pipe has no bytes currently.
                sleep(min(0.01, max(0, deadline - time.monotonic())))
                continue
            raise OSError(f"PIPE_READ_FAILED_{error}")
        if read.value <= 0:
            raise OSError("PIPE_READ_FAILED")
        result.extend(buffer.raw[:read.value])
    return bytes(result)


def _shell_execute_sta(ole32: Any, shell32: Any, info: Any, info_type: type) -> tuple[bool, int]:
    """Run only the fixed ShellExecuteEx call on an initialized STA thread."""
    ole32.CoInitializeEx.argtypes = [wintypes.LPVOID, wintypes.DWORD]
    ole32.CoInitializeEx.restype = ctypes.c_long
    ole32.CoUninitialize.argtypes = []
    ole32.CoUninitialize.restype = None
    hresult = int(ole32.CoInitializeEx(None, 0x2))  # COINIT_APARTMENTTHREADED
    if hresult < 0:
        raise OSError(f"UAC_STA_INITIALIZATION_FAILED_{hresult:#x}")
    try:
        shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(info_type)]
        shell32.ShellExecuteExW.restype = wintypes.BOOL
        ok = bool(shell32.ShellExecuteExW(ctypes.byref(info)))
        return ok, ctypes.get_last_error()
    finally:
        ole32.CoUninitialize()


def _decode_helper_result_body(raw: bytes, expected: Mapping[str, Any]) -> ProcessResult:
    body = json.loads(raw)
    if not isinstance(body, dict):
        raise OSError("PIPE_FRAME_INVALID")
    wire_fields = {
        "returncode", "output_b64", "timed_out", "output_truncated",
        "reader_incomplete", "error_code",
    }
    binding_fields = {
        "schema", "nonce", "request_sha256", "project_id", "profile_id",
        "artifact_id", "attempt_id", "input_sha256", "size_bytes", "identity",
    }
    if set(body) != binding_fields | wire_fields:
        raise OSError("PIPE_FRAME_FIELDS_INVALID")
    if any(body.get(key) != value for key, value in expected.items()):
        raise OSError("PIPE_BINDING_MISMATCH")
    if (not isinstance(body.get("identity"), list) or len(body["identity"]) != 2
            or any(type(part) is not int for part in body["identity"])
            or any(type(body.get(flag)) is not bool for flag in ("timed_out", "output_truncated", "reader_incomplete"))
            or (body.get("returncode") is not None and type(body.get("returncode")) is not int)
            or (body.get("error_code") is not None and not isinstance(body.get("error_code"), str))
            or (isinstance(body.get("error_code"), str) and len(body["error_code"]) > 256)
            or not isinstance(body.get("output_b64"), str)):
        raise OSError("PIPE_FRAME_TYPES_INVALID")
    output = base64.b64decode(body["output_b64"], validate=True)
    if len(output) > MAX_OUTPUT_BYTES:
        raise OSError("PIPE_OUTPUT_OVERSIZE")
    return ProcessResult(
        body["returncode"], output,
        timed_out=body["timed_out"],
        output_truncated=body["output_truncated"],
        reader_incomplete=body["reader_incomplete"],
        error_code=body["error_code"],
    )


def _process_creation_id(kernel32: Any, handle: Any) -> int:
    class FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]
    created, exited, kernel, user = FILETIME(), FILETIME(), FILETIME(), FILETIME()
    kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE, ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME)]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    if not kernel32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)):
        raise OSError("HELPER_CREATION_QUERY_FAILED")
    return (int(created.dwHighDateTime) << 32) | int(created.dwLowDateTime)


def _fixed_elevation_arguments() -> list[str]:
    bootstrap = (
        "import runpy,sys;"
        f"sys.path.insert(0,{str(PACKAGE_ROOT)!r});"
        f"runpy.run_module({HELPER_MODULE!r},run_name='__main__')"
    )
    return ["-B", "-I", "-c", bootstrap]


def _token_user_sid(advapi32: Any, kernel32: Any, token: Any) -> str:
    class SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Sid", wintypes.LPVOID), ("Attributes", wintypes.DWORD)]
    class TOKEN_USER(ctypes.Structure):
        _fields_ = [("User", SID_AND_ATTRIBUTES)]
    advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.LPWSTR)]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    required = wintypes.DWORD()
    queried = advapi32.GetTokenInformation(token, 1, None, 0, ctypes.byref(required))
    if (queried or ctypes.get_last_error() != 122 or not required.value or required.value > 65536):
        raise OSError("TOKEN_USER_QUERY_FAILED")
    buffer = ctypes.create_string_buffer(required.value)
    if not advapi32.GetTokenInformation(token, 1, buffer, required.value, ctypes.byref(required)):
        raise OSError("TOKEN_USER_QUERY_FAILED")
    user_sid = ctypes.cast(buffer, ctypes.POINTER(TOKEN_USER)).contents.User.Sid
    sid_text = wintypes.LPWSTR()
    if not advapi32.ConvertSidToStringSidW(user_sid, ctypes.byref(sid_text)):
        raise OSError("TOKEN_SID_QUERY_FAILED")
    try:
        return str(sid_text.value)
    finally:
        kernel32.LocalFree(sid_text)


def _current_user_sid() -> str:
    if os.name != "nt":
        raise OSError("WINDOWS_TOKEN_REQUIRED")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.LPWSTR)]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise OSError("TOKEN_QUERY_FAILED")
    try:
        return _token_user_sid(advapi32, kernel32, token)
    finally:
        kernel32.CloseHandle(token)


def _current_token_elevated() -> bool:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise OSError("TOKEN_QUERY_FAILED")
    try:
        elevation, returned = wintypes.DWORD(), wintypes.DWORD()
        if not advapi32.GetTokenInformation(token, 20, ctypes.byref(elevation), ctypes.sizeof(elevation), ctypes.byref(returned)):
            raise OSError("TOKEN_ELEVATION_QUERY_FAILED")
        return elevation.value == 1
    finally:
        kernel32.CloseHandle(token)


def _lock_target_windows(path: Path, expected_identity: tuple[int, int]) -> int:
    if os.name != "nt":
        raise OSError("WINDOWS_FILE_LOCK_REQUIRED")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.CreateFileW(str(path), 0x80000000, 0x1, None, 3, 0x00200000, None)
    if handle in (0, wintypes.HANDLE(-1).value):
        raise OSError("TARGET_LOCK_FAILED")
    class FILE_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD), ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME), ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD), ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD), ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD), ("nFileIndexLow", wintypes.DWORD),
        ]
    info = FILE_INFORMATION()
    kernel32.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(FILE_INFORMATION)]
    kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    if not kernel32.GetFileInformationByHandle(handle, ctypes.byref(info)) or info.dwFileAttributes & 0x400:
        kernel32.CloseHandle(handle)
        raise OSError("TARGET_HANDLE_REPARSE_REJECTED")
    size = (int(info.nFileSizeHigh) << 32) | int(info.nFileSizeLow)
    final_path = ctypes.create_unicode_buffer(32768)
    kernel32.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    length = kernel32.GetFinalPathNameByHandleW(handle, final_path, len(final_path), 0)
    canonical_final = final_path.value[4:] if final_path.value.startswith("\\\\?\\") else final_path.value
    if not length or os.path.normcase(os.path.normpath(canonical_final)) != os.path.normcase(os.path.normpath(str(path.resolve()))):
        kernel32.CloseHandle(handle)
        raise OSError("TARGET_HANDLE_PATH_MISMATCH")
    if size != INPUT_SIZE_BYTES:
        kernel32.CloseHandle(handle)
        raise OSError("TARGET_HANDLE_SIZE_MISMATCH")
    import msvcrt
    try:
        fd = msvcrt.open_osfhandle(int(handle), os.O_RDONLY | getattr(os, "O_BINARY", 0))
    except Exception:
        kernel32.CloseHandle(handle)
        raise
    try:
        info = os.fstat(fd)
        if (int(info.st_dev), int(info.st_ino)) != expected_identity or info.st_size != INPUT_SIZE_BYTES:
            raise OSError("TARGET_HANDLE_IDENTITY_MISMATCH")
    except Exception:
        os.close(fd)
        raise
    return fd


if __name__ == "__main__":  # pragma: no cover - only the elevated helper process enters here
    raise SystemExit(_helper_main())


__all__ = [
    "DatasetCsvElevatedRunner", "DatasetScanBinding", "ElevatedProcessResult",
    "ledger_processes_idle",
]
