"""
Generic data models for storage.

Defines Pydantic models for interactions and feedback,
decoupling the domain from specific database implementations.
"""

from datetime import datetime, timezone
from typing import Optional, Dict, Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class Interaction(BaseModel):
    """
    Represents a generic interaction event (e.g., User-Agent exchange).
    Maps to 'interactions' table.
    """

    id: UUID = Field(default_factory=uuid4)
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    input_transcription: Optional[str] = None
    output_transcription: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Feedback(BaseModel):
    """
    Represents feedback on an interaction.
    Maps to 'feedback' table.
    """

    id: UUID = Field(default_factory=uuid4)
    interaction_id: UUID
    score: Optional[float] = None  # Normalized score (e.g. 0.0-1.0 or -1/1)
    label: Optional[str] = None  # e.g. 'positive', 'negative', '5_star'
    comment: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
