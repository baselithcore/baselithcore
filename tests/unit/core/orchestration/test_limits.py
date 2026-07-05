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


class TestTokenBudget:
    def test_token_cap_defaults_off(self) -> None:
        b = LoopBudget()
        assert b.limits.max_tokens is None
        b.record_tokens(1_000_000)  # no cap → never raises
        assert b.tokens == 1_000_000
        assert b.snapshot().tokens == 1_000_000

    def test_record_tokens_accumulates(self) -> None:
        b = LoopBudget(limits=LoopLimits(max_tokens=100))
        b.record_tokens(30)
        b.record_tokens(40)
        assert b.tokens == 70

    def test_record_tokens_raises_over_cap(self) -> None:
        b = LoopBudget(limits=LoopLimits(max_tokens=100))
        b.record_tokens(80)
        with pytest.raises(BudgetExceededError) as exc:
            b.record_tokens(30)
        assert exc.value.reason == "max_tokens"
        assert exc.value.snapshot.tokens == 110

    def test_record_tokens_ignores_nonpositive(self) -> None:
        b = LoopBudget(limits=LoopLimits(max_tokens=10))
        b.record_tokens(0)
        b.record_tokens(-5)
        assert b.tokens == 0

    def test_token_pressure(self) -> None:
        assert LoopBudget().token_pressure() == 0.0  # no cap
        b = LoopBudget(limits=LoopLimits(max_tokens=100))
        b.record_tokens(80)
        assert b.token_pressure() == pytest.approx(0.8)
        # Pressure clamps at 1.0 even if the raise is caught elsewhere.
        b.tokens = 250
        assert b.token_pressure() == 1.0


class TestChargeLLMCostTokens:
    def test_tokens_recorded_for_unpriced_model(self) -> None:
        """Token cap enforces even for models absent from the pricing table."""
        from core.orchestration.budget_context import (
            activate_budget,
            charge_llm_cost,
            deactivate_budget,
        )

        b = LoopBudget(limits=LoopLimits(max_tokens=1000))
        token = activate_budget(b)
        try:
            cost = charge_llm_cost("some-self-hosted-model", 100, 50)
            assert cost == 0.0  # unpriced → no USD charged
            assert b.tokens == 150  # ...but tokens still counted
        finally:
            deactivate_budget(token)

    def test_token_cap_aborts_request(self) -> None:
        from core.orchestration.budget_context import (
            activate_budget,
            charge_llm_cost,
            deactivate_budget,
        )

        b = LoopBudget(limits=LoopLimits(max_tokens=100))
        token = activate_budget(b)
        try:
            with pytest.raises(BudgetExceededError) as exc:
                charge_llm_cost("unlisted-model", 80, 40)
            assert exc.value.reason == "max_tokens"
        finally:
            deactivate_budget(token)


class TestWallClockDeadline:
    def test_no_deadline_by_default(self) -> None:
        b = LoopBudget()
        assert b.remaining_seconds() is None
        b.check_deadline()  # must not raise
        b.tick()

    def test_deadline_exceeded_raises_on_tick(self) -> None:
        b = LoopBudget(limits=LoopLimits(max_seconds=10.0))
        b.started_at -= 11.0  # simulate 11s elapsed
        with pytest.raises(BudgetExceededError) as exc:
            b.tick()
        assert exc.value.reason == "max_seconds"

    def test_remaining_seconds_clamped_at_zero(self) -> None:
        b = LoopBudget(limits=LoopLimits(max_seconds=5.0))
        b.started_at -= 60.0
        assert b.remaining_seconds() == 0.0

    def test_remaining_seconds_counts_down(self) -> None:
        b = LoopBudget(limits=LoopLimits(max_seconds=100.0))
        remaining = b.remaining_seconds()
        assert remaining is not None
        assert 0.0 < remaining <= 100.0

    def test_snapshot_includes_elapsed(self) -> None:
        b = LoopBudget()
        b.started_at -= 2.0
        assert b.snapshot().elapsed_seconds >= 2.0
