"""Tests for the plan-approve gate (review the plan before anything runs)."""

from __future__ import annotations

import pytest
from core.planning.approval import (
    PlanRejectedError,
    approve_plan,
    render_plan_for_review,
)

from core.orchestration.autonomy import AutonomyLevel, AutonomyPolicy
from core.planning.budget import PlanCostEstimate
from core.planning.planner import Plan, PlanStep

pytestmark = [pytest.mark.unit]


def _plan() -> Plan:
    return Plan(
        goal="migrate the billing table",
        steps=[
            PlanStep(id="s1", description="snapshot the table", action="execute"),
            PlanStep(
                id="s2",
                description="apply migration",
                action="execute",
                dependencies=["s1"],
            ),
        ],
    )


class _Human:
    def __init__(self, answer: bool) -> None:
        self._answer = answer
        self.requests: list[tuple[str, dict]] = []

    async def request_approval(self, description, timeout=None, context=None):
        self.requests.append((description, context or {}))
        return self._answer


class TestRender:
    def test_render_carries_goal_steps_and_estimate(self):
        estimate = PlanCostEstimate(total_tokens=1200, total_tool_calls=3)
        text = render_plan_for_review(_plan(), estimate)
        assert "migrate the billing table" in text
        assert "snapshot the table" in text
        assert "apply migration" in text
        assert "1200" in text and "3" in text


class TestApprovePlan:
    async def test_supervised_requires_and_honors_approval(self):
        human = _Human(answer=True)
        approved = await approve_plan(
            _plan(),
            policy=AutonomyPolicy(level=AutonomyLevel.SUPERVISED),
            human=human,
        )
        assert approved is True
        assert len(human.requests) == 1
        assert "migrate the billing table" in human.requests[0][0]

    async def test_supervised_denied_raises(self):
        with pytest.raises(PlanRejectedError):
            await approve_plan(
                _plan(),
                policy=AutonomyPolicy(level=AutonomyLevel.SUPERVISED),
                human=_Human(answer=False),
            )

    async def test_supervised_without_channel_fails_closed(self):
        with pytest.raises(PlanRejectedError):
            await approve_plan(
                _plan(),
                policy=AutonomyPolicy(level=AutonomyLevel.SUPERVISED),
                human=None,
            )

    async def test_fully_autonomous_skips_review(self):
        approved = await approve_plan(
            _plan(),
            policy=AutonomyPolicy(level=AutonomyLevel.FULLY_AUTONOMOUS),
            human=None,
        )
        assert approved is True

    async def test_no_policy_skips_review(self):
        assert await approve_plan(_plan(), policy=None, human=None) is True
