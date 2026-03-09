"""
Chat History Manager - Backward Compatibility Re-export.

DEPRECATED: Import from core.services.chat.utils.history instead.
"""

from core.services.chat.utils.history import (
    ChatHistoryManager,
    CacheProtocol,
    HistoryTurns,
    HistorySummary,
    SUMMARY_HEADER,
    SUMMARY_DIVIDER,
)

# TTLCache adapter for backward compatibility
# (app.cache.TTLCache implements CacheProtocol)

__all__ = [
    "ChatHistoryManager",
    "CacheProtocol",
    "HistoryTurns",
    "HistorySummary",
    "SUMMARY_HEADER",
    "SUMMARY_DIVIDER",
]
