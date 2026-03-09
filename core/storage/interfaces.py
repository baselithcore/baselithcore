"""
Storage interfaces.

Defines the contract for storage repositories.
"""

from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any
from uuid import UUID

from core.storage.models import Interaction, Feedback


class InteractionRepository(ABC):
    """Abstract repository for interactions."""

    @abstractmethod
    async def store_interaction(self, interaction: Interaction) -> Interaction:
        """Store a new interaction."""
        pass

    @abstractmethod
    async def get_interaction(self, interaction_id: UUID) -> Optional[Interaction]:
        """Get an interaction by ID."""
        pass

    @abstractmethod
    async def get_interactions_by_session(
        self, session_id: str, limit: int = 100, offset: int = 0
    ) -> List[Interaction]:
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
    ) -> List[Feedback]:
        """Get feedback for a specific interaction."""
        pass

    @abstractmethod
    async def get_feedback_summary(
        self, agent_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get aggregate feedback statistics."""
        pass
