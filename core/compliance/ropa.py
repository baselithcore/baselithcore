"""GDPR Art. 30 records of processing activities (ROPA).

Art. 30 obliges controllers — and, in a reduced form, processors — to maintain a
written record of processing activities and to make it available to the
supervisory authority on request. It is the first artefact an authority asks for
and the cheapest one to be caught without.

Art. 30(1) fixes the controller's record content (a)–(g); Art. 30(2) the
processor's. This module models both, exposes the same completeness check the
FRIA module does, and keeps the register queryable rather than living in a
spreadsheet nobody updated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from core.compliance.types import _iso, _parse_dt, _utcnow


class ProcessingRole(str, Enum):
    """Whether the record is kept as controller (Art. 30(1)) or processor (30(2))."""

    CONTROLLER = "controller"
    PROCESSOR = "processor"


class LawfulBasis(str, Enum):
    """The Art. 6(1) lawful bases for processing."""

    CONSENT = "consent"
    CONTRACT = "contract"
    LEGAL_OBLIGATION = "legal_obligation"
    VITAL_INTERESTS = "vital_interests"
    PUBLIC_TASK = "public_task"
    LEGITIMATE_INTERESTS = "legitimate_interests"


@dataclass
class InternationalTransfer:
    """A transfer to a third country or international organisation — Art. 30(1)(e)."""

    destination: str
    #: Art. 45 adequacy decision, or the Art. 46 safeguard relied upon.
    safeguard: str = ""
    #: Art. 49(1) second subparagraph: documented derogation, where relied on.
    derogation: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "destination": self.destination,
            "safeguard": self.safeguard,
            "derogation": self.derogation,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InternationalTransfer:
        return cls(
            destination=data["destination"],
            safeguard=data.get("safeguard", ""),
            derogation=data.get("derogation"),
        )


@dataclass
class ProcessingActivity:
    """One entry in the Art. 30 register."""

    name: str
    role: ProcessingRole = ProcessingRole.CONTROLLER
    #: (a) identity and contact details.
    controller_name: str = ""
    controller_contact: str = ""
    dpo_contact: str | None = None
    representative_contact: str | None = None
    joint_controllers: list[str] = field(default_factory=list)
    #: (b) purposes of the processing.
    purposes: list[str] = field(default_factory=list)
    lawful_basis: LawfulBasis | None = None
    #: (c) categories of data subjects and of personal data.
    data_subject_categories: list[str] = field(default_factory=list)
    personal_data_categories: list[str] = field(default_factory=list)
    #: Art. 9/10 — special categories and criminal-conviction data.
    special_categories: list[str] = field(default_factory=list)
    #: (d) categories of recipients.
    recipient_categories: list[str] = field(default_factory=list)
    #: (e) transfers to third countries.
    transfers: list[InternationalTransfer] = field(default_factory=list)
    #: (f) envisaged time limits for erasure.
    retention_period: str = ""
    #: (g) general description of technical and organisational security measures.
    security_measures: str = ""
    #: Cross-link to the AI system this processing feeds, when there is one.
    ai_system_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid4().hex)
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)
    reviewed_at: datetime | None = None

    def missing_elements(self) -> list[str]:
        """Return the Art. 30 elements that are still empty for this role."""
        missing: list[str] = []
        if not self.controller_name or not self.controller_contact:
            missing.append("Art. 30(1)(a) — identity and contact details")
        if self.role is ProcessingRole.CONTROLLER:
            if not self.purposes:
                missing.append("Art. 30(1)(b) — purposes of the processing")
            if not self.data_subject_categories:
                missing.append("Art. 30(1)(c) — categories of data subjects")
            if not self.personal_data_categories:
                missing.append("Art. 30(1)(c) — categories of personal data")
            if not self.retention_period:
                missing.append("Art. 30(1)(f) — envisaged erasure time limits")
        if not self.recipient_categories:
            missing.append("Art. 30(1)(d) — categories of recipients")
        for transfer in self.transfers:
            if not transfer.safeguard and not transfer.derogation:
                missing.append(
                    f"Art. 30(1)(e) — safeguards for the transfer to "
                    f"{transfer.destination}"
                )
        if not self.security_measures:
            missing.append("Art. 30(1)(g) — technical and organisational measures")
        return missing

    @property
    def is_complete(self) -> bool:
        """Whether every applicable Art. 30 element carries content."""
        return not self.missing_elements()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role.value,
            "controller_name": self.controller_name,
            "controller_contact": self.controller_contact,
            "dpo_contact": self.dpo_contact,
            "representative_contact": self.representative_contact,
            "joint_controllers": list(self.joint_controllers),
            "purposes": list(self.purposes),
            "lawful_basis": self.lawful_basis.value if self.lawful_basis else None,
            "data_subject_categories": list(self.data_subject_categories),
            "personal_data_categories": list(self.personal_data_categories),
            "special_categories": list(self.special_categories),
            "recipient_categories": list(self.recipient_categories),
            "transfers": [t.to_dict() for t in self.transfers],
            "retention_period": self.retention_period,
            "security_measures": self.security_measures,
            "ai_system_id": self.ai_system_id,
            "details": self.details,
            "missing_elements": self.missing_elements(),
            "is_complete": self.is_complete,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "reviewed_at": _iso(self.reviewed_at),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProcessingActivity:
        """Reconstruct an entry from its :meth:`to_dict` payload (round-trip)."""
        basis = data.get("lawful_basis")
        return cls(
            name=data["name"],
            role=ProcessingRole(data["role"]),
            controller_name=data.get("controller_name", ""),
            controller_contact=data.get("controller_contact", ""),
            dpo_contact=data.get("dpo_contact"),
            representative_contact=data.get("representative_contact"),
            joint_controllers=list(data.get("joint_controllers", [])),
            purposes=list(data.get("purposes", [])),
            lawful_basis=LawfulBasis(basis) if basis else None,
            data_subject_categories=list(data.get("data_subject_categories", [])),
            personal_data_categories=list(data.get("personal_data_categories", [])),
            special_categories=list(data.get("special_categories", [])),
            recipient_categories=list(data.get("recipient_categories", [])),
            transfers=[
                InternationalTransfer.from_dict(t) for t in data.get("transfers", [])
            ],
            retention_period=data.get("retention_period", ""),
            security_measures=data.get("security_measures", ""),
            ai_system_id=data.get("ai_system_id"),
            details=dict(data.get("details", {})),
            id=data["id"],
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            reviewed_at=_parse_dt(data.get("reviewed_at")),
        )


__all__ = [
    "InternationalTransfer",
    "LawfulBasis",
    "ProcessingActivity",
    "ProcessingRole",
]
