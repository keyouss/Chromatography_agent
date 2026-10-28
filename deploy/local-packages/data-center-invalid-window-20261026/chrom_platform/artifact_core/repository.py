"""SQLAlchemy adapter for the structure_core repository port.

The adapter intentionally accepts the port structurally instead of importing
``structure_core``.  This keeps the persistence layer independent of the HTTP
module while allowing ``StructureService`` to receive this implementation.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import Artifact, Structure


class SQLAlchemyArtifactRepository:
    """Artifact persistence adapter using a caller-owned SQLAlchemy session.

    Methods flush changes so constraint errors are visible to the caller, but
    never commit, rollback, close, or create a transaction.  The application
    service therefore has an explicit transaction boundary to compose with
    audit/confirmation records.
    """

    def __init__(self, session: Session) -> None:
        if callable(session):
            raise TypeError("SQLAlchemyArtifactRepository requires a caller-owned Session, not a factory")
        self._session = session

    def create(self, row: Artifact) -> Artifact:
        self._session.add(row)
        self._session.flush()
        return row

    def get(self, artifact_id: str, *, for_update: bool = False) -> Artifact | None:
        statement = select(Artifact).where(Artifact.id == artifact_id)
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def get_by_upload_id(self, upload_id: str, *, for_update: bool = False) -> Artifact | None:
        statement = select(Artifact).where(Artifact.metadata_json["upload_id"].as_string() == upload_id)
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def save(self, row: Artifact) -> Artifact:
        self._session.flush()
        return row


class SQLAlchemyProjectArtifactReader:
    """Read an artifact by project and ID using the caller-owned session."""

    def __init__(self, session: Session) -> None:
        if callable(session):
            raise TypeError("SQLAlchemyProjectArtifactReader requires a caller-owned Session, not a factory")
        self._session = session

    def get(self, project_id: str, artifact_id: str) -> Artifact | None:
        return self._session.scalar(
            select(Artifact).where(
                Artifact.project_id == project_id,
                Artifact.id == artifact_id,
            )
        )


class SQLAlchemyArtifactReader:
    """Read-only adapter that closes each factory-created session safely."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def get(self, artifact_id: str) -> Artifact | None:
        with self._session_factory() as session:
            row = session.scalar(select(Artifact).where(Artifact.id == artifact_id))
            if row is not None:
                session.expunge(row)
            return row

    def get_by_upload_id(self, upload_id: str) -> Artifact | None:
        with self._session_factory() as session:
            row = session.scalar(select(Artifact).where(Artifact.metadata_json["upload_id"].as_string() == upload_id))
            if row is not None:
                session.expunge(row)
            return row


class SQLAlchemyStructureRepository:
    """Persist normalized structure views in a caller-owned transaction.

    ``session_factory`` must return a SQLAlchemy Session.  A duplicate
    ``(project_id, snapshot_hash)`` is a successful deduplication and returns
    the original resource; it never silently changes its project or snapshot.
    """

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def save(self, structure: Any) -> Any:
        session = self._session_factory()
        try:
            existing = session.scalar(
                select(Structure).where(
                    Structure.project_id == structure.project_id,
                    Structure.snapshot_hash == structure.snapshot_hash,
                )
            )
            if existing is None:
                original = structure.original_input
                original_input = (
                    original.model_dump(mode="json")
                    if hasattr(original, "model_dump")
                    else dict(original)
                )
                warnings = [
                    item.model_dump(mode="json") if hasattr(item, "model_dump") else dict(item)
                    for item in structure.stereochemistry_warnings
                ]
                existing = Structure(
                    id=structure.id,
                    project_id=structure.project_id,
                    schema_version=structure.schema_version,
                    original_input=original_input,
                    canonical_smiles=structure.canonical_smiles,
                    is_valid=True,
                    has_stereochemistry=structure.has_stereochemistry,
                    stereochemistry_status=structure.stereochemistry_status,
                    stereochemistry_warnings=warnings,
                    snapshot_hash=structure.snapshot_hash,
                    source_artifact_id=original_input.get("source_artifact_id"),
                    created_at=structure.created_at,
                )
                session.add(existing)
                session.commit()
                session.refresh(existing)
            return _to_view(structure_type=type(structure), row=existing)
        except IntegrityError:
            session.rollback()
            # Two workers may normalize the same snapshot concurrently.  The
            # unique project/hash constraint is the arbiter; return its winner
            # instead of exposing a spurious duplicate failure.
            existing = session.scalar(
                select(Structure).where(
                    Structure.project_id == structure.project_id,
                    Structure.snapshot_hash == structure.snapshot_hash,
                )
            )
            if existing is None:
                raise
            return _to_view(structure_type=type(structure), row=existing)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def _to_view(*, structure_type: type[Any], row: Structure) -> Any:
    """Rebuild the service's Pydantic view without coupling to its module."""

    payload = {
        "id": row.id,
        "project_id": row.project_id,
        "schema_version": row.schema_version,
        "original_input": row.original_input,
        "canonical_smiles": row.canonical_smiles,
        "is_valid": row.is_valid,
        "has_stereochemistry": row.has_stereochemistry,
        "stereochemistry_status": row.stereochemistry_status,
        "stereochemistry_warnings": row.stereochemistry_warnings,
        "snapshot_hash": row.snapshot_hash,
        "created_at": row.created_at,
    }
    return structure_type.model_validate(payload)
