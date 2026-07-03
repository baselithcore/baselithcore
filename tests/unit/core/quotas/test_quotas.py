"""Tests for per-key usage quotas."""

from datetime import UTC, datetime

import pytest

from core.config.quotas import QuotaConfig, set_key_quota
from core.quotas import (
    InMemoryQuotaStore,
    QuotaExceededError,
    QuotaManager,
    QuotaWindow,
)

NOW = datetime(2026, 6, 17, 12, 0, 0, tzinfo=UTC)


def _mgr(**cfg):
    base = dict(QUOTAS_ENABLED=True, QUOTA_BACKEND="memory")
    base.update(cfg)
    return QuotaManager(config=QuotaConfig(**base), store=InMemoryQuotaStore())


class TestEnforcement:
    @pytest.mark.asyncio
    async def test_consumes_up_to_limit(self):
        m = _mgr(QUOTA_DAILY_REQUESTS=3)
        for _ in range(3):
            status = await m.check_and_consume("k", now=NOW)
        assert status.windows["daily"].used == 3
        assert status.windows["daily"].remaining == 0

    @pytest.mark.asyncio
    async def test_rejects_over_limit(self):
        m = _mgr(QUOTA_DAILY_REQUESTS=2)
        await m.check_and_consume("k", now=NOW)
        await m.check_and_consume("k", now=NOW)
        with pytest.raises(QuotaExceededError) as ei:
            await m.check_and_consume("k", now=NOW)
        assert ei.value.window == QuotaWindow.DAILY
        assert ei.value.limit == 2

    @pytest.mark.asyncio
    async def test_rejected_request_does_not_consume(self):
        m = _mgr(QUOTA_DAILY_REQUESTS=1)
        await m.check_and_consume("k", now=NOW)
        with pytest.raises(QuotaExceededError):
            await m.check_and_consume("k", now=NOW)
        # Used stays at the limit — the rejected call burned nothing extra.
        status = await m.peek("k", now=NOW)
        assert status.windows["daily"].used == 1

    @pytest.mark.asyncio
    async def test_per_key_isolation(self):
        m = _mgr(QUOTA_DAILY_REQUESTS=1)
        await m.check_and_consume("a", now=NOW)
        # b has its own budget.
        await m.check_and_consume("b", now=NOW)
        with pytest.raises(QuotaExceededError):
            await m.check_and_consume("a", now=NOW)

    @pytest.mark.asyncio
    async def test_daily_window_resets_next_day(self):
        m = _mgr(QUOTA_DAILY_REQUESTS=1)
        await m.check_and_consume("k", now=NOW)
        next_day = NOW.replace(day=18)
        status = await m.check_and_consume("k", now=next_day)
        assert status.windows["daily"].used == 1

    @pytest.mark.asyncio
    async def test_monthly_window(self):
        m = _mgr(QUOTA_MONTHLY_REQUESTS=2)
        await m.check_and_consume("k", now=NOW)
        await m.check_and_consume("k", now=NOW.replace(day=20))
        with pytest.raises(QuotaExceededError) as ei:
            await m.check_and_consume("k", now=NOW.replace(day=25))
        assert ei.value.window == QuotaWindow.MONTHLY

    @pytest.mark.asyncio
    async def test_cost_greater_than_one(self):
        m = _mgr(QUOTA_DAILY_REQUESTS=10)
        await m.check_and_consume("k", cost=7, now=NOW)
        with pytest.raises(QuotaExceededError):
            await m.check_and_consume("k", cost=4, now=NOW)


class TestUnlimited:
    @pytest.mark.asyncio
    async def test_disabled_is_noop(self):
        m = QuotaManager(
            config=QuotaConfig(QUOTAS_ENABLED=False), store=InMemoryQuotaStore()
        )
        for _ in range(50):
            status = await m.check_and_consume("k", now=NOW)
        assert status.windows == {}

    @pytest.mark.asyncio
    async def test_no_limit_means_unlimited(self):
        m = _mgr()  # enabled but no limits set
        for _ in range(50):
            status = await m.check_and_consume("k", now=NOW)
        assert status.windows["daily"].limit is None
        assert status.windows["daily"].remaining is None

    @pytest.mark.asyncio
    async def test_zero_limit_treated_as_unlimited(self):
        # An env default of 0 must not lock everyone out.
        m = _mgr(QUOTA_DAILY_REQUESTS=0)
        for _ in range(20):
            await m.check_and_consume("k", now=NOW)


class TestOverrides:
    @pytest.mark.asyncio
    async def test_per_key_override_wins(self):
        set_key_quota("vip", daily=100)
        m = _mgr(QUOTA_DAILY_REQUESTS=1)
        # Default would cap at 1, but the override raises it.
        for _ in range(5):
            await m.check_and_consume("vip", now=NOW)
        status = await m.peek("vip", now=NOW)
        assert status.windows["daily"].limit == 100
