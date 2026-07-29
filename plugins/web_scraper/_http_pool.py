"""Plugin-local shared ``httpx.AsyncClient`` for lightweight auxiliary fetches.

The crawler's ``robots.txt`` fetch previously built a new ``httpx.AsyncClient``
per domain, paying a fresh TCP+TLS handshake each time. This module hands out a
single long-lived client (keep-alive reused across domains) and a ``close()``
hook wired into the scraper's shutdown path. The main page fetches already reuse
``HttpxFetcher._client``; this covers the auxiliary robots path.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx

_client: httpx.AsyncClient | None = None
_lock = asyncio.Lock()


async def get_robots_client(*, timeout: float = 10.0) -> httpx.AsyncClient:
    """Return (or lazily create) the shared client for auxiliary fetches."""
    global _client
    import httpx

    async with _lock:
        if _client is None or _client.is_closed:
            _client = httpx.AsyncClient(
                timeout=timeout,
                limits=httpx.Limits(max_keepalive_connections=10, max_connections=50),
            )
        return _client


async def close_robots_client() -> None:
    """Close the shared client (idempotent); called from scraper shutdown."""
    global _client
    async with _lock:
        if _client is not None:
            with suppress(Exception):
                await _client.aclose()
            _client = None


__all__ = ["close_robots_client", "get_robots_client"]
