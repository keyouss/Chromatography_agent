"""Assistant production worker assembly seam."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from chrom_platform.core.dto import ActorContext

from chrom_platform.modules.assistant.production_worker import (
    AssistantProductionWorker,
    build_assistant_worker_handler,
)


def build_assistant_worker(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    runtime: object | None = None,
    runtime_factory: object | None = None,
    services_factory: object | None = None,
    actor_context: ActorContext | None = None,
    cancel_probe: object | None = None,
    lease_seconds: int = 60,
) -> AssistantProductionWorker:
    return build_assistant_worker_handler(
        session_factory=session_factory,
        runtime=runtime,
        runtime_factory=runtime_factory,
        services_factory=services_factory,
        actor_context=actor_context,
        cancel_probe=cancel_probe,
        lease_seconds=lease_seconds,
    )


__all__ = ["build_assistant_worker"]
