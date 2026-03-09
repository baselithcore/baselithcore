"""
Security Middleware

Provides authentication, authorization, rate limiting, and security headers.
"""

from __future__ import annotations

import hashlib
import secrets
from typing import Iterable, Optional

from fastapi import HTTPException, Request, status
from prometheus_client import Counter
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from core.config import get_security_config, SecurityConfig
from core.cache.redis_cache import create_redis_client
from core.config.cache import get_redis_cache_config


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
        self._redis = create_redis_client(cache_config.url)
        self._prefix = cache_config.cache_prefix + ":ratelimit:"

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

        # Increment request count
        current = await self._redis.incr(key)

        # Set expiry on first request
        if current == 1:
            await self._redis.expire(key, window_seconds)

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

        if not user.is_authenticated:
            if not self.config.auth_required and not has_keys_for_allowed:
                return "anonymous"
            SECURITY_EVENTS.labels(reason="unauthorized").inc()
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
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Permission denied for this role.",
            )

        role = next(iter(matching_roles))

        if bearer:
            identifier = f"{role}:jwt:{user.user_id}"
        elif api_key:
            identifier = f"{role}:{api_key}"
        else:
            client_host = request.client.host if request.client else "unknown"
            identifier = f"{role}:{client_host}"

        await self.rate_limiter.check(
            identifier, limit_per_minute, self.config.rate_limit_window_seconds
        )
        return role

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


# Global instance
_security_config = get_security_config()
security_manager = SecurityManager(_security_config)
rate_limiter = security_manager.rate_limiter  # Backwards compatibility


async def require_user(request: Request) -> str:
    """Dependency for user routes."""
    return await security_manager.enforce_auth(
        request,
        allowed_roles={"user", "admin", "job"},
        limit_per_minute=security_manager.config.rate_limit_user_per_minute,
    )


async def require_admin(request: Request) -> str:
    """Dependency for admin routes."""
    return await security_manager.enforce_auth(
        request,
        allowed_roles={"admin"},
        limit_per_minute=security_manager.config.rate_limit_admin_per_minute,
    )


async def require_admin_or_job(request: Request) -> str:
    """Dependency for indexing/automation routes."""
    limit = (
        security_manager.config.rate_limit_job_per_minute
        or security_manager.config.rate_limit_admin_per_minute
    )
    return await security_manager.enforce_auth(
        request, allowed_roles={"admin", "job"}, limit_per_minute=limit
    )


def verify_admin_password(candidate: str) -> bool:
    """Verify admin password using global manager."""
    return security_manager.verify_admin_password(candidate)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Adds security headers to HTTP responses.
    """

    def __init__(self, app: ASGIApp, config: SecurityConfig = _security_config):
        super().__init__(app)
        self.config = config

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)

    async def dispatch(self, request: Request, call_next):
        """Add security headers to response."""
        response = await call_next(request)
        if not self.config.security_headers_enabled:
            return response

        headers = response.headers
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "same-origin")
        headers.setdefault("X-XSS-Protection", "1; mode=block")
        if self.config.content_security_policy:
            headers.setdefault(
                "Content-Security-Policy", self.config.content_security_policy
            )
        if self.config.enable_hsts:
            headers.setdefault(
                "Strict-Transport-Security", "max-age=63072000; includeSubDomains"
            )
        return response
