"""
Chat History Manager - Backward Compatibility Re-export.

DEPRECATED: Import from core.services.chat.utils.history instead.
"""

from core.services.chat.utils.history import (
    SUMMARY_DIVIDER,
    SUMMARY_HEADER,
    CacheProtocol,
    ChatHistoryManager,
    HistorySummary,
    HistoryTurns,
)

# TTLCache adapter for backward compatibility
# (app.cache.TTLCache implements CacheProtocol)

__all__ = [
    "SUMMARY_DIVIDER",
    "SUMMARY_HEADER",
    "CacheProtocol",
    "ChatHistoryManager",
    "HistorySummary",
    "HistoryTurns",
]
