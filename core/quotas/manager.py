"""
Per-key usage quota manager.

Enforces persistent request budgets per identity over calendar windows (daily
and monthly). Limits resolve from a programmatic per-identity override first,
then the config default; a ``None``/``0`` limit means unlimited for that window.

Enforcement is **check-then-consume**: both windows are read first and the
request is rejected without consuming if either would exceed, so a rejected
request never burns budget and windows stay consistent.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel

from core.config.quotas import (
    QuotaConfig,
    get_key_overrides,
    get_quota_config,
    get_tenant_overrides,
)
from core.observability.logging import get_logger
from core.quotas.store import QuotaStore, build_default_store

logger = get_logger(__name__)


class QuotaWindow(str, Enum):
    DAILY = "daily"
    MONTHLY = "monthly"


class QuotaExceededError(Exception):
    """An identity exceeded its quota for a window."""

    def __init__(
        self, identity: str, window: QuotaWindow, limit: int, used: int
    ) -> None:
        super().__init__(f"Quota exceeded for {window.value} window: {used}/{limit}")
        self.identity = identity
        self.window = window
        self.limit = limit
        self.used = used


class WindowStatus(BaseModel):
    limit: int | None = None  # None = unlimited
    used: int = 0
    remaining: int | None = None  # None = unlimited


class QuotaStatus(BaseModel):
    identity: str
    windows: dict[str, WindowStatus] = {}


def _period_id(window: QuotaWindow, now: datetime) -> str:
    return (
        now.strftime("%Y%m%d") if window == QuotaWindow.DAILY else now.strftime("%Y%m")
    )


def _seconds_until_window_end(window: QuotaWindow, now: datetime) -> int:
    """TTL for a window counter — seconds remaining in the calendar period."""
    if window == QuotaWindow.DAILY:
        end = now.replace(hour=23, minute=59, second=59, microsecond=0)
        return max(1, int((end - now).total_seconds()) + 1)
    # Monthly: start of next month minus now.
    year = now.year + (1 if now.month == 12 else 0)
    month = 1 if now.month == 12 else now.month + 1
    start_next = now.replace(
        year=year, month=month, day=1, hour=0, minute=0, second=0, microsecond=0
    )
    return max(1, int((start_next - now).total_seconds()))


class QuotaManager:
    """Resolve limits and enforce per-identity usage quotas."""

    def __init__(
        self,
        config: QuotaConfig | None = None,
        store: QuotaStore | None = None,
    ) -> None:
        self._config = config or get_quota_config()
        self._store = store or build_default_store(self._config.backend)

    def _limit_for(self, identity: str, window: QuotaWindow) -> int | None:
        daily_override, monthly_override = get_key_overrides(identity)
        if window == QuotaWindow.DAILY:
            limit = daily_override
            default = self._config.daily_request_limit
        else:
            limit = monthly_override
            default = self._config.monthly_request_limit
        effective = limit if limit is not None else default
        # Treat 0 as "unlimited" so an unset env (0) does not lock everyone out.
        return effective if effective else None

    def _tenant_limit_for(self, tenant_id: str, window: QuotaWindow) -> int | None:
        daily_override, monthly_override = get_tenant_overrides(tenant_id)
        if window == QuotaWindow.DAILY:
            limit = daily_override
            default = self._config.tenant_daily_request_limit
        else:
            limit = monthly_override
            default = self._config.tenant_monthly_request_limit
        effective = limit if limit is not None else default
        return effective if effective else None

    @staticmethod
    def _key(prefix: str, window: QuotaWindow, now: datetime) -> str:
        return f"{prefix}:{window.value}:{_period_id(window, now)}"

    async def _enforce(
        self,
        subject: str,
        key_prefix: str,
        limit_fn: Callable[[QuotaWindow], int | None],
        cost: int,
        when: datetime,
    ) -> QuotaStatus:
        """Check-then-consume both windows for one subject (identity or tenant).

        Generic over the key namespace and limit resolver so per-identity and
        per-tenant quotas share one enforcement path. Per-identity keys keep
        their historical ``{identity}:{window}:{period}`` form (prefix == id).
        """
        status = QuotaStatus(identity=subject)
        if not self._config.enabled:
            return status

        finite: list[tuple[QuotaWindow, int]] = []
        for window in (QuotaWindow.DAILY, QuotaWindow.MONTHLY):
            limit = limit_fn(window)
            if limit is None:
                status.windows[window.value] = WindowStatus()
                continue
            used = await self._store.get(self._key(key_prefix, window, when))
            if used + cost > limit:
                logger.warning(
                    "quota_exceeded",
                    extra={"subject": subject, "window": window.value, "limit": limit},
                )
                raise QuotaExceededError(subject, window, limit, used)
            finite.append((window, limit))

        for window, limit in finite:
            ttl = _seconds_until_window_end(window, when)
            new_used = await self._store.incr(
                self._key(key_prefix, window, when), cost, ttl
            )
            status.windows[window.value] = WindowStatus(
                limit=limit, used=new_used, remaining=max(0, limit - new_used)
            )
        return status

    async def _peek(
        self,
        subject: str,
        key_prefix: str,
        limit_fn: Callable[[QuotaWindow], int | None],
        when: datetime,
    ) -> QuotaStatus:
        status = QuotaStatus(identity=subject)
        for window in (QuotaWindow.DAILY, QuotaWindow.MONTHLY):
            limit = limit_fn(window)
            used = await self._store.get(self._key(key_prefix, window, when))
            status.windows[window.value] = WindowStatus(
                limit=limit,
                used=used,
                remaining=None if limit is None else max(0, limit - used),
            )
        return status

    async def check_and_consume(
        self, identity: str, *, cost: int = 1, now: datetime | None = None
    ) -> QuotaStatus:
        """Consume ``cost`` from the identity's budgets, or raise if over.

        Returns the post-consumption :class:`QuotaStatus`. A no-op (everything
        unlimited) when quotas are disabled.
        """
        when = now or datetime.now(UTC)
        return await self._enforce(
            identity, identity, lambda w: self._limit_for(identity, w), cost, when
        )

    async def peek(self, identity: str, *, now: datetime | None = None) -> QuotaStatus:
        """Report current usage without consuming."""
        when = now or datetime.now(UTC)
        return await self._peek(
            identity, identity, lambda w: self._limit_for(identity, w), when
        )

    async def check_and_consume_tenant(
        self, tenant_id: str, *, cost: int = 1, now: datetime | None = None
    ) -> QuotaStatus:
        """Consume from a tenant's aggregate budget (all its members combined).

        Independent of per-identity quotas — callers typically enforce both.
        """
        when = now or datetime.now(UTC)
        return await self._enforce(
            tenant_id,
            f"tenant:{tenant_id}",
            lambda w: self._tenant_limit_for(tenant_id, w),
            cost,
            when,
        )

    async def check_and_consume_pair(
        self,
        identity: str,
        tenant_id: str,
        *,
        cost: int = 1,
        now: datetime | None = None,
    ) -> tuple[QuotaStatus, QuotaStatus]:
        """Enforce identity AND tenant budgets in two batched round trips.

        All four window counters (identity/tenant x daily/monthly) are read
        with one ``get_many`` and, only if every window has room, consumed
        with one ``incr_many``. Compared to calling ``check_and_consume`` +
        ``check_and_consume_tenant`` sequentially this saves up to 6 store
        round trips per request AND removes the partial-consumption case: a
        rejected request no longer burns budget on the subject checked first.

        Falls back to the sequential path if the store lacks batch methods.

        Returns:
            Tuple of (identity status, tenant status), post-consumption.
        """
        when = now or datetime.now(UTC)
        identity_status = QuotaStatus(identity=identity)
        tenant_status = QuotaStatus(identity=tenant_id)
        if not self._config.enabled:
            return identity_status, tenant_status

        if not (hasattr(self._store, "get_many") and hasattr(self._store, "incr_many")):
            tenant_status = await self.check_and_consume_tenant(
                tenant_id, cost=cost, now=when
            )
            identity_status = await self.check_and_consume(
                identity, cost=cost, now=when
            )
            return identity_status, tenant_status

        # Plan: tenant first (mirrors the historical middleware check order,
        # so tie-breaking on which QuotaExceededError surfaces is unchanged).
        plan: list[tuple[QuotaStatus, str, QuotaWindow, int, str]] = []
        subjects = (
            (tenant_status, tenant_id, f"tenant:{tenant_id}", self._tenant_limit_for),
            (identity_status, identity, identity, self._limit_for),
        )
        for status, subject, key_prefix, limit_for in subjects:
            for window in (QuotaWindow.DAILY, QuotaWindow.MONTHLY):
                limit = limit_for(subject, window)
                if limit is None:
                    status.windows[window.value] = WindowStatus()
                    continue
                plan.append(
                    (
                        status,
                        subject,
                        window,
                        limit,
                        self._key(key_prefix, window, when),
                    )
                )

        if not plan:
            return identity_status, tenant_status

        used_values = await self._store.get_many([entry[4] for entry in plan])
        for (status, subject, window, limit, _), used in zip(plan, used_values):
            if used + cost > limit:
                logger.warning(
                    "quota_exceeded",
                    extra={"subject": subject, "window": window.value, "limit": limit},
                )
                raise QuotaExceededError(subject, window, limit, used)

        new_values = await self._store.incr_many(
            [
                (key, cost, _seconds_until_window_end(window, when))
                for (_, _, window, _, key) in plan
            ]
        )
        for (status, _, window, limit, _), new_used in zip(plan, new_values):
            status.windows[window.value] = WindowStatus(
                limit=limit, used=new_used, remaining=max(0, limit - new_used)
            )
        return identity_status, tenant_status

    async def peek_tenant(
        self, tenant_id: str, *, now: datetime | None = None
    ) -> QuotaStatus:
        """Report a tenant's aggregate usage without consuming."""
        when = now or datetime.now(UTC)
        return await self._peek(
            tenant_id,
            f"tenant:{tenant_id}",
            lambda w: self._tenant_limit_for(tenant_id, w),
            when,
        )


_quota_manager: QuotaManager | None = None


def get_quota_manager() -> QuotaManager:
    """Get or create the global quota manager."""
    global _quota_manager
    if _quota_manager is None:
        _quota_manager = QuotaManager()
    return _quota_manager
