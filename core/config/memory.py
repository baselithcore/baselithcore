"""
Memory configuration for BaselithCore.

This module provides configuration for the Supermemory integration —
the intelligent, persistent memory layer for agents. Settings map to
environment variables with the SUPERMEMORY_ prefix.
"""

import logging
from functools import lru_cache
from typing import Optional

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class SupermemoryConfig(BaseSettings):
    """
    Configuration for the Supermemory intelligent memory layer.

    Supermemory provides persistent, user-aware memory with automatic
    fact extraction, temporal reasoning, hybrid search (vector + profile),
    and multi-tenant isolation via container tags.

    Environment variables (all prefixed with SUPERMEMORY_):
        SUPERMEMORY_API_KEY         — API key from console.supermemory.ai
        SUPERMEMORY_BASE_URL        — Override for self-hosted instances
        SUPERMEMORY_ENABLED         — Toggle the integration on/off
        SUPERMEMORY_DEFAULT_TAG     — Default container tag for unnamed agents
        SUPERMEMORY_SEARCH_LIMIT    — Default number of results returned per query
        SUPERMEMORY_MIN_SCORE       — Minimum relevance score for search results
    """

    model_config = SettingsConfigDict(
        env_prefix="SUPERMEMORY_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    enabled: bool = Field(
        default=False,
        description="Enable the Supermemory integration",
    )

    api_key: Optional[SecretStr] = Field(
        default=None,
        description="API key from console.supermemory.ai",
    )

    base_url: Optional[str] = Field(
        default=None,
        description="Base URL override for self-hosted Supermemory instances",
    )

    default_tag: str = Field(
        default="baselithcore_default",
        description="Default container tag used when no agent/tenant ID is specified",
    )

    search_limit: int = Field(
        default=5,
        ge=1,
        le=100,
        description="Default number of results returned per memory search query",
    )

    min_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Minimum relevance score threshold for search results",
    )


@lru_cache(maxsize=1)
def get_supermemory_config() -> SupermemoryConfig:
    """Return the singleton SupermemoryConfig instance."""
    config = SupermemoryConfig()
    if config.enabled and not config.api_key:
        logger.warning(
            "SUPERMEMORY_ENABLED is True but SUPERMEMORY_API_KEY is not set. "
            "The Supermemory provider will fail at runtime."
        )
    return config
