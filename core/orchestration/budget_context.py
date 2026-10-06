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

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any, Final

from core.observability.logging import get_logger
from core.orchestration.limits import LoopBudget, LoopLimits

logger = get_logger(__name__)

_active_budget: ContextVar[LoopBudget | None] = ContextVar(
    "active_loop_budget", default=None
)
#: True while the ambient budget is one :func:`standalone_budget` created
#: (a run outside any orchestrated request), False under ``activate_budget``.
_standalone_bound: ContextVar[bool] = ContextVar(
    "baselith_standalone_budget_bound", default=False
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


class _DefaultLimits:
    """Sentinel: "use the safe default" (distinct from an explicit ``None``)."""

    def __repr__(self) -> str:
        return "DEFAULT"


#: Default for the ``loop_limits``/``budget`` arguments of runs that bind a
#: :func:`standalone_budget` — "the default caps", where ``None`` means "none".
DEFAULT_LIMITS: Final[Any] = _DefaultLimits()


@contextmanager
def standalone_budget(
    limits: LoopLimits | None, *, enforce_own: bool = False
) -> Iterator[LoopBudget | None]:
    """Bind a budget for a run that may be happening outside any request.

    ``Orchestrator.process`` gives every request a :class:`LoopBudget`; a
    typed ``Agent``, a ``GroupChat`` or a swarm batch started from a script,
    a worker or a test had none, so it was bounded by its own iteration count
    and nothing else — no dollar, token or wall-clock cap. This closes that
    gap without double-applying one:

    * an ambient budget already active (the run is inside an orchestrated
      request, or nested in another standalone run) is reused untouched —
      the run charges the request it belongs to, exactly as before;
    * otherwise, with ``limits``, a fresh budget is created and bound as the
      ambient one for the duration of the block, so every LLM call inside it
      is charged through :func:`charge_llm_cost`;
    * ``limits=None`` binds nothing — the explicit opt-out.

    With ``enforce_own=True`` (a caller's *explicit* caps, e.g.
    ``Agent(loop_limits=LoopLimits(budget_usd=0.05))``) and an ambient budget
    present, a nested child budget is bound instead of reusing the ambient
    one untouched: the child enforces ``limits`` and forwards every unit it
    records to the ambient budget (:attr:`LoopBudget.parent`), so the
    enclosing request, crew or chat still sees — and caps — the full spend,
    counted once. The ambient's orchestrated/standalone flag is unchanged.

    Args:
        limits: Caps for the fresh budget, or ``None`` to bind none.
        enforce_own: Also enforce ``limits`` under an ambient budget.

    Yields:
        The budget in force (ambient or fresh), or ``None`` when opted out
        with no ambient budget.
    """
    ambient = _active_budget.get()
    if ambient is not None and limits is not None and enforce_own:
        child = LoopBudget(limits=limits, parent=ambient)
        child_token = _active_budget.set(child)
        try:
            yield child
        finally:
            _active_budget.reset(child_token)
        return
    if ambient is not None or limits is None:
        yield ambient
        return
    budget = LoopBudget(limits=limits)
    token = _active_budget.set(budget)
    standalone_token = _standalone_bound.set(True)
    try:
        yield budget
    finally:
        _standalone_bound.reset(standalone_token)
        _active_budget.reset(token)


def in_orchestrated_request() -> bool:
    """Whether the current code runs under an orchestrator-bound budget.

    True inside ``Orchestrator.process`` (and anything it calls, such as a
    plugin handler building its own ``Agent``); False in a standalone run,
    including one nested in another standalone run.

    Returns:
        ``True`` when the ambient budget was bound by the orchestrator.
    """
    return _active_budget.get() is not None and not _standalone_bound.get()


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
