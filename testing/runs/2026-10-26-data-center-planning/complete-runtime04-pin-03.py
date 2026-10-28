"""Complete the fixed runtime04 source-pin handoff; never starts services."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import runpy
import subprocess
import sys

ROOT = Path(r"D:\Jupyter\Chromatography_agent")
RUN = ROOT / "testing/runs/2026-10-26-data-center-planning"
RUNTIME = Path(r"C:\Users\13459\AppData\Local\ChromatographyPlatform\data-accept-20261026")
CONFIG = RUNTIME / "runtime.json"
PENDING = RUNTIME / "runtime.json.source-pin-04.pending"
OUT = RUN / "runtime04-source-pin-completion-03.json"
KEY = "CHROM_BACKEND_SOURCE_MANIFEST_SHA256"
OLD_PIN = "02107f901b2574fe1ce51a335f908f17937727b55c3cd4d4beff40ada4746396"
NEW_PIN = "f1205b7d73214ac8169db9bf37d0d6af762a52bfc98bc4bb5cc7bb8b95e98cda"
CONFIG_SHA256 = "1f6f254e33476bbd471814ec2c10762f914627b269b189ef7d26d0e6fb63ae30"
PENDING_SHA256 = "f982a626f21bb4fcb8f4324f1f5f6c851deaeee1190e5ff25cd46891857e53dc"
FREEZE_SHA256 = "ee757632b51aaca58a60da07874c9cdbccad8264cf31f29d63fc9b10d10c9f14"
CANDIDATE_SHA256 = "2b07213023575c7c0566cb814443f8f6a7c50f24a191d7a0fa1b6175f56bb582"
PATCH_SHA256 = "72529640bbf34dee91ecef28a75bcbb6411fd280fe61e0cb42e47dff36a3c583"
PYTHON = r"D:\Anaconda\envs\SFC\python.exe"
PWSH = r"C:\Users\13459\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\powershell\pwsh.exe"

ACL_COPY_SCRIPT = r"""
$ErrorActionPreference='Stop'
$source=$env:CHROM_PIN_FORMAL
$target=$env:CHROM_PIN_PENDING
if ([IO.Path]::GetFileName($source) -cne 'runtime.json' -or
    $target -cne ($source + '.source-pin-04.pending') -or
    [IO.Path]::GetDirectoryName($source) -cne [IO.Path]::GetDirectoryName($target)) {
    throw 'UNEXPECTED_PIN_PATH'
}
. 'D:\Jupyter\Chromatography_agent\deploy\v1\dpapi-secret-source.ps1'
Assert-DpapiProtectedAcl $source $false 'Formal data configuration'
Assert-DpapiNoReparse $target 'Pending data configuration' | Out-Null
$sid=Get-DpapiCurrentUserSid
$targetBefore=Get-Acl -LiteralPath $target
if ($targetBefore.GetOwner([Security.Principal.SecurityIdentifier]).Value -cne $sid) {
    throw 'PENDING_OWNER_MISMATCH'
}
$before=Get-Acl -LiteralPath $source
Set-Acl -LiteralPath $target -AclObject $before
Assert-DpapiProtectedAcl $source $false 'Formal data configuration'
Assert-DpapiProtectedAcl $target $false 'Pending data configuration'
$after=Get-Acl -LiteralPath $target
$sid=Get-DpapiCurrentUserSid
$ownerSource=$before.GetOwner([Security.Principal.SecurityIdentifier]).Value
$ownerTarget=$after.GetOwner([Security.Principal.SecurityIdentifier]).Value
if ($ownerSource -cne $sid -or $ownerTarget -cne $ownerSource -or
    !$before.AreAccessRulesProtected -or !$after.AreAccessRulesProtected -or
    $before.Sddl -cne $after.Sddl) { throw 'PIN_ACL_MISMATCH' }
@{owner_sid=$ownerTarget;source_sddl=$before.Sddl;target_sddl=$after.Sddl;
  source_protected=$before.AreAccessRulesProtected;target_protected=$after.AreAccessRulesProtected} |
    ConvertTo-Json -Compress
