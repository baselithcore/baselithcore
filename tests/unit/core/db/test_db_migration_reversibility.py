"""Every packaged Alembic revision, driven against a recording ``op``.

Real upgrade/downgrade runs only happen against PostgreSQL in CI. These tests
pin what can be checked offline: the revisions form one linear chain, every
upgrade is idempotent (safe to re-run against a database a runtime store
already touched), and every downgrade removes exactly what its upgrade added,
in an order Postgres accepts — and nothing else.

Two revisions are deliberately not symmetric and are pinned separately:
``007`` adopts tables that runtime stores used to create (dropping them on
downgrade would destroy data that predates the migration), and ``010`` rewrites
the RLS policy in place (``tests/unit/test_system_tenant_rls_policy.py``).
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType

import pytest

VERSIONS = (
    Path(__file__).resolve().parents[4]
    / "baselith"
    / "core"
    / "db"
    / "migrations"
    / "versions"
)
ADOPTION_ONLY = "007_adopt_runtime_tables"
POLICY_REWRITE = "010_system_tenant_rls"

_CREATE_TABLE = re.compile(r"CREATE TABLE IF NOT EXISTS (\w+)")
_CREATE_INDEX = re.compile(r"CREATE INDEX IF NOT EXISTS (\w+) ON (\w+)")
_CREATE_POLICY = re.compile(r"CREATE POLICY (\w+) ON (\w+)")
_ENABLE_RLS = re.compile(r"ALTER TABLE (\w+) ENABLE ROW LEVEL SECURITY")
_ADD_COLUMN = re.compile(r"ALTER TABLE (\w+) ADD COLUMN IF NOT EXISTS (\w+)")
_DROP_TABLE = re.compile(r"DROP TABLE IF EXISTS (\w+)")
_DROP_INDEX = re.compile(r"DROP INDEX IF EXISTS (\w+)")
_DROP_POLICY = re.compile(r"DROP POLICY IF EXISTS (\w+) ON (\w+)")
_DISABLE_RLS = re.compile(r"ALTER TABLE (\w+) DISABLE ROW LEVEL SECURITY")
_DROP_COLUMN = re.compile(r"ALTER TABLE (\w+) DROP COLUMN IF EXISTS (\w+)")


class _RecordingOp:
    """Stands in for ``alembic.op``; refuses any operation but ``execute``."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: str) -> None:
        self.statements.append(" ".join(str(statement).split()))

    def __getattr__(self, name: str):
        raise AssertionError(f"migration used op.{name}, which this test cannot vet")


