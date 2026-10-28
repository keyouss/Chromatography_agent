"""Application-service ports for prediction creation.

These protocols are deliberately storage- and worker-agnostic.  Concrete
database, queue, and adapter wiring belongs to a later build assembly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol

from pydantic import BaseModel

from chrom_platform.core.errors import InputValidationError
from chrom_platform.task_core.service import OperationContext

from .adapters import ToolCapability
from .schemas import (
    ModelSelection,
    ModelSnapshot,
    PredictionAccepted,
    PredictionCreate,
    PredictionExecutionInputSnapshot,
)


@dataclass(frozen=True)
class StructureSnapshot:
    id: str
    project_id: str
    normalized_smiles: str


@dataclass(frozen=True)
class IdempotencyLookup:
    accepted: PredictionAccepted
    payload_hash: str


@dataclass(frozen=True)
class AtomicPredictionCreateResult:
    accepted: PredictionAccepted
    replayed: bool = False


@dataclass(frozen=True)
class PredictionCreateResult:
    data: PredictionAccepted
    replayed: bool = False
    status_code: int = 202

    @property
    def accepted(self) -> PredictionAccepted:
        return self.data

    def __getattr__(self, name: str):
        return getattr(self.data, name)


class PredictionAccessPort(Protocol):
    def ensure_project_access(
        self, ctx: OperationContext, project_id: str, *, permission: str
    ) -> None: ...


class PredictionStructurePort(Protocol):
    def load_many(
        self, ctx: OperationContext, project_id: str, structure_refs: list[str]
    ) -> list[StructureSnapshot]: ...

    def validate_enantiomer_pair(
        self,
        ctx: OperationContext,
        project_id: str,
        structures: list[StructureSnapshot],
        *,
        chromatography_type: str,
    ) -> None: ...

    def validate_chiral_proposal(
        self,
        ctx: OperationContext,
        project_id: str,
        structure: StructureSnapshot,
        *,
        proposal_id: str,
    ) -> None: ...


class PredictionCapabilityPort(Protocol):
    def get_tool(
        self, chromatography_type: str, tool: str, schema_version: str
    ) -> ToolCapability: ...

    def validate_request(
        self,
        chromatography_type: str,
        request: PredictionCreate,
        *,
        model_snapshot: ModelSnapshot,
        authorized_modifier_structures: Mapping[str, str] | None = None,
    ) -> PredictionCreate:
        """Raise InputValidationError only for caller-controlled invalid input."""
        ...


class PredictionModelPort(Protocol):
    def resolve_model(
        self,
        ctx: OperationContext,
        request: PredictionCreate,
        *,
        tool: ToolCapability,
    ) -> ModelSnapshot: ...

    def execute_snapshot(
        self,
        type_id: str,
        request: PredictionCreate,
        *,
        model_snapshot: ModelSnapshot,
    ) -> object: ...


class PredictionIdempotencyPort(Protocol):
    def lookup(
        self,
        ctx: OperationContext,
        project_id: str,
        *,
        key: str,
        payload_hash: str,
    ) -> IdempotencyLookup | None: ...


class PredictionReadPort(Protocol):
    def get(self, ctx: OperationContext, prediction_id: str) -> object: ...


class AtomicPredictionCreatePort(Protocol):
    """One transaction for prediction, record, job, outbox and idempotency.

    The implementation must enforce the idempotency key uniqueness decision in
    that same transaction under concurrency: an identical key and payload
    returns the existing accepted result with ``replayed=True``; an identical
    key with a different payload raises the public 409 conflict semantic.
    """

    def create(
        self,
        ctx: OperationContext,
        request: PredictionCreate,
        *,
        model_snapshot: ModelSnapshot,
        input_snapshot: PredictionExecutionInputSnapshot,
        source: str,
        idempotency_key: str,
        payload_hash: str,
    ) -> AtomicPredictionCreateResult: ...


__all__ = [
    "AtomicPredictionCreatePort",
    "AtomicPredictionCreateResult",
    "IdempotencyLookup",
    "PredictionAccessPort",
    "PredictionCapabilityPort",
    "PredictionCreateResult",
    "PredictionIdempotencyPort",
    "PredictionModelPort",
    "PredictionReadPort",
    "PredictionStructurePort",
    "StructureSnapshot",
]
