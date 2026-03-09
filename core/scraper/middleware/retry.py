# core/scraper/middleware/retry.py
"""Retry middleware with exponential backoff."""

from __future__ import annotations

import asyncio
import random
from typing import TYPE_CHECKING

from core.config.scraper import get_scraper_config
from .base import BaseMiddleware

if TYPE_CHECKING:
    from ..models import ScrapedPage


class RetryMiddleware(BaseMiddleware):
    """Retry middleware with exponential backoff and jitter.

    Automatically retries failed requests based on configurable criteria.
    """

    # Status codes that should trigger retry
    RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}

    def __init__(
        self,
        max_retries: int | None = None,
        backoff_factor: float | None = None,
        jitter: float = 0.1,
    ):
        """Initialize the retry middleware.

        Args:
            max_retries: Maximum retry attempts.
            backoff_factor: Exponential backoff factor.
            jitter: Random jitter factor (0-1).
        """
        config = get_scraper_config()
        self.max_retries = max_retries or config.max_retries
        self.backoff_factor = backoff_factor or config.retry_backoff_factor
        self.jitter = jitter

        # Track retry counts per URL
        self._retry_counts: dict[str, int] = {}

    def _calculate_delay(self, attempt: int) -> float:
        """Calculate delay for retry attempt.

        Args:
            attempt: Current attempt number (0-indexed).

        Returns:
            Delay in seconds.
        """
        base_delay = self.backoff_factor * (2**attempt)
        jitter_amount = base_delay * self.jitter * random.random()  # nosec B311
        return base_delay + jitter_amount

    def should_retry(self, page: ScrapedPage) -> bool:
        """Check if a response should be retried.

        Args:
            page: The scraped page.

        Returns:
            True if should retry.
        """
        # Don't retry if max retries exceeded
        retry_count = self._retry_counts.get(page.url, 0)
        if retry_count >= self.max_retries:
            return False

        # Retry on error
        if page.error:
            return True

        # Retry on retryable status codes
        if page.status_code in self.RETRYABLE_STATUS_CODES:
            return True

        return False

    async def process_request(self, url: str) -> str | None:
        """Initialize retry count for request.

        Args:
            url: The URL being requested.

        Returns:
            The URL unchanged.
        """
        if url not in self._retry_counts:
            self._retry_counts[url] = 0
        return url

    async def process_response(self, url: str, page: ScrapedPage) -> ScrapedPage:
        """Track retry state for response.

        Note: Actual retry logic should be handled by the fetcher/scraper.
        This middleware tracks state and provides delay calculation.

        Args:
            url: The original URL.
            page: The fetched page.

        Returns:
            The page unchanged.
        """
        if self.should_retry(page):
            # Increment retry count
            self._retry_counts[url] = self._retry_counts.get(url, 0) + 1

            # Calculate and apply delay
            delay = self._calculate_delay(self._retry_counts[url] - 1)
            await asyncio.sleep(delay)

        return page

    def get_retry_count(self, url: str) -> int:
        """Get current retry count for URL.

        Args:
            url: The URL.

        Returns:
            Retry count.
        """
        return self._retry_counts.get(url, 0)

    def reset(self, url: str | None = None) -> None:
        """Reset retry counts.

        Args:
            url: Specific URL to reset, or None for all.
        """
        if url:
            self._retry_counts.pop(url, None)
        else:
            self._retry_counts.clear()
