"""Tenant data purge — GDPR right-to-be-forgotten.

Deletes every row scoped to a tenant across **all** public tables that carry a
``tenant_id`` column — core (``interactions``/``feedback``) and any plugin store
(BOP, pitwall, red_agent, …). The table set is discovered dynamically from
``information_schema`` so no hand-maintained list can drift out of date.

Foreign keys are handled by a **fixpoint** loop: a table whose delete fails
because a not-yet-purged child still references it is retried on the next pass,
until no further progress is made. The pool is autocommit, so a failed delete
never poisons later statements.
"""

from __future__ import annotations

from core.db.connection import get_async_cursor
from core.observability.logging import get_logger

logger = get_logger(__name__)


async def tenant_scoped_tables() -> list[str]:
    """Public tables that carry a ``tenant_id`` column."""
    async with get_async_cursor() as cur:
        await cur.execute(
            "SELECT table_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND column_name = 'tenant_id' "
            "ORDER BY table_name"
        )
        rows = await cur.fetchall()
    return [r[0] for r in rows]


async def purge_tenant_data(tenant_id: str) -> dict[str, int]:
    """Delete all rows scoped to ``tenant_id`` across every tenant-scoped table.

    Returns a ``{table: rows_deleted}`` map. Idempotent (a second call deletes
    nothing). Tenant-scoped data only — the tenant entity row (``auth_tenants``)
    and membership are owned by the auth plugin's ``delete_tenant``.
    """
    tables = await tenant_scoped_tables()
    deleted: dict[str, int] = {}
    pending = set(tables)
    progress = True
    while pending and progress:
        progress = False
        for table in sorted(pending):
            try:
                async with get_async_cursor() as cur:
                    # table comes from information_schema (trusted), quoted as an
                    # identifier; the value is parameterised.
                    await cur.execute(
                        f'DELETE FROM "{table}" WHERE tenant_id = %s',  # nosec B608
                        (tenant_id,),
                    )
                    deleted[table] = deleted.get(table, 0) + cur.rowcount
                pending.discard(table)
                progress = True
            except Exception as exc:
                logger.debug("Tenant purge retry for %s: %s", table, exc)
    if pending:
        logger.warning(
            "Tenant %s purge incomplete; tables still pending: %s",
            tenant_id,
            sorted(pending),
        )
    return deleted
