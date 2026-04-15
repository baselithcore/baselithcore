"""
Security Middleware

Provides authentication, authorization, rate limiting, and security headers.
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import time
from typing import Any, Iterable, Optional

from fastapi import HTTPException, Request, status
from prometheus_client import Counter
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from core.config import get_security_config, SecurityConfig
from core.cache.redis_cache import create_redis_client
from core.config.cache import get_redis_cache_config
from core.observability.logging import get_logger

logger = get_logger(__name__)


# Global Metrics
SECURITY_EVENTS = Counter(
    "security_events_total",
    "Security events (auth/rate-limit)",
    ["reason"],
)


class RateLimiter:
    """
    Distributed rate limiter by role/key/IP, using Redis.
    """

    def __init__(self) -> None:
        cache_config = get_redis_cache_config()
        self._prefix = cache_config.cache_prefix + ":ratelimit:"
        self._redis = None
        self._fallback: dict[str, tuple[int, float]] = {}
        self._fallback_lock = asyncio.Lock()
        try:
            self._redis = create_redis_client(cache_config.url)
        except Exception as e:
            logger.warning(
                "Redis rate limiter unavailable during initialization (%s), using in-memory fallback",
                type(e).__name__,
            )

    async def close(self) -> None:
        """Close the underlying Redis connection."""
        if self._redis is not None:
            await self._redis.close()

    async def _check_fallback(
        self, identifier: str, limit: int, window_seconds: int
    ) -> None:
        """Best-effort local fixed-window fallback when Redis is unavailable."""
        async with self._fallback_lock:
            now = time.time()
            count, window_start = self._fallback.get(identifier, (0, now))
            if now - window_start >= window_seconds:
                count = 0
                window_start = now

            count += 1
            self._fallback[identifier] = (count, window_start)

            # Prune expired entries to prevent unbounded memory growth.
            # Only run periodically (every ~100 checks) to avoid O(n) cost on each request.
            if len(self._fallback) > 1000:
                cutoff = now - window_seconds
                self._fallback = {
                    k: v for k, v in self._fallback.items() if v[1] > cutoff
                }

            if count > limit:
                SECURITY_EVENTS.labels(reason="rate_limited").inc()
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Rate limit exceeded, please try again shortly.",
                )

    async def check(
        self, identifier: str, limit: Optional[int], window_seconds: int
    ) -> None:
        """
        Check if identifier is within rate limit.

        Args:
            identifier: Unique identifier (role:key format)
            limit: Maximum requests per window
            window_seconds: Time window in seconds

        Raises:
            HTTPException: 429 if rate limit exceeded
        """
        if limit is None or limit <= 0:
            return

        key = f"{self._prefix}{identifier}"

        if self._redis is None:
            await self._check_fallback(identifier, limit, window_seconds)
            return

        try:
            # SET NX EX atomically initialises the counter with a TTL on the first
            # request, so the expiry is always set before any INCR succeeds.
            # This eliminates the TOCTOU race in the original INCR-then-EXPIRE
            # pattern where concurrent callers could prevent the TTL from being set.
            await self._redis.set(key, 0, nx=True, ex=window_seconds)
            current = await self._redis.incr(key)
        except Exception as e:
            logger.warning(
                "Redis rate limit check failed (%s), using in-memory fallback",
                type(e).__name__,
            )
            await self._check_fallback(identifier, limit, window_seconds)
            return

        if current > limit:
            SECURITY_EVENTS.labels(reason="rate_limited").inc()
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded, please try again shortly.",
            )


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

    def _extract_credentials(
        self, request: Request
    ) -> tuple[Optional[str], Optional[str]]:
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
        limit_per_minute: Optional[int],
    ) -> str:
        """Enforce authentication and rate limiting."""
        from core.auth.manager import get_auth_manager
        from core.auth.types import AuthError

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

        try:
            user = await auth_manager.authenticate(auth_header)
        except AuthError as e:
            SECURITY_EVENTS.labels(reason="unauthorized").inc()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(e),
                headers={"WWW-Authenticate": "Bearer"},
            )

        client_ip = request.client.host if request.client else "unknown"
        user_agent = request.headers.get("user-agent", "unknown")[:200]

        if not user.is_authenticated:
            if not self.config.auth_required and not has_keys_for_allowed:
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

        if bearer:
            identifier = f"{role}:jwt:{user.user_id}"
        elif api_key:
            api_key_hash = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
            identifier = f"{role}:api:{api_key_hash}"
        else:
            client_host = request.client.host if request.client else "unknown"
            identifier = f"{role}:{client_host}"

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
        from core.context import set_tenant_context as _set_tenant_ctx

        _set_tenant_ctx(user.tenant_id)

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

    async def check_admin_lockout(self, username: str) -> None:
        """
        Raise HTTP 429 if the admin account is currently locked out.

        Args:
            username: Admin username attempting login
        """
        key = f"{self.rate_limiter._prefix}admin_lockout:{username}"
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
        count, lock_until = self._lockout_fallback.get(username, (0, 0.0))
        if count >= self._LOCKOUT_MAX_FAILURES and time.time() < lock_until:
            SECURITY_EVENTS.labels(reason="admin_lockout").inc()
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Account temporarily locked. Try again later.",
            )

    async def record_admin_failure(self, username: str) -> None:
        """
        Increment the failure counter for an admin login attempt.

        Args:
            username: Admin username that failed
        """
        key = f"{self.rate_limiter._prefix}admin_lockout:{username}"
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
        count, lock_until = self._lockout_fallback.get(username, (0, 0.0))
        count += 1
        lock_until = (
            time.time() + self._LOCKOUT_DURATION_SECONDS
            if count >= self._LOCKOUT_MAX_FAILURES
            else lock_until
        )
        self._lockout_fallback[username] = (count, lock_until)
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

    async def clear_admin_failures(self, username: str) -> None:
        """Clear failure counter after a successful admin login."""
        key = f"{self.rate_limiter._prefix}admin_lockout:{username}"
        self._lockout_fallback.pop(username, None)
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


_security_manager: Optional[SecurityManager] = None


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


async def check_admin_lockout(username: str) -> None:
    """Check admin lockout using global manager."""
    await get_security_manager().check_admin_lockout(username)


async def record_admin_failure(username: str) -> None:
    """Record a failed admin login attempt using global manager."""
    await get_security_manager().record_admin_failure(username)


async def clear_admin_failures(username: str) -> None:
    """Clear admin failure counter using global manager."""
    await get_security_manager().clear_admin_failures(username)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Adds security headers to HTTP responses.
    """

    def __init__(self, app: ASGIApp, config: Optional[SecurityConfig] = None):
        super().__init__(app)
        self.config = config if config is not None else get_security_config()

    def _default_csp(self) -> str:
        """Return a strict default CSP for runtime responses.

        'unsafe-inline' and 'unsafe-eval' are intentionally omitted from
        script-src to prevent XSS. If inline scripts or eval are required,
        use nonces or hashes and override this via SecurityConfig.csp_policy.
        """
        return (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https:; "
            "font-src 'self' data:; "
            "connect-src 'self' ws: wss:; "
            "frame-ancestors 'none';"
        )

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)

    async def dispatch(self, request: Request, call_next):
        """Add security headers to response.

        The four baseline headers are always emitted regardless of
        ``security_headers_enabled``.  CSP and HSTS are opt-in because they
        require operator-specific configuration (domain, TLS termination).
        """
        response = await call_next(request)

        headers = response.headers
        # Always-on baseline headers — cannot be disabled via config
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", self.config.frame_options)
        headers.setdefault("Referrer-Policy", "same-origin")
        headers.setdefault("X-XSS-Protection", "1; mode=block")

        # Opt-in headers controlled by security_headers_enabled
        if self.config.security_headers_enabled:
            headers.setdefault(
                "Content-Security-Policy",
                self.config.content_security_policy or self._default_csp(),
            )
            if self.config.permissions_policy:
                headers.setdefault("Permissions-Policy", self.config.permissions_policy)
            if self.config.enable_hsts:
                hsts = f"max-age={self.config.hsts_max_age}; includeSubDomains"
                headers.setdefault("Strict-Transport-Security", hsts)

        return response
