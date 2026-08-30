"""Cumulative per-tenant USD budgets over calendar windows.

Request-count quotas existed; dollar cost was only capped per-run
(``LoopBudget.budget_usd``). These tests pin the tenant-level ledger:
cost recorded after each LLM call, enforcement before the next one.
"""

from __future__ import annotations

import pytest

from core.config.quotas import QuotaConfig, set_tenant_cost_budget
from core.quotas.manager import CostBudgetExceededError, QuotaManager
from core.quotas.store import InMemoryQuotaStore


def _manager(**config_kwargs) -> QuotaManager:
    config = QuotaConfig(QUOTAS_ENABLED=True, QUOTA_BACKEND="memory", **config_kwargs)
    return QuotaManager(config=config, store=InMemoryQuotaStore())


@pytest.mark.asyncio
async def test_recorded_cost_accumulates_and_blocks_at_daily_limit():
    manager = _manager(QUOTA_TENANT_DAILY_COST_USD=1.00)

    await manager.record_tenant_cost("acme", 0.40)
    await manager.check_tenant_cost_budget("acme")  # 0.40 < 1.00: fine

    await manager.record_tenant_cost("acme", 0.70)  # total 1.10
    with pytest.raises(CostBudgetExceededError) as exc_info:
        await manager.check_tenant_cost_budget("acme")
    assert exc_info.value.window.value == "daily"
    assert exc_info.value.limit_usd == pytest.approx(1.00)
    assert exc_info.value.used_usd == pytest.approx(1.10)


@pytest.mark.asyncio
async def test_monthly_limit_enforced_independently():
    manager = _manager(QUOTA_TENANT_MONTHLY_COST_USD=0.50)

    await manager.record_tenant_cost("acme", 0.60)
    with pytest.raises(CostBudgetExceededError) as exc_info:
        await manager.check_tenant_cost_budget("acme")
    assert exc_info.value.window.value == "monthly"


@pytest.mark.asyncio
async def test_no_limits_configured_is_a_noop():
    manager = _manager()
    # Neither recording nor checking should raise or write counters.
    await manager.record_tenant_cost("acme", 5.0)
    await manager.check_tenant_cost_budget("acme")
    status = await manager.peek_tenant_cost("acme")
    assert status.windows["daily"].used_usd == 0.0


@pytest.mark.asyncio
async def test_per_tenant_override_beats_config_default():
    manager = _manager(QUOTA_TENANT_DAILY_COST_USD=100.0)
    set_tenant_cost_budget("small-tenant", daily=0.10)
    try:
        await manager.record_tenant_cost("small-tenant", 0.20)
        with pytest.raises(CostBudgetExceededError):
            await manager.check_tenant_cost_budget("small-tenant")
        # Another tenant still enjoys the generous default.
        await manager.record_tenant_cost("big-tenant", 0.20)
        await manager.check_tenant_cost_budget("big-tenant")
    finally:
        set_tenant_cost_budget("small-tenant", daily=None, monthly=None)


@pytest.mark.asyncio
async def test_tenants_are_isolated():
    manager = _manager(QUOTA_TENANT_DAILY_COST_USD=1.00)
    await manager.record_tenant_cost("acme", 2.00)
    await manager.check_tenant_cost_budget("globex")  # unaffected


@pytest.mark.asyncio
async def test_disabled_quotas_skip_enforcement():
    config = QuotaConfig(
        QUOTAS_ENABLED=False, QUOTA_BACKEND="memory", QUOTA_TENANT_DAILY_COST_USD=0.01
    )
    manager = QuotaManager(config=config, store=InMemoryQuotaStore())
    await manager.record_tenant_cost("acme", 9.99)
    await manager.check_tenant_cost_budget("acme")


@pytest.mark.asyncio
async def test_identity_cost_budget_enforced_independently_of_tenant():
    manager = _manager(QUOTA_IDENTITY_DAILY_COST_USD=0.50)

    await manager.record_identity_cost("user-1", 0.60)
    with pytest.raises(CostBudgetExceededError) as exc_info:
        await manager.check_identity_cost_budget("user-1")
    assert exc_info.value.window.value == "daily"
    # Another identity is unaffected.
    await manager.check_identity_cost_budget("user-2")


@pytest.mark.asyncio
async def test_identity_cost_override_beats_default():
    from core.config.quotas import set_key_cost_budget

    manager = _manager(QUOTA_IDENTITY_DAILY_COST_USD=100.0)
    set_key_cost_budget("small-key", daily=0.10)
    try:
        await manager.record_identity_cost("small-key", 0.20)
        with pytest.raises(CostBudgetExceededError):
            await manager.check_identity_cost_budget("small-key")
    finally:
        set_key_cost_budget("small-key", daily=None, monthly=None)


@pytest.mark.asyncio
async def test_identity_without_limits_is_noop():
    manager = _manager()
    await manager.record_identity_cost("user-1", 9.0)
    await manager.check_identity_cost_budget("user-1")
    status = await manager.peek_identity_cost("user-1")
    assert status.windows["daily"].used_usd == 0.0


@pytest.mark.asyncio
async def test_peek_reports_usd_usage():
    manager = _manager(QUOTA_TENANT_DAILY_COST_USD=2.00)
    await manager.record_tenant_cost("acme", 0.25)
    status = await manager.peek_tenant_cost("acme")
    daily = status.windows["daily"]
    assert daily.used_usd == pytest.approx(0.25)
    assert daily.limit_usd == pytest.approx(2.00)
    assert daily.remaining_usd == pytest.approx(1.75)
