"""Safe defaults of a standalone ``Agent``: destructive guard, default budget,
strict tool schemas."""

from __future__ import annotations

from typing import Any

import pytest

from core.agent import Agent
from core.agent._strict_tools import strict_tool_parameters
from core.orchestration.autonomy import AutonomyPolicy
from core.orchestration.budget_context import (
    activate_budget,
    deactivate_budget,
    get_active_budget,
)
from core.orchestration.limits import BudgetExceededError, LoopBudget, LoopLimits
from core.reasoning.react import ToolDefinition
from core.services.llm.tool_calling import LLMResult
from tests.unit.core.agent.test_agent_messages import _call, _histories, _service


def _recorder() -> tuple[list[str], Any]:
    calls: list[str] = []

    def wipe(target: str) -> str:
        """Delete a target."""
        calls.append(target)
        return "wiped"

    return calls, wipe


def _result_block(svc: Any) -> Any:
    return _histories(svc)[1][-1].content[0]


@pytest.mark.asyncio
class TestDestructiveGuard:
    async def test_explicitly_destructive_tool_is_refused_by_default(self) -> None:
        calls, wipe = _recorder()
        svc = _service([_call("wipe", {"target": "db"}), LLMResult(text="ok")])
        agent = Agent(
            tools=[
                ToolDefinition(
                    name="wipe", fn=wipe, description="d", category="destructive"
                )
            ],
            llm_service=svc,
        )

        await agent.run("q")

        assert calls == []
        block = _result_block(svc)
        assert block.is_error is True
        assert "declared destructive" in block.content
        assert "autonomy_policy=None" in block.content

    async def test_plain_callable_still_runs(self) -> None:
        calls, wipe = _recorder()
        svc = _service([_call("wipe", {"target": "db"}), LLMResult(text="ok")])

        await Agent(tools=[wipe], llm_service=svc).run("q")

        assert calls == ["db"]

    async def test_default_category_tool_definition_still_runs(self) -> None:
        calls, wipe = _recorder()
        svc = _service([_call("wipe", {"target": "db"}), LLMResult(text="ok")])
        tool = ToolDefinition(name="wipe", fn=wipe, description="d")
        assert tool.category == "destructive"
        assert tool.category_declared is False

        await Agent(tools=[tool], llm_service=svc).run("q")

        assert calls == ["db"]

    @pytest.mark.parametrize("category", ["read_only", "mutating"])
    async def test_other_declared_categories_run(self, category: str) -> None:
        calls, wipe = _recorder()
        svc = _service([_call("wipe", {"target": "db"}), LLMResult(text="ok")])
        tool = ToolDefinition(name="wipe", fn=wipe, description="d", category=category)

        await Agent(tools=[tool], llm_service=svc).run("q")

        assert calls == ["db"]

    async def test_none_opts_out_of_the_guard(self) -> None:
        calls, wipe = _recorder()
        svc = _service([_call("wipe", {"target": "db"}), LLMResult(text="ok")])
        tool = ToolDefinition(
            name="wipe", fn=wipe, description="d", category="destructive"
        )

        await Agent(tools=[tool], llm_service=svc, autonomy_policy=None).run("q")

        assert calls == ["db"]

    async def test_a_policy_replaces_the_guard_with_the_approval_gate(self) -> None:
        calls, wipe = _recorder()
        svc = _service([_call("wipe", {"target": "db"}), LLMResult(text="ok")])
        tool = ToolDefinition(
            name="wipe", fn=wipe, description="d", category="destructive"
        )
        agent = Agent(tools=[tool], llm_service=svc, autonomy_policy=AutonomyPolicy())

        await agent.run("q")

        # No approval channel: the real gate denies, with its own message.
        assert calls == []
        block = _result_block(svc)
        assert block.is_error is True
        assert "declared destructive" not in block.content

    async def test_a_host_injected_policy_disarms_the_guard(self) -> None:
        tool = ToolDefinition(
            name="wipe", fn=lambda target: "x", description="d", category="destructive"
        )
        agent = Agent(tools=[tool], llm_service=_service([]))
        from core.agent._safety import destructive_denial

        assert destructive_denial(agent, tool) is not None
        agent._autonomy_policy = AutonomyPolicy()
        assert destructive_denial(agent, tool) is None


@pytest.mark.asyncio
class TestDefaultBudget:
    async def _seen_budget(self, **agent_kwargs: Any) -> LoopBudget | None:
        seen: list[LoopBudget | None] = []

        def probe() -> str:
            """Record the ambient budget."""
            seen.append(get_active_budget())
            return "ok"

        svc = _service([_call("probe", {}), LLMResult(text="done")])
        await Agent(tools=[probe], llm_service=svc, **agent_kwargs).run("q")
        return seen[0]

    async def test_a_standalone_run_gets_a_default_budget(self) -> None:
        budget = await self._seen_budget(max_iterations=40)

        assert budget is not None
        assert budget.limits.budget_usd == LoopLimits().budget_usd
        assert budget.limits.max_iterations == 40  # widened to the agent's cap
        assert budget.iterations == 2  # both round trips were ticked
        assert budget.tool_calls == 1
        assert get_active_budget() is None  # unbound after the run

    async def test_an_ambient_budget_is_reused_not_doubled(self) -> None:
        ambient = LoopBudget()
        token = activate_budget(ambient)
        try:
            budget = await self._seen_budget()
        finally:
            deactivate_budget(token)

        assert budget is ambient

    async def test_none_disables_the_default_budget(self) -> None:
        assert await self._seen_budget(loop_limits=None) is None

    async def test_explicit_limits_are_used(self) -> None:
        limits = LoopLimits(max_iterations=9, budget_usd=0.01)

        budget = await self._seen_budget(loop_limits=limits)

        assert budget is not None and budget.limits is limits

    async def test_the_default_budget_is_enforced(self) -> None:
        svc = _service([_call("probe", {}), LLMResult(text="done")])

        def probe() -> str:
            """Probe."""
            return "ok"

        agent = Agent(
            tools=[probe],
            llm_service=svc,
            loop_limits=LoopLimits(max_iterations=1),
        )
        with pytest.raises(BudgetExceededError):
            await agent.run("q")


