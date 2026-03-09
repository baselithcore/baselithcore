# core/scraper/storage/base.py
"""Base storage class."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models import ExtractedData, ScrapedPage


class BaseStorage(ABC):
    """Abstract base class for storage backends."""

    @abstractmethod
    async def save(self, url: str, page: ScrapedPage, data: ExtractedData) -> None:
        """Save scraped data.

        Args:
            url: The URL that was scraped.
            page: The raw scraped page.
            data: The extracted data.
        """
        ...

    @abstractmethod
    async def load(self, url: str) -> tuple[ScrapedPage, ExtractedData] | None:
        """Load previously scraped data.

        Args:
            url: The URL to load data for.

        Returns:
            Tuple of (page, data) if found, None otherwise.
        """
        ...

    @abstractmethod
    async def exists(self, url: str) -> bool:
        """Check if data exists for a URL.

        Args:
            url: The URL to check.

        Returns:
            True if data exists.
        """
        ...

    @abstractmethod
    async def delete(self, url: str) -> bool:
        """Delete data for a URL.

        Args:
            url: The URL to delete.

        Returns:
            True if deleted, False if not found.
        """
        ...

    @abstractmethod
    async def clear(self) -> None:
        """Clear all stored data."""
        ...

    @abstractmethod
    async def list_urls(self) -> list[str]:
        """List all stored URLs.

        Returns:
            List of stored URLs.
        """
        ...

    async def __aenter__(self) -> BaseStorage:
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        pass
