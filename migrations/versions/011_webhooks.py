"""Create webhook_endpoints and webhook_deliveries (durable webhook store)

Revision ID: 011_webhooks
Revises: 010_system_tenant_rls
Create Date: 2026-09-23 16:00:00.000000

Durable backing for :class:`core.webhooks.store_postgres.PostgresWebhookStore`.
The in-memory store lost every subscription on restart and was invisible to a
second replica and to the RQ worker, which is where ``agent.*`` events are
emitted — so a registered endpoint could silently never fire.

``secret`` and ``headers`` hold ciphertext when ``DATA_ENCRYPTION_KEYS`` is
configured (AES-256-GCM via ``core.security.FieldEncryptor``, bound to the
endpoint id); they are ``TEXT`` so a rollout over existing plaintext rows keeps
reading them.

Like ``tool_invocations`` (009), nothing creates these tables at runtime, and
the row-level-security policy is born here with the system-tenant escape that
010 added to the older tables.
"""

from collections.abc import Sequence
from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "011_webhooks"
down_revision: Union[str, None] = "010_system_tenant_rls"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Tables this migration protects with a policy. Merged with the earlier
#: migrations' lists and compared against ``core.db.ddl.RLS_PROTECTED_TABLES``
#: by ``tests/unit/test_schema_ownership.py``.
TENANT_SCOPED_TABLES: tuple[str, ...] = ("webhook_endpoints", "webhook_deliveries")

POLICY_NAME = "tenant_isolation"
SYSTEM_TENANT_ID = "system"
_TENANT_EXPR = "COALESCE(current_setting('app.tenant_id', true), 'default')"
_PREDICATE = f"(tenant_id = {_TENANT_EXPR} OR {_TENANT_EXPR} = '{SYSTEM_TENANT_ID}')"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS webhook_endpoints (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL DEFAULT 'default',
            url TEXT NOT NULL,
            secret TEXT NOT NULL,
            event_types JSONB NOT NULL DEFAULT '["*"]'::jsonb,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            description TEXT,
            headers TEXT NOT NULL DEFAULT '{}',
            created_at DOUBLE PRECISION NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_webhook_endpoints_tenant
            ON webhook_endpoints (tenant_id)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS webhook_deliveries (
            id TEXT PRIMARY KEY,
            endpoint_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            tenant_id TEXT NOT NULL DEFAULT 'default',
            url TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            last_status_code INTEGER,
            last_error TEXT,
            created_at DOUBLE PRECISION NOT NULL,
            completed_at DOUBLE PRECISION,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb
        )
        """
    )
    # Operator surface ("recent deliveries for this tenant") and the retention
    # sweep that drops rows older than the replay window.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_webhook_deliveries_tenant_created
            ON webhook_deliveries (tenant_id, created_at DESC)
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
    op.execute("DROP INDEX IF EXISTS ix_webhook_deliveries_tenant_created")
    op.execute("DROP TABLE IF EXISTS webhook_deliveries")
    op.execute("DROP INDEX IF EXISTS ix_webhook_endpoints_tenant")
    op.execute("DROP TABLE IF EXISTS webhook_endpoints")
