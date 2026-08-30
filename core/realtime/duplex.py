"""Transport-agnostic duplex voice session contract.

Defines the event vocabulary and the :class:`DuplexVoiceSession` protocol for
full-duplex, low-latency voice conversations (user audio streaming up while
assistant audio streams down, with barge-in support). The contract is
deliberately transport- and provider-agnostic: WebSocket adapters, WebRTC
bridges or in-process fakes all satisfy the same protocol, and consumers
(playback loops, telephony bridges, tests) depend only on this module.

Providers implement ``events()`` as a coroutine returning an async iterator of
:data:`DuplexEvent` values; consumers therefore drive a session with::

    async for event in await session.events():
        ...

Domain-specific adapters (OpenAI Realtime, audio device plumbing) live under
``plugins/`` per the Sacred Core rule — only this protocol belongs in core.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class AudioDelta:
    """A chunk of assistant audio (raw PCM bytes) to be played back.

    Attributes:
        data: Raw audio payload (PCM16 unless the adapter documents otherwise).
    """

    data: bytes


@dataclass(frozen=True, slots=True)
class TranscriptDelta:
    """Incremental transcript text for either side of the conversation.

    Attributes:
        text: The transcript fragment.
        role: Which speaker the fragment belongs to.
    """

    text: str
    role: Literal["user", "assistant"]


@dataclass(frozen=True, slots=True)
class SpeechStarted:
    """The user started speaking (voice-activity detection fired)."""


@dataclass(frozen=True, slots=True)
class SpeechStopped:
    """The user stopped speaking (end of utterance detected)."""


@dataclass(frozen=True, slots=True)
class ResponseStarted:
    """The assistant began generating a response."""


@dataclass(frozen=True, slots=True)
class ResponseDone:
    """The assistant finished (or aborted) the current response."""


@dataclass(frozen=True, slots=True)
class SessionError:
    """A session-level error surfaced by the transport or provider.

    Attributes:
        message: Human-readable error description.
    """

    message: str


DuplexEvent = (
    AudioDelta
    | TranscriptDelta
    | SpeechStarted
    | SpeechStopped
    | ResponseStarted
    | ResponseDone
    | SessionError
)
"""Union of every event a duplex voice session may emit."""


@runtime_checkable
class DuplexVoiceSession(Protocol):
    """Full-duplex voice session over an arbitrary transport.

    Implementations own the underlying connection (WebSocket, WebRTC, an
    in-memory fake) and translate provider messages into
    :data:`DuplexEvent` values.
    """

    async def events(self) -> AsyncIterator[DuplexEvent]:
        """Return the inbound event stream for this session.

        Returns:
            An async iterator yielding :data:`DuplexEvent` values until the
            session ends. Connection failures must surface as a
            :class:`SessionError` event followed by the end of the stream.
        """
        ...

    async def send_audio(self, pcm: bytes) -> None:
        """Stream a chunk of user microphone audio to the provider.

        Args:
            pcm: Raw PCM16 audio bytes.
        """
        ...

    async def cancel_response(self) -> None:
        """Abort the in-flight assistant response (barge-in)."""
        ...

    async def close(self) -> None:
        """Tear down the transport and release resources."""
        ...

    @property
    def closed(self) -> bool:
        """Whether the session has been closed or has terminally failed."""
        ...


__all__ = [
    "AudioDelta",
    "DuplexEvent",
    "DuplexVoiceSession",
    "ResponseDone",
    "ResponseStarted",
    "SessionError",
    "SpeechStarted",
    "SpeechStopped",
    "TranscriptDelta",
]
