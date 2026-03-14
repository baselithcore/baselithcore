"""Tests for Voice Service."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from core.services.voice.service import VoiceService
from core.services.voice.models import VoiceProvider


@pytest.fixture
def voice_service():
    # Patch get_voice_config to return dummy values for Google API keys
    with patch("core.services.voice.service.get_voice_config") as mock_config:
        mock_config.return_value.openai_api_key = "fake-key"
        mock_config.return_value.elevenlabs_api_key = "fake-key"
        mock_config.return_value.google_api_key = "fake-google-key"

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
async def test_tts_elevenlabs(voice_service):
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        mock_response = MagicMock()
        mock_response.content = b"elevenlabs-audio"
        mock_response.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_response

        response = await voice_service.text_to_speech(
            text="Hello", provider=VoiceProvider.ELEVENLABS, voice="Rachel"
        )

        assert response.success
        assert response.content == b"elevenlabs-audio"
        assert response.provider == "elevenlabs"
        mock_client.post.assert_awaited_once()
        assert "elevenlabs.io" in mock_client.post.call_args[0][0]


@pytest.mark.asyncio
async def test_tts_google(voice_service):
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        mock_response = MagicMock()
        # Mock base64 encoded response
        import base64

        b64_content = base64.b64encode(b"google-audio").decode("utf-8")
        mock_response.json.return_value = {"audioContent": b64_content}
        mock_response.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_response

        response = await voice_service.text_to_speech(
            text="Hello", provider=VoiceProvider.GOOGLE, voice="en-US-Standard-A"
        )

        assert response.success
        assert response.content == b"google-audio"
        assert response.provider == "google"
        mock_client.post.assert_awaited_once()
        assert "googleapis.com" in mock_client.post.call_args[0][0]


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
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "results": [{"alternatives": [{"transcript": "Hello google"}]}]
        }
        mock_response.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_response

        response = await voice_service.speech_to_text(
            audio_data=b"audio-data", provider=VoiceProvider.GOOGLE
        )

        assert response.success
        assert response.text == "Hello google"
        mock_client.post.assert_awaited_once()


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
    with patch("core.services.voice.service.get_voice_config") as mock_config:
        mock_config.return_value.openai_api_key = None
        mock_config.return_value.elevenlabs_api_key = None
        mock_config.return_value.google_api_key = None
        mock_config.return_value.google_credentials_path = None

        # Initialize service AFTER patching config
        empty_service = VoiceService(
            openai_api_key=None, elevenlabs_api_key=None, google_credentials_path=None
        )

        # OpenAI
        with pytest.raises(ValueError, match="OpenAI API key"):
            await empty_service.text_to_speech("hi", provider=VoiceProvider.OPENAI)

        # ElevenLabs
        with pytest.raises(ValueError, match="ElevenLabs API key"):
            await empty_service.text_to_speech("hi", provider=VoiceProvider.ELEVENLABS)

        # Google
        with pytest.raises(ValueError, match="Google API key"):
            await empty_service.text_to_speech("hi", provider=VoiceProvider.GOOGLE)
