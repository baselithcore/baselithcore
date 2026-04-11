# core/scraper/middleware/logging.py
"""Logging middleware for request/response tracking."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from core.observability import get_logger
from .base import BaseMiddleware

if TYPE_CHECKING:
    from ..models import ScrapedPage


logger = get_logger("scraper")


class LoggingMiddleware(BaseMiddleware):
    """Logging middleware for debugging and monitoring.

    Logs request/response details including timing information.
    """

    def __init__(
        self,
        log_level: int = logging.INFO,
        log_headers: bool = False,
        log_body: bool = False,
    ):
        """Initialize the logging middleware.

        Args:
            log_level: Logging level to use.
            log_headers: Whether to log response headers.
            log_body: Whether to log response body (truncated).
        """
        self.log_level = log_level
        self.log_headers = log_headers
        self.log_body = log_body

        # Track request start times
        self._request_times: dict[str, float] = {}

    async def process_request(self, url: str) -> str | None:
        """Log request start.

        Args:
            url: The URL being requested.

        Returns:
            The URL unchanged.
        """
        self._request_times[url] = time.perf_counter()

        # Log based on configured level
        if self.log_level >= logging.INFO:
            logger.info("Fetching: %s", url)
        else:
            logger.debug("Fetching: %s", url)

        return url

    async def process_response(self, url: str, page: ScrapedPage) -> ScrapedPage:
        """Log response details.

        Args:
            url: The original URL.
            page: The fetched page.

        Returns:
            The page unchanged.
        """
        # Calculate duration
        start_time = self._request_times.pop(url, None)
        duration_ms = (
            (time.perf_counter() - start_time) * 1000
            if start_time
            else page.fetch_time_ms
        )

        # Build log message
        if page.error:
            logger.warning(
                "Failed: %s (%.0fms) - %s",
                url,
                duration_ms,
                page.error,
            )
        else:
            msg = "Fetched: %s [%d] (%.0fms, %d bytes)"
            args = (url, page.status_code, duration_ms, len(page.html))

            if self.log_level >= logging.INFO:
                logger.info(msg, *args)
            else:
                logger.debug(msg, *args)

        # Log headers if requested
        if self.log_headers and page.headers:
            logger.debug(
                "Headers: %s",
                dict(page.headers),
            )

        # Log body if requested (truncated)
        if self.log_body and page.html:
            truncated = page.html[:500] + "..." if len(page.html) > 500 else page.html
            logger.debug("Body: %s", truncated)

        return page
