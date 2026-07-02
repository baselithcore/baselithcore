"""
Outbound webhook subsystem.

Register endpoints and emit signed, retried, dead-lettered events to external
consumers. Opt-in via ``WEBHOOKS_ENABLED``. See
:class:`~core.webhooks.service.WebhookService`.
"""

from core.webhooks.dispatcher import WebhookDispatcher
from core.webhooks.service import WebhookService, get_webhook_service
from core.webhooks.signing import (
    SIGNATURE_HEADER,
    build_signature_header,
    verify_signature,
)
from core.webhooks.ssrf import (
    WebhookSSRFError,
    resolve_pinned_target,
    validate_webhook_url,
)
from core.webhooks.store import InMemoryWebhookStore, WebhookStore
from core.webhooks.types import (
    DeliveryStatus,
    WebhookDelivery,
    WebhookEndpoint,
    WebhookEvent,
)

__all__ = [
    "SIGNATURE_HEADER",
    "DeliveryStatus",
    "InMemoryWebhookStore",
    "WebhookDelivery",
    "WebhookDispatcher",
    "WebhookEndpoint",
    "WebhookEvent",
    "WebhookSSRFError",
    "WebhookService",
    "WebhookStore",
    "build_signature_header",
    "get_webhook_service",
    "resolve_pinned_target",
    "validate_webhook_url",
    "verify_signature",
]
