"""Art. 6 risk classification for a registered AI system.

Deriving the risk category is the pivot of every other obligation, and the rules
have a specific shape that is easy to get subtly wrong:

1. **Art. 5 wins first.** A declared prohibited practice makes the system
   ``PROHIBITED`` regardless of anything else — there is no high-risk path for
   a banned practice.
2. **Annex I** — a system used as the safety component of a product covered by
   the Union harmonisation legislation in Annex I is high-risk (Art. 6(1)).
3. **Annex III** — a system in one of the eight listed areas is high-risk
   (Art. 6(2)) **unless** the Art. 6(3) derogation applies.
4. **The derogation is defeated by profiling.** Art. 6(3), last subparagraph:
   a system that performs profiling of natural persons is *always* high-risk,
   whatever derogation ground is claimed.
5. **GPAI is a separate axis** (Chapter V), not a rung on the risk ladder — a
   general-purpose model carries Art. 53 duties, and Art. 55 on top when it
   presents systemic risk.
6. Everything else falls to ``LIMITED_RISK`` when it interacts with people or
   emits synthetic content (Art. 50 transparency duties), else ``MINIMAL_RISK``.

The derived category is *advisory*: the operator remains responsible for the
determination, and :class:`~core.compliance.types.AiSystem` stores whatever was
actually asserted. What the framework guarantees is that the assertion is
recorded, reasoned, and auditable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.compliance.prohibited import ProhibitedPractice
from core.compliance.types import AiSystem, RiskCategory
from core.observability.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ClassificationResult:
    """A derived risk category together with the reasoning that produced it."""

    category: RiskCategory
    rationale: str
    citations: list[str] = field(default_factory=list)
    #: Art. 6(4): claiming the derogation obliges documenting the assessment.
    derogation_claimed: bool = False
    requires_registration: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category.value,
            "rationale": self.rationale,
            "citations": list(self.citations),
            "derogation_claimed": self.derogation_claimed,
            "requires_registration": self.requires_registration,
        }


def classify_system(
    system: AiSystem,
    *,
    prohibited_practices: list[ProhibitedPractice] | None = None,
) -> ClassificationResult:
    """Derive the Art. 6 risk category for ``system``.

    ``prohibited_practices`` carries the Art. 5 declaration, which is screened
    separately by :mod:`core.compliance.prohibited` — passing it here only makes
    the resulting category consistent with that screening.
    """
    if prohibited_practices:
        return ClassificationResult(
            category=RiskCategory.PROHIBITED,
            rationale=(
                "A practice banned by Art. 5 was declared; the system cannot be "
                "placed on the market or put into service in the Union."
            ),
            citations=["Art. 5(1)"],
        )

    if system.is_gpai_model:
        systemic = system.gpai_systemic_risk
        return ClassificationResult(
            category=(
                RiskCategory.GPAI_SYSTEMIC_RISK if systemic else RiskCategory.GPAI
            ),
            rationale=(
                "General-purpose AI model with systemic risk — Art. 53 duties "
                "plus the Art. 55 obligations."
                if systemic
                else "General-purpose AI model — Chapter V Art. 53 duties."
            ),
            citations=["Art. 55" if systemic else "Art. 53"],
        )

    if system.annex_i_product:
        return ClassificationResult(
            category=RiskCategory.HIGH_RISK,
            rationale=(
                "Safety component of a product covered by the Union "
                "harmonisation legislation listed in Annex I."
            ),
            citations=["Art. 6(1)", "Annex I"],
            requires_registration=True,
        )

    if system.annex_iii_areas:
        areas = ", ".join(a.value for a in system.annex_iii_areas)
        if system.art6_derogations and not system.performs_profiling:
            grounds = ", ".join(d.value for d in system.art6_derogations)
            return ClassificationResult(
                category=RiskCategory.LIMITED_RISK
                if system.interacts_with_humans or system.generates_synthetic_content
                else RiskCategory.MINIMAL_RISK,
                rationale=(
                    f"In Annex III area(s) {areas}, but the Art. 6(3) derogation "
                    f"is claimed on the ground(s) {grounds}. The assessment must "
                    "be documented (Art. 6(4)) and the system still registered "
                    "(Art. 49(2))."
                ),
                citations=["Art. 6(3)", "Art. 6(4)", "Art. 49(2)"],
                derogation_claimed=True,
                requires_registration=True,
            )
        if system.art6_derogations and system.performs_profiling:
            return ClassificationResult(
                category=RiskCategory.HIGH_RISK,
                rationale=(
                    f"In Annex III area(s) {areas}. The claimed Art. 6(3) "
                    "derogation does not apply: the system performs profiling of "
                    "natural persons, which is always high-risk."
                ),
                citations=["Art. 6(2)", "Art. 6(3) last subparagraph", "Annex III"],
                requires_registration=True,
            )
        return ClassificationResult(
            category=RiskCategory.HIGH_RISK,
            rationale=f"In Annex III area(s) {areas}.",
            citations=["Art. 6(2)", "Annex III"],
            requires_registration=True,
        )

    if system.interacts_with_humans or system.generates_synthetic_content:
        return ClassificationResult(
            category=RiskCategory.LIMITED_RISK,
            rationale=(
                "Interacts with natural persons and/or emits synthetic content: "
                "the Art. 50 transparency duties apply."
            ),
            citations=["Art. 50"],
        )

    return ClassificationResult(
        category=RiskCategory.MINIMAL_RISK,
        rationale=(
            "Outside Annex I and Annex III, no Art. 50 trigger: no specific "
            "obligation beyond the voluntary codes of conduct of Art. 95."
        ),
        citations=["Art. 95"],
    )


def obligations_for(category: RiskCategory) -> list[str]:
    """Return the headline obligations that attach to ``category``.

    A checklist, not legal advice — it exists so a registered system can render
    "what do we owe for this?" without an operator reconstructing it by hand.
    """
    if category is RiskCategory.PROHIBITED:
        return ["Art. 5 — the practice is banned; the system must not be placed."]
    if category is RiskCategory.HIGH_RISK:
        return [
            "Art. 9 — risk management system across the lifecycle",
            "Art. 10 — data governance and bias examination of training data",
            "Art. 11 + Annex IV — technical documentation",
            "Art. 12 — automatic logging of events",
            "Art. 13 — transparency and instructions for use for the deployer",
            "Art. 14 — human oversight measures",
            "Art. 15 — accuracy, robustness and cybersecurity",
            "Art. 17 — quality management system",
            "Art. 19 / Art. 26(6) — retain the automatic logs for ≥ 6 months",
            "Art. 27 — fundamental rights impact assessment (deployers in scope)",
            "Art. 43/47/48 — conformity assessment, EU declaration, CE marking",
            "Art. 49 — registration in the EU database",
            "Art. 72 — post-market monitoring plan",
            "Art. 73 — serious incident reporting",
        ]
    if category is RiskCategory.GPAI:
        return [
            "Art. 53(1)(a) — technical documentation of the model",
            "Art. 53(1)(b) — information for downstream providers",
            "Art. 53(1)(c) — copyright policy, including TDM opt-out compliance",
            "Art. 53(1)(d) — public summary of the training content",
        ]
    if category is RiskCategory.GPAI_SYSTEMIC_RISK:
        return [
            *obligations_for(RiskCategory.GPAI),
            "Art. 55(1)(a) — model evaluation, including adversarial testing",
            "Art. 55(1)(b) — assess and mitigate systemic risks",
            "Art. 55(1)(c) — track and report serious incidents to the AI Office",
            "Art. 55(1)(d) — adequate cybersecurity protection",
        ]
    if category is RiskCategory.LIMITED_RISK:
        return [
            "Art. 50(1) — inform people they are interacting with an AI system",
            "Art. 50(2)/(4) — mark synthetic content in a machine-readable way",
        ]
    return ["Art. 95 — voluntary codes of conduct only."]


__all__ = ["ClassificationResult", "classify_system", "obligations_for"]
