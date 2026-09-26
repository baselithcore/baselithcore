"""Per-request credential verification, resolved once and shared (pure helper).

Three layers need the caller's identity *before* the route's auth dependency
runs: :class:`~core.middleware.quota.QuotaMiddleware` (to pick the budget
subject), :class:`~core.middleware.tenant.TenantMiddleware` (to bind the tenant
contextvar every inner layer and log line reads) and the dependency itself.

Verifying the same token three times per request is waste; *not* verifying it is
worse. ``TenantMiddleware`` used to read ``scope['state']['user']``, which only
the route dependency writes — and the dependency runs after every middleware —
so the tenant contextvar was ``"default"`` for every authenticated request and
every inner layer inherited that.

This module owns the single verification and the memo that carries it. The memo
lives on the ASGI ``scope['state']`` as ``(effective_header, id(manager), user)``
and is trusted only when both the header *and* the manager instance match, so a
different credential — or an ``AuthManager`` re-registered mid-process — always
re-verifies. ``core.middleware.security`` reads the same tuple through
``request.state._auth_memo``.

Nothing here ever raises: an unverifiable credential resolves to ``None`` and
the caller decides what that means (quota: unmetered pass-through; tenant: the
``default`` tenant; the dependency: a real 401).
"""

from __future__ import annotations

from starlette.datastructures import Headers
from starlette.types import Scope

from core.auth import AuthManager, AuthUser, get_auth_manager
from core.observability.logging import get_logger

logger = get_logger(__name__)

#: Key under ``scope['state']`` holding the memo tuple.
MEMO_KEY = "_auth_memo"

#: Paths that must never pay for a credential verification: liveness/readiness
#: probes, the interactive docs bundle and metrics scrapes. Without this a full
#: JWT/API-key verification ran on every ``/health`` poll and Prometheus scrape.
EXEMPT_PATHS: frozenset[str] = frozenset(
    {
        "/health",
        "/health/ready",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/metrics",
        # The same probes and scrape are also mounted under the versioned
        # prefix (``core.api.factory`` includes status/metrics at ``/v1``).
        "/v1/health",
        "/v1/health/ready",
        "/v1/metrics",
    }
)

#: Credential schemes the ``AuthManager`` can verify. Anything else — ``Basic``
#: on ``/admin`` and ``/metrics`` above all, which the route verifies itself —
#: used to go through ``AuthManager.authenticate`` on every call just to be
#: refused, logging an "unsupported scheme" WARNING per request.
_VERIFIABLE_SCHEMES: frozenset[str] = frozenset({"bearer", "apikey"})


def effective_auth_header(scope: Scope) -> str | None:
    """Build the credential header the auth dependency will also see.

    Mirrors ``SecurityManager._extract_credentials``: a caller authenticating
    with an ``X-API-Key`` header (and no ``Authorization``) is authenticated by
    the route dependency, which synthesizes ``ApiKey <key>``. Building the same
    value here keeps the memo comparable — and stops an API-key caller from
    slipping past a middleware that only looked at ``Authorization``.

    Args:
        scope: The ASGI connection scope.

    Returns:
        The effective ``Authorization``-style header, or ``None`` when the
        request carries no credential at all.
    """
    headers = Headers(raw=list(scope.get("headers") or []))
    header = headers.get("authorization")
    if header:
        return str(header)
    api_key = headers.get("x-api-key")
    if api_key:
        return f"ApiKey {api_key.strip()}"
    return None


def auth_manager() -> AuthManager | None:
    """The app-configured ``AuthManager``, or the core global as a fallback.

    Probes the registry with ``has`` rather than letting ``get`` raise: the
    ``AuthManager`` is normally *not* registered, and the old
    ``get``-and-catch raised and swallowed a ``ServiceNotFoundError`` on every
    call — twice per request (quota and tenant layers). Nothing is cached
    here on purpose: both sources are already process singletons, and reading
    them each time keeps a re-registration or a test reset effective at once.
    """
    from core.di.container import ServiceNotFoundError, ServiceRegistry

    if ServiceRegistry.has(AuthManager):
        try:
            return ServiceRegistry.get(AuthManager)
        except ServiceNotFoundError:
            # Cleared between has() and get(): fall back to the global.
            logger.debug("auth_manager_registry_race")
    try:
        return get_auth_manager()
    except Exception:
        logger.debug("auth_manager_unavailable", exc_info=True)
        return None


async def resolve_user(scope: Scope) -> AuthUser | None:
    """Resolve (and memoise) the caller's identity for this request.

    Safe to call from several middleware layers: the first call verifies the
    credential and stores the memo on ``scope['state']``; later calls — and the
    route's auth dependency — reuse it.

    Args:
        scope: The ASGI connection scope. Mutated to hold the memo.

    Returns:
        The authenticated user, or ``None`` for a non-HTTP scope, an exempt
        path, a credential-less request, or a credential that fails to verify.
    """
    if scope.get("type") != "http":
        return None
    if scope.get("path", "") in EXEMPT_PATHS:
        return None

    header = effective_auth_header(scope)
    if not header:
        return None
    if header.split(" ", 1)[0].lower() not in _VERIFIABLE_SCHEMES:
        # Basic (admin, metrics) or an unknown scheme: the AuthManager can only
        # refuse it, so don't pay for — or log — that refusal on every call.
        return None
    manager = auth_manager()
    if manager is None:
        return None

    state = scope.setdefault("state", {})
    if not isinstance(state, dict):
        state = None  # A non-dict state cannot carry the memo; verify anyway.

    if state is not None:
        memo = state.get(MEMO_KEY)
        if memo is not None and memo[0] == header and memo[1] == id(manager):
            cached = memo[2]
            return cached if isinstance(cached, AuthUser) else None

    try:
        user: AuthUser | None = await manager.authenticate(header)
    except Exception as exc:
        # Never fatal here: rejecting the credential is the dependency's job,
        # and it will produce the 401 (and the brute-force throttle) itself.
        logger.debug("auth_memo_verification_skipped: %s", exc)
        return None

    if state is not None:
        state[MEMO_KEY] = (header, id(manager), user)
    return user


__all__ = [
    "EXEMPT_PATHS",
    "MEMO_KEY",
    "auth_manager",
    "effective_auth_header",
    "resolve_user",
]
