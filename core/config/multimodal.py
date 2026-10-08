"""
Configuration schemas for the multimodal and adaptation services.

Covers Vision (image understanding / OCR), Voice (TTS + STT) and the
fine-tuning pipelines. Split out of :mod:`core.config.services`, which
re-exports every name here — import from ``core.config`` as usual.
"""

import logging
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# NOTE: Using direct logging.getLogger() here instead of core.observability.logging.get_logger()
# This is intentional: config modules initialize during framework bootstrap, before the
# observability infrastructure is fully set up. Direct logging prevents circular dependencies.
logger = logging.getLogger(__name__)


class VisionConfig(BaseSettings):
    """
    Configuration for multimodal Vision services (OCR, Image analysis).
    """

    model_config = SettingsConfigDict(
        env_prefix="VISION_",
        case_sensitive=False,
        extra="ignore",
    )

    provider: Literal["openai", "anthropic", "google", "ollama"] = Field(
        default="openai", description="Default vision capabilities provider"
    )

    openai_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("VISION_OPENAI_API_KEY", "OPENAI_API_KEY"),
    )
    # Each provider credential accepts the prefixed name first and the bare
    # vendor name as a fallback. A plain ``alias`` would REPLACE the
    # ``env_prefix``, so ``VISION_ANTHROPIC_API_KEY`` — the name the template
    # and the docs advertise — would silently bind nothing.
    anthropic_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("VISION_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
    )
    google_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("VISION_GOOGLE_API_KEY", "GOOGLE_API_KEY"),
    )
    ollama_url: str = Field(
        default="http://localhost:11434",
        validation_alias=AliasChoices("VISION_OLLAMA_HOST", "OLLAMA_HOST"),
    )

    # Per-provider vision model identifiers. Overridable via env
    # (VISION_OPENAI_MODEL, VISION_ANTHROPIC_MODEL, VISION_GOOGLE_MODEL,
    # VISION_OLLAMA_MODEL) so deployments are not pinned to a hardcoded model.
    openai_model: str = Field(
        default="gpt-4o", description="OpenAI vision model identifier."
    )
    anthropic_model: str = Field(
        default="claude-opus-5",
        description="Anthropic vision model identifier.",
    )
    google_model: str = Field(
        default="gemini-2.0-flash", description="Google vision model identifier."
    )
    ollama_model: str = Field(
        default="llava",
        description="Ollama vision model tag (e.g. 'llava', 'llava:7b', 'llama3.2-vision').",
    )

    # Native audio needs a distinct OpenAI model: the vision model (gpt-4o)
    # cannot take 'input_audio' content parts. Documents reuse the existing
    # per-provider vision models (Anthropic/Google accept PDFs on the same
    # models), so no per-provider document fields are needed.
    openai_audio_model: str = Field(
        default="gpt-4o-audio-preview",
        description=(
            "OpenAI audio-capable chat model for native audio analysis "
            "(env VISION_OPENAI_AUDIO_MODEL)."
        ),
    )


