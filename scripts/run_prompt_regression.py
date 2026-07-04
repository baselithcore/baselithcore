"""
Prompt/trajectory regression CLI (V&V criterion V5).

Two modes over the golden dataset (``tests/evaluation/golden/`` by default):

- **Capture**: drive every case end-to-end through a deployed BaselithCore
  instance's ``/v1/chat`` endpoint (real orchestration, real LLM provider),
  record the outputs, then score them::

      python scripts/run_prompt_regression.py --capture \\
        --target http://localhost:8000 --out validation-reports/2026-07-04-eval

- **Replay**: deterministically re-score a previous capture (no network)::

      python scripts/run_prompt_regression.py --runs runs.json

Exits 0 when the pass-rate meets the threshold (default 0.90), 1 when it does
not, 2 on usage or I/O errors. Referenced by ``core/evaluation/trajectory.py``
and the nightly workflow ``.github/workflows/eval-nightly.yml``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from core.evaluation.regression_runner import (
    DEFAULT_PASS_THRESHOLD,
    RegressionReport,
    load_cases,
    load_recorded_runs,
    run_regression,
)
from core.evaluation.trajectory import TrajectoryCase

DEFAULT_CASES_DIR = Path("tests/evaluation/golden")


def capture_runs(
    cases: list[TrajectoryCase],
    *,
    target: str,
    api_key: str,
    api_prefix: str,
    timeout_s: float,
) -> list[dict[str, Any]]:
    """POST each case's input to ``{target}{api_prefix}/chat`` and record it.

    A transport error or non-2xx response is recorded as an empty output so
    the case fails with evidence, rather than aborting the whole campaign.
    """
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    runs: list[dict[str, Any]] = []
    with httpx.Client(base_url=target, headers=headers, timeout=timeout_s) as client:
        for i, case in enumerate(cases, 1):
            case_id = case.get("case_id", "")
            payload = {
                "query": case.get("input", ""),
                # Fresh conversation per case: no cross-case memory bleed.
                "conversation_id": f"eval-{case_id}-{uuid.uuid4().hex[:8]}",
            }
            print(f"[{i}/{len(cases)}] {case_id} ...", flush=True)
            started = time.perf_counter()
            output_text, cost_usd, trajectory, error = "", 0.0, [], ""
            try:
                resp = client.post(f"{api_prefix}/chat", json=payload)
                latency_ms = int((time.perf_counter() - started) * 1000)
                if resp.status_code >= 400:
                    error = f"HTTP {resp.status_code}"
                else:
                    body = resp.json()
                    output_text = str(body.get("answer", ""))
                    meta = body.get("metadata") or {}
                    cost_usd = float(meta.get("cost_usd", 0.0) or 0.0)
                    raw_traj = meta.get("trajectory", [])
                    if isinstance(raw_traj, list):
                        trajectory = [t for t in raw_traj if isinstance(t, dict)]
            except httpx.HTTPError as exc:
                latency_ms = int((time.perf_counter() - started) * 1000)
                error = f"{type(exc).__name__}: {exc}"
            run: dict[str, Any] = {
                "case_id": case_id,
                "output_text": output_text,
                "trajectory": trajectory,
                "latency_ms": latency_ms,
                "cost_usd": cost_usd,
            }
            if error:
                run["error"] = error
                print(f"    capture error: {error}", flush=True)
            runs.append(run)
    return runs


def to_markdown(report: RegressionReport, *, target: str | None) -> str:
    """Render the evidence-pack report (see docs/validation/campaigns.md)."""
    lines = [
        "# Agentic evaluation report (V5)",
        "",
        f"- **Target:** {target or 'replay (recorded runs)'}",
        f"- **Cases:** {report.total}  |  **Passed:** {report.passed}  |  "
        f"**Failed:** {report.failed}",
        f"- **Pass-rate:** {report.pass_rate:.3f} "
        f"(threshold {report.threshold:.2f}) — "
        f"{'✅ PASS' if report.meets_threshold else '❌ FAIL'}",
        "",
        "| Case | Passed | Score | Latency (ms) | Cost (USD) | Violations |",
        "| ---- | ------ | ----- | ------------ | ---------- | ---------- |",
    ]
    for r in report.results:
        violations = "; ".join(f"{v.rule}: {v.detail}" for v in r.violations) or "—"
        lines.append(
            f"| {r.case_id} | {'✅' if r.passed else '❌'} | {r.score:.2f} "
            f"| {r.latency_ms} | {r.cost_usd:.4f} | {violations} |"
        )
    lines.append("")
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prompt/trajectory regression CLI (V&V criterion V5)."
    )
    parser.add_argument(
        "--cases",
        type=Path,
        default=DEFAULT_CASES_DIR,
        help=f"directory of golden case YAMLs (default: {DEFAULT_CASES_DIR})",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_PASS_THRESHOLD,
        help=f"minimum pass-rate (default: {DEFAULT_PASS_THRESHOLD})",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="report directory; writes runs.json, report.json, report.md",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--capture",
        action="store_true",
        help="capture runs live from a deployed instance (needs --target)",
    )
    mode.add_argument(
        "--runs",
        type=Path,
        help="replay a previously captured runs JSON file",
    )
    parser.add_argument("--target", help="base URL of the deployed instance")
    parser.add_argument(
        "--api-key",
        default=os.getenv("BASELITH_API_KEY", ""),
        help="X-API-Key value (default: $BASELITH_API_KEY)",
    )
    parser.add_argument(
        "--api-prefix",
        default=os.getenv("BASELITH_API_PREFIX", "/v1"),
        help="versioned API prefix (default: /v1)",
    )
    parser.add_argument(
        "--timeout-s",
        type=float,
        default=120.0,
        help="per-request timeout in capture mode (default: 120)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.capture and not args.target:
        print("error: --capture requires --target", file=sys.stderr)
        return 2

    try:
        cases = load_cases(args.cases)
    except Exception as exc:
        print(f"error loading cases: {exc}", file=sys.stderr)
        return 2

    if args.capture:
        raw_runs = capture_runs(
            cases,
            target=args.target,
            api_key=args.api_key,
            api_prefix=args.api_prefix,
            timeout_s=args.timeout_s,
        )
        if args.out:
            args.out.mkdir(parents=True, exist_ok=True)
            runs_path = args.out / "runs.json"
        else:
            runs_path = Path(f".eval-runs-{uuid.uuid4().hex[:8]}.json")
        runs_path.write_text(
            json.dumps(raw_runs, indent=2, sort_keys=True), encoding="utf-8"
        )
        # Round-trip through the validated loader so replay and capture are
        # scored identically (it also rejects duplicate case_ids).
        try:
            recorded_runs = load_recorded_runs(runs_path)
        finally:
            if not args.out:
                runs_path.unlink(missing_ok=True)
    else:
        try:
            recorded_runs = load_recorded_runs(args.runs)
        except Exception as exc:
            print(f"error loading runs: {exc}", file=sys.stderr)
            return 2

    report = run_regression(cases, recorded_runs, threshold=args.threshold)
    markdown = to_markdown(report, target=args.target if args.capture else None)
    print(markdown)
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "report.json").write_text(report.to_json(), encoding="utf-8")
        (args.out / "report.md").write_text(markdown, encoding="utf-8")
        print(f"report written to {args.out}/", flush=True)
    return 0 if report.meets_threshold else 1


if __name__ == "__main__":
    raise SystemExit(main())
