"""``scrape_url`` releases the fetchers each call opens.

The tool builds a fresh ``Scraper`` per call. It never closed it, so every
call leaked an ``httpx.AsyncClient`` — and a whole browser when
``wait_for_js`` was set. ``Scraper.close()`` is not the fix either: it also
closes the robots.txt client shared process-wide by every scraper.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from core.mcp.server import MCPServer
from core.mcp.tools import MCPToolAdapter


class _FakeScraper:
    """Scraper double that opens its fetchers lazily like the real one."""

    instances: list[_FakeScraper] = []

    def __init__(self, *, fail: bool = False) -> None:
        self._httpx_fetcher: Any = None
        self._playwright_fetcher: Any = None
        self.close = AsyncMock()  # the shared-robots-closing method
        self.close_fetchers = AsyncMock()
        self._fail = fail
        _FakeScraper.instances.append(self)

    async def scrape(self, url: str, use_js: bool = False) -> tuple[Any, Any]:
        fetcher = SimpleNamespace(close=AsyncMock())
        if use_js:
            self._playwright_fetcher = fetcher
        else:
            self._httpx_fetcher = fetcher
        if self._fail:
            raise RuntimeError("upstream down")
        extracted = SimpleNamespace(
            metadata=SimpleNamespace(title="T"), text="body", links=[]
        )
        return object(), extracted


def _scrape_tool() -> Any:
    server = MCPServer()
    MCPToolAdapter(server).register_scraper_tools()
    return server._tools["scrape_url"].handler


@pytest.fixture(autouse=True)
def _reset() -> None:
    _FakeScraper.instances.clear()


@pytest.mark.parametrize("use_js", [False, True])
async def test_fetcher_is_closed_after_a_successful_scrape(use_js: bool) -> None:
    with patch("core.scraper.scraper.Scraper", _FakeScraper):
        result = await _scrape_tool()("https://example.com", wait_for_js=use_js)

    assert result["status"] == "success"
    scraper = _FakeScraper.instances[0]
    scraper.close_fetchers.assert_awaited_once()
    scraper.close.assert_not_awaited()


async def test_fetcher_is_closed_when_the_scrape_fails() -> None:
    with patch("core.scraper.scraper.Scraper", lambda: _FakeScraper(fail=True)):
        result = await _scrape_tool()("https://example.com")

    assert result["status"] == "error"
    scraper = _FakeScraper.instances[0]
    scraper.close_fetchers.assert_awaited_once()
    scraper.close.assert_not_awaited()


async def test_close_fetchers_keeps_going_past_a_failing_fetcher() -> None:
    from core.scraper.scraper import Scraper

    broken = SimpleNamespace(close=AsyncMock(side_effect=RuntimeError("boom")))
    healthy = SimpleNamespace(close=AsyncMock())
    scraper = Scraper()
    scraper._httpx_fetcher = broken  # type: ignore[assignment]
    scraper._playwright_fetcher = healthy  # type: ignore[assignment]

    with patch("plugins.web_scraper._http_pool.close_robots_client") as robots:
        await scraper.close_fetchers()

    healthy.close.assert_awaited_once()
    robots.assert_not_called()
    assert scraper._httpx_fetcher is None
    assert scraper._playwright_fetcher is None
