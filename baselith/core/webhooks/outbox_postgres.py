"""Postgres-backed :class:`~baselith.core.webhooks.outbox.OutboxStore`.

Schema ownership: ``webhook_outbox`` is created by
``baselith/core/db/migrations/versions/016_webhook_outbox.py`` and by nothing
else; this module runs no DDL. Rows are tenant-scoped under the same
row-level-security policy as the other webhook tables.

Claiming uses ``FOR UPDATE SKIP LOCKED`` plus a lease, so any number of relays
(every API replica) drain the same table without delivering a row twice at the
same time. Every "is it due / is the lease over" test runs on the database
clock (``clock_timestamp()``), so clock skew between replicas cannot hand a
live lease to a second relay.
"""

from __future__ import annotations

import json
from typing import Any

from psycopg.rows import dict_row

from baselith.core.db.connection import get_async_cursor
from baselith.core.webhooks.outbox import OutboxEntry, OutboxState

__all__ = ["PostgresOutbox"]

# Every clock read below is the database's own, in epoch seconds
# (``EXTRACT(EPOCH FROM clock_timestamp())``), spelled out in each statement:
# the SQL is static text, never assembled from parts.
_INSERT = """
INSERT INTO webhook_outbox
    (id, tenant_id, event_type, payload, status, attempts, next_attempt_at, created_at)
VALUES (%s, %s, %s, %s::jsonb, 'pending', 0,
        EXTRACT(EPOCH FROM clock_timestamp()), %s)
ON CONFLICT (id) DO NOTHING
"""

_CLAIM = """
WITH due AS (
    SELECT id FROM webhook_outbox
    WHERE status = 'pending'
      AND next_attempt_at <= EXTRACT(EPOCH FROM clock_timestamp())
      AND (locked_until IS NULL
           OR locked_until <= EXTRACT(EPOCH FROM clock_timestamp()))
    ORDER BY next_attempt_at
    LIMIT %s
    FOR UPDATE SKIP LOCKED
)
UPDATE webhook_outbox AS o
SET locked_until = EXTRACT(EPOCH FROM clock_timestamp()) + %s,
    attempts = o.attempts + 1
FROM due
WHERE o.id = due.id
RETURNING o.id, o.tenant_id, o.event_type, o.payload, o.attempts, o.created_at,
          o.last_error
"""

_FAIL = """
UPDATE webhook_outbox
SET locked_until = NULL, last_error = %s,
    next_attempt_at = EXTRACT(EPOCH FROM clock_timestamp()) + %s
WHERE id = %s
"""


class PostgresOutbox:
    """Durable outbox rows in ``webhook_outbox``."""

    async def enqueue(self, entry: OutboxEntry, cur: Any | None = None) -> None:
        params = (
            entry.id,
            entry.tenant_id,
            entry.event_type,
            json.dumps(entry.data),
            entry.created_at,
        )
        if cur is not None:
            # The caller's transaction: nothing is committed here.
            await cur.execute(_INSERT, params)
            return
        from baselith.core.context import reset_tenant_context, set_tenant_context

        # The row's tenant decides the RLS check, not whichever tenant the
        # calling task happens to have bound.
        token = set_tenant_context(entry.tenant_id)
        try:
            async with get_async_cursor() as own:
                await own.execute(_INSERT, params)
        finally:
            reset_tenant_context(token)

    async def claim(self, limit: int, lease_seconds: float) -> list[OutboxEntry]:
        async with get_async_cursor(row_factory=dict_row) as cur:
            await cur.execute(_CLAIM, (max(limit, 0), lease_seconds))
            rows = await cur.fetchall()
        return [
            OutboxEntry(
                id=row["id"],
                tenant_id=row["tenant_id"],
                event_type=row["event_type"],
                data=(row["payload"] or {}),
                created_at=row["created_at"],
                attempts=row["attempts"],
                last_error=row["last_error"],
            )
            for row in rows
        ]

    async def complete(self, entry_id: str) -> None:
        async with get_async_cursor() as cur:
            await cur.execute("DELETE FROM webhook_outbox WHERE id = %s", (entry_id,))

    async def fail(self, entry_id: str, error: str, retry_in: float) -> None:
        async with get_async_cursor() as cur:
            await cur.execute(_FAIL, (error[:1000], max(retry_in, 0.0), entry_id))

    async def bury(self, entry_id: str, error: str) -> None:
        async with get_async_cursor() as cur:
            await cur.execute(
                "UPDATE webhook_outbox SET status = 'dead', locked_until = NULL, "
                "last_error = %s WHERE id = %s",
                (error[:1000], entry_id),
            )

    async def count(self, state: OutboxState = OutboxState.PENDING) -> int:
        async with get_async_cursor() as cur:
            await cur.execute(
                "SELECT COUNT(*) FROM webhook_outbox WHERE status = %s",
                (state.value,),
            )
            row = await cur.fetchone()
        return int(row[0]) if row else 0
