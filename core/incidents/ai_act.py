"""EU AI Act Art. 73 serious-incident domain types (Regulation (EU) 2024/1689).

Providers of high-risk AI systems placed on the Union market must report
**serious incidents** to the market surveillance authority of the Member State
where the incident occurred. A serious incident is defined in Art. 3(49) as an
incident or malfunctioning of an AI system that directly or indirectly leads to:

    (a) the death of a person, or serious harm to a person's health;
    (b) a serious and irreversible disruption of the management or operation of
        critical infrastructure;
    (c) the infringement of obligations under Union law intended to protect
        fundamental rights;
    (d) serious harm to property or the environment.

The reporting clock is **category-dependent** — this is the part that is easy to
get wrong, and the reason this module exists:

    * **2 days** — a serious incident under Art. 3(49)(b) (critical
      infrastructure) or a *widespread infringement* (Art. 73(3));
    * **10 days** — the death of a person, counted from awareness, reported
      immediately once a causal link is established or even suspected
      (Art. 73(4));
    * **15 days** — every other serious incident, counted from awareness
      (Art. 73(2)).

When several categories apply, the **shortest** deadline governs.

Art. 73(5) permits an initial *incomplete* report where that is what timeliness
requires, followed by a complete one; Art. 73(6) requires the provider to run
the investigation, risk assessment and corrective action that follow — without
altering the system in a way that would compromise the evaluation of its causes
before informing the authorities.

As with the NIS2 and DORA subsystems, the framework cannot file with the
authority on the operator's behalf. It produces the structured record and makes
the clock explicit, so an overdue obligation is detectable rather than silently
missed. Timestamps are timezone-aware UTC.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any
from uuid import uuid4

from core.incidents.types import (
    AiActMilestoneKind,
    IncidentSeverity,
    ReportingMilestone,
    _parse_dt,
    _utcnow,
)

#: Statutory reporting horizons in days, per Art. 73(2)/(3)/(4).
DEADLINE_DAYS_DEATH = 10
DEADLINE_DAYS_CRITICAL_INFRASTRUCTURE = 2
DEADLINE_DAYS_DEFAULT = 15


class SeriousIncidentCategory(str, Enum):
    """The Art. 3(49) categories of serious incident."""

    DEATH = "death"
    SERIOUS_HEALTH_HARM = "serious_health_harm"
    CRITICAL_INFRASTRUCTURE_DISRUPTION = "critical_infrastructure_disruption"
    FUNDAMENTAL_RIGHTS_INFRINGEMENT = "fundamental_rights_infringement"
    PROPERTY_OR_ENVIRONMENTAL_HARM = "property_or_environmental_harm"


class AiActIncidentStatus(str, Enum):
    """Lifecycle of a serious incident through the Art. 73 obligations."""

    DETECTED = "detected"
    CAUSAL_LINK_ESTABLISHED = "causal_link_established"
    REPORT_SUBMITTED = "report_submitted"
    COMPLETE_REPORT_SUBMITTED = "complete_report_submitted"
    CLOSED = "closed"


def report_deadline_days(
    categories: set[SeriousIncidentCategory] | list[SeriousIncidentCategory],
    *,
    widespread_infringement: bool = False,
) -> int:
    """Return the governing Art. 73 reporting horizon, in days.

    The shortest applicable horizon wins when several categories are present.
    An empty category set falls back to the Art. 73(2) 15-day default rather
    than to "no obligation" — a serious incident that resists categorisation is
    still reportable.
    """
    horizons = [DEADLINE_DAYS_DEFAULT]
    if widespread_infringement:
        horizons.append(DEADLINE_DAYS_CRITICAL_INFRASTRUCTURE)
    for category in categories:
        if category is SeriousIncidentCategory.DEATH:
            horizons.append(DEADLINE_DAYS_DEATH)
        elif category is SeriousIncidentCategory.CRITICAL_INFRASTRUCTURE_DISRUPTION:
            horizons.append(DEADLINE_DAYS_CRITICAL_INFRASTRUCTURE)
    return min(horizons)


@dataclass
class AiActSeriousIncident:
    """A serious incident tracked for EU AI Act Art. 73 reporting.

    ``became_aware_at`` anchors every statutory deadline — it is the moment the
    provider (or, where applicable, the deployer) became aware of the incident,
    **not** the moment the causal link was established. ``causal_link_at``
    records the latter, which Art. 73(2)/(4) use to require reporting
    *immediately* once known, ahead of the outer deadline.

    Only incidents flagged ``serious`` carry a reporting clock; the rest are
    recorded for the post-market monitoring trail (Art. 72) without one.
    """

    title: str
    ai_system_id: str
    severity: IncidentSeverity = IncidentSeverity.HIGH
    became_aware_at: datetime = field(default_factory=_utcnow)
    categories: list[SeriousIncidentCategory] = field(default_factory=list)
    widespread_infringement: bool = False
    serious: bool = True
    description: str = ""
    affected_persons: int = 0
    deployer: str | None = None
    member_state: str | None = None
    status: AiActIncidentStatus = AiActIncidentStatus.DETECTED
    details: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid4().hex)
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)
    # Art. 73(2)/(4): when the causal link to the AI system was established.
    causal_link_at: datetime | None = None
    # Submission timestamps (None until fulfilled).
    report_at: datetime | None = None
    complete_report_at: datetime | None = None
    # Art. 73(6) follow-up.
    investigation_at: datetime | None = None
    corrective_action_at: datetime | None = None
    closed_at: datetime | None = None

    @property
    def deadline_days(self) -> int:
        """The governing Art. 73 horizon for this incident, in days."""
        return report_deadline_days(
            self.categories, widespread_infringement=self.widespread_infringement
        )

    def milestones(self, *, complete_report_days: int = 30) -> list[ReportingMilestone]:
        """Compute the Art. 73 reporting milestones for this incident.

        Returns an empty list for a non-serious incident (no reporting clock).

        ``complete_report_days`` has **no statutory value**: Art. 73(5) allows an
        initial incomplete report followed by a complete one but sets no outer
        limit for the latter. The default is an internal SLA, anchored to the
        actual initial report (or, failing that, to its due date).
        """
        if not self.serious:
            return []
        report_due = self.became_aware_at + timedelta(days=self.deadline_days)
        complete_anchor = self.report_at or report_due
        return [
            ReportingMilestone(
                AiActMilestoneKind.SERIOUS_INCIDENT_REPORT,
                report_due,
                self.report_at,
            ),
            ReportingMilestone(
                AiActMilestoneKind.COMPLETE_REPORT,
                complete_anchor + timedelta(days=complete_report_days),
                self.complete_report_at,
            ),
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "ai_system_id": self.ai_system_id,
            "severity": self.severity.value,
            "status": self.status.value,
            "serious": self.serious,
            "categories": [c.value for c in self.categories],
            "widespread_infringement": self.widespread_infringement,
            "deadline_days": self.deadline_days,
            "became_aware_at": self.became_aware_at.isoformat(),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "description": self.description,
            "affected_persons": self.affected_persons,
            "deployer": self.deployer,
            "member_state": self.member_state,
            "details": self.details,
            "causal_link_at": (
                self.causal_link_at.isoformat() if self.causal_link_at else None
            ),
            "report_at": self.report_at.isoformat() if self.report_at else None,
            "complete_report_at": (
                self.complete_report_at.isoformat() if self.complete_report_at else None
            ),
            "investigation_at": (
                self.investigation_at.isoformat() if self.investigation_at else None
            ),
            "corrective_action_at": (
                self.corrective_action_at.isoformat()
                if self.corrective_action_at
                else None
            ),
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AiActSeriousIncident:
        """Reconstruct an incident from its :meth:`to_dict` payload (round-trip)."""
        return cls(
            title=data["title"],
            ai_system_id=data["ai_system_id"],
            severity=IncidentSeverity(data["severity"]),
            became_aware_at=datetime.fromisoformat(data["became_aware_at"]),
            categories=[SeriousIncidentCategory(c) for c in data.get("categories", [])],
            widespread_infringement=data.get("widespread_infringement", False),
            serious=data.get("serious", True),
            description=data.get("description", ""),
            affected_persons=data.get("affected_persons", 0),
            deployer=data.get("deployer"),
            member_state=data.get("member_state"),
            status=AiActIncidentStatus(data["status"]),
            details=dict(data.get("details", {})),
            id=data["id"],
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            causal_link_at=_parse_dt(data.get("causal_link_at")),
            report_at=_parse_dt(data.get("report_at")),
            complete_report_at=_parse_dt(data.get("complete_report_at")),
            investigation_at=_parse_dt(data.get("investigation_at")),
            corrective_action_at=_parse_dt(data.get("corrective_action_at")),
            closed_at=_parse_dt(data.get("closed_at")),
        )


__all__ = [
    "DEADLINE_DAYS_CRITICAL_INFRASTRUCTURE",
    "DEADLINE_DAYS_DEATH",
    "DEADLINE_DAYS_DEFAULT",
    "AiActIncidentStatus",
    "AiActSeriousIncident",
    "SeriousIncidentCategory",
    "report_deadline_days",
]
