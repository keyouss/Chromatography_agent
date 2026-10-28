"""MySQL-backed adapters for shared application ports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from chrom_platform.core.dto import ActorContext
from chrom_platform.core.idempotency import IdempotencyRecord as PortIdempotencyRecord
from chrom_platform.core.idempotency import IdempotencyScope

from .models import IdempotencyRecord as IdempotencyRow
from .models import Project


@dataclass(frozen=True)
class _FallbackBeginResult:
    """Compatibility result used until core exposes BeginResult."""

    record: PortIdempotencyRecord
    acquired: bool


class IdempotencyConflict(ValueError):
    """The same scope was used with a different request payload."""


class IdempotencyStateError(RuntimeError):
    """The reservation cannot be completed from its current state."""


_PUBLIC_FAILURE_CODES = frozenset(
    {
        "DEPENDENCY_UNAVAILABLE",
        "VALIDATION_FAILED",
        "STATE_CONFLICT",
        "OPERATION_TIMEOUT",
        "CONFIRMATION_REQUIRED",
        "DATA_CONFLICT",
        "DATA_VALIDATION_FAILED",
        "RESOURCE_NOT_FOUND",
        "DATA_CAPABILITY_UNAVAILABLE",
    }
)


def _begin_result(record: PortIdempotencyRecord, acquired: bool) -> Any:
    """Construct core's new BeginResult without changing core from this layer."""

    try:
        from chrom_platform.core.idempotency import BeginResult
    except ImportError:
        return _FallbackBeginResult(record=record, acquired=acquired)
    return BeginResult(record=record, acquired=acquired)


class SQLAlchemyProjectReader:
    """Read only real projects within a verified ActorContext scope."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def list_accessible(
        self,
        actor: ActorContext,
        *,
        limit: int,
        after_id: str | None,
    ) -> list[Project]:
        # ``limit + 1`` is intentionally allowed by the application for
        # lookahead pagination (the public page size itself remains <= 200).
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 201:
            raise ValueError("limit must be between 1 and 201")
        if after_id is not None and (not isinstance(after_id, str) or not after_id):
            raise ValueError("after_id must be a non-empty string or None")
        allowed_ids = tuple(dict.fromkeys(actor.project_ids))
        if not allowed_ids:
            return []
        session = self._session_factory()
        try:
            statement = select(Project).where(Project.id.in_(allowed_ids))
            if after_id is not None:
                statement = statement.where(Project.id > after_id)
            if actor.tenant_id is not None:
                statement = statement.where(Project.tenant_id == actor.tenant_id)
            statement = statement.order_by(Project.id.asc()).limit(limit)
            return list(session.scalars(statement).all())
        finally:
            session.close()

    def get_accessible(self, actor: ActorContext, project_id: str) -> Project | None:
        if not actor.can_access_project(project_id):
            return None
        session = self._session_factory()
        try:
            statement = select(Project).where(Project.id == project_id)
            if actor.tenant_id is not None:
                statement = statement.where(Project.tenant_id == actor.tenant_id)
            return session.scalar(statement)
        finally:
            session.close()


class SQLAlchemyIdempotencyStore:
    """Atomic MySQL idempotency reservation and completion adapter.

    ``begin`` never performs a check-then-insert.  It inserts first and relies
    on the database unique key; a concurrent duplicate is then read back as
    ``acquired=False``.  This is the only safe shape across worker processes.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session] | Session,
        *,
        retention: timedelta = timedelta(hours=24),
    ) -> None:
        self._session_factory = session_factory
        self._retention = retention

    def _session(self) -> tuple[Session, bool]:
        """Return ``(session, owns_session)`` for legacy and UoW callers."""

        if callable(self._session_factory):
            return self._session_factory(), True
        return self._session_factory, False

    @staticmethod
    def _close(session: Session, owns_session: bool) -> None:
        if owns_session:
            session.close()

    @staticmethod
    def _scope_filter(scope: IdempotencyScope):
        return (
            IdempotencyRow.actor_id == scope.actor_id,
            IdempotencyRow.project_id == scope.project_id,
            IdempotencyRow.method == scope.method,
            IdempotencyRow.route_identity == scope.route_identity,
            IdempotencyRow.key == scope.key,
        )

    @staticmethod
    def _to_record(row: IdempotencyRow) -> PortIdempotencyRecord:
        return PortIdempotencyRecord(
            payload_hash=row.payload_hash,
            status=row.status,
            status_code=row.result_status_code,
            response=row.result_resource_ref,
        )

    def get(self, scope: IdempotencyScope) -> PortIdempotencyRecord | None:
        session, owns_session = self._session()
        try:
            row = session.scalar(select(IdempotencyRow).where(*self._scope_filter(scope)))
            return self._to_record(row) if row is not None else None
        finally:
            self._close(session, owns_session)

    def begin(self, scope: IdempotencyScope, payload_hash: str) -> Any:
        now = datetime.now(timezone.utc)
        row = IdempotencyRow(
            id="idem_" + uuid4().hex,
            actor_id=scope.actor_id,
            project_id=scope.project_id,
            method=scope.method,
            route_identity=scope.route_identity,
            key=scope.key,
            payload_hash=payload_hash,
            status="IN_PROGRESS",
            expires_at=now + self._retention,
            created_at=now,
        )
        session, owns_session = self._session()
        try:
            # INSERT first is essential: an ordinary pre-read races under
            # concurrent requests and makes both callers think they acquired.
            if owns_session:
                session.add(row)
                session.flush()
                session.commit()
            else:
                # The savepoint keeps a duplicate-key race from aborting the
                # caller-owned transaction before the winner is read back.
                begin_nested = getattr(session, "begin_nested", None)
                if callable(begin_nested):
                    with begin_nested():
                        session.add(row)
                        session.flush()
                else:
                    session.add(row)
                    session.flush()
            return _begin_result(self._to_record(row), True)
        except IntegrityError:
            if owns_session:
                session.rollback()
            existing = session.scalar(
                select(IdempotencyRow)
                .where(*self._scope_filter(scope))
                .with_for_update()
            )
            if existing is None:
                raise
            record = self._to_record(existing)
            expires_at = existing.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at <= now or (record.status == "FAILED" and record.payload_hash == payload_hash):
                # Expired reservations are outside the 24-hour replay window;
                # failed reservations are explicitly retryable. The row stays
                # locked in this transaction while its payload/result is reset.
                existing.payload_hash = payload_hash
                existing.status = "IN_PROGRESS"
                existing.error_code = None
                existing.result_resource_ref = None
                existing.result_status_code = None
                existing.completed_at = None
                existing.expires_at = now + self._retention
                if owns_session:
                    session.commit()
                return _begin_result(self._to_record(existing), True)
            # The application layer owns the 409 mapping for a same-key,
            # different-payload request.  Returning the existing record here
            # keeps the persistence port uniform for both replay and conflict.
            return _begin_result(record, False)
        finally:
            self._close(session, owns_session)

    def complete(
        self,
        scope: IdempotencyScope,
        payload_hash: str,
        status_code: int,
        response: dict[str, Any],
    ) -> None:
        session, owns_session = self._session()
        try:
            row = session.scalar(
                select(IdempotencyRow)
                .where(*self._scope_filter(scope))
                .with_for_update()
            )
            if row is None:
                raise IdempotencyStateError("idempotency reservation does not exist")
            if row.payload_hash != payload_hash:
                raise IdempotencyConflict("idempotency key was reused for a different request")
            if row.status == "COMPLETED":
                if row.result_status_code != status_code or row.result_resource_ref != response:
                    raise IdempotencyConflict("completed idempotency result differs")
                return
            if row.status != "IN_PROGRESS":
                raise IdempotencyStateError(f"cannot complete idempotency record in {row.status}")
            row.status = "COMPLETED"
            row.error_code = None
            row.result_status_code = status_code
            row.result_resource_ref = dict(response)
            row.completed_at = datetime.now(timezone.utc)
            if owns_session:
                session.commit()
        except Exception:
            if owns_session:
                session.rollback()
            raise
        finally:
            self._close(session, owns_session)


    def fail(self, scope: IdempotencyScope, payload_hash: str, error_code: str) -> None:
        """Persist a public failure without storing SQL, traces, or exceptions."""

        if error_code not in _PUBLIC_FAILURE_CODES:
            raise ValueError("error_code is not an allowed public failure code")
        session, owns_session = self._session()
        try:
            row = session.scalar(
                select(IdempotencyRow)
                .where(*self._scope_filter(scope))
                .with_for_update()
            )
            if row is None:
                raise IdempotencyStateError("idempotency reservation does not exist")
            if row.payload_hash != payload_hash:
                raise IdempotencyConflict("idempotency key was reused for a different request")
            if row.status == "FAILED":
                if row.error_code == error_code:
                    return
                raise IdempotencyStateError("idempotency record already failed with another error code")
            if row.status != "IN_PROGRESS":
                raise IdempotencyStateError(f"cannot fail idempotency record in {row.status}")
            row.status = "FAILED"
            row.error_code = error_code
            row.result_resource_ref = None
            row.result_status_code = None
            row.completed_at = datetime.now(timezone.utc)
            if owns_session:
                session.commit()
        except Exception:
            if owns_session:
                session.rollback()
            raise
        finally:
            self._close(session, owns_session)


