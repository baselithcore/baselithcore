"""
Chat service package.

Provides the main ChatService for handling conversational AI interactions.
"""

from core.services.chat.exceptions import (
    ChatServiceError,
    HistoryError,
    StreamingError,
    ContextBuildError,
    DependencyError,
)
from core.services.chat.service import ChatService, ChatServiceConfig

# Singleton instance (lazy initialization)
_chat_service_instance: ChatService | None = None


def get_chat_service() -> ChatService:
    """
    Get the singleton ChatService instance.

    Returns:
        The global ChatService instance.
    """
    global _chat_service_instance
    if _chat_service_instance is None:
        _chat_service_instance = ChatService()
    return _chat_service_instance


__all__ = [
    "ChatService",
    "ChatServiceConfig",
    "get_chat_service",
    "ChatServiceError",
    "HistoryError",
    "StreamingError",
    "ContextBuildError",
    "DependencyError",
]
