from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import FunctionType, SimpleNamespace

import pytest


ROOT = Path(r"D:\Jupyter\Chromatography_agent")
SUCCESSOR_PATH = ROOT / "testing/runs/2026-10-28-data-import-audit03/audit03-pin-binding-successor.py"
SPEC = importlib.util.spec_from_file_location("audit03_pin_binding_successor", SUCCESSOR_PATH)
assert SPEC is not None and SPEC.loader is not None
successor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(successor)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _status_json() -> str:
    return json.dumps({
        "selected": [{"service": service, "status": "STOPPED"} for service in ("api", "worker", "frontend")],
        "other": [],
    })


def _acl_json() -> str:
    return json.dumps({
        "owner_sid": "S-1-5-TEST",
        "source_owner_sid": "S-1-5-TEST",
        "source_sddl": "D:P(A;;FA;;;OW)",
        "target_sddl": "D:P(A;;FA;;;OW)",
        "source_protected": True,
        "target_protected": True,
    })


def _prepare_apply(monkeypatch, tmp_path, *, invalid_status_call=None, replace_mode="write", acl_failure=None):
    config = tmp_path / "runtime.json"
    pending = tmp_path / "runtime.json.source-pin-audit03-successor.pending"
    backup = tmp_path / "runtime.json.audit03-successor-before.json"
    receipt_path = tmp_path / "audit03-pin-binding-successor-receipt.json"
    plan_path = tmp_path / "audit03-pin-binding-successor-plan.json"
    baseline_path = tmp_path / "database-baseline.json"
    start = tmp_path / "start-platform.ps1"
    pwsh = tmp_path / "pwsh.exe"
    start.write_text("fake start entry", encoding="utf-8")
    pwsh.write_text("fake powershell", encoding="utf-8")

    original = (json.dumps({successor.KEY: successor.OLD_PIN, "synthetic": {"version": 1}}, separators=(",", ":")) + "\n").encode()
    candidate = successor._candidate_config(original)
    baseline_sha = _sha(original)
    candidate_sha = _sha(candidate)
    baseline = json.dumps({
        "data_config_sha256": baseline_sha,
        "source_manifest_sha256": successor.OLD_PIN,
    }, sort_keys=True).encode() + b"\n"
    baseline_path.write_bytes(baseline)

    for name, value in {
        "RUNTIME": tmp_path,
        "CONFIG": config,
        "PENDING": pending,
        "BACKUP": backup,
        "RECEIPT": receipt_path,
        "PLAN": plan_path,
        "DATABASE_BASELINE": baseline_path,
        "DATABASE_BASELINE_SHA": _sha(baseline),
        "BASELINE_CONFIG_SHA": baseline_sha,
        "START_PLATFORM": start,
        "PWSH": pwsh,
    }.items():
        monkeypatch.setattr(successor, name, value)
    config.write_bytes(original)

    plan = {
        "status": successor.SUCCESSOR_PLAN_STATUS,
        "expected_new_config_sha256": candidate_sha,
        "baseline_config_sha256": baseline_sha,
        "deployment_candidate_sha256": successor.DEPLOYMENT_CANDIDATE_SHA,
        "candidate_patch_sha256": successor.DEPLOYMENT_PATCH_SHA,
        "source_freeze_sha256": successor.SOURCE_FREEZE_SHA,
        "package_source_manifest_sha256": successor.PACKAGE_MANIFEST_SHA,
        "database_baseline_sha256": successor.DATABASE_BASELINE_SHA,
        "request_filename": successor.REQUEST_NAME,
        "ledger_filename": successor.LEDGER_NAME,
        "pin_binding_script_sha256": successor.file_sha(Path(successor.__file__).resolve()),
    }
    plan_path.write_text(json.dumps(plan, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    plan_sha = successor.file_sha(plan_path)

    verified = []
    verify_sources = successor._verify_candidate_sources

    def actual_verify_sources():
        verified.append(True)
        return verify_sources()

    monkeypatch.setattr(successor, "_verify_candidate_sources", actual_verify_sources)
    events = []
    status_calls = []
    acl_calls = []

    def fake_run(args, **kwargs):
        if "-File" in args:
            profile = args[args.index("-Profile") + 1]
            status_calls.append(profile)
            events.append(("status", profile))
            stdout = "INVALID_JSON_SECRET" if len(status_calls) == invalid_status_call else _status_json()
            return SimpleNamespace(returncode=0, stdout=stdout, stderr="SECRET_STDERR")
        if "-Command" in args and kwargs.get("env") is not None:
            target = Path(kwargs["env"]["CHROM_PIN_PENDING"])
            acl_calls.append(target.name)
            events.append(("acl", target.name))
            if len(acl_calls) == 3 and acl_failure == "nonzero":
                return SimpleNamespace(returncode=5, stdout="SECRET_ACL_STDOUT", stderr="SECRET_ACL_STDERR")
            if len(acl_calls) == 3 and acl_failure == "metadata_json":
                return SimpleNamespace(returncode=0, stdout="INVALID_METADATA_SECRET", stderr="SECRET_ACL_STDERR")
            if len(acl_calls) == 3 and acl_failure == "metadata_mismatch":
                invalid = json.loads(_acl_json())
                invalid["target_protected"] = False
                return SimpleNamespace(returncode=0, stdout=json.dumps(invalid), stderr="SECRET_ACL_STDERR")
            return SimpleNamespace(returncode=0, stdout=_acl_json(), stderr="SECRET_STDERR")
        if "-Command" in args:
            events.append(("ports", "free"))
            return SimpleNamespace(returncode=0, stdout="0", stderr="SECRET_STDERR")
        raise AssertionError("unexpected subprocess shape")

    monkeypatch.setattr(successor.subprocess, "run", fake_run)

    real_core_loader = successor._core
    replace_calls = []

    def fake_replace(source, target):
        replace_calls.append((Path(source), Path(target)))
        if replace_mode == "raise":
            raise OSError("SECRET_REPLACE_MESSAGE")
        Path(target).write_bytes(Path(source).read_bytes())
        Path(source).unlink()

    def isolated_core_loader():
        core = real_core_loader()
        original_complete = core["complete_pin_files"]
        isolated_globals = dict(original_complete.__globals__)
        isolated_globals["os"] = SimpleNamespace(replace=fake_replace)
        core["complete_pin_files"] = FunctionType(
            original_complete.__code__, isolated_globals, original_complete.__name__,
            original_complete.__defaults__, original_complete.__closure__,
        )
        return core

    monkeypatch.setattr(successor, "_core", isolated_core_loader)
    return SimpleNamespace(
        config=config, pending=pending, backup=backup, receipt=receipt_path,
        plan_sha=plan_sha, original=original, candidate=candidate, events=events,
        status_calls=status_calls, verified=verified, replace_calls=replace_calls,
        acl_calls=acl_calls,
    )


def _read_stdout_receipt(capsys):
    output = capsys.readouterr().out
    return json.loads(output), output


def test_real_cli_apply_chain_succeeds_with_fake_external_boundaries(monkeypatch, tmp_path, capsys):
    fixture = _prepare_apply(monkeypatch, tmp_path)

    result = successor.main(["--apply", "--plan-sha256", fixture.plan_sha])

    receipt, output = _read_stdout_receipt(capsys)
    assert result == 0
    assert fixture.verified == [True]
    assert fixture.events == [
        ("status", "data-20261026"), ("status", "assistant-20261006"), ("ports", "free"),
        ("acl", fixture.backup.name), ("acl", fixture.pending.name), ("acl", fixture.pending.name),
        ("status", "data-20261026"), ("status", "assistant-20261006"), ("ports", "free"),
    ]
    assert fixture.replace_calls == [(fixture.pending, fixture.config)]
    assert fixture.config.read_bytes() == fixture.candidate
    assert receipt["status"] == "SUCCEEDED_AUDIT03_SUCCESSOR_PIN_BINDING_UPDATED"
    assert receipt["script_sha256"] == successor.file_sha(Path(successor.__file__).resolve())
    assert receipt["second_acl_copy_attempted"] is True
    assert receipt["second_acl_copy_completed"] is True
    assert [(row["call_role"], row["stage"], row["reason_code"]) for row in receipt["acl_diagnostics"]] == [
        ("BACKUP_INITIAL", "COMPLETE", "NONE"),
        ("PENDING_INITIAL", "COMPLETE", "NONE"),
        ("PENDING_CORE_SECOND", "COMPLETE", "NONE"),
    ]
    assert receipt["before_replace_callback_entered"] is True
    assert receipt["idle_gate_attempted"] is True
    assert receipt["idle_gate_succeeded"] is True
    assert receipt["pin_write_attempted"] is True
    assert receipt["configuration_write_observed"] is True
    assert receipt["final_config_verification"] == "PASSED"
    assert "SECRET" not in output


@pytest.mark.parametrize(
    ("acl_failure", "expected_stage", "expected_reason", "expected_error_type"),
    [
        ("nonzero", "COMMAND_NONZERO", "PIN_ACL_COPY_OR_VERIFY_FAILED", "RuntimeError"),
        ("metadata_json", "METADATA_JSON_PARSE", "PIN_ACL_METADATA_JSON_INVALID", "JSONDecodeError"),
        ("metadata_mismatch", "METADATA_MISMATCH", "PIN_ACL_METADATA_MISMATCH", "RuntimeError"),
    ],
)
def test_actual_third_acl_failure_keeps_common_error_and_substage(
    monkeypatch, tmp_path, capsys, acl_failure, expected_stage, expected_reason, expected_error_type
):
    fixture = _prepare_apply(monkeypatch, tmp_path, acl_failure=acl_failure)

    result = successor.main(["--apply", "--plan-sha256", fixture.plan_sha])

    receipt, output = _read_stdout_receipt(capsys)
    assert result == 1
    assert fixture.acl_calls == [fixture.backup.name, fixture.pending.name, fixture.pending.name]
    assert receipt["error_type"] == expected_error_type
    assert receipt["second_acl_copy_attempted"] is True
    assert receipt["second_acl_copy_completed"] is False
    assert receipt["before_replace_callback_entered"] is False
    assert receipt["pin_write_attempted"] is False
    assert receipt["acl_diagnostics"][-1] == {
        "call_role": "PENDING_CORE_SECOND",
        "stage": expected_stage,
        "reason_code": expected_reason,
    }
    assert "SECRET" not in output


def test_actual_idle_json_failure_is_recorded_without_setting_write_attempt(monkeypatch, tmp_path, capsys):
    fixture = _prepare_apply(monkeypatch, tmp_path, invalid_status_call=3)

    result = successor.main(["--apply", "--plan-sha256", fixture.plan_sha])

    receipt, output = _read_stdout_receipt(capsys)
    assert result == 1
    assert fixture.verified == [True]
    assert fixture.replace_calls == []
    assert receipt["status"] == "FAILED_STOP_NO_RETRY_NO_ROLLBACK"
    assert receipt["retry"] is False
    assert receipt["error_type"] == "RuntimeError"
    assert receipt["error_reason_code"] == "MANAGED_STATUS_INVALID"
    assert receipt["error_chain_types"][:3] == ["RuntimeError", "JSONDecodeError", "OTHER"]
    assert receipt["diagnostic_stage"] == "FRESH_IDLE_CHECK"
    assert receipt["before_replace_callback_entered"] is True
    assert receipt["idle_gate_attempted"] is True
    assert receipt["idle_gate_succeeded"] is False
    assert receipt["pin_write_attempted"] is False
    assert "SECRET" not in output


def test_primary_replace_failure_survives_observer_and_final_save_failures(monkeypatch, tmp_path, capsys):
    fixture = _prepare_apply(monkeypatch, tmp_path, replace_mode="raise")
    real_file_sha = successor.file_sha
    observer_triggered = []

    def fail_observer(path):
        if Path(path) == fixture.config and fixture.replace_calls:
            observer_triggered.append(True)
            raise ValueError("SECRET_OBSERVER_MESSAGE")
        return real_file_sha(path)

    monkeypatch.setattr(successor, "file_sha", fail_observer)
    real_save = successor._save_receipt
    save_calls = []

    def fail_final_save(receipt):
        save_calls.append(True)
        if len(save_calls) == 6:
            raise OSError("SECRET_FINAL_SAVE_MESSAGE")
        real_save(receipt)

    monkeypatch.setattr(successor, "_save_receipt", fail_final_save)

    result = successor.main(["--apply", "--plan-sha256", fixture.plan_sha])

    receipt, output = _read_stdout_receipt(capsys)
    assert result == 1
    assert observer_triggered == [True]
    assert len(save_calls) == 6
    assert receipt["status"] == "FAILED_STOP_NO_RETRY_NO_ROLLBACK"
    assert receipt["error_type"] == "OSError"
    assert receipt["error_chain_types"][0] == "OSError"
    assert receipt["final_observation_error_types"][0] == "ValueError"
    assert receipt["final_observation_reason_code"] == "FINAL_OBSERVATION_FAILED"
    assert receipt["configuration_write_observed"] is None
    assert receipt["final_config_verification"] == "UNKNOWN"
    assert receipt["final_receipt_save_error_types"][0] == "OSError"
    assert "SECRET" not in output


def test_final_save_failure_without_prior_error_raises_and_never_reports_success(monkeypatch, tmp_path, capsys):
    fixture = _prepare_apply(monkeypatch, tmp_path)
    real_save = successor._save_receipt
    save_calls = []

    def fail_final_save(receipt):
        save_calls.append(True)
        if len(save_calls) == 6:
            raise OSError("SECRET_FINAL_SAVE_MESSAGE")
        real_save(receipt)

    monkeypatch.setattr(successor, "_save_receipt", fail_final_save)

    with pytest.raises(OSError):
        successor.main(["--apply", "--plan-sha256", fixture.plan_sha])

    assert len(save_calls) == 6
    assert capsys.readouterr().out == ""
    persisted = json.loads(fixture.receipt.read_text(encoding="utf-8"))
    assert persisted["status"] == "STARTED_AUDIT03_SUCCESSOR_PIN_BINDING"
    assert not persisted["status"].startswith("SUCCEEDED")


def test_false_final_observation_converts_core_success_to_failure(monkeypatch, tmp_path, capsys):
    fixture = _prepare_apply(monkeypatch, tmp_path)
    real_file_sha = successor.file_sha

    def false_config_observation(path):
        if Path(path) == fixture.config:
            return "0" * 64
        return real_file_sha(path)

    monkeypatch.setattr(successor, "file_sha", false_config_observation)

    result = successor.main(["--apply", "--plan-sha256", fixture.plan_sha])

    receipt, output = _read_stdout_receipt(capsys)
    assert result == 1
    assert fixture.replace_calls == [(fixture.pending, fixture.config)]
    assert receipt["status"] == "FAILED_STOP_NO_RETRY_NO_ROLLBACK"
    assert receipt["error_reason_code"] == "FINAL_CONFIG_SHA_MISMATCH"
    assert receipt["retry"] is False
    assert receipt["configuration_write_observed"] is True
    assert receipt["final_config_verification"] == "FAILED"
    assert not receipt["status"].startswith("SUCCEEDED")
    assert "SECRET" not in output


def test_final_observer_exception_is_primary_and_preserves_write_observation(monkeypatch, tmp_path, capsys):
    fixture = _prepare_apply(monkeypatch, tmp_path)
    real_file_sha = successor.file_sha

    def fail_observer(path):
        if Path(path) == fixture.config and fixture.replace_calls:
            raise ValueError("SECRET_OBSERVER_MESSAGE")
        return real_file_sha(path)

    monkeypatch.setattr(successor, "file_sha", fail_observer)

    result = successor.main(["--apply", "--plan-sha256", fixture.plan_sha])

    receipt, output = _read_stdout_receipt(capsys)
    assert result == 1
    assert receipt["status"] == "FAILED_STOP_NO_RETRY_NO_ROLLBACK"
    assert receipt["retry"] is False
    assert receipt["error_type"] == "ValueError"
    assert receipt["error_chain_types"][0] == "ValueError"
    assert receipt["error_reason_code"] == "UNCLASSIFIED_EXCEPTION"
    assert receipt["final_observation_reason_code"] == "FINAL_OBSERVATION_FAILED"
    assert receipt["configuration_write_observed"] is True
    assert receipt["final_config_verification"] == "UNKNOWN"
    assert receipt["diagnostic_stage"] == "FINAL_CONFIG_OBSERVATION"
    assert "SECRET" not in output


def test_core_confirmation_survives_immediate_postcheck_and_final_read_failure(monkeypatch, tmp_path, capsys):
    fixture = _prepare_apply(monkeypatch, tmp_path)
    real_core_loader = successor._core
    real_read_bytes = Path.read_bytes

    def fail_config_read(path):
        if path == fixture.config:
            raise OSError("SECRET_POSTCHECK_READ")
        return real_read_bytes(path)

    def core_loader():
        core = real_core_loader()
        complete = core["complete_pin_files"]

        def confirm_then_fail_reads(*args, **kwargs):
            result = complete(*args, **kwargs)
            monkeypatch.setattr(Path, "read_bytes", fail_config_read)
            return result

        core["complete_pin_files"] = confirm_then_fail_reads
        return core

    monkeypatch.setattr(successor, "_core", core_loader)
    result = successor.main(["--apply", "--plan-sha256", fixture.plan_sha])
    receipt, output = _read_stdout_receipt(capsys)
    assert result == 1
    assert fixture.replace_calls == [(fixture.pending, fixture.config)]
    assert receipt["core_completion_returned"] is True
    assert receipt["configuration_write_observed"] is True
    assert receipt["post_core_config_verification"] == "UNKNOWN"
    assert receipt["final_config_verification"] == "UNKNOWN"
    assert receipt["error_type"] == "OSError"
    assert receipt["retry"] is False
    assert "SECRET" not in output
