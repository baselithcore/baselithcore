"""
Webhook service facade — registration and event emission.

This is the public entry point. Application/plugin code registers endpoints and
calls :meth:`WebhookService.emit`; the service finds the matching subscriptions
for the event's tenant and fans delivery out concurrently via the dispatcher.

Emission is a no-op (returns no deliveries) when webhooks are disabled, so call
sites can fire events unconditionally with zero overhead until an operator opts
in.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from pydantic import SecretStr

from core.config.webhooks import WebhookConfig, get_webhook_config
from core.observability.logging import get_logger
from core.webhooks.dispatcher import WebhookDispatcher
from core.webhooks.ssrf import validate_webhook_url
from core.webhooks.store import InMemoryWebhookStore, WebhookStore
from core.webhooks.types import (
    WebhookDelivery,
    WebhookEndpoint,
    WebhookEvent,
)

logger = get_logger(__name__)


class WebhookService:
    """Manage webhook endpoints and emit events to subscribers."""

    def __init__(
        self,
        store: Optional[WebhookStore] = None,
        config: Optional[WebhookConfig] = None,
        dispatcher: Optional[WebhookDispatcher] = None,
    ) -> None:
        self._config = config or get_webhook_config()
        self._store: WebhookStore = store or InMemoryWebhookStore()
        self._dispatcher = dispatcher or WebhookDispatcher(self._store, self._config)

    @property
    def store(self) -> WebhookStore:
        return self._store

    async def register_endpoint(
        self,
        url: str,
        secret: str,
        *,
        tenant_id: str = "default",
        event_types: Optional[set[str]] = None,
        description: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> WebhookEndpoint:
        """Register a new endpoint after validating the URL and the tenant cap.

        Raises:
            WebhookSSRFError: If the URL is unsafe (and internal is not allowed).
            ValueError: If the tenant's endpoint cap is exceeded.
        """
        validate_webhook_url(url, allow_internal=self._config.allow_internal)
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

    async def list_endpoints(self, tenant_id: str = "default") -> List[WebhookEndpoint]:
        return await self._store.list_endpoints(tenant_id)

    async def delete_endpoint(
        self, endpoint_id: str, *, tenant_id: Optional[str] = None
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
        if tenant_id is not None and endpoint.tenant_id != tenant_id:
            return False
        return await self._store.delete_endpoint(endpoint_id)

    async def emit(
        self,
        event_type: str,
        data: Dict[str, Any],
        *,
        tenant_id: str = "default",
    ) -> List[WebhookDelivery]:
        """Emit an event to all subscribed endpoints for the tenant.

        Returns the delivery records (empty if disabled or no subscribers).
        Deliveries run concurrently; a single failing endpoint does not affect
        the others.
        """
        if not self._config.enabled:
            return []
        event = WebhookEvent(type=event_type, tenant_id=tenant_id, data=data)
        endpoints = await self._store.endpoints_for_event(tenant_id, event_type)
        if not endpoints:
            return []
        results = await asyncio.gather(
            *(self._dispatcher.deliver(ep, event) for ep in endpoints),
            return_exceptions=True,
        )
        deliveries: List[WebhookDelivery] = []
        for ep, res in zip(endpoints, results):
            if isinstance(res, WebhookDelivery):
                deliveries.append(res)
            else:  # pragma: no cover - dispatcher.deliver does not raise
                logger.error(
                    "webhook_emit_unexpected_error",
                    extra={"endpoint_id": ep.id, "error": str(res)},
                )
        return deliveries

    async def replay_delivery(
        self, delivery_id: str, *, tenant_id: Optional[str] = None
    ) -> Optional[WebhookDelivery]:
        """Re-attempt a previously failed delivery using its stored payload.

        When ``tenant_id`` is given, a delivery owned by a different tenant is
        treated as not found (returns ``None``) to prevent cross-tenant IDOR.
        """
        original = await self._store.get_delivery(delivery_id)
        if original is None:
            return None
        if tenant_id is not None and original.tenant_id != tenant_id:
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


_webhook_service: Optional[WebhookService] = None


def get_webhook_service() -> WebhookService:
    """Get or create the global webhook service."""
    global _webhook_service
    if _webhook_service is None:
        _webhook_service = WebhookService()
    return _webhook_service
