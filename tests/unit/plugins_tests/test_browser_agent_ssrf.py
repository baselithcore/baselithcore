"""Tests for the BrowserAgent SSRF guard."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import plugins.browser_agent.agent as agent_mod
from plugins.browser_agent.agent import (
    BrowserAgent,
    _hostname_is_blocked,
    _hostname_resolves_to_internal,
    _ssrf_guard_disabled,
    _url_is_blocked,
    assert_navigation_allowed,
)


def _fake_getaddrinfo(ip: str):
    """Return a getaddrinfo stub that resolves any host to ``ip``."""

    def _inner(host, *args, **kwargs):
        return [(2, 1, 6, "", (ip, 0))]

    return _inner


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/path",
        "http://api.openai.com/v1/chat",
        "https://8.8.8.8/resource",
    ],
)
def test_allows_public_urls(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    monkeypatch.delenv("BASELITH_BROWSER_ALLOW_INTERNAL", raising=False)
    assert_navigation_allowed(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost",
        "http://localhost:8080/admin",
        "http://service.localhost",
        "http://127.0.0.1",
        "http://10.0.0.5",
        "http://192.168.1.1",
        "http://172.16.0.1",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]",
    ],
)
def test_blocks_internal_hosts(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    monkeypatch.delenv("BASELITH_BROWSER_ALLOW_INTERNAL", raising=False)
    with pytest.raises(ValueError):
        assert_navigation_allowed(url)


@pytest.mark.parametrize("scheme", ["file", "ftp", "javascript", "data"])
def test_blocks_disallowed_schemes(
    monkeypatch: pytest.MonkeyPatch, scheme: str
) -> None:
    monkeypatch.delenv("BASELITH_BROWSER_ALLOW_INTERNAL", raising=False)
    with pytest.raises(ValueError):
        assert_navigation_allowed(f"{scheme}://anything")


def test_opt_out_env_disables_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BASELITH_BROWSER_ALLOW_INTERNAL", "true")
    assert _ssrf_guard_disabled() is True
    # Should not raise
    assert_navigation_allowed("http://localhost:8080")
    assert_navigation_allowed("http://169.254.169.254/")


def test_hostname_helpers() -> None:
    assert _hostname_is_blocked("localhost") is True
    assert _hostname_is_blocked("127.0.0.1") is True
    assert _hostname_is_blocked("10.0.0.1") is True
    assert _hostname_is_blocked("192.168.0.1") is True
    assert _hostname_is_blocked("169.254.0.1") is True
    assert _hostname_is_blocked("8.8.8.8") is False
    assert _hostname_is_blocked("example.com") is False
    assert _hostname_is_blocked("") is True


def test_literal_check_blocks_ipv4_mapped_ipv6() -> None:
    # ::ffff:169.254.169.254 embeds the link-local metadata IP.
    assert _hostname_is_blocked("::ffff:169.254.169.254") is True
    assert _hostname_is_blocked("[::ffff:127.0.0.1]") is True


def test_dns_resolution_blocks_rebind_to_internal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A public-looking domain whose DNS resolves to the cloud metadata IP.
    monkeypatch.setattr(
        agent_mod.socket, "getaddrinfo", _fake_getaddrinfo("169.254.169.254")
    )
    assert _hostname_resolves_to_internal("evil.example.com") is True
    assert _url_is_blocked("https://evil.example.com/steal", resolve_dns=True) is True
    # Without DNS resolution the literal pre-check lets it through.
    assert _url_is_blocked("https://evil.example.com/steal") is False


def test_dns_resolution_allows_public(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        agent_mod.socket, "getaddrinfo", _fake_getaddrinfo("93.184.216.34")
    )
    assert _hostname_resolves_to_internal("example.com") is False
    assert _url_is_blocked("https://example.com/", resolve_dns=True) is False


def test_dns_resolution_normalizes_decimal_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # http://2130706433/ → getaddrinfo normalizes to 127.0.0.1.
    monkeypatch.setattr(agent_mod.socket, "getaddrinfo", _fake_getaddrinfo("127.0.0.1"))
    assert _url_is_blocked("http://2130706433/", resolve_dns=True) is True


def test_dns_failure_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket as _socket

    def _boom(*a, **k):
        raise _socket.gaierror("no such host")

    monkeypatch.setattr(agent_mod.socket, "getaddrinfo", _boom)
    assert _hostname_resolves_to_internal("nonexistent.invalid") is True


class TestSsrfRouteGuard:
    """``BrowserAgent._ssrf_route_guard`` validates every request.

    Before this migration the guard only ran for ``request.is_navigation_request()
    == True`` requests, so a sub-resource fetch/XHR/img toward an internal host
    was let through unchecked. It now validates every request the page issues,
    with a per-host verdict cache to avoid a DNS lookup per asset.
    """

    @pytest.fixture
    def guard_agent(self) -> BrowserAgent:
        agent = BrowserAgent.__new__(BrowserAgent)
        agent._ssrf_host_cache = {}
        return agent

    @pytest.mark.asyncio
    async def test_route_guard_blocks_subresource_to_metadata(
        self, guard_agent: BrowserAgent, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A sub-resource request (``is_navigation_request() -> False``) to the
        cloud metadata endpoint previously passed through unchecked; it must
        now be aborted like any other request."""
        monkeypatch.setattr(
            agent_mod.socket, "getaddrinfo", _fake_getaddrinfo("169.254.169.254")
        )
        route = AsyncMock()
        request = MagicMock()
        request.url = "http://169.254.169.254/latest"
        request.is_navigation_request.return_value = False

        await guard_agent._ssrf_route_guard(route, request)

        route.abort.assert_awaited_once_with("blockedbyclient")
        route.continue_.assert_not_called()

    @pytest.mark.asyncio
    async def test_route_guard_allows_safe_subresource(
        self, guard_agent: BrowserAgent, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            agent_mod.socket, "getaddrinfo", _fake_getaddrinfo("93.184.216.34")
        )
        route = AsyncMock()
        request = MagicMock()
        request.url = "https://cdn.example.com/script.js"
        request.is_navigation_request.return_value = False

        await guard_agent._ssrf_route_guard(route, request)

        route.continue_.assert_awaited_once()
        route.abort.assert_not_called()

    @pytest.mark.asyncio
    async def test_route_guard_fails_closed_on_unexpected_error(
        self, guard_agent: BrowserAgent, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom(*args, **kwargs):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(agent_mod, "_url_is_blocked", _boom)
        route = AsyncMock()
        request = MagicMock()
        request.url = "https://example.com/script.js"
        request.is_navigation_request.return_value = False

        await guard_agent._ssrf_route_guard(route, request)

        route.abort.assert_awaited_once_with("blockedbyclient")
        route.continue_.assert_not_called()

    @pytest.mark.asyncio
    async def test_route_guard_caches_verdict_per_host(
        self, guard_agent: BrowserAgent, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A second request to an already-seen host must not re-resolve DNS."""
        calls = {"n": 0}

        def _counting_getaddrinfo(host, *args, **kwargs):
            calls["n"] += 1
            return [(2, 1, 6, "", ("93.184.216.34", 0))]

        monkeypatch.setattr(agent_mod.socket, "getaddrinfo", _counting_getaddrinfo)
        route1, route2 = AsyncMock(), AsyncMock()
        request1, request2 = MagicMock(), MagicMock()
        request1.url = "https://cdn.example.com/a.js"
        request2.url = "https://cdn.example.com/b.js"
        request1.is_navigation_request.return_value = False
        request2.is_navigation_request.return_value = False

        await guard_agent._ssrf_route_guard(route1, request1)
        await guard_agent._ssrf_route_guard(route2, request2)

        assert calls["n"] == 1
        route1.continue_.assert_awaited_once()
        route2.continue_.assert_awaited_once()

    def test_reset_ssrf_host_cache_on_top_level_navigation(
        self, guard_agent: BrowserAgent
    ) -> None:
        guard_agent._ssrf_host_cache = {"cdn.example.com": False}
        top_level_frame = MagicMock()
        top_level_frame.parent_frame = None

        guard_agent._reset_ssrf_host_cache_on_navigation(top_level_frame)

        assert guard_agent._ssrf_host_cache == {}

    def test_reset_ssrf_host_cache_ignores_subframe_navigation(
        self, guard_agent: BrowserAgent
    ) -> None:
        guard_agent._ssrf_host_cache = {"cdn.example.com": False}
        iframe_frame = MagicMock()
        iframe_frame.parent_frame = MagicMock()  # has a parent: not top-level

        guard_agent._reset_ssrf_host_cache_on_navigation(iframe_frame)

        assert guard_agent._ssrf_host_cache == {"cdn.example.com": False}
