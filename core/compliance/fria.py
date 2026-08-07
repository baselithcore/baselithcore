"""Art. 27 fundamental rights impact assessment (FRIA).

Before putting a high-risk AI system into use, deployers that are bodies
governed by public law, private entities providing public services, or
deployers of the Annex III(5)(b)/(c) credit-scoring and life/health insurance
systems must perform a FRIA. Art. 27(1) fixes its content — six elements — and
Art. 27(3) requires notifying the market surveillance authority of the results.

This module models the assessment so its **completeness is checkable**: an
assessment missing one of the six statutory elements is not an assessment, and
:meth:`FundamentalRightsImpactAssessment.missing_elements` says which. Filling
it in is the deployer's substantive work; what the framework contributes is that
the gaps are visible before an authority finds them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4

from core.compliance.types import _iso, _parse_dt, _utcnow


@dataclass
class FriaRisk:
    """A specific risk of harm to a category of affected persons — Art. 27(1)(d)."""

    description: str
    affected_category: str = ""
    likelihood: str = ""
    severity: str = ""
    mitigation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "description": self.description,
            "affected_category": self.affected_category,
            "likelihood": self.likelihood,
            "severity": self.severity,
            "mitigation": self.mitigation,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FriaRisk:
        return cls(
            description=data["description"],
            affected_category=data.get("affected_category", ""),
            likelihood=data.get("likelihood", ""),
            severity=data.get("severity", ""),
            mitigation=data.get("mitigation", ""),
        )


@dataclass
class FundamentalRightsImpactAssessment:
    """A FRIA for one high-risk AI system, per Art. 27(1)(a)–(f)."""

    system_id: str
    deployer: str
    #: (a) the deployer's processes in which the system will be used.
    processes_description: str = ""
    #: (b) the period of time and frequency of intended use.
    usage_period: str = ""
    usage_frequency: str = ""
    #: (c) the categories of natural persons and groups likely to be affected.
    affected_categories: list[str] = field(default_factory=list)
    #: (d) the specific risks of harm likely to impact those categories.
    risks: list[FriaRisk] = field(default_factory=list)
    #: (e) the implementation of human oversight measures.
    human_oversight_measures: str = ""
    #: (f) the measures if the risks materialise, including internal governance
    #: arrangements and complaint mechanisms.
    measures_if_materialised: str = ""
    governance_arrangements: str = ""
    complaint_mechanism: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid4().hex)
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)
    completed_at: datetime | None = None
    #: Art. 27(3): the results are notified to the market surveillance authority.
    authority_notified_at: datetime | None = None

    def missing_elements(self) -> list[str]:
        """Return the Art. 27(1) elements that are still empty.

        An empty list means every statutory element carries content — not that
        the content is *adequate*, which no automated check can judge.
        """
        missing: list[str] = []
        if not self.processes_description:
            missing.append("Art. 27(1)(a) — description of the deployer's processes")
        if not self.usage_period:
            missing.append("Art. 27(1)(b) — period of intended use")
        if not self.usage_frequency:
            missing.append("Art. 27(1)(b) — frequency of intended use")
        if not self.affected_categories:
            missing.append("Art. 27(1)(c) — categories of persons affected")
        if not self.risks:
            missing.append("Art. 27(1)(d) — specific risks of harm")
        if not self.human_oversight_measures:
            missing.append("Art. 27(1)(e) — human oversight measures")
        if not self.measures_if_materialised:
            missing.append("Art. 27(1)(f) — measures if the risks materialise")
        if not self.governance_arrangements:
            missing.append("Art. 27(1)(f) — internal governance arrangements")
        if not self.complaint_mechanism:
            missing.append("Art. 27(1)(f) — complaint mechanisms")
        return missing

    @property
    def is_complete(self) -> bool:
        """Whether every Art. 27(1) element carries content."""
        return not self.missing_elements()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "system_id": self.system_id,
            "deployer": self.deployer,
            "processes_description": self.processes_description,
            "usage_period": self.usage_period,
            "usage_frequency": self.usage_frequency,
            "affected_categories": list(self.affected_categories),
            "risks": [r.to_dict() for r in self.risks],
            "human_oversight_measures": self.human_oversight_measures,
            "measures_if_materialised": self.measures_if_materialised,
            "governance_arrangements": self.governance_arrangements,
            "complaint_mechanism": self.complaint_mechanism,
            "details": self.details,
            "missing_elements": self.missing_elements(),
            "is_complete": self.is_complete,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "completed_at": _iso(self.completed_at),
            "authority_notified_at": _iso(self.authority_notified_at),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FundamentalRightsImpactAssessment:
        """Reconstruct a FRIA from its :meth:`to_dict` payload (round-trip)."""
        return cls(
            system_id=data["system_id"],
            deployer=data["deployer"],
            processes_description=data.get("processes_description", ""),
            usage_period=data.get("usage_period", ""),
            usage_frequency=data.get("usage_frequency", ""),
            affected_categories=list(data.get("affected_categories", [])),
            risks=[FriaRisk.from_dict(r) for r in data.get("risks", [])],
            human_oversight_measures=data.get("human_oversight_measures", ""),
            measures_if_materialised=data.get("measures_if_materialised", ""),
            governance_arrangements=data.get("governance_arrangements", ""),
            complaint_mechanism=data.get("complaint_mechanism", ""),
            details=dict(data.get("details", {})),
            id=data["id"],
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            completed_at=_parse_dt(data.get("completed_at")),
            authority_notified_at=_parse_dt(data.get("authority_notified_at")),
        )


__all__ = ["FriaRisk", "FundamentalRightsImpactAssessment"]
