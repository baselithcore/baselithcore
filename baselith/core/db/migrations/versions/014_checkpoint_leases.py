"""Per-run lease columns on agent_checkpoints

Revision ID: 014_checkpoint_leases
Revises: 013_online_eval_results
Create Date: 2026-10-11 10:00:00.000000

Crash recovery used to decide that a ``running`` checkpoint was abandoned from
how long nothing had been written to it, so a single LLM or tool call longer
than ``recovery_resume_after_seconds`` let a second worker resume a run that
was still executing. ``PostgresCheckpointStore`` now holds an explicit lease:
the executing attempt's ``owner`` id and the Unix time its lease runs out,
claimed with a conditional ``UPDATE`` and renewed by a heartbeat. Recovery
resumes only runs whose lease has expired.

Both columns are nullable and have no default, so ``ADD COLUMN`` is a
catalog-only change (no table rewrite) and every existing row reads as
"unowned" — exactly what a run interrupted before the upgrade is.
``IF NOT EXISTS`` makes this a no-op where the store's runtime DDL
(``DB_RUNTIME_DDL=true``) already added them.
"""

from collections.abc import Sequence
from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "014_checkpoint_leases"
down_revision: Union[str, None] = "013_online_eval_results"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE agent_checkpoints ADD COLUMN IF NOT EXISTS owner TEXT")
    op.execute(
        "ALTER TABLE agent_checkpoints "
        "ADD COLUMN IF NOT EXISTS lease_expires_at DOUBLE PRECISION"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE agent_checkpoints DROP COLUMN IF EXISTS lease_expires_at")
    op.execute("ALTER TABLE agent_checkpoints DROP COLUMN IF EXISTS owner")
