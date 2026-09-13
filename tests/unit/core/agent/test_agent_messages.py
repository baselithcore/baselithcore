"""The typed ``Agent`` runs a real message loop, not a rebuilt prompt.

Every assertion here was a defect before: a tool result arrived as a line of
text in a regenerated user prompt, so the model could not tell which call it
answered, could not tell a failure from a success, lost the assistant turn that
requested it, and invalidated the prompt cache on every iteration. The loop now
appends to a message history and sends ``tool_result`` blocks back.
"""

import json
from unittest.mock import AsyncMock

import pytest

from core.agent import Agent
from core.orchestration.tool_output import UNTRUSTED_OUTPUT_SYSTEM_RULE
from core.plugins.result import ok as skill_ok
from core.reasoning.react import ToolDefinition
from core.services.llm.messages import (
    Message,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    to_anthropic,
)
from core.services.llm.tool_calling import LLMResult, ToolCall


def _service(results):
    """A message-capable LLMService stub returning canned results in order.

    Records ``svc.sent``: each history rendered to the wire **as the call
    arrives**. Inspecting ``await_args_list`` afterwards cannot show what was
    sent — the loop hands over a shallow copy of its list, so every recorded
    call still points at the same live ``Message`` objects and reading them
    later shows their final state, not the state at the time of the call.
    """
    svc = AsyncMock()
    svc.supports_messages = True
    svc.sent = []
    queued = list(results)

    async def _record(messages, **kwargs):
        svc.sent.append(json.dumps(to_anthropic(messages)))
        if not queued:
            raise AssertionError("the agent asked for more turns than were queued")
        return queued.pop(0)

    svc.generate_messages = AsyncMock(side_effect=_record)
    return svc


def _histories(svc):
    """Every history the loop sent, as lists of messages.

    Use ``svc.sent`` for anything about what the *wire* saw; this view shares
    the loop's own ``Message`` objects and is only safe for assertions about
    their final state (block types, ids, identity).
    """
    return [call.args[0] for call in svc.generate_messages.await_args_list]


def _call(name, arguments, *, call_id="t1"):
    return LLMResult(
        tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)],
        stop_reason="tool_use",
    )


async def _pop(city: str) -> str:
    """Look up a city's population."""
    return f"{city}:2870000"


@pytest.mark.asyncio
class TestToolResultBlocks:
    async def test_results_go_back_as_correlated_tool_result_blocks(self):
        svc = _service([_call("_pop", {"city": "Rome"}), LLMResult(text="done")])
        agent = Agent(tools=[_pop], llm_service=svc)
        await agent.run("population of Rome?")

        history = _histories(svc)[1]
        assert [m.role for m in history] == ["user", "assistant", "user"]
        block = history[-1].content[0]
        assert isinstance(block, ToolResultBlock)
        assert block.tool_use_id == "t1"
        assert "2870000" in block.content
        assert block.is_error is False

    async def test_parallel_calls_answer_in_one_user_message(self):
        """Anthropic rejects a turn whose tool_use blocks are answered apart."""
        svc = _service(
            [
                LLMResult(
                    tool_calls=[
                        ToolCall(id="a", name="_pop", arguments={"city": "Rome"}),
                        ToolCall(id="b", name="_pop", arguments={"city": "Oslo"}),
                    ],
                    stop_reason="tool_use",
                ),
                LLMResult(text="done"),
            ]
        )
        agent = Agent(tools=[_pop], llm_service=svc)
        await agent.run("two cities")

        results = _histories(svc)[1][-1]
        assert len(results.content) == 2
        assert [b.tool_use_id for b in results.content] == ["a", "b"]

    async def test_a_failing_tool_sets_is_error(self):
        def explode(x: int = 1) -> str:
            """Always fails."""
            raise RuntimeError("kaboom")

        svc = _service([_call("explode", {"x": 1}), LLMResult(text="done")])
        agent = Agent(
            tools=[ToolDefinition(name="explode", fn=explode, description="d")],
            llm_service=svc,
        )
        await agent.run("q")

        block = _histories(svc)[1][-1].content[0]
        assert block.is_error is True
        assert "kaboom" in block.content

    async def test_an_unknown_tool_is_an_error_result_not_a_silent_note(self):
        svc = _service([_call("nope", {}), LLMResult(text="done")])
        agent = Agent(llm_service=_service([]), tools=[_pop])
        agent._llm_service = svc
        await agent.run("q")

        block = _histories(svc)[1][-1].content[0]
        assert block.is_error is True
        assert "unknown tool" in block.content.lower()


