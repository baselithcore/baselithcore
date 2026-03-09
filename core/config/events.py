"""
Events configuration.
"""

import logging
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class EventsConfig(BaseSettings):
    """
    Events configuration.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # === Event Bus ===
    event_max_history: int = Field(default=100, alias="EVENT_MAX_HISTORY", ge=0)
    event_enable_wildcards: bool = Field(default=True, alias="EVENT_ENABLE_WILDCARDS")

    # === Validation & DLQ ===
    event_enable_validation: bool = Field(
        default=False, alias="EVENT_ENABLE_VALIDATION"
    )
    event_enable_dlq: bool = Field(default=False, alias="EVENT_ENABLE_DLQ")
    event_dlq_max_size: int = Field(default=1000, alias="EVENT_DLQ_MAX_SIZE", ge=1)


# Global instance
_events_config: Optional[EventsConfig] = None


def get_events_config() -> EventsConfig:
    """Get or create the global events configuration instance."""
    global _events_config
    if _events_config is None:
        _events_config = EventsConfig()
        logger.info("Initialized EventsConfig")
    return _events_config