class SQLAlchemyStructureRepository:
    """Structure adapter bound to a caller-owned SQLAlchemy session.

    ``save`` only flushes.  The unique project/snapshot key is the dedupe
    arbiter; a savepoint makes a concurrent duplicate safe for the enclosing
    transaction.
    """

    def __init__(self, session: Session) -> None:
        if callable(session):
            raise TypeError("SQLAlchemyStructureRepository requires a caller-owned Session, not a factory")
        self._session = session

    def save(self, structure: Any) -> Any:
        # Lazy import avoids the infrastructure -> artifact_core ->
        # infrastructure package initialization cycle.
        from chrom_platform.artifact_core.models import Structure

        existing = self._session.scalar(
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
            row = Structure(
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
            try:
                begin_nested = getattr(self._session, "begin_nested", None)
                if callable(begin_nested):
                    with begin_nested():
                        self._session.add(row)
                        self._session.flush()
                else:
                    self._session.add(row)
                    self._session.flush()
                existing = row
            except IntegrityError:
                existing = self._session.scalar(
                    select(Structure).where(
                        Structure.project_id == structure.project_id,
                        Structure.snapshot_hash == structure.snapshot_hash,
                    )
                )
                if existing is None:
                    raise
        return _to_structure_view(type(structure), existing)


def _to_structure_view(structure_type: type[Any], row: Structure) -> Any:
    return structure_type.model_validate(
        {
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
    )


__all__ = [
    "IdempotencyConflict",
    "IdempotencyStateError",
    "SQLAlchemyIdempotencyStore",
    "SQLAlchemyProjectReader",
    "SQLAlchemyStructureRepository",
]
