"""Runtime enforcement chokepoint for the orchestration loop.

Bridges the safety primitives that :class:`~core.orchestration.mixins.execution.ExecutionMixin`
injects onto the orchestration context — :class:`~core.orchestration.limits.LoopBudget`,
:class:`~core.orchestration.contract.ContractValidator` and
:class:`~core.orchestration.autonomy.AutonomyPolicy` — to the points where
handlers actually iterate and invoke tools.

Historically these primitives were constructed and placed on the context but
never consulted on the hot path, so their caps/gates were advisory only. These
helpers give handlers (and the parallel tool executor) a single, uniform place
to enforce them.

Each helper is a **no-op when its primitive is absent** from the context, so
handlers may call them unconditionally without breaking legacy or
non-orchestrated code paths (backward compatible by construction).
"""

from __future__ import annotations

from typing import Any, Mapping

from core.observability.logging import get_logger
from core.orchestration.autonomy import enforce_approval

logger = get_logger(__name__)

__all__ = ["enforce_iteration", "enforce_tool_invocation"]


def enforce_iteration(context: Mapping[str, Any] | None) -> None:
    """Advance the per-request loop budget by one iteration.

    Call once per loop step in an agentic handler to enforce the iteration
    cap. No-op when no ``loop_budget`` is present on the context.

    Args:
        context: The orchestration context carrying ``loop_budget``.

    Raises:
        BudgetExceededError: The iteration cap has been reached.
    """
    if not context:
        return
    budget = context.get("loop_budget")
    if budget is not None:
        budget.tick()


async def enforce_tool_invocation(
    context: Mapping[str, Any] | None,
    tool_name: str,
    category: str = "read_only",
    *,
    cost_usd: float | None = None,
) -> None:
    """Gate a single tool invocation through every configured control.

    Fail-closed order:

    1. **Contract** — ``contract_validator.check_tool_call`` rejects tools that
       violate the agent's ``allowed_tools`` / ``must_not`` capabilities.
    2. **Autonomy** — ``enforce_approval`` requires human approval for tools
       whose ``category`` needs it at the active autonomy level, and fails
       closed when no approval channel is available.
    3. **Budget** — ``loop_budget.record_tool_call`` (and optional
       ``loop_budget.charge``) enforce the tool-call count and USD caps.

    Each control is skipped only when its primitive is not on the context.

    Args:
        context: The orchestration context carrying the safety primitives.
        tool_name: Name of the tool being invoked (for the contract check and
            audit/error context).
        category: Autonomy category — one of ``read_only`` / ``mutating`` /
            ``destructive`` / ``external_side_effect``. Defaults to the most
            permissive; side-effecting tools MUST pass their real category to
            be gated.
        cost_usd: Optional estimated cost of this call, charged against the
            budget's USD cap.

    Raises:
        ContractViolationError: Tool not permitted by the agent contract.
        ApprovalRequiredError: Approval required but unavailable or denied.
        BudgetExceededError: Tool-call or cost cap exceeded.
        ValueError: Unknown autonomy category.
    """
    if not context:
        return
    validator = context.get("contract_validator")
    if validator is not None:
        validator.check_tool_call(tool_name)
    policy = context.get("autonomy_policy")
    if policy is not None:
        await enforce_approval(
            policy,
            category,
            tool_name,
            context.get("human_intervention"),
        )
    budget = context.get("loop_budget")
    if budget is not None:
        budget.record_tool_call()
        if cost_usd is not None:
            budget.charge(cost_usd)
