"""
Chat service exceptions.

Custom exception types for the Chat service layer.
"""


class ChatServiceError(Exception):
    """
    Base exception for all Chat service related errors.

    Used as a catch-all for high-level chat operation failures.
    """

    pass


class HistoryError(ChatServiceError):
    """
    Exception raised during conversation history operations.

    Includes failures in loading, saving, or summarizing chat history.
    """

    pass


class StreamingError(ChatServiceError):
    """
    Exception raised during response streaming.

    Captures issues with generator exhaustion or stream connection drops.
    """

    pass


class ContextBuildError(ChatServiceError):
    """
    Exception raised when building RAG context fails.

    Occurs if retrieved documents cannot be formatted or merged properly.
    """

    pass


class DependencyError(ChatServiceError):
    """
    Exception raised when a required service dependency is missing.

    Typically occurs during bootstrap if a provider or tool is not configured.
    """

    pass
