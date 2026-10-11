"""Tests for Voice Service."""

import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import SecretStr

from baselith.core.services.voice.errors import VoiceNotConfiguredError
from baselith.core.services.voice.models import VoiceProvider
from baselith.core.services.voice.service import VoiceService

_REAL_HTTPX_CLIENT = httpx.AsyncClient


def _real_caps(cfg: MagicMock) -> None:
    """Give a mocked VoiceConfig the real cap defaults (a bare MagicMock ints to 1)."""
    cfg.max_audio_bytes = 10 * 1024 * 1024
    cfg.max_response_bytes = 20 * 1024 * 1024
    cfg.max_tts_chars = 4096
    cfg.timeout_seconds = 30.0


def _google_transport(payload: dict, seen: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handler)


@pytest.fixture
def voice_service():
    # VoiceConfig api_keys are typed `Optional[SecretStr]`; service calls
    # `.get_secret_value()`. Mock with real SecretStr instances.
    with patch("baselith.core.services.voice.service.get_voice_config") as mock_config:
        mock_config.return_value.openai_api_key = SecretStr("fake-key")
        mock_config.return_value.elevenlabs_api_key = SecretStr("fake-key")
        mock_config.return_value.google_api_key = SecretStr("fake-google-key")
        mock_config.return_value.provider = "openai"
        _real_caps(mock_config.return_value)

        service = VoiceService(
            openai_api_key="fake-key",
            elevenlabs_api_key="fake-key",
            google_credentials_path="/tmp/fake-creds.json",
        )
        yield service


@pytest.mark.asyncio
async def test_init(voice_service):
    assert voice_service.default_provider == VoiceProvider.OPENAI
    assert voice_service._openai_key == "fake-key"
    assert voice_service._elevenlabs_key == "fake-key"


def test_default_provider_comes_from_voice_provider_setting():
    """VOICE_PROVIDER is honoured when no provider is passed explicitly."""
    with patch("baselith.core.services.voice.service.get_voice_config") as mock_config:
        mock_config.return_value.openai_api_key = None
        mock_config.return_value.elevenlabs_api_key = SecretStr("k")
        mock_config.return_value.provider = "elevenlabs"
        assert VoiceService().default_provider == VoiceProvider.ELEVENLABS
        explicit = VoiceService(default_provider=VoiceProvider.GOOGLE)
        assert explicit.default_provider == VoiceProvider.GOOGLE


@pytest.mark.asyncio
async def test_tts_openai(voice_service):
    with patch("openai.AsyncOpenAI") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value = mock_client
        mock_response = MagicMock()
        mock_response.content = b"openai-audio"
        mock_client.audio.speech.create.return_value = mock_response

        response = await voice_service.text_to_speech(
            text="Hello", provider=VoiceProvider.OPENAI
        )

        assert response.success
        assert response.content == b"openai-audio"
        assert response.provider == "openai"
        mock_client.audio.speech.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_tts_google(voice_service):
    seen: list[httpx.Request] = []
    b64_content = base64.b64encode(b"google-audio").decode("utf-8")
    voice_service._http_client = _REAL_HTTPX_CLIENT(
        transport=_google_transport({"audioContent": b64_content}, seen)
    )

    response = await voice_service.text_to_speech(
        text="Hello", provider=VoiceProvider.GOOGLE, voice="en-US-Standard-A"
    )

    assert response.success
    assert response.content == b"google-audio"
    assert response.provider == "google"
    assert len(seen) == 1
    assert str(seen[0].url) == "https://texttospeech.googleapis.com/v1/text:synthesize"
    assert json.loads(seen[0].content)["voice"]["languageCode"] == "en-US"


@pytest.mark.asyncio
async def test_stt_openai(voice_service):
    with patch("openai.AsyncOpenAI") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value = mock_client
        mock_transcript = MagicMock()
        mock_transcript.text = "Hello world"
        mock_client.audio.transcriptions.create.return_value = mock_transcript

        response = await voice_service.speech_to_text(
            audio_data=b"audio-data", provider=VoiceProvider.OPENAI
        )

        assert response.success
        assert response.text == "Hello world"
        mock_client.audio.transcriptions.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_stt_google(voice_service):
    seen: list[httpx.Request] = []
    voice_service._http_client = _REAL_HTTPX_CLIENT(
        transport=_google_transport(
            {"results": [{"alternatives": [{"transcript": "Hello google"}]}]}, seen
        )
    )

    response = await voice_service.speech_to_text(
        audio_data=b"audio-data", provider=VoiceProvider.GOOGLE
    )

    assert response.success
    assert response.text == "Hello google"
    assert len(seen) == 1
    sent = json.loads(seen[0].content)
    assert base64.b64decode(sent["audio"]["content"]) == b"audio-data"


