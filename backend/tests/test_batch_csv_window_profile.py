from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from chrom_platform.artifact_core import scanner_defender as scanner_module
from chrom_platform.artifact_core.batch_csv_window import _build_reviewed_profile
from chrom_platform.artifact_core.scanner_defender import (
    DefenderCommandScanner,
    DefenderStatus,
    ProcessResult,
)


def _status(exe_sha256: str) -> DefenderStatus:
    return DefenderStatus(
        product_version="4.18.25080.5",
        engine_version="1.1.26090.9",
        signature_version="1.461.143.0",
        signature_updated_at_utc=datetime(2026, 10, 21, tzinfo=timezone.utc),
        antivirus_enabled=True,
        authenticode_status="Valid",
        signer_subject="CN=Microsoft Windows, O=Microsoft Corporation, L=Redmond, S=Washington, C=US",
        exe_sha256=exe_sha256,
    )


def test_csv_profile_requires_nonempty_signature_but_does_not_pin_version() -> None:
    profile = _build_reviewed_profile()
    status = _status("a" * 64)
    assert profile.allowed_signature_versions is None
    assert scanner_module._profile_matches_status(profile, status, allow_signature_updates=True)
    assert scanner_module._profile_matches_status(
        profile,
        replace(status, signature_version="1.461.144.0"),
        allow_signature_updates=True,
    )
    assert not scanner_module._profile_matches_status(
        profile,
        replace(status, signature_version=""),
        allow_signature_updates=True,
    )


def test_default_profile_mode_still_requires_exact_signature() -> None:
    profile = replace(_build_reviewed_profile(), allowed_signature_versions=frozenset({"1.461.143.0"}))
    status = _status("a" * 64)
    assert scanner_module._profile_matches_status(profile, status)
    assert not scanner_module._profile_matches_status(
        profile,
        replace(status, signature_version="1.461.144.0"),
    )


def test_default_scanner_rejects_signature_change_during_scan(tmp_path: Path, monkeypatch) -> None:
    exe = tmp_path / "MpCmdRun.exe"
    exe.write_bytes(b"reviewed exe")
    monkeypatch.setattr(scanner_module, "DEFENDER_EXE", exe)
    expected_exe_sha = hashlib.sha256(exe.read_bytes()).hexdigest()
    path = tmp_path / "input.csv"
    before = _status(expected_exe_sha)
    after = replace(before, signature_version="1.461.144.0")
    statuses = iter((before, after))
    clean = (
        b"Scan starting...\r\n"
        b"Scan finished.\r\n"
        + b"Scanning " + str(path).encode("ascii") + b" found no threats.\r\n"
    )
    scanner = _scanner(
        expected_exe_sha,
        frozenset({("a" * 64, 10)}),
        lambda *_a, **_k: ProcessResult(0, clean),
        status_reader=lambda: next(statuses),
        profile=replace(
            _build_reviewed_profile(),
            allowed_signature_versions=frozenset({"1.461.143.0"}),
        ),
        allow_signature_updates=False,
    )

    outcome = _scan(scanner, path, "a" * 64, 10)
    assert outcome.status == "FAILED"
    assert outcome.evidence.error_code == "DEFENDER_STATUS_CHANGED"
    assert outcome.evidence.signature_version_after is None


def _scanner(
    exe_sha256: str,
    allowed_inputs: frozenset[tuple[str, int]],
    runner,
    *,
    status_reader=None,
    profile=None,
    allow_signature_updates: bool = True,
) -> DefenderCommandScanner:
    return DefenderCommandScanner(
        runner=runner,
        status_reader=status_reader or (lambda: _status(exe_sha256)),
        reviewed_profile=profile or _build_reviewed_profile(),
        allowed_inputs=allowed_inputs,
        expected_exe_sha256=exe_sha256,
        allow_signature_updates=allow_signature_updates,
    )


def _scan(scanner: DefenderCommandScanner, path: Path, input_sha256: str, size: int):
    return scanner.scan(
        path=path,
        artifact_id="artifact-a",
        input_sha256=input_sha256,
        input_size_bytes=size,
        scan_attempt_id="attempt-a",
    )


