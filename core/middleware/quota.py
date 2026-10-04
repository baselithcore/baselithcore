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

Identity and tenant windows are enforced through one atomic
check-then-consume (``check_and_consume_pair``): all four counters are checked
and consumed only if every window has room, so a rejected request burns no
budget on either subject.

The unit is taken *before* the request reaches the route (the check must
precede the work), and given back when the request turns out to have done
none: a response whose status is in :data:`REFUNDED_STATUSES` — an inner
guard's ``401``/``403``/``429``, an unmatched ``404``/``405``, the idempotency
layer's ``409`` (a duplicate still in flight), a ``503`` from a plugin that
failed to activate — triggers ``refund_pair``, and so does a ``200`` the
idempotency layer answered from its store (``idempotency-replayed``). The
status is read from ``http.response.start``, so streaming responses are
handled the same way and nothing is buffered.

Two exceptions to "status decides". An inner layer that *did* spend
resources before refusing — ``CostControlMiddleware`` answers ``429`` only
after the handler ran through its token budget — marks the scope with
:data:`WORK_DONE_SCOPE_KEY`, and that unit stays spent. And a quota store
that cannot answer is a ``503`` with ``Retry-After`` (fail closed, but
honestly): the alternative, letting the exception out, was a ``500`` built
outside the observability layers.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from core.auth import AuthManager, AuthUser
from core.config.quotas import get_quota_config
from core.middleware._auth_memo import EXEMPT_PATHS, auth_manager, resolve_user
from core.middleware.cost_control import WORK_DONE_SCOPE_KEY
from core.observability.logging import get_logger
from core.quotas.manager import (
    QuotaExceededError,
    QuotaManager,
    get_quota_manager,
)
from core.quotas.windows import QuotaWindow

logger = get_logger(__name__)

#: Statuses that mean "admitted, but no work was done": the quota unit spent on
#: admission is refunded. 5xx other than 503 are *not* refunded — the handler
#: may well have done (and billed) the work before failing.
REFUNDED_STATUSES: frozenset[int] = frozenset({401, 403, 404, 405, 409, 429, 503})

#: ``Retry-After`` for a quota store that could not answer.
_STORE_DOWN_RETRY_AFTER = 5


def _seconds_to_window_end(window: QuotaWindow, when: datetime) -> int:
    """Seconds until *window* rolls over after *when* (UTC calendar)."""
    if window is QuotaWindow.MONTHLY:
        first = when.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        rollover = (first + timedelta(days=32)).replace(day=1)
    else:
        rollover = when.replace(hour=0, minute=0, second=0, microsecond=0)
        rollover += timedelta(days=1)
    return max(1, int((rollover - when).total_seconds()))


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
        # One timestamp for consume and refund, so a refund near midnight hits
        # the same period keys the consumption did.
        when = datetime.now(UTC)
        try:
            await quotas.check_and_consume_pair(user.user_id, user.tenant_id, now=when)
        except QuotaExceededError as exc:
            await self._too_many(send, exc, when)
            return
        except Exception as exc:
            logger.error("quota_store_unavailable: %s", type(exc).__name__)
            await self._unavailable(send)
            return

        status: list[int] = []
        replayed: list[bool] = []

        async def send_watching_status(message: Message) -> None:
            if message["type"] == "http.response.start":
                status.append(int(message["status"]))
                replayed.append(
                    any(
                        k.lower() == b"idempotency-replayed"
                        for k, _ in message.get("headers") or []
                    )
                )
            await send(message)

        try:
            await self.app(scope, receive, send_watching_status)
        finally:
            if status and not scope.get(WORK_DONE_SCOPE_KEY):
                if status[0] in REFUNDED_STATUSES or replayed[0]:
                    await self._refund(quotas, user, when)

    @staticmethod
    async def _refund(quotas: QuotaManager, user: AuthUser, when: datetime) -> None:
        """Best-effort give-back; a failure leaves the unit spent."""
        try:
            await quotas.refund_pair(user.user_id, user.tenant_id, now=when)
        except Exception as exc:
            logger.warning("quota_refund_failed: %s", type(exc).__name__)

    @staticmethod
    async def _too_many(send: Send, exc: QuotaExceededError, when: datetime) -> None:
        body = json.dumps(
            {
                "detail": f"Quota exceeded for the {exc.window.value} window",
                "limit": exc.limit,
                "used": exc.used,
            }
        ).encode("utf-8")
        # The window is a calendar period: nothing frees up before it rolls
        # over, so that is the honest Retry-After (a flat 60 s told a daily
        # or monthly caller to retry 1 440 times for nothing).
        retry_after = _seconds_to_window_end(exc.window, when)
        await send(
            {
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"retry-after", str(retry_after).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    async def _unavailable(send: Send) -> None:
        body = json.dumps({"detail": "Quota service unavailable"}).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 503,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"retry-after", str(_STORE_DOWN_RETRY_AFTER).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
