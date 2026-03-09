# core/scraper/protocols.py
"""Protocol definitions for the web scraper module.

This module defines abstract interfaces using Python's Protocol for
type-safe duck typing, enabling flexible implementations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from .models import ExtractedData, ScrapedPage


@runtime_checkable
class Fetcher(Protocol):
    """Protocol for page fetchers.

    Fetchers are responsible for retrieving web pages from URLs.
    Implementations can use different backends (httpx, playwright, etc).
    """

    async def fetch(self, url: str) -> ScrapedPage:
        """Fetch a single URL and return the scraped page.

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

        Args:
            urls: List of URLs to fetch.
            concurrency: Maximum concurrent requests.

        Yields:
            ScrapedPage for each fetched URL.
        """
        ...

    async def close(self) -> None:
        """Clean up resources (connections, browser instances, etc)."""
        ...


@runtime_checkable
class Extractor(Protocol):
    """Protocol for content extractors.

    Extractors parse HTML content and extract specific data types
    (text, links, images, metadata, etc).
    """

    name: str

    def extract(self, page: ScrapedPage, base_url: str | None = None) -> Any:
        """Extract data from a scraped page.

        Args:
            page: The scraped page to extract from.
            base_url: Optional base URL for resolving relative links.

        Returns:
            Extracted data (type depends on extractor).
        """
        ...


@runtime_checkable
class Middleware(Protocol):
    """Protocol for request/response middleware.

    Middleware can intercept and modify requests/responses,
    implementing cross-cutting concerns like caching, rate limiting, etc.
    """

    async def process_request(self, url: str) -> str | None:
        """Process a request before fetching.

        Args:
            url: The URL being requested.

        Returns:
            Modified URL or None to skip the request.
        """
        ...

    async def process_response(self, url: str, page: ScrapedPage) -> ScrapedPage:
        """Process a response after fetching.

        Args:
            url: The original URL.
            page: The fetched page.

        Returns:
            Potentially modified page.
        """
        ...


@runtime_checkable
class Storage(Protocol):
    """Protocol for data storage backends.

    Storage implementations persist scraped data for later retrieval.
    """

    async def save(self, url: str, page: ScrapedPage, data: ExtractedData) -> None:
        """Save scraped data.

        Args:
            url: The URL that was scraped.
            page: The raw scraped page.
            data: The extracted data.
        """
        ...

    async def load(self, url: str) -> tuple[ScrapedPage, ExtractedData] | None:
        """Load previously scraped data.

        Args:
            url: The URL to load data for.

        Returns:
            Tuple of (page, data) if found, None otherwise.
        """
        ...

    async def exists(self, url: str) -> bool:
        """Check if data exists for a URL.

        Args:
            url: The URL to check.

        Returns:
            True if data exists, False otherwise.
        """
        ...

    async def delete(self, url: str) -> bool:
        """Delete data for a URL.

        Args:
            url: The URL to delete.

        Returns:
            True if deleted, False if not found.
        """
        ...

    async def clear(self) -> None:
        """Clear all stored data."""
        ...
