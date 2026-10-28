"""Training production worker assembly seam."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chrom_platform.modules.training.production_worker import (
    TrainingProductionWorker,
    build_training_worker_handler,
)


def build_training_worker(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    templates: object | None = None,
    candidate_registration: object | None = None,
    data_loader: object | None = None,
    executor: object | None = None,
    lease_seconds: int = 60,
) -> TrainingProductionWorker:
    return build_training_worker_handler(
        session_factory=session_factory,
        templates=templates,
        candidate_registration=candidate_registration,
        data_loader=data_loader,
        executor=executor,
        lease_seconds=lease_seconds,
    )


__all__ = ["build_training_worker"]
