"""Strict, data-only package inspection for controlled model artifacts.

This module performs bounded ZIP and safetensors-header inspection only.  It
does not import model code or allocate tensors.  The ceilings below are hard
deny bounds; production readiness remains disabled until a reviewed fixed
baseline approves them.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import struct
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


MANIFEST_NAME = "manifest.json"
WEIGHTS_NAME = "weights.safetensors"
GOLDEN_NAME = "golden.json"
EXPECTED_MEMBERS = frozenset({MANIFEST_NAME, WEIGHTS_NAME, GOLDEN_NAME})
FIXED_LOADER_ID = "gc-seq240408-safetensors-v1"
FIXED_FORMAT = "safetensors"

# Strict upper bounds only.  They do not constitute an approved deployment
# resource profile; see RESOURCE_LIMITS_APPROVED.
MAX_CONTAINER_BYTES = 32 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_BYTES = 32 * 1024 * 1024
MAX_MEMBER_BYTES = 24 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100
MAX_MANIFEST_BYTES = 64 * 1024
MAX_GOLDEN_BYTES = 256 * 1024
MAX_SAFETENSORS_HEADER_BYTES = 1024 * 1024
MAX_TENSORS = 4096
MAX_TENSOR_ELEMENTS = 8 * 1024 * 1024
MAX_TENSOR_BYTES = 24 * 1024 * 1024
RESOURCE_LIMITS_APPROVED = False

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_DTYPE_BYTES = {
    "BOOL": 1,
    "I8": 1,
    "U8": 1,
    "I16": 2,
    "I32": 4,
    "I64": 8,
    "F16": 2,
    "BF16": 2,
    "F32": 4,
    "F64": 8,
}
_MANIFEST_FIELDS = frozenset({
    "schema_version", "version", "input_schema_version", "output_schema_version",
    "task_type", "scope_ids", "loader_id", "format",
    "preprocessing_protocol_id", "members",
})


class PackageValidationError(ValueError):
    """A package failed a bounded structural or content check."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class SafetensorsHeader:
    header_bytes: int
    data_bytes: int
    tensor_count: int
    tensor_names: tuple[str, ...]
    dtype_counts: Mapping[str, int]


@dataclass(frozen=True)
class InspectedPackage:
    manifest: Mapping[str, Any]
    manifest_bytes: bytes
    weights_bytes: bytes
    golden_bytes: bytes
    member_hashes: Mapping[str, str]
    member_sizes: Mapping[str, int]
    weights_header: SafetensorsHeader
    container_sha256: str
    container_size_bytes: int
    limits_approved: bool


def _reject_constant(_: str) -> None:
    raise PackageValidationError("JSON_NONFINITE_NUMBER")


def _no_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PackageValidationError("JSON_DUPLICATE_KEY")
        result[key] = value
    return result


def _strict_json(data: bytes, *, code: str) -> Any:
    try:
        return json.loads(
            data.decode("utf-8", errors="strict"),
            object_pairs_hook=_no_duplicate_pairs,
            parse_constant=_reject_constant,
        )
    except PackageValidationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise PackageValidationError(code) from exc


