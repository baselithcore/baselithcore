"""
Tests for core/services/llm/_deadline.py.

The wrapper must be a plain await outside an orchestrated request, and map
a deadline overrun to BudgetExceededError("max_seconds") — the same signal
the loop's tick raises.
"""

import asyncio

import pytest

from core.orchestration.budget_context import activate_budget, deactivate_budget
from core.orchestration.limits import BudgetExceededError, LoopBudget, LoopLimits
from core.services.llm._deadline import await_within_deadline


async def _value() -> int:
    return 42


@pytest.mark.asyncio
async def test_plain_await_without_budget():
    assert await await_within_deadline(_value()) == 42


@pytest.mark.asyncio
async def test_plain_await_with_budget_but_no_deadline():
    token = activate_budget(LoopBudget(limits=LoopLimits()))
    try:
        assert await await_within_deadline(_value()) == 42
    finally:
        deactivate_budget(token)


@pytest.mark.asyncio
async def test_returns_result_within_deadline():
    token = activate_budget(LoopBudget(limits=LoopLimits(max_seconds=5.0)))
    try:
        assert await await_within_deadline(_value()) == 42
    finally:
        deactivate_budget(token)


@pytest.mark.asyncio
async def test_overrun_maps_to_budget_exceeded():
    budget = LoopBudget(limits=LoopLimits(max_seconds=0.05))
    token = activate_budget(budget)
    try:

        async def slow() -> None:
            await asyncio.sleep(5.0)

        with pytest.raises(BudgetExceededError) as excinfo:
            await await_within_deadline(slow())
        assert excinfo.value.reason == "max_seconds"
    finally:
        deactivate_budget(token)
