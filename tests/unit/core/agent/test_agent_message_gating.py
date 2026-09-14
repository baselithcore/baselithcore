"""Gating, budget and compatibility for the typed ``Agent`` message loop.

Split from ``test_agent_messages`` (file-size cap): that module pins the shape
of the conversation, this one pins what the loop is allowed to *do* — argument
validation, the enforcement chokepoint, budget ticks, and the legacy path an
injected service that predates the message API still gets.
"""

import pytest

from core.agent import Agent
from core.reasoning.react import ToolDefinition
from core.services.llm.messages import Message, TextBlock
from core.services.llm.tool_calling import LLMResult
from tests.unit.core.agent.test_agent_messages import (
    _call,
    _histories,
    _pop,
    _service,
)


@pytest.mark.asyncio
class TestGating:
    async def test_arguments_are_validated_before_the_tool_runs(self):
        calls = []

        def add(a: int, b: int) -> int:
            """Add two integers."""
            calls.append((a, b))
            return a + b

        svc = _service(
            [_call("add", {"a": 1, "b": "not-an-int"}), LLMResult(text="done")]
        )
        agent = Agent(
            tools=[ToolDefinition(name="add", fn=add, description="d")],
            llm_service=svc,
        )
        await agent.run("q")

        assert calls == []  # never dispatched
        block = _histories(svc)[1][-1].content[0]
        assert block.is_error is True
        assert "invalid arguments" in block.content

    async def test_every_dispatch_passes_the_enforcement_chokepoint(self, monkeypatch):
        seen = []

        async def _spy(context, tool_name, category="read_only", **kwargs):
            seen.append((tool_name, category, kwargs.get("args")))

        monkeypatch.setattr(
            "core.orchestration.enforcement.enforce_tool_invocation", _spy
        )
        svc = _service([_call("_pop", {"city": "Rome"}), LLMResult(text="done")])
        agent = Agent(
            tools=[
                ToolDefinition(
                    name="_pop",
                    fn=_pop,
                    description="d",
                    category="external_side_effect",
                )
            ],
            llm_service=svc,
        )
        await agent.run("q")

        assert seen == [("_pop", "external_side_effect", {"city": "Rome"})]

    async def test_the_autonomy_policy_is_a_constructor_parameter(self, monkeypatch):
        """Public and discoverable, mirroring ``ReActAgent(autonomy_policy=…)``."""
        seen = {}

        async def _spy(context, tool_name, category="read_only", **kwargs):
            seen.update(context)

        monkeypatch.setattr(
            "core.orchestration.enforcement.enforce_tool_invocation", _spy
        )
        svc = _service([_call("_pop", {"city": "Rome"}), LLMResult(text="done")])
        policy = object()
        agent = Agent(tools=[_pop], llm_service=svc, autonomy_policy=policy)
        await agent.run("q")

        assert seen["autonomy_policy"] is policy

    async def test_an_injected_autonomy_policy_reaches_the_gate(self, monkeypatch):
        """Setting the attribute directly keeps working for existing hosts."""
        seen = {}

        async def _spy(context, tool_name, category="read_only", **kwargs):
            seen.update(context)

        monkeypatch.setattr(
            "core.orchestration.enforcement.enforce_tool_invocation", _spy
        )
        svc = _service([_call("_pop", {"city": "Rome"}), LLMResult(text="done")])
        agent = Agent(tools=[_pop], llm_service=svc)
        policy = object()
        agent._autonomy_policy = policy
        await agent.run("q")

        assert seen["autonomy_policy"] is policy

    async def test_no_policy_is_wired_by_default(self):
        """The docstring's ApprovalPendingError is unreachable without one."""
        from core.agent._tool_dispatch import gate_context

        assert "autonomy_policy" not in gate_context(Agent(llm_service=_service([])))

    async def test_a_refused_tool_becomes_an_error_result(self, monkeypatch):
        async def _deny(context, tool_name, category="read_only", **kwargs):
            raise PermissionError("tool not allowed by contract")

        monkeypatch.setattr(
            "core.orchestration.enforcement.enforce_tool_invocation", _deny
        )
        svc = _service([_call("_pop", {"city": "Rome"}), LLMResult(text="done")])
        agent = Agent(tools=[_pop], llm_service=svc)
        await agent.run("q")

        block = _histories(svc)[1][-1].content[0]
        assert block.is_error is True
        assert "not allowed" in block.content

    async def test_a_budget_abort_is_never_swallowed(self, monkeypatch):
        from core.orchestration.limits import BudgetExceededError, LoopBudgetSnapshot

        async def _broke(context, tool_name, category="read_only", **kwargs):
            raise BudgetExceededError(
                "tool call cap", LoopBudgetSnapshot(0, 0, 0, 0, 0.0, 0.0)
            )

        monkeypatch.setattr(
            "core.orchestration.enforcement.enforce_tool_invocation", _broke
        )
        svc = _service([_call("_pop", {"city": "Rome"}), LLMResult(text="done")])
        agent = Agent(tools=[_pop], llm_service=svc)
        with pytest.raises(BudgetExceededError):
            await agent.run("q")

    async def test_the_ambient_budget_counts_iterations_and_tool_calls(self):
        from core.orchestration.budget_context import activate_budget, deactivate_budget
        from core.orchestration.limits import LoopBudget, LoopLimits

        svc = _service([_call("_pop", {"city": "Rome"}), LLMResult(text="done")])
        agent = Agent(
            tools=[ToolDefinition(name="_pop", fn=_pop, description="d")],
            llm_service=svc,
        )
        budget = LoopBudget(limits=LoopLimits(max_iterations=10, max_tool_calls=10))
        token = activate_budget(budget)
        try:
            await agent.run("q")
        finally:
            deactivate_budget(token)

        assert budget.iterations == 2
        assert budget.tool_calls == 1


class TestTheRealServiceTakesTheMessagePath:
    def test_llm_service_advertises_the_message_api(self):
        """Without this the default agent silently falls back to prompts."""
        from core.services.llm.service import LLMService

        assert LLMService.supports_messages is True
        assert callable(LLMService.generate_messages)


@pytest.mark.asyncio
class TestLegacyServiceCompatibility:
    async def test_a_service_without_the_message_api_still_runs(self):
        """An injected service predating ``generate_messages`` keeps working."""

        class LegacyService:
            def __init__(self):
                self.prompts = []

            async def generate(self, prompt, **kwargs):
                self.prompts.append(prompt)
                if len(self.prompts) == 1:
                    return _call("_pop", {"city": "Rome"})
                return LLMResult(text="done")

        svc = LegacyService()
        agent = Agent(tools=[_pop], llm_service=svc)
        result = await agent.run("population of Rome?")

        assert result.output == "done"
        assert result.tool_calls_made == ["_pop"]
        # The history still reached it, flattened into the prompt.
        assert "population of Rome?" in svc.prompts[1]
        assert "2870000" in svc.prompts[1]
        # A transcript has no tool_result block saying the work came back, so
        # it carries the nudge the structured shape does not need.
        assert "answer without calling more tools" in svc.prompts[1]
        assert "answer without calling more tools" not in svc.prompts[0]


@pytest.mark.asyncio
class TestPublicResult:
    async def test_the_message_history_is_available_on_the_result(self):
        svc = _service([_call("_pop", {"city": "Rome"}), LLMResult(text="done")])
        agent = Agent(tools=[_pop], llm_service=svc)
        result = await agent.run("q")

        assert isinstance(result.messages[0], Message)
        assert isinstance(result.messages[0].content[0], TextBlock)
        assert result.messages[-1].role == "assistant"
        assert result.messages[-1].text == "done"
