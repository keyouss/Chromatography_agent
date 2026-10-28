from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import runpy

import pytest


ROOT = Path(r"D:\Jupyter\Chromatography_agent")
HELPER = ROOT / "testing/runs/2026-10-28-data-import-audit03/audit03-successor-config-restore.py"
CORE = ROOT / "testing/runs/2026-10-26-data-center-planning/complete-runtime04-pin-03.py"
spec = importlib.util.spec_from_file_location("audit03_restore_test_module", HELPER)
restore = importlib.util.module_from_spec(spec)
spec.loader.exec_module(restore)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _pins(monkeypatch):
    baseline_pin = "1" * 64
    candidate_pin = "2" * 64
    baseline = json.dumps({restore.KEY: baseline_pin, "kept": "same"}, sort_keys=True).encode()
    candidate = json.dumps({restore.KEY: candidate_pin, "kept": "same"}, sort_keys=True).encode()
    monkeypatch.setattr(restore, "BASELINE_SHA256", _sha(baseline))
    monkeypatch.setattr(restore, "CANDIDATE_SHA256", _sha(candidate))
    monkeypatch.setattr(restore, "BASELINE_PIN", baseline_pin)
    monkeypatch.setattr(restore, "CANDIDATE_PIN", candidate_pin)
    return baseline, candidate


def _identity():
    return {
        "volume_serial_decimal_str": "10", "file_id_hex": "aa",
        "mtime_ns_decimal_str": "20", "owner_sid_sha256": "a" * 64,
        "sddl_sha256": "b" * 64, "dacl_protected": True,
        "reparse_point": False, "size_bytes_decimal_str": "30",
    }


def _configure_restore(monkeypatch, tmp_path, *, mode="RESTORE_CANDIDATE_TO_BASELINE"):
    baseline, candidate = _pins(monkeypatch)
    config = tmp_path / "runtime.json"
    backup = tmp_path / "runtime.json.audit03-successor-before.json"
    pending = tmp_path / "runtime.json.source-pin-audit03-successor-restore.pending"
    receipt = tmp_path / "receipt.json"
    current = baseline if mode == "SKIP_ALREADY_BASELINE" else candidate
    config.write_bytes(current)
    if mode == "RESTORE_CANDIDATE_TO_BASELINE":
        backup.write_bytes(baseline)
    monkeypatch.setattr(restore, "CONFIG", config)
    monkeypatch.setattr(restore, "BACKUP", backup)
    monkeypatch.setattr(restore, "PENDING", pending)
    monkeypatch.setattr(restore, "RECEIPT", receipt)
    ident = _identity()
    binding = {"binding_attempt": {"configuration_write_observed": None, "core_completion_returned": None}}
    monkeypatch.setattr(restore, "_b1", lambda *a, **k: (binding, "c" * 64))
    monkeypatch.setattr(restore, "_validate_b1", lambda _: (
        mode, current, ident, baseline if mode == "RESTORE_CANDIDATE_TO_BASELINE" else None,
        ident if mode == "RESTORE_CANDIDATE_TO_BASELINE" else None))
    monkeypatch.setattr(restore, "_identity", lambda _: ident)
    monkeypatch.setattr(restore, "_fresh_boot_gate", lambda _: True)
    monkeypatch.setattr(restore, "_fresh_operational_gates", lambda _: None)
    monkeypatch.setattr(restore, "_fresh_pre_replace_gate", lambda *args: None)
    monkeypatch.setattr(restore, "_capture_acl", lambda *args: {"protected": True})
    return baseline, candidate, config, pending, receipt


def test_reverse_cas_uses_frozen_core_with_only_tmp_fake_replace(monkeypatch, tmp_path):
    baseline, candidate, config, pending, receipt = _configure_restore(monkeypatch, tmp_path)
    replaced = []

    def fake_replace(source, target):
        replaced.append((Path(source), Path(target)))
        Path(target).write_bytes(Path(source).read_bytes())
        Path(source).unlink()

    real_loader = restore._load_core
    monkeypatch.setattr(restore, "_load_core", lambda: real_loader(fake_replace))
    result = restore.restore_config(Path("binding.json"), "c" * 64)
    assert result["status"] == "RESTORED_VERIFIED"
    assert result["write_attempted"] is True
    assert result["configuration_write_observed"] is True
    assert result["final_config_verification"] == "PASSED"
    assert config.read_bytes() == baseline
    assert not pending.exists()
    assert len(replaced) == 1
    assert json.loads(receipt.read_text(encoding="utf-8"))["window_id"] == restore.WINDOW_ID
    with pytest.raises(RuntimeError, match="RECEIPT_EXISTS_NO_REPLAY"):
        restore.restore_config(Path("binding.json"), "c" * 64)


def test_already_baseline_is_verified_no_change_and_does_not_infer_prior_binding(monkeypatch, tmp_path):
    baseline, _, config, pending, receipt = _configure_restore(monkeypatch, tmp_path, mode="SKIP_ALREADY_BASELINE")
    calls = []
    monkeypatch.setattr(restore, "_load_core", lambda *args: calls.append("core"))
    result = restore.restore_config(Path("binding.json"), "c" * 64)
    assert result["status"] == "NO_CHANGE_VERIFIED"
    assert result["write_attempted"] is False
    assert result["configuration_write_observed"] is None
    assert result["binding_configuration_write_observed"] is None
    assert result["final_config_verification"] == "PASSED"
    assert config.read_bytes() == baseline and not pending.exists()
    assert not calls
    assert json.loads(receipt.read_text(encoding="utf-8"))["status"] == "NO_CHANGE_VERIFIED"


def test_no_change_fresh_stop_idle_resource_or_ledger_gate_fails_before_receipt(monkeypatch, tmp_path):
    _configure_restore(monkeypatch, tmp_path, mode="SKIP_ALREADY_BASELINE")
    monkeypatch.setattr(restore, "_fresh_operational_gates", lambda _: (_ for _ in ()).throw(RuntimeError("FRESH_GATE")))
    with pytest.raises(RuntimeError, match="FRESH_GATE"):
        restore.restore_config(Path("binding.json"), "c" * 64)
    assert not restore.RECEIPT.exists()


def test_third_config_hash_fails_before_receipt_or_write(monkeypatch, tmp_path):
    _, _, config, pending, receipt = _configure_restore(monkeypatch, tmp_path)
    restore.CONFIG.write_bytes(b"third-state")
    ident = _identity()
    monkeypatch.setattr(restore, "_validate_b1", lambda _: (
        "RESTORE_CANDIDATE_TO_BASELINE", config.read_bytes(), ident,
        None, None))
    with pytest.raises(RuntimeError, match="CURRENT_CONFIG_IDENTITY_OR_HASH_MISMATCH"):
        restore.restore_config(Path("binding.json"), "c" * 64)
    assert not pending.exists()
    assert not receipt.exists()


