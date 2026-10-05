"""LoopBudget parent/child: child caps enforced, spend forwarded once."""

from __future__ import annotations

import pytest

from core.orchestration.budget_context import (
    activate_budget,
    charge_llm_cost,
    deactivate_budget,
    get_active_budget,
    standalone_budget,
)
from core.orchestration.limits import BudgetExceededError, LoopBudget, LoopLimits

pytestmark = [pytest.mark.unit]


def _pair(**child_caps: object) -> tuple[LoopBudget, LoopBudget]:
    parent = LoopBudget(limits=LoopLimits(budget_usd=10.0, max_tokens=None))
    child = LoopBudget(limits=LoopLimits(**child_caps), parent=parent)  # type: ignore[arg-type]
    return parent, child


def test_counters_forward_to_parent_once() -> None:
    parent, child = _pair(max_tokens=None)
    child.tick()
    child.record_tool_call()
    child.charge(0.02)
    child.record_tokens(100)
    child.record_context_tokens(40)
    assert (parent.iterations, parent.tool_calls, parent.tokens) == (1, 1, 100)
    assert parent.cost_usd == pytest.approx(0.02)
    assert parent.context_tokens == 40
    assert (child.iterations, child.tool_calls, child.tokens) == (1, 1, 100)


def test_child_cap_raises_after_parent_recorded() -> None:
    parent, child = _pair(budget_usd=0.05)
    with pytest.raises(BudgetExceededError) as exc:
        child.charge(0.06)
    assert exc.value.reason == "budget_usd"
    assert parent.cost_usd == pytest.approx(0.06)


def test_parent_cap_still_binds_child() -> None:
    parent = LoopBudget(limits=LoopLimits(budget_usd=0.05))
    child = LoopBudget(limits=LoopLimits(budget_usd=1.0), parent=parent)
    with pytest.raises(BudgetExceededError):
        child.charge(0.06)


def test_remaining_seconds_is_the_tighter_deadline() -> None:
    parent = LoopBudget(limits=LoopLimits(max_seconds=5.0))
    child = LoopBudget(limits=LoopLimits(max_seconds=100.0), parent=parent)
    remaining = child.remaining_seconds()
    assert remaining is not None and remaining <= 5.0
    unbounded = LoopBudget(limits=LoopLimits(max_seconds=None), parent=parent)
    assert unbounded.remaining_seconds() is not None


def test_parent_deadline_bounds_child() -> None:
    parent = LoopBudget(limits=LoopLimits(max_seconds=1.0))
    parent.started_at -= 10
    child = LoopBudget(limits=LoopLimits(max_seconds=None), parent=parent)
    with pytest.raises(BudgetExceededError) as exc:
        child.tick()
    assert exc.value.reason == "max_seconds"


def test_token_pressure_reports_the_higher() -> None:
    parent = LoopBudget(limits=LoopLimits(max_tokens=100))
    parent.tokens = 90
    child = LoopBudget(limits=LoopLimits(max_tokens=1000), parent=parent)
    assert child.token_pressure() == pytest.approx(0.9)


def test_standalone_budget_enforce_own_binds_child_under_ambient() -> None:
    ambient = LoopBudget(limits=LoopLimits(budget_usd=10.0))
    token = activate_budget(ambient)
    try:
        with standalone_budget(LoopLimits(budget_usd=0.05), enforce_own=True) as b:
            assert b is not ambient and b is not None and b.parent is ambient
            assert get_active_budget() is b
        assert get_active_budget() is ambient
        with standalone_budget(LoopLimits(budget_usd=0.05)) as reused:
            assert reused is ambient
    finally:
        deactivate_budget(token)


def test_charge_llm_cost_hits_child_and_parent_once() -> None:
    ambient = LoopBudget(limits=LoopLimits(budget_usd=10.0, max_tokens=None))
    token = activate_budget(ambient)
    try:
        with standalone_budget(LoopLimits(max_tokens=None), enforce_own=True) as b:
            assert b is not None
            cost = charge_llm_cost("gpt-4o", 1000, 500)
            assert b.tokens == 1500 and ambient.tokens == 1500
            assert b.cost_usd == pytest.approx(cost)
            assert ambient.cost_usd == pytest.approx(cost)
    finally:
        deactivate_budget(token)
