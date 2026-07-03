"""
Chat service package.

Provides the main ChatService for handling conversational AI interactions.
"""

from core.services.chat.exceptions import (
    ChatServiceError,
    ContextBuildError,
    DependencyError,
    HistoryError,
    StreamingError,
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
    "ChatServiceError",
    "ContextBuildError",
    "DependencyError",
    "HistoryError",
    "StreamingError",
    "get_chat_service",
]
