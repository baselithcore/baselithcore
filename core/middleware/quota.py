"""Per-identity + per-tenant usage-quota enforcement (opt-in, pure ASGI).

A complete no-op unless ``QuotaConfig.enabled`` — so registering it is a zero
behaviour change until an operator turns quotas on. When enabled, an
authenticated request consumes one unit from BOTH the caller's identity budget
and their tenant's aggregate budget; if either window is exhausted the request
is rejected with ``429`` before reaching the route.

Self-authenticating via the bearer token (like ``PluginAccessMiddleware``), so
it does not depend on where it sits in the stack or on a route dependency
having run. Unauthenticated requests are not quota-scoped and pass through.

Identity and tenant windows are enforced through one batched
check-then-consume (``check_and_consume_pair``): all four counters are read
in a single round trip and consumed only if every window has room, so a
rejected request burns no budget on either subject.
"""

from __future__ import annotations

import json

from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from core.auth import AuthManager, AuthUser, get_auth_manager
from core.config.quotas import get_quota_config
from core.observability.logging import get_logger
from core.quotas.manager import QuotaExceededError, get_quota_manager

logger = get_logger(__name__)


class QuotaMiddleware:
    """Reject requests that exceed the caller's identity or tenant quota."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    @staticmethod
    def _auth_manager() -> AuthManager | None:
        """The app-configured AuthManager, or the core global as a fallback."""
        try:
            from core.di.container import ServiceRegistry

            return ServiceRegistry.get(AuthManager)
        except Exception:
            try:
                return get_auth_manager()
            except Exception:
                return None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not get_quota_config().enabled:
            await self.app(scope, receive, send)
            return

        header = Request(scope).headers.get("authorization")
        manager = self._auth_manager() if header else None
        user: AuthUser | None = None
        if header and manager is not None:
            try:
                user = await manager.authenticate(header)
            except Exception as exc:
                logger.debug("quota auth skipped: %s", exc)
                user = None
            else:
                # Memoize for the route's auth dependency so the same token is
                # not verified twice per request. The dependency only trusts
                # the memo when the header AND the manager instance match.
                scope.setdefault("state", {})["_auth_memo"] = (
                    header,
                    id(manager),
                    user,
                )

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
