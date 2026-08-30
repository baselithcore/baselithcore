"""Contract tests for the transport-agnostic duplex voice session protocol."""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator
from typing import get_args

import pytest

from core.realtime import (
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


class ScriptedSession:
    """Minimal in-memory session that replays a scripted event list."""

    def __init__(self, script: list[DuplexEvent]) -> None:
        self._script = list(script)
        self._closed = False
        self.sent_audio: list[bytes] = []
        self.cancels = 0

    async def events(self) -> AsyncIterator[DuplexEvent]:
        return self._generate()

    async def _generate(self) -> AsyncIterator[DuplexEvent]:
        for event in self._script:
            yield event

    async def send_audio(self, pcm: bytes) -> None:
        self.sent_audio.append(pcm)

    async def cancel_response(self) -> None:
        self.cancels += 1

    async def close(self) -> None:
        self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed


class TestEventDataclasses:
    def test_all_events_are_frozen(self) -> None:
        events = [
            AudioDelta(data=b"pcm"),
            TranscriptDelta(text="hi", role="assistant"),
            SpeechStarted(),
            SpeechStopped(),
            ResponseStarted(),
            ResponseDone(),
            SessionError(message="boom"),
        ]
        for event in events:
            assert dataclasses.is_dataclass(event)
            field = (
                dataclasses.fields(event)[0].name if dataclasses.fields(event) else None
            )
            if field is not None:
                with pytest.raises(dataclasses.FrozenInstanceError):
                    setattr(event, field, "mutated")

    def test_union_alias_covers_every_event(self) -> None:
        members = set(get_args(DuplexEvent))
        assert members == {
            AudioDelta,
            TranscriptDelta,
            SpeechStarted,
            SpeechStopped,
            ResponseStarted,
            ResponseDone,
            SessionError,
        }

    def test_transcript_roles(self) -> None:
        assert TranscriptDelta(text="a", role="user").role == "user"
        assert TranscriptDelta(text="b", role="assistant").role == "assistant"


class TestProtocolConformance:
    def test_scripted_session_is_runtime_instance(self) -> None:
        session = ScriptedSession([])
        assert isinstance(session, DuplexVoiceSession)

    def test_incomplete_object_is_not_an_instance(self) -> None:
        class NotASession:
            async def events(self) -> AsyncIterator[DuplexEvent]:
                raise NotImplementedError

        assert not isinstance(NotASession(), DuplexVoiceSession)

    async def test_scripted_session_full_round_trip(self) -> None:
        script: list[DuplexEvent] = [
            SpeechStarted(),
            SpeechStopped(),
            ResponseStarted(),
            AudioDelta(data=b"\x00\x01"),
            TranscriptDelta(text="hello", role="assistant"),
            ResponseDone(),
        ]
        session = ScriptedSession(script)

        received = [event async for event in await session.events()]
        assert received == script

        await session.send_audio(b"\xff")
        assert session.sent_audio == [b"\xff"]

        await session.cancel_response()
        assert session.cancels == 1

        assert session.closed is False
        await session.close()
        assert session.closed is True
