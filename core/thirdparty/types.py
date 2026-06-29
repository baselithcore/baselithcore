"""Domain types for the DORA Register of Information (Regulation (EU) 2022/2554).

DORA Art. 28(3) requires financial entities to maintain and keep up to date a
**Register of Information** on all contractual arrangements for the use of ICT
services provided by ICT third-party service providers. The structure and
templates are set by Commission Implementing Regulation (EU) 2024/2956 (the ESA
ITS on the register of information).

This module models the backbone of that register — providers, the ICT functions
they support, and the contractual arrangements (including the supply chain and
the Art. 28(8) substitutability/exit assessment) — as plain dataclasses. The
framework holds and validates the records and can render them in the register's
template layout (see :mod:`core.thirdparty.export`); the financial entity remains
responsible for completeness and for the regulatory submission.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4


def _utcnow() -> datetime:
    """Current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


class ProviderType(str, Enum):
    """Type of person of the ICT third-party service provider."""

    LEGAL_ENTITY = "legal_entity"
    NATURAL_PERSON = "natural_person"
    OTHER = "other"


class FunctionCriticality(str, Enum):
    """Criticality band of a business function (DORA "critical or important")."""

    CRITICAL = "critical"
    IMPORTANT = "important"
    NOT_CRITICAL = "not_critical"


class Substitutability(str, Enum):
    """How readily an ICT third-party provider could be replaced (Art. 28(8))."""

    NOT_SUBSTITUTABLE = "not_substitutable"
    HIGHLY_COMPLEX = "highly_complex"
    MEDIUM_COMPLEX = "medium_complex"
    EASILY_SUBSTITUTABLE = "easily_substitutable"


class DataSensitivity(str, Enum):
    """Sensitivity of data handled under the arrangement."""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class ICTProvider:
    """An ICT third-party service provider (register template B_05.01)."""

    name: str
    id: str = field(default_factory=lambda: uuid4().hex)
    lei: Optional[str] = None
    provider_type: ProviderType = ProviderType.LEGAL_ENTITY
    country: Optional[str] = None
    # Designated as a Critical ICT Third-Party Provider by the ESAs (Art. 31).
    is_critical_designated: bool = False
    # Ultimate parent / group head, when the provider belongs to a group.
    parent_id: Optional[str] = None
    total_annual_expense: Optional[float] = None
    currency: str = "EUR"
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "lei": self.lei,
            "provider_type": self.provider_type.value,
            "country": self.country,
            "is_critical_designated": self.is_critical_designated,
            "parent_id": self.parent_id,
            "total_annual_expense": self.total_annual_expense,
            "currency": self.currency,
        }


@dataclass
class ICTFunction:
    """A business function supported by ICT services (register template B_06.01)."""

    name: str
    id: str = field(default_factory=lambda: uuid4().hex)
    criticality: FunctionCriticality = FunctionCriticality.NOT_CRITICAL
    licensed_activity: Optional[str] = None
    reasons_for_criticality: str = ""

    @property
    def is_critical_or_important(self) -> bool:
        """Whether this function counts as *critical or important* under DORA."""
        return self.criticality in (
            FunctionCriticality.CRITICAL,
            FunctionCriticality.IMPORTANT,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "criticality": self.criticality.value,
            "licensed_activity": self.licensed_activity,
            "reasons_for_criticality": self.reasons_for_criticality,
        }


@dataclass
class ServiceAssessment:
    """Art. 28(8) assessment of an arrangement (register template B_07.01)."""

    supports_critical_function: bool = False
    substitutability: Substitutability = Substitutability.EASILY_SUBSTITUTABLE
    exit_plan_exists: bool = False
    reintegration_possible: bool = False
    alternative_providers_identified: bool = False
    processes_personal_data: bool = False
    data_sensitivity: DataSensitivity = DataSensitivity.NONE

    def to_dict(self) -> Dict[str, Any]:
        return {
            "supports_critical_function": self.supports_critical_function,
            "substitutability": self.substitutability.value,
            "exit_plan_exists": self.exit_plan_exists,
            "reintegration_possible": self.reintegration_possible,
            "alternative_providers_identified": self.alternative_providers_identified,
            "processes_personal_data": self.processes_personal_data,
            "data_sensitivity": self.data_sensitivity.value,
        }


@dataclass
class ContractualArrangement:
    """A contractual arrangement for ICT services (register templates B_02/B_05.02).

    Keyed by ``reference_number`` (the contract's unique reference). Links the
    main provider, the ICT functions it supports, its supply chain
    (subcontractor providers), and the Art. 28(8) assessment.
    """

    reference_number: str
    provider_id: str
    function_ids: List[str] = field(default_factory=list)
    ict_service_type: str = ""
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    notice_period_days: Optional[int] = None
    governing_law_country: Optional[str] = None
    annual_cost: Optional[float] = None
    currency: str = "EUR"
    data_locations: List[str] = field(default_factory=list)
    subcontractor_ids: List[str] = field(default_factory=list)
    assessment: ServiceAssessment = field(default_factory=ServiceAssessment)
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reference_number": self.reference_number,
            "provider_id": self.provider_id,
            "function_ids": list(self.function_ids),
            "ict_service_type": self.ict_service_type,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "notice_period_days": self.notice_period_days,
            "governing_law_country": self.governing_law_country,
            "annual_cost": self.annual_cost,
            "currency": self.currency,
            "data_locations": list(self.data_locations),
            "subcontractor_ids": list(self.subcontractor_ids),
            "assessment": self.assessment.to_dict(),
        }


__all__ = [
    "ProviderType",
    "FunctionCriticality",
    "Substitutability",
    "DataSensitivity",
    "ICTProvider",
    "ICTFunction",
    "ServiceAssessment",
    "ContractualArrangement",
]
