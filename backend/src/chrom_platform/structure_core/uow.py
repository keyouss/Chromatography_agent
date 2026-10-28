"""Transaction port for structure normalization assembly."""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol

from chrom_platform.core.idempotency import IdempotencyStore

from .service import StructureRepository


@dataclass(frozen=True)
class StructureTransaction:
    """Repositories sharing one caller-owned database transaction."""

    structures: StructureRepository
    idempotency: IdempotencyStore
    audit: object | None = None


class StructureUnitOfWork(Protocol):
    """Future router/service assembly port for atomic normalization."""

    def transaction(self) -> AbstractContextManager[StructureTransaction]: ...


__all__ = ["StructureTransaction", "StructureUnitOfWork"]
