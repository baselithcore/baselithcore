"""Tests for the performance budget gate (scripts/check_perf_budget.py).

The gate exists because the previous one could not fail for the reason it
existed: Locust's exit code only reports request failures, so a run that made
zero requests, or one 50x slower than the last, both exited 0.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from scripts.check_perf_budget import (
    BUDGET_PATH,
    EndpointStats,
    check_perf_budget,
    format_report,
    load_budget,
    parse_stats,
)

HEADER = (
    "Type,Name,Request Count,Failure Count,Median Response Time,Average Response Time,"
    "Min Response Time,Max Response Time,Average Content Size,Requests/s,Failures/s,"
    "50%,66%,75%,80%,90%,95%,98%,99%,99.9%,99.99%,100%"
)

BUDGET: dict[str, object] = {
    "min_total_requests": 200,
    "endpoints": {
        "GET /health": {"min_requests": 50, "p95_ms": 300, "max_failure_ratio": 0.0},
        "POST /feedback": {
            "min_requests": 20,
            "p95_ms": 1500,
            "max_failure_ratio": 0.0,
        },
    },
}


def _row(
    name: str, requests: int, failures: int, p95: float, method: str = "GET"
) -> str:
    cells = [
        method,
        name,
        str(requests),
        str(failures),
        "1",
        "1.0",
        "0.4",
        "9.0",
        "1.0",
        "5.0",
        "0.0",
    ]
    cells += ["1", "1", "2", "2", "2", str(p95), "3", "3", "4", "4", "4"]
    return ",".join(cells)


def _csv(
    tmp_path: Path, rows: list[str], total: tuple[int, int, float] | None = None
) -> Path:
    lines = [HEADER, *rows]
    if total is not None:
        requests, failures, p95 = total
        lines.append(_row("Aggregated", requests, failures, p95, method=""))
    path = tmp_path / "perf_stats.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class TestParseStats:
    def test_separates_the_aggregate_row_from_endpoints(self, tmp_path: Path) -> None:
        path = _csv(
            tmp_path,
            [_row("GET /health", 300, 0, 4), _row("POST /feedback", 60, 0, 3, "POST")],
            total=(360, 0, 4),
        )

        endpoints, aggregate = parse_stats(path)

        assert set(endpoints) == {"GET /health", "POST /feedback"}
        assert aggregate is not None and aggregate.requests == 360
        assert endpoints["GET /health"].p95_ms == 4

    def test_a_run_with_no_rows_yields_nothing(self, tmp_path: Path) -> None:
        path = _csv(tmp_path, [])

        endpoints, aggregate = parse_stats(path)

        assert endpoints == {} and aggregate is None


class TestCheckPerfBudget:
    def test_a_healthy_run_passes(self, tmp_path: Path) -> None:
        endpoints, aggregate = parse_stats(
            _csv(
                tmp_path,
                [
                    _row("GET /health", 300, 0, 4),
                    _row("POST /feedback", 60, 0, 3, "POST"),
                ],
                total=(360, 0, 4),
            )
        )

        assert check_perf_budget(endpoints, aggregate, BUDGET) == []

    def test_an_empty_run_is_rejected(self, tmp_path: Path) -> None:
        """The failure mode the old gate could not catch."""
        endpoints, aggregate = parse_stats(_csv(tmp_path, []))

        violations = check_perf_budget(endpoints, aggregate, BUDGET)

        assert any("below the 200 required" in v for v in violations)

    def test_a_slow_endpoint_is_rejected(self, tmp_path: Path) -> None:
        endpoints, aggregate = parse_stats(
            _csv(
                tmp_path,
                [
                    _row("GET /health", 300, 0, 4200),
                    _row("POST /feedback", 60, 0, 3, "POST"),
                ],
                total=(360, 0, 4200),
            )
        )

        violations = check_perf_budget(endpoints, aggregate, BUDGET)

        assert len(violations) == 1
        assert "p95 4200ms over the 300ms budget" in violations[0]

    def test_failures_are_rejected(self, tmp_path: Path) -> None:
        endpoints, aggregate = parse_stats(
            _csv(
                tmp_path,
                [
                    _row("GET /health", 300, 3, 4),
                    _row("POST /feedback", 60, 0, 3, "POST"),
                ],
                total=(360, 3, 4),
            )
        )

        violations = check_perf_budget(endpoints, aggregate, BUDGET)

        assert any("failure ratio 1.0%" in v for v in violations)

    def test_a_budgeted_endpoint_that_never_ran_is_rejected(
        self, tmp_path: Path
    ) -> None:
        endpoints, aggregate = parse_stats(
            _csv(tmp_path, [_row("GET /health", 300, 0, 4)], total=(300, 0, 4))
        )

        violations = check_perf_budget(endpoints, aggregate, BUDGET)

        assert any("POST /feedback: budgeted but absent" in v for v in violations)

    def test_too_few_requests_on_one_endpoint_is_rejected(self, tmp_path: Path) -> None:
        endpoints, aggregate = parse_stats(
            _csv(
                tmp_path,
                [
                    _row("GET /health", 300, 0, 4),
                    _row("POST /feedback", 2, 0, 3, "POST"),
                ],
                total=(302, 0, 4),
            )
        )

        violations = check_perf_budget(endpoints, aggregate, BUDGET)

        assert any(
            "POST /feedback: 2 request(s), below the 20" in v for v in violations
        )

    def test_an_unbudgeted_endpoint_is_rejected(self, tmp_path: Path) -> None:
        """A task added to the profile must not slip past unmeasured."""
        endpoints, aggregate = parse_stats(
            _csv(
                tmp_path,
                [
                    _row("GET /health", 300, 0, 4),
                    _row("POST /feedback", 60, 0, 3, "POST"),
                    _row("POST /new-thing", 40, 0, 5, "POST"),
                ],
                total=(400, 0, 4),
            )
        )

        violations = check_perf_budget(endpoints, aggregate, BUDGET)

        assert any("POST /new-thing" in v and "no budget" in v for v in violations)


class TestShippedBudget:
    def test_the_committed_budget_is_well_formed(self) -> None:
        budget = load_budget()

        assert budget["min_total_requests"] >= 1, (
            "a zero floor re-opens the vacuous pass"
        )
        endpoints = budget["endpoints"]
        assert endpoints, "an empty budget enforces nothing"
        for name, limits in endpoints.items():
            assert limits["p95_ms"] > 0, name
            assert 0.0 <= limits["max_failure_ratio"] <= 1.0, name
            assert limits["min_requests"] > 0, name

    def test_no_update_baseline_escape_hatch(self) -> None:
        """A perf budget that rewrites itself from a bad run enforces nothing.

        Checked on the parsed CLI flags, not the source text: the module
        docstring names the flag precisely to say it is absent.
        """
        source = (
            Path(__file__).resolve().parents[2] / "scripts" / "check_perf_budget.py"
        )
        tree = ast.parse(source.read_text(encoding="utf-8"))
        flags = {
            arg.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_argument"
            for arg in node.args
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
        }
        assert flags, "no CLI flags parsed — the check would pass vacuously"
        assert "--update-baseline" not in flags

    def test_budget_matches_the_locust_profile(self) -> None:
        """Every budgeted endpoint is a task name the profile actually uses."""
        profile = (
            Path(__file__).resolve().parents[2] / "tests" / "load" / "locustfile.py"
        ).read_text(encoding="utf-8")
        for name in load_budget()["endpoints"]:
            assert f'name="{name}"' in profile, name


def test_report_renders_observed_against_budget(tmp_path: Path) -> None:
    endpoints, _ = parse_stats(
        _csv(tmp_path, [_row("GET /health", 300, 0, 4)], total=(300, 0, 4))
    )

    report = format_report(endpoints, BUDGET)

    assert "GET /health" in report and "300" in report


def test_budget_path_points_at_the_committed_file() -> None:
    assert BUDGET_PATH.is_file()
    json.loads(BUDGET_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("requests", "failures", "expected"), [(0, 0, 1.0), (100, 5, 0.05)]
)
def test_failure_ratio(requests: int, failures: int, expected: float) -> None:
    assert EndpointStats("x", requests, failures, 1.0).failure_ratio == expected
