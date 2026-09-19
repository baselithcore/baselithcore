"""Ambient tenant cost-budget seam for the LLM generation path.

``LoopBudget.budget_usd`` caps one run; this seam caps a tenant's cumulative
spend across runs. It is called from inside the LLM service (like
``budget_context.charge_llm_cost``), resolves the tenant from the ambient
context, and **fails open on infrastructure errors**: a quota-store outage
must degrade to unmetered service, never to an outage of its own. The budget
rejection itself (:class:`~core.quotas.manager.CostBudgetExceededError`)
always propagates.

This module also owns the **unknown-model cost policy**: what to charge a
tenant for a model absent from :data:`core.models.pricing.DEFAULT_PRICING`.
See :class:`UnknownModelCostConfig` / ``BASELITH_UNKNOWN_MODEL_COST_POLICY``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from core.context import get_current_user_id, get_tenant_or_default
from core.observability.logging import get_logger
from core.quotas.manager import (
    CostBudgetExceededError,
    QuotaManager,
    get_quota_manager,
)

logger = get_logger(__name__)

UnknownModelCostPolicy = Literal["charge", "zero", "reject"]


class UnknownModelCostConfig(BaseSettings):
    """Policy for pricing an LLM call to a model absent from the pricing table."""

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    policy: UnknownModelCostPolicy = Field(
        default="charge",
        alias="BASELITH_UNKNOWN_MODEL_COST_POLICY",
        description=(
            "How to price an LLM call to a model absent from the pricing "
            "table: 'charge' (default) bills UNKNOWN_PRICE so a missing "
            "entry stays visible in cost dashboards instead of silently "
            "billing $0; 'zero' treats the call as free (e.g. a "
            "self-hosted model with no meaningful USD cost); 'reject' "
            "raises UnknownModelCostRejected instead of billing anything."
        ),
    )


_unknown_model_cost_config: UnknownModelCostConfig | None = None


def get_unknown_model_cost_config() -> UnknownModelCostConfig:
    """Get or create the global unknown-model cost policy configuration."""
    global _unknown_model_cost_config
    if _unknown_model_cost_config is None:
        _unknown_model_cost_config = UnknownModelCostConfig()
    return _unknown_model_cost_config


class UnknownModelCostRejected(Exception):
    """Raised for a call to an unpriced model when the policy is ``reject``."""


# Models we have already warned about in this process. A missing pricing
# entry is worth one loud log line, not one per call — a hot path calling an
# unpriced model thousands of times must not flood the logs.
_warned_unknown_model_ids: set[str] = set()


def _warn_unknown_model_once(model: str) -> None:
    if model in _warned_unknown_model_ids:
        return
    _warned_unknown_model_ids.add(model)
    logger.warning("unknown_model_cost", extra={"model": model})


def price_unknown_model(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    *,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    batch: bool = False,
) -> float:
    """Resolve the USD cost of a call to a model absent from the pricing table.

    Governed by :func:`get_unknown_model_cost_config` /
    ``BASELITH_UNKNOWN_MODEL_COST_POLICY`` (default ``charge``). Always warns
    once per model id per process, regardless of policy, so a missing entry
    is never silently invisible — including under ``zero``.

    A locally-served model never reaches here: it is priced at zero by
    :func:`core.models.pricing.get_price`, because charging it ``UNKNOWN_PRICE``
    would bill 100 $/M for inference that cost no money at all.

    Raises:
        UnknownModelCostRejected: When the policy is ``reject``.
    """
    _warn_unknown_model_once(model)
    policy = get_unknown_model_cost_config().policy
    if policy == "reject":
        raise UnknownModelCostRejected(
            f"model {model!r} has no pricing entry and "
            "BASELITH_UNKNOWN_MODEL_COST_POLICY=reject"
        )
    if policy == "zero":
        return 0.0

    from core.models.pricing import UNKNOWN_PRICE

    return UNKNOWN_PRICE.estimate(
        max(prompt_tokens, 0),
        max(completion_tokens, 0),
        cache_read_tokens=max(cache_read_tokens, 0),
        cache_write_tokens=max(cache_write_tokens, 0),
        batch=batch,
    )


def llm_call_cost_usd(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    *,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    batch: bool = False,
) -> float:
    """USD cost of one LLM call for tenant metering.

    Independent of the ambient per-run ``LoopBudget`` (whose ``charge_llm_cost``
    returns 0.0 when no budget is active), so out-of-request calls — background
    jobs, scripts — are metered too. A model absent from the pricing table is
    priced via :func:`price_unknown_model` (``BASELITH_UNKNOWN_MODEL_COST_POLICY``,
    default ``charge``).
    """
    from core.models.pricing import estimate_cost, is_priced

    if not is_priced(model):
        return price_unknown_model(
            model,
            prompt_tokens,
            completion_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            batch=batch,
        )
    return estimate_cost(
        model,
        max(prompt_tokens, 0),
        max(completion_tokens, 0),
        cache_read_tokens=max(cache_read_tokens, 0),
        cache_write_tokens=max(cache_write_tokens, 0),
        batch=batch,
    )


def _reject_unpriced_model(model: str) -> None:
    """Apply ``BASELITH_UNKNOWN_MODEL_COST_POLICY=reject`` before the call.

    The setting describes itself as raising "instead of billing anything",
    which can only be true of a check that runs *before* the provider is
    asked. Applied after the answer exists it destroys a completed, already
    paid-for generation — so the post-call accounting sites treat a rejection
    as "don't meter" and this is where the refusal actually lives.

    No-op under the ``charge`` and ``zero`` policies, and for any model that
    has a known rate — a pricing row, or the zero rate of a locally-served
    model, which is priced, not unpriceable.
    """
    from core.models.pricing import is_priced

    if is_priced(model):
        return
    if get_unknown_model_cost_config().policy != "reject":
        return
    _warn_unknown_model_once(model)
    raise UnknownModelCostRejected(
        f"model {model!r} has no pricing entry and "
        "BASELITH_UNKNOWN_MODEL_COST_POLICY=reject"
    )


async def enforce_tenant_cost_budget(
    *, model: str | None = None, manager: QuotaManager | None = None
) -> None:
    """Gate an LLM call on the ambient tenant's cumulative cost budget.

    Args:
        model: The model about to be called. When given, the unknown-model
            cost policy is applied here too, so a ``reject``-policy
            deployment refuses an unpriceable call before it spends rather
            than after the provider has answered.
        manager: Quota manager override (the process-wide one by default).

    Raises:
        CostBudgetExceededError: When the tenant's recorded spend has reached
            a configured window limit. Infrastructure errors are swallowed
            (fail-open) with a warning.
        UnknownModelCostRejected: When *model* has no pricing entry and the
            policy is ``reject``. Deliberately outside the fail-open guard: a
            configured refusal to run the call is not an infrastructure
            failure.
    """
    if model is not None:
        _reject_unpriced_model(model)
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
    "UnknownModelCostConfig",
    "UnknownModelCostPolicy",
    "UnknownModelCostRejected",
    "enforce_tenant_cost_budget",
    "get_unknown_model_cost_config",
    "llm_call_cost_usd",
    "price_unknown_model",
    "record_tenant_llm_cost",
]
