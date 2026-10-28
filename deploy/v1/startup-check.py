"""Read-only checks for the two fixed, isolated Acceptance runtimes."""
import argparse
import importlib
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
DATA_BACKEND_PACKAGE = Path(__file__).resolve().parents[2] / "deploy" / "local-packages" / "data-center-invalid-window-20261026"


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
    return Path(__file__).resolve().parents[2] / "backend/src"


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
    if selected["dataset_only"]:
        counts["dataset_scan_completion_unknown"] = 0 if dataset_scan_ledger_idle() else 1
    try:
        with engine.connect() as db:
            db.execute(text("SET TRANSACTION READ ONLY"))
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
    idle = not any(counts.values())
    print(json.dumps({"read_only": True, "profile": args.profile, "idle": idle, "pending_counts": counts}))
    return 0 if idle else 3


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        # Never print connection values, configuration contents or driver errors.
        print("startup_check_failed:" + type(exc).__name__)
        raise SystemExit(2)
