"""Performance budget gate over a Locust run.

The perf smoke job used to rely on Locust's own exit code, which fails only when
a request fails. That gate could not fail for the reason it exists:

* a run that made **zero requests** exited 0 — a backend that never became
  reachable, or a profile whose tasks all raised, looked exactly like success;
* a run 50x slower than the last one exited 0 as long as every response was a
  2xx.

This script reads the ``*_stats.csv`` Locust writes with ``--csv`` and checks it
against the explicit budgets in ``scripts/perf_budget.json``:

* the run made at least ``min_total_requests`` requests;
* every budgeted endpoint appears, with at least its own ``min_requests``;
* every budgeted endpoint's ``p95_ms`` and failure ratio are within budget;
* no endpoint in the run is missing from the budget file.

Budgets are **ceilings set deliberately**, not numbers derived from the last
run: there is no ``--update-baseline``. A CI runner is noisy, so they are set
with wide headroom — this catches a 10x regression or a dead backend, not a 10%
drift. Tightening one is a conscious edit.

Usage:
    python scripts/check_perf_budget.py --stats perf_stats.csv
    python scripts/check_perf_budget.py --stats perf_stats.csv --report
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BUDGET_PATH = Path(__file__).resolve().parent / "perf_budget.json"

#: Locust writes an aggregate row with an empty ``Type``; it is not an endpoint.
AGGREGATE_NAME = "Aggregated"


@dataclass(frozen=True)
class EndpointStats:
    """One row of Locust's stats CSV."""

    name: str
    requests: int
    failures: int
    p95_ms: float

    @property
    def failure_ratio(self) -> float:
        return self.failures / self.requests if self.requests else 1.0


def parse_stats(path: Path) -> tuple[dict[str, EndpointStats], EndpointStats | None]:
    """Return ``({endpoint name: stats}, aggregate)`` from a Locust stats CSV."""
    endpoints: dict[str, EndpointStats] = {}
    aggregate: EndpointStats | None = None
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            name = (row.get("Name") or "").strip()
            if not name:
                continue
            try:
                stats = EndpointStats(
                    name=name,
                    requests=int(float(row.get("Request Count") or 0)),
                    failures=int(float(row.get("Failure Count") or 0)),
                    p95_ms=float(row.get("95%") or 0.0),
                )
            except ValueError:
                continue
            if name == AGGREGATE_NAME and not (row.get("Type") or "").strip():
                aggregate = stats
            else:
                endpoints[name] = stats
    return endpoints, aggregate


def load_budget(path: Path = BUDGET_PATH) -> dict[str, object]:
    """Load the budget document."""
    return json.loads(path.read_text(encoding="utf-8"))


def check_perf_budget(
    endpoints: dict[str, EndpointStats],
    aggregate: EndpointStats | None,
    budget: dict[str, object],
) -> list[str]:
    """Return human-readable budget violations (empty when within budget)."""
    violations: list[str] = []
    per_endpoint: dict[str, dict[str, float]] = budget.get("endpoints", {})  # type: ignore[assignment]
    min_total = int(budget.get("min_total_requests", 0))  # type: ignore[arg-type]

    total = (
        aggregate.requests if aggregate else sum(e.requests for e in endpoints.values())
    )
    if total < min_total:
        violations.append(
            f"the run made {total} request(s), below the {min_total} required — a "
            "backend that never came up or a profile that raised on every task "
            "must not look like a pass"
        )

    for name, limits in sorted(per_endpoint.items()):
        stats = endpoints.get(name)
        if stats is None:
            violations.append(f"{name}: budgeted but absent from the run")
            continue
        min_requests = int(limits.get("min_requests", 0))
        if stats.requests < min_requests:
            violations.append(
                f"{name}: {stats.requests} request(s), below the {min_requests} required"
            )
        p95_budget = float(limits.get("p95_ms", 0))
        if p95_budget and stats.p95_ms > p95_budget:
            violations.append(
                f"{name}: p95 {stats.p95_ms:.0f}ms over the {p95_budget:.0f}ms budget"
            )
        max_failures = float(limits.get("max_failure_ratio", 0))
        if stats.failure_ratio > max_failures:
            violations.append(
                f"{name}: failure ratio {stats.failure_ratio:.1%} over the "
                f"{max_failures:.1%} budget ({stats.failures}/{stats.requests})"
            )

    unbudgeted = sorted(set(endpoints) - set(per_endpoint))
    if unbudgeted:
        violations.append(
            "endpoint(s) exercised with no budget — add them to "
            f"scripts/perf_budget.json: {unbudgeted}"
        )
    return violations


def format_report(
    endpoints: dict[str, EndpointStats], budget: dict[str, object]
) -> str:
    """A table of observed values against their budgets."""
    per_endpoint: dict[str, dict[str, float]] = budget.get("endpoints", {})  # type: ignore[assignment]
    lines = [f"{'endpoint':<24} {'reqs':>6} {'p95 ms':>8} {'budget':>8} {'fail':>7}"]
    for name, stats in sorted(endpoints.items()):
        limits = per_endpoint.get(name, {})
        budgeted = limits.get("p95_ms")
        lines.append(
            f"{name:<24} {stats.requests:>6} {stats.p95_ms:>8.0f} "
            f"{(f'{budgeted:.0f}' if budgeted else '-'):>8} {stats.failure_ratio:>6.1%}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stats",
        default="perf_stats.csv",
        help="Locust --csv stats file (default: perf_stats.csv)",
    )
    parser.add_argument(
        "--report", action="store_true", help="print observed vs budget"
    )
    args = parser.parse_args(argv)

    stats_path = Path(args.stats)
    if not stats_path.is_file():
        print(f"stats file not found: {stats_path}", file=sys.stderr)
        return 1

    endpoints, aggregate = parse_stats(stats_path)
    budget = load_budget()

    if args.report:
        print(format_report(endpoints, budget))

    violations = check_perf_budget(endpoints, aggregate, budget)
    if violations:
        print("Performance budget violations:", file=sys.stderr)
        for violation in violations:
            print(f" - {violation}", file=sys.stderr)
        return 1

    print(f"Performance budget OK ({len(endpoints)} endpoint(s) within budget).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
