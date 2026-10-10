"""
Event Types and Type Aliases.

Core data structures for the event system.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any, Union


@dataclass
class Event:
    """Represents an event in the system."""

    name: str
    data: dict[str, Any]
    timestamp: float = field(default_factory=time.time)
    source: str | None = None
    correlation_id: str | None = None


@dataclass
class EventStats:
    """Statistics for event bus operations."""

    events_published: int = 0
    events_handled: int = 0
    handlers_registered: int = 0
    errors: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert statistics to a dictionary for reporting."""
        return {
            "events_published": self.events_published,
            "events_handled": self.events_handled,
            "handlers_registered": self.handlers_registered,
            "errors": self.errors,
        }


# Type aliases
SyncHandler = Callable[[dict[str, Any]], None]
AsyncHandler = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]
Handler = Union[SyncHandler, AsyncHandler]


__all__ = [
    "AsyncHandler",
    "Event",
    "EventStats",
    "Handler",
    "SyncHandler",
]
