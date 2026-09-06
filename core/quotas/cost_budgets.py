"""USD cost budgets per tenant and per identity (post-paid metering).

Split out of :mod:`core.quotas.manager` for the module size cap. Cost is
known only AFTER an LLM call returns, so unlike request quotas this cannot be
check-then-consume: ``record_*_cost`` books spend post-call,
``check_*_cost_budget`` gates the NEXT call. Amounts are stored as integer
micro-dollars so the counter store's atomic increments stay exact.

:class:`CostBudgetMixin` is mixed into ``QuotaManager`` (which supplies
``_config``, ``_store`` and the ``_key`` namespacing); one generic engine
serves both subject kinds under namespaced key prefixes
(``tenant:<id>:cost`` / ``identity:<id>:cost``).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel

from core.config.quotas import get_key_cost_overrides, get_tenant_cost_overrides
from core.observability.logging import get_logger
from core.quotas.windows import QuotaWindow, seconds_until_window_end

logger = get_logger(__name__)

# USD amounts are stored as integer micro-dollars so the counter store's
# atomic integer increments stay exact (no float drift across INCRBY).
_MICRO_USD = 1_000_000


class CostBudgetExceededError(Exception):
    """A subject exceeded its cumulative USD cost budget for a window.

    Distinct from ``QuotaExceededError`` (request counts) and from the
    per-run ``BudgetExceededError`` (one request's cap): this is a subject's
    aggregate LLM spend over a calendar window.
    """

    def __init__(
        self, tenant_id: str, window: QuotaWindow, limit_usd: float, used_usd: float
    ) -> None:
        super().__init__(
            f"Cost budget exceeded for {window.value} window: "
            f"${used_usd:.4f}/${limit_usd:.2f}"
        )
        self.tenant_id = tenant_id
        self.window = window
        self.limit_usd = limit_usd
        self.used_usd = used_usd

    @property
    def subject(self) -> str:
        """The budget subject — a tenant id or an identity.

        ``tenant_id`` predates identity budgets and keeps its name for
        compatibility; ``subject`` is the accurate accessor for both kinds.
        """
        return self.tenant_id


class CostWindowStatus(BaseModel):
    limit_usd: float | None = None  # None = unlimited
    used_usd: float = 0.0
    remaining_usd: float | None = None  # None = unlimited


class TenantCostStatus(BaseModel):
    tenant_id: str
    windows: dict[str, CostWindowStatus] = {}


class _CostHost(Protocol):
    """What the mixin needs from its host (``QuotaManager``)."""

    _config: Any
    _store: Any

    @staticmethod
    def _key(prefix: str, window: QuotaWindow, now: datetime) -> str: ...


class CostBudgetMixin:
    """Tenant + identity USD cost budgets over the host's counter store."""

    def _cost_limit_for(
        self: _CostHost,
        window: QuotaWindow,
        overrides: tuple[float | None, float | None],
        daily_default: float | None,
        monthly_default: float | None,
    ) -> float | None:
        daily_override, monthly_override = overrides
        if window == QuotaWindow.DAILY:
            limit, default = daily_override, daily_default
        else:
            limit, default = monthly_override, monthly_default
        effective = limit if limit is not None else default
        return effective if effective else None

    def _tenant_cost_limit_for(
        self: Any, tenant_id: str, window: QuotaWindow
    ) -> float | None:
        limit: float | None = self._cost_limit_for(
            window,
            get_tenant_cost_overrides(tenant_id),
            self._config.tenant_daily_cost_limit_usd,
            self._config.tenant_monthly_cost_limit_usd,
        )
        return limit

    def _identity_cost_limit_for(
        self: Any, identity: str, window: QuotaWindow
    ) -> float | None:
        limit: float | None = self._cost_limit_for(
            window,
            get_key_cost_overrides(identity),
            self._config.identity_daily_cost_limit_usd,
            self._config.identity_monthly_cost_limit_usd,
        )
        return limit

    async def _record_cost(
        self: Any,
        prefix: str,
        limit_fn: Callable[[QuotaWindow], float | None],
        usd: float,
        when: datetime,
    ) -> None:
        """Book spend on both windows; no-op for unlimited subjects.

        Never raises for budget reasons — the money is already spent; the
        rejection happens on the next ``_check_cost``.
        """
        if not self._config.enabled or usd <= 0:
            return
        if all(limit_fn(w) is None for w in (QuotaWindow.DAILY, QuotaWindow.MONTHLY)):
            return
        micro = round(usd * _MICRO_USD)
        if micro <= 0:
            return
        for window in (QuotaWindow.DAILY, QuotaWindow.MONTHLY):
            ttl = seconds_until_window_end(window, when)
            await self._store.incr(self._key(prefix, window, when), micro, ttl)

    async def _check_cost(
        self: Any,
        subject: str,
        prefix: str,
        limit_fn: Callable[[QuotaWindow], float | None],
        when: datetime,
    ) -> None:
        """Raise when the subject's recorded spend has reached a window limit."""
        if not self._config.enabled:
            return
        for window in (QuotaWindow.DAILY, QuotaWindow.MONTHLY):
            limit = limit_fn(window)
            if limit is None:
                continue
            used = await self._store.get(self._key(prefix, window, when)) / _MICRO_USD
            if used >= limit:
                logger.warning(
                    "cost_budget_exceeded",
                    extra={
                        "subject": subject,
                        "window": window.value,
                        "limit_usd": limit,
                        "used_usd": used,
                    },
                )
                raise CostBudgetExceededError(subject, window, limit, used)

    async def _peek_cost(
        self: Any,
        subject: str,
        prefix: str,
        limit_fn: Callable[[QuotaWindow], float | None],
        when: datetime,
    ) -> TenantCostStatus:
        status = TenantCostStatus(tenant_id=subject)
        for window in (QuotaWindow.DAILY, QuotaWindow.MONTHLY):
            limit = limit_fn(window)
            used = await self._store.get(self._key(prefix, window, when)) / _MICRO_USD
            status.windows[window.value] = CostWindowStatus(
                limit_usd=limit,
                used_usd=used,
                remaining_usd=None if limit is None else max(0.0, limit - used),
            )
        return status

    async def record_tenant_cost(
        self: Any, tenant_id: str, usd: float, *, now: datetime | None = None
    ) -> None:
        """Book ``usd`` of LLM spend against the tenant's cost windows."""
        await self._record_cost(
            f"tenant:{tenant_id}:cost",
            lambda w: self._tenant_cost_limit_for(tenant_id, w),
            usd,
            now or datetime.now(UTC),
        )

    async def check_tenant_cost_budget(
        self: Any, tenant_id: str, *, now: datetime | None = None
    ) -> None:
        """Raise :class:`CostBudgetExceededError` at the tenant's cap."""
        await self._check_cost(
            tenant_id,
            f"tenant:{tenant_id}:cost",
            lambda w: self._tenant_cost_limit_for(tenant_id, w),
            now or datetime.now(UTC),
        )

    async def peek_tenant_cost(
        self: Any, tenant_id: str, *, now: datetime | None = None
    ) -> TenantCostStatus:
        """Report the tenant's USD spend per window without consuming."""
        status: TenantCostStatus = await self._peek_cost(
            tenant_id,
            f"tenant:{tenant_id}:cost",
            lambda w: self._tenant_cost_limit_for(tenant_id, w),
            now or datetime.now(UTC),
        )
        return status

    async def record_identity_cost(
        self: Any, identity: str, usd: float, *, now: datetime | None = None
    ) -> None:
        """Book ``usd`` of LLM spend against one identity's cost windows."""
        await self._record_cost(
            f"identity:{identity}:cost",
            lambda w: self._identity_cost_limit_for(identity, w),
            usd,
            now or datetime.now(UTC),
        )

    async def check_identity_cost_budget(
        self: Any, identity: str, *, now: datetime | None = None
    ) -> None:
        """Raise :class:`CostBudgetExceededError` at the identity's cap."""
        await self._check_cost(
            identity,
            f"identity:{identity}:cost",
            lambda w: self._identity_cost_limit_for(identity, w),
            now or datetime.now(UTC),
        )

    async def peek_identity_cost(
        self: Any, identity: str, *, now: datetime | None = None
    ) -> TenantCostStatus:
        """Report one identity's USD spend per window without consuming."""
        status: TenantCostStatus = await self._peek_cost(
            identity,
            f"identity:{identity}:cost",
            lambda w: self._identity_cost_limit_for(identity, w),
            now or datetime.now(UTC),
        )
        return status


__all__ = [
    "CostBudgetExceededError",
    "CostBudgetMixin",
    "CostWindowStatus",
    "TenantCostStatus",
]
