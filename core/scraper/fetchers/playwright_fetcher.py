# core/scraper/fetchers/playwright_fetcher.py
"""Playwright-based fetcher for JavaScript-rendered pages.

This fetcher is used when pages require JavaScript execution
(SPAs, dynamic content, etc).
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ..models import ScrapedPage
from ..utils import check_ssrf_safe
from .base import BaseFetcher, FetchError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from core.config.scraper import ScraperConfig

# Lazy import playwright to avoid requiring it when not used
try:
    from playwright.async_api import async_playwright, Browser, BrowserContext

    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    async_playwright = None  # type: ignore
    Browser = None  # type: ignore
    BrowserContext = None  # type: ignore


class PlaywrightFetcher(BaseFetcher):
    """Fetcher implementation using Playwright for JavaScript rendering.

    Features:
    - Full JavaScript execution
    - Configurable wait conditions
    - Screenshot capture
    - Cookie/session management
    - Headless/headed modes
    """

    def __init__(self, config: ScraperConfig | None = None):
        """Initialize the Playwright fetcher.

        Args:
            config: Optional scraper configuration.

        Raises:
            ImportError: If playwright is not installed.
        """
        if not PLAYWRIGHT_AVAILABLE:
            raise ImportError(
                "Playwright is not installed. Install it with: "
                "pip install playwright && playwright install chromium"
            )

        super().__init__(config)
        self._playwright: Any = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    async def _ensure_browser(self) -> BrowserContext:
        """Ensure browser and context are initialized."""
        if self._context is None:
            self._playwright = await async_playwright().start()
            assert self._playwright  # nosec B101
            self._browser = await self._playwright.chromium.launch(
                headless=self.config.playwright_headless,
                args=[
                    "--enable-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )
            self._context = await self._browser.new_context(
                user_agent=self.config.user_agent,
                viewport={"width": 1920, "height": 1080},
            )
        return self._context

    async def fetch(self, url: str) -> ScrapedPage:
        """Fetch a URL using Playwright for JavaScript rendering.

        Args:
            url: The URL to fetch.

        Returns:
            ScrapedPage with the rendered content.

        Raises:
            FetchError: If the fetch fails.
        """
        # SSRF protection
        if not check_ssrf_safe(url):
            raise FetchError(
                url=url,
                message="URL blocked by SSRF protection (private/internal IP)",
                status_code=403,
            )

        start = self._measure_time()

        try:
            context = await self._ensure_browser()
            page = await context.new_page()

            try:
                # Navigate to page
                response = await page.goto(
                    url,
                    wait_until=self.config.playwright_wait_until,
                    timeout=self.config.timeout_seconds * 1000,
                )

                if response is None:
                    raise FetchError(url=url, message="No response received")

                # Get rendered HTML
                html = await page.content()

                # Get headers from response
                headers = dict(response.headers) if response else {}

                return ScrapedPage(
                    url=url,
                    final_url=page.url,
                    status_code=response.status if response else 200,
                    html=html,
                    headers=headers,
                    fetched_at=datetime.now(),
                    fetch_time_ms=self._calc_duration_ms(start),
                    error=None,
                )

            finally:
                await page.close()

        except Exception as e:
            error_msg = str(e)
            if "Timeout" in error_msg:
                raise FetchError(
                    url=url,
                    message=f"Page load timed out after {self.config.timeout_seconds}s",
                    cause=e,
                ) from e
            raise FetchError(
                url=url,
                message=error_msg,
                cause=e,
            ) from e

    async def fetch_many(
        self, urls: list[str], concurrency: int = 3
    ) -> AsyncIterator[ScrapedPage]:
        """Fetch multiple URLs with Playwright.

        Note: Concurrency is limited due to browser resource constraints.

        Args:
            urls: List of URLs to fetch.
            concurrency: Maximum concurrent pages (default 3 for browser).

        Yields:
            ScrapedPage for each fetched URL.
        """
        # Playwright has higher overhead, so limit concurrency
        effective_concurrency = min(concurrency, 3)
        semaphore = asyncio.Semaphore(effective_concurrency)

        async def fetch_with_semaphore(url: str) -> ScrapedPage:
            async with semaphore:
                try:
                    return await self.fetch(url)
                except FetchError as e:
                    return self._create_error_page(url, str(e))

        tasks = [asyncio.create_task(fetch_with_semaphore(url)) for url in urls]

        for task in asyncio.as_completed(tasks):
            yield await task

    async def close(self) -> None:
        """Close browser and release resources."""
        if self._context:
            await self._context.close()
            self._context = None

        if self._browser:
            await self._browser.close()
            self._browser = None

        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

        self._closed = True
