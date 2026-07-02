"""
Privacy / data-subject-request configuration.

Gates the DSR admin API and sets the default retention horizon used by sweeps.
Opt-in and default-off so it adds no surface until enabled.
"""

import logging

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class PrivacyConfig(BaseSettings):
    """Configuration for the privacy / DSR subsystem."""

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    enabled: bool = Field(default=False, alias="PRIVACY_ENABLED")
    # Default retention horizon for sweeps, in days. 0 disables automatic purge.
    retention_days: int = Field(default=0, alias="PRIVACY_RETENTION_DAYS", ge=0)


_privacy_config: PrivacyConfig | None = None


def get_privacy_config() -> PrivacyConfig:
    """Get or create the global privacy configuration instance."""
    global _privacy_config
    if _privacy_config is None:
        _privacy_config = PrivacyConfig()
    return _privacy_config
