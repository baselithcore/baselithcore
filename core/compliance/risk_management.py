"""Art. 9 risk management system.

Art. 9 is the obligation most easily mistaken for something the runtime already
does. `core/world_model/risk_assessor.py` scores the risk of an *action* the
agent is about to take — useful, and a different thing entirely. Art. 9 requires
a **continuous iterative process planned and run across the whole lifecycle** of
a high-risk AI system, systematically reviewed and updated, comprising:

* **9(2)(a)** — identification and analysis of the known and reasonably
  foreseeable risks the system can pose to health, safety or fundamental rights
  when used for its intended purpose;
* **9(2)(b)** — estimation and evaluation of the risks that may emerge under
  intended use **and under conditions of reasonably foreseeable misuse**;
* **9(2)(c)** — evaluation of risks emerging from the post-market monitoring
  data (Art. 72), which is why a plan and a risk file must reference each other;
* **9(2)(d)** — adoption of appropriate and targeted risk management measures.

Art. 9(5) then bounds what "managed" means: residual risk must be judged
acceptable, after elimination or reduction as far as technically feasible,
mitigation where elimination is not possible, and information plus training for
deployers. Art. 9(9) adds specific consideration of impacts on persons under 18
and other vulnerable groups.

This module models that file so it is **auditable and checkable**: each risk
carries its analysis, its measures, and its residual evaluation, and the system
as a whole reports which Art. 9 elements are still missing. Section 5 of the
Annex IV technical documentation is generated from it.

The judgement — is this residual risk acceptable? — stays human. What the code
refuses to do is let an unreviewed, half-populated risk file pass for one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any
from uuid import uuid4

from core.compliance.types import _iso, _parse_dt, _utcnow


class RiskSeverity(str, Enum):
    """Severity of the harm a risk would cause if it materialised."""

    NEGLIGIBLE = "negligible"
    MINOR = "minor"
    MODERATE = "moderate"
    MAJOR = "major"
    CATASTROPHIC = "catastrophic"


class RiskLikelihood(str, Enum):
    """How likely the risk is to materialise under intended use."""

    RARE = "rare"
    UNLIKELY = "unlikely"
    POSSIBLE = "possible"
    LIKELY = "likely"
    ALMOST_CERTAIN = "almost_certain"


class RiskTreatment(str, Enum):
    """The Art. 9(5) hierarchy of what was done about the risk.

    The order is not a menu: elimination first, mitigation only where
    elimination is not technically feasible, information/training last.
    """

    ELIMINATED = "eliminated"
    REDUCED = "reduced"
    MITIGATED = "mitigated"
    INFORMATION_AND_TRAINING = "information_and_training"
    ACCEPTED = "accepted"


class HarmCategory(str, Enum):
    """What the risk threatens — the Art. 9(2)(a) triad, plus the environment."""

    HEALTH = "health"
    SAFETY = "safety"
    FUNDAMENTAL_RIGHTS = "fundamental_rights"
    ENVIRONMENT = "environment"


@dataclass
class IdentifiedRisk:
    """One risk in the Art. 9 file, with its analysis and its treatment."""

    description: str
    harm_categories: list[HarmCategory] = field(default_factory=list)
    #: Art. 9(2)(b): does this arise under intended use, foreseeable misuse, or
    #: both? A file with no misuse entries has skipped half the article.
    under_intended_use: bool = True
    under_foreseeable_misuse: bool = False
    #: Art. 9(2)(c): surfaced by post-market monitoring rather than up front.
    from_post_market_data: bool = False
    severity: RiskSeverity = RiskSeverity.MODERATE
    likelihood: RiskLikelihood = RiskLikelihood.POSSIBLE
    #: Art. 9(9): specific consideration of minors and other vulnerable groups.
    affects_vulnerable_groups: bool = False
    affected_groups: list[str] = field(default_factory=list)
    #: Art. 9(2)(d) / 9(5): the measures adopted, and what they achieved.
    treatment: RiskTreatment | None = None
    measures: str = ""
    residual_severity: RiskSeverity | None = None
    residual_likelihood: RiskLikelihood | None = None
    #: Art. 9(5): the acceptability judgement, and who made it.
    residual_accepted: bool = False
    accepted_by: str | None = None
    accepted_at: datetime | None = None
    #: Art. 9(8): testing that verified the measures work.
    verification: str = ""
    id: str = field(default_factory=lambda: uuid4().hex)

    @property
    def is_treated(self) -> bool:
        """Whether a treatment and its measures have been recorded."""
        return self.treatment is not None and bool(self.measures)

    @property
    def is_closed(self) -> bool:
        """Whether the risk is treated, verified and its residual accepted.

        Acceptance without verification does not close a risk: Art. 9(8)
        requires testing that the measures actually perform.
        """
        return self.is_treated and self.residual_accepted and bool(self.verification)

    def gaps(self) -> list[str]:
        """The Art. 9 elements this risk entry is still missing."""
        missing: list[str] = []
        if not self.harm_categories:
            missing.append("Art. 9(2)(a) — what the risk threatens")
        if not self.treatment or not self.measures:
            missing.append("Art. 9(2)(d) — risk management measures")
        if self.residual_severity is None or self.residual_likelihood is None:
            missing.append("Art. 9(5) — residual risk evaluation")
        if not self.residual_accepted:
            missing.append("Art. 9(5) — residual risk acceptance")
        elif not self.accepted_by:
            missing.append("Art. 9(5) — who accepted the residual risk")
        if not self.verification:
            missing.append("Art. 9(8) — testing that the measures perform")
        return missing

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "harm_categories": [h.value for h in self.harm_categories],
            "under_intended_use": self.under_intended_use,
            "under_foreseeable_misuse": self.under_foreseeable_misuse,
            "from_post_market_data": self.from_post_market_data,
            "severity": self.severity.value,
            "likelihood": self.likelihood.value,
            "affects_vulnerable_groups": self.affects_vulnerable_groups,
            "affected_groups": list(self.affected_groups),
            "treatment": self.treatment.value if self.treatment else None,
            "measures": self.measures,
            "residual_severity": (
                self.residual_severity.value if self.residual_severity else None
            ),
            "residual_likelihood": (
                self.residual_likelihood.value if self.residual_likelihood else None
            ),
            "residual_accepted": self.residual_accepted,
            "accepted_by": self.accepted_by,
            "accepted_at": _iso(self.accepted_at),
            "verification": self.verification,
            "is_treated": self.is_treated,
            "is_closed": self.is_closed,
            "gaps": self.gaps(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> IdentifiedRisk:
        """Reconstruct a risk entry from its :meth:`to_dict` payload."""
        treatment = data.get("treatment")
        residual_severity = data.get("residual_severity")
        residual_likelihood = data.get("residual_likelihood")
        return cls(
            description=data["description"],
            harm_categories=[HarmCategory(h) for h in data.get("harm_categories", [])],
            under_intended_use=data.get("under_intended_use", True),
            under_foreseeable_misuse=data.get("under_foreseeable_misuse", False),
            from_post_market_data=data.get("from_post_market_data", False),
            severity=RiskSeverity(data.get("severity", RiskSeverity.MODERATE.value)),
            likelihood=RiskLikelihood(
                data.get("likelihood", RiskLikelihood.POSSIBLE.value)
            ),
            affects_vulnerable_groups=data.get("affects_vulnerable_groups", False),
            affected_groups=list(data.get("affected_groups", [])),
            treatment=RiskTreatment(treatment) if treatment else None,
            measures=data.get("measures", ""),
            residual_severity=(
                RiskSeverity(residual_severity) if residual_severity else None
            ),
            residual_likelihood=(
                RiskLikelihood(residual_likelihood) if residual_likelihood else None
            ),
            residual_accepted=data.get("residual_accepted", False),
            accepted_by=data.get("accepted_by"),
            accepted_at=_parse_dt(data.get("accepted_at")),
            verification=data.get("verification", ""),
            id=data["id"],
        )


@dataclass
class RiskManagementSystem:
    """The Art. 9 risk management file for one high-risk AI system."""

    system_id: str
    #: Art. 9(1): the process itself — how risk management is planned and run.
    process_description: str = ""
    #: Art. 9(2)(a)/(b): the scope the analysis covered.
    intended_purpose: str = ""
    foreseeable_misuse: str = ""
    risks: list[IdentifiedRisk] = field(default_factory=list)
    #: Art. 9(5): information and training provided to deployers.
    deployer_information: str = ""
    #: Art. 9(6)/(8): the testing regime and its acceptance criteria.
    testing_regime: str = ""
    #: Art. 9(1): "regularly systematically reviewed and updated".
    review_cadence_days: int = 180
    responsible_contacts: list[str] = field(default_factory=list)
    #: Cross-links: the Art. 72 plan feeding 9(2)(c), and the FRIA if any.
    post_market_plan_id: str | None = None
    fria_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid4().hex)
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)
    last_reviewed_at: datetime | None = None

    @property
    def open_risks(self) -> list[IdentifiedRisk]:
        """Risks that are not yet treated, verified and accepted."""
        return [r for r in self.risks if not r.is_closed]

    @property
    def covers_foreseeable_misuse(self) -> bool:
        """Whether any risk was analysed under reasonably foreseeable misuse."""
        return any(r.under_foreseeable_misuse for r in self.risks)

    def review_due_at(self) -> datetime | None:
        """When the next systematic review falls due, or ``None`` if never done."""
        if self.last_reviewed_at is None:
            return None
        return self.last_reviewed_at + timedelta(days=self.review_cadence_days)

    def is_review_overdue(self, now: datetime | None = None) -> bool:
        """Whether the file is past its review cadence.

        A file that has *never* been reviewed counts as overdue once its cadence
        has elapsed since creation — Art. 9(1) asks for a continuous iterative
        process, and "we wrote it once" is not that.
        """
        moment = now or _utcnow()
        due = self.review_due_at()
        if due is None:
            return moment > self.created_at + timedelta(days=self.review_cadence_days)
        return moment > due

    def missing_elements(self) -> list[str]:
        """Return the Art. 9 elements the file is still missing."""
        missing: list[str] = []
        if not self.process_description:
            missing.append("Art. 9(1) — description of the risk management process")
        if not self.intended_purpose:
            missing.append("Art. 9(2)(a) — intended purpose the analysis covered")
        if not self.risks:
            missing.append("Art. 9(2)(a) — identified risks")
        if not self.foreseeable_misuse:
            missing.append("Art. 9(2)(b) — reasonably foreseeable misuse")
        elif not self.covers_foreseeable_misuse:
            missing.append(
                "Art. 9(2)(b) — no risk was analysed under foreseeable misuse"
            )
        if not self.testing_regime:
            missing.append("Art. 9(6)/(8) — testing regime and acceptance criteria")
        if not self.deployer_information:
            missing.append("Art. 9(5) — information and training for deployers")
        if not self.responsible_contacts:
            missing.append("Art. 9(1) — accountable owner for the process")
        if self.post_market_plan_id is None:
            missing.append(
                "Art. 9(2)(c) — link to the post-market monitoring data feeding "
                "the risk evaluation"
            )
        for risk in self.risks:
            missing.extend(f"{risk.description}: {gap}" for gap in risk.gaps())
        return missing

    @property
    def is_complete(self) -> bool:
        """Whether every Art. 9 element carries content and every risk is closed."""
        return not self.missing_elements()

    def to_markdown(self) -> str:
        """Render the file as Annex IV section 5 content."""
        lines = [
            f"**Process** — {self.process_description or 'not documented'}",
            "",
            f"**Intended purpose** — {self.intended_purpose or 'not documented'}",
            "",
            f"**Reasonably foreseeable misuse** — "
            f"{self.foreseeable_misuse or 'not documented'}",
            "",
            f"**Testing regime** — {self.testing_regime or 'not documented'}",
            "",
            f"**Information and training for deployers** — "
            f"{self.deployer_information or 'not documented'}",
            "",
            f"**Review cadence** — every {self.review_cadence_days} days; last "
            f"reviewed {_iso(self.last_reviewed_at) or 'never'}",
            "",
            "**Identified risks**",
            "",
        ]
        if not self.risks:
            lines.append("> **No risks identified.**")
        for risk in self.risks:
            harms = ", ".join(h.value for h in risk.harm_categories) or "unspecified"
            residual = (
                f"{risk.residual_severity.value}/{risk.residual_likelihood.value}"
                if risk.residual_severity and risk.residual_likelihood
                else "not evaluated"
            )
            lines.extend(
                [
                    f"- **{risk.description}** ({harms})",
                    f"    - Inherent: {risk.severity.value} / {risk.likelihood.value}",
                    f"    - Treatment: "
                    f"{risk.treatment.value if risk.treatment else 'none'} — "
                    f"{risk.measures or 'no measures recorded'}",
                    f"    - Residual: {residual}; accepted: {risk.residual_accepted}",
                    f"    - Verification: {risk.verification or 'none recorded'}",
                ]
            )
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "system_id": self.system_id,
            "process_description": self.process_description,
            "intended_purpose": self.intended_purpose,
            "foreseeable_misuse": self.foreseeable_misuse,
            "risks": [r.to_dict() for r in self.risks],
            "deployer_information": self.deployer_information,
            "testing_regime": self.testing_regime,
            "review_cadence_days": self.review_cadence_days,
            "responsible_contacts": list(self.responsible_contacts),
            "post_market_plan_id": self.post_market_plan_id,
            "fria_id": self.fria_id,
            "details": self.details,
            "open_risks": [r.id for r in self.open_risks],
            "covers_foreseeable_misuse": self.covers_foreseeable_misuse,
            "missing_elements": self.missing_elements(),
            "is_complete": self.is_complete,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "last_reviewed_at": _iso(self.last_reviewed_at),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RiskManagementSystem:
        """Reconstruct the file from its :meth:`to_dict` payload (round-trip)."""
        return cls(
            system_id=data["system_id"],
            process_description=data.get("process_description", ""),
            intended_purpose=data.get("intended_purpose", ""),
            foreseeable_misuse=data.get("foreseeable_misuse", ""),
            risks=[IdentifiedRisk.from_dict(r) for r in data.get("risks", [])],
            deployer_information=data.get("deployer_information", ""),
            testing_regime=data.get("testing_regime", ""),
            review_cadence_days=data.get("review_cadence_days", 180),
            responsible_contacts=list(data.get("responsible_contacts", [])),
            post_market_plan_id=data.get("post_market_plan_id"),
            fria_id=data.get("fria_id"),
            details=dict(data.get("details", {})),
            id=data["id"],
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            last_reviewed_at=_parse_dt(data.get("last_reviewed_at")),
        )


__all__ = [
    "HarmCategory",
    "IdentifiedRisk",
    "RiskLikelihood",
    "RiskManagementSystem",
    "RiskSeverity",
    "RiskTreatment",
]
