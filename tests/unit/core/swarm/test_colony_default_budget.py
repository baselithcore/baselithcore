"""A standalone swarm batch shares one default budget; ambient wins; opt-out."""

from __future__ import annotations

from typing import Any

import pytest

from core.config.swarm import AuctionConfig, SwarmConfig
from core.orchestration.budget_context import (
    activate_budget,
    deactivate_budget,
    get_active_budget,
)
from core.orchestration.limits import LoopBudget, LoopLimits
from core.swarm.colony import Colony
from core.swarm.types import AgentProfile, Capability, Task

pytestmark = [pytest.mark.unit]


def _colony(**kwargs: Any) -> Colony:
    colony = Colony(
        config=SwarmConfig(auction=AuctionConfig(tie_breaker="first")), **kwargs
    )
    for aid in ("a1", "a2"):
        colony.register_agent(
            AgentProfile(
                id=aid,
                name=aid,
                capabilities=[Capability(name="cap1", proficiency=0.9)],
                success_rate=0.9,
            )
        )
    return colony


async def _seen(colony: Colony) -> list[LoopBudget | None]:
    seen: list[LoopBudget | None] = []

    async def execute_fn(task: Task, agent: AgentProfile) -> str:
        seen.append(get_active_budget())
        return "ok"

    tasks = [Task(description=f"t{i}", required_capabilities=["cap1"]) for i in (1, 2)]
    await colony.execute_batch(tasks, execute_fn)
    return seen


async def test_batch_tasks_share_one_default_budget() -> None:
    seen = await _seen(_colony())

    assert len(seen) == 2
    assert seen[0] is not None and seen[0] is seen[1]
    assert seen[0].limits.budget_usd == LoopLimits().budget_usd
    assert get_active_budget() is None


async def test_ambient_budget_is_reused() -> None:
    ambient = LoopBudget()
    token = activate_budget(ambient)
    try:
        seen = await _seen(_colony())
    finally:
        deactivate_budget(token)

    assert seen == [ambient, ambient]


async def test_none_disables_and_explicit_limits_apply() -> None:
    assert await _seen(_colony(loop_limits=None)) == [None, None]

    limits = LoopLimits(budget_usd=0.01)
    seen = await _seen(_colony(loop_limits=limits))
    assert seen[0] is not None and seen[0].limits is limits
