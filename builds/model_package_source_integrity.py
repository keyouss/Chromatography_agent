"""Build-owned source checks for the fixed GC safetensors verifier closure."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Mapping


_INVENTORY_PATH = Path(__file__).with_name("model_package_source_inventory.json")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")

# This set is deliberately independent of the mutable runtime pin module.
_REQUIRED_SOURCE_FILES = frozenset({
    "chrom_platform/adapters/gc/package_source_pins.py",
    "chrom_platform/adapters/gc/package_loader.py",
    "chrom_platform/adapters/gc/package_model.py",
    "chrom_platform/adapters/gc/runtime.py",
    "chrom_platform/adapters/gc/manifest.py",
    "chrom_platform/adapters/gc/serving_profile.py",
    "chrom_platform/adapters/gc/source_pins.py",
    "chrom_platform/adapters/gc/validation.py",
    "chrom_platform/artifact_core/model_package_manifest.py",
    "chrom_platform/core/model_load_observation.py",
    "chrom_platform/modules/model_center/package_verification.py",
    "chrom_platform/modules/prediction/schemas.py",
})

# Fixed paths expected in a copied GC profile.  Model Center's package
# verifier is copied only when that module is selected; other GC files are
# part of the selected adapter/prediction closure.
_GC_COPY_FILES = frozenset({
    "chrom_platform/adapters/gc/package_source_pins.py",
    "chrom_platform/adapters/gc/package_loader.py",
    "chrom_platform/adapters/gc/package_model.py",
    "chrom_platform/adapters/gc/runtime.py",
    "chrom_platform/adapters/gc/manifest.py",
    "chrom_platform/adapters/gc/serving_profile.py",
    "chrom_platform/adapters/gc/source_pins.py",
    "chrom_platform/adapters/gc/validation.py",
    "chrom_platform/artifact_core/model_package_manifest.py",
    "chrom_platform/core/model_load_observation.py",
    "chrom_platform/modules/prediction/schemas.py",
})
_MODEL_CENTER_COPY_FILES = frozenset({
    "chrom_platform/modules/model_center/package_verification.py",
})


def _safe_inventory_path(raw: object) -> str:
    if not isinstance(raw, str) or not raw or "\\" in raw:
        raise ValueError("MODEL_PACKAGE_SOURCE_INVENTORY_PATH_INVALID")
    posix = PurePosixPath(raw)
    windows = PureWindowsPath(raw)
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or any(part in {"", ".", ".."} for part in raw.split("/"))
        or posix.as_posix() != raw
    ):
        raise ValueError("MODEL_PACKAGE_SOURCE_INVENTORY_PATH_INVALID")
    return raw


def load_inventory(path: str | Path | None = None) -> dict[str, str]:
    """Load a strict, build-owned digest list; reject omissions and extras."""

    inventory_path = Path(path) if path is not None else _INVENTORY_PATH
    try:
        raw = json.loads(inventory_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("MODEL_PACKAGE_SOURCE_INVENTORY_UNAVAILABLE") from exc
    if not isinstance(raw, dict) or set(raw) != {"inventory_version", "scope", "files"}:
        raise ValueError("MODEL_PACKAGE_SOURCE_INVENTORY_INVALID")
    if (raw.get("inventory_version") != "1.0"
            or raw.get("scope") != "fixed-gc-safetensors-package-verification-source"
            or not isinstance(raw.get("files"), list)):
        raise ValueError("MODEL_PACKAGE_SOURCE_INVENTORY_INVALID")

    files: dict[str, str] = {}
    for row in raw["files"]:
        if not isinstance(row, dict) or set(row) != {"path", "sha256"}:
            raise ValueError("MODEL_PACKAGE_SOURCE_INVENTORY_ENTRY_INVALID")
        relative = _safe_inventory_path(row.get("path"))
        digest = row.get("sha256")
        if relative in files or relative not in _REQUIRED_SOURCE_FILES:
            raise ValueError("MODEL_PACKAGE_SOURCE_INVENTORY_PATH_INVALID")
        if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
            raise ValueError("MODEL_PACKAGE_SOURCE_INVENTORY_HASH_INVALID")
        files[relative] = digest
    if set(files) != _REQUIRED_SOURCE_FILES:
        raise ValueError("MODEL_PACKAGE_SOURCE_INVENTORY_CLOSURE_INVALID")
    return files


def _verify_tree(root: str | Path, files: Mapping[str, str], selected: frozenset[str]) -> None:
    base = Path(root).resolve(strict=True)
    if not base.is_dir():
        raise ValueError("MODEL_PACKAGE_SOURCE_ROOT_INVALID")
    for relative in sorted(selected):
        path = base.joinpath(*PurePosixPath(relative).parts)
        cursor = base
        for part in PurePosixPath(relative).parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise ValueError("MODEL_PACKAGE_SOURCE_PATH_INVALID")
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(base)
            if not resolved.is_file():
                raise ValueError("MODEL_PACKAGE_SOURCE_FILE_MISSING")
            actual = hashlib.sha256(resolved.read_bytes()).hexdigest()
        except (OSError, ValueError) as exc:
            if isinstance(exc, ValueError) and str(exc).startswith("MODEL_PACKAGE_"):
                raise
            raise ValueError("MODEL_PACKAGE_SOURCE_FILE_MISSING") from exc
        if actual != files[relative]:
            raise ValueError("MODEL_PACKAGE_SOURCE_HASH_MISMATCH")


def verify_source_tree(source_root: str | Path, *, inventory_path: str | Path | None = None) -> None:
    """Verify the complete source closure before it enters a profile copy."""

    files = load_inventory(inventory_path)
    _verify_tree(source_root, files, _REQUIRED_SOURCE_FILES)


def verify_gc_profile_copy(
    source_root: str | Path,
    target_root: str | Path,
    *,
    model_center_selected: bool,
    inventory_path: str | Path | None = None,
) -> None:
    """Verify source and selected GC copy closure using only build-owned pins."""

    files = load_inventory(inventory_path)
    _verify_tree(source_root, files, _REQUIRED_SOURCE_FILES)
    copied = _GC_COPY_FILES | (_MODEL_CENTER_COPY_FILES if model_center_selected else frozenset())
    _verify_tree(target_root, files, copied)


__all__ = ["load_inventory", "verify_gc_profile_copy", "verify_source_tree"]
