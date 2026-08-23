"""Retention sweep timestamp index on interactions

Revision ID: 005_retention_timestamp_index
Revises: 004_composite_tenant_indexes
Create Date: 2026-08-22 10:00:00.000000

Adds a plain btree on ``interactions(timestamp)`` for the retention sweep in
``core/privacy/postgres.py`` (``PostgresDataProvider.purge_expired``), which the
background scheduler runs on every cycle:

  - ``DELETE FROM interactions WHERE timestamp < NOW() - make_interval(...)``
        → btree on ``timestamp``
  - ``DELETE FROM feedback WHERE interaction_id IN
    (SELECT id FROM interactions WHERE timestamp < ...)``
        → same index feeds the subquery; the parent delete then uses the
          existing ``idx_feedback_interaction_id`` from migration 003

None of the pre-existing ``interactions`` indexes can serve a bare ``timestamp``
predicate: ``idx_interactions_tenant``, ``idx_interactions_user_id`` and
``idx_interactions_agent_id`` are on other columns, and the two composites
(``(tenant_id, session_id, timestamp DESC)`` from ``core/storage/postgres.py``
init and ``(session_id, timestamp DESC)`` from migration 003) only expose
``timestamp`` as a *trailing* column — a composite btree is only usable when the
predicate matches a leading prefix. Every sweep therefore degraded to a full
sequential scan of ``interactions``.

Index direction is deliberately unspecified (ascending): the predicate is a
one-sided range (``<``), which a btree serves identically in either direction.
The ``DESC`` used elsewhere in this schema exists to match ``ORDER BY timestamp
DESC`` read paths, which retention has none of.

``chat_feedback`` — the third table purged by the same sweep — is intentionally
**not** touched here: ``idx_chat_feedback_timestamp ON chat_feedback(timestamp
DESC)`` already exists from migration ``001_initial_schema`` and covers its
``WHERE timestamp < ...`` delete.

Plain ``CREATE INDEX IF NOT EXISTS``, not ``CONCURRENTLY``, for the same reason
as migrations 003 and 004: this runs inside Alembic's transaction over the async
``run_sync`` bridge in ``migrations/env.py``, where ``autocommit_block()`` — the
only legal way to emit ``CONCURRENTLY`` — leaves the connection in a transaction
block and raises ``ActiveSqlTransaction`` from the CLI, or stalls the FastAPI
boot lifespan. A plain build is safe here because ``init_db()`` migrates
*before* the app connection pool opens (``core/bootstrap/lazy_init.py``), so the
``SHARE`` lock has no application writer to block, and ``migrations/env.py``
sets ``lock_timeout`` (default 5s) so a build that cannot acquire its lock fails
fast instead of hanging boot. Operators running migrations as a pre-deploy job
against a live, large table can build the index out of band beforehand
(``CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_interactions_timestamp ON
interactions(timestamp)``); ``IF NOT EXISTS`` then makes this step a no-op.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "005_retention_timestamp_idx"
down_revision: Union[str, None] = "004_composite_tenant_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS "
        "idx_interactions_timestamp ON interactions(timestamp)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_interactions_timestamp")
