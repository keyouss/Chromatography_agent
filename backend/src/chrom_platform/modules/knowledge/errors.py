"""Stable domain errors for the knowledge module."""

from __future__ import annotations


class KnowledgeError(Exception):
    status_code = 400
    code = "KNOWLEDGE_ERROR"

    def __init__(self, message: str, *, details: dict[str, object] | None = None, retryable: bool = False) -> None:
        self.message = message
        self.details = details or {}
        self.retryable = retryable
        super().__init__(message)


class KnowledgeValidationError(KnowledgeError):
    status_code = 422
    code = "KNOWLEDGE_VALIDATION_FAILED"


class KnowledgeConflictError(KnowledgeError):
    status_code = 409
    code = "KNOWLEDGE_CONFLICT"


class KnowledgePreconditionError(KnowledgeError):
    status_code = 412
    code = "VERSION_CONFLICT"


class KnowledgeNotFoundError(KnowledgeError):
    status_code = 404
    code = "RESOURCE_NOT_FOUND"


class KnowledgeUnavailableError(KnowledgeError):
    status_code = 503
    code = "KNOWLEDGE_DEPENDENCY_UNAVAILABLE"

    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(message, details=details, retryable=True)


__all__ = [
    "KnowledgeConflictError",
    "KnowledgeError",
    "KnowledgeNotFoundError",
    "KnowledgePreconditionError",
    "KnowledgeUnavailableError",
    "KnowledgeValidationError",
]
