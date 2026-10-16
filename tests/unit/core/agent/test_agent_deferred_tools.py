"""Deferred tools: callable all along, advertised to the model only once revealed."""

from unittest.mock import AsyncMock

import pytest

from baselith.core.agent import Agent
from baselith.core.services.llm.tool_calling import LLMResult, ToolCall


def _service(results):
    """A message-capable stub recording the tool names offered on every call."""
    svc = AsyncMock()
    svc.supports_messages = True
    svc.offered = []
    queued = list(results)

    async def _record(messages, **kwargs):
        svc.offered.append(sorted(s.name for s in kwargs.get("tools") or ()))
        return queued.pop(0)

    svc.generate_messages = AsyncMock(side_effect=_record)
    return svc


def _call(name, arguments, call_id="t1"):
    return LLMResult(
        tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)],
        stop_reason="tool_use",
    )


async def weather(city: str) -> str:
    """Today's weather in a city."""
    return f"{city}: sunny"


async def stocks(symbol: str) -> str:
    """A stock quote."""
    return f"{symbol}: 10"


def test_defer_and_reveal_bookkeeping():
    agent = Agent(tools=[weather, stocks], llm_service=_service([]))
    agent.defer_tools(["stocks", "unknown"])
    assert [s.name for s in agent._tool_specs()] == ["weather"]
    assert agent.tool_names == ("weather", "stocks")  # still armed with both
    assert agent.reveal_tools(["stocks", "stocks", "weather", "nope"]) == ("stocks",)
    assert sorted(s.name for s in agent._tool_specs()) == ["stocks", "weather"]
    assert agent.reveal_tools(["stocks"]) == ()


@pytest.mark.asyncio
async def test_a_tool_revealing_others_changes_the_next_call():
    holder: dict[str, Agent] = {}

    async def find_tools(area: str) -> str:
        """Load the tools for an area."""
        return ",".join(holder["agent"].reveal_tools(["stocks"]))

    svc = _service([_call("find_tools", {"area": "finance"}), LLMResult(text="ok")])
    agent = Agent(tools=[weather, stocks, find_tools], llm_service=svc)
    agent.defer_tools(["stocks"])
    holder["agent"] = agent
    result = await agent.run("price of ACME?")
    assert result.text == "ok"
    assert svc.offered == [
        ["find_tools", "weather"],
        ["find_tools", "stocks", "weather"],
    ]


@pytest.mark.asyncio
async def test_a_deferred_tool_called_by_name_still_runs():
    svc = _service([_call("stocks", {"symbol": "ACME"}), LLMResult(text="10")])
    agent = Agent(tools=[weather, stocks], llm_service=svc)
    agent.defer_tools(["stocks"])
    result = await agent.run("price of ACME?")
    assert result.tool_calls_made == ["stocks"]
    assert svc.offered[0] == ["weather"]
