from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(r"D:\Jupyter\Chromatography_agent")
CANDIDATE_PATH = ROOT / "testing/runs/2026-10-28-data-import-audit03/audit03-pin-binding-diagnostic-candidate.py"
SPEC = importlib.util.spec_from_file_location("audit03_pin_diagnostic_candidate", CANDIDATE_PATH)
assert SPEC is not None and SPEC.loader is not None
candidate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(candidate)


@pytest.mark.parametrize("config", [
    candidate.RUNTIME / "runtime.json",
    Path(r"C:\Users\13459\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0\LocalCache\Local\ChromatographyPlatform\data-accept-20261026\runtime.json"),
    candidate.RUNTIME.parent / "assistant-accept-20261006/runtime.json",
])
def test_rejected_runtime_paths_skip_even_final_observation(monkeypatch, config):
    calls = []

    def forbidden(*_args, **_kwargs):
        calls.append(True)
        raise AssertionError("Rejected paths must not reach any operation")

    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(candidate, "_core", forbidden)
    result = candidate.diagnose_synthetic_pin_files(
        config, config.with_name("runtime.json.pending"),
        formal_sha256="a" * 64, pending_sha256="b" * 64,
        copy_acl=forbidden, idle_check=forbidden, replace_file=forbidden,
        observe_replacement=forbidden,
    )
    assert result["outcome"] == "FAILED"
    assert result["reason_code"] == "PROTECTED_RUNTIME_PATH_REJECTED"
    assert result["configuration_write_observed"] is False
    assert calls == []


def _pin_pair():
    formal = (json.dumps({candidate.KEY: candidate.OLD_PIN, "unchanged": {"v": 1}}, separators=(",", ":")) + "\n").encode()
    pending = candidate._candidate_config(formal)
    return formal, pending


def _valid_acl_result():
    return SimpleNamespace(
        returncode=0,
        stdout=json.dumps({
            "owner_sid": "S-1-test",
            "source_owner_sid": "S-1-test",
            "source_sddl": "D:P(A;;FA;;;OW)",
            "target_sddl": "D:P(A;;FA;;;OW)",
            "source_protected": True,
            "target_protected": True,
        }),
        stderr="",
    )


def _blocked_replace(*_args):
    raise AssertionError("fake replace should not be reached")


def _configure_tmp_acl_paths(monkeypatch, tmp_path):
    config = tmp_path / "runtime.json"
    pending = tmp_path / "runtime.json.source-pin-audit03-be10.pending"
    backup = tmp_path / "runtime.json.audit03-be10-before.json"
    monkeypatch.setattr(candidate, "CONFIG", config)
    monkeypatch.setattr(candidate, "PENDING", pending)
    monkeypatch.setattr(candidate, "BACKUP", backup)
    return config, pending


def test_managed_status_invalid_json_is_fixed_and_keeps_cause(monkeypatch, tmp_path):
    start = tmp_path / "start-platform.ps1"
    pwsh = tmp_path / "pwsh.exe"
    start.write_text("fixture", encoding="utf-8")
    pwsh.write_text("fixture", encoding="utf-8")
    monkeypatch.setattr(candidate, "START_PLATFORM", start)
    monkeypatch.setattr(candidate, "PWSH", pwsh)
    trace = {"stage": ""}

    def fake_run(*_args, **_kwargs):
        return SimpleNamespace(returncode=0, stdout="not-json SECRET_PATH", stderr="SECRET_STDERR")

    with pytest.raises(RuntimeError) as caught:
        candidate._require_stopped(trace=trace, run=fake_run)

    assert str(caught.value) == "MANAGED_STATUS_INVALID"
    assert type(caught.value.__cause__).__name__ == "JSONDecodeError"
    assert caught.value.diagnostic_stage == "MANAGED_STATUS_JSON"
    assert trace["stage"] == "MANAGED_STATUS_JSON"
    assert "SECRET" not in repr(caught.value.__dict__)


