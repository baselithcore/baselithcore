"""Tests for ReActAgent per-tool timeout and transient-error retry."""

from __future__ import annotations

import asyncio

from core.reasoning.react import ReActAgent, ToolDefinition


def _agent(tool, **kwargs) -> ReActAgent:
    return ReActAgent(tools=[tool], **kwargs)


class TestToolTimeout:
    async def test_hung_async_tool_times_out(self) -> None:
        async def hang(*args):
            await asyncio.sleep(30)

        agent = _agent(
            ToolDefinition(name="hang", fn=hang, description="hangs"),
            tool_timeout=0.05,
        )
        result = await agent._execute_tool("hang", "")
        assert "timed out" in result

    async def test_no_timeout_by_default(self) -> None:
        async def quick(*args):
            return "ok"

        agent = _agent(ToolDefinition(name="quick", fn=quick, description="fast"))
        assert agent._tool_timeout is None
        result = await agent._execute_tool("quick", "")
        assert result == "ok"


class TestToolRetry:
    async def test_transient_connection_error_retried(self) -> None:
        calls = []

        async def flaky(*args):
            calls.append(1)
            if len(calls) == 1:
                raise ConnectionError("transient")
            return "recovered"

        agent = _agent(
            ToolDefinition(name="flaky", fn=flaky, description="flaky"),
            tool_retries=1,
            retry_backoff=0.01,
        )
        result = await agent._execute_tool("flaky", "")
        assert result == "recovered"
        assert len(calls) == 2

    async def test_non_transient_error_not_retried(self) -> None:
        calls = []

        async def broken(*args):
            calls.append(1)
            raise ValueError("logic bug")

        agent = _agent(
            ToolDefinition(name="broken", fn=broken, description="broken"),
            tool_retries=3,
            retry_backoff=0.01,
        )
        result = await agent._execute_tool("broken", "")
        assert "logic bug" in result
        assert len(calls) == 1

    async def test_timeout_not_retried(self) -> None:
        calls = []

        async def slow(*args):
            calls.append(1)
            await asyncio.sleep(30)

        agent = _agent(
            ToolDefinition(name="slow", fn=slow, description="slow"),
            tool_timeout=0.05,
            tool_retries=3,
            retry_backoff=0.01,
        )
        result = await agent._execute_tool("slow", "")
        assert "timed out" in result
        assert len(calls) == 1

    async def test_retries_exhausted_returns_error(self) -> None:
        async def always_down(*args):
            raise ConnectionError("still down")

        agent = _agent(
            ToolDefinition(name="down", fn=always_down, description="down"),
            tool_retries=2,
            retry_backoff=0.01,
        )
        result = await agent._execute_tool("down", "")
        assert "still down" in result
