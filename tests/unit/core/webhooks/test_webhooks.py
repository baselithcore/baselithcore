"""Tests for the outbound webhook subsystem."""

import httpx
import pytest

from core.config.webhooks import WebhookConfig
from core.webhooks.dispatcher import WebhookDispatcher
from core.webhooks.service import WebhookService
from core.webhooks.signing import (
    SIGNATURE_HEADER,
    build_signature_header,
    verify_signature,
)
from core.webhooks.ssrf import WebhookSSRFError, validate_webhook_url
from core.webhooks.store import InMemoryWebhookStore
from core.webhooks.types import (
    DeliveryStatus,
    WebhookEndpoint,
    WebhookEvent,
)
from pydantic import SecretStr

HOOK_URL = "https://hooks.test/receiver"


def _endpoint(**kw) -> WebhookEndpoint:
    defaults = dict(url=HOOK_URL, secret=SecretStr("whsec_test"))
    defaults.update(kw)
    return WebhookEndpoint(**defaults)


def _config(**kw) -> WebhookConfig:
    base = dict(
        WEBHOOKS_ENABLED=True,
        WEBHOOK_ALLOW_INTERNAL=True,  # skip DNS in tests
        WEBHOOK_RETRY_BACKOFF_SECONDS=0,  # near-zero sleeps
        WEBHOOK_MAX_ATTEMPTS=3,
    )
    base.update(kw)
    return WebhookConfig(**base)


def _dispatcher(handler, config=None):
    cfg = config or _config()
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return WebhookDispatcher(InMemoryWebhookStore(), cfg, http_client=client)


# === Signing ===
class TestSigning:
    def test_roundtrip(self):
        body = b'{"a":1}'
        hdr = build_signature_header("secret", body, timestamp=1000)
        assert verify_signature("secret", body, hdr, tolerance_seconds=0)

    def test_wrong_secret_fails(self):
        body = b'{"a":1}'
        hdr = build_signature_header("secret", body, timestamp=1000)
        assert not verify_signature("other", body, hdr, tolerance_seconds=0)

    def test_tampered_body_fails(self):
        hdr = build_signature_header("secret", b'{"a":1}', timestamp=1000)
        assert not verify_signature("secret", b'{"a":2}', hdr, tolerance_seconds=0)

    def test_stale_timestamp_rejected(self):
        body = b"x"
        hdr = build_signature_header("secret", body, timestamp=1000)
        assert not verify_signature("secret", body, hdr, tolerance_seconds=10, now=2000)
        assert verify_signature("secret", body, hdr, tolerance_seconds=0, now=2000)

    def test_malformed_header(self):
        assert not verify_signature("secret", b"x", "garbage", tolerance_seconds=0)


# === SSRF ===
class TestSSRF:
    @pytest.mark.parametrize(
        "url",
        ["http://127.0.0.1/h", "http://169.254.169.254/latest", "http://0.0.0.0/"],
    )
    def test_blocks_internal(self, url):
        with pytest.raises(WebhookSSRFError):
            validate_webhook_url(url)

    def test_blocks_bad_scheme(self):
        with pytest.raises(WebhookSSRFError):
            validate_webhook_url("ftp://example.com/x")

    def test_allow_internal_bypasses(self):
        validate_webhook_url("http://127.0.0.1/h", allow_internal=True)


