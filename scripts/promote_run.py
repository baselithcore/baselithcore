#!/usr/bin/env python3
"""Promote a recorded production run into the eval corpus.

Loads the completed checkpoint for ``run_id`` from the default checkpoint
store, scrubs PII and indirect-injection artifacts, appends the replayable
recording to ``evals/runs/recorded_runs.json``, and with ``--cases`` also
writes a starter trajectory case into ``evals/cases/``. All logic lives in
:mod:`core.evaluation.promotion` — this CLI stays thin.

Usage:
    python scripts/promote_run.py <run_id> [--cases] \
        [--runs-file evals/runs/recorded_runs.json] [--cases-dir evals/cases]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.evaluation.promotion import PromotionError, promote_run  # noqa: E402
from core.orchestration.checkpoint_factory import (  # noqa: E402
    initialize_default_checkpoint_store,
)


async def _promote(args: argparse.Namespace) -> int:
    store = await initialize_default_checkpoint_store()
    if store is None:
        print(
            "Checkpointing is disabled (ORCHESTRATOR_CHECKPOINT_ENABLED=false); "
            "there is no run store to promote from.",
            file=sys.stderr,
        )
        return 2
    try:
        result = await promote_run(
            store,
            args.run_id,
            runs_file=Path(args.runs_file),
            cases_dir=Path(args.cases_dir) if args.cases else None,
        )
    except PromotionError as exc:
        print(f"Promotion refused: {exc}", file=sys.stderr)
        return 1

    print(f"Promoted run {result.run_id} -> {args.runs_file}")
    if result.scrubbed:
        print("Scrub notes: " + ", ".join(result.scrubbed))
    else:
        print("Scrub notes: none (content was clean)")
    if result.case_path is not None:
        print(f"Starter case written: {result.case_path}")
        print("Review and tighten the case assertions before relying on it.")
    print(
        "The corpus grew — refresh the eval ratchet:\n"
        "    python scripts/check_eval_baseline.py --update-baseline"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("run_id", help="Checkpoint run id to promote")
    parser.add_argument(
        "--cases",
        action="store_true",
        help="Also write a starter trajectory case into the cases directory",
    )
    parser.add_argument(
        "--runs-file",
        default=str(REPO_ROOT / "evals" / "runs" / "recorded_runs.json"),
        help="Recorded-runs JSON file to append to",
    )
    parser.add_argument(
        "--cases-dir",
        default=str(REPO_ROOT / "evals" / "cases"),
        help="Directory for the starter case YAML (used with --cases)",
    )
    args = parser.parse_args(argv)
    return asyncio.run(_promote(args))


if __name__ == "__main__":
    raise SystemExit(main())