def test_identity_mismatch_fails_before_pending_or_receipt(monkeypatch, tmp_path):
    _, _, config, pending, receipt = _configure_restore(monkeypatch, tmp_path)
    monkeypatch.setattr(restore, "_identity", lambda _: {**_identity(), "file_id_hex": "ff"})
    with pytest.raises(RuntimeError, match="CURRENT_CONFIG_IDENTITY_OR_HASH_MISMATCH"):
        restore.restore_config(Path("binding.json"), "c" * 64)
    assert not pending.exists() and not receipt.exists()


def test_acl_failure_is_one_shot_and_never_reaches_replace(monkeypatch, tmp_path):
    _, _, config, pending, receipt = _configure_restore(monkeypatch, tmp_path)
    replace_calls = []
    real_loader = restore._load_core
    monkeypatch.setattr(restore, "_load_core", lambda: real_loader(lambda *_: replace_calls.append("replace")))
    monkeypatch.setattr(restore, "_capture_acl", lambda *_: (_ for _ in ()).throw(RuntimeError("ACL_FAKE_FAILURE")))
    with pytest.raises(RuntimeError, match="ACL_FAKE_FAILURE"):
        restore.restore_config(Path("binding.json"), "c" * 64)
    record = json.loads(receipt.read_text(encoding="utf-8"))
    assert record["status"] == "FAILED"
    assert record["write_attempted"] is False
    assert pending.exists() and not replace_calls
    with pytest.raises(RuntimeError, match="RECEIPT_EXISTS_NO_REPLAY"):
        restore.restore_config(Path("binding.json"), "c" * 64)


def test_core_cas_rechecks_drift_immediately_before_replace(monkeypatch, tmp_path):
    _, _, config, pending, receipt = _configure_restore(monkeypatch, tmp_path)
    replace_calls = []
    real_loader = restore._load_core
    monkeypatch.setattr(restore, "_load_core", lambda: real_loader(lambda *_: replace_calls.append("replace")))
    calls = 0

    def drift_on_core_callback(*_):
        nonlocal calls
        calls += 1
        if calls == 2:
            pending.write_bytes(b"concurrent-change")

    monkeypatch.setattr(restore, "_fresh_pre_replace_gate", drift_on_core_callback)
    with pytest.raises(RuntimeError, match="PIN_BYTES_CHANGED_BEFORE_REPLACE"):
        restore.restore_config(Path("binding.json"), "c" * 64)
    assert not replace_calls
    assert config.read_bytes() != pending.read_bytes()
    assert json.loads(receipt.read_text(encoding="utf-8"))["write_attempted"] is True


def test_bad_backup_or_authorization_binding_fails_before_temp_artifacts(monkeypatch, tmp_path):
    _configure_restore(monkeypatch, tmp_path)
    monkeypatch.setattr(restore, "_validate_b1", lambda _: (_ for _ in ()).throw(ValueError("BACKUP_ACTUAL_MISMATCH")))
    with pytest.raises(ValueError, match="BACKUP_ACTUAL_MISMATCH"):
        restore.restore_config(Path("binding.json"), "c" * 64)
    assert not restore.PENDING.exists() and not restore.RECEIPT.exists()


def test_core_return_preserves_write_true_when_postread_fails(monkeypatch, tmp_path):
    baseline, _, config, _, receipt = _configure_restore(monkeypatch, tmp_path)
    replaced = []

    def fake_replace(source, target):
        replaced.append(True)
        Path(target).write_bytes(Path(source).read_bytes())
        Path(source).unlink()

    real_loader = restore._load_core
    monkeypatch.setattr(restore, "_load_core", lambda: real_loader(fake_replace))
    real_hash = restore._file_sha

    def fail_final_config_read(path):
        if Path(path) == config and replaced:
            raise OSError("SECRET_PATH_OR_ERROR")
        return real_hash(path)

    monkeypatch.setattr(restore, "_file_sha", fail_final_config_read)
    with pytest.raises(OSError):
        restore.restore_config(Path("binding.json"), "c" * 64)
    saved = json.loads(receipt.read_text(encoding="utf-8"))
    assert saved["core_completion_returned"] is True
    assert saved["configuration_write_observed"] is True
    assert saved["final_config_verification"] == "UNKNOWN"
    assert saved["status"] == "FAILED"
    assert config.read_bytes() == baseline
    assert "SECRET_PATH_OR_ERROR" not in receipt.read_text(encoding="utf-8")


def test_final_save_error_does_not_mask_primary_cas_error(monkeypatch, tmp_path):
    _configure_restore(monkeypatch, tmp_path)
    real_write_text = Path.write_text
    calls = 0

    def fail_final_save(path, data, *args, **kwargs):
        nonlocal calls
        if Path(path) == restore.RECEIPT:
            calls += 1
            if calls >= 2:
                raise OSError("SECRET_SAVE_ERROR")
        return real_write_text(path, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_final_save)
    gate_calls = 0

    def primary_gate(*_):
        nonlocal gate_calls
        gate_calls += 1
        if gate_calls == 2:
            raise RuntimeError("CAS_PRIMARY")

    monkeypatch.setattr(restore, "_fresh_pre_replace_gate", primary_gate)
    with pytest.raises(RuntimeError, match="CAS_PRIMARY"):
        restore.restore_config(Path("binding.json"), "c" * 64)


def test_final_save_error_after_core_return_keeps_true_write_fact_in_exception_snapshot(monkeypatch, tmp_path):
    _configure_restore(monkeypatch, tmp_path)
    real_loader = restore._load_core

    def fake_replace(source, target):
        Path(target).write_bytes(Path(source).read_bytes())
        Path(source).unlink()

    monkeypatch.setattr(restore, "_load_core", lambda: real_loader(fake_replace))
    real_write_text = Path.write_text
    calls = 0

    def fail_only_final_receipt_save(path, data, *args, **kwargs):
        nonlocal calls
        if Path(path) == restore.RECEIPT:
            calls += 1
            if calls == 3:
                raise OSError("SECONDARY_SAVE_FAILURE")
        return real_write_text(path, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_only_final_receipt_save)
    with pytest.raises(OSError) as captured:
        restore.restore_config(Path("binding.json"), "c" * 64)
    snapshot = getattr(captured.value, "_audit03_restore_receipt_snapshot")
    assert snapshot["configuration_write_observed"] is True
    assert snapshot["core_completion_returned"] is True
    assert snapshot["final_config_verification"] == "PASSED"


def test_cli_no_replay_leaves_existing_receipt_bytes_unchanged(monkeypatch, tmp_path, capsys):
    receipt = tmp_path / "receipt.json"
    raw = b'{"status":"NO_CHANGE_VERIFIED","configuration_write_observed":null}\n'
    receipt.write_bytes(raw)
    monkeypatch.setattr(restore, "RECEIPT", receipt)
    monkeypatch.setattr(restore, "B1", tmp_path / "must-not-read.json")
    assert restore.main(["--restore-config", "--binding-sha256", "c" * 64]) == 1
    assert receipt.read_bytes() == raw
    result = json.loads(capsys.readouterr().out)
    assert result["reason_code"] == "RECEIPT_EXISTS_NO_REPLAY"


