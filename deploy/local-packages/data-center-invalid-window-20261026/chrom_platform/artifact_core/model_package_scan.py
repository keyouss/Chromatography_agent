"""Fenced data-only quarantine and member scanning for model packages.

The module does not enable Defender profiles.  It can materialize member files
only after the fixed scanner profile gate has been approved by the build.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
import uuid
from pathlib import Path
from typing import Any, Mapping

from . import scanner_defender
from .model_package_manifest import (
    EXPECTED_MEMBERS,
    InspectedPackage,
    MANIFEST_NAME,
    WEIGHTS_NAME,
    GOLDEN_NAME,
    MAX_TOTAL_UNCOMPRESSED_BYTES,
)
from .storage import LocalArtifactStore, ScanTarget, StorageContentInvalid


class ModelPackageScanError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def package_scan_profiles_approved() -> bool:
    """Return true only when the fixed Defender evidence gate is populated."""

    profiles = scanner_defender._APPROVED_PROFILES
    if scanner_defender._PROFILE_EVIDENCE_APPROVED is not True or not isinstance(profiles, dict) or not profiles:
        return False
    for version, profile in profiles.items():
        if (not isinstance(version, str) or not version.strip()
                or not isinstance(getattr(profile, "profile_id", None), str)
                or not profile.profile_id.strip()
                or getattr(profile, "product_version", None) != version):
            return False
    profile_ids = [profile.profile_id for profile in profiles.values()]
    if len(profile_ids) != len(set(profile_ids)):
        return False
    return True


def approved_package_profile_ids() -> frozenset[str]:
    if not package_scan_profiles_approved():
        return frozenset()
    return frozenset(profile.profile_id for profile in scanner_defender._APPROVED_PROFILES.values())


def package_profile_matches(product_version: object, profile_id: object) -> bool:
    if not isinstance(product_version, str) or not isinstance(profile_id, str):
        return False
    profile = scanner_defender._APPROVED_PROFILES.get(product_version)
    return bool(
        package_scan_profiles_approved()
        and profile is not None
        and profile.product_version == product_version
        and profile.profile_id == profile_id
    )


def _member_bytes(package: InspectedPackage) -> Mapping[str, bytes]:
    return {
        MANIFEST_NAME: package.manifest_bytes,
        WEIGHTS_NAME: package.weights_bytes,
        GOLDEN_NAME: package.golden_bytes,
    }


def _write_member(
    directory: Path, name: str, content: bytes, expected_hash: str, expected_size: int,
) -> tuple[Path, tuple[int, int]]:
    if name not in EXPECTED_MEMBERS or len(content) != expected_size:
        raise ModelPackageScanError("PACKAGE_MEMBER_IDENTITY_INVALID")
    destination = directory / name
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(destination, flags, 0o600)
    except OSError as exc:
        raise ModelPackageScanError("PACKAGE_MEMBER_CREATE_FAILED") from exc
    digest = hashlib.sha256()
    written = 0
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as output:
            view = memoryview(content)
            for offset in range(0, len(view), 64 * 1024):
                chunk = view[offset:offset + 64 * 1024]
                output.write(chunk)
                digest.update(chunk)
                written += len(chunk)
                if written > expected_size:
                    raise ModelPackageScanError("PACKAGE_MEMBER_SIZE_INVALID")
            output.flush()
            os.fsync(output.fileno())
    except Exception:
        try:
            destination.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    info = destination.lstat()
    if (not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400
            or not info.st_ino or info.st_size != expected_size or digest.hexdigest() != expected_hash):
        raise ModelPackageScanError("PACKAGE_MEMBER_WRITE_IDENTITY_INVALID")
    # Re-read after close to detect mutation between writing and scanner handoff.
    second = hashlib.sha256()
    actual_size = 0
    with destination.open("rb") as source:
        opened = os.fstat(source.fileno())
        if (opened.st_dev, opened.st_ino, opened.st_size) != (info.st_dev, info.st_ino, info.st_size):
            raise ModelPackageScanError("PACKAGE_MEMBER_CHANGED")
        while chunk := source.read(64 * 1024):
            second.update(chunk)
            actual_size += len(chunk)
    if actual_size != expected_size or second.hexdigest() != expected_hash:
        raise ModelPackageScanError("PACKAGE_MEMBER_CHANGED")
    return destination, (int(info.st_dev), int(info.st_ino))


def _assert_member_unchanged(
    directory: Path,
    path: Path,
    *,
    identity: tuple[int, int],
    expected_hash: str,
    expected_size: int,
    parent: Path,
    resolved_parent: Path,
    parent_chain: tuple[tuple[Path, int, int], ...],
    directory_identity: tuple[int, int],
) -> None:
    _assert_quarantine_location(
        directory, parent=parent, resolved_parent=resolved_parent,
        parent_chain=parent_chain, directory_identity=directory_identity,
    )
    resolved_directory = resolved_parent / directory.name
    resolved_path = path.resolve(strict=True)
    if resolved_path.parent != resolved_directory:
        raise ModelPackageScanError("PACKAGE_MEMBER_PATH_INVALID")
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & 0x400
            or identity != (int(info.st_dev), int(info.st_ino))
            or info.st_size != expected_size):
        raise ModelPackageScanError("PACKAGE_MEMBER_CHANGED")
    digest = hashlib.sha256()
    actual_size = 0
    with path.open("rb") as source:
        opened = os.fstat(source.fileno())
        if (opened.st_dev, opened.st_ino, opened.st_size) != (info.st_dev, info.st_ino, info.st_size):
            raise ModelPackageScanError("PACKAGE_MEMBER_CHANGED")
        while chunk := source.read(64 * 1024):
            digest.update(chunk)
            actual_size += len(chunk)
    if actual_size != expected_size or digest.hexdigest() != expected_hash:
        raise ModelPackageScanError("PACKAGE_MEMBER_CHANGED")


def _capture_quarantine_parent(parent: Path) -> tuple[Path, tuple[tuple[Path, int, int], ...]]:
    try:
        lexical_parent = Path(os.path.abspath(parent))
        resolved_parent = parent.resolve(strict=True)
        chain: list[tuple[Path, int, int]] = []
        current = lexical_parent
        while True:
            info = current.lstat()
            if (stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400
                    or not stat.S_ISDIR(info.st_mode)
                    or current != Path(current.anchor) and not info.st_ino):
                raise ModelPackageScanError("PACKAGE_QUARANTINE_PARENT_INVALID")
            chain.append((current, int(info.st_dev), int(info.st_ino)))
            if current == current.parent:
                break
            current = current.parent
        if lexical_parent.resolve(strict=True) != resolved_parent:
            raise ModelPackageScanError("PACKAGE_QUARANTINE_PARENT_INVALID")
        return resolved_parent, tuple(chain)
    except ModelPackageScanError:
        raise
    except OSError as exc:
        raise ModelPackageScanError("PACKAGE_QUARANTINE_PARENT_INVALID") from exc


def _assert_quarantine_location(
    directory: Path,
    *,
    parent: Path,
    resolved_parent: Path,
    parent_chain: tuple[tuple[Path, int, int], ...],
    directory_identity: tuple[int, int],
) -> None:
    try:
        if parent.resolve(strict=True) != resolved_parent:
            raise ModelPackageScanError("PACKAGE_QUARANTINE_PARENT_CHANGED")
        for path, expected_device, expected_inode in parent_chain:
            info = path.lstat()
            if (stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400
                    or not stat.S_ISDIR(info.st_mode)
                    or (int(info.st_dev), int(info.st_ino)) != (expected_device, expected_inode)):
                raise ModelPackageScanError("PACKAGE_QUARANTINE_PARENT_CHANGED")
        info = directory.lstat()
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & 0x400
                or directory_identity != (int(info.st_dev), int(info.st_ino))
                or directory.resolve(strict=True) != resolved_parent / directory.name):
            raise ModelPackageScanError("PACKAGE_QUARANTINE_CHANGED")
    except ModelPackageScanError:
        raise
    except OSError as exc:
        raise ModelPackageScanError("PACKAGE_QUARANTINE_CHANGED") from exc


def scan_model_package_members(
    *,
    package: InspectedPackage,
    target: ScanTarget,
    store: LocalArtifactStore,
    scanner: object,
    artifact_id: str,
    lease_guard: object | None = None,
) -> list[dict[str, Any]]:
    """Materialize and scan the exact three members; fail unless all are CLEAN."""

    if not package_scan_profiles_approved():
        raise ModelPackageScanError("SCANNER_PROFILE_UNAVAILABLE")
    if not isinstance(package, InspectedPackage):
        raise ModelPackageScanError("PACKAGE_INSPECTION_INVALID")
    contents = _member_bytes(package)
    if set(contents) != EXPECTED_MEMBERS or set(package.member_hashes) != EXPECTED_MEMBERS or set(package.member_sizes) != EXPECTED_MEMBERS:
        raise ModelPackageScanError("PACKAGE_MEMBER_SET_INVALID")
    if sum(package.member_sizes.values()) > MAX_TOTAL_UNCOMPRESSED_BYTES:
        raise ModelPackageScanError("PACKAGE_MEMBER_TOTAL_SIZE_INVALID")
    if (package.container_sha256 != target.sha256 or package.container_size_bytes != target.size_bytes
            or len(target.identity) != 2
            or type(target.identity[0]) is not int or target.identity[0] < 0
            or type(target.identity[1]) is not int or target.identity[1] <= 0):
        raise ModelPackageScanError("PACKAGE_CONTAINER_BINDING_INVALID")

    directory: Path | None = None
    directory_identity: tuple[int, int] | None = None
    resolved_parent: Path | None = None
    parent_chain: tuple[tuple[Path, int, int], ...] = ()
    try:
        if lease_guard is not None:
            lease_guard.check()
        store.assert_scan_target_unchanged(target)
        parent = target.path.parent
        parent_info = parent.lstat()
        if (not stat.S_ISDIR(parent_info.st_mode)
                or stat.S_ISLNK(parent_info.st_mode)
                or getattr(parent_info, "st_file_attributes", 0) & 0x400):
            raise ModelPackageScanError("PACKAGE_QUARANTINE_PARENT_INVALID")
        resolved_parent, parent_chain = _capture_quarantine_parent(parent)
        directory = Path(tempfile.mkdtemp(prefix=".model-package-scan-", dir=parent))
        directory_info = directory.lstat()
        if (not stat.S_ISDIR(directory_info.st_mode) or stat.S_ISLNK(directory_info.st_mode)
                or getattr(directory_info, "st_file_attributes", 0) & 0x400):
            raise ModelPackageScanError("PACKAGE_QUARANTINE_INVALID")
        directory_identity = (int(directory_info.st_dev), int(directory_info.st_ino))
        if not directory_identity[1]:
            raise ModelPackageScanError("PACKAGE_QUARANTINE_IDENTITY_INVALID")

        members: list[dict[str, Any]] = []
        for name in (MANIFEST_NAME, WEIGHTS_NAME, GOLDEN_NAME):
            if lease_guard is not None:
                lease_guard.check()
            content = contents[name]
            member_hash = package.member_hashes[name]
            member_size = package.member_sizes[name]
            _assert_quarantine_location(
                directory, parent=parent, resolved_parent=resolved_parent,
                parent_chain=parent_chain, directory_identity=directory_identity,
            )
            path, member_identity = _write_member(directory, name, content, member_hash, member_size)
            _assert_member_unchanged(
                directory, path, identity=member_identity,
                expected_hash=member_hash, expected_size=member_size,
                parent=parent, resolved_parent=resolved_parent, parent_chain=parent_chain,
                directory_identity=directory_identity,
            )
            scan_attempt_id = f"sca_{uuid.uuid4().hex}"
            outcome = scanner.scan(
                path=path, artifact_id=artifact_id, input_sha256=member_hash,
                input_size_bytes=member_size, scan_attempt_id=scan_attempt_id,
            )
            if lease_guard is not None:
                lease_guard.check()
            _assert_member_unchanged(
                directory, path, identity=member_identity,
                expected_hash=member_hash, expected_size=member_size,
                parent=parent, resolved_parent=resolved_parent, parent_chain=parent_chain,
                directory_identity=directory_identity,
            )
            evidence = getattr(outcome, "evidence", None)
            status = getattr(outcome, "status", None)
            if status != "PASSED" or evidence is None:
                raise ModelPackageScanError("PACKAGE_MEMBER_SCAN_NOT_PASSED")
            approved_profiles = approved_package_profile_ids()
            if (getattr(evidence, "artifact_id", None) != artifact_id
                    or getattr(evidence, "scan_attempt_id", None) != scan_attempt_id
                    or getattr(evidence, "input_sha256", None) != member_hash
                    or getattr(evidence, "input_size_bytes", None) != member_size
                    or getattr(evidence, "verdict", None) != "CLEAN"
                    or getattr(evidence, "exit_code", None) != 0
                    or getattr(evidence, "output_truncated", None) is not False
                    or getattr(evidence, "error_code", None) is not None
                    or getattr(evidence, "profile_id", None) not in approved_profiles):
                raise ModelPackageScanError("PACKAGE_MEMBER_SCAN_EVIDENCE_INVALID")
            members.append({
                "name": name,
                "sha256": member_hash,
                "size_bytes": member_size,
                "scan_attempt_id": scan_attempt_id,
                "scan_status": status,
                "evidence": evidence,
            })
        if {item.name for item in directory.iterdir()} != EXPECTED_MEMBERS:
            raise ModelPackageScanError("PACKAGE_QUARANTINE_MEMBER_SET_INVALID")
        store.assert_scan_target_unchanged(target)
        return members
    except StorageContentInvalid as exc:
        raise ModelPackageScanError("ARTIFACT_CONTENT_IDENTITY_INVALID") from exc
    finally:
        if directory is not None:
            try:
                info = directory.lstat()
                if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                        or getattr(info, "st_file_attributes", 0) & 0x400
                        or directory_identity != (int(info.st_dev), int(info.st_ino))):
                    raise ModelPackageScanError("PACKAGE_QUARANTINE_CLEANUP_FAILED")
                _assert_quarantine_location(
                    directory, parent=target.path.parent, resolved_parent=resolved_parent,
                    parent_chain=parent_chain, directory_identity=directory_identity,
                )
                shutil.rmtree(directory)
            except ModelPackageScanError:
                raise
            except OSError as exc:
                # A failed cleanup invalidates the whole scan result.
                raise ModelPackageScanError("PACKAGE_QUARANTINE_CLEANUP_FAILED") from exc


__all__ = [
    "ModelPackageScanError", "approved_package_profile_ids",
    "package_profile_matches", "package_scan_profiles_approved", "scan_model_package_members",
]
