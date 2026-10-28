from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from .app import create_app


def _serialize(document: dict[str, Any]) -> str:
    """Serialize every candidate with the one OpenAPI byte representation."""

    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _summary(document: dict[str, Any]) -> dict[str, Any]:
    operations = sorted(
        f"{method.upper()} {path}"
        for path, item in document["paths"].items()
        for method in item
        if method.lower() in {"get", "post", "put", "patch", "delete", "head", "options"}
    )
    return {
        "build_id": document["info"]["x-build-id"],
        "paths": sorted(document["paths"]),
        "operations": operations,
        "schemas": sorted(document.get("components", {}).get("schemas", {})),
    }


def _package_openapi(package: Path) -> dict[str, Any]:
    """Generate a document inside an isolated physical profile package.

    A package cannot be imported in-process after the source checkout's
    ``chrom_platform`` module has been loaded.  A child interpreter also
    proves that the resulting document really comes from the files shipped in
    that package and cannot accidentally resolve a discarded source module.
    """

    script = """
import json
import importlib.util
import sys
from pathlib import Path
package = Path.cwd().resolve()
sys.path.insert(0, str(package))
import chrom_platform
assert Path(chrom_platform.__file__).resolve().is_relative_to(package)
app_spec = importlib.util.find_spec('chrom_platform.app')
assert app_spec is not None and app_spec.origin is not None
assert Path(app_spec.origin).resolve().is_relative_to(package)
from chrom_platform import app as app_module
assert Path(app_module.__file__).resolve().is_relative_to(package)
app = app_module.create_app(Path('build-manifest.json'), contract_generation=True)
print(json.dumps(app.openapi(), ensure_ascii=False, sort_keys=True))
"""
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("CHROM_BUILD_MANIFEST", None)
    result = subprocess.run(
        [sys.executable, "-B", "-I", "-c", script],
        cwd=package,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def _write_candidate(
    document: dict[str, Any],
    target: Path,
    *,
    package: Path | None = None,
) -> Path:
    payload = _serialize(document)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(payload, encoding="utf-8")
    summary = _summary(document)
    summary["artifact_sha256"] = hashlib.sha256(target.read_bytes()).hexdigest().upper()
    summary["raw_sha256"] = summary["artifact_sha256"]
    canonical = (json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    summary["normalized_sha256"] = hashlib.sha256(canonical).hexdigest().upper()
    # Provenance is written next to (rather than into) the OpenAPI document so
    # the contract remains a pure OpenAPI object.  All values are captured from
    # the same package snapshot used to produce the document.
    if package is not None:
        inputs: dict[str, Any] = {}
        for name, key in (
            ("build-manifest.json", "build_manifest"),
            ("SOURCE_MANIFEST.json", "source_manifest"),
            ("requirements-profile.txt", "dependency_lock"),
        ):
            path = package / name
            inputs[key] = {
                "path": name,
                "present": path.is_file(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest().upper() if path.is_file() else None,
            }
        summary["generation_source"] = "isolated-profile-package"
        summary["build_inputs"] = inputs
        summary["source"] = {
            "manifest": "SOURCE_MANIFEST.json",
            "sha256": inputs["source_manifest"]["sha256"],
        }
        summary["direct_dependencies"] = [
            line.strip()
            for line in (package / "requirements-profile.txt").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ] if (package / "requirements-profile.txt").is_file() else []
        summary["dependency_manifest_scope"] = (
            "requirements-profile.txt pins direct dependencies only; "
            "it is not a complete transitive lock"
        )
    target.with_name("summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return target


def generate(
    builds_dir: Path,
    output_dir: Path,
    *,
    packages_dir: Path | None = None,
) -> list[Path]:
    """Generate profile OpenAPI files from one selected source of truth.

    ``packages_dir`` is required for release candidates: it makes the
    physical trimmed package the exact source used for the static artifact.
    The source-checkout mode remains useful for unit tests and development
    contracts, but it must not be used to claim package/runtime parity.
    """

    written: list[Path] = []
    seen_build_ids: set[str] = set()
    for manifest_path in sorted(builds_dir.glob("*.json")):
        if packages_dir is None:
            app = create_app(manifest_path=manifest_path, contract_generation=True)
            document = app.openapi()
            manifest = app.state.manifest
            package = None
        else:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            build_id = str(manifest["build_id"])
            package = packages_dir / build_id
            if not package.is_dir():
                raise FileNotFoundError(f"profile package missing: {package}")
            document = _package_openapi(package)
        build_id = manifest.build_id if hasattr(manifest, "build_id") else str(manifest["build_id"])
        if build_id in seen_build_ids:
            raise ValueError(f"duplicate build_id in build manifests: {build_id}")
        seen_build_ids.add(build_id)
        target = output_dir / build_id / "openapi.json"
        written.append(_write_candidate(document, target, package=package))
    return written


def generate_package(package: Path, output_dir: Path) -> Path:
    """Generate one static candidate from an already assembled package."""

    manifest = json.loads((package / "build-manifest.json").read_text(encoding="utf-8"))
    build_id = str(manifest["build_id"])
    document = _package_openapi(package)
    return _write_candidate(document, output_dir / build_id / "openapi.json", package=package)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate profile-specific OpenAPI documents")
    parser.add_argument("--builds-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--packages-dir",
        type=Path,
        help="generate from isolated assembled packages (required for release candidates)",
    )
    args = parser.parse_args()
    for path in generate(
        args.builds_dir.resolve(),
        args.output_dir.resolve(),
        packages_dir=args.packages_dir.resolve() if args.packages_dir else None,
    ):
        print(path)


if __name__ == "__main__":
    main()
