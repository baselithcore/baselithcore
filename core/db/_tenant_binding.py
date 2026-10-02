"""Binding the request tenant to a pooled PostgreSQL session (RLS opt-in).

Split out of :mod:`core.db.connection`, which re-exports these three helpers
under their old names; ``core.db.connection.DB_RLS_ENABLED`` stays the one
switch the helpers consult, read at call time, so a test that patches it on
the connection module still steers them.
"""

from __future__ import annotations

from psycopg import AsyncConnection, Connection


def _rls_enabled() -> bool:
    from core.db import connection

    return bool(connection.DB_RLS_ENABLED)


def _tenant_for_session() -> str:
    """Resolve through :mod:`core.db.connection`, the patchable seam."""
    from core.db import connection

    return connection._current_tenant_for_session()


def _current_tenant_for_session() -> str:
    """Resolve the tenant to bind to the DB session.

    Outside a request (background task, script) the tenant contextvar may be
    unset. What that should mean depends on whether row-level security is on:

    * **RLS off** — nothing downstream reads ``app.tenant_id`` for access
      control, so an unbound caller degrades to ``"default"`` exactly as
      before and its work is not broken by a missing context.
    * **RLS on** — ``"default"`` is the worst possible answer. Every RLS
      policy would then match the ``default`` tenant's rows, so an unbound
      background job reads and writes another tenant's data while the database
      reports that isolation is enforced. Such a caller must say what it is:
      wrap it in :func:`system_tenant_scope`.

    Returns:
        The tenant id to bind to ``app.tenant_id``.

    Raises:
        core.context.TenantContextError: RLS is enabled and no tenant is bound.
    """
    from core.context import (
        TenantContextError,
        get_current_tenant_id,
        tenant_is_bound,
    )

    # Ask whether a tenant is *bound*, not what it resolves to:
    # ``get_current_tenant_id`` only distinguishes bound from unbound when
    # ``strict_tenant_isolation`` is on, and RLS must fail closed regardless of
    # that unrelated switch.
    if _rls_enabled() and not tenant_is_bound():
        raise TenantContextError(
            "Row-level security is enabled (DB_RLS_ENABLED=true) but no tenant "
            "is bound to this context, so app.tenant_id cannot be set. Bind the "
            "request tenant upstream, or wrap out-of-request work in "
            "core.db.connection.system_tenant_scope()."
        )

    try:
        return get_current_tenant_id()
    except TenantContextError:
        return "default"


def _sync_apply_tenant(connection: Connection[object]) -> None:
    """Bind ``app.tenant_id`` to a sync connection for RLS (opt-in).

    A pooled connection serves different tenants across requests, so the GUC
    must always reflect the current one — but re-issuing ``set_config`` when
    the bound tenant is *unchanged* costs one full round-trip per checkout
    for nothing. The last-applied tenant is memoized on the connection
    (``set_config(..., false)`` is session-scoped, so it survives checkouts on
    the same physical connection) and only a tenant change re-applies it.
    """
    tenant = _tenant_for_session()
    if getattr(connection, "_app_tenant_id", None) == tenant:
        return
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT set_config('app.tenant_id', %s, false)",
            (tenant,),
        )
    # Dynamic marker attribute — psycopg's Connection doesn't declare it.
    setattr(connection, "_app_tenant_id", tenant)  # noqa: B010


async def _async_apply_tenant(connection: AsyncConnection[object]) -> None:
    """Async counterpart of :func:`_sync_apply_tenant`."""
    tenant = _tenant_for_session()
    if getattr(connection, "_app_tenant_id", None) == tenant:
        return
    async with connection.cursor() as cursor:
        await cursor.execute(
            "SELECT set_config('app.tenant_id', %s, false)",
            (tenant,),
        )
    # Dynamic marker attribute — psycopg's AsyncConnection doesn't declare it.
    setattr(connection, "_app_tenant_id", tenant)  # noqa: B010


__all__ = ["_async_apply_tenant", "_current_tenant_for_session", "_sync_apply_tenant"]
