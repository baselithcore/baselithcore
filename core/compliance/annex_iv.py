"""Art. 11 + Annex IV technical documentation.

Providers of high-risk AI systems must draw up technical documentation *before*
the system is placed on the market, keep it up to date, and hold it at the
disposal of national authorities for **10 years** (Art. 18). Annex IV fixes its
nine sections. Missing documentation is not a paperwork slip: without it the
conformity assessment in Art. 43 cannot be completed at all.

This module models the nine sections so the document is **checkable and
generatable**:

* :func:`draft_from_system` pre-fills what the registry already knows — the
  general description, the models involved, the oversight contacts, the applied
  harmonised standards — so the document starts as a draft, not a blank page;
* :meth:`TechnicalDocumentation.missing_sections` names the sections still
  empty;
* :meth:`TechnicalDocumentation.to_markdown` renders the whole thing for review
  or export.

What the framework cannot do is write the substance. Section 2 (development
process, data requirements, validation) and section 5 (the Art. 9 risk
management system) are engineering and governance work; the module makes their
absence loud instead of invisible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from core.compliance.types import AiSystem, _iso, _parse_dt, _utcnow

if TYPE_CHECKING:  # pragma: no cover - typing only
    from core.compliance.instructions import InstructionsForUse
    from core.compliance.risk_management import RiskManagementSystem


class AnnexIVSection(str, Enum):
    """The nine Annex IV sections, in order."""

    GENERAL_DESCRIPTION = "general_description"
    DEVELOPMENT_PROCESS = "development_process"
    MONITORING_AND_CONTROL = "monitoring_and_control"
    PERFORMANCE_METRICS = "performance_metrics"
    RISK_MANAGEMENT = "risk_management"
    LIFECYCLE_CHANGES = "lifecycle_changes"
    HARMONISED_STANDARDS = "harmonised_standards"
    DECLARATION_OF_CONFORMITY = "declaration_of_conformity"
    POST_MARKET_MONITORING = "post_market_monitoring"


#: Section headings as Annex IV phrases them.
SECTION_TITLES: dict[AnnexIVSection, str] = {
    AnnexIVSection.GENERAL_DESCRIPTION: "1. General description of the AI system",
    AnnexIVSection.DEVELOPMENT_PROCESS: (
        "2. Detailed description of the elements of the AI system and of the "
        "process for its development"
    ),
    AnnexIVSection.MONITORING_AND_CONTROL: (
        "3. Detailed information about the monitoring, functioning and control "
        "of the AI system"
    ),
    AnnexIVSection.PERFORMANCE_METRICS: (
        "4. Description of the appropriateness of the performance metrics"
    ),
    AnnexIVSection.RISK_MANAGEMENT: (
        "5. Detailed description of the risk management system (Art. 9)"
    ),
    AnnexIVSection.LIFECYCLE_CHANGES: (
        "6. Description of relevant changes made through the system's lifecycle"
    ),
    AnnexIVSection.HARMONISED_STANDARDS: (
        "7. List of the harmonised standards applied, in full or in part"
    ),
    AnnexIVSection.DECLARATION_OF_CONFORMITY: (
        "8. Copy of the EU declaration of conformity (Art. 47)"
    ),
    AnnexIVSection.POST_MARKET_MONITORING: (
        "9. Detailed description of the post-market monitoring plan (Art. 72)"
    ),
}

#: Art. 18: the documentation is kept at the authorities' disposal for 10 years.
RETENTION_YEARS = 10


@dataclass
class TechnicalDocumentation:
    """Annex IV technical documentation for one high-risk AI system."""

    system_id: str
    system_name: str
    version: str = "0.0.0"
    sections: dict[AnnexIVSection, str] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid4().hex)
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)
    approved_at: datetime | None = None
    approved_by: str | None = None

    def missing_sections(self) -> list[AnnexIVSection]:
        """Return the Annex IV sections with no content yet."""
        return [
            section
            for section in AnnexIVSection
            if not (self.sections.get(section) or "").strip()
        ]

    @property
    def is_complete(self) -> bool:
        """Whether all nine sections carry content (not whether they are *good*)."""
        return not self.missing_sections()

    def set_section(self, section: AnnexIVSection, content: str) -> None:
        """Write one section and stamp the document as updated."""
        self.sections[section] = content
        self.updated_at = _utcnow()

    def to_markdown(self) -> str:
        """Render the document for review or export."""
        lines = [
            f"# Technical documentation — {self.system_name} v{self.version}",
            "",
            f"*EU AI Act Art. 11 / Annex IV. System id: `{self.system_id}`.*",
            f"*Retain at the disposal of national authorities for "
            f"{RETENTION_YEARS} years after placing on the market (Art. 18).*",
            "",
        ]
        for section in AnnexIVSection:
            lines.append(f"## {SECTION_TITLES[section]}")
            lines.append("")
            content = (self.sections.get(section) or "").strip()
            lines.append(content if content else "> **Not documented.**")
            lines.append("")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "system_id": self.system_id,
            "system_name": self.system_name,
            "version": self.version,
            "sections": {k.value: v for k, v in self.sections.items()},
            "details": self.details,
            "missing_sections": [s.value for s in self.missing_sections()],
            "is_complete": self.is_complete,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "approved_at": _iso(self.approved_at),
            "approved_by": self.approved_by,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TechnicalDocumentation:
        """Reconstruct the document from its :meth:`to_dict` payload."""
        return cls(
            system_id=data["system_id"],
            system_name=data["system_name"],
            version=data.get("version", "0.0.0"),
            sections={
                AnnexIVSection(k): v for k, v in (data.get("sections") or {}).items()
            },
            details=dict(data.get("details", {})),
            id=data["id"],
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            approved_at=_parse_dt(data.get("approved_at")),
            approved_by=data.get("approved_by"),
        )


def draft_from_system(
    system: AiSystem,
    *,
    risk_file: RiskManagementSystem | None = None,
    instructions: InstructionsForUse | None = None,
) -> TechnicalDocumentation:
    """Pre-fill an Annex IV document from what the registry already knows.

    Sections 1, 7, 8 and 9 are drafted from the registered record. Pass the
    Art. 9 risk file and the Art. 13 instructions to fill sections 5 and 3 as
    well — those artefacts *are* the content Annex IV asks for, so a provider
    that maintains them should not retype them here.

    Whatever is not supplied stays empty on purpose: inventing content the
    operator never asserted would be worse than an obvious gap.
    """
    doc = TechnicalDocumentation(
        system_id=system.id, system_name=system.name, version=system.version
    )

    general = [
        f"- **Intended purpose**: {system.intended_purpose or 'not stated'}",
        f"- **Provider**: {system.provider_name or 'not stated'}",
        f"- **Operator role**: {system.role.value}",
        f"- **Risk category**: {system.risk_category.value}",
        f"- **Lifecycle stage**: {system.lifecycle_stage.value}",
        f"- **Version**: {system.version}",
    ]
    if system.description:
        general.append(f"- **Description**: {system.description}")
    if system.models:
        general.append(f"- **Models involved**: {', '.join(system.models)}")
    if system.annex_iii_areas:
        areas = ", ".join(a.value for a in system.annex_iii_areas)
        general.append(f"- **Annex III area(s)**: {areas}")
    if system.deployers:
        general.append(f"- **Known deployers**: {', '.join(system.deployers)}")
    if system.member_states:
        general.append(
            f"- **Member States of use**: {', '.join(system.member_states)}"
        )
    doc.sections[AnnexIVSection.GENERAL_DESCRIPTION] = "\n".join(general)

    monitoring: list[str] = []
    if system.human_oversight_contacts:
        monitoring.append(
            "- **Assigned human oversight (Art. 14)**: "
            + ", ".join(system.human_oversight_contacts)
        )
    if instructions is not None:
        if instructions.human_oversight_measures:
            monitoring.append(
                f"- **Oversight measures (Art. 13(3)(d))**: "
                f"{instructions.human_oversight_measures}"
            )
        if instructions.output_interpretation:
            monitoring.append(
                f"- **Interpreting the output (Art. 13(3)(b)(vii))**: "
                f"{instructions.output_interpretation}"
            )
        if instructions.log_collection:
            monitoring.append(
                f"- **Log collection (Art. 13(3)(f))**: {instructions.log_collection}"
            )
    if monitoring:
        doc.sections[AnnexIVSection.MONITORING_AND_CONTROL] = "\n".join(monitoring)

    if risk_file is not None:
        # Annex IV section 5 *is* the Art. 9 file — render it rather than asking
        # the provider to maintain the same content twice.
        doc.sections[AnnexIVSection.RISK_MANAGEMENT] = risk_file.to_markdown()

    if instructions is not None and instructions.performance_metrics:
        doc.sections[AnnexIVSection.PERFORMANCE_METRICS] = "\n".join(
            filter(
                None,
                [
                    instructions.performance_metrics,
                    instructions.accuracy_conditions,
                ],
            )
        )

    standards = system.conformity.harmonised_standards
    doc.sections[AnnexIVSection.HARMONISED_STANDARDS] = (
        "\n".join(f"- {s}" for s in standards)
        if standards
        else "No harmonised standards applied."
    )

    conformity = system.conformity
    if conformity.declaration_at:
        declaration = [
            f"- **Declared on**: {conformity.declaration_at.isoformat()}",
            f"- **Assessment procedure**: "
            f"{conformity.assessment_procedure or 'not stated'}",
        ]
        if conformity.notified_body:
            declaration.append(f"- **Notified body**: {conformity.notified_body}")
        if conformity.ce_marking_at:
            declaration.append(
                f"- **CE marking affixed**: {conformity.ce_marking_at.isoformat()}"
            )
        if conformity.eu_database_id:
            declaration.append(
                f"- **EU database id**: {conformity.eu_database_id}"
            )
        doc.sections[AnnexIVSection.DECLARATION_OF_CONFORMITY] = "\n".join(declaration)

    doc.sections[AnnexIVSection.POST_MARKET_MONITORING] = (
        "See the post-market monitoring plan for this system "
        "(`core.compliance.post_market`), which records the metrics collected in "
        "production, their alert thresholds, and the review cadence."
    )
    doc.updated_at = _utcnow()
    return doc


__all__ = [
    "RETENTION_YEARS",
    "SECTION_TITLES",
    "AnnexIVSection",
    "TechnicalDocumentation",
    "draft_from_system",
]
