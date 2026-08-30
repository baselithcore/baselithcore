"""Plan-approve gate: a human reviews the plan before anything executes.

``TaskPlanner`` produces a reviewable :class:`~core.planning.planner.Plan`
and ``PlanCostEstimate`` prices it, but nothing composed them into "emit
plan → block for sign-off → execute". This module is that composition:
under an :class:`~core.orchestration.autonomy.AutonomyPolicy` whose level
requires approval for mutating work, the rendered plan goes to the human
channel and execution is blocked until an explicit yes — fail closed when
no channel is available. At full autonomy (or with no policy) the gate is
a pass-through.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.observability.logging import get_logger
from core.orchestration.autonomy import MUTATING

if TYPE_CHECKING:
    from core.human.interaction import HumanIntervention
    from core.orchestration.autonomy import AutonomyPolicy
    from core.planning.budget import PlanCostEstimate
    from core.planning.planner import Plan

logger = get_logger(__name__)


class PlanRejectedError(PermissionError):
    """The plan was denied review approval (or no channel could grant it)."""


def render_plan_for_review(plan: Plan, estimate: PlanCostEstimate | None = None) -> str:
    """Render the plan as a compact, human-reviewable Markdown block."""
    lines = [f"**Plan:** {plan.goal}", ""]
    for index, step in enumerate(plan.steps, start=1):
        needs = f" (after {', '.join(step.dependencies)})" if step.dependencies else ""
        lines.append(f"{index}. [{step.action}] {step.description}{needs}")
    if estimate is not None:
        lines.append("")
        lines.append(
            "**Estimated cost:** "
            f"~{estimate.total_tokens} tokens, "
            f"{estimate.total_tool_calls} tool call(s), "
            f"~{estimate.total_latency_ms}ms"
        )
    return "\n".join(lines)


async def approve_plan(
    plan: Plan,
    *,
    policy: AutonomyPolicy | None,
    human: HumanIntervention | None,
    estimate: PlanCostEstimate | None = None,
    timeout: int | None = None,
) -> bool:
    """Gate a plan behind human review when the autonomy level demands it.

    Args:
        plan: The plan about to be executed.
        policy: Active autonomy policy; ``None`` skips the gate entirely.
            The gate applies when the policy requires approval for
            ``mutating`` work (plan execution changes state by definition).
        human: Approval channel. Required when the gate applies — its
            absence fails closed.
        estimate: Optional cost estimate shown alongside the plan.
        timeout: Seconds to wait for the human decision.

    Returns:
        True when execution may proceed.

    Raises:
        PlanRejectedError: Review was required and was denied, timed out,
            or had no channel to run on.
    """
    if policy is None or not policy.requires_approval(MUTATING):
        return True
    if human is None:
        raise PlanRejectedError(
            "plan review required at this autonomy level but no approval "
            "channel is available (fail closed)"
        )
    rendered = render_plan_for_review(plan, estimate)
    approved = await human.request_approval(
        rendered,
        timeout=timeout,
        context={
            "goal": plan.goal,
            "steps": len(plan.steps),
            "estimated_tokens": estimate.total_tokens if estimate else None,
        },
    )
    if not approved:
        logger.warning("plan_rejected goal=%s", plan.goal)
        raise PlanRejectedError(f"plan for {plan.goal!r} was not approved")
    return True


__all__ = ["PlanRejectedError", "approve_plan", "render_plan_for_review"]
