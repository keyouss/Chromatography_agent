from .state import (
    ConfirmationBinding,
    ConfirmationRecord,
    ConfirmationStatus,
    InvalidTransition,
    JobStatus,
    OutboxStatus,
    fencing_token_matches,
    idempotency_scope,
    transition_outbox,
    transition_job,
)

# Models live in infrastructure and infrastructure models depend on the pure
# state module.  Eagerly importing both here creates a package-init cycle
# (infrastructure.models -> task_core.state -> task_core.__init__ -> models).
# Keep the task boundary's convenient imports through a lazy attribute hook.
_MODEL_EXPORTS = {
    "ExecutionLedger",
    "IdempotencyRecord",
    "Job",
    "JobAttempt",
    "JobEvent",
    "OutboxEvent",
}

_SERVICE_EXPORTS = {
    "ConfirmedJobSpec",
    "ConfirmationService",
    "JobService",
    "OperationContext",
    "ServiceResult",
    "TargetSnapshot",
    "TaskError",
    "TaskPolicy",
    "EventPageResult",
}


def __getattr__(name: str):
    if name in _MODEL_EXPORTS:
        from . import models

        return getattr(models, name)
    if name in _SERVICE_EXPORTS:
        from . import service

        return getattr(service, name)
    raise AttributeError(name)

__all__ = [
    "ConfirmationBinding",
    "ConfirmationRecord",
    "ConfirmationStatus",
    "ExecutionLedger",
    "IdempotencyRecord",
    "InvalidTransition",
    "Job",
    "JobAttempt",
    "JobEvent",
    "JobStatus",
    "OutboxStatus",
    "OutboxEvent",
    "fencing_token_matches",
    "idempotency_scope",
    "transition_outbox",
    "transition_job",
    "ConfirmedJobSpec",
    "ConfirmationService",
    "JobService",
    "OperationContext",
    "ServiceResult",
    "TargetSnapshot",
    "TaskError",
    "TaskPolicy",
    "EventPageResult",
]
