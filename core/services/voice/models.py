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
