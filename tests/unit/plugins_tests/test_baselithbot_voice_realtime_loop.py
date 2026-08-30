"""Tests for the realtime duplex voice loop (barge-in, latency, playback)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any
from unittest.mock import MagicMock

import pytest

from core.realtime import (
    AudioDelta,
    DuplexEvent,
    ResponseDone,
    ResponseStarted,
    SessionError,
    SpeechStarted,
    SpeechStopped,
    TranscriptDelta,
)
from plugins.baselithbot.voice import realtime_loop as loop_module
from plugins.baselithbot.voice.realtime_loop import (
    AudioPlayer,
    BufferedAudioPlayer,
    RealtimeVoiceLoop,
)

ScriptItem = DuplexEvent | Callable[[], None]


class FakeClock:
    """Deterministic monotonic clock advanced explicitly by the script."""

    def __init__(self, start: float = 100.0) -> None:
        self.now = start

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def __call__(self) -> float:
        return self.now


class ScriptedSession:
    """Duplex session replaying events; callables in the script run inline."""

    def __init__(self, script: list[ScriptItem]) -> None:
        self._script = list(script)
        self._closed = False
        self.cancels = 0

    async def events(self) -> AsyncIterator[DuplexEvent]:
        return self._generate()

    async def _generate(self) -> AsyncIterator[DuplexEvent]:
        for item in self._script:
            if callable(item):
                item()
                continue
            yield item

    async def send_audio(self, pcm: bytes) -> None:
        del pcm

    async def cancel_response(self) -> None:
        self.cancels += 1

    async def close(self) -> None:
        self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed


class HangingSession(ScriptedSession):
    """Session whose event stream never produces anything."""

    def __init__(self) -> None:
        super().__init__([])
        self.started = asyncio.Event()

    async def _generate(self) -> AsyncIterator[DuplexEvent]:
        self.started.set()
        await asyncio.Event().wait()
        yield SpeechStarted()  # pragma: no cover - never reached


class RecordingPlayer:
    """AudioPlayer fake capturing chunks and stop calls."""

    def __init__(self) -> None:
        self.chunks: list[bytes] = []
        self.stops = 0
        self._playing = False

    async def play(self, chunk: bytes) -> None:
        self.chunks.append(chunk)
        self._playing = True

    async def stop(self) -> None:
        self.stops += 1
        self._playing = False

    @property
    def playing(self) -> bool:
        return self._playing


class TestBargeIn:
    async def test_speech_started_while_playing_cancels_and_stops(self) -> None:
        session = ScriptedSession(
            [ResponseStarted(), AudioDelta(data=b"a"), SpeechStarted()]
        )
        player = RecordingPlayer()
        loop = RealtimeVoiceLoop(session, player)

        await loop.run()

        assert session.cancels == 1
        assert player.stops == 1
        assert loop.stats()["barge_ins"] == 1

    async def test_speech_started_while_idle_does_not_cancel(self) -> None:
        session = ScriptedSession([SpeechStarted(), SpeechStopped()])
        player = RecordingPlayer()
        loop = RealtimeVoiceLoop(session, player)

        await loop.run()

        assert session.cancels == 0
        assert player.stops == 0
        assert loop.stats()["barge_ins"] == 0

    async def test_speech_started_after_response_done_does_not_cancel(self) -> None:
        session = ScriptedSession(
            [AudioDelta(data=b"a"), ResponseDone(), SpeechStarted()]
        )
        player = RecordingPlayer()
        loop = RealtimeVoiceLoop(session, player)

        await loop.run()

        assert session.cancels == 0
        assert loop.stats()["barge_ins"] == 0
        assert loop.stats()["responses"] == 1


class TestLatencyMetric:
    async def test_latency_measured_from_speech_stop_to_first_audio(self) -> None:
        clock = FakeClock()
        session = ScriptedSession(
            [
                SpeechStopped(),
                lambda: clock.advance(0.25),
                AudioDelta(data=b"a"),
                lambda: clock.advance(5.0),
                AudioDelta(data=b"b"),
                ResponseDone(),
            ]
        )
        player = RecordingPlayer()
        loop = RealtimeVoiceLoop(session, player, clock=clock)

        await loop.run()

        stats = loop.stats()
        assert stats["last_response_latency_ms"] == 250.0
        assert stats["responses"] == 1
        assert player.chunks == [b"a", b"b"]

    async def test_latency_over_budget_logs_warning(self) -> None:
        clock = FakeClock()
        session = ScriptedSession(
            [SpeechStopped(), lambda: clock.advance(0.9), AudioDelta(data=b"a")]
        )
        fake_logger = MagicMock()
        loop = RealtimeVoiceLoop(
            session, RecordingPlayer(), latency_budget_ms=500.0, clock=clock
        )
        original = loop_module.logger
        loop_module.logger = fake_logger
        try:
            await loop.run()
        finally:
            loop_module.logger = original

        assert fake_logger.warning.called
        assert loop.stats()["last_response_latency_ms"] == pytest.approx(900.0)

    async def test_latency_under_budget_no_warning(self) -> None:
        clock = FakeClock()
        session = ScriptedSession(
            [SpeechStopped(), lambda: clock.advance(0.1), AudioDelta(data=b"a")]
        )
        fake_logger = MagicMock()
        loop = RealtimeVoiceLoop(
            session, RecordingPlayer(), latency_budget_ms=500.0, clock=clock
        )
        original = loop_module.logger
        loop_module.logger = fake_logger
        try:
            await loop.run()
        finally:
            loop_module.logger = original

        assert not fake_logger.warning.called


class TestTranscriptAndErrors:
    async def test_transcript_deltas_reach_callback(self) -> None:
        seen: list[tuple[str, str]] = []
        session = ScriptedSession(
            [
                TranscriptDelta(text="hi", role="user"),
                TranscriptDelta(text="yo", role="assistant"),
            ]
        )
        loop = RealtimeVoiceLoop(
            session,
            RecordingPlayer(),
            on_transcript=lambda d: seen.append((d.text, d.role)),
        )

        await loop.run()

        assert seen == [("hi", "user"), ("yo", "assistant")]

    async def test_session_error_stops_loop_cleanly(self) -> None:
        session = ScriptedSession(
            [SessionError(message="boom"), AudioDelta(data=b"never")]
        )
        player = RecordingPlayer()
        loop = RealtimeVoiceLoop(session, player)

        await loop.run()

        assert player.chunks == []

    async def test_run_is_cancellation_safe(self) -> None:
        session = HangingSession()
        loop = RealtimeVoiceLoop(session, RecordingPlayer())
        task = asyncio.create_task(loop.run())
        await session.started.wait()

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert task.cancelled()


class TestBufferedAudioPlayer:
    def _collect_sink(self) -> tuple[list[bytes], Any]:
        captured: list[bytes] = []

        async def sink(chunk: bytes) -> None:
            captured.append(chunk)

        return captured, sink

    async def test_satisfies_audio_player_protocol(self) -> None:
        _, sink = self._collect_sink()
        player = BufferedAudioPlayer(sink)
        assert isinstance(player, AudioPlayer)

    async def test_play_and_stop_toggle_playing(self) -> None:
        captured, sink = self._collect_sink()
        player = BufferedAudioPlayer(sink)

        assert player.playing is False
        await player.play(b"one")
        assert player.playing is True
        assert captured == [b"one"]
        await player.stop()
        assert player.playing is False

    async def test_stream_play_forwards_all_chunks(self) -> None:
        captured, sink = self._collect_sink()
        player = BufferedAudioPlayer(sink)

        async def chunks() -> AsyncIterator[bytes]:
            for c in (b"a", b"b", b"c"):
                yield c

        await player.stream_play(chunks())

        assert captured == [b"a", b"b", b"c"]
        assert player.playing is False

    async def test_stop_halts_in_flight_stream(self) -> None:
        captured, sink = self._collect_sink()
        player = BufferedAudioPlayer(sink)

        async def chunks() -> AsyncIterator[bytes]:
            yield b"a"
            await player.stop()
            yield b"b"
            yield b"c"

        await player.stream_play(chunks())

        assert captured == [b"a"]
        assert player.playing is False
