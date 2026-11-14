"""Maintenance window arithmetic on aware instants.

Rules (every function takes an aware ``now`` and raises ``ValueError`` on a
naive one):

- A window's edges are wall-clock times in the window's zone. Each day in
  ``days`` has one occurrence: from ``start`` on that day to ``end`` on the
  same day, or on the next day when ``end < start`` (the window spans midnight
  and belongs to the day it starts on). An occurrence is the half-open UTC
  interval ``[start, end)``.
- A nonexistent wall-clock time (the spring-forward gap) resolves to the
  first instant after the gap: in Europe/Rome on 2026-03-29, 02:30 is 03:00
  CEST (01:00 UTC).
- An ambiguous wall-clock time (the fall-back repeated hour) resolves to its
  **first** occurrence (``fold=0``) for ``start`` and its **last**
  (``fold=1``) for ``end``, so the window is as long as it can be.
- An occurrence whose resolved end is not after its start (a window entirely
  inside a gap) does not open that day.
- Durations are real elapsed time between UTC instants, so a window across a
  DST change is an hour shorter or longer than its wall-clock span.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from .policy import MaintenanceWindow

_DAY = timedelta(days=1)


def _aware(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be an aware datetime")
    return now.astimezone(UTC)


def _exists(instant: datetime, wall: datetime, zone: ZoneInfo) -> bool:
    return instant.astimezone(zone).replace(tzinfo=None, fold=0) == wall


def _gap_end(before: datetime, after: datetime, zone: ZoneInfo) -> datetime:
    """The first whole second in ``(before, after]`` on the post-gap offset."""
    target = after.astimezone(zone).utcoffset()
    low, high = 0, int((after - before).total_seconds())
    while high - low > 1:  # offset(before + low) != target == offset(before + high)
        mid = (low + high) // 2
        if (before + timedelta(seconds=mid)).astimezone(zone).utcoffset() == target:
            high = mid
        else:
            low = mid
    return before + timedelta(seconds=high)


def _to_utc(wall: datetime, zone: ZoneInfo, *, last: bool) -> datetime:
    candidates = sorted(
        {wall.replace(tzinfo=zone, fold=f).astimezone(UTC) for f in (0, 1)}
    )
    real = [u for u in candidates if _exists(u, wall, zone)]
    if not real:  # inside a spring-forward gap
        return _gap_end(candidates[0], candidates[-1], zone)
    return real[-1] if last else real[0]  # ambiguous: first for start, last for end


def _occurrence(
    window: MaintenanceWindow, day: date, zone: ZoneInfo
) -> tuple[datetime, datetime] | None:
    if day.weekday() not in window.days:
        return None
    end_day = day + _DAY if window.end < window.start else day
    start = _to_utc(datetime.combine(day, window.start), zone, last=False)
    end = _to_utc(datetime.combine(end_day, window.end), zone, last=True)
    return (start, end) if end > start else None


def _containing(
    window: MaintenanceWindow, now: datetime
) -> tuple[datetime, datetime] | None:
    instant = _aware(now)
    zone = window.zone
    today = instant.astimezone(zone).date()
    for day in (today - _DAY, today):
        found = _occurrence(window, day, zone)
        if found is not None and found[0] <= instant < found[1]:
            return found
    return None


def in_window(window: MaintenanceWindow, now: datetime) -> bool:
    """Whether ``now`` falls inside an occurrence of ``window``."""
    return _containing(window, now) is not None


def remaining(window: MaintenanceWindow, now: datetime) -> timedelta:
    """Real time left until the open occurrence ends; zero outside the window."""
    found = _containing(window, now)
    return found[1] - _aware(now) if found is not None else timedelta(0)


def next_start(window: MaintenanceWindow, now: datetime) -> datetime:
    """The UTC start of the first occurrence starting at or after ``now``.

    While a window is open this is the following occurrence, not the open one.
    """
    instant = _aware(now)
    zone = window.zone
    today = instant.astimezone(zone).date()
    for offset in range(0, 9):
        found = _occurrence(window, today + offset * _DAY, zone)
        if found is not None and found[0] >= instant:
            return found[0]
    raise ValueError("the window has no occurrence in the next week")


__all__ = ["in_window", "next_start", "remaining"]
