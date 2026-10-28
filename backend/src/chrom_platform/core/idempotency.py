"""Server-side idempotency port.

Production implementations belong to the persistence layer.  Keeping the
port here prevents routers from silently using an in-memory success cache.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class IdempotencyScope:
    actor_id: str
    project_id: str
    method: str
    route_identity: str
    key: str


@dataclass(frozen=True)
class IdempotencyRecord:
    payload_hash: str
    status: str
    status_code: int | None = None
    response: dict[str, Any] | None = None


@dataclass(frozen=True)
class BeginResult:
    """Result of an atomic reservation attempt.

    ``acquired`` is true only for the caller that created the in-progress
    record.  An existing ``IN_PROGRESS`` record is therefore distinguishable
    from a newly acquired reservation with the same status.
    """

    record: IdempotencyRecord
    acquired: bool


class IdempotencyStore(Protocol):
    def get(self, scope: IdempotencyScope) -> IdempotencyRecord | None: ...

    def begin(self, scope: IdempotencyScope, payload_hash: str) -> BeginResult: ...

    def complete(
        self,
        scope: IdempotencyScope,
        payload_hash: str,
        status_code: int,
        response: dict[str, Any],
    ) -> None: ...

    def fail(self, scope: IdempotencyScope, payload_hash: str, error_code: str) -> None: ...


# Descriptive alias retained for callers that prefer the longer name.
IdempotencyBeginResult = BeginResult

__all__ = ["BeginResult", "IdempotencyBeginResult", "IdempotencyRecord", "IdempotencyScope", "IdempotencyStore"]
