"""Unit tests for ``core.evaluation.trajectory``."""

from __future__ import annotations

from core.evaluation.trajectory import (
    ToolCall,
    TrajectoryCase,
    TrajectoryEvaluator,
    aggregate_pass_rate,
)


def _evaluator() -> TrajectoryEvaluator:
    return TrajectoryEvaluator()


class TestTrajectoryEvaluator:
    def test_passes_when_all_rules_satisfied(self) -> None:
        case: TrajectoryCase = {
            "case_id": "c1",
            "expected_keywords": ["python"],
            "expected_tools": ["search"],
            "max_tool_calls": 3,
            "max_latency_ms": 500,
        }
        trajectory: list[ToolCall] = [{"name": "search"}]
        r = _evaluator().evaluate(case, "Python is great", trajectory, 100)
        assert r.passed
        assert r.violations == []
        assert r.tool_calls == 1

    def test_expected_keyword_missing(self) -> None:
        case: TrajectoryCase = {"case_id": "c", "expected_keywords": ["mongodb"]}
        r = _evaluator().evaluate(case, "we use postgres", [], 0)
        assert not r.passed
        assert any(v.rule == "expected_keyword_missing" for v in r.violations)

    def test_forbidden_keyword_present(self) -> None:
        case: TrajectoryCase = {"case_id": "c", "forbidden_keywords": ["password"]}
        r = _evaluator().evaluate(case, "your password is ...", [], 0)
        assert not r.passed
        assert any(v.rule == "forbidden_keyword_present" for v in r.violations)

    def test_keyword_match_is_case_insensitive(self) -> None:
        case: TrajectoryCase = {
            "case_id": "c",
            "expected_keywords": ["PYTHON"],
            "forbidden_keywords": ["SECRET"],
        }
        r = _evaluator().evaluate(case, "python is great", [], 0)
        assert r.passed

    def test_expected_tool_not_called(self) -> None:
        case: TrajectoryCase = {"case_id": "c", "expected_tools": ["search"]}
        r = _evaluator().evaluate(case, "ok", [{"name": "read"}], 0)
        assert not r.passed
        assert any(v.rule == "expected_tool_not_called" for v in r.violations)

    def test_forbidden_tool_called(self) -> None:
        case: TrajectoryCase = {"case_id": "c", "forbidden_tools": ["rm_rf"]}
        r = _evaluator().evaluate(case, "ok", [{"name": "rm_rf"}], 0)
        assert not r.passed
        assert any(v.rule == "forbidden_tool_called" for v in r.violations)

    def test_max_tool_calls_exceeded(self) -> None:
        case: TrajectoryCase = {"case_id": "c", "max_tool_calls": 2}
        trajectory: list[ToolCall] = [
            {"name": "a"},
            {"name": "b"},
            {"name": "c"},
        ]
        r = _evaluator().evaluate(case, "", trajectory, 0)
        assert not r.passed
        assert any(v.rule == "max_tool_calls_exceeded" for v in r.violations)

    def test_max_latency_exceeded(self) -> None:
        case: TrajectoryCase = {"case_id": "c", "max_latency_ms": 100}
        r = _evaluator().evaluate(case, "", [], 200)
        assert not r.passed
        assert any(v.rule == "max_latency_exceeded" for v in r.violations)

    def test_max_cost_exceeded_explicit(self) -> None:
        case: TrajectoryCase = {"case_id": "c", "max_cost_usd": 0.05}
        r = _evaluator().evaluate(case, "", [], 0, cost_usd=0.10)
        assert not r.passed
        assert any(v.rule == "max_cost_exceeded" for v in r.violations)
        assert r.cost_usd == 0.10

    def test_cost_summed_from_trajectory(self) -> None:
        case: TrajectoryCase = {"case_id": "c", "max_cost_usd": 0.05}
        trajectory: list[ToolCall] = [
            {"name": "a", "cost_usd": 0.04},
            {"name": "b", "cost_usd": 0.03},
        ]
        r = _evaluator().evaluate(case, "", trajectory, 0)
        assert not r.passed
        assert any(v.rule == "max_cost_exceeded" for v in r.violations)
        assert r.cost_usd == 0.07

    def test_cost_within_budget_passes(self) -> None:
        case: TrajectoryCase = {"case_id": "c", "max_cost_usd": 0.50}
        r = _evaluator().evaluate(case, "", [], 0, cost_usd=0.10)
        assert r.passed

    def test_no_cost_gate_ignores_cost(self) -> None:
        case: TrajectoryCase = {"case_id": "c"}
        r = _evaluator().evaluate(case, "", [], 0, cost_usd=9.99)
        assert r.passed
        assert r.cost_usd == 9.99

    def test_multiple_violations_collected(self) -> None:
        case: TrajectoryCase = {
            "case_id": "c",
            "expected_keywords": ["x"],
            "forbidden_tools": ["rm_rf"],
            "max_tool_calls": 1,
        }
        r = _evaluator().evaluate(
            case,
            "no",
            [{"name": "rm_rf"}, {"name": "y"}],
            0,
        )
        assert not r.passed
        assert len(r.violations) == 3


class TestAggregatePassRate:
    def test_empty_returns_zero(self) -> None:
        assert aggregate_pass_rate([]) == 0.0

    def test_all_passing(self) -> None:
        ev = _evaluator()
        case: TrajectoryCase = {"case_id": "c", "expected_keywords": ["ok"]}
        results = [
            ev.evaluate(case, "ok", [], 0),
            ev.evaluate(case, "ok again", [], 0),
        ]
        assert aggregate_pass_rate(results) == 1.0

    def test_mixed(self) -> None:
        ev = _evaluator()
        case: TrajectoryCase = {"case_id": "c", "expected_keywords": ["ok"]}
        results = [
            ev.evaluate(case, "ok", [], 0),
            ev.evaluate(case, "fail", [], 0),
        ]
        assert aggregate_pass_rate(results) == 0.5
