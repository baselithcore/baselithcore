#!/usr/bin/env python3
"""Scheduled LLM-as-judge eval run (NOT a merge gate).

Extends the deterministic regression replay with judge scoring via
``run_regression_async``: each case that passes the deterministic checks is
additionally scored by an LLM judge; a low score fails the case, while a
judge *error* never does (see the asymmetric failure semantics on
``run_regression_async``). Judging needs provider credentials and is
nondeterministic, which is exactly why this runs on a schedule
(``nightly-evals`` workflow) instead of gating merges.

Exit codes:
    0 — pass rate met the threshold, or the run was skipped (no provider
        credentials — a fork or credential-less runner must not go red).
    1 — regression: pass rate below threshold.
    2 — asset loading error (broken cases/runs are a repo bug, always red).

Usage:
    python scripts/run_judge_evals.py \
        [--cases evals/cases] [--runs evals/runs/recorded_runs.json] \
        [--threshold 1.0] [--judge-min-score 0.6] [--report report.json] \
        [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.evaluation.regression_runner import (  # noqa: E402
    RegressionLoadError,
    load_cases,
    load_recorded_runs,
    run_regression_async,
)


def _provider_configured() -> bool:
    """Whether the central LLM config can actually serve judge calls."""
    try:
        from core.config.services import get_llm_config
        from core.services.llm.runtime import provider_configured

        config = get_llm_config()
        return provider_configured(config, config.provider)
    except Exception:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=str(REPO_ROOT / "evals" / "cases"))
    parser.add_argument(
        "--runs", default=str(REPO_ROOT / "evals" / "runs" / "recorded_runs.json")
    )
    parser.add_argument("--threshold", type=float, default=1.0)
    parser.add_argument("--judge-min-score", type=float, default=0.6)
    parser.add_argument("--report", default=None)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Load assets and exit without judging (CI smoke).",
    )
    args = parser.parse_args(argv)

    try:
        cases = list(load_cases(Path(args.cases)))
        recorded = load_recorded_runs(Path(args.runs))
    except RegressionLoadError as exc:
        print(f"ERROR: eval assets failed to load: {exc}", file=sys.stderr)
        return 2

    print(f"Loaded {len(cases)} cases, {len(recorded)} recorded runs.")
    if args.dry_run:
        print("Dry run: assets OK, judging skipped.")
        return 0

    if not _provider_configured():
        print(
            "SKIPPED: no LLM provider credentials configured — judge run "
            "needs a keyed provider (set LLM_PROVIDER + its API key)."
        )
        return 0

    from core.evaluation.judges import RelevanceEvaluator

    report = asyncio.run(
        run_regression_async(
            cases,
            recorded,
            threshold=args.threshold,
            judge=RelevanceEvaluator(),
            judge_min_score=args.judge_min_score,
        )
    )

    payload = json.loads(report.to_json())
    payload["judge_min_score"] = args.judge_min_score
    print(json.dumps(payload, indent=2, default=str))
    if args.report:
        Path(args.report).write_text(json.dumps(payload, indent=2, default=str))

    if not report.meets_threshold:
        print(
            f"JUDGE REGRESSION: pass rate {report.pass_rate:.2%} below "
            f"threshold {args.threshold:.2%}",
            file=sys.stderr,
        )
        return 1
    print("Judge eval run passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
