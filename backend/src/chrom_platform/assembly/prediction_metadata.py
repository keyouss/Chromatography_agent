"""Protected, model-light Worker metadata shared with the API process."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import ctypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

FORMAT_VERSION = 1
_MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024
_CONFIG_KEYS = frozenset({
    "CHROM_BUILD_MANIFEST", "BUILD_PROFILE", "WORKER_READY_FILE", "WORKER_QUEUE", "CHROM_ASSISTANT_RUNTIME_ROOT",
    "CHROM_TLC_MODEL_ROOT", "CHROM_TLC_REGISTRY_PATH", "CHROM_TLC_ARTIFACT_MANIFEST_SHA256",
    "CHROM_TLC_MODEL_ID", "CHROM_TLC_MODEL_VERSION", "CHROM_TLC_BUSINESS_SCOPE_ID",
    "CHROM_CC_MODEL_ROOT", "CHROM_CC_REGISTRY_PATH", "CHROM_CC_REGISTRY_SHA256",
    "CHROM_CC_MODEL_ID", "CHROM_CC_MODEL_VERSION", "CHROM_CC_BUSINESS_SCOPE_ID",
    "CHROM_HPLC_MODEL_ROOT", "CHROM_HPLC_REGISTRY_PATH", "CHROM_HPLC_REGISTRY_SHA256",
    "CHROM_HPLC_MODEL_ID", "CHROM_HPLC_MODEL_VERSION", "CHROM_HPLC_BUSINESS_SCOPE_ID",
    "CHROM_GC_MODEL_ROOT", "CHROM_GC_REGISTRY_PATH", "CHROM_GC_REGISTRY_SHA256",
    "CHROM_GC_MODEL_ID", "CHROM_GC_MODEL_VERSION", "CHROM_GC_BUSINESS_SCOPE_ID",
    "CHROM_SFC_MODEL_ROOT", "CHROM_SFC_REGISTRY_PATH", "CHROM_SFC_REGISTRY_SHA256",
    "CHROM_SFC_CANDIDATE_DB_PATH", "CHROM_SFC_MODEL_ID", "CHROM_SFC_MODEL_VERSION",
    "CHROM_SFC_ARTIFACT_SHA256", "CHROM_SFC_BUSINESS_SCOPE_ID", "CHROM_SFC_SCOPE_STORAGE_PATH",
    "CHROM_SFC_INPUT_SCHEMA_VERSION", "CHROM_SFC_OUTPUT_SCHEMA_VERSION",
    "CHROM_SFC_FULL_MODEL_ID", "CHROM_SFC_FULL_MODEL_VERSION", "CHROM_SFC_FULL_ARTIFACT_SHA256",
    "CHROM_SFC_FULL_REFERENCE_PATH", "CHROM_SFC_FULL_REFERENCE_SHA256",
    "CHROM_TLC_GOLDEN_FIXTURE", "CHROM_SFC_GOLDEN_FIXTURE",
})
_ASSET_KEYS_BY_TYPE = {
    "tlc": ("CHROM_TLC_MODEL_ROOT", "CHROM_TLC_REGISTRY_PATH", "CHROM_TLC_GOLDEN_FIXTURE"),
    "cc": ("CHROM_CC_MODEL_ROOT",),
    "hplc": ("CHROM_HPLC_MODEL_ROOT",),
    "gc": ("CHROM_GC_MODEL_ROOT",),
    "sfc": ("CHROM_SFC_MODEL_ROOT", "CHROM_SFC_REGISTRY_PATH", "CHROM_SFC_CANDIDATE_DB_PATH",
            "CHROM_SFC_FULL_REFERENCE_PATH", "CHROM_SFC_GOLDEN_FIXTURE"),
}
_ASSET_PATH_KEYS = frozenset(key for keys in _ASSET_KEYS_BY_TYPE.values() for key in keys)
_OPTIONAL_FALLBACKS = {
    "CHROM_CC_MODEL_ROOT": "cc_column_chromatography",
    "CHROM_HPLC_MODEL_ROOT": "hplc_enantioseparation",
    "CHROM_GC_MODEL_ROOT": "gc_retention_time",
}
_HASH_CACHE: dict[str, tuple[int, int, int, int, str]] = {}


def _startup_reference_paths() -> dict[str, Path]:
    # Match assembly.prediction's fixed consumer paths in both source and packages.
    fixtures = Path(__file__).resolve().parents[1] / "resources"
    return {
        "gc": fixtures / "gc-reference-preprocessing-v2.json",
        "sfc": fixtures / "sfc-single-reference-2p5-warning-v3.json",
    }


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_change_time(path: Path, info: os.stat_result) -> int:
    if os.name != "nt":
        return info.st_ctime_ns

    class FileBasicInfo(ctypes.Structure):
        _fields_ = [("creation", ctypes.c_longlong), ("access", ctypes.c_longlong),
                    ("write", ctypes.c_longlong), ("change", ctypes.c_longlong),
                    ("attributes", ctypes.c_uint32)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                                  ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.GetFileInformationByHandleEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    kernel.GetFileInformationByHandleEx.restype = ctypes.c_int
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.restype = ctypes.c_int
    handle = kernel.CreateFileW(str(path), 0x80, 7, None, 3, 0, None)
    if handle == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), "METADATA_FILE_IDENTITY_UNAVAILABLE")
    try:
        basic = FileBasicInfo()
        if not kernel.GetFileInformationByHandleEx(handle, 0, ctypes.byref(basic), ctypes.sizeof(basic)):
            raise OSError(ctypes.get_last_error(), "METADATA_FILE_IDENTITY_UNAVAILABLE")
        return basic.change
    finally:
        kernel.CloseHandle(handle)


def _file_signature(path: Path) -> tuple[int, int, int, int]:
    info = path.stat()
    token = _file_change_time(path, info)
    after = path.stat()
    basic = (info.st_size, info.st_mtime_ns, getattr(info, "st_ino", 0))
    if basic != (after.st_size, after.st_mtime_ns, getattr(after, "st_ino", 0)) or token != _file_change_time(path, after):
        raise OSError("METADATA_FILE_CHANGED_DURING_HASH")
    return (*basic, token)


def _sha256_file(path: Path) -> str:
    signature = _file_signature(path)
    cached = _HASH_CACHE.get(str(path))
    if cached and cached[:4] == signature:
        return cached[4]
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    if _file_signature(path) != signature:
        raise OSError("METADATA_FILE_CHANGED_DURING_HASH")
    value = digest.hexdigest()
    _HASH_CACHE[str(path)] = (*signature, value)
    return value


def _reject_reparse_chain(path: Path) -> None:
    current = path.absolute()
    chain: list[Path] = []
    while True:
        chain.append(current)
        parent = current.parent
        if parent == current:
            break
        current = parent
    for item in reversed(chain):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        attributes = getattr(info, "st_file_attributes", 0)
        if stat.S_ISLNK(info.st_mode) or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise ValueError("METADATA_PATH_REPARSE")


def _runtime_root() -> Path:
    configured = os.environ.get("CHROM_ASSISTANT_RUNTIME_ROOT", "").strip()
    if configured:
        root = Path(configured)
    else:
        raise ValueError("METADATA_RUNTIME_ROOT_UNAVAILABLE")
    if not root.is_absolute() or ".." in root.parts:
        raise ValueError("METADATA_RUNTIME_ROOT_INVALID")
    if sys.platform.startswith("win"):
        local = os.environ.get("LOCALAPPDATA", "").strip()
        if not local or root.parent != Path(local) / "ChromatographyPlatform":
            raise ValueError("METADATA_RUNTIME_ROOT_INVALID")
    _reject_reparse_chain(root)
    if not root.is_dir():
        raise ValueError("METADATA_RUNTIME_ROOT_UNAVAILABLE")
    if os.name != "nt" and stat.S_IMODE(root.stat().st_mode) & 0o022:
        raise ValueError("METADATA_RUNTIME_ROOT_UNPROTECTED")
    canonical = root.resolve(strict=True)
    _reject_reparse_chain(canonical)
    return canonical


def prediction_metadata_path(ready_path: str | Path | None = None) -> Path:
    configured = ready_path or os.environ.get("WORKER_READY_FILE", "").strip()
    if not configured:
        raise ValueError("MISSING_WORKER_READY_FILE")
    ready = Path(configured)
    if not ready.is_absolute() or ".." in ready.parts:
        raise ValueError("WORKER_READY_FILE_INVALID")
    root = _runtime_root()
    canonical = ready.resolve(strict=False)
    if not canonical.is_relative_to(root):
        raise ValueError("WORKER_READY_FILE_OUTSIDE_RUNTIME")
    _reject_reparse_chain(ready.parent)
    if ready.exists():
        _reject_reparse_chain(ready)
    _reject_reparse_chain(canonical.parent)
    return canonical.with_name(canonical.name + ".prediction-metadata.json")


def _package_source_fingerprint() -> str:
    package_root = Path(__file__).resolve().parents[1]
    rows: list[tuple[str, int, int, int, str]] = []
    for path in sorted(package_root.rglob("*.py")):
        _reject_reparse_chain(path)
        info = path.stat()
        rows.append((path.relative_to(package_root).as_posix(), info.st_size, info.st_mtime_ns,
                     getattr(info, "st_ino", 0), _sha256_file(path)))
    return _canonical_hash(rows)


def _asset_inventory_fingerprint(manifest: Mapping[str, Any]) -> str:
    rows: list[tuple[str, str, int, int, int, str]] = []
    selected_types = manifest.get("chromatography_types", [])
    if not isinstance(selected_types, list):
        raise ValueError("BUILD_MANIFEST_INVALID")
    keys = sorted({key for chromatography_type in selected_types for key in _ASSET_KEYS_BY_TYPE.get(chromatography_type, ())})
    fixed_references = {f"startup:{kind}": path for kind, path in _startup_reference_paths().items() if kind in selected_types}
    keys.extend(sorted(fixed_references))
    for key in keys:
        raw = os.environ.get(key, "").strip()
        if key in fixed_references:
            path = fixed_references[key]
        elif raw:
            path = Path(raw)
        elif key in _OPTIONAL_FALLBACKS:
            path = Path(__file__).resolve().parents[4] / "deliverables" / "chromatography_tools_windows_cc_hplc_gc_tlc" / _OPTIONAL_FALLBACKS[key]
            if not path.is_dir():
                continue
        else:
            continue
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError("MODEL_ASSET_PATH_INVALID")
        _reject_reparse_chain(path)
        if not path.exists():
            rows.append((key, "missing", 0, 0, 0, ""))
            continue
        if path.is_file():
            candidates = [path]
        elif path.is_dir():
            candidates = sorted(item for item in path.rglob("*") if item.is_file())
            if len(candidates) > 20000:
                raise ValueError("MODEL_ASSET_INVENTORY_TOO_LARGE")
        else:
            raise ValueError("MODEL_ASSET_PATH_INVALID")
        for item in candidates:
            _reject_reparse_chain(item)
            info = item.stat()
            relative = item.name if path.is_file() else item.relative_to(path).as_posix()
            content_sha256 = _sha256_file(item)
            rows.append((key, relative, info.st_size, info.st_mtime_ns,
                         getattr(info, "st_ino", 0), content_sha256))
    return _canonical_hash(rows)


def _config_fingerprint() -> str:
    values = {key: os.environ.get(key, "") for key in sorted(_CONFIG_KEYS)}
    return _canonical_hash(values)


def _manifest_binding() -> tuple[Path, dict[str, Any], str]:
    raw_path = os.environ.get("CHROM_BUILD_MANIFEST", "").strip()
    if raw_path:
        path = Path(raw_path)
    else:
        path = Path(__file__).resolve().parents[2] / "build-manifest.json"
    if not path.is_absolute() or not path.is_file():
        raise ValueError("BUILD_MANIFEST_UNAVAILABLE")
    _reject_reparse_chain(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("build_id"), str) or not isinstance(raw.get("modules"), list):
        raise ValueError("BUILD_MANIFEST_INVALID")
    return path, raw, _sha256_file(path)


def current_prediction_metadata_expectations() -> dict[str, Any]:
    _, manifest, manifest_sha256 = _manifest_binding()
    return {
        "build_id": manifest["build_id"],
        "manifest_sha256": manifest_sha256,
        "modules": manifest["modules"],
        "config_fingerprint": _config_fingerprint(),
        "asset_source_fingerprint": _canonical_hash({
            "source": _package_source_fingerprint(),
            "assets": _asset_inventory_fingerprint(manifest),
        }),
        "worker_ready_file": str(Path(os.environ.get("WORKER_READY_FILE", "")).absolute()),
    }


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform.startswith("win"):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel.OpenProcess.restype = ctypes.c_void_p
        handle = kernel.OpenProcess(0x1000 | 0x00100000, 0, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_uint32()
            kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
            kernel.GetExitCodeProcess.restype = ctypes.c_int
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel.CloseHandle.restype = ctypes.c_int
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value == 259)
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def current_process_starttime(pid: int) -> str | None:
    if sys.platform.startswith("win"):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel.OpenProcess.restype = ctypes.c_void_p
        handle = kernel.OpenProcess(0x1000 | 0x00100000, 0, pid)
        if not handle:
            return None
        try:
            creation = ctypes.c_ulonglong()
            exit_time = ctypes.c_ulonglong()
            kernel_time = ctypes.c_ulonglong()
            user_time = ctypes.c_ulonglong()
            kernel.GetProcessTimes.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulonglong), ctypes.POINTER(ctypes.c_ulonglong), ctypes.POINTER(ctypes.c_ulonglong), ctypes.POINTER(ctypes.c_ulonglong)]
            kernel.GetProcessTimes.restype = ctypes.c_int
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel.CloseHandle.restype = ctypes.c_int
            if not kernel.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_time), ctypes.byref(kernel_time), ctypes.byref(user_time)):
                return None
            return str(creation.value)
        finally:
            kernel.CloseHandle(handle)
    if sys.platform.startswith("linux"):
        try:
            raw = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
            closing = raw.rfind(")")
            value = raw[closing + 2:].split()[19]
        except (OSError, UnicodeError, IndexError):
            return None
        return value if value.isdigit() else None
    return None


def _boot_id() -> str | None:
    if not sys.platform.startswith("linux"):
        return None
    try:
        value = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return None
    return value or None


def _process_cmdline(pid: int) -> str | None:
    if not sys.platform.startswith("linux"):
        return None
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
    except OSError:
        return None


def _read_ready(path: Path) -> dict[str, Any]:
    _reject_reparse_chain(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    expected = {"ready", "pid", "instance_id", "boot_id", "process_starttime"}
    if not isinstance(raw, dict) or set(raw) != expected or raw.get("ready") is not True:
        raise ValueError("READY_MARKER_INVALID")
    pid = raw.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0 or not _process_alive(pid):
        raise ValueError("WORKER_PID_NOT_ALIVE")
    instance_id = raw.get("instance_id")
    if not isinstance(instance_id, str) or re.fullmatch(r"[A-Za-z0-9_-]{8,128}", instance_id) is None:
        raise ValueError("READY_MARKER_INVALID")
    starttime = raw.get("process_starttime")
    current_starttime = current_process_starttime(pid)
    if not isinstance(starttime, str) or not starttime or current_starttime is None or starttime != current_starttime:
        raise ValueError("WORKER_STARTTIME_INVALID")
    if sys.platform.startswith("linux") and raw.get("boot_id") != _boot_id():
        raise ValueError("WORKER_BOOT_ID_INVALID")
    if sys.platform.startswith("linux"):
        command = _process_cmdline(pid)
        if not command or "chrom_platform.worker_cli" not in command:
            raise ValueError("WORKER_CMDLINE_INVALID")
    return raw


def _identity_from_marker(marker: Mapping[str, Any]) -> dict[str, Any]:
    return {key: marker[key] for key in ("pid", "instance_id", "boot_id", "process_starttime")}


def publish_prediction_metadata(
    registry: Mapping[str, Any],
    *,
    ready_path: str | Path | None = None,
    worker_identity: Mapping[str, Any],
) -> dict[str, Any]:
    target = prediction_metadata_path(ready_path)
    if set(worker_identity) != {"pid", "instance_id", "boot_id", "process_starttime"}:
        raise ValueError("WORKER_IDENTITY_INVALID")
    expected = current_prediction_metadata_expectations()
    envelope = {
        "format_version": FORMAT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "worker_identity": dict(worker_identity),
        "build": {key: expected[key] for key in ("build_id", "manifest_sha256", "modules")},
        "config_fingerprint": expected["config_fingerprint"],
        "asset_source_fingerprint": expected["asset_source_fingerprint"],
        "registry": dict(registry),
    }
    data = json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(data) > _MAX_SNAPSHOT_BYTES:
        raise ValueError("PREDICTION_METADATA_TOO_LARGE")
    target.parent.mkdir(parents=True, exist_ok=True)
    _reject_reparse_chain(target.parent)
    if target.exists():
        _reject_reparse_chain(target)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        if os.name != "nt":
            os.chmod(temporary, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        if os.name != "nt":
            os.chmod(target, 0o600)
        return envelope
    finally:
        temporary.unlink(missing_ok=True)


def remove_prediction_metadata(
    *, ready_path: str | Path | None = None,
    worker_identity: Mapping[str, Any],
) -> None:
    target = prediction_metadata_path(ready_path)
    try:
        snapshot = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return
    if isinstance(snapshot, dict) and snapshot.get("worker_identity") == dict(worker_identity):
        target.unlink(missing_ok=True)


def read_prediction_metadata() -> dict[str, Any] | None:
    """Return only a snapshot bound to the current protected runtime and live Worker."""
    try:
        ready_path = Path(os.environ.get("WORKER_READY_FILE", "").strip())
        target = prediction_metadata_path(ready_path)
        before = _read_ready(ready_path)
        info = target.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_SNAPSHOT_BYTES:
            return None
        _reject_reparse_chain(target)
        raw = json.loads(target.read_text(encoding="utf-8"))
        after = _read_ready(ready_path)
        if before != after or not isinstance(raw, dict):
            return None
        expected = current_prediction_metadata_expectations()
        if (
            raw.get("format_version") != FORMAT_VERSION
            or raw.get("worker_identity") != _identity_from_marker(before)
            or raw.get("build") != {key: expected[key] for key in ("build_id", "manifest_sha256", "modules")}
            or raw.get("config_fingerprint") != expected["config_fingerprint"]
            or raw.get("asset_source_fingerprint") != expected["asset_source_fingerprint"]
            or not isinstance(raw.get("registry"), dict)
            or _read_ready(ready_path) != before
        ):
            return None
        return raw
    except (OSError, UnicodeError, TypeError, ValueError, KeyError, json.JSONDecodeError):
        return None
