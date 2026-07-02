"""Per-tenant aggregate quotas: a tenant's budget spans all its members and is
independent of per-identity quotas. Uses the in-memory store; no live DB."""

from datetime import datetime, timezone

import pytest

from core.config.quotas import QuotaConfig, set_tenant_quota
from core.quotas import (
    InMemoryQuotaStore,
    QuotaExceededError,
    QuotaManager,
    QuotaWindow,
)

NOW = datetime(2026, 6, 17, 12, 0, 0, tzinfo=timezone.utc)


def _mgr(**cfg):
    base = dict(QUOTAS_ENABLED=True, QUOTA_BACKEND="memory")
    base.update(cfg)
    return QuotaManager(config=QuotaConfig(**base), store=InMemoryQuotaStore())


@pytest.mark.asyncio
async def test_tenant_daily_limit_enforced():
    m = _mgr(QUOTA_TENANT_DAILY_REQUESTS=2)
    await m.check_and_consume_tenant("t-acme", now=NOW)
    await m.check_and_consume_tenant("t-acme", now=NOW)
    with pytest.raises(QuotaExceededError) as ei:
        await m.check_and_consume_tenant("t-acme", now=NOW)
    assert ei.value.window == QuotaWindow.DAILY
    assert ei.value.limit == 2


@pytest.mark.asyncio
async def test_tenant_quota_independent_from_identity():
    # Tenant capped at 1/day; per-identity unlimited. Identity consumption must
    # NOT draw down the tenant budget (separate key namespaces).
    m = _mgr(QUOTA_TENANT_DAILY_REQUESTS=1)
    for _ in range(5):
        await m.check_and_consume("user-1", now=NOW)  # unlimited identity
    await m.check_and_consume_tenant("t-1", now=NOW)  # 1st tenant request OK
    with pytest.raises(QuotaExceededError):
        await m.check_and_consume_tenant("t-1", now=NOW)  # tenant exhausted


@pytest.mark.asyncio
async def test_two_tenants_isolated():
    m = _mgr(QUOTA_TENANT_DAILY_REQUESTS=1)
    await m.check_and_consume_tenant("t-a", now=NOW)
    # t-a exhausted, but t-b has its own fresh budget
    status = await m.check_and_consume_tenant("t-b", now=NOW)
    assert status.windows["daily"].used == 1


@pytest.mark.asyncio
async def test_per_tenant_override_beats_default():
    m = _mgr(QUOTA_TENANT_DAILY_REQUESTS=1)
    set_tenant_quota("t-enterprise", daily=3)  # this tenant's plan is bigger
    try:
        for _ in range(3):
            await m.check_and_consume_tenant("t-enterprise", now=NOW)
        with pytest.raises(QuotaExceededError):
            await m.check_and_consume_tenant("t-enterprise", now=NOW)
    finally:
        set_tenant_quota("t-enterprise", daily=None, monthly=None)


@pytest.mark.asyncio
async def test_peek_tenant_does_not_consume():
    m = _mgr(QUOTA_TENANT_DAILY_REQUESTS=5)
    await m.check_and_consume_tenant("t-peek", now=NOW)
    status = await m.peek_tenant("t-peek", now=NOW)
    assert status.windows["daily"].used == 1
    assert status.windows["daily"].remaining == 4


@pytest.mark.asyncio
async def test_pair_enforces_both_subjects_in_batch():
    """check_and_consume_pair enforces identity AND tenant windows."""
    m = _mgr(QUOTA_DAILY_REQUESTS=2, QUOTA_TENANT_DAILY_REQUESTS=3)
    id_status, tn_status = await m.check_and_consume_pair("u-1", "t-1", now=NOW)
    assert id_status.windows["daily"].used == 1
    assert tn_status.windows["daily"].used == 1

    await m.check_and_consume_pair("u-1", "t-1", now=NOW)
    with pytest.raises(QuotaExceededError) as ei:
        await m.check_and_consume_pair("u-1", "t-1", now=NOW)
    assert ei.value.identity == "u-1"  # identity (limit 2) trips before tenant (3)


@pytest.mark.asyncio
async def test_pair_rejection_burns_no_budget():
    """A rejected pair consumes nothing on EITHER subject (no partial burn)."""
    m = _mgr(QUOTA_DAILY_REQUESTS=1, QUOTA_TENANT_DAILY_REQUESTS=10)
    await m.check_and_consume_pair("u-1", "t-1", now=NOW)
    with pytest.raises(QuotaExceededError):
        await m.check_and_consume_pair("u-1", "t-1", now=NOW)
    # Tenant budget untouched by the rejected request.
    tn_peek = await m.peek_tenant("t-1", now=NOW)
    assert tn_peek.windows["daily"].used == 1
