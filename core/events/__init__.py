"""
Internal Event Bus for Baselith-Core.

Provides pub/sub event-driven communication between components
without tight coupling. Supports both sync and async handlers.

Usage:
    from core.events import EventBus, get_event_bus

    bus = get_event_bus()

    # Subscribe to events
    @bus.on("agent.completed")
    async def handle_completion(data):
        print(f"Agent completed: {data}")

    # Publish events
    await bus.emit("agent.completed", {"agent_id": "123", "result": "success"})
"""

from core.events.bus import EventBus, get_event_bus, reset_event_bus
from core.events.names import EventNames
from core.events.types import (
    AsyncHandler,
    Event,
    EventStats,
    Handler,
    SyncHandler,
)
from core.events.validation import (
    DeadLetterEntry,
    DeadLetterQueue,
    EventSchemaRegistry,
    get_dead_letter_queue,
    get_schema_registry,
)

__all__ = [
    # Types
    "Event",
    "EventStats",
    "SyncHandler",
    "AsyncHandler",
    "Handler",
    # Bus
    "EventBus",
    "get_event_bus",
    "reset_event_bus",
    # Names
    "EventNames",
    # Validation & DLQ
    "DeadLetterEntry",
    "DeadLetterQueue",
    "EventSchemaRegistry",
    "get_dead_letter_queue",
    "get_schema_registry",
]


# Lazy import for listener to avoid circular imports
def get_event_listener():
    """Get or create the global event listener."""
    from core.events.listener import EventListener

    return EventListener.setup()
