"""Session registry for the MCP Streamable HTTP transport.

The legacy era of the protocol mints an ``Mcp-Session-Id`` at ``initialize``
and expects every later request to present it. That contract was kept in a
process dictionary, which is correct for exactly one replica: behind an
ordinary load balancer the client's second request lands elsewhere, gets the
spec's ``404``, re-initializes, and does it again on the next hop.

Two implementations share one async interface:

* :class:`SessionStore` — process-local, the historical behaviour, used when
  the deployment runs no shared cache;
* :class:`RedisSessionStore` — the same contract in Redis, selected
  automatically by :func:`build_session_store` when the deployment already
  declares a Redis cache backend.

Both bind a session to the identity that created it (the 2025-06-18 transport
requires it, so one client cannot ride another's id) and both cap how many
live sessions a single identity may hold, so a client cannot mint sessions
unbounded and pin storage for the whole TTL.

Split out of :mod:`core.mcp.http_transport` to keep that module under the
500-line cap; ``SessionStore`` is re-exported there for existing importers.
"""

from __future__ import annotations

import secrets
import time
from typing import Any

from core.cache.redis_cache import create_redis_client
from core.config.cache import get_redis_cache_config
from core.config.storage import get_storage_config
from core.observability.logging import get_logger

logger = get_logger(__name__)

#: Key namespace for the Redis-backed store. Kept distinct from the cache's
#: own prefix so flushing an application cache never strands live sessions.
REDIS_KEY_PREFIX = "mcp:session"

#: Stand-in for "no authenticated owner" (auth disabled). An empty string
#: would collide with a real owner id of ``""``.
_ANONYMOUS_OWNER = "\x00anonymous"


def _owner_key(owner: str | None) -> str:
    return _ANONYMOUS_OWNER if owner is None else owner


class SessionStore:
    """Process-local MCP session registry with TTL-based expiry.

    Each session is bound to the identity that created it (the authenticated
    ``user_id``, or ``None`` when auth is disabled). ``touch``/``terminate``
    verify the presenting caller owns the session. A per-owner cap bounds how
    many live sessions a single identity can hold.

    Process-local by design, and the fallback when no shared cache is
    configured: Streamable HTTP sessions are then an affinity contract between
    one client and one server instance, and the spec's recovery path — a 404
    answered by re-initializing — covers failover.
    """

    def __init__(self, ttl_seconds: float, max_per_owner: int = 0) -> None:
        """
        Args:
            ttl_seconds: Idle lifetime of a session, refreshed on every touch.
            max_per_owner: Live sessions one identity may hold; 0 disables.
        """
        self._ttl = ttl_seconds
        self._max_per_owner = max_per_owner
        # session_id -> (owner, last_seen)
        self._sessions: dict[str, tuple[str | None, float]] = {}

    async def create(self, owner: str | None) -> str | None:
        """Mint a random session id bound to *owner*.

        Returns:
            The new session id, or ``None`` when *owner* already holds
            ``max_per_owner`` live sessions.
        """
        self._prune()
        if self._max_per_owner and self._count_for(owner) >= self._max_per_owner:
            return None
        session_id = secrets.token_urlsafe(32)
        self._sessions[session_id] = (owner, time.monotonic())
        return session_id

    async def touch(self, session_id: str, owner: str | None) -> bool:
        """Refresh *session_id*; False when unknown, expired, or not *owner*'s."""
        entry = self._sessions.get(session_id)
        if entry is None:
            return False
        stored_owner, last_seen = entry
        if time.monotonic() - last_seen > self._ttl:
            del self._sessions[session_id]
            return False
        if stored_owner != owner:
            # Belongs to a different identity — refuse (no session takeover).
            return False
        self._sessions[session_id] = (stored_owner, time.monotonic())
        return True

    async def terminate(self, session_id: str, owner: str | None) -> bool:
        """Drop *session_id*; False when not active or not *owner*'s."""
        entry = self._sessions.get(session_id)
        if entry is None or entry[0] != owner:
            return False
        del self._sessions[session_id]
        return True

    def _count_for(self, owner: str | None) -> int:
        return sum(1 for stored, _ in self._sessions.values() if stored == owner)

    def _prune(self) -> None:
        now = time.monotonic()
        expired = [
            s for s, (_, seen) in self._sessions.items() if now - seen > self._ttl
        ]
        for session_id in expired:
            del self._sessions[session_id]


