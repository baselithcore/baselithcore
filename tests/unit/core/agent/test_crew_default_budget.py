"""A standalone Crew run shares one crew-wide budget; ambient wins; opt-out."""

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


def _recording_agent(seen: list[LoopBudget | None], text: str = "ok") -> Agent:
    async def generate(*args: Any, **kwargs: Any) -> LLMResult:
        seen.append(get_active_budget())
        return LLMResult(text=text)

    svc = AsyncMock()
    svc.generate = AsyncMock(side_effect=generate)
    return Agent(llm_service=svc)


async def _seen(process: str = "sequential", **crew_kwargs: Any) -> list[Any]:
    seen: list[LoopBudget | None] = []
    a, b = _recording_agent(seen), _recording_agent(seen)
    extra: dict[str, Any] = {}
    if process == "hierarchical":
        extra["manager"] = _recording_agent(seen, text="APPROVE")
    crew = Crew(
        agents=[a, b],
        tasks=[Task("one", agent=a), Task("two", agent=b)],
        process=process,
        **extra,
        **crew_kwargs,
    )
    await crew.run()
    return seen


@pytest.mark.parametrize("process", ["sequential", "parallel", "hierarchical"])
async def test_every_task_shares_one_crew_budget(process: str) -> None:
    seen = await _seen(process)

    assert len(seen) >= 2
    first = seen[0]
    assert first is not None and all(b is first for b in seen)
    # Cost caps are the orchestrator defaults; counters lifted for sharing.
    assert first.limits.budget_usd == LoopLimits().budget_usd
    assert first.limits.max_iterations > LoopLimits().max_iterations
    assert get_active_budget() is None  # unbound after the run


async def test_ambient_budget_wins() -> None:
    ambient = LoopBudget()
    token = activate_budget(ambient)
    try:
        seen = await _seen(loop_limits=LoopLimits(budget_usd=0.01))
    finally:
        deactivate_budget(token)

    assert seen == [ambient, ambient]


async def test_none_opts_out_to_per_task_budgets() -> None:
    seen = await _seen(loop_limits=None)

    # Each Agent.run binds its own default budget, as before the crew cap.
    assert seen[0] is not None and seen[1] is not None
    assert seen[0] is not seen[1]


async def test_explicit_limits_replace_the_default() -> None:
    limits = LoopLimits(budget_usd=0.01, max_iterations=100)

    seen = await _seen(loop_limits=limits)

    assert seen[0] is not None and seen[0] is seen[1]
    assert seen[0].limits is limits


async def test_a_breach_of_the_shared_cap_aborts_the_crew() -> None:
    # Each task ticks one iteration; a crew-wide cap of one stops the second.
    with pytest.raises(BudgetExceededError):
        await _seen(loop_limits=LoopLimits(max_iterations=1))
    assert get_active_budget() is None
