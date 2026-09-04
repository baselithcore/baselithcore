"""Admin Basic-auth lockout state and checks.

Extracted from :mod:`core.middleware.security` to keep that module under the
500-line cap. Provides :class:`AdminLockoutMixin`, mixed into ``SecurityManager``
so the public API (``manager.check_admin_lockout`` / ``record_admin_failure`` /
``clear_admin_failures`` and the module-level wrappers) is unchanged.

The mixin relies on two attributes set by ``SecurityManager.__init__``:
``self.rate_limiter`` (for the shared Redis client) and ``self._lockout_fallback``
(the per-process in-memory fallback used when Redis is unavailable).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from fastapi import HTTPException, status

from core.middleware._security_env import (
    _is_production_env,
    _lockout_fail_open,
    _redis_backend_declared,
)
from core.middleware._security_metrics import SECURITY_EVENTS
from core.observability.logging import get_logger

if TYPE_CHECKING:
    from core.middleware.rate_limiter import RateLimiter

logger = get_logger(__name__)


class AdminLockoutMixin:
    """Redis-backed (with in-memory fallback) lockout for admin Basic-auth.

    Keys on the client **IP**, not the attacker-supplied username: keying on the
    username lets anyone lock out the real admin by hammering the (guessable)
    admin name; keying on the source IP throttles the attacker instead.
    """

    # Populated by SecurityManager.__init__; declared here for the type checker.
    rate_limiter: RateLimiter
    _lockout_fallback: dict[str, tuple[int, float]]

    # Admin lockout constants
    _LOCKOUT_MAX_FAILURES: int = 5
    _LOCKOUT_WINDOW_SECONDS: int = 60  # failures window
    _LOCKOUT_DURATION_SECONDS: int = 900  # 15 min lock

    def _refuse_without_shared_counter(self) -> None:
        """Fail closed in production when no shared lockout counter exists.

        In production the Redis counter *is* the control: per-replica memory is
        defeated by rotating replicas, so an attacker able to keep Redis
        unreachable would otherwise get unthrottled brute force against
        privileged auth. Only applies when the deployment actually declared a
        Redis backend — one that never configured Redis runs the in-process
        counter by design, and 503-ing it would be a self-inflicted outage.
        ``BASELITH_LOCKOUT_FAIL_OPEN=true`` remains the explicit opt-out.
        """
        if not (_is_production_env() and _redis_backend_declared()):
            return
        if _lockout_fail_open():
            return
        SECURITY_EVENTS.labels(reason="admin_lockout_store_down").inc()
        logger.error(
            "Redis is configured but unavailable for admin lockout in "
            "production — refusing privileged auth (fail closed). Set "
            "BASELITH_LOCKOUT_FAIL_OPEN=true to prefer availability."
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication temporarily unavailable.",
        )

    async def check_admin_lockout(self, identifier: str) -> None:
        """
        Raise HTTP 429 if this source is currently locked out.

        Args:
            identifier: Lockout key — the client **IP**, not the attacker-supplied
                username. Keying on the username lets anyone lock out the real
                admin by hammering the (guessable) admin name; keying on the
                source IP throttles the attacker instead.
        """
        key = f"{self.rate_limiter._prefix}admin_lockout:{identifier}"
        redis_client = self.rate_limiter._redis

        if redis_client is None:
            # The counter was never built. Redis unreachable at limiter
            # construction leaves ``_redis`` None for the process's whole life,
            # so this is not a transient blip the except-branch below would
            # catch — it is the same loss of the shared control, permanently,
            # and it used to drop silently to per-replica memory.
            self._refuse_without_shared_counter()

        if redis_client:
            try:
                failures = await redis_client.get(key)
                if failures and int(failures) >= self._LOCKOUT_MAX_FAILURES:
                    SECURITY_EVENTS.labels(reason="admin_lockout").inc()
                    raise HTTPException(
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        detail="Account temporarily locked. Try again later.",
                    )
                return
            except HTTPException:
                raise
            except Exception:
                # In production the shared counter IS the control: per-replica
                # memory is defeated by rotating replicas, so an attacker who
                # can degrade Redis would otherwise gain unthrottled
                # brute-force. Fail closed on privileged auth (503) unless the
                # operator explicitly prefers availability over the control
                # (BASELITH_LOCKOUT_FAIL_OPEN=true). Outside production the
                # in-memory fallback keeps local development frictionless.
                if _is_production_env() and not _lockout_fail_open():
                    SECURITY_EVENTS.labels(reason="admin_lockout_store_down").inc()
                    logger.error(
                        "Redis unavailable for admin lockout in production — "
                        "refusing privileged auth (fail closed). Set "
                        "BASELITH_LOCKOUT_FAIL_OPEN=true to prefer availability."
                    )
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail="Authentication temporarily unavailable.",
                    ) from None
                logger.warning(
                    "Redis failure during admin lockout check — using in-memory fallback"
                )

        # Fallback
        count, lock_until = self._lockout_fallback.get(identifier, (0, 0.0))
        if count >= self._LOCKOUT_MAX_FAILURES and time.time() < lock_until:
            SECURITY_EVENTS.labels(reason="admin_lockout").inc()
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Account temporarily locked. Try again later.",
            )

    async def record_admin_failure(self, identifier: str) -> None:
        """
        Increment the failure counter for a failed admin login.

        Args:
            identifier: Lockout key — the client **IP** (see
                :meth:`check_admin_lockout`).
        """
        key = f"{self.rate_limiter._prefix}admin_lockout:{identifier}"
        redis_client = self.rate_limiter._redis

        if redis_client:
            try:
                count = await redis_client.incr(key)
                if count == 1:
                    await redis_client.expire(key, self._LOCKOUT_WINDOW_SECONDS)
                if count >= self._LOCKOUT_MAX_FAILURES:
                    # Extend TTL to full lockout duration
                    await redis_client.expire(key, self._LOCKOUT_DURATION_SECONDS)
                return
            except Exception as exc:
                # Fall through to the in-process counter, but say so: a silently
                # dropped increment is a brute-force attempt that never counted,
                # and nothing else in the path would reveal it.
                SECURITY_EVENTS.labels(reason="admin_lockout_store_down").inc()
                logger.warning(
                    "Redis failure while recording an admin auth failure (%s) — "
                    "counting in process memory instead",
                    type(exc).__name__,
                )

        # Fallback
        count, lock_until = self._lockout_fallback.get(identifier, (0, 0.0))
        count += 1
        lock_until = (
            time.time() + self._LOCKOUT_DURATION_SECONDS
            if count >= self._LOCKOUT_MAX_FAILURES
            else lock_until
        )
        self._lockout_fallback[identifier] = (count, lock_until)
        # Evict stale entries to prevent unbounded growth when Redis is down.
        # An entry is stale if its lock_until timestamp is older than 2x the
        # lockout duration (entry has expired and is no longer tracking anything).
        now = time.time()
        stale_threshold = now - (2 * self._LOCKOUT_DURATION_SECONDS)
        if len(self._lockout_fallback) > 1000:
            stale_keys = [
                k
                for k, (_, lu) in self._lockout_fallback.items()
                if lu and lu < stale_threshold
            ]
            for k in stale_keys:
                self._lockout_fallback.pop(k, None)

    async def clear_admin_failures(self, identifier: str) -> None:
        """Clear failure counter after a successful admin login."""
        key = f"{self.rate_limiter._prefix}admin_lockout:{identifier}"
        self._lockout_fallback.pop(identifier, None)
        redis_client = self.rate_limiter._redis
        if redis_client:
            try:
                await redis_client.delete(key)
            except Exception:
                pass


__all__ = ["AdminLockoutMixin"]
