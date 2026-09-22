"""The orchestrated ReAct loop talks to the model in messages, not a transcript.

This is the loop the orchestrator actually runs, and until now it rebuilt a
flat string prompt on every turn — appending lines like
``Tool result [tu_1]: ...`` to a list and joining them. The typed ``Agent``
next door documented, at length, what that costs, and then this loop paid all
of it:

* **correlation** — a result reached the model as prose naming an id, not as a
  ``tool_result`` block carrying ``tool_use_id``, so with three parallel calls
  the pairing was the model's problem to infer;
* **the error flag** — a failed tool read as a successful one that happened to
  return the word "Error";
* **the assistant turn** — dropped and re-narrated, which for a provider that
  requires thinking blocks replayed unchanged is not a lossy choice but an
  invalid one;
* **the prompt cache** — a rebuilt prefix is a new prefix, so nothing before
  the newest turn could ever be served from cache.

The legacy transport is still exercised here, because a service that predates
the message API (or an injected double) must keep working.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from core.reasoning.react import ReActAgent, ToolDefinition
from core.services.llm.messages import (
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from core.services.llm.tool_calling import LLMResult, ToolCall


class MessageCapableLLM:
    """A service that advertises and serves the message API."""

    supports_messages = True

    def __init__(self, results: list[Any]) -> None:
        self._results = list(results)
        self.sent: list[list[Message]] = []
        self.config = SimpleNamespace(enable_native_tools=True)
        self.provider = SimpleNamespace(supports_native_tools=True)

    async def generate_messages(self, messages, **kwargs: Any) -> LLMResult:
        self.sent.append(list(messages))
        if not self._results:
            raise AssertionError("MessageCapableLLM exhausted")
        return self._results.pop(0)

    async def generate(self, prompt, **kwargs: Any) -> LLMResult:  # pragma: no cover
        raise AssertionError("the message path must be preferred")


class LegacyTranscriptLLM:
    """A service built before the message API: prompt in, result out."""

    def __init__(self, results: list[Any]) -> None:
        self._results = list(results)
        self.prompts: list[str] = []
        self.config = SimpleNamespace(enable_native_tools=True)
        self.provider = SimpleNamespace(supports_native_tools=True)

    async def generate(self, prompt, model=None, **kwargs: Any) -> LLMResult:
        self.prompts.append(prompt)
        if not self._results:
            raise AssertionError("LegacyTranscriptLLM exhausted")
        return self._results.pop(0)


def _tool_turn(*calls: ToolCall, text: str | None = None) -> LLMResult:
    return LLMResult(text=text, tool_calls=list(calls), native=True)


def _final(text: str) -> LLMResult:
    return LLMResult(text=text, tool_calls=[], native=True)


async def _ok(**_kwargs: Any) -> str:
    return "population 37M"


async def _boom(**_kwargs: Any) -> str:
    raise RuntimeError("upstream refused")


def _agent(llm: Any, *tools: ToolDefinition) -> ReActAgent:
    return ReActAgent(tools=list(tools), llm_service=llm, native_tools=True)


def _blocks(history: list[Message], kind: type) -> list[Any]:
    return [b for m in history for b in m.content if isinstance(b, kind)]


class TestMessageTransport:
    async def test_the_model_receives_messages_not_a_prompt(self) -> None:
        llm = MessageCapableLLM([_final("done")])
        await _agent(llm).run("hello")

        assert llm.sent, "nothing was sent"
        assert all(isinstance(m, Message) for m in llm.sent[0])
        assert llm.sent[0][0].content[0].text == "hello"

    async def test_tool_results_carry_their_call_id_and_error_flag(self) -> None:
        llm = MessageCapableLLM(
            [
                _tool_turn(
                    ToolCall(id="tu_ok", name="lookup", arguments={"q": "tokyo"}),
                    ToolCall(id="tu_bad", name="broken", arguments={}),
                ),
                _final("answered"),
            ]
        )
        agent = _agent(
            llm,
            ToolDefinition("lookup", _ok, "Look up", category="read_only"),
            ToolDefinition("broken", _boom, "Fails", category="read_only"),
        )

        await agent.run("population of Tokyo?")

        results = _blocks(llm.sent[-1], ToolResultBlock)
        assert [r.tool_use_id for r in results] == ["tu_ok", "tu_bad"]
        assert results[0].is_error is False
        assert results[1].is_error is True
        assert "37M" in results[0].content

    async def test_one_message_answers_a_whole_parallel_turn(self) -> None:
        """A provider rejects parallel calls answered in separate messages."""
        llm = MessageCapableLLM(
            [
                _tool_turn(
                    ToolCall(id="tu_1", name="lookup", arguments={}),
                    ToolCall(id="tu_2", name="lookup", arguments={}),
                ),
                _final("answered"),
            ]
        )
        agent = _agent(llm, ToolDefinition("lookup", _ok, "Look up", "read_only"))

        await agent.run("two things")

        answering = [
            m
            for m in llm.sent[-1]
            if any(isinstance(b, ToolResultBlock) for b in m.content)
        ]
        assert len(answering) == 1
        assert len(answering[0].content) == 2

    async def test_the_assistant_turn_goes_back_verbatim(self) -> None:
        llm = MessageCapableLLM(
            [
                _tool_turn(
                    ToolCall(id="tu_1", name="lookup", arguments={"q": "x"}),
                    text="I will look that up.",
                ),
                _final("answered"),
            ]
        )
        agent = _agent(llm, ToolDefinition("lookup", _ok, "Look up", "read_only"))

        await agent.run("go")

        uses = _blocks(llm.sent[-1], ToolUseBlock)
        assert [u.id for u in uses] == ["tu_1"]
        assert any(
            isinstance(b, TextBlock) and b.text == "I will look that up."
            for m in llm.sent[-1]
            for b in m.content
        )

    async def test_the_prefix_is_stable_so_the_cache_can_hit(self) -> None:
        """Every turn must extend the conversation, never rewrite its head."""
        llm = MessageCapableLLM(
            [
                _tool_turn(ToolCall(id="tu_1", name="lookup", arguments={})),
                _tool_turn(ToolCall(id="tu_2", name="lookup", arguments={})),
                _final("answered"),
            ]
        )
        agent = _agent(llm, ToolDefinition("lookup", _ok, "Look up", "read_only"))

        await agent.run("go")

        assert len(llm.sent) == 3
        for earlier, later in zip(llm.sent, llm.sent[1:], strict=False):
            assert later[: len(earlier)] == earlier, "the prefix was rewritten"
            assert len(later) > len(earlier), "the turn did not extend the history"

    async def test_each_send_is_a_snapshot_not_the_live_list(self) -> None:
        llm = MessageCapableLLM(
            [
                _tool_turn(ToolCall(id="tu_1", name="lookup", arguments={})),
                _final("answered"),
            ]
        )
        agent = _agent(llm, ToolDefinition("lookup", _ok, "Look up", "read_only"))

        await agent.run("go")

        assert len(llm.sent[0]) == 1, "a later append rewrote an earlier request"

    async def test_the_wire_format_is_a_real_tool_use_conversation(self) -> None:
        """What the provider receives, not just what the loop holds.

        The old transport produced one user message per turn whose text
        narrated the calls; a provider cannot pair those, and a cache cannot
        reuse a prefix that is rewritten each time.
        """
        from core.services.llm.messages import to_anthropic

        llm = MessageCapableLLM(
            [
                _tool_turn(ToolCall(id="tu_1", name="lookup", arguments={})),
                _tool_turn(ToolCall(id="tu_2", name="lookup", arguments={})),
                _final("answered"),
            ]
        )
        agent = _agent(llm, ToolDefinition("lookup", _ok, "Look up", "read_only"))

        await agent.run("go")

        wire = to_anthropic(llm.sent[-1])
        assert [m["role"] for m in wire] == [
            "user",
            "assistant",
            "user",
            "assistant",
            "user",
        ]
        assert [b["type"] for b in wire[1]["content"]] == ["tool_use"]
        assert [b["type"] for b in wire[2]["content"]] == ["tool_result"]
        first = to_anthropic(llm.sent[0])
        assert wire[: len(first)] == first, "the cacheable prefix was rewritten"


class TestLegacyServiceStillWorks:
    async def test_a_prompt_only_service_gets_a_transcript(self) -> None:
        llm = LegacyTranscriptLLM(
            [
                _tool_turn(ToolCall(id="tu_1", name="lookup", arguments={})),
                _final("answered"),
            ]
        )
        agent = _agent(llm, ToolDefinition("lookup", _ok, "Look up", "read_only"))

        result = await agent.run("go")

        assert result.final_answer == "answered"
        assert llm.prompts, "the legacy path was never used"
        assert isinstance(llm.prompts[0], str)

    async def test_the_flattened_transcript_carries_the_convergence_nudge(self) -> None:
        """Without a tool_result block, it needs telling the work came back."""
        from core.services.llm.messages import CONVERGENCE_NUDGE

        llm = LegacyTranscriptLLM(
            [
                _tool_turn(ToolCall(id="tu_1", name="lookup", arguments={})),
                _final("answered"),
            ]
        )
        agent = _agent(llm, ToolDefinition("lookup", _ok, "Look up", "read_only"))

        await agent.run("go")

        assert CONVERGENCE_NUDGE in llm.prompts[-1]


class TestCompactionKeepsTheConversationValid:
    @pytest.fixture(autouse=True)
    def tiny_budget(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BASELITH_REACT_HISTORY_MAX_TOKENS", "20")

    async def test_every_tool_use_still_has_its_answer(self) -> None:
        llm = MessageCapableLLM(
            [
                _tool_turn(
                    ToolCall(id="tu_1", name="lookup", arguments={}), text="x" * 4000
                ),
                _tool_turn(
                    ToolCall(id="tu_2", name="lookup", arguments={}), text="y" * 4000
                ),
                _final("answered"),
            ]
        )
        agent = _agent(llm, ToolDefinition("lookup", _ok, "Look up", "read_only"))

        await agent.run("go")

        final = llm.sent[-1]
        used = {b.id for b in _blocks(final, ToolUseBlock)}
        answered = {b.tool_use_id for b in _blocks(final, ToolResultBlock)}
        assert used == answered == {"tu_1", "tu_2"}

    async def test_the_task_itself_is_never_compacted(self) -> None:
        llm = MessageCapableLLM(
            [
                _tool_turn(
                    ToolCall(id="tu_1", name="lookup", arguments={}), text="x" * 4000
                ),
                _final("answered"),
            ]
        )
        agent = _agent(llm, ToolDefinition("lookup", _ok, "Look up", "read_only"))

        await agent.run("the original question, at length " * 20)

        assert "[compacted]" not in llm.sent[-1][0].content[0].text
