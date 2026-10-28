from __future__ import annotations

import hashlib
import json
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

import chrom_platform.artifact_core.data_csv_window as csv_window
import chrom_platform.artifact_core.dataset_csv_elevated_runner as elevated
import chrom_platform.artifact_core.scanner_defender as scanner
from chrom_platform.artifact_core.defender_profile_candidate import (
    _classify_fixed_clean,
    build_defender_clean_candidate,
)
from chrom_platform.artifact_core.scanner_defender import (
    DefenderStatus,
    ProcessResult,
    _ResultProfile,
)


NEW_ENGINE = "1.1.26100.4"
OLD_ENGINE = "1.1.26090.9"
SIGNER = "CN=Microsoft Windows, O=Microsoft Corporation, L=Redmond, S=Washington, C=US"


def _status(exe_sha256: str, *, engine: str = NEW_ENGINE, signature: str = "1.461.143.0") -> DefenderStatus:
    return DefenderStatus(
        product_version="4.18.25080.5",
        engine_version=engine,
        signature_version=signature,
        signature_updated_at_utc=datetime(2026, 10, 28, tzinfo=timezone.utc),
        antivirus_enabled=True,
        authenticode_status="Valid",
        signer_subject=SIGNER,
        exe_sha256=exe_sha256,
    )


def _csv_profile() -> _ResultProfile:
    return _ResultProfile(
        profile_id=csv_window.DATASET_SCAN_PROFILE_ID,
        product_version=csv_window.DEFENDER_PRODUCT_VERSION,
        clean_exit_code=0,
        detection_exit_code=None,
        classify=_classify_fixed_clean,
        expected_engine_version=csv_window.DEFENDER_ENGINE_VERSION,
        expected_signer_subject=csv_window.DEFENDER_SIGNER_SUBJECT,
        allowed_signature_versions=None,
    )


def _scanner(tmp_path: Path, statuses, result: ProcessResult, *, launched=None) -> scanner.DefenderCommandScanner:
    exe = tmp_path / "MpCmdRun.exe"
    exe.write_bytes(b"synthetic defender executable")
    exe_sha = hashlib.sha256(exe.read_bytes()).hexdigest()
    path = tmp_path / "fixed.csv"
    allowed = frozenset({("a" * 64, 10)})
    def runner(*_args, **_kwargs):
        if launched is not None:
            launched.append(True)
        return result
    return scanner.DefenderCommandScanner(
        runner=runner,
        status_reader=lambda: next(statuses),
        reviewed_profile=_csv_profile(),
        allowed_inputs=allowed,
        expected_exe_sha256=exe_sha,
        allow_signature_updates=True,
    )


def _clean_output(path: Path) -> bytes:
    return (
        b"Scan starting...\r\n"
        b"Scan finished.\r\n"
        + b"Scanning " + str(path).encode("ascii") + b" found no threats.\r\n"
    )


def test_csv_window_and_elevated_helper_share_exact_new_engine_binding(monkeypatch, tmp_path: Path) -> None:
    runtime_root = tmp_path / "local-app-data"
    expected_runtime = runtime_root / "ChromatographyPlatform" / "data-accept-20261026"
    ready_file = expected_runtime / "state" / "worker-ready.json"
    expected_runtime.mkdir(parents=True)
    runtime = {
        "LOCALAPPDATA": str(runtime_root),
        "ARTIFACT_ROOT": str(expected_runtime / "artifacts"),
        "CHROM_BUILD_MANIFEST": str(csv_window.EXPECTED_MANIFEST_PATH),
        "CHROM_ACCEPTANCE_DATABASE_SCHEMA": "chrom_data_accept_20261026",
        "CHROM_DEFAULT_PROJECT_ID": "project_data_accept_20261026",
        "CHROM_PROJECT_IDS": "project_data_accept_20261026",
        "WORKER_QUEUE": "chrom-platform-data-accept-20261026",
        "API_PORT": "9012",
        "FRONTEND_PORT": "5174",
        "WORKER_READY_FILE": str(ready_file),
        "DATABASE_URL": "mysql+asyncmy://chrom_data_accept_runtime:fixture@127.0.0.1:3306/chrom_data_accept_20261026",
        "SYNC_DATABASE_URL": "mysql+pymysql://chrom_data_accept_runtime:fixture@127.0.0.1:3306/chrom_data_accept_20261026",
        "REDIS_URL": "redis://127.0.0.1:6379",
    }
    (expected_runtime / "runtime.json").write_text(json.dumps(runtime), encoding="utf-8")
    monkeypatch.setattr(csv_window, "EXPECTED_LOCALAPPDATA", runtime_root)
    monkeypatch.setattr(csv_window, "EXPECTED_RUNTIME_ROOT", expected_runtime)

    window = csv_window.build_data_csv_window(
        csv_window.BUILD_ID,
        csv_window.BUILD_MANIFEST_SHA256,
        ("datasets",),
        manifest_path=csv_window.EXPECTED_MANIFEST_PATH,
        environ=runtime,
    )

    assert window is not None
    profile = window.scanner._scanner._reviewed_profile
    assert csv_window.DEFENDER_ENGINE_VERSION == elevated.DEFENDER_ENGINE_VERSION == NEW_ENGINE
    assert profile.expected_engine_version == elevated.DEFENDER_ENGINE_VERSION
    assert profile.product_version == "4.18.25080.5"
    assert csv_window.DEFENDER_EXE_SHA256 == elevated.DEFENDER_EXE_SHA256
    assert window.scanner._scanner._allow_signature_updates is True


