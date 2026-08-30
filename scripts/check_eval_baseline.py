#!/usr/bin/env python3
"""Eval-corpus ratchet: the eval suites may grow, never silently shrink.

The CI quality gates are only as strong as their corpora — a deleted
red-team case or a trimmed regression suite weakens the gate without any
test failing. This script freezes the current per-suite case counts in
``evals/baseline.json`` (the same ratchet pattern as
``scripts/check_file_size.py``): a run fails when any suite has fewer cases
than its baselined count. Growing a suite is always allowed; after growing
it, refresh the baseline with ``--update-baseline`` so the new floor sticks.

Usage:
    python scripts/check_eval_baseline.py
    python scripts/check_eval_baseline.py --update-baseline
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
EVALS_DIR = REPO_ROOT / "evals"
BASELINE_FILE = EVALS_DIR / "baseline.json"

#: Suites under evals/ counted by the ratchet: directory -> glob of corpus
#: files. YAML files must hold a top-level list of cases; JSON a list of runs.
_SUITES: dict[str, str] = {
    "cases": "*.yaml",
    "red_team": "*.yaml",
    "runs": "*.json",
}


def _count_file(path: Path) -> int:
    """Number of corpus entries in one file (0 for malformed/non-list)."""
    try:
        if path.suffix in (".yaml", ".yml"):
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        else:
            data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    return len(data) if isinstance(data, list) else 0


def count_suites(evals_dir: Path) -> dict[str, int]:
    """Per-suite case counts for every ratcheted suite directory."""
    counts: dict[str, int] = {}
    for suite, pattern in _SUITES.items():
        suite_dir = evals_dir / suite
        total = 0
        if suite_dir.is_dir():
            for file in sorted(suite_dir.glob(pattern)):
                total += _count_file(file)
        counts[suite] = total
    return counts


def load_baseline(baseline_file: Path) -> dict[str, int]:
    """The committed baseline counts ({} when the file is absent)."""
    try:
        data = json.loads(baseline_file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    return {k: int(v) for k, v in data.items()}


def check_eval_baseline(evals_dir: Path, baseline: dict[str, int]) -> list[str]:
    """Return one violation line per suite that shrank below its baseline."""
    counts = count_suites(evals_dir)
    violations: list[str] = []
    for suite, floor in baseline.items():
        current = counts.get(suite, 0)
        if current < floor:
            violations.append(
                f"evals/{suite}: {current} case(s), baseline floor is {floor} "
                "— eval corpora are a ratchet: restore the deleted cases or "
                "replace them with equivalents"
            )
    return violations


def write_baseline(evals_dir: Path, baseline_file: Path) -> dict[str, int]:
    """Freeze the current counts as the new baseline."""
    counts = count_suites(evals_dir)
    baseline_file.write_text(
        json.dumps(counts, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Freeze the current suite counts as the new floor.",
    )
    args = parser.parse_args()

    if args.update_baseline:
        counts = write_baseline(EVALS_DIR, BASELINE_FILE)
        print(f"Baseline updated: {counts}")
        return 0

    baseline = load_baseline(BASELINE_FILE)
    if not baseline:
        print(
            "No evals/baseline.json found — create one with --update-baseline.",
            file=sys.stderr,
        )
        return 1
    violations = check_eval_baseline(EVALS_DIR, baseline)
    for line in violations:
        print(f"ERROR: {line}", file=sys.stderr)
    if not violations:
        print("Eval corpus ratchet OK.")
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
