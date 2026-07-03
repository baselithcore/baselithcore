"""
Generic data models for storage.

Defines Pydantic models for interactions and feedback,
decoupling the domain from specific database implementations.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class Interaction(BaseModel):
    """
    Represents a generic interaction event (e.g., User-Agent exchange).
    Maps to 'interactions' table.
    """

    id: UUID = Field(default_factory=uuid4)
    session_id: str | None = None
    user_id: str | None = None
    agent_id: str | None = None
    input_transcription: str | None = None
    output_transcription: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Feedback(BaseModel):
    """
    Represents feedback on an interaction.
    Maps to 'feedback' table.
    """

    id: UUID = Field(default_factory=uuid4)
    interaction_id: UUID
    score: float | None = None  # Normalized score (e.g. 0.0-1.0 or -1/1)
    label: str | None = None  # e.g. 'positive', 'negative', '5_star'
    comment: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
