"""The ``system`` tenant must be able to see and write every tenant-scoped row.

Migration 008 wrote ``USING (tenant_id = COALESCE(current_setting(...),
'default'))`` before the ``system`` identity existed. Every maintenance path that
now binds it — crash recovery, GDPR purge, boot, the worker, the CLI — is
cross-tenant by construction, so against that policy a least-privilege runtime
role sees *nothing*: the recovery sweep discovers zero interrupted runs, and
``purge_tenant_data`` issues its ``DELETE`` against rows the ``USING`` clause
hides and reports a truthful ``0`` that reads as a successful erasure.

Migration 010 widens the predicate. These tests pin the three things that can be
checked without a database: the revision chain, that the widening is exactly the
session-side escape (so ordinary tenants are unchanged), and that ``downgrade``
puts 008/009's predicate back verbatim.

**Not covered here** — and stated plainly rather than implied: nothing in this
file runs SQL against Postgres. That the server parses the DDL, and that a
``NOSUPERUSER NOBYPASSRLS`` role actually observes this truth table, need a live
database (`docker compose up -d postgres`, then `alembic upgrade head` /
`downgrade -1`); ``tests/integration/test_rls_tenant_isolation.py`` is where
that is checked. The truth table below is evaluated by SQLite, which exercises
the boolean logic of the predicate but not Postgres' RLS engine.
"""

from __future__ import annotations

import ast
import importlib.util
import sqlite3
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "migrations" / "versions"
MIGRATION_PATH = MIGRATIONS_DIR / "010_system_tenant_rls_exemption.py"

