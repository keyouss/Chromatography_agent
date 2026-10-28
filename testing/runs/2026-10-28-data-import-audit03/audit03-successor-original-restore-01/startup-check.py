"""Read-only checks for the two fixed, isolated Acceptance runtimes."""
import argparse
import importlib
import importlib.util
import json
import os
from pathlib import Path
import sys

PROFILES = {
    "assistant-20261006": {
        "runtime": "assistant-accept-20261006",
        "schema": "chrom_assistant_accept_20261006",
        "queue": "chrom-platform-assistant-accept-20261006",
        "dataset_only": False,
    },
    "data-20261026": {
        "runtime": "data-accept-20261026",
        "schema": "chrom_data_accept_20261026",
        "queue": "chrom-platform-data-accept-20261026",
        "dataset_only": True,
    },
}
REPO_ROOT = Path(r"D:\Jupyter\Chromatography_agent")
DATA_BACKEND_PACKAGE = Path(
    r"D:\Jupyter\Chromatography_agent\testing\runs\2026-10-27-data-import-audit"
    r"\backend-runtime-package-data-window-08\data-center-invalid-window-20261026"
)
SUCCESSOR_DIR = REPO_ROOT / r"testing/runs/2026-10-28-data-import-audit03/audit03-successor-original-restore-01"
SUCCESSOR_AUTH = SUCCESSOR_DIR / "authorization.json"
SUCCESSOR_BINDING = REPO_ROOT / r"testing/runs/2026-10-28-data-import-audit03/audit03-successor-original-start-binding.json"
SUCCESSOR_HELPER = REPO_ROOT / r"testing/runs/2026-10-28-data-import-audit03/audit03-successor-config-restore.py"

def profile_binding(profile: str, local_app_data: str | Path) -> dict[str, str | bool]:
    """Return a fixed profile binding; caller input never becomes a path."""
    if profile not in PROFILES:
        raise ValueError("unknown acceptance profile")
    selected = PROFILES[profile]
    return {**selected, "runtime_file": str(Path(local_app_data) / "ChromatographyPlatform" / selected["runtime"] / "runtime.json")}


def worker_package_path(profile: str) -> Path:
    if profile not in PROFILES:
        raise ValueError("unknown acceptance profile")
    if PROFILES[profile]["dataset_only"]:
        return DATA_BACKEND_PACKAGE
    return REPO_ROOT / "backend/src"


def prepare_worker_package_import(profile: str) -> None:
    if profile not in PROFILES:
        raise ValueError("unknown acceptance profile")
    if PROFILES[profile]["dataset_only"]:
        sys.dont_write_bytecode = True


def dataset_scan_ledger_idle() -> bool:
    package = worker_package_path("data-20261026")
    expected = package / "chrom_platform/artifact_core/dataset_csv_elevated_runner.py"
    if not expected.is_file():
        raise ValueError("dataset scan completion checker is missing")
    prepare_worker_package_import("data-20261026")
    sys.path.insert(0, str(package))
    module = importlib.import_module("chrom_platform.artifact_core.dataset_csv_elevated_runner")
    if Path(module.__file__).resolve() != expected.resolve():
        raise ValueError("dataset scan completion checker origin mismatch")
    return module.ledger_processes_idle() is True



def preserved_unknown_exception(counts: dict, active_leases: dict, profile: str) -> bool:
    """Use the successor API for the sole bound audit02 UNKNOWN exception."""
    if profile != "data-20261026" or counts.get("dataset_scan_completion_unknown") != 1:
        return False
    if any(value != 0 for key, value in counts.items() if key != "dataset_scan_completion_unknown"):
        return False
    if any(value != 0 for value in active_leases.values()):
        return False
    try:
        auth = json.loads(SUCCESSOR_AUTH.read_text(encoding="utf-8-sig"))
        expected = auth.get("binding_sha256") if isinstance(auth, dict) else None
        if (auth.get("status") != "ACTIVE_ORIGINAL_START"
                or auth.get("window_id") != "audit03-successor-binding-01"
                or not isinstance(expected, str) or len(expected) != 64
                or any(ch not in "0123456789abcdef" for ch in expected)):
            return False
        spec = importlib.util.spec_from_file_location("audit03_successor_config_restore", SUCCESSOR_HELPER)
        if spec is None or spec.loader is None:
            return False
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        result = module.check_preserved_unknown_exception(
            binding_path=SUCCESSOR_BINDING,
            expected_binding_sha256=expected,
            profile=profile,
            pending_counts=counts,
            active_leases=active_leases,
        )
        return (
            isinstance(result, dict)
            and result.get("status") == "PRESERVED_UNKNOWN_EXCEPTION_ALLOWED"
            and isinstance(result.get("checks"), dict)
            and bool(result["checks"])
            and all(value is True for value in result["checks"].values())
        )
    except (OSError, ValueError, TypeError, AttributeError, ImportError):
        return False


def idle_outcome(counts: dict, selected: dict, active_leases: dict) -> tuple[bool, int, bool]:
    ordinary_idle = not any(counts.values()) and not any(active_leases.values())
    exception = bool(
        selected["dataset_only"]
        and preserved_unknown_exception(counts, active_leases, "data-20261026")
    )
    return ordinary_idle, 0 if ordinary_idle or exception else 3, exception

def queue_counts(client, queue: str, *, include_assistant: bool = True) -> tuple[int, int]:
    """Count runnable/delayed and fetched work, excluding retained dead letters."""
    names = [queue, queue + ".DQ"]
    if include_assistant:
        names.extend((queue + "-assistant", queue + "-assistant.DQ"))
    pending = 0
    inflight = 0
    for name in names:
        key = "dramatiq:" + name
        kind = client.type(key)
        if kind == b"list":
            pending += client.llen(key)
        elif kind == b"zset":
            pending += client.zcard(key)
        for ack_key in client.scan_iter(match="dramatiq:__acks__.*." + name, count=50):
            inflight += client.scard(ack_key)
    return pending, inflight


