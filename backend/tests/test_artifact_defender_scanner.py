from __future__ import annotations

import io
import threading
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

import pytest

import chrom_platform.artifact_core.scanner_defender as defender


def _status(**overrides) -> defender.DefenderStatus:
    values = {
        "product_version": "test-build",
        "engine_version": "test-engine",
        "signature_version": "test-signatures",
        "signature_updated_at_utc": datetime(2026, 10, 13, tzinfo=timezone.utc),
        "antivirus_enabled": True,
        "authenticode_status": "Valid",
        "signer_subject": "CN=Microsoft Windows, O=Microsoft Corporation",
    }
    values.update(overrides)
    return defender.DefenderStatus(**values)


def _scan(scanner: defender.DefenderCommandScanner, tmp_path: Path) -> defender.ScanOutcome:
    return scanner.scan(
        path=(tmp_path / "artifact with spaces.bin").absolute(),
        artifact_id="artifact-1",
        input_sha256="a" * 64,
        input_size_bytes=12,
        scan_attempt_id="attempt-1",
    )


def _classify_record(output: bytes, requested_path: str):
    for verdict in ("CLEAN", "DETECTED"):
        if output == f"{verdict}|{requested_path}".encode("utf-8"):
            return defender.ResultClassification(verdict, requested_path)
    return None


def _approved_test_profile(monkeypatch, *, classify=_classify_record) -> None:
    profile = defender._ResultProfile(
        profile_id="test-only-profile",
        product_version="test-build",
        clean_exit_code=0,
        detection_exit_code=2,
        classify=classify,
        expected_engine_version="test-engine",
        expected_signer_subject="CN=Microsoft Windows, O=Microsoft Corporation",
        allowed_signature_versions=frozenset({"test-signatures"}),
    )
    monkeypatch.setattr(defender, "_APPROVED_PROFILES", {"test-build": profile})
    # This explicit test-only override does not change the production default.
    monkeypatch.setattr(defender, "_PROFILE_EVIDENCE_APPROVED", True)


