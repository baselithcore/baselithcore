"""
API Event Models.

Defines the standard event protocol for Agentic UI communication.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class EventType(str, Enum):
    """Types of events in the standard agent stream."""

    THOUGHT = "thought"  # Reasoning step (Chain of Thought)
    TOOL_CALL = "tool_call"  # System calling a tool
    TOOL_RESULT = "tool_result"  # Result of a tool call
    MEMORY = "memory"  # Memory update (stored/recalled)
    HUMAN_REQUEST = "human"  # Request for human intervention
    RESPONSE_CHUNK = "chunk"  # Streaming text chunk
    RESPONSE_FINAL = "final"  # Final complete response
    ERROR = "error"  # System error


class AgentEvent(BaseModel):
    """
    Standard event for the Agentic UI Protocol.

    Compatible with SSE (Server-Sent Events) or WebSocket frames.
    """

    type: EventType
    content: Optional[str] = None
    data: Dict[str, Any] = Field(default_factory=dict)
    agent_id: str = "system"
    id: str = Field(default_factory=lambda: str(uuid4()))
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return self.model_dump()
