"""One-shot, fail-closed successor config restore and read-only start gate.

Importing this module performs no I/O. Runtime actions require an active,
hash-bound two-phase binding and are deliberately not run during preparation.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import runpy
import subprocess
import sys
from pathlib import Path
from typing import Mapping

ROOT = Path(r"D:\Jupyter\Chromatography_agent")
RUN = ROOT / "testing/runs/2026-10-28-data-import-audit03"
B1 = RUN / "audit03-successor-recovery-binding.json"
B2 = RUN / "audit03-successor-original-start-binding.json"
RECEIPT = RUN / "audit03-pin-binding-successor-config-restore.json"
RUNTIME = Path(r"C:\Users\13459\AppData\Local\ChromatographyPlatform\data-accept-20261026")
STATE_ROOT = Path(r"C:\Users\13459\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0\LocalCache\Local\ChromatographyPlatform\data-accept-20261026\state")
LEGACY_LEDGER = STATE_ROOT / "dataset-csv-scan-v1-audit02-20261027.used"
LEGACY_REQUEST = STATE_ROOT / "dataset-csv-scan-v1-audit02-20261027.request.json"
LEGACY_REQUEST_SHA256 = "ec3e44ff8b52266c80c48c44d4bbf625473a364b5cd60f8706a319c2f20f4f06"
LEGACY_LEDGER_SHA256 = "41bb2e5a6432096c2cb786d4ac440814d81fdc3fdc6f7c6a2509a8679d48d4e0"
CONFIG = RUNTIME / "runtime.json"
PENDING = RUNTIME / "runtime.json.source-pin-audit03-successor-restore.pending"
BACKUP = RUNTIME / "runtime.json.audit03-successor-before.json"
ASSISTANT_CONFIG = Path(r"C:\Users\13459\AppData\Local\ChromatographyPlatform\assistant-accept-20261006\runtime.json")
START_PLATFORM = ROOT / "deploy/v1/start-platform.ps1"
STARTUP_CHECK = RUN / "audit03-successor-original-restore-01/startup-check.py"
START_WRAPPER = RUN / "audit03-successor-original-start-once.ps1"
START_MANIFEST = RUN / "audit03-successor-original-restore-01/manifest.json"
AUTHORIZATION_TEMPLATE = RUN / "audit03-successor-original-restore-01/authorization.template.json"
PYTHON = Path(r"D:\Anaconda\envs\SFC\python.exe")
PWSH = Path(r"C:\Users\13459\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\powershell\pwsh.exe")
CORE = ROOT / "testing/runs/2026-10-26-data-center-planning/complete-runtime04-pin-03.py"
CORE_SHA256 = "0dff88173bf7856f7b3417c4af1bcd8e1b9ac2ba557be3e85a8968c78827a525"
SUCCESSOR_SOURCE = RUN / "audit03-pin-binding-successor.py"
SUCCESSOR_SOURCE_SHA256 = "a386a0f689783c01b08de56185eeca441730cfa98111cb53f811fa015b1b81ba"
KEY = "CHROM_BACKEND_SOURCE_MANIFEST_SHA256"
ASSISTANT_SHA256 = "b1e15b036a22e080dfc38dc2a3faf8eee475dd8b2ff8308336d174b14d09a106"
BASELINE_SHA256 = "e475ad2f9299bc659f295271ac7607f9bcc7295520edf004a67354cb2bac4c81"
CANDIDATE_SHA256 = "001cd2e7871b97be2c6fe263b0347995f50de555c3c73c1b26e64d0ce817f4f5"
BASELINE_PIN = "31675275a79398770cbcb8238b3ee09d4580cb953dc1f6c2e0ebf82edddf7886"
CANDIDATE_PIN = "aa89cfca8cc212dcc762211d28147f51bbe89f131b9332c46f20dcb115fd8a89"
WINDOW_ID = "audit03-successor-binding-01"
LEGACY_ATTEMPT_UTC = "2026-10-27T17:32:08.855993Z"
LEGACY_DEADLINE_UTC = "2026-10-27T17:34:12.341Z"
MIN_COMMIT_HEADROOM = "3758096384"
REASON_CODES = {
    "RECEIPT_EXISTS_NO_REPLAY", "RESTORE_ARTIFACT_EXISTS_NO_REPLAY", "CURRENT_CONFIG_IDENTITY_OR_HASH_MISMATCH",
    "FRESH_BOOT_BINDING_CHANGED", "INDEPENDENT_BACKUP_REQUIRED", "PENDING_BYTES_MISMATCH",
    "PENDING_ACL_BYTES_CHANGED", "FRESH_STOPPED_OR_PORT_GATE_FAILED", "FRESH_IDLE_GATE_FAILED",
    "UNKNOWN_EXCEPTION_CHANGED", "RESOURCE_HEADROOM_GATE_FAILED", "CAS_BYTES_CHANGED",
    "CAS_IDENTITY_CHANGED", "PIN_BYTES_CHANGED_DURING_ACL_COPY", "PIN_BYTES_CHANGED_BEFORE_REPLACE",
    "ATOMIC_PIN_REPLACE_MISMATCH", "FINAL_CONFIG_SHA_MISMATCH", "ACL_COPY_FAILED", "ACL_METADATA_INVALID",
    "PINNED_CORE_CHANGED", "BINDING_AUTHORIZATION_INVALID", "CONFIG_PIN_BINDING_INVALID",
    "UNKNOWN_EXCEPTION_BINDING_INVALID", "BACKUP_ACTUAL_MISMATCH", "NO_CHANGE_BYTES_MISMATCH",
    "TEMPLATE_NOT_ACTIVE", "RESOURCE_EVIDENCE_INVALID", "NO_CHANGE_BYTES_OR_IDENTITY_CHANGED",
    "CONFIG_BACKUP_ACL_MISMATCH", "ASSISTANT_CONFIG_CHANGED", "LEGACY_UNKNOWN_IDENTITY_CHANGED",
    "UNCLASSIFIED_EXCEPTION",
}
PENDING_KEYS = {
    "dataset_scan_completion_unknown", "job", "job_attempt", "outbox",
    "data_import", "queue", "queue_inflight",
}
LEASE_KEYS = {"job_attempt_active", "outbox_active"}
ASSISTANT_LEASE_KEYS = LEASE_KEYS | {"agent_run_active", "agent_attempt_active"}
CHECK_KEYS = (
    "binding_hash", "approval_scope_plan_qa", "stop_receipt", "assistant_config",
    "data_config_baseline", "config_identity", "backup_bytes_identity",
    "both_profiles_stopped", "ports_free", "queue_and_leases",
    "preserved_unknown_exception", "resources",
)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _file_sha(path: Path) -> str:
    return _sha(path.read_bytes())


def _json_file(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if type(value) is not dict:
        raise ValueError("JSON_OBJECT_REQUIRED")
    return value


def _safe_ref(ref: object, *, runtime_names: set[str] = frozenset()) -> Path:
    if not isinstance(ref, str) or not ref or "\\" in ref or ":" in ref and not ref.startswith("runtime:"):
        raise ValueError("ARTIFACT_REF_INVALID")
    if ref.startswith("runtime:"):
        if ref not in runtime_names:
            raise ValueError("RUNTIME_REF_INVALID")
        return {
            "runtime:config": CONFIG,
            "runtime:successor_before": BACKUP,
            "runtime:restore_pending": PENDING,
        }[ref]
    rel = Path(ref)
    if rel.is_absolute() or any(part in (".", "..") for part in rel.parts) or len(rel.parts) != 1:
        raise ValueError("ARTIFACT_REF_INVALID")
    path = RUN / rel.name
    if path.resolve().parent != RUN.resolve():
        raise ValueError("ARTIFACT_REF_INVALID")
    return path


def _artifact(obj: object, *, runtime_names: set[str] = frozenset(), nullable: bool = False) -> tuple[Path, str] | None:
    if obj is None and nullable:
        return None
    if type(obj) is not dict or not {"path", "sha256"} <= set(obj) or set(obj) - {
            "path", "sha256", "status", "qa_status", "mode", "write_attempted",
            "configuration_write_observed", "final_config_verification"}:
        raise ValueError("ARTIFACT_BINDING_INVALID")
    path = _safe_ref(obj["path"], runtime_names=runtime_names)
    digest = obj["sha256"]
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("ARTIFACT_HASH_INVALID")
    if _file_sha(path) != digest:
        raise ValueError("ARTIFACT_HASH_MISMATCH")
    return path, digest


def _subject_sha(binding: dict, phase: str) -> str:
    clone = json.loads(json.dumps(binding))
    if phase == "restore":
        clone.get("authorization", {}).pop("qa_seal", None)
    elif phase == "start":
        clone.pop("fresh_identity_seal", None)
    else:
        raise ValueError("SUBJECT_PHASE_INVALID")
    return _sha(json.dumps(clone, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _start_dependencies_valid(deps: object) -> bool:
    try:
        if (type(deps) is not dict or set(deps) != {
                "config_restore", "original_start_wrapper", "original_start_manifest", "authorization_template"}):
            return False
        helper_sha = _file_sha(Path(__file__).resolve())
        expected = {
            "config_restore": helper_sha,
            "original_start_wrapper": _file_sha(START_WRAPPER),
            "original_start_manifest": _file_sha(START_MANIFEST),
            "authorization_template": _file_sha(AUTHORIZATION_TEMPLATE),
        }
        if deps != expected:
            return False
        helper = _json_file(START_MANIFEST).get("helper_source")
        return (type(helper) is dict and helper.get("path") == Path(__file__).name and
                helper.get("sha256") == helper_sha)
    except (OSError, ValueError, TypeError, KeyError):
        return False


def _identity(path: Path) -> dict:
    """Return a non-secret identity tuple; runtime use is Windows-only."""
    st = path.stat(follow_symlinks=False)
    if path.is_symlink():
        raise ValueError("REPARSE_POINT_REJECTED")
    acl_script = r"""
