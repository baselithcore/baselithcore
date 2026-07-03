"""
Storage interfaces.

Defines the contract for storage repositories.
"""

from abc import ABC, abstractmethod
from typing import Any
from uuid import UUID

from core.storage.models import Feedback, Interaction


class InteractionRepository(ABC):
    """Abstract repository for interactions."""

    @abstractmethod
    async def store_interaction(self, interaction: Interaction) -> Interaction:
        """Store a new interaction."""
        pass

    @abstractmethod
    async def get_interaction(self, interaction_id: UUID) -> Interaction | None:
        """Get an interaction by ID."""
        pass

    @abstractmethod
    async def get_interactions_by_session(
        self, session_id: str, limit: int = 100, offset: int = 0
    ) -> list[Interaction]:
        """Get interactions for a session."""
        pass


class FeedbackRepository(ABC):
    """Abstract repository for feedback."""

    @abstractmethod
    async def store_feedback(self, feedback: Feedback) -> Feedback:
        """Store new feedback."""
        pass

    @abstractmethod
    async def get_feedback_for_interaction(
        self, interaction_id: UUID
    ) -> list[Feedback]:
        """Get feedback for a specific interaction."""
        pass

    @abstractmethod
    async def get_feedback_summary(self, agent_id: str | None = None) -> dict[str, Any]:
        """Get aggregate feedback statistics."""
        pass
