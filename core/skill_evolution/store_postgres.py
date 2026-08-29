"""Postgres-backed pattern store (durable wiki layer).

Table ``agent_patterns`` (migration ``006_agent_patterns``), tenant-scoped
via :func:`core.context.get_tenant_or_default` (never raises — the loop
runs from background tasks with no bound tenant context) like the other
core stores. Dedup is pushed into the database: an upsert on a known
``(tenant_id, fingerprint)`` pair increments occurrences and merges the
incoming evidence atomically instead of racing a read-modify-write.
"""

from __future__ import annotations

from typing import Any

from psycopg import InterfaceError, OperationalError
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from core.context import get_tenant_or_default
from core.db.connection import get_async_cursor
from core.resilience.retry import retry
from core.skill_evolution.types import (
    MAX_EVIDENCE,
    EvidenceRef,
    Pattern,
    PatternKind,
    PatternStatus,
)

__all__ = ["PostgresPatternStore"]

# Evidence merge keeps only the newest MAX_EVIDENCE entries: elements are
# enumerated WITH ORDINALITY (append order), the newest are selected, then
# re-aggregated back in chronological order.
_UPSERT_SQL = """
INSERT INTO agent_patterns
    (id, tenant_id, fingerprint, kind, title, summary, evidence,
     occurrences, status, created_at, updated_at)
VALUES
    (%(id)s, %(tenant_id)s, %(fingerprint)s, %(kind)s, %(title)s,
     %(summary)s, %(evidence)s, %(occurrences)s, %(status)s,
     %(created_at)s, %(updated_at)s)
ON CONFLICT (tenant_id, fingerprint) DO UPDATE SET
    occurrences = agent_patterns.occurrences + 1,
    evidence = (
        SELECT COALESCE(jsonb_agg(elem ORDER BY ord), '[]'::jsonb)
        FROM (
            SELECT elem, ord
            FROM jsonb_array_elements(
                     agent_patterns.evidence || EXCLUDED.evidence
                 ) WITH ORDINALITY AS t(elem, ord)
            ORDER BY ord DESC
            LIMIT %(max_evidence)s
        ) newest
    ),
    updated_at = now()
RETURNING *
"""

_RETRYABLE = (OperationalError, InterfaceError)


class PostgresPatternStore:
    """Durable :class:`core.skill_evolution.store.PatternStore` backend."""

    @retry(max_attempts=3, retryable_exceptions=_RETRYABLE)
    async def upsert(self, pattern: Pattern) -> Pattern:
        params: dict[str, Any] = {
            "id": pattern.id,
            "tenant_id": get_tenant_or_default(),
            "fingerprint": pattern.fingerprint,
            "kind": pattern.kind.value,
            "title": pattern.title,
            "summary": pattern.summary,
            "evidence": Jsonb([e.model_dump(mode="json") for e in pattern.evidence]),
            "occurrences": pattern.occurrences,
            "status": pattern.status.value,
            "created_at": pattern.created_at,
            "updated_at": pattern.updated_at,
            "max_evidence": MAX_EVIDENCE,
        }
        async with get_async_cursor(row_factory=dict_row) as cur:
            await cur.execute(_UPSERT_SQL, params)
            row = await cur.fetchone()
        assert row is not None  # RETURNING always yields on insert/update
        return _row_to_pattern(row)

    @retry(max_attempts=3, retryable_exceptions=_RETRYABLE)
    async def get(self, pattern_id: str) -> Pattern | None:
        async with get_async_cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT * FROM agent_patterns WHERE id = %s AND tenant_id = %s",
                (pattern_id, get_tenant_or_default()),
            )
            row = await cur.fetchone()
        return None if row is None else _row_to_pattern(row)

    @retry(max_attempts=3, retryable_exceptions=_RETRYABLE)
    async def list_patterns(
        self,
        *,
        kind: PatternKind | None = None,
        status: PatternStatus | None = None,
        limit: int = 50,
    ) -> list[Pattern]:
        clauses = ["tenant_id = %s"]
        params: list[Any] = [get_tenant_or_default()]
        if kind is not None:
            clauses.append("kind = %s")
            params.append(kind.value)
        if status is not None:
            clauses.append("status = %s")
            params.append(status.value)
        params.append(limit)
        query = (
            "SELECT * FROM agent_patterns WHERE "
            + " AND ".join(clauses)
            + " ORDER BY occurrences DESC, updated_at DESC LIMIT %s"
        )
        async with get_async_cursor(row_factory=dict_row) as cur:
            await cur.execute(query, params)
            rows = await cur.fetchall()
        return [_row_to_pattern(row) for row in rows]

    @retry(max_attempts=3, retryable_exceptions=_RETRYABLE)
    async def set_status(self, pattern_id: str, status: PatternStatus) -> bool:
        async with get_async_cursor() as cur:
            await cur.execute(
                "UPDATE agent_patterns SET status = %s, updated_at = now() "
                "WHERE id = %s AND tenant_id = %s",
                (status.value, pattern_id, get_tenant_or_default()),
            )
            return bool(cur.rowcount)


def _row_to_pattern(row: dict[str, Any]) -> Pattern:
    evidence_raw = row.get("evidence") or []
    return Pattern(
        id=row["id"],
        fingerprint=row["fingerprint"],
        kind=PatternKind(row["kind"]),
        title=row["title"],
        summary=row["summary"],
        evidence=[EvidenceRef.model_validate(e) for e in evidence_raw],
        occurrences=row["occurrences"],
        status=PatternStatus(row["status"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
