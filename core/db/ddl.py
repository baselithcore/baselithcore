"""Schema-ownership policy for the shared Postgres database.

**Alembic owns every table.** A module that runs ``CREATE TABLE IF NOT EXISTS``
against the shared pool at first use forces the *runtime* role to hold DDL
privileges in production, lets the schema drift per deployment, and leaves the
table with no migration history and no rollback.

Four modules historically self-initialized their schema
(:mod:`core.a2a.task_store_postgres`, :mod:`core.orchestration.checkpoint_postgres`,
:mod:`core.prompts.store_postgres`, :mod:`core.storage.postgres`). Their tables
are now created by migrations as well, and the self-init path survives only as a
**development convenience**, gated by :func:`runtime_ddl_allowed`:

* ``DB_RUNTIME_DDL`` unset — allowed outside production, refused in production;
* ``DB_RUNTIME_DDL=true`` — always allowed (single-role local Postgres);
* ``DB_RUNTIME_DDL=false`` — never allowed.

When it is refused the store logs once and continues: the table is expected to
exist already, put there by the migrations job. A missing table then surfaces as
an ordinary query error naming the table, instead of an opaque permission error
on a ``CREATE``.

Embedded SQLite stores (``core.privacy``, ``core.incidents``,
``core.compliance``, ``core.thirdparty``, ``core.observability.audit_chain``,
``core.orchestration.checkpoint_sqlite``) are out of scope — a single-file store
has no migration job, so creating its own schema is the correct design. So is
:mod:`core.services.vectorstore.providers.pgvector_provider`, which creates one
table per *collection* on demand: that is a data-plane operation, the direct
equivalent of creating a Qdrant collection.
"""

from __future__ import annotations

from core.observability.logging import get_logger

logger = get_logger(__name__)

__all__ = [
    "RLS_PROTECTED_TABLES",
    "runtime_ddl_allowed",
    "skip_runtime_ddl",
]

#: Tables carrying a ``tenant_id`` column, protected by a row-level-security
#: policy (``migrations/versions/008_row_level_security.py``). Keep in step with
#: the migration — ``tests/unit/test_schema_ownership.py`` fails otherwise.
RLS_PROTECTED_TABLES: tuple[str, ...] = (
    "a2a_tasks",
    "agent_checkpoints",
    "agent_patterns",
    "chat_feedback",
    "feedback",
    "interactions",
)


def runtime_ddl_allowed() -> bool:
    """Whether a store may create its own Postgres schema at first use.

    Returns:
        ``True`` outside production, or when ``DB_RUNTIME_DDL`` is explicitly
        set to a truthy value; ``False`` in production by default.
    """
    from core.config import get_storage_config

    configured = get_storage_config().db_runtime_ddl
    if configured is not None:
        return configured

    from core.utils.runtime_env import is_production_env

    return not is_production_env()


def skip_runtime_ddl(store: str, tables: str) -> bool:
    """Log-and-report whether ``store`` should skip its ``CREATE TABLE`` step.

    Args:
        store: Human-readable store name, for the log line.
        tables: The table names the store would have created.

    Returns:
        ``True`` when the DDL must be skipped (the migrations job owns it).
    """
    if runtime_ddl_allowed():
        return False
    logger.debug(
        "runtime_ddl_skipped",
        extra={"store": store, "tables": tables},
    )
    return True