def test_reviewed_scanner_rejects_non_allowlisted_input_before_launch(tmp_path: Path, monkeypatch) -> None:
    exe = tmp_path / "MpCmdRun.exe"
    exe.write_bytes(b"reviewed exe")
    monkeypatch.setattr(scanner_module, "DEFENDER_EXE", exe)
    expected_exe_sha = hashlib.sha256(exe.read_bytes()).hexdigest()
    launched = False

    def runner(*_args, **_kwargs):
        nonlocal launched
        launched = True
        return ProcessResult(0, b"")

    allowed = frozenset({("a" * 64, 10)})
    scanner = _scanner(expected_exe_sha, allowed, runner)
    outcome = _scan(scanner, tmp_path / "input.csv", "b" * 64, 10)
    assert outcome.status == "FAILED"
    assert outcome.evidence.error_code == "CSV_INPUT_NOT_ALLOWLISTED"
    assert not launched


def test_reviewed_scanner_checks_exe_before_launch(tmp_path: Path, monkeypatch) -> None:
    exe = tmp_path / "MpCmdRun.exe"
    exe.write_bytes(b"reviewed exe")
    monkeypatch.setattr(scanner_module, "DEFENDER_EXE", exe)
    expected_exe_sha = hashlib.sha256(exe.read_bytes()).hexdigest()
    allowed = frozenset({("a" * 64, 10)})
    launched = False

    exe.write_bytes(b"drifted before launch")

    def runner(*_args, **_kwargs):
        nonlocal launched
        launched = True
        return ProcessResult(0, b"")

    scanner = _scanner(expected_exe_sha, allowed, runner)
    outcome = _scan(scanner, tmp_path / "input.csv", "a" * 64, 10)
    assert not launched
    assert outcome.status == "FAILED"
    assert outcome.evidence.error_code == "DEFENDER_EXE_IDENTITY_MISMATCH"


def test_reviewed_scanner_checks_exe_after_launch(tmp_path: Path, monkeypatch) -> None:
    exe = tmp_path / "MpCmdRun.exe"
    exe.write_bytes(b"reviewed exe")
    monkeypatch.setattr(scanner_module, "DEFENDER_EXE", exe)
    expected_exe_sha = hashlib.sha256(exe.read_bytes()).hexdigest()
    allowed = frozenset({("a" * 64, 10)})
    launched = False

    def runner(*_args, **_kwargs):
        nonlocal launched
        launched = True
        exe.write_bytes(b"drifted exe")
        return ProcessResult(0, b"Scan starting...\r\nScan finished.\r\nScanning ")

    scanner = _scanner(expected_exe_sha, allowed, runner)
    outcome = _scan(scanner, tmp_path / "input.csv", "a" * 64, 10)
    assert launched
    assert outcome.status == "FAILED"
    assert outcome.evidence.error_code == "DEFENDER_EXE_CHANGED"
    assert outcome.evidence.exe_sha256 == expected_exe_sha


def test_reviewed_scanner_passes_only_exact_clean_text_and_exit_code(tmp_path: Path, monkeypatch) -> None:
    exe = tmp_path / "MpCmdRun.exe"
    exe.write_bytes(b"reviewed exe")
    monkeypatch.setattr(scanner_module, "DEFENDER_EXE", exe)
    expected_exe_sha = hashlib.sha256(exe.read_bytes()).hexdigest()
    path = tmp_path / "input.csv"
    allowed = frozenset({("a" * 64, 10)})

    exact_clean = (
        b"Scan starting...\r\n"
        b"Scan finished.\r\n"
        + b"Scanning " + str(path).encode("ascii") + b" found no threats.\r\n"
    )
    clean_scanner = _scanner(expected_exe_sha, allowed, lambda *_a, **_k: ProcessResult(0, exact_clean))
    clean = _scan(clean_scanner, path, "a" * 64, 10)
    assert clean.status == "PASSED"
    assert clean.evidence.verdict == "CLEAN"
    assert clean.evidence.exe_sha256 == expected_exe_sha

    unknown_scanner = _scanner(
        expected_exe_sha, allowed,
        lambda *_a, **_k: ProcessResult(0, exact_clean + b"extra output"),
    )
    unknown = _scan(unknown_scanner, path, "a" * 64, 10)
    assert unknown.status == "FAILED"
    assert unknown.evidence.error_code == "SCAN_RESULT_UNPARSEABLE"
    assert unknown.evidence.verdict == "INDETERMINATE"