def test_passive_binding_is_rejected_before_runtime_access(monkeypatch, tmp_path):
    path = tmp_path / "binding.json"
    path.write_text(json.dumps({"schema_version": "audit03-successor-recovery-binding/v1",
                                "window_id": restore.WINDOW_ID,
                                "status": "TEMPLATE_NOT_ACTIVE"}), encoding="utf-8")
    monkeypatch.setattr(restore, "CONFIG", tmp_path / "must-not-read")
    with pytest.raises(ValueError, match="TEMPLATE_NOT_ACTIVE"):
        restore._b1(path, _sha(path.read_bytes()), active=True)
    assert not restore.CONFIG.exists()


def _b2_file(tmp_path, monkeypatch):
    path = tmp_path / "start-binding.json"
    obj = {"schema_version": "audit03-successor-original-start-binding/v1",
           "window_id": restore.WINDOW_ID, "status": "ACTIVE_ORIGINAL_START",
           "preserved_unknown_exception": {
               "profile": "data-20261026",
               "ledger_sha256": "41bb2e5a6432096c2cb786d4ac440814d81fdc3fdc6f7c6a2509a8679d48d4e0",
               "request_sha256": "ec3e44ff8b52266c80c48c44d4bbf625473a364b5cd60f8706a319c2f20f4f06",
               "unknown_key": "dataset_scan_completion_unknown", "unknown_count": 1,
               "all_other_pending_counts_zero": True, "active_leases_zero": True,
               "status": "UNKNOWN_PRESERVED"}}
    raw = json.dumps(obj, sort_keys=True).encode()
    path.write_bytes(raw)
    monkeypatch.setattr(restore, "_validate_b2_chain", lambda _: ({}, "x", {}))
    return path, _sha(raw)


def test_unknown_exception_requires_exact_complete_count_keys_and_b2_binding(tmp_path, monkeypatch):
    path, digest = _b2_file(tmp_path, monkeypatch)
    pending = {key: 0 for key in restore.PENDING_KEYS}
    pending["dataset_scan_completion_unknown"] = 1
    leases = {key: 0 for key in restore.LEASE_KEYS}
    good = restore.check_preserved_unknown_exception(binding_path=path, expected_binding_sha256=digest,
        profile="data-20261026", pending_counts=pending, active_leases=leases)
    assert good["status"] == "PRESERVED_UNKNOWN_EXCEPTION_ALLOWED"
    assert pending["dataset_scan_completion_unknown"] == 1
    assert good["checks"]["other_pending_zero"] is True

    missing = dict(pending)
    del missing["job_attempt"]
    blocked = restore.check_preserved_unknown_exception(binding_path=path, expected_binding_sha256=digest,
        profile="data-20261026", pending_counts=missing, active_leases=leases)
    assert blocked["status"] == "BLOCKED"

    wrong_hash = restore.check_preserved_unknown_exception(binding_path=path, expected_binding_sha256="0" * 64,
        profile="data-20261026", pending_counts=pending, active_leases=leases)
    assert wrong_hash["status"] == "BLOCKED"


def test_unknown_exception_rejects_bool_counts_nonzero_lease_and_wrong_profile(tmp_path, monkeypatch):
    path, digest = _b2_file(tmp_path, monkeypatch)
    pending = {key: 0 for key in restore.PENDING_KEYS}
    pending["dataset_scan_completion_unknown"] = True
    leases = {key: 0 for key in restore.LEASE_KEYS}
    assert restore.check_preserved_unknown_exception(binding_path=path, expected_binding_sha256=digest,
        profile="data-20261026", pending_counts=pending, active_leases=leases)["status"] == "BLOCKED"
    pending["dataset_scan_completion_unknown"] = 1
    leases["outbox_active"] = 1
    assert restore.check_preserved_unknown_exception(binding_path=path, expected_binding_sha256=digest,
        profile="data-20261026", pending_counts=pending, active_leases=leases)["status"] == "BLOCKED"
    assert restore.check_preserved_unknown_exception(binding_path=path, expected_binding_sha256=digest,
        profile="assistant-20261006", pending_counts=pending, active_leases=leases)["status"] == "BLOCKED"


def test_profile_count_contract_requires_full_real_lease_keys():
    data_pending = {key: 0 for key in restore.PENDING_KEYS}
    assert not restore._zero_counts("data-20261026", data_pending, {}, allow_unknown=True)
    leases = {key: 0 for key in restore.LEASE_KEYS}
    assert restore._zero_counts("data-20261026", data_pending, leases, allow_unknown=False)
    assistant_pending = {key: 0 for key in restore.PENDING_KEYS - {"dataset_scan_completion_unknown", "data_import"}}
    assistant_pending.update(agent_run=0, agent_attempt=0)
    assert not restore._zero_counts("assistant-20261006", assistant_pending, leases, allow_unknown=False)
    full_assistant_leases = {key: 0 for key in restore.ASSISTANT_LEASE_KEYS}
    assert restore._zero_counts("assistant-20261006", assistant_pending, full_assistant_leases, allow_unknown=False)


def test_fresh_operational_gate_executes_complete_positive_path(monkeypatch, tmp_path):
    request = tmp_path / "request.json"
    ledger = tmp_path / "ledger.used"
    request.write_bytes(b'{"fixture":"request"}')
    request_sha = _sha(request.read_bytes())
    ledger.write_text(json.dumps({"status": "STARTED", "scan_completed": None, "scan_returncode": None,
                                  "helper_pid": None, "request_sha256": request_sha}), encoding="utf-8")
    monkeypatch.setattr(restore, "LEGACY_REQUEST", request)
    monkeypatch.setattr(restore, "LEGACY_LEDGER", ledger)
    monkeypatch.setattr(restore, "LEGACY_REQUEST_SHA256", request_sha)
    monkeypatch.setattr(restore, "LEGACY_LEDGER_SHA256", _sha(ledger.read_bytes()))
    assistant = tmp_path / "assistant-runtime.json"
    assistant.write_text("assistant fixture", encoding="utf-8")
    monkeypatch.setattr(restore, "ASSISTANT_CONFIG", assistant)
    monkeypatch.setattr(restore, "ASSISTANT_SHA256", _sha(assistant.read_bytes()))
    data_pending = {key: 0 for key in restore.PENDING_KEYS}
    data_pending["dataset_scan_completion_unknown"] = 1
    assistant_pending = {key: 0 for key in (restore.PENDING_KEYS - {"dataset_scan_completion_unknown", "data_import"}) | {"agent_run", "agent_attempt"}}
    monkeypatch.setattr(restore, "_fresh_stopped_ports_gate", lambda: (True, True))
    monkeypatch.setattr(restore, "_readonly_counts", lambda profile: (data_pending, {key: 0 for key in restore.LEASE_KEYS})
                        if profile == "data-20261026" else (assistant_pending, {key: 0 for key in restore.ASSISTANT_LEASE_KEYS}))
    monkeypatch.setattr(restore, "_fresh_resource_gate", lambda: True)
    monkeypatch.setattr(restore, "_fresh_boot_gate", lambda _: True)
    restore._fresh_operational_gates({"boot": {"fixture": True}})