pytestmark = [pytest.mark.unit]


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(f"mig_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _later_migrations() -> list:
    """Migrations after 010 that declare tenant-scoped tables of their own."""
    found = []
    for path in sorted(MIGRATIONS_DIR.glob("*.py")):
        prefix = path.name.split("_", 1)[0]
        if prefix.isdigit() and int(prefix) > 10:
            module = _load(path)
            if getattr(module, "TENANT_SCOPED_TABLES", ()):
                found.append(module)
    return found


@pytest.fixture(scope="module")
def migration():
    return _load(MIGRATION_PATH)


class _Recorder:
    """Stands in for ``alembic.op``, capturing the SQL a migration emits."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, sql: str) -> None:
        self.statements.append(" ".join(str(sql).split()))


def _emit(migration, monkeypatch, direction: str) -> list[str]:
    recorder = _Recorder()
    monkeypatch.setattr(migration, "op", recorder)
    getattr(migration, direction)()
    return recorder.statements


class TestRevisionChain:
    def test_it_follows_the_previous_head(self, migration):
        assert migration.revision == "010_system_tenant_rls"
        assert migration.down_revision == "009_tool_invocations"

    def test_there_is_exactly_one_head(self):
        """Two heads is a merge conflict that only shows up as a boot failure."""
        revisions: set[str] = set()
        parents: set[str] = set()
        for path in MIGRATIONS_DIR.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.AnnAssign) or not isinstance(
                    node.target, ast.Name
                ):
                    continue
                value = getattr(node.value, "value", None)
                if node.target.id == "revision" and isinstance(value, str):
                    revisions.add(value)
                elif node.target.id == "down_revision" and isinstance(value, str):
                    parents.add(value)

        assert len(revisions - parents) == 1

    def test_it_never_edits_a_shipped_migration(self):
        """008 and 009 keep their original, un-widened predicate — someone's
        database has already recorded them as applied."""
        for name in ("008_row_level_security.py", "009_tool_invocations.py"):
            text = (MIGRATIONS_DIR / name).read_text(encoding="utf-8")
            assert "'system'" not in text


class TestConstantsStayInStep:
    def test_the_system_tenant_id_matches_the_runtime(self, migration):
        from core.db.session_setup import SYSTEM_TENANT_ID

        assert migration.SYSTEM_TENANT_ID == SYSTEM_TENANT_ID

    def test_it_covers_every_protected_table(self, migration):
        """010 widened every table that existed; later ones are born widened."""
        from core.db.ddl import RLS_PROTECTED_TABLES

        later = set().union(*(m.TENANT_SCOPED_TABLES for m in _later_migrations()))
        assert set(migration.TENANT_SCOPED_TABLES) == set(RLS_PROTECTED_TABLES) - later

    def test_later_migrations_keep_the_system_escape(self):
        """A table created after 010 must carry the widened predicate itself."""
        for later in _later_migrations():
            assert later.SYSTEM_TENANT_ID == "system"
            assert "OR" in later._PREDICATE and "'system'" in later._PREDICATE

    def test_the_policy_name_matches_008(self, migration):
        eight = _load(MIGRATIONS_DIR / "008_row_level_security.py")
        assert migration.POLICY_NAME == eight.POLICY_NAME
        assert migration._TENANT_EXPR == eight._TENANT_EXPR


class TestEmittedSql:
    def test_upgrade_rewrites_the_policy_on_every_table(self, migration, monkeypatch):
        statements = _emit(migration, monkeypatch, "upgrade")

        for table in migration.TENANT_SCOPED_TABLES:
            assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in statements
            assert (
                f"DROP POLICY IF EXISTS {migration.POLICY_NAME} ON {table}"
                in statements
            )
            create = next(
                s
                for s in statements
                if s.startswith("CREATE POLICY") and f"ON {table} " in s
            )
            assert "USING (tenant_id =" in create
            assert "WITH CHECK (tenant_id =" in create
            assert create.count(f"= '{migration.SYSTEM_TENANT_ID}'") == 2

    def test_downgrade_restores_008s_predicate_verbatim(self, migration, monkeypatch):
        eight = _load(MIGRATIONS_DIR / "008_row_level_security.py")
        expected = (
            f"USING (tenant_id = {eight._TENANT_EXPR}) "
            f"WITH CHECK (tenant_id = {eight._TENANT_EXPR})"
        )
        expected = " ".join(expected.split())

        for statement in _emit(migration, monkeypatch, "downgrade"):
            if statement.startswith("CREATE POLICY"):
                assert statement.endswith(expected)
                assert migration.SYSTEM_TENANT_ID not in statement

    def test_neither_direction_drops_anything_but_the_policy(
        self, migration, monkeypatch
    ):
        for direction in ("upgrade", "downgrade"):
            for statement in _emit(migration, monkeypatch, direction):
                assert "DROP TABLE" not in statement.upper()
                assert "DROP COLUMN" not in statement.upper()


class TestTruthTable:
    """Evaluate the predicate's boolean logic, with the GUC lookup substituted.

    SQLite is only a boolean evaluator here — it knows nothing about RLS. What
    this pins is the property the review asked for: the escape is a comparison on
    the *session*, so widening it for ``system`` cannot widen anything for an
    ordinary tenant, in ``USING`` or in ``WITH CHECK``.
    """

    @staticmethod
    def _evaluate(predicate: str, *, session: str, row_tenant: str) -> bool:
        sql = predicate.replace(
            "COALESCE(current_setting('app.tenant_id', true), 'default')",
            f"'{session}'",
        ).replace("tenant_id", f"'{row_tenant}'")
        conn = sqlite3.connect(":memory:")
        try:
            return bool(conn.execute(f"SELECT {sql}").fetchone()[0])
        finally:
            conn.close()

    @pytest.mark.parametrize(
        ("session", "row_tenant", "expected"),
        [
            ("acme", "acme", True),
            ("acme", "globex", False),  # ordinary tenants are NOT widened
            ("acme", "system", False),
            ("system", "acme", True),  # the maintenance identity sees everything
            ("system", "globex", True),
            ("default", "globex", False),
        ],
    )
    def test_widened_predicate(self, migration, session, row_tenant, expected):
        assert (
            self._evaluate(migration._PREDICATE, session=session, row_tenant=row_tenant)
            is expected
        )

    @pytest.mark.parametrize(
        ("session", "row_tenant", "expected"),
        [
            ("acme", "acme", True),
            ("system", "acme", False),  # the defect 010 fixes
        ],
    )
    def test_the_previous_predicate_hid_foreign_rows_from_system(
        self, migration, session, row_tenant, expected
    ):
        assert (
            self._evaluate(
                migration._PREVIOUS_PREDICATE, session=session, row_tenant=row_tenant
            )
            is expected
        )
