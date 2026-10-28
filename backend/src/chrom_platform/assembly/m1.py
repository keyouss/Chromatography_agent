"""M1 persistence/service assembly.

This module only wires existing ports.  It deliberately leaves scanner,
confirmation, audit, deletion queue, policy, resolver and worker ports unset;
their services then return the existing explicit unavailable/quarantine
responses.

Deployment note: this assembly currently uses synchronous SQLAlchemy
``Session``.  ``asyncmy``/``AsyncSession`` integration remains blocked pending
installation authorization and is not claimed by this adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from sqlalchemy.orm import Session

from chrom_platform.artifact_core.repository import SQLAlchemyArtifactReader
from chrom_platform.artifact_core.service import ArtifactService
from chrom_platform.artifact_core.storage import LocalArtifactStore
from chrom_platform.artifact_core.uow import SQLAlchemyArtifactUnitOfWork
from chrom_platform.infrastructure.repositories import (
    SQLAlchemyIdempotencyStore,
    SQLAlchemyProjectReader,
)
from chrom_platform.infrastructure.structure_uow import SQLAlchemyStructureUnitOfWork
from chrom_platform.task_core.repository import SQLAlchemyTaskRepository
from chrom_platform.task_core.policy import DeploymentTaskPolicy
from chrom_platform.task_core.service import ConfirmationService, JobService


@dataclass(frozen=True)
class M1Assembly:
    """All optional M1 ports produced from one shared session factory."""

    session_factory: Callable[[], Session] | None = None
    structure_uow: SQLAlchemyStructureUnitOfWork | None = None
    project_reader: SQLAlchemyProjectReader | None = None
    idempotency_store: SQLAlchemyIdempotencyStore | None = None
    artifact_reader: SQLAlchemyArtifactReader | None = None
    artifact_service: ArtifactService | None = None
    artifact_uow_factory: Callable[[], SQLAlchemyArtifactUnitOfWork] | None = None
    task_repository: SQLAlchemyTaskRepository | None = None
    job_service: JobService | None = None
    confirmation_service: ConfirmationService | None = None


def assemble_m1(
    session_factory: Callable[[], Session] | None,
    *,
    artifact_root: str | None,
    artifact_validation_available: bool = False,
) -> M1Assembly:
    """Assemble M1 when persistence exists; never invent a fallback backend."""

    if session_factory is None:
        return M1Assembly()

    structure_uow = SQLAlchemyStructureUnitOfWork(session_factory)
    project_reader = SQLAlchemyProjectReader(session_factory)
    idempotency_store = SQLAlchemyIdempotencyStore(session_factory)

    task_repository = SQLAlchemyTaskRepository(session_factory)
    # The local deployment has a server-owned actor/project scope.  Bind the
    # explicit read policy here; an absent policy remains unavailable in
    # independently assembled/test services and is still reported as 503.
    job_service = JobService(task_repository, policy=DeploymentTaskPolicy(), queue_available=False)
    confirmation_service = ConfirmationService(
        task_repository,
        policy=DeploymentTaskPolicy(),
        job_service=job_service,
        resolver=None,
    )
    job_service.confirmation_service = confirmation_service

    if not artifact_root or not artifact_root.strip():
        return M1Assembly(
            session_factory=session_factory,
            structure_uow=structure_uow,
            project_reader=project_reader,
            idempotency_store=idempotency_store,
            task_repository=task_repository,
            job_service=job_service,
            confirmation_service=confirmation_service,
        )

    try:
        artifact_reader = SQLAlchemyArtifactReader(session_factory)
        artifact_store = LocalArtifactStore(artifact_root)
    except Exception:  # noqa: BLE001 - invalid storage stays unavailable
        return M1Assembly(
            session_factory=session_factory,
            structure_uow=structure_uow,
            project_reader=project_reader,
            idempotency_store=idempotency_store,
            task_repository=task_repository,
            job_service=job_service,
            confirmation_service=confirmation_service,
        )

    return M1Assembly(
        session_factory=session_factory,
        structure_uow=structure_uow,
        project_reader=project_reader,
        idempotency_store=idempotency_store,
        artifact_reader=artifact_reader,
        artifact_service=ArtifactService(artifact_reader, artifact_store),
        artifact_uow_factory=lambda: SQLAlchemyArtifactUnitOfWork(
            session_factory, validation_queue_available=artifact_validation_available,
        ),
        task_repository=task_repository,
        job_service=job_service,
        confirmation_service=confirmation_service,
    )


__all__ = ["M1Assembly", "assemble_m1"]