def test_csv_scanner_accepts_exact_clean_result_with_new_engine_and_records_signature_update(tmp_path: Path, monkeypatch) -> None:
    exe = tmp_path / "MpCmdRun.exe"
    exe.write_bytes(b"synthetic defender executable")
    exe_sha = hashlib.sha256(exe.read_bytes()).hexdigest()
    monkeypatch.setattr(scanner, "DEFENDER_EXE", exe)
    path = tmp_path / "fixed.csv"
    before = _status(exe_sha, signature="1.500.1.0")
    after = replace(before, signature_version="1.500.2.0", signature_updated_at_utc=datetime(2026, 10, 29, tzinfo=timezone.utc))
    client = scanner.DefenderCommandScanner(
        runner=lambda *_args, **_kwargs: ProcessResult(0, _clean_output(path)),
        status_reader=iter((before, after)).__next__,
        reviewed_profile=_csv_profile(),
        allowed_inputs=frozenset({("a" * 64, 10)}),
        expected_exe_sha256=exe_sha,
        allow_signature_updates=True,
    )

    outcome = client.scan(path=path, artifact_id="fake-artifact", input_sha256="a" * 64,
                          input_size_bytes=10, scan_attempt_id="fake-attempt")

    assert outcome.status == "PASSED"
    assert outcome.evidence.verdict == "CLEAN"
    assert outcome.evidence.engine_version == NEW_ENGINE
    assert outcome.evidence.signature_version == "1.500.1.0"
    assert outcome.evidence.signature_version_after == "1.500.2.0"
    assert outcome.evidence.signature_updated_at_utc_after == after.signature_updated_at_utc


@pytest.mark.parametrize("initial_engine", [OLD_ENGINE, "1.1.99999.9"])
def test_csv_scanner_rejects_old_or_unknown_engine_before_launch(tmp_path: Path, monkeypatch, initial_engine: str) -> None:
    exe = tmp_path / "MpCmdRun.exe"
    exe.write_bytes(b"synthetic defender executable")
    exe_sha = hashlib.sha256(exe.read_bytes()).hexdigest()
    monkeypatch.setattr(scanner, "DEFENDER_EXE", exe)
    launched: list[bool] = []
    status = _status(exe_sha, engine=initial_engine)
    client = _scanner(tmp_path, iter((status,)), ProcessResult(0, b""), launched=launched)

    outcome = client.scan(path=tmp_path / "fixed.csv", artifact_id="fake-artifact",
                          input_sha256="a" * 64, input_size_bytes=10, scan_attempt_id="fake-attempt")

    assert outcome.status == "FAILED"
    assert outcome.evidence.error_code == "DEFENDER_PROFILE_UNSUPPORTED"
    assert launched == []


def test_csv_scanner_rejects_engine_change_during_scan(tmp_path: Path, monkeypatch) -> None:
    exe = tmp_path / "MpCmdRun.exe"
    exe.write_bytes(b"synthetic defender executable")
    exe_sha = hashlib.sha256(exe.read_bytes()).hexdigest()
    monkeypatch.setattr(scanner, "DEFENDER_EXE", exe)
    before = _status(exe_sha)
    after = replace(before, engine_version=OLD_ENGINE)
    launched: list[bool] = []
    client = scanner.DefenderCommandScanner(
        runner=lambda *_args, **_kwargs: launched.append(True) or ProcessResult(0, _clean_output(tmp_path / "fixed.csv")),
        status_reader=iter((before, after)).__next__,
        reviewed_profile=_csv_profile(),
        allowed_inputs=frozenset({("a" * 64, 10)}),
        expected_exe_sha256=exe_sha,
        allow_signature_updates=True,
    )

    outcome = client.scan(path=tmp_path / "fixed.csv", artifact_id="fake-artifact",
                          input_sha256="a" * 64, input_size_bytes=10, scan_attempt_id="fake-attempt")

    assert launched == [True]
    assert outcome.status == "FAILED"
    assert outcome.evidence.error_code == "DEFENDER_STATUS_CHANGED"