# === SSRF pinning (anti DNS-rebinding) ===
class TestSSRFPinning:
    def test_pins_url_to_resolved_public_ip(self, monkeypatch):
        from core.webhooks import ssrf

        monkeypatch.setattr(ssrf, "_resolve_addresses", lambda host: ["93.184.216.34"])
        pinned_url, host = ssrf.resolve_pinned_target("https://example.com/hook")
        # Host swapped for the validated IP; original hostname preserved for
        # Host header + TLS SNI.
        assert pinned_url == "https://93.184.216.34/hook"
        assert host == "example.com"

    def test_pins_preserves_port_and_ipv6_brackets(self, monkeypatch):
        from core.webhooks import ssrf

        monkeypatch.setattr(
            ssrf, "_resolve_addresses", lambda host: ["2606:2800:220::1"]
        )
        pinned_url, host = ssrf.resolve_pinned_target("https://example.com:8443/x")
        assert pinned_url == "https://[2606:2800:220::1]:8443/x"
        assert host == "example.com"

    def test_rebind_to_internal_fails_closed(self, monkeypatch):
        from core.webhooks import ssrf

        # A public-looking name whose resolution returns an internal address
        # (the DNS-rebinding attack) must be rejected.
        monkeypatch.setattr(
            ssrf, "_resolve_addresses", lambda host: ["169.254.169.254"]
        )
        with pytest.raises(WebhookSSRFError):
            ssrf.resolve_pinned_target("https://evil.example.com/steal")

    def test_any_blocked_address_taints_result(self, monkeypatch):
        from core.webhooks import ssrf

        monkeypatch.setattr(
            ssrf, "_resolve_addresses", lambda host: ["93.184.216.34", "127.0.0.1"]
        )
        with pytest.raises(WebhookSSRFError):
            ssrf.resolve_pinned_target("https://example.com/x")

    @pytest.mark.asyncio
    async def test_dispatcher_connects_to_pinned_ip(self, monkeypatch):
        from core.webhooks import ssrf

        monkeypatch.setattr(ssrf, "_resolve_addresses", lambda host: ["93.184.216.34"])
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["host"] = request.headers.get("Host")
            seen["sni"] = request.extensions.get("sni_hostname")
            return httpx.Response(200)

        # allow_internal=False so the dispatcher actually pins.
        cfg = _config(WEBHOOK_ALLOW_INTERNAL=False)
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        disp = WebhookDispatcher(InMemoryWebhookStore(), cfg, http_client=client)
        d = await disp.deliver(
            _endpoint(url="https://example.com/hook"), WebhookEvent(type="chat.done")
        )
        assert d.status == DeliveryStatus.SUCCESS
        assert seen["url"] == "https://93.184.216.34/hook"
        assert seen["host"] == "example.com"
        assert seen["sni"] == "example.com"
        await disp.aclose()


