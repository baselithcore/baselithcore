"""Startup comparison of the database schema revision against the packaged head.

Extracted from :mod:`core.api.startup_checks` for the module size cap. The
policy is ``DB_SCHEMA_CHECK``:

- ``strict`` (the production default) refuses to start when the database is
  not at the packaged Alembic head. A replica serving code that expects a
  column the database does not have fails on the first request that touches
  it — as a 500, long after the rollout reported healthy.
- ``warn`` logs the mismatch at ERROR and starts anyway — for operators who
  roll out first and migrate out of band afterwards.
- ``off`` skips the comparison.

Unset, the check runs only in production (``strict``), as it always has;
an explicit value applies in every environment.

A check that cannot *run* — PostgreSQL unreachable, the revision unreadable —
only warns, in every mode. An unreachable database is an outage the app is
built to ride out degraded (lazy pools, background store initialization);
refusing to boot on it would turn a database blip into a fleet-wide crash loop
that the readiness probe already reports more precisely.
"""

from __future__ import annotations

import asyncio
from typing import Literal

from core.config import get_storage_config
from core.observability.logging import get_logger

logger = get_logger(__name__)

SchemaCheckMode = Literal["strict", "warn", "off"]

#: The command that brings the database to the packaged head. Not bare
#: ``alembic upgrade head``: an installed deployment has no ``alembic.ini``.
MIGRATE_COMMAND = "baselith db migrate"


class SchemaRevisionMismatchError(RuntimeError):
    """Startup refused: the database schema is not at the packaged head."""


def resolve_schema_check_mode(
    configured: str | None, *, is_production: bool
) -> SchemaCheckMode:
    """Effective ``DB_SCHEMA_CHECK`` mode.

    Args:
        configured: The ``DB_SCHEMA_CHECK`` value, or ``None`` when unset.
        is_production: Whether ``APP_ENV`` is production.

    Returns:
        The configured mode, or ``strict`` in production and ``off`` elsewhere
        when nothing is configured.
    """
    if configured == "strict":
        return "strict"
    if configured == "warn":
        return "warn"
    if configured == "off":
        return "off"
    return "strict" if is_production else "off"


def _read_revisions() -> tuple[str, str, bool]:
    """Return ``(current, head, ahead)``; blocking, so callers run it in a thread.

    ``ahead`` is true when the database is stamped with a revision this
    package does not know: a newer image migrated it (a rollback, or an old
    pod restarting mid rolling deploy). That is not a stale schema — new
    migrations are expand-only — so it must not stop the older code.
    """
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory
    from alembic.util import CommandError
    from sqlalchemy import create_engine

    from core.db.migration_config import build_alembic_config

    alembic_cfg = build_alembic_config()
    script = ScriptDirectory.from_config(alembic_cfg)
    head_rev: str = script.get_current_head() or "unknown"

    db_url = alembic_cfg.get_main_option("sqlalchemy.url") or (
        get_storage_config().conninfo
    )
    # Force the sync psycopg (v3) driver: only psycopg3 is installed, so a bare
    # ``postgresql://`` (defaults to psycopg2) or an async driver
    # (``+psycopg_async`` / ``+asyncpg``) would fail to import under this sync
    # ``create_engine``. ``conninfo`` carries ``connect_timeout``, so a
    # database that stops answering cannot pin this thread.
    for scheme in (
        "postgresql+psycopg_async://",
        "postgresql+asyncpg://",
        "postgresql://",
    ):
        if db_url.startswith(scheme):
            db_url = "postgresql+psycopg://" + db_url[len(scheme) :]
            break
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            current_rev: str = ctx.get_current_revision() or "none"
    finally:
        engine.dispose()
    ahead = False
    if current_rev not in ("none", head_rev):
        try:
            ahead = script.get_revision(current_rev) is None
        except CommandError:  # alembic's answer for an id it cannot resolve
            ahead = True
    return current_rev, head_rev, ahead


async def check_schema_revision(
    *, is_production: bool, postgres_reachable: bool
) -> None:
    """Compare the database revision with the packaged head per ``DB_SCHEMA_CHECK``.

    Callers invoke it only with PostgreSQL enabled.

    Args:
        is_production: Whether ``APP_ENV`` is production.
        postgres_reachable: Whether the startup probe reached PostgreSQL.

    Raises:
        SchemaRevisionMismatchError: The mode is ``strict`` and the database is
            behind the packaged head. A database *ahead* of it (stamped by a
            newer image) only warns, so a rollback can start.
    """
    mode = resolve_schema_check_mode(
        getattr(get_storage_config(), "db_schema_check", None),
        is_production=is_production,
    )
    if mode == "off":
        return
    if not postgres_reachable:
        logger.warning("Could not verify migration status: PostgreSQL unreachable")
        return
    try:
        current, head, ahead = await asyncio.to_thread(_read_revisions)
    except Exception as exc:
        logger.warning("Could not verify migration status: %s", type(exc).__name__)
        return
    if current == head:
        logger.info("✅ Startup health check: DB migrations up to date (%s)", current)
        return
    if ahead:
        logger.warning(
            "Database schema %s is newer than this package's head %s "
            "(rollback or mixed-version rollout); starting anyway",
            current,
            head,
        )
        return
    message = (
        f"Database schema is not at the packaged migration head — current: "
        f"{current}, head: {head}. Run `{MIGRATE_COMMAND}` (or "
        "`python -m core.db.migrate`) against this database, or set "
        "DB_SCHEMA_CHECK=warn if migrations deliberately run after the rollout."
    )
    if mode == "strict":
        raise SchemaRevisionMismatchError(message)
    logger.error(message)


__all__ = [
    "MIGRATE_COMMAND",
    "SchemaCheckMode",
    "SchemaRevisionMismatchError",
    "check_schema_revision",
    "resolve_schema_check_mode",
]