class VoiceConfig(BaseSettings):
    """
    Configuration for Voice/Audio synthesis and recognition.
    """

    model_config = SettingsConfigDict(
        env_prefix="VOICE_",
        case_sensitive=False,
        extra="ignore",
    )

    provider: Literal["openai", "elevenlabs", "google", "openai_compatible"] = Field(
        default="openai",
        description=(
            "Default voice provider. 'openai_compatible' talks to any server that speaks "
            "the OpenAI audio API (faster-whisper-server, Piper/Kokoro wrappers) at "
            "VOICE_STT_BASE_URL / VOICE_TTS_BASE_URL, with no external provider key."
        ),
    )

    # OpenAI-compatible mode (local sidecars). Base URLs are the API root, /v1 included.
    stt_base_url: str | None = Field(
        default=None,
        description="OpenAI-compatible STT root, e.g. http://whisper:8000/v1 (VOICE_STT_BASE_URL).",
    )
    tts_base_url: str | None = Field(
        default=None,
        description="OpenAI-compatible TTS root, e.g. http://tts:8000/v1 (VOICE_TTS_BASE_URL).",
    )
    compat_api_key: SecretStr | None = Field(
        default=None,
        description="Optional bearer key for the OpenAI-compatible servers (most sidecars are keyless).",
    )
    stt_model: str = Field(
        default="whisper-1", description="STT model name sent to the server."
    )
    tts_model: str = Field(
        default="tts-1", description="TTS model name sent to the server."
    )
    tts_voice: str = Field(
        default="alloy", description="Default TTS voice for compatible servers."
    )
    allow_internal_endpoints: bool = Field(
        default=False,
        description=(
            "Allow the compatible endpoints to sit on private, loopback or link-local "
            "addresses (a sidecar on the same host or network). Off by default: the SSRF "
            "guard refuses them."
        ),
    )
    max_audio_bytes: int = Field(
        default=10 * 1024 * 1024,
        ge=1024,
        description="Largest audio upload sent to STT.",
    )
    max_response_bytes: int = Field(
        default=20 * 1024 * 1024,
        ge=1024,
        description="Largest response body read from a server.",
    )
    max_tts_chars: int = Field(
        default=4096,
        ge=1,
        le=100_000,
        description="Longest text accepted for synthesis.",
    )
    timeout_seconds: float = Field(
        default=30.0, gt=0.0, le=300.0, description="Deadline of one voice request."
    )

    @field_validator("stt_base_url", "tts_base_url")
    @classmethod
    def _http_url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        parts = urlsplit(value.strip())
        if (
            parts.scheme not in ("http", "https")
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
        ):
            raise ValueError(
                "must be an http(s) URL without credentials, query or fragment"
            )
        return value.strip().rstrip("/")

    openai_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("VOICE_OPENAI_API_KEY", "OPENAI_API_KEY"),
    )
    # Prefixed name first, bare vendor name as fallback — see VisionConfig.
    elevenlabs_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("VOICE_ELEVENLABS_API_KEY", "ELEVENLABS_API_KEY"),
    )
    google_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("VOICE_GOOGLE_API_KEY", "GOOGLE_API_KEY"),
    )
    google_credentials_path: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "VOICE_GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_APPLICATION_CREDENTIALS"
        ),
    )

    # ElevenLabs (native provider, VOICE_PROVIDER=elevenlabs). The key above is the
    # credential; everything below is what it is pointed at and how it speaks.
    elevenlabs_base_url: str = Field(
        default="https://api.elevenlabs.io",
        description=(
            "ElevenLabs API root (VOICE_ELEVENLABS_BASE_URL). https only; the egress guard "
            "allows exactly this host and follows no redirect. Change it for a data-residency "
            "or enterprise endpoint (e.g. https://api.eu.residency.elevenlabs.io)."
        ),
    )
    elevenlabs_tts_model: str = Field(
        default="eleven_flash_v2_5",
        validation_alias=AliasChoices(
            "VOICE_ELEVENLABS_TTS_MODEL", "VOICE_ELEVENLABS_MODEL_ID"
        ),
        description=(
            "ElevenLabs text-to-speech model. eleven_flash_v2_5 is the default: low latency "
            "and half the credit cost, multilingual (Italian included), which suits a voice "
            "assistant that speaks sentence by sentence. eleven_multilingual_v2 trades "
            "latency for richer prosody; eleven_v3 is the most expressive."
        ),
    )
    elevenlabs_stt_model: str = Field(
        default="scribe_v1",
        description="ElevenLabs speech-to-text (Scribe) model (VOICE_ELEVENLABS_STT_MODEL).",
    )
    elevenlabs_voice_id: str = Field(
        default="21m00Tcm4TlvDq8ikWAM",
        description=(
            "Default ElevenLabs voice id (VOICE_ELEVENLABS_VOICE_ID); the premade 'Rachel', "
            "which every account can use."
        ),
    )
    elevenlabs_output_format: Literal[
        "mp3_22050_32",
        "mp3_24000_48",
        "mp3_44100_32",
        "mp3_44100_64",
        "mp3_44100_96",
        "mp3_44100_128",
        "mp3_44100_192",
    ] = Field(
        default="mp3_44100_128",
        description=(
            "ElevenLabs output_format (VOICE_ELEVENLABS_OUTPUT_FORMAT). MP3 only: that is "
            "what browsers play; mp3_44100_192 needs a paid tier."
        ),
    )
    elevenlabs_stability: float = Field(
        default=0.5, ge=0.0, le=1.0, description="ElevenLabs voice stability"
    )
    elevenlabs_similarity_boost: float = Field(
        default=0.75, ge=0.0, le=1.0, description="ElevenLabs similarity boost"
    )
    elevenlabs_style: float = Field(
        default=0.0, ge=0.0, le=1.0, description="ElevenLabs style exaggeration"
    )
    elevenlabs_retries: int = Field(
        default=2,
        ge=0,
        le=4,
        description="Retries after an HTTP 429 (bounded by the request deadline).",
    )

    @model_validator(mode="after")
    def _elevenlabs_endpoint(self) -> "VoiceConfig":
        parts = urlsplit(self.elevenlabs_base_url.strip())
        local_http = parts.scheme == "http" and self.allow_internal_endpoints
        if (
            (parts.scheme != "https" and not local_http)
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
        ):
            raise ValueError(
                "VOICE_ELEVENLABS_BASE_URL must be an https URL without credentials, "
                "query or fragment (http only with VOICE_ALLOW_INTERNAL_ENDPOINTS)"
            )
        self.elevenlabs_base_url = self.elevenlabs_base_url.strip().rstrip("/")
        return self

    # Model used for caching voice samples by content similarity.
    embedding_model: str = Field(
        default="all-MiniLM-L6-v2",
        description="Sentence-transformer model for semantic voice cache",
    )


class FineTuningConfig(BaseSettings):
    """
    Configuration for model fine-tuning and adaptation pipelines.
    """

    model_config = SettingsConfigDict(
        env_prefix="FINETUNE_",
        case_sensitive=False,
        extra="ignore",
    )

    # Prefixed name first: without it these bound the bare vendor keys, so a
    # fine-tuning job silently reused the chat provider's credentials.
    openai_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("FINETUNE_OPENAI_API_KEY", "OPENAI_API_KEY"),
    )
    together_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("FINETUNE_TOGETHER_API_KEY", "TOGETHER_API_KEY"),
    )


_vision_config: VisionConfig | None = None
_voice_config: VoiceConfig | None = None
_finetuning_config: FineTuningConfig | None = None


def get_vision_config() -> VisionConfig:
    """Retrieve or initialize the global Vision configuration."""
    global _vision_config
    if _vision_config is None:
        _vision_config = VisionConfig()
    return _vision_config


def get_voice_config() -> VoiceConfig:
    """Retrieve or initialize the global Voice configuration."""
    global _voice_config
    if _voice_config is None:
        _voice_config = VoiceConfig()
    return _voice_config


def get_finetuning_config() -> FineTuningConfig:
    """Retrieve or initialize the global Fine-tuning configuration."""
    global _finetuning_config
    if _finetuning_config is None:
        _finetuning_config = FineTuningConfig()
    return _finetuning_config


__all__ = [
    "FineTuningConfig",
    "VisionConfig",
    "VoiceConfig",
    "get_finetuning_config",
    "get_vision_config",
    "get_voice_config",
]
