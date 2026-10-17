"""Create webhook_outbox (transactional outbox for webhook events)

Revision ID: 016_webhook_outbox
Revises: 015_mcp_tasks
Create Date: 2026-10-16 12:00:00.000000

Durable backing for :class:`baselith.core.webhooks.outbox_postgres.PostgresOutbox`.
``WebhookService.publish`` inserts one row per event, in the caller's own
transaction when it passes a cursor, and the outbox relay deletes the row once
the event has been dispatched. A relay that dies mid-delivery leaves the row
behind, so the event is delivered again by another replica.

``next_attempt_at`` and ``locked_until`` are epoch seconds taken from the
database clock (``clock_timestamp()``), never from a replica. The partial index
covers the only query that runs often (due, pending rows in age order); dead
rows are parked and rare.

Like the other webhook tables, nothing creates this one at runtime, and the
row-level-security policy is born here with the system-tenant escape the relay
needs (it drains every tenant).
"""

from collections.abc import Sequence
from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "016_webhook_outbox"
down_revision: Union[str, None] = "015_mcp_tasks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Tables this migration protects with a policy. Merged with the earlier
#: migrations' lists and compared against ``baselith.core.db.ddl.RLS_PROTECTED_TABLES``
#: by ``tests/unit/test_schema_ownership.py``.
TENANT_SCOPED_TABLES: tuple[str, ...] = ("webhook_outbox",)

POLICY_NAME = "tenant_isolation"
SYSTEM_TENANT_ID = "system"
_TENANT_EXPR = "COALESCE(current_setting('app.tenant_id', true), 'default')"
_PREDICATE = f"(tenant_id = {_TENANT_EXPR} OR {_TENANT_EXPR} = '{SYSTEM_TENANT_ID}')"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS webhook_outbox (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL DEFAULT 'default',
            event_type TEXT NOT NULL,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_at DOUBLE PRECISION NOT NULL,
            locked_until DOUBLE PRECISION,
            last_error TEXT,
            created_at DOUBLE PRECISION NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_webhook_outbox_due
            ON webhook_outbox (next_attempt_at)
            WHERE status = 'pending'
        """
    )

    for table in TENANT_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON {table}")
        op.execute(
            f"CREATE POLICY {POLICY_NAME} ON {table} "
            f"USING {_PREDICATE} "
            f"WITH CHECK {_PREDICATE}"
        )


def downgrade() -> None:
    for table in TENANT_SCOPED_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON {table}")
    op.execute("DROP INDEX IF EXISTS ix_webhook_outbox_due")
    op.execute("DROP TABLE IF EXISTS webhook_outbox")
