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

from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Optional

from pydantic import BaseModel

from core.config.quotas import QuotaConfig, get_key_overrides, get_quota_config
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
    limit: Optional[int] = None  # None = unlimited
    used: int = 0
    remaining: Optional[int] = None  # None = unlimited


class QuotaStatus(BaseModel):
    identity: str
    windows: Dict[str, WindowStatus] = {}


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
        config: Optional[QuotaConfig] = None,
        store: Optional[QuotaStore] = None,
    ) -> None:
        self._config = config or get_quota_config()
        self._store = store or build_default_store(self._config.backend)

    def _limit_for(self, identity: str, window: QuotaWindow) -> Optional[int]:
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

    def _window_key(self, identity: str, window: QuotaWindow, now: datetime) -> str:
        return f"{identity}:{window.value}:{_period_id(window, now)}"

    async def check_and_consume(
        self, identity: str, *, cost: int = 1, now: Optional[datetime] = None
    ) -> QuotaStatus:
        """Consume ``cost`` from the identity's budgets, or raise if over.

        Returns the post-consumption :class:`QuotaStatus`. A no-op (everything
        unlimited) when quotas are disabled.
        """
        when = now or datetime.now(timezone.utc)
        status = QuotaStatus(identity=identity)
        if not self._config.enabled:
            return status

        finite: list[tuple[QuotaWindow, int]] = []
        for window in (QuotaWindow.DAILY, QuotaWindow.MONTHLY):
            limit = self._limit_for(identity, window)
            if limit is None:
                status.windows[window.value] = WindowStatus()
                continue
            used = await self._store.get(self._window_key(identity, window, when))
            if used + cost > limit:
                logger.warning(
                    "quota_exceeded",
                    extra={
                        "identity": identity,
                        "window": window.value,
                        "limit": limit,
                    },
                )
                raise QuotaExceededError(identity, window, limit, used)
            finite.append((window, limit))

        # All finite windows pass — now consume.
        for window, limit in finite:
            ttl = _seconds_until_window_end(window, when)
            new_used = await self._store.incr(
                self._window_key(identity, window, when), cost, ttl
            )
            status.windows[window.value] = WindowStatus(
                limit=limit, used=new_used, remaining=max(0, limit - new_used)
            )
        return status

    async def peek(
        self, identity: str, *, now: Optional[datetime] = None
    ) -> QuotaStatus:
        """Report current usage without consuming."""
        when = now or datetime.now(timezone.utc)
        status = QuotaStatus(identity=identity)
        for window in (QuotaWindow.DAILY, QuotaWindow.MONTHLY):
            limit = self._limit_for(identity, window)
            used = await self._store.get(self._window_key(identity, window, when))
            status.windows[window.value] = WindowStatus(
                limit=limit,
                used=used,
                remaining=None if limit is None else max(0, limit - used),
            )
        return status


_quota_manager: Optional[QuotaManager] = None


def get_quota_manager() -> QuotaManager:
    """Get or create the global quota manager."""
    global _quota_manager
    if _quota_manager is None:
        _quota_manager = QuotaManager()
    return _quota_manager
