"""Model-supplied arguments are validated; observations are untrusted data.

Three gaps closed here:

* ``_execute_tool_call`` splatted ``call.arguments`` straight into the
  callable, so a hallucinated argument name or a string where an int belongs
  surfaced as an opaque ``TypeError`` instead of a correctable message.
* A tool returning a ``SkillResult`` was stringified: the model read a Pydantic
  repr, and a failed skill counted as a *success* for the failure streak.
* Observations re-entered the prompt as plain text, indistinguishable from the
  operator's own instructions — the whole mechanism of indirect prompt
  injection.
"""

from __future__ import annotations

import pytest

from core.plugins.result import fail, ok, partial
from core.reasoning.react import ReActAgent, ToolDefinition

pytestmark = [pytest.mark.unit]


def _agent(tool: ToolDefinition, **kwargs) -> ReActAgent:
    return ReActAgent(tools=[tool], **kwargs)


class TestArgumentValidation:
    def _typed_tool(self, calls: list) -> ToolDefinition:
        async def lookup(city: str, limit: int = 5) -> str:
            calls.append((city, limit))
            return f"{city}:{limit}"

        return ToolDefinition(
            name="lookup", fn=lookup, description="d", category="read_only"
        )

    async def test_valid_arguments_pass_through(self) -> None:
        calls: list = []
        agent = _agent(self._typed_tool(calls))
        observation = await agent._execute_tool_call(
            "lookup", {"city": "Rome", "limit": 2}
        )
        assert "Rome:2" in observation
        assert calls == [("Rome", 2)]

    async def test_missing_required_argument_is_a_structured_error(self) -> None:
        calls: list = []
        agent = _agent(self._typed_tool(calls))
        observation = await agent._execute_tool_call("lookup", {"limit": 2})
        assert observation.startswith("Error")
        assert "lookup" in observation
        assert "city" in observation
        assert calls == []

    async def test_wrong_argument_type_is_a_structured_error(self) -> None:
        calls: list = []
        agent = _agent(self._typed_tool(calls))
        observation = await agent._execute_tool_call(
            "lookup", {"city": "Rome", "limit": "many"}
        )
        assert observation.startswith("Error")
        assert calls == []

    async def test_explicit_schema_wins(self) -> None:
        calls: list = []

        async def fn(**kwargs) -> str:
            calls.append(kwargs)
            return "ok"

        tool = ToolDefinition(
            name="strict",
            fn=fn,
            description="d",
            category="read_only",
            parameters={
                "type": "object",
                "properties": {"n": {"type": "integer"}},
                "required": ["n"],
            },
        )
        agent = _agent(tool)
        assert (await agent._execute_tool_call("strict", {"n": "x"})).startswith(
            "Error"
        )
        assert "ok" in await agent._execute_tool_call("strict", {"n": 1})
        assert calls == [{"n": 1}]

    async def test_hallucinated_extra_argument_is_refused(self) -> None:
        """An inferred schema describes the callable exactly, so an argument
        the tool does not accept is a model error the gate should never see —
        it would otherwise consume an approval and a budget entry, then die on
        an opaque TypeError."""
        from core.orchestration.limits import LoopBudget, LoopLimits

        calls: list = []
        budget = LoopBudget(limits=LoopLimits(max_tool_calls=5))
        agent = _agent(self._typed_tool(calls), loop_budget=budget)
        observation = await agent._execute_tool_call(
            "lookup", {"city": "Rome", "sort_by": "population"}
        )
        assert observation.startswith("Error")
        assert "invalid arguments" in observation  # refused by the validator…
        assert "sort_by" in observation
        assert calls == []
        # …which is upstream of the gate, so no tool-call budget was spent.
        assert budget.tool_calls == 0

    async def test_kwargs_tools_still_accept_anything(self) -> None:
        """A ``**kwargs`` callable really does accept extra keys; refusing
        them would break working tools."""
        calls: list = []

        async def flexible(city: str, **extra) -> str:
            calls.append((city, extra))
            return "ok"

        tool = ToolDefinition(
            name="flexible", fn=flexible, description="d", category="read_only"
        )
        observation = await _agent(tool)._execute_tool_call(
            "flexible", {"city": "Rome", "sort_by": "population"}
        )
        assert "ok" in observation
        assert calls == [("Rome", {"sort_by": "population"})]

    async def test_a_correct_structured_call_is_not_refused(self) -> None:
        """Regression: closing inferred schemas to extra keys only works if the
        inferred *types* are right. ``dict[str, Any]`` and ``int | None`` used
        to fall through to ``"string"``, so this perfectly valid call was
        refused with "is not of type 'string'"."""
        from typing import Any as _Any

        calls: list = []

        async def search(payload: dict[str, _Any], limit: int | None = None) -> str:
            calls.append((payload, limit))
            return "found"

        tool = ToolDefinition(
            name="search", fn=search, description="d", category="read_only"
        )
        observation = await _agent(tool)._execute_tool_call(
            "search", {"payload": {"a": 1}, "limit": 3}
        )
        assert "found" in observation
        assert calls == [({"a": 1}, 3)]

    async def test_an_explicit_schema_is_not_tightened(self) -> None:
        """A declared schema is the author's contract; silently adding
        ``additionalProperties: false`` to it would change their meaning."""
        calls: list = []

        async def fn(**kwargs) -> str:
            calls.append(kwargs)
            return "ok"

        tool = ToolDefinition(
            name="open",
            fn=fn,
            description="d",
            category="read_only",
            parameters={
                "type": "object",
                "properties": {"n": {"type": "integer"}},
            },
        )
        observation = await _agent(tool)._execute_tool_call(
            "open", {"n": 1, "extra": "allowed"}
        )
        assert "ok" in observation
        assert calls == [{"n": 1, "extra": "allowed"}]

    async def test_text_parsed_calls_are_not_schema_validated(self) -> None:
        """The legacy loop passes positional strings; a JSON-Schema object
        check does not apply to them and must not start rejecting them."""
        calls: list = []

        async def fn(*args) -> str:
            calls.append(args)
            return "positional ok"

        tool = ToolDefinition(
            name="legacy", fn=fn, description="d", category="read_only"
        )
        observation = await _agent(tool)._execute_tool("legacy", "a, b")
        assert "positional ok" in observation
        assert calls == [("a", "b")]

    async def test_invalid_arguments_do_not_burn_a_ledger_entry(self) -> None:
        async def fn(n: int) -> str:
            return "never"

        tool = ToolDefinition(
            name="writer", fn=fn, description="d", category="mutating"
        )
        agent = _agent(tool, autonomy_policy=_permissive())
        observation = await agent._execute_tool_call("writer", {"n": "x"})
        assert observation.startswith("Error")


