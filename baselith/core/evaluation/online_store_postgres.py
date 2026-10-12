"""Postgres-backed :class:`~baselith.core.evaluation.online_store.OnlineEvalStore`.

Select it with ``EVAL_ONLINE_STORE=postgres``. Schema ownership:
``online_eval_results`` is created by
``baselith/core/db/migrations/versions/013_online_eval_results.py`` and by
nothing else; this module runs no DDL.

Tenancy: verdicts are written from the evaluation workers, outside any request,
so each insert binds the tenant the flow ran under — a value the framework
recorded on the ``FLOW_COMPLETED`` event, not one a caller asserted — and the
row passes the table's row-level-security ``WITH CHECK`` as that tenant. The
retention sweep spans every tenant and runs as the ``system`` maintenance
identity. Reads are scoped to the tenant passed in, and RLS scopes them to the
ambient tenant on top.
"""

from __future__ import annotations

import json
from typing import Any

from psycopg.rows import dict_row

from baselith.core.context import reset_tenant_context, set_tenant_context
from baselith.core.db.connection import get_async_cursor, system_tenant_scope
from baselith.core.evaluation.online_store import OnlineEvalRecord

__all__ = ["PostgresOnlineEvalStore"]

_INSERT = """
INSERT INTO online_eval_results
    (id, tenant_id, run_id, intent, source, score, quality, aspects, flags,
     signals, query_text, response_text, created_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, %s)
ON CONFLICT (id) DO NOTHING
"""

_SELECT = """
SELECT id, tenant_id, run_id, intent, source, score, quality, aspects, flags,
       signals, query_text, response_text, created_at
FROM online_eval_results
"""


def _json(value: Any, empty: Any) -> Any:
    """Tolerate a JSONB column returned as text."""
    if value is None:
        return empty
    if isinstance(value, str):
        return json.loads(value)
    return value


def _record(row: dict[str, Any]) -> OnlineEvalRecord:
    return OnlineEvalRecord(
        id=row["id"],
        tenant_id=row["tenant_id"],
        run_id=row["run_id"],
        intent=row["intent"],
        source=row["source"],
        score=float(row["score"]),
        quality=row["quality"],
        aspects=dict(_json(row["aspects"], {})),
        flags=tuple(_json(row["flags"], [])),
        signals=dict(_json(row["signals"], {})),
        query_text=row["query_text"],
        response_text=row["response_text"],
        created_at=float(row["created_at"]),
    )


class PostgresOnlineEvalStore:
    """Durable online evaluation verdicts (``online_eval_results``)."""

    async def save(self, record: OnlineEvalRecord) -> None:
        token = set_tenant_context(record.tenant_id)
        try:
            async with get_async_cursor() as cur:
                await cur.execute(
                    _INSERT,
                    (
                        record.id,
                        record.tenant_id,
                        record.run_id,
                        record.intent,
                        record.source,
                        record.score,
                        record.quality,
                        json.dumps(record.aspects),
                        json.dumps(list(record.flags)),
                        json.dumps(record.signals),
                        record.query_text,
                        record.response_text,
                        record.created_at,
                    ),
                )
        finally:
            reset_tenant_context(token)

    async def recent(
        self,
        *,
        intent: str | None = None,
        limit: int = 100,
        tenant_id: str | None = None,
    ) -> list[OnlineEvalRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if tenant_id is not None:
            clauses.append("tenant_id = %s")
            params.append(tenant_id)
        if intent is not None:
            clauses.append("intent = %s")
            params.append(intent)
        where = f"WHERE {' AND '.join(clauses)} " if clauses else ""
        params.append(max(0, limit))
        # Only the constant clauses above are interpolated; values are bound.
        query = f"{_SELECT}{where}ORDER BY created_at DESC LIMIT %s"  # nosec B608
        async with get_async_cursor(row_factory=dict_row) as cur:
            await cur.execute(query, tuple(params))
            rows = await cur.fetchall()
        return [_record(row) for row in rows]

    async def purge_before(self, cutoff: float) -> int:
        with system_tenant_scope():
            async with get_async_cursor() as cur:
                await cur.execute(
                    "DELETE FROM online_eval_results WHERE created_at < %s", (cutoff,)
                )
                return int(cur.rowcount or 0)
