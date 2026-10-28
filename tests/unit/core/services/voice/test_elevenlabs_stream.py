"""ElevenLabs streaming: the ``/stream`` endpoint, chunk delivery, caps and failures."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from elevenlabs_rig import TARGET, VOICE, client, error, make_service

from baselith.core.services.voice import (
    SpeechContext,
    VoiceStream,
    supports_language,
    supports_speed,
    supports_stitching,
    supports_tags,
)
from baselith.core.services.voice._elevenlabs_payload import speech_payload
from baselith.core.services.voice.errors import (
    VoiceInputError,
    VoiceKeyInvalidError,
    VoiceLimitError,
    VoiceTimeoutError,
    VoiceUpstreamError,
)


def _chunked(parts: list[bytes], seen: list[httpx.Request], **headers: str) -> Any:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)

        async def body() -> Any:
            for part in parts:
                yield part

        return httpx.Response(
            200, content=body(), headers={"content-type": "audio/pcm", **headers}
        )

    return handle


async def _stream(c: Any, model: str = "eleven_v4_turbo", **kw: Any) -> Any:
    return await c.speech_stream(
        TARGET,
        kw.pop("text", "Ciao a tutti."),
        voice_id=VOICE,
        model=model,
        output_format=kw.pop("output_format", "pcm_24000"),
        stability=0.5,
        similarity_boost=0.75,
        style=0.0,
        **kw,
    )


async def _drain(chunks: Any) -> list[bytes]:
    return [c async for c in chunks]


class TestStreamWire:
    async def test_the_stream_endpoint_and_chunks_in_order(self) -> None:
        seen: list[httpx.Request] = []
        stream = await _stream(
            client(_chunked([b"aa", b"bb", b"c"], seen), max_response_bytes=64),
            context=SpeechContext(previous_request_ids=("req_0",)),
        )
        (req,) = seen
        assert req.url.path == f"/v1/text-to-speech/{VOICE}/stream"
        assert req.url.params["output_format"] == "pcm_24000"
        assert json.loads(req.content)["previous_request_ids"] == ["req_0"]
        assert stream.stitched is True
        assert stream.content_type == "audio/pcm"
        assert await _drain(stream.chunks) == [b"aa", b"bb", b"c"]

    async def test_the_request_id_is_known_before_the_body(self) -> None:
        seen: list[httpx.Request] = []
        stream = await _stream(
            client(_chunked([b"a"], seen, **{"request-id": "req_9"}))
        )
        assert stream.request_id == "req_9"

    async def test_a_refusal_is_raised_before_the_stream_exists(self) -> None:
        with pytest.raises(VoiceKeyInvalidError):
            await _stream(client(error(401, "invalid_api_key")))

    async def test_an_invalid_output_format_never_reaches_the_wire(self) -> None:
        seen: list[httpx.Request] = []
        with pytest.raises(VoiceInputError):
            await _stream(client(_chunked([b"a"], seen)), output_format="pcm_24000?x=1")
        assert seen == []

    async def test_an_empty_stream_is_an_upstream_failure(self) -> None:
        stream = await _stream(client(_chunked([], [])))
        with pytest.raises(VoiceUpstreamError, match="empty"):
            await _drain(stream.chunks)

    async def test_the_cap_cuts_the_stream(self) -> None:
        stream = await _stream(client(_chunked([b"x" * 400] * 10, [])))
        with pytest.raises(VoiceLimitError):
            await _drain(stream.chunks)

    async def test_a_stalled_stream_times_out_per_chunk(self) -> None:
        def handle(request: httpx.Request) -> httpx.Response:
            async def body() -> Any:
                yield b"first"
                await asyncio.sleep(1.0)
                yield b"never"

            return httpx.Response(200, content=body())

        c = client(handle, timeout_seconds=0.05)
        stream = await _stream(c)
        chunks = stream.chunks
        assert await anext(chunks) == b"first"
        with pytest.raises(VoiceTimeoutError):
            await anext(chunks)

    async def test_closing_early_releases_the_response(self) -> None:
        stream = await _stream(client(_chunked([b"a", b"b", b"c"], [])))
        assert await anext(stream.chunks) == b"a"
        await stream.chunks.aclose()  # type: ignore[attr-defined]
        with pytest.raises(StopAsyncIteration):
            await anext(stream.chunks)


class TestModelCapabilities:
    def test_the_v4_family_takes_no_speed_or_style(self) -> None:
        body = speech_payload(
            "ciao",
            model="eleven_v4_turbo",
            stability=0.5,
            similarity_boost=0.75,
            style=0.3,
            speed=1.1,
            language="it",
            context=None,
        )
        assert body["voice_settings"] == {"stability": 0.5, "similarity_boost": 0.75}
        assert body["language_code"] == "it"

    def test_the_predicates_name_the_families(self) -> None:
        assert supports_speed("eleven_multilingual_v2") and not supports_speed(
            "eleven_v4"
        )
        assert supports_stitching("eleven_v4_turbo") and not supports_stitching(
            "eleven_v3"
        )
        assert supports_tags("eleven_v4") and supports_tags("eleven_v3")
        assert not supports_tags("eleven_flash_v2_5")
        assert supports_language("eleven_v4") and not supports_language(
            "eleven_multilingual_v2"
        )


class TestServiceStream:
    async def test_elevenlabs_streams_pcm_and_reports_characters(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from baselith.core.services.llm.usage_sinks import (
            register_usage_sink,
            unregister_usage_sink,
        )

        reports: list[Any] = []
        seen: list[httpx.Request] = []
        register_usage_sink(reports.append)
        try:
            svc = make_service(
                _chunked([b"\x00\x01", b"\x02\x03"], seen, **{"request-id": "req_1"}),
                monkeypatch,
            )
            stream = await svc.text_to_speech_stream(
                "Buongiorno a tutti",
                output_format="pcm_24000",
                context=SpeechContext(next_text="Poi."),
            )
        finally:
            unregister_usage_sink(reports.append)
        assert isinstance(stream, VoiceStream)
        assert stream.provider == "elevenlabs"
        assert stream.content_type == "audio/pcm" and stream.sample_rate == 24000
        assert stream.metadata["request_id"] == "req_1" and stream.metadata["stitched"]
        assert await stream.read() == b"\x00\x01\x02\x03"
        assert seen[0].url.path.endswith("/stream")
        tts = [r for r in reports if r.model.startswith("tts:elevenlabs/")]
        assert tts and tts[0].usage.input_tokens == len("Buongiorno a tutti")

    async def test_the_configured_format_is_the_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[httpx.Request] = []
        svc = make_service(_chunked([b"a"], seen), monkeypatch)
        stream = await svc.text_to_speech_stream("ciao")
        assert seen[0].url.params["output_format"] == "mp3_44100_128"
        assert stream.content_type == "audio/mpeg" and stream.sample_rate is None

    async def test_another_provider_answers_whole_as_one_chunk(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from unittest.mock import AsyncMock

        from baselith.core.services.voice import VoiceProvider, VoiceResponse

        svc = make_service(error(500), monkeypatch)
        whole = VoiceResponse(
            True, b"mp3", "openai", "tts-1", metadata={"format": "mp3"}
        )
        monkeypatch.setattr(svc, "text_to_speech", AsyncMock(return_value=whole))
        stream = await svc.text_to_speech_stream("ciao", provider=VoiceProvider.OPENAI)
        assert stream.content_type == "audio/mpeg" and stream.provider == "openai"
        assert await stream.read() == b"mp3"