@pytest.mark.asyncio
class TestHistoryIsAppendOnly:
    async def test_the_prefix_of_an_earlier_turn_is_re_sent_unchanged(self):
        svc = _service(
            [
                _call("_pop", {"city": "Rome"}, call_id="a"),
                _call("_pop", {"city": "Oslo"}, call_id="b"),
                LLMResult(text="done"),
            ]
        )
        agent = Agent(tools=[_pop], llm_service=svc, max_iterations=5)
        await agent.run("q")

        # ``svc.sent`` is serialized at call time, so an in-place edit of an
        # earlier turn's content block between iterations shows up as a prefix
        # mismatch. Comparing the recorded histories after the run cannot see
        # it: they share the loop's live Message objects, so every side of the
        # comparison would show the same mutated text and agree.
        first, second, third = svc.sent
        assert second.startswith(first[:-1])
        assert third.startswith(second[:-1])
        assert [len(h) for h in _histories(svc)] == [1, 3, 5]

    async def test_the_system_prompt_is_identical_on_every_turn(self):
        """The cacheable prefix: recomputing it per turn would evict the cache."""
        svc = _service([_call("_pop", {"city": "Rome"}), LLMResult(text="done")])
        agent = Agent(tools=[_pop], system_prompt="be terse", llm_service=svc)
        await agent.run("q")

        systems = {
            call.kwargs["system"] for call in svc.generate_messages.await_args_list
        }
        assert len(systems) == 1

    async def test_the_assistant_turn_is_appended_verbatim(self):
        turn = Message(
            role="assistant",
            content=[
                ThinkingBlock(payload={"type": "thinking", "signature": "sig-1"}),
                ToolUseBlock(id="t1", name="_pop", input={"city": "Rome"}),
            ],
        )
        svc = _service(
            [
                LLMResult(
                    tool_calls=[
                        ToolCall(id="t1", name="_pop", arguments={"city": "Rome"})
                    ],
                    stop_reason="tool_use",
                    message=turn,
                ),
                LLMResult(text="done"),
            ]
        )
        agent = Agent(tools=[_pop], llm_service=svc)
        await agent.run("q")

        assert _histories(svc)[1][1] is turn

    async def test_validation_feedback_is_a_new_turn_not_a_rewritten_prompt(self):
        from pydantic import BaseModel

        class CityInfo(BaseModel):
            city: str
            population: int

        svc = _service(
            [
                LLMResult(text='{"city": "Rome"}'),
                LLMResult(text='{"city": "Rome", "population": 1}'),
            ]
        )
        agent = Agent(output_type=CityInfo, llm_service=svc, max_retries=2)
        result = await agent.run("info")

        assert result.output.population == 1
        first, second = _histories(svc)
        assert second[: len(first)] == first
        assert "population" in second[-1].text


@pytest.mark.asyncio
class TestToolSpecsAndSystemPrompt:
    async def test_the_autonomy_category_is_emitted_as_an_annotation(self):
        svc = _service([LLMResult(text="ok")])
        agent = Agent(
            tools=[
                ToolDefinition(
                    name="read", fn=_pop, description="d", category="read_only"
                ),
                ToolDefinition(
                    name="wipe", fn=_pop, description="d", category="destructive"
                ),
            ],
            llm_service=svc,
        )
        await agent.run("q")

        specs = {s.name: s for s in svc.generate_messages.await_args.kwargs["tools"]}
        assert specs["read"].annotations == {
            "readOnlyHint": True,
            "destructiveHint": False,
        }
        assert specs["wipe"].annotations == {
            "readOnlyHint": False,
            "destructiveHint": True,
        }

    async def test_an_unknown_category_annotates_as_destructive(self):
        svc = _service([LLMResult(text="ok")])
        agent = Agent(
            tools=[
                ToolDefinition(name="odd", fn=_pop, description="d", category="typo")
            ],
            llm_service=svc,
        )
        await agent.run("q")
        spec = svc.generate_messages.await_args.kwargs["tools"][0]
        assert spec.annotations["destructiveHint"] is True

    async def test_the_untrusted_envelope_rule_is_stated_once_when_tools_exist(self):
        svc = _service([LLMResult(text="ok")])
        agent = Agent(tools=[_pop], system_prompt="be terse", llm_service=svc)
        await agent.run("q")

        system = svc.generate_messages.await_args.kwargs["system"]
        assert system.startswith("be terse")
        assert system.count(UNTRUSTED_OUTPUT_SYSTEM_RULE) == 1

    async def test_no_tools_means_no_envelope_rule(self):
        svc = _service([LLMResult(text="ok")])
        agent = Agent(system_prompt="be terse", llm_service=svc)
        await agent.run("q")
        assert svc.generate_messages.await_args.kwargs["system"] == "be terse"


