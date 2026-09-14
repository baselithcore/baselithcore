"""Golden trajectories for the typed ``Agent`` loop.

Each test drives the real :class:`core.agent.Agent` with a recorded cassette.
The cassette asserts, turn by turn, what the loop sent to the provider — the
tools offered, the shape of the conversation, the tool results fed back, the
validation-retry wording — so a change in prompt assembly fails here even
though every unit test that mocks ``LLMService`` keeps passing.

The cassettes pin the **message-based** shape: an append-only history, one
user message carrying every tool result of a turn, each correlated by
``tool_use_id`` and sealed in the untrusted-content envelope. One cassette
(``agent_tool_loop_legacy``) deliberately pins the flattened-transcript path a
service predating the message API still receives.
"""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from core.agent import Agent
from core.orchestration.tool_output import unwrap_untrusted
from core.services.llm.messages import ToolResultBlock
from tests.golden.cassette import Cassette, CassetteMismatch, RecordedLLMService


def _results_of(call: dict) -> list[ToolResultBlock]:
    """The tool results carried by the last message of a recorded call."""
    return [b for b in call["messages"][-1].content if isinstance(b, ToolResultBlock)]


class CityInfo(BaseModel):
    city: str
    population: int


def lookup_capital(country: str) -> str:
    """Return the capital city of ``country``."""
    return {"Italy": "Rome"}.get(country, "unknown")


def get_order(order_id: int) -> dict[str, object]:
    """Fetch an order by id."""
    return {"order_id": order_id, "shipment_id": "SHP-7"}


def get_shipment(shipment_id: str) -> str:
    """Fetch the shipment status."""
    return "in transit, ETA 2 days" if shipment_id == "SHP-7" else "unknown"


@pytest.mark.asyncio
async def test_tool_loop_matches_cassette(golden_llm) -> None:
    svc = golden_llm("agent_tool_loop")
    agent = Agent(tools=[lookup_capital], llm_service=svc)

    result = await agent.run("What is the capital of Italy?")

    assert result.output == "The capital of Italy is Rome."
    assert result.tool_calls_made == ["lookup_capital"]
    assert result.iterations == 2
    # The first turn offered the tool with the schema inferred from the signature.
    offered = svc.calls[0]["tools"][0]
    assert offered.name == "lookup_capital"
    assert offered.parameters["properties"]["country"]["type"] == "string"


@pytest.mark.asyncio
async def test_structured_output_retry_feeds_validation_error_back(golden_llm) -> None:
    svc = golden_llm("agent_structured_retry")
    agent = Agent(output_type=CityInfo, llm_service=svc)

    result = await agent.run("Give me population data for Rome")

    assert result.output == CityInfo(city="Rome", population=2870000)
    assert result.iterations == 2
    # Strict structured output was requested on both turns with the model's schema.
    for call in svc.calls:
        assert call["response_format"].strict is True
        assert call["response_format"].schema == CityInfo.model_json_schema()


@pytest.mark.asyncio
async def test_sequential_tools_accumulate_results(golden_llm) -> None:
    svc = golden_llm("agent_multi_tool_order")
    agent = Agent(tools=[get_order, get_shipment], llm_service=svc)

    result = await agent.run("Where is order 42?")

    assert result.tool_calls_made == ["get_order", "get_shipment"]
    assert result.iterations == 3
    # The second tool's arguments came from the first tool's JSON result,
    # which travelled back as a correlated tool_result block.
    first = _results_of(svc.calls[1])[0]
    assert first.tool_use_id == "call_1"
    assert json.loads(unwrap_untrusted(first.content)) == {
        "order_id": 42,
        "shipment_id": "SHP-7",
    }
    # The history is append-only: the last turn still carries the first
    # exchange, and answers only the newest call.
    assert [m.role for m in svc.calls[2]["messages"]] == [
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
    ]
    assert [b.tool_use_id for b in _results_of(svc.calls[2])] == ["call_2"]


@pytest.mark.asyncio
async def test_parallel_tool_results_share_one_message(golden_llm) -> None:
    """Two calls in one assistant turn are answered by ONE user message.

    Splitting them across messages is not a style choice: a provider rejects a
    conversation whose ``tool_use`` blocks are not all answered by the turn
    immediately following them.
    """
    svc = golden_llm("agent_parallel_tools")
    agent = Agent(tools=[get_order, get_shipment], llm_service=svc)

    result = await agent.run("Look both up at once")

    assert result.tool_calls_made == ["get_order", "get_shipment"]
    answering = svc.calls[1]["messages"][-1]
    assert len(answering.content) == 2
    assert [b.tool_use_id for b in answering.content] == ["par_1", "par_2"]


@pytest.mark.asyncio
async def test_a_failing_tool_comes_back_flagged(golden_llm) -> None:
    """``is_error`` is the whole point: a failure the model cannot see is one
    it will not correct."""
    svc = golden_llm("agent_tool_failure")

    def broken_lookup(country: str) -> str:
        """Always fails."""
        raise RuntimeError("upstream registry unavailable")

    agent = Agent(tools=[broken_lookup], llm_service=svc)
    result = await agent.run("What is the capital of Italy?")

    assert result.output == "I could not reach the registry."
    failed = _results_of(svc.calls[1])[0]
    assert failed.is_error is True
    assert failed.tool_use_id == "call_1"


@pytest.mark.asyncio
async def test_a_service_without_the_message_api_gets_a_transcript(golden_llm) -> None:
    """The legacy path is still live, and still pinned end to end."""
    svc = RecordedLLMService(
        Cassette.load("agent_tool_loop_legacy"), supports_messages=False
    )
    agent = Agent(tools=[lookup_capital], llm_service=svc)

    result = await agent.run("What is the capital of Italy?")

    assert result.output == "The capital of Italy is Rome."
    assert "messages" not in svc.calls[1]
    svc.assert_exhausted()


@pytest.mark.asyncio
async def test_cassette_drift_fails_loudly(golden_llm) -> None:
    svc = golden_llm("agent_mismatch_probe")
    agent = Agent(tools=[lookup_capital], llm_service=svc)

    with pytest.raises(CassetteMismatch, match="tools offered"):
        await agent.run("anything")
    # The turn was consumed while checking, so teardown's exhaustion check passes.


@pytest.mark.asyncio
async def test_unplayed_turns_are_reported() -> None:
    from tests.golden.cassette import Cassette, RecordedLLMService

    svc = RecordedLLMService(Cassette.load("agent_tool_loop"))
    agent = Agent(llm_service=svc)  # no tools: the first turn's tool call is ignored

    with pytest.raises(CassetteMismatch, match="tools offered"):
        await agent.run("What is the capital of Italy?")
    with pytest.raises(AssertionError, match="never played"):
        svc.assert_exhausted()
