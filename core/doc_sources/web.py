"""Web document source for crawling dynamic websites.

This module provides WebDocumentSource for crawling web pages using
Playwright for JavaScript rendering with httpx fallback.
"""

from __future__ import annotations

import contextlib
from core.observability.logging import get_logger
from collections import deque
from datetime import datetime, timezone
from typing import AsyncIterator, Optional, Sequence, Tuple
from urllib.parse import urlparse

import asyncio
import httpx

from core.config import get_processing_config
from .models import DocumentItem, DocumentSourceError
from .registry import register_source
from .utils import compute_remote_fingerprint, warn_missing_dependency
from .web_constants import (
    BINARY_EXTENSIONS,
    MIN_DOCUMENT_CHARS,
    PRIVATE_IP_PREFIXES,
)
from .web_parser import parse_page

logger = get_logger(__name__)

_proc_config = get_processing_config()
WEB_DOCUMENTS_ENABLED = _proc_config.web_documents_enabled
WEB_DOCUMENTS_MAX_DEPTH = _proc_config.web_documents_max_depth
WEB_DOCUMENTS_MAX_PAGES = _proc_config.web_documents_max_pages
WEB_DOCUMENTS_RENDER_TIMEOUT = _proc_config.web_documents_render_timeout
WEB_DOCUMENTS_URLS = _proc_config.web_documents_urls
WEB_DOCUMENTS_USER_AGENT = _proc_config.web_documents_user_agent
WEB_DOCUMENTS_WAIT_SELECTOR = _proc_config.web_documents_wait_selector
WEB_DOCUMENTS_ALLOWLIST = _proc_config.web_documents_allowlist

_PLAYWRIGHT_FACTORY = None
_PLAYWRIGHT_TIMEOUT = None
_PLAYWRIGHT_LOADED = False


def _load_playwright():
    """Load Playwright library if available."""
    global _PLAYWRIGHT_FACTORY, _PLAYWRIGHT_TIMEOUT, _PLAYWRIGHT_LOADED
    if _PLAYWRIGHT_LOADED:
        return _PLAYWRIGHT_FACTORY, _PLAYWRIGHT_TIMEOUT
    try:  # pragma: no cover - optional dependency
        from playwright.async_api import (  # type: ignore
            TimeoutError as PWTimeoutError,
            async_playwright as async_runner,
        )

        _PLAYWRIGHT_FACTORY = async_runner
        _PLAYWRIGHT_TIMEOUT = PWTimeoutError
    except Exception:
        _PLAYWRIGHT_FACTORY = None
        _PLAYWRIGHT_TIMEOUT = None
    _PLAYWRIGHT_LOADED = True
    return _PLAYWRIGHT_FACTORY, _PLAYWRIGHT_TIMEOUT


