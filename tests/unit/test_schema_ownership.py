"""Alembic owns every Postgres table the runtime creates.

A module that runs ``CREATE TABLE IF NOT EXISTS`` on the shared Postgres pool
at first use makes the application role need DDL privileges in production, and
leaves that table with no migration history and no rollback. These tests pin
the invariant both ways:

* every Postgres table a ``core/`` module self-initializes must also be created
  by a migration under ``migrations/versions/`` — otherwise a deployment that
  runs migrations as a pre-deploy job and revokes DDL from the runtime role
  crashes the first time that feature is touched;
* the reverse direction is deliberately *not* enforced: migrations may own
  tables no module self-initializes (that is the goal state).

SQLite file stores (``core.privacy``, ``core.incidents``, ``core.compliance``,
``core.thirdparty``, ``core.observability.audit_chain``,
``core.orchestration.checkpoint_sqlite``) are out of scope: they are embedded
single-file stores with no migration job, where self-initialization is the
correct design. So is the pgvector provider, which creates one table per
*collection* on demand — a data-plane operation, not a deploy-time schema.
"""

from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "migrations" / "versions"

#: Modules that self-initialize schema on the shared **Postgres** pool.
POSTGRES_SELF_INIT_MODULES = (
    "core/a2a/task_store_postgres.py",
    "core/orchestration/checkpoint_postgres.py",
    "core/prompts/store_postgres.py",
    "core/storage/postgres.py",
)

_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+([a-z_][a-z0-9_]*)", re.IGNORECASE
)


def _tables_in(text: str) -> set[str]:
    """Table names actually created by SQL in ``text``.

    Prose that merely mentions ``CREATE TABLE IF NOT EXISTS`` without naming a
    table (docstrings explaining this very policy) does not match: the regex
    requires an identifier.
    """
    return {m.group(1).lower() for m in _CREATE_TABLE_RE.finditer(text)}


def _table_bodies(text: str) -> dict[str, str]:
    """``{table: column list}`` for every ``CREATE TABLE`` in ``text``.

    The body is read by scanning balanced parentheses from the opening one, so
    one table's definition can never bleed into the next — the statements carry
    no terminating semicolon inside ``op.execute`` blocks.
    """
    bodies: dict[str, str] = {}
    for match in _CREATE_TABLE_RE.finditer(text):
        start = text.find("(", match.end())
        if start == -1:
            continue
        depth = 0
        for index in range(start, len(text)):
            if text[index] == "(":
                depth += 1
            elif text[index] == ")":
                depth -= 1
                if depth == 0:
                    bodies[match.group(1).lower()] = text[start + 1 : index]
                    break
    return bodies


def tables_created_by_migrations() -> set[str]:
    """Every table name any migration creates."""
    tables: set[str] = set()
    for path in sorted(MIGRATIONS_DIR.glob("*.py")):
        tables |= _tables_in(path.read_text(encoding="utf-8"))
    return tables


def tables_created_at_runtime() -> dict[str, set[str]]:
    """``{module path: tables it self-initializes}`` for the Postgres modules."""
    found: dict[str, set[str]] = {}
    for relative in POSTGRES_SELF_INIT_MODULES:
        tables = _tables_in((REPO_ROOT / relative).read_text(encoding="utf-8"))
        if tables:
            found[relative] = tables
    return found


def test_every_runtime_postgres_table_has_a_migration() -> None:
    owned = tables_created_by_migrations()
    orphans = {
        module: sorted(tables - owned)
        for module, tables in tables_created_at_runtime().items()
        if tables - owned
    }

    assert not orphans, (
        "these Postgres tables are created at runtime but by no migration, so a "
        "deployment whose runtime role has no DDL rights fails on first use: "
        f"{orphans}"
    )


def test_no_new_module_self_initializes_postgres_schema() -> None:
    """The self-init list is a closed set: a new entry needs a migration too."""
    suspects: list[str] = []
    for path in sorted((REPO_ROOT / "core").rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if not _tables_in(text):
            continue
        if "sqlite3" in text:
            continue  # embedded file store, no migration job
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative in POSTGRES_SELF_INIT_MODULES:
            continue
        if relative.endswith("pgvector_provider.py"):
            continue  # one table per collection, created on demand
        suspects.append(relative)

    assert not suspects, (
        "new module(s) self-initialize Postgres schema; add the table to a "
        f"migration and list the module in POSTGRES_SELF_INIT_MODULES: {suspects}"
    )


def test_runtime_ddl_is_gated_by_configuration() -> None:
    """Each self-init call site must honour the ``DB_RUNTIME_DDL`` gate."""
    from core.db.ddl import runtime_ddl_allowed, skip_runtime_ddl

    assert callable(runtime_ddl_allowed)
    assert callable(skip_runtime_ddl)
    for relative in POSTGRES_SELF_INIT_MODULES:
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        assert "from core.db.ddl import" in text, relative
        assert "skip_runtime_ddl(" in text, relative


def test_rls_migration_covers_every_tenant_scoped_table() -> None:
    """Every migrated table with a ``tenant_id`` column gets an RLS policy."""
    from core.db.ddl import RLS_PROTECTED_TABLES

    migration_text = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(MIGRATIONS_DIR.glob("*.py"))
    )
    tenant_scoped = {
        table
        for table, body in _table_bodies(migration_text).items()
        if re.search(r"\btenant_id\b", body)
    }

    assert tenant_scoped, "no tenant-scoped table found — the regex stopped matching"
    assert tenant_scoped <= set(RLS_PROTECTED_TABLES), (
        "tenant-scoped tables without an RLS policy: "
        f"{sorted(tenant_scoped - set(RLS_PROTECTED_TABLES))}"
    )
    assert "ENABLE ROW LEVEL SECURITY" in migration_text


def test_rls_table_list_matches_the_migration() -> None:
    """``core.db.ddl`` and the migration must name the same tables."""
    from core.db.ddl import RLS_PROTECTED_TABLES

    spec = importlib.util.spec_from_file_location(
        "rls_migration", MIGRATIONS_DIR / "008_row_level_security.py"
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    assert migration.TENANT_SCOPED_TABLES == RLS_PROTECTED_TABLES


def test_ddl_module_has_no_core_to_plugin_import() -> None:
    """``core.db.ddl`` stays domain-agnostic (Sacred Core)."""
    tree = ast.parse((REPO_ROOT / "core" / "db" / "ddl.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("plugins"), node.module
