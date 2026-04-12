# core/scraper/fetchers/base.py
"""Base fetcher class and common utilities."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

from core.config.scraper import ScraperConfig, get_scraper_config
from ..models import ScrapedPage


class FetchError(Exception):
    """Exception raised when fetching fails."""

    def __init__(
        self,
        url: str,
        message: str,
        status_code: int | None = None,
        cause: Exception | None = None,
    ):
        """
        Initialize the FetchError response exception.

        Args:
            url: The URL that failed to fetch.
            message: A descriptive error message.
            status_code: HTTP status code, if applicable.
            cause: The underlying exception that triggered this error.
        """
        self.url = url
        self.message = message
        self.status_code = status_code
        self.cause = cause
        super().__init__(f"Failed to fetch {url}: {message}")


class BaseFetcher(ABC):
    """Abstract base class for page fetchers.

    Provides common functionality for all fetcher implementations.
    """

    def __init__(self, config: ScraperConfig | None = None):
        """Initialize the fetcher.

        Args:
            config: Optional scraper configuration.
        """
        self.config = config or get_scraper_config()
        self._closed = False

    @abstractmethod
    async def fetch(self, url: str) -> ScrapedPage:
        """Fetch a single URL.

        Args:
            url: The URL to fetch.

        Returns:
            ScrapedPage with the fetched content.

        Raises:
            FetchError: If the fetch fails.
        """
        ...

    async def fetch_many(
        self, urls: list[str], concurrency: int = 5
    ) -> AsyncIterator[ScrapedPage]:
        """Fetch multiple URLs concurrently.

        Default implementation fetches sequentially.
        Subclasses can override for true concurrency.

        Args:
            urls: List of URLs to fetch.
            concurrency: Maximum concurrent requests (ignored in base impl).

        Yields:
            ScrapedPage for each fetched URL.
        """
        for url in urls:
            try:
                yield await self.fetch(url)
            except FetchError as e:
                # Yield error page
                yield self._create_error_page(url, str(e))

    @abstractmethod
    async def close(self) -> None:
        """Clean up resources."""
        ...

    def _create_error_page(
        self,
        url: str,
        error: str,
        status_code: int = 0,
    ) -> ScrapedPage:
        """Create a ScrapedPage representing an error.

        Args:
            url: The URL that failed.
            error: Error message.
            status_code: HTTP status code if available.

        Returns:
            ScrapedPage with error information.
        """
        return ScrapedPage(
            url=url,
            final_url=url,
            status_code=status_code,
            html="",
            headers={},
            fetched_at=datetime.now(),
            fetch_time_ms=0.0,
            error=error,
        )

    def _measure_time(self) -> float:
        """Get current time for measuring fetch duration."""
        return time.perf_counter()

    def _calc_duration_ms(self, start: float) -> float:
        """Calculate duration in milliseconds.

        Args:
            start: Start time from _measure_time().

        Returns:
            Duration in milliseconds.
        """
        return (time.perf_counter() - start) * 1000

    async def __aenter__(self) -> BaseFetcher:
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        await self.close()
