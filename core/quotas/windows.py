"""Calendar quota windows shared by request quotas and cost budgets.

Split out of :mod:`core.quotas.manager` for the module size cap: both the
request-quota engine (``manager``) and the USD cost-budget engine
(``cost_budgets``) key their counters by these calendar periods.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum


class QuotaWindow(str, Enum):
    DAILY = "daily"
    MONTHLY = "monthly"


def period_id(window: QuotaWindow, now: datetime) -> str:
    """Calendar period id embedded in a counter key (resets on rollover)."""
    return (
        now.strftime("%Y%m%d") if window == QuotaWindow.DAILY else now.strftime("%Y%m")
    )


def seconds_until_window_end(window: QuotaWindow, now: datetime) -> int:
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


__all__ = ["QuotaWindow", "period_id", "seconds_until_window_end"]