def _apply_attempt_fixture(monkeypatch, tmp_path):
    monkeypatch.setattr(restore, "RUN", tmp_path)
    real_source = ROOT / "testing/runs/2026-10-28-data-import-audit03/audit03-pin-binding-successor.py"
    source = tmp_path / real_source.name
    source.write_bytes(real_source.read_bytes())
    monkeypatch.setattr(restore, "SUCCESSOR_SOURCE", source)
    monkeypatch.setattr(restore, "SUCCESSOR_SOURCE_SHA256", _sha(source.read_bytes()))
    scope = tmp_path / "scope.md"
    plan = tmp_path / "plan.json"
    scope.write_text("approved scope fixture", encoding="utf-8")
    plan.write_text("{}", encoding="utf-8")
    launch = {
        "schema_version": "audit03-successor-apply-launch/v1", "window_id": restore.WINDOW_ID,
        "status": "RETURNED", "user_approval_ref": "approval-01",
        "scope_sha256": _sha(scope.read_bytes()), "plan_sha256": _sha(plan.read_bytes()),
        "source_sha256": restore.SUCCESSOR_SOURCE_SHA256, "apply_invocations": 1,
        "child_pid_decimal_str": "123", "child_creation_filetime_decimal_str": "134376264399578250",
        "child_ended": True, "exit_code": 1, "at_utc": "2026-10-28T05:00:00Z",
    }
    launch_path = tmp_path / "audit03-pin-binding-successor-launch.json"
    launch_path.write_text(json.dumps(launch), encoding="utf-8")
    binding = {
        "authorization": {"approval_ref": "approval-01",
                           "scope": {"path": scope.name, "sha256": _sha(scope.read_bytes())},
                           "plan": {"path": plan.name, "sha256": _sha(plan.read_bytes())}},
        "binding_attempt": {"apply_invocations": 1,
                            "launch_receipt": {"path": launch_path.name, "sha256": _sha(launch_path.read_bytes())},
                            "operator_receipt": None, "stdout_receipt": None,
                            "configuration_write_observed": None, "core_completion_returned": None,
                            "final_config_verification": "UNKNOWN"},
    }
    return binding, launch, launch_path


def test_apply_launch_context_must_match_source_and_plan_before_any_write(monkeypatch, tmp_path):
    for field, wrong in (("source_sha256", "9" * 64), ("plan_sha256", "8" * 64)):
        case = tmp_path / field
        case.mkdir()
        binding, launch, launch_path = _apply_attempt_fixture(monkeypatch, case)
        launch[field] = wrong
        launch_path.write_text(json.dumps(launch), encoding="utf-8")
        binding["binding_attempt"]["launch_receipt"]["sha256"] = _sha(launch_path.read_bytes())

        runtime = case / "runtime.json"
        runtime.write_text("untouched", encoding="utf-8")
        pending, receipt = case / "pending", case / "receipt"
        monkeypatch.setattr(restore, "CONFIG", runtime)
        monkeypatch.setattr(restore, "PENDING", pending)
        monkeypatch.setattr(restore, "RECEIPT", receipt)
        monkeypatch.setattr(restore, "_b1", lambda *args, **kwargs: (binding, "c" * 64))

        def validate(_):
            assert not restore._binding_attempt_valid(binding)
            raise ValueError("BINDING_AUTHORIZATION_INVALID")

        monkeypatch.setattr(restore, "_validate_b1", validate)
        acl_calls = []
        monkeypatch.setattr(restore, "_capture_acl", lambda *args: acl_calls.append(True))
        with pytest.raises(ValueError, match="BINDING_AUTHORIZATION_INVALID"):
            restore.restore_config(Path("binding.json"), "c" * 64)
        assert runtime.read_text(encoding="utf-8") == "untouched"
        assert not pending.exists() and not receipt.exists() and not acl_calls


def test_complete_launch_with_missing_result_refs_remains_unknown_not_unstarted(monkeypatch, tmp_path):
    binding, _, _ = _apply_attempt_fixture(monkeypatch, tmp_path)
    assert restore._binding_attempt_valid(binding) is True
    assert binding["binding_attempt"]["configuration_write_observed"] is None
    assert binding["binding_attempt"]["core_completion_returned"] is None
    assert binding["binding_attempt"]["final_config_verification"] == "UNKNOWN"


def test_minimal_stopped_stdout_receipt_is_valid_unknown_apply_outcome(monkeypatch, tmp_path):
    binding, _, _ = _apply_attempt_fixture(monkeypatch, tmp_path)
    stdout = tmp_path / "audit03-pin-binding-successor.stdout.log"
    stdout.write_text(json.dumps({"status": "STOPPED", "error_type": "ValueError", "retry": False}), encoding="utf-8")
    attempt = binding["binding_attempt"]
    attempt["stdout_receipt"] = {"path": stdout.name, "sha256": _sha(stdout.read_bytes())}
    assert restore._binding_attempt_valid(binding) is True
    attempt["final_config_verification"] = "FAILED"
    assert restore._binding_attempt_valid(binding) is False


def test_current_operator_result_is_bound_and_later_started_snapshot_cannot_downgrade_facts(monkeypatch, tmp_path):
    binding, _, _ = _apply_attempt_fixture(monkeypatch, tmp_path)
    plan_sha = binding["authorization"]["plan"]["sha256"]
    source_sha = restore.SUCCESSOR_SOURCE_SHA256
    terminal = {"status": "FAILED_STOP_NO_RETRY_NO_ROLLBACK", "script_sha256": source_sha,
                "plan_sha256": plan_sha, "baseline_config_sha256": restore.BASELINE_SHA256,
                "expected_new_config_sha256": restore.CANDIDATE_SHA256,
                "source_manifest_sha256": restore.CANDIDATE_PIN, "retry": False, "rollback": False,
                "configuration_write_observed": True, "core_completion_returned": True,
                "final_config_verification": "PASSED"}
    operator = tmp_path / "audit03-pin-binding-successor-receipt.json"
    operator.write_text(json.dumps(terminal), encoding="utf-8")
    started = {**terminal, "status": "STARTED_AUDIT03_SUCCESSOR_PIN_BINDING",
               "configuration_write_observed": False, "core_completion_returned": False,
               "final_config_verification": "UNKNOWN"}
    stdout = tmp_path / "audit03-pin-binding-successor.stdout.log"
    stdout.write_text(json.dumps(started), encoding="utf-8")
    attempt = binding["binding_attempt"]
    attempt.update(operator_receipt={"path": operator.name, "sha256": _sha(operator.read_bytes())},
                   stdout_receipt={"path": stdout.name, "sha256": _sha(stdout.read_bytes())},
                   configuration_write_observed=True, core_completion_returned=True,
                   final_config_verification="PASSED")
    assert restore._binding_attempt_valid(binding) is True
    bad_terminal = {**terminal, "plan_sha256": "0" * 64}
    operator.write_text(json.dumps(bad_terminal), encoding="utf-8")
    attempt["operator_receipt"]["sha256"] = _sha(operator.read_bytes())
    assert restore._binding_attempt_valid(binding) is False
    operator.write_text(json.dumps(terminal), encoding="utf-8")
    attempt["operator_receipt"]["sha256"] = _sha(operator.read_bytes())
    attempt["core_completion_returned"] = None
    assert restore._binding_attempt_valid(binding) is False


