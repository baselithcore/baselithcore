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
    # RESILIENCE_API_RATE_*: deprecated, no effect. They only seed the
    # defaults of the library helpers get_api_limiter()/RateLimiter() in
    # core.resilience, and no framework request path calls them — HTTP
    # request limits are the RATE_LIMIT_* settings of the security middleware.
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

    # RESILIENCE_LLM_RATE_*: client-side throttle on outgoing LLM calls,
    # enforced by core.services.llm.rate_limit on every generation path.
    llm_rate_enabled: bool = Field(
        default=False,
        description=(
            "Opt-in client-side rate limit on outgoing LLM calls (text, "
            "tool calling, structured, messages, streaming, images, batch "
            "submission): at most RESILIENCE_LLM_RATE_LIMIT calls per "
            "RESILIENCE_LLM_RATE_WINDOW seconds. Per worker process unless "
            "CACHE_BACKEND=redis, which shares the window across workers"
        ),
    )
    llm_rate_limit: int = Field(
        default=20,
        description=(
            "LLM calls allowed per window when RESILIENCE_LLM_RATE_ENABLED is "
            "true (per provider unless RESILIENCE_LLM_RATE_PER_PROVIDER=false)"
        ),
    )
    llm_rate_window: int = Field(
        default=60,
        description="Window length in seconds for RESILIENCE_LLM_RATE_LIMIT",
    )
    llm_rate_max_wait: float = Field(
        default=30.0,
        ge=0.0,
        description=(
            "Longest a call waits for a free LLM rate-limit slot before "
            "failing with LocalLLMRateLimitError (0 = fail immediately)"
        ),
    )
    llm_rate_per_provider: bool = Field(
        default=True,
        description=(
            "Keep a separate LLM rate-limit window per provider name; false "
            "shares one window across every provider"
        ),
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
