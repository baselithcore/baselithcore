"""Postgres-backed :class:`~core.orchestration.idempotency.ToolLedger`.

The in-process ledger deduplicates a retry inside one worker. It cannot help
across a restart or a second replica — and those are precisely the moments a
redelivered task re-runs a payment or an outbound webhook. This ledger closes
that gap with one row per derived key in ``tool_invocations``.

The claim is the primary key, not a lock. Two replicas that both miss on
:meth:`lookup` race a single ``INSERT ... ON CONFLICT``; exactly one wins, and
the loser is handed the winner's row rather than a success. No advisory lock,
no lease to expire, nothing to reconcile after a crash — the row *is* the
state.

    ledger = PostgresToolLedger()
    held = await ledger.begin(key, run_id=run_id, tool="charge_card")
    if held is not None:
        if held.is_replayable:
            return held.result
        raise ToolCallInFlight("charge_card", key)
    ...

One round trip does both: ``begin`` claims the key and returns the row already
holding it. :meth:`PostgresToolLedger.lookup` is for the operator surface
("what did run X do?"), not for the hot path — a lookup-then-claim pair is two
round trips *and* a race.

Schema ownership: ``tool_invocations`` is created by
``migrations/versions/009_tool_invocations.py`` and by nothing else. This module
runs no DDL — the policy in :mod:`core.db.ddl` is that Alembic owns every table,
and a table introduced after that policy has no legacy self-init path to keep.
A deployment that has not run migrations therefore sees an ordinary "relation
does not exist" error naming the table, which is the diagnosable failure.

Rows are tenant-scoped (the row-level-security policy is defined in the same
migration) and bounded only by :meth:`purge_completed_before`: a redelivery
window is hours, not forever, and the ledger is not an audit log — the audit
trail lives in :mod:`core.observability.audit_chain`.
"""

from __future__ import annotations

import json
from typing import Any

from psycopg.rows import dict_row

from core.context import get_current_tenant_id
from core.db.connection import get_async_cursor
from core.observability.logging import get_logger
from core.orchestration.idempotency import ToolOutcome

logger = get_logger(__name__)

__all__ = ["PostgresToolLedger"]

_LOOKUP = """
SELECT status, result, error,
       EXTRACT(EPOCH FROM updated_at)::float8 AS recorded_at
FROM tool_invocations
WHERE key = %s
"""

# The claim. ``DO UPDATE ... WHERE status = 'failed'`` re-takes a row whose call
# never landed; every other conflict matches no row, so RETURNING is empty and
# the caller learns it lost. ``xmax`` tricks are unnecessary: presence of a
# returned row is the answer.
_CLAIM = """
INSERT INTO tool_invocations (key, run_id, tool, tenant_id, status)
VALUES (%s, %s, %s, %s, 'in_flight')
ON CONFLICT (key) DO UPDATE SET
    run_id = EXCLUDED.run_id,
    tool = EXCLUDED.tool,
    status = 'in_flight',
    result = NULL,
    error = NULL,
    updated_at = now()
WHERE tool_invocations.status = 'failed'
RETURNING key
"""

_COMPLETE = """
UPDATE tool_invocations
SET status = 'completed', result = %s, error = NULL, updated_at = now()
WHERE key = %s
"""

_FAIL = """
UPDATE tool_invocations
SET status = 'failed', error = %s, updated_at = now()
WHERE key = %s
"""

_PURGE = """
DELETE FROM tool_invocations
WHERE status = 'completed' AND updated_at < now() - make_interval(secs => %s)
"""


def _decode_result(raw: Any) -> Any:
    """Read back a ``result`` column.

    psycopg returns ``jsonb`` already decoded; a driver or column type that
    hands back text is decoded here rather than surfacing a JSON string where
    the caller expects the original value.
    """
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return raw
    return raw


class PostgresToolLedger:
    """Durable tool-call ledger shared by every replica of a deployment.

    Args:
        tenant_id: Rows are written under this tenant; ``None`` resolves the
            ambient tenant per call via :func:`core.context.get_current_tenant_id`,
            which is what a request-scoped agent loop wants.
    """

    def __init__(self, tenant_id: str | None = None) -> None:
        self._tenant_id = tenant_id

    def _tenant(self) -> str:
        return self._tenant_id or get_current_tenant_id()

    async def lookup(self, key: str) -> ToolOutcome | None:
        """The recorded outcome for ``key``, or ``None`` when unseen."""
        async with get_async_cursor(row_factory=dict_row) as cur:
            await cur.execute(_LOOKUP, (key,))
            row = await cur.fetchone()
        if row is None:
            return None
        return ToolOutcome(
            status=row["status"],
            result=_decode_result(row["result"]),
            error=row["error"],
            recorded_at=float(row["recorded_at"] or 0.0),
        )

    async def begin(self, key: str, *, run_id: str, tool: str) -> ToolOutcome | None:
        """Claim ``key``, or return the row that already holds it.

        Args:
            key: The derived idempotency key.
            run_id: The run this call belongs to.
            tool: Tool name, for the operator surface.

        Returns:
            ``None`` when this caller owns the call. The existing outcome when
            another worker claimed it first — treat it exactly like a
            :meth:`lookup` hit.
        """
        async with get_async_cursor() as cur:
            await cur.execute(_CLAIM, (key, run_id, tool, self._tenant()))
            claimed = await cur.fetchone()
        if claimed is not None:
            return None
        # Lost the race. The winner's row is authoritative; a NULL here would
        # mean it was deleted between the two statements, and re-executing an
        # effectful call on that basis is worse than reporting in-flight.
        return await self.lookup(key) or ToolOutcome(status="in_flight")

    async def complete(self, key: str, result: Any) -> None:
        """Record that the call succeeded, with its result."""
        payload = json.dumps(result, default=str)
        async with get_async_cursor() as cur:
            await cur.execute(_COMPLETE, (payload, key))

    async def fail(self, key: str, error: str) -> None:
        """Record that the call failed, so a retry is allowed."""
        async with get_async_cursor() as cur:
            await cur.execute(_FAIL, (error, key))

    async def purge_completed_before(self, max_age_seconds: float) -> int:
        """Drop completed rows older than ``max_age_seconds``.

        ``in_flight`` and ``failed`` rows are kept regardless of age: the first
        is an unresolved question an operator should see, the second is the
        record that a retry is permitted.

        Args:
            max_age_seconds: Retention window; should exceed the longest
                redelivery window of the task queue in front of the loop.

        Returns:
            Number of rows deleted.
        """
        async with get_async_cursor() as cur:
            await cur.execute(_PURGE, (max_age_seconds,))
            # psycopg reports -1 when the count is unknown; never surface it.
            deleted = int(cur.rowcount or 0)
        if deleted:
            logger.info(f"tool ledger purged {deleted} completed rows")
        return max(0, deleted)
