"""
Voice Models.

Data models for voice service requests and responses.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Literal


class VoiceProvider(str, Enum):
    """Supported voice providers."""

    OPENAI = "openai"
    ELEVENLABS = "elevenlabs"
    GOOGLE = "google"
    LOCAL = "local"  # For local TTS/STT engines
    OPENAI_COMPATIBLE = "openai_compatible"  # OpenAI audio API on any server


class VoiceModel(str, Enum):
    """Available voice models."""

    # OpenAI
    OPENAI_TTS_1 = "tts-1"
    OPENAI_TTS_1_HD = "tts-1-hd"
    OPENAI_WHISPER = "whisper-1"

    # ElevenLabs
    ELEVEN_MULTILINGUAL_V2 = "eleven_multilingual_v2"
    ELEVEN_TURBO_V2 = "eleven_turbo_v2"

    # Google
    GOOGLE_STANDARD = "standard"
    GOOGLE_WAVENET = "wavenet"
    GOOGLE_NEURAL2 = "neural2"


class OpenAIVoice(str, Enum):
    """OpenAI TTS voices."""

    ALLOY = "alloy"
    ECHO = "echo"
    FABLE = "fable"
    ONYX = "onyx"
    NOVA = "nova"
    SHIMMER = "shimmer"


class AudioFormat(str, Enum):
    """Supported audio formats."""

    MP3 = "mp3"
    WAV = "wav"
    OGG = "ogg"
    FLAC = "flac"
    PCM = "pcm"


@dataclass
class TTSRequest:
    """Text-to-Speech request."""

    text: str
    voice: str = "alloy"  # Default OpenAI voice
    model: str = "tts-1"
    speed: float = 1.0  # 0.25 to 4.0
    format: AudioFormat = AudioFormat.MP3
    provider: VoiceProvider = VoiceProvider.OPENAI

    def validate(self) -> None:
        """Validate request parameters."""
        if not self.text:
            raise ValueError("Text cannot be empty")
        if not 0.25 <= self.speed <= 4.0:
            raise ValueError("Speed must be between 0.25 and 4.0")


@dataclass
class STTRequest:
    """Speech-to-Text request."""

    audio_data: bytes | None = None
    audio_file: str | None = None
    language: str | None = None  # Auto-detect if None
    model: str = "whisper-1"
    response_format: Literal["text", "json", "verbose_json"] = "text"
    provider: VoiceProvider = VoiceProvider.OPENAI
    timestamp_granularity: Literal["word", "segment"] | None = None

    def validate(self) -> None:
        """Validate request parameters."""
        if not self.audio_data and not self.audio_file:
            raise ValueError("Either audio_data or audio_file must be provided")

    def get_audio_bytes(self) -> bytes:
        """
        Retrieve the raw audio data from the request.

        If audio_data is provided directly, it is returned. Otherwise,
        it attempts to read from audio_file.

        Returns:
            bytes: Raw audio binary data.

        Raises:
            ValueError: If neither data nor file path is provided.
        """
        if self.audio_data:
            return self.audio_data
        if self.audio_file:
            with open(self.audio_file, "rb") as f:
                return f.read()
        raise ValueError("No audio data available")


@dataclass
class VoiceResponse:
    """Response from voice operations."""

    success: bool
    content: str | bytes  # Text for STT, audio bytes for TTS
    provider: str
    model: str
    duration_seconds: float = 0.0
    language: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        """
        The transcribed text content.

        Returns:
            str: Decoded transcription.
        """
        if isinstance(self.content, str):
            return self.content
        return self.content.decode("utf-8")

    @property
    def audio_bytes(self) -> bytes:
        """
        The raw generated audio bytes.

        Returns:
            bytes: PCM or encoded audio data.
        """
        if isinstance(self.content, bytes):
            return self.content
        return self.content.encode("utf-8")

    def save_audio(self, path: str | Path) -> None:
        """
        Write the audio content to a local file.

        Args:
            path: Destination file path.
        """
        with open(path, "wb") as f:
            f.write(self.audio_bytes)

    @property
    def audio_base64(self) -> str:
        """
        The audio content encoded as a base64 string.

        Useful for embedding in JSON responses or HTML.

        Returns:
            str: Base64 representation of the audio bytes.
        """
        return base64.b64encode(self.audio_bytes).decode("utf-8")


@dataclass
class VoiceRequest:
    """Generic voice request (wraps TTS or STT)."""

    request_type: Literal["tts", "stt"]
    tts_request: TTSRequest | None = None
    stt_request: STTRequest | None = None

    @classmethod
    def tts(
        cls,
        text: str,
        voice: str = "alloy",
        speed: float = 1.0,
        provider: VoiceProvider = VoiceProvider.OPENAI,
    ) -> VoiceRequest:
        """Create a TTS request."""
        return cls(
            request_type="tts",
            tts_request=TTSRequest(
                text=text, voice=voice, speed=speed, provider=provider
            ),
        )

    @classmethod
    def stt(
        cls,
        audio_data: bytes | None = None,
        audio_file: str | None = None,
        language: str | None = None,
        provider: VoiceProvider = VoiceProvider.OPENAI,
    ) -> VoiceRequest:
        """Create an STT request."""
        return cls(
            request_type="stt",
            stt_request=STTRequest(
                audio_data=audio_data,
                audio_file=audio_file,
                language=language,
                provider=provider,
            ),
        )


@dataclass(frozen=True, slots=True)
class VoiceInfo:
    """One synthesis voice a provider offers the account.

    Attributes:
        id: The provider's voice id (what ``text_to_speech(voice=...)`` takes).
        name: Display name.
        languages: Lower-case ISO 639-1 codes the voice is verified for (may be empty).
        category: The provider's own class (``premade``, ``cloned``...), or ``""``.
    """

    id: str
    name: str
    languages: tuple[str, ...] = ()
    category: str = ""


@dataclass(frozen=True, slots=True)
class VoiceOverrides:
    """Settings a host layers over ``VOICE_*`` at call time (an admin console, say).

    The host passes a callable returning this (or None) to :class:`VoiceService`, so a
    change applies on the next call without a restart. The environment stays the
    ceiling for credentials: a key set in ``VOICE_ELEVENLABS_API_KEY`` is never
    replaced by ``elevenlabs_api_key`` here.

    Attributes:
        provider: ``"elevenlabs"``, ``"sidecar"`` (the OpenAI-compatible family) or None
            (follow ``VOICE_PROVIDER``).
        elevenlabs_api_key: Used only while the environment sets no key.
        elevenlabs_voice_id: Default voice, over ``VOICE_ELEVENLABS_VOICE_ID``.
        elevenlabs_tts_model: Synthesis model, over ``VOICE_ELEVENLABS_TTS_MODEL``.
    """

    provider: str | None = None
    elevenlabs_api_key: str | None = None
    elevenlabs_voice_id: str | None = None
    elevenlabs_tts_model: str | None = None


@dataclass(frozen=True, slots=True)
class VoiceProbe:
    """The answer of a cheap authenticated check of a provider key.

    Attributes:
        ok: The key works and the account can still synthesize.
        code: ``ok`` or a stable error code (``key_invalid``, ``permission_missing``,
            ``quota_exceeded``, ``rate_limited``, ``provider_unavailable``,
            ``not_configured``, ``timeout``, ``egress_blocked``).
        characters_used: Characters spent this billing period, when the key may read it.
        characters_limit: The period's allowance, when known.
    """

    ok: bool
    code: str
    characters_used: int | None = None
    characters_limit: int | None = None


@dataclass(frozen=True, slots=True)
class SpeechContext:
    """What surrounds one synthesised passage, so consecutive passages sound like one voice.

    Providers that support request stitching (ElevenLabs, except ``eleven_v3``) use it to
    carry prosody across the boundary; others ignore it. Everything here is advisory: a
    provider that cannot use a field simply drops it.

    Attributes:
        previous_text: Text spoken just before this passage.
        next_text: Text that will be spoken right after it, when already known.
        previous_request_ids: Provider request ids of the passages just before (at most
            three are used); they take precedence over ``previous_text``.
        normalization: ``auto``, ``on`` or ``off``: the provider's own text normalisation.
    """

    previous_text: str | None = None
    next_text: str | None = None
    previous_request_ids: tuple[str, ...] = ()
    normalization: Literal["auto", "on", "off"] | None = None


@dataclass(frozen=True, slots=True)
class SpeechTuning:
    """Preferred synthesis settings for one call, used where the operator chose none.

    A value set explicitly by the operator (``VOICE_ELEVENLABS_*`` in the environment, or a
    host override) always wins over the one here; see ``VoiceService.elevenlabs_explicit``.
    """

    model: str | None = None
    stability: float | None = None
    similarity_boost: float | None = None
    style: float | None = None
