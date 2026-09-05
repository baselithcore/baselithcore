"""Resolve the caller's tenant for baselithbot endpoints — fail CLOSED.

Baselithbot authenticates write endpoints with its own static-token
``DashboardAuth`` (orthogonal to central auth), so the tenant context var is
never bound on its routes. This resolves the tenant from the request's bearer
JWT via the central auth manager.

Security: tenant isolation must fail closed. We do NOT collapse "auth failed /
absent" into a real tenant id (the old code returned ``"default"`` — itself a
valid tenant — letting an unauthenticated caller read the default tenant's
data). Instead:

* No central auth subsystem configured  → ``"default"`` (single-tenant
  deployment; multi-tenancy isn't in play, so there is nothing to leak).
* Central auth IS active but the caller has no valid identity (missing bearer,
  bad/expired token, transient verify error, anonymous) → ``None``.

Callers MUST treat ``None`` as "no tenant" and refuse to read or write tenant
data (HTTP 401 on writes, empty/404 on reads) — never fall back to a real
tenant id.
"""

from __future__ import annotations

from typing import Any


def _central_auth_manager() -> Any | None:
    """The app-registered central AuthManager, or ``None`` if auth is absent.

    Resolved ONLY from the DI registry (where the auth plugin registers it) — a
    successful lookup is the signal that central auth (hence multi-tenancy) is
    active. The core global fallback is intentionally NOT used here: it exists
    even without the auth plugin and would falsely signal an active auth system.
    """
    try:
        from core.auth import AuthManager
        from core.di.container import ServiceRegistry

        return ServiceRegistry.get(AuthManager)
    except Exception:  # noqa: BLE001 — not registered → no central auth
        return None


async def tenant_from_request(request: Any) -> str | None:
    """Tenant for the request, or ``None`` when it cannot be authenticated.

    Returns ``"default"`` only when no central auth subsystem exists. Otherwise
    a real tenant id on success, or ``None`` (fail closed) on any failure.
    """
    manager = _central_auth_manager()
    if manager is None:
        # No central auth → single-tenant deployment; "default" is the only tenant.
        return "default"

    try:
        header = request.headers.get("authorization")
    except Exception:  # noqa: BLE001
        return None
    if not header:
        return None  # central auth active but no credential → fail closed

    try:
        user = await manager.authenticate(header)
    except Exception:  # noqa: BLE001 — invalid/expired/transient → never leak
        return None
    if user is None or not user.is_authenticated:
        return None
    return user.tenant_id
