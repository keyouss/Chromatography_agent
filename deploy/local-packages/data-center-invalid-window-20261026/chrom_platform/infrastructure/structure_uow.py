"""SQLAlchemy UnitOfWork for atomic structure normalization."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta
from typing import Callable, Iterator

from sqlalchemy.orm import Session

from .models import AuditEvent
from chrom_platform.structure_core.uow import StructureTransaction

from .repositories import SQLAlchemyIdempotencyStore, SQLAlchemyStructureRepository


class SQLAlchemyStructureUnitOfWork:
    """Bind structure dedupe and idempotency to one caller-owned transaction.

    The base app assembly may leave this port unset until deployment wiring is
    provided.  The normalize route puts access checks before entering
    ``transaction`` and performs reservation, save, and completion through the
    returned repositories before leaving the context.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        retention: timedelta = timedelta(hours=24),
    ) -> None:
        self._session_factory = session_factory
        self._retention = retention

    @contextmanager
    def transaction(self) -> Iterator[StructureTransaction]:
        session = self._session_factory()
        try:
            with session.begin():
                yield StructureTransaction(
                    structures=SQLAlchemyStructureRepository(session),
                    idempotency=SQLAlchemyIdempotencyStore(session, retention=self._retention),
                    audit=_SQLAlchemyStructureAuditRepository(session),
                )
        except Exception:
            # Explicit rollback also covers drivers that release a nested
            # savepoint during repository dedupe before the outer context
            # observes the application exception.
            session.rollback()
            raise
        finally:
            session.close()


class _SQLAlchemyStructureAuditRepository:
    """Stage structure audit rows in the caller's normalization transaction."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def record(self, event: AuditEvent) -> AuditEvent:
        self._session.add(event)
        self._session.flush()
        return event


__all__ = ["SQLAlchemyStructureUnitOfWork"]
