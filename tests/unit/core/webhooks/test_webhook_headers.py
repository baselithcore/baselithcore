"""Delivery-header integrity and outbound connection budget of the dispatcher.

Endpoint-supplied static headers ride along on every delivery, but a fixed
set of names belongs to the dispatcher: the signature is the receiver's only
proof of origin, the framing headers describe the signed body, and ``Host``
is the SSRF pin. These tests pin that a subscriber can neither register nor
smuggle any of them, and that the lazily-built client is bounded.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from pydantic import SecretStr

from core.config.webhooks import WebhookConfig
from core.webhooks.dispatcher import WebhookDispatcher
from core.webhooks.service import WebhookService
from core.webhooks.signing import (
    RESERVED_DELIVERY_HEADERS,
    SIGNATURE_HEADER,
    reserved_header_names,
    verify_signature,
)
from core.webhooks.store import InMemoryWebhookStore
from core.webhooks.types import DeliveryStatus, WebhookEndpoint, WebhookEvent

HOOK_URL = "https://hooks.test/receiver"


def _endpoint(**kw) -> WebhookEndpoint:
    defaults = dict(url=HOOK_URL, secret=SecretStr("whsec_test"))
    defaults.update(kw)
    return WebhookEndpoint(**defaults)


def _config(**kw) -> WebhookConfig:
    base = dict(
        WEBHOOKS_ENABLED=True,
        WEBHOOK_ALLOW_INTERNAL=True,  # skip DNS in tests
        WEBHOOK_RETRY_BACKOFF_SECONDS=0,
        WEBHOOK_MAX_ATTEMPTS=3,
    )
    base.update(kw)
    return WebhookConfig(**base)


def _dispatcher(handler, config=None):
    cfg = config or _config()
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return WebhookDispatcher(InMemoryWebhookStore(), cfg, http_client=client)


def test_reserved_set_covers_signature_pin_and_framing():
    assert SIGNATURE_HEADER.lower() in RESERVED_DELIVERY_HEADERS
    assert {"host", "content-type", "content-length"} <= RESERVED_DELIVERY_HEADERS
    assert reserved_header_names({"X-Custom": "1", "HOST": "x"}) == ["HOST"]
    assert reserved_header_names({"X-Custom": "1"}) == []


@pytest.mark.asyncio
async def test_endpoint_headers_cannot_override_framework_headers():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = request.headers
        seen["body"] = request.content
        return httpx.Response(200)

    disp = _dispatcher(handler)
    endpoint = _endpoint(
        headers={
            # Case-insensitive collisions with every reserved header.
            "x-baselith-signature": "forged",
            "content-type": "text/plain",
            "Host": "attacker.example",
            "user-agent": "spoofed",
            "X-Custom": "kept",
        }
    )
    d = await disp.deliver(endpoint, WebhookEvent(type="chat.done"))
    assert d.status == DeliveryStatus.SUCCESS
    headers = seen["headers"]
    assert verify_signature(
        "whsec_test", seen["body"], headers[SIGNATURE_HEADER], tolerance_seconds=0
    )
    assert headers["content-type"] == "application/json"
    assert headers["host"] == "hooks.test"
    assert headers["user-agent"].startswith("baselith-webhooks/")
    assert headers["x-custom"] == "kept"
    await disp.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name", ["X-Baselith-Signature", "content-type", "HOST", "User-Agent"]
)
async def test_register_rejects_reserved_header_names(name):
    svc = WebhookService(InMemoryWebhookStore(), _config())
    with pytest.raises(ValueError, match="reserved"):
        await svc.register_endpoint(HOOK_URL, "s", headers={name: "x"})
    assert await svc.list_endpoints() == []


@pytest.mark.asyncio
async def test_register_keeps_custom_headers():
    svc = WebhookService(InMemoryWebhookStore(), _config())
    ep = await svc.register_endpoint(HOOK_URL, "s", headers={"X-Routing-Token": "abc"})
    assert ep.headers == {"X-Routing-Token": "abc"}


def test_dispatcher_default_client_is_bounded_and_does_not_coalesce():
    """The lazily-built client bounds fan-out sockets and disables keep-alive:
    deliveries are pinned to IPs, and httpx pools by (scheme, host, port), so a
    kept-alive TLS session validated for one hostname could otherwise be reused
    for a different hostname sharing the same address."""
    disp = WebhookDispatcher(InMemoryWebhookStore(), _config(WEBHOOK_MAX_CONNECTIONS=7))
    client = disp._get_client()
    try:
        pool = client._transport._pool
        assert pool._max_connections == 7
        assert pool._max_keepalive_connections == 0
    finally:
        asyncio.run(disp.aclose())
