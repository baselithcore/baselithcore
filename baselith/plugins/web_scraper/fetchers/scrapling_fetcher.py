"""Anti-bot HTTP fetching on Scrapling's transport, under the scraper's own SSRF rules.

Many sites answer a plain HTTP client with 403 or a challenge page because its TLS and HTTP/2
fingerprint is not a browser's. Scrapling gets past most of them by impersonating Chrome at the
TLS layer (``curl_cffi``), without starting a browser. Its own ``AsyncFetcher`` opens an unpinned
session and reads the whole body, so this fetcher drives the same transport itself (see
:mod:`._curl`): every hop passes the SSRF guard and is pinned to the verified address,
redirects are followed by hand so each target is checked again, and the body is capped like the
httpx fetcher's. What comes back is parsed by Scrapling (``extractors.markdown``).

Optional: it needs ``pip install "scrapling[fetchers]" markdownify``; without them a fetch
fails with an install hint and the other fetchers are unaffected.
"""

from __future__ import annotations

import asyncio
import importlib.util
import ipaddress
import re
from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol
from urllib.parse import urljoin, urlsplit

from baselith.core.security.ssrf import SsrfError, SsrfPolicy, resolve_pinned_target

from ..models import ScrapedPage
from ..utils import get_pinned_url_for_host
from ._curl import (
    CurlReply,
    PinMismatchError,
    ResponseTooLargeError,
    UnsupportedEncodingError,
    curl_get,
)
from .base import BaseFetcher, FetchError

if TYPE_CHECKING:
    from baselith.core.config.scraper import ScraperConfig

MAX_REDIRECTS = 10
MAX_BYTES = 10 * 1024 * 1024
#: The browser whose TLS and HTTP/2 fingerprint the requests carry.
IMPERSONATE = "chrome"
INSTALL_HINT = (
    'scrapling is not installed: pip install "scrapling[fetchers]" markdownify'
)
_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_CHARSET = re.compile(r"charset\s*=\s*\"?([\w.:-]+)", re.IGNORECASE)


class Transport(Protocol):
    """One GET (see :func:`._curl.curl_get`); swapped out in tests."""

    def __call__(
        self,
        url: str,
        *,
        resolve: list[str],
        expected_ip: str | None,
        timeout: float,
        max_bytes: int,
        impersonate: str,
    ) -> Awaitable[CurlReply]: ...


def scrapling_available() -> bool:
    """Whether Scrapling and its HTTP transport are installed (found, not imported)."""
    return all(
        importlib.util.find_spec(m) is not None for m in ("scrapling", "curl_cffi")
    )


def resolve_entry(url: str, pinned_url: str) -> list[str]:
    """The curl ``RESOLVE`` entry that sends ``url``'s host to the pinned address.

    Empty when nothing was pinned (the policy allows internal targets and returned ``url``).
    """
    target, pinned = urlsplit(url), urlsplit(pinned_url)
    if not target.hostname or not pinned.hostname or pinned.hostname == target.hostname:
        return []
    port = target.port or (443 if target.scheme == "https" else 80)
    ip = pinned.hostname
    return [f"{target.hostname}:{port}:{f'[{ip}]' if ':' in ip else ip}"]


def pinned_ip(pinned_url: str) -> str | None:
    """The address a pinned URL names, or None when it still names a host (nothing pinned)."""
    host = urlsplit(pinned_url).hostname or ""
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return None


