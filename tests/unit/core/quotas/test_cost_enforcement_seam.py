"""The ambient LLM-generation seam for tenant cost budgets.

``enforce_tenant_cost_budget`` runs before a generation, ``record_tenant_llm_cost``
after it; both resolve the tenant from the ambient context and fail open on
infrastructure errors — a quota-store outage must not take chat down.
"""

from __future__ import annotations

from unittest.mock import patch

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
from core.quotas import cost_enforcement as ce_mod
from core.quotas.cost_enforcement import UnknownModelCostRejected
from core.quotas.store import InMemoryQuotaStore


@pytest.fixture(autouse=True)
def _reset_unknown_model_cost_state(monkeypatch):
    """Isolate the module-level warn-once set and policy singleton per test."""
    ce_mod._warned_unknown_model_ids.clear()
    monkeypatch.setattr(ce_mod, "_unknown_model_cost_config", None)
    monkeypatch.delenv("BASELITH_UNKNOWN_MODEL_COST_POLICY", raising=False)
    yield
    ce_mod._warned_unknown_model_ids.clear()


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


def test_llm_call_cost_unpriced_model_charges_unknown_price_by_default():
    """Default policy is 'charge': UNKNOWN_PRICE is billed, not silently 0."""
    from core.models.pricing import UNKNOWN_PRICE

    cost = llm_call_cost_usd("my-self-hosted-model", 1000, 1000)
    assert cost == pytest.approx(UNKNOWN_PRICE.estimate(1000, 1000))
    assert cost > 0


def test_unknown_model_policy_zero_bills_nothing(monkeypatch):
    monkeypatch.setenv("BASELITH_UNKNOWN_MODEL_COST_POLICY", "zero")
    assert llm_call_cost_usd("my-self-hosted-model", 1000, 1000) == 0.0


def test_unknown_model_policy_reject_raises(monkeypatch):
    monkeypatch.setenv("BASELITH_UNKNOWN_MODEL_COST_POLICY", "reject")
    with pytest.raises(UnknownModelCostRejected):
        llm_call_cost_usd("my-self-hosted-model", 1000, 1000)


@pytest.mark.asyncio
async def test_reject_policy_fires_on_the_pre_call_gate(manager, monkeypatch):
    """The setting says "instead of billing anything" — so it has to run
    before the provider is asked, not after it has answered and been paid."""
    monkeypatch.setenv("BASELITH_UNKNOWN_MODEL_COST_POLICY", "reject")
    set_tenant_context("acme")

    with pytest.raises(UnknownModelCostRejected):
        await enforce_tenant_cost_budget(model="my-self-hosted-model", manager=manager)


@pytest.mark.asyncio
async def test_the_gate_is_silent_for_a_priced_model_and_other_policies(
    manager, monkeypatch
):
    from core.models.pricing import DEFAULT_PRICING

    monkeypatch.setenv("BASELITH_UNKNOWN_MODEL_COST_POLICY", "reject")
    set_tenant_context("acme")
    await enforce_tenant_cost_budget(model=next(iter(DEFAULT_PRICING)), manager=manager)

    monkeypatch.setenv("BASELITH_UNKNOWN_MODEL_COST_POLICY", "charge")
    monkeypatch.setattr(ce_mod, "_unknown_model_cost_config", None)
    await enforce_tenant_cost_budget(model="my-self-hosted-model", manager=manager)


def test_unknown_model_warns_once_per_model_id_per_process():
    with patch.object(ce_mod.logger, "warning") as warn:
        llm_call_cost_usd("repeat-offender", 10, 10)
        llm_call_cost_usd("repeat-offender", 10, 10)
        llm_call_cost_usd("repeat-offender", 10, 10)
        assert warn.call_count == 1
        llm_call_cost_usd("a-different-unpriced-model", 10, 10)
        assert warn.call_count == 2


def test_llm_call_cost_threads_cache_and_batch_kwargs():
    from core.models.pricing import DEFAULT_PRICING

    model = next(iter(DEFAULT_PRICING))
    price = DEFAULT_PRICING[model]
    cost = llm_call_cost_usd(
        model, 0, 0, cache_read_tokens=1_000_000, cache_write_tokens=0, batch=True
    )
    assert cost == pytest.approx(price.effective_cache_read_usd_per_million * 0.5)


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
