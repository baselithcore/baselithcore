"""
Event Schema Validation and Dead Letter Queue.

Provides optional validation of event payloads using Pydantic schemas
and a dead-letter queue for failed event handlers.
"""

from __future__ import annotations

from core.observability.logging import get_logger
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Type, TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic import BaseModel

logger = get_logger(__name__)


@dataclass
class DeadLetterEntry:
    """Entry in the dead letter queue."""

    event_name: str
    data: Dict[str, Any]
    error: str
    handler_name: str
    timestamp: float = field(default_factory=time.time)
    retry_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert entry to a dictionary for analysis."""
        return {
            "event_name": self.event_name,
            "data": self.data,
            "error": self.error,
            "handler_name": self.handler_name,
            "timestamp": self.timestamp,
            "retry_count": self.retry_count,
        }


class DeadLetterQueue:
    """
    Dead letter queue for failed event handlers.

    Stores events that failed to be processed for later analysis or retry.

    Usage:
        dlq = DeadLetterQueue(max_size=1000)

        # Failed event gets added
        dlq.add("user.created", {"id": 123}, "Connection timeout", "email_handler")

        # Process failed events
        for entry in dlq.get_all():
            print(f"Failed: {entry.event_name} - {entry.error}")

        # Retry mechanism
        await dlq.retry_all(event_bus)
    """

    def __init__(self, max_size: int = 1000) -> None:
        self._queue: deque[DeadLetterEntry] = deque(maxlen=max_size)
        self._max_size = max_size

    def add(
        self,
        event_name: str,
        data: Dict[str, Any],
        error: str,
        handler_name: str,
    ) -> None:
        """Add a failed event to the queue."""
        entry = DeadLetterEntry(
            event_name=event_name,
            data=data,
            error=str(error),
            handler_name=handler_name,
        )
        self._queue.append(entry)
        logger.warning(
            f"Event added to DLQ: {event_name} (handler={handler_name}, error={error})"
        )

    def get_all(self) -> List[DeadLetterEntry]:
        """Get all entries in the queue."""
        return list(self._queue)

    def get_by_event(self, event_name: str) -> List[DeadLetterEntry]:
        """Get entries for a specific event type."""
        return [e for e in self._queue if e.event_name == event_name]

    def clear(self) -> None:
        """Clear all entries."""
        self._queue.clear()

    def pop(self) -> Optional[DeadLetterEntry]:
        """Pop the oldest entry."""
        if self._queue:
            return self._queue.popleft()
        return None

    @property
    def size(self) -> int:
        """Current queue size."""
        return len(self._queue)

    def stats(self) -> Dict[str, Any]:
        """Get queue statistics."""
        by_event: Dict[str, int] = {}
        by_handler: Dict[str, int] = {}
        for entry in self._queue:
            by_event[entry.event_name] = by_event.get(entry.event_name, 0) + 1
            by_handler[entry.handler_name] = by_handler.get(entry.handler_name, 0) + 1
        return {
            "total": len(self._queue),
            "max_size": self._max_size,
            "by_event": by_event,
            "by_handler": by_handler,
        }


class EventSchemaRegistry:
    """
    Registry for event payload schemas.

    Allows registering Pydantic models for event validation.

    Usage:
        from pydantic import BaseModel

        class UserCreatedEvent(BaseModel):
            user_id: str
            email: str

        registry = EventSchemaRegistry()
        registry.register("user.created", UserCreatedEvent)

        # Validation happens automatically when emitting
        is_valid, error = registry.validate("user.created", {"user_id": "123"})
        # Returns (False, "email: field required")
    """

    def __init__(self) -> None:
        self._schemas: Dict[str, Type["BaseModel"]] = {}

    def register(self, event_name: str, schema: Type["BaseModel"]) -> None:
        """
        Register a Pydantic schema for an event.

        Args:
            event_name: Event name to validate
            schema: Pydantic model class
        """
        self._schemas[event_name] = schema
        logger.debug(f"Registered schema for event: {event_name}")

    def unregister(self, event_name: str) -> None:
        """Remove schema registration."""
        self._schemas.pop(event_name, None)

    def has_schema(self, event_name: str) -> bool:
        """Check if event has a registered schema."""
        return event_name in self._schemas

    def validate(
        self, event_name: str, data: Dict[str, Any]
    ) -> tuple[bool, Optional[str]]:
        """
        Validate event data against registered schema.

        Args:
            event_name: Event to validate
            data: Event payload

        Returns:
            Tuple of (is_valid, error_message)
        """
        if event_name not in self._schemas:
            return True, None  # No schema = no validation

        schema = self._schemas[event_name]
        try:
            schema.model_validate(data)
            return True, None
        except Exception as e:
            error_msg = str(e)
            logger.warning(f"Event validation failed for '{event_name}': {error_msg}")
            return False, error_msg

    def get_schema(self, event_name: str) -> Optional[Type["BaseModel"]]:
        """Get registered schema for an event."""
        return self._schemas.get(event_name)

    @property
    def registered_events(self) -> List[str]:
        """List all events with registered schemas."""
        return list(self._schemas.keys())


# Global instances
_global_dlq: Optional[DeadLetterQueue] = None
_global_schema_registry: Optional[EventSchemaRegistry] = None


def get_dead_letter_queue() -> DeadLetterQueue:
    """Get the global dead letter queue."""
    global _global_dlq
    if _global_dlq is None:
        _global_dlq = DeadLetterQueue()
    return _global_dlq


def get_schema_registry() -> EventSchemaRegistry:
    """Get the global event schema registry."""
    global _global_schema_registry
    if _global_schema_registry is None:
        _global_schema_registry = EventSchemaRegistry()
    return _global_schema_registry


__all__ = [
    "DeadLetterEntry",
    "DeadLetterQueue",
    "EventSchemaRegistry",
    "get_dead_letter_queue",
    "get_schema_registry",
]
