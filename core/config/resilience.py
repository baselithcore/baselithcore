"""
Resilience configuration.

Settings for circuit breakers, rate limiters, and retries.
"""

import logging
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class ResilienceConfig(BaseSettings):
    """
    Resilience configuration.
    """

    model_config = SettingsConfigDict(
        env_prefix="RESILIENCE_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # === Circuit Breaker ===
    cb_fail_max: int = Field(
        default=5, description="Number of failures before opening circuit"
    )
    cb_reset_timeout: int = Field(
        default=60, description="Seconds before trying half-open state"
    )
    cb_half_open_max: int = Field(
        default=1, description="Max requests in half-open state"
    )

    # === Rate Limiting ===
    # General API limits
    api_rate_limit: int = Field(default=100, description="Max API requests per window")
    api_rate_window: int = Field(
        default=60, description="API rate limit window in seconds"
    )

    # LLM specific limits
    llm_rate_limit: int = Field(default=20, description="Max LLM calls per window")
    llm_rate_window: int = Field(
        default=60, description="LLM rate limit window in seconds"
    )

    # === Retry ===
    retry_max_attempts: int = Field(default=3, description="Maximum retry attempts")
    retry_base_delay: float = Field(default=1.0, description="Base delay for retries")
    retry_max_delay: float = Field(
        default=60.0, description="Maximum delay for retries"
    )
    retry_exponential_base: float = Field(
        default=2.0, description="Base for exponential backoff"
    )
    retry_jitter: bool = Field(default=True, description="Add jitter to retries")

    # === Bulkhead ===
    bulkhead_max_concurrent: int = Field(
        default=10, description="Default max concurrent operations"
    )


# Global instance
_resilience_config: Optional[ResilienceConfig] = None


def get_resilience_config() -> ResilienceConfig:
    """Get or create the global resilience configuration instance."""
    global _resilience_config
    if _resilience_config is None:
        _resilience_config = ResilienceConfig()
        logger.info(
            f"Initialized ResilienceConfig (CB_max={_resilience_config.cb_fail_max})"
        )
    return _resilience_config
