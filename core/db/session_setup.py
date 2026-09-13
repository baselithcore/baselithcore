"""Per-checkout session setup for pooled PostgreSQL connections.

Two things must be true of every connection handed out by the pools in
:mod:`core.db.connection`: it speaks the application's timezone, and — when
row-level security is enabled — it carries the tenant the work belongs to.
Both are session-scoped ``set_config`` calls applied on checkout, and both are
memoized on the connection so a checkout that changes nothing costs no round
trip.

They live here rather than in ``connection.py`` so that module stays under the
500-line cap; ``connection.py`` re-exports the names, so existing imports and
monkeypatch targets are unchanged.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from psycopg import AsyncConnection, Connection

from core.config import get_app_config

APP_TIMEZONE_NAME = get_app_config().app_timezone

__all__ = [
    "APP_TIMEZONE_NAME",
    "SYSTEM_TENANT_ID",
    "system_tenant_scope",
]


def _sync_apply_timezone(connection: Connection[object]) -> None:
    """Apply the configured timezone to a sync connection once per checkout."""
    if getattr(connection, "_app_timezone", None) == APP_TIMEZONE_NAME:
        return

    with connection.cursor() as cursor:
        # PostgreSQL doesn't accept bind placeholders in `SET TIME ZONE`,
        # but `set_config()` does and avoids string interpolation here.
        cursor.execute("SELECT set_config('TimeZone', %s, false)", (APP_TIMEZONE_NAME,))

    # Dynamic marker attribute — psycopg's Connection doesn't declare it.
    setattr(connection, "_app_timezone", APP_TIMEZONE_NAME)  # noqa: B010


async def _async_apply_timezone(connection: AsyncConnection[object]) -> None:
    """Apply the configured timezone to an async connection once per checkout."""
    if getattr(connection, "_app_timezone", None) == APP_TIMEZONE_NAME:
        return

    async with connection.cursor() as cursor:
        await cursor.execute(
            "SELECT set_config('TimeZone', %s, false)", (APP_TIMEZONE_NAME,)
        )

    # Dynamic marker attribute — psycopg's AsyncConnection doesn't declare it.
    setattr(connection, "_app_timezone", APP_TIMEZONE_NAME)  # noqa: B010


#: Tenant id bound by :func:`system_tenant_scope`. Deployments that enable RLS
#: give it whatever policy their maintenance work needs (commonly ``BYPASSRLS``
#: on the role, or a policy clause matching this id); the point is that the
#: identity is *explicit* rather than a fallback nobody chose.
SYSTEM_TENANT_ID = "system"


@contextmanager
def system_tenant_scope() -> Iterator[None]:
    """Run a block as the ``system`` tenant for DB session binding.

    With RLS enabled, a caller that binds no tenant has no business touching
    tenant data: ``_current_tenant_for_session`` refuses to invent one. Work
    that legitimately runs outside a request — a queue worker draining jobs, a
    CLI command, a migration or bootstrap step — declares that here::

        with system_tenant_scope():
            await run_maintenance()

    The tenant context is bound for the whole block and restored on exit, so
    everything else that scopes by tenant (caches, memory, stores) sees the
    same explicit identity rather than a per-subsystem fallback. Works in sync
    and async code alike: ``contextvars`` propagate into awaited coroutines,
    and the block is restored even if the body raises.

    Yields:
        None. Use it purely for its scope.
    """
    from core.context import reset_tenant_context, set_tenant_context

    token = set_tenant_context(SYSTEM_TENANT_ID)
    try:
        yield
    finally:
        reset_tenant_context(token)
