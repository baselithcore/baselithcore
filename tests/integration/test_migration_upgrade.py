"""Integration test: upgrading a populated database keeps its rows and its shape.

A fresh ``alembic upgrade head`` is what every other test exercises, and it is
the one path a deployed database never takes: production upgrades *from* the
revision the previous release shipped, with rows already in the tables. This
module replays that path from every revision in the chain, not only from the
previous release's head — the release that skips a version (or restores an old
backup) upgrades from further back, and the walk costs seconds.

For each revision ``R`` below head, on a throwaway database:

1. ``upgrade R`` — the schema as some earlier release left it;
2. insert one representative row into every core table that exists at ``R``,
   using only the columns that exist at ``R``;
3. ``upgrade head``;
4. every seeded row is still there, and the schema (columns, indexes,
   constraints, row-level-security policies) is identical to a database that
   went straight to head.

Plus one round trip — ``head -> base -> head`` — so the ``downgrade()``
functions are executed rather than merely present, and a static check that
every revision has a ``downgrade()`` that either does something or says why it
deliberately does not.

What this does not repeat: the single-head check lives in
``tests/unit/core/db/test_migration_config.py`` and in
``scripts/check_distribution_artifacts.py``; the destructive-statement guard is
``scripts/check_migrations.py``. There is no autogenerate comparison because
there is no SQLAlchemy metadata to compare against — the migrations are
hand-written SQL (``target_metadata = None`` in ``env.py``).

Runs only with ``BASELITH_TEST_REAL_DB=1`` and a reachable PostgreSQL; the
user needs ``CREATEDB`` (the CI service and the compose image use a superuser).
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.integration]

VERSIONS_DIR = (
    Path(__file__).resolve().parents[2] / "core" / "db" / "migrations" / "versions"
)

#: Every row this module writes carries it in a text column.
MARKER = "upgrade-itest"

#: One representative row per core table, in insertion order (``feedback``
#: references ``interactions``). Columns a revision does not have yet are
#: dropped at insert time; the values cover every NOT NULL column without a
#: default at head. ``key`` names the column that identifies the row afterwards.
SEEDS: dict[str, dict[str, Any]] = {
    "tenants": {"key": "id", "row": {"id": MARKER, "name": "Upgrade test"}},
    "chat_feedback": {
        "key": "query",
        "row": {"query": MARKER, "answer": "a", "feedback": "positive"},
    },
    "interactions": {
        "key": "session_id",
        "row": {
            "id": "00000000-0000-0000-0000-00000000c0de",
            "session_id": MARKER,
            "metadata": '{"k": 1}',
        },
    },
    "feedback": {
        "key": "label",
        "row": {
            "id": "00000000-0000-0000-0000-00000000feed",
            "interaction_id": "00000000-0000-0000-0000-00000000c0de",
            "score": 1.0,
            "label": MARKER,
        },
    },
    "agent_patterns": {
        "key": "id",
        "row": {
            "id": MARKER,
            "fingerprint": "fp",
            "kind": "k",
            "title": "t",
            "summary": "s",
        },
    },
    "a2a_tasks": {
        "key": "task_id",
        "row": {"task_id": MARKER, "status": "done", "data": "{}", "updated_at": 1.0},
    },
    "agent_checkpoints": {"key": "run_id", "row": {"run_id": MARKER, "data": "{}"}},
    "agent_checkpoint_history": {
        "key": "run_id",
        "row": {"run_id": MARKER, "version": 1, "data": "{}"},
    },
    "prompt_versions": {
        "key": "name",
        "row": {"name": MARKER, "version": "1", "template": "t", "created_at": 1.0},
    },
    "prompt_labels": {
        "key": "name",
        "row": {"name": MARKER, "label": "prod", "version": "1"},
    },
    "tool_invocations": {
        "key": "key",
        "row": {"key": MARKER, "run_id": "r", "tool": "t"},
    },
    "webhook_endpoints": {
        "key": "id",
        "row": {
            "id": MARKER,
            "url": "https://example.test",
            "secret": "s",
            "created_at": 1.0,
        },
    },
    "webhook_deliveries": {
        "key": "id",
        "row": {
            "id": MARKER,
            "endpoint_id": MARKER,
            "event_id": "e",
            "event_type": "t",
            "url": "https://example.test",
            "created_at": 1.0,
        },
    },
}

#: Tables at head that deliberately carry no seed row.
UNSEEDED: frozenset[str] = frozenset({"alembic_version"})


# ---------------------------------------------------------------------------
# Revision graph (no database needed)
# ---------------------------------------------------------------------------


def _script() -> Any:
    from alembic.script import ScriptDirectory

    from core.db.migration_config import build_alembic_config

    return ScriptDirectory.from_config(build_alembic_config())


def _revisions_below_head() -> list[str]:
    """Every revision except head, oldest first."""
    revisions = [rev.revision for rev in _script().walk_revisions()]
    return list(reversed(revisions[1:]))


def _downgrade_is_intentional(path: Path) -> str | None:
    """``None`` when ``downgrade()`` is acceptable, else the reason it is not.

    A body that does nothing (``pass`` / ``...`` / a docstring) is accepted only
    when a comment inside the function says why: a migration that adopts
    data-bearing tables cannot drop them on the way down, and the comment is
    the only record of that decision.
    """
    source = path.read_text(encoding="utf-8")
    func = next(
        (
            node
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef) and node.name == "downgrade"
        ),
        None,
    )
    if func is None:
        return "no downgrade() function"
    effective = [
        stmt
        for stmt in func.body
        if not isinstance(stmt, ast.Pass)
        and not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant))
    ]
    if effective:
        return None
    lines = source.splitlines()[func.lineno - 1 : func.end_lineno]
    if any(line.strip().startswith("#") for line in lines):
        return None
    return "downgrade() does nothing and no comment inside it says why"


def test_every_revision_has_an_intentional_downgrade() -> None:
    problems = {
        path.name: reason
        for path in sorted(VERSIONS_DIR.glob("*.py"))
        if (reason := _downgrade_is_intentional(path)) is not None
    }
    assert not problems, problems


def test_seed_table_covers_every_shipped_table(head_schema: dict[str, Any]) -> None:
    """A new table must get a seed row, or this upgrade test silently skips it."""
    missing = set(head_schema["tables"]) - set(SEEDS) - UNSEEDED
    assert not missing, f"add a row for {sorted(missing)} to SEEDS"


# ---------------------------------------------------------------------------
# Database plumbing
# ---------------------------------------------------------------------------


def _conninfo(dbname: str | None = None) -> str:
    from core.config import get_storage_config

    config = get_storage_config()
    return (
        f"postgresql://{config.db_user}:{config.db_password.get_secret_value()}"
        f"@{config.db_host}:{config.db_port}/{dbname or config.db_name}"
    )


def _real_db() -> bool:
    if os.environ.get("BASELITH_TEST_REAL_DB", "").strip().lower() not in {
        "1",
        "true",
        "yes",
    }:
        return False
    try:
        import psycopg

        with psycopg.connect(_conninfo(), connect_timeout=3) as conn:
            row = conn.execute("SELECT 1").fetchone()
        return bool(row and row[0] == 1)
    except Exception:
        return False


@pytest.fixture(scope="module")
def real_db() -> None:
    if not _real_db():
        pytest.skip("set BASELITH_TEST_REAL_DB=1 and run PostgreSQL")


@pytest.fixture
def scratch_db(real_db: None) -> Iterator[str]:
    yield from _scratch_db()


def _scratch_db() -> Iterator[str]:
    import psycopg

    name = f"bl_upgrade_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(_conninfo(), autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    try:
        yield name
    finally:
        with psycopg.connect(_conninfo(), autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _alembic(dbname: str, *steps: tuple[str, str]) -> None:
    """Run ``(command, revision)`` steps against ``dbname`` in a child process.

    A child, because ``env.py`` resolves the URL from the cached storage
    settings and configures logging: a fresh interpreter per database is the
    only way to point it elsewhere without reaching into those caches.
    """
    script = (
        "import sys\n"
        "from alembic import command\n"
        "from core.db.migration_config import build_alembic_config\n"
        "cfg = build_alembic_config()\n"
        "args = sys.argv[1:]\n"
        "for cmd, rev in zip(args[::2], args[1::2]):\n"
        "    getattr(command, cmd)(cfg, rev)\n"
    )
    env = {**os.environ, "DB_NAME": dbname}
    env.pop("DATABASE_URL", None)
    argv = [arg for step in steps for arg in step]
    result = subprocess.run(
        [sys.executable, "-c", script, *argv],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, (
        f"alembic {steps} failed on {dbname}:\n{result.stdout}\n{result.stderr}"
    )


def _schema(dbname: str) -> dict[str, Any]:
    """Everything a migration shapes, in a comparable form."""
    import psycopg

    queries = {
        "columns": """
            SELECT table_name, column_name, data_type, is_nullable,
                   COALESCE(column_default, '')
            FROM information_schema.columns WHERE table_schema = 'public'
        """,
        "indexes": """
            SELECT tablename, indexname, indexdef
            FROM pg_indexes WHERE schemaname = 'public'
        """,
        "constraints": """
            SELECT rel.relname, con.conname, pg_get_constraintdef(con.oid)
            FROM pg_constraint con
            JOIN pg_class rel ON rel.oid = con.conrelid
            JOIN pg_namespace ns ON ns.oid = rel.relnamespace
            WHERE ns.nspname = 'public'
        """,
        "policies": """
            SELECT tablename, policyname, permissive, cmd,
                   COALESCE(qual, ''), COALESCE(with_check, '')
            FROM pg_policies WHERE schemaname = 'public'
        """,
        "rls": """
            SELECT relname, relrowsecurity, relforcerowsecurity
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind = 'r'
        """,
    }
    with psycopg.connect(_conninfo(dbname)) as conn:
        shape: dict[str, Any] = {
            key: sorted(tuple(row) for row in conn.execute(sql).fetchall())
            for key, sql in queries.items()
        }
    shape["tables"] = sorted({row[0] for row in shape["rls"]})
    return shape


@pytest.fixture(scope="module")
def head_schema(real_db: None) -> Iterator[dict[str, Any]]:
    """The schema of a database that went straight to head."""
    for name in _scratch_db():
        _alembic(name, ("upgrade", "head"))
        yield _schema(name)


def _columns(conn: Any, table: str) -> set[str]:
    rows = conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s",
        (table,),
    ).fetchall()
    return {row[0] for row in rows}


def _seed(dbname: str) -> list[str]:
    """Insert one row per existing core table; return the tables seeded."""
    import psycopg
    from psycopg import sql

    seeded: list[str] = []
    with psycopg.connect(_conninfo(dbname)) as conn:
        for table, spec in SEEDS.items():
            present = _columns(conn, table)
            if not present:
                continue
            row = {col: val for col, val in spec["row"].items() if col in present}
            conn.execute(
                sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                    sql.Identifier(table),
                    sql.SQL(", ").join(map(sql.Identifier, row)),
                    sql.SQL(", ").join(sql.Placeholder() * len(row)),
                ),
                list(row.values()),
            )
            seeded.append(table)
    return seeded


def _surviving(dbname: str, tables: list[str]) -> list[str]:
    import psycopg
    from psycopg import sql

    found: list[str] = []
    with psycopg.connect(_conninfo(dbname)) as conn:
        for table in tables:
            key = SEEDS[table]["key"]
            row = conn.execute(
                sql.SQL("SELECT count(*) FROM {} WHERE {}::text = %s").format(
                    sql.Identifier(table), sql.Identifier(key)
                ),
                (str(SEEDS[table]["row"][key]),),
            ).fetchone()
            if row and row[0] == 1:
                found.append(table)
    return found


# ---------------------------------------------------------------------------
# The upgrade path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("start", _revisions_below_head())
def test_upgrade_from_revision_keeps_rows_and_converges(
    start: str, scratch_db: str, head_schema: dict[str, Any]
) -> None:
    _alembic(scratch_db, ("upgrade", start))
    seeded = _seed(scratch_db)
    assert seeded, f"no core table exists at {start}"

    _alembic(scratch_db, ("upgrade", "head"))

    assert _surviving(scratch_db, seeded) == seeded
    upgraded = _schema(scratch_db)
    for key in ("columns", "indexes", "constraints", "policies", "rls"):
        assert upgraded[key] == head_schema[key], f"{key} differ after {start} -> head"


def test_downgrade_to_base_and_back_converges(
    scratch_db: str, head_schema: dict[str, Any]
) -> None:
    _alembic(
        scratch_db, ("upgrade", "head"), ("downgrade", "base"), ("upgrade", "head")
    )
    assert _schema(scratch_db) == head_schema
