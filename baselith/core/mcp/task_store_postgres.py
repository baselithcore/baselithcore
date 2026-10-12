"""PostgreSQL :class:`~baselith.core.mcp.task_persistence.TaskBackend`.

One ``mcp_tasks`` row per handle, tenant-scoped like
:mod:`baselith.core.a2a.task_store_postgres` and created by migration
``015_mcp_tasks`` only (no runtime DDL: the table is the migrations job's).
Selected with ``MCP_TASK_STORE_BACKEND=postgres`` — or ``auto`` while
``POSTGRES_ENABLED`` is on.
"""

from __future__ import annotations

import time
from typing import Any

import orjson
from psycopg.rows import dict_row

from baselith.core.context import get_tenant_or_default
from baselith.core.db.connection import get_async_cursor
from baselith.core.mcp.task_persistence import TaskRow

__all__ = ["PostgresTaskBackend"]

_UPSERT = """
INSERT INTO mcp_tasks
    (task_id, tenant_id, status, data, owner, lease_expires_at, expires_at, updated_at)
VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s, %s)
ON CONFLICT (task_id) DO UPDATE SET
    status = EXCLUDED.status,
    data = EXCLUDED.data,
    owner = EXCLUDED.owner,
    lease_expires_at = EXCLUDED.lease_expires_at,
    expires_at = EXCLUDED.expires_at,
    updated_at = EXCLUDED.updated_at
WHERE mcp_tasks.tenant_id = EXCLUDED.tenant_id
"""

_SELECT = """
SELECT task_id, status, data, owner, lease_expires_at, expires_at, updated_at
FROM mcp_tasks
WHERE task_id = %s AND tenant_id = %s AND expires_at > %s
"""

# Conditional: only a row still live whose lease has lapsed is failed, so the
# owner renewing (or finishing) and a second reader racing both lose cleanly.
_FAIL_ORPHAN = """
UPDATE mcp_tasks
SET status = %s, data = %s::jsonb, owner = NULL, lease_expires_at = NULL,
    updated_at = %s
WHERE task_id = %s AND tenant_id = %s
  AND status IN ('working', 'input_required')
  AND (lease_expires_at IS NULL OR lease_expires_at <= %s)
"""

_RENEW = """
UPDATE mcp_tasks SET lease_expires_at = %s, updated_at = %s
WHERE task_id = ANY(%s) AND tenant_id = %s AND owner = %s
  AND status IN ('working', 'input_required')
"""

_PRUNE = "DELETE FROM mcp_tasks WHERE tenant_id = %s AND expires_at <= %s"


def _tenant() -> str:
    return get_tenant_or_default()


def _row(record: dict[str, Any]) -> TaskRow:
    data = record["data"]
    if isinstance(data, str):  # tolerate a driver returning raw JSON text
        data = orjson.loads(data)
    lease = record.get("lease_expires_at")
    return TaskRow(
        task_id=str(record["task_id"]),
        status=str(record["status"]),
        data=dict(data or {}),
        expires_at=float(record["expires_at"]),
        owner=record.get("owner"),
        lease_expires_at=float(lease) if lease is not None else None,
        updated_at=float(record["updated_at"]),
    )


class PostgresTaskBackend:
    """Durable MCP task rows in PostgreSQL (see module docstring)."""

    async def save(self, row: TaskRow) -> None:
        """Upsert ``row`` under the ambient tenant."""
        async with get_async_cursor() as cur:
            await cur.execute(
                _UPSERT,
                (
                    row.task_id,
                    _tenant(),
                    row.status,
                    orjson.dumps(row.data).decode(),
                    row.owner,
                    row.lease_expires_at,
                    row.expires_at,
                    row.updated_at,
                ),
            )

    async def load(self, task_id: str, *, now: float | None = None) -> TaskRow | None:
        """The unexpired row for ``task_id`` in the ambient tenant."""
        clock = time.time() if now is None else now
        async with get_async_cursor(row_factory=dict_row) as cur:  # type: ignore[arg-type]
            await cur.execute(_SELECT, (task_id, _tenant(), clock))
            record = await cur.fetchone()
        return None if record is None else _row(record)

    async def fail_orphaned(
        self, row: TaskRow, failed: TaskRow, *, now: float | None = None
    ) -> bool:
        """Write ``failed`` over a live row with a lapsed lease, atomically."""
        clock = time.time() if now is None else now
        async with get_async_cursor() as cur:
            await cur.execute(
                _FAIL_ORPHAN,
                (
                    failed.status,
                    orjson.dumps(failed.data).decode(),
                    failed.updated_at,
                    row.task_id,
                    _tenant(),
                    clock,
                ),
            )
            return bool(cur.rowcount)

    async def renew(
        self, task_ids: list[str], owner: str, lease_expires_at: float
    ) -> None:
        """Extend ``owner``'s lease on its live tasks in one statement."""
        if not task_ids:
            return
        async with get_async_cursor() as cur:
            await cur.execute(
                _RENEW, (lease_expires_at, time.time(), task_ids, _tenant(), owner)
            )

    async def prune(self, *, now: float | None = None) -> int:
        """Delete the ambient tenant's expired rows."""
        clock = time.time() if now is None else now
        async with get_async_cursor() as cur:
            await cur.execute(_PRUNE, (_tenant(), clock))
            return int(cur.rowcount or 0)
