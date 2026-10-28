from __future__ import annotations

import hashlib
import json
import struct
import zipfile

import pytest

from chrom_platform.artifact_core.model_package_manifest import (
    PackageValidationError,
    inspect_model_package,
    parse_safetensors_header,
)


def _safetensors(*, offsets: tuple[int, int] = (0, 4), payload: bytes = b"\0" * 4) -> bytes:
    header = json.dumps({"weight": {"dtype": "F32", "shape": [1], "data_offsets": list(offsets)}}).encode()
    return struct.pack("<Q", len(header)) + header + payload


def _package_bytes() -> tuple[bytes, dict[str, object]]:
    weights = _safetensors()
    golden = b'{"reference_id":"server-owned-fixture-only"}'
    manifest = {
        "schema_version": "1.0",
        "version": "gc-seq240408-250c-safetensors-v1",
        "input_schema_version": "1.1",
        "output_schema_version": "gc-retention-time-1",
        "task_type": "gc",
        "scope_ids": ["gc-default"],
        "loader_id": "gc-seq240408-safetensors-v1",
        "format": "safetensors",
        "preprocessing_protocol_id": "gc-preprocessing-rdkit-seed42-v1",
        "members": {
            "weights.safetensors": {"sha256": hashlib.sha256(weights).hexdigest(), "size_bytes": len(weights)},
            "golden.json": {"sha256": hashlib.sha256(golden).hexdigest(), "size_bytes": len(golden)},
        },
    }
    content = {
        "manifest.json": json.dumps(manifest, separators=(",", ":")).encode(),
        "weights.safetensors": weights,
        "golden.json": golden,
    }
    return content, manifest


def _write_package(path, members: dict[str, bytes]) -> tuple[str, int]:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, value in members.items():
            archive.writestr(name, value)
    data = path.read_bytes()
    return hashlib.sha256(data).hexdigest(), len(data)


def test_safetensors_header_checks_layout_before_loading() -> None:
    header = parse_safetensors_header(_safetensors())
    assert header.tensor_count == 1
    assert header.data_bytes == 4
    assert header.dtype_counts == {"F32": 1}


def test_safetensors_header_rejects_gap_or_wrong_shape_size() -> None:
    with pytest.raises(PackageValidationError, match="SAFETENSORS_OFFSETS_INVALID"):
        parse_safetensors_header(_safetensors(offsets=(1, 5), payload=b"\0" * 5))


def test_package_inspection_binds_outer_and_member_hashes(tmp_path) -> None:
    members, _ = _package_bytes()
    package_path = tmp_path / "package.zip"
    sha256, size = _write_package(package_path, members)

    inspected = inspect_model_package(package_path, expected_sha256=sha256, expected_size_bytes=size)

    assert inspected.container_sha256 == sha256
    assert inspected.member_hashes["weights.safetensors"] == hashlib.sha256(members["weights.safetensors"]).hexdigest()
    assert set(inspected.member_hashes) == {"manifest.json", "weights.safetensors", "golden.json"}
    assert set(inspected.member_sizes) == {"manifest.json", "weights.safetensors", "golden.json"}
    assert inspected.weights_header.tensor_count == 1
    assert inspected.limits_approved is False


def test_package_inspection_rejects_duplicate_or_unexpected_members(tmp_path) -> None:
    members, _ = _package_bytes()
    package_path = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, value in members.items():
            archive.writestr(name, value)
        archive.writestr("../escape", b"x")
    data = package_path.read_bytes()

    with pytest.raises(PackageValidationError):
        inspect_model_package(package_path, expected_sha256=hashlib.sha256(data).hexdigest(), expected_size_bytes=len(data))


def test_package_inspection_rejects_container_hash_mismatch(tmp_path) -> None:
    members, _ = _package_bytes()
    package_path = tmp_path / "package.zip"
    _, size = _write_package(package_path, members)

    with pytest.raises(PackageValidationError, match="PACKAGE_CONTAINER_HASH_MISMATCH"):
        inspect_model_package(package_path, expected_sha256="0" * 64, expected_size_bytes=size)
