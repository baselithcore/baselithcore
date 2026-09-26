"""SSRF guard for the web document source.

Crawled links and server-driven redirects are attacker-influenced URLs, so the
crawler must pass every fetch — including each redirect hop and every
Playwright navigation — through :mod:`core.security.ssrf` instead of trusting
the cheap string-prefix filter alone.
"""

from __future__ import annotations

import importlib

import httpx
import pytest

from plugins.document_sources.web import WebDocumentSource

# Literal public IP: no DNS resolution involved, no real connection made.
PUBLIC_URL = "http://93.184.216.34/"


def _source_with_transport(handler) -> WebDocumentSource:
    source = WebDocumentSource([PUBLIC_URL])
    old_client = source._client
    source._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers=old_client.headers,
        follow_redirects=False,
        timeout=5.0,
    )
    return source


class TestHttpxFetchGuard:
    async def test_internal_url_is_never_requested(self) -> None:
        requested: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url))
            return httpx.Response(200, text="x")

        source = _source_with_transport(handler)
        try:
            result = await source._fetch_with_httpx("http://169.254.169.254/latest")
        finally:
            await source.close()
        assert result is None
        assert requested == []

    async def test_redirect_to_internal_target_is_blocked(self) -> None:
        requested: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url))
            return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})

        source = _source_with_transport(handler)
        try:
            result = await source._fetch_with_httpx(PUBLIC_URL)
        finally:
            await source.close()
        assert result is None
        assert requested == [PUBLIC_URL]  # the internal hop was never fetched

    async def test_safe_redirect_chain_is_followed(self) -> None:
        final = PUBLIC_URL + "final"

        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == PUBLIC_URL:
                return httpx.Response(301, headers={"location": final})
            return httpx.Response(200, text="hello world")

        source = _source_with_transport(handler)
        try:
            result = await source._fetch_with_httpx(PUBLIC_URL)
        finally:
            await source.close()
        assert result == ("hello world", final)


class _RecordingPage:
    """Minimal Playwright page stand-in."""

    def __init__(self, final_url: str = PUBLIC_URL) -> None:
        self.goto_calls: list[str] = []
        self.url = final_url

    async def goto(self, url: str, **_: object) -> None:
        self.goto_calls.append(url)

    async def wait_for_load_state(self, **_: object) -> None:
        return None

    async def content(self) -> str:
        return "<html><body>rendered</body></html>"


class TestPlaywrightGuard:
    async def test_internal_url_is_never_navigated(self) -> None:
        source = WebDocumentSource([PUBLIC_URL])
        page = _RecordingPage()
        try:
            result = await source._render_with_playwright(
                page, "http://169.254.169.254/latest"
            )
        finally:
            await source.close()
        assert result is None
        assert page.goto_calls == []

    async def test_navigation_landing_on_internal_host_is_discarded(self) -> None:
        """A server-side redirect can land the browser on an internal host;
        the rendered content must then be thrown away."""
        source = WebDocumentSource([PUBLIC_URL])
        page = _RecordingPage(final_url="http://127.0.0.1/admin")
        try:
            result = await source._render_with_playwright(page, PUBLIC_URL)
        finally:
            await source.close()
        assert result is None


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/x",
        "http://[::1]/x",
        "http://10.0.0.8/x",
        "http://100.64.0.1/x",  # RFC 6598 CGNAT — not in the string-prefix list
    ],
)
async def test_fetch_refuses_other_internal_shapes(url: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError(f"unexpected request to {request.url}")

    source = _source_with_transport(handler)
    try:
        assert await source._fetch_with_httpx(url) is None
    finally:
        await source.close()


class TestHardening:
    def test_client_pins_every_request(self) -> None:
        """The production client re-validates and IP-pins at the transport,
        closing the rebinding window between screen and connect."""
        from core.security.http import SsrfBlockingTransport

        source = WebDocumentSource([PUBLIC_URL])
        try:
            assert isinstance(source._client._transport, SsrfBlockingTransport)
        finally:
            import anyio

            anyio.run(source.close)

    async def test_oversized_body_is_refused(self, monkeypatch) -> None:
        web_module = importlib.import_module("plugins.document_sources.web")
        monkeypatch.setattr(web_module, "WEB_DOCUMENTS_MAX_BODY_BYTES", 1024)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"x" * 4096)

        source = _source_with_transport(handler)
        try:
            assert await source._fetch_with_httpx(PUBLIC_URL) is None
        finally:
            await source.close()

    async def test_declared_oversized_body_is_refused(self, monkeypatch) -> None:
        web_module = importlib.import_module("plugins.document_sources.web")
        monkeypatch.setattr(web_module, "WEB_DOCUMENTS_MAX_BODY_BYTES", 1024)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, content=b"small", headers={"Content-Length": "999999"}
            )

        source = _source_with_transport(handler)
        try:
            assert await source._fetch_with_httpx(PUBLIC_URL) is None
        finally:
            await source.close()


class TestBrowserRouteGuard:
    async def test_internal_subresource_is_aborted(self) -> None:
        from unittest.mock import AsyncMock, MagicMock

        from plugins.document_sources.web_guard import ssrf_route_guard

        route = AsyncMock()
        request = MagicMock(url="http://169.254.169.254/latest/meta-data")
        await ssrf_route_guard(route, request)
        route.abort.assert_awaited_once_with("blockedbyclient")
        route.continue_.assert_not_called()

    async def test_public_subresource_continues(self) -> None:
        from unittest.mock import AsyncMock, MagicMock

        from plugins.document_sources.web_guard import ssrf_route_guard

        route = AsyncMock()
        request = MagicMock(url=PUBLIC_URL + "app.js")
        await ssrf_route_guard(route, request)
        route.continue_.assert_awaited_once()
        route.abort.assert_not_called()


async def test_page_parsing_runs_off_the_event_loop(monkeypatch) -> None:
    import threading

    web_module = importlib.import_module("plugins.document_sources.web")
    loop_thread = threading.get_ident()
    threads: list[int] = []

    def fake_parse(*_args):
        threads.append(threading.get_ident())
        return None

    monkeypatch.setattr(web_module, "parse_page", fake_parse)
    source = WebDocumentSource([PUBLIC_URL])

    async def fake_fetch(url, page):
        return "<html></html>", PUBLIC_URL

    monkeypatch.setattr(source, "_fetch_page", fake_fetch)
    try:
        items = [i async for i in source._crawl_seed(PUBLIC_URL, None)]
    finally:
        await source.close()
    assert items == []
    assert threads and threads[0] != loop_thread
