"""Create tool_invocations table (durable tool-call ledger)

Revision ID: 009_tool_invocations
Revises: 008_row_level_security
Create Date: 2026-09-06 15:00:00.000000

Durable backing for :mod:`core.orchestration.idempotency`. The in-process
ledger deduplicates a retry inside one worker; it cannot survive a restart or
reach a second replica, which is exactly when a redelivered task re-runs a
payment or an outbound webhook.

One row per derived idempotency key: ``begin`` inserts it ``in_flight``,
``complete``/``fail`` move it on. ``key`` is the primary key, so two replicas
racing the same call collide on the insert instead of both proceeding — the
loser reads the winner's row.

Unlike the tables adopted by migration 007, nothing creates this one at
runtime: :class:`core.orchestration.idempotency_postgres.PostgresToolLedger`
expects the migrations job to have run. That is the goal state described in
``core/db/ddl.py``, and a new table is born in it rather than being adopted
later.

The table is tenant-scoped, so the row-level-security policy is defined here
alongside it rather than being retrofitted — see migration 008 for why the
policy is inert until the deployment separates the owner and runtime roles.
"""

from collections.abc import Sequence
from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "009_tool_invocations"
down_revision: Union[str, None] = "008_row_level_security"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Tables this migration protects with a policy. Merged with migration 008's
#: list and compared against ``core.db.ddl.RLS_PROTECTED_TABLES`` by
#: ``tests/unit/test_schema_ownership.py``.
TENANT_SCOPED_TABLES: tuple[str, ...] = ("tool_invocations",)

POLICY_NAME = "tenant_isolation"
_TENANT_EXPR = "COALESCE(current_setting('app.tenant_id', true), 'default')"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS tool_invocations (
            key TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            tool TEXT NOT NULL,
            tenant_id TEXT NOT NULL DEFAULT 'default',
            status TEXT NOT NULL DEFAULT 'in_flight',
            result JSONB,
            error TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    # Operator surface: "what did run X do?", and the retention sweep that
    # drops rows older than the redelivery window.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_tool_invocations_tenant_run
            ON tool_invocations (tenant_id, run_id)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_tool_invocations_created_at
            ON tool_invocations (created_at)
        """
    )

    for table in TENANT_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON {table}")
        op.execute(
            f"CREATE POLICY {POLICY_NAME} ON {table} "
            f"USING (tenant_id = {_TENANT_EXPR}) "
            f"WITH CHECK (tenant_id = {_TENANT_EXPR})"
        )


def downgrade() -> None:
    for table in TENANT_SCOPED_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON {table}")
    op.execute("DROP INDEX IF EXISTS ix_tool_invocations_created_at")
    op.execute("DROP INDEX IF EXISTS ix_tool_invocations_tenant_run")
    op.execute("DROP TABLE IF EXISTS tool_invocations")
