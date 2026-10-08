"""A model that keeps asking for the same tool call must be stopped, not run to the cap."""

from unittest.mock import AsyncMock

import pytest

from core.agent import (
    Agent,
    AgentIterationLimitError,
    AgentLoopError,
    RepeatedToolCallError,
)
from core.services.llm.tool_calling import LLMResult, ToolCall


def _svc(results):
    svc = AsyncMock()
    svc.generate = AsyncMock(side_effect=list(results))
    return svc


def _call(i: int, **args) -> LLMResult:
    return LLMResult(
        tool_calls=[ToolCall(id=str(i), name="fetch", arguments=args or {"url": "x"})],
        stop_reason="tool_use",
    )


def _fetch_tool(counter: list[int]):
    async def fetch(url: str) -> str:
        """Fetch a page."""
        counter.append(1)
        return "error: refused"

    return fetch


@pytest.mark.asyncio
async def test_identical_calls_beyond_limit_are_not_executed_and_told_so():
    ran: list[int] = []
    svc = _svc([_call(1), _call(2), _call(3), LLMResult(text="I cannot open it.")])
    agent = Agent(
        tools=[_fetch_tool(ran)],
        llm_service=svc,
        max_iterations=8,
        max_identical_calls=2,
    )
    result = await agent.run("go")
    assert result.text == "I cannot open it."
    assert len(ran) == 2  # the 3rd identical call never reached the tool
    last_prompt = svc.generate.await_args_list[3].args[0]
    assert "already" in last_prompt.lower() and "same" in last_prompt.lower()


@pytest.mark.asyncio
async def test_persisting_after_the_warning_raises_a_typed_error():
    ran: list[int] = []
    svc = _svc([_call(i) for i in range(1, 9)])
    agent = Agent(
        tools=[_fetch_tool(ran)],
        llm_service=svc,
        max_iterations=8,
        max_identical_calls=2,
    )
    with pytest.raises(RepeatedToolCallError) as info:
        await agent.run("go")
    assert info.value.tool_name == "fetch"
    assert isinstance(info.value, AgentLoopError)
    assert len(ran) == 2
    assert svc.generate.await_count < 8  # stopped early, did not burn the cap


@pytest.mark.asyncio
async def test_different_arguments_are_not_repeats():
    ran: list[int] = []
    results = [_call(i, url=f"u{i}") for i in range(1, 5)] + [LLMResult(text="ok")]
    agent = Agent(
        tools=[_fetch_tool(ran)],
        llm_service=_svc(results),
        max_iterations=8,
        max_identical_calls=2,
    )
    assert (await agent.run("go")).text == "ok"
    assert len(ran) == 4


@pytest.mark.asyncio
async def test_guard_is_off_by_default():
    ran: list[int] = []
    results = [_call(i) for i in range(1, 5)] + [LLMResult(text="ok")]
    agent = Agent(tools=[_fetch_tool(ran)], llm_service=_svc(results), max_iterations=8)
    assert (await agent.run("go")).text == "ok"
    assert len(ran) == 4


@pytest.mark.asyncio
async def test_iteration_cap_raises_typed_error_still_a_runtime_error():
    results = [_call(i, url=f"u{i}") for i in range(1, 6)]
    agent = Agent(tools=[_fetch_tool([])], llm_service=_svc(results), max_iterations=3)
    with pytest.raises(AgentIterationLimitError, match="max_iterations") as info:
        await agent.run("go")
    assert isinstance(info.value, RuntimeError)
    assert info.value.tool_calls_made == ["fetch", "fetch", "fetch"]