class RedisSessionStore:
    """Shared MCP session registry backed by the deployment's Redis cache.

    Layout, chosen so every operation is a single round trip on keys Redis
    expires by itself:

    * ``{prefix}:id:{session_id}`` → the owner, with the session TTL. This is
      the authoritative record; expiry is Redis's job, not a sweep of ours.
    * ``{prefix}:owner:{owner}`` → a hash of ``session_id -> expiry``, used
      only to count a single identity's live sessions for the cap. Entries are
      filtered by their recorded expiry on read, and the hash itself carries a
      TTL so an idle owner leaves nothing behind.

    A Redis failure degrades to a process-local :class:`SessionStore` rather
    than refusing service: that is exactly the behaviour this transport had
    before a shared store existed — the owner binding is still enforced, just
    only within this process — and a hard failure would turn a Redis blip into
    a client re-initialize loop.
    """

    def __init__(
        self,
        redis: Any,
        ttl_seconds: float,
        max_per_owner: int = 0,
        *,
        prefix: str = REDIS_KEY_PREFIX,
    ) -> None:
        """
        Args:
            redis: An async Redis client decoding responses to ``str``.
            ttl_seconds: Idle lifetime of a session, refreshed on every touch.
            max_per_owner: Live sessions one identity may hold; 0 disables.
            prefix: Key namespace, so sessions never collide with cache keys.
        """
        self._redis = redis
        # Whole seconds, and at least one. redis-py validates `ex`/`seconds`
        # up front and raises DataError ("ex must be datetime.timedelta or
        # int") on a float — before any I/O — so a float TTL made *every*
        # operation raise and silently degrade to the process-local fallback,
        # with nothing in Redis and no sign of it beyond a warning. Flooring at
        # 1 also avoids `EXPIRE key 0`, which deletes the key outright.
        self._ttl = max(1, int(ttl_seconds))
        self._max_per_owner = max_per_owner
        self._prefix = prefix
        self._fallback = SessionStore(ttl_seconds, max_per_owner)

    def _session_key(self, session_id: str) -> str:
        return f"{self._prefix}:id:{session_id}"

    def _owner_bucket(self, owner: str | None) -> str:
        return f"{self._prefix}:owner:{_owner_key(owner)}"

    def _degrade(self, operation: str, exc: Exception) -> None:
        logger.warning(
            "mcp_session_store_redis_unavailable",
            operation=operation,
            error=str(exc),
            hint="Serving sessions from process-local memory until Redis recovers.",
        )

    async def create(self, owner: str | None) -> str | None:
        """Mint a session id bound to *owner*, shared across replicas."""
        try:
            if self._max_per_owner:
                live = await self._live_sessions(owner)
                if len(live) >= self._max_per_owner:
                    return None
            session_id = secrets.token_urlsafe(32)
            await self._redis.set(
                self._session_key(session_id), _owner_key(owner), ex=self._ttl
            )
            await self._record(owner, session_id)
            return session_id
        except Exception as exc:
            self._degrade("create", exc)
            return await self._fallback.create(owner)

    async def touch(self, session_id: str, owner: str | None) -> bool:
        """Refresh *session_id*; False when unknown, expired, or not *owner*'s."""
        try:
            stored = await self._redis.get(self._session_key(session_id))
            if stored is None:
                return False
            if _decode(stored) != _owner_key(owner):
                return False
            await self._redis.expire(self._session_key(session_id), self._ttl)
            await self._record(owner, session_id)
            return True
        except Exception as exc:
            self._degrade("touch", exc)
            return await self._fallback.touch(session_id, owner)

    async def terminate(self, session_id: str, owner: str | None) -> bool:
        """Drop *session_id* everywhere; False when not active or not *owner*'s."""
        try:
            stored = await self._redis.get(self._session_key(session_id))
            if stored is None or _decode(stored) != _owner_key(owner):
                return False
            await self._redis.delete(self._session_key(session_id))
            await self._redis.hdel(self._owner_bucket(owner), session_id)
            return True
        except Exception as exc:
            self._degrade("terminate", exc)
            return await self._fallback.terminate(session_id, owner)

    async def _record(self, owner: str | None, session_id: str) -> None:
        """Note the session against its owner, for the per-owner cap."""
        bucket = self._owner_bucket(owner)
        await self._redis.hset(bucket, session_id, str(time.time() + self._ttl))
        await self._redis.expire(bucket, self._ttl)

    async def _live_sessions(self, owner: str | None) -> list[str]:
        """The owner's sessions that have not expired, pruning the ones that have."""
        bucket = self._owner_bucket(owner)
        entries = await self._redis.hgetall(bucket)
        now = time.time()
        live: list[str] = []
        stale: list[str] = []
        for session_id, expires_at in (entries or {}).items():
            identifier = _decode(session_id)
            try:
                expiry = float(_decode(expires_at))
            except (TypeError, ValueError):
                stale.append(identifier)
                continue
            (live if expiry > now else stale).append(identifier)
        if stale:
            await self._redis.hdel(bucket, *stale)
        return live


def _decode(value: Any) -> str:
    """Accept both decoding modes, so a caller-supplied client works either way."""
    return value.decode() if isinstance(value, bytes | bytearray) else str(value)


def build_session_store(cfg: Any) -> SessionStore | RedisSessionStore:
    """Pick the session store this deployment should use.

    Redis-backed when the deployment already declares a Redis cache backend
    (``CACHE_BACKEND=redis``), process-local otherwise. Never a new dependency:
    a deployment that runs no Redis keeps exactly the behaviour it had, and one
    that does gets replica-safe sessions with no extra setting to discover.

    Args:
        cfg: The MCP config (``mcp_http_session_ttl_seconds``,
            ``mcp_http_max_sessions_per_client``).

    Returns:
        The store the HTTP transport should mint sessions in.
    """
    ttl = float(getattr(cfg, "mcp_http_session_ttl_seconds", 3600))
    max_per_owner = int(getattr(cfg, "mcp_http_max_sessions_per_client", 0) or 0)
    if getattr(get_storage_config(), "cache_backend", "") != "redis":
        return SessionStore(ttl_seconds=ttl, max_per_owner=max_per_owner)
    try:
        client = create_redis_client(
            get_redis_cache_config().url, decode_responses=True
        )
    except Exception as exc:
        logger.warning(
            "mcp_session_store_redis_unavailable_at_build",
            error=str(exc),
            hint="Falling back to process-local MCP sessions.",
        )
        return SessionStore(ttl_seconds=ttl, max_per_owner=max_per_owner)
    logger.info("mcp_session_store_backend", backend="redis")
    return RedisSessionStore(client, ttl_seconds=ttl, max_per_owner=max_per_owner)


__all__ = [
    "REDIS_KEY_PREFIX",
    "RedisSessionStore",
    "SessionStore",
    "build_session_store",
]
