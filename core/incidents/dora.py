"""DORA major ICT-related incident domain types (Regulation (EU) 2022/2554).

DORA Art. 19 obliges financial entities to report *major* ICT-related incidents
to their competent authority against a three-step clock:

    * **initial notification** — as soon as possible and within **4h** of the
      incident being classified as major, and in any case no later than **24h**
      from becoming aware of it;
    * **intermediate report** — within **72h** of the initial notification;
    * **final report** — no later than **one month** after the intermediate
      report (when the root-cause analysis is complete).

Whether an incident is *major* is decided by the classification criteria of
Commission Delegated Regulation (EU) 2024/1772: clients/financial counterparts
affected, reputational impact, duration/service downtime, geographical spread,
data losses, criticality of services affected, and economic impact. The exact
materiality thresholds for each criterion are operator-set against that RTS;
this module records which criteria are met and derives a *major* determination,
which can also be set explicitly.

As with the NIS2 subsystem, the framework cannot file with the authority on the
operator's behalf — it produces the structured record and the computed
deadlines so the reporting clock is explicit. Timestamps are timezone-aware UTC.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any
from uuid import uuid4

from core.incidents.types import (
    DoraMilestoneKind,
    IncidentSeverity,
    ReportingMilestone,
    _parse_dt,
    _utcnow,
)


class DoraIncidentStatus(str, Enum):
    """Lifecycle of a DORA incident through the Art. 19 reporting milestones."""

    DETECTED = "detected"
    CLASSIFIED = "classified"
    INITIAL_SUBMITTED = "initial_submitted"
    INTERMEDIATE_SUBMITTED = "intermediate_submitted"
    FINAL_SUBMITTED = "final_submitted"
    CLOSED = "closed"


@dataclass
class DoraImpactAssessment:
    """The DORA classification criteria (Delegated Regulation (EU) 2024/1772).

    Each flag is the operator's assessment of whether that criterion's
    materiality threshold has been met. ``critical_services_affected`` is the
    pivotal criterion; the remaining six are the "other" criteria weighed
    against it in :meth:`DoraClassification.is_major`.
    """

    critical_services_affected: bool = False
    clients_affected: bool = False
    reputational_impact: bool = False
    service_downtime: bool = False
    geographical_spread: bool = False
    data_losses: bool = False
    economic_impact: bool = False

    def other_criteria_met(self) -> int:
        """Count of met criteria other than ``critical_services_affected``."""
        return sum(
            (
                self.clients_affected,
                self.reputational_impact,
                self.service_downtime,
                self.geographical_spread,
                self.data_losses,
                self.economic_impact,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "critical_services_affected": self.critical_services_affected,
            "clients_affected": self.clients_affected,
            "reputational_impact": self.reputational_impact,
            "service_downtime": self.service_downtime,
            "geographical_spread": self.geographical_spread,
            "data_losses": self.data_losses,
            "economic_impact": self.economic_impact,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DoraImpactAssessment:
        """Reconstruct the assessment from its :meth:`to_dict` payload."""
        return cls(
            critical_services_affected=data.get("critical_services_affected", False),
            clients_affected=data.get("clients_affected", False),
            reputational_impact=data.get("reputational_impact", False),
            service_downtime=data.get("service_downtime", False),
            geographical_spread=data.get("geographical_spread", False),
            data_losses=data.get("data_losses", False),
            economic_impact=data.get("economic_impact", False),
        )


@dataclass
class DoraClassification:
    """A major-incident determination and the moment it was reached.

    ``classified_at`` anchors the 4h initial-notification clock. ``is_major``
    follows the RTS-aligned default rule — *critical services affected* plus at
    least two other criteria — unless ``major_override`` pins it explicitly
    (use the override when the operator's RTS threshold analysis differs).
    """

    assessment: DoraImpactAssessment
    classified_at: datetime = field(default_factory=_utcnow)
    major_override: bool | None = None

    @property
    def is_major(self) -> bool:
        """Whether the incident qualifies as *major* under DORA Art. 18."""
        if self.major_override is not None:
            return self.major_override
        return (
            self.assessment.critical_services_affected
            and self.assessment.other_criteria_met() >= 2
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "assessment": self.assessment.to_dict(),
            "classified_at": self.classified_at.isoformat(),
            "major_override": self.major_override,
            "is_major": self.is_major,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DoraClassification:
        """Reconstruct the classification (``is_major`` is derived, ignored here)."""
        return cls(
            assessment=DoraImpactAssessment.from_dict(data["assessment"]),
            classified_at=datetime.fromisoformat(data["classified_at"]),
            major_override=data.get("major_override"),
        )


@dataclass
class DoraIncident:
    """A major ICT-related incident tracked for DORA Art. 19 reporting.

    Only incidents whose ``classification`` resolves to *major* carry reporting
    deadlines; others are recorded for the incident-handling trail without a
    reporting clock. ``detected_at`` is the moment of awareness (anchors the 24h
    cap); ``classification.classified_at`` anchors the 4h initial-notification
    deadline.
    """

    title: str
    severity: IncidentSeverity = IncidentSeverity.HIGH
    detected_at: datetime = field(default_factory=_utcnow)
    classification: DoraClassification | None = None
    description: str = ""
    affected_systems: list[str] = field(default_factory=list)
    affected_clients: int = 0
    status: DoraIncidentStatus = DoraIncidentStatus.DETECTED
    details: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid4().hex)
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)
    # Submission timestamps for the three milestones (None until fulfilled).
    initial_notification_at: datetime | None = None
    intermediate_report_at: datetime | None = None
    final_report_at: datetime | None = None
    closed_at: datetime | None = None

    @property
    def is_major(self) -> bool:
        """Whether this incident has been classified as major (reportable)."""
        return self.classification is not None and self.classification.is_major

    def milestones(
        self,
        *,
        initial_hours: int = 4,
        awareness_cap_hours: int = 24,
        intermediate_hours: int = 72,
        final_days: int = 30,
    ) -> list[ReportingMilestone]:
        """Compute the DORA reporting milestones for this incident.

        Returns an empty list until the incident is classified as major (no
        reporting clock before then). The initial-notification deadline is the
        *earlier* of ``classified_at + initial_hours`` and the hard
        ``detected_at + awareness_cap_hours`` cap — both must be satisfied. The
        intermediate deadline is anchored to the actual initial notification
        (else its due date); the final deadline to the actual intermediate
        report (else its due date).
        """
        if self.classification is None or not self.classification.is_major:
            return []
        classified_at = self.classification.classified_at
        initial_due = min(
            classified_at + timedelta(hours=initial_hours),
            self.detected_at + timedelta(hours=awareness_cap_hours),
        )
        intermediate_anchor = self.initial_notification_at or initial_due
        intermediate_due = intermediate_anchor + timedelta(hours=intermediate_hours)
        final_anchor = self.intermediate_report_at or intermediate_due
        final_due = final_anchor + timedelta(days=final_days)
        return [
            ReportingMilestone(
                DoraMilestoneKind.INITIAL_NOTIFICATION,
                initial_due,
                self.initial_notification_at,
            ),
            ReportingMilestone(
                DoraMilestoneKind.INTERMEDIATE_REPORT,
                intermediate_due,
                self.intermediate_report_at,
            ),
            ReportingMilestone(
                DoraMilestoneKind.FINAL_REPORT,
                final_due,
                self.final_report_at,
            ),
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "severity": self.severity.value,
            "status": self.status.value,
            "is_major": self.is_major,
            "detected_at": self.detected_at.isoformat(),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "description": self.description,
            "affected_systems": list(self.affected_systems),
            "affected_clients": self.affected_clients,
            "classification": (
                self.classification.to_dict() if self.classification else None
            ),
            "details": self.details,
            "initial_notification_at": (
                self.initial_notification_at.isoformat()
                if self.initial_notification_at
                else None
            ),
            "intermediate_report_at": (
                self.intermediate_report_at.isoformat()
                if self.intermediate_report_at
                else None
            ),
            "final_report_at": (
                self.final_report_at.isoformat() if self.final_report_at else None
            ),
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DoraIncident:
        """Reconstruct an incident from its :meth:`to_dict` payload (round-trip)."""
        classification = data.get("classification")
        return cls(
            title=data["title"],
            severity=IncidentSeverity(data["severity"]),
            detected_at=datetime.fromisoformat(data["detected_at"]),
            classification=(
                DoraClassification.from_dict(classification)
                if classification is not None
                else None
            ),
            description=data.get("description", ""),
            affected_systems=list(data.get("affected_systems", [])),
            affected_clients=data.get("affected_clients", 0),
            status=DoraIncidentStatus(data["status"]),
            details=dict(data.get("details", {})),
            id=data["id"],
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            initial_notification_at=_parse_dt(data.get("initial_notification_at")),
            intermediate_report_at=_parse_dt(data.get("intermediate_report_at")),
            final_report_at=_parse_dt(data.get("final_report_at")),
            closed_at=_parse_dt(data.get("closed_at")),
        )


__all__ = [
    "DoraClassification",
    "DoraImpactAssessment",
    "DoraIncident",
    "DoraIncidentStatus",
]
