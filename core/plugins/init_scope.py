"""The tenant identity a plugin's ``initialize()`` runs under.

Activating a plugin is boot work, not a request: it registers permissions,
opens stores, reads its own settings and starts background loops — none of it
on behalf of a tenant. With row-level security on (``DB_RLS_ENABLED=true``)
that matters, because :func:`core.db.connection._current_tenant_for_session`
refuses to invent a tenant, so every such database call raised
``TenantContextError``: a plugin whose store opens a connection at activation
failed outright, others silently fell back to in-memory state, and a
scheduler started from ``initialize()`` failed every tick.

So activation runs inside :func:`core.db.connection.system_tenant_scope`, the
identity migration 010 grants cross-tenant maintenance. ``asyncio`` tasks copy
the context they are created in, so a background loop a plugin starts from
``initialize()`` keeps that identity — the same rows it saw before RLS existed,
which is what a cross-tenant scheduler is. The same holds when an admin enables
a plugin from a request: activation is still not that admin's tenant's work,
and a loop pinned to it would see only one tenant for its whole life.

With RLS off nothing changes: no tenant is bound, exactly as before, so no
cache key or store namespace moves.
"""

from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from typing import Any

__all__ = ["plugin_init_scope"]


def plugin_init_scope() -> AbstractContextManager[Any]:
    """``system_tenant_scope()`` when RLS is enabled, else a no-op context."""
    from core.db import connection

    if not connection.DB_RLS_ENABLED:
        return nullcontext()
    return connection.system_tenant_scope()
