"""
AI-transparency configuration (EU AI Act Article 50).

Gates the AI-interaction disclosure and configures the content-provenance tagger.
Opt-in and default-off so it adds no behaviour until enabled. The signing secret
is wrapped in :class:`~pydantic.SecretStr` so it never leaks via ``repr``/Sentry.
"""

import logging

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from core.transparency.disclosure import DEFAULT_DISCLOSURE_TEXT

logger = logging.getLogger(__name__)


class TransparencyConfig(BaseSettings):
    """Configuration for the Article 50 transparency subsystem."""

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    enabled: bool = Field(default=False, alias="TRANSPARENCY_ENABLED")
    disclosure_text: str = Field(
        default=DEFAULT_DISCLOSURE_TEXT, alias="TRANSPARENCY_DISCLOSURE_TEXT"
    )
    provider_name: str | None = Field(default=None, alias="TRANSPARENCY_PROVIDER_NAME")
    # Identifies the producing system in provenance tags (C2PA claim_generator).
    claim_generator: str = Field(
        default="BaselithCore", alias="TRANSPARENCY_CLAIM_GENERATOR"
    )
    # Optional HMAC secret; when set, provenance tags are signed and verifiable.
    signing_secret: SecretStr | None = Field(
        default=None, alias="TRANSPARENCY_SIGNING_SECRET"
    )


_transparency_config: TransparencyConfig | None = None


def get_transparency_config() -> TransparencyConfig:
    """Get or create the global transparency configuration instance."""
    global _transparency_config
    if _transparency_config is None:
        _transparency_config = TransparencyConfig()
    return _transparency_config
