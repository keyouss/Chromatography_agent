"""Fixed child entry point for package verification and bounded OS probes."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Mapping


_MAX_REQUEST_BYTES = 64 * 1024
_PROBE_OUTPUT_BYTES = 256 * 1024
_PROBE_ALLOCATION_BYTES = 256 * 1024 * 1024
_PROBE_SLEEP_SECONDS = 30
_ALLOWED_PROBES = {"started", "alloc", "sleep", "attempt-child", "output", "crash"}


def _response(status: str, *, evidence: Mapping[str, object] | None = None, error_code: str | None = None) -> bytes:
    value: dict[str, object] = {"status": status}
    if evidence is not None:
        value["evidence"] = dict(evidence)
    if error_code is not None:
        value["error_code"] = error_code
    return (json.dumps(value, allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")


def _read_request() -> dict[str, object]:
    raw = sys.stdin.buffer.read(_MAX_REQUEST_BYTES + 1)
    if not raw or len(raw) > _MAX_REQUEST_BYTES:
        raise ValueError("invalid")
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("invalid")
    return value


def _probe(case: str) -> None:
    if case == "started":
        sys.stdout.buffer.write(_response("SUCCEEDED", evidence={"probe_case": case, "started": True}))
    elif case == "alloc":
        try:
            allocation = bytearray(_PROBE_ALLOCATION_BYTES)
            for offset in range(0, len(allocation), 4096):
                allocation[offset] = 1
        except MemoryError:
            sys.stdout.buffer.write(_response("SUCCEEDED", evidence={"probe_case": case, "allocation_refused": True}))
        else:
            allocation[0] = 1
            sys.stdout.buffer.write(_response("SUCCEEDED", evidence={"probe_case": case, "allocation_refused": False}))
            del allocation
    elif case == "sleep":
        time.sleep(_PROBE_SLEEP_SECONDS)
        sys.stdout.buffer.write(_response("SUCCEEDED", evidence={"probe_case": case, "completed": True}))
    elif case == "attempt-child":
        blocked = False
        child: subprocess.Popen[bytes] | None = None
        try:
            child = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(5)"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                shell=False, close_fds=True,
            )
        except OSError:
            blocked = True
        if child is not None:
            try:
                child.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                blocked = False
                child.kill()
                child.wait(timeout=2.0)
            else:
                # The Job may reject or immediately terminate the nested process.
                blocked = True
        sys.stdout.buffer.write(_response("SUCCEEDED", evidence={"probe_case": case, "child_blocked": blocked}))
    elif case == "output":
        sys.stdout.buffer.write(b"X" * _PROBE_OUTPUT_BYTES)
    elif case == "crash":
        os._exit(23)
    else:
        raise ValueError("invalid")
    sys.stdout.buffer.flush()


def main() -> int:
    try:
        request = _read_request()
        op = request.get("op")
        if op == "probe":
            if set(request) != {"op", "case"} or request.get("case") not in _ALLOWED_PROBES:
                raise ValueError("invalid")
            _probe(str(request["case"]))
            return 0
        if op == "verify_gc_safetensors":
            required = {
                "op", "project_id", "artifact_id", "task_type", "package_path",
                "expected_sha256", "expected_size_bytes", "allowed_loader_ids",
            }
            if set(request) != required:
                raise ValueError("invalid")
            from chrom_platform.adapters.gc.package_loader import build_gc_safetensors_package_loader
            from .package_verification import verify_package_request

            loader = build_gc_safetensors_package_loader()
            evidence = verify_package_request(request, loader=loader)
            if not isinstance(evidence, Mapping):
                raise TypeError("invalid evidence")
            sys.stdout.buffer.write(_response("SUCCEEDED", evidence=evidence))
            sys.stdout.buffer.flush()
            return 0
        raise ValueError("invalid")
    except BaseException:
        try:
            sys.stdout.buffer.write(_response("FAILED", error_code="VERIFY_FAILED"))
            sys.stdout.buffer.flush()
        except BaseException:
            return 2
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
