"""Composite tenant indexes for analytics group-by paths

Revision ID: 004_composite_tenant_indexes
Revises: 003_interactions_feedback_indexes
Create Date: 2026-07-04 12:00:00.000000

Adds a composite index to ``chat_feedback`` for the per-tenant analytics
aggregations in ``core/db/feedback.py``:

  - top-queries / active-learning:
        ``WHERE tenant_id = ? [AND ...] GROUP BY query``
        → composite ``(tenant_id, query)``

Before this, the tenant filter used the bare ``idx_chat_feedback_tenant_id`` and
the ``GROUP BY query`` fell back to a sort/hash over the window slice (the bare
``idx_chat_feedback_query`` is not tenant-leftmost, so it cannot serve the
scoped grouping). The composite lets the planner satisfy both the tenant filter
and the grouping key from one index, which matters as per-tenant feedback volume
grows.

The matching ``interactions`` covering index — ``(tenant_id, session_id,
timestamp DESC)`` — is created in ``core/storage/postgres.py`` init instead of
here, because ``interactions.tenant_id`` is added by that runtime schema init
(``ADD COLUMN IF NOT EXISTS``), which runs *after* Alembic; adding the index in
this migration would race a not-yet-present column.

Plain ``CREATE INDEX IF NOT EXISTS`` (not ``CONCURRENTLY``) for the same reason
as migration 003: it must run inside Alembic's transaction over the async
``run_sync`` bridge, and ``init_db()`` migrates before the app pool opens, so
there is no concurrent writer to lock out. ``migrations/env.py`` sets
``lock_timeout`` so a build that cannot take its lock fails fast.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "004_composite_tenant_indexes"
down_revision: Union[str, None] = "003_interactions_fb_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS "
        "idx_chat_feedback_tenant_query "
        "ON chat_feedback(tenant_id, query)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_chat_feedback_tenant_query")
