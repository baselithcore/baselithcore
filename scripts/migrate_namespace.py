#!/usr/bin/env python3
"""Move ``core``/``plugins`` under the single top-level package ``baselith``.

Rewrites every reference (imports, dotted-path strings, filesystem paths,
packaging and tooling configuration, documentation) and then moves the two
directories. Deterministic and idempotent: running it on an already migrated
tree changes nothing, which is what lets an in-flight branch re-run it after
the migration lands.

Usage::

    python scripts/migrate_namespace.py --dry-run         # report only (default)
    python scripts/migrate_namespace.py --dry-run --json report.json
    python scripts/migrate_namespace.py --apply           # rewrite + git mv
    python scripts/migrate_namespace.py --apply --post    # ... + ruff/regeneration
    python scripts/migrate_namespace.py --check           # gate: exit 1 if anything is left

Exit codes: 0 success / nothing left; 1 ``--check`` found pending rewrites or
moves; 2 the run itself failed (parse errors under ``--apply``).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from namespace_migration.engine import run  # type: ignore[import-not-found]
    from namespace_migration.post import (
        run_post_steps,  # type: ignore[import-not-found]
    )
else:
    from .namespace_migration.engine import run
    from .namespace_migration.post import run_post_steps


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0] if __doc__ else None,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="report only (default)")
    mode.add_argument(
        "--apply", action="store_true", help="rewrite files and move the trees"
    )
    mode.add_argument(
        "--check", action="store_true", help="exit 1 when anything is left to migrate"
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root (default: the checkout holding this script)",
    )
    parser.add_argument(
        "--no-move", action="store_true", help="rewrite only; skip the directory move"
    )
    parser.add_argument(
        "--post",
        action="store_true",
        help="with --apply: stage the result and run the deterministic regeneration "
        "steps (ruff fix/format via pre-commit, config-surface --write)",
    )
    parser.add_argument(
        "--json", type=Path, help="write the full report as JSON to this path"
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=min(8, os.cpu_count() or 1),
        help="worker processes (output is identical for any value)",
    )
    parser.add_argument(
        "--limit", type=int, default=40, help="unresolved entries to print"
    )
    parser.add_argument(
        "--quiet", action="store_true", help="print only the summary line"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns the process exit code."""
    args = _parser().parse_args(argv)
    root = args.root.resolve()
    report = run(root, apply=args.apply, move=not args.no_move, jobs=args.jobs)
    if args.json:
        args.json.write_text(report.dumps() + "\n", encoding="utf-8")
    mode = "apply" if args.apply else ("check" if args.check else "dry-run")
    if not args.quiet:
        print(report.render(limit=args.limit))
    pending = report.violations()
    print(
        f"[{mode}] files={report.files_scanned} changed={len(report.files_changed)} "
        f"rewrites={report.rewrite_total()} unresolved={len(report.unresolved)} "
        f"errors={len(report.errors)} moves={len(report.moved)} {report.elapsed_s:.1f}s"
    )
    if args.apply and args.post:
        for step, outcome in run_post_steps(root):
            print(f"[post] {step}: {outcome}")
    if args.check:
        if pending:
            print(
                f"namespace check FAILED: {pending} legacy reference(s)/move(s) pending"
            )
            return 1
        print("namespace check OK")
        return 0
    if args.apply and report.errors:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
