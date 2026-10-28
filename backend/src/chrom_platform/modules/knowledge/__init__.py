"""Knowledge centre vertical slice.

The package is intentionally self contained.  Application assembly supplies
the artifact, parser, scanner, queue, authorization and audit ports; this
module never reaches through an old RAG implementation or accepts filesystem
paths from a caller.
"""

from .errors import (
    KnowledgeConflictError,
    KnowledgeError,
    KnowledgeNotFoundError,
    KnowledgeUnavailableError,
    KnowledgeValidationError,
)
from .service import KnowledgeService

__all__ = [
    "KnowledgeConflictError",
    "KnowledgeError",
    "KnowledgeNotFoundError",
    "KnowledgeService",
    "KnowledgeUnavailableError",
    "KnowledgeValidationError",
]
