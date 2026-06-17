"""
Persistence layer for webhook endpoints and deliveries.

:class:`WebhookStore` is the pluggable interface; :class:`InMemoryWebhookStore`
is the default, process-local implementation suitable for single-node and tests.
A durable (Postgres/Redis) implementation can be dropped in behind the same
Protocol without touching the dispatcher or service.
"""

from __future__ import annotations

import asyncio
from typing import Dict, List, Optional, Protocol, runtime_checkable

from core.webhooks.types import WebhookDelivery, WebhookEndpoint


@runtime_checkable
class WebhookStore(Protocol):
    """Storage interface for endpoints and delivery records."""

    async def add_endpoint(self, endpoint: WebhookEndpoint) -> WebhookEndpoint: ...

    async def get_endpoint(self, endpoint_id: str) -> Optional[WebhookEndpoint]: ...

    async def list_endpoints(self, tenant_id: str) -> List[WebhookEndpoint]: ...

    async def delete_endpoint(self, endpoint_id: str) -> bool: ...

    async def endpoints_for_event(
        self, tenant_id: str, event_type: str
    ) -> List[WebhookEndpoint]: ...

    async def count_endpoints(self, tenant_id: str) -> int: ...

    async def record_delivery(self, delivery: WebhookDelivery) -> WebhookDelivery: ...

    async def get_delivery(self, delivery_id: str) -> Optional[WebhookDelivery]: ...

    async def list_deliveries(
        self, tenant_id: str, *, limit: int = 50
    ) -> List[WebhookDelivery]: ...


class InMemoryWebhookStore:
    """Process-local store guarded by an async lock.

    Endpoints are keyed by id; deliveries are kept in a bounded per-store list
    (newest first) so inspection/replay works without unbounded growth.
    """

    def __init__(self, max_deliveries: int = 1000) -> None:
        self._endpoints: Dict[str, WebhookEndpoint] = {}
        self._deliveries: Dict[str, WebhookDelivery] = {}
        self._delivery_order: List[str] = []
        self._max_deliveries = max_deliveries
        self._lock = asyncio.Lock()

    async def add_endpoint(self, endpoint: WebhookEndpoint) -> WebhookEndpoint:
        async with self._lock:
            self._endpoints[endpoint.id] = endpoint
        return endpoint

    async def get_endpoint(self, endpoint_id: str) -> Optional[WebhookEndpoint]:
        return self._endpoints.get(endpoint_id)

    async def list_endpoints(self, tenant_id: str) -> List[WebhookEndpoint]:
        return [e for e in self._endpoints.values() if e.tenant_id == tenant_id]

    async def delete_endpoint(self, endpoint_id: str) -> bool:
        async with self._lock:
            return self._endpoints.pop(endpoint_id, None) is not None

    async def endpoints_for_event(
        self, tenant_id: str, event_type: str
    ) -> List[WebhookEndpoint]:
        return [
            e
            for e in self._endpoints.values()
            if e.tenant_id == tenant_id and e.enabled and e.subscribes_to(event_type)
        ]

    async def count_endpoints(self, tenant_id: str) -> int:
        return sum(1 for e in self._endpoints.values() if e.tenant_id == tenant_id)

    async def record_delivery(self, delivery: WebhookDelivery) -> WebhookDelivery:
        async with self._lock:
            if delivery.id not in self._deliveries:
                self._delivery_order.insert(0, delivery.id)
            self._deliveries[delivery.id] = delivery
            # Evict oldest beyond the cap.
            while len(self._delivery_order) > self._max_deliveries:
                evicted = self._delivery_order.pop()
                self._deliveries.pop(evicted, None)
        return delivery

    async def get_delivery(self, delivery_id: str) -> Optional[WebhookDelivery]:
        return self._deliveries.get(delivery_id)

    async def list_deliveries(
        self, tenant_id: str, *, limit: int = 50
    ) -> List[WebhookDelivery]:
        out: List[WebhookDelivery] = []
        for did in self._delivery_order:
            d = self._deliveries.get(did)
            if d and d.tenant_id == tenant_id:
                out.append(d)
            if len(out) >= limit:
                break
        return out
