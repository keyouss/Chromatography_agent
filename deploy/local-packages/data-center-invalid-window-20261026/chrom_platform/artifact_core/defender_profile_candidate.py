"""Unactivated, CLEAN-only candidate derived from four fixed Defender samples."""

from __future__ import annotations

from pathlib import Path

from .scanner_defender import ResultClassification, _ResultProfile


_PRODUCT_VERSION = "4.18.25080.5"
_ENGINE_VERSION = "1.1.26090.9"
_SIGNER_SUBJECT = "CN=Microsoft Windows, O=Microsoft Corporation, L=Redmond, S=Washington, C=US"
_SIGNATURE_VERSIONS = frozenset({"1.461.40.0", "1.461.43.0"})


def _classify_fixed_clean(output: bytes, requested_path: str) -> ResultClassification | None:
    if not isinstance(output, bytes) or not isinstance(requested_path, str):
        return None
    try:
        path = Path(requested_path)
        path_bytes = requested_path.encode("ascii")
    except (OSError, UnicodeEncodeError, TypeError, ValueError):
        return None
    if not path.is_absolute() or any(value in path_bytes for value in (b"\r", b"\n", b"\x00")):
        return None
    expected = (
        b"Scan starting...\r\n"
        b"Scan finished.\r\n"
        b"Scanning " + path_bytes + b" found no threats.\r\n"
    )
    if output != expected:
        return None
    return ResultClassification("CLEAN", requested_path)


def build_defender_clean_candidate() -> _ResultProfile:
    """Return a candidate profile; never mutate scanner approval globals."""

    return _ResultProfile(
        profile_id="defender-clean-only-4.18.25080.5-engine-1.1.26090.9-v1",
        product_version=_PRODUCT_VERSION,
        clean_exit_code=0,
        detection_exit_code=None,
        classify=_classify_fixed_clean,
        expected_engine_version=_ENGINE_VERSION,
        expected_signer_subject=_SIGNER_SUBJECT,
        allowed_signature_versions=_SIGNATURE_VERSIONS,
    )
