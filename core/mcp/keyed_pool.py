"""A bounded per-key pool of MCP clients.

:class:`~core.mcp.pool.MCPConnectionPool` is a process-wide registry keyed by
server name: right for operator-declared servers, wrong for a multi-tenant
deployment where each (tenant, user, server) has its own credentials and must
never share a connection with another. :class:`KeyedMCPClientPool` keeps one
client per caller-chosen key and bounds the whole thing: at most
``MCP_POOL_MAX_CLIENTS`` live clients, a least-recently-used idle client is
closed to make room, and a client unused for ``MCP_POOL_IDLE_SECONDS`` is
closed on the next access or :meth:`~KeyedMCPClientPool.reap`.

A client is *leased* for the duration of a call; a leased client is never
evicted or expired, and when every slot is leased a new key gets
:class:`MCPPoolExhaustedError` rather than an unbounded pool.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable, Hashable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Protocol

from core.config import get_mcp_config
from core.mcp.client_errors import MCPClientError
from core.observability.logging import get_logger

logger = get_logger(__name__)


class MCPPoolExhaustedError(MCPClientError):
    """Every slot of the pool is in use and none can be evicted."""


class PoolableClient(Protocol):
    """What the pool needs from a client (``MCPClient`` satisfies it)."""

    async def connect(self) -> object: ...

    async def disconnect(self) -> None: ...


@dataclass
class _Entry:
    client: PoolableClient
    last_used: float
    leases: int = 0


class KeyedMCPClientPool:
    """One connected client per key, bounded, with idle expiry.

    Example:
        pool = KeyedMCPClientPool()
        key = (tenant_id, user_id, "notion")
        async with pool.lease(key, lambda: MCPClient(url=..., auth=token_for(key))) as c:
            await c.call_tool("search", {"q": "roadmap"})
    """

    def __init__(
        self,
        *,
        max_clients: int | None = None,
        idle_seconds: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """
        Args:
            max_clients: Live-client cap; defaults to ``MCP_POOL_MAX_CLIENTS``.
            idle_seconds: Idle lifetime; defaults to ``MCP_POOL_IDLE_SECONDS``.
            clock: Monotonic time source (injectable for tests).
        """
        config = get_mcp_config()
        self.max_clients = max_clients or config.mcp_pool_max_clients
        self.idle_seconds = idle_seconds or config.mcp_pool_idle_seconds
        self._clock = clock
        self._entries: OrderedDict[Hashable, _Entry] = OrderedDict()
        self._locks: dict[Hashable, asyncio.Lock] = {}
        self._reserved = 0  # slots promised to connects still in flight

    def __len__(self) -> int:
        return len(self._entries)

    def keys(self) -> list[Hashable]:
        """Keys of the live clients, least recently used first."""
        return list(self._entries)

    def _expired(self, entry: _Entry, now: float) -> bool:
        return entry.leases == 0 and now - entry.last_used > self.idle_seconds

    @staticmethod
    async def _close_client(client: PoolableClient) -> None:
        try:
            await client.disconnect()
        except Exception as exc:  # closing is best effort
            logger.debug("mcp_pool_disconnect_failed", error=type(exc).__name__)

    def _make_room(self) -> list[PoolableClient]:
        """Pop idle LRU entries until a slot is free; raise if none can go."""
        victims: list[PoolableClient] = []
        while len(self._entries) + self._reserved >= self.max_clients:
            idle = next((k for k, e in self._entries.items() if e.leases == 0), None)
            if idle is None:
                raise MCPPoolExhaustedError(
                    f"MCP client pool is full ({self.max_clients} clients, all in use)"
                )
            victims.append(self._entries.pop(idle).client)
        return victims

    async def _acquire(
        self, key: Hashable, factory: Callable[[], PoolableClient]
    ) -> _Entry:
        lock = self._locks.setdefault(key, asyncio.Lock())
        try:
            async with lock:
                now = self._clock()
                entry = self._entries.get(key)
                if entry is not None and self._expired(entry, now):
                    del self._entries[key]
                    await self._close_client(entry.client)
                    entry = None
                if entry is None:
                    victims = self._make_room()
                    self._reserved += 1
                    try:
                        for victim in victims:
                            await self._close_client(victim)
                        client = factory()
                        await client.connect()
                    finally:
                        self._reserved -= 1
                    entry = _Entry(client=client, last_used=now)
                    self._entries[key] = entry
                self._entries.move_to_end(key)
                entry.leases += 1
                entry.last_used = now
                return entry
        finally:
            if key not in self._entries and not lock.locked():
                self._locks.pop(key, None)

    @asynccontextmanager
    async def lease[C: PoolableClient](
        self, key: Hashable, factory: Callable[[], C]
    ) -> AsyncIterator[C]:
        """Hold the client for *key* (connecting it on first use) for a call.

        Args:
            key: Any hashable identity, e.g. ``(tenant, user, server)``.
            factory: Builds an *unconnected* client; called only on a miss.

        Raises:
            MCPPoolExhaustedError: Full pool, every client currently leased.
            Exception: Whatever ``connect()`` raised; nothing is cached then.
        """
        entry = await self._acquire(key, factory)
        try:
            yield entry.client  # type: ignore[misc]
        finally:
            entry.leases -= 1
            entry.last_used = self._clock()

    async def reap(self) -> int:
        """Close every idle client past its lifetime; return how many."""
        now = self._clock()
        stale = [k for k, e in self._entries.items() if self._expired(e, now)]
        for key in stale:
            await self._close_client(self._entries.pop(key).client)
        return len(stale)

    async def close(self, key: Hashable) -> None:
        """Close and forget the client for *key* (no-op when absent).

        Closes even a leased client: use it to drop a connection that failed.
        """
        entry = self._entries.pop(key, None)
        if entry is not None:
            await self._close_client(entry.client)

    async def close_all(self) -> None:
        """Close every client."""
        entries = list(self._entries.values())
        self._entries.clear()
        for entry in entries:
            await self._close_client(entry.client)

    async def __aenter__(self) -> KeyedMCPClientPool:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close_all()


__all__ = ["KeyedMCPClientPool", "MCPPoolExhaustedError", "PoolableClient"]