def test_managed_status_success_requires_all_profiles_and_empty_ports(monkeypatch, tmp_path):
    start = tmp_path / "start-platform.ps1"
    pwsh = tmp_path / "pwsh.exe"
    start.write_text("fixture", encoding="utf-8")
    pwsh.write_text("fixture", encoding="utf-8")
    monkeypatch.setattr(candidate, "START_PLATFORM", start)
    monkeypatch.setattr(candidate, "PWSH", pwsh)
    calls = []
    status = json.dumps({"selected": [{"service": svc, "status": "STOPPED"} for svc in ("api", "worker", "frontend")], "other": []})

    def fake_run(args, **_kwargs):
        calls.append(args)
        out = "0" if "-Command" in args else status
        return SimpleNamespace(returncode=0, stdout=out, stderr="SECRET_STDERR")

    trace = {"stage": ""}
    candidate._require_stopped(trace=trace, run=fake_run)

    assert len(calls) == 3
    assert trace["stage"] == "IDLE_GATE_SUCCEEDED"


@pytest.mark.parametrize(
    ("result", "expected_reason"),
    [
        (SimpleNamespace(returncode=5, stdout="SECRET_STDOUT", stderr="SECRET_STDERR"), "PIN_ACL_COPY_OR_VERIFY_FAILED"),
        (SimpleNamespace(returncode=0, stdout=json.dumps({"source_sddl": "D:P", "target_sddl": "D:P", "source_protected": True, "target_protected": False, "owner_sid": "S-1-test"}), stderr="SECRET_STDERR"), "PIN_ACL_METADATA_INVALID"),
        (SimpleNamespace(returncode=0, stdout="[]", stderr="SECRET_STDERR"), "PIN_ACL_METADATA_INVALID"),
    ],
)
def test_acl_subprocess_and_metadata_failures_are_distinct(monkeypatch, tmp_path, result, expected_reason):
    config, pending = _configure_tmp_acl_paths(monkeypatch, tmp_path)
    config.write_bytes(b"synthetic")
    pending.write_bytes(b"synthetic")
    calls = []

    def fake_run(*_args, **_kwargs):
        calls.append(True)
        return result

    with pytest.raises(RuntimeError) as caught:
        candidate._copy_acl(config, pending, candidate._core(), run=fake_run)

    assert calls == [True]
    assert caught.value.reason_code == expected_reason
    assert caught.value.diagnostic_stage == "SECOND_ACL_COPY"
    assert "SECRET" not in str(caught.value)
    assert "SECRET" not in repr(caught.value.__dict__)


def test_pinned_core_second_acl_byte_cas_rejects_changed_pending(monkeypatch, tmp_path):
    formal, pending_bytes = _pin_pair()
    config, pending = _configure_tmp_acl_paths(monkeypatch, tmp_path)
    config.write_bytes(formal)
    pending.write_bytes(pending_bytes)
    acl_calls = []
    core = candidate._core()

    def fake_acl(source, target):
        acl_calls.append((str(source), str(target)))
        metadata = candidate._copy_acl(source, target, core, run=lambda *_args, **_kwargs: _valid_acl_result())
        target.write_bytes(b"changed-by-fake-acl")
        return metadata

    trace = candidate.diagnose_synthetic_pin_files(
        config, pending,
        formal_sha256=hashlib.sha256(formal).hexdigest(),
        pending_sha256=hashlib.sha256(pending_bytes).hexdigest(),
        copy_acl=fake_acl,
        idle_check=lambda: pytest.fail("idle gate must not run after failed byte CAS"),
        replace_file=_blocked_replace,
        observe_replacement=lambda _config, _pending: False,
    )

    assert len(acl_calls) == 1
    assert trace["stage"] == "SECOND_ACL_COPY"
    assert trace["second_acl_copy_attempted"] is True
    assert trace["second_acl_copy_succeeded"] is True
    assert trace["before_replace_callback_entered"] is False
    assert trace["fresh_idle_check_attempted"] is False
    assert trace["pin_write_attempted"] is False
    assert trace["reason_code"] == "PIN_BYTES_CHANGED_DURING_ACL_COPY"
    assert trace["configuration_write_observed"] is False
    assert trace["outcome"] == "FAILED"


