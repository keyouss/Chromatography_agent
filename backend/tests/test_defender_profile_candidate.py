from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

import chrom_platform.artifact_core.scanner_defender as scanner_defender
from chrom_platform.artifact_core.defender_profile_candidate import (
    build_defender_clean_candidate,
)


def _status(**overrides) -> scanner_defender.DefenderStatus:
    values = {
        "product_version": "4.18.25080.5",
        "engine_version": "1.1.26090.9",
        "signature_version": "1.461.43.0",
        "signature_updated_at_utc": datetime(2026, 10, 16, tzinfo=timezone.utc),
        "antivirus_enabled": True,
        "authenticode_status": "Valid",
        "signer_subject": "CN=Microsoft Windows, O=Microsoft Corporation, L=Redmond, S=Washington, C=US",
    }
    values.update(overrides)
    return scanner_defender.DefenderStatus(**values)


def _expected(path: str) -> bytes:
    return (
        b"Scan starting...\r\n"
        b"Scan finished.\r\n"
        + f"Scanning {path} found no threats.\r\n".encode("ascii")
    )


def _scan(scanner: scanner_defender.DefenderCommandScanner, tmp_path: Path):
    path = (tmp_path / "fixed-member.bin").absolute()
    return scanner.scan(
        path=path,
        artifact_id="synthetic-artifact",
        input_sha256="a" * 64,
        input_size_bytes=8,
        scan_attempt_id="synthetic-attempt",
    )


def _activate_copy_for_test(monkeypatch, profile=None) -> None:
    monkeypatch.setattr(
        scanner_defender,
        "_APPROVED_PROFILES",
        {"4.18.25080.5": profile or build_defender_clean_candidate()},
    )
    monkeypatch.setattr(scanner_defender, "_PROFILE_EVIDENCE_APPROVED", True)


def test_factory_returns_strict_clean_only_profile_without_activation() -> None:
    profile = build_defender_clean_candidate()

    assert isinstance(profile, scanner_defender._ResultProfile)
    assert profile.product_version == "4.18.25080.5"
    assert profile.expected_engine_version == "1.1.26090.9"
    assert profile.expected_signer_subject == _status().signer_subject
    assert profile.allowed_signature_versions == frozenset({"1.461.40.0", "1.461.43.0"})
    assert profile.detection_exit_code is None
    assert scanner_defender._APPROVED_PROFILES == {}
    assert scanner_defender._PROFILE_EVIDENCE_APPROVED is False


@pytest.mark.parametrize("signature_version", ["1.461.40.0", "1.461.43.0"])
def test_candidate_profile_scans_exact_clean_for_each_observed_signature(
    monkeypatch, tmp_path: Path, signature_version: str
) -> None:
    _activate_copy_for_test(monkeypatch)
    invocations: list[list[str]] = []
    status_reads = 0

    def read_status():
        nonlocal status_reads
        status_reads += 1
        return _status(signature_version=signature_version)

    def run(argv, **kwargs):
        invocations.append(list(argv))
        return scanner_defender.ProcessResult(0, _expected(argv[5]))

    path = (tmp_path / "fixed-member.bin").absolute()
    result = _scan(
        scanner_defender.DefenderCommandScanner(runner=run, status_reader=read_status),
        tmp_path,
    )

    assert result.status == "PASSED"
    assert result.evidence.verdict == "CLEAN"
    assert result.evidence.product_version == "4.18.25080.5"
    assert result.evidence.engine_version == "1.1.26090.9"
    assert result.evidence.signature_version == signature_version
    assert result.evidence.artifact_id == "synthetic-artifact"
    assert result.evidence.scan_attempt_id == "synthetic-attempt"
    assert result.evidence.input_sha256 == "a" * 64
    assert result.evidence.input_size_bytes == 8
    assert len(invocations) == 1
    assert invocations[0][5] == str(path)
    assert status_reads == 2


