#!/usr/bin/env python3
"""Destructive-migration gate for ``migrations/versions/``.

``DROP TABLE`` and ``DROP COLUMN`` inside ``upgrade()`` are irreversible the
moment the migration runs: the rows are gone before anyone notices the mistake,
and ``alembic downgrade`` recreates an empty structure, not the data. Every
other schema change in this repository is additive and reversible, so a drop in
an upgrade path is nearly always either a mistake or a step that belongs in a
separate, deliberately-reviewed release.

The same statement inside ``downgrade()`` is exactly right — it is the inverse
of the create — so ``downgrade()`` is the *only* part of a migration module that
is exempt. Everything else is scanned, including module level and helper
functions: a drop does not become safe by being one call away from
``upgrade()``, and scanning only the ``upgrade`` body let
``def _cleanup(): op.drop_table(...)`` through unnoticed.

A genuinely intended drop opts out with a marker on (or immediately above) the
statement::

    def upgrade() -> None:
        # migration-guard: allow-drop — table shipped empty in 2.4, never written
        op.drop_table("legacy_scratch")

The reason after the marker is not parsed, but write one: it is the only record
of why the data was expendable.

Usage:
    python scripts/check_migrations.py            # gate (exit 1 on a finding)
    python scripts/check_migrations.py --list     # print every drop, both paths
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Default tree to scan, relative to the repository root.
MIGRATIONS_DIR = REPO_ROOT / "migrations"

#: Opt-out marker. A reason should follow it.
ALLOW_MARKER = "migration-guard: allow-drop"

#: The one function whose body may legitimately drop things.
DOWNGRADE_FUNCTION = "downgrade"

#: Raw-SQL drops that destroy data. ``DROP INDEX``/``DROP CONSTRAINT`` are
#: deliberately absent: both are rebuildable from the schema.
_SQL_DROP_RE = re.compile(r"\bDROP\s+(?:TABLE|COLUMN)\b", re.IGNORECASE)

#: Alembic helpers with the same effect as the SQL above.
_DROP_CALLS = frozenset({"drop_table", "drop_column"})


@dataclass(frozen=True)
class DropFinding:
    """One destructive statement inside an ``upgrade()`` body."""

    path: Path
    line: int
    statement: str

    def render(self, root: Path = REPO_ROOT) -> str:
        """Format as a ``path:line: message`` finding."""
        try:
            location = self.path.relative_to(root)
        except ValueError:
            location = self.path
        return (
            f"{location}:{self.line}: destructive statement on the upgrade path: "
            f"{self.statement} — move it to {DOWNGRADE_FUNCTION}(), split it "
            f"into its own reviewed release, or mark it with `# {ALLOW_MARKER}`"
        )


def _downgrade_spans(tree: ast.Module) -> list[tuple[int, int]]:
    """Line ranges of every ``downgrade`` definition, at any nesting level."""
    spans: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == DOWNGRADE_FUNCTION
        ):
            spans.append((node.lineno, getattr(node, "end_lineno", node.lineno)))
    return spans


def _parent_map(tree: ast.Module) -> dict[ast.AST, ast.AST]:
    """Child → parent, so a drop can be attributed to its own statement."""
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    return parents


def _enclosing_statement(
    node: ast.AST, parents: dict[ast.AST, ast.AST]
) -> ast.stmt | None:
    """Innermost statement containing *node*."""
    current: ast.AST | None = node
    while current is not None and not isinstance(current, ast.stmt):
        current = parents.get(current)
    return current if isinstance(current, ast.stmt) else None


def _is_docstring(statement: ast.stmt) -> bool:
    """A bare string expression is prose, not SQL."""
    return isinstance(statement, ast.Expr) and isinstance(
        getattr(statement, "value", None), ast.Constant
    )


def _statement_is_marked(statement: ast.stmt, lines: list[str]) -> bool:
    """True when the opt-out marker covers *statement*.

    The marker counts on the statement's own lines, or on a **comment-only**
    line directly above it. Both halves matter: a marker anywhere in the file
    would let one blanket comment license every later drop, and a trailing
    marker on the previous statement would silently cover the next one too.
    """
    start = statement.lineno
    end = getattr(statement, "end_lineno", start) or start
    if any(ALLOW_MARKER in line for line in lines[start - 1 : end]):
        return True
    if start > 1:
        previous = lines[start - 2].strip()
        return previous.startswith("#") and ALLOW_MARKER in previous
    return False


def _describe(node: ast.AST) -> str | None:
    """Return a short description when *node* is a destructive operation."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        match = _SQL_DROP_RE.search(node.value)
        if match:
            return match.group(0).upper()
        return None
    if isinstance(node, ast.Call):
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else None
        if name is None and isinstance(func, ast.Name):
            name = func.id
        if name in _DROP_CALLS:
            return f"{name}()"
    return None