def test_before_replace_idle_failure_is_entered_but_not_write_attempted(tmp_path):
    formal, pending_bytes = _pin_pair()
    config = tmp_path / "runtime.json"
    pending = tmp_path / "runtime.json.source-pin-audit03-be10.pending"
    config.write_bytes(formal)
    pending.write_bytes(pending_bytes)

    def fake_acl(_source, _target):
        return {"protected": True, "owner_match": True}

    def fail_idle():
        try:
            raise ValueError("SECRET_PATH")
        except ValueError as cause:
            raise RuntimeError("SECRET_STDOUT") from cause

    trace = candidate.diagnose_synthetic_pin_files(
        config, pending,
        formal_sha256=hashlib.sha256(formal).hexdigest(),
        pending_sha256=hashlib.sha256(pending_bytes).hexdigest(),
        copy_acl=fake_acl,
        idle_check=fail_idle,
        replace_file=_blocked_replace,
        observe_replacement=lambda _config, _pending: False,
    )

    assert trace["stage"] == "FRESH_IDLE_CHECK"
    assert trace["before_replace_callback_entered"] is True
    assert trace["fresh_idle_check_attempted"] is True
    assert trace["fresh_idle_check_succeeded"] is False
    assert trace["pin_write_attempted"] is False
    assert trace["reason_code"] == "FRESH_IDLE_GATE_FAILED"
    assert trace["exception_types"] == ["RuntimeError", "RuntimeError", "ValueError"]
    assert "SECRET" not in json.dumps(trace)


def test_before_replace_preserves_actual_managed_status_failure(monkeypatch, tmp_path):
    formal, pending_bytes = _pin_pair()
    config = tmp_path / "runtime.json"
    pending = tmp_path / "runtime.json.source-pin-audit03-be10.pending"
    start = tmp_path / "start-platform.ps1"
    pwsh = tmp_path / "pwsh.exe"
    config.write_bytes(formal)
    pending.write_bytes(pending_bytes)
    start.write_text("fixture", encoding="utf-8")
    pwsh.write_text("fixture", encoding="utf-8")
    monkeypatch.setattr(candidate, "START_PLATFORM", start)
    monkeypatch.setattr(candidate, "PWSH", pwsh)
    calls = []

    def fake_run(*_args, **_kwargs):
        calls.append(True)
        return SimpleNamespace(returncode=0, stdout="invalid-json SECRET_PATH", stderr="SECRET_STDERR")

    trace = candidate.diagnose_synthetic_pin_files(
        config, pending,
        formal_sha256=hashlib.sha256(formal).hexdigest(),
        pending_sha256=hashlib.sha256(pending_bytes).hexdigest(),
        copy_acl=lambda _source, _target: {"protected": True, "owner_match": True},
        idle_check=lambda: candidate._require_stopped(run=fake_run),
        replace_file=_blocked_replace,
    )

    assert calls == [True]
    assert trace["stage"] == "FRESH_IDLE_CHECK"
    assert trace["before_replace_callback_entered"] is True
    assert trace["fresh_idle_check_attempted"] is True
    assert trace["fresh_idle_check_succeeded"] is False
    assert trace["pin_write_attempted"] is False
    assert trace["reason_code"] == "MANAGED_STATUS_INVALID"
    assert trace["exception_types"] == ["RuntimeError", "RuntimeError", "JSONDecodeError", "OTHER"]
    assert "SECRET" not in json.dumps(trace)


def test_success_requires_gates_and_uses_no_real_os_replace(monkeypatch, tmp_path):
    formal, pending_bytes = _pin_pair()
    config = tmp_path / "runtime.json"
    pending = tmp_path / "runtime.json.source-pin-audit03-be10.pending"
    config.write_bytes(formal)
    pending.write_bytes(pending_bytes)
    core = candidate._core()
    monkeypatch.setattr(candidate, "_core", lambda: core)
    monkeypatch.setattr(candidate, "CONFIG", config)
    monkeypatch.setattr(candidate, "PENDING", pending)
    replace_calls = []
    original_os_replace = os.replace

    def fake_replace(source, target):
        replace_calls.append((Path(source), Path(target)))
        target.write_bytes(Path(source).read_bytes())
        Path(source).unlink()

    idle_calls = []
    trace = candidate.diagnose_synthetic_pin_files(
        config, pending,
        formal_sha256=hashlib.sha256(formal).hexdigest(),
        pending_sha256=hashlib.sha256(pending_bytes).hexdigest(),
        copy_acl=lambda source, target: candidate._copy_acl(
            source, target, core, run=lambda *_args, **_kwargs: _valid_acl_result()
        ),
        idle_check=lambda: idle_calls.append(True),
        replace_file=fake_replace,
        observe_replacement=lambda cfg, _pending: cfg.read_bytes() == pending_bytes,
    )

    assert replace_calls == [(pending, config)]
    assert idle_calls == [True]
    assert trace["outcome"] == "SUCCEEDED"
    assert trace["reason_code"] == "NONE"
    assert trace["second_acl_copy_attempted"] is True
    assert trace["second_acl_copy_succeeded"] is True
    assert trace["before_replace_callback_entered"] is True
    assert trace["fresh_idle_check_attempted"] is True
    assert trace["fresh_idle_check_succeeded"] is True
    assert trace["pin_write_attempted"] is True
    assert trace["configuration_write_observed"] is True
    assert os.replace is original_os_replace


