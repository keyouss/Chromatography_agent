"""Fail-closed, fixed-command Windows Defender scanner adapter."""

from __future__ import annotations

import json
import hashlib
import subprocess
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path


DEFENDER_EXE = Path(r"C:\Program Files\Windows Defender\MpCmdRun.exe")
POWERSHELL_EXE = Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
COMMAND_PROFILE_ID = "defender-custom-file-no-remediation-v1"
MAX_OUTPUT_BYTES = 65_536
SCAN_TIMEOUT_SECONDS = 120.0


@dataclass(frozen=True)
class DefenderStatus:
    product_version: str | None
    engine_version: str | None
    signature_version: str | None
    signature_updated_at_utc: datetime | None
    antivirus_enabled: bool
    authenticode_status: str | None
    signer_subject: str | None
    exe_sha256: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class ProcessResult:
    returncode: int | None
    output: bytes
    timed_out: bool = False
    output_truncated: bool = False
    reader_incomplete: bool = False
    error_code: str | None = None


@dataclass(frozen=True)
class ScanEvidence:
    artifact_id: str
    scan_attempt_id: str
    input_sha256: str
    input_size_bytes: int
    provider_id: str
    product_version: str | None
    engine_version: str | None
    signature_version: str | None
    signature_updated_at_utc: datetime | None
    started_at_utc: datetime
    finished_at_utc: datetime
    profile_id: str
    exit_code: int | None
    verdict: str
    error_code: str | None
    output_truncated: bool
    exe_sha256: str | None = None
    signature_version_after: str | None = None
    signature_updated_at_utc_after: datetime | None = None


@dataclass(frozen=True)
class ScanOutcome:
    status: str
    warning: dict[str, str] | None
    evidence: ScanEvidence


@dataclass(frozen=True)
class ResultClassification:
    verdict: str
    scanned_path: str


@dataclass(frozen=True)
class _ResultProfile:
    profile_id: str
    product_version: str
    clean_exit_code: int
    detection_exit_code: int | None
    classify: Callable[[bytes, str], ResultClassification | None]
    expected_engine_version: str | None = None
    expected_signer_subject: str | None = None
    allowed_signature_versions: frozenset[str] | None = None


def _profile_matches_status(
    profile: _ResultProfile,
    status: DefenderStatus,
    *,
    allow_signature_updates: bool = False,
) -> bool:
    """Require every profile to bind the scanner/runtime identity it was checked against."""

    versions = profile.allowed_signature_versions
    signature_matches = (
        isinstance(status.signature_version, str)
        and bool(status.signature_version.strip())
        if allow_signature_updates and versions is None
        else isinstance(versions, frozenset)
        and bool(versions)
        and all(isinstance(value, str) and value.strip() for value in versions)
        and status.signature_version in versions
    )
    return bool(
        isinstance(profile.expected_engine_version, str)
        and profile.expected_engine_version.strip()
        and isinstance(profile.expected_signer_subject, str)
        and profile.expected_signer_subject.strip()
        and profile.product_version == status.product_version
        and profile.expected_engine_version == status.engine_version
        and profile.expected_signer_subject == status.signer_subject
        and signature_matches
    )


# Production stays unsupported until an exact build/flags/result profile has
# been reviewed and validated in an isolated scan window. Tests patch this map.
_APPROVED_PROFILES: dict[str, _ResultProfile] = {}
# Explicit release/evidence approval gate. It stays false in production until
# an isolated validation establishes exact clean/detection semantics.
_PROFILE_EVIDENCE_APPROVED = False


