"""SOC 2 domain types — AICPA Trust Services Criteria (2017, revised points of focus 2022).

SOC 2 is not a statute with a clock: it is an *attestation* an independent CPA
firm issues over a service organisation's controls, against criteria the AICPA
publishes. Two things follow for what a framework can usefully do.

* It cannot attest. It can make the organisation ready, and make the evidence
  an auditor samples easy to find, complete and tamper-evident.
* The unit of work is not an incident or a record but a **control that keeps
  being performed**: designed once (Type I looks at that, at a date), operated
  repeatedly (Type II looks at that, over an observation period of typically
  three to twelve months). So the model is scope → controls → dated evidence,
  and readiness is a question asked of the evidence over time.

Timestamps are timezone-aware UTC.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import Enum
from typing import Any
from uuid import uuid4


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class TrustCategory(str, Enum):
    """The five Trust Services Categories. Security is always in scope."""

    SECURITY = "security"
    AVAILABILITY = "availability"
    CONFIDENTIALITY = "confidentiality"
    PROCESSING_INTEGRITY = "processing_integrity"
    PRIVACY = "privacy"


class AttestationType(str, Enum):
    """What the auditor examines.

    ``TYPE_I`` — whether the controls are suitably *designed* as of one date.
    ``TYPE_II`` — whether they *operated effectively* over a period.
    """

    TYPE_I = "type_i"
    TYPE_II = "type_ii"


class ControlFrequency(str, Enum):
    """How often a control is performed, hence how often evidence is expected."""

    CONTINUOUS = "continuous"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUALLY = "annually"
    #: Performed when something happens (an incident, a hire): no schedule.
    EVENT = "event"


#: Days between two expected pieces of evidence; ``None`` for ``EVENT``.
FREQUENCY_DAYS: dict[ControlFrequency, int | None] = {
    ControlFrequency.CONTINUOUS: 1,
    ControlFrequency.DAILY: 1,
    ControlFrequency.WEEKLY: 7,
    ControlFrequency.MONTHLY: 31,
    ControlFrequency.QUARTERLY: 92,
    ControlFrequency.ANNUALLY: 366,
    ControlFrequency.EVENT: None,
}


class EvidenceKind(str, Enum):
    AUTOMATIC = "automatic"
    MANUAL = "manual"


class EvidenceOutcome(str, Enum):
    """What the evidence shows about the control."""

    #: The control operated as described.
    OPERATED = "operated"
    #: A deviation: the control did not operate, or a check failed.
    EXCEPTION = "exception"
    #: Context an auditor wants to see, not a verdict.
    INFORMATIONAL = "informational"


@dataclass
class Soc2Scope:
    """One attestation engagement: what is in scope and over which period."""

    name: str
    categories: list[TrustCategory] = field(
        default_factory=lambda: [TrustCategory.SECURITY]
    )
    attestation_type: AttestationType = AttestationType.TYPE_I
    #: Type II: the first day of the observation period. Type I: the date the
    #: design is examined as of (also ``period_end``).
    period_start: date = field(default_factory=lambda: _utcnow().date())
    #: Type II: the last day. ``None`` keeps the period open (readiness is then
    #: judged up to today). Type I: the as-of date.
    period_end: date | None = None
    #: The system description the report opens with (services, infrastructure,
    #: people, procedures, data): free text the auditor reads first.
    system_description: str = ""
    id: str = field(default_factory=lambda: uuid4().hex)
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)

    def __post_init__(self) -> None:
        # Common Criteria are the Security category: it is never optional.
        if TrustCategory.SECURITY not in self.categories:
            self.categories = [TrustCategory.SECURITY, *self.categories]

    @property
    def as_of(self) -> date:
        """The as-of date: the period's end, or its start when there is none.

        An open Type II period has no as-of date and is judged up to now (see
        :func:`baselith.core.compliance.soc2.readiness.judged_at`).
        """
        return self.period_end or self.period_start

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "categories": [c.value for c in self.categories],
            "attestation_type": self.attestation_type.value,
            "period_start": self.period_start.isoformat(),
            "period_end": _iso(self.period_end),
            "system_description": self.system_description,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Soc2Scope:
        end = data.get("period_end")
        return cls(
            name=data["name"],
            categories=[TrustCategory(c) for c in data.get("categories", [])],
            attestation_type=AttestationType(data["attestation_type"]),
            period_start=date.fromisoformat(data["period_start"]),
            period_end=date.fromisoformat(end) if end else None,
            system_description=data.get("system_description", ""),
            id=data["id"],
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
        )


@dataclass
class Soc2Control:
    """A control the organisation performs, mapped to the criteria it addresses.

    Controls are the organisation's own (a statement of what *you* do), not a
    copy of the AICPA text: one control commonly addresses several criteria and
    one criterion several controls.
    """

    title: str
    description: str = ""
    owner: str = ""
    criteria: list[str] = field(default_factory=list)
    frequency: ControlFrequency = ControlFrequency.MONTHLY
    #: Names of automatic evidence collectors that feed this control.
    collectors: list[str] = field(default_factory=list)
    active: bool = True
    id: str = field(default_factory=lambda: uuid4().hex)
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "owner": self.owner,
            "criteria": list(self.criteria),
            "frequency": self.frequency.value,
            "collectors": list(self.collectors),
            "active": self.active,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Soc2Control:
        return cls(
            title=data["title"],
            description=data.get("description", ""),
            owner=data.get("owner", ""),
            criteria=list(data.get("criteria", [])),
            frequency=ControlFrequency(data["frequency"]),
            collectors=list(data.get("collectors", [])),
            active=data.get("active", True),
            id=data["id"],
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
        )


@dataclass
class Soc2Evidence:
    """One dated piece of evidence that a control operated (or did not).

    ``sha256`` binds the record to its content: of the canonical JSON of
    ``details`` for an automatic collection, or of the document the operator
    points at for a manual one. Recording it also writes the digest to the
    audit trail, so the evidence cannot be quietly replaced afterwards.
    """

    control_id: str
    title: str
    kind: EvidenceKind
    source: str
    sha256: str
    outcome: EvidenceOutcome = EvidenceOutcome.OPERATED
    summary: str = ""
    #: Where the artefact lives (a ticket, a document-store URL, a file path).
    reference: str = ""
    recorded_by: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    collected_at: datetime = field(default_factory=_utcnow)
    id: str = field(default_factory=lambda: uuid4().hex)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "control_id": self.control_id,
            "title": self.title,
            "kind": self.kind.value,
            "source": self.source,
            "sha256": self.sha256,
            "outcome": self.outcome.value,
            "summary": self.summary,
            "reference": self.reference,
            "recorded_by": self.recorded_by,
            "details": self.details,
            "collected_at": self.collected_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Soc2Evidence:
        return cls(
            control_id=data["control_id"],
            title=data["title"],
            kind=EvidenceKind(data["kind"]),
            source=data["source"],
            sha256=data["sha256"],
            outcome=EvidenceOutcome(data["outcome"]),
            summary=data.get("summary", ""),
            reference=data.get("reference", ""),
            recorded_by=data.get("recorded_by", ""),
            details=dict(data.get("details", {})),
            collected_at=datetime.fromisoformat(data["collected_at"]),
            id=data["id"],
        )


__all__ = [
    "FREQUENCY_DAYS",
    "AttestationType",
    "ControlFrequency",
    "EvidenceKind",
    "EvidenceOutcome",
    "Soc2Control",
    "Soc2Evidence",
    "Soc2Scope",
    "TrustCategory",
]