def test_final_false_observation_does_not_report_success(monkeypatch, tmp_path):
    formal, pending_bytes = _pin_pair()
    config = tmp_path / "runtime.json"
    pending = tmp_path / "runtime.json.source-pin-audit03-be10.pending"
    config.write_bytes(formal)
    pending.write_bytes(pending_bytes)
    core = candidate._core()
    monkeypatch.setattr(candidate, "_core", lambda: core)
    monkeypatch.setattr(candidate, "CONFIG", config)
    monkeypatch.setattr(candidate, "PENDING", pending)

    def fake_replace(source, target):
        target.write_bytes(Path(source).read_bytes())
        Path(source).unlink()

    trace = candidate.diagnose_synthetic_pin_files(
        config, pending,
        formal_sha256=hashlib.sha256(formal).hexdigest(),
        pending_sha256=hashlib.sha256(pending_bytes).hexdigest(),
        copy_acl=lambda source, target: candidate._copy_acl(
            source, target, core, run=lambda *_args, **_kwargs: _valid_acl_result()
        ),
        idle_check=lambda: None,
        replace_file=fake_replace,
        observe_replacement=lambda _config, _pending: False,
    )

    assert trace["pin_write_attempted"] is True
    assert trace["configuration_write_observed"] is False
    assert trace["outcome"] == "FAILED"
    assert trace["stage"] == "FINAL_OBSERVATION"
    assert trace["reason_code"] == "FINAL_CONFIG_SHA_MISMATCH"


def test_final_observation_error_cannot_mask_core_failure(tmp_path):
    formal, pending_bytes = _pin_pair()
    config = tmp_path / "runtime.json"
    pending = tmp_path / "runtime.json.source-pin-audit03-be10.pending"
    config.write_bytes(formal)
    pending.write_bytes(pending_bytes)

    def fail_idle():
        raise RuntimeError("CORE_CAUSE_SECRET")

    def fail_observation(_config, _pending):
        raise ValueError("OBSERVER_PATH_SECRET")

    trace = candidate.diagnose_synthetic_pin_files(
        config, pending,
        formal_sha256=hashlib.sha256(formal).hexdigest(),
        pending_sha256=hashlib.sha256(pending_bytes).hexdigest(),
        copy_acl=lambda _source, _target: {"protected": True, "owner_match": True},
        idle_check=fail_idle,
        replace_file=_blocked_replace,
        observe_replacement=fail_observation,
    )

    assert trace["outcome"] == "FAILED"
    assert trace["reason_code"] == "FRESH_IDLE_GATE_FAILED"
    assert trace["stage"] == "FRESH_IDLE_CHECK"
    assert trace["exception_types"] == ["RuntimeError", "RuntimeError"]
    assert "SECRET" not in json.dumps(trace)


def test_candidate_execution_entrypoints_are_disabled():
    assert candidate.main(["--apply", "--plan-sha256", "a" * 64]) == 2
    with pytest.raises(RuntimeError, match="^DIAGNOSTIC_ONLY_EXECUTION_DISABLED$"):
        candidate.preview()
    with pytest.raises(RuntimeError, match="^DIAGNOSTIC_ONLY_EXECUTION_DISABLED$"):
        candidate.apply("a" * 64)


