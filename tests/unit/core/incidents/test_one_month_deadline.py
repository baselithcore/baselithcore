"""A statutory "one month" is a calendar month, not thirty days.

NIS2 Art. 23(4)(d) and DORA Art. 19 both give the final report "one month".
Thirty days overshoots that after a notification late in January, which is the
one direction a compliance clock must never err in.
"""

from datetime import UTC, datetime, timedelta

import pytest

from baselith.core.incidents import (
    DoraClassification,
    DoraImpactAssessment,
    DoraIncident,
    DoraMilestoneKind,
    IncidentSeverity,
    MilestoneKind,
    SecurityIncident,
)
from baselith.core.incidents.types import final_report_due, one_month_after


def _utc(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=UTC)


@pytest.mark.parametrize(
    ("start", "expected"),
    [
        (_utc(2027, 1, 15, 9), _utc(2027, 2, 15, 9)),
        (_utc(2027, 1, 31, 9), _utc(2027, 2, 28, 9)),  # no 31 February
        (_utc(2028, 1, 31, 9), _utc(2028, 2, 29, 9)),  # leap year
        (_utc(2027, 3, 31, 9), _utc(2027, 4, 30, 9)),
        (_utc(2027, 12, 15, 23, 30), _utc(2028, 1, 15, 23, 30)),  # year rollover
    ],
)
def test_one_month_after_follows_the_calendar(start, expected):
    assert one_month_after(start) == expected


def test_the_window_can_be_stricter_than_the_month_but_never_looser():
    anchor = _utc(2027, 1, 31, 9)

    assert final_report_due(anchor, 30) == _utc(2027, 2, 28, 9)
    assert final_report_due(anchor, 90) == _utc(2027, 2, 28, 9)
    assert final_report_due(anchor, 10) == anchor + timedelta(days=10)


def test_nis2_final_report_after_a_late_january_notification():
    """Thirty days from 31 January is 2 March — two days past the limit."""
    incident = SecurityIncident(
        title="Outage",
        severity=IncidentSeverity.HIGH,
        detected_at=_utc(2027, 1, 28, 10),
        notification_at=_utc(2027, 1, 31, 9),
    )

    final = next(
        m for m in incident.milestones() if m.kind is MilestoneKind.FINAL_REPORT
    )

    assert final.due_at == _utc(2027, 2, 28, 9)
    assert final.is_overdue(_utc(2027, 3, 1, 9)) is True


def test_nis2_final_report_in_a_long_month_keeps_the_stricter_thirty_days():
    incident = SecurityIncident(
        title="Outage",
        severity=IncidentSeverity.HIGH,
        detected_at=_utc(2027, 3, 12, 10),
        notification_at=_utc(2027, 3, 15, 9),
    )

    final = next(
        m for m in incident.milestones() if m.kind is MilestoneKind.FINAL_REPORT
    )

    assert final.due_at == _utc(2027, 4, 14, 9)  # one day inside the month


def test_dora_final_report_after_a_late_january_intermediate_report():
    incident = DoraIncident(
        title="Core banking down",
        detected_at=_utc(2027, 1, 27, 8),
        classification=DoraClassification(
            assessment=DoraImpactAssessment(),
            classified_at=_utc(2027, 1, 27, 9),
            major_override=True,
        ),
        initial_notification_at=_utc(2027, 1, 27, 12),
        intermediate_report_at=_utc(2027, 1, 30, 11),
    )

    final = next(
        m for m in incident.milestones() if m.kind is DoraMilestoneKind.FINAL_REPORT
    )

    assert final.due_at == _utc(2027, 2, 28, 11)
