"""Verify an offline bundle directory or ZIP using only the Python stdlib."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any

try:
    from assemble_offline_bundle import BASE_SERVICES, SERVICES, WORKER_MODULES, IMAGE_KEYS, _read_env_assignments, _validate_model_assets, _validate_source_manifest
except ImportError:  # pragma: no cover - direct library use from another cwd
    BASE_SERVICES = ("api", "frontend", "mysql", "redis", "nginx")
    SERVICES = BASE_SERVICES + ("worker",)
    WORKER_MODULES = frozenset({"prediction", "knowledge", "training", "model_center", "assistant"})
    IMAGE_KEYS = {"api": "CHROM_API_IMAGE", "worker": "CHROM_WORKER_IMAGE", "frontend": "CHROM_FRONTEND_IMAGE", "mysql": "MYSQL_IMAGE", "redis": "REDIS_IMAGE", "nginx": "NGINX_IMAGE"}
    _read_env_assignments = _validate_model_assets = _validate_source_manifest = None


PROFILES = {"universal-v1", "sfc-v1", "cc-v1", "minimal-structure-v1"}
SERVICES = {"api", "worker", "frontend", "mysql", "redis", "nginx"}
IMMUTABLE_IMAGE = re.compile(r"^\S+@sha256:[0-9a-f]{64}$")
FORBIDDEN = (
    re.compile(r"8010"), re.compile(r"unified_server\.py", re.I),
    re.compile(r"docker\.sock", re.I), re.compile(r"/var/run/docker", re.I),
    re.compile(r"(?:coding[_-](?:agent|worker|workspace)|sandbox[_-](?:worker|workspace)|shell[_-](?:agent|worker|workspace))", re.I),
)
IMAGE_KEYS = {"api": "CHROM_API_IMAGE", "worker": "CHROM_WORKER_IMAGE", "frontend": "CHROM_FRONTEND_IMAGE", "mysql": "MYSQL_IMAGE", "redis": "REDIS_IMAGE", "nginx": "NGINX_IMAGE"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must be an object")
    return value


def verify(root: Path) -> list[dict[str, str]]:
    failures: list[dict[str, str]] = []
    report_path = root / "OFFLINE_BUNDLE.json"
    sums_path = root / "SHA256SUMS"
    compose_path = root / "compose.yaml"
    for path in (report_path, sums_path, compose_path, root / "nginx" / "nginx.conf", root / ".env.example", root / "image-lock.json"):
        if not path.is_file():
            failures.append({"code": "REQUIRED_FILE_MISSING", "path": path.relative_to(root).as_posix()})
    if failures:
        return failures
    compose_text = compose_path.read_text(encoding="utf-8")
    nginx_text = (root / "nginx" / "nginx.conf").read_text(encoding="utf-8") if (root / "nginx" / "nginx.conf").is_file() else ""
    if "./profiles/${BUILD_PROFILE" not in compose_text:
        failures.append({"code": "COMPOSE_PROFILE_PATH_MISSING", "path": "compose.yaml"})
    if '"--port", "9012"' not in compose_text or "api:9012" not in nginx_text:
        failures.append({"code": "API_PORT_NOT_9012", "path": "compose.yaml"})
    try:
        report = read_json(report_path)
        profile = report.get("profile")
        if profile not in PROFILES:
            failures.append({"code": "PROFILE_UNSUPPORTED", "path": str(profile)})
        if report.get("release_ready") is not True:
            failures.append({"code": "BUNDLE_NOT_RELEASE_READY", "path": "OFFLINE_BUNDLE.json"})
        lock = read_json(root / "image-lock.json")
        try:
            env = _read_env_assignments(root / ".env.example") if _read_env_assignments else {}
            if env.get("BUILD_PROFILE") != profile:
                failures.append({"code": "ENV_PROFILE_MISMATCH", "path": ".env.example"})
        except (OSError, ValueError) as exc:
            failures.append({"code": "ENV_INVALID", "path": str(exc)})
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        failures.append({"code": "METADATA_INVALID", "path": str(exc)})
        return failures
    if isinstance(profile, str):
        backend = root / "profiles" / profile / "backend"
        frontend = root / "profiles" / profile / "frontend"
        for path in (backend / "build-manifest.json", frontend / "ASSEMBLED_PROFILE.json", frontend / "dist" / "index.html"):
            if not path.is_file():
                failures.append({"code": "STAGED_FILE_MISSING", "path": path.relative_to(root).as_posix()})
        backend_manifest: dict[str, Any] = {}
        try:
            backend_manifest = read_json(backend / "build-manifest.json")
            if backend_manifest.get("build_id") != profile:
                failures.append({"code": "PROFILE_MISMATCH", "path": "backend/build-manifest.json"})
            if read_json(frontend / "ASSEMBLED_PROFILE.json").get("build_id") != profile:
                failures.append({"code": "PROFILE_MISMATCH", "path": "frontend/ASSEMBLED_PROFILE.json"})
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            failures.append({"code": "STAGED_MANIFEST_INVALID", "path": str(exc)})
        worker_enabled = bool(set(backend_manifest.get("modules", [])) & WORKER_MODULES)
        required_services = SERVICES if worker_enabled else BASE_SERVICES
        for service in required_services:
            if not re.search(rf"(?m)^  {service}:\s*$", compose_text):
                failures.append({"code": "COMPOSE_SERVICE_MISSING", "path": service})
        if not worker_enabled and re.search(r"(?m)^  worker:\s*$", compose_text):
            failures.append({"code": "COMPOSE_SERVICE_UNEXPECTED", "path": "worker"})
        if not worker_enabled and re.search(r"(?m)^  worker-run:\s*$", compose_text):
            failures.append({"code": "COMPOSE_VOLUME_UNEXPECTED", "path": "worker-run"})
        try:
            lock = read_json(root / "image-lock.json")
            env = _read_env_assignments(root / ".env.example") if _read_env_assignments else {}
            for service in required_services:
                key = IMAGE_KEYS[service]
                if not isinstance(lock.get(service), str) or not IMMUTABLE_IMAGE.fullmatch(lock[service]):
                    failures.append({"code": "IMAGE_NOT_IMMUTABLE", "path": service})
                if env.get(key) != lock.get(service):
                    failures.append({"code": "ENV_IMAGE_MISMATCH", "path": key})
            if worker_enabled and not env.get("WORKER_COMMAND", "").strip():
                failures.append({"code": "WORKER_COMMAND_MISSING", "path": ".env.example"})
            if not worker_enabled and ("worker" in lock or "CHROM_WORKER_IMAGE" in env or "WORKER_COMMAND" in env):
                failures.append({"code": "WORKER_CONFIGURATION_UNEXPECTED", "path": ".env.example"})
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            failures.append({"code": "DEPLOYMENT_METADATA_INVALID", "path": str(exc)})
        if backend.is_dir() and (backend / "build-manifest.json").is_file() and _validate_source_manifest is not None:
            try:
                backend_manifest = read_json(backend / "build-manifest.json")
                _validate_source_manifest(backend, profile, backend_manifest, failures)
                _validate_model_assets(backend, list(backend_manifest.get("chromatography_types", [])), failures)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                failures.append({"code": "STAGED_MANIFEST_INVALID", "path": str(exc)})
        if frontend.is_dir():
            manifest = read_json(frontend / "ASSEMBLED_PROFILE.json") if (frontend / "ASSEMBLED_PROFILE.json").is_file() else {}
            expected_modules = set(manifest.get("modules", []))
            module_root = frontend / "src" / "modules"
            if module_root.is_dir():
                actual_modules = {item.name for item in module_root.iterdir() if item.is_dir()}
                if actual_modules != expected_modules:
                    failures.append({"code": "FRONTEND_MODULES_UNTRIMMED", "path": "frontend/src/modules"})
    for path in root.rglob("*"):
        if path.is_symlink():
            failures.append({"code": "BUNDLE_SYMLINK_FORBIDDEN", "path": path.relative_to(root).as_posix()})
    text_files = [path for path in root.rglob("*") if path.is_file() and not path.is_symlink() and path.stat().st_size <= 8 * 1024 * 1024]
    for path in text_files:
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for pattern in FORBIDDEN:
            if pattern.search(text):
                failures.append({"code": "FORBIDDEN_RUNTIME_REFERENCE", "path": path.relative_to(root).as_posix()})
                break
    expected_files: dict[str, str] = {}
    for raw in sums_path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        digest, separator, relative = raw.partition("  ")
        if not separator or not re.fullmatch(r"[0-9a-f]{64}", digest):
            failures.append({"code": "SHA256SUMS_INVALID", "path": raw})
            continue
        if not relative or Path(relative).is_absolute() or ".." in Path(relative).parts or "\\" in relative:
            failures.append({"code": "SHA256_PATH_ESCAPE", "path": relative})
            continue
        target = (root / relative).resolve()
        if target.parent != root.resolve() and root.resolve() not in target.parents:
            failures.append({"code": "SHA256_PATH_ESCAPE", "path": relative})
            continue
        if not target.is_file() or target.name in {"SHA256SUMS", "bundle.sha256"}:
            failures.append({"code": "SHA256_TARGET_MISSING", "path": relative})
            continue
        if relative in expected_files:
            failures.append({"code": "SHA256_DUPLICATE", "path": relative})
            continue
        expected_files[relative] = digest
        if sha256(target) != digest:
            failures.append({"code": "SHA256_MISMATCH", "path": relative})
    actual_files = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file() and not path.is_symlink() and path.name not in {"SHA256SUMS", "bundle.sha256"}}
    if actual_files != set(expected_files):
        for path in sorted(actual_files - set(expected_files)):
            failures.append({"code": "UNLISTED_FILE", "path": path})
        for path in sorted(set(expected_files) - actual_files):
            failures.append({"code": "SHA256_TARGET_MISSING", "path": path})
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    args = parser.parse_args()
    source = args.bundle.resolve()
    archive_failures: list[dict[str, str]] = []
    with tempfile.TemporaryDirectory(prefix="chrom-offline-verify-") as temporary:
        root = source
        if source.is_file() and source.suffix.lower() == ".zip":
            sidecar = source.with_suffix(source.suffix + ".sha256")
            if not sidecar.is_file():
                archive_failures.append({"code": "ZIP_SHA256_SIDECAR_MISSING", "path": sidecar.name})
            else:
                fields = sidecar.read_text(encoding="utf-8").split()
                if len(fields) < 1 or not re.fullmatch(r"[0-9a-f]{64}", fields[0]) or fields[0] != sha256(source):
                    archive_failures.append({"code": "ZIP_SHA256_MISMATCH", "path": source.name})
            root = Path(temporary) / "bundle"
            root.mkdir()
            with zipfile.ZipFile(source) as archive:
                names: set[str] = set()
                folded_names: set[str] = set()
                for item in archive.infolist():
                    if item.filename in names:
                        print(json.dumps({"status": "BLOCKED", "failures": [{"code": "ZIP_DUPLICATE_PATH", "path": item.filename}]}, ensure_ascii=False, indent=2))
                        return 2
                    if item.filename.casefold() in folded_names:
                        print(json.dumps({"status": "BLOCKED", "failures": [{"code": "ZIP_CASE_COLLISION", "path": item.filename}]}, ensure_ascii=False, indent=2))
                        return 2
                    names.add(item.filename)
                    folded_names.add(item.filename.casefold())
                    if (item.external_attr >> 16) & 0o170000 == 0o120000:
                        print(json.dumps({"status": "BLOCKED", "failures": [{"code": "ZIP_SYMLINK_FORBIDDEN", "path": item.filename}]}, ensure_ascii=False, indent=2))
                        return 2
                    target = (root / item.filename).resolve()
                    if root.resolve() not in target.parents and target != root.resolve():
                        print(json.dumps({"status": "BLOCKED", "failures": [{"code": "ZIP_PATH_ESCAPE", "path": item.filename}]}, ensure_ascii=False, indent=2))
                        return 2
                archive.extractall(root)
        if not root.is_dir():
            print(json.dumps({"status": "BLOCKED", "failures": [{"code": "BUNDLE_MISSING", "path": str(source)}]}, ensure_ascii=False, indent=2))
            return 2
        failures = archive_failures + verify(root)
    print(json.dumps({"status": "READY" if not failures else "BLOCKED", "failures": failures}, ensure_ascii=False, indent=2))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
