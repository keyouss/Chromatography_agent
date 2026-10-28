"""Build the immutable candidate-07 snapshot with strict physical gates."""

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
DEPLOY = ROOT / "deploy"
SNAPSHOT_PARENT = BACKEND / ".generated" / "snapshots"
SNAPSHOT_NAME = "m3-m4-v1-candidate-20260921-07"
FINAL = SNAPSHOT_PARENT / SNAPSHOT_NAME
CANDIDATE_05 = SNAPSHOT_PARENT / "m3-m4-v1-candidate-20260921-05"
CANDIDATE_06 = SNAPSHOT_PARENT / "m3-m4-v1-candidate-20260921-06"
PROFILES = ("minimal-structure-v1", "sfc-v1", "cc-v1", "universal-v1")
WORKER_PROFILES = {"sfc-v1", "cc-v1", "universal-v1"}
WORKER_MODULES = ("prediction", "knowledge", "training", "model_center", "assistant")
EXPECTED_MIGRATIONS = {
    "minimal-structure-v1": ("0001_m1_persistence.py",),
    "sfc-v1": ("0001_m1_persistence.py", "0002_m2_prediction_data.py", "0007_worker_outbox_lease.py"),
    "cc-v1": ("0001_m1_persistence.py", "0002_m2_prediction_data.py", "0007_worker_outbox_lease.py"),
    "universal-v1": (
        "0001_m1_persistence.py", "0002_m2_prediction_data.py", "0003_m3_knowledge.py",
        "0004_m3_training.py", "0005_m3_model_center.py", "0006_m4_assistant.py",
        "0007_worker_outbox_lease.py", "0008_merge_worker_outbox.py", "0009_training_outbox_lease.py",
    ),
}
FORBIDDEN = (
    re.compile(r"(?:127\.0\.0\.1|localhost):8010", re.I),
    re.compile(r"(?:127\.0\.0\.1|localhost):8000", re.I),
    re.compile(r"(?<![A-Za-z0-9_])/api/predict(?:/|['\"?]|$)", re.I),
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def tree_digest(root: Path, *, exclude_manifest: bool = False) -> tuple[str, int]:
    rows = []
    for path in root.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix.lower() == ".pyc":
            continue
        if exclude_manifest and path.name == "SOURCE_MANIFEST.json":
            continue
        rows.append(f"{path.relative_to(root).as_posix()}\t{path.stat().st_size}\t{sha256(path)}")
    return hashlib.sha256("\n".join(sorted(rows)).encode()).hexdigest().upper(), len(rows)


def _input_files(root: Path) -> list[dict[str, str]]:
    if not root.is_dir():
        raise FileNotFoundError(root)
    rows = []
    for path in root.rglob("*"):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix.lower() != ".pyc":
            rows.append({"path": path.relative_to(ROOT).as_posix(), "sha256": sha256(path)})
    return sorted(rows, key=lambda item: item["path"])


def input_fingerprint() -> dict[str, Any]:
    groups = {
        "backend_source": _input_files(SOURCE),
        "backend_alembic": _input_files(BACKEND / "alembic"),
        "contracts": _input_files(ROOT / "contracts"),
        "builds": _input_files(BUILDS),
        "deploy": _input_files(DEPLOY),
        "scripts": _input_files(ROOT / "scripts"),
        "candidate_05": _input_files(CANDIDATE_05),
        "candidate_06": _input_files(CANDIDATE_06),
    }
    payload = json.dumps(groups, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return {"aggregate_sha256": hashlib.sha256(payload).hexdigest().upper(), "files": groups}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize(path: Path, build_id: str) -> str:
    sys.path.insert(0, str(ROOT / "contracts"))
    from freeze_openapi import normalize as normalize_openapi

    _body, digest = normalize_openapi(path, build_id)
    return digest.upper()


def revision_info(path: Path) -> tuple[str, tuple[str, ...]]:
    values: dict[str, Any] = {}
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id in {"revision", "down_revision"}:
                    values[target.id] = ast.literal_eval(node.value)
    revision = values.get("revision")
    down = values.get("down_revision")
    if not isinstance(revision, str):
        raise ValueError(f"migration revision missing: {path}")
    if down is None:
        parents = ()
    elif isinstance(down, str):
        parents = (down,)
    elif isinstance(down, (tuple, list)) and all(isinstance(item, str) for item in down):
        parents = tuple(down)
    else:
        raise ValueError(f"invalid migration down_revision: {path}")
    return revision, parents


def validate_migrations(package: Path, profile: str) -> list[str]:
    versions = package / "alembic" / "versions"
    actual = tuple(sorted(path.name for path in versions.glob("*.py")))
    expected = tuple(sorted(EXPECTED_MIGRATIONS[profile]))
    if actual != expected:
        raise ValueError(f"{profile}: migration set {actual!r} != {expected!r}")
    graph: dict[str, tuple[str, ...]] = {}
    for filename in actual:
        revision, parents = revision_info(versions / filename)
        if revision in graph:
            raise ValueError(f"{profile}: duplicate migration revision {revision}")
        graph[revision] = parents
    revisions = set(graph)
    parents = {parent for row in graph.values() for parent in row}
    if not parents <= revisions:
        raise ValueError(f"{profile}: dangling migration parent {sorted(parents - revisions)}")
    roots = {revision for revision, row in graph.items() if not row}
    heads = revisions - parents
    if len(roots) != 1 or len(heads) != 1:
        raise ValueError(f"{profile}: graph roots={sorted(roots)} heads={sorted(heads)}")
    if profile == "universal-v1":
        if heads != {"0009_training_outbox_lease"} or set(graph) != {
            "0001_m1_persistence", "0002_m2_prediction_data", "0003_m3_knowledge",
            "0004_m3_training", "0005_m3_model_center", "0006_m4_assistant",
            "0007_worker_outbox_lease", "0008_merge_worker_outbox", "0009_training_outbox_lease",
        }:
            raise ValueError(f"{profile}: expected 0001..0009 with head 0009")
        if graph["0008_merge_worker_outbox"] != ("0006_m4_assistant", "0007_worker_outbox_lease"):
            raise ValueError(f"{profile}: invalid 0008 merge parents")
    return sorted(graph)


def write_worker_files(package: Path, profile: str, manifest: dict[str, Any]) -> dict[str, Any]:
    selected = [module for module in WORKER_MODULES if module in manifest.get("modules", [])]
    worker_paths = [f"chrom_platform/modules/{module}/worker.py" for module in selected]
    missing = [path for path in worker_paths if not (package / path).is_file()]
    if missing:
        raise ValueError(f"{profile}: missing selected worker source {missing}")
    cli = package / "worker_cli.py"
    if profile in WORKER_PROFILES:
        source_cli = SOURCE / "chrom_platform" / "worker_cli.py"
        shutil.copy2(source_cli, cli)
        if sha256(cli) != sha256(source_cli):
            raise ValueError(f"{profile}: worker_cli hash mismatch")
        cli_text = cli.read_text(encoding="utf-8")
        for module in selected:
            if f'"{module}":' not in cli_text or f"chrom_platform.modules.{module}.worker" not in cli_text:
                raise ValueError(f"{profile}: static handler dependency missing for {module}")
        status = "BLOCKED"
        reason = "production handler registry is not bound; MySQL/Redis/Dramatiq health is unverified"
        record = {
            "profile": profile, "status": status, "production_ready": False,
            "handler_binding": {"bound": False, "source": "worker_cli.py"},
            "infrastructure": {"mysql": "UNVERIFIED", "redis": "UNVERIFIED", "dramatiq": "UNVERIFIED"},
            "selected_worker_modules": selected, "worker_sources": worker_paths,
            "static_handler_dependencies": [f"chrom_platform.modules.{module}.worker" for module in selected],
            "reason": reason,
        }
        (package / "worker-status.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        if cli.exists() or (package / "worker-status.json").exists() or list(package.rglob("worker.py")):
            raise ValueError(f"{profile}: minimal package contains worker artifacts")
        record = {
            "profile": profile, "status": "NOT_SELECTED", "production_ready": False,
            "handler_binding": {"bound": False, "source": None}, "selected_worker_modules": [],
            "worker_sources": [], "static_handler_dependencies": [],
            "reason": "profile has no worker service, volume, image, env, or WORKER_COMMAND",
        }
    return record


def refresh_manifest(package: Path, manifest: dict[str, Any], worker_status: dict[str, Any]) -> None:
    files = []
    for path in sorted(package.rglob("*")):
        if path.is_file() and path.name != "SOURCE_MANIFEST.json" and "__pycache__" not in path.parts and path.suffix.lower() != ".pyc":
            files.append({"path": path.relative_to(package).as_posix(), "sha256": sha256(path)})
    record = {
        "build_id": manifest["build_id"], "modules": manifest["modules"],
        "chromatography_types": manifest["chromatography_types"], "files": files,
        "migrations": [item["path"] for item in files if "alembic/versions/" in item["path"]],
        "workers": [item["path"] for item in files if item["path"].endswith("worker.py")],
        "worker_cli": "worker_cli.py" if (package / "worker_cli.py").is_file() else None,
        "worker_status": "worker-status.json" if (package / "worker-status.json").is_file() else None,
        "dependencies": [line.strip() for line in (package / "requirements-profile.txt").read_text(encoding="utf-8").splitlines() if line.strip()],
        "worker_status_record": worker_status,
    }
    (package / "SOURCE_MANIFEST.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def validate_manifest(package: Path, profile: str) -> dict[str, Any]:
    manifest = load_json(package / "SOURCE_MANIFEST.json")
    expected = {item["path"]: item["sha256"].upper() for item in manifest["files"]}
    actual = {
        path.relative_to(package).as_posix(): sha256(path)
        for path in package.rglob("*")
        if path.is_file() and path.name != "SOURCE_MANIFEST.json" and "__pycache__" not in path.parts and path.suffix.lower() != ".pyc"
    }
    if set(expected) != set(actual) or any(expected[key] != actual[key] for key in expected):
        raise ValueError(f"{profile}: SOURCE_MANIFEST files/hash mismatch")
    if manifest.get("build_id") != profile:
        raise ValueError(f"{profile}: SOURCE_MANIFEST build binding mismatch")
    return {
        "package_sha256": tree_digest(package, exclude_manifest=True)[0],
        "source_manifest_sha256": sha256(package / "SOURCE_MANIFEST.json"),
        "build_manifest_sha256": sha256(package / "build-manifest.json"),
        "dependency_lock_sha256": sha256(package / "requirements-profile.txt"),
        "file_count": len(actual),
    }


def independent_import(package: Path, profile: str) -> None:
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
        raise RuntimeError(f"{profile}: isolated import failed: {result.stderr or result.stdout}")
    output = json.loads(result.stdout)
    if output["build_id"] != profile or not Path(output["module"]).resolve().is_relative_to(package.resolve()):
        raise RuntimeError(f"{profile}: import escaped canonical package")


def forbidden_hits(package: Path) -> list[str]:
    hits = []
    for path in package.rglob("*"):
        if not path.is_file() or path.suffix.lower() in {".json", ".pyc"} or "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="strict")
        if any(pattern.search(text) for pattern in FORBIDDEN):
            hits.append(path.relative_to(package).as_posix())
    return hits


def remove_caches(root: Path) -> None:
    for path in sorted(root.rglob("__pycache__"), reverse=True):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
    for path in root.rglob("*.pyc"):
        path.unlink(missing_ok=True)


def build() -> dict[str, Any]:
    if FINAL.exists():
        raise FileExistsError(f"immutable snapshot already exists: {FINAL}")
    if not CANDIDATE_05.is_dir() or not CANDIDATE_06.is_dir():
        raise FileNotFoundError("candidate-05 and candidate-06 comparators are required")
    before = input_fingerprint()
    staging = Path(tempfile.mkdtemp(prefix=f".{SNAPSHOT_NAME}.", dir=SNAPSHOT_PARENT))
    reports: dict[str, Any] = {}
    try:
        sys.path.insert(0, str(SOURCE))
        from chrom_platform.assembly.profile import assemble_profile
        from chrom_platform.generate_openapi import generate_package

        package_root, openapi_root = staging / "profile-packages", staging / "openapi"
        for profile in PROFILES:
            package = assemble_profile(BUILDS / f"{profile}.json", package_root, source_root=BACKEND)
            if package.resolve() != (package_root / profile).resolve():
                raise ValueError(f"{profile}: non-canonical package output")
            manifest = load_json(package / "build-manifest.json")
            if manifest.get("build_id") != profile:
                raise ValueError(f"{profile}: build manifest binding mismatch")
            worker_status = write_worker_files(package, profile, manifest)
            refresh_manifest(package, manifest, worker_status)
            migrations = validate_migrations(package, profile)
            independent_import(package, profile)
            hits = forbidden_hits(package)
            if hits:
                raise ValueError(f"{profile}: forbidden legacy references {hits}")
            package_report = validate_manifest(package, profile)
            static = generate_package(package, openapi_root)
            expected_static = openapi_root / profile / "openapi.json"
            if static.resolve() != expected_static.resolve():
                raise ValueError(f"{profile}: non-canonical OpenAPI output")
            current = normalize(expected_static, profile)
            active = normalize(ROOT / "contracts" / profile / "openapi.json", profile)
            old05 = normalize(CANDIDATE_05 / "openapi" / profile / "openapi.json", profile)
            old06 = normalize(CANDIDATE_06 / "openapi" / profile / "openapi.json", profile)
            if len({current, active, old05, old06}) != 1:
                raise ValueError(f"{profile}: normalized OpenAPI parity failed")
            summary = load_json(expected_static.with_name("summary.json"))
            reports[profile] = {
                **package_report, "openapi_sha256": sha256(expected_static),
                "openapi_normalized_sha256": current, "candidate_05_normalized_sha256": old05,
                "candidate_06_normalized_sha256": old06, "active_contract_normalized_sha256": active,
                "summary_sha256": sha256(expected_static.with_name("summary.json")),
                "migrations": migrations, "worker_status": worker_status,
                "summary_build_inputs": summary.get("build_inputs", {}),
            }
        remove_caches(staging)
        if sorted(path.name for path in staging.iterdir() if path.is_dir()) != ["openapi", "profile-packages"]:
            raise ValueError("snapshot root must contain only openapi and profile-packages directories")
        if sorted(path.name for path in package_root.iterdir() if path.is_dir()) != sorted(PROFILES):
            raise ValueError("profile-packages root is not canonical")
        manifests = sorted(package_root.rglob("build-manifest.json"), key=lambda path: path.as_posix())
        expected_manifests = sorted((package_root / profile / "build-manifest.json" for profile in PROFILES), key=lambda path: path.as_posix())
        if manifests != expected_manifests:
            raise ValueError("nested or duplicate profile package detected")
        after = input_fingerprint()
        if after != before:
            shutil.rmtree(staging, ignore_errors=True)
            raise RuntimeError("source/build/deploy/contract input fingerprint drifted during candidate-07 generation")
        fingerprint = {"before": before, "after": after, "snapshot": SNAPSHOT_NAME}
        (staging / "BUILD_FINGERPRINT.json").write_text(json.dumps(fingerprint, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(staging, FINAL)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    snapshot_sha, snapshot_files = tree_digest(FINAL)
    result = {"snapshot": str(FINAL.resolve()), "fingerprint_sha256": before["aggregate_sha256"], "snapshot_sha256": snapshot_sha, "snapshot_file_count": snapshot_files, "profiles": reports}
    (BACKEND / "CANDIDATE_07_HANDOFF.md").write_text(handoff(result), encoding="utf-8")
    status = BACKEND / "STATUS.md"
    current = status.read_text(encoding="utf-8")
    if "## candidate-07 构建交接" not in current:
        status.write_text(current.rstrip() + "\n\n" + status_section(result), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def handoff(result: dict[str, Any]) -> str:
    lines = [
        "# candidate-07 构建交接", "", "日期：2026-09-21（Asia/Shanghai）",
        f"候选根：`{result['snapshot']}`",
        f"输入 fingerprint（before/after 相同）：`{result['fingerprint_sha256']}`",
        f"候选根 SHA-256（相对路径、字节数、文件 SHA 排序）：`{result['snapshot_sha256']}`，文件数 `{result['snapshot_file_count']}`",
        "输入分组：backend source、backend/alembic、contracts、builds、deploy、scripts、candidate-05、candidate-06；完整 before/after 记录位于候选根 `BUILD_FINGERPRINT.json`。", "",
        "## 逐 profile 制品", "", "| profile | OpenAPI SHA | normalized OpenAPI SHA | package SHA | SOURCE_MANIFEST SHA | migrations | worker status |", "|---|---|---|---|---|---|---|",
    ]
    for profile in PROFILES:
        item = result["profiles"][profile]
        lines.append(f"| `{profile}` | `{item['openapi_sha256']}` | `{item['openapi_normalized_sha256']}` | `{item['package_sha256']}` | `{item['source_manifest_sha256']}` | `{', '.join(item['migrations'])}` | `{item['worker_status']['status']}` |")
    lines += [
        "", "## 结构与真实状态", "",
        "- snapshot 根仅有 `openapi/`、`profile-packages/` 和必要的 `BUILD_FINGERPRINT.json`；四 profile 各自只有一个 canonical 包。",
        "- normalized OpenAPI 逐 profile 同时等于 active contracts、candidate-05 和 candidate-06；构建未修改 contracts。",
        "- minimal 仅含 `0001`，无 worker service/volume/image/env/WORKER_COMMAND 或 worker artifact；sfc/cc 为 `0001+0002+0007`；universal 为 `0001..0009` 且唯一 head 为 `0009_training_outbox_lease`。",
        "- sfc/cc/universal 物理包含 `worker_cli.py`、选中 worker 及静态 handler 依赖；生产 handler registry、真实 MySQL/Redis/Dramatiq 尚未验收，状态保持 BLOCKED。",
        "", "## 定向验证与限制", "",
        "- 已在构建过程中执行 canonical root、profile physical trim、SOURCE_MANIFEST 双向 hash、隔离 import、migration graph、forbidden legacy reference 和 OpenAPI parity 门禁。",
        "- 后续应使用 candidate-07 根运行 parity/root/contract/migration/profile physical trim/offline script 定向测试；旧测试若固定 candidate-04/05 或旧迁移常量，需标明常量代际差异，不得覆盖真实失败。",
        "- 真实 MySQL、Redis、Dramatiq broker/Worker、生产 handler registry、真实模型/训练/provider、干净离线安装和业务 E2E 仍未验收，因此候选不是 release-ready。",
        "", "构建脚本：`scripts/build_candidate_07.py`。候选目录为不可变输出，后续变更应生成新的候选编号。", "",
    ]
    return "\n".join(lines)


def status_section(result: dict[str, Any]) -> str:
    return "\n".join([
        "## candidate-07 构建交接", "",
        f"candidate-07 已由 `scripts/build_candidate_07.py` 原子生成：`{result['snapshot']}`。",
        f"输入 fingerprint（before/after 相同）：`{result['fingerprint_sha256']}`；候选根 fingerprint：`{result['snapshot_sha256']}`（{result['snapshot_file_count']} files）。",
        "OpenAPI normalized digest 逐 profile 与 active contracts、candidate-05、candidate-06 相同；contracts 未修改。minimal=`0001`，sfc/cc=`0001+0002+0007`，universal=`0001..0009`、唯一 head=`0009_training_outbox_lease`。",
        "sfc/cc/universal 包含 worker_cli、选中 worker 和静态 handler 依赖；生产 handler、真实 MySQL/Redis/Dramatiq 未验收，worker 状态为 BLOCKED。精确 SHA 与限制见 `backend/CANDIDATE_07_HANDOFF.md`。", "",
    ])


if __name__ == "__main__":
    build()