def test_old_operator_receipt_source_is_rejected_before_pending_or_acl(monkeypatch, tmp_path):
    binding, _, _ = _apply_attempt_fixture(monkeypatch, tmp_path)
    record = {
        "status": "FAILED_STOP_NO_RETRY_NO_ROLLBACK", "script_sha256": "92de" + "0" * 60,
        "plan_sha256": binding["authorization"]["plan"]["sha256"],
        "baseline_config_sha256": restore.BASELINE_SHA256,
        "expected_new_config_sha256": restore.CANDIDATE_SHA256,
        "source_manifest_sha256": restore.CANDIDATE_PIN,
        "retry": False, "rollback": False, "configuration_write_observed": None,
        "core_completion_returned": None, "final_config_verification": "UNKNOWN",
    }
    path = tmp_path / "audit03-pin-binding-successor-receipt.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    binding["binding_attempt"]["operator_receipt"] = {"path": path.name, "sha256": _sha(path.read_bytes())}
    runtime = tmp_path / "runtime.json"
    runtime.write_text("untouched", encoding="utf-8")
    pending, receipt = tmp_path / "pending", tmp_path / "receipt"
    monkeypatch.setattr(restore, "CONFIG", runtime)
    monkeypatch.setattr(restore, "PENDING", pending)
    monkeypatch.setattr(restore, "RECEIPT", receipt)
    monkeypatch.setattr(restore, "_b1", lambda *args, **kwargs: (binding, "c" * 64))
    monkeypatch.setattr(restore, "_validate_b1", lambda _: (
        (_ for _ in ()).throw(ValueError("BINDING_AUTHORIZATION_INVALID"))
        if not restore._binding_attempt_valid(binding) else None))
    acl_calls = []
    monkeypatch.setattr(restore, "_capture_acl", lambda *args: acl_calls.append(True))
    with pytest.raises(ValueError, match="BINDING_AUTHORIZATION_INVALID"):
        restore.restore_config(Path("binding.json"), "c" * 64)
    assert runtime.read_text(encoding="utf-8") == "untouched"
    assert not pending.exists() and not receipt.exists() and not acl_calls


def test_actual_approval_validator_accepts_hash_pinned_a386_plan_and_subject_seal(monkeypatch, tmp_path):
    run = tmp_path
    source_real = ROOT / "testing/runs/2026-10-28-data-import-audit03/audit03-pin-binding-successor.py"
    helper_real = HELPER
    source = run / source_real.name
    helper = run / helper_real.name
    source.write_bytes(source_real.read_bytes())
    helper.write_bytes(helper_real.read_bytes())
    monkeypatch.setattr(restore, "RUN", run)
    monkeypatch.setattr(restore, "SUCCESSOR_SOURCE", source)
    monkeypatch.setattr(restore, "SUCCESSOR_SOURCE_SHA256", _sha(source.read_bytes()))
    monkeypatch.setattr(restore, "__file__", str(helper))
    wrapper = run / "start-once.ps1"
    manifest = run / "manifest.json"
    auth_template = run / "authorization.template.json"
    wrapper.write_text("wrapper", encoding="utf-8")
    auth_template.write_text("template", encoding="utf-8")
    manifest.write_text(json.dumps({"helper_source": {"path": helper.name, "sha256": _sha(helper.read_bytes())}}), encoding="utf-8")
    monkeypatch.setattr(restore, "START_WRAPPER", wrapper)
    monkeypatch.setattr(restore, "START_MANIFEST", manifest)
    monkeypatch.setattr(restore, "AUTHORIZATION_TEMPLATE", auth_template)

    operator = runpy.run_path(str(source_real), run_name="approval_plan_fixture")
    plan_obj = {
        "status": operator["SUCCESSOR_PLAN_STATUS"],
        "expected_new_config_sha256": restore.CANDIDATE_SHA256,
        "baseline_config_sha256": operator["BASELINE_CONFIG_SHA"],
        "deployment_candidate_sha256": operator["DEPLOYMENT_CANDIDATE_SHA"],
        "candidate_patch_sha256": operator["DEPLOYMENT_PATCH_SHA"],
        "source_freeze_sha256": operator["SOURCE_FREEZE_SHA"],
        "package_source_manifest_sha256": operator["PACKAGE_MANIFEST_SHA"],
        "database_baseline_sha256": operator["DATABASE_BASELINE_SHA"],
        "request_filename": operator["REQUEST_NAME"],
        "ledger_filename": operator["LEDGER_NAME"],
        "pin_binding_script_sha256": _sha(source.read_bytes()),
    }
    plan = run / "audit03-pin-binding-successor-plan.json"
    plan.write_text(json.dumps(plan_obj), encoding="utf-8")
    scope = run / "audit03-successor-real-window-scope-01.md"
    scope.write_text("approved", encoding="utf-8")
    qa = run / "audit03-successor-recovery-qa-seal.json"
    binding = {"window_id": restore.WINDOW_ID, "authorization": {
        "approval_ref": "chief-approval-01",
        "scope": {"path": scope.name, "sha256": _sha(scope.read_bytes())},
        "plan": {"path": plan.name, "sha256": _sha(plan.read_bytes())},
        "successor_source": {"path": source.name, "sha256": _sha(source.read_bytes())},
        "restore_source": {"path": helper.name, "sha256": _sha(helper.read_bytes())},
        "qa_seal": {"path": qa.name, "sha256": "0" * 64, "status": "QA_VERIFIED"},
    }}
    seal = {"schema_version": "audit03-successor-qa-seal/v1", "window_id": restore.WINDOW_ID,
            "status": "QA_VERIFIED", "phase": "CONFIG_RESTORE",
            "subject_sha256": restore._subject_sha(binding, "restore"),
            "dependency_hashes": {"config_restore": _sha(helper.read_bytes()),
                                  "original_start_wrapper": _sha(wrapper.read_bytes()),
                                  "original_start_manifest": _sha(manifest.read_bytes()),
                                  "authorization_template": _sha(auth_template.read_bytes())}}
    qa.write_text(json.dumps(seal), encoding="utf-8")
    binding["authorization"]["qa_seal"]["sha256"] = _sha(qa.read_bytes())
    assert restore._approval_b1(binding) is True

    binding["authorization"]["successor_source"]["sha256"] = "9" * 64
    assert restore._approval_b1(binding) is False