def _runtime_for(profile: str, base: Path) -> dict:
    binding = profile_binding(profile, base)
    runtime = json.loads(Path(binding["runtime_file"]).read_text(encoding="utf-8-sig"))
    if runtime.get("CHROM_ACCEPTANCE_DATABASE_SCHEMA") != binding["schema"]:
        raise ValueError("runtime schema mismatch")
    if runtime.get("WORKER_QUEUE") != binding["queue"]:
        raise ValueError("runtime queue mismatch")
    return runtime


def _database_url(profile: str, runtime: dict, base: Path):
    from sqlalchemy.engine import make_url

    if PROFILES[profile]["dataset_only"]:
        # Data acceptance uses independently provisioned CRUD URLs. Never derive
        # this principal from the formal or assistant runtime credentials.
        worker_url = make_url(runtime["DATABASE_URL"])
        url = make_url(runtime["SYNC_DATABASE_URL"])
        if (worker_url.drivername != "mysql+asyncmy" or worker_url.host != "127.0.0.1"
                or worker_url.port != 3306 or worker_url.database != PROFILES[profile]["schema"]
                or worker_url.username != "chrom_data_accept_runtime"):
            raise ValueError("acceptance worker database binding mismatch")
    else:
        formal = json.loads((base / "ChromatographyPlatform/priority-local/runtime.json").read_text(encoding="utf-8-sig"))
        url = make_url(formal["SYNC_DATABASE_URL"])
        if url.database != "chrom_platform":
            raise ValueError("formal schema mismatch")
        url = url.set(database=PROFILES[profile]["schema"])
    if url.drivername != "mysql+pymysql" or url.host != "127.0.0.1" or url.port != 3306 or url.database != PROFILES[profile]["schema"]:
        raise ValueError("acceptance database binding mismatch")
    if PROFILES[profile]["dataset_only"] and url.username != "chrom_data_accept_runtime":
        raise ValueError("acceptance database principal mismatch")
    return url


def _parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=tuple(PROFILES), default="assistant-20261006")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--idle", action="store_true")
    action.add_argument("--worker-healthcheck", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    base = Path(os.environ["LOCALAPPDATA"])
    selected = PROFILES[args.profile]
    accepted = _runtime_for(args.profile, base)
    if args.worker_healthcheck:
        package_root = worker_package_path(args.profile)
        if not (package_root / "chrom_platform" / "worker_cli.py").is_file():
            raise ValueError("selected worker package is missing")
        prepare_worker_package_import(args.profile)
        sys.path.insert(0, str(package_root))
        from chrom_platform.worker_cli import worker_healthcheck
        return worker_healthcheck(accepted["WORKER_READY_FILE"])

    import redis
    from sqlalchemy import create_engine, text

    engine = create_engine(_database_url(args.profile, accepted, base),
                           connect_args={"connect_timeout": 5, "read_timeout": 5, "write_timeout": 5})
    counts = {}
    active_leases = {}
    if selected["dataset_only"]:
        counts["dataset_scan_completion_unknown"] = 0 if dataset_scan_ledger_idle() else 1
    try:
        with engine.connect() as db:
            db.execute(text("SET TRANSACTION READ ONLY"))
            active_leases["job_attempt_active"] = db.execute(text("SELECT COUNT(*) FROM job_attempt WHERE lease_until > UTC_TIMESTAMP()")).scalar_one()
            active_leases["outbox_active"] = db.execute(text("SELECT COUNT(*) FROM outbox_event WHERE lease_expires_at > UTC_TIMESTAMP()")).scalar_one()
            if not selected["dataset_only"]:
                for table in ("agent_run", "agent_attempt"):
                    active_leases[table + "_active"] = db.execute(text(f"SELECT COUNT(*) FROM {table} WHERE lease_until > UTC_TIMESTAMP()")).scalar_one()
            tables = ("job", "job_attempt")
            if not selected["dataset_only"]:
                tables += ("agent_run", "agent_attempt")
            for table in tables:
                statuses = "'QUEUED','RUNNING','CANCEL_REQUESTED'"
                if table in ("agent_run", "agent_attempt"):
                    statuses += ",'WAITING_CONFIRMATION'"
                counts[table] = db.execute(text(f"SELECT COUNT(*) FROM {table} WHERE status IN ({statuses})")).scalar_one()
            counts["outbox"] = db.execute(text("SELECT COUNT(*) FROM outbox_event WHERE status IN ('PENDING','PROCESSING')")).scalar_one()
            if selected["dataset_only"]:
                counts["data_import"] = db.execute(text("SELECT COUNT(*) FROM data_import WHERE status IN ('VALIDATING','COMMITTING')")).scalar_one()
            db.rollback()
    finally:
        engine.dispose()
    client = redis.Redis.from_url(accepted["REDIS_URL"], socket_timeout=5, socket_connect_timeout=5)
    try:
        counts["queue"], counts["queue_inflight"] = queue_counts(
            client, accepted["WORKER_QUEUE"], include_assistant=not selected["dataset_only"],
        )
    finally:
        client.close()
    idle, exit_code, recovery_exception = idle_outcome(counts, selected, active_leases)
    print(json.dumps({"read_only": True, "profile": args.profile, "idle": idle,
                      "recovery_authorized_exception": recovery_exception,
                      "pending_counts": counts, "active_leases": active_leases}))
    return exit_code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        # Never print connection values, configuration contents or driver errors.
        print("startup_check_failed:" + type(exc).__name__)
        raise SystemExit(2)
