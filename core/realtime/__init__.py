"""
Realtime Module.

Provides real-time event broadcasting and pub/sub capabilities
using Redis Pub/Sub for cross-process communication, plus the
transport-agnostic duplex voice session contract.
"""

from core.realtime.duplex import (
    AudioDelta,
    DuplexEvent,
    DuplexVoiceSession,
    ResponseDone,
    ResponseStarted,
    SessionError,
    SpeechStarted,
    SpeechStopped,
    TranscriptDelta,
)
from core.realtime.events import EventType, RealtimeEvent
from core.realtime.pubsub import PubSubManager
from core.realtime.subscriptions import (
    DEFAULT_IDLE_TIMEOUT,
    close_pubsub,
    iter_messages,
)

__all__ = [
    "DEFAULT_IDLE_TIMEOUT",
    "AudioDelta",
    "DuplexEvent",
    "DuplexVoiceSession",
    "EventType",
    "PubSubManager",
    "RealtimeEvent",
    "ResponseDone",
    "ResponseStarted",
    "SessionError",
    "SpeechStarted",
    "SpeechStopped",
    "TranscriptDelta",
    "close_pubsub",
    "iter_messages",
]