def test_csv_scanner_accepts_signature_update_and_records_both_statuses(tmp_path: Path, monkeypatch) -> None:
    exe = tmp_path / "MpCmdRun.exe"
    exe.write_bytes(b"reviewed exe")
    monkeypatch.setattr(scanner_module, "DEFENDER_EXE", exe)
    expected_exe_sha = hashlib.sha256(exe.read_bytes()).hexdigest()
    path = tmp_path / "input.csv"
    allowed = frozenset({("a" * 64, 10)})
    before = replace(
        _status(expected_exe_sha),
        signature_version="1.461.140.0",
        signature_updated_at_utc=datetime(2026, 10, 20, tzinfo=timezone.utc),
    )
    after = replace(
        before,
        signature_version="1.461.144.0",
        signature_updated_at_utc=datetime(2026, 10, 22, tzinfo=timezone.utc),
    )
    statuses = iter((before, after))
    clean = (
        b"Scan starting...\r\n"
        b"Scan finished.\r\n"
        + b"Scanning " + str(path).encode("ascii") + b" found no threats.\r\n"
    )
    scanner = _scanner(
        expected_exe_sha,
        allowed,
        lambda *_a, **_k: ProcessResult(0, clean),
        status_reader=lambda: next(statuses),
        allow_signature_updates=True,
    )

    outcome = _scan(scanner, path, "a" * 64, 10)
    assert outcome.status == "PASSED"
    assert outcome.evidence.signature_version == "1.461.140.0"
    assert outcome.evidence.signature_updated_at_utc == before.signature_updated_at_utc
    assert outcome.evidence.signature_version_after == "1.461.144.0"
    assert outcome.evidence.signature_updated_at_utc_after == after.signature_updated_at_utc
    from chrom_platform.artifact_core.validation_worker import _evidence_payload
    persisted = _evidence_payload(outcome.evidence)
    assert persisted["signature_version_after"] == "1.461.144.0"
    assert persisted["signature_updated_at_utc_after"] == after.signature_updated_at_utc.isoformat()


def test_csv_scanner_still_rejects_wrong_identity_and_invalid_status(tmp_path: Path, monkeypatch) -> None:
    exe = tmp_path / "MpCmdRun.exe"
    exe.write_bytes(b"reviewed exe")
    monkeypatch.setattr(scanner_module, "DEFENDER_EXE", exe)
    expected_exe_sha = hashlib.sha256(exe.read_bytes()).hexdigest()
    allowed = frozenset({("a" * 64, 10)})

    cases = (
        (replace(_status(expected_exe_sha), exe_sha256="b" * 64), "DEFENDER_PROFILE_UNSUPPORTED"),
        (replace(_status(expected_exe_sha), engine_version="1.1.26090.8"), "DEFENDER_PROFILE_UNSUPPORTED"),
        (replace(_status(expected_exe_sha), signature_version=""), "DEFENDER_STATUS_UNTRUSTED"),
    )
    for status, expected_error in cases:
        launched = False

        def runner(*_a, **_k):
            nonlocal launched
            launched = True
            return ProcessResult(0, b"")

        scanner = _scanner(
            expected_exe_sha,
            allowed,
            runner,
            status_reader=lambda status=status: status,
            allow_signature_updates=True,
        )
        outcome = _scan(scanner, tmp_path / "input.csv", "a" * 64, 10)
        assert not launched
        assert outcome.status == "FAILED"
        assert outcome.evidence.error_code == expected_error


def test_csv_scanner_rejects_non_signature_status_change(tmp_path: Path, monkeypatch) -> None:
    exe = tmp_path / "MpCmdRun.exe"
    exe.write_bytes(b"reviewed exe")
    monkeypatch.setattr(scanner_module, "DEFENDER_EXE", exe)
    expected_exe_sha = hashlib.sha256(exe.read_bytes()).hexdigest()
    path = tmp_path / "input.csv"
    before = _status(expected_exe_sha)
    after = replace(before, antivirus_enabled=False, signature_version="1.461.144.0")
    statuses = iter((before, after))
    clean = (
        b"Scan starting...\r\n"
        b"Scan finished.\r\n"
        + b"Scanning " + str(path).encode("ascii") + b" found no threats.\r\n"
    )
    scanner = _scanner(
        expected_exe_sha,
        frozenset({("a" * 64, 10)}),
        lambda *_a, **_k: ProcessResult(0, clean),
        status_reader=lambda: next(statuses),
        allow_signature_updates=True,
    )

    outcome = _scan(scanner, path, "a" * 64, 10)
    assert outcome.status == "FAILED"
    assert outcome.evidence.error_code == "DEFENDER_STATUS_CHANGED"
    assert outcome.evidence.signature_version_after == "1.461.144.0"
