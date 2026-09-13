"""Redis-backed scratchpad storage.

Durable :class:`~core.memory.scratchpad.ScratchpadBackend`: sections survive
process restarts and are shared across workers, completing the durability
story of checkpoint/resume (the checkpoint restores the loop's steps; this
restores the agent's written working memory).

Layout: one Redis hash per thread — ``{prefix}:{tenant}:{thread_id}`` with
one field per section. Every operation is O(1) server-side (HGET/HSET/HDEL);
``list_sections`` is HKEYS over a hash capped by the facade's
``max_sections``.

Security & lifecycle:

* **Tenant-scoped keys** — the key embeds the tenant resolved from the
  authenticated request context (``core.context.get_current_tenant_id``),
  never from caller input; under ``strict_tenant_isolation`` an unbound
  context fails closed instead of silently writing to a shared namespace.
* **Sliding TTL** — ``BASELITH_SCRATCHPAD_TTL_SECONDS`` (default 86400)
  refreshes on every write, so abandoned threads expire instead of
  accumulating forever. ``0`` disables expiry.

The backend holds **two** clients. The ``a``-prefixed protocol methods — the
ones the agent loop calls — use the asyncio ``redis-py`` client, so a
scratchpad round-trip suspends the coroutine instead of blocking the whole
event loop for the duration of the command. The synchronous methods stay as
thin wrappers over the sync client for non-async callers (scripts, sync tool
adapters). When no async client can be built the async methods offload the
sync call with :func:`asyncio.to_thread` rather than blocking the loop.
"""

from __future__ import annotations

import asyncio
import os
import weakref
from collections import OrderedDict
from typing import Any, Final

from core.observability.logging import get_logger

logger = get_logger(__name__)

DEFAULT_TTL_SECONDS: Final[int] = 86400

#: Retained lazily built async clients, keyed by event loop. More than a
#: couple means a process cycling through loops; the oldest are dropped.
_MAX_LOOP_CLIENTS: Final[int] = 8


def _ttl_from_env() -> int:
    raw = os.getenv("BASELITH_SCRATCHPAD_TTL_SECONDS", str(DEFAULT_TTL_SECONDS))
    try:
        return max(int(raw), 0)
    except ValueError:
        return DEFAULT_TTL_SECONDS


