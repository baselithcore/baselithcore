"""
Database Access Layer.

Exposes database initialization and common access patterns for feedback and documents.
"""

from __future__ import annotations

from .connection import close_pool
from .documents import get_document_feedback_summary
from .feedback import get_feedback_analytics, get_feedbacks, insert_feedback
from .schema import init_db

__all__ = [
    "close_pool",
    "get_document_feedback_summary",
    "get_feedback_analytics",
    "get_feedbacks",
    "init_db",
    "insert_feedback",
]
