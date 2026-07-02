"""Security-incident domain types for regulatory reporting.

Models a *significant incident* and the three regulatory milestones the NIS2
Directive (EU 2022/2555) imposes on essential/important entities:

    * **early warning** — within 24h of becoming aware;
    * **incident notification** — within 72h of becoming aware;
    * **final report** — within one month of the notification.

The DORA Regulation (EU 2022/2554) imposes a different clock on financial
entities for *major* ICT-related incidents (initial 4h / intermediate 72h /
final one-month); its kinds live in :class:`DoraMilestoneKind` and its incident
model in :mod:`core.incidents.dora`. Both regimes share the generic
:class:`ReportingMilestone` deadline primitive defined here.

The framework cannot file with a competent authority on the operator's behalf,
but it gives them a structured record with computed deadlines so the reporting
clock is explicit and overdue obligations are detectable. Timestamps are
timezone-aware UTC throughout.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any
from uuid import uuid4


def _utcnow() -> datetime:
    """Current time as a timezone-aware UTC datetime."""
    return datetime.now(UTC)


class IncidentSeverity(str, Enum):
    """Severity of a security incident (CVSS-aligned bands)."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class IncidentStatus(str, Enum):
    """Lifecycle of an incident through the NIS2 reporting milestones."""

    DETECTED = "detected"
    EARLY_WARNING_SUBMITTED = "early_warning_submitted"
    NOTIFICATION_SUBMITTED = "notification_submitted"
    FINAL_SUBMITTED = "final_submitted"
    CLOSED = "closed"


class MilestoneKind(str, Enum):
    """The three NIS2 Art. 23 reporting obligations."""

    EARLY_WARNING = "early_warning"
    NOTIFICATION = "notification"
    FINAL_REPORT = "final_report"


class DoraMilestoneKind(str, Enum):
    """The three DORA Art. 19 reporting obligations for major ICT incidents."""

    INITIAL_NOTIFICATION = "initial_notification"
    INTERMEDIATE_REPORT = "intermediate_report"
    FINAL_REPORT = "final_report"


@dataclass
class ReportingMilestone:
    """A single regulatory reporting obligation with its deadline and status.

    Shared across reporting regimes: ``kind`` carries either a NIS2
    :class:`MilestoneKind` or a DORA :class:`DoraMilestoneKind`.
    """

    kind: MilestoneKind | DoraMilestoneKind
    due_at: datetime
    submitted_at: datetime | None = None

    @property
    def is_submitted(self) -> bool:
        """Whether this obligation has been fulfilled."""
        return self.submitted_at is not None

    def is_overdue(self, now: datetime | None = None) -> bool:
        """Whether the deadline has passed without a submission."""
        if self.is_submitted:
            return False
        return (now or _utcnow()) > self.due_at

    def seconds_remaining(self, now: datetime | None = None) -> float:
        """Seconds until the deadline (negative if overdue)."""
        return (self.due_at - (now or _utcnow())).total_seconds()

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "due_at": self.due_at.isoformat(),
            "submitted_at": (
                self.submitted_at.isoformat() if self.submitted_at else None
            ),
            "submitted": self.is_submitted,
            "overdue": self.is_overdue(),
        }


@dataclass
class SecurityIncident:
    """A security incident tracked for NIS2 Art. 23 reporting.

    Only ``significant`` incidents carry regulatory reporting deadlines; others
    are recorded for the incident-handling trail (Art. 21(2)(b)) without a
    reporting clock.
    """

    title: str
    severity: IncidentSeverity
    detected_at: datetime = field(default_factory=_utcnow)
    significant: bool = True
    description: str = ""
    affected_systems: list[str] = field(default_factory=list)
    affected_subjects: int = 0
    status: IncidentStatus = IncidentStatus.DETECTED
    details: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid4().hex)
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)
    # Submission timestamps for the three milestones (None until fulfilled).
    early_warning_at: datetime | None = None
    notification_at: datetime | None = None
    final_report_at: datetime | None = None
    closed_at: datetime | None = None

    def milestones(
        self,
        *,
        early_warning_hours: int = 24,
        notification_hours: int = 72,
        final_report_days: int = 30,
    ) -> list[ReportingMilestone]:
        """Compute the NIS2 reporting milestones for this incident.

        Returns an empty list for non-significant incidents (no reporting clock).
        Early-warning and notification deadlines are anchored to ``detected_at``;
        the final-report deadline is one month after the *notification* (using
        its actual submission time when available, else the notification due
        date as a planning anchor).
        """
        if not self.significant:
            return []
        early_due = self.detected_at + timedelta(hours=early_warning_hours)
        notif_due = self.detected_at + timedelta(hours=notification_hours)
        final_anchor = self.notification_at or notif_due
        final_due = final_anchor + timedelta(days=final_report_days)
        return [
            ReportingMilestone(
                MilestoneKind.EARLY_WARNING, early_due, self.early_warning_at
            ),
            ReportingMilestone(
                MilestoneKind.NOTIFICATION, notif_due, self.notification_at
            ),
            ReportingMilestone(
                MilestoneKind.FINAL_REPORT, final_due, self.final_report_at
            ),
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "severity": self.severity.value,
            "status": self.status.value,
            "significant": self.significant,
            "detected_at": self.detected_at.isoformat(),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "description": self.description,
            "affected_systems": list(self.affected_systems),
            "affected_subjects": self.affected_subjects,
            "details": self.details,
            "early_warning_at": (
                self.early_warning_at.isoformat() if self.early_warning_at else None
            ),
            "notification_at": (
                self.notification_at.isoformat() if self.notification_at else None
            ),
            "final_report_at": (
                self.final_report_at.isoformat() if self.final_report_at else None
            ),
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
        }


__all__ = [
    "DoraMilestoneKind",
    "IncidentSeverity",
    "IncidentStatus",
    "MilestoneKind",
    "ReportingMilestone",
    "SecurityIncident",
]
