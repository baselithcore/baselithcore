"""Guardrails configuration (``GUARDRAILS_``).

The live input/output guards and the moderation layer take a
:class:`core.guardrails.config.GuardrailsConfig`. That dataclass has fixed
defaults and nothing built it from the environment, so a topical rail
(``allowed_topics``), custom block patterns or a different length cap could
only be set by code — and the ``out_of_scope`` verdict could never fire in the
running app. This settings class is the environment surface; the dataclass
stays the value object the guards consume.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from core.config._collections import csv_list


class GuardrailsSettings(BaseSettings):
    """Environment-driven settings for the input/output guards."""

    model_config = SettingsConfigDict(
        env_prefix="GUARDRAILS_",
        case_sensitive=False,
        extra="ignore",
    )

    input_enabled: bool = Field(
        default=True, description="Run the input guard on every query"
    )
    max_input_length: int = Field(
        default=10000, ge=1, description="Longest query accepted, in characters"
    )
    block_injection_patterns: bool = Field(
        default=True, description="Block known prompt-injection phrasings"
    )
    block_code_execution: bool = Field(
        default=True, description="Block code-execution payloads in queries"
    )
    custom_block_patterns: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        description="Extra case-insensitive regexes that block a query "
        "(comma-separated or a JSON array)",
    )
    allowed_topics: str | None = Field(
        default=None,
        description="Free-text description of the in-scope domain. Set, the "
        "LLM input taxonomy may rule a query out_of_scope; unset, it never "
        "does",
    )
    output_enabled: bool = Field(
        default=True, description="Run the output guard on every response"
    )
    filter_pii: bool = Field(default=True, description="Redact PII from responses")
    filter_harmful_content: bool = Field(
        default=True, description="Filter harmful content from responses"
    )
    max_output_length: int = Field(
        default=50000, ge=1, description="Longest response returned, in characters"
    )
    moderation_enabled: bool = Field(
        default=True,
        description="Allow the content-moderation layer; it still needs "
        "BASELITH_MODERATION_PROVIDER to do anything",
    )
    moderation_threshold: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Moderation score at or above which content is flagged",
    )

    @field_validator("custom_block_patterns", mode="before")
    @classmethod
    def _split_patterns(cls, value: Any) -> Any:
        return csv_list(value)


_guardrails_settings: GuardrailsSettings | None = None


def get_guardrails_settings() -> GuardrailsSettings:
    """Get or create the global guardrails settings instance."""
    global _guardrails_settings
    if _guardrails_settings is None:
        _guardrails_settings = GuardrailsSettings()
    return _guardrails_settings


__all__ = ["GuardrailsSettings", "get_guardrails_settings"]