def test_default_production_gate_fails_without_launching_scan(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    scanner = defender.DefenderCommandScanner(
        runner=lambda argv, **kwargs: calls.append(list(argv)) or defender.ProcessResult(0, b""),
        status_reader=_status,
    )

    result = _scan(scanner, tmp_path)

    assert result.status == "FAILED"
    assert result.evidence.verdict == "INDETERMINATE"
    assert result.evidence.error_code == "DEFENDER_PROFILE_UNSUPPORTED"
    assert calls == []


def test_matching_profile_still_fails_without_explicit_evidence_approval(
    monkeypatch, tmp_path: Path
) -> None:
    matching_profile = defender._ResultProfile(
        profile_id="matching-but-unapproved-test-profile",
        product_version="test-build",
        clean_exit_code=0,
        detection_exit_code=2,
        classify=_classify_record,
        expected_engine_version="test-engine",
        expected_signer_subject="CN=Microsoft Windows, O=Microsoft Corporation",
        allowed_signature_versions=frozenset({"test-signatures"}),
    )
    monkeypatch.setattr(defender, "_APPROVED_PROFILES", {"test-build": matching_profile})
    monkeypatch.setattr(defender, "_PROFILE_EVIDENCE_APPROVED", False)
    calls: list[list[str]] = []
    scanner = defender.DefenderCommandScanner(
        runner=lambda argv, **kwargs: calls.append(list(argv)) or defender.ProcessResult(0, b""),
        status_reader=_status,
    )

    result = _scan(scanner, tmp_path)

    assert result.status == "FAILED"
    assert result.evidence.error_code == "DEFENDER_PROFILE_UNSUPPORTED"
    assert result.evidence.verdict == "INDETERMINATE"
    assert calls == []


def test_fake_validated_profile_records_path_and_attempt_bound_clean(monkeypatch, tmp_path: Path) -> None:
    _approved_test_profile(monkeypatch)
    invocations: list[tuple[list[str], dict[str, object]]] = []

    def runner(argv, **kwargs):
        invocations.append((list(argv), kwargs))
        return defender.ProcessResult(0, f"CLEAN|{argv[5]}".encode("utf-8"))

    result = _scan(
        defender.DefenderCommandScanner(runner=runner, status_reader=_status), tmp_path
    )

    assert result.status == "PASSED"
    assert result.evidence.verdict == "CLEAN"
    assert result.evidence.artifact_id == "artifact-1"
    assert result.evidence.scan_attempt_id == "attempt-1"
    assert result.evidence.input_sha256 == "a" * 64
    assert result.evidence.input_size_bytes == 12
    assert result.evidence.product_version == "test-build"
    assert result.evidence.profile_id == "test-only-profile"
    assert len(invocations) == 1
    argv, kwargs = invocations[0]
    assert argv == [
        str(defender.DEFENDER_EXE),
        "-Scan",
        "-ScanType",
        "3",
        "-File",
        str((tmp_path / "artifact with spaces.bin").absolute()),
        "-DisableRemediation",
    ]
    assert kwargs == {
        "timeout": 120.0,
        "max_output_bytes": 65_536,
    }


@pytest.mark.parametrize(
    ("output_factory", "expected_error"),
    [
        (lambda path: b"", "SCAN_RESULT_UNPARSEABLE"),
        (lambda path: b"unrecognized output", "SCAN_RESULT_UNPARSEABLE"),
        (
            lambda path: f"CLEAN|{path}.other".encode("utf-8"),
            "SCAN_RESULT_UNPARSEABLE",
        ),
        (
            lambda path: b"PROFILE_RETURNED_MISMATCHED_PATH",
            "SCAN_RESULT_UNBOUND",
        ),
    ],
)
def test_exit_zero_without_exact_path_bound_clean_evidence_fails(
    monkeypatch, tmp_path: Path, output_factory, expected_error
) -> None:
    def classify(output, path):
        if isinstance(output, defender.ResultClassification):
            return output
        if output == b"PROFILE_RETURNED_MISMATCHED_PATH":
            return defender.ResultClassification("CLEAN", path + ".other")
        if output == f"CLEAN|{path}".encode("utf-8"):
            return defender.ResultClassification("CLEAN", path)
        return None

    _approved_test_profile(monkeypatch, classify=classify)
    scanner = defender.DefenderCommandScanner(
        runner=lambda argv, **kwargs: defender.ProcessResult(0, output_factory(argv[5])),
        status_reader=_status,
    )

    result = _scan(scanner, tmp_path)

    assert result.status == "FAILED"
    assert result.evidence.verdict == "INDETERMINATE"
    assert result.evidence.error_code == expected_error


def test_zero_exit_with_detection_is_conflicting_and_fails_closed(monkeypatch, tmp_path: Path) -> None:
    _approved_test_profile(monkeypatch)
    scanner = defender.DefenderCommandScanner(
        runner=lambda argv, **kwargs: defender.ProcessResult(0, f"DETECTED|{argv[5]}".encode()),
        status_reader=_status,
    )

    result = _scan(scanner, tmp_path)

    assert result.status == "FAILED"
    assert result.evidence.error_code == "SCAN_RESULT_CONFLICT"
    assert result.evidence.verdict == "INDETERMINATE"


def test_exact_path_bound_detection_rejects(monkeypatch, tmp_path: Path) -> None:
    _approved_test_profile(monkeypatch)
    scanner = defender.DefenderCommandScanner(
        runner=lambda argv, **kwargs: defender.ProcessResult(2, f"DETECTED|{argv[5]}".encode()),
        status_reader=_status,
    )

    result = _scan(scanner, tmp_path)

    assert result.status == "REJECTED"
    assert result.evidence.verdict == "DETECTED"
    assert result.evidence.exit_code == 2


def test_output_over_limit_fails_even_if_runner_did_not_mark_truncated(
    monkeypatch, tmp_path: Path
) -> None:
    _approved_test_profile(monkeypatch)
    scanner = defender.DefenderCommandScanner(
        runner=lambda argv, **kwargs: defender.ProcessResult(0, b"x" * 65_537),
        status_reader=_status,
    )

    result = _scan(scanner, tmp_path)

    assert result.status == "FAILED"
    assert result.evidence.error_code == "SCAN_OUTPUT_TRUNCATED"


@pytest.mark.parametrize(
    "overrides",
    [
        {"product_version": 4},
        {"engine_version": 1.1},
        {"signature_version": None},
        {"signature_updated_at_utc": "2026-10-13T00:00:00Z"},
        {"signer_subject": 7},
    ],
)
def test_invalid_status_field_types_fail_closed(monkeypatch, tmp_path: Path, overrides) -> None:
    calls: list[int] = []
    scanner = defender.DefenderCommandScanner(
        runner=lambda argv, **kwargs: calls.append(1) or defender.ProcessResult(0, b""),
        status_reader=lambda: _status(**overrides),
    )

    result = _scan(scanner, tmp_path)

    assert result.status == "FAILED"
    assert result.evidence.error_code == "DEFENDER_STATUS_UNTRUSTED"
    assert calls == []


def test_timeout_makes_provider_uncertain_and_blocks_another_launch(
    monkeypatch, tmp_path: Path
) -> None:
    _approved_test_profile(monkeypatch)
    calls: list[int] = []

    def runner(argv, **kwargs):
        calls.append(1)
        return defender.ProcessResult(None, b"partial", timed_out=True)

    scanner = defender.DefenderCommandScanner(runner=runner, status_reader=_status)
    first = _scan(scanner, tmp_path)
    second = _scan(scanner, tmp_path)

    assert first.evidence.error_code == "SCAN_TIMEOUT_COMPLETION_UNKNOWN"
    assert second.evidence.error_code == "SCANNER_STATE_UNCERTAIN"
    assert calls == [1]


def test_capture_retains_only_limit_and_marks_overflow(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeProcess:
        def __init__(self, args, **kwargs):
            captured["args"] = args
            captured.update(kwargs)
            self.stdout = io.BytesIO(b"abcdefghij")

        def wait(self, timeout):
            return 0

    monkeypatch.setattr(defender.subprocess, "Popen", FakeProcess)

    result = defender._run_bounded_process(
        ["fixed-command"], timeout=1.0, max_output_bytes=4
    )

    assert result.output == b"abcd"
    assert result.output_truncated
    assert captured["args"] == ["fixed-command"]
    assert captured["shell"] is False


def test_timeout_kills_only_owned_launcher_and_stuck_reader_returns_bounded(
    monkeypatch,
) -> None:
    release_reader = threading.Event()
    reader_started = threading.Event()
    events: list[str] = []

    class BlockingStream:
        def read(self, size):
            reader_started.set()
            release_reader.wait()
            return b""

        def close(self):
            events.append("close")

    class FakeProcess:
        def __init__(self, *args, **kwargs):
            self.stdout = BlockingStream()
            self.wait_calls = 0

        def wait(self, timeout):
            self.wait_calls += 1
            if self.wait_calls == 1:
                assert reader_started.wait(timeout=1.0)
                raise defender.subprocess.TimeoutExpired("fixed-command", timeout)
            events.append("wait-after-kill")
            return -9

        def kill(self):
            events.append("kill-owned-launcher")

    monkeypatch.setattr(defender.subprocess, "Popen", FakeProcess)

    started = perf_counter()
    try:
        result = defender._run_bounded_process(
            ["fixed-command"], timeout=0.01, max_output_bytes=64
        )
    finally:
        release_reader.set()
    elapsed = perf_counter() - started

    assert result.timed_out
    assert result.reader_incomplete
    assert result.output_truncated
    assert elapsed < 2.5
    assert events == ["kill-owned-launcher", "wait-after-kill"]


def test_incomplete_reader_marks_scanner_uncertain(monkeypatch, tmp_path: Path) -> None:
    _approved_test_profile(monkeypatch)
    calls: list[int] = []

    def runner(argv, **kwargs):
        calls.append(1)
        return defender.ProcessResult(0, b"partial", reader_incomplete=True)

    scanner = defender.DefenderCommandScanner(runner=runner, status_reader=_status)
    first = _scan(scanner, tmp_path)
    second = _scan(scanner, tmp_path)

    assert first.evidence.error_code == "OUTPUT_DRAIN_INCOMPLETE"
    assert second.evidence.error_code == "SCANNER_STATE_UNCERTAIN"
    assert calls == [1]


def test_only_one_scan_can_hold_provider_slot(monkeypatch, tmp_path: Path) -> None:
    _approved_test_profile(monkeypatch)
    entered = threading.Event()
    release = threading.Event()

    def runner(argv, **kwargs):
        entered.set()
        assert release.wait(timeout=2.0)
        return defender.ProcessResult(0, f"CLEAN|{argv[5]}".encode())

    scanner = defender.DefenderCommandScanner(runner=runner, status_reader=_status)
    first_result: list[defender.ScanOutcome] = []
    first_thread = threading.Thread(target=lambda: first_result.append(_scan(scanner, tmp_path)))
    first_thread.start()
    assert entered.wait(timeout=2.0)
    second = _scan(scanner, tmp_path)
    release.set()
    first_thread.join(timeout=2.0)

    assert not first_thread.is_alive()
    assert first_result[0].status == "PASSED"
    assert second.status == "FAILED"
    assert second.evidence.error_code == "SCANNER_BUSY"
