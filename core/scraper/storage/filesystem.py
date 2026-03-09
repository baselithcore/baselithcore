# core/scraper/storage/filesystem.py
"""Filesystem storage backend."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .base import BaseStorage

if TYPE_CHECKING:
    from ..models import ExtractedData, ScrapedPage


class FilesystemStorage(BaseStorage):
    """Filesystem-based storage backend.

    Stores scraped data as JSON files organized by domain.
    """

    def __init__(self, base_path: str | Path = "./scraper_data"):
        """Initialize filesystem storage.

        Args:
            base_path: Base directory for storing data.
        """
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _url_to_path(self, url: str) -> Path:
        """Convert URL to file path.

        Args:
            url: The URL.

        Returns:
            Path to the file.
        """
        # Hash the URL for filename
        url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
        return self.base_path / f"{url_hash}.json"

    def _serialize(
        self, url: str, page: "ScrapedPage", data: "ExtractedData"
    ) -> dict[str, Any]:
        """Serialize data for storage.

        Args:
            url: The URL.
            page: The scraped page.
            data: The extracted data.

        Returns:
            Serializable dict.
        """
        return {
            "url": url,
            "page": {
                "url": page.url,
                "final_url": page.final_url,
                "status_code": page.status_code,
                "html": page.html,
                "headers": dict(page.headers),
                "fetched_at": page.fetched_at.isoformat(),
                "fetch_time_ms": page.fetch_time_ms,
                "error": page.error,
            },
            "data": data.to_dict(),
        }

    async def save(self, url: str, page: "ScrapedPage", data: "ExtractedData") -> None:
        """Save scraped data to filesystem.

        Args:
            url: The URL that was scraped.
            page: The raw scraped page.
            data: The extracted data.
        """
        path = self._url_to_path(url)
        serialized = self._serialize(url, page, data)
        path.write_text(json.dumps(serialized, indent=2, default=str))

    async def load(self, url: str) -> tuple["ScrapedPage", "ExtractedData"] | None:
        """Load previously scraped data from filesystem.

        Note: This returns raw dict data, not reconstructed objects.

        Args:
            url: The URL to load data for.

        Returns:
            Tuple of (page, data) if found, None otherwise.
        """
        path = self._url_to_path(url)
        if not path.exists():
            return None

        try:
            content = json.loads(path.read_text())
            # Return as-is for now - proper deserialization would need imports
            return content.get("page"), content.get("data")  # type: ignore
        except (json.JSONDecodeError, KeyError):
            return None

    async def exists(self, url: str) -> bool:
        """Check if data exists for a URL.

        Args:
            url: The URL to check.

        Returns:
            True if data exists.
        """
        return self._url_to_path(url).exists()

    async def delete(self, url: str) -> bool:
        """Delete data for a URL.

        Args:
            url: The URL to delete.

        Returns:
            True if deleted, False if not found.
        """
        path = self._url_to_path(url)
        if path.exists():
            path.unlink()
            return True
        return False

    async def clear(self) -> None:
        """Clear all stored data."""
        for file in self.base_path.glob("*.json"):
            file.unlink()

    async def list_urls(self) -> list[str]:
        """List all stored URLs.

        Returns:
            List of stored URLs.
        """
        urls = []
        for file in self.base_path.glob("*.json"):
            try:
                content = json.loads(file.read_text())
                if "url" in content:
                    urls.append(content["url"])
            except (json.JSONDecodeError, KeyError):
                continue
        return urls
