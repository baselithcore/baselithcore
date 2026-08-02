"""SSRF protection tests for the web_scraper plugin.

Covers the Task 5 migration: ``plugins.web_scraper.utils`` now delegates its
public SSRF surface (``check_ssrf_safe``, ``get_pinned_url_for_host``, and
the legacy ``is_private_ip``/``resolve_safe_ips``) to the unified
``core.security.ssrf`` module, and ``PlaywrightFetcher`` gained a route
guard that re-validates every request (not just the pre-``goto`` check),
including sub-resource loads (scripts, images, fetch/XHR).

DNS is always mocked via ``socket.getaddrinfo`` (same pattern as
``tests/unit/core/security/test_ssrf.py``) — no real network I/O.
"""

from __future__ import annotations

import socket
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.config.scraper import ScraperConfig
from plugins.web_scraper.fetchers.playwright_fetcher import PlaywrightFetcher
from plugins.web_scraper.utils import (
    check_ssrf_safe,
    get_pinned_url_for_host,
    is_private_ip,
    resolve_safe_ips,
)

# ``core.scraper.*`` (and ``core.scraper`` itself) are backward-compat shims
# that self-replace via ``sys.modules[__name__] = <plugins.web_scraper module>``
# (see core/scraper/__init__.py and siblings). Once anything in the suite
# imports through that dotted path (e.g. ``core.scraper.tools``), Python's
# import machinery resolves the submodule using the *aliased* parent's
# ``__path__`` — which points at ``plugins/web_scraper`` — and re-executes
# the target file fresh under the ``core.scraper.<name>`` key. The loader
# then does its normal "set submodule as attribute on parent" step, and
# since the parent is literally the same object as the ``plugins.web_scraper``
# package, this silently clobbers the ``plugins.web_scraper.<name>``
# *attribute* with that independently-executed twin module (though the
# canonical ``sys.modules["plugins.web_scraper.<name>"]`` entry is untouched).
#
# ``pytest.MonkeyPatch.setattr("plugins.web_scraper.utils.x", ...)`` resolves
# its dotted string via attribute-chain traversal (``_pytest.monkeypatch.
# resolve``), so once that clobbering has happened anywhere else in the test
# session it silently patches the twin instead of the module that
# ``check_ssrf_safe``/``PlaywrightFetcher`` actually read their globals from
# — a real bug, not just test pollution, since it also affects `resolve()`
# in production monkeypatch-based tooling. Patch through ``sys.modules[...]``
# (a plain dict lookup, immune to the attribute clobbering) instead, so the
# module object we mutate is guaranteed to be the same one the code under
# test resolves its globals against, regardless of collection order.
_UTILS_MODULE = sys.modules["plugins.web_scraper.utils"]
_PLAYWRIGHT_FETCHER_MODULE = sys.modules["plugins.web_scraper.fetchers.playwright_fetcher"]


def _fake_getaddrinfo(mapping: dict[str, list[str]]):
    """Build a fake ``socket.getaddrinfo`` resolving only the given hosts."""

    def fake(host, port, *args, **kwargs):
        if host not in mapping:
            raise socket.gaierror(f"unknown host {host}")
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in mapping[host]
        ]

    return fake


@pytest.fixture
def strict_scraper_config(monkeypatch: pytest.MonkeyPatch) -> ScraperConfig:
    """Force ``block_private_ips=True`` regardless of environment/global state."""
    config = ScraperConfig(block_private_ips=True)
    monkeypatch.setattr(_UTILS_MODULE, "get_scraper_config", lambda: config)
    return config


