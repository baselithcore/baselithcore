"""
High-Fidelity Web Scrambling and Extraction Facade.

Provides a unified interface for complex web data ingestion. Orchestrates
asynchronous fetchers (Playwright for JS-heavy sites, HTTPX for speed),
a modular middleware chain (caching, rate-limiting, logging), and a suite
of specialized extractors for structured data.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.config.scraper import ScraperConfig, get_scraper_config
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
                data = self._extract(cached, url, extractors)
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
        data = self._extract(page, url, extractors)

        return page, data

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
            data = self._extract(page, page.url, extractors)
            yield page, data

    async def close(self) -> None:
        """Close all resources."""
        if self._httpx_fetcher:
            await self._httpx_fetcher.close()
        if self._playwright_fetcher:
            await self._playwright_fetcher.close()

    async def __aenter__(self) -> Scraper:
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        await self.close()
