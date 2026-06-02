# core/scraper/fetchers/httpx_fetcher.py
"""HTTP fetcher using httpx library.

This is the default fetcher for fast, lightweight scraping of static pages.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING

import httpx

from ..models import ScrapedPage
from ..utils import get_pinned_url_for_host
from .base import BaseFetcher, FetchError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from core.config.scraper import ScraperConfig


class HttpxFetcher(BaseFetcher):
    """Fetcher implementation using httpx for async HTTP requests.

    Features:
    - Connection pooling
    - Automatic redirect following
    - SSRF protection
    - Configurable timeouts
    - Proxy support
    """

    def __init__(self, config: ScraperConfig | None = None):
        """Initialize the httpx fetcher.

        Args:
            config: Optional scraper configuration.
        """
        super().__init__(config)
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the httpx client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.config.timeout_seconds),
                follow_redirects=False,  # Managed manually for SSRF protection
                headers={
                    "User-Agent": self.config.user_agent,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.5",
                    "Accept-Encoding": "gzip, deflate",
                },
                limits=httpx.Limits(
                    max_connections=self.config.max_connections,
                    max_keepalive_connections=self.config.max_connections // 2,
                ),
            )
        return self._client

    async def fetch(self, url: str) -> ScrapedPage:
        """Fetch a single URL using httpx.

        Args:
            url: The URL to fetch.

        Returns:
            ScrapedPage with the fetched content.

        Raises:
            FetchError: If the fetch fails.
        """
        start = self._measure_time()

        try:
            client = await self._get_client()

            # Manual redirect loop for SSRF protection
            current_url = url
            max_redirects = 10
            redirects = 0

            max_size_bytes = 10 * 1024 * 1024
            final_html = ""
            final_url = url
            status_code = 0
            headers = {}

            while redirects <= max_redirects:
                # Resolve DNS now and pin the connection to the verified IP.
                # This prevents DNS rebinding: the IP we check is the IP we
                # connect to, with no second resolution by the HTTP client.
                pinned = get_pinned_url_for_host(current_url)
                if pinned is None:
                    raise FetchError(
                        url=url,
                        message=f"SSRF protection blocked {'redirect to ' if redirects else ''}{current_url}",
                        status_code=403,
                    )
                pinned_url, original_host = pinned

                async with client.stream(
                    "GET", pinned_url, headers={"Host": original_host}
                ) as response:
                    final_url = str(response.url)
                    status_code = response.status_code
                    headers = dict(response.headers)

                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("Location")
                        if not location:
                            break

                        # Handle relative redirects
                        if location.startswith("/"):
                            parsed = httpx.URL(current_url)
                            current_url = str(parsed.join(location))
                        else:
                            current_url = location

                        redirects += 1
                        continue

                    content_len = response.headers.get("Content-Length")
                    if (
                        content_len
                        and content_len.isdigit()
                        and int(content_len) > max_size_bytes
                    ):
                        raise FetchError(
                            url=url,
                            message=f"Response too large: {content_len} bytes exceeds {max_size_bytes} limit",
                            status_code=413,
                        )

                    content_bytes = bytearray()
                    async for chunk in response.aiter_bytes():
                        content_bytes.extend(chunk)
                        if len(content_bytes) > max_size_bytes:
                            raise FetchError(
                                url=url,
                                message=f"Response too large: exceeds {max_size_bytes} limit",
                                status_code=413,
                            )

                    final_html = content_bytes.decode("utf-8", errors="replace")
                    # Scraped pages are untrusted: scan for indirect prompt
                    # injection (hidden CSS/comments/zero-width) before the HTML
                    # reaches the agent. Log-only unless sanitizing is enabled.
                    from core.guardrails import scan_external_content

                    final_html = scan_external_content(
                        final_html, source=f"web_scraper:{url}"
                    )
                    break

            if redirects > max_redirects:
                raise FetchError(
                    url=url,
                    message="Too many redirects",
                    status_code=302,
                )

            return ScrapedPage(
                url=url,
                final_url=final_url,
                status_code=status_code,
                html=final_html,
                headers=headers,
                fetched_at=datetime.now(),
                fetch_time_ms=self._calc_duration_ms(start),
                error=None,
            )

        except httpx.TimeoutException as e:
            raise FetchError(
                url=url,
                message=f"Request timed out after {self.config.timeout_seconds}s",
                cause=e,
            ) from e

        except httpx.ConnectError as e:
            raise FetchError(
                url=url,
                message=f"Connection failed: {e}",
                cause=e,
            ) from e

        except httpx.HTTPStatusError as e:
            raise FetchError(
                url=url,
                message=f"HTTP error: {e.response.status_code}",
                status_code=e.response.status_code,
                cause=e,
            ) from e

        except Exception as e:
            raise FetchError(
                url=url,
                message=str(e),
                cause=e,
            ) from e

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
        semaphore = asyncio.Semaphore(concurrency)

        async def fetch_with_semaphore(url: str) -> ScrapedPage:
            async with semaphore:
                try:
                    return await self.fetch(url)
                except FetchError as e:
                    return self._create_error_page(url, str(e))

        tasks = [asyncio.create_task(fetch_with_semaphore(url)) for url in urls]

        for task in asyncio.as_completed(tasks):
            yield await task

    async def close(self) -> None:
        """Close the httpx client and release resources."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
        self._closed = True