def test_start_qa_dependencies_match_fixed_files_and_manifest_helper_pin(monkeypatch, tmp_path):
    helper = tmp_path / "audit03-successor-config-restore.py"
    helper.write_text("helper", encoding="utf-8")
    wrapper = tmp_path / "start-once.ps1"
    manifest = tmp_path / "manifest.json"
    auth_template = tmp_path / "authorization.template.json"
    wrapper.write_text("wrapper", encoding="utf-8")
    auth_template.write_text("template", encoding="utf-8")
    manifest.write_text(json.dumps({"helper_source": {"path": helper.name, "sha256": _sha(helper.read_bytes())}}), encoding="utf-8")
    monkeypatch.setattr(restore, "__file__", str(helper))
    monkeypatch.setattr(restore, "START_WRAPPER", wrapper)
    monkeypatch.setattr(restore, "START_MANIFEST", manifest)
    monkeypatch.setattr(restore, "AUTHORIZATION_TEMPLATE", auth_template)
    deps = {"config_restore": _sha(helper.read_bytes()), "original_start_wrapper": _sha(wrapper.read_bytes()),
            "original_start_manifest": _sha(manifest.read_bytes()), "authorization_template": _sha(auth_template.read_bytes())}
    assert restore._start_dependencies_valid(deps)
    manifest.write_text(json.dumps({"helper_source": {"path": helper.name, "sha256": "f" * 64}}), encoding="utf-8")
    assert not restore._start_dependencies_valid(deps)
    manifest.write_text(json.dumps({"helper_source": {"path": helper.name, "sha256": _sha(helper.read_bytes())}}), encoding="utf-8")
    wrapper.write_text("changed", encoding="utf-8")
    assert not restore._start_dependencies_valid(deps)
    wrapper.write_text("wrapper", encoding="utf-8")
    auth_template.write_text("changed", encoding="utf-8")
    assert not restore._start_dependencies_valid(deps)


def _ready_identity_snapshot():
    return {
        "api": {"pid_decimal_str": "101", "creation_filetime_decimal_str": "134376264300000000",
                "image_ref": "sfc_python", "image_sha256": "a" * 64, "created_at_utc": "2026-10-28T05:00:00.000000Z"},
        "worker": {"pid_decimal_str": "202", "creation_filetime_decimal_str": "134376264400000000",
                   "image_ref": "sfc_python", "image_sha256": "b" * 64, "created_at_utc": "2026-10-28T05:00:01.000000Z"},
        "frontend": {"pid_decimal_str": "303", "creation_filetime_decimal_str": "134376264500000000",
                     "image_ref": "node", "image_sha256": "c" * 64, "created_at_utc": "2026-10-28T05:00:02.000000Z"},
        "worker_marker": {"pid_decimal_str": "202", "process_starttime_decimal_str": "134376264400000000",
                          "instance_id": "b974b7206e704d46b133414f36e37d7a", "boot_id": None},
    }


def _stop_boot_fixture(monkeypatch, tmp_path):
    monkeypatch.setattr(restore, "RUN", tmp_path)
    scope = tmp_path / "audit03-successor-real-window-scope-01.md"
    plan = tmp_path / "audit03-pin-binding-successor-plan.json"
    scope.write_text("approved scope", encoding="utf-8")
    plan.write_text("approved plan", encoding="utf-8")
    ids = _ready_identity_snapshot()
    boot = {"schema_version": "audit03-successor-boot/v1", "window_id": restore.WINDOW_ID,
            "status": "BOOT_BOUNDARY_VERIFIED", "qa_status": "QA_VERIFIED",
            "cim_boot_utc": "2026-10-28T01:42:16.500000Z",
            "kernel_start_utc": "2026-10-28T01:42:17.000000Z",
            "uptime_derived_boot_utc": "2026-10-28T01:42:18.000000Z",
            "legacy_attempt_utc": restore.LEGACY_ATTEMPT_UTC,
            "legacy_deadline_utc": restore.LEGACY_DEADLINE_UTC,
            "old_scan_completion_proven": False}
    boot_path = tmp_path / "audit03-successor-boot-seal.json"
    boot_path.write_text(json.dumps(boot), encoding="utf-8")
    ready = {"schema_version": "audit03-successor-original-ready/v1", "window_id": restore.WINDOW_ID,
             "status": "READY_IDENTITY_VERIFIED", "scope_sha256": _sha(scope.read_bytes()),
             "plan_sha256": _sha(plan.read_bytes()), "boot_evidence_sha256": _sha(boot_path.read_bytes()),
             "assistant_config_sha256": restore.ASSISTANT_SHA256,
             "data_config_sha256": restore.BASELINE_SHA256, "identity_snapshot": ids}
    ready_path = tmp_path / "audit03-successor-original-ready.json"
    ready_path.write_text(json.dumps(ready), encoding="utf-8")
    stop = {"schema_version": "audit03-successor-stop/v1", "window_id": restore.WINDOW_ID,
            "status": "STOPPED_VERIFIED", "profile": "assistant-20261006", "stop_invocations": 1,
            "exit_code": 0, "scope_sha256": ready["scope_sha256"], "plan_sha256": ready["plan_sha256"],
            "user_approval_ref": "approval", "source_ready_receipt": {"path": ready_path.name, "sha256": _sha(ready_path.read_bytes())},
            "identity_snapshot": ids, "both_profiles_stopped": True, "ports_free": True}
    stop_path = tmp_path / "audit03-successor-original-stop.json"
    stop_path.write_text(json.dumps(stop), encoding="utf-8")
    binding = {"authorization": {"approval_ref": "approval",
             "scope": {"path": scope.name, "sha256": _sha(scope.read_bytes())},
             "plan": {"path": plan.name, "sha256": _sha(plan.read_bytes())}},
             "stop_receipt": {"path": stop_path.name, "sha256": _sha(stop_path.read_bytes()),
                 "status": "STOPPED_VERIFIED", "profile": "assistant-20261006", "stop_invocations": 1,
                 "exit_code": 0, "identity_snapshot": ids},
             "boot": {"evidence": {"path": boot_path.name, "sha256": _sha(boot_path.read_bytes())},
                      "cim_boot_utc": boot["cim_boot_utc"], "kernel_start_utc": boot["kernel_start_utc"],
                      "uptime_derived_boot_utc": boot["uptime_derived_boot_utc"], "qa_status": "QA_VERIFIED"}}
    return binding, stop, stop_path, boot, boot_path, ready_path


