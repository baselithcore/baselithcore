# core/scraper/crawler.py
"""Crawl engine for recursive website crawling."""

from __future__ import annotations

from collections import deque
from datetime import datetime
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import httpx

from core.config.scraper import ScraperConfig, get_scraper_config
from .models import CrawlError, CrawlResult, CrawlStats, ExtractedData, ScrapedPage
from .scraper import Scraper
from .utils import (
    extract_domain,
    is_blocked_extension,
    is_url_allowed_by_robots,
    is_valid_url,
    normalize_url,
    parse_robots_txt,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class CrawlEngine:
    """Engine for crawling websites recursively.

    Uses BFS traversal with configurable depth and page limits.
    """

    def __init__(
        self,
        config: ScraperConfig | None = None,
        max_depth: int | None = None,
        max_pages: int | None = None,
        follow_external: bool = False,
        extractors: list[str] | None = None,
    ):
        """Initialize the crawl engine.

        Args:
            config: Scraper configuration.
            max_depth: Maximum crawl depth (overrides config).
            max_pages: Maximum pages to crawl (overrides config).
            follow_external: Whether to follow external links.
            extractors: Extractors to use for each page.
        """
        self.config = config or get_scraper_config()
        self.max_depth = max_depth or self.config.max_depth
        self.max_pages = max_pages or self.config.max_pages
        self.follow_external = follow_external
        self.extractors = extractors or ["text", "links", "metadata"]

        # Crawl state
        self._visited: set[str] = set()
        self._robots_cache: dict[str, dict[str, list[str]]] = {}

    async def crawl(
        self,
        seed_url: str,
        use_js: bool = False,
    ) -> AsyncIterator[tuple[ScrapedPage, ExtractedData]]:
        """Crawl a website starting from seed URL.

        Args:
            seed_url: Starting URL for the crawl.
            use_js: Use JavaScript rendering for all pages.

        Yields:
            Tuple of (ScrapedPage, ExtractedData) for each crawled page.
        """
        # Normalize seed URL
        seed_url = normalize_url(seed_url)
        seed_domain = extract_domain(seed_url)

        # Initialize BFS queue: (url, depth)
        queue: deque[tuple[str, int]] = deque([(seed_url, 0)])
        self._visited.clear()

        # Fetch robots.txt if configured
        robots_rules = {}
        if self.config.follow_robots_txt:
            robots_rules = await self._fetch_robots(seed_url)

        async with Scraper(config=self.config) as scraper:
            pages_crawled = 0

            while queue and pages_crawled < self.max_pages:
                url, depth = queue.popleft()

                # Skip if already visited
                if url in self._visited:
                    continue

                # Check robots.txt
                if not is_url_allowed_by_robots(url, robots_rules):
                    continue

                # Skip blocked extensions
                if is_blocked_extension(url):
                    continue

                # Mark as visited
                self._visited.add(url)

                try:
                    # Scrape the page
                    page, data = await scraper.scrape(
                        url,
                        extractors=self.extractors,
                        use_js=use_js,
                    )

                    pages_crawled += 1
                    yield page, data

                    # Add new links to queue if within depth limit
                    if depth < self.max_depth and page.is_success:
                        for link in data.links:
                            # Skip if nofollow and config says to respect it
                            if link.nofollow and self.config.respect_nofollow:
                                continue

                            # Check if should follow link
                            link_domain = extract_domain(link.url)
                            is_same_domain = link_domain == seed_domain

                            if is_same_domain or self.follow_external:
                                normalized = normalize_url(link.url, url)
                                if normalized not in self._visited and is_valid_url(
                                    normalized
                                ):
                                    queue.append((normalized, depth + 1))

                except Exception as e:
                    # Log error but continue crawling
                    pages_crawled += 1
                    yield (
                        ScrapedPage(
                            url=url,
                            final_url=url,
                            status_code=0,
                            html="",
                            error=str(e),
                        ),
                        ExtractedData(),
                    )

    async def crawl_full(
        self,
        seed_url: str,
        use_js: bool = False,
    ) -> CrawlResult:
        """Crawl a website and return complete results.

        Args:
            seed_url: Starting URL for the crawl.
            use_js: Use JavaScript rendering for all pages.

        Returns:
            CrawlResult with all pages and extracted data.
        """
        result = CrawlResult(
            seed_url=seed_url,
            stats=CrawlStats(start_time=datetime.now()),
        )

        total_bytes = 0
        total_time_ms = 0.0

        async for page, data in self.crawl(seed_url, use_js=use_js):
            result.pages.append(page)
            result.extracted[page.url] = data

            if page.is_success:
                result.stats.pages_crawled += 1
                total_bytes += len(page.html)
                total_time_ms += page.fetch_time_ms
            else:
                result.stats.pages_failed += 1
                result.errors.append(
                    CrawlError(
                        url=page.url,
                        error_type="fetch_error",
                        message=page.error or "Unknown error",
                    )
                )

        # Finalize stats
        result.stats.end_time = datetime.now()
        result.stats.total_bytes = total_bytes
        if result.stats.pages_crawled > 0:
            result.stats.avg_response_time_ms = (
                total_time_ms / result.stats.pages_crawled
            )

        return result

    async def _fetch_robots(self, seed_url: str) -> dict[str, list[str]]:
        """Fetch and parse robots.txt for a domain.

        Args:
            seed_url: URL to get robots.txt for.

        Returns:
            Parsed robots.txt rules.
        """
        parsed = urlparse(seed_url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"

        from core.scraper.utils import check_ssrf_safe

        if not check_ssrf_safe(robots_url):
            return {"allow": [], "disallow": []}

        domain = parsed.netloc
        if domain in self._robots_cache:
            return self._robots_cache[domain]

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(robots_url)
                if response.status_code == 200:
                    rules = parse_robots_txt(
                        response.text,
                        user_agent=self.config.user_agent,
                    )
                    self._robots_cache[domain] = rules
                    return rules
        except Exception:
            pass  # nosec B110

        # Return empty rules if fetch fails
        return {"allow": [], "disallow": []}


async def create_crawler(
    max_pages: int = 10,
    max_depth: int = 2,
    **kwargs,
) -> CrawlEngine:
    """Factory function to create a crawler.

    Args:
        max_pages: Maximum pages to crawl.
        max_depth: Maximum crawl depth.
        **kwargs: Additional crawler options.

    Returns:
        Configured CrawlEngine.
    """
    return CrawlEngine(
        max_pages=max_pages,
        max_depth=max_depth,
        **kwargs,
    )