@pytest.mark.asyncio
class TestObservationRendering:
    async def test_tool_output_leaves_inside_the_untrusted_envelope(self):
        svc = _service([_call("_pop", {"city": "Rome"}), LLMResult(text="done")])
        agent = Agent(tools=[_pop], llm_service=svc)
        await agent.run("q")

        content = _histories(svc)[1][-1].content[0].content
        assert content.startswith("<untrusted_tool_output tool=")
        assert content.rstrip().endswith("</untrusted_tool_output>")

    async def test_a_skill_result_travels_as_its_snapshot(self):
        def lookup(q: str = "x"):
            """Returns a SkillResult."""
            return skill_ok(data={"rows": [1, 2]}, snapshot="2 rows found")

        svc = _service([_call("lookup", {"q": "x"}), LLMResult(text="done")])
        agent = Agent(
            tools=[ToolDefinition(name="lookup", fn=lookup, description="d")],
            llm_service=svc,
        )
        await agent.run("q")

        block = _histories(svc)[1][-1].content[0]
        assert "2 rows found" in block.content
        assert "SkillResult" not in block.content
        assert block.is_error is False

    async def test_a_failed_skill_result_is_flagged_as_an_error(self):
        from core.plugins.result import fail as skill_fail

        def lookup(q: str = "x"):
            """Returns a failed SkillResult."""
            return skill_fail("index offline", error_code="E_OFFLINE")

        svc = _service([_call("lookup", {"q": "x"}), LLMResult(text="done")])
        agent = Agent(
            tools=[ToolDefinition(name="lookup", fn=lookup, description="d")],
            llm_service=svc,
        )
        await agent.run("q")

        block = _histories(svc)[1][-1].content[0]
        assert block.is_error is True
        assert "index offline" in block.content

    async def test_a_skill_with_nothing_to_show_never_sends_empty_content(self):
        """An empty tool_result content block is a 400 on some providers."""

        def quiet(q: str = "x"):
            """Succeeds with nothing to report."""
            return skill_ok(data=None, snapshot="")

        svc = _service([_call("quiet", {"q": "x"}), LLMResult(text="done")])
        agent = Agent(
            tools=[ToolDefinition(name="quiet", fn=quiet, description="d")],
            llm_service=svc,
        )
        await agent.run("q")

        block = _histories(svc)[1][-1].content[0]
        assert block.content
        assert block.is_error is False

    async def test_an_empty_ledger_row_never_replays_as_empty_content(self):
        """The last route to an empty tool_result: a row written before the guard."""
        from core.orchestration.idempotency import (
            InMemoryToolLedger,
            derive_idempotency_key,
        )

        ran = []

        def charge(amount: int = 1) -> str:
            """Must not run — this call was already recorded."""
            ran.append(amount)
            return "charged"

        ledger = InMemoryToolLedger()
        key = derive_idempotency_key("run-1", 0, "charge", {"amount": 1})
        await ledger.begin(key, run_id="run-1", tool="charge")
        await ledger.complete(key, "")

        svc = _service([_call("charge", {"amount": 1}), LLMResult(text="done")])
        agent = Agent(
            tools=[ToolDefinition(name="charge", fn=charge, description="d")],
            llm_service=svc,
            tool_ledger=ledger,
        )
        await agent.run("q", run_id="run-1")

        assert ran == []  # replayed, not re-executed
        block = _histories(svc)[1][-1].content[0]
        assert block.content == "(tool returned no output)"

    async def test_oversized_output_is_truncated(self):
        def huge(q: str = "x") -> str:
            """Returns far too much."""
            return "y" * 500_000

        svc = _service([_call("huge", {"q": "x"}), LLMResult(text="done")])
        agent = Agent(
            tools=[ToolDefinition(name="huge", fn=huge, description="d")],
            llm_service=svc,
        )
        await agent.run("q")

        block = _histories(svc)[1][-1].content[0]
        assert len(block.content) < 500_000