def find_drops(path: Path) -> list[DropFinding]:
    """Destructive statements in ``upgrade()`` that carry no opt-out marker.

    Args:
        path: A migration module.

    Returns:
        One finding per offending statement, in source order. A file that does
        not parse yields a single finding rather than being skipped — a gate
        that silently ignores what it cannot read is not a gate.
    """
    source = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [
            DropFinding(
                path, exc.lineno or 1, f"file could not be parsed (syntax): {exc.msg}"
            )
        ]

    lines = source.splitlines()
    exempt = _downgrade_spans(tree)
    parents = _parent_map(tree)

    # One entry per offending statement, keyed by line so a statement with two
    # drops in it is reported once with both reasons.
    reasons_by_statement: dict[int, set[str]] = {}
    for node in ast.walk(tree):
        described = _describe(node)
        if described is None:
            continue
        statement = _enclosing_statement(node, parents)
        if statement is None or _is_docstring(statement):
            continue
        if any(start <= statement.lineno <= end for start, end in exempt):
            continue
        if _statement_is_marked(statement, lines):
            continue
        reasons_by_statement.setdefault(statement.lineno, set()).add(described)

    return [
        DropFinding(path, line, ", ".join(sorted(reasons)))
        for line, reasons in sorted(reasons_by_statement.items())
    ]


def scan(migrations_dir: Path = MIGRATIONS_DIR) -> list[DropFinding]:
    """Every unmarked destructive upgrade statement under *migrations_dir*."""
    versions = migrations_dir / "versions"
    if not versions.is_dir():
        return []
    findings: list[DropFinding] = []
    for path in sorted(versions.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        findings.extend(find_drops(path))
    return findings


def _list_all(migrations_dir: Path) -> int:
    """Print every drop in both paths, marked or not (audit aid)."""
    versions = migrations_dir / "versions"
    if not versions.is_dir():
        print(f"No migrations found under {migrations_dir}", file=sys.stderr)
        return 0
    for path in sorted(versions.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), start=1):
            if _SQL_DROP_RE.search(line) or any(
                f"{call}(" in line for call in _DROP_CALLS
            ):
                print(f"{path.relative_to(REPO_ROOT)}:{number}: {line.strip()}")
    return 0


def main() -> int:
    """Entry point. Returns the process exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--list",
        action="store_true",
        help="print every drop found, on the upgrade path and in downgrade()",
    )
    parser.add_argument(
        "--migrations-dir",
        type=Path,
        default=MIGRATIONS_DIR,
        help="migrations directory to scan (default: ./migrations)",
    )
    args = parser.parse_args()

    if args.list:
        return _list_all(args.migrations_dir)

    findings = scan(args.migrations_dir)
    if not findings:
        print("Migrations OK (no unguarded DROP TABLE/COLUMN outside downgrade()).")
        return 0

    print("Destructive statements found on the upgrade path:\n", file=sys.stderr)
    for finding in findings:
        print(f"  {finding.render()}", file=sys.stderr)
    print(
        f"\n{len(findings)} finding(s). A drop reached from upgrade() destroys "
        "data the moment it runs and downgrade() cannot bring it back.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
