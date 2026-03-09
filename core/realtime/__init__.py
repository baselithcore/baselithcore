"""
Realtime Module.

Provides real-time event broadcasting and pub/sub capabilities
using Redis Pub/Sub for cross-process communication.
"""

from core.realtime.events import RealtimeEvent, EventType
from core.realtime.pubsub import PubSubManager

__all__ = [
    "RealtimeEvent",
    "EventType",
    "PubSubManager",
]
