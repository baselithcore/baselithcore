"""Ambient tenant cost-budget seam for the LLM generation path.

``LoopBudget.budget_usd`` caps one run; this seam caps a tenant's cumulative
spend across runs. It is called from inside the LLM service (like
``budget_context.charge_llm_cost``), resolves the tenant from the ambient
context, and **fails open on infrastructure errors**: a quota-store outage
must degrade to unmetered service, never to an outage of its own. The budget
rejection itself (:class:`~core.quotas.manager.CostBudgetExceededError`)
always propagates.
"""

from __future__ import annotations

from core.context import get_current_user_id, get_tenant_or_default
from core.observability.logging import get_logger
from core.quotas.manager import (
    CostBudgetExceededError,
    QuotaManager,
    get_quota_manager,
)

logger = get_logger(__name__)


def llm_call_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """USD cost of one LLM call for tenant metering.

    Independent of the ambient per-run ``LoopBudget`` (whose ``charge_llm_cost``
    returns 0.0 when no budget is active), so out-of-request calls — background
    jobs, scripts — are metered too. Same pricing policy as enforcement:
    models absent from the pricing table cost 0 (a self-hosted model must not
    burn a dollar budget on punitive fallback pricing).
    """
    from core.models.pricing import DEFAULT_PRICING, estimate_cost

    if model not in DEFAULT_PRICING:
        return 0.0
    return estimate_cost(model, max(prompt_tokens, 0), max(completion_tokens, 0))


async def enforce_tenant_cost_budget(*, manager: QuotaManager | None = None) -> None:
    """Gate an LLM call on the ambient tenant's cumulative cost budget.

    Raises:
        CostBudgetExceededError: When the tenant's recorded spend has reached
            a configured window limit. Infrastructure errors are swallowed
            (fail-open) with a warning.
    """
    manager = manager or get_quota_manager()
    tenant_id = get_tenant_or_default()
    try:
        await manager.check_tenant_cost_budget(tenant_id)
        user_id = get_current_user_id()
        if user_id:
            await manager.check_identity_cost_budget(user_id)
    except CostBudgetExceededError:
        raise
    except Exception as exc:
        logger.warning(
            "tenant_cost_budget_check_failed_open",
            extra={"tenant_id": tenant_id, "error": str(exc)},
        )


async def record_tenant_llm_cost(
    usd: float, *, manager: QuotaManager | None = None
) -> None:
    """Book one LLM call's dollar cost against the ambient tenant's ledger.

    Never raises: the spend already happened, and a store outage must not
    fail the request that produced a perfectly good answer.
    """
    if usd <= 0:
        return
    manager = manager or get_quota_manager()
    tenant_id = get_tenant_or_default()
    try:
        await manager.record_tenant_cost(tenant_id, usd)
        user_id = get_current_user_id()
        if user_id:
            await manager.record_identity_cost(user_id, usd)
    except Exception as exc:
        logger.warning(
            "tenant_cost_record_failed_open",
            extra={"tenant_id": tenant_id, "error": str(exc)},
        )


__all__ = [
    "enforce_tenant_cost_budget",
    "llm_call_cost_usd",
    "record_tenant_llm_cost",
]
