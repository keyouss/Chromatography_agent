"""Build and audit the immutable M3/M4 candidate-06 snapshot.

The script owns only generated build output and handoff evidence.  Product
source, active contracts, and candidate-05 are read-only inputs.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
SOURCE = BACKEND / "src"
BUILDS = ROOT / "builds"
SNAPSHOT_PARENT = BACKEND / ".generated" / "snapshots"
SNAPSHOT_NAME = "m3-m4-v1-candidate-20260921-06"
FINAL = SNAPSHOT_PARENT / SNAPSHOT_NAME
CANDIDATE_05 = SNAPSHOT_PARENT / "m3-m4-v1-candidate-20260921-05"
PROFILES = ("minimal-structure-v1", "sfc-v1", "cc-v1", "universal-v1")
M2_PROFILES = {"sfc-v1", "cc-v1", "universal-v1"}
EXPECTED_MIGRATIONS = {
    "minimal-structure-v1": ("0001_m1_persistence.py",),
    "sfc-v1": ("0001_m1_persistence.py", "0002_m2_prediction_data.py", "0007_worker_outbox_lease.py"),
    "cc-v1": ("0001_m1_persistence.py", "0002_m2_prediction_data.py", "0007_worker_outbox_lease.py"),
    "universal-v1": (
        "0001_m1_persistence.py", "0002_m2_prediction_data.py", "0003_m3_knowledge.py",
        "0004_m3_training.py", "0005_m3_model_center.py", "0006_m4_assistant.py",
        "0007_worker_outbox_lease.py", "0008_merge_worker_outbox.py",
    ),
}
WORKER_MODULES = ("prediction", "knowledge", "training", "model_center", "assistant")
FORBIDDEN = (
    re.compile(r"(?:127\.0\.0\.1|localhost):8010", re.I),
    re.compile(r"(?:127\.0\.0\.1|localhost):8000", re.I),
    re.compile(r"(?<![A-Za-z0-9_])/api/predict(?:/|['\"?]|$)", re.I),
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def tree_digest(root: Path, *, exclude_manifest: bool = False) -> tuple[str, int]:
    rows: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix.lower() == ".pyc":
            continue
        if exclude_manifest and path.name == "SOURCE_MANIFEST.json":
            continue
        rows.append(f"{path.relative_to(root).as_posix()}\t{path.stat().st_size}\t{sha256(path)}")
    payload = "\n".join(sorted(rows)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest().upper(), len(rows)


def _files(directory: Path, patterns: tuple[str, ...]) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    for pattern in patterns:
        for path in directory.rglob(pattern):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix.lower() != ".pyc":
                found.append({"path": path.relative_to(ROOT).as_posix(), "sha256": sha256(path)})
    return sorted(found, key=lambda row: row["path"])


def input_fingerprint() -> dict[str, Any]:
    groups = {
        "backend_source": _files(SOURCE, ("*.py",)),
        "backend_migrations": _files(BACKEND / "alembic", ("*.py", "*.ini", "*.mako")),
        "build_manifests": _files(BUILDS, ("*.json",)),
        "build_scripts": _files(ROOT / "scripts", ("*.py", "*.ps1", "*.cmd", "*.json")),
        "active_contracts": _files(ROOT / "contracts", ("*.json", "*.py")),
        "candidate_05": _files(CANDIDATE_05, ("*",)) if CANDIDATE_05.is_dir() else [],
    }
    payload = json.dumps(groups, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {"aggregate_sha256": hashlib.sha256(payload).hexdigest().upper(), "files": groups}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize(path: Path, build_id: str) -> tuple[str, int]:
    sys.path.insert(0, str(ROOT / "contracts"))
    from freeze_openapi import normalize

    body, digest = normalize(path, build_id)
    return digest.upper(), len(body)


def _revision_info(path: Path) -> tuple[str, tuple[str, ...]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values: dict[str, Any] = {}
    for node in tree.body:
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
        if value is None:
            continue
        for target in targets:
            if isinstance(target, ast.Name) and target.id in {"revision", "down_revision"}:
                values[target.id] = ast.literal_eval(value)
    revision = values.get("revision")
    parent = values.get("down_revision")
    if not isinstance(revision, str):
        raise ValueError(f"migration revision missing: {path}")
    if parent is None:
        parents: tuple[str, ...] = ()
    elif isinstance(parent, str):
        parents = (parent,)
    elif isinstance(parent, (tuple, list)) and all(isinstance(item, str) for item in parent):
        parents = tuple(parent)
    else:
        raise ValueError(f"invalid migration down_revision: {path}")
    return revision, parents


def _validate_migrations(package: Path, profile: str) -> list[str]:
    versions = package / "alembic" / "versions"
    actual = tuple(sorted(path.name for path in versions.glob("*.py")))
    expected = tuple(sorted(EXPECTED_MIGRATIONS[profile]))
    if actual != expected:
        raise ValueError(f"{profile}: migration set {actual!r} != {expected!r}")
    graph: dict[str, tuple[str, ...]] = {}
    for filename in actual:
        revision, parents = _revision_info(versions / filename)
        if revision in graph:
            raise ValueError(f"{profile}: duplicate Alembic revision {revision}")
        graph[revision] = parents
    revisions = set(graph)
    parents = {parent for values in graph.values() for parent in values}
    if not parents <= revisions:
        raise ValueError(f"{profile}: dangling migration parent {sorted(parents - revisions)}")
    heads = revisions - parents
    if len(heads) != 1:
        raise ValueError(f"{profile}: expected one Alembic head, got {sorted(heads)}")
    if profile == "universal-v1" and graph.get("0008_merge_worker_outbox") != (
        "0006_m4_assistant", "0007_worker_outbox_lease"
    ):
        raise ValueError(f"{profile}: 0008 merge parents are not 0006/0007")
    return sorted(graph)


def _write_worker_status(package: Path, profile: str, manifest: dict[str, Any]) -> dict[str, Any]:
    selected = [module for module in WORKER_MODULES if module in manifest["modules"]]
    worker_paths = [f"chrom_platform/modules/{module}/worker.py" for module in selected]
    missing = [path for path in worker_paths if not (package / path).is_file()]
    if missing:
        raise ValueError(f"{profile}: missing selected worker source {missing}")
    worker_cli = package / "worker_cli.py"
    if profile in M2_PROFILES:
        source_cli = SOURCE / "chrom_platform" / "worker_cli.py"
        shutil.copy2(source_cli, worker_cli)
        if sha256(worker_cli) != sha256(source_cli):
            raise ValueError(f"{profile}: worker_cli source hash mismatch")
        status = "BLOCKED"
        reason = (
            "production handler registry is not bound; WorkerRuntime fail-closes with "
            "WORKER_HANDLER_BINDING_UNAVAILABLE; MySQL/Redis/Dramatiq health is unverified"
        )
        handler_binding = False
    else:
        status = "NOT_SELECTED"
        reason = "profile selects no production worker module"
        handler_binding = False
    record = {
        "profile": profile,
        "status": status,
        "production_ready": False,
        "handler_binding": {"bound": handler_binding, "source": "worker_cli.py" if worker_cli.is_file() else None},
        "infrastructure": {"mysql": "UNVERIFIED", "redis": "UNVERIFIED", "dramatiq": "UNVERIFIED"},
        "selected_worker_modules": selected,
        "worker_sources": worker_paths,
        "reason": reason,
    }
    (package / "worker-status.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return record


def _refresh_manifest(package: Path, manifest: dict[str, Any], worker_status: dict[str, Any]) -> None:
    files = []
    for path in sorted(package.rglob("*")):
        if path.is_file() and path.name != "SOURCE_MANIFEST.json" and "__pycache__" not in path.parts and path.suffix.lower() != ".pyc":
            files.append({"path": path.relative_to(package).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    workers = [item["path"] for item in files if item["path"].endswith("worker.py")]
    registry = {
        "build_id": manifest["build_id"],
        "modules": manifest["modules"],
        "chromatography_types": manifest["chromatography_types"],
        "files": files,
        "migrations": [item["path"] for item in files if "alembic/versions/" in item["path"]],
        "workers": workers,
        "worker_cli": "worker_cli.py" if (package / "worker_cli.py").is_file() else None,
        "worker_status": "worker-status.json",
        "dependencies": json.loads((package / "build-manifest.json").read_text(encoding="utf-8")) and _load_dependencies(package),
        "worker_status_record": worker_status,
    }
    (package / "SOURCE_MANIFEST.json").write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load_dependencies(package: Path) -> list[str]:
    return [line.strip() for line in (package / "requirements-profile.txt").read_text(encoding="utf-8").splitlines() if line.strip()]


def _validate_manifest(package: Path, profile: str) -> dict[str, Any]:
    record = _load_json(package / "SOURCE_MANIFEST.json")
    expected = {str(item["path"]): str(item["sha256"]).upper() for item in record["files"]}
    actual = {
        path.relative_to(package).as_posix(): sha256(path)
        for path in package.rglob("*")
        if path.is_file() and path.name != "SOURCE_MANIFEST.json" and "__pycache__" not in path.parts and path.suffix.lower() != ".pyc"
    }
    if set(expected) != set(actual):
        raise ValueError(f"{profile}: SOURCE_MANIFEST file set mismatch")
    if any(expected[key] != actual[key] for key in expected):
        raise ValueError(f"{profile}: SOURCE_MANIFEST hash drift")
    if record.get("build_id") != profile or record.get("worker_status") != "worker-status.json":
        raise ValueError(f"{profile}: SOURCE_MANIFEST binding mismatch")
    package_hash, file_count = tree_digest(package, exclude_manifest=True)
    return {
        "package_sha256": package_hash,
        "source_manifest_sha256": sha256(package / "SOURCE_MANIFEST.json"),
        "build_manifest_sha256": sha256(package / "build-manifest.json"),
        "dependency_lock_sha256": sha256(package / "requirements-profile.txt"),
        "file_count": file_count,
    }


def _independent_import(package: Path, profile: str) -> None:
    probe = """