def _load(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"revcheck_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(module: ModuleType, direction: str, monkeypatch) -> list[str]:
    recorder = _RecordingOp()
    monkeypatch.setattr(module, "op", recorder)
    getattr(module, direction)()
    return recorder.statements


MIGRATIONS = [_load(p) for p in sorted(VERSIONS.glob("[0-9]*.py"))]
SYMMETRIC = [m for m in MIGRATIONS if m.revision not in {ADOPTION_ONLY, POLICY_REWRITE}]


def _ids(modules: list[ModuleType]) -> list[str]:
    return [m.revision for m in modules]


# --------------------------------------------------------------------------- #
# Revision chain
# --------------------------------------------------------------------------- #


def test_revisions_form_one_linear_chain() -> None:
    by_revision = {m.revision: m for m in MIGRATIONS}
    assert len(by_revision) == len(MIGRATIONS), "duplicate revision id"

    roots = [m.revision for m in MIGRATIONS if m.down_revision is None]
    assert roots == ["001_initial_schema"]

    parents = [m.down_revision for m in MIGRATIONS if m.down_revision is not None]
    assert len(parents) == len(set(parents)), "two revisions share a parent (branch)"
    assert set(parents) <= set(by_revision), "a down_revision points nowhere"

    # Walk from the single head back to the root: every revision is visited.
    (head,) = set(by_revision) - set(parents)
    seen: list[str] = []
    cursor: str | None = head
    while cursor is not None:
        seen.append(cursor)
        cursor = by_revision[cursor].down_revision
    assert sorted(seen) == sorted(by_revision)
    assert seen[-1] == "001_initial_schema"


@pytest.mark.parametrize("module", MIGRATIONS, ids=_ids(MIGRATIONS))
def test_revision_metadata_is_plain(module) -> None:
    assert module.branch_labels is None
    assert module.depends_on is None
    # Alembic's version_num column is VARCHAR(32).
    assert len(module.revision) <= 32


# --------------------------------------------------------------------------- #
# Upgrade idempotency
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("module", MIGRATIONS, ids=_ids(MIGRATIONS))
def test_upgrade_is_idempotent(module, monkeypatch) -> None:
    statements = _run(module, "upgrade", monkeypatch)
    assert statements, "an upgrade that emits nothing is dead weight"
    for i, stmt in enumerate(statements):
        if stmt.startswith(("CREATE TABLE", "CREATE INDEX")):
            assert "IF NOT EXISTS" in stmt, stmt
        if "ADD COLUMN" in stmt:
            assert "ADD COLUMN IF NOT EXISTS" in stmt, stmt
        policy = _CREATE_POLICY.search(stmt)
        if policy:
            name, table = policy.groups()
            # CREATE POLICY has no IF NOT EXISTS: it must be preceded by a drop.
            assert f"DROP POLICY IF EXISTS {name} ON {table}" in statements[:i]


# --------------------------------------------------------------------------- #
# Downgrade reverts exactly what upgrade created
# --------------------------------------------------------------------------- #


def _created(statements: list[str]) -> dict[str, set]:
    found: dict[str, set] = {
        "tables": set(),
        "indexes": set(),
        "policies": set(),
        "rls": set(),
        "columns": set(),
    }
    index_tables: dict[str, str] = {}
    for stmt in statements:
        found["tables"].update(_CREATE_TABLE.findall(stmt))
        for name, table in _CREATE_INDEX.findall(stmt):
            found["indexes"].add(name)
            index_tables[name] = table
        found["policies"].update(_CREATE_POLICY.findall(stmt))
        found["rls"].update(_ENABLE_RLS.findall(stmt))
        found["columns"].update(_ADD_COLUMN.findall(stmt))
    found["index_tables"] = set(index_tables.items())
    return found


def _dropped(statements: list[str]) -> dict[str, set]:
    found: dict[str, set] = {
        "tables": set(),
        "indexes": set(),
        "policies": set(),
        "rls": set(),
        "columns": set(),
    }
    for stmt in statements:
        found["tables"].update(_DROP_TABLE.findall(stmt))
        found["indexes"].update(_DROP_INDEX.findall(stmt))
        found["policies"].update(_DROP_POLICY.findall(stmt))
        found["rls"].update(_DISABLE_RLS.findall(stmt))
        found["columns"].update(_DROP_COLUMN.findall(stmt))
    return found


@pytest.mark.parametrize("module", SYMMETRIC, ids=_ids(SYMMETRIC))
def test_downgrade_reverts_everything_upgrade_created(module, monkeypatch) -> None:
    created = _created(_run(module, "upgrade", monkeypatch))
    dropped = _dropped(_run(module, "downgrade", monkeypatch))

    assert dropped["tables"] == created["tables"]
    assert dropped["columns"] == created["columns"]
    for name, table in created["index_tables"]:
        # An index goes with its table if the downgrade drops that table.
        assert name in dropped["indexes"] or table in dropped["tables"], name
    for policy in created["policies"]:
        assert policy in dropped["policies"] or policy[1] in dropped["tables"]
    for table in created["rls"]:
        assert table in dropped["rls"] or table in dropped["tables"], table


@pytest.mark.parametrize("module", SYMMETRIC, ids=_ids(SYMMETRIC))
def test_downgrade_drops_nothing_upgrade_did_not_create(module, monkeypatch) -> None:
    created = _created(_run(module, "upgrade", monkeypatch))
    dropped = _dropped(_run(module, "downgrade", monkeypatch))

    assert dropped["indexes"] <= created["indexes"]
    assert dropped["tables"] <= created["tables"]
    assert dropped["policies"] <= created["policies"]
    assert dropped["rls"] <= created["rls"]


@pytest.mark.parametrize("module", SYMMETRIC, ids=_ids(SYMMETRIC))
def test_downgrade_never_touches_a_table_it_already_dropped(
    module, monkeypatch
) -> None:
    gone: set[str] = set()
    for stmt in _run(module, "downgrade", monkeypatch):
        referenced = set(re.findall(r"\b(?:ON|TABLE)\s+(\w+)", stmt))
        referenced.discard("IF")
        assert not (referenced & gone), f"{stmt!r} after dropping {gone}"
        gone.update(_DROP_TABLE.findall(stmt))


def test_downgrade_drops_the_child_table_before_its_parent(monkeypatch) -> None:
    (module,) = [m for m in MIGRATIONS if m.revision == "002b_interactions_feedback"]
    statements = _run(module, "downgrade", monkeypatch)
    assert statements == [
        "DROP TABLE IF EXISTS feedback",  # carries the FK to interactions
        "DROP TABLE IF EXISTS interactions",
    ]


def test_adoption_revision_is_irreversible_by_design(monkeypatch) -> None:
    """007 adopts tables that may already hold production data."""
    (module,) = [m for m in MIGRATIONS if m.revision == ADOPTION_ONLY]
    upgrade = _run(module, "upgrade", monkeypatch)

    assert _run(module, "downgrade", monkeypatch) == []
    created = _created(upgrade)
    assert {
        "a2a_tasks",
        "agent_checkpoints",
        "agent_checkpoint_history",
        "prompt_versions",
        "prompt_labels",
    } <= created["tables"]
    assert ("interactions", "tenant_id") in created["columns"]
    assert ("feedback", "tenant_id") in created["columns"]
    assert not any(s.startswith(("DROP", "DELETE", "TRUNCATE")) for s in upgrade)
