"""AI-system governance domain types (Regulation (EU) 2024/1689).

Every obligation in the AI Act is conditional on two facts an operator must be
able to state, per system: **what role do we play** (provider, deployer,
importer, distributor, authorised representative) and **what risk category does
the system fall into**. Without those two, "are we compliant?" has no answer —
Art. 11 technical documentation, Art. 27 FRIA, Art. 49 registration and Art. 73
incident reporting all attach to a *specific system in a specific category*.

This module models those facts. :mod:`core.compliance.registry` stores them,
:mod:`core.compliance.classification` derives the category, and the Annex IV /
FRIA / ROPA modules consume them.

Timestamps are timezone-aware UTC.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import uuid4


def _utcnow() -> datetime:
    """Current time as a timezone-aware UTC datetime."""
    return datetime.now(UTC)


def _parse_dt(value: str | None) -> datetime | None:
    """Parse an ISO-8601 string back to a datetime (``None`` passes through)."""
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _iso(value: datetime | None) -> str | None:
    """ISO-8601 render that passes ``None`` through."""
    return value.isoformat() if value is not None else None


class RiskCategory(str, Enum):
    """The AI Act risk tiers that determine which obligations apply."""

    #: Art. 5 — banned outright since 2 Feb 2025.
    PROHIBITED = "prohibited"
    #: Art. 6 + Annex I/III — the full Chapter III obligation set.
    HIGH_RISK = "high_risk"
    #: Art. 50 — transparency duties only (chatbots, synthetic content…).
    LIMITED_RISK = "limited_risk"
    #: No specific obligation beyond voluntary codes of conduct (Art. 95).
    MINIMAL_RISK = "minimal_risk"
    #: Chapter V — general-purpose AI model obligations (Art. 53).
    GPAI = "gpai"
    #: Chapter V Section 3 — GPAI with systemic risk (Art. 55).
    GPAI_SYSTEMIC_RISK = "gpai_systemic_risk"


class OperatorRole(str, Enum):
    """The Art. 3 operator roles. Obligations differ sharply between them."""

    PROVIDER = "provider"
    DEPLOYER = "deployer"
    IMPORTER = "importer"
    DISTRIBUTOR = "distributor"
    AUTHORISED_REPRESENTATIVE = "authorised_representative"
    PRODUCT_MANUFACTURER = "product_manufacturer"


class AnnexIIIArea(str, Enum):
    """The eight Annex III areas that make a system high-risk under Art. 6(2)."""

    BIOMETRICS = "biometrics"
    CRITICAL_INFRASTRUCTURE = "critical_infrastructure"
    EDUCATION = "education_and_vocational_training"
    EMPLOYMENT = "employment_and_worker_management"
    ESSENTIAL_SERVICES = "essential_private_and_public_services"
    LAW_ENFORCEMENT = "law_enforcement"
    MIGRATION = "migration_asylum_and_border_control"
    JUSTICE_AND_DEMOCRACY = "administration_of_justice_and_democratic_processes"


class Art6Derogation(str, Enum):
    """The Art. 6(3) grounds for an Annex III system *not* being high-risk.

    All four share one condition: the system must not pose a significant risk of
    harm to health, safety or fundamental rights, including by not materially
    influencing the outcome of decision-making. The derogation **never** applies
    to a system that performs profiling of natural persons (Art. 6(3), last
    subparagraph), and claiming it obliges the provider to document the
    assessment and still register the system (Art. 6(4), Art. 49(2)).
    """

    NARROW_PROCEDURAL_TASK = "narrow_procedural_task"
    IMPROVES_PRIOR_HUMAN_ACTIVITY = "improves_prior_human_activity"
    DETECTS_DECISION_PATTERNS = "detects_decision_patterns"
    PREPARATORY_TASK = "preparatory_task"


class LifecycleStage(str, Enum):
    """Where the system sits in its lifecycle — drives which duties are live."""

    DEVELOPMENT = "development"
    TESTING = "testing"
    PLACED_ON_MARKET = "placed_on_market"
    IN_SERVICE = "in_service"
    WITHDRAWN = "withdrawn"


@dataclass
class ConformityRecord:
    """Chapter III Section 4/5 conformity evidence for a high-risk system.

    The framework records *whether and when* each step happened; performing them
    (the assessment, the declaration, the registration) is the provider's act.
    """

    #: Art. 43 — internal control or notified-body route.
    assessment_procedure: str | None = None
    assessed_at: datetime | None = None
    notified_body: str | None = None
    #: Art. 47 — EU declaration of conformity.
    declaration_at: datetime | None = None
    #: Art. 48 — CE marking affixed.
    ce_marking_at: datetime | None = None
    #: Art. 49 — registration in the EU database, and the id it returned.
    eu_database_registration_at: datetime | None = None
    eu_database_id: str | None = None
    #: Art. 40 — harmonised standards applied.
    harmonised_standards: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "assessment_procedure": self.assessment_procedure,
            "assessed_at": _iso(self.assessed_at),
            "notified_body": self.notified_body,
            "declaration_at": _iso(self.declaration_at),
            "ce_marking_at": _iso(self.ce_marking_at),
            "eu_database_registration_at": _iso(self.eu_database_registration_at),
            "eu_database_id": self.eu_database_id,
            "harmonised_standards": list(self.harmonised_standards),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConformityRecord:
        return cls(
            assessment_procedure=data.get("assessment_procedure"),
            assessed_at=_parse_dt(data.get("assessed_at")),
            notified_body=data.get("notified_body"),
            declaration_at=_parse_dt(data.get("declaration_at")),
            ce_marking_at=_parse_dt(data.get("ce_marking_at")),
            eu_database_registration_at=_parse_dt(
                data.get("eu_database_registration_at")
            ),
            eu_database_id=data.get("eu_database_id"),
            harmonised_standards=list(data.get("harmonised_standards", [])),
        )


@dataclass
class AiSystem:
    """A registered AI system — the unit every AI Act obligation attaches to.

    ``risk_category`` may be set explicitly or derived by
    :func:`core.compliance.classification.classify_system`. Keeping it on the
    record (rather than recomputing everywhere) means an auditor sees the
    category the operator actually asserted, and when it last changed.
    """

    name: str
    role: OperatorRole = OperatorRole.PROVIDER
    risk_category: RiskCategory = RiskCategory.MINIMAL_RISK
    version: str = "0.0.0"
    intended_purpose: str = ""
    description: str = ""
    annex_iii_areas: list[AnnexIIIArea] = field(default_factory=list)
    #: Art. 6(1)/Annex I — safety component of a product covered by Union law.
    annex_i_product: bool = False
    #: Art. 6(3) — claimed derogation grounds, if any.
    art6_derogations: list[Art6Derogation] = field(default_factory=list)
    #: Art. 6(3) last subparagraph — profiling always defeats the derogation.
    performs_profiling: bool = False
    #: Art. 50 — the system interacts with people or emits synthetic content.
    interacts_with_humans: bool = False
    generates_synthetic_content: bool = False
    #: Chapter V — this record describes a general-purpose AI model.
    is_gpai_model: bool = False
    gpai_systemic_risk: bool = False
    lifecycle_stage: LifecycleStage = LifecycleStage.DEVELOPMENT
    #: Art. 14 — the named humans assigned to oversight.
    human_oversight_contacts: list[str] = field(default_factory=list)
    provider_name: str | None = None
    deployers: list[str] = field(default_factory=list)
    member_states: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    conformity: ConformityRecord = field(default_factory=ConformityRecord)
    tags: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid4().hex)
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)
    classified_at: datetime | None = None
    placed_on_market_at: datetime | None = None
    withdrawn_at: datetime | None = None

    @property
    def is_high_risk(self) -> bool:
        """Whether the Chapter III obligation set applies to this system."""
        return self.risk_category is RiskCategory.HIGH_RISK

    @property
    def requires_registration(self) -> bool:
        """Whether Art. 49 EU-database registration applies.

        High-risk systems register under Art. 49(1). A system claiming the
        Art. 6(3) derogation still registers under Art. 49(2) — the derogation
        removes the Chapter III duties, not the visibility.
        """
        if self.is_high_risk:
            return True
        return bool(self.art6_derogations) and bool(self.annex_iii_areas)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "role": self.role.value,
            "risk_category": self.risk_category.value,
            "intended_purpose": self.intended_purpose,
            "description": self.description,
            "annex_iii_areas": [a.value for a in self.annex_iii_areas],
            "annex_i_product": self.annex_i_product,
            "art6_derogations": [d.value for d in self.art6_derogations],
            "performs_profiling": self.performs_profiling,
            "interacts_with_humans": self.interacts_with_humans,
            "generates_synthetic_content": self.generates_synthetic_content,
            "is_gpai_model": self.is_gpai_model,
            "gpai_systemic_risk": self.gpai_systemic_risk,
            "lifecycle_stage": self.lifecycle_stage.value,
            "human_oversight_contacts": list(self.human_oversight_contacts),
            "provider_name": self.provider_name,
            "deployers": list(self.deployers),
            "member_states": list(self.member_states),
            "models": list(self.models),
            "conformity": self.conformity.to_dict(),
            "tags": list(self.tags),
            "details": self.details,
            "requires_registration": self.requires_registration,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "classified_at": _iso(self.classified_at),
            "placed_on_market_at": _iso(self.placed_on_market_at),
            "withdrawn_at": _iso(self.withdrawn_at),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AiSystem:
        """Reconstruct a system from its :meth:`to_dict` payload (round-trip)."""
        return cls(
            name=data["name"],
            version=data.get("version", "0.0.0"),
            role=OperatorRole(data["role"]),
            risk_category=RiskCategory(data["risk_category"]),
            intended_purpose=data.get("intended_purpose", ""),
            description=data.get("description", ""),
            annex_iii_areas=[AnnexIIIArea(a) for a in data.get("annex_iii_areas", [])],
            annex_i_product=data.get("annex_i_product", False),
            art6_derogations=[
                Art6Derogation(d) for d in data.get("art6_derogations", [])
            ],
            performs_profiling=data.get("performs_profiling", False),
            interacts_with_humans=data.get("interacts_with_humans", False),
            generates_synthetic_content=data.get("generates_synthetic_content", False),
            is_gpai_model=data.get("is_gpai_model", False),
            gpai_systemic_risk=data.get("gpai_systemic_risk", False),
            lifecycle_stage=LifecycleStage(data["lifecycle_stage"]),
            human_oversight_contacts=list(data.get("human_oversight_contacts", [])),
            provider_name=data.get("provider_name"),
            deployers=list(data.get("deployers", [])),
            member_states=list(data.get("member_states", [])),
            models=list(data.get("models", [])),
            conformity=ConformityRecord.from_dict(data.get("conformity", {})),
            tags=list(data.get("tags", [])),
            details=dict(data.get("details", {})),
            id=data["id"],
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            classified_at=_parse_dt(data.get("classified_at")),
            placed_on_market_at=_parse_dt(data.get("placed_on_market_at")),
            withdrawn_at=_parse_dt(data.get("withdrawn_at")),
        )


__all__ = [
    "AiSystem",
    "AnnexIIIArea",
    "Art6Derogation",
    "ConformityRecord",
    "LifecycleStage",
    "OperatorRole",
    "RiskCategory",
]