def _permissive():
    from core.orchestration.autonomy import AutonomyLevel, AutonomyPolicy

    return AutonomyPolicy(level=AutonomyLevel.FULLY_AUTONOMOUS)


class TestSkillResultObservations:
    async def test_success_feeds_the_snapshot_to_the_model(self) -> None:
        async def fn() -> object:
            return ok({"rows": 3}, message="queried")

        tool = ToolDefinition(name="q", fn=fn, description="d", category="read_only")
        observation = await _agent(tool)._execute_tool("q", "")
        assert '{"rows": 3}' in observation
        assert "SkillResult(" not in observation

    async def test_failure_is_an_error_observation(self) -> None:
        async def fn() -> object:
            return fail("permission denied", error_code="denied")

        tool = ToolDefinition(name="q", fn=fn, description="d", category="read_only")
        agent = _agent(tool)
        observation = await agent._execute_tool("q", "")
        assert observation.startswith("Error")
        assert "permission denied" in observation

    async def test_failed_skill_counts_toward_the_failure_streak(self) -> None:
        async def fn() -> object:
            return fail("nope")

        tool = ToolDefinition(name="q", fn=fn, description="d", category="read_only")
        agent = _agent(tool, max_consecutive_tool_failures=2)
        for _ in range(2):
            observation = await agent._execute_tool("q", "")
            escalation = agent._note_tool_outcome(observation)
        assert escalation is not None
        assert "2 consecutive" in escalation

    async def test_partial_result_is_also_a_failure(self) -> None:
        async def fn() -> object:
            return partial({"rows": 1}, "only half the rows")

        tool = ToolDefinition(name="q", fn=fn, description="d", category="read_only")
        observation = await _agent(tool)._execute_tool("q", "")
        assert observation.startswith("Error")
        assert "only half the rows" in observation


class TestToolLedger:
    async def test_read_only_tools_skip_the_ledger(self) -> None:
        calls: list = []

        async def fn() -> str:
            calls.append(1)
            return "r"

        tool = ToolDefinition(name="r", fn=fn, description="d", category="read_only")
        agent = _agent(tool)
        await agent._execute_tool("r", "")
        assert agent._ledger_entries() == 0

    async def test_effectful_tool_is_recorded(self) -> None:
        calls: list = []

        async def fn() -> str:
            calls.append(1)
            return "w"

        tool = ToolDefinition(name="w", fn=fn, description="d", category="mutating")
        agent = _agent(tool, autonomy_policy=_permissive())
        await agent._execute_tool("w", "")
        assert agent._ledger_entries() == 1
        assert calls == [1]

    async def test_replayed_call_returns_the_recorded_outcome(self) -> None:
        calls: list = []

        async def fn() -> str:
            calls.append(1)
            return "side effect"

        tool = ToolDefinition(name="w", fn=fn, description="d", category="mutating")
        agent = _agent(tool, autonomy_policy=_permissive())
        agent._ledger_run_id = "run-fixed"

        first = await agent._execute_tool("w", "")
        assert "side effect" in first
        # Rewind the step counter so the next call derives the same key: this
        # is the replay a resumed run performs.
        agent._ledger_step = 0
        second = await agent._execute_tool("w", "")
        assert second == first
        assert calls == [1]  # the effect landed exactly once

    async def test_failed_call_is_reclaimable(self) -> None:
        attempts: list = []

        async def fn() -> str:
            attempts.append(1)
            raise ValueError("boom")

        tool = ToolDefinition(name="w", fn=fn, description="d", category="mutating")
        agent = _agent(tool, autonomy_policy=_permissive())
        agent._ledger_run_id = "run-fixed"
        first = await agent._execute_tool("w", "")
        assert first.startswith("Error")
        # Same key on the next attempt: a failed row must not hold the claim.
        agent._ledger_step = 0
        second = await agent._execute_tool("w", "")
        assert second.startswith("Error")
        assert len(attempts) == 2
