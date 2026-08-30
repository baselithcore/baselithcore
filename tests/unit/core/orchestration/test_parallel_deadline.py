"""Parallel tool timeouts must respect the request's wall-clock budget.

``_execute_single`` used only the per-call timeout, so a parallel tool could
outlive ``LoopBudget.max_seconds`` (the sequential path in react_tools already
clamps to ``remaining_seconds()``).
"""

from __future__ import annotations

import time

from core.orchestration.limits import LoopBudget, LoopLimits
from core.orchestration.parallel import ParallelToolExecutor, ToolCall


async def test_parallel_tool_timeout_clamped_to_budget_deadline():
    budget = LoopBudget(LoopLimits(max_seconds=0.3, max_tool_calls=10))
    executor = ParallelToolExecutor(loop_budget=budget)

    async def slow_tool() -> str:
        import asyncio

        await asyncio.sleep(5)
        return "done"

    executor.register_tool("slow", slow_tool, category="read_only")

    started = time.monotonic()
    results = await executor.execute_parallel(
        [ToolCall(tool_name="slow", timeout_seconds=30.0)]
    )
    elapsed = time.monotonic() - started

    assert len(results) == 1
    assert results[0].success is False  # timed out at the budget deadline
    assert elapsed < 2.0  # nowhere near the 30s per-call timeout
