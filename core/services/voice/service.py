"""
Voice Service Implementation.

Multi-provider voice service supporting TTS (Text-to-Speech) and
STT (Speech-to-Text) operations.

Usage:
    from core.services.voice import VoiceService, TTSRequest, STTRequest

    service = VoiceService()

    # Text-to-Speech
    audio = await service.text_to_speech("Hello, world!")
    audio.save_audio("output.mp3")

    # Speech-to-Text
    text = await service.speech_to_text(audio_file="recording.mp3")
    print(text.text)
"""

from __future__ import annotations


from core.observability.logging import get_logger
from core.services.voice.models import (
    AudioFormat,
    STTRequest,
    TTSRequest,
    VoiceProvider,
    VoiceResponse,
)

from core.config import get_voice_config

logger = get_logger(__name__)


class VoiceService:
    """
    Multi-provider voice service.

    Supports:
    - OpenAI TTS/Whisper
    - ElevenLabs TTS
    - Google Cloud TTS/STT

    Features:
    - Multiple voices and models
    - Audio format conversion
    - Language detection
    - Real-time streaming (future)
    """

    # Default voices per provider
    DEFAULT_VOICES = {
        VoiceProvider.OPENAI: "alloy",
        VoiceProvider.ELEVENLABS: "Rachel",
        VoiceProvider.GOOGLE: "en-US-Wavenet-D",
    }

    def __init__(
        self,
        default_provider: VoiceProvider = VoiceProvider.OPENAI,
        openai_api_key: str | None = None,
        elevenlabs_api_key: str | None = None,
        google_credentials_path: str | None = None,
    ) -> None:
        """
        Initialize voice service.

        Args:
            default_provider: Default provider for voice operations
            openai_api_key: OpenAI API key (or from env OPENAI_API_KEY)
            elevenlabs_api_key: ElevenLabs API key (or from env ELEVENLABS_API_KEY)
            google_credentials_path: Path to Google credentials JSON
        """
        self.default_provider = default_provider
        self._openai_key = openai_api_key or get_voice_config().openai_api_key
        self._elevenlabs_key = (
            elevenlabs_api_key or get_voice_config().elevenlabs_api_key
        )
        # For Google, credentials path is complex, usually handled by library.
        # But we can try to respect the pattern if needed.
        # The existing code did os.getenv("GOOGLE_APPLICATION_CREDENTIALS") which is standard.
        # However, VoiceConfig doesn't seem to have google_credentials_path field in config/services.py?
        # Let's check config/services.py content again.
        # It has google_api_key.
        # The original code checked GOOGLE_APPLICATION_CREDENTIALS for the path.
        # config/services.py VoiceConfig only has api keys.
        # I will leave the Google Creds one alone OR update config to support it?
        # Let's leave it alone for now or use os.environ if strictly needed, but better to follow pattern.
        # I will replace only the keys that are in the config.
        self._google_creds = (
            google_credentials_path or get_voice_config().google_credentials_path
        )

        logger.info(
            "voice_service_initialized",
            default_provider=default_provider.value,
            openai_available=bool(self._openai_key),
            elevenlabs_available=bool(self._elevenlabs_key),
            google_available=bool(self._google_creds),
        )

    # -------------------------------------------------------------------------
    # Text-to-Speech
    # -------------------------------------------------------------------------

    async def text_to_speech(
        self,
        text: str,
        voice: str | None = None,
        speed: float = 1.0,
        format: AudioFormat = AudioFormat.MP3,
        provider: VoiceProvider | None = None,
    ) -> VoiceResponse:
        """
        Convert text to speech.

        Args:
            text: Text to convert
            voice: Voice to use (provider-specific)
            speed: Speech speed (0.25 to 4.0)
            format: Output audio format
            provider: Provider to use (default from init)

        Returns:
            VoiceResponse with audio data
        """
        provider = provider or self.default_provider
        voice = voice or self.DEFAULT_VOICES.get(provider, "alloy")

        request = TTSRequest(
            text=text, voice=voice, speed=speed, format=format, provider=provider
        )
        request.validate()

        logger.info(
            "tts_start",
            provider=provider.value,
            voice=voice,
            text_length=len(text),
        )

        try:
            if provider == VoiceProvider.OPENAI:
                return await self._tts_openai(request)
            elif provider == VoiceProvider.ELEVENLABS:
                return await self._tts_elevenlabs(request)
            elif provider == VoiceProvider.GOOGLE:
                return await self._tts_google(request)
            else:
                raise ValueError(f"Unsupported TTS provider: {provider}")

        except Exception as e:
            logger.error("tts_error", provider=provider.value, error=str(e))
            raise

    async def _tts_openai(self, request: TTSRequest) -> VoiceResponse:
        """
        Generate speech using OpenAI TTS.

        Args:
            request: Audio generation parameters.

        Returns:
            VoiceResponse: Generated audio data and metadata.
        """
        if not self._openai_key:
            raise ValueError("OpenAI API key not configured")

        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise ImportError("openai package required: pip install openai") from None

        client = AsyncOpenAI(api_key=self._openai_key)

        response = await client.audio.speech.create(
            model=request.model,
            voice=request.voice,  # type: ignore
            input=request.text,
            speed=request.speed,
            response_format=request.format.value,  # type: ignore[arg-type]
        )

        audio_bytes = response.content

        return VoiceResponse(
            success=True,
            content=audio_bytes,
            provider="openai",
            model=request.model,
            metadata={"voice": request.voice, "format": request.format.value},
        )

    async def _tts_elevenlabs(self, request: TTSRequest) -> VoiceResponse:
        """
        Generate high-quality speech using ElevenLabs API.

        Args:
            request: Audio generation parameters.

        Returns:
            VoiceResponse: Generated audio data.
        """
        if not self._elevenlabs_key:
            raise ValueError("ElevenLabs API key not configured")

        try:
            import httpx
        except ImportError:
            raise ImportError("httpx package required") from None

        voice_cfg = get_voice_config()
        model_id = voice_cfg.elevenlabs_model_id

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{request.voice}",
                headers={
                    "xi-api-key": self._elevenlabs_key,
                    "Content-Type": "application/json",
                },
                json={
                    "text": request.text,
                    "model_id": model_id,
                    "voice_settings": {
                        "stability": voice_cfg.elevenlabs_stability,
                        "similarity_boost": voice_cfg.elevenlabs_similarity_boost,
                    },
                },
                timeout=60.0,
            )
            response.raise_for_status()
            audio_bytes = response.content

        return VoiceResponse(
            success=True,
            content=audio_bytes,
            provider="elevenlabs",
            model=model_id,
            metadata={"voice": request.voice},
        )

    async def _tts_google(self, request: TTSRequest) -> VoiceResponse:
        """
        Generate speech using Google Cloud Text-to-Speech.

        Args:
            request: Audio generation parameters.

        Returns:
            VoiceResponse: Generated audio data.
        """
        try:
            import httpx
        except ImportError:
            raise ImportError("httpx package required") from None

        # Google Cloud TTS API
        google_api_key = get_voice_config().google_api_key
        if not google_api_key:
            raise ValueError("Google API key not configured")

        # Parse voice name for language code
        language_code = "en-US"
        if "-" in request.voice:
            parts = request.voice.split("-")
            if len(parts) >= 2:
                language_code = f"{parts[0]}-{parts[1]}"

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://texttospeech.googleapis.com/v1/text:synthesize",
                params={"key": google_api_key},
                json={
                    "input": {"text": request.text},
                    "voice": {
                        "languageCode": language_code,
                        "name": request.voice,
                    },
                    "audioConfig": {
                        "audioEncoding": "MP3",
                        "speakingRate": request.speed,
                    },
                },
                timeout=60.0,
            )
            response.raise_for_status()
            data = response.json()

        import base64

        audio_bytes = base64.b64decode(data["audioContent"])

        return VoiceResponse(
            success=True,
            content=audio_bytes,
            provider="google",
            model="wavenet",
            metadata={"voice": request.voice, "language": language_code},
        )

    # -------------------------------------------------------------------------
    # Speech-to-Text
    # -------------------------------------------------------------------------

    async def speech_to_text(
        self,
        audio_data: bytes | None = None,
        audio_file: str | None = None,
        language: str | None = None,
        provider: VoiceProvider | None = None,
    ) -> VoiceResponse:
        """
        Convert speech to text using the selected provider.

        Args:
            audio_data: Raw audio bytes.
            audio_file: Path to an audio file on disk.
            language: ISO language code (e.g., 'en', 'it').
            provider: Explicit provider override.

        Returns:
            VoiceResponse: Transcribed text and provider info.
        """
        provider = provider or self.default_provider

        request = STTRequest(
            audio_data=audio_data,
            audio_file=audio_file,
            language=language,
            provider=provider,
        )
        request.validate()

        logger.info(
            "stt_start",
            provider=provider.value,
            has_file=bool(audio_file),
            language=language,
        )

        try:
            if provider == VoiceProvider.OPENAI:
                return await self._stt_openai(request)
            elif provider == VoiceProvider.GOOGLE:
                return await self._stt_google(request)
            else:
                raise ValueError(f"Unsupported STT provider: {provider}")

        except Exception as e:
            logger.error("stt_error", provider=provider.value, error=str(e))
            raise

    async def _stt_openai(self, request: STTRequest) -> VoiceResponse:
        """
        Transcribe audio using OpenAI Whisper model.

        Args:
            request: Transcription parameters.

        Returns:
            VoiceResponse: Transcribed text.
        """
        if not self._openai_key:
            raise ValueError("OpenAI API key not configured")

        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise ImportError("openai package required: pip install openai") from None

        client = AsyncOpenAI(api_key=self._openai_key)

        # Get audio data
        audio_bytes = request.get_audio_bytes()

        # Whisper needs a file-like object with a name
        import io

        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = "audio.mp3"

        transcript = await client.audio.transcriptions.create(
            model=request.model,
            file=audio_file,  # type: ignore
            language=request.language,  # type: ignore[arg-type]
        )

        return VoiceResponse(
            success=True,
            content=transcript.text,
            provider="openai",
            model=request.model,
            language=request.language,
        )

    async def _stt_google(self, request: STTRequest) -> VoiceResponse:
        """
        Transcribe audio using Google Cloud Speech-to-Text.

        Args:
            request: Transcription parameters.

        Returns:
            VoiceResponse: Transcribed text.
        """
        try:
            import httpx
            import base64
        except ImportError:
            raise ImportError("httpx package required") from None

        google_api_key = get_voice_config().google_api_key
        if not google_api_key:
            raise ValueError("Google API key not configured")

        audio_bytes = request.get_audio_bytes()
        audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://speech.googleapis.com/v1/speech:recognize",
                params={"key": google_api_key},
                json={
                    "config": {
                        "encoding": "MP3",
                        "languageCode": request.language or "en-US",
                        "enableAutomaticPunctuation": True,
                    },
                    "audio": {"content": audio_b64},
                },
                timeout=60.0,
            )
            response.raise_for_status()
            data = response.json()

        # Extract transcript
        results = data.get("results", [])
        transcript = " ".join(
            r["alternatives"][0]["transcript"] for r in results if r.get("alternatives")
        )

        return VoiceResponse(
            success=True,
            content=transcript,
            provider="google",
            model="speech-v1",
            language=request.language,
        )

    # -------------------------------------------------------------------------
    # Convenience Methods
    # -------------------------------------------------------------------------

    async def speak(self, text: str, voice: str = "alloy") -> bytes:
        """Simple TTS that returns audio bytes."""
        response = await self.text_to_speech(text, voice=voice)
        return response.audio_bytes

    async def transcribe(self, audio_file: str) -> str:
        """Simple STT that returns text."""
        response = await self.speech_to_text(audio_file=audio_file)
        return response.text

    async def speak_and_save(
        self, text: str, output_path: str, voice: str = "alloy"
    ) -> str:
        """Generate speech and save to file."""
        response = await self.text_to_speech(text, voice=voice)
        response.save_audio(output_path)
        return output_path
