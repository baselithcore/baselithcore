"""Unit tests for the pure-stdlib 5-field cron expression parser."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from core.task_queue.cron import CronExpression


def _dt(*args: int) -> datetime:
    """Build a tz-aware UTC datetime from positional components."""
    return datetime(*args, tzinfo=UTC)


class TestParse:
    def test_parse_returns_cron_expression(self) -> None:
        expr = CronExpression.parse("* * * * *")
        assert isinstance(expr, CronExpression)

    def test_parse_preserves_expression_string(self) -> None:
        expr = CronExpression.parse("*/15 9-17 * * 1-5")
        assert str(expr) == "*/15 9-17 * * 1-5"

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "* * * *",  # too few fields
            "* * * * * *",  # too many fields
            "a * * * *",  # non-integer
            "60 * * * *",  # minute out of range
            "* 24 * * *",  # hour out of range
            "* * 0 * *",  # day-of-month out of range (low)
            "* * 32 * *",  # day-of-month out of range (high)
            "* * * 0 *",  # month out of range (low)
            "* * * 13 *",  # month out of range (high)
            "* * * * 8",  # day-of-week out of range
            "5-1 * * * *",  # inverted range
            "*/0 * * * *",  # zero step
            "*/x * * * *",  # non-integer step
            "1--5 * * * *",  # malformed range
            "1,,5 * * * *",  # empty list item
            "1.5 * * * *",  # float
            "-5 * * * *",  # negative / open range
        ],
    )
    def test_parse_rejects_malformed(self, raw: str) -> None:
        with pytest.raises(ValueError):
            CronExpression.parse(raw)


class TestNextAfter:
    def test_every_minute(self) -> None:
        expr = CronExpression.parse("* * * * *")
        assert expr.next_after(_dt(2026, 1, 5, 10, 30)) == _dt(2026, 1, 5, 10, 31)

    def test_strictly_after_exact_match(self) -> None:
        expr = CronExpression.parse("30 14 * * *")
        assert expr.next_after(_dt(2026, 1, 5, 14, 30)) == _dt(2026, 1, 6, 14, 30)

    def test_exact_date_match(self) -> None:
        expr = CronExpression.parse("30 14 15 6 *")
        assert expr.next_after(_dt(2026, 1, 1, 0, 0)) == _dt(2026, 6, 15, 14, 30)

    def test_step_star(self) -> None:
        expr = CronExpression.parse("*/15 * * * *")
        assert expr.next_after(_dt(2026, 1, 5, 10, 7)) == _dt(2026, 1, 5, 10, 15)

    def test_step_star_rolls_to_next_hour(self) -> None:
        expr = CronExpression.parse("*/15 * * * *")
        assert expr.next_after(_dt(2026, 1, 5, 10, 45)) == _dt(2026, 1, 5, 11, 0)

    def test_step_on_range(self) -> None:
        # 1-30/5 -> {1, 6, 11, 16, 21, 26}
        expr = CronExpression.parse("1-30/5 * * * *")
        assert expr.next_after(_dt(2026, 1, 5, 10, 0)) == _dt(2026, 1, 5, 10, 1)
        assert expr.next_after(_dt(2026, 1, 5, 10, 26)) == _dt(2026, 1, 5, 11, 1)

    def test_list(self) -> None:
        expr = CronExpression.parse("1,15,30 * * * *")
        assert expr.next_after(_dt(2026, 1, 5, 10, 15)) == _dt(2026, 1, 5, 10, 30)
        assert expr.next_after(_dt(2026, 1, 5, 10, 30)) == _dt(2026, 1, 5, 11, 1)

    def test_hour_range_rolls_to_next_day(self) -> None:
        expr = CronExpression.parse("0 9-17 * * *")
        assert expr.next_after(_dt(2026, 1, 5, 18, 0)) == _dt(2026, 1, 6, 9, 0)

    def test_day_of_week_sunday_zero(self) -> None:
        # 2026-01-05 is a Monday; next Sunday is 2026-01-11.
        expr = CronExpression.parse("0 0 * * 0")
        assert expr.next_after(_dt(2026, 1, 5, 0, 0)) == _dt(2026, 1, 11, 0, 0)

    def test_day_of_week_seven_is_sunday(self) -> None:
        seven = CronExpression.parse("0 0 * * 7")
        zero = CronExpression.parse("0 0 * * 0")
        start = _dt(2026, 1, 5, 0, 0)
        assert seven.next_after(start) == zero.next_after(start)

    def test_day_of_week_only_restriction(self) -> None:
        # 2026-01-01 is a Thursday; next Monday is 2026-01-05.
        expr = CronExpression.parse("0 0 * * 1")
        assert expr.next_after(_dt(2026, 1, 1, 0, 0)) == _dt(2026, 1, 5, 0, 0)

    def test_dom_and_dow_are_ored_when_both_restricted(self) -> None:
        # "13th of the month OR Friday".
        expr = CronExpression.parse("0 0 13 * 5")
        # From Thu 2026-01-01 the nearest match is Fri 2026-01-02.
        assert expr.next_after(_dt(2026, 1, 1, 0, 0)) == _dt(2026, 1, 2, 0, 0)
        # From Sat 2026-01-10 the 13th (a Tuesday) precedes the next Friday.
        assert expr.next_after(_dt(2026, 1, 10, 0, 0)) == _dt(2026, 1, 13, 0, 0)

    def test_dom_only_skips_short_month(self) -> None:
        expr = CronExpression.parse("0 0 31 * *")
        assert expr.next_after(_dt(2026, 2, 1, 0, 0)) == _dt(2026, 3, 31, 0, 0)

    def test_month_rollover_to_next_year(self) -> None:
        expr = CronExpression.parse("0 0 1 1 *")
        assert expr.next_after(_dt(2026, 3, 1, 0, 0)) == _dt(2027, 1, 1, 0, 0)

    def test_month_restriction_rolls_forward(self) -> None:
        expr = CronExpression.parse("0 0 * 2 *")
        assert expr.next_after(_dt(2026, 3, 15, 0, 0)) == _dt(2027, 2, 1, 0, 0)

    def test_leap_day(self) -> None:
        expr = CronExpression.parse("0 0 29 2 *")
        assert expr.next_after(_dt(2025, 3, 1, 0, 0)) == _dt(2028, 2, 29, 0, 0)

    def test_impossible_schedule_raises(self) -> None:
        expr = CronExpression.parse("0 0 31 2 *")
        with pytest.raises(ValueError):
            expr.next_after(_dt(2026, 1, 1, 0, 0))

    def test_naive_input_treated_as_utc(self) -> None:
        expr = CronExpression.parse("30 14 * * *")
        result = expr.next_after(datetime(2026, 1, 5, 10, 0))
        assert result.tzinfo is not None
        assert result == _dt(2026, 1, 5, 14, 30)

    def test_aware_non_utc_input_converted(self) -> None:
        expr = CronExpression.parse("0 12 * * *")
        plus_two = timezone(timedelta(hours=2))
        # 2026-01-05 13:00+02:00 == 11:00 UTC -> next noon UTC is same day.
        result = expr.next_after(datetime(2026, 1, 5, 13, 0, tzinfo=plus_two))
        assert result == _dt(2026, 1, 5, 12, 0)

    def test_seconds_are_truncated(self) -> None:
        expr = CronExpression.parse("* * * * *")
        result = expr.next_after(_dt(2026, 1, 5, 10, 30) + timedelta(seconds=42))
        assert result == _dt(2026, 1, 5, 10, 31)
