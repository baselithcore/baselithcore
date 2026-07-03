"""
Workflow Protocols.

Abstract protocols for chat workflow components.
Implementations can be domain-specific (RAG, planning, etc.)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from core.chat.agent_state import AgentState


class ValidatorProtocol(Protocol):
    """Protocol for input validation."""

    def validate_input(self, state: AgentState) -> None:
        """Validate and preprocess input."""
        ...


class RetrieverProtocol(Protocol):
    """Protocol for document retrieval."""

    def retrieve(self, state: AgentState) -> None:
        """Retrieve relevant documents."""
        ...


class ResponseGeneratorProtocol(Protocol):
    """Protocol for response generation."""

    def generate_answer(self, state: AgentState) -> None:
        """Generate answer synchronously."""
        ...

    def generate_answer_stream(self, state: AgentState) -> Iterator[str]:
        """Generate answer with streaming."""
        ...

    def finalize_answer(self, state: AgentState) -> None:
        """Finalize the answer."""
        ...


class ClarifierProtocol(Protocol):
    """Protocol for clarification requests."""

    def request_clarification(
        self,
        state: AgentState,
        *,
        message_builder: Any | None = None,
    ) -> None:
        """Request clarification from user."""
        ...


class WorkflowStep(ABC):
    """Base class for workflow steps."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Step name for routing."""
        ...

    @abstractmethod
    def execute(self, state: AgentState) -> None:
        """Execute the step."""
        ...


__all__ = [
    "ClarifierProtocol",
    "ResponseGeneratorProtocol",
    "RetrieverProtocol",
    "ValidatorProtocol",
    "WorkflowStep",
]
