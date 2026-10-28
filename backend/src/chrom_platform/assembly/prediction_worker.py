"""Prediction worker assembly seam used by the profile worker registry."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chrom_platform.modules.prediction.adapters import CapabilityRegistry
from chrom_platform.modules.prediction.production_worker import (
    PredictionProductionWorker,
    build_prediction_worker_handler,
)


def build_prediction_worker(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    registry: CapabilityRegistry,
) -> PredictionProductionWorker:
    return build_prediction_worker_handler(session_factory=session_factory, registry=registry)


__all__ = ["build_prediction_worker"]
