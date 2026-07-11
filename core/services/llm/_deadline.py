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

from collections.abc import AsyncIterator, Awaitable
from typing import TypeVar

__all__ = ["await_within_deadline", "stream_within_deadline"]

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


async def stream_within_deadline(stream: AsyncIterator[T]) -> AsyncIterator[T]:
    """Yield from ``stream``, each chunk bounded by the budget's remaining time.

    The streaming path historically bypassed deadline enforcement: a stalled
    or slow provider stream could outlive the request's ``max_seconds``
    wall-clock. Here every ``__anext__`` is capped by the ambient budget's
    remaining seconds; an overrun cancels the underlying stream and surfaces
    as ``BudgetExceededError("max_seconds")`` — consistent with the
    non-streaming path. Outside an orchestrated request this is a plain
    pass-through.
    """
    import asyncio

    # Lazy: a module-level import of core.orchestration would be circular.
    from core.orchestration.budget_context import get_active_budget
    from core.orchestration.limits import BudgetExceededError

    budget = get_active_budget()
    if budget is None or budget.remaining_seconds() is None:
        async for item in stream:
            yield item
        return

    iterator = stream.__aiter__()
    while True:
        remaining = budget.remaining_seconds()
        if remaining is not None and remaining <= 0:
            raise BudgetExceededError("max_seconds", budget.snapshot())
        try:
            item = await asyncio.wait_for(iterator.__anext__(), timeout=remaining)
        except StopAsyncIteration:
            return
        except TimeoutError:
            raise BudgetExceededError("max_seconds", budget.snapshot()) from None
        yield item
