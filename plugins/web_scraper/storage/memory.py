# core/scraper/storage/memory.py
"""In-memory storage backend."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import BaseStorage

if TYPE_CHECKING:
    from ..models import ExtractedData, ScrapedPage


class MemoryStorage(BaseStorage):
    """In-memory storage backend.

    Simple dict-based storage for development and testing.
    Data is lost when the process ends.
    """

    def __init__(self):
        """Initialize memory storage."""
        self._data: dict[str, tuple[ScrapedPage, ExtractedData]] = {}

    async def save(self, url: str, page: ScrapedPage, data: ExtractedData) -> None:
        """Save scraped data to memory.

        Args:
            url: The URL that was scraped.
            page: The raw scraped page.
            data: The extracted data.
        """
        self._data[url] = (page, data)

    async def load(self, url: str) -> tuple[ScrapedPage, ExtractedData] | None:
        """Load previously scraped data from memory.

        Args:
            url: The URL to load data for.

        Returns:
            Tuple of (page, data) if found, None otherwise.
        """
        return self._data.get(url)

    async def exists(self, url: str) -> bool:
        """Check if data exists for a URL.

        Args:
            url: The URL to check.

        Returns:
            True if data exists.
        """
        return url in self._data

    async def delete(self, url: str) -> bool:
        """Delete data for a URL.

        Args:
            url: The URL to delete.

        Returns:
            True if deleted, False if not found.
        """
        if url in self._data:
            del self._data[url]
            return True
        return False

    async def clear(self) -> None:
        """Clear all stored data."""
        self._data.clear()

    async def list_urls(self) -> list[str]:
        """List all stored URLs.

        Returns:
            List of stored URLs.
        """
        return list(self._data.keys())
