"""Narrow, dry-run-first maintenance entrypoint for one assistant run.

This command accepts one fully identified run and never performs a provider
request.  ``--apply`` is required before the transactional failure reconcile
can write state and its audit row.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from typing import Sequence

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .production_worker import AsyncAssistantExecutionStore


_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def _id(value: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise argparse.ArgumentTypeError("identifier must be a bounded simple identifier")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("run-id", "job-id", "agent-attempt-id", "llm-call-id", "project-id", "actor-id"):
        parser.add_argument(f"--{name}", required=True, type=_id)
    parser.add_argument("--attempt", required=True, type=int)
    parser.add_argument("--execution-token", required=True, type=_id)
    parser.add_argument("--fencing-token", required=True, type=int)
    parser.add_argument("--request-id", type=_id)
    parser.add_argument("--apply", action="store_true", help="write one exact failed terminal state; default is read-only dry-run")
    return parser


def _database_url() -> str:
    raw = os.environ.get("DATABASE_URL", "").strip()
    if not raw:
        raise RuntimeError("DATABASE_URL_REQUIRED")
    parsed = make_url(raw)
    if parsed.get_backend_name() != "mysql" or parsed.get_driver_name() != "asyncmy":
        raise RuntimeError("DATABASE_URL_MUST_USE_MYSQL_ASYNCMY")
    return raw


async def run_once(namespace: argparse.Namespace) -> dict[str, object]:
    if namespace.attempt < 1 or namespace.fencing_token < 1:
        raise RuntimeError("INVALID_EXACT_FENCE")
    database_url = _database_url()
    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)
        result = await AsyncAssistantExecutionStore(factory).reconcile_finalization_failure(
            run_id=namespace.run_id,
            job_id=namespace.job_id,
            attempt=namespace.attempt,
            agent_attempt_id=namespace.agent_attempt_id,
            llm_call_id=namespace.llm_call_id,
            execution_token=namespace.execution_token,
            fencing_token=namespace.fencing_token,
            project_id=namespace.project_id,
            actor_id=namespace.actor_id,
            apply=bool(namespace.apply),
            request_id=namespace.request_id,
        )
        return {
            "ok": True,
            "operation": "assistant.run.reconcile_finalization_failure",
            "mode": "APPLY" if namespace.apply else "DRY_RUN",
            "scope": {
                "run_id": namespace.run_id,
                "job_id": namespace.job_id,
                "attempt": namespace.attempt,
                "agent_attempt_id": namespace.agent_attempt_id,
                "llm_call_id": namespace.llm_call_id,
                "project_id": namespace.project_id,
                "actor_id": namespace.actor_id,
            },
            "result": result,
        }
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        namespace = parser.parse_args(argv)
        output = asyncio.run(run_once(namespace))
        print(json.dumps(output, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        return 0 if output.get("result", {}).get("matched", False) else 2
    except SystemExit:
        raise
    except Exception as exc:  # safe CLI boundary: never print URL, token, body, or traceback
        print(json.dumps({"ok": False, "error": {"code": type(exc).__name__}}, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        return 2


__all__ = ["build_parser", "main", "run_once"]

