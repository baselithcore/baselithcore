"""
Outbound webhook delivery with signing, retries, and dead-lettering.

The dispatcher owns the HTTP delivery of a single event to a single endpoint:
it serializes the envelope, signs it (HMAC), enforces the SSRF guard, POSTs with
bounded retries (exponential backoff + jitter), and records the resulting
:class:`WebhookDelivery`. A delivery that exhausts its attempts is persisted in
the FAILED state (the dead-letter equivalent) so it can be inspected and
replayed.
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Optional

import httpx
import orjson

from core.config.webhooks import WebhookConfig, get_webhook_config
from core.observability.logging import get_logger
from core.webhooks.signing import SIGNATURE_HEADER, build_signature_header
from core.webhooks.ssrf import WebhookSSRFError, validate_webhook_url
from core.webhooks.store import WebhookStore
from core.webhooks.types import (
    DeliveryStatus,
    WebhookDelivery,
    WebhookEndpoint,
    WebhookEvent,
)

logger = get_logger(__name__)


class WebhookDispatcher:
    """Delivers signed events to endpoints with retry and dead-lettering."""

    def __init__(
        self,
        store: WebhookStore,
        config: Optional[WebhookConfig] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._store = store
        self._config = config or get_webhook_config()
        self._client = http_client
        self._owns_client = http_client is None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._config.timeout_seconds)
        return self._client

    async def aclose(self) -> None:
        """Close the pooled HTTP client if this dispatcher created it."""
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def deliver(
        self, endpoint: WebhookEndpoint, event: WebhookEvent
    ) -> WebhookDelivery:
        """Deliver ``event`` to ``endpoint``, retrying on failure.

        Always returns a recorded :class:`WebhookDelivery` (SUCCESS or FAILED);
        it does not raise on delivery failure — the outcome is on the record.
        """
        body = orjson.dumps(event.envelope())
        delivery = WebhookDelivery(
            endpoint_id=endpoint.id,
            event_id=event.id,
            event_type=event.type,
            tenant_id=event.tenant_id,
            url=endpoint.url,
            payload=event.envelope(),
        )

        # SSRF is checked once up front; the target host does not change between
        # attempts. A blocked URL fails closed without any network call.
        try:
            await asyncio.to_thread(
                validate_webhook_url,
                endpoint.url,
                allow_internal=self._config.allow_internal,
            )
        except WebhookSSRFError as e:
            return await self._finalize_failure(delivery, error=f"ssrf_blocked: {e}")

        last_error: Optional[str] = None
        for attempt in range(1, self._config.max_attempts + 1):
            delivery.attempts = attempt
            try:
                status_code = await self._post(endpoint, body)
                delivery.last_status_code = status_code
                if 200 <= status_code < 300:
                    delivery.status = DeliveryStatus.SUCCESS
                    delivery.completed_at = time.time()
                    return await self._store.record_delivery(delivery)
                last_error = f"http_{status_code}"
            except httpx.HTTPError as e:
                last_error = f"{type(e).__name__}: {e}"

            if attempt < self._config.max_attempts:
                await asyncio.sleep(self._backoff(attempt))

        return await self._finalize_failure(delivery, error=last_error or "unknown")

    async def _post(self, endpoint: WebhookEndpoint, body: bytes) -> int:
        """Send one signed POST and return the HTTP status code."""
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "baselith-webhooks/1.0",
            SIGNATURE_HEADER: build_signature_header(
                endpoint.secret.get_secret_value(), body
            ),
            **endpoint.headers,
        }
        resp = await self._get_client().post(
            endpoint.url, content=body, headers=headers
        )
        return resp.status_code

    def _backoff(self, attempt: int) -> float:
        base = self._config.retry_backoff_seconds
        return min(base * (2 ** (attempt - 1)), 60.0) + random.uniform(0, base)

    async def _finalize_failure(
        self, delivery: WebhookDelivery, *, error: str
    ) -> WebhookDelivery:
        delivery.status = DeliveryStatus.FAILED
        delivery.last_error = error
        delivery.completed_at = time.time()
        logger.warning(
            "webhook_delivery_failed",
            extra={
                "endpoint_id": delivery.endpoint_id,
                "event_type": delivery.event_type,
                "attempts": delivery.attempts,
                "error": error,
            },
        )
        return await self._store.record_delivery(delivery)
