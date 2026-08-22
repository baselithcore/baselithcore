"""
Security Middleware

Provides authentication, authorization, rate limiting, and security headers.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterable
from typing import Any

from fastapi import HTTPException, Request, status

from core.auth.types import AuthError
from core.config import SecurityConfig, get_security_config
from core.context import set_tenant_context as _set_tenant_ctx
from core.context import set_user_context as _set_user_ctx
from core.middleware._admin_credentials import (
    VerifiedCredentialCache,
    verify_pbkdf2_sha256,
)
from core.middleware._admin_lockout import AdminLockoutMixin
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
from core.observability.audit import AuditEventType, get_audit_logger
from core.observability.logging import get_logger
from core.security.digest import credential_digest

logger = get_logger(__name__)


class SecurityManager(AdminLockoutMixin):
    """
    Manages Authentication, Authorization and Rate Limiting logic.

    Admin Basic-auth lockout (``check_admin_lockout`` / ``record_admin_failure``
    / ``clear_admin_failures``) is provided by :class:`AdminLockoutMixin`.
    """

    def __init__(self, config: SecurityConfig) -> None:
        self.config = config
        self.rate_limiter = RateLimiter()
        # In-memory fallback for admin lockout when Redis is unavailable.
        # Maps username -> (failure_count, lock_until_timestamp).
        self._lockout_fallback: dict[str, tuple[int, float]] = {}
        # Cache of *successfully* verified admin credentials so repeated
        # identical Basic-auth requests (Prometheus scrapes, dashboard polls)
        # skip the 100k+ iteration PBKDF2 derivation on every call.
        self._cred_cache = VerifiedCredentialCache()

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
                # Throttle credential brute-force / stuffing per source IP.
                # authenticate() rejects bad credentials *before* any per-role
                # limiter below runs, so without this an attacker gets an
                # unmetered stream of 401s on every require_* route. Count only
                # failures (successful auth never reaches here): a dedicated
                # per-IP window trips to 429 once the budget is exhausted. Runs
                # before the 401 so the 429 (with Retry-After) wins.
                failure_ip = request.client.host if request.client else "unknown"
                await self.rate_limiter.check(
                    f"authfail:{failure_ip}",
                    self.config.auth_failure_limit_per_minute,
                    self.config.rate_limit_window_seconds,
                )
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
                # Anonymous traffic is still rate-limited (per client IP):
                # an auth-disabled deployment must not hand out unmetered
                # LLM invocation to anyone who can reach the port.
                await self.rate_limiter.check(
                    f"default:anonymous:{client_ip}",
                    limit_per_minute,
                    self.config.rate_limit_window_seconds,
                )
                return "anonymous"
            SECURITY_EVENTS.labels(reason="unauthorized").inc()
            logger.warning(
                "AUDIT | AUTH | unauthorized | ip=%s ua=%s path=%s",
                client_ip,
                user_agent,
                request.url.path,
            )
            await get_audit_logger().log(
                AuditEventType.AUTH_FAILED,
                resource=request.url.path,
                action="unauthorized",
                success=False,
                ip_address=client_ip,
                details={"user_agent": user_agent},
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        user_roles_str = {r.value for r in user.roles}
        if "service" in user_roles_str:
            user_roles_str.add("job")

        # An RFC 8693 agent-delegated token keeps the user's roles for audit,
        # but authority-wise it is a capability identity: adjudicate it like a
        # SCOPED key. Otherwise a narrowly-scoped delegation for an admin user
        # would pass every control-plane gate on the user's role alone.
        # (`is True` so a MagicMock user in tests doesn't accidentally match.)
        if getattr(user, "is_agent_delegated", False) is True:
            user_roles_str = {"scoped"}

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
            await get_audit_logger().log(
                AuditEventType.AUTH_FAILED,
                user_id=user.user_id,
                resource=request.url.path,
                action="forbidden",
                success=False,
                ip_address=client_ip,
                details={"roles": sorted(user_roles_str)},
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
            api_key_hash = credential_digest(api_key.encode())
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

    def verify_admin_password(self, candidate: str) -> bool:
        """
        Verify admin password.
        Uses PBKDF2-SHA256 if ADMIN_PASS_HASHED is set, otherwise plaintext.

        Successful PBKDF2 verifications are memoized for a short TTL (see
        :class:`VerifiedCredentialCache`) so a burst of identical requests
        (metrics scrapes, admin polls) does not re-run the KDF each time. Only
        successes are cached, so this can never turn a wrong password into an
        accepted one.
        """
        if self.config.admin_pass_hashed:
            if self._cred_cache.is_fresh(candidate):
                return True
            ok = verify_pbkdf2_sha256(
                self.config.admin_pass_hashed.get_secret_value(), candidate
            )
            if ok:
                self._cred_cache.remember(candidate)
            return ok
        if self.config.admin_pass:
            return secrets.compare_digest(
                candidate, self.config.admin_pass.get_secret_value()
            )
        return False


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
    """Dependency for user routes.

    ``scoped`` identities (least-privilege API keys) are admitted at the coarse
    gate so capability-enforced routes (webhooks/compliance/privacy, which call
    ``enforce_scopes``) can adjudicate them by their explicit scopes. Because the
    SCOPED role carries no role-derived scopes, such a key is authorized only
    where its own scopes cover the requirement. Control-plane dependencies
    (``require_admin`` / ``require_admin_or_job``) deliberately omit ``scoped``.
    """
    manager = get_security_manager()
    return await manager.enforce_auth(
        request,
        allowed_roles={"user", "admin", "job", "scoped"},
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
