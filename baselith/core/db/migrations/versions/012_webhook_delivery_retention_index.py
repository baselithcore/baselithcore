"""Retention-sweep index on webhook_deliveries.created_at

Revision ID: 012_webhook_retention_idx
Revises: 011_webhooks
Create Date: 2026-09-26 10:00:00.000000

``PostgresWebhookStore.purge_deliveries_before`` runs
``DELETE FROM webhook_deliveries WHERE created_at < %s`` across every tenant.
The only index carrying ``created_at`` is the composite
``(tenant_id, created_at DESC)`` from 011, where it is the *trailing* column,
so the planner can only answer the sweep by reading that index end to end:
every purge costs O(table), not O(rows purged) — the same gap 005 closed for
``interactions``. EXPLAIN on 300k seeded deliveries: a full scan of
``ix_webhook_deliveries_tenant_created`` (1 193 buffers) before, an index range
scan on this index after.

``created_at`` is written once at insert and never updated, so the index costs
nothing on the status/attempt updates the dispatcher makes (they stay HOT).

Plain ``CREATE INDEX IF NOT EXISTS`` (not ``CONCURRENTLY``), for the reason
003 documents: migrations run inside Alembic's transaction over the async
``run_sync`` bridge, where ``autocommit_block()`` is unreliable, and
``migrations/env.py`` bounds the build with ``lock_timeout``.
"""

from collections.abc import Sequence
from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "012_webhook_retention_idx"
down_revision: Union[str, None] = "011_webhooks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS "
        "ix_webhook_deliveries_created_at ON webhook_deliveries (created_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_webhook_deliveries_created_at")