"""


def _strict_json_object(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=pairs)
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise ValueError("CONFIG_NOT_OBJECT")
    return value


def validate_pin_pair(formal, pending, *, formal_sha256, pending_sha256,
                      old_pin=OLD_PIN, new_pin=NEW_PIN):
    """Validate the two existing byte snapshots without rewriting either one."""
    formal_hash = hashlib.sha256(formal).hexdigest()
    pending_hash = hashlib.sha256(pending).hexdigest()
    if formal_hash != formal_sha256 or pending_hash != pending_sha256:
        raise ValueError("PIN_INPUT_BYTES_CHANGED")
    before = _strict_json_object(formal)
    after = _strict_json_object(pending)
    if before.get(KEY) != old_pin or after.get(KEY) != new_pin:
        raise ValueError("PIN_VALUE_MISMATCH")
    if before.keys() != after.keys() or any(
        before[key] != after[key] for key in before if key != KEY
    ):
        raise ValueError("NON_PIN_CONFIG_CHANGED")
    return {"formal_sha256": formal_hash, "pending_sha256": pending_hash,
            "changed_key": KEY, "old_pin": old_pin, "new_pin": new_pin}


def complete_pin_files(config, pending, *, formal_sha256, pending_sha256,
                       old_pin=OLD_PIN, new_pin=NEW_PIN, copy_acl, before_replace=None):
    """Copy/verify ACL, CAS both bytes again, then atomically replace once."""
    first_formal = Path(config).read_bytes()
    first_pending = Path(pending).read_bytes()
    receipt = validate_pin_pair(first_formal, first_pending,
                                formal_sha256=formal_sha256,
                                pending_sha256=pending_sha256,
                                old_pin=old_pin, new_pin=new_pin)
    acl_verification = copy_acl(Path(config), Path(pending))
    if Path(config).read_bytes() != first_formal or Path(pending).read_bytes() != first_pending:
        raise RuntimeError("PIN_BYTES_CHANGED_DURING_ACL_COPY")
    validate_pin_pair(Path(config).read_bytes(), Path(pending).read_bytes(),
                      formal_sha256=formal_sha256, pending_sha256=pending_sha256,
                      old_pin=old_pin, new_pin=new_pin)
    if acl_verification is not None:
        receipt["acl_verification"] = acl_verification
    if before_replace is not None:
        before_replace(receipt)
    final_formal = Path(config).read_bytes()
    final_pending = Path(pending).read_bytes()
    if final_formal != first_formal or final_pending != first_pending:
        raise RuntimeError("PIN_BYTES_CHANGED_BEFORE_REPLACE")
    validate_pin_pair(final_formal, final_pending,
                      formal_sha256=formal_sha256, pending_sha256=pending_sha256,
                      old_pin=old_pin, new_pin=new_pin)
    os.replace(pending, config)
    final = Path(config).read_bytes()
    if final != first_pending:
        raise RuntimeError("ATOMIC_PIN_REPLACE_MISMATCH")
    receipt["result_sha256"] = hashlib.sha256(final).hexdigest()
    return receipt


def acl_copy_payload_environment(source, target):
    source, target = Path(source).absolute(), Path(target).absolute()
    if (source.name != "runtime.json" or
            target.name != source.name + ".source-pin-04.pending" or
            source.parent != target.parent):
        raise RuntimeError("UNEXPECTED_PIN_PATH")
    return {"CHROM_PIN_FORMAL": str(source), "CHROM_PIN_PENDING": str(target)}


def _acl_copy(source, target):
    if Path(source) != CONFIG or Path(target) != PENDING:
        raise RuntimeError("UNEXPECTED_PIN_PATH")
    env = os.environ.copy()
    env.update(acl_copy_payload_environment(source, target))
    result = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", ACL_COPY_SCRIPT],
                            capture_output=True, text=True, timeout=45, env=env)
    if result.returncode:
        raise RuntimeError("PIN_ACL_COPY_OR_VERIFY_FAILED")
    metadata = json.loads(result.stdout)
    if (metadata.get("source_sddl") != metadata.get("target_sddl") or
            metadata.get("source_protected") is not True or
            metadata.get("target_protected") is not True or
            not metadata.get("owner_sid")):
        raise RuntimeError("PIN_ACL_COPY_OR_VERIFY_FAILED")
    return {
        "owner_sid": metadata["owner_sid"],
        "protected": True,
        "sddl_sha256": hashlib.sha256(metadata["source_sddl"].encode("utf-8")).hexdigest(),
    }


def _verify_source_freeze():
    freeze_path = RUN / "data-window-source-freeze-10.json"
    if hashlib.sha256(freeze_path.read_bytes()).hexdigest() != FREEZE_SHA256:
        raise RuntimeError("SOURCE_FREEZE_CHANGED")
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    candidate_path = RUN / "runtime04-deployment-path-candidate.json"
    if hashlib.sha256(candidate_path.read_bytes()).hexdigest() != CANDIDATE_SHA256:
        raise RuntimeError("PATH_CANDIDATE_CHANGED")
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    if (candidate.get("patch_sha256") != PATCH_SHA256 or
            hashlib.sha256((RUN / "runtime04-deployment-path-candidate.patch").read_bytes()).hexdigest() != PATCH_SHA256):
        raise RuntimeError("PATH_CANDIDATE_PATCH_CHANGED")
    path_items = candidate["files"]
    overrides = {item["path"]: item["candidate_sha256"] for item in path_items}
    expected_paths = {
        "deploy/v1/run-local.ps1", "deploy/v1/startup-check.py",
        "deploy/v1/test_data_accept_profile.ps1", "backend/tests/test_startup_check_profile.py",
    }
    if len(path_items) != 4 or set(overrides) != expected_paths:
        raise RuntimeError("PATH_CANDIDATE_SET_CHANGED")
    for group in ("all_source_files", "owned_source_files", "files"):
        records = freeze[group]
        if not isinstance(records, list):
            raise RuntimeError("FREEZE_SECTION_INVALID")
        for item in records:
            path = item["path"]
            expected = overrides.get(path, item["sha256"])
            if hashlib.sha256((ROOT / path).read_bytes()).hexdigest() != expected:
                raise RuntimeError("SOURCE_FREEZE_DRIFT")
    for path, expected in overrides.items():
        if hashlib.sha256((ROOT / path).read_bytes()).hexdigest() != expected:
            raise RuntimeError("PATH_CANDIDATE_DRIFT")
    return {"freeze_sha256": FREEZE_SHA256, "candidate_paths": sorted(overrides)}


def _run_preflight(op, checks):
    if op["CONFIG"] != CONFIG or op["KEY"] != KEY:
        raise RuntimeError("RUNTIME_BINDING_MISMATCH")
    checks["source_freeze"] = _verify_source_freeze()
    legacy_path = RUN / "activate-runtime04-path-pin-02.py"
    if hashlib.sha256(legacy_path.read_bytes()).hexdigest() != "44799e545d99342e5bbfe3030cc936d22ecaa8ed5b7546a418bdee518e76fe14":
        raise RuntimeError("READONLY_HELPER_CHANGED")
    legacy = runpy.run_path(str(legacy_path),
                            run_name="readonly_runtime04_helpers")
    legacy["checked_package"]("03", legacy["OLD_PIN"])
    legacy["checked_package"]("04", NEW_PIN)
    checks["packages"] = {"runtime03": legacy["OLD_PIN"], "runtime04": NEW_PIN}
    metadata = json.loads(op["powershell"](op["ACL_SCRIPT"]))
    if metadata.get("normal_token") is not True or metadata.get("shared_ports_free") is not True:
        raise RuntimeError("RUNTIME_ACL_OR_PORT_GUARD_FAILED")
    if metadata.get("protected_acl") is not True or metadata.get("owner_is_current_user") is not True:
        raise RuntimeError("RUNTIME_ACL_OWNER_GUARD_FAILED")
    checks["formal_acl_owner_protected"] = True
    prior = CONFIG.read_bytes()
    if hashlib.sha256(prior).hexdigest() != CONFIG_SHA256:
        raise RuntimeError("FORMAL_CONFIG_CHANGED")
    if _strict_json_object(prior).get(KEY) != OLD_PIN:
        raise RuntimeError("FORMAL_PIN_CHANGED")
    pending = PENDING.read_bytes()
    checks["pin_pair"] = validate_pin_pair(prior, pending,
                                           formal_sha256=CONFIG_SHA256,
                                           pending_sha256=PENDING_SHA256)
    csv = RUN / "inputs/tlc_dataset_old_header_named.csv"
    if csv.stat().st_size != 463170 or hashlib.sha256(csv.read_bytes()).hexdigest() != "9c14648c40a834bb8403f4de49eed8a4f6070d3069eec8dfc1c985d5832c3068":
        raise RuntimeError("FIXED_CSV_CHANGED")
    if any(os.path.lexists(RUNTIME / "state" / name) for name in (
        "dataset-csv-scan-v1.used", "dataset-csv-scan-v1.request.json")):
        raise RuntimeError("SCAN_LEDGER_ALREADY_USED")
    checks["usage_counts"] = legacy["usage_zero"]()
    checks["idle"] = {}
    for profile in ("data-20261026", "assistant-20261006"):
        idle = legacy["checked_json"]([PYTHON, "-B", str(ROOT / "deploy/v1/startup-check.py"),
                                       "--profile", profile, "--idle"])
        if idle.get("profile") != profile or idle.get("idle") is not True or any(idle["pending_counts"].values()):
            raise RuntimeError("PROFILE_NOT_IDLE")
        checks["idle"][profile] = idle
    checks["managed_status"] = legacy["stopped_status"](op)
    if CONFIG.read_bytes() != prior or PENDING.read_bytes() != pending:
        raise RuntimeError("PIN_BYTES_CHANGED_DURING_PREFLIGHT")
    return metadata


def execute():
    if OUT.exists():
        raise RuntimeError("Receipt exists; no replay")
    receipt = {"status": "STARTED", "started_at_utc": datetime.now(timezone.utc).isoformat(),
               "phase": "readonly_preflight", "pin_write_attempted": False,
               "services_started": False, "termination_performed": False,
               "uac_or_scan": False, "automatic_retry": False, "rollback": False}
    with OUT.open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, indent=2)

    def save():
        OUT.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    phase = "readonly_preflight"
    try:
        acl_helper = RUN / "activate-runtime03-source-pin.py"
        if hashlib.sha256(acl_helper.read_bytes()).hexdigest() != "c5b26d069d9cb904ec140c4028e68ea03cc948c97d0d74454a632729300f4416":
            raise RuntimeError("ACL_HELPER_CHANGED")
        op = runpy.run_path(str(acl_helper),
                            run_name="readonly_runtime_acl_helpers")
        original_acl = _run_preflight(op, receipt)
        save()
        phase = "pending_acl_copy_and_pin_cas"
        receipt["phase"] = phase
        save()
        def mark_pin_attempt(_result):
            receipt["pin_write_attempted"] = True
            save()

        result = complete_pin_files(CONFIG, PENDING, formal_sha256=CONFIG_SHA256,
                                    pending_sha256=PENDING_SHA256, copy_acl=_acl_copy,
                                    before_replace=mark_pin_attempt)
        receipt["pin_result"] = result
        save()
        final_metadata = json.loads(op["powershell"](op["ACL_SCRIPT"]))
        if (final_metadata.get("normal_token") is not True or
                final_metadata.get("protected_acl") is not True or
                final_metadata.get("owner_sid") != original_acl.get("owner_sid") or
                final_metadata.get("configuration_sddl") != original_acl.get("configuration_sddl")):
            raise RuntimeError("FINAL_CONFIG_ACL_GUARD_FAILED")
        final = CONFIG.read_bytes()
        if hashlib.sha256(final).hexdigest() != PENDING_SHA256 or _strict_json_object(final).get(KEY) != NEW_PIN:
            raise RuntimeError("FINAL_CONFIG_PIN_MISMATCH")
        receipt.update(status="SUCCEEDED_SINGLE_PIN_04", phase="complete",
                       final_config_sha256=hashlib.sha256(final).hexdigest(),
                       services_started=False, rollback=False)
    except Exception as error:
        receipt.update(status="FAILED_STOP_NO_RETRY_NO_ROLLBACK",
                       failed_phase=phase, error_type=type(error).__name__)
    finally:
        receipt["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        save()
    print(json.dumps(receipt))
    return 0 if receipt["status"] == "SUCCEEDED_SINGLE_PIN_04" else 1


if __name__ == "__main__":
    if sys.argv[1:] != ["--complete-approved-runtime04-pin"]:
        raise SystemExit("No action: require --complete-approved-runtime04-pin")
    raise SystemExit(execute())