class TestCheckSsrfSafeDelegation:
    """``check_ssrf_safe``/``get_pinned_url_for_host`` now delegate to core."""

    def test_public_host_is_safe(self, strict_scraper_config, monkeypatch):
        monkeypatch.setattr(
            "socket.getaddrinfo", _fake_getaddrinfo({"example.com": ["93.184.216.34"]})
        )
        assert check_ssrf_safe("http://example.com/page") is True

    def test_private_ip_target_is_blocked(self, strict_scraper_config, monkeypatch):
        monkeypatch.setattr(
            "socket.getaddrinfo", _fake_getaddrinfo({"internal.example": ["10.0.0.5"]})
        )
        assert check_ssrf_safe("http://internal.example/") is False

    def test_metadata_endpoint_literal_ip_is_blocked(self, strict_scraper_config):
        # 169.254.169.254 is a literal IP: blocked before any DNS resolution.
        assert check_ssrf_safe("http://169.254.169.254/latest/meta-data") is False

    def test_cgnat_range_is_blocked(self, strict_scraper_config, monkeypatch):
        # RFC 6598 CGNAT (100.64.0.0/10): the core guard is stricter than the
        # scraper's pre-migration literal checks, which did not cover it.
        monkeypatch.setattr(
            "socket.getaddrinfo", _fake_getaddrinfo({"cgnat.example": ["100.64.0.5"]})
        )
        assert check_ssrf_safe("http://cgnat.example/") is False

    def test_localhost_literal_is_blocked(self, strict_scraper_config):
        # Literal "localhost" hostname: blocked by core's hostname_is_blocked_literal
        # without needing DNS resolution.
        assert check_ssrf_safe("http://localhost/admin") is False

    def test_dns_resolution_failure_is_blocked(self, strict_scraper_config, monkeypatch):
        monkeypatch.setattr("socket.getaddrinfo", _fake_getaddrinfo({}))
        assert check_ssrf_safe("http://does-not-resolve.example/") is False

    def test_malformed_url_is_blocked_not_raised(self, strict_scraper_config):
        # Regression: core.security.ssrf._parse_and_screen wraps urlparse()'s
        # bare ValueError ("Invalid IPv6 URL") as SsrfError. check_ssrf_safe
        # only catches SsrfError, so before that fix a malformed URL crashed
        # here instead of degrading to False.
        assert check_ssrf_safe("http://[invalid/path") is False

    def test_get_pinned_url_for_host_returns_none_for_malformed_url(
        self, strict_scraper_config
    ):
        assert get_pinned_url_for_host("http://[invalid/path") is None

    def test_allow_internal_when_block_private_ips_disabled(self, monkeypatch):
        config = ScraperConfig(block_private_ips=False)
        monkeypatch.setattr(_UTILS_MODULE, "get_scraper_config", lambda: config)
        # No DNS mock installed: allow_internal short-circuits before any
        # resolution is attempted.
        assert check_ssrf_safe("http://127.0.0.1/") is True

    def test_get_pinned_url_for_host_pins_to_resolved_ip(
        self, strict_scraper_config, monkeypatch
    ):
        monkeypatch.setattr(
            "socket.getaddrinfo", _fake_getaddrinfo({"example.com": ["93.184.216.34"]})
        )
        result = get_pinned_url_for_host("http://example.com/page")
        assert result is not None
        pinned_url, host = result
        assert pinned_url == "http://93.184.216.34/page"
        assert host == "example.com"

    def test_get_pinned_url_for_host_returns_none_when_unsafe(
        self, strict_scraper_config, monkeypatch
    ):
        monkeypatch.setattr(
            "socket.getaddrinfo", _fake_getaddrinfo({"internal.example": ["192.168.1.1"]})
        )
        assert get_pinned_url_for_host("http://internal.example/") is None


class TestLegacyDelegates:
    """``is_private_ip``/``resolve_safe_ips`` stay importable, now as delegates."""

    def test_is_private_ip_true_for_literal_private_address(self):
        assert is_private_ip("127.0.0.1") is True
        assert is_private_ip("localhost") is True

    def test_is_private_ip_false_for_public_address(self):
        assert is_private_ip("93.184.216.34") is False

    def test_resolve_safe_ips_returns_addresses_for_public_host(self, monkeypatch):
        monkeypatch.setattr(
            "socket.getaddrinfo", _fake_getaddrinfo({"example.com": ["93.184.216.34"]})
        )
        assert resolve_safe_ips("example.com") == ["93.184.216.34"]

    def test_resolve_safe_ips_fails_closed_for_private_host(self, monkeypatch):
        monkeypatch.setattr(
            "socket.getaddrinfo", _fake_getaddrinfo({"internal.example": ["10.0.0.1"]})
        )
        assert resolve_safe_ips("internal.example") == []

    def test_resolve_safe_ips_fails_closed_on_resolution_failure(self, monkeypatch):
        monkeypatch.setattr("socket.getaddrinfo", _fake_getaddrinfo({}))
        assert resolve_safe_ips("does-not-resolve.example") == []


class TestPlaywrightRouteGuard:
    """``PlaywrightFetcher._ssrf_route_guard`` validates every request.

    The guard doesn't touch ``self``, so the fetcher instance is built via
    ``__new__`` to avoid requiring the (test-environment-absent) playwright
    package.
    """

    @pytest.fixture
    def fetcher(self) -> PlaywrightFetcher:
        return PlaywrightFetcher.__new__(PlaywrightFetcher)

    @pytest.mark.asyncio
    async def test_playwright_route_guard_blocks_subresource(
        self, fetcher, strict_scraper_config, monkeypatch
    ):
        """fetch/img toward the metadata endpoint is aborted, not just navigation."""
        monkeypatch.setattr(
            "socket.getaddrinfo", _fake_getaddrinfo({"page.example": ["93.184.216.34"]})
        )
        route = AsyncMock()
        request = MagicMock()
        request.url = "http://169.254.169.254/latest/meta-data"

        await fetcher._ssrf_route_guard(route, request)

        route.abort.assert_awaited_once_with("blockedbyclient")
        route.continue_.assert_not_called()

    @pytest.mark.asyncio
    async def test_playwright_route_guard_allows_safe_subresource(
        self, fetcher, strict_scraper_config, monkeypatch
    ):
        monkeypatch.setattr(
            "socket.getaddrinfo",
            _fake_getaddrinfo({"cdn.example": ["93.184.216.34"]}),
        )
        route = AsyncMock()
        request = MagicMock()
        request.url = "http://cdn.example/script.js"

        await fetcher._ssrf_route_guard(route, request)

        route.continue_.assert_awaited_once()
        route.abort.assert_not_called()

    @pytest.mark.asyncio
    async def test_playwright_route_guard_fails_closed_on_unexpected_error(
        self, fetcher, strict_scraper_config, monkeypatch
    ):
        async def _boom(*args, **kwargs):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(
            _PLAYWRIGHT_FETCHER_MODULE,
            "assert_url_safe_async",
            _boom,
        )
        route = AsyncMock()
        request = MagicMock()
        request.url = "http://example.com/script.js"

        await fetcher._ssrf_route_guard(route, request)

        route.abort.assert_awaited_once_with("blockedbyclient")
        route.continue_.assert_not_called()
