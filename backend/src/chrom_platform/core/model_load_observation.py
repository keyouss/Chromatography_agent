"""Small process-local observations at actual scientific cold-load sites."""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from threading import Lock

_counts: Counter[str] = Counter()
_lock = Lock()


def record_model_load(kind: str, model_id: str) -> None:
    key = f"{kind}:{model_id}"
    with _lock:
        _counts[key] += 1
        count = _counts[key]
    print("MODEL_LOAD_OBSERVATION " + json.dumps({
        "pid": os.getpid(), "event": "cold_load", "binding": key, "count": count,
    }, sort_keys=True), file=sys.stderr, flush=True)


def observe_api_model_loads() -> None:
    with _lock:
        counts = dict(_counts)
    print("MODEL_LOAD_OBSERVATION " + json.dumps({
        "pid": os.getpid(), "event": "api_registry_ready", "counts": counts,
        "total": sum(counts.values()),
    }, sort_keys=True), file=sys.stderr, flush=True)
