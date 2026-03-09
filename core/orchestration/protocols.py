"""
Orchestration Protocols Module

Defines abstract interfaces for the orchestration framework, enabling
loose coupling between components and easier testing via dependency injection.
"""

from __future__ import annotations
from abc import abstractmethod
from typing import (
    Any,
    Dict,
    AsyncGenerator,
    Optional,
    Protocol,
    runtime_checkable,
)


from core.lifecycle import AgentLifecycle


@runtime_checkable
class AgentProtocol(AgentLifecycle, Protocol):
    """Protocol for agents that can be orchestrated."""

    @abstractmethod
    async def execute(
        self, input: Any, context: Optional[Dict[str, Any]] = None
    ) -> Any:
        """
        Execute the agent's primary task logic.

        Args:
            input: The data or prompt to process.
            context: Optional execution context, metadata, or state.

        Returns:
            Any: The result of the agent's processing.
        """
        ...


@runtime_checkable
class FlowHandler(Protocol):
    """Protocol for flow handlers that process specific intents."""

    @abstractmethod
    async def handle(
        self,
        query: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Process a specific user intent and generate a response.

        Args:
            query: The user input text.
            context: Enriched context including memory and metadata.

        Returns:
            Dict[str, Any]: Structured processing result.
        """
        ...


@runtime_checkable
class StreamHandler(Protocol):
    """Protocol for streaming handlers that yield progressive results."""

    @abstractmethod
    def handle(
        self,
        query: str,
        context: Dict[str, Any],
    ) -> AsyncGenerator[str, None]:
        """
        Handle a streaming flow.

        Args:
            query: User query/input
            context: Execution context

        Yields:
            Progressive tokens/chunks
        """
        ...


@runtime_checkable
class IntentClassifierProtocol(Protocol):
    """Protocol for intent classification."""

    @abstractmethod
    async def classify(self, text: str) -> str:
        """
        Classify user intent from input text.

        Args:
            text: User input text

        Returns:
            Intent name (string identifier)
        """
        ...


@runtime_checkable
class OrchestratorProtocol(Protocol):
    """Protocol for the main orchestrator."""

    @abstractmethod
    async def process(
        self,
        query: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Process a user query through the orchestration pipeline.

        Args:
            query: User query
            context: Optional context (conversation history, metadata)

        Returns:
            Processing result
        """
        ...

    @abstractmethod
    def process_stream(
        self,
        query: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[str, None]:
        """
        Process a user query with streaming response.

        Args:
            query: User query
            context: Optional context

        Yields:
            Response tokens/chunks
        """
        ...