class TestStrictToolSchemas:
    def _spec(self, tool: Any) -> Any:
        specs = Agent(tools=[tool], llm_service=object())._tool_specs()
        assert specs is not None
        return specs[0]

    def test_all_required_inferred_schema_is_strict(self) -> None:
        def lookup(city: str, year: int) -> str:
            """Look up."""
            return city

        spec = self._spec(lookup)

        assert spec.strict is True
        assert spec.parameters["additionalProperties"] is False
        assert sorted(spec.parameters["required"]) == ["city", "year"]

    def test_optional_argument_keeps_the_schema_lax(self) -> None:
        def lookup(city: str, year: int = 2024) -> str:
            """Look up."""
            return city

        spec = self._spec(lookup)

        assert spec.strict is False
        assert "additionalProperties" not in spec.parameters

    def test_explicit_schema_must_already_forbid_extras(self) -> None:
        open_schema = {
            "type": "object",
            "properties": {"q": {"type": "string"}},
            "required": ["q"],
        }
        assert strict_tool_parameters(open_schema, inferred=False) is None
        closed = {**open_schema, "additionalProperties": False}
        assert strict_tool_parameters(closed, inferred=False) == closed

    @pytest.mark.parametrize(
        "schema",
        [
            {"type": "object"},
            {"type": "object", "properties": {"q": {}}, "required": ["q"]},
            {
                "type": "object",
                "properties": {"q": {"type": "string", "pattern": "x"}},
                "required": ["q"],
            },
            {
                "type": "object",
                "properties": {"q": {"type": "array"}},
                "required": ["q"],
            },
        ],
    )
    def test_unsupported_shapes_stay_lax(self, schema: dict[str, Any]) -> None:
        assert strict_tool_parameters(schema, inferred=True) is None

    def test_nested_objects_must_also_be_strict(self) -> None:
        nested = {
            "type": "object",
            "properties": {
                "where": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                    "additionalProperties": False,
                }
            },
            "required": ["where"],
            "additionalProperties": False,
        }
        assert strict_tool_parameters(nested, inferred=False) == nested
        loose = {
            **nested,
            "properties": {"where": {**nested["properties"]["where"], "required": []}},
        }
        assert strict_tool_parameters(loose, inferred=False) is None

    def test_provider_mappings_emit_the_strict_flag(self) -> None:
        from core.services.llm.providers._anthropic_mapping import (
            _to_anthropic_tools,
        )
        from core.services.llm.providers._openai_mapping import to_openai_tools

        def lookup(city: str) -> str:
            """Look up."""
            return city

        spec = self._spec(lookup)

        assert _to_anthropic_tools([spec])[0]["strict"] is True
        assert to_openai_tools([spec])[0]["function"]["strict"] is True


@pytest.mark.asyncio
class TestGuardScope:
    """The guard is for standalone runs and for explicitly destructive tools."""

    async def test_agent_inside_an_orchestrated_request_is_not_guarded(
        self,
    ) -> None:
        calls, wipe = _recorder()
        svc = _service([_call("wipe", {"target": "db"}), LLMResult(text="ok")])
        agent = Agent(
            tools=[
                ToolDefinition(
                    name="wipe", fn=wipe, description="d", category="destructive"
                )
            ],
            llm_service=svc,
        )
        token = activate_budget(LoopBudget(limits=LoopLimits()))  # orchestrator
        try:
            await agent.run("q")
        finally:
            deactivate_budget(token)

        assert calls == ["db"]

    async def test_connector_action_left_at_default_category_runs(self) -> None:
        from core.connectors.types import ActionSpec

        action = ActionSpec(name="send", description="d")
        assert action.category == "destructive"

        calls, wipe = _recorder()
        svc = _service([_call("wipe", {"target": "db"}), LLMResult(text="ok")])
        tool = ToolDefinition(
            name="wipe", fn=wipe, description="d", category=action.category
        )
        assert tool.category_declared is False

        await Agent(tools=[tool], llm_service=svc).run("q")

        assert calls == ["db"]


def test_shared_limits_lift_counters_but_keep_cost_caps() -> None:
    from core.agent._safety import shared_loop_limits

    shared = shared_loop_limits()
    defaults = LoopLimits()
    assert shared.max_iterations >= 1_000_000
    assert shared.max_tool_calls >= 1_000_000
    assert shared.budget_usd == defaults.budget_usd
    assert shared.max_tokens == defaults.max_tokens
