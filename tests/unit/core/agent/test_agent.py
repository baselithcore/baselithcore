"""Unit tests for the typed developer-facing Agent API."""

import json
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

from core.agent import Agent, AgentOutputValidationError, AgentResult
from core.services.llm.tool_calling import LLMResult, ToolCall


class CityInfo(BaseModel):
    city: str
    population: int


def _mock_service(results):
    """LLMService stub whose generate() pops canned LLMResults in order."""
    svc = AsyncMock()
    svc.generate = AsyncMock(side_effect=list(results))
    svc.generate_response = AsyncMock(return_value="plain answer")

    async def _stream(*a, **k):
        for chunk in ("hel", "lo"):
            yield chunk

    svc.generate_response_stream = _stream
    return svc


class TestPlainRun:
    @pytest.mark.asyncio
    async def test_plain_text_run(self):
        svc = _mock_service([LLMResult(text="Rome is the capital.")])
        agent = Agent(llm_service=svc)
        result = await agent.run("capital of Italy?")
        assert isinstance(result, AgentResult)
        assert result.output == "Rome is the capital."
        assert result.iterations == 1

    @pytest.mark.asyncio
    async def test_system_prompt_and_model_forwarded(self):
        svc = _mock_service([LLMResult(text="ok")])
        agent = Agent(model="my-model", system_prompt="be terse", llm_service=svc)
        await agent.run("q")
        kwargs = svc.generate.await_args.kwargs
        assert kwargs["model"] == "my-model"
        assert kwargs["system_prompt"] == "be terse"


class TestTypedOutput:
    @pytest.mark.asyncio
    async def test_output_type_parsed_and_validated(self):
        payload = json.dumps({"city": "Rome", "population": 2870000})
        svc = _mock_service([LLMResult(text=payload)])
        agent = Agent(output_type=CityInfo, llm_service=svc)
        result = await agent.run("info on Rome")
        assert isinstance(result.output, CityInfo)
        assert result.output.city == "Rome"
        # A response_format constraint was requested.
        assert svc.generate.await_args.kwargs["response_format"] is not None

    @pytest.mark.asyncio
    async def test_validation_error_retries_with_feedback(self):
        bad = json.dumps({"city": "Rome", "population": "a lot"})
        good = json.dumps({"city": "Rome", "population": 2870000})
        svc = _mock_service([LLMResult(text=bad), LLMResult(text=good)])
        agent = Agent(output_type=CityInfo, llm_service=svc, max_retries=2)
        result = await agent.run("info on Rome")
        assert result.output.population == 2870000
        assert svc.generate.await_count == 2
        retry_prompt = svc.generate.await_args_list[1].args[0]
        assert "population" in retry_prompt  # validation feedback included

    @pytest.mark.asyncio
    async def test_retries_exhausted_raises(self):
        bad = json.dumps({"city": "Rome"})
        svc = _mock_service([LLMResult(text=bad)] * 3)
        agent = Agent(output_type=CityInfo, llm_service=svc, max_retries=2)
        with pytest.raises(AgentOutputValidationError):
            await agent.run("info")

    @pytest.mark.asyncio
    async def test_code_fenced_json_is_tolerated(self):
        fenced = '```json\n{"city": "Rome", "population": 1}\n```'
        svc = _mock_service([LLMResult(text=fenced)])
        agent = Agent(output_type=CityInfo, llm_service=svc)
        result = await agent.run("q")
        assert result.output.city == "Rome"


class TestTools:
    @pytest.mark.asyncio
    async def test_tool_loop_executes_and_finishes(self):
        async def lookup_population(city: str) -> str:
            """Look up a city's population."""
            return "2870000"

        svc = _mock_service(
            [
                LLMResult(
                    tool_calls=[
                        ToolCall(id="1", name="lookup_population", arguments={"city": "Rome"})
                    ],
                    stop_reason="tool_use",
                ),
                LLMResult(text="Rome has 2870000 inhabitants."),
            ]
        )
        agent = Agent(tools=[lookup_population], llm_service=svc)
        result = await agent.run("population of Rome?")
        assert "2870000" in result.output
        assert result.tool_calls_made == ["lookup_population"]
        assert result.iterations == 2
        # Tool result was fed back to the model.
        follow_up = svc.generate.await_args_list[1].args[0]
        assert "2870000" in follow_up
        # Tool specs were passed on the first call.
        specs = svc.generate.await_args_list[0].kwargs["tools"]
        assert specs[0].name == "lookup_population"
        assert specs[0].parameters["properties"]["city"]["type"] == "string"

    @pytest.mark.asyncio
    async def test_sync_tool_supported(self):
        def add(a: int, b: int) -> int:
            """Add two integers."""
            return a + b

        svc = _mock_service(
            [
                LLMResult(
                    tool_calls=[ToolCall(id="1", name="add", arguments={"a": 2, "b": 3})],
                    stop_reason="tool_use",
                ),
                LLMResult(text="The sum is 5."),
            ]
        )
        agent = Agent(tools=[add], llm_service=svc)
        result = await agent.run("2+3?")
        assert result.output == "The sum is 5."

    @pytest.mark.asyncio
    async def test_unknown_tool_call_feeds_error_back(self):
        svc = _mock_service(
            [
                LLMResult(
                    tool_calls=[ToolCall(id="1", name="nope", arguments={})],
                    stop_reason="tool_use",
                ),
                LLMResult(text="done"),
            ]
        )
        agent = Agent(llm_service=svc)
        result = await agent.run("q")
        assert result.output == "done"
        follow_up = svc.generate.await_args_list[1].args[0]
        assert "unknown tool" in follow_up.lower()

    @pytest.mark.asyncio
    async def test_iteration_cap_enforced(self):
        looping = LLMResult(
            tool_calls=[ToolCall(id="1", name="nope", arguments={})],
            stop_reason="tool_use",
        )
        svc = _mock_service([looping] * 10)
        agent = Agent(llm_service=svc, max_iterations=3)
        with pytest.raises(RuntimeError, match="max_iterations"):
            await agent.run("q")


class TestStream:
    @pytest.mark.asyncio
    async def test_run_stream_yields_chunks(self):
        svc = _mock_service([])
        agent = Agent(llm_service=svc)
        chunks = [c async for c in agent.run_stream("hi")]
        assert "".join(chunks) == "hello"

    @pytest.mark.asyncio
    async def test_run_stream_rejects_typed_output(self):
        svc = _mock_service([])
        agent = Agent(output_type=CityInfo, llm_service=svc)
        with pytest.raises(ValueError, match="output_type"):
            async for _ in agent.run_stream("hi"):
                pass
