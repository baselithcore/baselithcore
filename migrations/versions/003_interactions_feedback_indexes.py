"""Interactions and feedback performance indexes

Revision ID: 003_interactions_feedback_indexes
Revises: 002_feedback_performance_indexes
Create Date: 2026-05-17 11:00:00.000000

Adds indexes to the ``interactions`` and ``feedback`` tables to support the
hottest query paths in ``core/storage/postgres.py``:

  - ``interactions WHERE session_id = ? ORDER BY timestamp DESC``
        → composite ``(session_id, timestamp DESC)``
  - ``interactions WHERE agent_id = ?`` (FeedbackRepository JOIN filter)
        → btree on ``agent_id``
  - ``interactions WHERE user_id = ?`` (anticipated per-user lookups)
        → btree on ``user_id``
  - ``feedback WHERE interaction_id = ?`` (Postgres does not auto-create a
    btree on foreign keys; the JOIN path uses this column)
        → btree on ``interaction_id``
  - ``feedback ORDER BY timestamp DESC`` (recent-feedback pagination)
        → btree on ``timestamp DESC``

All indexes are created ``CONCURRENTLY`` to avoid locking writers on existing
data. The migration disables Alembic's implicit transaction so the
``CONCURRENTLY`` statements can run.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "003_interactions_fb_indexes"
down_revision: Union[str, None] = "002b_interactions_feedback"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ``CREATE INDEX CONCURRENTLY`` cannot run inside a transaction block.
    # ``autocommit_block`` leaves Alembic's per-migration transaction and puts the
    # connection in AUTOCOMMIT for the block, so each CONCURRENTLY runs standalone.
    # A bare ``COMMIT`` is NOT enough: psycopg immediately opens a fresh implicit
    # transaction for the next statement, so the very next CREATE INDEX
    # CONCURRENTLY still fails with ActiveSqlTransaction.
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "idx_interactions_session_timestamp "
            "ON interactions(session_id, timestamp DESC)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "idx_interactions_agent_id ON interactions(agent_id)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "idx_interactions_user_id ON interactions(user_id)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "idx_feedback_interaction_id ON feedback(interaction_id)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "idx_feedback_timestamp ON feedback(timestamp DESC)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_feedback_timestamp")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_feedback_interaction_id")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_interactions_user_id")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_interactions_agent_id")
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS idx_interactions_session_timestamp"
        )
