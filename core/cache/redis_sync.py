"""Shared, bounded connection pools for the **synchronous** Redis client.

The async cache layer already funnels every caller through one bounded pool per
URL (:func:`core.cache.redis_cache.create_redis_client`). The synchronous
call sites did not: each built its own client with ``Redis.from_url``, which
gives redis-py's defaults — ``max_connections`` of ``2**31``, no health check
interval and, worse, **no socket deadlines**. A Redis that accepts the
connection and then stops answering mid-command therefore blocked the caller
forever, and every such caller was a thread that never came back.

This module is the sync twin: same bounds, same deadlines, one pool per
``(url, decode_responses, socket_timeout)``. It lives apart from
``redis_cache`` on purpose — that module binds ``Redis``/``ConnectionPool`` to
the *asyncio* classes, and holding both meanings of those two names in one
namespace is exactly the kind of ambiguity that produces a coroutine where a
value was expected.

Closing a client built here never disconnects the shared pool: redis-py only
tears the pool down when the client created it (``auto_close_connection_pool``
is False whenever a pool is passed in), so a component that closes its own
handle cannot take the pool away from the others.
"""

from __future__ import annotations

from threading import Lock
from typing import Any

from core.observability.logging import get_logger

try:  # pragma: no cover - exercised via the import fallback in tests
    from redis import ConnectionPool, Redis
except ImportError:  # pragma: no cover - redis is an optional dependency
    ConnectionPool = None  # type: ignore[assignment,misc]
    Redis = None  # type: ignore[assignment,misc]

logger = get_logger(__name__)

#: Keyed by ``(url, decode_responses, socket_timeout)``. ``decode_responses``
#: and the socket deadline are connection-level settings in redis-py, so two
#: callers that disagree on either must not share a pool: they would otherwise
#: get whichever setting happened to create it first.
_sync_pools: dict[tuple[str, bool, float | None], Any] = {}
_sync_pools_lock = Lock()


def create_sync_redis_client(
    url: str,
    *,
    decode_responses: bool = False,
    socket_timeout: float | None = None,
) -> Any:
    """Create a synchronous Redis client backed by a shared bounded pool.

    Args:
        url: Redis connection URL.
        decode_responses: When True the client returns ``str`` instead of
            ``bytes``.
        socket_timeout: Per-operation deadline in seconds. Defaults to the
            cache config's ``socket_timeout``; pass a value only when the
            component has its own budget (the graph client does).

    Returns:
        A ``redis.Redis`` bound to the shared pool for these settings.

    Raises:
        RuntimeError: If the ``redis`` package is not installed.
    """
    if Redis is None or ConnectionPool is None:
        raise RuntimeError("redis package is not installed.")

    from core.config.cache import get_redis_cache_config

    config = get_redis_cache_config()
    timeout = config.socket_timeout if socket_timeout is None else socket_timeout
    pool_key = (url, decode_responses, socket_timeout)

    with _sync_pools_lock:
        pool = _sync_pools.get(pool_key)
        if pool is None:
            pool = ConnectionPool.from_url(
                url,
                max_connections=config.max_connections,
                health_check_interval=config.health_check_interval,
                socket_timeout=timeout,
                socket_connect_timeout=config.socket_connect_timeout,
                decode_responses=decode_responses,
            )
            _sync_pools[pool_key] = pool

    return Redis(connection_pool=pool)


def close_sync_redis_pools() -> None:
    """Disconnect and forget every shared synchronous pool.

    Called on lifespan shutdown so a rolling deploy releases the server-side
    connections promptly instead of leaving them for Redis to time out. Safe to
    call when redis is not installed or nothing was ever created.
    """
    if ConnectionPool is None:
        return

    with _sync_pools_lock:
        pools = list(_sync_pools.values())
        _sync_pools.clear()

    for pool in pools:
        try:
            pool.disconnect()
        except Exception as exc:  # pragma: no cover - best effort on shutdown
            logger.debug("Sync Redis pool close skipped: %s", type(exc).__name__)


__all__ = ["close_sync_redis_pools", "create_sync_redis_client"]
