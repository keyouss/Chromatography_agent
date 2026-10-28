"""Offline-only diagnostics for the audit03 pin binding failure.

This file deliberately has no preview/apply path. Runtime paths and gates are
retained as evidence, but the command line cannot read or mutate them.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import runpy
from types import FunctionType
from pathlib import Path

ROOT = Path(r"D:\Jupyter\Chromatography_agent")
RUN = ROOT / "testing/runs/2026-10-28-data-import-audit03"
RUNTIME = Path(r"C:\Users\13459\AppData\Local\ChromatographyPlatform\data-accept-20261026")
CONFIG = RUNTIME / "runtime.json"
PENDING = RUNTIME / "runtime.json.source-pin-audit03-be10.pending"
BACKUP = RUNTIME / "runtime.json.audit03-be10-before.json"
_PROTECTED_RUNTIME_ROOT = Path(r"C:\Users\13459\AppData\Local\ChromatographyPlatform\data-accept-20261026")
_PROTECTED_RUNTIME_FILES = (
    _PROTECTED_RUNTIME_ROOT / "runtime.json",
    _PROTECTED_RUNTIME_ROOT / "runtime.json.source-pin-audit03-be10.pending",
    _PROTECTED_RUNTIME_ROOT / "runtime.json.audit03-be10-before.json",
)
OLD_CORE = ROOT / "testing/runs/2026-10-26-data-center-planning/complete-runtime04-pin-03.py"
OLD_CORE_SHA = "0dff88173bf7856f7b3417c4af1bcd8e1b9ac2ba557be3e85a8968c78827a525"
START_PLATFORM = ROOT / "deploy/v1/start-platform.ps1"
PWSH = Path(r"C:\Users\13459\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\powershell\pwsh.exe")
KEY = "CHROM_BACKEND_SOURCE_MANIFEST_SHA256"
OLD_PIN = "31675275a79398770cbcb8238b3ee09d4580cb953dc1f6c2e0ebf82edddf7886"
NEW_PIN = "aa89cfca8cc212dcc762211d28147f51bbe89f131b9332c46f20dcb115fd8a89"
BASELINE_CONFIG_SHA = "e475ad2f9299bc659f295271ac7607f9bcc7295520edf004a67354cb2bac4c81"
NEW_CONFIG_SHA = "001cd2e7871b97be2c6fe263b0347995f50de555c3c73c1b26e64d0ce817f4f5"

_REASON_CODES = {
    "DIAGNOSTIC_ONLY_EXECUTION_DISABLED",
    "PROTECTED_RUNTIME_PATH_REJECTED",
    "MANAGED_STATUS_TOOL_MISSING",
    "MANAGED_STATUS_FAILED",
    "MANAGED_STATUS_INVALID",
    "PROFILE_NOT_STOPPED",
    "SHARED_PORTS_NOT_FREE",
    "PROFILE_STATUS_INCOMPLETE",
    "ACL_TARGET_OUTSIDE_ALLOWLIST",
    "ACL_COPY_TEMPLATE_CHANGED",
    "PIN_ACL_COPY_OR_VERIFY_FAILED",
    "PIN_ACL_METADATA_INVALID",
    "PIN_BYTES_CHANGED_DURING_ACL_COPY",
    "PIN_BYTES_CHANGED_BEFORE_REPLACE",
    "ATOMIC_PIN_REPLACE_MISMATCH",
    "PIN_INPUT_BYTES_CHANGED",
    "PIN_VALUE_MISMATCH",
    "NON_PIN_CONFIG_CHANGED",
    "DUPLICATE_JSON_KEY",
    "CONFIG_NOT_OBJECT",
    "FRESH_IDLE_GATE_FAILED",
    "UNCLASSIFIED_EXCEPTION",
    "FINAL_CONFIG_SHA_MISMATCH",
}
_STAGES = {
    "DIAGNOSTIC_FAILURE",
    "MANAGED_STATUS_PREFLIGHT",
    "MANAGED_STATUS_JSON",
    "MANAGED_STATUS_COMMAND",
    "MANAGED_STATUS_ROWS",
    "SHARED_PORT_CHECK",
    "IDLE_GATE_SUCCEEDED",
    "PIN_VALUE_CHECK",
    "SECOND_ACL_COPY",
    "INITIAL_PIN_CAS",
    "BEFORE_REPLACE_CALLBACK",
    "FRESH_IDLE_CHECK",
    "PIN_WRITE_ATTEMPTED",
    "CORE_RETURNED",
    "FINAL_OBSERVATION",
}
_CORE_REASON_CODES = {
    "PIN_BYTES_CHANGED_DURING_ACL_COPY",
    "PIN_BYTES_CHANGED_BEFORE_REPLACE",
    "ATOMIC_PIN_REPLACE_MISMATCH",
    "PIN_INPUT_BYTES_CHANGED",
    "PIN_VALUE_MISMATCH",
    "NON_PIN_CONFIG_CHANGED",
    "DUPLICATE_JSON_KEY",
    "CONFIG_NOT_OBJECT",
}
_DIAGNOSTIC_TOKEN = object()
_EXCEPTION_TYPES = {
    "RuntimeError", "ValueError", "TypeError", "OSError", "PermissionError",
    "TimeoutError", "JSONDecodeError", "UnicodeDecodeError", "KeyError",
}


def _core() -> dict:
    raw = OLD_CORE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != OLD_CORE_SHA:
        raise RuntimeError("PIN_CORE_CHANGED")
    return runpy.run_path(str(OLD_CORE), run_name="audit03_pin_diagnostic_pinned_core")


def _exception_chain_types(exc: BaseException) -> list[str]:
    result: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen and len(result) < 8:
        seen.add(id(current))
        name = type(current).__name__
        result.append(name if name in _EXCEPTION_TYPES else "OTHER")
        current = current.__cause__ if current.__cause__ is not None else current.__context__
    return result


def _raise_diagnostic(stage: str, reason: str, cause: BaseException | None = None) -> None:
    if stage not in _STAGES:
        stage = "DIAGNOSTIC_FAILURE"
    if reason not in _REASON_CODES:
        reason = "UNCLASSIFIED_EXCEPTION"
    error = RuntimeError(reason)
    error._diagnostic_token = _DIAGNOSTIC_TOKEN  # type: ignore[attr-defined]
    error.diagnostic_stage = stage  # type: ignore[attr-defined]
    error.reason_code = reason  # type: ignore[attr-defined]
    if cause is None:
        raise error
    raise error from cause


def _strict_json_object(raw: bytes) -> dict:
    return _core()["_strict_json_object"](raw)


def _candidate_config(raw: bytes) -> bytes:
    before = _strict_json_object(raw)
    if before.get(KEY) != OLD_PIN:
        _raise_diagnostic("PIN_VALUE_CHECK", "PIN_VALUE_MISMATCH")
    pattern = re.compile(rb'("CHROM_BACKEND_SOURCE_MANIFEST_SHA256"\s*:\s*")' + OLD_PIN.encode("ascii") + rb'(")')
    updated, count = pattern.subn(lambda match: match.group(1) + NEW_PIN.encode("ascii") + match.group(2), raw)
    if count != 1:
        _raise_diagnostic("PIN_VALUE_CHECK", "PIN_VALUE_MISMATCH")
    after = _strict_json_object(updated)
    if before.keys() != after.keys() or any(before[key] != after[key] for key in before if key != KEY):
        _raise_diagnostic("PIN_VALUE_CHECK", "NON_PIN_CONFIG_CHANGED")
    # This helper is exercised with synthetic input bytes; deployment hash checks
    # belong to the disabled operator path and are never weakened here.
    if after.get(KEY) != NEW_PIN:
        _raise_diagnostic("PIN_VALUE_CHECK", "PIN_VALUE_MISMATCH")
    return updated


def _require_stopped(*, run, trace: dict | None = None) -> None:
    if not START_PLATFORM.is_file() or not PWSH.is_file():
        _raise_diagnostic("MANAGED_STATUS_PREFLIGHT", "MANAGED_STATUS_TOOL_MISSING")
    seen = set()
    for profile in ("data-20261026", "assistant-20261006"):
        stage = "MANAGED_STATUS_JSON"
        if trace is not None:
            trace["stage"] = stage
        try:
            result = run(
                [str(PWSH), "-NoProfile", "-File", str(START_PLATFORM), "-Profile", profile,
                 "-Action", "status", "-NoBrowser"],
                capture_output=True, text=True, timeout=45,
            )
        except Exception as exc:
            _raise_diagnostic("MANAGED_STATUS_COMMAND", "MANAGED_STATUS_FAILED", exc)
        if result.returncode:
            _raise_diagnostic("MANAGED_STATUS_COMMAND", "MANAGED_STATUS_FAILED")
        try:
            status = json.loads(result.stdout)
        except Exception as exc:
            _raise_diagnostic(stage, "MANAGED_STATUS_INVALID", exc)
        if not isinstance(status, dict):
            _raise_diagnostic(stage, "MANAGED_STATUS_INVALID")
        selected, other = status.get("selected", []), status.get("other", [])
        if (not isinstance(selected, list) or not isinstance(other, list) or
                any(not isinstance(row, dict) for row in selected + other)):
            _raise_diagnostic(stage, "MANAGED_STATUS_INVALID")
        rows = selected + other
        services = {row.get("service") for row in rows}
        if services != {"api", "worker", "frontend"} or any(row.get("status") != "STOPPED" for row in rows):
            _raise_diagnostic("MANAGED_STATUS_ROWS", "PROFILE_NOT_STOPPED")
        seen.update(services)
    port_script = "$ErrorActionPreference='Stop'; $p=@(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort -in @(9012,5174) }); [Console]::WriteLine($p.Count)"
    try:
        result = run([str(PWSH), "-NoProfile", "-NonInteractive", "-Command", port_script],
                     capture_output=True, text=True, timeout=45)
    except Exception as exc:
        _raise_diagnostic("SHARED_PORT_CHECK", "SHARED_PORTS_NOT_FREE", exc)
    if result.returncode or result.stdout.strip() != "0":
        _raise_diagnostic("SHARED_PORT_CHECK", "SHARED_PORTS_NOT_FREE")
    if seen != {"api", "worker", "frontend"}:
        _raise_diagnostic("MANAGED_STATUS_ROWS", "PROFILE_STATUS_INCOMPLETE")
    if trace is not None:
        trace["stage"] = "IDLE_GATE_SUCCEEDED"


def _copy_acl(source_path: Path, target: Path, core: dict, *, run, stage: str = "SECOND_ACL_COPY") -> dict:
    source_path, target = Path(source_path), Path(target)
    if source_path != CONFIG or target not in (PENDING, BACKUP) or target.parent != CONFIG.parent:
        _raise_diagnostic(stage, "ACL_TARGET_OUTSIDE_ALLOWLIST")
    if target.name not in ("runtime.json.source-pin-audit03-be10.pending", "runtime.json.audit03-be10-before.json"):
        _raise_diagnostic(stage, "ACL_TARGET_OUTSIDE_ALLOWLIST")
    script_source = core["ACL_COPY_SCRIPT"]
    if script_source.count(".source-pin-04.pending") != 1:
        _raise_diagnostic(stage, "ACL_COPY_TEMPLATE_CHANGED")
    suffix = ".source-pin-audit03-be10.pending" if target == PENDING else ".audit03-be10-before.json"
    script = script_source.replace(".source-pin-04.pending", suffix, 1)
    env = os.environ.copy()
    env.update({"CHROM_PIN_FORMAL": str(CONFIG), "CHROM_PIN_PENDING": str(target)})
    try:
        result = run([str(PWSH), "-NoProfile", "-NonInteractive", "-Command", script],
                     capture_output=True, text=True, timeout=45, env=env)
    except Exception as exc:
        _raise_diagnostic(stage, "PIN_ACL_COPY_OR_VERIFY_FAILED", exc)
    if result.returncode:
        _raise_diagnostic(stage, "PIN_ACL_COPY_OR_VERIFY_FAILED")
    try:
        metadata = json.loads(result.stdout)
    except Exception as exc:
        _raise_diagnostic(stage, "PIN_ACL_METADATA_INVALID", exc)
    if not isinstance(metadata, dict):
        _raise_diagnostic(stage, "PIN_ACL_METADATA_INVALID")
    sddl = metadata.get("source_sddl")
    if (not isinstance(sddl, str) or sddl != metadata.get("target_sddl") or
            metadata.get("source_protected") is not True or
            metadata.get("target_protected") is not True or
            metadata.get("owner_sid") != metadata.get("source_owner_sid", metadata.get("owner_sid"))):
        _raise_diagnostic(stage, "PIN_ACL_METADATA_INVALID")
    return {"owner_match": True, "protected": True,
            "sddl_sha256": hashlib.sha256(sddl.encode("utf-8")).hexdigest()}


def _reason_from_exception(exc: BaseException) -> str:
    attached = getattr(exc, "reason_code", None)
    if (getattr(exc, "_diagnostic_token", None) is _DIAGNOSTIC_TOKEN and
            attached in _REASON_CODES):
        return attached
    # Only exact tokens from the pinned core/operator are considered; never emit text.
    try:
        marker = str(exc)
    except Exception:
        marker = ""
    if marker in _CORE_REASON_CODES:
        return marker
    return "UNCLASSIFIED_EXCEPTION"


def _assert_synthetic_paths(config: Path, pending: Path) -> None:
    runtime_roots = (
        _PROTECTED_RUNTIME_ROOT.parent,
        Path(r"C:\Users\13459\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0\LocalCache\Local\ChromatographyPlatform"),
    )
    roots = [os.path.normcase(os.path.abspath(os.fspath(root))) for root in runtime_roots]
    protected = {
        os.path.normcase(os.path.abspath(os.fspath(path)))
        for path in _PROTECTED_RUNTIME_FILES
    }
    for path in (Path(config), Path(pending)):
        lexical = os.path.normcase(os.path.abspath(os.fspath(path)))
        if lexical in protected or any(lexical == root or lexical.startswith(root.rstrip("\\/") + os.sep) for root in roots):
            _raise_diagnostic("INITIAL_PIN_CAS", "PROTECTED_RUNTIME_PATH_REJECTED")
        resolved = os.path.normcase(os.path.abspath(os.fspath(path.resolve(strict=False))))
        if resolved in protected or any(resolved == root or resolved.startswith(root.rstrip("\\/") + os.sep) for root in roots):
            _raise_diagnostic("INITIAL_PIN_CAS", "PROTECTED_RUNTIME_PATH_REJECTED")


class _CoreOsProxy:
    def __init__(self, replace_file):
        self.replace = replace_file


def _diagnostic_core_complete(core: dict, replace_file):
    original = core["complete_pin_files"]
    isolated_globals = dict(original.__globals__)
    isolated_globals["os"] = _CoreOsProxy(replace_file)
    return FunctionType(original.__code__, isolated_globals, original.__name__, original.__defaults__, original.__closure__)


def diagnose_synthetic_pin_files(
    config: Path,
    pending: Path,
    *,
    formal_sha256: str,
    pending_sha256: str,
    old_pin: str = OLD_PIN,
    new_pin: str = NEW_PIN,
    copy_acl,
    idle_check,
    replace_file,
    observe_replacement=None,
) -> dict:
    """Exercise the pinned core against caller-owned synthetic files only."""
    trace = {
        "stage": "INITIAL_PIN_CAS",
        "reason_code": "UNCLASSIFIED_EXCEPTION",
        "second_acl_copy_attempted": False,
        "second_acl_copy_succeeded": False,
        "before_replace_callback_entered": False,
        "fresh_idle_check_attempted": False,
        "fresh_idle_check_succeeded": False,
        "pin_write_attempted": False,
        "configuration_write_observed": False,
        "exception_types": [],
    }
    outcome = "FAILED"
    synthetic_paths_accepted = False

    def second_acl(source, target):
        trace["stage"] = "SECOND_ACL_COPY"
        trace["second_acl_copy_attempted"] = True
        result = copy_acl(source, target)
        trace["second_acl_copy_succeeded"] = True
        return result

    def immediately_before_replace(_metadata):
        trace["stage"] = "BEFORE_REPLACE_CALLBACK"
        trace["before_replace_callback_entered"] = True
        trace["fresh_idle_check_attempted"] = True
        trace["stage"] = "FRESH_IDLE_CHECK"
        try:
            idle_check()
        except Exception as exc:
            inner_reason = _reason_from_exception(exc)
            if inner_reason == "UNCLASSIFIED_EXCEPTION":
                inner_reason = "FRESH_IDLE_GATE_FAILED"
            _raise_diagnostic("FRESH_IDLE_CHECK", inner_reason, exc)
        trace["fresh_idle_check_succeeded"] = True
        trace["pin_write_attempted"] = True
        trace["stage"] = "PIN_WRITE_ATTEMPTED"

    try:
        _assert_synthetic_paths(config, pending)
        synthetic_paths_accepted = True
        core = _core()
        _diagnostic_core_complete(core, replace_file)(
            config, pending,
            formal_sha256=formal_sha256,
            pending_sha256=pending_sha256,
            old_pin=old_pin,
            new_pin=new_pin,
            copy_acl=second_acl,
            before_replace=immediately_before_replace,
        )
        outcome = "SUCCEEDED"
        trace["reason_code"] = "NONE"
        trace["stage"] = "CORE_RETURNED"
    except Exception as exc:
        trace["reason_code"] = _reason_from_exception(exc)
        attached_stage = getattr(exc, "diagnostic_stage", None)
        if (getattr(exc, "_diagnostic_token", None) is _DIAGNOSTIC_TOKEN and
                attached_stage in _STAGES):
            trace["stage"] = attached_stage
        trace["exception_types"] = _exception_chain_types(exc)
    finally:
        if synthetic_paths_accepted and observe_replacement is not None:
            try:
                observed = bool(observe_replacement(config, pending))
                trace["configuration_write_observed"] = observed
                if outcome == "SUCCEEDED" and not observed:
                    outcome = "FAILED"
                    trace["stage"] = "FINAL_OBSERVATION"
                    trace["reason_code"] = "FINAL_CONFIG_SHA_MISMATCH"
            except Exception as exc:
                # A failed observation never turns a failure into success or overwrites its cause.
                if outcome == "SUCCEEDED":
                    outcome = "FAILED"
                    trace["stage"] = "FINAL_OBSERVATION"
                    trace["reason_code"] = "UNCLASSIFIED_EXCEPTION"
                    trace["exception_types"] = _exception_chain_types(exc)
    trace["outcome"] = outcome
    return trace


def preview() -> None:
    raise RuntimeError("DIAGNOSTIC_ONLY_EXECUTION_DISABLED")


def apply(_plan_sha256: str) -> None:
    raise RuntimeError("DIAGNOSTIC_ONLY_EXECUTION_DISABLED")


def main(_argv=None) -> int:
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
