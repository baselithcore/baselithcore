"""Shared ``httpx.AsyncClient`` pool with bounded keep-alive lifetime.

Channel adapters and the ClawHub client previously instantiated a new
``httpx.AsyncClient`` per request, paying the TCP+TLS handshake every
time. This module returns a long-lived client per (timeout, base_url)
key and provides a `close_all()` hook called from the plugin shutdown
path. TLS verification is enforced; explicit ``verify=False`` callers
must pass it through ``ClientFactory.acquire(verify=False)``.

Every pooled client is SSRF-hardened via :func:`plugins.baselithbot.http.
hardened_client` — this pool backs the custom agent/cron "webhook" actions
(``agents/custom.py``, ``cron/custom.py``), whose target URL comes straight
from user-supplied action config, making it a prime SSRF vector.
``verify``/``limits`` are applied to the pool's own inner transport (passed
through as the ``transport`` kwarg) so the hardening wrapper doesn't
override the pool's TLS/keep-alive configuration.
"""

from __future__ import annotations

import asyncio
import threading
from contextlib import suppress
from typing import TYPE_CHECKING

from plugins.baselithbot.http import hardened_client

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
                # create_hardened_async_client ignores `verify`/`limits` kwargs
                # forwarded to httpx.AsyncClient once a `transport` is given (httpx
                # only applies them when building its own default transport), so
                # build the inner transport ourselves to keep both TLS-verify
                # overrides and pool keep-alive working, then let hardened_client
                # wrap it in the SSRF-validating transport.
                inner_transport = httpx.AsyncHTTPTransport(verify=verify, limits=limits)
                client = hardened_client(
                    timeout=timeout or self._default_timeout,
                    transport=inner_transport,
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
