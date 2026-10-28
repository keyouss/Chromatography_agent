"""Source-only pins for the reviewed local five-CSV Defender window."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from .scanner_defender import DefenderCommandScanner, _ResultProfile


WINDOW_ID = "batch-csv-20261022-v2"
PROJECT_ID = "priority-local"
PROFILE_ID = "defender-clean-only-4.18.25080.5-engine-1.1.26090.9-batch-csv-20261022-v2"
PRIOR_PROFILES = (
    (
        "defender-clean-only-4.18.25080.5-engine-1.1.26090.9-signature-1.461.143.0-batch-csv-20261022-v2",
        "1.461.143.0",
    ),
)
EXPECTED_EXE_SHA256 = "a4291c5b4325895d13d32e232bf50c1b41356306803ac5dc75bba9aecb021141"
INPUT_MANIFEST_SHA256 = "b76265cf9c746bbd0c7390f784d69072e81ea79b39aeb901dca6f46475ce2f50"
BUILD_MANIFEST_SHA256 = "002507de595ff8fdb607b7b15bd92d5c391e5ac1ed0f754520f3b4bf1d68e3fe"
INPUT_MANIFEST_PATH = Path(__file__).resolve().parents[1] / "resources" / "batch-scan-manifest.json"

# This tuple is the complete allowlist. It is intentionally not generated from
# a runtime manifest so later edits cannot widen this reviewed scan window.
ALLOWED_INPUTS: frozenset[tuple[str, int]] = frozenset(
    {
        ("37278b70cc49df0f542ed7d768e983280aa9ddc2d1107ee986e13e3770aa1c01", 205),
        ("df39b76a0fcaa7423cad34632620094f672659c6713dff6f6d5b5fa21a14059c", 554),
        ("81b5b6b5cde519a1be7451815b23c1e3d13dfbdc814f1b6fb4dc4a03da590245", 235),
        ("06bf63989a93468fb25088696cc2b4d8c36723556179f95546147fd4e2fe3f88", 550),
        ("7d0cc9a8d2b52d1d1e897a83a01e3e0ffb2af6dd18b7b9f3af004efbf7e0ff4e", 750),
    }
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _build_reviewed_profile() -> _ResultProfile:
    # Reuse the existing byte-for-byte CLEAN-only classifier. This new exact
    # profile does not mutate the legacy candidate or global approval map.
    from .defender_profile_candidate import _classify_fixed_clean

    return _ResultProfile(
        profile_id=PROFILE_ID,
        product_version="4.18.25080.5",
        clean_exit_code=0,
        detection_exit_code=None,
        classify=_classify_fixed_clean,
        expected_engine_version="1.1.26090.9",
        expected_signer_subject="CN=Microsoft Windows, O=Microsoft Corporation, L=Redmond, S=Washington, C=US",
        allowed_signature_versions=None,
    )


@dataclass(frozen=True)
class CsvScanWindow:
    window_id: str
    project_id: str
    profile_id: str
    exe_sha256: str
    allowed_inputs: frozenset[tuple[str, int]]
    input_manifest_sha256: str
    build_manifest_sha256: str
    prior_profiles: tuple[tuple[str, str], ...] = ()

    def build_scanner(self) -> DefenderCommandScanner:
        return DefenderCommandScanner(
            reviewed_profile=_build_reviewed_profile(),
            allowed_inputs=self.allowed_inputs,
            expected_exe_sha256=self.exe_sha256,
            allow_signature_updates=True,
        )


def reviewed_csv_window_for_manifest(path: str | Path) -> CsvScanWindow | None:
    """Return the local window only for both reviewed manifest byte pins."""
    try:
        build_path = Path(path)
        if not build_path.is_absolute() or not build_path.is_file():
            return None
        if _sha256_file(build_path) != BUILD_MANIFEST_SHA256:
            return None
        if _sha256_file(INPUT_MANIFEST_PATH) != INPUT_MANIFEST_SHA256:
            return None
    except (OSError, TypeError, ValueError):
        return None
    return CsvScanWindow(
        window_id=WINDOW_ID,
        project_id=PROJECT_ID,
        profile_id=PROFILE_ID,
        exe_sha256=EXPECTED_EXE_SHA256,
        allowed_inputs=ALLOWED_INPUTS,
        input_manifest_sha256=INPUT_MANIFEST_SHA256,
        build_manifest_sha256=BUILD_MANIFEST_SHA256,
        prior_profiles=PRIOR_PROFILES,
    )


__all__ = ["CsvScanWindow", "reviewed_csv_window_for_manifest"]