def _valid_digest(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def validate_member_name(name: str) -> None:
    """Reject every name except the three exact package members."""
    if (not isinstance(name, str) or name not in EXPECTED_MEMBERS
            or "\\" in name or "/" in name or "\x00" in name
            or name.startswith(("/", "\\")) or ":" in name):
        raise PackageValidationError("PACKAGE_MEMBER_NAME_INVALID")


def _validate_manifest(value: object) -> Mapping[str, Any]:
    if not isinstance(value, dict) or set(value) != _MANIFEST_FIELDS:
        raise PackageValidationError("PACKAGE_MANIFEST_SCHEMA_INVALID")
    string_fields = (
        "schema_version", "version", "input_schema_version", "output_schema_version",
        "task_type", "loader_id", "format", "preprocessing_protocol_id",
    )
    if any(not isinstance(value.get(field), str) or not value[field].strip() for field in string_fields):
        raise PackageValidationError("PACKAGE_MANIFEST_SCHEMA_INVALID")
    scopes = value.get("scope_ids")
    if (not isinstance(scopes, list) or not scopes or len(scopes) > 32
            or any(not isinstance(item, str) or not item.strip() for item in scopes)
            or len(set(scopes)) != len(scopes)):
        raise PackageValidationError("PACKAGE_MANIFEST_SCHEMA_INVALID")
    members = value.get("members")
    expected_content = {WEIGHTS_NAME, GOLDEN_NAME}
    if not isinstance(members, dict) or set(members) != expected_content:
        raise PackageValidationError("PACKAGE_MANIFEST_MEMBERS_INVALID")
    for name, record in members.items():
        if (not isinstance(record, dict) or set(record) != {"sha256", "size_bytes"}
                or not _valid_digest(record.get("sha256"))
                or type(record.get("size_bytes")) is not int
                or record["size_bytes"] <= 0):
            raise PackageValidationError("PACKAGE_MANIFEST_MEMBERS_INVALID")
    return value


def parse_safetensors_header(data: bytes) -> SafetensorsHeader:
    """Validate a safetensors header and byte layout before tensor allocation."""
    if len(data) < 8:
        raise PackageValidationError("SAFETENSORS_HEADER_TRUNCATED")
    header_length = struct.unpack("<Q", data[:8])[0]
    if header_length <= 1 or header_length > MAX_SAFETENSORS_HEADER_BYTES:
        raise PackageValidationError("SAFETENSORS_HEADER_SIZE_INVALID")
    header_end = 8 + header_length
    if header_end > len(data):
        raise PackageValidationError("SAFETENSORS_HEADER_TRUNCATED")
    header = _strict_json(data[8:header_end], code="SAFETENSORS_HEADER_INVALID")
    if not isinstance(header, dict) or len(header) > MAX_TENSORS + 1:
        raise PackageValidationError("SAFETENSORS_HEADER_INVALID")
    data_bytes = len(data) - header_end
    spans: list[tuple[int, int]] = []
    dtype_counts: dict[str, int] = {}
    element_total = 0
    tensor_names: list[str] = []
    for name, spec in header.items():
        if name == "__metadata__":
            if (not isinstance(spec, dict)
                    or any(not isinstance(k, str) or not isinstance(v, str) for k, v in spec.items())):
                raise PackageValidationError("SAFETENSORS_METADATA_INVALID")
            continue
        if not isinstance(name, str) or not name or not isinstance(spec, dict):
            raise PackageValidationError("SAFETENSORS_TENSOR_INVALID")
        if set(spec) != {"dtype", "shape", "data_offsets"}:
            raise PackageValidationError("SAFETENSORS_TENSOR_INVALID")
        dtype = spec.get("dtype")
        shape = spec.get("shape")
        offsets = spec.get("data_offsets")
        if not isinstance(dtype, str) or dtype not in _DTYPE_BYTES or not isinstance(shape, list) or len(shape) > 16:
            raise PackageValidationError("SAFETENSORS_DTYPE_OR_SHAPE_INVALID")
        if any(type(dim) is not int or dim < 0 for dim in shape):
            raise PackageValidationError("SAFETENSORS_DTYPE_OR_SHAPE_INVALID")
        elements = 1
        for dimension in shape:
            if dimension > MAX_TENSOR_ELEMENTS:
                raise PackageValidationError("SAFETENSORS_TENSOR_TOO_LARGE")
            elements *= dimension
            if elements > MAX_TENSOR_ELEMENTS:
                raise PackageValidationError("SAFETENSORS_TENSOR_TOO_LARGE")
        if elements <= 0 or elements > MAX_TENSOR_ELEMENTS:
            raise PackageValidationError("SAFETENSORS_TENSOR_TOO_LARGE")
        element_total += elements
        if element_total > MAX_TENSOR_ELEMENTS:
            raise PackageValidationError("SAFETENSORS_TENSOR_TOO_LARGE")
        if (not isinstance(offsets, list) or len(offsets) != 2
                or any(type(v) is not int for v in offsets)):
            raise PackageValidationError("SAFETENSORS_OFFSETS_INVALID")
        start, end = offsets
        expected_size = elements * _DTYPE_BYTES[dtype]
        if (start < 0 or end <= start or end > data_bytes
                or end - start != expected_size or expected_size > MAX_TENSOR_BYTES):
            raise PackageValidationError("SAFETENSORS_OFFSETS_INVALID")
        spans.append((start, end))
        tensor_names.append(name)
        dtype_counts[dtype] = dtype_counts.get(dtype, 0) + 1
    if not tensor_names or len(tensor_names) > MAX_TENSORS:
        raise PackageValidationError("SAFETENSORS_TENSOR_COUNT_INVALID")
    spans.sort()
    cursor = 0
    for start, end in spans:
        if start != cursor:
            raise PackageValidationError("SAFETENSORS_OFFSETS_INVALID")
        cursor = end
    if cursor != data_bytes or data_bytes > MAX_TENSOR_BYTES:
        raise PackageValidationError("SAFETENSORS_DATA_SIZE_INVALID")
    return SafetensorsHeader(
        header_bytes=header_length,
        data_bytes=data_bytes,
        tensor_count=len(tensor_names),
        tensor_names=tuple(sorted(tensor_names)),
        dtype_counts=dict(sorted(dtype_counts.items())),
    )


def inspect_model_package(
    path: str | Path,
    *,
    expected_sha256: str,
    expected_size_bytes: int,
) -> InspectedPackage:
    """Read and hash only the fixed bounded package members."""
    package_path = Path(path)
    if not _valid_digest(expected_sha256) or type(expected_size_bytes) is not int or expected_size_bytes <= 0:
        raise PackageValidationError("PACKAGE_EXPECTED_IDENTITY_INVALID")
    try:
        before = package_path.stat(follow_symlinks=False)
        if (not stat.S_ISREG(before.st_mode) or before.st_size != expected_size_bytes
                or before.st_size > MAX_CONTAINER_BYTES or getattr(before, "st_file_attributes", 0) & 0x400):
            raise PackageValidationError("PACKAGE_CONTAINER_IDENTITY_INVALID")
        with package_path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (
                before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
            ):
                raise PackageValidationError("PACKAGE_CONTAINER_CHANGED")
            digest = hashlib.sha256()
            total = 0
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
                total += len(chunk)
                if total > MAX_CONTAINER_BYTES:
                    raise PackageValidationError("PACKAGE_CONTAINER_TOO_LARGE")
            if total != expected_size_bytes or digest.hexdigest() != expected_sha256:
                raise PackageValidationError("PACKAGE_CONTAINER_HASH_MISMATCH")
            stream.seek(0)
            with zipfile.ZipFile(stream, "r") as archive:
                infos = archive.infolist()
                if len(infos) != len(EXPECTED_MEMBERS):
                    raise PackageValidationError("PACKAGE_MEMBER_SET_INVALID")
                names: set[str] = set()
                total_uncompressed = 0
                for info in infos:
                    validate_member_name(info.filename)
                    if info.filename in names:
                        raise PackageValidationError("PACKAGE_DUPLICATE_MEMBER")
                    names.add(info.filename)
                    mode = (info.external_attr >> 16) & 0xFFFF
                    file_kind = stat.S_IFMT(mode)
                    if (stat.S_ISLNK(mode) or file_kind not in {0, stat.S_IFREG}
                            or info.is_dir() or info.flag_bits & (0x1 | 0x40 | 0x2000)):
                        raise PackageValidationError("PACKAGE_MEMBER_TYPE_INVALID")
                    if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                        raise PackageValidationError("PACKAGE_COMPRESSION_UNSUPPORTED")
                    cap = MAX_MANIFEST_BYTES if info.filename == MANIFEST_NAME else (
                        MAX_GOLDEN_BYTES if info.filename == GOLDEN_NAME else MAX_MEMBER_BYTES
                    )
                    if info.file_size <= 0 or info.file_size > cap:
                        raise PackageValidationError("PACKAGE_MEMBER_SIZE_INVALID")
                    total_uncompressed += info.file_size
                    if total_uncompressed > MAX_TOTAL_UNCOMPRESSED_BYTES:
                        raise PackageValidationError("PACKAGE_UNCOMPRESSED_SIZE_INVALID")
                    ratio = info.file_size / max(info.compress_size, 1)
                    if ratio > MAX_COMPRESSION_RATIO:
                        raise PackageValidationError("PACKAGE_COMPRESSION_RATIO_INVALID")
                if names != EXPECTED_MEMBERS:
                    raise PackageValidationError("PACKAGE_MEMBER_SET_INVALID")
                contents: dict[str, bytes] = {}
                for name in EXPECTED_MEMBERS:
                    info = next(item for item in infos if item.filename == name)
                    cap = MAX_MANIFEST_BYTES if name == MANIFEST_NAME else (
                        MAX_GOLDEN_BYTES if name == GOLDEN_NAME else MAX_MEMBER_BYTES
                    )
                    chunks: list[bytes] = []
                    read_size = 0
                    with archive.open(info, "r") as member_stream:
                        while chunk := member_stream.read(min(64 * 1024, cap - read_size + 1)):
                            read_size += len(chunk)
                            if read_size > cap or read_size > info.file_size:
                                raise PackageValidationError("PACKAGE_MEMBER_SIZE_INVALID")
                            chunks.append(chunk)
                    if read_size != info.file_size:
                        raise PackageValidationError("PACKAGE_MEMBER_SIZE_INVALID")
                    contents[name] = b"".join(chunks)
            stream.seek(0)
            final_digest = hashlib.sha256()
            final_size = 0
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                final_digest.update(chunk)
                final_size += len(chunk)
            after_open = os.fstat(stream.fileno())
        after = package_path.stat(follow_symlinks=False)
        if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after_open.st_dev, after_open.st_ino, after_open.st_size, after_open.st_mtime_ns)
                or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                or final_size != expected_size_bytes or final_digest.hexdigest() != expected_sha256):
            raise PackageValidationError("PACKAGE_CONTAINER_CHANGED")
    except PackageValidationError:
        raise
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, EOFError) as exc:
        raise PackageValidationError("PACKAGE_ARCHIVE_INVALID") from exc

    manifest_obj = _validate_manifest(_strict_json(contents[MANIFEST_NAME], code="PACKAGE_MANIFEST_INVALID"))
    golden_obj = _strict_json(contents[GOLDEN_NAME], code="PACKAGE_GOLDEN_INVALID")
    if not isinstance(golden_obj, dict):
        raise PackageValidationError("PACKAGE_GOLDEN_INVALID")
    # The manifest does not self-declare a digest.  The verifier still returns
    # its actual digest and size so scanner evidence can cover all three files.
    member_hashes = {name: hashlib.sha256(contents[name]).hexdigest() for name in EXPECTED_MEMBERS}
    member_sizes = {name: len(contents[name]) for name in EXPECTED_MEMBERS}
    for name in (WEIGHTS_NAME, GOLDEN_NAME):
        expected = manifest_obj["members"][name]
        if expected["sha256"] != member_hashes[name] or expected["size_bytes"] != member_sizes[name]:
            raise PackageValidationError("PACKAGE_MEMBER_HASH_MISMATCH")
    header = parse_safetensors_header(contents[WEIGHTS_NAME])
    return InspectedPackage(
        manifest=manifest_obj,
        manifest_bytes=contents[MANIFEST_NAME],
        weights_bytes=contents[WEIGHTS_NAME],
        golden_bytes=contents[GOLDEN_NAME],
        member_hashes=member_hashes,
        member_sizes=member_sizes,
        weights_header=header,
        container_sha256=expected_sha256,
        container_size_bytes=expected_size_bytes,
        limits_approved=RESOURCE_LIMITS_APPROVED,
    )


__all__ = [
    "FIXED_FORMAT", "FIXED_LOADER_ID", "GOLDEN_NAME", "InspectedPackage",
    "MANIFEST_NAME", "PackageValidationError", "RESOURCE_LIMITS_APPROVED",
    "SafetensorsHeader", "WEIGHTS_NAME", "inspect_model_package",
    "parse_safetensors_header", "validate_member_name",
]
