"""The ReAct loop must tick the LoopBudget once per iteration.

Only tool calls were recorded, so ``LoopLimits.max_iterations`` was never
enforced per agent iteration: a run whose budget allowed 1 iteration happily
looped up to the agent's own ``max_iterations``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from core.orchestration.limits import BudgetExceededError, LoopBudget, LoopLimits
from core.reasoning.react import ReActAgent, ToolDefinition

# An Action keeps the loop spinning (no action = output treated as final).
_LOOPING_OUTPUT = "Thought: still thinking\nAction: probe()"


def _probe_tool() -> ToolDefinition:
    async def fn(*args, **kwargs):
        return "probed"

    return ToolDefinition(
        name="probe", fn=fn, description="test tool", category="read_only"
    )


async def test_loop_budget_caps_react_iterations():
    budget = LoopBudget(LoopLimits(max_iterations=1, max_tool_calls=50))
    agent = ReActAgent(tools=[_probe_tool()], max_iterations=5, loop_budget=budget)
    # Never a final answer: the loop would spin to the agent's own
    # max_iterations (5) without the budget tick.
    agent._call_llm = AsyncMock(return_value=_LOOPING_OUTPUT)

    with pytest.raises(BudgetExceededError):
        await agent.run("q")

    assert agent._call_llm.await_count <= 2  # stopped by the budget, not by 5


async def test_native_loop_ticks_budget_too(monkeypatch):
    """The native tool-calling loop must enforce the same per-pass budget as
    the text-parsed loop — a budget that only bounds one variant is not a
    budget."""
    from types import SimpleNamespace

    from core.reasoning.react_native import run_native_loop
    from core.services.llm.tool_calling import LLMResult, ToolCall

    budget = LoopBudget(LoopLimits(max_iterations=1, max_tool_calls=50))
    agent = ReActAgent(tools=[_probe_tool()], max_iterations=5, loop_budget=budget)

    class _LoopingLLM:
        config = SimpleNamespace(enable_native_tools=True)
        provider = SimpleNamespace(supports_native_tools=True)

        async def generate(self, prompt, model=None, *, tools=None, **kwargs):
            return LLMResult(
                text=None,
                tool_calls=[ToolCall(id="c1", name="probe", arguments={})],
                stop_reason="tool_use",
            )

    monkeypatch.setattr(agent, "_get_llm_service", lambda: _LoopingLLM())

    with pytest.raises(BudgetExceededError):
        await run_native_loop(agent, "q")


async def test_react_without_budget_keeps_own_cap():
    agent = ReActAgent(tools=[_probe_tool()], max_iterations=2)
    agent._call_llm = AsyncMock(return_value=_LOOPING_OUTPUT)

    result = await agent.run("q")

    # No budget: the agent's own iteration cap still bounds the loop.
    assert agent._call_llm.await_count == 2
    assert result is not None
