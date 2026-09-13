"""The ReAct loop must use the one enforcement chokepoint, not its own copy.

``react_tools`` re-implemented contract → autonomy → budget by hand, so every
control added to ``core.orchestration.enforcement`` (plugin capability check,
tool rate limit, pre-hooks, tool audit trail) applied to orchestrated handlers
and silently skipped the ReAct loop — the path that actually calls tools.
"""

from __future__ import annotations

import pytest

from core.orchestration.autonomy import AutonomyLevel, AutonomyPolicy
from core.orchestration.hooks import (
    ToolHookEvent,
    ToolHookRegistry,
    reset_tool_hook_registry,
)
from core.reasoning.react import ReActAgent, ToolDefinition

pytestmark = [pytest.mark.unit]


@pytest.fixture(autouse=True)
def _clean_hooks():
    reset_tool_hook_registry()
    yield
    reset_tool_hook_registry()


def _tool(calls: list, category: str = "read_only", name: str = "t") -> ToolDefinition:
    async def fn(*args, **kwargs):
        calls.append(kwargs or args)
        return "done"

    return ToolDefinition(name=name, fn=fn, description="test tool", category=category)


def _agent(tools: list[ToolDefinition], *, hooks=None, **kwargs) -> ReActAgent:
    """A ReAct agent with a per-agent hook registry.

    ``ToolExecutionMixin`` reads ``_tool_hooks`` off the host, falling back to
    the process-wide registry; setting it here keeps the test isolated.
    """
    agent = ReActAgent(tools=tools, **kwargs)
    agent._tool_hooks = hooks
    return agent


class TestPreHooksReachTheReActLoop:
    async def test_pre_hook_fires_before_the_tool(self) -> None:
        calls: list = []
        seen: list[ToolHookEvent] = []
        registry = ToolHookRegistry()

        async def hook(event: ToolHookEvent) -> None:
            seen.append(event)

        registry.register("pre", "*", hook)
        agent = _agent([_tool(calls)], hooks=registry)

        assert "done" in await agent._execute_tool("t", "")
        assert len(seen) == 1
        assert seen[0].tool_name == "t"
        assert seen[0].phase == "pre"

    async def test_raising_pre_hook_blocks_the_tool(self) -> None:
        calls: list = []
        registry = ToolHookRegistry()

        async def veto(event: ToolHookEvent) -> None:
            raise PermissionError("policy says no")

        registry.register("pre", "*", veto)
        agent = _agent([_tool(calls)], hooks=registry)

        observation = await agent._execute_tool("t", "")
        assert observation.startswith("Error")
        assert "policy says no" in observation
        assert calls == []

    async def test_post_hook_fires_after_the_tool(self) -> None:
        calls: list = []
        seen: list[ToolHookEvent] = []
        registry = ToolHookRegistry()

        async def hook(event: ToolHookEvent) -> None:
            seen.append(event)

        registry.register("post", "*", hook)
        agent = _agent([_tool(calls)], hooks=registry)
        await agent._execute_tool("t", "")

        assert len(seen) == 1
        assert seen[0].phase == "post"
        assert seen[0].metadata["ok"] is True

    async def test_post_hook_reports_a_failed_tool(self) -> None:
        seen: list[ToolHookEvent] = []
        registry = ToolHookRegistry()

        async def hook(event: ToolHookEvent) -> None:
            seen.append(event)

        registry.register("post", "*", hook)

        async def broken(*args, **kwargs):
            raise ValueError("boom")

        agent = _agent(
            [
                ToolDefinition(
                    name="t", fn=broken, description="d", category="read_only"
                )
            ],
            hooks=registry,
        )
        observation = await agent._execute_tool("t", "")
        assert observation.startswith("Error")
        assert seen[0].metadata["ok"] is False


class TestGateStillBehaves:
    """The observable contract of the old hand-rolled gate is preserved."""

    async def test_autonomy_still_fails_closed(self) -> None:
        calls: list = []
        agent = ReActAgent(
            tools=[_tool(calls, category="destructive")],
            autonomy_policy=AutonomyPolicy(level=AutonomyLevel.SUPERVISED),
        )
        observation = await agent._execute_tool("t", "")
        assert "requires human" in observation
        assert calls == []

    async def test_budget_records_exactly_one_call_per_invocation(self) -> None:
        from core.orchestration.limits import LoopBudget, LoopLimits

        calls: list = []
        budget = LoopBudget(limits=LoopLimits(max_tool_calls=5))
        agent = ReActAgent(tools=[_tool(calls)], loop_budget=budget)
        await agent._execute_tool("t", "")
        await agent._execute_tool("t", "")
        assert budget.tool_calls == 2