class WebDocumentSource:
    """Document source that crawls dynamic websites.

    Supports JavaScript-rendered pages via Playwright with automatic
    fallback to httpx for simple HTML pages.

    Example:
        ```python
        source = WebDocumentSource(["https://example.com"])
        async for doc in source.iter_items():
            print(doc.content)
        await source.close()
        ```
    """

    def __init__(
        self,
        urls: Sequence[str],
        *,
        max_pages: int = WEB_DOCUMENTS_MAX_PAGES,
        max_depth: int = WEB_DOCUMENTS_MAX_DEPTH,
        render_timeout: float = WEB_DOCUMENTS_RENDER_TIMEOUT,
        wait_selector: str | None = WEB_DOCUMENTS_WAIT_SELECTOR,
        user_agent: str = WEB_DOCUMENTS_USER_AGENT,
    ) -> None:
        """Initialize web document source.

        Args:
            urls: Seed URLs to crawl
            max_pages: Maximum pages to crawl per seed
            max_depth: Maximum crawl depth from seed
            render_timeout: Timeout for page rendering
            wait_selector: CSS selector to wait for before extraction
            user_agent: User agent string for requests

        Raises:
            DocumentSourceError: If no valid URLs provided
        """
        normalized = [self._normalize_url(url) for url in urls]
        self._seeds = [url for url in normalized if url]
        if not self._seeds:
            raise DocumentSourceError(
                "WEB_DOCUMENTS_URLS does not contain valid URLs to index."
            )
        self._max_pages = max(1, max_pages)
        self._max_depth = max(1, max_depth)
        self._render_timeout = max(1.0, render_timeout)
        self._wait_selector = wait_selector
        self._user_agent = user_agent
        self._playwright_timeout = None

        timeout_config = httpx.Timeout(self._render_timeout, connect=10.0)
        limits = httpx.Limits(max_connections=4, max_keepalive_connections=2)
        self._client = httpx.AsyncClient(
            headers={
                "User-Agent": self._user_agent,
                "Accept": "text/html,application/xhtml+xml;q=0.9",
                "Accept-Language": "it,it-IT;q=0.9,en;q=0.8",
            },
            follow_redirects=True,
            timeout=timeout_config,
            limits=limits,
        )

    async def iter_items(self) -> AsyncIterator[DocumentItem]:
        """Iterate over crawled documents asynchronously."""
        async with self._playwright_page() as page:
            for seed in self._seeds:
                async for item in self._crawl_seed(seed, page):
                    yield item

    async def close(self) -> None:
        """Close HTTP client resources."""
        await self._client.aclose()

    async def _crawl_seed(self, seed_url: str, page) -> AsyncIterator[DocumentItem]:
        """Crawl a single seed URL and yield documents."""
        queue: deque[Tuple[str, int]] = deque([(seed_url, 0)])
        queued = {seed_url}
        visited: set[str] = set()
        domain = self._normalize_domain(urlparse(seed_url).netloc)

        while queue and len(visited) < self._max_pages:
            current_url, depth = queue.popleft()
            queued.discard(current_url)
            if self._should_skip_url(current_url):
                continue

            html_tuple = await self._fetch_page(current_url, page)
            if not html_tuple:
                continue
            html, final_url = html_tuple

            if self._normalize_domain(urlparse(final_url).netloc) != domain:
                continue

            doc_id = self._document_id(final_url)
            if doc_id in visited:
                continue

            parsed = parse_page(
                html,
                final_url,
                domain,
                self._normalize_domain,
                self._should_skip_url,
                self._normalize_parsed,
            )
            if not parsed:
                continue
            text, title, lang, links = parsed
            if len(text) < MIN_DOCUMENT_CHARS:
                continue

            visited.add(doc_id)
            metadata = self._build_metadata(seed_url, final_url, title, lang, depth)
            fingerprint = compute_remote_fingerprint(final_url, text)
            yield DocumentItem(
                uid=f"web://{doc_id}",
                content=text,
                fingerprint=fingerprint,
                metadata=metadata,
            )

            if depth >= self._max_depth:
                continue
            for link in links:
                if (
                    link not in visited
                    and link not in queued
                    and len(queue) + len(visited) < self._max_pages
                ):
                    queue.append((link, depth + 1))
                    queued.add(link)
            # Yield control to avoid starvation on long crawls
            await asyncio.sleep(0)

    async def _fetch_page(
        self,
        url: str,
        page,
    ) -> Optional[Tuple[str, str]]:
        """Fetch a page using Playwright or httpx fallback."""
        if page:
            rendered = await self._render_with_playwright(page, url)
            if rendered:
                return rendered
        return await self._fetch_with_httpx(url)

    async def _render_with_playwright(
        self,
        page,
        url: str,
    ) -> Optional[Tuple[str, str]]:
        """Render a page using Playwright."""
        if not page:
            return None
        try:
            await page.goto(
                url,
                wait_until="networkidle",
                timeout=int(self._render_timeout * 1000),
            )
            if self._wait_selector:
                await page.wait_for_selector(
                    self._wait_selector,
                    timeout=int(self._render_timeout * 1000),
                )
            else:
                await page.wait_for_load_state(
                    state="networkidle",
                    timeout=int(self._render_timeout * 1000),
                )
            return await page.content(), page.url
        except Exception as exc:  # pragma: no cover
            timeout_type = self._playwright_timeout
            if timeout_type and isinstance(exc, timeout_type):
                logger.warning(f"[web-source] Timeout Playwright on {url}")
            else:
                logger.warning(f"[web-source] Error Playwright on {url}: {exc}")
        return None

    async def _fetch_with_httpx(self, url: str) -> Optional[Tuple[str, str]]:
        """Fetch a page using httpx (fallback for non-JS pages)."""
        try:
            response = await self._client.get(url)
            response.raise_for_status()
            return response.text, str(response.url)
        except httpx.HTTPError as exc:
            logger.warning(f"[web-source] HTTP failed on {url}: {exc}")
            return None

    def _playwright_page(self):
        """Context manager for Playwright page."""

        @contextlib.asynccontextmanager
        async def manager():
            runner_factory, timeout_exc = _load_playwright()
            if runner_factory is None:
                warn_missing_dependency("playwright", "sorgente web dinamica")
                yield None
                return
            playwright = await runner_factory().start()
            self._playwright_timeout = timeout_exc
            browser = None
            context = None
            try:
                browser = await playwright.chromium.launch(headless=True)
                context = await browser.new_context(user_agent=self._user_agent)
                page = await context.new_page()
                yield page
            except Exception as exc:  # pragma: no cover
                logger.warning(f"[web-source] Playwright not available: {exc}")
                yield None
            finally:
                if context:
                    await context.close()
                if browser:
                    await browser.close()
                await playwright.stop()

        return manager()

    def _normalize_url(self, raw_url: str | None) -> str | None:
        """Normalize a URL string."""
        if not raw_url:
            return None
        trimmed = raw_url.strip()
        if not trimmed:
            return None
        if "://" not in trimmed:
            trimmed = f"https://{trimmed}"
        parsed = urlparse(trimmed)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return None
        return self._normalize_parsed(parsed)

    def _normalize_parsed(self, parsed) -> str:
        """Normalize a parsed URL."""
        path = parsed.path or "/"
        normalized_path = path if path.startswith("/") else f"/{path}"
        query = f"?{parsed.query}" if parsed.query else ""
        return f"{parsed.scheme}://{parsed.netloc}{normalized_path}{query}"

    def _should_skip_url(self, url: str) -> bool:
        """Check if a URL should be skipped."""
        parsed = urlparse(url)
        extension = ""
        if "." in parsed.path.rsplit("/", 1)[-1]:
            _, _, candidate = parsed.path.rpartition(".")
            extension = f".{candidate.lower()}"
        if extension in BINARY_EXTENSIONS:
            return True

        hostname = parsed.hostname or ""
        # Block private/loopback/link-local IPs
        if any(hostname.startswith(prefix) for prefix in PRIVATE_IP_PREFIXES):
            return True
        # Block non-standard ports (SSRF protection)
        if parsed.port and parsed.port not in {80, 443}:
            return True

        # Domain allowlist if configured
        if WEB_DOCUMENTS_ALLOWLIST:
            domain = self._normalize_domain(hostname)
            allowed = {self._normalize_domain(d) for d in WEB_DOCUMENTS_ALLOWLIST}
            return domain not in allowed

        return False

    def _document_id(self, url: str) -> str:
        """Generate document ID from URL."""
        parsed = urlparse(url)
        path = parsed.path or "/"
        normalized_path = path if path.startswith("/") else f"/{path}"
        query = f"?{parsed.query}" if parsed.query else ""
        return f"{parsed.netloc}{normalized_path}{query}"

    def _normalize_domain(self, domain: str) -> str:
        """Normalize a domain name."""
        return domain.lower().lstrip("www.")

    def _build_metadata(
        self,
        seed_url: str,
        final_url: str,
        title: str,
        lang: str | None,
        depth: int,
    ) -> dict[str, str]:
        """Build document metadata."""
        parsed = urlparse(final_url)
        timestamp = (
            datetime.now(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )
        metadata: dict[str, str] = {
            "origin": "web",
            "source": final_url,
            "seed": seed_url,
            "site": parsed.netloc,
            "doc_type": "web",
            "depth": str(depth),
            "fetched_at": timestamp,
        }
        if title:
            metadata["title"] = title
        if lang:
            metadata["language"] = lang
        return metadata


def _create_web_source():
    """Factory function for web document source."""
    if not WEB_DOCUMENTS_ENABLED:
        return None
    if not WEB_DOCUMENTS_URLS:
        return None
    try:
        return WebDocumentSource(WEB_DOCUMENTS_URLS)
    except DocumentSourceError as exc:
        logger.warning(f"[web-source] {exc}")
        return None


register_source("web", _create_web_source)
