"""Per-identity + per-tenant usage-quota enforcement (opt-in, pure ASGI).

A complete no-op unless ``QuotaConfig.enabled`` — so registering it is a zero
behaviour change until an operator turns quotas on. When enabled, an
authenticated request consumes one unit from BOTH the caller's identity budget
and their tenant's aggregate budget; if either window is exhausted the request
is rejected with ``429`` before reaching the route.

Self-authenticating via the bearer token (like ``PluginAccessMiddleware``), so
it does not depend on where it sits in the stack or on a route dependency
having run. Unauthenticated requests are not quota-scoped and pass through.
Verification goes through the shared per-request memo in
:mod:`core.middleware._auth_memo`, so the tenant middleware and the route's auth
dependency reuse this one result instead of re-verifying the same token.

Identity and tenant windows are enforced through one batched
check-then-consume (``check_and_consume_pair``): all four counters are read
in a single round trip and consumed only if every window has room, so a
rejected request burns no budget on either subject.
"""

from __future__ import annotations

import json

from starlette.types import ASGIApp, Receive, Scope, Send

from core.auth import AuthManager, AuthUser
from core.config.quotas import get_quota_config
from core.middleware._auth_memo import EXEMPT_PATHS, auth_manager, resolve_user
from core.observability.logging import get_logger
from core.quotas.manager import QuotaExceededError, get_quota_manager

logger = get_logger(__name__)


class QuotaMiddleware:
    """Reject requests that exceed the caller's identity or tenant quota."""

    # Never quota-metered: liveness/readiness probes, the interactive docs
    # bundle, and metrics scrapes. Without this allowlist a full JWT/API-key
    # verification ran on every /health poll and Prometheus scrape. Shared with
    # the other identity-resolving layers; kept as a class attribute because it
    # is part of this middleware's published surface.
    _EXEMPT_PATHS = EXEMPT_PATHS

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    @staticmethod
    def _auth_manager() -> AuthManager | None:
        """The app-configured AuthManager, or the core global as a fallback."""
        return auth_manager()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not get_quota_config().enabled:
            await self.app(scope, receive, send)
            return
        if scope.get("path", "") in self._EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return

        # One verification per request, memoised on the scope for the tenant
        # middleware and the route's auth dependency. Handles the API-key case
        # too: a caller sending only ``X-API-Key`` is authenticated by the
        # dependency via a synthesized ``ApiKey <key>`` header, and quota that
        # read ``Authorization`` alone would never scope them — an unmetered
        # bypass of QUOTAS_ENABLED.
        user: AuthUser | None = await resolve_user(scope)

        # Only authenticated callers are quota-scoped; anyone else passes through.
        if user is None or not user.is_authenticated:
            await self.app(scope, receive, send)
            return

        quotas = get_quota_manager()
        try:
            await quotas.check_and_consume_pair(user.user_id, user.tenant_id)
        except QuotaExceededError as exc:
            await self._too_many(send, exc)
            return

        await self.app(scope, receive, send)

    @staticmethod
    async def _too_many(send: Send, exc: QuotaExceededError) -> None:
        body = json.dumps(
            {
                "detail": f"Quota exceeded for the {exc.window.value} window",
                "limit": exc.limit,
                "used": exc.used,
            }
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"retry-after", b"60"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
