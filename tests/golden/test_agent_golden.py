"""Golden trajectories for the typed ``Agent`` loop.

Each test drives the real :class:`core.agent.Agent` with a recorded cassette.
The cassette asserts, turn by turn, what the loop sent to the provider — the
tools offered, the tool results fed back, the validation-retry wording — so
a change in prompt assembly fails here even though every unit test that
mocks ``LLMService`` keeps passing.
"""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from core.agent import Agent
from tests.golden.cassette import CassetteMismatch


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
    # The second tool's arguments came from the first tool's JSON result.
    assert json.loads(
        svc.calls[1]["prompt"].split("[get_order] -> ", 1)[1].splitlines()[0]
    ) == {
        "order_id": 42,
        "shipment_id": "SHP-7",
    }


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
