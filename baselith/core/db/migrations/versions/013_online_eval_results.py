"""Create online_eval_results (durable online evaluation store)

Revision ID: 013_online_eval_results
Revises: 012_webhook_retention_idx
Create Date: 2026-10-11 10:00:00.000000

Durable backing for
:class:`baselith.core.evaluation.online_store_postgres.PostgresOnlineEvalStore`
(``EVAL_ONLINE_STORE=postgres``): one row per online evaluation verdict of a
sampled production flow — the deterministic checks and, when the flow was
also judged, the LLM judge.

A row holds the verdict, not the conversation. ``query_text`` and
``response_text`` stay ``NULL`` unless ``EVAL_ONLINE_STORE_RAW_TEXT`` is on,
and then hold redacted, length-capped text.

``created_at`` carries its own index for the retention sweep
(``DELETE ... WHERE created_at < %s`` across every tenant — see 012 for why a
trailing column of the composite index cannot serve it). Like 011, nothing
creates this table at runtime, and its row-level-security policy is born here
with the system-tenant escape that 010 added.
"""

from collections.abc import Sequence
from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "013_online_eval_results"
down_revision: Union[str, None] = "012_webhook_retention_idx"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Tables this migration protects with a policy. Merged with the earlier
#: migrations' lists and compared against ``baselith.core.db.ddl.RLS_PROTECTED_TABLES``
#: by ``tests/unit/test_schema_ownership.py``.
TENANT_SCOPED_TABLES: tuple[str, ...] = ("online_eval_results",)

POLICY_NAME = "tenant_isolation"
SYSTEM_TENANT_ID = "system"
_TENANT_EXPR = "COALESCE(current_setting('app.tenant_id', true), 'default')"
_PREDICATE = f"(tenant_id = {_TENANT_EXPR} OR {_TENANT_EXPR} = '{SYSTEM_TENANT_ID}')"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS online_eval_results (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL DEFAULT 'default',
            run_id TEXT,
            intent TEXT NOT NULL,
            source TEXT NOT NULL,
            score DOUBLE PRECISION NOT NULL,
            quality TEXT NOT NULL,
            aspects JSONB NOT NULL DEFAULT '{}'::jsonb,
            flags JSONB NOT NULL DEFAULT '[]'::jsonb,
            signals JSONB NOT NULL DEFAULT '{}'::jsonb,
            query_text TEXT,
            response_text TEXT,
            created_at DOUBLE PRECISION NOT NULL
        )
        """
    )
    # Operator surface ("recent verdicts for this intent") and the sweep.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_online_eval_results_tenant_intent_created
            ON online_eval_results (tenant_id, intent, created_at DESC)
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS "
        "ix_online_eval_results_created_at ON online_eval_results (created_at)"
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
    op.execute("DROP INDEX IF EXISTS ix_online_eval_results_created_at")
    op.execute("DROP INDEX IF EXISTS ix_online_eval_results_tenant_intent_created")
    op.execute("DROP TABLE IF EXISTS online_eval_results")