def test_stop_boot_and_identity_snapshot_require_consistent_fresh_evidence(monkeypatch, tmp_path):
    binding, stop, stop_path, boot, boot_path, ready_path = _stop_boot_fixture(monkeypatch, tmp_path)
    assert restore._stop_and_boot_valid(binding) is True
    assert restore._identity_snapshot_valid(binding["stop_receipt"]["identity_snapshot"]) is True

    bad_boot = {**boot, "kernel_start_utc": "2026-10-28T01:42:19.000000Z"}
    boot_path.write_text(json.dumps(bad_boot), encoding="utf-8")
    binding["boot"]["kernel_start_utc"] = bad_boot["kernel_start_utc"]
    binding["boot"]["evidence"]["sha256"] = _sha(boot_path.read_bytes())
    assert restore._stop_and_boot_valid(binding) is False

    bad_ids = {**_ready_identity_snapshot(), "api": {"pid_decimal_str": None}}
    assert restore._identity_snapshot_valid(bad_ids) is False


def test_post_restore_binding_identity_must_match_verified_receipt():
    identity = _identity()
    post = {"data_config_sha256": restore.BASELINE_SHA256, "current_config_identity": identity}
    receipt = {"post_config_sha256": restore.BASELINE_SHA256, "post_config_identity": identity}
    assert restore._post_restore_receipt_matches(post, receipt)
    changed = {**identity, "file_id_hex": "ff"}
    assert not restore._post_restore_receipt_matches({**post, "current_config_identity": changed}, receipt)


def _full_b2_chain_fixture(monkeypatch, tmp_path):
    run = tmp_path
    helper_copy = run / HELPER.name
    helper_copy.write_bytes(HELPER.read_bytes())
    monkeypatch.setattr(restore, "__file__", str(helper_copy))
    monkeypatch.setattr(restore, "RUN", run)
    monkeypatch.setattr(restore, "B1", run / "audit03-successor-recovery-binding.json")
    monkeypatch.setattr(restore, "B2", run / "audit03-successor-original-start-binding.json")
    monkeypatch.setattr(restore, "RECEIPT", run / "audit03-pin-binding-successor-config-restore.json")
    wrapper = run / "audit03-successor-original-start-once.ps1"
    manifest = run / "manifest.json"
    auth_template = run / "authorization.template.json"
    wrapper.write_text("fixed wrapper fixture", encoding="utf-8")
    auth_template.write_text("fixed authorization template fixture", encoding="utf-8")
    manifest.write_text(json.dumps({"helper_source": {"path": helper_copy.name, "sha256": _sha(helper_copy.read_bytes())}}), encoding="utf-8")
    monkeypatch.setattr(restore, "START_WRAPPER", wrapper)
    monkeypatch.setattr(restore, "START_MANIFEST", manifest)
    monkeypatch.setattr(restore, "AUTHORIZATION_TEMPLATE", auth_template)

    stop_binding, stop, stop_path, boot, boot_path, ready_path = _stop_boot_fixture(monkeypatch, run)
    source_real = ROOT / "testing/runs/2026-10-28-data-import-audit03/audit03-pin-binding-successor.py"
    source = run / source_real.name
    source.write_bytes(source_real.read_bytes())
    source_sha = _sha(source.read_bytes())
    monkeypatch.setattr(restore, "SUCCESSOR_SOURCE", source)
    monkeypatch.setattr(restore, "SUCCESSOR_SOURCE_SHA256", source_sha)
    operator = runpy.run_path(str(source_real), run_name="full_b2_plan_fixture")
    plan_obj = {
        "status": operator["SUCCESSOR_PLAN_STATUS"], "expected_new_config_sha256": restore.CANDIDATE_SHA256,
        "baseline_config_sha256": operator["BASELINE_CONFIG_SHA"], "deployment_candidate_sha256": operator["DEPLOYMENT_CANDIDATE_SHA"],
        "candidate_patch_sha256": operator["DEPLOYMENT_PATCH_SHA"], "source_freeze_sha256": operator["SOURCE_FREEZE_SHA"],
        "package_source_manifest_sha256": operator["PACKAGE_MANIFEST_SHA"], "database_baseline_sha256": operator["DATABASE_BASELINE_SHA"],
        "request_filename": operator["REQUEST_NAME"], "ledger_filename": operator["LEDGER_NAME"],
        "pin_binding_script_sha256": source_sha,
    }
    plan_path = run / "audit03-pin-binding-successor-plan.json"
    plan_path.write_text(json.dumps(plan_obj), encoding="utf-8")
    scope_path = run / "audit03-successor-real-window-scope-01.md"
    scope_path.write_text("approved scope fixture", encoding="utf-8")
    ready = json.loads(ready_path.read_text(encoding="utf-8"))
    ready["scope_sha256"], ready["plan_sha256"] = _sha(scope_path.read_bytes()), _sha(plan_path.read_bytes())
    ready_path.write_text(json.dumps(ready), encoding="utf-8")
    stop["scope_sha256"], stop["plan_sha256"] = ready["scope_sha256"], ready["plan_sha256"]
    stop["source_ready_receipt"] = {"path": ready_path.name, "sha256": _sha(ready_path.read_bytes())}
    stop_path.write_text(json.dumps(stop), encoding="utf-8")

    qa_b1_path = run / "audit03-successor-recovery-qa-seal.json"
    b1 = {
        "schema_version": "audit03-successor-recovery-binding/v1", "window_id": restore.WINDOW_ID,
        "status": "ACTIVE_CONFIG_RESTORE", "authorization": {
            "approval_ref": "approval", "scope": {"path": scope_path.name, "sha256": _sha(scope_path.read_bytes())},
            "plan": {"path": plan_path.name, "sha256": _sha(plan_path.read_bytes())},
            "successor_source": {"path": source.name, "sha256": source_sha},
            "restore_source": {"path": helper_copy.name, "sha256": _sha(helper_copy.read_bytes())},
            "qa_seal": {"path": qa_b1_path.name, "sha256": "0" * 64, "status": "QA_VERIFIED"}},
        "binding_attempt": {"apply_invocations": 0, "launch_receipt": None, "operator_receipt": None,
                            "stdout_receipt": None, "configuration_write_observed": None,
                            "core_completion_returned": None, "final_config_verification": "UNKNOWN"},
        "stop_receipt": {"path": stop_path.name, "sha256": _sha(stop_path.read_bytes()), **{
            k: stop[k] for k in ("status", "profile", "stop_invocations", "exit_code", "identity_snapshot")}},
        "boot": stop_binding["boot"], "pre_restore": {"mode": "SKIP_ALREADY_BASELINE", "independent_backup": None},
    }
    qa_b1 = {"schema_version": "audit03-successor-qa-seal/v1", "window_id": restore.WINDOW_ID,
             "status": "QA_VERIFIED", "phase": "CONFIG_RESTORE", "subject_sha256": restore._subject_sha(b1, "restore"),
             "dependency_hashes": {"config_restore": _sha(helper_copy.read_bytes()),
                                   "original_start_wrapper": _sha(wrapper.read_bytes()),
                                   "original_start_manifest": _sha(manifest.read_bytes()),
                                   "authorization_template": _sha(auth_template.read_bytes())}}
    qa_b1_path.write_text(json.dumps(qa_b1), encoding="utf-8")
    b1["authorization"]["qa_seal"]["sha256"] = _sha(qa_b1_path.read_bytes())
    b1_path = restore.B1
    b1_path.write_text(json.dumps(b1), encoding="utf-8")
    b1_sha = _sha(b1_path.read_bytes())

    identity = _identity()
    receipt = {"window_id": restore.WINDOW_ID, "binding_sha256": b1_sha,
               "status": "NO_CHANGE_VERIFIED", "mode": "SKIP_ALREADY_BASELINE", "write_attempted": False,
               "configuration_write_observed": None, "final_config_verification": "PASSED",
               "post_config_sha256": restore.BASELINE_SHA256, "post_config_identity": identity}
    restore.RECEIPT.write_text(json.dumps(receipt), encoding="utf-8")
    b2 = {
        "schema_version": "audit03-successor-original-start-binding/v1", "window_id": restore.WINDOW_ID,
        "status": "ACTIVE_ORIGINAL_START", "restore_binding": {"path": b1_path.name, "sha256": b1_sha},
        "restore_receipt": {"path": restore.RECEIPT.name, "sha256": _sha(restore.RECEIPT.read_bytes()),
                            "status": receipt["status"], "mode": receipt["mode"], "write_attempted": False,
                            "configuration_write_observed": None, "final_config_verification": "PASSED"},
        "stop_receipt": {"path": stop_path.name, "sha256": _sha(stop_path.read_bytes())},
        "post_restore": {"assistant_config_sha256": restore.ASSISTANT_SHA256,
                         "data_config_sha256": restore.BASELINE_SHA256, "data_source_pin_sha256": restore.BASELINE_PIN,
                         "current_config_identity": identity, "backup_sha256": None, "backup_identity": None},
        "preserved_unknown_exception": {"profile": "data-20261026", "ledger_sha256": restore.LEGACY_LEDGER_SHA256,
            "request_sha256": restore.LEGACY_REQUEST_SHA256, "unknown_key": "dataset_scan_completion_unknown",
            "unknown_count": 1, "all_other_pending_counts_zero": True, "active_leases_zero": True,
            "status": "UNKNOWN_PRESERVED"},
        "fresh_identity_seal": {"path": "audit03-successor-original-start-qa-seal.json", "sha256": "0" * 64,
                                 "qa_status": "QA_VERIFIED"},
    }
    qa_b2_path = run / "audit03-successor-original-start-qa-seal.json"
    qa_b2 = {"schema_version": "audit03-successor-qa-seal/v1", "window_id": restore.WINDOW_ID,
             "status": "QA_VERIFIED", "phase": "ORIGINAL_START", "subject_sha256": restore._subject_sha(b2, "start"),
             "dependency_hashes": qa_b1["dependency_hashes"]}
    qa_b2_path.write_text(json.dumps(qa_b2), encoding="utf-8")
    b2["fresh_identity_seal"]["sha256"] = _sha(qa_b2_path.read_bytes())
    restore.B2.write_text(json.dumps(b2), encoding="utf-8")

    request = run / "legacy.request"
    ledger = run / "legacy.used"
    request.write_bytes(b'{"fixture":"legacy-request"}')
    request_sha = _sha(request.read_bytes())
    ledger_raw = json.dumps({"status": "STARTED", "scan_completed": None, "scan_returncode": None,
                             "helper_pid": None, "request_sha256": request_sha}).encode()
    ledger.write_bytes(ledger_raw)
    monkeypatch.setattr(restore, "LEGACY_REQUEST", request)
    monkeypatch.setattr(restore, "LEGACY_LEDGER", ledger)
    monkeypatch.setattr(restore, "LEGACY_REQUEST_SHA256", request_sha)
    monkeypatch.setattr(restore, "LEGACY_LEDGER_SHA256", _sha(ledger_raw))
    return b2, identity, ledger


