from __future__ import annotations

from typing import NoReturn

from .dto import ErrorDetail, ErrorEnvelope


class APIError(Exception):
    def __init__(self, status_code: int, code: str, message: str, *, retryable: bool = False, details: dict[str, object] | None = None, field_errors: list[dict[str, str]] | None = None) -> None:
        self.status_code = status_code
        self.payload = ErrorDetail(code=code, message=message, retryable=retryable, details=details or {}, field_errors=field_errors or [])
        super().__init__(message)


class InputValidationError(ValueError):
    """Explicit signal that trusted validation rejected caller input.

    Dependency and programming errors must keep their native exception types
    so API boundaries do not misreport them as client mistakes.
    """


def fail(status_code: int, code: str, message: str, **kwargs: object) -> NoReturn:
    raise APIError(status_code, code, message, **kwargs)


__all__ = ["APIError", "ErrorEnvelope", "InputValidationError", "fail"]
