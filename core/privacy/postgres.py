"""
PostgreSQL data provider for data-subject requests.

Wires the relational store into the DSR framework (:mod:`core.privacy`) so
export, erasure, and retention actually touch persisted personal data instead
of returning empty results.

A *subject* maps to ``interactions.user_id`` — a person's interaction records
and the feedback attached to them. Every query is **tenant-scoped** to the
active tenant context (degrading to ``"default"`` outside a request via
:func:`core.context.get_tenant_or_default`), so one tenant's admin can never
reach another tenant's rows.

The provider also participates in retention sweeps: interactions and their
feedback, plus ``chat_feedback`` rows, older than the cutoff are purged within
the active tenant. ``chat_feedback`` is keyed by conversation, not by user, so
it takes part in retention only — not subject export/erasure.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg.rows import dict_row

from core.context import get_tenant_or_default
from core.db.connection import get_async_cursor
from core.observability.logging import get_logger

logger = get_logger(__name__)


def _jsonable(value: Any) -> Any:
    """Coerce a DB value into something JSON-serialisable for the export bundle."""
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    return {key: _jsonable(val) for key, val in row.items()}


class PostgresDataProvider:
    """DSR provider over the core relational store (``interactions``/``feedback``).

    Satisfies :class:`~core.privacy.provider.DataProvider` and
    :class:`~core.privacy.provider.RetentionProvider`. The opaque ``subject_id``
    is matched against ``interactions.user_id``.
    """

    name = "postgres"

    async def export(self, subject_id: str) -> dict[str, list[dict[str, Any]]]:
        """Return the subject's interactions and their feedback (right to access)."""
        tenant = get_tenant_or_default()
        async with get_async_cursor(row_factory=dict_row) as cur:  # type: ignore[arg-type]
            await cur.execute(
                "SELECT id, session_id, user_id, agent_id, input_transcription, "
                "output_transcription, metadata, timestamp FROM interactions "
                "WHERE user_id = %s AND tenant_id = %s ORDER BY timestamp DESC",
                (subject_id, tenant),
            )
            interactions = [_row_to_dict(r) for r in await cur.fetchall()]

        async with get_async_cursor(row_factory=dict_row) as cur:  # type: ignore[arg-type]
            await cur.execute(
                "SELECT id, interaction_id, score, label, comment, metadata, "
                "timestamp FROM feedback WHERE tenant_id = %s AND interaction_id IN "
                "(SELECT id FROM interactions WHERE user_id = %s AND tenant_id = %s)",
                (tenant, subject_id, tenant),
            )
            feedback = [_row_to_dict(r) for r in await cur.fetchall()]

        return {"interactions": interactions, "feedback": feedback}

    async def erase(self, subject_id: str) -> int:
        """Delete the subject's interactions and dependent feedback (right to erasure).

        Children are removed first to respect the
        ``feedback.interaction_id -> interactions.id`` foreign key. Returns the
        total number of rows removed.
        """
        tenant = get_tenant_or_default()
        removed = 0
        async with get_async_cursor() as cur:
            await cur.execute(
                "DELETE FROM feedback WHERE tenant_id = %s AND interaction_id IN "
                "(SELECT id FROM interactions WHERE user_id = %s AND tenant_id = %s)",
                (tenant, subject_id, tenant),
            )
            removed += cur.rowcount
        async with get_async_cursor() as cur:
            await cur.execute(
                "DELETE FROM interactions WHERE user_id = %s AND tenant_id = %s",
                (subject_id, tenant),
            )
            removed += cur.rowcount
        return removed

    async def purge_expired(self, older_than_seconds: int) -> int:
        """Purge rows older than the cutoff across **all tenants** (retention).

        Retention enforces the storage-limitation principle (GDPR Art. 5(1)(e))
        globally — it is a data-lifecycle policy, not a tenant-isolation concern
        (it deletes expired rows, it never exposes them) — and the background
        scheduler runs without a tenant context. Subject export/erasure stay
        tenant-scoped; only retention is global. Feedback is removed before its
        parent interaction so the foreign key is never violated.
        """
        removed = 0
        # Feedback whose parent interaction is expiring (FK-safe ordering).
        removed += await self._delete(
            "DELETE FROM feedback WHERE interaction_id IN "
            "(SELECT id FROM interactions "
            "WHERE timestamp < NOW() - make_interval(secs => %s))",
            (older_than_seconds,),
        )
        removed += await self._delete(
            "DELETE FROM interactions "
            "WHERE timestamp < NOW() - make_interval(secs => %s)",
            (older_than_seconds,),
        )
        # chat_feedback is conversation-keyed (no user_id) → retention only.
        removed += await self._delete(
            "DELETE FROM chat_feedback "
            "WHERE timestamp < NOW() - make_interval(secs => %s)",
            (older_than_seconds,),
            optional=True,
        )
        return removed

    @staticmethod
    async def _delete(
        sql: str, params: tuple[Any, ...], *, optional: bool = False
    ) -> int:
        """Run a parameterised DELETE and return its rowcount.

        ``optional`` deletes (e.g. ``chat_feedback``, which a deployment may not
        have migrated) swallow errors so they never abort the rest of the sweep;
        core-table deletes propagate so real failures surface to the service.
        """
        try:
            async with get_async_cursor() as cur:
                await cur.execute(sql, params)
                return cur.rowcount
        except Exception as exc:
            if not optional:
                raise
            logger.debug("privacy_retention_skip", extra={"error": str(exc)})
            return 0
