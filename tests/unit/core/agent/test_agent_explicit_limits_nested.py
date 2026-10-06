"""An Agent's explicit LoopLimits hold under an ambient budget (child budget)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from core.agent import Agent, Crew, Task
from core.orchestration.budget_context import (
    activate_budget,
    deactivate_budget,
    get_active_budget,
)
from core.orchestration.limits import BudgetExceededError, LoopBudget, LoopLimits
from core.services.llm.tool_calling import LLMResult

pytestmark = [pytest.mark.unit]


def _charging_agent(
    cost: float, seen: list[LoopBudget | None], **agent_kwargs: Any
) -> Agent:
    """An agent whose every LLM round trip charges ``cost`` to the ambient."""

    async def generate(*args: Any, **kwargs: Any) -> LLMResult:
        budget = get_active_budget()
        seen.append(budget)
        assert budget is not None
        budget.charge(cost)
        return LLMResult(text="ok")

    svc = AsyncMock()
    svc.generate = AsyncMock(side_effect=generate)
    return Agent(llm_service=svc, **agent_kwargs)


async def test_explicit_cap_aborts_inside_crew_and_parent_sees_spend() -> None:
    seen: list[LoopBudget | None] = []
    agent = _charging_agent(0.06, seen, loop_limits=LoopLimits(budget_usd=0.05))
    crew = Crew(agents=[agent], tasks=[Task("one", agent=agent)])

    with pytest.raises(BudgetExceededError) as exc_info:
        await crew.run()

    assert exc_info.value.reason == "budget_usd"
    child = seen[0]
    assert child is not None and child.limits.budget_usd == 0.05
    parent = child.parent
    assert parent is not None
    # Crew-wide default cap is 0.50: the agent's own cap fired, not the crew's.
    assert parent.limits.budget_usd == LoopLimits().budget_usd
    assert parent.cost_usd == pytest.approx(0.06)  # charged once, not twice
    assert get_active_budget() is None


async def test_explicit_cap_under_orchestrated_ambient_budget() -> None:
    ambient = LoopBudget(limits=LoopLimits(budget_usd=10.0))
    seen: list[LoopBudget | None] = []
    agent = _charging_agent(0.06, seen, loop_limits=LoopLimits(budget_usd=0.05))
    token = activate_budget(ambient)
    try:
        with pytest.raises(BudgetExceededError):
            await agent.run("hi")
        assert get_active_budget() is ambient  # child unbound after the run
    finally:
        deactivate_budget(token)
    assert seen[0] is not ambient and seen[0].parent is ambient  # type: ignore[union-attr]
    assert ambient.cost_usd == pytest.approx(0.06)


async def test_explicit_cap_within_bounds_charges_parent_once() -> None:
    ambient = LoopBudget(limits=LoopLimits(budget_usd=10.0))
    seen: list[LoopBudget | None] = []
    agent = _charging_agent(0.01, seen, loop_limits=LoopLimits(budget_usd=0.05))
    token = activate_budget(ambient)
    try:
        await agent.run("hi")
    finally:
        deactivate_budget(token)
    child = seen[0]
    assert child is not None and child.cost_usd == pytest.approx(0.01)
    assert ambient.cost_usd == pytest.approx(0.01)
    assert ambient.iterations == child.iterations


@pytest.mark.parametrize("limits", ["default", None])
async def test_default_and_none_limits_reuse_the_ambient(limits: Any) -> None:
    ambient = LoopBudget(limits=LoopLimits(budget_usd=10.0))
    seen: list[LoopBudget | None] = []
    kwargs: dict[str, Any] = {} if limits == "default" else {"loop_limits": None}
    agent = _charging_agent(0.06, seen, **kwargs)
    token = activate_budget(ambient)
    try:
        await agent.run("hi")
    finally:
        deactivate_budget(token)
    assert seen == [ambient]
    assert ambient.cost_usd == pytest.approx(0.06)
