"""
Webhook service facade — registration and event emission.

This is the public entry point. Application/plugin code registers endpoints and
calls :meth:`WebhookService.emit`; the service finds the matching subscriptions
for the event's tenant and fans delivery out concurrently via the dispatcher.

Emission is a no-op (returns no deliveries) when webhooks are disabled, so call
sites can fire events unconditionally with zero overhead until an operator opts
in.

:meth:`WebhookService.publish` is the durable variant: with
``WEBHOOK_OUTBOX_ENABLED`` it queues the event in the transactional outbox
(:mod:`baselith.core.webhooks.outbox`) instead of delivering in-process.
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import SecretStr

from baselith.core.config.webhooks import WebhookConfig, get_webhook_config
from baselith.core.observability.logging import get_logger
from baselith.core.security.ssrf import SsrfPolicy, assert_url_safe
from baselith.core.tenancy.guard import tenants_match
from baselith.core.webhooks.dispatcher import WebhookDispatcher
from baselith.core.webhooks.outbox import InMemoryOutbox, OutboxEntry, OutboxStore
from baselith.core.webhooks.relay import OutboxRelay
from baselith.core.webhooks.signing import reserved_header_names
from baselith.core.webhooks.store import InMemoryWebhookStore, WebhookStore
from baselith.core.webhooks.types import (
    WebhookDelivery,
    WebhookEndpoint,
    WebhookEvent,
)

logger = get_logger(__name__)


class WebhookService:
    """Manage webhook endpoints and emit events to subscribers."""

    def __init__(
        self,
        store: WebhookStore | None = None,
        config: WebhookConfig | None = None,
        dispatcher: WebhookDispatcher | None = None,
    ) -> None:
        self._config = config or get_webhook_config()
        self._store: WebhookStore = store or InMemoryWebhookStore()
        self._dispatcher = dispatcher or WebhookDispatcher(self._store, self._config)
        self._outbox: OutboxStore | None = None

    @property
    def store(self) -> WebhookStore:
        return self._store

    async def register_endpoint(
        self,
        url: str,
        secret: str,
        *,
        tenant_id: str = "default",
        event_types: set[str] | None = None,
        description: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> WebhookEndpoint:
        """Register a new endpoint after validating the URL and the tenant cap.

        Raises:
            SsrfError: If the URL is unsafe (and internal is not allowed).
            ValueError: If the tenant's endpoint cap is exceeded, or ``headers``
                names one the dispatcher reserves (signature, Host, framing).
        """
        assert_url_safe(url, SsrfPolicy(allow_internal=self._config.allow_internal))
        reserved = reserved_header_names(headers or {})
        if reserved:
            raise ValueError(
                "Webhook headers use reserved names set by the dispatcher: "
                + ", ".join(reserved)
            )
        if (
            await self._store.count_endpoints(tenant_id)
            >= self._config.max_endpoints_per_tenant
        ):
            raise ValueError(
                f"Tenant {tenant_id!r} has reached the webhook endpoint cap "
                f"({self._config.max_endpoints_per_tenant})"
            )
        endpoint = WebhookEndpoint(
            tenant_id=tenant_id,
            url=url,
            secret=SecretStr(secret),
            event_types=event_types or {"*"},
            description=description,
            headers=headers or {},
        )
        await self._store.add_endpoint(endpoint)
        logger.info(
            "webhook_endpoint_registered",
            extra={"endpoint_id": endpoint.id, "tenant_id": tenant_id, "url": url},
        )
        return endpoint

    async def list_endpoints(self, tenant_id: str = "default") -> list[WebhookEndpoint]:
        return await self._store.list_endpoints(tenant_id)

    async def delete_endpoint(
        self, endpoint_id: str, *, tenant_id: str | None = None
    ) -> bool:
        """Delete an endpoint, scoped to ``tenant_id`` when provided.

        When ``tenant_id`` is given, an endpoint owned by a different tenant is
        treated as not found (returns ``False``) — this prevents cross-tenant
        IDOR via a guessed/known endpoint id. ``None`` (internal callers) skips
        the ownership check.
        """
        endpoint = await self._store.get_endpoint(endpoint_id)
        if endpoint is None:
            return False
        if tenant_id is not None and not tenants_match(endpoint.tenant_id, tenant_id):
            return False
        return await self._store.delete_endpoint(endpoint_id)

    async def emit(
        self,
        event_type: str,
        data: dict[str, Any],
        *,
        tenant_id: str = "default",
    ) -> list[WebhookDelivery]:
        """Emit an event to all subscribed endpoints for the tenant.

        Returns the delivery records (empty if disabled or no subscribers).
        Deliveries run concurrently; a single failing endpoint does not affect
        the others. The delivery happens in this process, so it does not
        survive a crash; :meth:`publish` is the durable path.
        """
        if not self._config.enabled:
            return []
        event = WebhookEvent(type=event_type, tenant_id=tenant_id, data=data)
        return await self.deliver_event(event)

    async def deliver_event(self, event: WebhookEvent) -> list[WebhookDelivery]:
        """Deliver an already-built ``event`` to its tenant's subscribers.

        Raises whatever the store raises (for instance an unreachable
        database): the outbox relay relies on that to reschedule the row.
        """
        endpoints = await self._store.endpoints_for_event(event.tenant_id, event.type)
        if not endpoints:
            return []
        results = await asyncio.gather(
            *(self._dispatcher.deliver(ep, event) for ep in endpoints),
            return_exceptions=True,
        )
        deliveries: list[WebhookDelivery] = []
        for ep, res in zip(endpoints, results, strict=True):
            if isinstance(res, WebhookDelivery):
                deliveries.append(res)
            else:  # pragma: no cover - dispatcher.deliver does not raise
                logger.error(
                    "webhook_emit_unexpected_error",
                    extra={"endpoint_id": ep.id, "error": str(res)},
                )
        return deliveries

    @property
    def outbox(self) -> OutboxStore:
        """The outbox store (built on first use)."""
        if self._outbox is None:
            self._outbox = _configured_outbox()
        return self._outbox

    async def publish(
        self,
        event_type: str,
        data: dict[str, Any],
        *,
        tenant_id: str = "default",
        cur: Any | None = None,
    ) -> str | None:
        """Emit durably through the outbox, or fall back to :meth:`emit`.

        With ``WEBHOOK_OUTBOX_ENABLED`` the event is queued and the relay
        delivers it later; pass ``cur`` (the open cursor of the transaction
        that changed the data) to make the queueing atomic with that change.
        Without the outbox this is a plain in-process :meth:`emit`.

        Returns:
            The event id when it was queued, ``None`` otherwise (webhooks off,
            or delivered in-process).
        """
        if not self._config.enabled:
            return None
        if not self._config.outbox_enabled:
            await self.emit(event_type, data, tenant_id=tenant_id)
            return None
        event = WebhookEvent(type=event_type, tenant_id=tenant_id, data=data)
        await self.outbox.enqueue(
            OutboxEntry(
                id=event.id,
                tenant_id=tenant_id,
                event_type=event_type,
                data=data,
                created_at=event.created_at,
            ),
            cur,
        )
        return event.id

    def build_relay(self) -> OutboxRelay:
        """A relay wired to this service, tuned by the ``WEBHOOK_OUTBOX_*`` knobs."""
        cfg = self._config
        return OutboxRelay(
            self,
            self.outbox,
            poll_seconds=cfg.outbox_poll_seconds,
            batch_size=cfg.outbox_batch_size,
            lease_seconds=cfg.outbox_lease_seconds,
            max_attempts=cfg.outbox_max_attempts,
            backoff_seconds=max(cfg.retry_backoff_seconds, 1.0),
        )

    async def replay_delivery(
        self, delivery_id: str, *, tenant_id: str | None = None
    ) -> WebhookDelivery | None:
        """Re-attempt a previously failed delivery using its stored payload.

        When ``tenant_id`` is given, a delivery owned by a different tenant is
        treated as not found (returns ``None``) to prevent cross-tenant IDOR.
        """
        original = await self._store.get_delivery(delivery_id)
        if original is None:
            return None
        if tenant_id is not None and not tenants_match(original.tenant_id, tenant_id):
            return None
        endpoint = await self._store.get_endpoint(original.endpoint_id)
        if endpoint is None:
            return None
        event = WebhookEvent(
            id=original.event_id,
            type=original.event_type,
            tenant_id=original.tenant_id,
            data=original.payload.get("data", {}),
        )
        return await self._dispatcher.deliver(endpoint, event)

    async def aclose(self) -> None:
        await self._dispatcher.aclose()


_webhook_service: WebhookService | None = None


def get_webhook_service() -> WebhookService:
    """Get or create the global webhook service."""
    global _webhook_service
    if _webhook_service is None:
        _webhook_service = WebhookService(store=_configured_store())
    return _webhook_service


def _configured_store() -> WebhookStore | None:
    """The store ``WEBHOOK_STORE`` selects; ``None`` keeps the in-memory one."""
    from baselith.core.config.webhooks import get_webhook_config

    if get_webhook_config().store == "postgres":
        from baselith.core.webhooks.store_postgres import PostgresWebhookStore

        return PostgresWebhookStore()
    return None


def _configured_outbox() -> OutboxStore:
    """The outbox store ``WEBHOOK_STORE`` selects (durable only with postgres)."""
    from baselith.core.config.webhooks import get_webhook_config

    if get_webhook_config().store == "postgres":
        from baselith.core.webhooks.outbox_postgres import PostgresOutbox

        return PostgresOutbox()
    return InMemoryOutbox()
