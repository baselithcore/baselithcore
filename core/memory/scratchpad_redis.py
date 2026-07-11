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

The client is the synchronous ``redis-py`` client because the
``ScratchpadBackend`` protocol is synchronous; operations are single
round-trip O(1) commands.
"""

from __future__ import annotations

import os
from typing import Any, Final

from core.observability.logging import get_logger

logger = get_logger(__name__)

DEFAULT_TTL_SECONDS: Final[int] = 86400


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
        url: str | None = None,
        ttl_seconds: int | None = None,
        key_prefix: str = "baselithcore:scratchpad",
    ) -> None:
        """
        Args:
            redis_client: Pre-built (sync) redis client; overrides ``url``.
            url: Redis connection URL; defaults to the cache Redis config.
            ttl_seconds: Sliding per-thread TTL; defaults to
                ``BASELITH_SCRATCHPAD_TTL_SECONDS`` (86400). ``0`` disables.
            key_prefix: Namespace prefix for scratchpad keys.
        """
        if redis_client is None:
            import redis as redis_lib

            from core.config.cache import get_redis_cache_config

            resolved_url = url or get_redis_cache_config().url
            redis_client = redis_lib.Redis.from_url(resolved_url, decode_responses=True)
        # Any: redis-py types sync commands as ``ResponseT | Awaitable`` (shared
        # stubs with the async client); this backend only ever holds a sync one.
        self._redis: Any = redis_client
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
        self._redis.hset(key, section, content)
        self._touch(key)

    def delete(self, thread_id: str, section: str) -> None:
        self._redis.hdel(self._key(thread_id), section)

    def list_sections(self, thread_id: str) -> list[str]:
        keys = self._redis.hkeys(self._key(thread_id))
        return sorted(k if isinstance(k, str) else k.decode() for k in keys)

    def clear(self, thread_id: str) -> None:
        self._redis.delete(self._key(thread_id))


__all__ = ["RedisScratchpadBackend"]
