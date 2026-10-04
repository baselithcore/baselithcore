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

Indexes are created with plain ``CREATE INDEX IF NOT EXISTS`` (not
``CONCURRENTLY``). This migration runs inside Alembic's transaction through the
async ``run_sync`` bridge in ``migrations/env.py``, where ``autocommit_block()``
— the only legal way to emit ``CONCURRENTLY`` — is unreliable: it leaves the
connection in a transaction block and raises ``ActiveSqlTransaction`` from the
CLI, or stalls the FastAPI boot lifespan instead of failing cleanly.

Plain builds are safe here: ``init_db()`` runs migrations *before* the app
connection pool opens (``core/bootstrap/lazy_init.py``), so there is no writer
to lock out, and ``migrations/env.py`` sets ``lock_timeout`` (default 5s) so a
build that cannot acquire its lock fails fast rather than hanging boot.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "003_interactions_fb_indexes"
down_revision: Union[str, None] = "002b_interactions_feedback"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Plain (non-CONCURRENTLY) index builds: they run inside Alembic's normal
    # transaction, which is the only reliable mode over the async run_sync bridge
    # in migrations/env.py. See the module docstring for why CONCURRENTLY /
    # autocommit_block was removed.
    op.execute(
        "CREATE INDEX IF NOT EXISTS "
        "idx_interactions_session_timestamp "
        "ON interactions(session_id, timestamp DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_interactions_agent_id ON interactions(agent_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_interactions_user_id ON interactions(user_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS "
        "idx_feedback_interaction_id ON feedback(interaction_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_feedback_timestamp ON feedback(timestamp DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_feedback_timestamp")
    op.execute("DROP INDEX IF EXISTS idx_feedback_interaction_id")
    op.execute("DROP INDEX IF EXISTS idx_interactions_user_id")
    op.execute("DROP INDEX IF EXISTS idx_interactions_agent_id")
    op.execute("DROP INDEX IF EXISTS idx_interactions_session_timestamp")
