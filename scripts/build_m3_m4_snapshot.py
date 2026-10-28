"""Build one immutable M3/M4 snapshot from canonical profile package roots.

The builder deliberately calls the profile assembler once per explicit build
manifest.  It never discovers a package with a recursive glob and never copies
an already generated parent directory into the snapshot.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import date
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any

from chrom_platform.assembly.profile import assemble_profile
from chrom_platform.generate_openapi import generate_package


BUILD_IDS = ("minimal-structure-v1", "sfc-v1", "cc-v1", "universal-v1")
SNAPSHOT_PREFIX = "m3-m4-v1-candidate-"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _fingerprint(root: Path) -> dict[str, Any]:
    groups: dict[str, list[dict[str, str]]] = {}
    for label, directory, patterns in (
        ("business_source", root / "backend" / "src", ("*.py",)),
        ("build_scripts", root / "scripts", ("*.py", "*.ps1", "*.cmd", "*.json")),
    ):
        files: list[dict[str, str]] = []
        if directory.is_dir():
            for pattern in patterns:
                for path in directory.rglob(pattern):
                    if path.is_file() and "__pycache__" not in path.parts:
                        files.append({
                            "path": path.relative_to(root).as_posix(),
                            "sha256": _sha256(path),
                        })
        groups[label] = sorted(files, key=lambda item: item["path"])
    payload = json.dumps(groups, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "aggregate_sha256": hashlib.sha256(payload).hexdigest().upper(),
        "files": groups,
    }


def _next_destination(snapshot_parent: Path, requested: Path | None) -> Path:
    if requested is not None:
        destination = requested.resolve()
        if destination.exists():
            raise FileExistsError(f"immutable snapshot already exists: {destination}")
        return destination
    stamp = date.today().strftime("%Y%m%d")
    pattern = re.compile(rf"^{re.escape(SNAPSHOT_PREFIX)}{stamp}-(\d+)$")
    revisions = [
        int(match.group(1))
        for path in snapshot_parent.iterdir()
        if (match := pattern.match(path.name))
    ]
    revision = max(revisions, default=0) + 1
    return snapshot_parent / f"{SNAPSHOT_PREFIX}{stamp}-{revision:02d}"


def _tracked(package: Path) -> dict[str, str]:
    return {
        path.relative_to(package).as_posix(): _sha256(path)
        for path in package.rglob("*")
        if path.is_file()
        and path.name != "SOURCE_MANIFEST.json"
        and "__pycache__" not in path.parts
        and path.suffix.lower() != ".pyc"
    }


def _validate_package(package: Path, build_id: str) -> dict[str, Any]:
    manifest_path = package / "SOURCE_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = {str(item["path"]): str(item["sha256"]).upper() for item in manifest["files"]}
    actual = _tracked(package)
    if manifest.get("build_id") != build_id:
        raise ValueError(f"SOURCE_MANIFEST build_id mismatch: {package}")
    if set(expected) != set(actual):
        raise ValueError(f"SOURCE_MANIFEST file set mismatch: {package}")
    drift = [path for path in expected if expected[path] != actual[path]]
    if drift:
        raise ValueError(f"SOURCE_MANIFEST hash drift in {package}: {drift}")
    return {
        "build_id": build_id,
        "source_manifest_sha256": _sha256(manifest_path),
        "build_manifest_sha256": _sha256(package / "build-manifest.json"),
        "dependency_lock_sha256": _sha256(package / "requirements-profile.txt"),
        "file_count": len(actual),
    }


def build(root: Path, destination: Path | None = None, fingerprint_output: Path | None = None) -> dict[str, Any]:
    root = root.resolve()
    backend = root / "backend"
    builds = root / "builds"
    snapshot_parent = backend / ".generated" / "snapshots"
    snapshot_parent.mkdir(parents=True, exist_ok=True)
    final = _next_destination(snapshot_parent, destination)
    if final.exists():
        raise FileExistsError(f"immutable snapshot already exists: {final}")

    before = _fingerprint(root)
    record_path = (fingerprint_output or Path(tempfile.gettempdir()) / f"m3-m4-build-fingerprint-{final.name}.json").resolve()
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(json.dumps({"before": before}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    staging = Path(tempfile.mkdtemp(prefix=f".{final.name}.", dir=snapshot_parent))
    try:
        package_root = staging / "profile-packages"
        openapi_root = staging / "openapi"
        package_reports: list[dict[str, Any]] = []
        openapi_reports: list[dict[str, Any]] = []
        for build_id in BUILD_IDS:
            manifest_path = builds / f"{build_id}.json"
            package = assemble_profile(manifest_path, package_root, source_root=backend)
            expected_package = package_root / build_id
            if package.resolve() != expected_package.resolve():
                raise ValueError(f"non-canonical package output: {package}")
            package_reports.append(_validate_package(expected_package, build_id))
            static_path = generate_package(expected_package, openapi_root)
            expected_static = openapi_root / build_id / "openapi.json"
            if static_path.resolve() != expected_static.resolve():
                raise ValueError(f"non-canonical OpenAPI output: {static_path}")
            document = json.loads(expected_static.read_text(encoding="utf-8"))
            if document.get("info", {}).get("x-build-id") != build_id:
                raise ValueError(f"OpenAPI build binding mismatch: {expected_static}")
            summary = json.loads(expected_static.with_name("summary.json").read_text(encoding="utf-8"))
            if summary.get("build_id") != build_id:
                raise ValueError(f"OpenAPI summary binding mismatch: {expected_static}")
            inputs = summary.get("build_inputs", {})
            for key, path in (
                ("build_manifest", expected_package / "build-manifest.json"),
                ("source_manifest", expected_package / "SOURCE_MANIFEST.json"),
                ("dependency_lock", expected_package / "requirements-profile.txt"),
            ):
                if str(inputs.get(key, {}).get("sha256", "")).upper() != _sha256(path):
                    raise ValueError(f"OpenAPI provenance mismatch: {expected_static} ({key})")
            openapi_reports.append({
                "build_id": build_id,
                "path_count": len(document.get("paths", {})),
                "operation_count": sum(
                    1 for item in document.get("paths", {}).values()
                    for method in item
                    if method.lower() in {"get", "post", "put", "patch", "delete", "head", "options"}
                ),
                "openapi_sha256": _sha256(expected_static),
                "summary_sha256": _sha256(expected_static.with_name("summary.json")),
            })

        actual_direct = sorted(path.name for path in package_root.iterdir() if path.is_dir())
        if actual_direct != sorted(BUILD_IDS):
            raise ValueError(f"profile-packages root is not canonical: {actual_direct}")
        manifests = list(package_root.rglob("build-manifest.json"))
        if sorted(path.parent.relative_to(staging).as_posix() for path in manifests) != [
            f"profile-packages/{build_id}" for build_id in sorted(BUILD_IDS)
        ]:
            raise ValueError("recursive package discovery found a non-canonical directory")

        after = _fingerprint(root)
        if after != before:
            raise RuntimeError("business source or build-script fingerprint changed during generation")
        record_path.write_text(json.dumps({"before": before, "after": after}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        # The destination is absent and staging is a sibling on the same volume;
        # the rename publishes the complete, already validated tree in one step.
        os.replace(staging, final)
        return {
            "snapshot": str(final),
            "fingerprint_record": str(record_path),
            "fingerprint_sha256": before["aggregate_sha256"],
            "packages": package_reports,
            "openapi": openapi_reports,
            "root_directories": sorted(path.name for path in final.iterdir() if path.is_dir()),
        }
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--fingerprint-output", type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.root, args.destination, args.fingerprint_output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
