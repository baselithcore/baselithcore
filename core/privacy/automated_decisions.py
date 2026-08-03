"""GDPR Art. 22 automated decision-making.

Art. 22(1) gives the data subject the right **not to be subject** to a decision
based solely on automated processing — profiling included — which produces legal
effects or similarly significantly affects them. It is a prohibition with three
exceptions (Art. 22(2)): necessity for a contract, authorisation by Union or
Member State law, or explicit consent.

Two of those three do not stand on their own. Art. 22(3) requires, for the
contract and consent grounds, that the controller implement **suitable measures
to safeguard** the subject's rights — at minimum the right to obtain **human
intervention**, to **express a point of view**, and to **contest** the decision.
The legal-authorisation ground instead needs the safeguards that law itself lays
down.

Alongside that sit the transparency duties: Art. 13(2)(f), 14(2)(g) and
15(1)(h) require *meaningful information about the logic involved* and the
significance and envisaged consequences of the processing. In an agentic system
this is the obligation most likely to be assumed rather than written.

This module records the policy per decision-making activity: what the decision
is, whether it is solely automated, which Art. 22(2) ground is claimed, which
safeguards actually exist and how they are reached. It is a **declaration**, not
a detector — whether a decision "significantly affects" someone is a legal
judgement about its consequences, not a property visible at runtime. What the
record buys is that the judgement was made, written down, and can be audited.

An activity whose safeguards are incomplete is reported as such rather than
being quietly treated as compliant.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4

from core.observability.audit import AuditEventType, audit_emit
from core.observability.logging import get_logger

logger = get_logger(__name__)


class Art22Ground(str, Enum):
    """The Art. 22(2) grounds permitting a solely automated decision."""

    #: (a) necessary for entering into, or performance of, a contract.
    CONTRACT = "contract"
    #: (b) authorised by Union or Member State law laying down safeguards.
    LEGAL_AUTHORISATION = "legal_authorisation"
    #: (c) based on the data subject's explicit consent.
    EXPLICIT_CONSENT = "explicit_consent"


@dataclass
class AutomatedDecisionActivity:
    """One automated decision-making activity and its Art. 22 posture."""

    name: str
    description: str = ""
    #: Art. 22(1): is there meaningful human involvement in the decision?
    #: A human who rubber-stamps an output is not involvement — the question is
    #: whether a person with authority and competence actually decides.
    solely_automated: bool = True
    #: Art. 22(1): does it produce legal effects or similarly significantly
    #: affect the person?
    legal_or_significant_effect: bool = True
    involves_profiling: bool = False
    #: Art. 22(4): decisions on Art. 9 special categories are barred unless
    #: 9(2)(a) explicit consent or 9(2)(g) substantial public interest applies,
    #: with suitable safeguards in place.
    uses_special_categories: bool = False
    special_category_ground: str = ""
    ground: Art22Ground | None = None
    #: Art. 22(3) safeguards, and where the subject actually reaches them.
    human_intervention_channel: str = ""
    contest_channel: str = ""
    express_view_channel: str = ""
    #: Art. 13(2)(f) / 14(2)(g) / 15(1)(h) transparency.
    logic_explanation: str = ""
    significance_and_consequences: str = ""
    #: Cross-links to the AI system and the DPIA covering this activity.
    ai_system_id: str | None = None
    dpia_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid4().hex)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    reviewed_at: float | None = None

    @property
    def in_scope(self) -> bool:
        """Whether Art. 22(1) applies to this activity at all.

        Both conditions must hold: the decision is *solely* automated **and** it
        produces legal or similarly significant effects. Fail either and the
        article does not bite — though the transparency duties of Art. 13/14
        may still apply to the processing.
        """
        return self.solely_automated and self.legal_or_significant_effect

    @property
    def requires_art22_3_safeguards(self) -> bool:
        """Whether the Art. 22(3) minimum safeguards are owed.

        Owed on the contract and explicit-consent grounds. The legal-
        authorisation ground defers to the safeguards that law provides, which
        this module cannot verify — it records the ground instead.
        """
        return self.in_scope and self.ground in (
            Art22Ground.CONTRACT,
            Art22Ground.EXPLICIT_CONSENT,
        )

    def missing_elements(self) -> list[str]:
        """Return the Art. 22 elements this activity is still missing."""
        missing: list[str] = []
        if not self.in_scope:
            # Out of Art. 22 scope, but the logic still has to be explainable
            # wherever Art. 13/14 transparency applies.
            if not self.description:
                missing.append("description of the decision-making activity")
            return missing
        if self.ground is None:
            missing.append(
                "Art. 22(2) — no ground claimed; a solely automated decision "
                "with significant effects is prohibited without one"
            )
        if self.requires_art22_3_safeguards:
            if not self.human_intervention_channel:
                missing.append("Art. 22(3) — right to obtain human intervention")
            if not self.express_view_channel:
                missing.append("Art. 22(3) — right to express a point of view")
            if not self.contest_channel:
                missing.append("Art. 22(3) — right to contest the decision")
        if self.uses_special_categories and not self.special_category_ground:
            missing.append(
                "Art. 22(4) — Art. 9(2)(a) or 9(2)(g) ground for deciding on "
                "special categories of data"
            )
        if not self.logic_explanation:
            missing.append(
                "Art. 13(2)(f)/14(2)(g)/15(1)(h) — meaningful information about "
                "the logic involved"
            )
        if not self.significance_and_consequences:
            missing.append(
                "Art. 13(2)(f)/14(2)(g)/15(1)(h) — significance and envisaged "
                "consequences"
            )
        return missing

    @property
    def is_compliant(self) -> bool:
        """Whether the recorded posture covers every applicable Art. 22 element.

        "Compliant" here means the *declaration* is complete — the safeguards
        are recorded and reachable. Whether they work is a matter for the people
        who operate them.
        """
        return not self.missing_elements()

    def subject_information(self) -> dict[str, Any]:
        """The Art. 15(1)(h) disclosure to hand a data subject on request."""
        return {
            "activity": self.name,
            "solely_automated": self.solely_automated,
            "profiling": self.involves_profiling,
            "significant_effect": self.legal_or_significant_effect,
            "logic": self.logic_explanation,
            "significance_and_consequences": self.significance_and_consequences,
            "rights": {
                "human_intervention": self.human_intervention_channel,
                "express_point_of_view": self.express_view_channel,
                "contest_decision": self.contest_channel,
            },
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "solely_automated": self.solely_automated,
            "legal_or_significant_effect": self.legal_or_significant_effect,
            "involves_profiling": self.involves_profiling,
            "uses_special_categories": self.uses_special_categories,
            "special_category_ground": self.special_category_ground,
            "ground": self.ground.value if self.ground else None,
            "human_intervention_channel": self.human_intervention_channel,
            "contest_channel": self.contest_channel,
            "express_view_channel": self.express_view_channel,
            "logic_explanation": self.logic_explanation,
            "significance_and_consequences": self.significance_and_consequences,
            "ai_system_id": self.ai_system_id,
            "dpia_id": self.dpia_id,
            "details": self.details,
            "in_scope": self.in_scope,
            "requires_art22_3_safeguards": self.requires_art22_3_safeguards,
            "missing_elements": self.missing_elements(),
            "is_compliant": self.is_compliant,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "reviewed_at": self.reviewed_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AutomatedDecisionActivity:
        """Reconstruct an activity from its :meth:`to_dict` payload."""
        ground = data.get("ground")
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            solely_automated=data.get("solely_automated", True),
            legal_or_significant_effect=data.get("legal_or_significant_effect", True),
            involves_profiling=data.get("involves_profiling", False),
            uses_special_categories=data.get("uses_special_categories", False),
            special_category_ground=data.get("special_category_ground", ""),
            ground=Art22Ground(ground) if ground else None,
            human_intervention_channel=data.get("human_intervention_channel", ""),
            contest_channel=data.get("contest_channel", ""),
            express_view_channel=data.get("express_view_channel", ""),
            logic_explanation=data.get("logic_explanation", ""),
            significance_and_consequences=data.get("significance_and_consequences", ""),
            ai_system_id=data.get("ai_system_id"),
            dpia_id=data.get("dpia_id"),
            details=dict(data.get("details", {})),
            id=data["id"],
            created_at=data.get("created_at", 0.0),
            updated_at=data.get("updated_at", 0.0),
            reviewed_at=data.get("reviewed_at"),
        )


class AutomatedDecisionRegistry:
    """In-process registry of Art. 22 decision-making activities."""

    def __init__(self) -> None:
        self._activities: dict[str, AutomatedDecisionActivity] = {}

    def register(
        self, activity: AutomatedDecisionActivity
    ) -> AutomatedDecisionActivity:
        """Record an activity and audit whether its Art. 22 posture is complete."""
        activity.updated_at = time.time()
        self._activities[activity.id] = activity
        if activity.in_scope and not activity.is_compliant:
            logger.warning(
                "AUDIT | PRIVACY | Art. 22 activity missing safeguards | "
                "activity=%s missing=%s",
                activity.name,
                activity.missing_elements(),
            )
        audit_emit(
            AuditEventType.PRIVACY_OBJECT,
            resource=activity.id,
            action="automated_decision_registered",
            success=activity.is_compliant,
            details={
                "name": activity.name,
                "in_scope": activity.in_scope,
                "ground": activity.ground.value if activity.ground else None,
                "missing_elements": activity.missing_elements(),
            },
        )
        return activity

    def get(self, activity_id: str) -> AutomatedDecisionActivity | None:
        return self._activities.get(activity_id)

    def by_name(self, name: str) -> AutomatedDecisionActivity | None:
        return next((a for a in self._activities.values() if a.name == name), None)

    def all(self) -> list[AutomatedDecisionActivity]:
        return list(self._activities.values())

    def in_scope(self) -> list[AutomatedDecisionActivity]:
        """Activities Art. 22(1) actually applies to."""
        return [a for a in self._activities.values() if a.in_scope]

    def non_compliant(self) -> list[AutomatedDecisionActivity]:
        """In-scope activities whose Art. 22 posture is incomplete."""
        return [a for a in self.in_scope() if not a.is_compliant]


_registry: AutomatedDecisionRegistry | None = None


def get_automated_decision_registry() -> AutomatedDecisionRegistry:
    """Get or create the global Art. 22 activity registry."""
    global _registry
    if _registry is None:
        _registry = AutomatedDecisionRegistry()
    return _registry


def reset_automated_decision_registry() -> None:
    """Drop the cached registry (tests, and reconfiguration)."""
    global _registry
    _registry = None


__all__ = [
    "Art22Ground",
    "AutomatedDecisionActivity",
    "AutomatedDecisionRegistry",
    "get_automated_decision_registry",
    "reset_automated_decision_registry",
]
