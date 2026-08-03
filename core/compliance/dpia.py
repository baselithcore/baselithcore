"""GDPR Art. 35/36 data protection impact assessment.

A DPIA and an Art. 27 FRIA are neighbours, not duplicates, and conflating them
is how one of the two ends up unperformed. The DPIA protects **personal data**
and is owed by the *controller* to the supervisory authority; the FRIA protects
**fundamental rights** broadly and is owed by certain *deployers* of high-risk
AI systems. A system can need both, one, or neither.

Art. 35(1) requires a DPIA where processing is likely to result in a high risk,
in particular using new technologies. Art. 35(3) lists three cases where one is
required *in particular*, and the first of them — systematic and extensive
automated evaluation producing legal or similarly significant effects — is what
most agentic deployments touching personal data actually do.

Art. 35(7) fixes the content in four elements, modelled here so completeness is
checkable. Two procedural duties travel with it: Art. 35(2) advice from the DPO,
and Art. 35(9) seeking the views of data subjects where appropriate. And when
the assessment indicates a high residual risk that mitigation does not bring
down, Art. 36(1) requires **prior consultation** with the supervisory authority
*before* processing starts — the one deadline in this file that can invalidate a
launch, which is why it is tracked rather than assumed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from core.compliance.types import _iso, _parse_dt, _utcnow


class DpiaTrigger(str, Enum):
    """Why a DPIA is required — the Art. 35(1)/(3) grounds."""

    #: Art. 35(3)(a) — systematic and extensive automated evaluation, including
    #: profiling, producing legal or similarly significant effects.
    AUTOMATED_EVALUATION = "automated_evaluation"
    #: Art. 35(3)(b) — large-scale processing of Art. 9 special categories or
    #: Art. 10 criminal-conviction data.
    SPECIAL_CATEGORIES_AT_SCALE = "special_categories_at_scale"
    #: Art. 35(3)(c) — systematic monitoring of a publicly accessible area on a
    #: large scale.
    PUBLIC_AREA_MONITORING = "public_area_monitoring"
    #: Art. 35(1) — high risk from new technologies, or a national authority's
    #: Art. 35(4) list.
    NEW_TECHNOLOGY = "new_technology"
    SUPERVISORY_AUTHORITY_LIST = "supervisory_authority_list"


@dataclass
class DpiaRisk:
    """A risk to the rights and freedoms of data subjects — Art. 35(7)(c)."""

    description: str
    affected_subjects: str = ""
    likelihood: str = ""
    severity: str = ""
    #: Art. 35(7)(d): the measures envisaged to address it.
    measures: str = ""
    residual_high_risk: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "description": self.description,
            "affected_subjects": self.affected_subjects,
            "likelihood": self.likelihood,
            "severity": self.severity,
            "measures": self.measures,
            "residual_high_risk": self.residual_high_risk,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DpiaRisk:
        return cls(
            description=data["description"],
            affected_subjects=data.get("affected_subjects", ""),
            likelihood=data.get("likelihood", ""),
            severity=data.get("severity", ""),
            measures=data.get("measures", ""),
            residual_high_risk=data.get("residual_high_risk", False),
        )


@dataclass
class DataProtectionImpactAssessment:
    """A DPIA for one processing operation, per Art. 35(7)(a)–(d)."""

    name: str
    controller: str = ""
    triggers: list[DpiaTrigger] = field(default_factory=list)
    # (a) systematic description of the processing and its purposes.
    processing_description: str = ""
    purposes: str = ""
    legitimate_interest: str = ""
    # (b) necessity and proportionality.
    necessity_assessment: str = ""
    proportionality_assessment: str = ""
    # (c) risks to the rights and freedoms of data subjects.
    risks: list[DpiaRisk] = field(default_factory=list)
    # (d) the measures, safeguards and mechanisms addressing them.
    safeguards: str = ""
    security_measures: str = ""
    #: Art. 35(2): the DPO's advice, where a DPO is designated.
    dpo_advice: str = ""
    #: Art. 35(9): the views of data subjects, where appropriate.
    data_subject_views: str = ""
    data_subject_views_not_sought_reason: str = ""
    #: Cross-links to the AI system and the ROPA entry this covers.
    ai_system_id: str | None = None
    processing_activity_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid4().hex)
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)
    completed_at: datetime | None = None
    #: Art. 36(1): prior consultation, when residual risk stays high.
    prior_consultation_at: datetime | None = None
    authority_response_at: datetime | None = None
    #: Art. 35(11): the review that keeps the assessment current.
    last_reviewed_at: datetime | None = None

    @property
    def has_residual_high_risk(self) -> bool:
        """Whether any risk remains high after the envisaged measures."""
        return any(r.residual_high_risk for r in self.risks)

    @property
    def requires_prior_consultation(self) -> bool:
        """Whether Art. 36(1) prior consultation is owed and not yet done."""
        return self.has_residual_high_risk and self.prior_consultation_at is None

    @property
    def may_start_processing(self) -> bool:
        """Whether processing may lawfully begin.

        Art. 36(1) is a *precondition*: with a high residual risk and no prior
        consultation, starting is unlawful — so this is a hard gate, not a
        reminder.
        """
        return self.completed_at is not None and not self.requires_prior_consultation

    def missing_elements(self) -> list[str]:
        """Return the Art. 35(7) elements that are still empty."""
        missing: list[str] = []
        if not self.controller:
            missing.append("Art. 35 — the controller performing the assessment")
        if not self.processing_description:
            missing.append("Art. 35(7)(a) — systematic description of the processing")
        if not self.purposes:
            missing.append("Art. 35(7)(a) — purposes of the processing")
        if not self.necessity_assessment:
            missing.append("Art. 35(7)(b) — necessity assessment")
        if not self.proportionality_assessment:
            missing.append("Art. 35(7)(b) — proportionality assessment")
        if not self.risks:
            missing.append("Art. 35(7)(c) — risks to rights and freedoms")
        else:
            missing.extend(
                f"Art. 35(7)(d) — measures for risk: {r.description}"
                for r in self.risks
                if not r.measures
            )
        if not self.safeguards:
            missing.append("Art. 35(7)(d) — safeguards addressing the risks")
        if not self.security_measures:
            missing.append("Art. 35(7)(d) — security measures")
        if not self.dpo_advice:
            missing.append("Art. 35(2) — advice of the data protection officer")
        if not self.data_subject_views and not self.data_subject_views_not_sought_reason:
            missing.append(
                "Art. 35(9) — views of data subjects, or the reason for not "
                "seeking them"
            )
        return missing

    @property
    def is_complete(self) -> bool:
        """Whether every Art. 35(7) element carries content."""
        return not self.missing_elements()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "controller": self.controller,
            "triggers": [t.value for t in self.triggers],
            "processing_description": self.processing_description,
            "purposes": self.purposes,
            "legitimate_interest": self.legitimate_interest,
            "necessity_assessment": self.necessity_assessment,
            "proportionality_assessment": self.proportionality_assessment,
            "risks": [r.to_dict() for r in self.risks],
            "safeguards": self.safeguards,
            "security_measures": self.security_measures,
            "dpo_advice": self.dpo_advice,
            "data_subject_views": self.data_subject_views,
            "data_subject_views_not_sought_reason": (
                self.data_subject_views_not_sought_reason
            ),
            "ai_system_id": self.ai_system_id,
            "processing_activity_id": self.processing_activity_id,
            "details": self.details,
            "has_residual_high_risk": self.has_residual_high_risk,
            "requires_prior_consultation": self.requires_prior_consultation,
            "may_start_processing": self.may_start_processing,
            "missing_elements": self.missing_elements(),
            "is_complete": self.is_complete,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "completed_at": _iso(self.completed_at),
            "prior_consultation_at": _iso(self.prior_consultation_at),
            "authority_response_at": _iso(self.authority_response_at),
            "last_reviewed_at": _iso(self.last_reviewed_at),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DataProtectionImpactAssessment:
        """Reconstruct the assessment from its :meth:`to_dict` payload."""
        return cls(
            name=data["name"],
            controller=data.get("controller", ""),
            triggers=[DpiaTrigger(t) for t in data.get("triggers", [])],
            processing_description=data.get("processing_description", ""),
            purposes=data.get("purposes", ""),
            legitimate_interest=data.get("legitimate_interest", ""),
            necessity_assessment=data.get("necessity_assessment", ""),
            proportionality_assessment=data.get("proportionality_assessment", ""),
            risks=[DpiaRisk.from_dict(r) for r in data.get("risks", [])],
            safeguards=data.get("safeguards", ""),
            security_measures=data.get("security_measures", ""),
            dpo_advice=data.get("dpo_advice", ""),
            data_subject_views=data.get("data_subject_views", ""),
            data_subject_views_not_sought_reason=data.get(
                "data_subject_views_not_sought_reason", ""
            ),
            ai_system_id=data.get("ai_system_id"),
            processing_activity_id=data.get("processing_activity_id"),
            details=dict(data.get("details", {})),
            id=data["id"],
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            completed_at=_parse_dt(data.get("completed_at")),
            prior_consultation_at=_parse_dt(data.get("prior_consultation_at")),
            authority_response_at=_parse_dt(data.get("authority_response_at")),
            last_reviewed_at=_parse_dt(data.get("last_reviewed_at")),
        )


__all__ = ["DataProtectionImpactAssessment", "DpiaRisk", "DpiaTrigger"]
