"""Pure Python state and confirmation binding rules."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import json
from typing import Any, Mapping


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELED = "CANCELED"
    TIMED_OUT = "TIMED_OUT"
    INTERRUPTED = "INTERRUPTED"


class ConfirmationStatus(StrEnum):
    CONFIRMED = "CONFIRMED"
    CONSUMED = "CONSUMED"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"
    REVOKED = "REVOKED"


class OutboxStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    SENT = "SENT"
    FAILED = "FAILED"


class InvalidTransition(ValueError):
    pass


def idempotency_scope(
    *, actor_id: str, project_id: str, method: str, route_identity: str, key: str
) -> tuple[str, str, str, str, str]:
    """Return the complete server-side idempotency scope.

    Actor and project are deliberately part of the key.  A client cannot
    widen scope by putting either value in a JSON body; the caller supplies
    these from verified request context and the route.
    """

    values = (actor_id, project_id, method, route_identity, key)
    if any(not isinstance(value, str) or not value for value in values):
        raise ValueError("idempotency scope values must be non-empty strings")
    if len(key) > 128:
        raise ValueError("idempotency key exceeds 128 characters")
    return values


def fencing_token_matches(*, worker_token: int, current_token: int) -> bool:
    """Only the currently leased fencing token may apply a side effect."""

    return worker_token > 0 and current_token > 0 and worker_token == current_token


def transition_outbox(current: OutboxStatus | str, *, published: bool) -> OutboxStatus:
    """Resolve one dispatch attempt without turning a send failure into loss."""

    current = OutboxStatus(current)
    if current is OutboxStatus.SENT:
        return current
    if published:
        return OutboxStatus.SENT
    if current in {OutboxStatus.PENDING, OutboxStatus.PROCESSING, OutboxStatus.FAILED}:
        return OutboxStatus.PENDING
    raise InvalidTransition(f"invalid Outbox transition {current}")


_TERMINAL = {
    JobStatus.SUCCEEDED,
    JobStatus.PARTIAL,
    JobStatus.FAILED,
    JobStatus.CANCELED,
    JobStatus.TIMED_OUT,
    JobStatus.INTERRUPTED,
}
_RETRYABLE_TERMINAL = {JobStatus.FAILED, JobStatus.CANCELED, JobStatus.TIMED_OUT, JobStatus.INTERRUPTED}
_CANCELABLE_ACTIVE = {JobStatus.QUEUED, JobStatus.RUNNING}


def job_action_flags(
    status: JobStatus | str, *, cancelable: bool, retryable: bool
) -> tuple[bool, bool]:
    """Return the configured actions that remain valid in this Job state."""

    status = JobStatus(status)
    return (
        cancelable and status in _CANCELABLE_ACTIVE,
        retryable and status in _RETRYABLE_TERMINAL,
    )


def transition_job(
    current: JobStatus | str,
    target: JobStatus | str,
    *,
    retry: bool = False,
    actual_finished: bool = False,
    coordinator: bool = False,
) -> JobStatus:
    """Validate a Job transition and return the normalized target status."""

    current = JobStatus(current)
    target = JobStatus(target)
    if retry:
        if current not in _RETRYABLE_TERMINAL or target is not JobStatus.QUEUED:
            raise InvalidTransition(f"cannot retry {current} as {target}")
        return target
    if current is JobStatus.QUEUED and target in {JobStatus.RUNNING, JobStatus.CANCELED}:
        return target
    if current is JobStatus.RUNNING and (target in _TERMINAL or target is JobStatus.CANCEL_REQUESTED):
        return target
    if current is JobStatus.CANCEL_REQUESTED and target is JobStatus.CANCELED:
        return target
    if current is JobStatus.CANCEL_REQUESTED and target in _TERMINAL and target is not JobStatus.CANCELED:
        if not actual_finished:
            raise InvalidTransition("a completed result is required to leave CANCEL_REQUESTED")
        return target
    if current is JobStatus.INTERRUPTED and target is JobStatus.QUEUED and coordinator:
        return target
    raise InvalidTransition(f"invalid Job transition {current} -> {target}")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True)
class ConfirmationBinding:
    project_id: str
    actor_id: str
    resource_ref: Mapping[str, Any]
    action: str
    revision: int
    config_hash: str
    policy_version: str

    @property
    def binding_hash(self) -> str:
        payload = {
            "project_id": self.project_id,
            "actor_id": self.actor_id,
            "resource_ref": dict(self.resource_ref),
            "action": self.action,
            "revision": self.revision,
            "config_hash": self.config_hash,
            "policy_version": self.policy_version,
        }
        return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


@dataclass
class ConfirmationRecord:
    binding: ConfirmationBinding
    expires_at: datetime
    status: ConfirmationStatus = ConfirmationStatus.CONFIRMED
    consumed_result_ref: Mapping[str, Any] | None = None
    consumed_at: datetime | None = None
    _consumed_request_hash: str | None = field(default=None, repr=False)

    def validate_binding(self, candidate: ConfirmationBinding) -> None:
        if self.binding.binding_hash != candidate.binding_hash:
            raise InvalidTransition("confirmation binding hash does not match")

    def consume(
        self,
        candidate: ConfirmationBinding,
        *,
        result_ref: Mapping[str, Any],
        request_hash: str,
        now: datetime | None = None,
    ) -> None:
        self.validate_binding(candidate)
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        expiry = self.expires_at.astimezone(timezone.utc) if self.expires_at.tzinfo else self.expires_at.replace(tzinfo=timezone.utc)
        if self.status is ConfirmationStatus.CONSUMED:
            if self._consumed_request_hash != request_hash:
                raise InvalidTransition("confirmation was already consumed by another request")
            return
        if self.status is not ConfirmationStatus.CONFIRMED:
            raise InvalidTransition(f"confirmation is {self.status}")
        if now >= expiry:
            self.status = ConfirmationStatus.EXPIRED
            raise InvalidTransition("confirmation has expired")
        self.status = ConfirmationStatus.CONSUMED
        self.consumed_at = now
        self.consumed_result_ref = dict(result_ref)
        self._consumed_request_hash = request_hash

    def revoke(self) -> None:
        if self.status is not ConfirmationStatus.CONFIRMED:
            raise InvalidTransition(f"cannot revoke confirmation in state {self.status}")
        self.status = ConfirmationStatus.REVOKED
