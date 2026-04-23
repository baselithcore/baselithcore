"""Shared ``httpx.AsyncClient`` pool with bounded keep-alive lifetime.

Channel adapters and the ClawHub client previously instantiated a new
``httpx.AsyncClient`` per request, paying the TCP+TLS handshake every
time. This module returns a long-lived client per (timeout, base_url)
key and provides a `close_all()` hook called from the plugin shutdown
path. TLS verification is enforced; explicit ``verify=False`` callers
must pass it through ``ClientFactory.acquire(verify=False)``.
"""

from __future__ import annotations

import asyncio
import threading
from contextlib import suppress
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx


_DEFAULT_TIMEOUT_SECONDS = 15.0


class HTTPClientPool:
    """Bounded pool of shared ``httpx.AsyncClient`` instances."""

    def __init__(
        self,
        default_timeout: float = _DEFAULT_TIMEOUT_SECONDS,
        max_keepalive_connections: int = 50,
        max_connections: int = 200,
    ) -> None:
        self._default_timeout = default_timeout
        self._max_keepalive = max_keepalive_connections
        self._max_total = max_connections
        self._clients: dict[tuple[float, bool], httpx.AsyncClient] = {}
        self._lock = threading.Lock()
        self._async_lock = asyncio.Lock()

    async def acquire(
        self,
        *,
        timeout: float | None = None,
        verify: bool = True,
    ) -> httpx.AsyncClient:
        """Return (or lazily create) a shared client for the given key."""
        try:
            import httpx  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("httpx not installed; pip install httpx") from exc

        key = (timeout or self._default_timeout, verify)
        async with self._async_lock:
            client = self._clients.get(key)
            if client is None or client.is_closed:
                limits = httpx.Limits(
                    max_keepalive_connections=self._max_keepalive,
                    max_connections=self._max_total,
                )
                client = httpx.AsyncClient(
                    timeout=timeout or self._default_timeout,
                    verify=verify,
                    limits=limits,
                )
                self._clients[key] = client
            return client

    async def close_all(self) -> None:
        async with self._async_lock:
            for client in list(self._clients.values()):
                with suppress(Exception):
                    await client.aclose()
            self._clients.clear()


_GLOBAL_POOL = HTTPClientPool()


async def get_shared_client(
    *,
    timeout: float | None = None,
    verify: bool = True,
) -> httpx.AsyncClient:
    """Convenience wrapper around the module-level pool."""
    return await _GLOBAL_POOL.acquire(timeout=timeout, verify=verify)


async def shutdown_shared_clients() -> None:
    await _GLOBAL_POOL.close_all()


__all__ = [
    "HTTPClientPool",
    "get_shared_client",
    "shutdown_shared_clients",
]
