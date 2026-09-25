"""
Resilience configuration.

Settings for circuit breakers, rate limiters, and retries.
"""

import logging

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class ResilienceConfig(BaseSettings):
    """
    Resilience configuration.
    """

    model_config = SettingsConfigDict(
        env_prefix="RESILIENCE_",
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
    # Deprecated, no effect: these only seed the defaults of the library
    # helpers get_api_limiter()/get_llm_limiter()/RateLimiter() in
    # core.resilience, and no framework request or LLM path calls them.
    api_rate_limit: int = Field(
        default=100,
        description=(
            "Deprecated, no effect: only the default of get_api_limiter(), "
            "which nothing in the framework calls; HTTP request limits are "
            "RATE_LIMIT_USER_PER_MINUTE and RATE_LIMIT_ADMIN_PER_MINUTE"
        ),
    )
    api_rate_window: int = Field(
        default=60,
        description=(
            "Deprecated, no effect: pairs with RESILIENCE_API_RATE_LIMIT; the "
            "HTTP rate-limit window is RATE_LIMIT_WINDOW_SECONDS"
        ),
    )

    llm_rate_limit: int = Field(
        default=20,
        description=(
            "Deprecated, no effect: only the default of get_llm_limiter(), "
            "which nothing in the framework calls, so LLM calls are not "
            "throttled by it"
        ),
    )
    llm_rate_window: int = Field(
        default=60,
        description="Deprecated, no effect: pairs with RESILIENCE_LLM_RATE_LIMIT",
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
_resilience_config: ResilienceConfig | None = None


def get_resilience_config() -> ResilienceConfig:
    """Get or create the global resilience configuration instance."""
    global _resilience_config
    if _resilience_config is None:
        _resilience_config = ResilienceConfig()
        logger.info(
            f"Initialized ResilienceConfig (CB_max={_resilience_config.cb_fail_max})"
        )
    return _resilience_config
