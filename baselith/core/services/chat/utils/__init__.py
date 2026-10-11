"""
Chat service utilities.

Provides utility functions for history, streaming, and context building.
"""

from baselith.core.services.chat.utils.history import ChatHistoryManager
from baselith.core.services.chat.utils.streaming import (
    build_cached_stream,
    build_fallback_stream,
    stream_answer,
)

__all__ = [
    "ChatHistoryManager",
    "build_cached_stream",
    "build_fallback_stream",
    "stream_answer",
]
