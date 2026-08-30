"""Tests for the OpenAI Realtime duplex session adapter (fake WS only)."""

from __future__ import annotations

import base64
import json
from typing import Any

import aiohttp
import pytest
from plugins.baselithbot.voice.openai_realtime import (
    OpenAIRealtimeSession,
    RealtimeVoiceSettings,
    build_realtime_loop,
)
from plugins.baselithbot.voice.realtime_loop import RealtimeVoiceLoop
from pydantic import SecretStr

from core.realtime import (
    AudioDelta,
    DuplexVoiceSession,
    ResponseDone,
    ResponseStarted,
    SessionError,
    SpeechStarted,
    SpeechStopped,
    TranscriptDelta,
)


def _text(payload: dict[str, Any]) -> aiohttp.WSMessage:
    return aiohttp.WSMessage(aiohttp.WSMsgType.TEXT, json.dumps(payload), "")


class FakeWS:
    """Scripted stand-in for aiohttp's ClientWebSocketResponse."""

    def __init__(self, inbound: list[aiohttp.WSMessage] | None = None) -> None:
        self.sent: list[dict[str, Any]] = []
        self._inbound = list(inbound or [])
        self.closed = False

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)

    def __aiter__(self) -> FakeWS:
        return self

    async def __anext__(self) -> aiohttp.WSMessage:
        if not self._inbound:
            raise StopAsyncIteration
        return self._inbound.pop(0)

    async def close(self) -> None:
        self.closed = True


def _session_with(
    ws: FakeWS, **kwargs: Any
) -> tuple[OpenAIRealtimeSession, dict[str, Any]]:
    seen: dict[str, Any] = {}

    async def fake_connect(url: str, headers: dict[str, str]) -> FakeWS:
        seen["url"] = url
        seen["headers"] = headers
        return ws

    session = OpenAIRealtimeSession(
        api_key=SecretStr("sk-test"), ws_connect=fake_connect, **kwargs
    )
    return session, seen


class TestHandshake:
    async def test_conforms_to_core_protocol(self) -> None:
        session, _ = _session_with(FakeWS())
        assert isinstance(session, DuplexVoiceSession)
        await session.close()

    async def test_session_update_sent_on_start(self) -> None:
        ws = FakeWS()
        session, seen = _session_with(ws, silence_duration_ms=350, voice="verse")

        [event async for event in await session.events()]

        assert ws.sent[0] == {
            "type": "session.update",
            "session": {
                "turn_detection": {
                    "type": "server_vad",
                    "silence_duration_ms": 350,
                },
                "voice": "verse",
                "modalities": ["audio", "text"],
            },
        }
        assert "model=" in seen["url"]
        assert seen["headers"]["Authorization"] == "Bearer sk-test"
        await session.close()

    async def test_default_silence_duration_is_500(self) -> None:
        ws = FakeWS()
        session, _ = _session_with(ws)
        [event async for event in await session.events()]
        turn = ws.sent[0]["session"]["turn_detection"]
        assert turn == {"type": "server_vad", "silence_duration_ms": 500}
        await session.close()


class TestOutbound:
    async def test_send_audio_appends_base64_pcm(self) -> None:
        ws = FakeWS()
        session, _ = _session_with(ws)

        await session.send_audio(b"\x01\x02\xff")

        append = ws.sent[-1]
        assert append["type"] == "input_audio_buffer.append"
        assert base64.b64decode(append["audio"]) == b"\x01\x02\xff"
        await session.close()

    async def test_cancel_response_sends_response_cancel(self) -> None:
        ws = FakeWS()
        session, _ = _session_with(ws)

        await session.cancel_response()

        assert ws.sent[-1] == {"type": "response.cancel"}
        await session.close()


