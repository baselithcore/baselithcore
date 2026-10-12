"""
Core Domain Models.

Exposes the primary Pydantic and dataclass models used across the framework.
"""

from baselith.core.models.chat import (
    ChatRequest,
    ChatResponse,
    FeedbackDocumentReference,
    FeedbackRequest,
)
from baselith.core.models.domain import Document, SearchResult

__all__ = [
    "ChatRequest",
    "ChatResponse",
    "Document",
    "FeedbackDocumentReference",
    "FeedbackRequest",
    "SearchResult",
]