import json
from pathlib import Path
import chrom_platform
from chrom_platform.app import create_app
manifest = json.loads(Path('build-manifest.json').read_text(encoding='utf-8'))
create_app(Path('build-manifest.json'), contract_generation=True)
if Path('worker_cli.py').is_file():
    import worker_cli
print(json.dumps({'build_id': manifest['build_id'], 'module': str(Path(chrom_platform.__file__).resolve())}))
"""
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("CHROM_BUILD_MANIFEST", None)
    result = subprocess.run([sys.executable, "-c", probe], cwd=package, env=env, capture_output=True, text=True, check=False, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"{profile}: independent import failed: {result.stderr or result.stdout}")
    output = json.loads(result.stdout)
    if output["build_id"] != profile or not Path(output["module"]).resolve().is_relative_to(package.resolve()):
        raise RuntimeError(f"{profile}: import resolved outside canonical package")


def _forbidden_hits(package: Path) -> list[str]:
    hits: list[str] = []
    for path in package.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix.lower() in {".pyc", ".json"}:
            continue
        text = path.read_text(encoding="utf-8", errors="strict")
        if any(pattern.search(text) for pattern in FORBIDDEN):
            hits.append(path.relative_to(package).as_posix())
    return hits


def _remove_caches(root: Path) -> None:
    for path in sorted(root.rglob("__pycache__"), reverse=True):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
    for path in root.rglob("*.pyc"):
        path.unlink(missing_ok=True)


def build() -> dict[str, Any]:
    if FINAL.exists():
        raise FileExistsError(f"immutable snapshot already exists: {FINAL}")
    if not CANDIDATE_05.is_dir():
        raise FileNotFoundError(f"candidate-05 comparator missing: {CANDIDATE_05}")
    before = input_fingerprint()
    fingerprint_path = BACKEND / "candidate-06-fingerprint.json"
    fingerprint_path.write_text(json.dumps({"before": before}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    staging = Path(tempfile.mkdtemp(prefix=f".{SNAPSHOT_NAME}.", dir=SNAPSHOT_PARENT))
    reports: dict[str, Any] = {}
    try:
        sys.path.insert(0, str(SOURCE))
        from chrom_platform.assembly.profile import assemble_profile
        from chrom_platform.generate_openapi import generate_package

        package_root = staging / "profile-packages"
        openapi_root = staging / "openapi"
        for profile in PROFILES:
            package = assemble_profile(BUILDS / f"{profile}.json", package_root, source_root=BACKEND)
            if package.resolve() != (package_root / profile).resolve():
                raise ValueError(f"{profile}: non-canonical package output")
            manifest = _load_json(package / "build-manifest.json")
            if manifest.get("build_id") != profile:
                raise ValueError(f"{profile}: build manifest binding mismatch")
            worker_status = _write_worker_status(package, profile, manifest)
            _refresh_manifest(package, manifest, worker_status)
            migrations = _validate_migrations(package, profile)
            _independent_import(package, profile)
            hits = _forbidden_hits(package)
            if hits:
                raise ValueError(f"{profile}: legacy endpoint/port references {hits}")
            package_report = _validate_manifest(package, profile)
            static = generate_package(package, openapi_root)
            static_expected = openapi_root / profile / "openapi.json"
            if static.resolve() != static_expected.resolve():
                raise ValueError(f"{profile}: non-canonical OpenAPI output")
            current_normalized, _ = _normalize(static_expected, profile)
            active_path = ROOT / "contracts" / profile / "openapi.json"
            candidate05_path = CANDIDATE_05 / "openapi" / profile / "openapi.json"
            active_normalized, _ = _normalize(active_path, profile)
            old_normalized, _ = _normalize(candidate05_path, profile)
            if current_normalized != active_normalized or current_normalized != old_normalized:
                raise ValueError(f"{profile}: normalized OpenAPI differs from active contracts or candidate-05")
            summary = _load_json(static_expected.with_name("summary.json"))
            reports[profile] = {
                **package_report,
                "openapi_sha256": sha256(static_expected),
                "openapi_normalized_sha256": current_normalized,
                "summary_sha256": sha256(static_expected.with_name("summary.json")),
                "active_contract_normalized_sha256": active_normalized,
                "candidate_05_normalized_sha256": old_normalized,
                "migrations": migrations,
                "worker_status": worker_status,
                "summary_build_inputs": summary.get("build_inputs", {}),
            }
        _remove_caches(staging)
        if sorted(path.name for path in staging.iterdir() if path.is_dir()) != ["openapi", "profile-packages"]:
            raise ValueError("snapshot root must contain only openapi and profile-packages")
        if sorted(path.name for path in package_root.iterdir() if path.is_dir()) != sorted(PROFILES):
            raise ValueError("profile-packages root is not canonical")
        after = input_fingerprint()
        if after != before:
            raise RuntimeError("source/build inputs changed during candidate-06 generation")
        fingerprint_path.write_text(json.dumps({"before": before, "after": after}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(staging, FINAL)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    snapshot_sha, snapshot_files = tree_digest(FINAL)
    result = {
        "snapshot": str(FINAL.resolve()),
        "fingerprint_record": str(fingerprint_path.resolve()),
        "fingerprint_sha256": before["aggregate_sha256"],
        "snapshot_sha256": snapshot_sha,
        "snapshot_file_count": snapshot_files,
        "profiles": reports,
    }
    handoff = BACKEND / "CANDIDATE_06_HANDOFF.md"
    handoff.write_text(_handoff(result), encoding="utf-8")
    status = BACKEND / "STATUS.md"
    current = status.read_text(encoding="utf-8")
    marker = "## candidate-06 构建交接"
    if marker not in current:
        status.write_text(current.rstrip() + "\n\n" + _status_section(result), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def _handoff(result: dict[str, Any]) -> str:
    lines = [
        "# candidate-06 构建交接",
        "",
        "日期：2026-09-21（Asia/Shanghai）",
        f"候选根：`{result['snapshot']}`",
        f"源码/构建输入 fingerprint SHA-256：`{result['fingerprint_sha256']}`",
        f"候选根 SHA-256（相对路径、字节数、文件 SHA 排序）：`{result['snapshot_sha256']}`，文件数 `{result['snapshot_file_count']}`",
        f"指纹记录：`{result['fingerprint_record']}`（before/after 相同）",
        "",
        "## 逐 profile 制品",
        "",
        "| profile | OpenAPI SHA | normalized OpenAPI SHA | package SHA | SOURCE_MANIFEST SHA | build-manifest SHA | requirements SHA | migrations | worker status |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for profile in PROFILES:
        item = result["profiles"][profile]
        lines.append(
            f"| `{profile}` | `{item['openapi_sha256']}` | `{item['openapi_normalized_sha256']}` | "
            f"`{item['package_sha256']}` | `{item['source_manifest_sha256']}` | `{item['build_manifest_sha256']}` | "
            f"`{item['dependency_lock_sha256']}` | `{', '.join(item['migrations'])}` | `{item['worker_status']['status']}` |"
        )
    lines += [
        "",
        "## 结构与真实状态",
        "",
        "- snapshot 根仅有 `openapi/` 与 `profile-packages/`；每个 profile 只有一个 canonical 包，未保留 nested profile 或重复 build_id。",
        "- normalized OpenAPI 逐 profile 同时等于当前 active contracts 与 candidate-05；构建过程未修改 contracts。",
        "- migration 闭包为 minimal=`0001`、sfc/cc=`0001+0002+0007`、universal=`0001..0008`；universal `0008` parents=`0006_m4_assistant/0007_worker_outbox_lease`，每包单一 Alembic head。",
        "- sfc/cc/universal 包含当前 `worker_cli.py` 与选中模块 worker 源码，并记录 `worker-status.json`；生产 handler binding 未绑定，MySQL/Redis/Dramatiq 未做真实健康验收，因此状态为 BLOCKED，未写 READY。",
        "",
        "## 未完成功能与验收边界",
        "",
        "- 真实 MySQL、Redis、Dramatiq broker/Worker 和生产 handler registry 尚未完成部署验收。",
        "- 真实模型资产、模型 provider、训练执行、模型治理发布/回滚和外部 TLC 四资产烟测尚未完成。",
        "- 完整传递依赖锁、平台 wheel、干净/离线安装、真实业务端到端和运行中服务重启尚未验收。",
        "",
        "构建脚本：`scripts/build_candidate_06.py`。候选目录为不可变输出，后续变更应生成新的候选编号。",
        "",
    ]
    return "\n".join(lines)


def _status_section(result: dict[str, Any]) -> str:
    return "\n".join([
        "## candidate-06 构建交接",
        "",
        f"candidate-06 已由 `scripts/build_candidate_06.py` 从当前源码原子生成：`{result['snapshot']}`。",
        f"源码/构建输入 fingerprint：`{result['fingerprint_sha256']}`；候选根 fingerprint：`{result['snapshot_sha256']}`（{result['snapshot_file_count']} files）。",
        "OpenAPI normalized digest 逐 profile 与 active contracts、candidate-05 相同；contracts 未修改。迁移闭包为 minimal=`0001`、sfc/cc=`0001+0002+0007`、universal=`0001..0008`，universal `0008` 使用 `0006/0007` merge parents。",
        "candidate-06 的 sfc/cc/universal 包含当前 Worker CLI 与选中 worker 源码；生产 handler binding、真实 MySQL/Redis/Dramatiq 尚未绑定或验收，worker 状态保持 BLOCKED，未宣称 READY。精确制品 SHA 与未完成功能见 `backend/CANDIDATE_06_HANDOFF.md`。",
    ]) + "\n"


if __name__ == "__main__":
    build()
