"""
Ambient-budget deadline enforcement for provider calls.

``LoopLimits.max_seconds`` gives an orchestrated request a wall-clock
deadline, but a single provider call could outlive it: the SDK-level
request timeout is static (e.g. 120s) and knows nothing about how much of
the request's deadline is already spent. This module bridges the two — it
bounds one awaitable by the ambient :class:`~core.orchestration.limits.LoopBudget`'s
remaining seconds, so the per-call timeout shrinks as the request ages.

Outside an orchestrated request (no ambient budget, or no ``max_seconds``)
the wrapper is a plain ``await`` — zero behavior change for direct
LLMService callers.
"""

from __future__ import annotations

from collections.abc import Awaitable
from typing import TypeVar

__all__ = ["await_within_deadline"]

T = TypeVar("T")


async def await_within_deadline(awaitable: Awaitable[T]) -> T:
    """Await ``awaitable``, bounded by the ambient budget's remaining time.

    A deadline overrun cancels the underlying call (freeing its connection)
    and surfaces as ``BudgetExceededError("max_seconds")`` — the same signal
    the orchestrator loop's ``tick()`` raises — instead of a bare
    ``TimeoutError`` that callers would misread as a transient network issue.
    """
    import asyncio

    # Lazy: a module-level import of core.orchestration would be circular
    # (orchestration handlers import the LLM service).
    from core.orchestration.budget_context import get_active_budget
    from core.orchestration.limits import BudgetExceededError

    budget = get_active_budget()
    remaining = budget.remaining_seconds() if budget is not None else None
    if budget is None or remaining is None:
        return await awaitable
    try:
        return await asyncio.wait_for(awaitable, timeout=remaining)
    except TimeoutError:
        raise BudgetExceededError("max_seconds", budget.snapshot()) from None
