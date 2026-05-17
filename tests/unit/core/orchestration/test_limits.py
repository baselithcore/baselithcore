"""Unit tests for ``core.orchestration.limits``."""

from __future__ import annotations

import pytest

from core.orchestration.limits import (
    BudgetExceededError,
    LoopBudget,
    LoopBudgetSnapshot,
    LoopLimits,
)


class TestLoopBudget:
    def test_defaults(self) -> None:
        b = LoopBudget()
        snap = b.snapshot()
        assert snap.iterations == 0
        assert snap.tool_calls == 0
        assert snap.cost_usd == 0.0
        assert b.limits.max_iterations == 25
        assert b.limits.budget_usd == 0.50

    def test_tick_advances_iterations(self) -> None:
        b = LoopBudget(limits=LoopLimits(max_iterations=3))
        b.tick()
        b.tick()
        assert b.iterations == 2

    def test_tick_raises_when_max_iterations_exceeded(self) -> None:
        b = LoopBudget(limits=LoopLimits(max_iterations=2))
        b.tick()
        b.tick()
        with pytest.raises(BudgetExceededError) as exc:
            b.tick()
        assert exc.value.reason == "max_iterations"
        assert exc.value.snapshot.iterations == 3

    def test_record_tool_call_raises_when_cap_exceeded(self) -> None:
        b = LoopBudget(limits=LoopLimits(max_tool_calls=2))
        b.record_tool_call()
        b.record_tool_call()
        with pytest.raises(BudgetExceededError) as exc:
            b.record_tool_call()
        assert exc.value.reason == "max_tool_calls"

    def test_charge_accumulates_cost(self) -> None:
        b = LoopBudget(limits=LoopLimits(budget_usd=1.0))
        b.charge(0.3)
        b.charge(0.2)
        assert b.cost_usd == pytest.approx(0.5)

    def test_charge_raises_when_budget_exceeded(self) -> None:
        b = LoopBudget(limits=LoopLimits(budget_usd=0.10))
        b.charge(0.05)
        with pytest.raises(BudgetExceededError) as exc:
            b.charge(0.10)
        assert exc.value.reason == "budget_usd"
        assert exc.value.snapshot.cost_usd == pytest.approx(0.15)

    def test_charge_rejects_negative(self) -> None:
        b = LoopBudget()
        with pytest.raises(ValueError):
            b.charge(-1.0)

    def test_snapshot_is_immutable(self) -> None:
        b = LoopBudget()
        b.tick()
        snap1 = b.snapshot()
        b.tick()
        snap2 = b.snapshot()
        assert snap1.iterations == 1
        assert snap2.iterations == 2
        assert isinstance(snap1, LoopBudgetSnapshot)
