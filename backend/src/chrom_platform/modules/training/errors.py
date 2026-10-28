from __future__ import annotations


class TrainingError(Exception):
    status_code = 400
    code = "TRAINING_ERROR"

    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        self.message = message
        self.details = details or {}
        super().__init__(message)


class TrainingValidationError(TrainingError):
    status_code = 422
    code = "TRAINING_VALIDATION_FAILED"


class TrainingConflictError(TrainingError):
    status_code = 409
    code = "TRAINING_CONFLICT"


class TrainingPreconditionError(TrainingError):
    status_code = 412
    code = "VERSION_CONFLICT"


class TrainingNotFoundError(TrainingError):
    status_code = 404
    code = "RESOURCE_NOT_FOUND"


class TrainingUnavailableError(TrainingError):
    status_code = 503
    code = "TRAINING_CAPABILITY_UNAVAILABLE"


class ConfirmationRequiredError(TrainingError):
    status_code = 409
    code = "CONFIRMATION_REQUIRED"


__all__ = [
    "ConfirmationRequiredError",
    "TrainingConflictError",
    "TrainingError",
    "TrainingNotFoundError",
    "TrainingPreconditionError",
    "TrainingUnavailableError",
    "TrainingValidationError",
]