$ErrorActionPreference='Stop'
$i=Get-Item -LiteralPath $env:CHROM_IDENTITY_PATH -Force
$a=Get-Acl -LiteralPath $env:CHROM_IDENTITY_PATH
$sid=$a.GetOwner([Security.Principal.SecurityIdentifier]).Value
$sddl=$a.GetSecurityDescriptorSddlForm([Security.AccessControl.AccessControlSections]::All)
[Console]::WriteLine((@{owner_sid_sha256=[Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($sid))).ToLowerInvariant();sddl_sha256=[Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($sddl))).ToLowerInvariant();dacl_protected=$a.AreAccessRulesProtected;reparse_point=(($i.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)}|ConvertTo-Json -Compress))
"""
    env = os.environ.copy()
    env["CHROM_IDENTITY_PATH"] = str(path)
    result = subprocess.run([str(PWSH), "-NoProfile", "-NonInteractive", "-Command", acl_script],
                            capture_output=True, text=True, timeout=30, env=env)
    if result.returncode:
        raise RuntimeError("IDENTITY_QUERY_FAILED")
    acl = json.loads(result.stdout)
    if type(acl) is not dict or acl.get("reparse_point") is not False or acl.get("dacl_protected") is not True:
        raise RuntimeError("IDENTITY_ACL_INVALID")
    return _require_identity({
        "volume_serial_decimal_str": str(st.st_dev),
        "file_id_hex": format(st.st_ino, "x"),
        "mtime_ns_decimal_str": str(st.st_mtime_ns),
        "owner_sid_sha256": acl["owner_sid_sha256"],
        "sddl_sha256": acl["sddl_sha256"],
        "dacl_protected": True,
        "reparse_point": False,
        "size_bytes_decimal_str": str(st.st_size),
    })


def _require_identity(value: object) -> dict:
    keys = {"volume_serial_decimal_str", "file_id_hex", "mtime_ns_decimal_str", "owner_sid_sha256",
            "sddl_sha256", "dacl_protected", "reparse_point", "size_bytes_decimal_str"}
    if type(value) is not dict or set(value) != keys:
        raise ValueError("IDENTITY_SCHEMA_INVALID")
    for key in ("volume_serial_decimal_str", "mtime_ns_decimal_str", "size_bytes_decimal_str"):
        if not isinstance(value[key], str) or not value[key].isdigit():
            raise ValueError("IDENTITY_NUMBER_INVALID")
    if not isinstance(value["file_id_hex"], str) or not re.fullmatch(r"[0-9a-f]+", value["file_id_hex"]):
        raise ValueError("IDENTITY_FILE_ID_INVALID")
    for key in ("owner_sid_sha256", "sddl_sha256"):
        if not isinstance(value[key], str) or not re.fullmatch(r"[0-9a-f]{64}", value[key]):
            raise ValueError("IDENTITY_HASH_INVALID")
    if value["dacl_protected"] is not True or value["reparse_point"] is not False:
        raise ValueError("IDENTITY_ACL_INVALID")
    return value


def _fresh_resource_gate() -> bool:
    script = "$ErrorActionPreference='Stop'; $p=Get-CimInstance Win32_PerfRawData_PerfOS_Memory; [Console]::WriteLine(([long]$p.CommitLimit-[long]$p.CommittedBytes).ToString([Globalization.CultureInfo]::InvariantCulture))"
    result = subprocess.run([str(PWSH), "-NoProfile", "-NonInteractive", "-Command", script],
                            capture_output=True, text=True, timeout=30)
    return result.returncode == 0 and result.stdout.strip().isdigit() and int(result.stdout.strip()) >= int(MIN_COMMIT_HEADROOM)


def _fresh_boot_gate(binding: dict) -> bool:
    script = "$ErrorActionPreference='Stop'; $v=(Get-CimInstance Win32_OperatingSystem).LastBootUpTime; [Console]::WriteLine($v.ToUniversalTime().ToString('o',[Globalization.CultureInfo]::InvariantCulture))"
    result = subprocess.run([str(PWSH), "-NoProfile", "-NonInteractive", "-Command", script],
                            capture_output=True, text=True, timeout=30)
    if result.returncode:
        return False
    try:
        actual = dt.datetime.fromisoformat(result.stdout.strip().replace("Z", "+00:00"))
        expected = dt.datetime.fromisoformat(binding.get("boot", {}).get("cim_boot_utc", "").replace("Z", "+00:00"))
        return actual.astimezone(dt.timezone.utc) == expected.astimezone(dt.timezone.utc)
    except (TypeError, ValueError):
        return False


def _fresh_stopped_ports_gate() -> tuple[bool, bool]:
    seen = set()
    for profile in ("data-20261026", "assistant-20261006"):
        result = subprocess.run([str(PWSH), "-NoProfile", "-File", str(START_PLATFORM), "-Profile", profile,
                                 "-Action", "status", "-NoBrowser"], capture_output=True, text=True, timeout=45)
        if result.returncode:
            return False, False
        try:
            status = json.loads(result.stdout)
        except Exception:
            return False, False
        rows = status.get("selected", []) + status.get("other", [])
        if {row.get("service") for row in rows} != {"api", "worker", "frontend"} or any(
                row.get("status") != "STOPPED" for row in rows):
            return False, False
        seen.update(row.get("service") for row in rows)
    port_script = "$ErrorActionPreference='Stop'; $p=@(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort -in @(9012,5174) }); [Console]::WriteLine($p.Count)"
    result = subprocess.run([str(PWSH), "-NoProfile", "-NonInteractive", "-Command", port_script],
                            capture_output=True, text=True, timeout=45)
    return seen == {"api", "worker", "frontend"}, result.returncode == 0 and result.stdout.strip() == "0"


def _legacy_unknown_valid() -> bool:
    try:
        request_raw, ledger_raw = LEGACY_REQUEST.read_bytes(), LEGACY_LEDGER.read_bytes()
        if (_sha(request_raw) != LEGACY_REQUEST_SHA256 or _sha(ledger_raw) != LEGACY_LEDGER_SHA256):
            return False
        ledger = json.loads(ledger_raw)
        return (type(ledger) is dict and ledger.get("status") == "STARTED" and
                ledger.get("scan_completed") is None and ledger.get("scan_returncode") is None and
                ledger.get("helper_pid") is None and
                ledger.get("request_sha256") == LEGACY_REQUEST_SHA256)
    except (OSError, ValueError, TypeError):
        return False


def _readonly_counts(profile: str) -> tuple[dict, dict]:
    result = subprocess.run([str(PYTHON), "-B", str(STARTUP_CHECK), "--profile", profile, "--idle"],
                            capture_output=True, text=True, timeout=60)
    if result.returncode not in (0, 3):
        raise RuntimeError("IDLE_CHECK_FAILED")
    value = json.loads(result.stdout)
    if value.get("read_only") is not True or value.get("profile") != profile:
        raise RuntimeError("IDLE_CHECK_INVALID")
    pending, leases = value.get("pending_counts"), value.get("active_leases")
    if type(pending) is not dict or type(leases) is not dict:
        raise RuntimeError("IDLE_COUNTS_MISSING")
    return pending, leases


def _known_counts(profile: str, pending: Mapping[str, int], leases: Mapping[str, int]) -> bool:
    if profile == "data-20261026":
        expected_pending, expected_leases = PENDING_KEYS, LEASE_KEYS
    elif profile == "assistant-20261006":
        expected_pending = PENDING_KEYS - {"dataset_scan_completion_unknown", "data_import"} | {
            "agent_run", "agent_attempt"}
        expected_leases = ASSISTANT_LEASE_KEYS
    else:
        return False
    return (set(pending) == expected_pending and set(leases) == expected_leases and
            all(type(v) is int and v >= 0 for v in pending.values()) and
            all(type(v) is int and v >= 0 for v in leases.values()))


def _zero_counts(profile: str, pending: Mapping[str, int], leases: Mapping[str, int], *, allow_unknown: bool) -> bool:
    if not _known_counts(profile, pending, leases):
        return False
    ignored = {"dataset_scan_completion_unknown"} if allow_unknown and profile == "data-20261026" else set()
    return all(value == 0 for key, value in pending.items() if key not in ignored) and all(value == 0 for value in leases.values())


def _b1(path: Path = B1, expected_sha256: str | None = None, *, active: bool) -> tuple[dict, str]:
    raw = path.read_bytes()
    digest = _sha(raw)
    if expected_sha256 is not None and digest != expected_sha256.lower():
        raise ValueError("BINDING_HASH_MISMATCH")
    value = json.loads(raw.decode("utf-8-sig"))
    if (type(value) is not dict or value.get("schema_version") != "audit03-successor-recovery-binding/v1" or
            value.get("window_id") != WINDOW_ID or value.get("status") != ("ACTIVE_CONFIG_RESTORE" if active else "TEMPLATE_NOT_ACTIVE")):
        raise ValueError("TEMPLATE_NOT_ACTIVE" if active else "BINDING_SCHEMA_INVALID")
    return value, digest


def _b2(path: Path = B2, expected_sha256: str | None = None) -> tuple[dict, str]:
    raw = path.read_bytes()
    digest = _sha(raw)
    if expected_sha256 is not None and digest != expected_sha256.lower():
        raise ValueError("BINDING_HASH_MISMATCH")
    value = json.loads(raw.decode("utf-8-sig"))
    if (type(value) is not dict or value.get("schema_version") != "audit03-successor-original-start-binding/v1" or
            value.get("window_id") != WINDOW_ID or value.get("status") != "ACTIVE_ORIGINAL_START"):
        raise ValueError("TEMPLATE_NOT_ACTIVE")
    return value, digest


def _validate_b2_chain(binding: dict) -> tuple[dict, str, dict]:
    ref = binding.get("restore_binding")
    if type(ref) is not dict:
        raise ValueError("RESTORE_BINDING_INVALID")
    b1_path, b1_hash = _artifact(ref)
    if b1_path != B1:
        raise ValueError("RESTORE_BINDING_PATH_INVALID")
    b1, actual_b1_hash = _b1(b1_path, b1_hash, active=True)
    if not _approval_b1(b1) or not _stop_and_boot_valid(b1) or not _binding_attempt_valid(b1):
        raise ValueError("RESTORE_BINDING_INVALID")
    restore = binding.get("restore_receipt")
    if type(restore) is not dict:
        raise ValueError("RESTORE_RECEIPT_BINDING_INVALID")
    receipt_path, receipt_hash = _artifact(restore)
    if receipt_path != RECEIPT:
        raise ValueError("RESTORE_RECEIPT_PATH_INVALID")
    receipt = _json_file(receipt_path)
    if (restore.get("status") not in {"RESTORED_VERIFIED", "NO_CHANGE_VERIFIED"} or
            restore.get("final_config_verification") != "PASSED" or
            receipt.get("window_id") != WINDOW_ID or receipt.get("binding_sha256") != actual_b1_hash or
            receipt.get("status") != restore.get("status") or
            receipt.get("mode") != restore.get("mode") or
            receipt.get("write_attempted") is not restore.get("write_attempted") or
            receipt.get("configuration_write_observed") != restore.get("configuration_write_observed") or
            receipt.get("final_config_verification") != "PASSED"):
        raise ValueError("RESTORE_RECEIPT_INVALID")
    mode = restore.get("mode")
    if mode not in {"RESTORE_CANDIDATE_TO_BASELINE", "SKIP_ALREADY_BASELINE"}:
        raise ValueError("RESTORE_MODE_INVALID")
    if mode == "RESTORE_CANDIDATE_TO_BASELINE":
        if (restore.get("status") != "RESTORED_VERIFIED" or restore.get("write_attempted") is not True or
                restore.get("configuration_write_observed") is not True or
                b1.get("pre_restore", {}).get("independent_backup") is None):
            raise ValueError("RESTORE_WRITE_FACT_INVALID")
    else:
        if (restore.get("status") != "NO_CHANGE_VERIFIED" or restore.get("write_attempted") is not False or
                restore.get("configuration_write_observed") is not None or
                b1.get("pre_restore", {}).get("mode") != "SKIP_ALREADY_BASELINE"):
            raise ValueError("NO_CHANGE_FACT_INVALID")
    b2_stop, b1_stop = binding.get("stop_receipt"), b1.get("stop_receipt")
    if (type(b2_stop) is not dict or type(b1_stop) is not dict or
            b2_stop.get("path") != b1_stop.get("path") or b2_stop.get("sha256") != b1_stop.get("sha256") or
            _safe_ref(b2_stop.get("path")) != RUN / "audit03-successor-original-stop.json"):
        raise ValueError("STOP_RECEIPT_LINK_INVALID")
    seal_obj = binding.get("fresh_identity_seal")
    seal = _artifact(seal_obj)
    if (not seal or seal_obj.get("qa_status") != "QA_VERIFIED" or
            seal[0] != RUN / "audit03-successor-original-start-qa-seal.json"):
        raise ValueError("START_QA_SEAL_INVALID")
    seal_doc = _json_file(seal[0])
    if (seal_doc.get("window_id") != WINDOW_ID or seal_doc.get("subject_sha256") != _subject_sha(binding, "start") or
            seal_doc.get("schema_version") != "audit03-successor-qa-seal/v1" or
            seal_doc.get("phase") != "ORIGINAL_START" or seal_doc.get("status") != "QA_VERIFIED" or
            seal_doc.get("status") != seal_obj.get("qa_status")):
        raise ValueError("START_QA_SEAL_SUBJECT_MISMATCH")
    deps = seal_doc.get("dependency_hashes")
    if not _start_dependencies_valid(deps):
        raise ValueError("START_QA_DEPENDENCIES_INVALID")
    post = binding.get("post_restore")
    if type(post) is not dict or any(post.get(k) != v for k, v in {
            "assistant_config_sha256": ASSISTANT_SHA256,
            "data_config_sha256": BASELINE_SHA256,
            "data_source_pin_sha256": BASELINE_PIN}.items()):
        raise ValueError("POST_RESTORE_PINS_INVALID")
    current_identity = _require_identity(post.get("current_config_identity"))
    if not _post_restore_receipt_matches(post, receipt):
        raise ValueError("POST_RESTORE_RECEIPT_IDENTITY_MISMATCH")
    if binding.get("status") != "ACTIVE_ORIGINAL_START":
        raise ValueError("TEMPLATE_NOT_ACTIVE")
    if not _fresh_boot_gate(b1):
        raise ValueError("FRESH_BOOT_BINDING_CHANGED")
    if not _legacy_unknown_valid():
        raise ValueError("LEGACY_UNKNOWN_IDENTITY_CHANGED")
    return b1, actual_b1_hash, receipt


def _post_restore_receipt_matches(post: dict, receipt: dict) -> bool:
    try:
        identity = _require_identity(post.get("current_config_identity"))
        return (post.get("data_config_sha256") == BASELINE_SHA256 and
                receipt.get("post_config_sha256") == BASELINE_SHA256 and
                receipt.get("post_config_identity") == identity)
    except (ValueError, TypeError):
        return False


def _approval_b1(binding: dict) -> bool:
    auth = binding.get("authorization")
    if type(auth) is not dict:
        return False
    try:
        scope = _artifact(auth.get("scope"))
        plan = _artifact(auth.get("plan"))
        qa_obj = auth.get("qa_seal")
        qa = _artifact(qa_obj)
        source = _artifact(auth.get("successor_source"))
        this_source = _artifact(auth.get("restore_source"))
        if not auth.get("approval_ref") or not scope or not plan or not qa or not source or not this_source:
            return False
        if (scope[0] != RUN / "audit03-successor-real-window-scope-01.md" or
                plan[0] != RUN / "audit03-pin-binding-successor-plan.json" or
                qa[0] != RUN / "audit03-successor-recovery-qa-seal.json"):
            return False
        qa_obj_file = _json_file(qa[0])
        subject = _subject_sha(binding, "restore")
        if _file_sha(SUCCESSOR_SOURCE) != SUCCESSOR_SOURCE_SHA256 or source[0] != SUCCESSOR_SOURCE or source[1] != SUCCESSOR_SOURCE_SHA256:
            return False
        successor = runpy.run_path(str(SUCCESSOR_SOURCE), run_name="audit03_successor_plan_validator")
        successor["_load_plan"].__globals__["PLAN"] = plan[0]
        successor["_load_plan"](plan[1])
        deps = qa_obj_file.get("dependency_hashes")
        return (source[1] == SUCCESSOR_SOURCE_SHA256 and source[0] == SUCCESSOR_SOURCE and
                this_source[1] == _file_sha(Path(__file__).resolve()) and this_source[0] == Path(__file__).resolve() and
                isinstance(auth.get("approval_ref"), str) and bool(auth.get("approval_ref")) and
                isinstance(qa_obj, dict) and qa_obj.get("status") == "QA_VERIFIED" and
                qa_obj_file.get("schema_version") == "audit03-successor-qa-seal/v1" and
                qa_obj_file.get("window_id") == WINDOW_ID and qa_obj_file.get("phase") == "CONFIG_RESTORE" and
                qa_obj_file.get("subject_sha256") == subject and qa_obj_file.get("status") == "QA_VERIFIED" and
                this_source[1] == _file_sha(Path(__file__).resolve()) and
                _start_dependencies_valid(deps))
    except (OSError, ValueError, TypeError, KeyError):
        return False


def _binding_attempt_valid(binding: dict) -> bool:
    attempt = binding.get("binding_attempt")
    if type(attempt) is not dict or type(attempt.get("apply_invocations")) is not int or attempt.get("apply_invocations") not in (0, 1):
        return False
    n = attempt["apply_invocations"]
    if n == 0:
        return (attempt.get("launch_receipt") is None and attempt.get("operator_receipt") is None and
                attempt.get("stdout_receipt") is None and attempt.get("configuration_write_observed") is None and
                attempt.get("core_completion_returned") is None and attempt.get("final_config_verification") == "UNKNOWN")
    try:
        auth = binding["authorization"]
        _, scope_sha = _artifact(auth["scope"])
        _, plan_sha = _artifact(auth["plan"])
        launch_ref = attempt.get("launch_receipt")
        if type(launch_ref) is not dict or launch_ref.get("path") != "audit03-pin-binding-successor-launch.json":
            return False
        launch_path, _ = _artifact(launch_ref)
        if launch_path != RUN / "audit03-pin-binding-successor-launch.json":
            return False
        launch = _json_file(launch_path)
        if (launch.get("schema_version") != "audit03-successor-apply-launch/v1" or
                launch.get("window_id") != WINDOW_ID or launch.get("status") != "RETURNED" or
                launch.get("user_approval_ref") != auth.get("approval_ref") or
                launch.get("scope_sha256") != scope_sha or launch.get("plan_sha256") != plan_sha or
                launch.get("source_sha256") != SUCCESSOR_SOURCE_SHA256 or
                type(launch.get("apply_invocations")) is not int or launch.get("apply_invocations") != 1 or launch.get("child_ended") is not True or
                type(launch.get("exit_code")) is not int or
                not _positive_decimal(launch.get("child_pid_decimal_str")) or
                not _positive_decimal(launch.get("child_creation_filetime_decimal_str")) or
                not isinstance(launch.get("at_utc"), str) or not launch["at_utc"] or
                _file_sha(SUCCESSOR_SOURCE) != SUCCESSOR_SOURCE_SHA256):
            return False

        records = []
        for key, fixed_name in (("operator_receipt", "audit03-pin-binding-successor-receipt.json"),
                                ("stdout_receipt", "audit03-pin-binding-successor.stdout.log")):
            ref = attempt.get(key)
            if ref is None:
                continue
            if type(ref) is not dict or ref.get("path") != fixed_name:
                return False
            path, _ = _artifact(ref)
            if path != RUN / fixed_name:
                return False
            try:
                record = _json_file(path) if key == "operator_receipt" else json.loads(path.read_text(encoding="utf-8-sig").strip())
            except (OSError, ValueError, TypeError):
                return False
            if type(record) is not dict:
                return False
            records.append((record, key))

        facts = {"configuration_write_observed": [], "core_completion_returned": [], "final_config_verification": []}
        for record, kind in records:
            status = record.get("status")
            if status == "STOPPED":
                if (kind != "stdout_receipt" or launch["exit_code"] == 0 or record.get("retry") is not False or
                        not isinstance(record.get("error_type"), str) or not record["error_type"] or
                        attempt.get("configuration_write_observed") is not None or
                        attempt.get("core_completion_returned") is not None or
                        attempt.get("final_config_verification") != "UNKNOWN"):
                    return False
                continue
            if (record.get("plan_sha256") != plan_sha or record.get("script_sha256") != SUCCESSOR_SOURCE_SHA256 or
                    record.get("baseline_config_sha256") != BASELINE_SHA256 or
                    record.get("expected_new_config_sha256") != CANDIDATE_SHA256 or
                    record.get("source_manifest_sha256") != CANDIDATE_PIN or
                    record.get("retry") is not False or record.get("rollback") is not False or
                    status not in {"STARTED_AUDIT03_SUCCESSOR_PIN_BINDING", "SUCCEEDED_AUDIT03_SUCCESSOR_PIN_BINDING_UPDATED",
                                   "FAILED_STOP_NO_RETRY_NO_ROLLBACK"}):
                return False
            for key in ("configuration_write_observed", "core_completion_returned"):
                value = record.get(key)
                if value is not None and type(value) is not bool:
                    return False
                if value is not None:
                    facts[key].append(value)
            if status != "STARTED_AUDIT03_SUCCESSOR_PIN_BINDING":
                final_record = record.get("final_config_verification")
                if final_record not in {"PASSED", "FAILED", "UNKNOWN"}:
                    return False
                facts["final_config_verification"].append(final_record)
        for key in ("configuration_write_observed", "core_completion_returned"):
            binding_value = attempt.get(key)
            if type(binding_value) not in (bool, type(None)):
                return False
            if True in facts[key] and binding_value is not True:
                return False
            if binding_value is True and True not in facts[key]:
                return False
            if binding_value is False and False not in facts[key] and True not in facts[key]:
                return False
        final = attempt.get("final_config_verification")
        if final not in {"PASSED", "FAILED", "UNKNOWN"}:
            return False
        if facts["final_config_verification"] and final != facts["final_config_verification"][-1]:
            return False
        return True
    except (OSError, ValueError, TypeError):
        return False


def _positive_decimal(value: object) -> bool:
    return isinstance(value, str) and value.isdigit() and int(value) > 0


def _attach_receipt_snapshot(error: BaseException, receipt: dict) -> None:
    try:
        setattr(error, "_audit03_restore_receipt_snapshot", dict(receipt))
    except Exception:
        pass


def _identity_snapshot_valid(snapshot: object) -> bool:
    if type(snapshot) is not dict or set(snapshot) != {"api", "worker", "frontend", "worker_marker"}:
        return False
    for name, image in (("api", "sfc_python"), ("worker", "sfc_python"), ("frontend", "node")):
        row = snapshot.get(name)
        if (type(row) is not dict or set(row) != {"pid_decimal_str", "creation_filetime_decimal_str", "image_ref", "image_sha256", "created_at_utc"} or
                not _positive_decimal(row.get("pid_decimal_str")) or
                not _positive_decimal(row.get("creation_filetime_decimal_str")) or row.get("image_ref") != image or
                not isinstance(row.get("image_sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", row["image_sha256"]) or
                not isinstance(row.get("created_at_utc"), str) or not row["created_at_utc"]):
            return False
    marker = snapshot.get("worker_marker")
    return (type(marker) is dict and set(marker) == {"pid_decimal_str", "process_starttime_decimal_str", "instance_id", "boot_id"} and
            marker.get("pid_decimal_str") == snapshot["worker"]["pid_decimal_str"] and
            marker.get("process_starttime_decimal_str") == snapshot["worker"]["creation_filetime_decimal_str"] and
            isinstance(marker.get("instance_id"), str) and bool(marker["instance_id"]) and
            (marker.get("boot_id") is None or isinstance(marker.get("boot_id"), str)))


def _stop_and_boot_valid(binding: dict) -> bool:
    try:
        stop = binding["stop_receipt"]
        stop_file, _ = _artifact({"path": stop["path"], "sha256": stop["sha256"]})
        if stop_file != RUN / "audit03-successor-original-stop.json":
            return False
        stop_doc = _json_file(stop_file)
        auth = binding["authorization"]
        scope = _artifact(auth["scope"])
        plan = _artifact(auth["plan"])
        if (stop.get("profile") != "assistant-20261006" or stop.get("stop_invocations") != 1 or
                stop.get("exit_code") != 0 or stop.get("status") != "STOPPED_VERIFIED" or
                stop_doc.get("schema_version") != "audit03-successor-stop/v1" or
                stop_doc.get("window_id") != WINDOW_ID or stop_doc.get("profile") != "assistant-20261006" or
                stop_doc.get("stop_invocations") != 1 or stop_doc.get("exit_code") != 0 or
                stop_doc.get("status") != "STOPPED_VERIFIED" or stop_doc.get("scope_sha256") != scope[1] or
                stop_doc.get("plan_sha256") != plan[1] or stop_doc.get("user_approval_ref") != auth.get("approval_ref") or
                stop_doc.get("both_profiles_stopped") is not True or stop_doc.get("ports_free") is not True):
            return False
        source_ready = _artifact(stop_doc.get("source_ready_receipt"))
        if source_ready[0] != RUN / "audit03-successor-original-ready.json":
            return False
        ready = _json_file(source_ready[0])
        boot = binding["boot"]
        evidence, _ = _artifact(boot["evidence"])
        if evidence != RUN / "audit03-successor-boot-seal.json":
            return False
        doc = _json_file(evidence)
        if (doc.get("schema_version") != "audit03-successor-boot/v1" or doc.get("window_id") != WINDOW_ID or
                doc.get("status") != "BOOT_BOUNDARY_VERIFIED" or
                boot.get("qa_status") != "QA_VERIFIED" or doc.get("qa_status") != "QA_VERIFIED"):
            return False
        evidence_values = {"cim_boot_utc": boot.get("cim_boot_utc"),
                           "kernel_start_utc": boot.get("kernel_start_utc"),
                           "uptime_derived_boot_utc": boot.get("uptime_derived_boot_utc")}
        legacy_attempt = dt.datetime.fromisoformat(doc["legacy_attempt_utc"].replace("Z", "+00:00"))
        legacy_deadline = dt.datetime.fromisoformat(doc["legacy_deadline_utc"].replace("Z", "+00:00"))
        boots = [dt.datetime.fromisoformat(v.replace("Z", "+00:00")) for v in evidence_values.values()]
        cim_boot, kernel_boot, uptime_boot = boots
        if any(value.tzinfo is None for value in boots):
            return False
        fresh = min(boots) > max(legacy_attempt, legacy_deadline)
        same_boot = (abs((kernel_boot - cim_boot).total_seconds()) <= 1.0 and
                     abs((uptime_boot - cim_boot).total_seconds()) <= 5.0)
        stop_identity = stop_doc.get("identity_snapshot")
        ready_identity = ready.get("identity_snapshot")
        if not _identity_snapshot_valid(stop_identity) or not _identity_snapshot_valid(ready_identity):
            return False
        if not _identity_snapshot_valid(stop.get("identity_snapshot")):
            return False
        return (doc.get("legacy_attempt_utc") == LEGACY_ATTEMPT_UTC and
                doc.get("legacy_deadline_utc") == LEGACY_DEADLINE_UTC and fresh and same_boot and
                doc.get("old_scan_completion_proven") is False and
                stop_identity == ready_identity == stop.get("identity_snapshot") and
                ready.get("schema_version") == "audit03-successor-original-ready/v1" and
                ready.get("window_id") == WINDOW_ID and ready.get("status") == "READY_IDENTITY_VERIFIED" and
                ready.get("scope_sha256") == scope[1] and ready.get("plan_sha256") == plan[1] and
                ready.get("boot_evidence_sha256") == _file_sha(evidence) and
                ready.get("assistant_config_sha256") == ASSISTANT_SHA256 and
                ready.get("data_config_sha256") == BASELINE_SHA256 and
                all(isinstance(v, str) and v for v in evidence_values.values()) and
                all(doc.get(k) == v for k, v in evidence_values.items()) and
                doc.get("qa_status") == boot.get("qa_status"))
    except (OSError, ValueError, TypeError, KeyError):
        return False


def _capture_acl(source: Path, target: Path) -> dict:
    core = _load_core()
    script = core["ACL_COPY_SCRIPT"]
    if script.count(".source-pin-04.pending") != 1:
        raise RuntimeError("ACL_TEMPLATE_INVALID")
    suffix = ".source-pin-audit03-successor-restore.pending" if target == PENDING else ".audit03-successor-before.json"
    script = script.replace(".source-pin-04.pending", suffix, 1)
    env = os.environ.copy()
    env.update({"CHROM_PIN_FORMAL": str(source), "CHROM_PIN_PENDING": str(target)})
    result = subprocess.run([str(PWSH), "-NoProfile", "-NonInteractive", "-Command", script],
                            capture_output=True, text=True, timeout=45, env=env)
    if result.returncode:
        raise RuntimeError("ACL_COPY_FAILED")
    data = json.loads(result.stdout)
    if (type(data) is not dict or data.get("source_sddl") != data.get("target_sddl") or
            data.get("source_protected") is not True or data.get("target_protected") is not True or
            not data.get("owner_sid")):
        raise RuntimeError("ACL_METADATA_INVALID")
    return {"sddl_sha256": _sha(data["source_sddl"].encode()), "protected": True, "owner_match": True}


def _load_core(replace_fn=None) -> dict:
    if _file_sha(CORE) != CORE_SHA256:
        raise RuntimeError("PINNED_CORE_CHANGED")
    core = runpy.run_path(str(CORE), run_name="audit03_successor_restore_core")
    if replace_fn is not None:
        # The frozen core's functions share this private globals dict; tests can
        # inject an isolated replacement without changing process-wide os.
        from types import SimpleNamespace
        core["complete_pin_files"].__globals__["os"] = SimpleNamespace(replace=replace_fn)
    return core


def _write_exclusive(path: Path, raw: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _fresh_pre_replace_gate(binding: dict, original: bytes, pending: bytes, current_identity: dict, backup_identity: dict) -> None:
    _fresh_operational_gates(binding)
    if CONFIG.read_bytes() != original or PENDING.read_bytes() != pending:
        raise RuntimeError("CAS_BYTES_CHANGED")
    if _identity(CONFIG) != current_identity or _identity(BACKUP) != backup_identity:
        raise RuntimeError("CAS_IDENTITY_CHANGED")


def _fresh_operational_gates(binding: dict) -> None:
    stopped, ports = _fresh_stopped_ports_gate()
    if not stopped or not ports:
        raise RuntimeError("FRESH_STOPPED_OR_PORT_GATE_FAILED")
    if _file_sha(ASSISTANT_CONFIG) != ASSISTANT_SHA256:
        raise RuntimeError("ASSISTANT_CONFIG_CHANGED")
    if not _legacy_unknown_valid():
        raise RuntimeError("LEGACY_UNKNOWN_IDENTITY_CHANGED")
    for profile in ("data-20261026", "assistant-20261006"):
        pending_counts, active_leases = _readonly_counts(profile)
        if not _zero_counts(profile, pending_counts, active_leases, allow_unknown=(profile == "data-20261026")):
            raise RuntimeError("FRESH_IDLE_GATE_FAILED")
        if profile == "data-20261026" and pending_counts.get("dataset_scan_completion_unknown") != 1:
            raise RuntimeError("UNKNOWN_EXCEPTION_CHANGED")
    if not _fresh_resource_gate():
        raise RuntimeError("RESOURCE_HEADROOM_GATE_FAILED")
    if not _fresh_boot_gate(binding):
        raise RuntimeError("FRESH_BOOT_BINDING_CHANGED")


def _validate_b1(binding: dict) -> tuple[str, bytes, dict, bytes | None, dict | None]:
    if binding.get("window_id") != WINDOW_ID or not _approval_b1(binding) or not _binding_attempt_valid(binding) or not _stop_and_boot_valid(binding):
        raise ValueError("BINDING_AUTHORIZATION_INVALID")
    pins = binding.get("pins")
    if type(pins) is not dict or any(pins.get(k) != v for k, v in {
            "assistant_config_sha256": ASSISTANT_SHA256,
            "data_config_baseline_sha256": BASELINE_SHA256,
            "data_candidate_sha256": CANDIDATE_SHA256,
            "data_original_pin_sha256": BASELINE_PIN,
            "data_candidate_pin_sha256": CANDIDATE_PIN}.items()):
        raise ValueError("CONFIG_PIN_BINDING_INVALID")
    unknown = binding.get("preserved_unknown_exception")
    if (type(unknown) is not dict or unknown.get("profile") != "data-20261026" or
            unknown.get("ledger_sha256") != "41bb2e5a6432096c2cb786d4ac440814d81fdc3fdc6f7c6a2509a8679d48d4e0" or
            unknown.get("request_sha256") != "ec3e44ff8b52266c80c48c44d4bbf625473a364b5cd60f8706a319c2f20f4f06" or
            unknown.get("unknown_key") != "dataset_scan_completion_unknown" or unknown.get("unknown_count") != 1 or
            unknown.get("all_other_pending_counts_zero") is not True or unknown.get("active_leases_zero") is not True or
            unknown.get("status") != "UNKNOWN_PRESERVED"):
        raise ValueError("UNKNOWN_EXCEPTION_BINDING_INVALID")
    resource = binding.get("resource_gate")
    if type(resource) is not dict or resource.get("minimum_commit_headroom_bytes_decimal_str") != MIN_COMMIT_HEADROOM:
        raise ValueError("RESOURCE_GATE_BINDING_INVALID")
    resource_path, _ = _artifact(resource.get("evidence"))
    if resource_path != RUN / "audit03-successor-resource-seal.json":
        raise ValueError("RESOURCE_EVIDENCE_PATH_INVALID")
    resource_doc = _json_file(resource_path)
    if (resource_doc.get("schema_version") != "audit03-successor-resource/v1" or
            resource_doc.get("window_id") != WINDOW_ID or resource_doc.get("status") != "RESOURCE_SAMPLED" or
            not all(isinstance(resource_doc.get(k), str) and resource_doc[k].isdigit() for k in
                    ("commit_limit_bytes_decimal_str", "committed_bytes_decimal_str", "headroom_bytes_decimal_str")) or
            int(resource_doc["headroom_bytes_decimal_str"]) != int(resource_doc["commit_limit_bytes_decimal_str"]) - int(resource_doc["committed_bytes_decimal_str"]) or
            int(resource_doc["headroom_bytes_decimal_str"]) < int(MIN_COMMIT_HEADROOM)):
        raise ValueError("RESOURCE_EVIDENCE_INVALID")
    pre = binding.get("pre_restore")
    if type(pre) is not dict or pre.get("mode") not in {"RESTORE_CANDIDATE_TO_BASELINE", "SKIP_ALREADY_BASELINE"}:
        raise ValueError("PRE_RESTORE_INVALID")
    config_obj = pre.get("current_config")
    if type(config_obj) is not dict or config_obj.get("sha256") not in {BASELINE_SHA256, CANDIDATE_SHA256}:
        raise ValueError("CURRENT_CONFIG_BINDING_INVALID")
    config_identity = _require_identity(config_obj.get("identity"))
    if binding["binding_attempt"]["apply_invocations"] == 0 and (
            pre.get("mode") != "SKIP_ALREADY_BASELINE" or config_obj.get("sha256") != BASELINE_SHA256):
        raise ValueError("NO_APPLY_REQUIRES_BASELINE")
    if pre.get("mode") == "SKIP_ALREADY_BASELINE" and config_obj.get("sha256") != BASELINE_SHA256:
        raise ValueError("SKIP_REQUIRES_BASELINE")
    backup_obj = pre.get("independent_backup")
    if backup_obj is None:
        if pre["mode"] != "SKIP_ALREADY_BASELINE" or config_obj["sha256"] != BASELINE_SHA256:
            raise ValueError("BACKUP_REQUIRED")
        backup_identity, backup_raw = None, None
    else:
        if type(backup_obj) is not dict or backup_obj.get("path") != "runtime:successor_before" or backup_obj.get("sha256") != BASELINE_SHA256:
            raise ValueError("BACKUP_BINDING_INVALID")
        backup_identity = _require_identity(backup_obj.get("identity"))
        if (config_identity["owner_sid_sha256"] != backup_identity["owner_sid_sha256"] or
                config_identity["sddl_sha256"] != backup_identity["sddl_sha256"]):
            raise ValueError("CONFIG_BACKUP_ACL_MISMATCH")
        backup_raw = BACKUP.read_bytes()
        if _sha(backup_raw) != BASELINE_SHA256 or _identity(BACKUP) != backup_identity:
            raise ValueError("BACKUP_ACTUAL_MISMATCH")
    return pre["mode"], CONFIG.read_bytes(), config_identity, backup_raw, backup_identity


def restore_config(binding_path: Path = B1, expected_binding_sha256: str | None = None) -> dict:
    """Restore candidate pin once or verify already-baseline bytes; never replay."""
    if RECEIPT.exists():
        raise RuntimeError("RECEIPT_EXISTS_NO_REPLAY")
    binding, binding_sha = _b1(binding_path, expected_binding_sha256, active=True)
    mode, original, current_identity, backup_raw, backup_identity = _validate_b1(binding)
    current_sha = _sha(original)
    expected_sha = BASELINE_SHA256 if mode == "SKIP_ALREADY_BASELINE" else CANDIDATE_SHA256
    if current_sha != expected_sha or _identity(CONFIG) != current_identity:
        raise RuntimeError("CURRENT_CONFIG_IDENTITY_OR_HASH_MISMATCH")
    _fresh_operational_gates(binding)
    if mode == "SKIP_ALREADY_BASELINE":
        if CONFIG.read_bytes() != original or _identity(CONFIG) != current_identity:
            raise RuntimeError("NO_CHANGE_BYTES_OR_IDENTITY_CHANGED")
        if backup_raw is not None and (
                _sha(BACKUP.read_bytes()) != BASELINE_SHA256 or _identity(BACKUP) != backup_identity):
            raise RuntimeError("BACKUP_ACTUAL_MISMATCH")
    receipt = {
        "schema_version": "audit03-successor-config-restore-receipt/v1",
        "window_id": WINDOW_ID, "status": "STARTED", "binding_sha256": binding_sha,
        "mode": mode, "configuration_write_observed": None,
        "binding_configuration_write_observed": binding["binding_attempt"]["configuration_write_observed"],
        "binding_core_completion_returned": binding["binding_attempt"]["core_completion_returned"],
        "write_attempted": False, "final_config_verification": "UNKNOWN", "retry": False,
        "automatic_retry": False, "rollback": False, "services_started": False,
        "database_business_writes": 0, "readonly_database_checks": True, "uac_or_scan": False,
    }
    _write_exclusive(RECEIPT, (json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))

    def save_receipt() -> None:
        RECEIPT.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if mode == "SKIP_ALREADY_BASELINE":
        primary_error = None
        try:
            if current_sha != BASELINE_SHA256 or CONFIG.read_bytes() != original:
                raise RuntimeError("NO_CHANGE_BYTES_MISMATCH")
            receipt.update(status="NO_CHANGE_VERIFIED", configuration_write_observed=None,
                           final_config_verification="PASSED", post_config_sha256=current_sha,
                           post_config_identity=_identity(CONFIG))
        except Exception as exc:
            primary_error = exc
            receipt.update(status="FAILED", error_type=type(exc).__name__)
            marker = str(exc) if type(exc) in (RuntimeError, ValueError) else ""
            receipt["reason_code"] = marker if marker in REASON_CODES else "UNCLASSIFIED_EXCEPTION"
            _attach_receipt_snapshot(exc, receipt)
            raise
        finally:
            try:
                save_receipt()
            except Exception as save_exc:
                if primary_error is None:
                    _attach_receipt_snapshot(save_exc, receipt)
                    raise
        return receipt
    if backup_raw is None or _sha(backup_raw) != BASELINE_SHA256 or backup_identity is None:
        raise RuntimeError("INDEPENDENT_BACKUP_REQUIRED")
    core = _load_core()
    core["validate_pin_pair"](original, backup_raw, formal_sha256=CANDIDATE_SHA256,
                              pending_sha256=BASELINE_SHA256, old_pin=CANDIDATE_PIN, new_pin=BASELINE_PIN)
    if PENDING.exists():
        raise RuntimeError("RESTORE_ARTIFACT_EXISTS_NO_REPLAY")
    primary_error = None
    try:
        _write_exclusive(PENDING, backup_raw)
        if _sha(PENDING.read_bytes()) != BASELINE_SHA256:
            raise RuntimeError("PENDING_BYTES_MISMATCH")
        receipt["pending_written"] = True
        save_receipt()
        _capture_acl(CONFIG, PENDING)
        pending_raw = PENDING.read_bytes()
        if _sha(pending_raw) != BASELINE_SHA256:
            raise RuntimeError("PENDING_ACL_BYTES_CHANGED")
        _fresh_pre_replace_gate(binding, original, pending_raw, current_identity, backup_identity)

        def copy_acl(source: Path, target: Path):
            return _capture_acl(source, target)
        def before_replace(_):
            _fresh_pre_replace_gate(binding, original, pending_raw, current_identity, backup_identity)
            receipt["write_attempted"] = True
            save_receipt()

        core["complete_pin_files"](
            CONFIG, PENDING, formal_sha256=CANDIDATE_SHA256, pending_sha256=BASELINE_SHA256,
            old_pin=CANDIDATE_PIN, new_pin=BASELINE_PIN, copy_acl=copy_acl, before_replace=before_replace,
        )
        receipt["core_completion_returned"] = True
        receipt["configuration_write_observed"] = True
        try:
            final_hash = _file_sha(CONFIG)
        except Exception:
            receipt["final_config_verification"] = "UNKNOWN"
            raise
        receipt["final_config_verification"] = "PASSED" if final_hash == BASELINE_SHA256 else "FAILED"
        if receipt["final_config_verification"] != "PASSED":
            raise RuntimeError("FINAL_CONFIG_SHA_MISMATCH")
        receipt.update(status="RESTORED_VERIFIED", post_config_sha256=BASELINE_SHA256,
                       post_config_identity=_identity(CONFIG))
    except Exception as exc:
        primary_error = exc
        receipt.update(status="FAILED", error_type=type(exc).__name__)
        marker = str(exc) if type(exc) in (RuntimeError, ValueError) else ""
        receipt["reason_code"] = marker if marker in REASON_CODES else "UNCLASSIFIED_EXCEPTION"
        if receipt["configuration_write_observed"] is not True:
            receipt["final_config_verification"] = "UNKNOWN"
        _attach_receipt_snapshot(exc, receipt)
        raise
    finally:
        try:
            save_receipt()
        except Exception as exc:
            if primary_error is None:
                _attach_receipt_snapshot(exc, receipt)
                raise
            receipt["final_save_error_type"] = type(exc).__name__
    return receipt


def check_preserved_unknown_exception(*, binding_path: Path = B2, expected_binding_sha256: str,
                                      profile: str, pending_counts: Mapping[str, int],
                                      active_leases: Mapping[str, int]) -> dict:
    checks = {"binding_hash": False, "profile": False, "unknown_exactly_one": False,
              "other_pending_zero": False, "leases_zero": False, "binding_exception": False}
    try:
        binding, digest = _b2(binding_path, expected_binding_sha256)
        _validate_b2_chain(binding)
        checks["binding_hash"] = digest == expected_binding_sha256.lower()
        checks["profile"] = profile == "data-20261026"
        checks["binding_exception"] = binding.get("preserved_unknown_exception") == {
            "profile": "data-20261026", "ledger_sha256": "41bb2e5a6432096c2cb786d4ac440814d81fdc3fdc6f7c6a2509a8679d48d4e0",
            "request_sha256": "ec3e44ff8b52266c80c48c44d4bbf625473a364b5cd60f8706a319c2f20f4f06",
            "unknown_key": "dataset_scan_completion_unknown", "unknown_count": 1,
            "all_other_pending_counts_zero": True, "active_leases_zero": True, "status": "UNKNOWN_PRESERVED"}
        checks["unknown_exactly_one"] = (set(pending_counts) == PENDING_KEYS and
            type(pending_counts.get("dataset_scan_completion_unknown")) is int and pending_counts["dataset_scan_completion_unknown"] == 1)
        checks["other_pending_zero"] = _zero_counts(profile, pending_counts, active_leases, allow_unknown=True)
        checks["leases_zero"] = (set(active_leases) == LEASE_KEYS and
            all(type(value) is int and value == 0 for value in active_leases.values()))
        ok = all(checks.values())
        return {"status": "PRESERVED_UNKNOWN_EXCEPTION_ALLOWED" if ok else "BLOCKED",
                "reason_code": "NONE" if ok else "UNKNOWN_EXCEPTION_BINDING_OR_COUNTS_INVALID", "checks": checks}
    except Exception:
        return {"status": "BLOCKED", "reason_code": "UNKNOWN_EXCEPTION_BINDING_INVALID", "checks": checks}


def check_start(binding_path: Path = B2, expected_binding_sha256: str | None = None) -> dict:
    checks = dict.fromkeys(CHECK_KEYS, False)
    digest = ""
    reason = "START_GATE_BLOCKED"
    try:
        binding, digest = _b2(binding_path, expected_binding_sha256)
        checks["binding_hash"] = True
        restore_ref = binding.get("restore_binding")
        if type(restore_ref) is not dict:
            raise ValueError("RESTORE_BINDING_INVALID")
        b1_path, b1_hash = _artifact(restore_ref)
        b1, actual_b1_hash = _b1(b1_path, b1_hash, active=True)
        checks["approval_scope_plan_qa"] = _approval_b1(b1)
        checks["stop_receipt"] = _stop_and_boot_valid(b1)
        checks["assistant_config"] = _file_sha(ASSISTANT_CONFIG) == ASSISTANT_SHA256
        checks["data_config_baseline"] = _file_sha(CONFIG) == BASELINE_SHA256
        post = binding.get("post_restore")
        expected_identity = _require_identity(post.get("current_config_identity"))
        checks["config_identity"] = _identity(CONFIG) == expected_identity
        restore = binding.get("restore_receipt")
        receipt_path, receipt_hash = _artifact({"path": restore["path"], "sha256": restore["sha256"]})
        receipt = _json_file(receipt_path)
        _validate_b2_chain(binding)
        checks["backup_bytes_identity"] = (
            restore.get("status") in {"RESTORED_VERIFIED", "NO_CHANGE_VERIFIED"} and
            restore.get("final_config_verification") == "PASSED" and
            receipt.get("binding_sha256") == actual_b1_hash and receipt.get("status") == restore.get("status") and
            receipt.get("final_config_verification") == "PASSED" and
            ((post.get("backup_sha256") == BASELINE_SHA256 and
              post.get("backup_identity") == b1.get("pre_restore", {}).get("independent_backup", {}).get("identity") and
              _file_sha(BACKUP) == BASELINE_SHA256 and
              _identity(BACKUP) == _require_identity(post.get("backup_identity"))) if post.get("backup_sha256") is not None else
             (restore.get("mode") == "SKIP_ALREADY_BASELINE" and
              b1.get("pre_restore", {}).get("independent_backup") is None and post.get("backup_identity") is None))
        )
        stopped, ports = _fresh_stopped_ports_gate()
        checks["both_profiles_stopped"], checks["ports_free"] = stopped, ports
        profiles_idle = True
        for profile in ("data-20261026", "assistant-20261006"):
            pending, leases = _readonly_counts(profile)
            if profile == "data-20261026":
                ordinary = _zero_counts(profile, pending, leases, allow_unknown=False)
                ex = check_preserved_unknown_exception(binding_path=binding_path, expected_binding_sha256=digest,
                                                       profile=profile, pending_counts=pending, active_leases=leases)
                checks["preserved_unknown_exception"] = ordinary or ex["status"] == "PRESERVED_UNKNOWN_EXCEPTION_ALLOWED"
                profiles_idle = profiles_idle and checks["preserved_unknown_exception"]
            else:
                profiles_idle = profiles_idle and _zero_counts(profile, pending, leases, allow_unknown=False)
        checks["queue_and_leases"] = profiles_idle
        checks["resources"] = _fresh_resource_gate()
        ready = all(checks.values())
        reason = "NONE" if ready else "START_GATE_CHECK_FAILED"
        return {"status": "READY_FOR_ORIGINAL_START" if ready else "BLOCKED",
                "binding_sha256": digest, "reason_code": reason, "checks": checks}
    except Exception:
        return {"status": "BLOCKED", "binding_sha256": digest, "reason_code": reason, "checks": checks}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--restore-config", action="store_true")
    group.add_argument("--check-start", action="store_true")
    parser.add_argument("--binding-sha256", required=True)
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[0-9a-fA-F]{64}", args.binding_sha256):
        parser.error("--binding-sha256 must be 64 hex characters")
    try:
        if args.restore_config:
            result = restore_config(B1, args.binding_sha256)
            result = {**result, "receipt_sha256": _file_sha(RECEIPT)}
        else:
            result = check_start(B2, args.binding_sha256)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result.get("status") in {"RESTORED_VERIFIED", "NO_CHANGE_VERIFIED", "READY_FOR_ORIGINAL_START"} else 1
    except Exception as exc:
        saved = getattr(exc, "_audit03_restore_receipt_snapshot", {})
        if not isinstance(saved, dict):
            saved = {}
        if not saved and args.restore_config and RECEIPT.is_file():
            try:
                saved = _json_file(RECEIPT)
            except Exception:
                saved = {}
        marker = str(exc) if type(exc) in (RuntimeError, ValueError) else ""
        reason = marker if marker in REASON_CODES else saved.get("reason_code", "RESTORE_FAILED")
        receipt_hash = None
        if args.restore_config and RECEIPT.is_file():
            try:
                receipt_hash = _file_sha(RECEIPT)
            except Exception:
                receipt_hash = None
        print(json.dumps({"status": "FAILED" if args.restore_config else "BLOCKED",
                          "binding_sha256": args.binding_sha256.lower(),
                          "reason_code": reason,
                          "configuration_write_observed": saved.get("configuration_write_observed"),
                          "final_config_verification": saved.get("final_config_verification", "UNKNOWN"),
                          "receipt_sha256": receipt_hash}, sort_keys=True))
        return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:
        marker = str(exc) if type(exc) in (RuntimeError, ValueError) else ""
        reason = marker if marker in REASON_CODES else "RESTORE_FAILED"
        print(json.dumps({"status": "FAILED", "binding_sha256": None, "reason_code": reason,
                          "configuration_write_observed": None, "final_config_verification": "UNKNOWN",
                          "receipt_sha256": None}, sort_keys=True))
        raise SystemExit(1)
