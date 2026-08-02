"""
Configuration schemas for the multimodal and adaptation services.

Covers Vision (image understanding / OCR), Voice (TTS + STT) and the
fine-tuning pipelines. Split out of :mod:`core.config.services`, which
re-exports every name here — import from ``core.config`` as usual.
"""

import logging
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
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
    anthropic_api_key: SecretStr | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    google_api_key: SecretStr | None = Field(default=None, alias="GOOGLE_API_KEY")
    ollama_url: str = Field(default="http://localhost:11434", alias="OLLAMA_HOST")

    # Per-provider vision model identifiers. Overridable via env
    # (VISION_OPENAI_MODEL, VISION_ANTHROPIC_MODEL, VISION_GOOGLE_MODEL,
    # VISION_OLLAMA_MODEL) so deployments are not pinned to a hardcoded model.
    openai_model: str = Field(
        default="gpt-4o", description="OpenAI vision model identifier."
    )
    anthropic_model: str = Field(
        default="claude-3-5-sonnet-20241022",
        description="Anthropic vision model identifier.",
    )
    google_model: str = Field(
        default="gemini-2.0-flash", description="Google vision model identifier."
    )
    ollama_model: str = Field(
        default="llava",
        description="Ollama vision model tag (e.g. 'llava', 'llava:7b', 'llama3.2-vision').",
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

    provider: Literal["openai", "elevenlabs", "google"] = Field(
        default="openai", description="Default voice synthesis provider"
    )

    openai_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("VOICE_OPENAI_API_KEY", "OPENAI_API_KEY"),
    )
    elevenlabs_api_key: SecretStr | None = Field(
        default=None, alias="ELEVENLABS_API_KEY"
    )
    google_api_key: SecretStr | None = Field(default=None, alias="GOOGLE_API_KEY")
    google_credentials_path: str | None = Field(
        default=None, alias="GOOGLE_APPLICATION_CREDENTIALS"
    )

    # ElevenLabs specific voice tuning.
    elevenlabs_model_id: str = Field(
        default="eleven_multilingual_v2",
        description="ElevenLabs model ID for TTS",
    )
    elevenlabs_stability: float = Field(
        default=0.5, ge=0.0, le=1.0, description="ElevenLabs voice stability"
    )
    elevenlabs_similarity_boost: float = Field(
        default=0.75, ge=0.0, le=1.0, description="ElevenLabs similarity boost"
    )

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

    openai_api_key: SecretStr | None = Field(default=None, alias="OPENAI_API_KEY")
    together_api_key: SecretStr | None = Field(default=None, alias="TOGETHER_API_KEY")


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
