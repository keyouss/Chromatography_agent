"""Import-safe successor for the single-use audit03 BE10 source-pin operator."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import runpy
import subprocess
import sys
from pathlib import Path

ROOT = Path(r"D:\Jupyter\Chromatography_agent")
RUN = ROOT / "testing/runs/2026-10-28-data-import-audit03"
RUNTIME = Path(r"C:\Users\13459\AppData\Local\ChromatographyPlatform\data-accept-20261026")
CONFIG = RUNTIME / "runtime.json"
PENDING = RUNTIME / "runtime.json.source-pin-audit03-successor.pending"
BACKUP = RUNTIME / "runtime.json.audit03-successor-before.json"
RECEIPT = RUN / "audit03-pin-binding-successor-receipt.json"
PLAN = RUN / "audit03-pin-binding-successor-plan.json"
FREEZE = RUN / "audit03-source-freeze-02.json"
CANDIDATE = RUN / "audit03-deployment-candidate-02.json"
PATCH = RUN / "audit03-deployment-candidate-02.patch"
PACKAGE_PARITY = RUN / "audit03-package-parity-02.json"
DATABASE_BASELINE = RUN / "audit03-database-baseline-01.json"
OLD_CORE = ROOT / "testing/runs/2026-10-26-data-center-planning/complete-runtime04-pin-03.py"
OLD_CORE_SHA = "0dff88173bf7856f7b3417c4af1bcd8e1b9ac2ba557be3e85a8968c78827a525"
DIAGNOSTIC_HELPERS = RUN / "audit03-pin-binding-diagnostic-candidate.py"
DIAGNOSTIC_HELPERS_SHA = "3dbbe80b814f33802762c9c91f387f972db25f1859c7e530315a7b9ab69b188d"
START_PLATFORM = ROOT / "deploy/v1/start-platform.ps1"
PYTHON = Path(r"D:\Anaconda\envs\SFC\python.exe")
PWSH = Path(r"C:\Users\13459\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\powershell\pwsh.exe")
KEY = "CHROM_BACKEND_SOURCE_MANIFEST_SHA256"
OLD_PIN = "31675275a79398770cbcb8238b3ee09d4580cb953dc1f6c2e0ebf82edddf7886"
NEW_PIN = "aa89cfca8cc212dcc762211d28147f51bbe89f131b9332c46f20dcb115fd8a89"
BASELINE_CONFIG_SHA = "e475ad2f9299bc659f295271ac7607f9bcc7295520edf004a67354cb2bac4c81"
SOURCE_FREEZE_SHA = "02d5938b41ca501e6a8167a259b12b946846e3aafa23a70391e9dd240c759bde"
PACKAGE_PARITY_SHA = "ad0e69286eb2a51e17dddaaedb100f81acd564a4dd0f383c3e83f16291cfba0d"
PACKAGE_MANIFEST_SHA = NEW_PIN
DEPLOYMENT_CANDIDATE_SHA = "480fba03e5020633986e343a459aafad0575300b5e878159db91caed6b6da427"
DEPLOYMENT_PATCH_SHA = "15f3cf5441acaa801dbd88f9df012758505c57508fce7b1c5c166da9e6ccae4d"
DATABASE_BASELINE_SHA = "6cb80d393f0bff62ce2ec0e3aa06a164485c288b919eb647ddb715bbd6d22d34"
REQUEST_NAME = "dataset-csv-scan-v1-audit03-20261028.request.json"
LEDGER_NAME = "dataset-csv-scan-v1-audit03-20261028.used"
EXPECTED_PLAN_KEYS = {
    "status",
    "expected_new_config_sha256",
    "baseline_config_sha256",
    "deployment_candidate_sha256",
    "candidate_patch_sha256",
    "source_freeze_sha256",
    "package_source_manifest_sha256",
    "database_baseline_sha256",
    "request_filename",
    "ledger_filename",
    "pin_binding_script_sha256",
}
SUCCESSOR_PLAN_STATUS = "APPROVED_AUDIT03_SUCCESSOR_PIN_BINDING_NOT_APPLIED"
_DIAGNOSTIC_STAGES = {
    "INITIAL_STOPPED_GATE", "BACKUP_ACL", "PENDING_ACL", "SECOND_ACL_COPY",
    "POST_SECOND_ACL_COPY_CAS", "BEFORE_REPLACE_CALLBACK", "FRESH_IDLE_CHECK",
    "PIN_WRITE_ATTEMPTED", "CORE_RETURNED", "FINAL_CONFIG_OBSERVATION",
    "FINAL_RECEIPT_SAVE", "DIAGNOSTIC_FAILURE",
}
_DIAGNOSTIC_REASONS = {
    "MANAGED_STATUS_TOOL_MISSING", "MANAGED_STATUS_FAILED", "MANAGED_STATUS_INVALID",
    "PROFILE_NOT_STOPPED", "PROFILE_STATUS_INCOMPLETE", "SHARED_PORTS_NOT_FREE",
    "PIN_ACL_COPY_OR_VERIFY_FAILED", "ACL_TARGET_OUTSIDE_ALLOWLIST", "ACL_COPY_TEMPLATE_CHANGED",
    "ACL_COMMAND_EXCEPTION", "PIN_ACL_METADATA_JSON_INVALID", "PIN_ACL_METADATA_MISMATCH",
    "PIN_BYTES_CHANGED_DURING_ACL_COPY", "PIN_BYTES_CHANGED_BEFORE_REPLACE",
    "ATOMIC_PIN_REPLACE_MISMATCH", "PIN_INPUT_BYTES_CHANGED", "PIN_VALUE_MISMATCH",
    "DUPLICATE_JSON_KEY", "CONFIG_NOT_OBJECT", "NON_PIN_CONFIG_CHANGED",
    "FINAL_CONFIG_SHA_MISMATCH", "FINAL_OBSERVATION_FAILED", "UNCLASSIFIED_EXCEPTION",
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha(path: Path) -> str:
    return sha(path.read_bytes())


def _core():
    if file_sha(OLD_CORE) != OLD_CORE_SHA:
        raise RuntimeError("PIN_CORE_CHANGED")
    return runpy.run_path(str(OLD_CORE), run_name="audit03_readonly_pin_core")


def _exception_chain_types(exc: BaseException) -> list[str]:
    try:
        if file_sha(DIAGNOSTIC_HELPERS) != DIAGNOSTIC_HELPERS_SHA:
            return ["OTHER"]
        helpers = runpy.run_path(str(DIAGNOSTIC_HELPERS), run_name="audit03_readonly_diagnostic_helpers")
        return helpers["_exception_chain_types"](exc)
    except Exception:
        return ["OTHER"]


def _fixed_reason(exc: BaseException) -> str:
    try:
        marker = str(exc)
    except Exception:
        marker = ""
    return marker if marker in _DIAGNOSTIC_REASONS else "UNCLASSIFIED_EXCEPTION"


def _mark_failed(receipt: dict, exc: BaseException, *, phase: str | None = None) -> None:
    receipt["status"] = "FAILED_STOP_NO_RETRY_NO_ROLLBACK"
    receipt["failed_phase"] = phase or receipt.get("phase", "single_config_replace")
    receipt["error_type"] = type(exc).__name__
    receipt["error_reason_code"] = _fixed_reason(exc)
    receipt["error_chain_types"] = _exception_chain_types(exc)


def _save_receipt(receipt: dict) -> None:
    RECEIPT.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _set_diagnostic_stage(receipt: dict, stage: str) -> None:
    receipt["diagnostic_stage"] = stage if stage in _DIAGNOSTIC_STAGES else "DIAGNOSTIC_FAILURE"


def _verify_candidate_sources() -> dict:
    if file_sha(FREEZE) != SOURCE_FREEZE_SHA or file_sha(PACKAGE_PARITY) != PACKAGE_PARITY_SHA:
        raise RuntimeError("SOURCE_EVIDENCE_CHANGED")
    parity = json.loads(PACKAGE_PARITY.read_text(encoding="utf-8"))
    if (parity.get("source_manifest_sha256") != PACKAGE_MANIFEST_SHA or
            parity.get("status") != "AUDIT03_BE10_PACKAGE_PARITY_PASS_NOT_ACTIVATED"):
        raise RuntimeError("PACKAGE_PARITY_MISMATCH")
    if file_sha(DATABASE_BASELINE) != DATABASE_BASELINE_SHA:
        raise RuntimeError("DATABASE_BASELINE_CHANGED")
    baseline = json.loads(DATABASE_BASELINE.read_text(encoding="utf-8"))
    if (baseline.get("data_config_sha256") != BASELINE_CONFIG_SHA or
            baseline.get("source_manifest_sha256") != OLD_PIN):
        raise RuntimeError("BASELINE_BINDING_MISMATCH")
    if file_sha(CANDIDATE) != DEPLOYMENT_CANDIDATE_SHA or file_sha(PATCH) != DEPLOYMENT_PATCH_SHA:
        raise RuntimeError("DEPLOYMENT_CANDIDATE_CHANGED")
    candidate = json.loads(CANDIDATE.read_text(encoding="utf-8"))
    if (candidate.get("status") != "CANDIDATE_ONLY_NOT_APPLIED" or
            candidate.get("source_freeze_sha256") != SOURCE_FREEZE_SHA or
            candidate.get("package_source_manifest_sha256") != PACKAGE_MANIFEST_SHA or
            candidate.get("patch_sha256") != DEPLOYMENT_PATCH_SHA):
        raise RuntimeError("DEPLOYMENT_CANDIDATE_BINDING_MISMATCH")
    expected_paths = {
        str(ROOT / "deploy/v1/run-local.ps1"): "6473d3d99c2960866457d99cd3a3b13a8d05b92dd7a5e80eb6c3208dbcaa8016",
        str(ROOT / "deploy/v1/startup-check.py"): "5198cd9f7815d91b47da3c0fb597510dde07e229750b85fd09008ff21dc60fd4",
    }
    if len(candidate.get("files", [])) != 2 or {row.get("path") for row in candidate["files"]} != set(expected_paths):
        raise RuntimeError("DEPLOYMENT_CANDIDATE_PATH_SET_MISMATCH")
    frozen = json.loads(FREEZE.read_text(encoding="utf-8"))
    frozen_hashes = {
        row["path"]: row["sha256"]
        for section in ("all_source_files", "owned_source_files", "files")
        for row in frozen[section]
    }
    for row in candidate["files"]:
        path = Path(row["path"])
        relative = row["repo_relative_path"]
        before = Path(row["before_copy"])
        after = Path(row["after_copy"])
        if str(path) not in expected_paths or relative != path.relative_to(ROOT).as_posix():
            raise RuntimeError("DEPLOYMENT_CANDIDATE_PATH_OUTSIDE_ALLOWLIST")
        expected_before = expected_paths[str(path)]
        if frozen_hashes.get(relative) != expected_before or file_sha(path) != expected_before:
            raise RuntimeError("DEPLOYMENT_SOURCE_BASELINE_DRIFT")
        if file_sha(before) != expected_before or file_sha(after) != row.get("candidate_sha256"):
            raise RuntimeError("DEPLOYMENT_CANDIDATE_COPY_DRIFT")
        if row.get("before_sha256") != expected_before or row.get("logical_binding") != "DATA_BACKEND_PACKAGE":
            raise RuntimeError("DEPLOYMENT_CANDIDATE_ENTRY_MISMATCH")
        changes = row.get("changes")
        if changes != [{
            "before_utf8": r"D:\Jupyter\Chromatography_agent\testing\runs\2026-10-27-data-import-audit\backend-runtime-package-data-window-08\data-center-invalid-window-20261026",
            "after_utf8": r"D:\Jupyter\Chromatography_agent\testing\runs\2026-10-28-data-import-audit03\backend-runtime-package-data-window-10\data-center-invalid-window-20261026",
            "count": 1,
        }]:
            raise RuntimeError("DEPLOYMENT_BINDING_DELTA_MISMATCH")
    return {
        "source_freeze_sha256": SOURCE_FREEZE_SHA,
        "deployment_candidate_sha256": DEPLOYMENT_CANDIDATE_SHA,
        "candidate_patch_sha256": DEPLOYMENT_PATCH_SHA,
        "package_source_manifest_sha256": PACKAGE_MANIFEST_SHA,
        "database_baseline_sha256": DATABASE_BASELINE_SHA,
    }


def _require_stopped() -> None:
    if not START_PLATFORM.is_file() or not PWSH.is_file():
        raise RuntimeError("MANAGED_STATUS_TOOL_MISSING")
    seen = set()
    for profile in ("data-20261026", "assistant-20261006"):
        result = subprocess.run(
            [str(PWSH), "-NoProfile", "-File", str(START_PLATFORM), "-Profile", profile,
             "-Action", "status", "-NoBrowser"],
            capture_output=True, text=True, timeout=45,
        )
        if result.returncode:
            raise RuntimeError("MANAGED_STATUS_FAILED")
        try:
            status = json.loads(result.stdout)
        except Exception as exc:
            raise RuntimeError("MANAGED_STATUS_INVALID") from exc
        rows = status.get("selected", []) + status.get("other", [])
        services = {row.get("service") for row in rows}
        if services != {"api", "worker", "frontend"} or any(row.get("status") != "STOPPED" for row in rows):
            raise RuntimeError("PROFILE_NOT_STOPPED")
        seen.update(services)
    port_script = "$ErrorActionPreference='Stop'; $p=@(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort -in @(9012,5174) }); [Console]::WriteLine($p.Count)"
    result = subprocess.run([str(PWSH), "-NoProfile", "-NonInteractive", "-Command", port_script],
                            capture_output=True, text=True, timeout=45)
    if result.returncode or result.stdout.strip() != "0":
        raise RuntimeError("SHARED_PORTS_NOT_FREE")
    if seen != {"api", "worker", "frontend"}:
        raise RuntimeError("PROFILE_STATUS_INCOMPLETE")


def _candidate_config(raw: bytes) -> bytes:
    core = _core()
    before = core["_strict_json_object"](raw)
    if before.get(KEY) != OLD_PIN:
        raise RuntimeError("FORMAL_PIN_MISMATCH")
    pattern = re.compile(rb'("CHROM_BACKEND_SOURCE_MANIFEST_SHA256"\s*:\s*")' + OLD_PIN.encode("ascii") + rb'(")')
    updated, count = pattern.subn(lambda match: match.group(1) + NEW_PIN.encode("ascii") + match.group(2), raw)
    if count != 1:
        raise RuntimeError("SOURCE_PIN_KEY_NOT_UNIQUE")
    after = core["_strict_json_object"](updated)
    if before.keys() != after.keys() or any(before[key] != after[key] for key in before if key != KEY):
        raise RuntimeError("NON_PIN_CONFIG_CHANGED")
    if after.get(KEY) != NEW_PIN:
        raise RuntimeError("CANDIDATE_PIN_MISMATCH")
    return updated


def preview() -> dict:
    evidence = _verify_candidate_sources()
    # Read-only byte preview does not require stopping the ordinary platform.
    original = CONFIG.read_bytes()
    if sha(original) != BASELINE_CONFIG_SHA:
        raise RuntimeError("FORMAL_CONFIG_BASELINE_CHANGED")
    candidate = _candidate_config(original)
    _core()["validate_pin_pair"](
        original, candidate,
        formal_sha256=BASELINE_CONFIG_SHA,
        pending_sha256=sha(candidate), old_pin=OLD_PIN, new_pin=NEW_PIN,
    )
    return {
        "status": "PREVIEW_ONLY_NOT_WRITTEN",
        "baseline_config_sha256": BASELINE_CONFIG_SHA,
        "expected_new_config_sha256": sha(candidate),
        "changed_key": KEY,
        "old_pin": OLD_PIN,
        "candidate_pin": NEW_PIN,
        **evidence,
    }


def _load_plan(plan_sha256: str) -> tuple[dict, str]:
    script_sha256 = file_sha(Path(__file__).resolve())
    actual_plan_sha = file_sha(PLAN)
    if actual_plan_sha != plan_sha256.lower():
        raise RuntimeError("PLAN_HASH_MISMATCH")
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    if type(plan) is not dict or set(plan) != EXPECTED_PLAN_KEYS:
        raise RuntimeError("PLAN_KEYS_MISMATCH")
    if (plan.get("status") != SUCCESSOR_PLAN_STATUS or
            plan.get("baseline_config_sha256") != BASELINE_CONFIG_SHA or
            plan.get("deployment_candidate_sha256") != DEPLOYMENT_CANDIDATE_SHA or
            plan.get("candidate_patch_sha256") != DEPLOYMENT_PATCH_SHA or
            plan.get("source_freeze_sha256") != SOURCE_FREEZE_SHA or
            plan.get("package_source_manifest_sha256") != PACKAGE_MANIFEST_SHA or
            plan.get("database_baseline_sha256") != DATABASE_BASELINE_SHA or
            plan.get("request_filename") != REQUEST_NAME or
            plan.get("ledger_filename") != LEDGER_NAME or
            plan.get("pin_binding_script_sha256") != script_sha256):
        raise RuntimeError("PLAN_BINDING_MISMATCH")
    return plan, actual_plan_sha


def _copy_acl(source_path: Path, target: Path, core: dict, *, trace: dict | None = None, call_role: str = "") -> dict:
    allowed_roles = {"BACKUP_INITIAL", "PENDING_INITIAL", "PENDING_CORE_SECOND"}
    allowed_stages = {
        "TARGET_GUARD", "SCRIPT_TEMPLATE", "COMMAND", "COMMAND_EXCEPTION",
        "COMMAND_NONZERO", "METADATA_JSON_PARSE", "METADATA_MISMATCH", "COMPLETE",
    }
    allowed_reasons = {
        "NONE", "ACL_TARGET_OUTSIDE_ALLOWLIST", "ACL_COPY_TEMPLATE_CHANGED",
        "ACL_COMMAND_EXCEPTION", "PIN_ACL_COPY_OR_VERIFY_FAILED",
        "PIN_ACL_METADATA_JSON_INVALID", "PIN_ACL_METADATA_MISMATCH",
    }

    def mark(stage: str, reason_code: str) -> None:
        if trace is not None:
            trace.clear()
            trace.update({
                "call_role": call_role if call_role in allowed_roles else "OTHER",
                "stage": stage if stage in allowed_stages else "OTHER",
                "reason_code": reason_code if reason_code in allowed_reasons else "UNCLASSIFIED_EXCEPTION",
            })

    mark("TARGET_GUARD", "NONE")
    if Path(source_path) != CONFIG or target not in (PENDING, BACKUP) or target.parent != CONFIG.parent:
        mark("TARGET_GUARD", "ACL_TARGET_OUTSIDE_ALLOWLIST")
        raise RuntimeError("ACL_TARGET_OUTSIDE_ALLOWLIST")
    if target.name not in ("runtime.json.source-pin-audit03-successor.pending", "runtime.json.audit03-successor-before.json"):
        mark("TARGET_GUARD", "ACL_TARGET_OUTSIDE_ALLOWLIST")
        raise RuntimeError("ACL_TARGET_OUTSIDE_ALLOWLIST")
    source = core["ACL_COPY_SCRIPT"]
    if source.count(".source-pin-04.pending") != 1:
        mark("SCRIPT_TEMPLATE", "ACL_COPY_TEMPLATE_CHANGED")
        raise RuntimeError("ACL_COPY_TEMPLATE_CHANGED")
    suffix = ".source-pin-audit03-successor.pending" if target == PENDING else ".audit03-successor-before.json"
    script = source.replace(".source-pin-04.pending", suffix, 1)
    env = os.environ.copy()
    env.update({"CHROM_PIN_FORMAL": str(CONFIG), "CHROM_PIN_PENDING": str(target)})
    mark("COMMAND", "NONE")
    try:
        result = subprocess.run([str(PWSH), "-NoProfile", "-NonInteractive", "-Command", script],
                                capture_output=True, text=True, timeout=45, env=env)
    except Exception:
        mark("COMMAND_EXCEPTION", "ACL_COMMAND_EXCEPTION")
        raise
    if result.returncode:
        mark("COMMAND_NONZERO", "PIN_ACL_COPY_OR_VERIFY_FAILED")
        raise RuntimeError("PIN_ACL_COPY_OR_VERIFY_FAILED")
    try:
        metadata = json.loads(result.stdout)
    except Exception:
        mark("METADATA_JSON_PARSE", "PIN_ACL_METADATA_JSON_INVALID")
        raise
    if not isinstance(metadata, dict):
        mark("METADATA_MISMATCH", "PIN_ACL_METADATA_MISMATCH")
        metadata.get("source_sddl")
    sddl = metadata.get("source_sddl")
    if (not isinstance(sddl, str) or sddl != metadata.get("target_sddl") or
            metadata.get("source_protected") is not True or
            metadata.get("target_protected") is not True or
            metadata.get("owner_sid") != metadata.get("source_owner_sid", metadata.get("owner_sid"))):
        mark("METADATA_MISMATCH", "PIN_ACL_METADATA_MISMATCH")
        raise RuntimeError("PIN_ACL_COPY_OR_VERIFY_FAILED")
    mark("COMPLETE", "NONE")
    return {"owner_match": True, "protected": True, "sddl_sha256": sha(sddl.encode("utf-8"))}


def _write_exclusive(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=False, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def apply(plan_sha256: str) -> int:
    if RECEIPT.exists():
        raise RuntimeError("RECEIPT_EXISTS_NO_REPLAY")
    plan, actual_plan_sha = _load_plan(plan_sha256)
    evidence = _verify_candidate_sources()
    _require_stopped()
    if any(os.path.lexists(path) for path in (PENDING, BACKUP)):
        raise RuntimeError("PIN_ARTIFACT_EXISTS_NO_REPLAY")
    original = CONFIG.read_bytes()
    if sha(original) != BASELINE_CONFIG_SHA:
        raise RuntimeError("FORMAL_CONFIG_BASELINE_CHANGED")
    candidate = _candidate_config(original)
    candidate_sha = sha(candidate)
    if plan["expected_new_config_sha256"] != candidate_sha:
        raise RuntimeError("EXPECTED_CONFIG_SHA_MISMATCH")
    core = _core()
    core["validate_pin_pair"](original, candidate, formal_sha256=BASELINE_CONFIG_SHA,
                              pending_sha256=candidate_sha, old_pin=OLD_PIN, new_pin=NEW_PIN)
    receipt = {
        "status": "STARTED_AUDIT03_SUCCESSOR_PIN_BINDING",
        "plan_sha256": actual_plan_sha,
        "script_sha256": file_sha(Path(__file__).resolve()),
        "candidate_sha256": DEPLOYMENT_CANDIDATE_SHA,
        "source_freeze_sha256": SOURCE_FREEZE_SHA,
        "source_manifest_sha256": PACKAGE_MANIFEST_SHA,
        "baseline_config_sha256": BASELINE_CONFIG_SHA,
        "expected_new_config_sha256": candidate_sha,
        "changed_key": KEY,
        "backup_creation_attempted": False,
        "backup_written": False,
        "pending_creation_attempted": False,
        "pending_written": False,
        "acl_copy_attempted": False,
        "acl_diagnostics": [],
        "second_acl_copy_attempted": False,
        "second_acl_copy_completed": False,
        "before_replace_callback_entered": False,
        "idle_gate_attempted": False,
        "idle_gate_succeeded": False,
        "pin_write_attempted": False,
        "core_completion_returned": False,
        "post_core_config_verification": "UNKNOWN",
        "configuration_write_observed": False,
        "final_config_verification": "UNKNOWN",
        "services_started": False,
        "database_accessed": False,
        "uac_or_scan": False,
        "retry": False,
        "rollback": False,
        "final_receipt_save_attempted": False,
        "diagnostic_stage": "INITIAL_STOPPED_GATE",
        **evidence,
    }
    with RECEIPT.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2)
        stream.write("\n")

    def save() -> None:
        _save_receipt(receipt)

    def copy_acl_traced(source, target, role: str) -> dict:
        trace = {"call_role": role, "stage": "STARTED", "reason_code": "NONE"}
        receipt["acl_diagnostics"].append(trace)
        return _copy_acl(Path(source), Path(target), core, trace=trace, call_role=role)

    phase = "exclusive_backup_and_pending"
    primary_error: BaseException | None = None
    try:
        receipt["backup_creation_attempted"] = True
        save()
        _write_exclusive(BACKUP, original)
        receipt["backup_written"] = sha(BACKUP.read_bytes()) == BASELINE_CONFIG_SHA
        if not receipt["backup_written"]:
            raise RuntimeError("BACKUP_HASH_MISMATCH")
        receipt["acl_copy_attempted"] = True
        _set_diagnostic_stage(receipt, "BACKUP_ACL")
        save()
        receipt["backup_acl"] = copy_acl_traced(CONFIG, BACKUP, "BACKUP_INITIAL")
        receipt["pending_creation_attempted"] = True
        save()
        _write_exclusive(PENDING, candidate)
        receipt["pending_written"] = sha(PENDING.read_bytes()) == candidate_sha
        if not receipt["pending_written"]:
            raise RuntimeError("PENDING_HASH_MISMATCH")
        _set_diagnostic_stage(receipt, "PENDING_ACL")
        receipt["pending_acl"] = copy_acl_traced(CONFIG, PENDING, "PENDING_INITIAL")
        receipt["phase"] = "single_config_replace"
        _set_diagnostic_stage(receipt, "SECOND_ACL_COPY")
        save()

        def second_acl_copy(source, target):
            receipt["second_acl_copy_attempted"] = True
            receipt["diagnostic_stage"] = "SECOND_ACL_COPY"
            receipt["phase"] = "single_config_replace"
            result = copy_acl_traced(Path(source), Path(target), "PENDING_CORE_SECOND")
            receipt["second_acl_copy_completed"] = True
            _set_diagnostic_stage(receipt, "POST_SECOND_ACL_COPY_CAS")
            return result

        def immediately_before_replace(_metadata) -> None:
            receipt["before_replace_callback_entered"] = True
            receipt["idle_gate_attempted"] = True
            receipt["phase"] = "before_replace_idle_gate"
            _set_diagnostic_stage(receipt, "FRESH_IDLE_CHECK")
            _require_stopped()
            receipt["idle_gate_succeeded"] = True
            receipt["pin_write_attempted"] = True
            _set_diagnostic_stage(receipt, "PIN_WRITE_ATTEMPTED")
            save()

        result = core["complete_pin_files"](
            CONFIG, PENDING,
            formal_sha256=BASELINE_CONFIG_SHA,
            pending_sha256=candidate_sha,
            old_pin=OLD_PIN,
            new_pin=NEW_PIN,
            copy_acl=second_acl_copy,
            before_replace=immediately_before_replace,
        )
        # The pinned core verifies candidate bytes after os.replace before returning.
        receipt["core_completion_returned"] = True
        receipt["configuration_write_observed"] = True
        _set_diagnostic_stage(receipt, "CORE_RETURNED")
        if sha(CONFIG.read_bytes()) != candidate_sha:
            receipt["post_core_config_verification"] = "FAILED"
            raise RuntimeError("FINAL_CONFIG_SHA_MISMATCH")
        receipt["post_core_config_verification"] = "PASSED"
        _set_diagnostic_stage(receipt, "FINAL_CONFIG_OBSERVATION")
        receipt["configuration_write_observed"] = True
        receipt["pin_result"] = {
            "result_sha256": result["result_sha256"],
            "changed_key": KEY,
            "new_pin": NEW_PIN,
        }
        receipt["status"] = "SUCCEEDED_AUDIT03_SUCCESSOR_PIN_BINDING_UPDATED"
        receipt["phase"] = "complete"
    except Exception as exc:
        primary_error = exc
        _mark_failed(receipt, exc, phase=receipt.get("phase", phase))
    finally:
        if receipt["pin_write_attempted"]:
            try:
                if primary_error is None:
                    _set_diagnostic_stage(receipt, "FINAL_CONFIG_OBSERVATION")
                observed = file_sha(CONFIG) == candidate_sha
                receipt["final_config_verification"] = "PASSED" if observed else "FAILED"
                if observed:
                    receipt["configuration_write_observed"] = True
                elif receipt["configuration_write_observed"] is not True:
                    receipt["configuration_write_observed"] = False
                if primary_error is None and not observed:
                    primary_error = RuntimeError("FINAL_CONFIG_SHA_MISMATCH")
                    _mark_failed(receipt, primary_error, phase="final_config_observation")
            except Exception as exc:
                receipt["final_config_verification"] = "UNKNOWN"
                if receipt["configuration_write_observed"] is not True:
                    receipt["configuration_write_observed"] = None
                receipt["final_observation_reason_code"] = "FINAL_OBSERVATION_FAILED"
                if primary_error is None:
                    primary_error = exc
                    _mark_failed(receipt, primary_error, phase="final_config_observation")
                    receipt["final_observation_error_types"] = _exception_chain_types(exc)
                else:
                    receipt["final_observation_error_types"] = _exception_chain_types(exc)
        receipt["final_receipt_save_attempted"] = True
        try:
            save()
        except Exception as exc:
            if primary_error is None:
                # Preserve the save exception and its type through main/CLI handling.
                raise
            receipt["final_receipt_save_error_types"] = _exception_chain_types(exc)
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0 if receipt["status"] == "SUCCEEDED_AUDIT03_SUCCESSOR_PIN_BINDING_UPDATED" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument("--preview", action="store_true")
    operation.add_argument("--apply", action="store_true")
    parser.add_argument("--plan-sha256")
    args = parser.parse_args(argv)
    if args.preview:
        if args.plan_sha256 is not None:
            parser.error("--plan-sha256 applies only with --apply")
        print(json.dumps(preview(), ensure_ascii=False, sort_keys=True))
        return 0
    if not args.plan_sha256 or not re.fullmatch(r"[0-9a-fA-F]{64}", args.plan_sha256):
        parser.error("--apply requires --plan-sha256 with 64 hex digits")
    return apply(args.plan_sha256)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"status": "STOPPED", "error_type": type(exc).__name__, "retry": False}))
        raise SystemExit(1)
