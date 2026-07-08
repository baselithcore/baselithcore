"""
Database Access Layer.

Exposes database initialization and common access patterns for feedback and documents.
"""

from __future__ import annotations

from .connection import (
    close_pool,
    get_async_read_connection,
    get_pool_stats,
    get_read_connection,
)
from .documents import get_document_feedback_summary
from .feedback import get_feedback_analytics, get_feedbacks, insert_feedback
from .schema import init_db

__all__ = [
    "close_pool",
    "get_async_read_connection",
    "get_document_feedback_summary",
    "get_pool_stats",
    "get_feedback_analytics",
    "get_feedbacks",
    "get_read_connection",
    "init_db",
    "insert_feedback",
]
