"""A turn's tool calls run off the event loop, bounded, and overlapped.

The typed loop used to call ``definition.fn(**args)`` inline, one call after
another, with no deadline. Three consequences, one test class each:

* a synchronous tool ran **on the event loop**, so one blocking call stalled
  every other in-flight request in the process;
* a tool that never returned pinned the agent, because nothing else in the
  typed loop carries a deadline;
* a multi-tool turn paid the sum of its latencies although the provider had
  emitted every call before seeing any result.

The ReAct executor already had all three. What it also has, and what the last
class pins, is that gates run strictly in order *ahead* of execution: an
approval or budget refusal is fail-closed and aborts the turn, so a tool later
in the turn must not already be running when an earlier one is denied.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import pytest

from core.agent.agent import Agent
from core.reasoning.react import ToolDefinition
from core.services.llm.tool_calling import LLMResult, ToolCall


class _ScriptedService:
    """Replies with one tool-calling turn, then a final answer."""

    supports_messages = True

    def __init__(self, calls: list[ToolCall]) -> None:
        self._turns = [
            LLMResult(text=None, tool_calls=calls, native=True),
            LLMResult(text="done", tool_calls=[], native=True),
        ]

    async def generate_messages(self, _messages: list[Any], **_kwargs: Any) -> Any:
        return self._turns.pop(0)


def _read_only(name: str, fn: Any) -> ToolDefinition:
    """A tool the ledger and approval gate leave alone."""
    return ToolDefinition(
        name=name, fn=fn, description=f"{name} tool", category="read_only"
    )


def _call(name: str, call_id: str | None = None) -> ToolCall:
    return ToolCall(id=call_id or f"tu_{name}", name=name, arguments={})


class TestSyncToolsLeaveTheEventLoop:
    async def test_blocking_tool_does_not_stall_the_loop(self) -> None:
        released = asyncio.Event()
        ticks = 0

        def blocking() -> str:
            time.sleep(0.15)
            return "slept"

        async def ticker() -> None:
            nonlocal ticks
            while not released.is_set():
                ticks += 1
                await asyncio.sleep(0.01)

        agent = Agent(
            tools=[_read_only("blocking", blocking)],
            llm_service=_ScriptedService([_call("blocking")]),
        )
        background = asyncio.create_task(ticker())
        await agent.run("go")
        released.set()
        await background

        # On the event loop the ticker could not have run at all.
        assert ticks > 3

    async def test_sync_tool_runs_in_a_worker_thread(self) -> None:
        seen: list[int] = []

        def where() -> str:
            seen.append(threading.get_ident())
            return "ok"

        agent = Agent(
            tools=[_read_only("where", where)],
            llm_service=_ScriptedService([_call("where")]),
        )
        await agent.run("go")

        assert seen and seen[0] != threading.get_ident()


class TestDeadline:
    async def test_a_hung_tool_fails_the_call_not_the_agent(self) -> None:
        async def hang() -> str:
            await asyncio.sleep(30)
            return "never"

        agent = Agent(
            tools=[_read_only("hang", hang)],
            llm_service=_ScriptedService([_call("hang")]),
            tool_timeout=0.05,
        )

        result = await asyncio.wait_for(agent.run("go"), timeout=5)

        assert result.text == "done"
        observation = result.messages[-2].content[0].content
        assert "failed" in observation.lower()

    async def test_budget_deadline_shrinks_the_cap(self) -> None:
        from core.agent._tool_runtime import effective_tool_timeout
        from core.orchestration.budget_context import activate_budget, deactivate_budget
        from core.orchestration.limits import LoopBudget, LoopLimits

        agent = Agent(tool_timeout=100.0)
        budget = LoopBudget(limits=LoopLimits(max_seconds=1.0))
        token = activate_budget(budget)
        try:
            bounded = effective_tool_timeout(agent)
        finally:
            deactivate_budget(token)

        assert bounded is not None
        assert bounded <= 1.0

    async def test_no_cap_and_no_budget_means_no_deadline(self) -> None:
        from core.agent._tool_runtime import effective_tool_timeout

        assert effective_tool_timeout(Agent(tool_timeout=None)) is None


class TestOverlap:
    async def test_a_multi_tool_turn_overlaps_its_calls(self) -> None:
        async def slow() -> str:
            await asyncio.sleep(0.1)
            return "ok"

        agent = Agent(
            tools=[
                _read_only("a", slow),
                _read_only("b", slow),
                _read_only("c", slow),
            ],
            llm_service=_ScriptedService(
                [_call("a"), _call("b"), _call("c")],
            ),
        )

        started = time.perf_counter()
        result = await agent.run("go")
        elapsed = time.perf_counter() - started

        assert result.tool_calls_made == ["a", "b", "c"]
        assert elapsed < 0.25, "three 0.1s calls ran serially"

    async def test_results_keep_the_order_the_model_asked_in(self) -> None:
        async def fast() -> str:
            return "fast"

        async def slow() -> str:
            await asyncio.sleep(0.05)
            return "slow"

        agent = Agent(
            tools=[_read_only("slow", slow), _read_only("fast", fast)],
            llm_service=_ScriptedService([_call("slow"), _call("fast")]),
        )

        result = await agent.run("go")

        assert result.tool_calls_made == ["slow", "fast"]
        blocks = result.messages[-2].content
        assert [block.tool_use_id for block in blocks] == ["tu_slow", "tu_fast"]
        assert "slow" in blocks[0].content
        assert "fast" in blocks[1].content

    async def test_an_unknown_tool_does_not_displace_the_others(self) -> None:
        async def ok() -> str:
            return "ok"

        agent = Agent(
            tools=[_read_only("ok", ok)],
            llm_service=_ScriptedService([_call("ghost"), _call("ok")]),
        )

        result = await agent.run("go")

        blocks = result.messages[-2].content
        assert blocks[0].is_error
        assert "unknown tool" in blocks[0].content
        assert not blocks[1].is_error


class TestGateOrderIsPreserved:
    async def test_a_refusal_aborts_the_turn_before_later_tools_run(self) -> None:
        """Fail-closed refusals must not race the calls that follow them."""
        from core.orchestration.limits import BudgetExceededError

        ran: list[str] = []

        async def marker(name: str) -> str:
            ran.append(name)
            return name

        async def first() -> str:
            return await marker("first")

        async def second() -> str:
            return await marker("second")

        agent = Agent(
            tools=[_read_only("first", first), _read_only("second", second)],
            llm_service=_ScriptedService([_call("first"), _call("second")]),
        )

        import core.agent._tool_dispatch as dispatch
        from core.orchestration.limits import LoopBudget

        original = dispatch._gate
        snapshot = LoopBudget().snapshot()

        async def gate(definition, call, context):
            if definition.name == "second":
                raise BudgetExceededError("tool budget exhausted", snapshot)
            return await original(definition, call, context)

        dispatch._gate = gate
        try:
            with pytest.raises(BudgetExceededError):
                await agent.run("go")
        finally:
            dispatch._gate = original

        assert ran == [], "a denied turn must not have run any of its tools"
