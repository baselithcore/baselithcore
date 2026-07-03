"""
Ambient per-request LoopBudget propagation.

The orchestrator creates a :class:`~core.orchestration.limits.LoopBudget`
per request and carries it on the orchestration context dict — but LLM
calls happen many layers below (handlers, reasoning engines, LLMService),
where threading the dict through every signature is impractical. This
module exposes the active budget through a ``ContextVar`` so the LLM
service can charge real dollar cost against the request that triggered it,
making ``LoopLimits.budget_usd`` an enforced cap instead of an advisory one.

Charging policy: only models present in the pricing table are charged.
The pricing table's punitive ``UNKNOWN_PRICE`` fallback is right for cost
*reporting* (missing entries become visible) but wrong for *enforcement* —
a self-hosted or unlisted model would burn any realistic budget within a
few thousand tokens and abort production requests spuriously.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

from core.observability.logging import get_logger
from core.orchestration.limits import LoopBudget

logger = get_logger(__name__)

_active_budget: ContextVar[LoopBudget | None] = ContextVar(
    "active_loop_budget", default=None
)


def activate_budget(budget: LoopBudget) -> Token:
    """Bind ``budget`` as the ambient budget for the current async context."""
    return _active_budget.set(budget)


def deactivate_budget(token: Token) -> None:
    """Restore the previous ambient budget."""
    _active_budget.reset(token)


def get_active_budget() -> LoopBudget | None:
    """Return the ambient budget, or None outside an orchestrated request."""
    return _active_budget.get()


def charge_llm_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Charge an LLM call against the ambient budget, if one is active.

    Returns the USD cost charged (0.0 when no budget is active or the model
    is not in the pricing table). Raises
    :class:`~core.orchestration.limits.BudgetExceededError` when the charge
    pushes the request over its ``budget_usd`` cap.
    """
    budget = _active_budget.get()
    if budget is None:
        return 0.0

    # Record token usage first, for EVERY model (including self-hosted/unpriced).
    # Tokens are a capability cap independent of dollar pricing, so a model
    # absent from the pricing table still counts against ``max_tokens`` and can
    # raise BudgetExceededError("max_tokens").
    budget.record_tokens(max(prompt_tokens, 0) + max(completion_tokens, 0))

    from core.models.pricing import DEFAULT_PRICING, estimate_cost

    if model not in DEFAULT_PRICING:
        logger.debug("llm_cost_not_charged_unknown_model", extra={"model": model})
        return 0.0

    cost = estimate_cost(model, max(prompt_tokens, 0), max(completion_tokens, 0))
    if cost > 0:
        budget.charge(cost)
    return cost
