"""A standalone group chat binds a default budget; ambient wins; opt-out."""

from __future__ import annotations

import pytest

from core.agent.group_chat import ChatMessage, GroupChat, RoundRobinSelector
from core.orchestration.budget_context import (
    activate_budget,
    deactivate_budget,
    get_active_budget,
)
from core.orchestration.limits import BudgetExceededError, LoopBudget

pytestmark = [pytest.mark.unit]


class _Probe:
    def __init__(self, name: str = "p", *, spend: bool = False) -> None:
        self.name = name
        self.capabilities: list[str] = []
        self.seen: list[LoopBudget | None] = []
        self._spend = spend

    async def respond(self, topic: str, transcript: list[ChatMessage]) -> str:
        budget = get_active_budget()
        self.seen.append(budget)
        if self._spend and budget is not None:
            budget.charge(budget.limits.budget_usd + 1)
        return "hi"


async def test_standalone_chat_binds_a_default_budget() -> None:
    probe = _Probe()

    result = await GroupChat([probe], RoundRobinSelector(), max_rounds=3).run("t")

    assert result.rounds == 3
    budget = probe.seen[0]
    assert budget is not None and all(b is budget for b in probe.seen)
    assert budget.iterations == 0  # deadline-checked, not ticked
    assert get_active_budget() is None


async def test_breach_of_the_default_budget_ends_the_chat_cleanly() -> None:
    result = await GroupChat(
        [_Probe(spend=True)], RoundRobinSelector(), max_rounds=5
    ).run("t")

    assert result.terminated_by == "budget"
    assert result.rounds == 0


async def test_ambient_budget_is_used_and_its_breach_propagates() -> None:
    ambient = LoopBudget()
    token = activate_budget(ambient)
    try:
        probe = _Probe()
        await GroupChat([probe], RoundRobinSelector(), max_rounds=2).run("t")
        assert probe.seen == [ambient, ambient]
        assert ambient.iterations == 0  # not ticked by the chat

        with pytest.raises(BudgetExceededError):
            await GroupChat(
                [_Probe(spend=True)], RoundRobinSelector(), max_rounds=2
            ).run("t")
    finally:
        deactivate_budget(token)


async def test_none_opts_out() -> None:
    probe = _Probe()

    await GroupChat([probe], RoundRobinSelector(), max_rounds=2, budget=None).run("t")

    assert probe.seen == [None, None]
