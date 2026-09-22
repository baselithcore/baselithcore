#!/usr/bin/env python3
"""Deterministic eval regression gate.

Grades agent runs against the trajectory cases in ``evals/cases/`` and exits
non-zero when the pass rate falls below the threshold. No LLM is invoked — the
gate is deterministic and CI-safe (no API keys, no cost).

Runs come from two places, and the difference is what the gate can catch:

* ``evals/scenarios/`` — **replayed**. Each scenario carries the provider's
  side of a conversation, which is driven through the *real* agent loop; the
  trajectory graded is the one the loop actually produced. A change to prompt
  assembly, tool dispatch, the message history or answer parsing moves it, and
  the gate fails.
* ``evals/runs/recorded_runs.json`` — **fixtures**. Hand-written objects
  graded against hand-written expectations. They can only fail if somebody
  edits a YAML file or breaks the evaluator, so they are a floor, not a guard.
  Migrating a case to a scenario is what gives it teeth.

A case may be served by one or the other, never both.

Usage:
    python scripts/run_regression_evals.py \
        [--cases evals/cases] [--runs evals/runs/recorded_runs.json] \
        [--scenarios evals/scenarios] [--threshold 1.0] [--report report.json]

The LLM-as-judge extension (``run_regression_async``) is intentionally not
wired here: judge scoring needs provider credentials and is non-deterministic,
so it stays a manual/scheduled concern, not a merge gate.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.evaluation.regression_runner import (  # noqa: E402
    RecordedRun,
    RegressionLoadError,
    load_cases,
    load_recorded_runs,
    run_regression,
)
from core.evaluation.replay import (  # noqa: E402
    ReplayError,
    load_scenarios,
    replay_scenario,
)


def _replayed(directory: str) -> dict[str, RecordedRun]:
    """Run every scenario under ``directory`` through the real agent loop."""
    scenarios = load_scenarios(directory)
    runs = asyncio.run(_replay_all(scenarios))
    return {run.case_id: run for run in runs}


async def _replay_all(scenarios: list[Any]) -> list[RecordedRun]:
    """Replay sequentially: a shared failure reads better one case at a time."""
    return [await replay_scenario(scenario) for scenario in scenarios]


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
        "--scenarios",
        default=str(REPO_ROOT / "evals" / "scenarios"),
        help="Directory of YAML scenarios replayed through the real agent",
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
        replayed = _replayed(args.scenarios) if args.scenarios else {}
    except RegressionLoadError as exc:
        print(f"eval regression gate: load error: {exc}", file=sys.stderr)
        return 2
    except ReplayError as exc:
        # A scenario that will not replay is a gate failure, not a skip: the
        # conversation the loop builds has changed, which is the whole point.
        print(f"eval regression gate: replay failed: {exc}", file=sys.stderr)
        return 1

    both = sorted(set(recorded) & set(replayed))
    if both:
        print(
            "eval regression gate: these cases have both a recorded fixture "
            f"and a scenario, so it is ambiguous which one is graded: {both}. "
            "Delete the fixture — a replayed scenario supersedes it.",
            file=sys.stderr,
        )
        return 2

    orphans = sorted(set(replayed) - {c.get("case_id") for c in cases})
    if orphans:
        print(
            f"eval regression gate: scenarios with no matching case: {orphans}",
            file=sys.stderr,
        )
        return 2

    report = run_regression(cases, {**recorded, **replayed}, threshold=args.threshold)

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
        f"eval regression gate OK: {report.passed}/{report.total} cases passed "
        f"({len(replayed)} replayed through the agent, {len(recorded)} fixtures)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
