"""Build-owned evidence for trimmed GC package source bindings.

This module creates candidate receipts only. It does not approve or sign them.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import stat
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping


RECEIPT_VERSION = "1.0"
TRANSFORM_VERSION = "prediction-schemas-ast-trim-v1"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_REPARSE_FLAG = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_MANIFEST_FIELDS = frozenset({
    "manifest_version", "build_id", "modules", "chromatography_types",
    "prediction_tools", "utilities", "export_formats", "auth_provider",
    "assistant_features",
})

_GC_BASE_CLOSURE = frozenset({
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
_GC_MODEL_CENTER_CLOSURE = frozenset({
    "chrom_platform/modules/model_center/package_verification.py",
})
_SCHEMA_PATH = "chrom_platform/modules/prediction/schemas.py"
_PIN_PATH = "chrom_platform/adapters/gc/package_source_pins.py"
_PIN_ENTRY = re.compile(
    r'("chrom_platform/modules/prediction/schemas\.py"\s*:\s*")[0-9a-f]{64}(")'
)


def _sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def _pairs_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("MODEL_PACKAGE_RECEIPT_DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _read_json(path: str | Path) -> Any:
    try:
        return json.loads(
            Path(path).read_text(encoding="utf-8"),
            object_pairs_hook=_pairs_no_duplicates,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("MODEL_PACKAGE_RECEIPT_JSON_UNAVAILABLE") from exc


def _safe_relative_path(raw: object) -> str:
    if not isinstance(raw, str) or not raw or "\\" in raw:
        raise ValueError("MODEL_PACKAGE_RECEIPT_PATH_INVALID")
    posix = PurePosixPath(raw)
    windows = PureWindowsPath(raw)
    if (
        posix.is_absolute() or windows.is_absolute() or windows.drive
        or any(part in {"", ".", ".."} for part in raw.split("/"))
        or posix.as_posix() != raw
    ):
        raise ValueError("MODEL_PACKAGE_RECEIPT_PATH_INVALID")
    return raw


def _manifest_copy(manifest: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(manifest, Mapping) or set(manifest) != _MANIFEST_FIELDS:
        raise ValueError("MODEL_PACKAGE_RECEIPT_MANIFEST_NOT_NORMALIZED")
    # assemble_profile supplies BuildManifest.model_dump(mode="json"), so
    # nullable/default fields are present exactly as Pydantic serialized them.
    copied = json.loads(_canonical_json(dict(manifest)))
    if not isinstance(copied.get("build_id"), str) or not copied["build_id"]:
        raise ValueError("MODEL_PACKAGE_RECEIPT_MANIFEST_NOT_NORMALIZED")
    if not isinstance(copied.get("modules"), list) or not isinstance(copied.get("chromatography_types"), list):
        raise ValueError("MODEL_PACKAGE_RECEIPT_MANIFEST_NOT_NORMALIZED")
    return copied


def _selection_matrix(manifest: Mapping[str, Any]) -> tuple[frozenset[str], frozenset[str], str]:
    modules = set(manifest["modules"])
    chromatographies = set(manifest["chromatography_types"])
    if "gc" in chromatographies:
        if "model_center" in modules:
            return (
                _GC_BASE_CLOSURE | _GC_MODEL_CENTER_CLOSURE,
                frozenset(),
                "gc+model_center",
            )
        return (
            _GC_BASE_CLOSURE,
            frozenset({"chrom_platform/modules/model_center"}),
            "gc-only",
        )
    forbidden = {"chrom_platform/adapters/gc", "chrom_platform/assembly/gc_candidate_policy.py"}
    if "model_center" not in modules:
        forbidden.add("chrom_platform/modules/model_center")
    return frozenset(), frozenset(forbidden), "non-gc"


def expected_gc_closure(manifest: Mapping[str, Any]) -> tuple[frozenset[str], frozenset[str], str]:
    """Return required/forbidden fixed GC paths from the normalized manifest."""

    return _selection_matrix(_manifest_copy(manifest))


def profile_trim_parameters(manifest: Mapping[str, Any]) -> dict[str, Any]:
    normalized = _manifest_copy(manifest)
    modules = set(normalized["modules"])
    batch_enabled = any("batch" in tools for tools in normalized["prediction_tools"].values())
    return {
        "selected_modules": sorted(modules),
        "selected_chromatography_types": sorted(normalized["chromatography_types"]),
        "prediction_tools": normalized["prediction_tools"],
        "utilities": normalized["utilities"],
        "assistant_features": normalized["assistant_features"],
        "batch_enabled": batch_enabled,
        "task_sse_enabled": bool(modules & {
            "prediction", "records", "datasets", "knowledge", "training", "model_center", "assistant",
        }),
        "m2_selected": sorted(modules & {"prediction", "records", "datasets"}),
        "worker_modules": sorted(modules & {
            "prediction", "knowledge", "training", "model_center", "assistant",
        }),
        "preserve_batch_contract": normalized["build_id"] == "universal-v1",
    }


def runtime_boundary_note(matrix_id: str) -> str:
    if matrix_id == "gc-only":
        return (
            "source-binding-only; package_source_pins references "
            "chrom_platform/modules/model_center/package_verification.py, "
            "which the fixed GC-only profile excludes; loader.verify_integrity is not-ready"
        )
    return "source-binding-only; loader and model validation are not executed by receipt generation"


def derive_output_schema_pin(
    source_pin_path: str | Path,
    output_pin_path: str | Path,
    output_schema_path: str | Path,
) -> str:
    """Replace only the schema digest in the copied runtime pin module."""

    source_text = Path(source_pin_path).read_text(encoding="utf-8")
    matches = list(_PIN_ENTRY.finditer(source_text))
    if len(matches) != 1:
        raise ValueError("MODEL_PACKAGE_RECEIPT_SOURCE_PIN_INVALID")
    output_schema_sha = _sha256(output_schema_path)
    updated, count = _PIN_ENTRY.subn(
        lambda match: f"{match.group(1)}{output_schema_sha}{match.group(2)}",
        source_text,
        count=1,
    )
    if count != 1:
        raise ValueError("MODEL_PACKAGE_RECEIPT_SOURCE_PIN_INVALID")
    Path(output_pin_path).write_text(updated, encoding="utf-8")
    return output_schema_sha


def _load_source_integrity_helper():
    helper_path = Path(__file__).with_name("model_package_source_integrity.py")
    spec = importlib.util.spec_from_file_location("_derived_receipt_source_integrity", helper_path)
    if spec is None or spec.loader is None:
        raise ValueError("MODEL_PACKAGE_SOURCE_CHECK_UNAVAILABLE")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    return helper


def _expected_trimmed_schema(
    source_root: Path,
    manifest: Mapping[str, Any],
    profile_source_path: str | Path,
) -> tuple[bytes, str]:
    expected_profile = source_root / "chrom_platform/assembly/profile.py"
    trusted_profile = Path(profile_source_path).resolve(strict=True)
    if trusted_profile != expected_profile.resolve(strict=True):
        raise ValueError("MODEL_PACKAGE_RECEIPT_PROFILE_SOURCE_PATH_MISMATCH")

    module = sys.modules.get("chrom_platform.assembly.profile")
    if module is None:
        raise ValueError("MODEL_PACKAGE_RECEIPT_TRUSTED_PROFILE_NOT_PRELOADED")
    module_path = Path(getattr(module, "__file__", "")).resolve(strict=True)
    if module_path != trusted_profile:
        raise ValueError("MODEL_PACKAGE_RECEIPT_PROFILE_TRANSFORMER_NOT_TRUSTED")
    transformer = getattr(module, "_prediction_schemas", None)
    if not callable(transformer):
        raise ValueError("MODEL_PACKAGE_RECEIPT_PROFILE_TRANSFORMER_UNAVAILABLE")
    code_path = Path(transformer.__code__.co_filename).resolve(strict=True)
    if code_path != trusted_profile or transformer.__globals__ is not module.__dict__:
        raise ValueError("MODEL_PACKAGE_RECEIPT_PROFILE_TRANSFORMER_NOT_TRUSTED")

    source_schema = source_root / _SCHEMA_PATH
    source_text = source_schema.read_text(encoding="utf-8")
    params = profile_trim_parameters(manifest)
    expected_text = transformer(
        source_text,
        set(manifest["chromatography_types"]),
        batch_enabled=params["batch_enabled"],
    )
    # profile.py writes this text through Path.write_text with platform-default
    # newline translation. The expected hash is derived from trusted source
    # output, not from the candidate package or receipt.
    expected_bytes = expected_text.replace("\n", os.linesep).encode("utf-8")
    return expected_bytes, hashlib.sha256(expected_bytes).hexdigest()


def _verify_no_reparse_path(path: Path) -> Path:
    if any(part == ".." for part in path.parts):
        raise ValueError("MODEL_PACKAGE_RECEIPT_PATH_INVALID")
    absolute = Path(os.path.abspath(path))
    cursor = absolute
    while cursor != cursor.parent:
        try:
            metadata = os.lstat(cursor)
        except FileNotFoundError:
            cursor = cursor.parent
            continue
        if stat.S_ISLNK(metadata.st_mode) or (getattr(metadata, "st_file_attributes", 0) & _REPARSE_FLAG):
            raise ValueError("MODEL_PACKAGE_RECEIPT_REPARSE_POINT")
        cursor = cursor.parent
    return absolute.resolve(strict=False)


def _verified_package_paths(
    package_root: str | Path,
    source_manifest_path: str | Path,
) -> tuple[Path, Path]:
    package = _verify_no_reparse_path(Path(package_root))
    manifest = _verify_no_reparse_path(Path(source_manifest_path))
    if manifest != package / "SOURCE_MANIFEST.json":
        raise ValueError("MODEL_PACKAGE_RECEIPT_SOURCE_MANIFEST_PATH_MISMATCH")
    if not package.is_dir():
        raise ValueError("MODEL_PACKAGE_RECEIPT_PACKAGE_ROOT_INVALID")
    return package, manifest


def _verify_actual_build_manifest(
    package_root: Path,
    normalized_manifest: Mapping[str, Any],
    source_manifest_files: Mapping[str, str],
) -> None:
    relative = "build-manifest.json"
    actual_path = package_root / relative
    try:
        resolved = actual_path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ValueError("MODEL_PACKAGE_RECEIPT_BUILD_MANIFEST_MISSING") from exc
    try:
        resolved.relative_to(package_root.resolve(strict=True))
    except ValueError as exc:
        raise ValueError("MODEL_PACKAGE_RECEIPT_PATH_INVALID") from exc
    metadata = os.lstat(actual_path)
    if stat.S_ISLNK(metadata.st_mode) or (getattr(metadata, "st_file_attributes", 0) & _REPARSE_FLAG):
        raise ValueError("MODEL_PACKAGE_RECEIPT_REPARSE_POINT")
    actual_sha = _sha256(actual_path)
    if source_manifest_files.get(relative) != actual_sha:
        raise ValueError("MODEL_PACKAGE_RECEIPT_BUILD_MANIFEST_HASH_MISMATCH")
    if _read_json(actual_path) != dict(normalized_manifest):
        raise ValueError("MODEL_PACKAGE_RECEIPT_BUILD_MANIFEST_PROFILE_MISMATCH")


def validate_receipt_destination(
    receipt_path: str | Path,
    *,
    package_root: str | Path,
) -> Path:
    """Reject overwrite, package overlap, deliverables, traversal and reparse paths."""

    raw = os.fspath(receipt_path)
    raw_path = Path(raw)
    receipt = _verify_no_reparse_path(raw_path)
    package_raw = Path(package_root)
    package = _verify_no_reparse_path(package_raw)
    if receipt == package or package in receipt.parents or receipt in package.parents:
        raise ValueError("MODEL_PACKAGE_RECEIPT_OVERLAPS_PACKAGE")
    if any(part.casefold() == "deliverables" for part in (*receipt.parts, *package.parts)):
        raise ValueError("MODEL_PACKAGE_RECEIPT_DELIVERABLES_FORBIDDEN")
    if receipt.exists():
        raise FileExistsError("MODEL_PACKAGE_RECEIPT_ALREADY_EXISTS")
    existing_parent = receipt.parent
    while not existing_parent.exists() and existing_parent != existing_parent.parent:
        existing_parent = existing_parent.parent
    if not existing_parent.is_dir():
        raise NotADirectoryError("MODEL_PACKAGE_RECEIPT_PARENT_INVALID")
    return receipt


def _assert_no_forbidden(package_root: Path, forbidden: frozenset[str]) -> None:
    for relative in forbidden:
        path = package_root.joinpath(*PurePosixPath(relative).parts)
        try:
            metadata = os.lstat(path)
        except FileNotFoundError:
            metadata = None
        if metadata is not None:
            raise ValueError("MODEL_PACKAGE_RECEIPT_FORBIDDEN_CLOSURE_PRESENT")


def _verified_bound_hashes(
    source_root: Path,
    package_root: Path,
    manifest: Mapping[str, Any],
    profile_source_path: str | Path,
    inventory_path: str | Path | None,
) -> tuple[dict[str, str], dict[str, str], str]:
    source_integrity = _load_source_integrity_helper()
    source_hashes = source_integrity.load_inventory(inventory_path)
    source_integrity.verify_source_tree(source_root, inventory_path=inventory_path)
    required, forbidden, matrix_id = _selection_matrix(manifest)
    _assert_no_forbidden(package_root, forbidden)
    if not required:
        return {}, source_hashes, matrix_id

    # The set comes from this fixed matrix and source inventory contract,
    # never from a list supplied by the candidate receipt.
    if not required.issubset(source_hashes):
        raise ValueError("MODEL_PACKAGE_RECEIPT_SOURCE_CLOSURE_INVALID")
    expected_schema_bytes, expected_schema_sha = _expected_trimmed_schema(
        source_root, manifest, profile_source_path,
    )
    copied: dict[str, str] = {}
    for relative in sorted(required):
        relative = _safe_relative_path(relative)
        source_path = source_root.joinpath(*PurePosixPath(relative).parts)
        output_path = package_root.joinpath(*PurePosixPath(relative).parts)
        for root, item in ((source_root, source_path), (package_root, output_path)):
            resolved_root = root.resolve(strict=True)
            cursor = resolved_root
            for part in PurePosixPath(relative).parts:
                cursor = cursor / part
                try:
                    metadata = os.lstat(cursor)
                except FileNotFoundError:
                    metadata = None
                if metadata is not None and (
                    stat.S_ISLNK(metadata.st_mode)
                    or (getattr(metadata, "st_file_attributes", 0) & _REPARSE_FLAG)
                ):
                    raise ValueError("MODEL_PACKAGE_RECEIPT_REPARSE_POINT")
            try:
                resolved = item.resolve(strict=True)
            except FileNotFoundError as exc:
                raise ValueError("MODEL_PACKAGE_RECEIPT_FILE_MISSING") from exc
            resolved.relative_to(resolved_root)
            if not resolved.is_file():
                raise ValueError("MODEL_PACKAGE_RECEIPT_FILE_MISSING")
        source_digest = _sha256(source_path)
        output_digest = _sha256(output_path)
        if source_digest != source_hashes[relative]:
            raise ValueError("MODEL_PACKAGE_RECEIPT_SOURCE_HASH_MISMATCH")
        if relative == _SCHEMA_PATH and output_path.read_bytes() != expected_schema_bytes:
            raise ValueError("MODEL_PACKAGE_RECEIPT_TRIMMED_SCHEMA_MISMATCH")
        if relative not in {_SCHEMA_PATH, _PIN_PATH} and output_digest != source_digest:
            raise ValueError("MODEL_PACKAGE_RECEIPT_COPIED_HASH_MISMATCH")
        copied[relative] = output_digest

    source_pin_text = source_root.joinpath(*PurePosixPath(_PIN_PATH).parts).read_text(encoding="utf-8")
    output_pin_text = package_root.joinpath(*PurePosixPath(_PIN_PATH).parts).read_text(encoding="utf-8")
    schema_matches = list(_PIN_ENTRY.finditer(source_pin_text))
    if len(schema_matches) != 1:
        raise ValueError("MODEL_PACKAGE_RECEIPT_SOURCE_PIN_INVALID")
    original_schema_pin = schema_matches[0].group(0)
    digest_match = re.search(r':\s*"([0-9a-f]{64})"', original_schema_pin)
    if digest_match is None:
        raise ValueError("MODEL_PACKAGE_RECEIPT_SOURCE_PIN_INVALID")
    original_digest = digest_match.group(1)
    schema_digest = expected_schema_sha
    expected_output_pin, replacement_count = _PIN_ENTRY.subn(
        lambda match: f"{match.group(1)}{schema_digest}{match.group(2)}",
        source_pin_text,
        count=1,
    )
    if replacement_count != 1:
        raise ValueError("MODEL_PACKAGE_RECEIPT_SOURCE_PIN_INVALID")
    if output_pin_text != expected_output_pin:
        raise ValueError("MODEL_PACKAGE_RECEIPT_GENERATED_PIN_MISMATCH")
    if source_hashes[_SCHEMA_PATH] != original_digest:
        raise ValueError("MODEL_PACKAGE_RECEIPT_SOURCE_SCHEMA_PIN_MISMATCH")
    if original_schema_pin == "":
        raise ValueError("MODEL_PACKAGE_RECEIPT_SOURCE_PIN_INVALID")
    return copied, source_hashes, matrix_id


def create_candidate_receipt(
    *,
    manifest: Mapping[str, Any],
    source_root: str | Path,
    package_root: str | Path,
    source_manifest_path: str | Path,
    profile_source_path: str | Path,
    helper_path: str | Path,
    inventory_path: str | Path | None = None,
) -> dict[str, Any]:
    """Create a candidate-only receipt after SOURCE_MANIFEST is finalized."""

    normalized = _manifest_copy(manifest)
    if Path(helper_path).resolve(strict=True) != Path(__file__).resolve(strict=True):
        raise ValueError("MODEL_PACKAGE_RECEIPT_HELPER_PATH_MISMATCH")
    source = Path(source_root).resolve(strict=True)
    package, source_manifest = _verified_package_paths(package_root, source_manifest_path)
    helper = Path(helper_path).resolve(strict=True)
    profile_source = Path(profile_source_path).resolve(strict=True)
    copied, source_hashes, matrix_id = _verified_bound_hashes(
        source, package, normalized, profile_source, inventory_path,
    )
    required, _, _ = _selection_matrix(normalized)
    source_schema_sha = source_hashes[_SCHEMA_PATH] if required else None
    output_schema_sha = copied.get(_SCHEMA_PATH)
    output_pin_sha = copied.get(_PIN_PATH)
    source_pin_sha = source_hashes.get(_PIN_PATH)
    source_inventory_path = Path(inventory_path) if inventory_path is not None else Path(__file__).with_name("model_package_source_inventory.json")
    source_manifest_data = _read_json(source_manifest)
    manifest_files = source_manifest_data.get("files") if isinstance(source_manifest_data, dict) else None
    if not isinstance(manifest_files, list):
        raise ValueError("MODEL_PACKAGE_RECEIPT_SOURCE_MANIFEST_INVALID")
    if (
        source_manifest_data.get("normalized_build_manifest") != normalized
        or source_manifest_data.get("profile_trim_parameters") != profile_trim_parameters(normalized)
        or source_manifest_data.get("build_manifest_sha256") != hashlib.sha256(_canonical_json(normalized)).hexdigest()
    ):
        raise ValueError("MODEL_PACKAGE_RECEIPT_SOURCE_MANIFEST_PROFILE_MISMATCH")
    expected_profile_transform = {
        "version": TRANSFORM_VERSION,
        "profile_source_sha256": _sha256(profile_source),
        "derived_receipt_helper_sha256": _sha256(helper),
        "source_integrity_helper_sha256": _sha256(Path(__file__).with_name("model_package_source_integrity.py")),
        "source_inventory_sha256": _sha256(source_inventory_path),
    }
    if source_manifest_data.get("profile_transform") != expected_profile_transform:
        raise ValueError("MODEL_PACKAGE_RECEIPT_SOURCE_MANIFEST_TRANSFORM_MISMATCH")
    source_manifest_files: dict[str, str] = {}
    for row in manifest_files:
        if not isinstance(row, dict) or set(row) != {"path", "sha256"}:
            raise ValueError("MODEL_PACKAGE_RECEIPT_SOURCE_MANIFEST_INVALID")
        relative = _safe_relative_path(row["path"])
        if relative in source_manifest_files or not isinstance(row["sha256"], str) or _SHA256.fullmatch(row["sha256"]) is None:
            raise ValueError("MODEL_PACKAGE_RECEIPT_SOURCE_MANIFEST_INVALID")
        source_manifest_files[relative] = row["sha256"]
    _verify_actual_build_manifest(package, normalized, source_manifest_files)
    for relative, digest in copied.items():
        if source_manifest_files.get(relative) != digest:
            raise ValueError("MODEL_PACKAGE_RECEIPT_SOURCE_MANIFEST_HASH_MISMATCH")
    receipt = {
        "receipt_version": RECEIPT_VERSION,
        "status": "candidate",
        "approval": None,
        "validation_scope": "trimmed-source-binding-only",
        "runtime_loader_check": "not-run",
        "runtime_boundary_note": runtime_boundary_note(matrix_id),
        "build_id": normalized["build_id"],
        "selection_matrix": matrix_id,
        "normalized_build_manifest": normalized,
        "build_manifest_sha256": hashlib.sha256(_canonical_json(normalized)).hexdigest(),
        "trim_parameters": profile_trim_parameters(normalized),
        "source_binding": {
            "source_inventory_sha256": _sha256(source_inventory_path),
            "source_inventory_file_sha256": source_hashes,
            "profile_source_sha256": _sha256(profile_source),
            "source_integrity_helper_sha256": _sha256(Path(__file__).with_name("model_package_source_integrity.py")),
            "derived_receipt_helper_sha256": _sha256(helper),
            "transform_version": TRANSFORM_VERSION,
            "schema_source_sha256": source_schema_sha,
            "schema_output_sha256": output_schema_sha,
            "pin_source_sha256": source_pin_sha,
            "pin_output_sha256": output_pin_sha,
            "source_manifest_sha256": _sha256(source_manifest),
        },
        "required_closure_sha256": copied,
    }
    validate_candidate_receipt_data(
        receipt,
        manifest=normalized,
        source_root=source,
        package_root=package,
        source_manifest_path=source_manifest,
        profile_source_path=profile_source,
        helper_path=helper,
        inventory_path=inventory_path,
    )
    return receipt


def validate_candidate_receipt_data(
    receipt: Mapping[str, Any],
    *,
    manifest: Mapping[str, Any],
    source_root: str | Path,
    package_root: str | Path,
    source_manifest_path: str | Path,
    profile_source_path: str | Path,
    helper_path: str | Path,
    inventory_path: str | Path | None = None,
) -> None:
    """Verify candidate data against trusted caller inputs and fixed closure."""

    normalized = _manifest_copy(manifest)
    if Path(helper_path).resolve(strict=True) != Path(__file__).resolve(strict=True):
        raise ValueError("MODEL_PACKAGE_RECEIPT_HELPER_PATH_MISMATCH")
    package, source_manifest_path = _verified_package_paths(package_root, source_manifest_path)
    if not isinstance(receipt, Mapping) or set(receipt) != {
        "receipt_version", "status", "approval", "validation_scope",
        "runtime_loader_check", "runtime_boundary_note", "build_id", "selection_matrix",
        "normalized_build_manifest", "build_manifest_sha256", "trim_parameters",
        "source_binding", "required_closure_sha256",
    }:
        raise ValueError("MODEL_PACKAGE_RECEIPT_SCHEMA_INVALID")
    if (
        receipt["receipt_version"] != RECEIPT_VERSION
        or receipt["status"] != "candidate"
        or receipt["approval"] is not None
        or receipt["validation_scope"] != "trimmed-source-binding-only"
        or receipt["runtime_loader_check"] != "not-run"
        or receipt["build_id"] != normalized["build_id"]
        or receipt["normalized_build_manifest"] != normalized
        or receipt["build_manifest_sha256"] != hashlib.sha256(_canonical_json(normalized)).hexdigest()
    ):
        raise ValueError("MODEL_PACKAGE_RECEIPT_PROFILE_MISMATCH")
    required, forbidden, matrix_id = _selection_matrix(normalized)
    if receipt["selection_matrix"] != matrix_id:
        raise ValueError("MODEL_PACKAGE_RECEIPT_SELECTION_MISMATCH")
    if receipt["runtime_boundary_note"] != runtime_boundary_note(matrix_id):
        raise ValueError("MODEL_PACKAGE_RECEIPT_RUNTIME_BOUNDARY_MISMATCH")
    if not isinstance(receipt["required_closure_sha256"], dict) or set(receipt["required_closure_sha256"]) != required:
        raise ValueError("MODEL_PACKAGE_RECEIPT_CLOSURE_MISMATCH")
    if not isinstance(receipt["source_binding"], dict):
        raise ValueError("MODEL_PACKAGE_RECEIPT_BINDING_INVALID")
    _assert_no_forbidden(package, forbidden)
    copied, source_hashes, _ = _verified_bound_hashes(
        source_root, package, normalized, profile_source_path, inventory_path,
    )
    if copied != receipt["required_closure_sha256"]:
        raise ValueError("MODEL_PACKAGE_RECEIPT_CLOSURE_HASH_MISMATCH")
    source_manifest_data = _read_json(source_manifest_path)
    if (
        not isinstance(source_manifest_data, dict)
        or source_manifest_data.get("normalized_build_manifest") != normalized
        or source_manifest_data.get("build_manifest_sha256") != hashlib.sha256(_canonical_json(normalized)).hexdigest()
        or source_manifest_data.get("profile_trim_parameters") != profile_trim_parameters(normalized)
    ):
        raise ValueError("MODEL_PACKAGE_RECEIPT_SOURCE_MANIFEST_PROFILE_MISMATCH")
    expected_profile_transform = {
        "version": TRANSFORM_VERSION,
        "profile_source_sha256": _sha256(profile_source_path),
        "derived_receipt_helper_sha256": _sha256(helper_path),
        "source_integrity_helper_sha256": _sha256(Path(__file__).with_name("model_package_source_integrity.py")),
        "source_inventory_sha256": _sha256(
            Path(inventory_path) if inventory_path is not None else Path(__file__).with_name("model_package_source_inventory.json")
        ),
    }
    if source_manifest_data.get("profile_transform") != expected_profile_transform:
        raise ValueError("MODEL_PACKAGE_RECEIPT_SOURCE_MANIFEST_TRANSFORM_MISMATCH")
    manifest_files = source_manifest_data.get("files")
    if not isinstance(manifest_files, list):
        raise ValueError("MODEL_PACKAGE_RECEIPT_SOURCE_MANIFEST_INVALID")
    manifest_file_hashes: dict[str, str] = {}
    for row in manifest_files:
        if not isinstance(row, dict) or set(row) != {"path", "sha256"}:
            raise ValueError("MODEL_PACKAGE_RECEIPT_SOURCE_MANIFEST_INVALID")
        relative = _safe_relative_path(row["path"])
        if relative in manifest_file_hashes or not isinstance(row["sha256"], str) or _SHA256.fullmatch(row["sha256"]) is None:
            raise ValueError("MODEL_PACKAGE_RECEIPT_SOURCE_MANIFEST_INVALID")
        manifest_file_hashes[relative] = row["sha256"]
    _verify_actual_build_manifest(package, normalized, manifest_file_hashes)
    if any(manifest_file_hashes.get(relative) != digest for relative, digest in copied.items()):
        raise ValueError("MODEL_PACKAGE_RECEIPT_SOURCE_MANIFEST_HASH_MISMATCH")
    source_inventory_path = Path(inventory_path) if inventory_path is not None else Path(__file__).with_name("model_package_source_inventory.json")
    source_binding = receipt["source_binding"]
    expected_binding = {
        "source_inventory_sha256": _sha256(source_inventory_path),
        "source_inventory_file_sha256": source_hashes,
        "profile_source_sha256": _sha256(profile_source_path),
        "source_integrity_helper_sha256": _sha256(Path(__file__).with_name("model_package_source_integrity.py")),
        "derived_receipt_helper_sha256": _sha256(helper_path),
        "transform_version": TRANSFORM_VERSION,
        "schema_source_sha256": source_hashes[_SCHEMA_PATH] if required else None,
        "schema_output_sha256": copied.get(_SCHEMA_PATH),
        "pin_source_sha256": source_hashes.get(_PIN_PATH),
        "pin_output_sha256": copied.get(_PIN_PATH),
        "source_manifest_sha256": _sha256(source_manifest_path),
    }
    if source_binding != expected_binding:
        raise ValueError("MODEL_PACKAGE_RECEIPT_BINDING_MISMATCH")
    expected_trim_parameters = profile_trim_parameters(normalized)
    if receipt["trim_parameters"] != expected_trim_parameters:
        raise ValueError("MODEL_PACKAGE_RECEIPT_TRIM_PARAMETERS_MISMATCH")


def validate_candidate_receipt(
    receipt_path: str | Path,
    *,
    manifest: Mapping[str, Any],
    source_root: str | Path,
    package_root: str | Path,
    source_manifest_path: str | Path,
    profile_source_path: str | Path,
    helper_path: str | Path,
    inventory_path: str | Path | None = None,
) -> None:
    """Read a candidate receipt strictly and validate it against caller inputs."""

    receipt = _read_json(receipt_path)
    validate_candidate_receipt_data(
        receipt,
        manifest=manifest,
        source_root=source_root,
        package_root=package_root,
        source_manifest_path=source_manifest_path,
        profile_source_path=profile_source_path,
        helper_path=helper_path,
        inventory_path=inventory_path,
    )


def write_candidate_receipt(receipt_path: str | Path, receipt: Mapping[str, Any], *, package_root: str | Path) -> Path:
    destination = validate_receipt_destination(receipt_path, package_root=package_root)
    data = json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    try:
        with destination.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(data)
    except FileExistsError as exc:
        raise FileExistsError("MODEL_PACKAGE_RECEIPT_ALREADY_EXISTS") from exc
    return destination


__all__ = [
    "RECEIPT_VERSION", "TRANSFORM_VERSION", "create_candidate_receipt",
    "derive_output_schema_pin", "expected_gc_closure", "profile_trim_parameters",
    "runtime_boundary_note", "validate_candidate_receipt", "validate_candidate_receipt_data",
    "validate_receipt_destination", "write_candidate_receipt",
]
