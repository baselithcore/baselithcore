"""
Database Schema Management.

Handles table creation, schema migrations, and index optimization for PostgreSQL.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any, Final

from core.config import get_storage_config
from core.observability.logging import get_logger

logger = get_logger(__name__)

_storage_config = get_storage_config()

POSTGRES_ENABLED = _storage_config.postgres_enabled

# Fixed 64-bit key for the migration advisory lock. Any process that reaches
# ``ensure_schema`` must hold this session-level lock before running Alembic, so
# that under multi-worker boot (``WEB_CONCURRENCY>1``) only ONE process migrates
# at a time. The losers block until the leader finishes, then run ``upgrade head``
# as a no-op (already at head). Without this, N workers race the same DDL and a
# loser can crash-loop on ``lock_timeout`` (see migrations/env.py). The constant
# is arbitrary but must stay stable across releases so old and new workers
# contend on the same lock during a rolling deploy.
_MIGRATION_ADVISORY_LOCK_KEY: Final[int] = 0x6273_6C74_6D67_7274  # "bsltmgrt"

_EXTRA_COLUMNS: Final[tuple[tuple[str, str], ...]] = (
    ("conversation_id", "TEXT"),
    ("sources", "TEXT"),
    ("comment", "TEXT"),
)


@contextlib.contextmanager
def _migration_leader_lock() -> Iterator[None]:
    """Serialize Alembic upgrades across processes via a Postgres advisory lock.

    Holds a session-level advisory lock on a dedicated sync connection for the
    duration of the migration. Concurrent workers block on ``pg_advisory_lock``
    until the leader releases it, so exactly one process runs the DDL. If the
    lock cannot be taken (e.g. Postgres unreachable), it degrades to running the
    upgrade unlocked rather than blocking startup — the single-worker/no-lock
    behaviour is unchanged for deployments that never had contention.
    """
    import psycopg

    conn: Any = None
    locked = False
    try:
        conn = psycopg.connect(_storage_config.conninfo, autocommit=True)
        conn.execute("SELECT pg_advisory_lock(%s)", (_MIGRATION_ADVISORY_LOCK_KEY,))
        locked = True
    except Exception as exc:  # pragma: no cover - infra-dependent
        logger.warning(
            "migration_advisory_lock_unavailable", error=str(exc), degraded="unlocked"
        )
        if conn is not None:
            with contextlib.suppress(Exception):
                conn.close()
            conn = None
    try:
        yield
    finally:
        if conn is not None:
            with contextlib.suppress(Exception):
                if locked:
                    conn.execute(
                        "SELECT pg_advisory_unlock(%s)",
                        (_MIGRATION_ADVISORY_LOCK_KEY,),
                    )
                conn.close()


async def ensure_schema() -> None:
    """
    Ensures that the database schema is up-to-date by running Alembic migrations.
    This runs synchronously in an executor because Alembic's core is synchronous.
    """
    import asyncio
    import os

    from alembic import command
    from alembic.config import Config

    def run_upgrade():
        # Get absolute path to alembic.ini
        alembic_ini_path = os.path.join(os.getcwd(), "alembic.ini")
        alembic_cfg = Config(alembic_ini_path)
        # Hold the cross-process advisory lock across the whole upgrade so
        # multi-worker boot cannot race the same DDL.
        with _migration_leader_lock():
            command.upgrade(alembic_cfg, "head")

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, run_upgrade)


async def init_db() -> None:
    """
    Initializes the database ensuring that the schema is updated via Alembic.
    """

    if not POSTGRES_ENABLED:
        return

    await ensure_schema()
