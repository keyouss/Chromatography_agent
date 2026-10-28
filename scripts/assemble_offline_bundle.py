"""Assemble and hash one profile-specific offline deployment bundle.

The command consumes already staged frontend/backend profile packages. It never
installs dependencies, invokes Docker, copies from a checkout at runtime, or
creates a scientific model asset. A release ZIP is emitted only when every
required runtime and asset check is satisfied.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import stat
import zipfile
from pathlib import Path
from typing import Any


PROFILES = ("universal-v1", "sfc-v1", "cc-v1", "minimal-structure-v1")
WORKER_MODULES = frozenset({"prediction", "knowledge", "training", "model_center", "assistant"})
BASE_SERVICES = ("api", "frontend", "mysql", "redis", "nginx")
SERVICES = BASE_SERVICES + ("worker",)
IMAGE_KEYS = {
    "api": "CHROM_API_IMAGE",
    "worker": "CHROM_WORKER_IMAGE",
    "frontend": "CHROM_FRONTEND_IMAGE",
    "mysql": "MYSQL_IMAGE",
    "redis": "REDIS_IMAGE",
    "nginx": "NGINX_IMAGE",
}
SHA256 = re.compile(r"^[0-9a-f]{64}$")
IMMUTABLE_IMAGE = re.compile(r"^\S+@sha256:[0-9a-f]{64}$")
ABSOLUTE_CHECKS = (
    re.compile(r"[A-Za-z]:[\\/]Jupyter[\\/]Chromatography_agent", re.I),
    re.compile(r"(?:^|[\s\"'])/(?:workspace|app|repo)/", re.I),
)
FORBIDDEN_TEXT = (
    re.compile(r"8010"),
    re.compile(r"unified_server\.py", re.I),
    re.compile(r"docker\.sock", re.I),
    re.compile(r"/var/run/docker", re.I),
    re.compile(r"(?:^|[\s\"'])/api/predict(?:[\s\"']|$)"),
    # Do not reject ordinary words such as ``shell`` in a deny-list or a
    # validation message.  Reject actual runtime component names instead.
    re.compile(r"(?:coding[_-](?:agent|worker|workspace)|sandbox[_-](?:worker|workspace)|shell[_-](?:agent|worker|workspace))", re.I),
)


class BundleBlocked(RuntimeError):
    def __init__(self, blockers: list[dict[str, str]]):
        super().__init__("offline bundle is blocked")
        self.blockers = blockers


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def _inside(root: Path, child: Path) -> bool:
    try:
        child.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _scan_tree(root: Path, blockers: list[dict[str, str]]) -> None:
    for path in root.rglob("*"):
        if path.is_symlink():
            blockers.append({"code": "STAGED_SYMLINK_FORBIDDEN", "path": path.relative_to(root).as_posix()})
            continue
        if not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        relative = path.relative_to(root).as_posix()
        for pattern in ABSOLUTE_CHECKS:
            if pattern.search(text):
                blockers.append({"code": "CHECKOUT_ABSOLUTE_PATH", "path": relative})
                break
        for pattern in FORBIDDEN_TEXT:
            if pattern.search(text):
                blockers.append({"code": "FORBIDDEN_RUNTIME_REFERENCE", "path": relative})
                break


def _read_env_assignments(path: Path) -> dict[str, str]:
    """Read the small KEY=value template used by an offline bundle."""

    result: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key.strip()):
            raise ValueError("invalid environment file syntax")
        key = key.strip()
        if key in result:
            raise ValueError(f"duplicate environment key: {key}")
        result[key] = value.strip().strip('"').strip("'")
    return result


def _validate_env_template(
    env_template: Path,
    image_lock: dict[str, Any],
    blockers: list[dict[str, str]],
    *,
    worker_enabled: bool,
) -> None:
    if not env_template.is_file() or env_template.is_symlink():
        blockers.append({"code": "ENV_TEMPLATE_MISSING", "path": str(env_template)})
        return
    try:
        values = _read_env_assignments(env_template)
    except (OSError, ValueError) as exc:
        blockers.append({"code": "ENV_TEMPLATE_INVALID", "path": str(exc)})
        return
    required_keys = {IMAGE_KEYS[name] for name in (SERVICES if worker_enabled else BASE_SERVICES)}
    for key in required_keys:
        if not values.get(key, "").strip():
            blockers.append({"code": "ENV_IMAGE_MISSING", "path": key})
    if worker_enabled and not values.get("WORKER_COMMAND", "").strip():
        blockers.append({"code": "WORKER_COMMAND_MISSING", "path": "WORKER_COMMAND"})
    if values.get("BUILD_PROFILE", "") and values["BUILD_PROFILE"] not in PROFILES:
        blockers.append({"code": "ENV_PROFILE_UNSUPPORTED", "path": values["BUILD_PROFILE"]})


def _validate_model_assets(backend: Path, types: list[str], blockers: list[dict[str, str]]) -> None:
    if not types:
        return
    manifest_path = backend / "model-assets" / "manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        blockers.append({"code": "MODEL_ASSETS_MISSING", "path": "model-assets/manifest.json"})
        return
    try:
        manifest = _read_json(manifest_path)
        assets = manifest.get("assets")
        if not isinstance(assets, list) or not assets:
            raise ValueError("assets must be a non-empty list")
        expected_paths = {"manifest.json"}
        for item in assets:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not SHA256.fullmatch(str(item.get("sha256", ""))):
                raise ValueError("each asset needs a relative path and sha256")
            asset = (backend / "model-assets" / item["path"]).resolve()
            if not _inside((backend / "model-assets").resolve(), asset) or not asset.is_file() or asset.is_symlink():
                raise ValueError(f"missing asset: {item['path']}")
            if _sha256(asset) != item["sha256"]:
                raise ValueError(f"asset hash mismatch: {item['path']}")
            relative = asset.relative_to(backend / "model-assets").as_posix()
            if relative in expected_paths:
                raise ValueError(f"duplicate asset: {relative}")
            expected_paths.add(relative)
        actual_paths = {
            path.relative_to(backend / "model-assets").as_posix()
            for path in (backend / "model-assets").rglob("*")
            if path.is_file() and not path.is_symlink() and "__pycache__" not in path.parts and path.suffix != ".pyc"
        }
        if actual_paths != expected_paths:
            raise ValueError("model asset files differ from manifest")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        blockers.append({"code": "MODEL_ASSETS_INVALID", "path": str(exc)})


def _safe_relative(value: object) -> str | None:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        return None
    path = Path(value)
    if ".." in path.parts or "" in path.parts:
        return None
    normalized = path.as_posix()
    return normalized if normalized == value.replace("\\", "/") else None


def _migration_revision(path: Path) -> tuple[str, tuple[str, ...]]:
    """Read Alembic revision metadata without importing a staged package."""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id in {"revision", "down_revision"}:
                values[node.targets[0].id] = ast.literal_eval(node.value)
    revision = values.get("revision")
    down = values.get("down_revision")
    if not isinstance(revision, str) or not revision:
        # Tiny test/staging roots may omit Alembic boilerplate.  Only infer a
        # root from its conventional 0001 filename; all real descendants
        # must carry their declared graph metadata.
        if path.stem.startswith("0001_"):
            return path.stem, ()
        raise ValueError(f"migration revision missing: {path.name}")
    if down is None:
        return revision, ()
    if isinstance(down, str):
        return revision, (down,)
    if isinstance(down, (tuple, list)) and all(isinstance(item, str) and item for item in down):
        return revision, tuple(down)
    raise ValueError(f"migration down_revision invalid: {path.name}")


def _validate_migration_graph(backend: Path, declared: object) -> None:
    if not isinstance(declared, list) or not declared:
        raise ValueError("migrations must be a non-empty list")
    declared_paths = {_safe_relative(item) for item in declared}
    if None in declared_paths:
        raise ValueError("migration path is unsafe")
    paths = {path.relative_to(backend).as_posix() for path in (backend / "alembic" / "versions").glob("*.py") if path.name != "__init__.py"}
    if paths != declared_paths:
        raise ValueError("staged migration files differ from SOURCE_MANIFEST")
    revisions: dict[str, tuple[str, ...]] = {}
    for relative in sorted(paths):
        revision, down = _migration_revision(backend / relative)
        if revision in revisions:
            raise ValueError(f"duplicate migration revision: {revision}")
        revisions[revision] = down
    for revision, down in revisions.items():
        missing = [item for item in down if item not in revisions]
        if missing:
            raise ValueError(f"migration {revision} depends on missing revision(s): {','.join(missing)}")
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(revision: str) -> None:
        if revision in visiting:
            raise ValueError(f"migration graph cycle: {revision}")
        if revision in visited:
            return
        visiting.add(revision)
        for parent in revisions[revision]:
            visit(parent)
        visiting.remove(revision)
        visited.add(revision)

    for revision in revisions:
        visit(revision)


def _validate_source_manifest(backend: Path, profile: str, build_manifest: dict[str, Any], blockers: list[dict[str, str]]) -> None:
    """Ensure the staged backend is the physically trimmed profile it claims."""

    source_path = backend / "SOURCE_MANIFEST.json"
    if not source_path.is_file() or source_path.is_symlink():
        blockers.append({"code": "SOURCE_MANIFEST_MISSING", "path": "SOURCE_MANIFEST.json"})
        return
    try:
        source = _read_json(source_path)
        if source.get("build_id") != profile:
            blockers.append({"code": "SOURCE_PROFILE_MISMATCH", "path": "SOURCE_MANIFEST.json"})
        if set(source.get("modules", [])) != set(build_manifest.get("modules", [])):
            blockers.append({"code": "SOURCE_MODULES_MISMATCH", "path": "SOURCE_MANIFEST.json"})
        if set(source.get("chromatography_types", [])) != set(build_manifest.get("chromatography_types", [])):
            blockers.append({"code": "SOURCE_TYPES_MISMATCH", "path": "SOURCE_MANIFEST.json"})
        rows = source.get("files")
        if not isinstance(rows, list) or not rows:
            raise ValueError("files must be a non-empty list")
        expected: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("file row must be an object")
            relative = _safe_relative(row.get("path"))
            if relative is None or not SHA256.fullmatch(str(row.get("sha256", ""))):
                raise ValueError("file row requires a safe relative path and sha256")
            if relative in expected:
                raise ValueError(f"duplicate file: {relative}")
            expected.add(relative)
            target = backend / Path(relative)
            if not target.is_file() or target.is_symlink() or _sha256(target) != row["sha256"]:
                raise ValueError(f"file hash mismatch or missing: {relative}")
        actual = {
            path.relative_to(backend).as_posix()
            for path in backend.rglob("*")
            if path.is_file() and not path.is_symlink()
            and path.name != "SOURCE_MANIFEST.json"
            and "__pycache__" not in path.parts and path.suffix != ".pyc"
            and not path.relative_to(backend).as_posix().startswith("model-assets/")
        }
        expected_non_assets = {item for item in expected if not item.startswith("model-assets/")}
        if actual != expected_non_assets:
            raise ValueError("staged files differ from SOURCE_MANIFEST")

        modules = set(build_manifest.get("modules", []))
        _validate_migration_graph(backend, source.get("migrations"))
        worker_paths = set(source.get("workers", []))
        expected_workers = {f"chrom_platform/modules/{module}/worker.py" for module in (modules & {"prediction", "knowledge", "training", "model_center", "assistant"})}
        if worker_paths != expected_workers:
            raise ValueError("worker set does not match selected modules")
        worker_entrypoint = source.get("worker_entrypoint")
        expected_entrypoint = "chrom_platform/worker_cli.py" if modules & WORKER_MODULES else None
        if expected_entrypoint is not None and worker_entrypoint != expected_entrypoint:
            raise ValueError("worker entrypoint does not match selected modules")
        if expected_entrypoint is not None and not (backend / expected_entrypoint).is_file():
            raise ValueError("worker entrypoint missing from staged profile")
        if expected_entrypoint is None and (backend / "chrom_platform" / "worker_cli.py").exists():
            raise ValueError("worker entrypoint must be trimmed from workerless profile")
        requirements_path = backend / "requirements-profile.txt"
        dependencies = source.get("dependencies")
        if not requirements_path.is_file() or not isinstance(dependencies, list):
            raise ValueError("dependency manifest missing")
        declared = [line.strip() for line in requirements_path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.lstrip().startswith("#")]
        if declared != dependencies:
            raise ValueError("dependency lock differs from SOURCE_MANIFEST")
        if "assistant" not in modules and any(str(item).lower().startswith("pydantic-ai") for item in dependencies):
            raise ValueError("assistant dependency present in a profile without assistant")
        module_root = backend / "chrom_platform" / "modules"
        selected_dirs = {"data_center"} if modules & {"prediction", "records", "datasets"} else set()
        selected_dirs.update(modules & {"prediction", "knowledge", "training", "model_center", "assistant"})
        actual_dirs = {item.name for item in module_root.iterdir() if item.is_dir()} if module_root.is_dir() else set()
        if actual_dirs != selected_dirs:
            raise ValueError("module source directories are not physically trimmed")
    except (OSError, TypeError, ValueError, SyntaxError, json.JSONDecodeError) as exc:
        blockers.append({"code": "SOURCE_MANIFEST_INVALID", "path": str(exc)})


def _validate_inputs(profile: str, backend: Path, frontend: Path, image_lock: Path | None, env_template: Path) -> list[dict[str, str]]:
    blockers: list[dict[str, str]] = []
    if profile not in PROFILES:
        blockers.append({"code": "PROFILE_UNSUPPORTED", "path": profile})
    for label, path in (("backend", backend), ("frontend", frontend)):
        if not path.is_dir():
            blockers.append({"code": "STAGED_PACKAGE_MISSING", "path": f"{label}:{path}"})
    if not backend.is_dir() or not frontend.is_dir():
        return blockers
    backend_manifest_path = backend / "build-manifest.json"
    frontend_manifest_path = frontend / "ASSEMBLED_PROFILE.json"
    try:
        backend_manifest = _read_json(backend_manifest_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        blockers.append({"code": "BACKEND_MANIFEST_MISSING_OR_INVALID", "path": str(exc)})
        backend_manifest = {}
    try:
        frontend_manifest = _read_json(frontend_manifest_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        blockers.append({"code": "FRONTEND_MANIFEST_MISSING_OR_INVALID", "path": str(exc)})
        frontend_manifest = {}
    if backend_manifest.get("build_id") != profile:
        blockers.append({"code": "PROFILE_MISMATCH", "path": "backend/build-manifest.json"})
    if frontend_manifest.get("build_id") != profile:
        blockers.append({"code": "PROFILE_MISMATCH", "path": "frontend/ASSEMBLED_PROFILE.json"})
    if isinstance(frontend_manifest.get("modules"), list) and set(frontend_manifest["modules"]) != set(backend_manifest.get("modules", [])):
        blockers.append({"code": "PROFILE_MODULES_MISMATCH", "path": "frontend/ASSEMBLED_PROFILE.json"})
    if isinstance(frontend_manifest.get("chromatography_types"), list) and set(frontend_manifest["chromatography_types"]) != set(backend_manifest.get("chromatography_types", [])):
        blockers.append({"code": "PROFILE_TYPES_MISMATCH", "path": "frontend/ASSEMBLED_PROFILE.json"})
    if not (frontend / "dist" / "index.html").is_file():
        blockers.append({"code": "FRONTEND_DIST_MISSING", "path": "frontend/dist/index.html"})
    frontend_modules = frontend_manifest.get("modules")
    module_root = frontend / "src" / "modules"
    if isinstance(frontend_modules, list):
        actual_modules = {item.name for item in module_root.iterdir() if item.is_dir()} if module_root.is_dir() else set()
        if actual_modules != set(frontend_modules):
            blockers.append({"code": "FRONTEND_MODULES_UNTRIMMED", "path": "frontend/src/modules"})
    generated_manifest = frontend / "src" / "app" / "generated" / "build-manifest.json"
    if generated_manifest.is_file():
        try:
            if _read_json(generated_manifest).get("build_id") != profile:
                blockers.append({"code": "FRONTEND_GENERATED_MANIFEST_MISMATCH", "path": "frontend/src/app/generated/build-manifest.json"})
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            blockers.append({"code": "FRONTEND_GENERATED_MANIFEST_INVALID", "path": str(exc)})
    _validate_model_assets(backend, list(backend_manifest.get("chromatography_types", [])), blockers)
    _validate_source_manifest(backend, profile, backend_manifest, blockers)
    worker_enabled = bool(set(backend_manifest.get("modules", [])) & WORKER_MODULES)
    required_services = SERVICES if worker_enabled else BASE_SERVICES
    lock_data: dict[str, Any] | None = None
    if image_lock is None or not image_lock.is_file() or image_lock.is_symlink():
        blockers.append({"code": "IMAGE_LOCK_MISSING", "path": "image-lock.json"})
    else:
        try:
            lock = _read_json(image_lock)
            lock_data = lock
            for service in required_services:
                value = lock.get(service)
                if not isinstance(value, str) or not IMMUTABLE_IMAGE.fullmatch(value):
                    blockers.append({"code": "IMAGE_NOT_IMMUTABLE", "path": service})
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            blockers.append({"code": "IMAGE_LOCK_INVALID", "path": str(exc)})
    _scan_tree(backend, blockers)
    _scan_tree(frontend, blockers)
    # A successful bundle must be directly usable by Compose.  The repository
    # template intentionally leaves this command blank; a staged release must
    # provide the audited worker command and the exact locked image values.
    if lock_data is not None:
        _validate_env_template(env_template, lock_data, blockers, worker_enabled=worker_enabled)
    return blockers


def _copy_tree(source: Path, target: Path) -> None:
    shutil.copytree(source, target, symlinks=False, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))


def _write_sums(root: Path) -> None:
    rows: list[str] = []
    for path in sorted((item for item in root.rglob("*") if item.is_file()), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if relative in {"SHA256SUMS", "bundle.sha256"}:
            continue
        rows.append(f"{_sha256(path)}  {relative}")
    (root / "SHA256SUMS").write_text("\n".join(rows) + "\n", encoding="utf-8")


def _zip_bundle(root: Path, zip_path: Path) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    files = sorted((item for item in root.rglob("*") if item.is_file()), key=lambda item: item.relative_to(root).as_posix())
    folded: set[str] = set()
    for path in files:
        relative = path.relative_to(root).as_posix()
        key = relative.casefold()
        if key in folded:
            raise BundleBlocked([{"code": "ZIP_CASE_COLLISION", "path": relative}])
        folded.add(key)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            relative = path.relative_to(root).as_posix()
            info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())
    (zip_path.with_suffix(zip_path.suffix + ".sha256")).write_text(f"{_sha256(zip_path)}  {zip_path.name}\n", encoding="utf-8")


def _path_has_reparse_component(path: Path) -> bool:
    """Inspect raw path components before canonicalizing them."""

    raw = Path(os.path.abspath(os.fspath(path)))
    for item in (raw, *raw.parents):
        if not os.path.lexists(item):
            continue
        if item.is_symlink():
            return True
        try:
            attributes = int(getattr(os.lstat(item), "st_file_attributes", 0))
        except OSError:
            return True
        if attributes & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
            return True
    return False


def _output_blockers(output_dir: Path, inputs: tuple[Path, ...], profile: str) -> list[dict[str, str]]:
    raw_output = Path(os.path.abspath(os.fspath(output_dir)))
    if _path_has_reparse_component(raw_output):
        return [{"code": "OUTPUT_SYMLINK_FORBIDDEN", "path": str(output_dir)}]
    if os.path.lexists(raw_output):
        return [{"code": "OUTPUT_DIR_EXISTS", "path": str(output_dir)}]
    canonical_output = raw_output.resolve(strict=False)
    repository = Path(__file__).resolve().parents[1]
    if _inside(repository, canonical_output) or _inside(canonical_output, repository):
        return [{"code": "OUTPUT_OVERLAPS_REPOSITORY", "path": str(output_dir)}]
    if any(_inside(item, canonical_output) or _inside(canonical_output, item) for item in inputs):
        return [{"code": "OUTPUT_OVERLAPS_INPUT", "path": str(output_dir)}]
    zip_path = raw_output.parent / f"chrom-platform-{profile}.zip"
    if zip_path.exists() or zip_path.with_suffix(zip_path.suffix + ".sha256").exists():
        return [{"code": "OUTPUT_ARCHIVE_EXISTS", "path": str(zip_path)}]
    return []


def _remove_compose_section(text: str, name: str, *, until: str | None = None) -> str:
    lines = text.splitlines(keepends=True)
    start = next((index for index, line in enumerate(lines) if re.match(rf"^  {re.escape(name)}:\s*$", line)), None)
    if start is None:
        return text
    end = start + 1
    while end < len(lines):
        line = lines[end]
        if (until and re.match(rf"^{re.escape(until)}\s*$", line)) or (not until and re.match(r"^  \S.*:\s*$", line)):
            break
        end += 1
    del lines[start:end]
    return "".join(lines)


def _trim_worker_deployment_files(output_dir: Path, worker_enabled: bool) -> None:
    if worker_enabled:
        return
    compose = output_dir / "compose.yaml"
    compose_text = compose.read_text(encoding="utf-8")
    compose_text = _remove_compose_section(compose_text, "worker")
    compose_text = _remove_compose_section(compose_text, "worker-run", until="secrets:")
    compose.write_text(compose_text, encoding="utf-8")
    env = output_dir / ".env.example"
    env.write_text(
        "\n".join(line for line in env.read_text(encoding="utf-8").splitlines() if not re.match(r"^(?:CHROM_WORKER_IMAGE|WORKER_COMMAND)=", line)) + "\n",
        encoding="utf-8",
    )
    lock = output_dir / "image-lock.json"
    values = _read_json(lock)
    values.pop("worker", None)
    lock.write_text(json.dumps(values, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def assemble(*, profile: str, backend_package: Path, frontend_package: Path, output_dir: Path, image_lock: Path | None, env_template: Path) -> dict[str, Any]:
    backend_package = backend_package.resolve()
    frontend_package = frontend_package.resolve()
    output_dir = Path(os.path.abspath(os.fspath(output_dir)))
    env_template = env_template.resolve()
    output_blockers = _output_blockers(output_dir, (backend_package, frontend_package, env_template, image_lock.resolve() if image_lock else output_dir / "__missing_lock__"), profile)
    if output_blockers:
        raise BundleBlocked(output_blockers)
    blockers = _validate_inputs(profile, backend_package, frontend_package, image_lock.resolve() if image_lock else None, env_template)
    output_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"format": "chrom-platform-offline-bundle-v1", "profile": profile, "release_ready": not blockers, "blockers": blockers}
    if blockers:
        (output_dir / "OFFLINE_BUNDLE.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        raise BundleBlocked(blockers)
    (output_dir / "profiles" / profile).mkdir(parents=True)
    _copy_tree(backend_package, output_dir / "profiles" / profile / "backend")
    _copy_tree(frontend_package, output_dir / "profiles" / profile / "frontend")
    root = Path(__file__).resolve().parents[1]
    deploy_root = root / "deploy" / "v1"
    shutil.copy2(deploy_root / "compose.yaml", output_dir / "compose.yaml")
    (output_dir / "nginx").mkdir()
    shutil.copy2(deploy_root / "nginx" / "nginx.conf", output_dir / "nginx" / "nginx.conf")
    shutil.copy2(env_template.resolve(), output_dir / ".env.example")
    if image_lock is not None:
        shutil.copy2(image_lock.resolve(), output_dir / "image-lock.json")
        lock = _read_json(image_lock.resolve())
        env_path = output_dir / ".env.example"
        env = env_path.read_text(encoding="utf-8")
        env = re.sub(r"(?m)^BUILD_PROFILE=.*$", f"BUILD_PROFILE={profile}", env)
        worker_enabled = bool(set(_read_json(backend_package / "build-manifest.json").get("modules", [])) & WORKER_MODULES)
        for service in (SERVICES if worker_enabled else BASE_SERVICES):
            key = IMAGE_KEYS[service]
            value = str(lock[service])
            env = re.sub(rf"(?m)^{re.escape(key)}=.*$", f"{key}={value}", env)
        env_path.write_text(env, encoding="utf-8")
        final_env = _read_env_assignments(env_path)
        expected_services = SERVICES if worker_enabled else BASE_SERVICES
        if any(final_env.get(IMAGE_KEYS[service]) != lock[service] for service in expected_services):
            raise BundleBlocked([{"code": "ENV_IMAGE_MISMATCH", "path": ".env.example"}])
        _trim_worker_deployment_files(output_dir, worker_enabled)
    report["files"] = ["compose.yaml", "nginx/nginx.conf", ".env.example", "image-lock.json", f"profiles/{profile}/backend", f"profiles/{profile}/frontend"]
    (output_dir / "OFFLINE_BUNDLE.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_sums(output_dir)
    zip_path = output_dir.parent / f"chrom-platform-{profile}.zip"
    _zip_bundle(output_dir, zip_path)
    report["zip"] = zip_path.name
    # Keep the ZIP digest beside the archive. Embedding it in the archive
    # would be self-referential and would make deterministic hashing false.
    (output_dir / "OFFLINE_BUNDLE.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_sums(output_dir)
    _zip_bundle(output_dir, zip_path)
    # The digest is carried by the sidecar.  Embedding it in the archive would
    # be self-referential; return the final digest without rewriting the ZIP.
    report["zip_sha256"] = _sha256(zip_path)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, choices=PROFILES)
    parser.add_argument("--backend-package", type=Path, required=True)
    parser.add_argument("--frontend-package", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--image-lock", type=Path)
    parser.add_argument("--env-template", type=Path, default=Path(__file__).resolve().parents[1] / "deploy" / "v1" / ".env.example")
    args = parser.parse_args()
    try:
        report = assemble(profile=args.profile, backend_package=args.backend_package, frontend_package=args.frontend_package, output_dir=args.output_dir, image_lock=args.image_lock, env_template=args.env_template)
    except BundleBlocked as exc:
        print(json.dumps({"status": "BLOCKED", "blockers": exc.blockers}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps({"status": "READY", **report}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
