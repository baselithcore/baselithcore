"""
Ambient per-request LoopBudget propagation.

The orchestrator creates a :class:`~core.orchestration.limits.LoopBudget`
per request and carries it on the orchestration context dict — but LLM
calls happen many layers below (handlers, reasoning engines, LLMService),
where threading the dict through every signature is impractical. This
module exposes the active budget through a ``ContextVar`` so the LLM
service can charge real dollar cost against the request that triggered it,
making ``LoopLimits.budget_usd`` an enforced cap instead of an advisory one.

Charging policy: a model absent from the pricing table is priced via
:func:`core.quotas.cost_enforcement.price_unknown_model`, so this seam and
tenant/identity metering share one ``BASELITH_UNKNOWN_MODEL_COST_POLICY``
knob (default ``charge``, i.e. ``UNKNOWN_PRICE`` — visible in cost
dashboards instead of silently free). A ``reject``-policy rejection
(:class:`~core.quotas.cost_enforcement.UnknownModelCostRejected`) is guarded
here and treated as a zero charge: this module's only charging exception is
:class:`~core.orchestration.limits.BudgetExceededError`. Regardless of
policy or whether a budget is even active, an unpriced model id gets a
one-time warning per process so a missing pricing entry stays visible.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

from core.observability.logging import get_logger
from core.orchestration.limits import LoopBudget

logger = get_logger(__name__)

_active_budget: ContextVar[LoopBudget | None] = ContextVar(
    "active_loop_budget", default=None
)

# Models we have already warned about missing a pricing entry in this
# process. One loud log line per model id, not one per call.
_warned_unpriced_model_ids: set[str] = set()


def _warn_unpriced_model_once(model: str) -> None:
    if model in _warned_unpriced_model_ids:
        return
    _warned_unpriced_model_ids.add(model)
    logger.warning("llm_cost_not_charged_unknown_model", extra={"model": model})


def activate_budget(budget: LoopBudget) -> Token:
    """Bind ``budget`` as the ambient budget for the current async context."""
    return _active_budget.set(budget)


def deactivate_budget(token: Token) -> None:
    """Restore the previous ambient budget."""
    _active_budget.reset(token)


def get_active_budget() -> LoopBudget | None:
    """Return the ambient budget, or None outside an orchestrated request."""
    return _active_budget.get()


def charge_llm_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    *,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    batch: bool = False,
) -> float:
    """Charge an LLM call against the ambient budget, if one is active.

    ``cache_read_tokens``/``cache_write_tokens`` line up with the field names
    on the LLM service's ``Usage`` dataclass, so a caller can forward
    ``usage.cache_read_tokens`` / ``usage.cache_write_tokens`` directly.

    Returns the USD cost charged (0.0 when no budget is active). Raises
    :class:`~core.orchestration.limits.BudgetExceededError` when the charge
    pushes the request over its ``budget_usd`` cap.
    """
    from core.models.pricing import is_priced

    unpriced = not is_priced(model)
    if unpriced:
        # Visibility into a missing pricing entry must not depend on whether
        # an orchestrated request happens to be running.
        _warn_unpriced_model_once(model)

    budget = _active_budget.get()
    if budget is None:
        return 0.0

    # Record token usage first, for EVERY model (including self-hosted/unpriced).
    # Tokens are a capability cap independent of dollar pricing, so a model
    # absent from the pricing table still counts against ``max_tokens`` and can
    # raise BudgetExceededError("max_tokens").
    budget.record_tokens(max(prompt_tokens, 0) + max(completion_tokens, 0))

    if unpriced:
        from core.quotas.cost_enforcement import (
            UnknownModelCostRejected,
            price_unknown_model,
        )

        try:
            cost = price_unknown_model(
                model,
                prompt_tokens,
                completion_tokens,
                cache_read_tokens=cache_read_tokens,
                cache_write_tokens=cache_write_tokens,
                batch=batch,
            )
        except UnknownModelCostRejected:
            # This seam's only charging exception is BudgetExceededError; a
            # policy rejection is a "don't charge", not a run-aborting error.
            return 0.0
    else:
        from core.models.pricing import estimate_cost

        cost = estimate_cost(
            model,
            max(prompt_tokens, 0),
            max(completion_tokens, 0),
            cache_read_tokens=max(cache_read_tokens, 0),
            cache_write_tokens=max(cache_write_tokens, 0),
            batch=batch,
        )

    if cost > 0:
        budget.charge(cost)
    return cost