def test_classifier_requires_complete_ascii_crlf_path_bound_template(tmp_path: Path) -> None:
    profile = build_defender_clean_candidate()
    path = str((tmp_path / "fixed.bin").absolute())
    output = _expected(path)

    assert profile.classify(output, path) == scanner_defender.ResultClassification("CLEAN", path)
    assert profile.classify(_expected(path + ".other"), path) is None
    assert profile.classify(output + b"extra", path) is None
    assert profile.classify(output.replace(b"\r\n", b"\n"), path) is None
    assert profile.classify(b"Scan starting...\r\nScan finished.\r\nScanning " + path.encode() + b" found threats.\r\n", path) is None
    assert profile.classify(output, "relative.bin") is None
    assert profile.classify(output, path + "\r\n") is None
    assert profile.classify(output, str(tmp_path / "非ASCII.bin")) is None


@pytest.mark.parametrize(
    "override",
    [
        {"product_version": "4.18.25080.4"},
        {"engine_version": "1.1.26090.8"},
        {"signer_subject": "CN=Microsoft Windows"},
        {"signature_version": "1.461.44.0"},
    ],
)
def test_candidate_status_mismatch_fails_before_runner(monkeypatch, tmp_path: Path, override) -> None:
    _activate_copy_for_test(monkeypatch)
    calls: list[int] = []
    scanner = scanner_defender.DefenderCommandScanner(
        runner=lambda argv, **kwargs: calls.append(1) or scanner_defender.ProcessResult(0, b""),
        status_reader=lambda: _status(**override),
    )

    result = _scan(scanner, tmp_path)

    assert result.status == "FAILED"
    assert result.evidence.error_code == "DEFENDER_PROFILE_UNSUPPORTED"
    assert calls == []


def test_missing_profile_status_restrictions_fail_before_runner(monkeypatch, tmp_path: Path) -> None:
    unpinned = scanner_defender._ResultProfile(
        profile_id="unbound-test-profile",
        product_version="4.18.25080.5",
        clean_exit_code=0,
        detection_exit_code=2,
        classify=lambda output, path: scanner_defender.ResultClassification("CLEAN", path),
    )
    _activate_copy_for_test(monkeypatch, unpinned)
    calls: list[int] = []
    scanner = scanner_defender.DefenderCommandScanner(
        runner=lambda argv, **kwargs: calls.append(1) or scanner_defender.ProcessResult(0, b""),
        status_reader=_status,
    )

    result = _scan(scanner, tmp_path)

    assert result.status == "FAILED"
    assert result.evidence.error_code == "DEFENDER_PROFILE_UNSUPPORTED"
    assert calls == []


def test_exact_template_plus_nonzero_exit_is_conflict(monkeypatch, tmp_path: Path) -> None:
    _activate_copy_for_test(monkeypatch)
    scanner = scanner_defender.DefenderCommandScanner(
        runner=lambda argv, **kwargs: scanner_defender.ProcessResult(2, _expected(argv[5])),
        status_reader=_status,
    )

    result = _scan(scanner, tmp_path)

    assert result.status == "FAILED"
    assert result.evidence.error_code == "SCAN_RESULT_CONFLICT"


def test_before_after_status_change_fails_closed(monkeypatch, tmp_path: Path) -> None:
    _activate_copy_for_test(monkeypatch)
    statuses = iter((_status(signature_version="1.461.40.0"), _status(signature_version="1.461.43.0")))
    scanner = scanner_defender.DefenderCommandScanner(
        runner=lambda argv, **kwargs: scanner_defender.ProcessResult(0, _expected(argv[5])),
        status_reader=lambda: next(statuses),
    )

    result = _scan(scanner, tmp_path)

    assert result.status == "FAILED"
    assert result.evidence.error_code == "DEFENDER_STATUS_CHANGED"
    assert result.evidence.verdict == "INDETERMINATE"


def test_candidate_factory_does_not_activate_default_scanner(monkeypatch, tmp_path: Path) -> None:
    build_defender_clean_candidate()
    calls: list[int] = []
    scanner = scanner_defender.DefenderCommandScanner(
        runner=lambda argv, **kwargs: calls.append(1) or scanner_defender.ProcessResult(0, b""),
        status_reader=_status,
    )

    result = _scan(scanner, tmp_path)

    assert result.status == "FAILED"
    assert result.evidence.error_code == "DEFENDER_PROFILE_UNSUPPORTED"
    assert calls == []