_STATUS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1') -ErrorAction Stop
$p = 'C:\Program Files\Windows Defender\MpCmdRun.exe'
$m = Get-MpComputerStatus
$f = Get-Item -LiteralPath $p
$s = Get-AuthenticodeSignature -LiteralPath $p
$updated = $m.AntivirusSignatureLastUpdated
[ordered]@{
  product_version = $f.VersionInfo.ProductVersion
  engine_version = [string]$m.AMEngineVersion
  signature_version = [string]$m.AntivirusSignatureVersion
  signature_updated_at_utc = if ($updated) { $updated.ToUniversalTime().ToString('o') } else { $null }
  antivirus_enabled = [bool]$m.AntivirusEnabled
  authenticode_status = [string]$s.Status
  signer_subject = if ($s.SignerCertificate) { $s.SignerCertificate.Subject } else { $null }
} | ConvertTo-Json -Compress
""".strip()


def _run_bounded_process(
    argv: Sequence[str], *, timeout: float, max_output_bytes: int
) -> ProcessResult:
    """Drain one launcher while retaining at most the configured output bytes."""

    try:
        process = subprocess.Popen(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            shell=False,
        )
    except Exception:
        return ProcessResult(None, b"", error_code="PROCESS_START_FAILED")

    output = bytearray()
    truncated = threading.Event()
    reader_error = threading.Event()

    def drain() -> None:
        assert process.stdout is not None
        try:
            while True:
                chunk = process.stdout.read(8192)
                if not chunk:
                    return
                room = max_output_bytes - len(output)
                if room > 0:
                    output.extend(chunk[:room])
                if len(chunk) > room:
                    truncated.set()
        except Exception:
            reader_error.set()

    reader = threading.Thread(target=drain, name="defender-output", daemon=True)
    reader.start()
    timed_out = False
    wait_error = False
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        # This kills only the Popen launcher. Defender may continue its service-side scan.
        timed_out = True
        try:
            process.kill()
        except Exception:
            pass
        try:
            process.wait(timeout=5.0)
        except Exception:
            pass
        returncode = None
    except Exception:
        wait_error = True
        returncode = None
        try:
            process.kill()
            process.wait(timeout=5.0)
        except Exception:
            pass
    reader.join(timeout=1.0)
    reader_incomplete = reader.is_alive()
    if not reader_incomplete and process.stdout is not None:
        try:
            process.stdout.close()
        except Exception:
            reader_error.set()
    if reader_incomplete or reader_error.is_set():
        truncated.set()
    return ProcessResult(
        returncode,
        bytes(output),
        timed_out=timed_out,
        output_truncated=truncated.is_set(),
        reader_incomplete=reader_incomplete,
        error_code=(
            "OUTPUT_DRAIN_INCOMPLETE"
            if reader_incomplete
            else "PROCESS_WAIT_FAILED"
            if wait_error
            else None
        ),
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_status(output: bytes) -> DefenderStatus:
    try:
        data = json.loads(output.decode("utf-8-sig"))
        updated = data.get("signature_updated_at_utc")
        updated_at = datetime.fromisoformat(updated.replace("Z", "+00:00")) if updated else None
        if updated_at is not None:
            if updated_at.tzinfo is None or updated_at.utcoffset() is None:
                raise ValueError("DEFENDER_STATUS_TIMEZONE_MISSING")
            updated_at = updated_at.astimezone(timezone.utc)
        return DefenderStatus(
            product_version=data.get("product_version"),
            engine_version=data.get("engine_version"),
            signature_version=data.get("signature_version"),
            signature_updated_at_utc=updated_at,
            antivirus_enabled=data.get("antivirus_enabled") is True,
            authenticode_status=data.get("authenticode_status"),
            signer_subject=data.get("signer_subject"),
            exe_sha256=data.get("exe_sha256"),
        )
    except Exception as exc:
        raise ValueError("DEFENDER_STATUS_INVALID") from exc


def _read_status() -> DefenderStatus:
    result = _run_bounded_process(
        [str(POWERSHELL_EXE), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", _STATUS_SCRIPT],
        timeout=10.0,
        max_output_bytes=MAX_OUTPUT_BYTES,
    )
    if result.returncode != 0 or result.timed_out or result.output_truncated or result.error_code:
        raise RuntimeError("DEFENDER_STATUS_UNAVAILABLE")
    return replace(_parse_status(result.output), exe_sha256=_sha256_file(DEFENDER_EXE))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _valid_status(status: DefenderStatus) -> bool:
    if not isinstance(status, DefenderStatus):
        return False
    try:
        signature_time = status.signature_updated_at_utc
        required_versions = (status.product_version, status.engine_version, status.signature_version)
        return bool(
            status.antivirus_enabled is True
            and all(isinstance(value, str) and value.strip() for value in required_versions)
            and isinstance(signature_time, datetime)
            and signature_time.tzinfo is not None
            and signature_time.utcoffset() == timedelta(0)
            and status.authenticode_status == "Valid"
            and isinstance(status.signer_subject, str)
            and status.signer_subject.strip()
            and "CN=Microsoft Windows" in status.signer_subject
        )
    except Exception:
        return False


class DefenderCommandScanner:
    """Runs one fixed Defender custom scan; no exit code is trusted by itself."""

    def __init__(
        self,
        *,
        runner: Callable[..., ProcessResult] = _run_bounded_process,
        status_reader: Callable[[], DefenderStatus] = _read_status,
        reviewed_profile: _ResultProfile | None = None,
        allowed_inputs: frozenset[tuple[str, int]] | None = None,
        expected_exe_sha256: str | None = None,
        allow_signature_updates: bool = False,
    ) -> None:
        self._runner = runner
        self._status_reader = status_reader
        self._reviewed_profile = reviewed_profile
        self._allowed_inputs = allowed_inputs
        self._expected_exe_sha256 = expected_exe_sha256
        self._allow_signature_updates = allow_signature_updates
        self._slot = threading.Lock()
        self._uncertain = False

    def scan(
        self,
        *,
        path: Path,
        artifact_id: str,
        input_sha256: str,
        input_size_bytes: int,
        scan_attempt_id: str,
    ) -> ScanOutcome:
        started = _utc_now()
        status: DefenderStatus | None = None
        status_after: DefenderStatus | None = None
        profile_id = self._reviewed_profile.profile_id if self._reviewed_profile else COMMAND_PROFILE_ID
        process_result: ProcessResult | None = None
        error_code: str | None = None
        verdict = "INDETERMINATE"
        outcome_status = "FAILED"

        def finish() -> ScanOutcome:
            finished = _utc_now()
            evidence = ScanEvidence(
                artifact_id=artifact_id,
                scan_attempt_id=scan_attempt_id,
                input_sha256=input_sha256,
                input_size_bytes=input_size_bytes,
                provider_id="windows_defender_mpcmdrun",
                product_version=status.product_version if status else None,
                engine_version=status.engine_version if status else None,
                signature_version=status.signature_version if status else None,
                signature_updated_at_utc=status.signature_updated_at_utc if status else None,
                started_at_utc=started,
                finished_at_utc=finished,
                profile_id=profile_id,
                exit_code=process_result.returncode if process_result else None,
                verdict=verdict,
                error_code=error_code,
                output_truncated=process_result.output_truncated if process_result else False,
                exe_sha256=status.exe_sha256 if status else None,
                signature_version_after=(
                    status_after.signature_version
                    if self._allow_signature_updates and status_after
                    else None
                ),
                signature_updated_at_utc_after=(
                    status_after.signature_updated_at_utc
                    if self._allow_signature_updates and status_after
                    else None
                ),
            )
            warning = {"code": error_code, "message": "Defender 扫描未能确认安全结果"} if error_code else None
            return ScanOutcome(outcome_status, warning, evidence)

        if not isinstance(path, Path) or not path.is_absolute() or not artifact_id or not scan_attempt_id:
            error_code = "SCAN_INPUT_INVALID"
            return finish()
        if len(input_sha256) != 64 or any(c not in "0123456789abcdef" for c in input_sha256) or input_size_bytes <= 0:
            error_code = "SCAN_INPUT_INVALID"
            return finish()
        reviewed = self._reviewed_profile is not None or self._allowed_inputs is not None or self._expected_exe_sha256 is not None
        if self._allow_signature_updates and not reviewed:
            error_code = "DEFENDER_REVIEW_BINDING_INVALID"
            return finish()
        if reviewed and not self._reviewed_dependencies_valid():
            error_code = "DEFENDER_REVIEW_BINDING_INVALID"
            return finish()
        if reviewed and (input_sha256, input_size_bytes) not in self._allowed_inputs:
            error_code = "CSV_INPUT_NOT_ALLOWLISTED"
            return finish()
        if not self._slot.acquire(blocking=False):
            error_code = "SCANNER_BUSY"
            return finish()
        try:
            if self._uncertain:
                error_code = "SCANNER_STATE_UNCERTAIN"
                return finish()
            try:
                status = self._status_reader()
            except Exception:
                error_code = "DEFENDER_STATUS_UNAVAILABLE"
                return finish()
            if not _valid_status(status):
                error_code = "DEFENDER_STATUS_UNTRUSTED"
                return finish()
            if reviewed:
                profile = self._reviewed_profile
                assert profile is not None
                if (
                    status.exe_sha256 != self._expected_exe_sha256
                    or not _profile_matches_status(
                        profile,
                        status,
                        allow_signature_updates=self._allow_signature_updates,
                    )
                ):
                    error_code = "DEFENDER_PROFILE_UNSUPPORTED"
                    return finish()
                try:
                    exe_before = _sha256_file(DEFENDER_EXE)
                except Exception:
                    error_code = "DEFENDER_EXE_UNAVAILABLE"
                    return finish()
                if exe_before != self._expected_exe_sha256:
                    error_code = "DEFENDER_EXE_IDENTITY_MISMATCH"
                    return finish()
            else:
                profile = _APPROVED_PROFILES.get(status.product_version or "")
                if (
                    not _PROFILE_EVIDENCE_APPROVED
                    or profile is None
                    or profile.product_version != status.product_version
                    or not _profile_matches_status(profile, status)
                ):
                    error_code = "DEFENDER_PROFILE_UNSUPPORTED"
                    return finish()
            profile_id = profile.profile_id

            argv = [
                str(DEFENDER_EXE), "-Scan", "-ScanType", "3", "-File", str(path), "-DisableRemediation"
            ]
            try:
                process_result = self._runner(
                    argv,
                    timeout=SCAN_TIMEOUT_SECONDS,
                    max_output_bytes=MAX_OUTPUT_BYTES,
                )
            except Exception:
                self._uncertain = True
                error_code = "DEFENDER_PROCESS_FAILED"
                if reviewed:
                    try:
                        if _sha256_file(DEFENDER_EXE) != self._expected_exe_sha256:
                            error_code = "DEFENDER_EXE_CHANGED"
                    except Exception:
                        error_code = "DEFENDER_EXE_UNAVAILABLE"
                return finish()
            if reviewed:
                try:
                    exe_after = _sha256_file(DEFENDER_EXE)
                except Exception:
                    error_code = "DEFENDER_EXE_UNAVAILABLE"
                    return finish()
                if exe_after != self._expected_exe_sha256:
                    error_code = "DEFENDER_EXE_CHANGED"
                    return finish()
            if process_result.timed_out:
                self._uncertain = True
                error_code = "SCAN_TIMEOUT_COMPLETION_UNKNOWN"
                return finish()
            if process_result.reader_incomplete:
                self._uncertain = True
                error_code = "OUTPUT_DRAIN_INCOMPLETE"
                return finish()
            if process_result.error_code:
                if process_result.error_code == "PROCESS_WAIT_FAILED":
                    self._uncertain = True
                error_code = process_result.error_code
                return finish()
            if process_result.output_truncated or len(process_result.output) > MAX_OUTPUT_BYTES:
                error_code = "SCAN_OUTPUT_TRUNCATED"
                return finish()
            if self._allow_signature_updates:
                try:
                    after = self._status_reader()
                except Exception:
                    error_code = "DEFENDER_STATUS_UNAVAILABLE"
                    return finish()
                status_after = after if isinstance(after, DefenderStatus) else None
                if (
                    not _valid_status(after)
                    or replace(
                        after,
                        signature_version=status.signature_version,
                        signature_updated_at_utc=status.signature_updated_at_utc,
                    )
                    != status
                    or after.exe_sha256 != self._expected_exe_sha256
                ):
                    error_code = "DEFENDER_STATUS_CHANGED"
                    return finish()
            try:
                classification = profile.classify(process_result.output, str(path))
            except Exception:
                error_code = "SCAN_RESULT_UNPARSEABLE"
                return finish()
            if not isinstance(classification, ResultClassification):
                error_code = "SCAN_RESULT_UNPARSEABLE"
                return finish()
            if not isinstance(classification.verdict, str) or classification.verdict not in {
                "CLEAN",
                "DETECTED",
            }:
                error_code = "SCAN_RESULT_UNPARSEABLE"
                return finish()
            if not isinstance(classification.scanned_path, str) or classification.scanned_path != str(path):
                error_code = "SCAN_RESULT_UNBOUND"
                return finish()
            expected_exit = (
                profile.clean_exit_code
                if classification.verdict == "CLEAN"
                else profile.detection_exit_code
            )
            if expected_exit is None or process_result.returncode != expected_exit:
                error_code = "SCAN_RESULT_CONFLICT"
                return finish()

            if not self._allow_signature_updates:
                try:
                    after = self._status_reader()
                except Exception:
                    error_code = "DEFENDER_STATUS_UNAVAILABLE"
                    return finish()
                if (
                    not _valid_status(after)
                    or after != status
                    or (reviewed and after.exe_sha256 != self._expected_exe_sha256)
                ):
                    error_code = "DEFENDER_STATUS_CHANGED"
                    return finish()

            if classification.verdict == "DETECTED":
                verdict = "DETECTED"
                outcome_status = "REJECTED"
                return finish()
            if classification.verdict == "CLEAN":
                verdict = "CLEAN"
                outcome_status = "PASSED"
                return finish()
            error_code = "SCAN_RESULT_UNPARSEABLE"
            return finish()
        finally:
            self._slot.release()

    def _reviewed_dependencies_valid(self) -> bool:
        try:
            profile = self._reviewed_profile
            allowed_inputs = self._allowed_inputs
            expected_exe_sha256 = self._expected_exe_sha256
            return bool(
                isinstance(profile, _ResultProfile)
                and isinstance(allowed_inputs, frozenset)
                and bool(allowed_inputs)
                and all(
                    isinstance(item, tuple)
                    and len(item) == 2
                    and isinstance(item[0], str)
                    and len(item[0]) == 64
                    and all(char in "0123456789abcdef" for char in item[0])
                    and isinstance(item[1], int)
                    and not isinstance(item[1], bool)
                    and item[1] > 0
                    for item in allowed_inputs
                )
                and isinstance(expected_exe_sha256, str)
                and len(expected_exe_sha256) == 64
                and all(char in "0123456789abcdef" for char in expected_exe_sha256)
                and isinstance(self._allow_signature_updates, bool)
                and (not self._allow_signature_updates or profile.allowed_signature_versions is None)
            )
        except Exception:
            return False


__all__ = ["DefenderCommandScanner", "DefenderStatus", "ProcessResult", "ScanEvidence", "ScanOutcome"]
