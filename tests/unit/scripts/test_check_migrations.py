"""The destructive-migration gate.

``DROP TABLE``/``DROP COLUMN`` in ``upgrade()`` is data loss the moment the
migration runs — no rollback recovers rows Postgres has already dropped. The
same statement in ``downgrade()`` is the normal, expected inverse of a create.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.check_migrations import (
    ALLOW_MARKER,
    find_drops,
    scan,
)

pytestmark = [pytest.mark.unit]

REPO_ROOT = Path(__file__).resolve().parents[3]


def _write(tmp_path: Path, source: str, name: str = "010_x.py") -> Path:
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return path


class TestFindDrops:
    def test_raw_sql_drop_table_in_upgrade_is_flagged(self, tmp_path):
        path = _write(
            tmp_path,
            'def upgrade():\n    op.execute("DROP TABLE users")\n',
        )
        (finding,) = find_drops(path)
        assert finding.line == 2
        assert "DROP TABLE" in finding.statement

    def test_raw_sql_drop_column_in_upgrade_is_flagged(self, tmp_path):
        path = _write(
            tmp_path,
            'def upgrade():\n    op.execute("ALTER TABLE t DROP COLUMN c")\n',
        )
        assert len(find_drops(path)) == 1

    def test_alembic_drop_table_helper_is_flagged(self, tmp_path):
        path = _write(tmp_path, 'def upgrade():\n    op.drop_table("users")\n')
        (finding,) = find_drops(path)
        assert "drop_table" in finding.statement

    def test_alembic_drop_column_helper_is_flagged(self, tmp_path):
        path = _write(tmp_path, 'def upgrade():\n    op.drop_column("t", "c")\n')
        assert len(find_drops(path)) == 1

    def test_case_is_ignored(self, tmp_path):
        path = _write(tmp_path, 'def upgrade():\n    op.execute("drop table users")\n')
        assert len(find_drops(path)) == 1

    def test_drops_in_downgrade_are_ignored(self, tmp_path):
        path = _write(
            tmp_path,
            "def upgrade():\n"
            '    op.execute("CREATE TABLE users (id int)")\n'
            "\n"
            "def downgrade():\n"
            '    op.execute("DROP TABLE users")\n'
            '    op.drop_column("t", "c")\n',
        )
        assert find_drops(path) == []

    def test_creates_are_not_flagged(self, tmp_path):
        path = _write(
            tmp_path,
            "def upgrade():\n"
            '    op.execute("CREATE INDEX IF NOT EXISTS i ON t (c)")\n'
            '    op.create_table("t")\n',
        )
        assert find_drops(path) == []

    def test_a_drop_in_a_helper_called_from_upgrade_is_flagged(self, tmp_path):
        """The regression: scanning only the ``upgrade`` body let a drop hide
        one call away from it."""
        path = _write(
            tmp_path,
            "def _cleanup():\n"
            '    op.drop_table("users")\n'
            "\n"
            "def upgrade():\n"
            "    _cleanup()\n",
        )
        (finding,) = find_drops(path)
        assert finding.line == 2

    def test_a_module_level_drop_is_flagged(self, tmp_path):
        path = _write(tmp_path, 'op.execute("DROP TABLE users")\n')
        assert len(find_drops(path)) == 1

    def test_a_helper_drop_can_still_be_marked(self, tmp_path):
        path = _write(
            tmp_path,
            "def _cleanup():\n"
            f'    op.drop_table("users")  # {ALLOW_MARKER}\n'
            "\n"
            "def upgrade():\n"
            "    _cleanup()\n",
        )
        assert find_drops(path) == []

    def test_a_nested_downgrade_helper_is_still_exempt(self, tmp_path):
        path = _write(
            tmp_path,
            "def downgrade():\n"
            "    def _inner():\n"
            '        op.drop_table("users")\n'
            "    _inner()\n",
        )
        assert find_drops(path) == []

    def test_prose_mentioning_a_drop_is_not_flagged(self, tmp_path):
        """A docstring is documentation, not a statement that runs."""
        path = _write(
            tmp_path,
            '"""This migration does not DROP TABLE anything."""\n'
            "def upgrade():\n"
            '    """No DROP COLUMN here either."""\n'
            '    op.execute("CREATE TABLE t (id int)")\n',
        )
        assert find_drops(path) == []

    def test_drop_index_is_not_flagged(self, tmp_path):
        """Rebuildable objects are not data loss; only tables and columns are."""
        path = _write(tmp_path, 'def upgrade():\n    op.drop_index("idx_t_c")\n')
        assert find_drops(path) == []

    def test_multiple_drops_are_all_reported(self, tmp_path):
        path = _write(
            tmp_path,
            "def upgrade():\n"
            '    op.execute("DROP TABLE a")\n'
            '    op.drop_column("b", "c")\n',
        )
        assert len(find_drops(path)) == 2

    def test_unparsable_file_is_reported_not_swallowed(self, tmp_path):
        path = _write(tmp_path, "def upgrade(:\n")
        (finding,) = find_drops(path)
        assert "syntax" in finding.statement.lower()


class TestAllowMarker:
    def test_marker_on_the_same_line_allows_the_drop(self, tmp_path):
        path = _write(
            tmp_path,
            f'def upgrade():\n    op.drop_table("users")  # {ALLOW_MARKER}\n',
        )
        assert find_drops(path) == []

    def test_marker_on_the_preceding_line_allows_the_drop(self, tmp_path):
        path = _write(
            tmp_path,
            f"def upgrade():\n    # {ALLOW_MARKER}: table was never populated\n"
            '    op.drop_table("users")\n',
        )
        assert find_drops(path) == []

    def test_marker_inside_a_multiline_statement_allows_the_drop(self, tmp_path):
        path = _write(
            tmp_path,
            "def upgrade():\n"
            "    op.execute(\n"
            f"        # {ALLOW_MARKER}\n"
            '        "DROP TABLE users"\n'
            "    )\n",
        )
        assert find_drops(path) == []

    def test_marker_elsewhere_in_the_file_does_not_allow_the_drop(self, tmp_path):
        path = _write(
            tmp_path,
            f"# {ALLOW_MARKER}\n"
            "def upgrade():\n"
            '    op.execute("CREATE TABLE t (id int)")\n'
            '    op.execute("SELECT 1")\n'
            '    op.execute("SELECT 2")\n'
            '    op.drop_table("users")\n',
        )
        assert len(find_drops(path)) == 1

    def test_marker_only_allows_the_statement_it_marks(self, tmp_path):
        path = _write(
            tmp_path,
            f'def upgrade():\n    op.drop_table("a")  # {ALLOW_MARKER}\n'
            '    op.drop_table("b")\n',
        )
        (finding,) = find_drops(path)
        assert finding.line == 3


class TestRepositoryIsClean:
    def test_shipped_migrations_pass_the_gate(self):
        """Every existing drop lives in downgrade(), so the gate starts green."""
        assert scan(REPO_ROOT / "migrations") == []

    def test_the_scan_actually_saw_the_migrations(self):
        versions = list((REPO_ROOT / "migrations" / "versions").glob("*.py"))
        assert len(versions) >= 5  # guard against a silently empty scan
