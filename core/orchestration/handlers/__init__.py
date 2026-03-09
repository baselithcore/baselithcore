"""
Base Flow Handlers Module

Provides base classes for flow handlers that process specific intents.
These handlers can be extended for domain-specific logic.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Dict, Generator, Optional
from core.observability.logging import get_logger

from core.orchestration.protocols import FlowHandler, StreamHandler


__all__ = [
    "BaseFlowHandler",
    "BaseStreamHandler",
    "FlowHandler",
    "StreamHandler",
    "ReasoningHandler",
    "VisionHandler",
    "MultiModalReasoningHandler",
    "SwarmHandler",
]


logger = get_logger(__name__)


class BaseFlowHandler(ABC):
    """
    Abstract base class for flow handlers.

    Provides common functionality and defines the interface for
    handling specific intents in the orchestration pipeline.
    """

    def __init__(
        self,
        agents: Optional[Dict[str, Any]] = None,
        services: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Initialize base flow handler.

        Args:
            agents: Dictionary of available agents (by name)
            services: Dictionary of available services (by name)
        """
        self.agents = agents or {}
        self.services = services or {}

    @abstractmethod
    async def handle(
        self,
        query: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Handle a flow for a specific intent.

        Args:
            query: User query/input
            context: Execution context containing:
                - history: Conversation history
                - metadata: Additional metadata
                - user: User information (if available)

        Returns:
            Handler result dictionary with at least:
                - response: Generated response text
                - sources: Optional list of sources used
        """
        ...

    def get_agent(self, name: str) -> Optional[Any]:
        """Get an agent by name."""
        return self.agents.get(name)

    def get_service(self, name: str) -> Optional[Any]:
        """Get a service by name."""
        return self.services.get(name)


class BaseStreamHandler(ABC):
    """
    Abstract base class for streaming flow handlers.

    Provides common functionality for handlers that yield progressive
    results (like token-by-token LLM responses).
    """

    def __init__(
        self,
        agents: Optional[Dict[str, Any]] = None,
        services: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Initialize base stream handler.

        Args:
            agents: Dictionary of available agents (by name)
            services: Dictionary of available services (by name)
        """
        self.agents = agents or {}
        self.services = services or {}

    @abstractmethod
    def handle(
        self,
        query: str,
        context: Dict[str, Any],
    ) -> Generator[str, None, Dict[str, Any]]:
        """
        Handle a streaming flow for a specific intent.

        Args:
            query: User query/input
            context: Execution context

        Yields:
            Progressive tokens/chunks (strings)

        Returns:
            Final result dictionary (via StopIteration.value)
        """
        ...

    def get_agent(self, name: str) -> Optional[Any]:
        """Get an agent by name."""
        return self.agents.get(name)

    def get_service(self, name: str) -> Optional[Any]:
        """Get a service by name."""
        return self.services.get(name)

    @staticmethod
    def yield_status(status: str) -> str:
        """
        Create a status update for streaming.

        Args:
            status: Status message

        Returns:
            Formatted status string (can be parsed by clients)
        """
        return f"[STATUS] {status}"

    @staticmethod
    def yield_progress(current: int, total: int, message: str = "") -> str:
        """
        Create a progress update for streaming.

        Args:
            current: Current step number
            total: Total number of steps
            message: Optional progress message

        Returns:
            Formatted progress string
        """
        percentage = int((current / total) * 100) if total > 0 else 0
        return (
            f"[PROGRESS] {percentage}% - {message}"
            if message
            else f"[PROGRESS] {percentage}%"
        )


from .reasoning import ReasoningHandler  # noqa: E402
from .vision import VisionHandler  # noqa: E402
from .multimodal_reasoning import MultiModalReasoningHandler  # noqa: E402
from .swarm_handler import SwarmHandler  # noqa: E402
