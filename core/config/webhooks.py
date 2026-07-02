"""
Webhook subsystem configuration.

Controls outbound webhook delivery: enablement, HTTP timeouts/retries, the HMAC
signature replay-tolerance window, SSRF policy, and per-tenant endpoint caps.
Opt-in and default-off so the feature adds nothing until configured.
"""

import logging

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class WebhookConfig(BaseSettings):
    """Configuration for outbound webhooks."""

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    enabled: bool = Field(default=False, alias="WEBHOOKS_ENABLED")

    # Per-delivery HTTP timeout (seconds).
    timeout_seconds: float = Field(default=10.0, alias="WEBHOOK_TIMEOUT_SECONDS", gt=0)
    # Delivery attempts before a webhook is dead-lettered (1 = no retry).
    max_attempts: int = Field(default=4, alias="WEBHOOK_MAX_ATTEMPTS", ge=1)
    # Base backoff (seconds); exponential with jitter between attempts.
    retry_backoff_seconds: float = Field(
        default=1.0, alias="WEBHOOK_RETRY_BACKOFF_SECONDS", ge=0
    )

    # Signature freshness window enforced by verify_signature (seconds).
    signature_tolerance_seconds: int = Field(
        default=300, alias="WEBHOOK_SIGNATURE_TOLERANCE_SECONDS", ge=0
    )

    # SSRF: by default reject loopback/private/link-local/reserved targets and
    # non-http(s) schemes. Enable only for trusted local development.
    allow_internal: bool = Field(default=False, alias="WEBHOOK_ALLOW_INTERNAL")

    # Cap registrations per tenant to bound fan-out and memory.
    max_endpoints_per_tenant: int = Field(
        default=50, alias="WEBHOOK_MAX_ENDPOINTS_PER_TENANT", ge=1
    )


_webhook_config: WebhookConfig | None = None


def get_webhook_config() -> WebhookConfig:
    """Get or create the global webhook configuration instance."""
    global _webhook_config
    if _webhook_config is None:
        _webhook_config = WebhookConfig()
    return _webhook_config