@pytest.mark.asyncio
async def test_http_client_reused_between_calls(voice_service):
    seen: list[httpx.Request] = []
    payload = {"audioContent": base64.b64encode(b"google-audio").decode()}

    def build(**kwargs):
        kwargs.pop("transport", None)
        return _REAL_HTTPX_CLIENT(transport=_google_transport(payload, seen), **kwargs)

    with patch("httpx.AsyncClient", side_effect=build) as mock_client_cls:
        await voice_service.text_to_speech(
            text="Hello", provider=VoiceProvider.GOOGLE, voice="en-US-Standard-A"
        )
        await voice_service.text_to_speech(
            text="Hello again", provider=VoiceProvider.GOOGLE, voice="en-US-Standard-A"
        )

        mock_client_cls.assert_called_once()
        assert len(seen) == 2
    await voice_service.aclose()


@pytest.mark.asyncio
async def test_convenience_methods(voice_service):
    # Mock text_to_speech
    mock_resp_tts = MagicMock()
    mock_resp_tts.audio_bytes = b"audio"
    mock_resp_tts.save_audio = MagicMock()

    # Mock speech_to_text
    mock_resp_stt = MagicMock()
    mock_resp_stt.text = "transcribed text"

    with patch.object(
        voice_service, "text_to_speech", new_callable=AsyncMock
    ) as mock_tts:
        mock_tts.return_value = mock_resp_tts

        # Test speak
        audio = await voice_service.speak("hello")
        assert audio == b"audio"
        mock_tts.assert_awaited_with("hello", voice="alloy")

        # Test speak_and_save
        path = await voice_service.speak_and_save("hello", "out.mp3")
        assert path == "out.mp3"
        mock_resp_tts.save_audio.assert_called_with("out.mp3")

    with patch.object(
        voice_service, "speech_to_text", new_callable=AsyncMock
    ) as mock_stt:
        mock_stt.return_value = mock_resp_stt

        # Test transcribe
        text = await voice_service.transcribe("in.mp3")
        assert text == "transcribed text"
        mock_stt.assert_awaited_with(audio_file="in.mp3")


@pytest.mark.asyncio
async def test_missing_api_keys():
    # Patch config to return None
    with patch("baselith.core.services.voice.service.get_voice_config") as mock_config:
        mock_config.return_value.openai_api_key = None
        mock_config.return_value.elevenlabs_api_key = None
        mock_config.return_value.google_api_key = None
        mock_config.return_value.google_credentials_path = None
        mock_config.return_value.provider = "openai"
        _real_caps(mock_config.return_value)

        # Initialize service AFTER patching config
        empty_service = VoiceService(
            openai_api_key=None, elevenlabs_api_key=None, google_credentials_path=None
        )

        # OpenAI
        with pytest.raises(ValueError, match="OpenAI API key"):
            await empty_service.text_to_speech("hi", provider=VoiceProvider.OPENAI)

        # ElevenLabs
        with pytest.raises(VoiceNotConfiguredError, match="ElevenLabs API key"):
            await empty_service.text_to_speech("hi", provider=VoiceProvider.ELEVENLABS)

        # Google
        with pytest.raises(ValueError, match="Google API key"):
            await empty_service.text_to_speech("hi", provider=VoiceProvider.GOOGLE)


class _FakeAsyncOpenAI:
    """Captures constructor kwargs; counts instantiations."""

    instances = 0
    last_kwargs: dict = {}

    def __init__(self, **kwargs):
        type(self).instances += 1
        type(self).last_kwargs = kwargs
        self.closed = False

    async def close(self):
        self.closed = True


def _install_fake_openai(monkeypatch):
    import sys
    import types

    _FakeAsyncOpenAI.instances = 0
    _FakeAsyncOpenAI.last_kwargs = {}
    module = types.ModuleType("openai")
    module.AsyncOpenAI = _FakeAsyncOpenAI
    monkeypatch.setitem(sys.modules, "openai", module)


@pytest.mark.asyncio
async def test_openai_client_is_lazy_singleton_with_timeout(voice_service, monkeypatch):
    """_tts_openai/_stt_openai used to build a fresh AsyncOpenAI per call —
    leaked httpx pool, SDK-default 600s timeout. One shared client now."""
    _install_fake_openai(monkeypatch)
    first = voice_service._get_openai_client()
    second = voice_service._get_openai_client()
    assert first is second
    assert _FakeAsyncOpenAI.instances == 1
    assert _FakeAsyncOpenAI.last_kwargs["api_key"] == "fake-key"
    assert _FakeAsyncOpenAI.last_kwargs.get("timeout") is not None


@pytest.mark.asyncio
async def test_aclose_also_closes_openai_client(voice_service, monkeypatch):
    _install_fake_openai(monkeypatch)
    client = voice_service._get_openai_client()
    await voice_service.aclose()
    assert client.closed is True
