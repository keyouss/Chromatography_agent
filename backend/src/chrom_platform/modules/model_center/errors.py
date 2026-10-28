from __future__ import annotations


class ModelCenterError(Exception):
    status_code = 400
    code = "MODEL_CENTER_ERROR"

    def __init__(self, message: str, *, details: dict[str, object] | None = None):
        self.message, self.details = message, details or {}
        super().__init__(message)


class ModelCenterValidationError(ModelCenterError):
    status_code, code = 422, "MODEL_CENTER_VALIDATION_FAILED"


class ModelCenterConflictError(ModelCenterError):
    status_code, code = 409, "MODEL_CENTER_CONFLICT"


class ModelCenterPreconditionError(ModelCenterError):
    status_code, code = 412, "PRECONDITION_FAILED"


class ModelCenterNotFoundError(ModelCenterError):
    status_code, code = 404, "RESOURCE_NOT_FOUND"


class ModelCenterUnavailableError(ModelCenterError):
    status_code, code = 503, "MODEL_CENTER_CAPABILITY_UNAVAILABLE"


class ModelCenterPermissionError(ModelCenterError):
    status_code, code = 403, "PERMISSION_DENIED"


class ModelCenterConfirmationError(ModelCenterError):
    status_code, code = 409, "CONFIRMATION_REQUIRED"


__all__ = [
    "ModelCenterError", "ModelCenterValidationError", "ModelCenterConflictError", "ModelCenterPreconditionError",
    "ModelCenterNotFoundError", "ModelCenterUnavailableError",
    "ModelCenterPermissionError", "ModelCenterConfirmationError",
]
