#!/usr/bin/env python3
"""Deterministic red-team regression gate.

Replays the adversarial corpus in ``evals/red_team/`` through the guardrail
surfaces (input guard, indirect-injection scanner, output guard) via
:mod:`core.evaluation.red_team` and exits non-zero when a case flips verdict.

No LLM is invoked — the gate is deterministic and CI-safe (no API keys, no
cost, no network), exactly like the trajectory and fairness gates. Red-team
cases belong in the suite permanently: a jailbreak that was blocked last
quarter and passes today is a regression, and nothing else will catch it.

An **empty corpus directory fails the gate**. A red-team suite that silently
tests nothing is the failure mode this exists to prevent.

Usage:
    python scripts/run_red_team_evals.py [--cases evals/red_team] \
        [--threshold 1.0] [--report report.json] [--allow-empty]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.evaluation.red_team import (  # noqa: E402
    RedTeamLoadError,
    load_red_team_cases,
    run_red_team_suite,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases",
        default=str(REPO_ROOT / "evals" / "red_team"),
        help="Directory of YAML red-team case files",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=1.0,
        help="Minimum pass rate (a checked-in corpus must pass entirely: 1.0)",
    )
    parser.add_argument(
        "--report",
        default=None,
        help="Optional path to write the JSON report to",
    )
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="Do not fail when the corpus directory holds no cases",
    )
    args = parser.parse_args(argv)

    try:
        cases = load_red_team_cases(args.cases)
    except RedTeamLoadError as exc:
        print(f"red-team gate: load error: {exc}", file=sys.stderr)
        return 2

    if not cases and not args.allow_empty:
        print(
            f"red-team gate FAILED: no cases found in {args.cases}. "
            "An empty adversarial corpus tests nothing — add cases or pass "
            "--allow-empty deliberately.",
            file=sys.stderr,
        )
        return 1

    report = run_red_team_suite(cases, threshold=args.threshold)
    payload = report.to_json()
    if args.report:
        Path(args.report).write_text(payload + "\n", encoding="utf-8")
    print(payload)

    if not report.meets_threshold:
        failed = [
            f"{r.case_id}(expected={r.expected}, actual={r.actual})"
            for r in report.failures()
        ]
        print(
            f"red-team gate FAILED: pass_rate={report.pass_rate:.2f} "
            f"< threshold={report.threshold:.2f}; failing cases: {failed}",
            file=sys.stderr,
        )
        return 1

    print(
        f"red-team gate PASSED: {report.passed}/{report.total} cases "
        f"(threshold={report.threshold:.2f})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
