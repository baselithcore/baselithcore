"""``Agent.run_events`` streams text within a turn (many ``TextDelta``).

A real ``LLMService`` runs over a fake streaming provider, so the whole chain
under test is the production one: loop -> bridge task -> message runtime ->
provider stream. The consumer contract pinned here: any number of deltas per
turn, tool events only once a call is complete, ``Completed`` carries the
consolidated text, and leaving the iteration stops the model call.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest

from baselith.core.agent import (
    Agent,
    Completed,
    Failed,
    TextDelta,
    ToolCallFinished,
    ToolCallStarted,
)
from baselith.core.reasoning.react import ToolDefinition
from baselith.core.services.llm.exceptions import (
    LLMProviderError,
    StreamInterruptedError,
)
from baselith.core.services.llm.stream_events import StreamEnd
from baselith.core.services.llm.stream_events import TextDelta as StreamText
from baselith.core.services.llm.tool_calling import LLMResult, ToolCall


class ScriptedProvider:
    """One script per model turn; a script is chunks, then an optional call."""

    supports_native_tools = True
    supports_messages = True
    supports_streaming_tools = True

    def __init__(self, turns: list[list[Any]]) -> None:
        self.turns = turns
        self.streamed = 0
        self.closed: list[bool] = []

    async def generate_messages(self, messages, model, **kwargs):
        script = self.turns.pop(0)
        return _result(script)

    async def generate_messages_stream(self, messages, model, **kwargs):
        script = self.turns.pop(0)
        self.streamed += 1
        self.closed.append(False)
        try:
            for item in script:
                if isinstance(item, str):
                    if item == "<hang>":
                        await asyncio.Event().wait()
                    yield StreamText(item)
                elif isinstance(item, BaseException):
                    raise item
                await asyncio.sleep(0)
            yield StreamEnd(_result(script))
        finally:
            self.closed[-1] = True

    async def close(self) -> None:
        return None


def _result(script: list[Any]) -> LLMResult:
    text = "".join(i for i in script if isinstance(i, str) and i != "<hang>")
    calls = [i for i in script if isinstance(i, ToolCall)]
    return LLMResult(
        text=text or None,
        tool_calls=calls,
        stop_reason="tool_calls" if calls else "stop",
    )


def _service(provider, *, stream: bool = True):
    from baselith.core.config.services import LLMConfig
    from baselith.core.services.llm.service import LLMService

    config = LLMConfig(
        provider="ollama", model="m", fallback_chain="", stream_agent_text=stream
    )
    with patch.object(LLMService, "_create_provider", return_value=provider):
        return LLMService(config=config, enable_cache=False)


async def _clock() -> str:
    """The time."""
    return "12:00"


CLOCK = ToolDefinition(name="_clock", fn=_clock, description="t", category="read_only")


@pytest.fixture(autouse=True)
def _no_tenant_ledger(monkeypatch):
    async def _record(usd, **_kwargs):
        return None

    monkeypatch.setattr(
        "baselith.core.quotas.cost_enforcement.record_tenant_llm_cost", _record
    )


async def _collect(agent, prompt="q"):
    return [event async for event in agent.run_events(prompt)]


async def test_a_turn_yields_many_deltas_and_completed_is_consolidated():
    provider = ScriptedProvider([["Ci", "ao ", "mondo"]])
    events = await _collect(Agent(llm_service=_service(provider)))

    deltas = [e.text for e in events if isinstance(e, TextDelta)]
    assert deltas == ["Ci", "ao ", "mondo"]
    assert isinstance(events[-1], Completed)
    assert events[-1].result.text == "Ciao mondo"


async def test_text_before_a_tool_call_streams_then_the_call_then_the_answer():
    call = ToolCall(id="c1", name="_clock", arguments={})
    provider = ScriptedProvider([["Let me ", "check.", call], ["It is ", "noon."]])
    events = await _collect(Agent(tools=[CLOCK], llm_service=_service(provider)))

    kinds = [e.text if isinstance(e, TextDelta) else type(e).__name__ for e in events]
    assert kinds == [
        "Let me ",
        "check.",
        "ToolCallStarted",
        "ToolCallFinished",
        "It is ",
        "noon.",
        "Completed",
    ]
    started = next(e for e in events if isinstance(e, ToolCallStarted))
    assert started.name == "_clock"
    assert any(isinstance(e, ToolCallFinished) for e in events)


async def test_run_awaited_does_not_bridge_through_a_task_and_still_answers():
    provider = ScriptedProvider([["a", "b"]])
    result = await Agent(llm_service=_service(provider)).run("q")
    assert result.text == "ab"


async def test_kill_switch_off_is_one_delta_per_turn_again():
    provider = ScriptedProvider([["a", "b", "c"]])
    events = await _collect(Agent(llm_service=_service(provider, stream=False)))

    assert [e.text for e in events if isinstance(e, TextDelta)] == ["abc"]
    assert provider.streamed == 0


async def test_a_provider_that_cannot_stream_yields_one_delta():
    provider = ScriptedProvider([["a", "b"]])
    provider.supports_streaming_tools = False
    events = await _collect(Agent(llm_service=_service(provider)))
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["ab"]


async def test_leaving_the_iteration_stops_the_model_call():
    provider = ScriptedProvider([["first", "<hang>"]])
    agent = Agent(llm_service=_service(provider))
    events = agent.run_events("q")

    first = await events.__anext__()
    assert isinstance(first, TextDelta) and first.text == "first"
    await events.aclose()

    assert provider.closed == [True]


async def test_cancelling_the_consumer_closes_the_provider_stream():
    provider = ScriptedProvider([["first", "<hang>"]])
    agent = Agent(llm_service=_service(provider))
    seen: list[str] = []

    async def consume() -> None:
        async for event in agent.run_events("q"):
            if isinstance(event, TextDelta):
                seen.append(event.text)

    task = asyncio.ensure_future(consume())
    for _ in range(200):
        if seen:
            break
        await asyncio.sleep(0.005)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert seen == ["first"] and provider.closed == [True]


async def test_a_failure_before_any_chunk_is_a_failed_event_with_no_text():
    provider = ScriptedProvider([[LLMProviderError("boom")]])
    events = await _collect(Agent(llm_service=_service(provider)))

    assert [type(e) for e in events] == [Failed]


async def test_a_failure_after_chunks_keeps_the_partial_on_the_failed_event():
    provider = ScriptedProvider([["Hel", "lo", LLMProviderError("reset")]])
    events = await _collect(Agent(llm_service=_service(provider)))

    assert [e.text for e in events if isinstance(e, TextDelta)] == ["Hel", "lo"]
    failed = events[-1]
    assert isinstance(failed, Failed)
    assert isinstance(failed.error, StreamInterruptedError)
    assert failed.error.partial_text == "Hello"
