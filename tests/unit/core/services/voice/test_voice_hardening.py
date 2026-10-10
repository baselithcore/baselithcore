"""Regression tests for the voice caps and the Google client's hardening.

Each test pins a defect that existed before: the Google key rode in the query
string (and so into every ``HTTPStatusError`` message), the Google path had no
deadline and no response cap, the OpenAI SDK and Google paths ignored
``VOICE_MAX_AUDIO_BYTES`` and ``VOICE_MAX_TTS_CHARS``, and a file-based STT
request was read synchronously on the event loop, twice.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import SecretStr

from baselith.core.services.voice import _audio_input
from baselith.core.services.voice.errors import (
    VoiceInputError,
    VoiceLimitError,
    VoiceTimeoutError,
    VoiceUpstreamError,
)
from baselith.core.services.voice.models import STTRequest, VoiceProvider
from baselith.core.services.voice.service import VoiceService

_GOOGLE_KEY = "AIza-test-google-key"


@pytest.fixture
def cfg() -> Iterator[MagicMock]:
    with patch("baselith.core.services.voice.service.get_voice_config") as factory:
        config = factory.return_value
        config.openai_api_key = SecretStr("fake-key")
        config.elevenlabs_api_key = None
        config.google_api_key = SecretStr(_GOOGLE_KEY)
        config.google_credentials_path = None
        config.provider = "openai"
        config.max_audio_bytes = 1024
        config.max_response_bytes = 2048
        config.max_tts_chars = 50
        config.timeout_seconds = 5.0
        yield config


@pytest.fixture
def service(cfg: MagicMock) -> VoiceService:
    return VoiceService(openai_api_key="fake-key")


def _install(service: VoiceService, handler: Any) -> None:
    service._http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- Google client ----------------------------------------------------------


async def test_google_key_travels_in_a_header_never_in_the_url(
    service: VoiceService,
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json={"audioContent": base64.b64encode(b"a").decode()}
        )

    _install(service, handler)
    await service.text_to_speech("hello", provider=VoiceProvider.GOOGLE)

    assert seen[0].headers["x-goog-api-key"] == _GOOGLE_KEY
    assert _GOOGLE_KEY not in str(seen[0].url)
    assert "key=" not in str(seen[0].url)


async def test_google_error_never_carries_the_key(service: VoiceService) -> None:
    _install(service, lambda _r: httpx.Response(403, text=f"denied {_GOOGLE_KEY}"))

    with pytest.raises(VoiceUpstreamError) as caught:
        await service.text_to_speech("hello", provider=VoiceProvider.GOOGLE)

    assert caught.value.status == 403
    assert _GOOGLE_KEY not in str(caught.value)
    assert caught.value.__cause__ is None


async def test_google_response_is_abandoned_at_the_cap(
    service: VoiceService,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        big = b'{"audioContent":"' + b"A" * 4096 + b'"}'
        # Chunked (no Content-Length): only the streaming counter can stop it.

        async def chunks() -> AsyncIterator[bytes]:
            yield big[:1000]
            yield big[1000:]

        return httpx.Response(200, content=chunks())

    _install(service, handler)
    with pytest.raises(VoiceLimitError):
        await service.text_to_speech("hello", provider=VoiceProvider.GOOGLE)


async def test_google_declared_oversize_is_refused_before_reading(
    service: VoiceService,
) -> None:
    _install(
        service,
        lambda _r: httpx.Response(
            200, headers={"content-length": "999999"}, content=b"{}"
        ),
    )
    with pytest.raises(VoiceLimitError):
        await service.text_to_speech("hello", provider=VoiceProvider.GOOGLE)


async def test_google_call_is_bound_by_the_voice_deadline(
    service: VoiceService, cfg: MagicMock
) -> None:
    cfg.timeout_seconds = 0.05

    async def handler(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, json={})

    _install(service, handler)
    with pytest.raises(VoiceTimeoutError):
        await service.text_to_speech("hello", provider=VoiceProvider.GOOGLE)


async def test_google_invalid_json_is_a_typed_error(service: VoiceService) -> None:
    _install(service, lambda _r: httpx.Response(200, content=b"<html>"))
    with pytest.raises(VoiceUpstreamError, match="invalid JSON"):
        await service.text_to_speech("hello", provider=VoiceProvider.GOOGLE)


# --- caps on every provider -------------------------------------------------


@pytest.mark.parametrize("provider", [VoiceProvider.OPENAI, VoiceProvider.GOOGLE])
async def test_tts_char_cap_applies_to_sdk_and_google(
    service: VoiceService, provider: VoiceProvider
) -> None:
    with pytest.raises(VoiceLimitError, match="synthesis cap"):
        await service.text_to_speech("x" * 51, provider=provider)


@pytest.mark.parametrize("provider", [VoiceProvider.OPENAI, VoiceProvider.GOOGLE])
async def test_stt_audio_cap_applies_to_sdk_and_google(
    service: VoiceService, provider: VoiceProvider
) -> None:
    service._openai_client = AsyncMock()
    _install(service, lambda _r: pytest.fail("the oversized upload reached the wire"))

    with pytest.raises(VoiceLimitError, match="upload cap"):
        await service.speech_to_text(audio_data=b"x" * 1025, provider=provider)

    service._openai_client.audio.transcriptions.create.assert_not_awaited()


# --- file input -------------------------------------------------------------


async def test_audio_file_is_read_once_in_a_worker_thread(
    service: VoiceService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audio = tmp_path / "in.mp3"
    audio.write_bytes(b"abc")
    offloaded: list[Any] = []
    real_to_thread = asyncio.to_thread

    async def spy(fn: Any, *args: Any) -> Any:
        offloaded.append(fn)
        return await real_to_thread(fn, *args)

    monkeypatch.setattr(_audio_input.asyncio, "to_thread", spy)
    client = AsyncMock()
    client.audio.transcriptions.create.return_value = MagicMock(text="ok")
    service._openai_client = client

    response = await service.speech_to_text(audio_file=str(audio))

    assert response.text == "ok"
    assert offloaded == [_audio_input._read_file_capped]
    sent = client.audio.transcriptions.create.await_args.kwargs["file"]
    assert sent.getvalue() == b"abc"


async def test_oversized_file_is_refused_from_its_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audio = tmp_path / "big.mp3"
    audio.write_bytes(b"x" * 2000)
    monkeypatch.setattr(
        "builtins.open", lambda *_a, **_k: pytest.fail("an over-cap file was opened")
    )
    with pytest.raises(VoiceLimitError):
        await _audio_input.load_audio(STTRequest(audio_file=str(audio)), 1024)


async def test_unreadable_file_does_not_echo_the_path(tmp_path: Path) -> None:
    missing = tmp_path / "secret-dir" / "nope.mp3"
    with pytest.raises(VoiceInputError) as caught:
        await _audio_input.load_audio(STTRequest(audio_file=str(missing)), 1024)
    assert "secret-dir" not in str(caught.value)


async def test_bytes_input_is_returned_untouched() -> None:
    request = STTRequest(audio_data=b"abc")
    assert await _audio_input.load_audio(request, 1024) == b"abc"