def _decode(body: bytes, content_type: str) -> str:
    found = _CHARSET.search(content_type)
    try:
        return body.decode(found.group(1) if found else "utf-8", errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


class ScraplingFetcher(BaseFetcher):
    """Fetch pages with a real browser's TLS fingerprint, pinned and bounded."""

    def __init__(
        self,
        config: ScraperConfig | None = None,
        *,
        transport: Transport | None = None,
        policy: SsrfPolicy | None = None,
    ) -> None:
        """``policy`` overrides the scraper's own SSRF policy (``SCRAPER_BLOCK_PRIVATE_IPS``).

        A caller that fetches addresses a model chose passes its own, so that loosening the
        scraper for an internal crawl never loosens it for that caller too.
        """
        super().__init__(config)
        self._transport: Transport = transport or curl_get
        self._policy = policy

    def _pin(self, url: str) -> tuple[str, str] | None:
        """``(pinned_url, host)`` for ``url`` under the effective policy, or None if refused."""
        if self._policy is None:
            return get_pinned_url_for_host(url)
        try:
            return resolve_pinned_target(url, self._policy)
        except SsrfError:
            return None

    async def _hop(self, url: str, current: str, pinned_url: str) -> CurlReply:
        timeout = self.config.timeout_seconds
        try:
            async with asyncio.timeout(timeout + 5):
                return await self._transport(
                    current,
                    resolve=resolve_entry(current, pinned_url),
                    expected_ip=pinned_ip(pinned_url),
                    timeout=timeout,
                    max_bytes=MAX_BYTES,
                    impersonate=IMPERSONATE,
                )
        except ResponseTooLargeError as exc:
            raise FetchError(
                url=url,
                message=f"Response too large: exceeds {MAX_BYTES} limit",
                status_code=413,
                cause=exc,
            ) from exc
        except PinMismatchError as exc:
            raise FetchError(
                url=url,
                message="SSRF protection blocked a connection to an unverified address",
                status_code=403,
                cause=exc,
            ) from exc
        except UnsupportedEncodingError as exc:
            raise FetchError(
                url=url, message=f"Unreadable body: {exc}", status_code=415, cause=exc
            ) from exc
        except TimeoutError as exc:
            raise FetchError(
                url=url, message=f"Request timed out after {timeout}s", cause=exc
            ) from exc
        except Exception as exc:
            raise FetchError(
                url=url, message=f"Connection failed: {type(exc).__name__}", cause=exc
            ) from exc

    async def fetch(self, url: str) -> ScrapedPage:
        """Fetch ``url``, following up to ``MAX_REDIRECTS`` redirects, each one checked.

        Raises:
            FetchError: Scrapling missing (501), a blocked target or an address with
                credentials (403), a body over the cap (413) or with an encoding that cannot be
                inflated under it (415), too many redirects, a timeout or a connection failure.
        """
        if not scrapling_available():
            raise FetchError(url=url, message=INSTALL_HINT, status_code=501)
        start = self._measure_time()
        current = url
        for hop in range(MAX_REDIRECTS + 1):
            parts = urlsplit(current)
            pinned = (
                None
                if parts.username or parts.password
                else await asyncio.to_thread(self._pin, current)
            )
            if pinned is None:
                where = f"redirect to {current}" if hop else current
                raise FetchError(
                    url=url, message=f"SSRF protection blocked {where}", status_code=403
                )
            reply = await self._hop(url, current, pinned[0])
            location = reply.headers.get("location")
            if reply.status in _REDIRECTS and location:
                current = urljoin(current, location)
                continue
            from baselith.core.guardrails import scan_external_content

            html = scan_external_content(
                _decode(reply.body, reply.headers.get("content-type", "")),
                source=f"web_scraper:{url}",
            )
            return ScrapedPage(
                url=url,
                final_url=current,
                status_code=reply.status,
                html=html,
                headers=reply.headers,
                fetched_at=datetime.now(UTC),
                fetch_time_ms=self._calc_duration_ms(start),
            )
        raise FetchError(url=url, message="Too many redirects", status_code=302)

    async def close(self) -> None:
        """Nothing is held between fetches: each one opens and closes its own session."""
        self._closed = True


__all__ = [
    "IMPERSONATE",
    "INSTALL_HINT",
    "MAX_BYTES",
    "MAX_REDIRECTS",
    "ScraplingFetcher",
    "Transport",
    "pinned_ip",
    "resolve_entry",
    "scrapling_available",
]
