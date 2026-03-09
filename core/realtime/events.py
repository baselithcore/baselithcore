"""
Real-time Events Module.

Defines the core data models and enumerations used for real-time
system event broadcasting and Server-Sent Events (SSE).
"""

from pydantic import BaseModel, Field
from typing import Optional, Any
from enum import Enum


class EventType(str, Enum):
    """Enumeration of standard real-time event types."""

    JOB_STARTED = "job_started"
    JOB_PROGRESS = "job_progress"
    JOB_COMPLETED = "job_completed"
    JOB_FAILED = "job_failed"
    GENERIC_MESSAGE = "message"


class RealtimeEvent(BaseModel):
    """
    Standardized payload format for real-time events.

    Attributes:
        type: The category/type of the event.
        job_id: Optional tracking ID for the associated job.
        payload: Detailed JSON-serializable data payload.
        channel: The distribution channel (defaults to 'global').
    """

    type: EventType
    job_id: Optional[str] = None
    payload: dict[str, Any] = Field(default_factory=dict)
    channel: str = "global"

    def to_sse_dict(self) -> dict[str, Any]:
        """Convert to format expected by sse-starlette."""
        return {"event": self.type.value, "data": self.model_dump_json()}
