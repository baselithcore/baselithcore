"""Binding the request tenant to a pooled PostgreSQL session (RLS opt-in).

Split out of :mod:`core.db.connection`, which re-exports these three helpers
under their old names; ``core.db.connection.DB_RLS_ENABLED`` stays the one
switch the helpers consult, read at call time, so a test that patches it on
the connection module still steers them.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager

from psycopg import AsyncConnection, Connection

#: Transaction-local binding: the GUC ends with the transaction, so a
#: transaction-mode pooler can never hand it to another tenant's statement.
_SET_TENANT_LOCAL = "SELECT set_config('app.tenant_id', %s, true)"


def _rls_enabled() -> bool:
    from core.db import connection

    return bool(connection.DB_RLS_ENABLED)


def _transaction_scope() -> bool:
    from core.db import connection

    return connection.DB_RLS_TENANT_SCOPE == "transaction"


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


@contextmanager
def bind_tenant(connection: Connection[object]) -> Iterator[None]:
    """Bind the request tenant for one sync checkout, per ``DB_RLS_TENANT_SCOPE``.

    No-op with RLS off. ``session`` scope applies the memoized session GUC
    through :mod:`core.db.connection` (the patchable seam). ``transaction``
    scope wraps the whole checkout in ``connection.transaction()`` and binds
    with ``set_config(..., true)``: the work commits on a clean exit, rolls
    back on an exception, and a caller's own ``transaction()`` becomes a
    savepoint. The session memo is neither read nor written there.
    """
    from core.db import connection as seam

    if not _rls_enabled():
        yield
        return
    if not _transaction_scope():
        seam._sync_apply_tenant(connection)
        yield
        return
    tenant = _tenant_for_session()
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute(_SET_TENANT_LOCAL, (tenant,))
        yield


@asynccontextmanager
async def bind_tenant_async(connection: AsyncConnection[object]) -> AsyncIterator[None]:
    """Async counterpart of :func:`bind_tenant`."""
    from core.db import connection as seam

    if not _rls_enabled():
        yield
        return
    if not _transaction_scope():
        await seam._async_apply_tenant(connection)
        yield
        return
    tenant = _tenant_for_session()
    async with connection.transaction():
        async with connection.cursor() as cursor:
            await cursor.execute(_SET_TENANT_LOCAL, (tenant,))
        yield


__all__ = [
    "_async_apply_tenant",
    "_current_tenant_for_session",
    "_sync_apply_tenant",
    "bind_tenant",
    "bind_tenant_async",
]
