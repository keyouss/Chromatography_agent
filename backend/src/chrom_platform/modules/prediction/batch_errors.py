"""Shared batch service and row preparation error type."""

from __future__ import annotations

from typing import Any


class BatchServiceError(Exception):
    def __init__(self, status_code: int, code: str, message: str, *, retryable: bool = False, details: dict[str, Any] | None = None) -> None:
        self.status_code, self.code, self.message = status_code, code, message
        self.retryable, self.details = retryable, details or {}
        super().__init__(message)