def test_unknown_value_error_and_untrusted_stage_are_not_echoed(tmp_path):
    formal, pending_bytes = _pin_pair()
    config = tmp_path / "runtime.json"
    pending = tmp_path / "runtime.json.source-pin-audit03-be10.pending"
    config.write_bytes(formal)
    pending.write_bytes(pending_bytes)

    def unknown_acl(_source, _target):
        error = ValueError("SECRET_CONFIG_PATH")
        error.diagnostic_stage = "C:\\SECRET\\PATH"  # type: ignore[attr-defined]
        error.reason_code = "SECRET_REASON"  # type: ignore[attr-defined]
        raise error

    trace = candidate.diagnose_synthetic_pin_files(
        config, pending,
        formal_sha256=hashlib.sha256(formal).hexdigest(),
        pending_sha256=hashlib.sha256(pending_bytes).hexdigest(),
        copy_acl=unknown_acl,
        idle_check=lambda: pytest.fail("idle gate must not run after ACL error"),
        replace_file=_blocked_replace,
    )

    assert trace["stage"] == "SECOND_ACL_COPY"
    assert trace["reason_code"] == "UNCLASSIFIED_EXCEPTION"
    assert trace["exception_types"] == ["ValueError"]
    assert "SECRET" not in json.dumps(trace)
    assert candidate._reason_from_exception(ValueError("DUPLICATE_JSON_KEY")) == "DUPLICATE_JSON_KEY"
    assert candidate._reason_from_exception(ValueError("unrecognized token")) == "UNCLASSIFIED_EXCEPTION"


def test_invalid_diagnostic_stage_is_replaced_by_fixed_stage():
    with pytest.raises(RuntimeError) as caught:
        candidate._raise_diagnostic("D:\\SECRET\\PATH", "PIN_ACL_METADATA_INVALID")

    assert caught.value.diagnostic_stage == "DIAGNOSTIC_FAILURE"
    assert "SECRET" not in caught.value.diagnostic_stage


def test_protected_runtime_paths_are_rejected_before_reads(monkeypatch):
    def forbidden_read(_self):
        raise AssertionError("protected file must not be read")

    monkeypatch.setattr(Path, "read_bytes", forbidden_read)
    monkeypatch.setattr(candidate, "_core", lambda: pytest.fail("pinned core must not be loaded"))
    trace = candidate.diagnose_synthetic_pin_files(
        candidate.CONFIG,
        candidate.PENDING,
        formal_sha256="0" * 64,
        pending_sha256="1" * 64,
        copy_acl=lambda *_args: pytest.fail("ACL callback must not run"),
        idle_check=lambda: pytest.fail("idle callback must not run"),
        replace_file=_blocked_replace,
    )

    assert trace["outcome"] == "FAILED"
    assert trace["stage"] == "INITIAL_PIN_CAS"
    assert trace["reason_code"] == "PROTECTED_RUNTIME_PATH_REJECTED"


def test_resolved_protected_runtime_path_is_rejected_before_reads(monkeypatch, tmp_path):
    real_resolve = Path.resolve
    protected_config = candidate._PROTECTED_RUNTIME_ROOT / "runtime.json"
    config = tmp_path / "linked-runtime.json"
    pending = tmp_path / "runtime.json.source-pin-audit03-be10.pending"

    def resolved_path(self, *args, **kwargs):
        if self == config:
            return protected_config
        return real_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolved_path)
    monkeypatch.setattr(Path, "read_bytes", lambda _self: pytest.fail("path must be rejected before read"))
    monkeypatch.setattr(candidate, "_core", lambda: pytest.fail("pinned core must not be loaded"))
    trace = candidate.diagnose_synthetic_pin_files(
        config,
        pending,
        formal_sha256="0" * 64,
        pending_sha256="1" * 64,
        copy_acl=lambda *_args: pytest.fail("ACL callback must not run"),
        idle_check=lambda: pytest.fail("idle callback must not run"),
        replace_file=_blocked_replace,
    )

    assert trace["reason_code"] == "PROTECTED_RUNTIME_PATH_REJECTED"
    assert trace["outcome"] == "FAILED"


def test_diagnostic_replace_dependency_is_mandatory(tmp_path):
    with pytest.raises(TypeError):
        candidate.diagnose_synthetic_pin_files(
            tmp_path / "runtime.json",
            tmp_path / "runtime.json.source-pin-audit03-be10.pending",
            formal_sha256="0" * 64,
            pending_sha256="1" * 64,
            copy_acl=lambda *_args: None,
            idle_check=lambda: None,
        )
    with pytest.raises(TypeError):
        candidate._require_stopped()
    with pytest.raises(TypeError):
        candidate._copy_acl(tmp_path / "runtime.json", tmp_path / "pending.json", {})
