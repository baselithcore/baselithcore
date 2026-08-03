"""GDPR personal-data-breach domain types (Regulation (EU) 2016/679).

A personal data breach triggers a different clock from NIS2 and DORA, on a
different trigger, toward a different authority — which is precisely why an
incident that satisfies NIS2 Art. 23 can still leave GDPR Art. 33 unmet:

    * **Art. 33(1)** — notify the *supervisory authority* without undue delay
      and, where feasible, **within 72 hours** of becoming aware, *unless* the
      breach is unlikely to result in a risk to the rights and freedoms of
      natural persons. A notification made later must state the reasons for the
      delay.
    * **Art. 33(2)** — a *processor* notifies its controller without undue
      delay; it does not file with the authority itself.
    * **Art. 33(5)** — the controller documents **every** breach, including
      those not notified: the facts, the effects, and the remedial action. That
      register is what a supervisory authority inspects.
    * **Art. 34(1)** — where the breach is likely to result in a **high** risk,
      communicate it to the *data subjects* without undue delay, unless one of
      the Art. 34(3) exemptions applies.

The framework produces the structured record and the deadline; filing remains
the controller's action. Timestamps are timezone-aware UTC.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any
from uuid import uuid4

from core.incidents.types import (
    GdprMilestoneKind,
    ReportingMilestone,
    _parse_dt,
    _utcnow,
)

#: Art. 33(1): the outer limit for notifying the supervisory authority.
AUTHORITY_NOTIFICATION_HOURS = 72


class BreachRiskLevel(str, Enum):
    """Risk to the rights and freedoms of natural persons.

    Drives both obligations: ``NONE`` exempts from Art. 33 notification (the
    breach is still documented under Art. 33(5)); ``HIGH`` additionally triggers
    the Art. 34 communication to data subjects.
    """

    NONE = "none"
    RISK = "risk"
    HIGH = "high"


class BreachRole(str, Enum):
    """Whether this entity acts as controller or processor for the breach."""

    CONTROLLER = "controller"
    PROCESSOR = "processor"


class Art34Exemption(str, Enum):
    """The Art. 34(3) grounds for not communicating to data subjects.

    ``PROTECTION_MEASURES`` — the data was rendered unintelligible (e.g.
    encrypted). ``SUBSEQUENT_MEASURES`` — later measures make the high risk no
    longer likely. ``DISPROPORTIONATE_EFFORT`` — individual communication would
    take disproportionate effort, so a public communication is made instead.
    """

    PROTECTION_MEASURES = "protection_measures"
    SUBSEQUENT_MEASURES = "subsequent_measures"
    DISPROPORTIONATE_EFFORT = "disproportionate_effort"


class BreachStatus(str, Enum):
    """Lifecycle of a personal data breach through the Art. 33/34 obligations."""

    DETECTED = "detected"
    AUTHORITY_NOTIFIED = "authority_notified"
    SUBJECTS_COMMUNICATED = "subjects_communicated"
    CLOSED = "closed"


@dataclass
class PersonalDataBreach:
    """A personal data breach tracked for GDPR Art. 33/34.

    ``became_aware_at`` anchors the 72-hour clock — awareness, not detection by
    a monitoring system, and not the moment the investigation concluded.
    """

    title: str
    became_aware_at: datetime = field(default_factory=_utcnow)
    risk_level: BreachRiskLevel = BreachRiskLevel.RISK
    role: BreachRole = BreachRole.CONTROLLER
    description: str = ""
    data_categories: list[str] = field(default_factory=list)
    affected_subjects: int = 0
    affected_records: int = 0
    # Art. 33(5): the register content — consequences and remedial action.
    likely_consequences: str = ""
    remedial_action: str = ""
    # Art. 33(1): required when the notification lands past the 72h horizon.
    delay_reason: str | None = None
    # Art. 34(3): set when data-subject communication is not performed.
    subject_exemption: Art34Exemption | None = None
    status: BreachStatus = BreachStatus.DETECTED
    details: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid4().hex)
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)
    # Submission timestamps (None until fulfilled).
    authority_notified_at: datetime | None = None
    subjects_communicated_at: datetime | None = None
    # Art. 33(2): a processor's notification to its controller.
    controller_notified_at: datetime | None = None
    closed_at: datetime | None = None

    @property
    def requires_authority_notification(self) -> bool:
        """Whether Art. 33(1) applies (a processor reports to its controller)."""
        return (
            self.role is BreachRole.CONTROLLER
            and self.risk_level is not BreachRiskLevel.NONE
        )

    @property
    def requires_subject_communication(self) -> bool:
        """Whether Art. 34(1) applies and no Art. 34(3) exemption was claimed."""
        return (
            self.risk_level is BreachRiskLevel.HIGH and self.subject_exemption is None
        )

    @property
    def is_late(self) -> bool:
        """Whether the authority notification landed past the 72h horizon.

        A late notification is lawful but must carry ``delay_reason``.
        """
        if self.authority_notified_at is None:
            return False
        deadline = self.became_aware_at + timedelta(hours=AUTHORITY_NOTIFICATION_HOURS)
        return self.authority_notified_at > deadline

    def milestones(
        self,
        *,
        authority_hours: int = AUTHORITY_NOTIFICATION_HOURS,
        subject_communication_hours: int = 72,
    ) -> list[ReportingMilestone]:
        """Compute the applicable Art. 33/34 milestones.

        Returns only the obligations that actually apply: a no-risk breach has
        no notification clock (it is still documented under Art. 33(5)), and a
        breach with an Art. 34(3) exemption has no communication clock.

        ``subject_communication_hours`` has **no statutory value** — Art. 34(1)
        says "without undue delay" and fixes no outer limit. The default is an
        internal SLA anchored to awareness.
        """
        milestones: list[ReportingMilestone] = []
        if self.requires_authority_notification:
            milestones.append(
                ReportingMilestone(
                    GdprMilestoneKind.AUTHORITY_NOTIFICATION,
                    self.became_aware_at + timedelta(hours=authority_hours),
                    self.authority_notified_at,
                )
            )
        if self.requires_subject_communication:
            milestones.append(
                ReportingMilestone(
                    GdprMilestoneKind.SUBJECT_COMMUNICATION,
                    self.became_aware_at
                    + timedelta(hours=subject_communication_hours),
                    self.subjects_communicated_at,
                )
            )
        return milestones

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status.value,
            "risk_level": self.risk_level.value,
            "role": self.role.value,
            "became_aware_at": self.became_aware_at.isoformat(),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "description": self.description,
            "data_categories": list(self.data_categories),
            "affected_subjects": self.affected_subjects,
            "affected_records": self.affected_records,
            "likely_consequences": self.likely_consequences,
            "remedial_action": self.remedial_action,
            "delay_reason": self.delay_reason,
            "subject_exemption": (
                self.subject_exemption.value if self.subject_exemption else None
            ),
            "requires_authority_notification": self.requires_authority_notification,
            "requires_subject_communication": self.requires_subject_communication,
            "is_late": self.is_late,
            "details": self.details,
            "authority_notified_at": (
                self.authority_notified_at.isoformat()
                if self.authority_notified_at
                else None
            ),
            "subjects_communicated_at": (
                self.subjects_communicated_at.isoformat()
                if self.subjects_communicated_at
                else None
            ),
            "controller_notified_at": (
                self.controller_notified_at.isoformat()
                if self.controller_notified_at
                else None
            ),
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PersonalDataBreach:
        """Reconstruct a breach from its :meth:`to_dict` payload (round-trip)."""
        exemption = data.get("subject_exemption")
        return cls(
            title=data["title"],
            became_aware_at=datetime.fromisoformat(data["became_aware_at"]),
            risk_level=BreachRiskLevel(data["risk_level"]),
            role=BreachRole(data.get("role", BreachRole.CONTROLLER.value)),
            description=data.get("description", ""),
            data_categories=list(data.get("data_categories", [])),
            affected_subjects=data.get("affected_subjects", 0),
            affected_records=data.get("affected_records", 0),
            likely_consequences=data.get("likely_consequences", ""),
            remedial_action=data.get("remedial_action", ""),
            delay_reason=data.get("delay_reason"),
            subject_exemption=Art34Exemption(exemption) if exemption else None,
            status=BreachStatus(data["status"]),
            details=dict(data.get("details", {})),
            id=data["id"],
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            authority_notified_at=_parse_dt(data.get("authority_notified_at")),
            subjects_communicated_at=_parse_dt(data.get("subjects_communicated_at")),
            controller_notified_at=_parse_dt(data.get("controller_notified_at")),
            closed_at=_parse_dt(data.get("closed_at")),
        )


__all__ = [
    "AUTHORITY_NOTIFICATION_HOURS",
    "Art34Exemption",
    "BreachRiskLevel",
    "BreachRole",
    "BreachStatus",
    "PersonalDataBreach",
]
