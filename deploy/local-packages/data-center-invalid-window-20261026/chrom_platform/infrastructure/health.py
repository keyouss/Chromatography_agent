"""Small dependency health ports used by readiness implementations.

Health is deliberately an active probe.  A syntactically valid URL is not a
healthy dependency: MySQL must answer ``SELECT 1`` and Redis must answer
``PING``.  This module does not create containers or retry indefinitely.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Protocol

from sqlalchemy.engine import Engine

from .db import check_database_health, create_database_engine


class DependencyStatus(StrEnum):
    READY = "READY"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class DependencyHealthResult:
    id: str
    status: DependencyStatus
    checked_at: datetime
    reason: str | None = None


class RedisPingPort(Protocol):
    def ping(self) -> Any: ...


def _now() -> datetime:
    return datetime.now(timezone.utc)


def database_health(engine: Engine | None) -> DependencyHealthResult:
    """Probe a configured SQLAlchemy engine with a real ``SELECT 1``."""

    if engine is None:
        return DependencyHealthResult("database", DependencyStatus.UNAVAILABLE, _now(), "NOT_CONFIGURED")
    ok, reason = check_database_health(engine)
    return DependencyHealthResult(
        "database",
        DependencyStatus.READY if ok else DependencyStatus.UNAVAILABLE,
        _now(),
        reason,
    )


def database_health_from_url(database_url: str | None) -> DependencyHealthResult:
    """Build a MySQL engine and actively probe it, returning UNAVAILABLE on any failure."""

    if not database_url:
        return database_health(None)
    engine = None
    try:
        engine = create_database_engine(database_url)
        return database_health(engine)
    except Exception as exc:  # noqa: BLE001 - do not leak driver/URL details
        return DependencyHealthResult("database", DependencyStatus.UNAVAILABLE, _now(), type(exc).__name__)
    finally:
        if engine is not None:
            engine.dispose()


def redis_health(client: RedisPingPort | None) -> DependencyHealthResult:
    """Probe Redis through a tiny injectable port.

    ``configured`` distinguishes a missing URL from a client whose connection
    could not be built.  In both cases the result is UNAVAILABLE; callers never
    get READY merely because a URL exists.
    """

    if client is None:
        return DependencyHealthResult("queue", DependencyStatus.UNAVAILABLE, _now(), "NOT_CONFIGURED")
    try:
        value = client.ping()
    except Exception as exc:  # noqa: BLE001 - health probes must not expose details
        return DependencyHealthResult("queue", DependencyStatus.UNAVAILABLE, _now(), type(exc).__name__)
    if value is False:
        return DependencyHealthResult("queue", DependencyStatus.UNAVAILABLE, _now(), "PING_FAILED")
    return DependencyHealthResult("queue", DependencyStatus.READY, _now(), None)


def create_redis_ping_client(redis_url: str | None) -> RedisPingPort | None:
    """Create a Redis client port without treating URL parsing as readiness."""

    if not redis_url:
        return None
    try:
        from redis import Redis

        return Redis.from_url(redis_url, socket_connect_timeout=1.0, socket_timeout=1.0)
    except Exception:  # noqa: BLE001 - the subsequent health result is UNAVAILABLE
        return None
