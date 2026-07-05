"""
Security Middleware

Provides authentication, authorization, rate limiting, and security headers.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from collections.abc import Iterable
from typing import Any

from fastapi import HTTPException, Request, status

from core.auth.types import AuthError
from core.config import SecurityConfig, get_security_config
from core.context import set_tenant_context as _set_tenant_ctx
from core.context import set_user_context as _set_user_ctx
from core.middleware._security_metrics import SECURITY_EVENTS

# The distributed rate limiter lives in a sibling module (extracted to keep
# this file under the 500-line cap); re-exported so
# ``from core.middleware.security import RateLimiter`` keeps working.
from core.middleware.rate_limiter import RateLimiter as RateLimiter

# Pure ASGI security middlewares live in a sibling module; re-exported here so
# ``from core.middleware.security import SecurityHeadersMiddleware`` keeps working.
from core.middleware.security_headers import (
    RequestSizeLimitMiddleware as RequestSizeLimitMiddleware,
)
from core.middleware.security_headers import (
    SecurityHeadersMiddleware as SecurityHeadersMiddleware,
)
from core.observability.logging import get_logger

logger = get_logger(__name__)


class SecurityManager:
    """
    Manages Authentication, Authorization and Rate Limiting logic.
    """

    def __init__(self, config: SecurityConfig) -> None:
        self.config = config
        self.rate_limiter = RateLimiter()
        # In-memory fallback for admin lockout when Redis is unavailable.
        # Maps username -> (failure_count, lock_until_timestamp).
        self._lockout_fallback: dict[str, tuple[int, float]] = {}

    def _extract_credentials(self, request: Request) -> tuple[str | None, str | None]:
        """Extract API key and bearer token from request headers."""
        header_key = request.headers.get("x-api-key") or request.headers.get(
            "X-API-Key"
        )
        api_key = header_key.strip() if header_key else None

        authorization = request.headers.get("authorization") or request.headers.get(
            "Authorization"
        )
        bearer = None
        if authorization and authorization.lower().startswith("bearer "):
            bearer = authorization[7:].strip()
        return api_key, bearer

    async def enforce_auth(
        self,
        request: Request,
        allowed_roles: Iterable[str],
        *,
        limit_per_minute: int | None,
    ) -> str:
        """Enforce authentication and rate limiting."""
        # Local import kept for get_auth_manager only: core.auth.manager pulls
        # in the full auth stack (JWT/Redis) which must stay lazy at import
        # time; the result is a cached singleton so the per-request cost is a
        # sys.modules lookup.
        from core.auth.manager import get_auth_manager

        auth_manager = get_auth_manager()

        allowed_set = set(allowed_roles)
        has_keys_for_allowed = any(
            [
                ("admin" in allowed_set and self.config.api_keys_admin),
                ("job" in allowed_set and self.config.api_keys_job),
                ("user" in allowed_set and self.config.api_keys_user),
            ]
        )
        api_key, bearer = self._extract_credentials(request)

        auth_header = request.headers.get("authorization") or request.headers.get(
            "Authorization"
        )
        if not auth_header and api_key:
            auth_header = f"ApiKey {api_key}"

        # Reuse the quota middleware's verification when it already
        # authenticated this exact header with this exact AuthManager instance
        # (avoids verifying the same token twice per request). Any mismatch —
        # different header, different manager, quotas disabled — falls through
        # to a full authenticate.
        memo = getattr(request.state, "_auth_memo", None)
        if memo is not None and memo[0] == auth_header and memo[1] == id(auth_manager):
            user = memo[2]
        else:
            try:
                user = await auth_manager.authenticate(auth_header)
            except AuthError as e:
                SECURITY_EVENTS.labels(reason="unauthorized").inc()
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=str(e),
                    headers={"WWW-Authenticate": "Bearer"},
                ) from e

        client_ip = request.client.host if request.client else "unknown"
        user_agent = request.headers.get("user-agent", "unknown")[:200]

        if not user.is_authenticated:
            # Anonymous bypass is only permitted for non-privileged routes when
            # auth is globally disabled AND no API keys are configured for the
            # allowed roles. Admin/job/service routes must NEVER accept
            # anonymous traffic, regardless of `auth_required`.
            privileged_required = bool(allowed_set & {"admin", "job", "service"})
            if (
                not self.config.auth_required
                and not has_keys_for_allowed
                and not privileged_required
            ):
                return "anonymous"
            SECURITY_EVENTS.labels(reason="unauthorized").inc()
            logger.warning(
                "AUDIT | AUTH | unauthorized | ip=%s ua=%s path=%s",
                client_ip,
                user_agent,
                request.url.path,
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        user_roles_str = {r.value for r in user.roles}
        if "service" in user_roles_str:
            user_roles_str.add("job")

        matching_roles = user_roles_str.intersection(allowed_set)

        if not matching_roles:
            SECURITY_EVENTS.labels(reason="forbidden").inc()
            logger.warning(
                "AUDIT | AUTH | forbidden | user=%s roles=%s ip=%s path=%s",
                user.user_id,
                list(user_roles_str),
                client_ip,
                request.url.path,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Permission denied for this role.",
            )

        role = next(iter(matching_roles))

        # Tenant-scope the rate-limit key so buckets never collide across
        # tenants and per-tenant limiting/analytics can be layered on later.
        tenant = getattr(user, "tenant_id", None) or "default"

        if bearer:
            identifier = f"{tenant}:{role}:jwt:{user.user_id}"
        elif api_key:
            api_key_hash = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
            identifier = f"{tenant}:{role}:api:{api_key_hash}"
        else:
            client_host = request.client.host if request.client else "unknown"
            identifier = f"{tenant}:{role}:{client_host}"

        await self.rate_limiter.check(
            identifier, limit_per_minute, self.config.rate_limit_window_seconds
        )

        # Attach the authenticated user to request.state so that any code
        # reading request.state.user gets the full AuthUser object.
        request.state.user = user

        # Override the tenant context that TenantMiddleware pre-set to
        # "default" before dependencies ran.  enforce_auth runs inside
        # call_next, so the middleware's initial set("default") has already
        # happened.  Overriding here ensures the route handler sees the
        # correct tenant_id.  The middleware's finally-block reset(token)
        # will correctly restore the context to its pre-request state
        # regardless of this intermediate set.
        _set_tenant_ctx(user.tenant_id)
        # Bind the user id too (identity-derived), so plugins declaring
        # ``tenancy: personal`` can resolve a per-user tenant via
        # core.context.resolve_plugin_tenant even on a shared deployment.
        _set_user_ctx(user.user_id)

        logger.debug(
            "AUDIT | AUTH | ok | user=%s role=%s ip=%s path=%s",
            user.user_id,
            role,
            client_ip,
            request.url.path,
        )

        return role

    # Admin lockout constants
    _LOCKOUT_MAX_FAILURES: int = 5
    _LOCKOUT_WINDOW_SECONDS: int = 60  # failures window
    _LOCKOUT_DURATION_SECONDS: int = 900  # 15 min lock

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
            except Exception:
                pass

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

    def verify_admin_password(self, candidate: str) -> bool:
        """
        Verify admin password.
        Uses PBKDF2-SHA256 if ADMIN_PASS_HASHED is set, otherwise plaintext.
        """
        if self.config.admin_pass_hashed:
            return self._verify_pbkdf2_sha256(
                self.config.admin_pass_hashed.get_secret_value(), candidate
            )
        if self.config.admin_pass:
            return secrets.compare_digest(
                candidate, self.config.admin_pass.get_secret_value()
            )
        return False

    def _verify_pbkdf2_sha256(self, encoded: str, candidate: str) -> bool:
        """Verify PBKDF2-SHA256 hash."""
        try:
            scheme, iter_str, salt_hex, hash_hex = encoded.split("$", 3)
            if scheme != "pbkdf2_sha256":
                return False
            iterations = int(iter_str)
            salt = bytes.fromhex(salt_hex)
            digest = bytes.fromhex(hash_hex)
        except Exception:
            return False

        derived = hashlib.pbkdf2_hmac(
            "sha256", candidate.encode("utf-8"), salt, iterations
        )
        return secrets.compare_digest(derived, digest)


_security_manager: SecurityManager | None = None


def get_security_manager() -> SecurityManager:
    """Get or create the global security manager instance."""
    global _security_manager
    if _security_manager is None:
        _security_manager = SecurityManager(get_security_config())
    return _security_manager


class _RateLimiterProxy:
    """Lazily resolve the shared rate limiter when accessed."""

    def __getattr__(self, name: str) -> Any:
        return getattr(get_security_manager().rate_limiter, name)


rate_limiter = _RateLimiterProxy()


async def require_user(request: Request) -> str:
    """Dependency for user routes."""
    manager = get_security_manager()
    return await manager.enforce_auth(
        request,
        allowed_roles={"user", "admin", "job"},
        limit_per_minute=manager.config.rate_limit_user_per_minute,
    )


async def require_admin(request: Request) -> str:
    """Dependency for admin routes."""
    manager = get_security_manager()
    return await manager.enforce_auth(
        request,
        allowed_roles={"admin"},
        limit_per_minute=manager.config.rate_limit_admin_per_minute,
    )


async def require_admin_or_job(request: Request) -> str:
    """Dependency for indexing/automation routes."""
    manager = get_security_manager()
    limit = (
        manager.config.rate_limit_job_per_minute
        or manager.config.rate_limit_admin_per_minute
    )
    return await manager.enforce_auth(
        request, allowed_roles={"admin", "job"}, limit_per_minute=limit
    )


def verify_admin_password(candidate: str) -> bool:
    """Verify admin password using global manager."""
    return get_security_manager().verify_admin_password(candidate)


async def verify_admin_password_async(candidate: str) -> bool:
    """Verify admin password without blocking the event loop.

    PBKDF2-SHA256 runs 100k+ iterations of CPU-bound hashing; on the async
    request path that stalls every in-flight request for its duration, so
    the derivation is offloaded to a worker thread.
    """
    import asyncio

    return await asyncio.to_thread(
        get_security_manager().verify_admin_password, candidate
    )


async def check_admin_lockout(identifier: str) -> None:
    """Check admin lockout using global manager (key on client IP)."""
    await get_security_manager().check_admin_lockout(identifier)


async def record_admin_failure(identifier: str) -> None:
    """Record a failed admin login attempt using global manager (key on IP)."""
    await get_security_manager().record_admin_failure(identifier)


async def clear_admin_failures(identifier: str) -> None:
    """Clear admin failure counter using global manager (key on IP)."""
    await get_security_manager().clear_admin_failures(identifier)
