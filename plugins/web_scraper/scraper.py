"""
High-Fidelity Web Scrambling and Extraction Facade.

Provides a unified interface for complex web data ingestion. Orchestrates
asynchronous fetchers (Playwright for JS-heavy sites, HTTPX for speed),
a modular middleware chain (caching, rate-limiting, logging), and a suite
of specialized extractors for structured data.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from core.config.scraper import ScraperConfig, get_scraper_config
from core.observability.logging import get_logger

from .extractors import (
    CssSelectorExtractor,
    ImageExtractor,
    LinkExtractor,
    MetadataExtractor,
    SchemaOrgExtractor,
    TextExtractor,
)
from .fetchers import HttpxFetcher, PlaywrightFetcher
from .middleware import (
    CacheMiddleware,
    LoggingMiddleware,
    MiddlewareChain,
    RateLimiterMiddleware,
)
from .models import ExtractedData, ScrapedPage

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


logger = get_logger(__name__)


class Scraper:
    """
    Orchestrator for automated web data extraction.

    Acts as the primary entry point for scraping operations. Manages resource
    lifecycles for browser instances, handles concurrent request steering,
    and synthesizes results from multiple extraction strategies (CSS, Meta,
    Schema.org, etc.) into a normalized data model.
    """

    # Available extractors by name
    EXTRACTORS = {
        "text": TextExtractor,
        "links": LinkExtractor,
        "images": ImageExtractor,
        "metadata": MetadataExtractor,
        "schema_org": SchemaOrgExtractor,
        "css_selector": CssSelectorExtractor,
    }

    def __init__(
        self,
        config: ScraperConfig | None = None,
        use_cache: bool = True,
        use_rate_limiter: bool = True,
        use_logging: bool = True,
    ):
        """Initialize the scraper.

        Args:
            config: Scraper configuration.
            use_cache: Enable response caching.
            use_rate_limiter: Enable rate limiting.
            use_logging: Enable request logging.
        """
        self.config = config or get_scraper_config()

        # Initialize fetchers
        self._httpx_fetcher: HttpxFetcher | None = None
        self._playwright_fetcher: PlaywrightFetcher | None = None

        # Build middleware chain
        self.middleware = MiddlewareChain()
        self._cache_middleware: CacheMiddleware | None = None

        if use_cache and self.config.cache_enabled:
            self._cache_middleware = CacheMiddleware()
            self.middleware.add(self._cache_middleware)

        if use_rate_limiter and self.config.rate_limit_enabled:
            self.middleware.add(RateLimiterMiddleware())

        if use_logging and self.config.log_requests:
            self.middleware.add(LoggingMiddleware())

        # Initialize extractors
        self._extractors: dict[str, object] = {}

    async def _get_fetcher(self, use_js: bool = False):
        """Get or create the appropriate fetcher.

        Args:
            use_js: Whether to use JavaScript rendering.

        Returns:
            Fetcher instance.
        """
        if use_js or self.config.default_fetcher == "playwright":
            if self._playwright_fetcher is None:
                self._playwright_fetcher = PlaywrightFetcher(self.config)
            return self._playwright_fetcher
        else:
            if self._httpx_fetcher is None:
                self._httpx_fetcher = HttpxFetcher(self.config)
            return self._httpx_fetcher

    def _get_extractor(self, name: str):
        """Get or create an extractor by name.

        Args:
            name: Extractor name.

        Returns:
            Extractor instance.
        """
        if name not in self._extractors:
            extractor_class = self.EXTRACTORS.get(name)
            if extractor_class:
                self._extractors[name] = extractor_class()
        return self._extractors.get(name)

    async def scrape(
        self,
        url: str,
        extractors: list[str] | None = None,
        use_js: bool = False,
    ) -> tuple[ScrapedPage, ExtractedData]:
        """Scrape a single URL.

        Args:
            url: The URL to scrape.
            extractors: List of extractor names to use.
            use_js: Whether to use JavaScript rendering.

        Returns:
            Tuple of (ScrapedPage, ExtractedData).
        """
        extractors = extractors or ["text", "links", "metadata"]

        # Check cache first
        if self._cache_middleware:
            cached = self._cache_middleware.get_cached(url)
            if cached:
                # Re-extract data from cached page
                data = await self._extract_async(cached, url, extractors)
                return cached, data

        # Fetch the page
        fetcher = await self._get_fetcher(use_js)

        # Apply middleware
        processed_url = await self.middleware.process_request(url)
        if processed_url is None:
            # Request was blocked
            page = ScrapedPage(
                url=url,
                final_url=url,
                status_code=0,
                html="",
                error="Request blocked by middleware",
            )
            return page, ExtractedData()

        # Fetch
        page = await fetcher.fetch(processed_url)

        # Process response through middleware
        page = await self.middleware.process_response(url, page)

        # Extract data
        data = await self._extract_async(page, url, extractors)

        return page, data

    async def _extract_async(
        self,
        page: ScrapedPage,
        base_url: str,
        extractor_names: list[str],
    ) -> ExtractedData:
        """Run :meth:`_extract` in a worker thread.

        Extraction is synchronous BeautifulSoup parsing, once per extractor,
        of a body the fetcher allows up to 10 MB: on the event loop that is
        hundreds of milliseconds to seconds of CPU during which every other
        request in the process waits.
        """
        return await asyncio.to_thread(self._extract, page, base_url, extractor_names)

    def _extract(
        self,
        page: ScrapedPage,
        base_url: str,
        extractor_names: list[str],
    ) -> ExtractedData:
        """Extract data from a page.

        Args:
            page: The scraped page.
            base_url: Base URL for resolving.
            extractor_names: Names of extractors to use.

        Returns:
            ExtractedData with extracted content.
        """
        data = ExtractedData()

        for name in extractor_names:
            extractor = self._get_extractor(name)
            if extractor is None:
                continue

            try:
                result = extractor.extract(page, base_url)

                if name == "text":
                    data.text = result
                elif name == "links":
                    data.links = result
                elif name == "images":
                    data.images = result
                elif name == "metadata":
                    data.metadata = result
                elif name == "schema_org":
                    data.schema_org = result
                elif name == "css_selector":
                    data.custom.update(result if result else {})
            except Exception:
                # Log but continue with other extractors
                pass  # nosec B110

        return data

    async def scrape_many(
        self,
        urls: list[str],
        extractors: list[str] | None = None,
        use_js: bool = False,
        concurrency: int = 5,
    ) -> AsyncIterator[tuple[ScrapedPage, ExtractedData]]:
        """Scrape multiple URLs concurrently.

        Args:
            urls: List of URLs to scrape.
            extractors: List of extractor names to use.
            use_js: Whether to use JavaScript rendering.
            concurrency: Maximum concurrent requests.

        Yields:
            Tuple of (ScrapedPage, ExtractedData) for each URL.
        """
        extractors = extractors or ["text", "links", "metadata"]
        fetcher = await self._get_fetcher(use_js)

        async for page in fetcher.fetch_many(urls, concurrency):
            # Process through middleware
            page = await self.middleware.process_response(page.url, page)
            data = await self._extract_async(page, page.url, extractors)
            yield page, data

    async def close_fetchers(self) -> None:
        """Close the fetchers this scraper opened, keeping shared clients.

        Use this for a short-lived scraper in a long-lived process: unlike
        :meth:`close` it leaves the process-wide robots.txt client open for
        every other scraper. Each fetcher is closed even if the other fails.
        """
        fetchers = (self._httpx_fetcher, self._playwright_fetcher)
        self._httpx_fetcher = None
        self._playwright_fetcher = None
        for fetcher in fetchers:
            if fetcher is None:
                continue
            try:
                await fetcher.close()
            except Exception as exc:
                logger.warning(
                    "scraper_fetcher_close_failed",
                    fetcher=type(fetcher).__name__,
                    error=type(exc).__name__,
                )

    async def close(self) -> None:
        """Close all resources, including the shared robots.txt client."""
        await self.close_fetchers()
        from ._http_pool import close_robots_client

        await close_robots_client()

    async def __aenter__(self) -> Scraper:
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit: release this scraper's own fetchers.

        Deliberately *not* :meth:`close`: that also closes the process-wide
        robots.txt client, and a ``with Scraper()`` block ending (every
        ``web_scrape`` tool call, every crawl) used to close it under any
        concurrent crawl still reading robots.txt through it — the fetch then
        failed and the crawl silently proceeded with *no* robots rules. The
        shared client is closed by the plugin's ``shutdown`` instead.
        """
        await self.close_fetchers()
