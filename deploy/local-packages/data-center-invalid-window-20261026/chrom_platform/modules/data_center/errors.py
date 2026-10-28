"""Safe domain errors for the data-center application adapter."""

from __future__ import annotations


class DataCenterError(Exception):
    """A domain failure with a stable machine-readable code."""

    status_code = 400
    code = "DATA_CENTER_ERROR"

    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        self.message = message
        self.details = details or {}
        super().__init__(message)


class DataValidationError(DataCenterError):
    status_code = 422
    code = "DATA_VALIDATION_FAILED"


class DataConflictError(DataCenterError):
    status_code = 409
    code = "DATA_CONFLICT"


class DataNotFoundError(DataCenterError):
    status_code = 404
    code = "RESOURCE_NOT_FOUND"


class DataUnavailableError(DataCenterError):
    status_code = 503
    code = "DATA_CAPABILITY_UNAVAILABLE"


class ConfirmationRequiredError(DataCenterError):
    status_code = 409
    code = "CONFIRMATION_REQUIRED"


__all__ = [
    "ConfirmationRequiredError",
    "DataCenterError",
    "DataConflictError",
    "DataNotFoundError",
    "DataUnavailableError",
    "DataValidationError",
]