class RedisScratchpadBackend:
    """Durable, tenant-scoped scratchpad backend over a Redis hash per thread."""

    def __init__(
        self,
        redis_client: Any | None = None,
        *,
        async_redis_client: Any | None = None,
        url: str | None = None,
        ttl_seconds: int | None = None,
        key_prefix: str = "baselithcore:scratchpad",
    ) -> None:
        """
        Args:
            redis_client: Pre-built (sync) redis client; overrides ``url``.
            async_redis_client: Pre-built asyncio redis client used by the
                ``a``-prefixed methods. Built lazily from ``url`` / the cache
                config on first async use when omitted.
            url: Redis connection URL; defaults to the cache Redis config.
            ttl_seconds: Sliding per-thread TTL; defaults to
                ``BASELITH_SCRATCHPAD_TTL_SECONDS`` (86400). ``0`` disables.
            key_prefix: Namespace prefix for scratchpad keys.
        """
        if redis_client is None:
            from core.cache.redis_sync import create_sync_redis_client
            from core.config.cache import get_redis_cache_config

            resolved_url = url or get_redis_cache_config().url
            redis_client = create_sync_redis_client(resolved_url, decode_responses=True)
        # Any: redis-py types sync commands as ``ResponseT | Awaitable`` (shared
        # stubs with the async client); this backend holds one of each.
        self._redis: Any = redis_client
        # An injected client is used verbatim on every loop; a lazily built
        # one is memoised per event loop (see _resolve_async_client).
        self._async_redis: Any | None = async_redis_client
        self._loop_clients: OrderedDict[int, tuple[weakref.ref[Any], Any]] = (
            OrderedDict()
        )
        self._async_client_failed = False
        self._url = url
        self._ttl = _ttl_from_env() if ttl_seconds is None else max(ttl_seconds, 0)
        self._prefix = key_prefix

    def _key(self, thread_id: str) -> str:
        # Tenant from the authenticated context — never caller-supplied.
        # Fails closed under strict_tenant_isolation when unbound.
        from core.context import get_current_tenant_id

        return f"{self._prefix}:{get_current_tenant_id()}:{thread_id}"

    def _touch(self, key: str) -> None:
        if self._ttl > 0:
            self._redis.expire(key, self._ttl)

    def get(self, thread_id: str, section: str) -> str | None:
        value = self._redis.hget(self._key(thread_id), section)
        return value if value is None or isinstance(value, str) else value.decode()

    def set(self, thread_id: str, section: str, content: str) -> None:
        key = self._key(thread_id)
        # HSET + EXPIRE in one pipelined round-trip instead of two sequential
        # commands (falls back for clients without pipeline support).
        pipeline_factory = getattr(self._redis, "pipeline", None)
        if self._ttl > 0 and callable(pipeline_factory):
            pipe: Any = pipeline_factory(transaction=False)
            pipe.hset(key, section, content)
            pipe.expire(key, self._ttl)
            pipe.execute()
            return
        self._redis.hset(key, section, content)
        self._touch(key)

    def delete(self, thread_id: str, section: str) -> None:
        self._redis.hdel(self._key(thread_id), section)

    def list_sections(self, thread_id: str) -> list[str]:
        keys = self._redis.hkeys(self._key(thread_id))
        return sorted(k if isinstance(k, str) else k.decode() for k in keys)

    def get_all(self, thread_id: str) -> dict[str, str]:
        """Every section in ONE ``HGETALL`` round-trip.

        Optional fast path the :class:`~core.memory.scratchpad.Scratchpad`
        facade sniffs for ``read_all`` — without it a full read paid HKEYS
        plus one HGET per section.
        """
        raw = self._redis.hgetall(self._key(thread_id))
        return {
            (k if isinstance(k, str) else k.decode()): (
                v if isinstance(v, str) else v.decode()
            )
            for k, v in raw.items()
        }

    def clear(self, thread_id: str) -> None:
        self._redis.delete(self._key(thread_id))

    # -- async surface -----------------------------------------------------

    def _resolve_async_client(self) -> Any | None:
        """The asyncio redis client for the *running loop*; ``None`` if impossible.

        An injected client is always returned as-is — it is the caller's to
        manage. A lazily built one is memoised **per event loop**, mirroring
        the loop-keyed pool cache in
        :func:`core.cache.redis_cache.create_redis_client`: an asyncio Redis
        connection is bound to the loop that opened it, so a backend reused
        from a second loop (a restarted worker, two ``asyncio.run`` calls)
        must get a fresh client rather than one whose pool belongs to a
        closed loop.

        A deployment without any async client (redis missing, no resolvable
        URL) must still work: the async methods then offload the synchronous
        command to a worker thread, which is slower but never blocks the loop.
        That failure is remembered so the import/connect cost is paid once.
        """
        if self._async_redis is not None:
            return self._async_redis
        if self._async_client_failed:
            return None
        try:
            loop: Any | None = asyncio.get_running_loop()
        except RuntimeError:  # no running loop: nothing to key the memo by
            loop = None
        loop_key = None if loop is None else id(loop)
        if loop_key is not None:
            cached = self._loop_clients.get(loop_key)
            # CPython reuses the id of a collected loop, so the id alone is
            # not proof of identity: the weakref must still resolve to *this*
            # loop. A dead or mismatched entry is a miss, not a stale client.
            if cached is not None:
                loop_ref, client = cached
                if loop_ref() is loop:
                    return client
                del self._loop_clients[loop_key]
        try:
            from core.cache.redis_cache import create_redis_client
            from core.config.cache import get_redis_cache_config

            url = self._url or get_redis_cache_config().url
            client = create_redis_client(url, decode_responses=True)
        except Exception as exc:
            logger.warning(
                "async redis client unavailable for the scratchpad (%s); "
                "async calls will be offloaded to a thread",
                exc,
            )
            self._async_client_failed = True
            return None
        if loop_key is not None:
            try:
                loop_ref = weakref.ref(loop)
            except TypeError:  # a loop implementation without weak references
                return client
            # Bounded: a process cycling through loops (test suites, repeated
            # asyncio.run) must not accumulate one dead entry per loop.
            self._loop_clients[loop_key] = (loop_ref, client)
            while len(self._loop_clients) > _MAX_LOOP_CLIENTS:
                self._loop_clients.popitem(last=False)
        return client

    @staticmethod
    def _decode(value: Any) -> str:
        return value if isinstance(value, str) else value.decode()

    async def aget(self, thread_id: str, section: str) -> str | None:
        client = self._resolve_async_client()
        if client is None:
            return await asyncio.to_thread(self.get, thread_id, section)
        value = await client.hget(self._key(thread_id), section)
        return None if value is None else self._decode(value)

    async def aset(self, thread_id: str, section: str, content: str) -> None:
        client = self._resolve_async_client()
        if client is None:
            await asyncio.to_thread(self.set, thread_id, section, content)
            return
        key = self._key(thread_id)
        # HSET + EXPIRE in one pipelined round-trip where the client supports
        # it, mirroring the synchronous path.
        pipeline_factory = getattr(client, "pipeline", None)
        if self._ttl > 0 and callable(pipeline_factory):
            pipe: Any = pipeline_factory(transaction=False)
            pipe.hset(key, section, content)
            pipe.expire(key, self._ttl)
            await pipe.execute()
            return
        await client.hset(key, section, content)
        if self._ttl > 0:
            await client.expire(key, self._ttl)

    async def adelete(self, thread_id: str, section: str) -> None:
        client = self._resolve_async_client()
        if client is None:
            await asyncio.to_thread(self.delete, thread_id, section)
            return
        await client.hdel(self._key(thread_id), section)

    async def alist(self, thread_id: str) -> list[str]:
        client = self._resolve_async_client()
        if client is None:
            return await asyncio.to_thread(self.list_sections, thread_id)
        keys = await client.hkeys(self._key(thread_id))
        return sorted(self._decode(k) for k in keys)

    async def aget_all(self, thread_id: str) -> dict[str, str]:
        """Every section in ONE ``HGETALL`` (async fast path for ``read_all``)."""
        client = self._resolve_async_client()
        if client is None:
            return await asyncio.to_thread(self.get_all, thread_id)
        raw = await client.hgetall(self._key(thread_id))
        return {self._decode(k): self._decode(v) for k, v in raw.items()}

    async def aclear(self, thread_id: str) -> None:
        client = self._resolve_async_client()
        if client is None:
            await asyncio.to_thread(self.clear, thread_id)
            return
        await client.delete(self._key(thread_id))


__all__ = ["RedisScratchpadBackend"]
