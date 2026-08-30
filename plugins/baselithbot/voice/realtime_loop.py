"""Realtime duplex voice loop: playback, barge-in and latency accounting.

Consumes :class:`core.realtime.DuplexVoiceSession` events and drives an
:class:`AudioPlayer`. This is the realtime counterpart of the sequential
wake -> STT -> LLM -> TTS pipeline built from ``wake.py`` / ``tts.py``: instead
of turn-by-turn round trips, provider audio streams down while user audio
streams up, and the loop interrupts assistant playback the instant the user
starts talking (barge-in).

Composition example (the existing sequential voice surface stays untouched;
this loop is opt-in via ``BASELITHBOT_VOICE_REALTIME_ENABLED``, see
``openai_realtime.build_realtime_loop``)::

    from plugins.baselithbot.voice.openai_realtime import build_realtime_loop
    from plugins.baselithbot.voice.realtime_loop import BufferedAudioPlayer

    player = BufferedAudioPlayer(sink=speaker.write)   # any async byte sink
    loop = build_realtime_loop(player)                 # None unless enabled
    if loop is not None:
        run_task = asyncio.create_task(loop.run())
        async for frame in microphone_frames():        # e.g. SoundDeviceAudioBackend
            await loop.session.send_audio(frame)

Out of scope (future work): a telephony bridge (SIP/Twilio media streams)
would sit in front of this loop as another ``DuplexVoiceSession`` transport;
it is deliberately not implemented here.
"""

from __future__ import annotations

import inspect
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Protocol, runtime_checkable

from core.observability.logging import get_logger
from core.realtime import (
    AudioDelta,
    DuplexVoiceSession,
    ResponseDone,
    SessionError,
    SpeechStarted,
    SpeechStopped,
    TranscriptDelta,
)

logger = get_logger(__name__)


@runtime_checkable
class AudioPlayer(Protocol):
    """Minimal playback surface the realtime loop drives."""

    async def play(self, chunk: bytes) -> None:
        """Queue one chunk of assistant audio for playback."""
        ...

    async def stop(self) -> None:
        """Halt playback immediately and drop any buffered audio."""
        ...

    @property
    def playing(self) -> bool:
        """Whether assistant audio is currently audible/buffered."""
        ...


class BufferedAudioPlayer:
    """:class:`AudioPlayer` that forwards chunks to an injected async sink.

    The sink is any ``async (bytes) -> None`` callable — an audio-device
    writer in production, a capture list in tests. ``playing`` turns on with
    the first chunk and off on :meth:`stop` (or when a :meth:`stream_play`
    iterator is exhausted); a plain :meth:`play` has no completion signal, so
    callers mark end-of-response themselves (the loop uses ``ResponseDone``).
    """

    def __init__(self, sink: Callable[[bytes], Awaitable[None]]) -> None:
        """Initialize the player.

        Args:
            sink: Async callable receiving each raw audio chunk.
        """
        self._sink = sink
        self._playing = False
        self._stop_requested = False

    async def play(self, chunk: bytes) -> None:
        """Forward ``chunk`` to the sink and mark playback active."""
        await self._sink(chunk)
        self._playing = True

    async def stop(self) -> None:
        """Stop playback and halt any in-flight :meth:`stream_play`."""
        self._stop_requested = True
        self._playing = False

    async def stream_play(self, chunks: AsyncIterator[bytes]) -> None:
        """Play a full async stream of chunks, honoring :meth:`stop`.

        Args:
            chunks: Async iterator of raw audio chunks.
        """
        self._stop_requested = False
        try:
            async for chunk in chunks:
                if self._stop_requested:
                    break
                await self.play(chunk)
        finally:
            self._playing = False

    @property
    def playing(self) -> bool:
        """Whether playback is currently active."""
        return self._playing


class RealtimeVoiceLoop:
    """Drives a duplex voice session: playback, barge-in, latency stats.

    Event handling:
        - ``AudioDelta`` -> play chunk; assistant-speaking state on.
        - ``SpeechStarted`` while assistant audio is playing -> barge-in:
          cancel the in-flight response and stop playback immediately.
        - ``SpeechStopped`` -> start the response-latency stopwatch.
        - ``ResponseDone`` -> assistant-speaking state off; count response.
        - ``TranscriptDelta`` -> optional ``on_transcript`` callback.
        - ``SessionError`` -> log and stop the loop.
    """

    def __init__(
        self,
        session: DuplexVoiceSession,
        player: AudioPlayer,
        *,
        on_transcript: Callable[[TranscriptDelta], None] | None = None,
        latency_budget_ms: float = 500.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialize the loop.

        Args:
            session: Duplex voice session supplying events.
            player: Playback target for assistant audio.
            on_transcript: Optional callback invoked per transcript delta.
            latency_budget_ms: Warn when end-of-user-speech to first
                assistant audio exceeds this many milliseconds.
            clock: Monotonic clock (seconds); injectable for tests.
        """
        self.session = session
        self._player = player
        self._on_transcript = on_transcript
        self._latency_budget_ms = latency_budget_ms
        self._clock = clock
        self._assistant_speaking = False
        self._speech_stopped_at: float | None = None
        self._barge_ins = 0
        self._responses = 0
        self._last_latency_ms: float | None = None

    async def run(self) -> None:
        """Consume session events until the stream ends or errors.

        Cancellation-safe: an ``asyncio.CancelledError`` propagates after
        playback is stopped.
        """
        stream = self.session.events()
        if inspect.isawaitable(stream):
            stream = await stream
        try:
            async for event in stream:
                if not await self._handle(event):
                    break
        finally:
            if self._player.playing:
                await self._player.stop()

    async def _handle(self, event: object) -> bool:
        """Dispatch one event; return ``False`` to stop the loop."""
        match event:
            case AudioDelta(data=data):
                await self._on_audio(data)
            case SpeechStarted():
                await self._maybe_barge_in()
            case SpeechStopped():
                self._speech_stopped_at = self._clock()
            case ResponseDone():
                self._assistant_speaking = False
                self._responses += 1
            case TranscriptDelta() as delta:
                if self._on_transcript is not None:
                    self._on_transcript(delta)
            case SessionError(message=message):
                logger.error("baselithbot_realtime_session_error", error=message)
                return False
            case _:
                pass  # ResponseStarted and future events need no action here.
        return True

    async def _on_audio(self, data: bytes) -> None:
        if self._speech_stopped_at is not None:
            latency_ms = (self._clock() - self._speech_stopped_at) * 1000.0
            self._speech_stopped_at = None
            self._last_latency_ms = latency_ms
            if latency_ms > self._latency_budget_ms:
                logger.warning(
                    "baselithbot_realtime_latency_over_budget",
                    latency_ms=round(latency_ms, 1),
                    budget_ms=self._latency_budget_ms,
                )
        await self._player.play(data)
        self._assistant_speaking = True

    async def _maybe_barge_in(self) -> None:
        if not (self._assistant_speaking and self._player.playing):
            return
        self._barge_ins += 1
        logger.info("baselithbot_realtime_barge_in", total=self._barge_ins)
        await self.session.cancel_response()
        await self._player.stop()
        self._assistant_speaking = False

    def stats(self) -> dict[str, float | int | None]:
        """Return loop counters and the last measured response latency.

        Returns:
            Mapping with ``barge_ins``, ``last_response_latency_ms`` (``None``
            until the first response) and ``responses``.
        """
        return {
            "barge_ins": self._barge_ins,
            "last_response_latency_ms": self._last_latency_ms,
            "responses": self._responses,
        }


__all__ = ["AudioPlayer", "BufferedAudioPlayer", "RealtimeVoiceLoop"]
