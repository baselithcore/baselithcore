"""
Schema and consistency checks for the golden trajectory dataset.

The golden cases under ``tests/evaluation/golden/`` are executed against a
deployed instance by ``scripts/run_prompt_regression.py`` (V&V criterion V5).
These tests keep the dataset loadable, unambiguous, and satisfiable without
any network access, so a malformed case fails CI instead of the nightly run.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from core.evaluation.regression_runner import load_cases
from core.evaluation.trajectory import TrajectoryEvaluator

GOLDEN_DIR = Path(__file__).parent / "golden"

_ASSERTION_KEYS = (
    "expected_keywords",
    "forbidden_keywords",
    "expected_tools",
    "forbidden_tools",
    "expected_tool_args",
    "expected_tool_order",
    "max_tool_calls",
    "max_latency_ms",
    "max_cost_usd",
)


@pytest.fixture(scope="module")
def cases():
    return load_cases(GOLDEN_DIR)


def test_dataset_is_non_trivial(cases) -> None:
    assert len(cases) >= 10, "golden dataset must stay a meaningful sample"


def test_case_ids_unique_and_kebab_case(cases) -> None:
    ids = [c["case_id"] for c in cases]
    assert len(ids) == len(set(ids)), "duplicate case_id in golden dataset"
    for case_id in ids:
        assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", case_id), (
            f"case_id {case_id!r} must be kebab-case"
        )


def test_every_case_has_input_and_an_assertion(cases) -> None:
    for case in cases:
        assert case.get("input"), f"{case['case_id']}: missing 'input'"
        assert any(case.get(k) for k in _ASSERTION_KEYS), (
            f"{case['case_id']}: no assertion — the case can never fail"
        )


def test_bounds_are_positive(cases) -> None:
    for case in cases:
        for key in ("max_tool_calls", "max_latency_ms"):
            if key in case:
                assert case[key] > 0, f"{case['case_id']}: {key} must be > 0"
        if "max_cost_usd" in case:
            assert case["max_cost_usd"] > 0, (
                f"{case['case_id']}: max_cost_usd must be > 0"
            )


def test_cases_are_satisfiable(cases) -> None:
    """A run built to honour each case's constraints must pass it.

    Catches self-contradictory cases (e.g. an expected keyword that contains
    a forbidden one) that would otherwise fail every nightly run.
    """
    evaluator = TrajectoryEvaluator()
    for case in cases:
        ideal_output = " ".join(case.get("expected_keywords", []))
        result = evaluator.evaluate(
            case=case,
            output_text=ideal_output,
            trajectory=[{"name": t} for t in case.get("expected_tools", [])],
            latency_ms=1,
            cost_usd=0.0,
        )
        assert result.passed, (
            f"{case['case_id']}: unsatisfiable case — {result.violations}"
        )
