"""Persistence port and SQLAlchemy adapter for task core.

The adapter exposes only fixed row operations.  It does not know how to
import or execute a worker handler; an outbox row is the only queue handoff.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator, Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from chrom_platform.core.idempotency import IdempotencyScope
from chrom_platform.infrastructure.models import (
    AuditEvent,
    Confirmation,
    IdempotencyRecord,
    Job,
    JobAttempt,
    JobEvent,
    OutboxEvent,
)


class TaskRepository(Protocol):
    @contextmanager
    def transaction(self, session: Session | None = None) -> Iterator[Session]: ...

    def get_job(self, session: Session, job_id: str, *, for_update: bool = False) -> Job | None: ...
    def get_attempts(self, session: Session, job_id: str, *, limit: int | None = None, after_attempt: int | None = None, after_id: str | None = None) -> list[JobAttempt]: ...
    def get_events(self, session: Session, job_id: str, after_seq: int, limit: int) -> list[JobEvent]: ...
    def list_jobs(self, session: Session, project_id: str, *, status: str | None = None, kind: str | None = None, limit: int | None = None, after_created_at: Any | None = None, after_id: str | None = None) -> list[Job]: ...
    def next_event_seq(self, session: Session, job_id: str) -> int: ...
    def get_confirmation(self, session: Session, confirmation_id: str, *, for_update: bool = False) -> Confirmation | None: ...
    def get_idempotency(self, session: Session, scope: IdempotencyScope, *, for_update: bool = False) -> IdempotencyRecord | None: ...


class SQLAlchemyTaskRepository:
    """MySQL repository.  A supplied session remains owned by its caller."""

    def __init__(self, session_factory) -> None:
        self.session_factory = session_factory

    @contextmanager
    def transaction(self, session: Session | None = None) -> Iterator[Session]:
        if session is not None:
            yield session
            return
        db = self.session_factory()
        try:
            if hasattr(db, "begin"):
                with db.begin():
                    yield db
            else:
                # Small test/session adapters may expose commit/rollback but
                # not SQLAlchemy's context manager.  Production sessions use
                # the branch above.
                yield db
                db.commit()
        except Exception:
            # ``begin`` already rolls back, but fake/session adapters often
            # only implement rollback; retaining this makes failure explicit.
            try:
                db.rollback()
            except Exception:
                pass
            raise
        finally:
            db.close()

    @staticmethod
    def _one(session: Session, statement, *, for_update: bool = False):
        if for_update:
            statement = statement.with_for_update()
        return session.scalar(statement)

    def get_job(self, session: Session, job_id: str, *, for_update: bool = False) -> Job | None:
        return self._one(session, select(Job).where(Job.id == job_id), for_update=for_update)

    def get_attempts(self, session: Session, job_id: str, *, limit: int | None = None, after_attempt: int | None = None, after_id: str | None = None) -> list[JobAttempt]:
        statement = select(JobAttempt).where(JobAttempt.job_id == job_id)
        if after_attempt is not None and after_id is not None:
            statement = statement.where((JobAttempt.attempt > after_attempt) | ((JobAttempt.attempt == after_attempt) & (JobAttempt.id > after_id)))
        statement = statement.order_by(JobAttempt.attempt, JobAttempt.id)
        if limit is not None:
            statement = statement.limit(limit)
        return list(session.scalars(statement))

    def get_events(self, session: Session, job_id: str, after_seq: int, limit: int) -> list[JobEvent]:
        statement = (
            select(JobEvent)
            .where(JobEvent.job_id == job_id, JobEvent.seq > after_seq)
            .order_by(JobEvent.seq)
            .limit(limit)
        )
        return list(session.scalars(statement))

    def list_jobs(self, session: Session, project_id: str, *, status: str | None = None, kind: str | None = None, limit: int | None = None, after_created_at: Any | None = None, after_id: str | None = None) -> list[Job]:
        statement = select(Job).where(Job.project_id == project_id)
        if status:
            statement = statement.where(Job.status == status)
        if kind:
            statement = statement.where(Job.kind == kind)
        if after_created_at is not None and after_id is not None:
            statement = statement.where((Job.created_at < after_created_at) | ((Job.created_at == after_created_at) & (Job.id < after_id)))
        statement = statement.order_by(Job.created_at.desc(), Job.id.desc())
        if limit is not None:
            statement = statement.limit(limit)
        return list(session.scalars(statement))

    def next_event_seq(self, session: Session, job_id: str) -> int:
        current = session.scalar(select(func.max(JobEvent.seq)).where(JobEvent.job_id == job_id))
        # Seq 0 is the canonical initial event and is falsy in Python; using
        # ``current or -1`` would allocate seq 0 again for attempt two.
        return (int(current) + 1) if current is not None else 0

    def get_confirmation(self, session: Session, confirmation_id: str, *, for_update: bool = False) -> Confirmation | None:
        return self._one(session, select(Confirmation).where(Confirmation.id == confirmation_id), for_update=for_update)

    def get_idempotency(self, session: Session, scope: IdempotencyScope, *, for_update: bool = False) -> IdempotencyRecord | None:
        statement = select(IdempotencyRecord).where(
            IdempotencyRecord.actor_id == scope.actor_id,
            IdempotencyRecord.project_id == scope.project_id,
            IdempotencyRecord.method == scope.method,
            IdempotencyRecord.route_identity == scope.route_identity,
            IdempotencyRecord.key == scope.key,
        )
        return self._one(session, statement, for_update=for_update)

    @staticmethod
    def add(session: Session, row: Any) -> None:
        session.add(row)

    @staticmethod
    def flush(session: Session) -> None:
        session.flush()

    @staticmethod
    def complete_idempotency(row: IdempotencyRecord, *, status: str, result: dict[str, Any] | None, status_code: int | None, completed_at: datetime | None) -> None:
        row.status = status
        row.result_resource_ref = result
        row.result_status_code = status_code
        row.completed_at = completed_at


__all__ = ["SQLAlchemyTaskRepository", "TaskRepository"]
