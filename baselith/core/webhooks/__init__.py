"""
Outbound webhook subsystem.

Register endpoints and emit signed, retried, dead-lettered events to external
consumers. Opt-in via ``WEBHOOKS_ENABLED``. See
:class:`~baselith.core.webhooks.service.WebhookService`.
"""

from baselith.core.webhooks.dispatcher import WebhookDispatcher
from baselith.core.webhooks.service import WebhookService, get_webhook_service
from baselith.core.webhooks.signing import (
    SIGNATURE_HEADER,
    build_signature_header,
    verify_signature,
)
from baselith.core.webhooks.ssrf import (
    WebhookSSRFError,
    resolve_pinned_target,
    validate_webhook_url,
)
from baselith.core.webhooks.store import InMemoryWebhookStore, WebhookStore
from baselith.core.webhooks.types import (
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
