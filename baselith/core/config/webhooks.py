"""
Webhook subsystem configuration.

Controls outbound webhook delivery: enablement, HTTP timeouts/retries, the HMAC
signature replay-tolerance window, SSRF policy, and per-tenant endpoint caps.
Opt-in and default-off so the feature adds nothing until configured.
"""

import logging
from typing import Literal

from pydantic import Field
from pydantic_settings import SettingsConfigDict

from baselith.core.config._aliased import AliasedSettings
from baselith.core.config.overrides import overridable

logger = logging.getLogger(__name__)


class WebhookConfig(AliasedSettings):
    """Configuration for outbound webhooks."""

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    enabled: bool = Field(default=False, alias="WEBHOOKS_ENABLED")

    # Where endpoints and delivery records live. "memory" is process-local:
    # lost on restart and invisible to other replicas and to the RQ worker.
    # "postgres" needs migration 011 applied.
    store: Literal["memory", "postgres"] = Field(
        default="memory", alias="WEBHOOK_STORE"
    )

    # Per-delivery HTTP timeout (seconds).
    timeout_seconds: float = Field(default=10.0, alias="WEBHOOK_TIMEOUT_SECONDS", gt=0)
    # Upper bound on concurrent outbound sockets across all deliveries, so a
    # burst of events to many endpoints cannot open an unbounded number of
    # connections from the process.
    max_connections: int = Field(default=20, alias="WEBHOOK_MAX_CONNECTIONS", ge=1)
    # Delivery attempts before a webhook is dead-lettered (1 = no retry).
    max_attempts: int = Field(default=4, alias="WEBHOOK_MAX_ATTEMPTS", ge=1)
    # Base backoff (seconds); exponential with jitter between attempts.
    retry_backoff_seconds: float = Field(
        default=1.0, alias="WEBHOOK_RETRY_BACKOFF_SECONDS", ge=0
    )

    # Transactional outbox: ``WebhookService.publish`` queues the event in a
    # durable table (in the caller's transaction when it passes a cursor) and a
    # relay in every API process delivers it, so an event survives a crash
    # between the business commit and the delivery. At-least-once: receivers
    # deduplicate on the envelope ``id``. Only durable with WEBHOOK_STORE=postgres
    # (migration 016); with "memory" it queues in-process and survives nothing.
    outbox_enabled: bool = Field(default=False, alias="WEBHOOK_OUTBOX_ENABLED")
    # Seconds between polls when the outbox is empty.
    outbox_poll_seconds: float = Field(
        default=2.0, alias="WEBHOOK_OUTBOX_POLL_SECONDS", gt=0
    )
    # Rows claimed per poll.
    outbox_batch_size: int = Field(default=50, alias="WEBHOOK_OUTBOX_BATCH_SIZE", ge=1)
    # How long a relay owns a claimed row. Keep it above one event's worst-case
    # delivery time (max attempts x timeout + backoff across its endpoints).
    outbox_lease_seconds: float = Field(
        default=120.0, alias="WEBHOOK_OUTBOX_LEASE_SECONDS", gt=0
    )
    # Dispatch attempts before a row is parked as dead for an operator.
    outbox_max_attempts: int = Field(
        default=8, alias="WEBHOOK_OUTBOX_MAX_ATTEMPTS", ge=1
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


@overridable(WebhookConfig)
def get_webhook_config() -> WebhookConfig:
    """Get or create the global webhook configuration instance."""
    global _webhook_config
    if _webhook_config is None:
        _webhook_config = WebhookConfig()
    return _webhook_config
