"""
Voice Service Implementation.

Multi-provider voice service supporting TTS (Text-to-Speech) and
STT (Speech-to-Text) operations.

Usage:
    from baselith.core.services.voice import VoiceService, TTSRequest, STTRequest

    service = VoiceService()

    # Text-to-Speech
    audio = await service.text_to_speech("Hello, world!")
    audio.save_audio("output.mp3")

    # Speech-to-Text
    text = await service.speech_to_text(audio_file="recording.mp3")
    print(text.text)
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

from baselith.core.config import get_voice_config
from baselith.core.observability.logging import get_logger
from baselith.core.services.voice import _usage
from baselith.core.services.voice._audio_input import load_audio
from baselith.core.services.voice._elevenlabs import ElevenLabsClient
from baselith.core.services.voice._elevenlabs_calls import ElevenLabsCalls
from baselith.core.services.voice._http_providers import HttpProviders
from baselith.core.services.voice._openai_sdk import OpenAISdkCalls
from baselith.core.services.voice._openai_wire import OpenAIWireClient
from baselith.core.services.voice._wire_calls import WireCalls
from baselith.core.services.voice.errors import VoiceLimitError
from baselith.core.services.voice.models import (
    AudioFormat,
    SpeechContext,
    SpeechTuning,
    STTRequest,
    TTSRequest,
    VoiceOverrides,
    VoiceProvider,
    VoiceResponse,
    VoiceStream,
)

logger = get_logger(__name__)


class VoiceService(WireCalls, ElevenLabsCalls, OpenAISdkCalls, HttpProviders):
    """
    Multi-provider voice service.

    Supports:
    - OpenAI TTS/Whisper
    - ElevenLabs TTS and Scribe STT (native; ``VOICE_PROVIDER=elevenlabs``)
    - Google Cloud TTS/STT
    - Any OpenAI-compatible audio server (``VOICE_PROVIDER=openai_compatible``)

    Built with ``plugin=`` the service obeys the central per-plugin LLM policy
    pin (see :mod:`baselith.core.services.voice._routing`). Failures of the compatible
    and governed paths are :class:`baselith.core.services.voice.errors.VoiceError`.
    """

    # Default voices per provider
    DEFAULT_VOICES = {
        VoiceProvider.OPENAI: "alloy",
        VoiceProvider.GOOGLE: "en-US-Wavenet-D",
        VoiceProvider.OPENAI_COMPATIBLE: "alloy",
    }

    def _config(self) -> Any:
        return get_voice_config()

    def __init__(
        self,
        default_provider: VoiceProvider | None = None,
        openai_api_key: str | None = None,
        elevenlabs_api_key: str | None = None,
        google_credentials_path: str | None = None,
        plugin: str | None = None,
        scope: str | None = "voice",
        overrides: Callable[[], VoiceOverrides | None] | None = None,
    ) -> None:
        """
        Initialize voice service.

        Args:
            default_provider: Default provider for voice operations. If None,
                reads from VoiceConfig.provider (env VOICE_PROVIDER).
            openai_api_key: OpenAI API key (or from env OPENAI_API_KEY)
            elevenlabs_api_key: ElevenLabs API key (or from env ELEVENLABS_API_KEY)
            google_credentials_path: Path to Google credentials JSON
            plugin: Plugin the service acts for; its central LLM pin (scope
                ``scope``) then wins over ``VOICE_*`` credentials.
            scope: Named LLM sub-policy to resolve for ``plugin``.
            overrides: Called on every call for the host's live settings (provider choice,
                an ElevenLabs key/voice/model); see :class:`VoiceOverrides`. The
                environment's ElevenLabs key always wins over the override's.
        """
        self._plugin = plugin
        self._scope = scope
        self._wire: OpenAIWireClient | None = None
        self._eleven: ElevenLabsClient | None = None
        self._overrides_fn = overrides
        _voice_cfg = get_voice_config()
        self._base_provider = VoiceProvider(default_provider or _voice_cfg.provider)
        self._openai_key = openai_api_key or (
            _voice_cfg.openai_api_key.get_secret_value()
            if _voice_cfg.openai_api_key
            else None
        )
        # A blank key (``VOICE_ELEVENLABS_API_KEY=`` in a copied template) is no key.
        self._elevenlabs_key = elevenlabs_api_key or (
            _voice_cfg.elevenlabs_api_key.get_secret_value()
            if _voice_cfg.elevenlabs_api_key
            else None
        )
        if self._elevenlabs_key is not None and not self._elevenlabs_key.strip():
            self._elevenlabs_key = None
        self._google_creds = (
            google_credentials_path or _voice_cfg.google_credentials_path
        )
        self._http_client: Any = None
        # Shared AsyncOpenAI client (lazy). A fresh client per TTS/STT call
        # leaked its httpx pool and inherited the SDK's 600s default timeout.
        self._openai_client: Any = None

        logger.info(
            "voice_service_initialized",
            default_provider=self._base_provider.value,
            openai_available=bool(self._openai_key),
            elevenlabs_available=bool(self._elevenlabs_key),
            google_available=bool(self._google_creds),
        )

    @property
    def default_provider(self) -> VoiceProvider:
        """The provider in force now: the host's override, else ``VOICE_PROVIDER``."""
        chosen = getattr(self._overrides(), "provider", None)
        if chosen == "elevenlabs":
            return VoiceProvider.ELEVENLABS
        if chosen == "sidecar" and self._base_provider not in (
            VoiceProvider.OPENAI,
            VoiceProvider.OPENAI_COMPATIBLE,
        ):
            return VoiceProvider.OPENAI_COMPATIBLE
        return self._base_provider

    def _get_http_client(self) -> Any:
        """Lazily create a shared HTTP client for provider calls.

        Returns ``Any`` rather than ``httpx.AsyncClient``: httpx is imported
        lazily inside the body (it is optional at this layer), so the concrete
        type is not available at module scope. Matches ``_get_openai_client``
        below.
        """
        if self._http_client is None:
            try:
                import httpx
            except ImportError:
                raise ImportError("httpx package required") from None

            self._http_client = httpx.AsyncClient(
                timeout=60.0,
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )
        return self._http_client

    async def aclose(self) -> None:
        """Close provider HTTP resources."""
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None
        if self._openai_client is not None:
            await self._openai_client.close()
            self._openai_client = None
        if self._wire is not None:
            await self._wire.aclose()
            self._wire = None
        if self._eleven is not None:
            await self._eleven.aclose()
            self._eleven = None

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
        *,
        language: str | None = None,
        context: SpeechContext | None = None,
        tuning: SpeechTuning | None = None,
    ) -> VoiceResponse:
        """
        Convert text to speech.

        Args:
            text: Text to convert
            voice: Voice to use (provider-specific)
            speed: Speech speed (0.25 to 4.0)
            format: Output audio format
            provider: Provider to use (default from init)
            language: ISO 639-1 language of the text (ElevenLabs models that take it).
            context: Neighbouring passages for continuity (ElevenLabs request stitching).
            tuning: Preferred model and voice settings for this call; the operator's
                explicit ``VOICE_ELEVENLABS_*`` values win (ElevenLabs only).

        Returns:
            VoiceResponse with audio data
        """
        provider = provider or self.default_provider
        if provider == VoiceProvider.ELEVENLABS:
            return await self._tts_elevenlabs_native(
                text,
                voice,
                speed,
                language=language,
                context=context,
                tuning=tuning,
            )
        route = self._route("tts", provider)
        if route is not None:
            return await self._tts_wire(route, text, voice, speed, format)
        voice = voice or self.DEFAULT_VOICES.get(provider, "alloy")

        request = TTSRequest(
            text=text, voice=voice, speed=speed, format=format, provider=provider
        )
        request.validate()
        # The wire and ElevenLabs paths check this themselves; the SDK and
        # Google paths did not, so VOICE_MAX_TTS_CHARS held for two providers
        # out of four.
        if len(text) > int(self._config().max_tts_chars):
            raise VoiceLimitError("text exceeds the synthesis cap")

        logger.info(
            "tts_start",
            provider=provider.value,
            voice=voice,
            text_length=len(text),
        )

        try:
            if provider == VoiceProvider.OPENAI:
                response = await self._tts_openai(request)
            elif provider == VoiceProvider.GOOGLE:
                response = await self._tts_google(request)
            else:
                raise ValueError(f"Unsupported TTS provider: {provider}")

        except Exception as e:
            logger.error("tts_error", provider=provider.value, error=str(e))
            raise
        _usage.report_tts(response.model, text)
        return response

    async def text_to_speech_stream(
        self,
        text: str,
        voice: str | None = None,
        speed: float = 1.0,
        provider: VoiceProvider | None = None,
        *,
        language: str | None = None,
        context: SpeechContext | None = None,
        tuning: SpeechTuning | None = None,
        output_format: str | None = None,
    ) -> VoiceStream:
        """Convert text to speech, the audio arriving as it is generated.

        ElevenLabs streams natively (its ``/stream`` endpoint; ``output_format`` names the
        provider format, ``pcm_24000`` for raw audio a player can schedule as it arrives, else
        ``VOICE_ELEVENLABS_OUTPUT_FORMAT``). Every other provider answers whole, as one chunk,
        so a caller can always consume a :class:`VoiceStream`. Same arguments as
        :meth:`text_to_speech`; a refusal is raised before the stream exists.
        """
        provider = provider or self.default_provider
        if provider == VoiceProvider.ELEVENLABS:
            return await self._tts_elevenlabs_stream(
                text,
                voice,
                speed,
                language=language,
                context=context,
                tuning=tuning,
                output_format=output_format,
            )
        response = await self.text_to_speech(
            text,
            voice,
            speed,
            provider=provider,
            language=language,
            context=context,
            tuning=tuning,
        )
        return _whole(response)

    # -------------------------------------------------------------------------
    # Speech-to-Text
    # -------------------------------------------------------------------------

    async def speech_to_text(
        self,
        audio_data: bytes | None = None,
        audio_file: str | None = None,
        language: str | None = None,
        provider: VoiceProvider | None = None,
        *,
        content_type: str | None = None,
        duration_seconds: float | None = None,
    ) -> VoiceResponse:
        """
        Convert speech to text using the selected provider.

        Args:
            audio_data: Raw audio bytes.
            audio_file: Path to an audio file on disk.
            language: ISO language code (e.g., 'en', 'it').
            provider: Explicit provider override.
            content_type: MIME type of ``audio_data`` (names the upload).
            duration_seconds: Audio length, for the usage estimate only.

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
        # One read, in a worker thread, under VOICE_MAX_AUDIO_BYTES, for every
        # provider — cached on the request so the provider paths below and the
        # usage report never read the file again.
        audio = await load_audio(request, int(self._config().max_audio_bytes))

        logger.info(
            "stt_start",
            provider=provider.value,
            has_file=bool(audio_file),
            audio_bytes=len(audio),
            language=language,
        )

        if provider == VoiceProvider.ELEVENLABS:
            return await self._stt_elevenlabs(request, content_type, duration_seconds)
        route = self._route("stt", provider)
        if route is not None:
            return await self._stt_wire(route, request, content_type, duration_seconds)
        try:
            if provider == VoiceProvider.OPENAI:
                response = await self._stt_openai(request)
            elif provider == VoiceProvider.GOOGLE:
                response = await self._stt_google(request)
            else:
                raise ValueError(f"Unsupported STT provider: {provider}")

        except Exception as e:
            logger.error("stt_error", provider=provider.value, error=str(e))
            raise
        _usage.report_stt(response.model, len(audio), response.text, duration_seconds)
        return response

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


def _whole(response: VoiceResponse) -> VoiceStream:
    """A whole answer as a one-chunk stream."""
    fmt = str(response.metadata.get("format") or "mp3")

    async def one() -> AsyncIterator[bytes]:
        yield response.audio_bytes

    return VoiceStream(
        chunks=one(),
        provider=response.provider,
        model=response.model,
        content_type="audio/mpeg" if fmt == "mp3" else f"audio/{fmt}",
        metadata=dict(response.metadata),
    )
