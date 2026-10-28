from __future__ import annotations

import json
from pathlib import Path

import pytest

from chrom_platform.assembly import load_manifest, validate_manifest


ROOT = Path(__file__).resolve().parents[2]
BUILDS = ROOT / "builds"


@pytest.mark.parametrize("profile", ["universal-v1", "sfc-v1", "cc-v1", "minimal-structure-v1"])
def test_profile_is_strictly_validated(profile: str) -> None:
    manifest = load_manifest(BUILDS / f"{profile}.json")
    assert manifest.build_id == profile


def test_manifest_rejects_unknown_fields() -> None:
    raw = json.loads((BUILDS / "minimal-structure-v1.json").read_text(encoding="utf-8"))
    raw["coding"] = True
    with pytest.raises(ValueError):
        validate_manifest(raw)


def test_manifest_does_not_search_parent_builds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHROM_BUILD_MANIFEST", raising=False)
    with pytest.raises(FileNotFoundError):
        load_manifest()
