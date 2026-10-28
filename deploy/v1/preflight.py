"""Read-only V1 deployment preflight; reports no connection strings or secrets."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit


def read_env(path: Path) -> dict[str, str]:
    result = {}
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not key.strip().replace("_", "").isalnum():
            raise ValueError("invalid environment file syntax")
        key = key.strip()
        if key in result:
            raise ValueError("duplicate environment key")
        result[key] = value.strip().strip('"').strip("'")
    return result


def configuration_checks(config: dict[str, str]) -> list[dict]:
    checks = []
    def add(name, ok, reason):
        checks.append({"check": name, "status": "PASS" if ok else "FAIL", "reason": reason})
    ports = []
    for key in ("API_PORT", "FRONTEND_PORT", "WEB_PORT"):
        value = config.get(key, "")
        ok = value.isascii() and value.isdigit() and 1 <= int(value) <= 65535
        add(key, ok, "valid" if ok else "missing or invalid port")
        if ok:
            ports.append(int(value))
    add("distinct_ports", len(ports) == 3 and len(set(ports)) == 3, "three distinct listening ports required")
    for key, scheme in (("DATABASE_URL", {"mysql+asyncmy"}), ("REDIS_URL", {"redis", "rediss"})):
        raw = config.get(key, "")
        try:
            parsed = urlsplit(raw)
            ok = bool(parsed.hostname) and parsed.scheme in scheme and "REPLACE_ME" not in raw and "APP_USER" not in raw
        except ValueError:
            ok = False
        add(key, ok, "configured" if ok else "missing, placeholder or unsupported URL")
    for key in ("MYSQL_DATABASE", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_ROOT_PASSWORD"):
        value = config.get(key, "")
        ok = bool(value) and value not in {"REPLACE_ME", "APP_USER"} and "REPLACE_ME" not in value
        add(key, ok, "configured" if ok else "missing or placeholder")
    add("trace_mode", config.get("TRACE_CONTENT_MODE") in {"off", "redacted"}, "raw trace content is forbidden")
    for key in ("ARTIFACT_ROOT", "SECRET_KEY_FILE"):
        add(key, bool(config.get(key)), "required internal path configured" if config.get(key) else "missing internal path")
    if config.get("API_HOST") not in {"127.0.0.1", "localhost", "::1"}:
        checks.append({"check": "network_exposure", "status": "REVIEW", "reason": "default actor cannot authenticate multiple users; verify reverse proxy and network restriction"})
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--docker", default=shutil.which("docker"))
    parser.add_argument("--check-ports", action="store_true", help="probe loopback listeners; no service is started")
    args = parser.parse_args()
    try:
        config = read_env(args.env_file)
        checks = configuration_checks(config)
    except (OSError, ValueError):
        print(json.dumps({"status": "BLOCKED", "reason": "environment file unreadable or invalid"}))
        return 1
    checks.append({"check": "python", "status": "PASS" if sys.version_info >= (3, 11) else "FAIL", "version": f"{sys.version_info.major}.{sys.version_info.minor}", "reason": "3.11 development authorized; 3.12 compatibility needs a separate run"})
    for name in ("fastapi", "pydantic", "sqlalchemy", "alembic", "asyncmy", "redis", "dramatiq", "rdkit"):
        try:
            version = importlib.metadata.version(name)
            checks.append({"check": name, "status": "PASS", "version": version})
        except importlib.metadata.PackageNotFoundError:
            checks.append({"check": name, "status": "FAIL", "reason": "not installed; request dependency approval before installation"})
    try:
        result = subprocess.run([args.docker, "info", "--format", "{{.OSType}}"], capture_output=True, text=True, timeout=15) if args.docker else None
        linux = result is not None and result.returncode == 0 and result.stdout.strip() == "linux"
    except (OSError, subprocess.TimeoutExpired):
        linux = False
    checks.append({"check": "linux_execution_layer", "status": "PASS" if linux else "FAIL", "reason": "Linux Docker reachable" if linux else "no verified Linux Docker; worker delivery blocked"})
    if args.check_ports:
        for key in ("API_PORT", "FRONTEND_PORT", "WEB_PORT"):
            value = config.get(key, "")
            if value.isascii() and value.isdigit() and 1 <= int(value) <= 65535:
                with socket.socket() as sock:
                    sock.settimeout(0.2)
                    busy = sock.connect_ex(("127.0.0.1", int(value))) == 0
                checks.append({"check": key + "_listener", "status": "REVIEW" if busy else "PASS", "reason": "loopback listener present; verify ownership" if busy else "no loopback listener detected; startup must still bind-check"})
    # Successful preflight never claims application readiness or scientific acceptance.
    failed = any(c["status"] == "FAIL" for c in checks)
    print(json.dumps({"status": "BLOCKED" if failed else "PREFLIGHT_ONLY", "checks": checks}, ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
