"""Maintenance window arithmetic: midnight, weekdays and both DST edges.

Every case uses a fixed aware ``now``; nothing patches the clock. Europe/Rome
switches to summer time on 2026-03-29 at 02:00 (to 03:00, 01:00 UTC) and back
on 2026-10-25 at 03:00 (to 02:00, 01:00 UTC); both are Sundays (day 6).
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from baselith.core.plugin_updates.policy import MaintenanceWindow
from baselith.core.plugin_updates.window import in_window, next_start, remaining

ROME = ZoneInfo("Europe/Rome")
EVERY_DAY = frozenset(range(7))


def _w(start: time, end: time, days=EVERY_DAY, tz: str = "Europe/Rome"):
    return MaintenanceWindow(days=frozenset(days), start=start, end=end, tz=tz)


def _rome(*args: int, fold: int = 0) -> datetime:
    return datetime(*args, tzinfo=ROME, fold=fold)  # type: ignore[misc]


def test_in_and_out_on_a_plain_day() -> None:
    w = _w(time(2, 0), time(4, 0))
    assert in_window(w, _rome(2026, 6, 10, 2, 0))  # start is inclusive
    assert in_window(w, _rome(2026, 6, 10, 3, 59))
    assert not in_window(w, _rome(2026, 6, 10, 4, 0))  # end is exclusive
    assert not in_window(w, _rome(2026, 6, 10, 1, 59))
    assert remaining(w, _rome(2026, 6, 10, 3, 0)) == timedelta(hours=1)
    assert remaining(w, _rome(2026, 6, 10, 5, 0)) == timedelta(0)


def test_now_in_another_zone_is_compared_as_an_instant() -> None:
    w = _w(time(2, 0), time(4, 0))
    assert in_window(w, datetime(2026, 6, 10, 0, 30, tzinfo=UTC))  # 02:30 CEST


def test_window_spanning_midnight_belongs_to_its_start_day() -> None:
    w = _w(time(23, 0), time(1, 0), days={4})  # Friday night
    friday_late = _rome(2026, 6, 12, 23, 30)
    saturday_early = _rome(2026, 6, 13, 0, 30)
    assert friday_late.weekday() == 4
    assert in_window(w, friday_late)
    assert in_window(w, saturday_early)  # still Friday's window
    assert remaining(w, saturday_early) == timedelta(minutes=30)
    assert not in_window(w, _rome(2026, 6, 13, 23, 30))  # Saturday night: not allowed
    assert not in_window(w, _rome(2026, 6, 12, 0, 30))  # Thursday's would-be window


def test_day_filter_uses_the_start_day() -> None:
    w = _w(time(2, 0), time(4, 0), days={0})  # Monday only
    assert in_window(w, _rome(2026, 6, 8, 3, 0))  # a Monday
    assert not in_window(w, _rome(2026, 6, 9, 3, 0))  # a Tuesday


def test_spring_forward_gap_start_resolves_after_gap() -> None:
    w = _w(time(2, 30), time(4, 0))  # 02:30 does not exist on 2026-03-29
    gap_end = datetime(2026, 3, 29, 1, 0, tzinfo=UTC)  # 03:00 CEST
    assert not in_window(w, gap_end - timedelta(seconds=1))
    assert in_window(w, gap_end)
    assert remaining(w, gap_end) == timedelta(hours=1)  # until 04:00 CEST
    assert next_start(w, datetime(2026, 3, 28, 23, 0, tzinfo=UTC)) == gap_end


def test_window_entirely_inside_the_gap_does_not_open() -> None:
    w = _w(time(2, 0), time(2, 30), days={6})
    sunday = datetime(2026, 3, 29, 0, 0, tzinfo=UTC)
    for minutes in range(0, 180, 5):
        assert not in_window(w, sunday + timedelta(minutes=minutes))
    assert next_start(w, sunday) == _rome(2026, 4, 5, 2, 0).astimezone(UTC)


def test_fall_back_end_uses_last_occurrence() -> None:
    w = _w(time(1, 0), time(2, 30))  # 02:30 happens twice on 2026-10-25
    first_0230 = _rome(2026, 10, 25, 2, 30, fold=0)  # 00:30 UTC, still CEST
    second_0230 = _rome(2026, 10, 25, 2, 30, fold=1)  # 01:30 UTC, CET
    assert in_window(w, first_0230)
    assert in_window(w, second_0230 - timedelta(seconds=1))
    assert not in_window(w, second_0230)


def test_fall_back_start_uses_first_occurrence() -> None:
    w = _w(time(2, 30), time(4, 0))
    first_0230 = _rome(2026, 10, 25, 2, 30, fold=0)
    assert in_window(w, first_0230)
    assert not in_window(w, first_0230 - timedelta(seconds=1))


def test_remaining_counts_real_elapsed_time_across_dst() -> None:
    fall = _w(time(1, 0), time(2, 30))
    start = _rome(2026, 10, 25, 1, 0)  # 23:00 UTC the day before
    assert remaining(fall, start) == timedelta(hours=1, minutes=30) + timedelta(hours=1)
    spring = _w(time(1, 0), time(4, 0))
    assert remaining(spring, _rome(2026, 3, 29, 1, 0)) == timedelta(hours=2)


def test_next_start_skips_to_the_next_allowed_day() -> None:
    w = _w(time(2, 0), time(4, 0), days={2})  # Wednesday
    monday = _rome(2026, 6, 8, 12, 0)
    assert next_start(w, monday) == _rome(2026, 6, 10, 2, 0).astimezone(UTC)
    inside = _rome(2026, 6, 10, 3, 0)  # while open: the following one
    assert next_start(w, inside) == _rome(2026, 6, 17, 2, 0).astimezone(UTC)
    exactly = _rome(2026, 6, 10, 2, 0)
    assert next_start(w, exactly) == exactly.astimezone(UTC)
    assert next_start(w, monday).tzinfo is UTC


def test_naive_now_rejected() -> None:
    w = _w(time(2, 0), time(4, 0))
    naive = datetime(2026, 6, 10, 3, 0, tzinfo=UTC).replace(tzinfo=None)
    for fn in (in_window, remaining, next_start):
        with pytest.raises(ValueError, match="aware"):
            fn(w, naive)


def test_utc_window_has_no_dst_surprises() -> None:
    w = _w(time(1, 0), time(3, 0), tz="UTC")
    for day in (datetime(2026, 3, 29, tzinfo=UTC), datetime(2026, 10, 25, tzinfo=UTC)):
        assert in_window(w, day + timedelta(hours=1))
        assert remaining(w, day + timedelta(hours=1)) == timedelta(hours=2)
        assert not in_window(w, day + timedelta(hours=3))


def test_tzdata_is_installed() -> None:
    import tzdata  # a declared dependency: zoneinfo's fallback database

    assert tzdata.IANA_VERSION
    assert ZoneInfo("Europe/Rome").key == "Europe/Rome"
