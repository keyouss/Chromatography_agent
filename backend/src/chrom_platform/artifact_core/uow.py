"""Explicit transaction boundary for artifact HTTP writes."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from typing import Protocol

from sqlalchemy.orm import Session

from chrom_platform.core.dto import ResourceRef
from chrom_platform.core.idempotency import IdempotencyStore

from .repository import SQLAlchemyArtifactRepository
from chrom_platform.infrastructure.repositories import SQLAlchemyIdempotencyStore
from .service import ArtifactJobPort, JobSubmission
from chrom_platform.task_core.repository import SQLAlchemyTaskRepository
from chrom_platform.task_core.service import add_job_rows


class ArtifactUnitOfWork(Protocol):
    """One caller-owned DB transaction exposing both required write ports."""

    artifacts: object
    idempotency: IdempotencyStore
    jobs: ArtifactJobPort | None

    def __enter__(self) -> "ArtifactUnitOfWork": ...

    def __exit__(self, exc_type, exc_value, traceback) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def business_savepoint(self): ...


class SQLAlchemyArtifactUnitOfWork:
    """Bind artifact rows and idempotency rows to the same SQLAlchemy session.

    The session factory is supplied by assembly.  This class owns the session
    lifetime but never invents a database backend.  Deletion cleanup remains
    outside this class and must be represented by a DB Job + Outbox adapter.
    """

    def __init__(self, session_factory: Callable[[], Session], *, validation_queue_available: bool = False) -> None:
        self._session_factory = session_factory
        self._validation_queue_available = validation_queue_available
        self.session: Session | None = None
        self.artifacts: ArtifactRepository | None = None
        self.idempotency: IdempotencyStore | None = None
        self.jobs: ArtifactJobPort | None = None
        self._task_repository = SQLAlchemyTaskRepository(session_factory)

    def __enter__(self) -> "SQLAlchemyArtifactUnitOfWork":
        if self.session is not None:
            raise RuntimeError("artifact unit of work cannot be entered twice")
        self.session = self._session_factory()
        self.artifacts = SQLAlchemyArtifactRepository(self.session)
        self.idempotency = SQLAlchemyIdempotencyStore(self.session)
        self.jobs = (
            _SQLAlchemyArtifactJobPort(self._task_repository, self.session)
            if self._validation_queue_available else None
        )
        return self

    def commit(self) -> None:
        if self.session is None:
            raise RuntimeError("artifact unit of work is not active")
        self.session.commit()

    def rollback(self) -> None:
        if self.session is not None:
            self.session.rollback()

    @contextmanager
    def business_savepoint(self):
        if self.session is None:
            raise RuntimeError("artifact unit of work is not active")
        with self.session.begin_nested():
            yield

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        try:
            if exc_type is not None:
                self.rollback()
        finally:
            if self.session is not None:
                self.session.close()
            self.session = None
            self.artifacts = None
            self.idempotency = None
            self.jobs = None


class _SQLAlchemyArtifactJobPort:
    """Create the validation Job and Outbox in the artifact UOW transaction."""

    transactional_outbox = True

    def __init__(self, repository: SQLAlchemyTaskRepository, session: Session) -> None:
        self._repository, self._session = repository, session

    def enqueue_validation(self, *, artifact_id: str, project_id: str, kind: str, owner_id: str) -> JobSubmission:
        job = add_job_rows(
            self._repository,
            self._session,
            project_id=project_id,
            owner_id=owner_id,
            kind="artifact_validation",
            resource_ref=ResourceRef(type="artifact", id=artifact_id),
            cancelable=False,
            retryable=False,
            resume_mode="restart",
            deadline_at=None,
        )
        return JobSubmission(job_id=job.id, resource_ref=ResourceRef(type="artifact", id=artifact_id))


class ArtifactUnitOfWorkFactory(Protocol):
    def __call__(self) -> ArtifactUnitOfWork: ...


__all__ = ["ArtifactUnitOfWork", "ArtifactUnitOfWorkFactory", "SQLAlchemyArtifactUnitOfWork"]