class TestInboundMapping:
    async def test_every_server_event_maps_to_a_duplex_event(self) -> None:
        audio = base64.b64encode(b"pcm-bytes").decode("ascii")
        ws = FakeWS(
            [
                _text({"type": "input_audio_buffer.speech_started"}),
                _text({"type": "input_audio_buffer.speech_stopped"}),
                _text({"type": "response.created"}),
                _text({"type": "response.audio.delta", "delta": audio}),
                _text({"type": "response.audio_transcript.delta", "delta": "hey"}),
                _text(
                    {
                        "type": (
                            "conversation.item.input_audio_transcription.completed"
                        ),
                        "transcript": "user words",
                    }
                ),
                _text({"type": "response.done"}),
                _text({"type": "error", "error": {"message": "rate limited"}}),
                _text({"type": "some.unknown.event"}),
            ]
        )
        session, _ = _session_with(ws)

        received = [event async for event in await session.events()]

        assert received == [
            SpeechStarted(),
            SpeechStopped(),
            ResponseStarted(),
            AudioDelta(data=b"pcm-bytes"),
            TranscriptDelta(text="hey", role="assistant"),
            TranscriptDelta(text="user words", role="user"),
            ResponseDone(),
            SessionError(message="rate limited"),
        ]
        await session.close()

    async def test_ws_error_message_yields_session_error_and_closes(self) -> None:
        ws = FakeWS(
            [aiohttp.WSMessage(aiohttp.WSMsgType.ERROR, RuntimeError("dead"), None)]
        )
        session, _ = _session_with(ws)

        received = [event async for event in await session.events()]

        assert len(received) == 1
        assert isinstance(received[0], SessionError)
        assert session.closed is True

    async def test_connect_failure_yields_session_error_then_closed(self) -> None:
        async def failing_connect(url: str, headers: dict[str, str]) -> FakeWS:
            raise aiohttp.ClientError("connection refused")

        session = OpenAIRealtimeSession(
            api_key=SecretStr("sk-test"), ws_connect=failing_connect
        )

        received = [event async for event in await session.events()]

        assert len(received) == 1
        assert isinstance(received[0], SessionError)
        assert "connection refused" in received[0].message
        assert session.closed is True

    async def test_missing_api_key_yields_session_error(self) -> None:
        session = OpenAIRealtimeSession(api_key=None)

        received = [event async for event in await session.events()]

        assert len(received) == 1
        assert isinstance(received[0], SessionError)
        assert session.closed is True


class TestClose:
    async def test_close_closes_ws_and_marks_session(self) -> None:
        ws = FakeWS()
        session, _ = _session_with(ws)
        [event async for event in await session.events()]

        assert session.closed is False
        await session.close()

        assert ws.closed is True
        assert session.closed is True

    async def test_send_audio_after_close_raises(self) -> None:
        ws = FakeWS()
        session, _ = _session_with(ws)
        await session.close()

        with pytest.raises(RuntimeError):
            await session.send_audio(b"x")


class TestOptInSeam:
    def test_realtime_disabled_by_default(self) -> None:
        assert RealtimeVoiceSettings().realtime_enabled is False

    def test_env_flag_enables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BASELITHBOT_VOICE_REALTIME_ENABLED", "true")
        assert RealtimeVoiceSettings().realtime_enabled is True

    async def test_factory_returns_none_when_disabled(self) -> None:
        class NullPlayer:
            async def play(self, chunk: bytes) -> None: ...

            async def stop(self) -> None: ...

            @property
            def playing(self) -> bool:
                return False

        assert (
            build_realtime_loop(NullPlayer(), settings=RealtimeVoiceSettings()) is None
        )

    async def test_factory_builds_loop_with_openai_session_when_enabled(self) -> None:
        class NullPlayer:
            async def play(self, chunk: bytes) -> None: ...

            async def stop(self) -> None: ...

            @property
            def playing(self) -> bool:
                return False

        async def fake_connect(url: str, headers: dict[str, str]) -> FakeWS:
            return FakeWS()

        settings = RealtimeVoiceSettings(
            realtime_enabled=True, realtime_silence_duration_ms=321
        )
        loop = build_realtime_loop(
            NullPlayer(),
            settings=settings,
            api_key=SecretStr("sk-test"),
            ws_connect=fake_connect,
        )

        assert isinstance(loop, RealtimeVoiceLoop)
        assert isinstance(loop.session, OpenAIRealtimeSession)
        await loop.session.close()
