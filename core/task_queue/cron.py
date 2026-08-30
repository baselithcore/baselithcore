"""Pure-stdlib 5-field cron expression parser.

Parses classic ``minute hour day-of-month month day-of-week`` expressions and
computes the next matching UTC instant. Supported syntax per field: ``*``,
single numbers, ranges (``1-5``), steps (``*/15``, ``1-30/5``) and lists
(``1,15,30``). Day-of-week runs 0-6 with 0 = Sunday; 7 is accepted as an
alias for Sunday.

Day matching follows standard (Vixie) cron semantics: when **both**
day-of-month and day-of-week are restricted (i.e. neither field starts with
``*``), a day matches if it satisfies *either* field; otherwise both apply.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

__all__ = ["CronExpression"]

# Inclusive (low, high) bounds per field position.
_FIELD_BOUNDS: tuple[tuple[str, int, int], ...] = (
    ("minute", 0, 59),
    ("hour", 0, 23),
    ("day-of-month", 1, 31),
    ("month", 1, 12),
    ("day-of-week", 0, 7),
)

# Search horizon for :meth:`CronExpression.next_after` (covers leap days).
_MAX_SEARCH = timedelta(days=4 * 366)


def _parse_int(token: str, field_name: str) -> int:
    """Parse a strictly-numeric cron token or raise ``ValueError``."""
    if not token.isdigit():
        raise ValueError(f"Invalid {field_name} value {token!r} in cron expression")
    return int(token)


def _parse_part(part: str, field_name: str, low: int, high: int) -> set[int]:
    """Parse one comma-separated part of a cron field into a value set."""
    base, sep, step_str = part.partition("/")
    step = 1
    if sep:
        step = _parse_int(step_str, field_name)
        if step < 1:
            raise ValueError(f"Step must be >= 1 in {field_name} field: {part!r}")

    if base == "*":
        start, end = low, high
    elif "-" in base:
        start_str, _, end_str = base.partition("-")
        start = _parse_int(start_str, field_name)
        end = _parse_int(end_str, field_name)
        if start > end:
            raise ValueError(f"Inverted range in {field_name} field: {base!r}")
    else:
        start = end = _parse_int(base, field_name)
        if sep:
            # "N/step" means "from N to the field maximum, every step".
            end = high
    if start < low or end > high:
        raise ValueError(
            f"Value out of range ({low}-{high}) in {field_name} field: {part!r}"
        )
    return set(range(start, end + 1, step))


def _parse_field(field: str, field_name: str, low: int, high: int) -> frozenset[int]:
    """Parse a full cron field (possibly a list) into a frozen value set."""
    if not field:
        raise ValueError(f"Empty {field_name} field in cron expression")
    values: set[int] = set()
    for part in field.split(","):
        if not part:
            raise ValueError(f"Empty list item in {field_name} field: {field!r}")
        values |= _parse_part(part, field_name, low, high)
    return frozenset(values)


class CronExpression:
    """A parsed 5-field cron expression.

    Instances are immutable value objects; build them with :meth:`parse`.

    Example:
        >>> expr = CronExpression.parse("*/15 9-17 * * 1-5")
        >>> expr.next_after(datetime(2026, 1, 5, 10, 7, tzinfo=UTC))
        datetime.datetime(2026, 1, 5, 10, 15, tzinfo=datetime.timezone.utc)
    """

    __slots__ = (
        "_dom_restricted",
        "_dow_restricted",
        "days_of_month",
        "days_of_week",
        "expression",
        "hours",
        "minutes",
        "months",
    )

    def __init__(
        self,
        expression: str,
        minutes: frozenset[int],
        hours: frozenset[int],
        days_of_month: frozenset[int],
        months: frozenset[int],
        days_of_week: frozenset[int],
        *,
        dom_restricted: bool,
        dow_restricted: bool,
    ) -> None:
        """Initialize from pre-parsed field sets (use :meth:`parse` instead)."""
        self.expression = expression
        self.minutes = minutes
        self.hours = hours
        self.days_of_month = days_of_month
        self.months = months
        self.days_of_week = days_of_week
        self._dom_restricted = dom_restricted
        self._dow_restricted = dow_restricted

    @classmethod
    def parse(cls, expr: str) -> CronExpression:
        """Parse a 5-field cron expression.

        Args:
            expr: ``minute hour day-of-month month day-of-week`` string.

        Returns:
            The parsed, immutable :class:`CronExpression`.

        Raises:
            ValueError: If the expression is malformed (wrong field count,
                non-numeric tokens, out-of-range values, inverted ranges,
                zero steps, empty list items).
        """
        fields = expr.split()
        if len(fields) != 5:
            raise ValueError(
                f"Cron expression must have exactly 5 fields, got {len(fields)}: "
                f"{expr!r}"
            )
        parsed: list[frozenset[int]] = [
            _parse_field(field, name, low, high)
            for field, (name, low, high) in zip(fields, _FIELD_BOUNDS, strict=True)
        ]
        minutes, hours, dom, months, dow = parsed
        # Normalize day-of-week: 7 is an alias for Sunday (0).
        if 7 in dow:
            dow = (dow - {7}) | {0}
        return cls(
            expr,
            minutes,
            hours,
            dom,
            months,
            dow,
            # Vixie-cron day semantics key off whether the raw field is a "*"
            # pattern, not off the resulting value set.
            dom_restricted=not fields[2].startswith("*"),
            dow_restricted=not fields[4].startswith("*"),
        )

    def _day_matches(self, day: date) -> bool:
        """Return whether a calendar day satisfies the day fields."""
        dom_ok = day.day in self.days_of_month
        # ``date.weekday()``: Monday == 0; cron: Sunday == 0.
        dow_ok = (day.weekday() + 1) % 7 in self.days_of_week
        if self._dom_restricted and self._dow_restricted:
            return dom_ok or dow_ok
        return dom_ok and dow_ok

    def next_after(self, dt: datetime) -> datetime:
        """Return the next matching UTC datetime strictly after ``dt``.

        Args:
            dt: Reference instant. A naive datetime is treated as UTC; an
                aware one is converted to UTC first.

        Returns:
            The next matching tz-aware UTC datetime (seconds truncated).

        Raises:
            ValueError: If no matching instant exists within roughly four
                years (e.g. ``0 0 31 2 *``).
        """
        dt = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
        candidate = (dt + timedelta(minutes=1)).replace(second=0, microsecond=0)
        limit = dt + _MAX_SEARCH
        while candidate <= limit:
            if candidate.month not in self.months:
                year, month = candidate.year, candidate.month + 1
                if month > 12:
                    year, month = year + 1, 1
                candidate = candidate.replace(
                    year=year, month=month, day=1, hour=0, minute=0
                )
            elif not self._day_matches(candidate.date()):
                candidate = (candidate + timedelta(days=1)).replace(hour=0, minute=0)
            elif candidate.hour not in self.hours:
                candidate = (candidate + timedelta(hours=1)).replace(minute=0)
            elif candidate.minute not in self.minutes:
                candidate += timedelta(minutes=1)
            else:
                return candidate
        raise ValueError(
            f"No occurrence of cron expression {self.expression!r} within "
            f"{_MAX_SEARCH.days} days after {dt.isoformat()}"
        )

    def __str__(self) -> str:
        """Return the original expression string."""
        return self.expression

    def __repr__(self) -> str:
        """Return a debug representation."""
        return f"CronExpression({self.expression!r})"
