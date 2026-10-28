"""Isolated filesystem mechanics; scanner calls are fixed fakes, never Defender."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from chrom_platform.artifact_core import model_package_scan
from chrom_platform.artifact_core.model_package_manifest import (
    GOLDEN_NAME,
    MANIFEST_NAME,
    WEIGHTS_NAME,
    InspectedPackage,
)
from chrom_platform.artifact_core.storage import ScanTarget


def _package(container: bytes = b"container") -> tuple[InspectedPackage, ScanTarget]:
    contents = {
        MANIFEST_NAME: b'{"data":"manifest"}',
        WEIGHTS_NAME: b"not-loaded-weight-bytes",
        GOLDEN_NAME: b'{"data":"golden"}',
    }
    path = Path("C:/server-store/objects/pkg/content")
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()}
    sizes = {name: len(data) for name, data in contents.items()}
    package = InspectedPackage(
        manifest={}, manifest_bytes=contents[MANIFEST_NAME],
        weights_bytes=contents[WEIGHTS_NAME], golden_bytes=contents[GOLDEN_NAME],
        member_hashes=hashes, member_sizes=sizes,
        weights_header=SimpleNamespace(),
        container_sha256=hashlib.sha256(container).hexdigest(),
        container_size_bytes=len(container), limits_approved=False,
    )
    # Windows and some filesystems report device zero; inode still must be real.
    target = ScanTarget("objects/pkg/content", path, (0, 2), len(container), package.container_sha256)
    return package, target


class _Store:
    def __init__(self) -> None:
        self.assertions = 0

    def assert_scan_target_unchanged(self, target: ScanTarget) -> None:
        self.assertions += 1


class _Scanner:
    def __init__(self, root: Path, *, fail_on: int | None = None) -> None:
        self.root = root
        self.fail_on = fail_on
        self.calls: list[dict[str, object]] = []

    def scan(self, **kwargs):
        self.calls.append(kwargs)
        assert Path(kwargs["path"]).is_file()
        if self.fail_on == len(self.calls):
            return SimpleNamespace(status="FAILED", evidence=None)
        return SimpleNamespace(
            status="PASSED",
            evidence=SimpleNamespace(
                artifact_id=kwargs["artifact_id"],
                scan_attempt_id=kwargs["scan_attempt_id"],
                input_sha256=kwargs["input_sha256"],
                input_size_bytes=kwargs["input_size_bytes"],
                verdict="CLEAN", exit_code=0, output_truncated=False,
                error_code=None, profile_id="approved-fixed-profile",
            ),
        )


def _approve_test_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_package_scan.scanner_defender, "_PROFILE_EVIDENCE_APPROVED", True)
    monkeypatch.setattr(model_package_scan.scanner_defender, "_APPROVED_PROFILES", {
        "fixed-product": SimpleNamespace(product_version="fixed-product", profile_id="approved-fixed-profile"),
    })


def test_closed_profile_gate_writes_nothing_and_never_calls_scanner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_package_scan.scanner_defender, "_PROFILE_EVIDENCE_APPROVED", False)
    monkeypatch.setattr(model_package_scan.scanner_defender, "_APPROVED_PROFILES", {})
    package, target = _package()
    scanner = _Scanner(tmp_path)
    with pytest.raises(model_package_scan.ModelPackageScanError, match="SCANNER_PROFILE_UNAVAILABLE"):
        model_package_scan.scan_model_package_members(
            package=package, target=target, store=_Store(), scanner=scanner, artifact_id="pkg1",
        )
    assert scanner.calls == []
    assert list(tmp_path.iterdir()) == []


def test_three_members_are_hash_checked_scanned_and_removed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _approve_test_profile(monkeypatch)
    container = b"container"
    package, target = _package(container)
    target_path = tmp_path / "content"
    target_path.write_bytes(container)
    target = ScanTarget(target.storage_key, target_path, target.identity, target.size_bytes, target.sha256)
    store = _Store()
    scanner = _Scanner(tmp_path)

    members = model_package_scan.scan_model_package_members(
        package=package, target=target, store=store, scanner=scanner, artifact_id="pkg1",
    )

    assert [call["input_sha256"] for call in scanner.calls] == [
        package.member_hashes[name] for name in (MANIFEST_NAME, WEIGHTS_NAME, GOLDEN_NAME)
    ]
    assert [item["name"] for item in members] == [MANIFEST_NAME, WEIGHTS_NAME, GOLDEN_NAME]
    assert all(item["scan_status"] == "PASSED" for item in members)
    assert store.assertions == 2
    assert list(tmp_path.glob(".model-package-scan-*")) == []
    assert all("path" not in item for item in members)


def test_failed_member_scan_stops_and_cleans_quarantine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _approve_test_profile(monkeypatch)
    package, target = _package()
    target_path = tmp_path / "content"
    target_path.write_bytes(b"container")
    target = ScanTarget(target.storage_key, target_path, target.identity, target.size_bytes, target.sha256)
    scanner = _Scanner(tmp_path, fail_on=2)

    with pytest.raises(model_package_scan.ModelPackageScanError, match="PACKAGE_MEMBER_SCAN_NOT_PASSED"):
        model_package_scan.scan_model_package_members(
            package=package, target=target, store=_Store(), scanner=scanner, artifact_id="pkg1",
        )

    assert len(scanner.calls) == 2
    assert list(tmp_path.glob(".model-package-scan-*")) == []


def test_cleanup_failure_invalidates_scan_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _approve_test_profile(monkeypatch)
    package, target = _package()
    target_path = tmp_path / "content"
    target_path.write_bytes(b"container")
    target = ScanTarget(target.storage_key, target_path, target.identity, target.size_bytes, target.sha256)
    monkeypatch.setattr(model_package_scan.shutil, "rmtree", lambda _path: (_ for _ in ()).throw(OSError("blocked")))

    with pytest.raises(model_package_scan.ModelPackageScanError, match="PACKAGE_QUARANTINE_CLEANUP_FAILED"):
        model_package_scan.scan_model_package_members(
            package=package, target=target, store=_Store(), scanner=_Scanner(tmp_path), artifact_id="pkg1",
        )


def test_parent_identity_change_after_scan_blocks_success_and_rmtree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _approve_test_profile(monkeypatch)
    package, target = _package()
    target_path = tmp_path / "content"
    target_path.write_bytes(b"container")
    target = ScanTarget(target.storage_key, target_path, target.identity, target.size_bytes, target.sha256)
    changed = {"value": False}
    real_assert = model_package_scan._assert_quarantine_location

    def assert_parent_unchanged(*args, **kwargs) -> None:
        real_assert(*args, **kwargs)
        if changed["value"]:
            raise model_package_scan.ModelPackageScanError("PACKAGE_QUARANTINE_PARENT_CHANGED")

    class _ChangesParentAfterScan(_Scanner):
        def scan(self, **kwargs):
            result = super().scan(**kwargs)
            changed["value"] = True
            return result

    cleanup_calls: list[Path] = []
    monkeypatch.setattr(model_package_scan, "_assert_quarantine_location", assert_parent_unchanged)
    monkeypatch.setattr(model_package_scan.shutil, "rmtree", lambda path: cleanup_calls.append(Path(path)))
    scanner = _ChangesParentAfterScan(tmp_path)

    with pytest.raises(model_package_scan.ModelPackageScanError, match="PACKAGE_QUARANTINE_PARENT_CHANGED"):
        model_package_scan.scan_model_package_members(
            package=package, target=target, store=_Store(), scanner=scanner, artifact_id="pkg1",
        )

    assert len(scanner.calls) == 1
    assert cleanup_calls == []
