"""
Core Domain Models.

Exposes the primary Pydantic and dataclass models used across the framework.
"""

from core.models.chat import (
    ChatRequest,
    ChatResponse,
    FeedbackDocumentReference,
    FeedbackRequest,
)
from core.models.domain import Document, SearchResult

__all__ = [
    "ChatRequest",
    "ChatResponse",
    "FeedbackDocumentReference",
    "FeedbackRequest",
    "Document",
    "SearchResult",
]
