# core/scraper/middleware/cache.py
"""Caching middleware."""

from __future__ import annotations

import hashlib
import time
from collections import OrderedDict
from typing import TYPE_CHECKING

from core.config.scraper import get_scraper_config
from ..models import ScrapedPage
from .base import BaseMiddleware

if TYPE_CHECKING:
    pass


class CacheMiddleware(BaseMiddleware):
    """Caching middleware for scraped pages.

    Supports in-memory LRU cache and optional Redis backend.
    """

    def __init__(
        self,
        ttl_seconds: int | None = None,
        max_size: int = 1000,
        backend: str | None = None,
    ):
        """Initialize the cache middleware.

        Args:
            ttl_seconds: Cache TTL in seconds.
            max_size: Maximum cache entries (memory backend).
            backend: Cache backend ('memory' or 'redis').
        """
        config = get_scraper_config()
        self.ttl_seconds = ttl_seconds or config.cache_ttl_seconds
        self.max_size = max_size
        self.backend = backend or config.cache_backend

        # In-memory cache
        self._cache: OrderedDict[str, tuple[ScrapedPage, float]] = OrderedDict()

    def _cache_key(self, url: str) -> str:
        """Generate cache key for URL.

        Args:
            url: The URL.

        Returns:
            Cache key.
        """
        return hashlib.sha256(url.encode()).hexdigest()[:32]

    def _is_expired(self, timestamp: float) -> bool:
        """Check if a cache entry is expired.

        Args:
            timestamp: Entry timestamp.

        Returns:
            True if expired.
        """
        return time.time() - timestamp > self.ttl_seconds

    def _get_from_cache(self, url: str) -> ScrapedPage | None:
        """Get page from cache.

        Args:
            url: The URL.

        Returns:
            Cached page or None.
        """
        key = self._cache_key(url)

        if key in self._cache:
            page, timestamp = self._cache[key]
            if not self._is_expired(timestamp):
                # Move to end (LRU)
                self._cache.move_to_end(key)
                return page
            else:
                # Remove expired entry
                del self._cache[key]

        return None

    def _put_in_cache(self, url: str, page: ScrapedPage) -> None:
        """Store page in cache.

        Args:
            url: The URL.
            page: The page to cache.
        """
        key = self._cache_key(url)

        # Evict if at capacity
        while len(self._cache) >= self.max_size:
            self._cache.popitem(last=False)

        self._cache[key] = (page, time.time())

    async def process_request(self, url: str) -> str | None:
        """Check cache before request.

        Note: Actual cache check happens in wrap_fetch pattern.
        This just returns the URL unchanged.

        Args:
            url: The URL being requested.

        Returns:
            The URL unchanged.
        """
        return url

    async def process_response(self, url: str, page: ScrapedPage) -> ScrapedPage:
        """Cache successful responses.

        Args:
            url: The original URL.
            page: The fetched page.

        Returns:
            The page unchanged.
        """
        # Only cache successful responses
        if page.is_success:
            self._put_in_cache(url, page)

        return page

    def get_cached(self, url: str) -> ScrapedPage | None:
        """Get cached page if available.

        Args:
            url: The URL.

        Returns:
            Cached page or None.
        """
        return self._get_from_cache(url)

    def clear(self) -> None:
        """Clear the cache."""
        self._cache.clear()
