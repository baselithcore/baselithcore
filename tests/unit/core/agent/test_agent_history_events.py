"""``Agent`` continues a conversation (``history=``) and streams typed events.

A conversational host (Baselithbot's assistant) needs both: every turn resumes
the stored conversation, and the UI renders tool activity while the loop runs.
"""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from core.agent import (
    Agent,
    AgentResult,
    Completed,
    Failed,
    TextDelta,
    ToolCallFinished,
    ToolCallStarted,
)
from core.reasoning.react import ToolDefinition
from core.services.llm.messages import Message, ToolUseBlock, to_anthropic
from core.services.llm.tool_calling import LLMResult, ToolCall


def _service(results):
    """Message-capable LLMService stub; ``svc.sent`` holds each wire history."""
    svc = AsyncMock()
    svc.supports_messages = True
    svc.sent = []
    queued = list(results)

    async def _record(messages, **kwargs):
        svc.sent.append(json.loads(json.dumps(to_anthropic(messages))))
        if not queued:
            raise AssertionError("the agent asked for more turns than were queued")
        return queued.pop(0)

    svc.generate_messages = AsyncMock(side_effect=_record)
    return svc


def _call(name, arguments, *, call_id="t1"):
    return LLMResult(
        tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)],
        stop_reason="tool_use",
    )


async def _pop(city: str) -> str:
    """Look up a city's population."""
    return f"{city}:2870000"


POP = ToolDefinition(name="_pop", fn=_pop, description="pop", category="read_only")


def test_event_types_are_exported():
    result = AgentResult(output="x", text="x")
    events = [
        TextDelta(text="hi"),
        ToolCallStarted(call_id="c", name="n", arguments={}),
        ToolCallFinished(call_id="c", name="n", content="ok", is_error=False),
        Completed(result=result),
        Failed(error=RuntimeError("boom")),
    ]
    assert [type(e).__name__ for e in events] == [
        "TextDelta",
        "ToolCallStarted",
        "ToolCallFinished",
        "Completed",
        "Failed",
    ]


@pytest.mark.asyncio
class TestHistory:
    async def test_no_history_sends_only_the_prompt(self):
        for history in (None, []):
            svc = _service([LLMResult(text="hello")])
            await Agent(llm_service=svc).run("hi", history=history)
            assert svc.sent[0] == [
                {"role": "user", "content": [{"type": "text", "text": "hi"}]}
            ]

    async def test_history_is_prepended_verbatim(self):
        svc = _service([LLMResult(text="Rome, as you said.")])
        prior = [Message.user("I live in Rome"), Message.assistant("Noted.")]
        result = await Agent(llm_service=svc).run("where do I live?", history=prior)
        roles = [m["role"] for m in svc.sent[0]]
        texts = [m["content"][0]["text"] for m in svc.sent[0]]
        assert roles == ["user", "assistant", "user"]
        assert texts == ["I live in Rome", "Noted.", "where do I live?"]
        # The transcript returned covers the prior turns too, oldest first.
        assert [m.text for m in result.messages][:3] == texts

    async def test_caller_history_is_not_mutated(self):
        svc = _service([LLMResult(text="ok")])
        prior = [Message.user("a"), Message.assistant("b")]
        await Agent(llm_service=svc).run("c", history=prior)
        assert len(prior) == 2

    async def test_history_ending_in_unanswered_tool_use_is_refused(self):
        svc = _service([])
        dangling = Message(
            role="assistant", content=[ToolUseBlock(id="t9", name="_pop", input={})]
        )
        with pytest.raises(ValueError, match="tool_use"):
            await Agent(llm_service=svc, tools=[POP]).run(
                "next", history=[Message.user("q"), dangling]
            )
        svc.generate_messages.assert_not_awaited()

    async def test_run_without_history_unchanged_with_tools(self):
        svc = _service([_call("_pop", {"city": "Rome"}), LLMResult(text="done")])
        result = await Agent(tools=[POP], llm_service=svc).run("population?")
        assert result.output == "done"
        assert result.tool_calls_made == ["_pop"]
        assert result.iterations == 2


async def _collect(agen):
    return [event async for event in agen]


@pytest.mark.asyncio
class TestRunEvents:
    async def test_plain_answer_streams_text_then_completed(self):
        svc = _service([LLMResult(text="hello")])
        events = await _collect(Agent(llm_service=svc).run_events("hi"))
        assert [type(e) for e in events] == [TextDelta, Completed]
        assert events[0].text == "hello"
        assert events[-1].result.output == "hello"

    async def test_tool_turn_order(self):
        svc = _service([_call("_pop", {"city": "Rome"}), LLMResult(text="2.87M")])
        events = await _collect(
            Agent(tools=[POP], llm_service=svc).run_events("population?")
        )
        assert [type(e) for e in events] == [
            ToolCallStarted,
            ToolCallFinished,
            TextDelta,
            Completed,
        ]
        assert events[0].name == "_pop" and events[0].arguments == {"city": "Rome"}
        # ``content`` is the observation the model sees, which the runtime wraps
        # in an untrusted-output envelope.
        assert "Rome:2870000" in events[1].content
        assert events[1].is_error is False

    async def test_failing_tool_is_flagged(self):
        async def _boom(city: str) -> str:
            """Always fails."""
            raise RuntimeError("down")

        tool = ToolDefinition(
            name="_boom", fn=_boom, description="x", category="read_only"
        )
        svc = _service([_call("_boom", {"city": "Rome"}), LLMResult(text="sorry")])
        events = await _collect(Agent(tools=[tool], llm_service=svc).run_events("q"))
        finished = [e for e in events if isinstance(e, ToolCallFinished)]
        assert finished[0].is_error is True

    async def test_exception_becomes_single_failed_event(self):
        svc = _service([_call("_pop", {"city": "Rome"})] * 2)
        agent = Agent(tools=[POP], llm_service=svc, max_iterations=2)
        events = await _collect(agent.run_events("loop forever"))
        assert isinstance(events[-1], Failed)
        assert isinstance(events[-1].error, RuntimeError)
        assert sum(isinstance(e, Failed) for e in events) == 1

    async def test_history_is_honoured(self):
        svc = _service([LLMResult(text="Rome")])
        prior = [Message.user("I live in Rome"), Message.assistant("Noted.")]
        await _collect(Agent(llm_service=svc).run_events("where?", history=prior))
        assert len(svc.sent[0]) == 3

    async def test_consumer_leaving_early_stops_the_loop(self):
        svc = _service([_call("_pop", {"city": "Rome"}), LLMResult(text="done")])
        stream = Agent(tools=[POP], llm_service=svc).run_events("q")
        first = await anext(stream)
        assert isinstance(first, ToolCallStarted)
        await stream.aclose()
        assert svc.generate_messages.await_count == 1

    async def test_cancellation_propagates_instead_of_failed(self):
        svc = _service([])
        svc.generate_messages = AsyncMock(side_effect=asyncio.CancelledError())
        with pytest.raises(asyncio.CancelledError):
            await _collect(Agent(llm_service=svc).run_events("q"))