def test_real_b2_chain_drives_start_and_unknown_gates_and_rejects_boot_or_ledger_drift(monkeypatch, tmp_path):
    b2, identity, ledger = _full_b2_chain_fixture(monkeypatch, tmp_path)
    cim = "2026-10-28T01:42:16.500000+00:00"
    monkeypatch.setattr(restore.subprocess, "run", lambda *a, **k: type("Result", (), {"returncode": 0, "stdout": cim})())
    assert restore._validate_b2_chain(b2)[2]["status"] == "NO_CHANGE_VERIFIED"
    data_pending = {key: 0 for key in restore.PENDING_KEYS}
    data_pending["dataset_scan_completion_unknown"] = 1
    data_leases = {key: 0 for key in restore.LEASE_KEYS}
    allowed = restore.check_preserved_unknown_exception(binding_path=restore.B2,
        expected_binding_sha256=_sha(restore.B2.read_bytes()), profile="data-20261026",
        pending_counts=data_pending, active_leases=data_leases)
    assert allowed["status"] == "PRESERVED_UNKNOWN_EXCEPTION_ALLOWED"

    assistant = tmp_path / "assistant.json"
    assistant.write_text("assistant fixture", encoding="utf-8")
    monkeypatch.setattr(restore, "ASSISTANT_CONFIG", assistant)
    monkeypatch.setattr(restore, "CONFIG", tmp_path / "data.json")
    monkeypatch.setattr(restore, "_file_sha", lambda p: restore.ASSISTANT_SHA256 if p == assistant else
                        restore.BASELINE_SHA256 if p == restore.CONFIG else _sha(Path(p).read_bytes()))
    monkeypatch.setattr(restore, "_identity", lambda _: identity)
    monkeypatch.setattr(restore, "_fresh_stopped_ports_gate", lambda: (True, True))
    assistant_pending = {key: 0 for key in (restore.PENDING_KEYS - {"dataset_scan_completion_unknown", "data_import"}) | {"agent_run", "agent_attempt"}}
    monkeypatch.setattr(restore, "_readonly_counts", lambda profile: (data_pending, data_leases)
                        if profile == "data-20261026" else (assistant_pending, {key: 0 for key in restore.ASSISTANT_LEASE_KEYS}))
    monkeypatch.setattr(restore, "_fresh_resource_gate", lambda: True)
    start = restore.check_start(restore.B2, _sha(restore.B2.read_bytes()))
    assert start["status"] == "READY_FOR_ORIGINAL_START" and all(start["checks"].values())

    monkeypatch.setattr(restore.subprocess, "run", lambda *a, **k: type("Result", (), {"returncode": 0, "stdout": "2026-10-28T01:43:00+00:00"})())
    assert restore.check_preserved_unknown_exception(binding_path=restore.B2,
        expected_binding_sha256=_sha(restore.B2.read_bytes()), profile="data-20261026",
        pending_counts=data_pending, active_leases=data_leases)["status"] == "BLOCKED"
    monkeypatch.setattr(restore.subprocess, "run", lambda *a, **k: type("Result", (), {"returncode": 0, "stdout": cim})())
    ledger.write_bytes(ledger.read_bytes() + b" ")
    assert restore.check_start(restore.B2, _sha(restore.B2.read_bytes()))["status"] == "BLOCKED"


