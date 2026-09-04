"""Shared (cross-worker) replay guard for the AP2 mandate chain.

:class:`core.world_model.mandates.InMemoryReplayGuard` is process-local. With
``WEB_CONCURRENCY > 1`` — the normal production shape — each worker keeps its
own ledger, so the same signed intent+cart chain verifies once *per worker*:
replay protection silently degrades to "N executions of one authorized
purchase". This module supplies a Redis-backed guard whose ``SET key NX EX``
is atomic across every worker and replica, plus a resolver that picks it
automatically when a cache Redis URL is configured.

Fail-closed by design: if the ledger cannot be reached, verification raises
rather than allowing the purchase. A replay guard that cannot guarantee
single-use must not wave a payment through — for commerce, an availability
blip is cheaper than a double charge.
"""

from __future__ import annotations

from typing import Any

from core.observability.logging import get_logger

logger = get_logger(__name__)

# How long a consumed intent id stays on the ledger. It only has to outlive the
# intent's own expiry window (``verify_chain`` already rejects an expired
# intent, so a key that outlives it is redundant, never wrong). Seven days
# covers any sane window while keeping the keyspace bounded.
_DEFAULT_LEDGER_TTL_SECONDS = 7 * 24 * 3600

_KEY_PREFIX = "baselith:ap2:intent:"


class ReplayLedgerUnavailableError(RuntimeError):
    """The shared replay ledger could not be reached.

    Raised instead of returning a verdict: a guard that cannot prove an intent
    is unused must not report it as unused.
    """


class RedisReplayGuard:
    """Cross-worker :class:`ReplayGuard` backed by Redis ``SET NX EX``.

    The ``SET key value NX EX ttl`` round trip is atomic server-side, so two
    workers racing on the same intent id can never both observe "first use".

    Args:
        client: A **synchronous** Redis client (``redis.Redis``). Injected so
            the caller owns the connection lifecycle; ``verify_chain`` is a
            sync function, hence the sync client.
        ttl_seconds: How long a consumed intent id is remembered.
        key_prefix: Namespace for ledger keys.
    """

    def __init__(
        self,
        client: Any,
        *,
        ttl_seconds: int = _DEFAULT_LEDGER_TTL_SECONDS,
        key_prefix: str = _KEY_PREFIX,
    ) -> None:
        self._client = client
        self._ttl_seconds = max(1, ttl_seconds)
        self._key_prefix = key_prefix

    def register_once(self, key: str) -> bool:
        """Atomically claim ``key``; True on first use, False on replay.

        Raises:
            ReplayLedgerUnavailableError: The ledger could not be reached, so
                single-use cannot be guaranteed (fail-closed).
        """
        try:
            created = self._client.set(
                f"{self._key_prefix}{key}", b"1", nx=True, ex=self._ttl_seconds
            )
        except Exception as exc:
            # Never downgrade to "allowed": an unreachable ledger means the
            # single-use property is unknown, and for a payment authorization
            # unknown must read as refused.
            raise ReplayLedgerUnavailableError(
                f"AP2 replay ledger unavailable ({type(exc).__name__}); "
                "refusing to verify the mandate chain rather than risk a replay"
            ) from exc
        # redis-py returns True on a successful NX write and None when the key
        # already existed.
        return bool(created)


def build_default_replay_guard() -> Any:
    """Pick the strongest replay guard the deployment can support.

    Redis-backed when the deployment actually runs on Redis — the only correct
    choice once more than one worker or replica exists; otherwise the
    process-local in-memory guard, with a warning in production because that
    combination silently weakens replay protection to per-worker scope.

    The selection keys on ``CACHE_BACKEND == "redis"`` *and* a URL, not on the
    URL alone: ``CACHE_REDIS_URL`` ships with a non-empty default
    (``redis://localhost:6379/1``) while ``CACHE_BACKEND`` defaults to
    ``local``, so a URL-only test would pick the Redis guard on a stock config
    with no Redis deployed — and because this guard fails closed, every
    mandate verification would then raise instead of falling back.
    """
    from core.world_model.mandates import InMemoryReplayGuard

    redis_url = ""
    try:
        from core.config import get_storage_config

        storage = get_storage_config()
        if getattr(storage, "cache_backend", "") == "redis":
            redis_url = getattr(storage, "cache_redis_url", "") or ""
    except Exception:  # pragma: no cover - config unavailable in minimal envs
        redis_url = ""

    if redis_url:
        try:
            from core.cache.redis_sync import create_sync_redis_client

            return RedisReplayGuard(create_sync_redis_client(redis_url))
        except Exception as exc:
            logger.warning(
                "AP2 replay guard: Redis client construction failed (%s); "
                "falling back to the process-local ledger.",
                type(exc).__name__,
            )

    try:
        from core.config.environment import is_production_env

        if is_production_env():
            logger.error(
                "AP2 replay guard is process-local: with more than one worker "
                "or replica the same signed mandate chain can be executed once "
                "PER WORKER. Configure CACHE_REDIS_URL so the shared ledger is "
                "used, or pass an explicit shared replay_guard to verify_chain."
            )
    except Exception:  # pragma: no cover - advisory only
        pass

    return InMemoryReplayGuard()


__all__ = [
    "RedisReplayGuard",
    "ReplayLedgerUnavailableError",
    "build_default_replay_guard",
]
