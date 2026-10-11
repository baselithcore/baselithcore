"""
Outbound webhook subsystem.

Register endpoints and emit signed, retried, dead-lettered events to external
consumers. Opt-in via ``WEBHOOKS_ENABLED``. See
:class:`~baselith.core.webhooks.service.WebhookService`.

``WebhookSSRFError``, ``resolve_pinned_target`` and ``validate_webhook_url`` are
re-exported from the deprecated :mod:`baselith.core.webhooks.ssrf` shim for
backward compatibility only: deprecated since 1.0, removed in 2.0. The two
functions warn on call; ``WebhookSSRFError`` is the same class as
:class:`baselith.core.security.ssrf.SsrfError` and does not warn. Use
:mod:`baselith.core.security.ssrf` (``assert_url_safe``,
``resolve_pinned_target`` with an ``SsrfPolicy``, ``SsrfError``) instead.
"""

from baselith.core.webhooks.dispatcher import WebhookDispatcher
from baselith.core.webhooks.service import WebhookService, get_webhook_service
from baselith.core.webhooks.signing import (
    SIGNATURE_HEADER,
    build_signature_header,
    verify_signature,
)

# Deprecated re-exports (since 1.0, removed in 2.0) — see the module docstring.
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
