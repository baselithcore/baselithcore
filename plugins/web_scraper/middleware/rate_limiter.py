# core/scraper/middleware/rate_limiter.py
"""Rate limiting middleware."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from core.config.scraper import get_scraper_config
from .base import BaseMiddleware

if TYPE_CHECKING:
    from ..models import ScrapedPage


class RateLimiterMiddleware(BaseMiddleware):
    """Rate limiting middleware using token bucket algorithm.

    Limits requests per domain or globally based on configuration.
    """

    def __init__(
        self,
        requests_per_period: int | None = None,
        period_seconds: float | None = None,
        per_domain: bool | None = None,
    ):
        """Initialize the rate limiter.

        Args:
            requests_per_period: Max requests per period.
            period_seconds: Period duration in seconds.
            per_domain: Apply limits per domain vs globally.
        """
        config = get_scraper_config()
        self.requests_per_period = requests_per_period or config.rate_limit_requests
        self.period_seconds = period_seconds or config.rate_limit_period_seconds
        self.per_domain = (
            per_domain if per_domain is not None else config.rate_limit_per_domain
        )

        # Token buckets per domain (or global)
        self._buckets: dict[str, float] = defaultdict(lambda: self.requests_per_period)
        self._last_update: dict[str, float] = defaultdict(time.monotonic)
        self._lock = asyncio.Lock()

        # Cleanup configuration
        self._last_cleanup = time.monotonic()
        self.CLEANUP_INTERVAL = 300.0  # 5 minutes
        self.BUCKET_TTL = 3600.0  # 1 hour

    def _get_bucket_key(self, url: str) -> str:
        """Get the bucket key for rate limiting.

        Args:
            url: The URL being requested.

        Returns:
            Bucket key (domain or 'global').
        """
        if self.per_domain:
            return urlparse(url).netloc.lower()
        return "global"

    def _cleanup_buckets(self, now: float) -> None:
        """Remove buckets that haven't been used for a long time.

        Args:
            now: Current monotonic time.
        """
        keys_to_remove = [
            k
            for k, last_ts in self._last_update.items()
            if now - last_ts > self.BUCKET_TTL
        ]
        for k in keys_to_remove:
            del self._buckets[k]
            del self._last_update[k]

    async def _acquire_token(self, key: str) -> None:
        """Acquire a token from the bucket, waiting if necessary.

        Args:
            key: Bucket key.
        """
        async with self._lock:
            now = time.monotonic()

            # Lazy cleanup check
            if now - self._last_cleanup > self.CLEANUP_INTERVAL:
                self._cleanup_buckets(now)
                self._last_cleanup = now

            # Ensure key is initialized if it was cleaned up or new
            if key not in self._last_update:
                self._last_update[key] = now
                # DefaultDict initializes bucket automatically on access if deleted

            elapsed = now - self._last_update[key]

            # Refill tokens based on elapsed time
            tokens_to_add = elapsed * (self.requests_per_period / self.period_seconds)
            self._buckets[key] = min(
                self.requests_per_period,
                self._buckets[key] + tokens_to_add,
            )
            self._last_update[key] = now

            # Wait if no tokens available
            if self._buckets[key] < 1:
                wait_time = (1 - self._buckets[key]) * (
                    self.period_seconds / self.requests_per_period
                )
                await asyncio.sleep(wait_time)
                self._buckets[key] = 0
            else:
                self._buckets[key] -= 1

    async def process_request(self, url: str) -> str | None:
        """Apply rate limiting before request.

        Args:
            url: The URL being requested.

        Returns:
            The URL (unchanged).
        """
        key = self._get_bucket_key(url)
        await self._acquire_token(key)
        return url

    async def process_response(self, url: str, page: ScrapedPage) -> ScrapedPage:
        """Pass through response unchanged.

        Args:
            url: The original URL.
            page: The fetched page.

        Returns:
            The page unchanged.
        """
        return page