@pytest.mark.parametrize(
    ("result", "expected_error"),
    [
        ("timeout", "SCAN_TIMEOUT_COMPLETION_UNKNOWN"),
        ("truncated", "SCAN_OUTPUT_TRUNCATED"),
        ("reader_incomplete", "OUTPUT_DRAIN_INCOMPLETE"),
        ("extra_output", "SCAN_RESULT_UNPARSEABLE"),
        ("nonzero_exit", "SCAN_RESULT_CONFLICT"),
    ],
)
def test_csv_scanner_fails_closed_on_uncertain_or_nonexact_result(tmp_path: Path, monkeypatch, result: str, expected_error: str) -> None:
    exe = tmp_path / "MpCmdRun.exe"
    exe.write_bytes(b"synthetic defender executable")
    exe_sha = hashlib.sha256(exe.read_bytes()).hexdigest()
    monkeypatch.setattr(scanner, "DEFENDER_EXE", exe)
    path = tmp_path / "fixed.csv"
    exact = _clean_output(path)
    process_result = {
        "timeout": ProcessResult(None, exact, timed_out=True),
        "truncated": ProcessResult(0, exact, output_truncated=True),
        "reader_incomplete": ProcessResult(None, exact, reader_incomplete=True),
        "extra_output": ProcessResult(0, exact + b"additional output\r\n"),
        "nonzero_exit": ProcessResult(2, exact),
    }[result]
    before = _status(exe_sha)
    client = scanner.DefenderCommandScanner(
        runner=lambda *_args, **_kwargs: process_result,
        status_reader=iter((before, before)).__next__,
        reviewed_profile=_csv_profile(),
        allowed_inputs=frozenset({("a" * 64, 10)}),
        expected_exe_sha256=exe_sha,
        allow_signature_updates=True,
    )

    outcome = client.scan(path=path, artifact_id="fake-artifact", input_sha256="a" * 64,
                          input_size_bytes=10, scan_attempt_id="fake-attempt")

    assert outcome.status == "FAILED"
    assert outcome.evidence.verdict == "INDETERMINATE"
    assert outcome.evidence.error_code == expected_error


@pytest.mark.parametrize(
    ("engines", "expected_error", "expected_calls"),
    [
        ((NEW_ENGINE, NEW_ENGINE), None, 1),
        ((OLD_ENGINE, NEW_ENGINE), "DEFENDER_IDENTITY_INVALID", 0),
        ((NEW_ENGINE, OLD_ENGINE), "DEFENDER_IDENTITY_CHANGED", 1),
    ],
)
def test_helper_locked_accepts_only_unchanged_new_engine_and_creates_no_side_files(
    monkeypatch, tmp_path: Path, engines: tuple[str, str], expected_error: str | None, expected_calls: int,
) -> None:
    import chrom_platform.artifact_core.scanner_defender as scanner_defender

    source = tmp_path / "fixed.csv"
    source.write_bytes(b"fixture only; never passed to a native scanner")
    identity = (7, 11)
    exe_sha = elevated.DEFENDER_EXE_SHA256
    statuses = iter((_status(exe_sha, engine=engines[0]), _status(exe_sha, engine=engines[1])))
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(scanner_defender, "_read_status", lambda: next(statuses))
    monkeypatch.setattr(scanner_defender, "_valid_status", lambda _status: True)
    monkeypatch.setattr(scanner_defender, "_sha256_file", lambda _path: exe_sha)
    monkeypatch.setattr(elevated, "_digest", lambda _path: (elevated.INPUT_SIZE_BYTES, elevated.INPUT_SHA256))
    monkeypatch.setattr(elevated, "_file_identity", lambda _path: identity)
    monkeypatch.setattr(elevated, "_run_bounded_process", lambda argv, **_kwargs: calls.append(tuple(argv)) or ProcessResult(0, b"fixture output"))
    request = {
        "nonce": "a" * 32,
        "profile_id": elevated.PROFILE_ID,
        "attempt_id": "fake-attempt",
        "artifact_id": "fake-artifact",
        "deadline_monotonic": time.monotonic() + 30,
    }

    if expected_error:
        with pytest.raises(ValueError, match=expected_error):
            elevated._helper_result_locked(request, "b" * 64, 30, source, identity)
    else:
        result = elevated._helper_result_locked(request, "b" * 64, 30, source, identity)
        assert result["returncode"] == 0
        assert calls[0][1:] == ("-Scan", "-ScanType", "3", "-File", str(source), "-DisableRemediation")

    assert len(calls) == expected_calls
    assert sorted(path.name for path in tmp_path.iterdir()) == ["fixed.csv"]


def test_model_profile_remains_bound_to_old_engine_and_pinned_signatures() -> None:
    model_profile = build_defender_clean_candidate()
    exe_sha = "a" * 64
    assert model_profile.expected_engine_version == OLD_ENGINE
    assert scanner._profile_matches_status(model_profile, _status(exe_sha)) is False
    changed_signature = replace(_status(exe_sha, engine=OLD_ENGINE), signature_version="1.461.144.0")
    assert scanner._profile_matches_status(model_profile, changed_signature) is False
