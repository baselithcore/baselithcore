#!/usr/bin/env python3
"""Deterministic eval regression gate.

Replays the recorded agent runs in ``evals/runs/`` against the trajectory
cases in ``evals/cases/`` via :mod:`core.evaluation.regression_runner` and
exits non-zero when the pass rate falls below the threshold. No LLM is
invoked — the gate is deterministic and CI-safe (no API keys, no cost).

Usage:
    python scripts/run_regression_evals.py \
        [--cases evals/cases] [--runs evals/runs/recorded_runs.json] \
        [--threshold 1.0] [--report report.json]

The LLM-as-judge extension (``run_regression_async``) is intentionally not
wired here: judge scoring needs provider credentials and is non-deterministic,
so it stays a manual/scheduled concern, not a merge gate.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.evaluation.regression_runner import (  # noqa: E402
    RegressionLoadError,
    load_cases,
    load_recorded_runs,
    run_regression,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases",
        default=str(REPO_ROOT / "evals" / "cases"),
        help="Directory of YAML trajectory-case files",
    )
    parser.add_argument(
        "--runs",
        default=str(REPO_ROOT / "evals" / "runs" / "recorded_runs.json"),
        help="JSON file of recorded agent runs",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=1.0,
        help="Minimum pass rate (checked-in recordings must all pass: 1.0)",
    )
    parser.add_argument(
        "--report",
        default=None,
        help="Optional path to write the JSON report to",
    )
    args = parser.parse_args(argv)

    try:
        cases = load_cases(args.cases)
        recorded = load_recorded_runs(args.runs)
        report = run_regression(cases, recorded, threshold=args.threshold)
    except RegressionLoadError as exc:
        print(f"eval regression gate: load error: {exc}", file=sys.stderr)
        return 2

    payload = report.to_json()
    if args.report:
        Path(args.report).write_text(payload + "\n", encoding="utf-8")
    print(payload)

    if not report.meets_threshold:
        failed = [r.case_id for r in report.results if not r.passed]
        print(
            f"eval regression gate FAILED: pass_rate={report.pass_rate:.2f} "
            f"< threshold={report.threshold:.2f}; failing cases: {failed}",
            file=sys.stderr,
        )
        return 1
    print(
        f"eval regression gate OK: {report.passed}/{report.total} cases passed",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
