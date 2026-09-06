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

import httpx
import orjson

from core.config.webhooks import WebhookConfig, get_webhook_config
from core.observability.logging import get_logger
from core.webhooks.signing import (
    RESERVED_DELIVERY_HEADERS,
    SIGNATURE_HEADER,
    build_signature_header,
)
from core.webhooks.ssrf import WebhookSSRFError, resolve_pinned_target
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
        config: WebhookConfig | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._store = store
        self._config = config or get_webhook_config()
        self._client = http_client
        self._owns_client = http_client is None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            # follow_redirects=False: a 3xx must not let httpx re-resolve and
            # follow a redirect to an unvetted (internal) host, bypassing the
            # pinned connection. Redirects surface as non-2xx and fail closed.
            # Bounded fan-out, no keep-alive: every delivery targets a pinned
            # IP with Host/SNI set per request, and httpx pools connections by
            # (scheme, host, port) alone — so a kept-alive TLS session validated
            # for one hostname could be reused for a different hostname that
            # resolves to the same address (shared CDN edge). One handshake per
            # delivery is the price of never mixing them up.
            self._client = httpx.AsyncClient(
                timeout=self._config.timeout_seconds,
                follow_redirects=False,
                limits=httpx.Limits(
                    max_connections=self._config.max_connections,
                    max_keepalive_connections=0,
                ),
            )
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

        # SSRF is checked once up front and the connection is PINNED to the
        # verified IP: the address we validate here is the exact address we
        # connect to on every attempt, so a DNS rebind between validation and
        # delivery cannot redirect the POST to an internal host. A blocked URL
        # fails closed without any network call.
        try:
            pinned_url, pin_host = await asyncio.to_thread(
                resolve_pinned_target,
                endpoint.url,
                allow_internal=self._config.allow_internal,
            )
        except WebhookSSRFError as e:
            return await self._finalize_failure(delivery, error=f"ssrf_blocked: {e}")

        last_error: str | None = None
        for attempt in range(1, self._config.max_attempts + 1):
            delivery.attempts = attempt
            try:
                status_code = await self._post(endpoint, body, pinned_url, pin_host)
                delivery.last_status_code = status_code
                if 200 <= status_code < 300:
                    delivery.status = DeliveryStatus.SUCCESS
                    delivery.completed_at = time.time()
                    return await self._store.record_delivery(delivery)
                last_error = f"http_{status_code}"
                # Deterministic client errors (bad signature config, revoked
                # endpoint, 404) fail identically on every attempt — retrying
                # burns the whole backoff budget against an endpoint that can
                # never accept the delivery. Only 408/429 are transient 4xx.
                if 400 <= status_code < 500 and status_code not in (408, 429):
                    break
            except httpx.HTTPError as e:
                last_error = f"{type(e).__name__}: {e}"

            if attempt < self._config.max_attempts:
                await asyncio.sleep(self._backoff(attempt))

        return await self._finalize_failure(delivery, error=last_error or "unknown")

    async def _post(
        self,
        endpoint: WebhookEndpoint,
        body: bytes,
        pinned_url: str,
        pin_host: str,
    ) -> int:
        """Send one signed POST to the pinned IP and return the HTTP status.

        The request targets ``pinned_url`` (host replaced by the validated IP)
        while ``Host`` and the TLS SNI are set to the original hostname, so the
        connection lands on the exact address vetted by the SSRF guard.
        """
        # Endpoint-supplied headers go first and are filtered case-insensitively
        # against the reserved set, so a stored record can neither override the
        # signature/Host/framing nor smuggle a second, differently-cased copy.
        extra = {
            k: v
            for k, v in endpoint.headers.items()
            if k.lower() not in RESERVED_DELIVERY_HEADERS
        }
        headers = {
            **extra,
            "Content-Type": "application/json",
            "User-Agent": "baselith-webhooks/1.0",
            SIGNATURE_HEADER: build_signature_header(
                endpoint.secret.get_secret_value(), body
            ),
            "Host": pin_host,
        }
        resp = await self._get_client().post(
            pinned_url,
            content=body,
            headers=headers,
            extensions={"sni_hostname": pin_host},
        )
        status_code: int = resp.status_code
        return status_code

    def _backoff(self, attempt: int) -> float:
        base: float = self._config.retry_backoff_seconds
        return min(base * (2.0 ** (attempt - 1)), 60.0) + random.uniform(0, base)

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
