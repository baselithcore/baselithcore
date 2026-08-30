"""The ambient LLM-generation seam for tenant cost budgets.

``enforce_tenant_cost_budget`` runs before a generation, ``record_tenant_llm_cost``
after it; both resolve the tenant from the ambient context and fail open on
infrastructure errors — a quota-store outage must not take chat down.
"""

from __future__ import annotations

import pytest

from core.config.quotas import QuotaConfig
from core.context import set_tenant_context
from core.quotas import (  # package re-exports: house convention
    CostBudgetExceededError,
    QuotaManager,
    enforce_tenant_cost_budget,
    llm_call_cost_usd,
    record_tenant_llm_cost,
)
from core.quotas.store import InMemoryQuotaStore


@pytest.fixture()
def manager() -> QuotaManager:
    return QuotaManager(
        config=QuotaConfig(
            QUOTAS_ENABLED=True,
            QUOTA_BACKEND="memory",
            QUOTA_TENANT_DAILY_COST_USD=1.00,
        ),
        store=InMemoryQuotaStore(),
    )


@pytest.mark.asyncio
async def test_records_against_ambient_tenant_and_enforces(manager):
    token = set_tenant_context("acme")
    try:
        await record_tenant_llm_cost(1.50, manager=manager)
        with pytest.raises(CostBudgetExceededError):
            await enforce_tenant_cost_budget(manager=manager)
    finally:
        token and None  # contextvar token: reset not required in test isolation


@pytest.mark.asyncio
async def test_zero_cost_is_not_recorded(manager):
    set_tenant_context("acme")
    await record_tenant_llm_cost(0.0, manager=manager)
    status = await manager.peek_tenant_cost("acme")
    assert status.windows["daily"].used_usd == 0.0


def test_llm_call_cost_priced_model_is_positive():
    # Independent of the ambient LoopBudget: an out-of-request LLM call must
    # still be meterable on the tenant ledger.
    from core.models.pricing import DEFAULT_PRICING

    model = next(iter(DEFAULT_PRICING))
    assert llm_call_cost_usd(model, 1000, 1000) > 0


def test_llm_call_cost_unpriced_model_is_zero():
    assert llm_call_cost_usd("my-self-hosted-model", 1000, 1000) == 0.0


@pytest.mark.asyncio
async def test_identity_budget_enforced_from_ambient_user(monkeypatch):
    from core.config.quotas import QuotaConfig
    from core.context import set_user_context

    identity_manager = QuotaManager(
        config=QuotaConfig(
            QUOTAS_ENABLED=True,
            QUOTA_BACKEND="memory",
            QUOTA_IDENTITY_DAILY_COST_USD=0.50,
        ),
        store=InMemoryQuotaStore(),
    )
    set_tenant_context("acme")
    set_user_context("user-77")

    await record_tenant_llm_cost(0.60, manager=identity_manager)
    with pytest.raises(CostBudgetExceededError):
        await enforce_tenant_cost_budget(manager=identity_manager)


@pytest.mark.asyncio
async def test_fails_open_when_store_errors(manager, monkeypatch):
    set_tenant_context("acme")

    async def boom(*args, **kwargs):
        raise ConnectionError("redis down")

    monkeypatch.setattr(manager, "record_tenant_cost", boom)
    monkeypatch.setattr(manager, "check_tenant_cost_budget", boom)

    # Neither call may propagate the infrastructure error.
    await record_tenant_llm_cost(0.10, manager=manager)
    await enforce_tenant_cost_budget(manager=manager)