# === Dispatcher ===
class TestDispatcher:
    @pytest.mark.asyncio
    async def test_successful_delivery(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["headers"] = request.headers
            seen["body"] = request.content
            return httpx.Response(200)

        disp = _dispatcher(handler)
        d = await disp.deliver(_endpoint(), WebhookEvent(type="chat.done"))
        assert d.status == DeliveryStatus.SUCCESS
        assert d.attempts == 1
        # Signature header present and valid over the exact body.
        sig = seen["headers"][SIGNATURE_HEADER]
        assert verify_signature("whsec_test", seen["body"], sig, tolerance_seconds=0)
        await disp.aclose()

    @pytest.mark.asyncio
    async def test_retries_then_succeeds(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(500 if calls["n"] == 1 else 200)

        disp = _dispatcher(handler)
        d = await disp.deliver(_endpoint(), WebhookEvent(type="e"))
        assert d.status == DeliveryStatus.SUCCESS
        assert d.attempts == 2
        await disp.aclose()

    @pytest.mark.asyncio
    async def test_exhausts_and_dead_letters(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503)

        disp = _dispatcher(handler, _config(WEBHOOK_MAX_ATTEMPTS=2))
        d = await disp.deliver(_endpoint(), WebhookEvent(type="e"))
        assert d.status == DeliveryStatus.FAILED
        assert d.attempts == 2
        assert d.last_status_code == 503
        # Recorded in the store for inspection/replay.
        stored = await disp._store.get_delivery(d.id)
        assert stored is not None and stored.status == DeliveryStatus.FAILED
        await disp.aclose()

    @pytest.mark.asyncio
    async def test_network_error_retried_then_failed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom")

        disp = _dispatcher(handler, _config(WEBHOOK_MAX_ATTEMPTS=2))
        d = await disp.deliver(_endpoint(), WebhookEvent(type="e"))
        assert d.status == DeliveryStatus.FAILED
        assert d.attempts == 2
        assert "ConnectError" in (d.last_error or "")
        await disp.aclose()

    @pytest.mark.asyncio
    async def test_ssrf_blocked_no_http(self):
        called = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            called["n"] += 1
            return httpx.Response(200)

        # allow_internal False → loopback URL fails closed, no HTTP call.
        disp = _dispatcher(handler, _config(WEBHOOK_ALLOW_INTERNAL=False))
        d = await disp.deliver(
            _endpoint(url="http://127.0.0.1/x"), WebhookEvent(type="e")
        )
        assert d.status == DeliveryStatus.FAILED
        assert "ssrf_blocked" in (d.last_error or "")
        assert called["n"] == 0
        await disp.aclose()


# === Service ===
class TestService:
    def _service(self, handler, config=None):
        cfg = config or _config()
        store = InMemoryWebhookStore()
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        disp = WebhookDispatcher(store, cfg, http_client=client)
        return WebhookService(store=store, config=cfg, dispatcher=disp)

    @pytest.mark.asyncio
    async def test_emit_fans_out(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200)

        svc = self._service(handler)
        await svc.register_endpoint(HOOK_URL, "s1", event_types={"*"})
        await svc.register_endpoint("https://b.test/h", "s2", event_types={"chat.done"})
        deliveries = await svc.emit("chat.done", {"x": 1})
        assert len(deliveries) == 2
        assert all(d.status == DeliveryStatus.SUCCESS for d in deliveries)
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_emit_filters_by_event_type(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200)

        svc = self._service(handler)
        await svc.register_endpoint(HOOK_URL, "s", event_types={"other.event"})
        deliveries = await svc.emit("chat.done", {"x": 1})
        assert deliveries == []
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_emit_disabled_is_noop(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200)

        svc = self._service(handler, _config(WEBHOOKS_ENABLED=False))
        # Register bypasses the enabled flag; emit must still no-op.
        await svc.register_endpoint(HOOK_URL, "s")
        assert await svc.emit("chat.done", {}) == []
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_register_rejects_ssrf(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200)

        svc = self._service(handler, _config(WEBHOOK_ALLOW_INTERNAL=False))
        with pytest.raises(WebhookSSRFError):
            await svc.register_endpoint("http://169.254.169.254/", "s")
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_register_enforces_tenant_cap(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200)

        svc = self._service(handler, _config(WEBHOOK_MAX_ENDPOINTS_PER_TENANT=1))
        await svc.register_endpoint(HOOK_URL, "s")
        with pytest.raises(ValueError, match="cap"):
            await svc.register_endpoint("https://b.test/h", "s2")
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_replay_failed_delivery(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            # Fail on first emit, succeed on replay.
            return httpx.Response(500 if calls["n"] == 1 else 200)

        svc = self._service(handler, _config(WEBHOOK_MAX_ATTEMPTS=1))
        ep = await svc.register_endpoint(HOOK_URL, "s")
        deliveries = await svc.emit("chat.done", {"x": 1})
        assert deliveries[0].status == DeliveryStatus.FAILED
        replayed = await svc.replay_delivery(deliveries[0].id)
        assert replayed is not None
        assert replayed.status == DeliveryStatus.SUCCESS
        assert replayed.endpoint_id == ep.id
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_replay_unknown_returns_none(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200)

        svc = self._service(handler)
        assert await svc.replay_delivery("whd_nope") is None
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_delete_endpoint_rejects_cross_tenant(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200)

        svc = self._service(handler)
        ep = await svc.register_endpoint(HOOK_URL, "s", tenant_id="acme")
        # Wrong tenant → not deleted, treated as not found.
        assert await svc.delete_endpoint(ep.id, tenant_id="other") is False
        assert await svc.store.get_endpoint(ep.id) is not None
        # Correct tenant → deleted.
        assert await svc.delete_endpoint(ep.id, tenant_id="acme") is True
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_replay_rejects_cross_tenant(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)  # force a failed delivery to replay

        svc = self._service(handler, _config(WEBHOOK_MAX_ATTEMPTS=1))
        await svc.register_endpoint(HOOK_URL, "s", tenant_id="acme")
        deliveries = await svc.emit("e", {}, tenant_id="acme")
        did = deliveries[0].id
        assert await svc.replay_delivery(did, tenant_id="other") is None
        # Owner can replay.
        assert await svc.replay_delivery(did, tenant_id="acme") is not None
        await svc.aclose()


# === Endpoint model ===
def test_endpoint_redacted_hides_secret():
    ep = _endpoint()
    red = ep.redacted()
    assert "secret" not in red
    assert red["has_secret"] is True


def test_endpoint_subscribes_to():
    assert _endpoint(event_types={"*"}).subscribes_to("anything")
    assert _endpoint(event_types={"chat.done"}).subscribes_to("chat.done")
    assert not _endpoint(event_types={"chat.done"}).subscribes_to("other")
