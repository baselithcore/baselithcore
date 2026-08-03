"""Art. 13 instructions for use.

Annex IV documentation is written for authorities. Art. 13 is a different
artefact for a different reader: the **deployer**. It must accompany the
high-risk system in a concise, complete, correct and clear form, and it is what
makes the deployer's own duties performable at all — a deployer cannot assign
human oversight under Art. 26(2) to people who were never told what the system's
limitations are.

Art. 13(3) fixes the content. The subsections are modelled here one by one,
because "we documented it somewhere in the technical file" is the usual way this
obligation is missed: the information exists, but never in the deployer's hands.

Most of it can be drafted from records the framework already holds — the
registry (identity, purpose, oversight contacts), the Art. 9 risk file
(foreseeable misuse and limitations), the Art. 72 plan (performance metrics),
the audit configuration (log collection under Art. 13(3)(f) and Art. 12). What
remains is genuinely deployer-facing prose the provider must write.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from core.compliance.types import AiSystem, _iso, _parse_dt, _utcnow

if TYPE_CHECKING:  # pragma: no cover - typing only
    from core.compliance.post_market import PostMarketMonitoringPlan
    from core.compliance.risk_management import RiskManagementSystem


@dataclass
class InstructionsForUse:
    """The Art. 13(3) instructions accompanying a high-risk AI system."""

    system_id: str
    system_name: str
    version: str = "0.0.0"
    # (a) identity and contact details of the provider (and representative).
    provider_identity: str = ""
    provider_contact: str = ""
    authorised_representative: str | None = None
    # (b)(i) intended purpose.
    intended_purpose: str = ""
    # (b)(ii) accuracy, robustness and cybersecurity levels it was validated
    # against, and what can degrade them.
    performance_metrics: str = ""
    accuracy_conditions: str = ""
    # (b)(iii) circumstances — intended use or foreseeable misuse — that may
    # lead to risks to health, safety or fundamental rights.
    risk_circumstances: str = ""
    # (b)(iv) technical means to explain the output.
    explainability: str = ""
    # (b)(v) performance on specific persons or groups it is used on.
    group_performance: str = ""
    # (b)(vi) input-data specifications and the training/validation/testing sets.
    input_specifications: str = ""
    # (b)(vii) how to interpret the output and use it appropriately.
    output_interpretation: str = ""
    # (c) predetermined changes to the system and its performance.
    predetermined_changes: str = ""
    # (d) the Art. 14 human oversight measures, technical means included.
    human_oversight_measures: str = ""
    # (e) compute and hardware needed, expected lifetime, maintenance.
    resource_requirements: str = ""
    expected_lifetime: str = ""
    maintenance: str = ""
    # (f) how the deployer collects, stores and interprets the Art. 12 logs.
    log_collection: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid4().hex)
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)
    issued_at: datetime | None = None

    def missing_elements(self) -> list[str]:
        """Return the Art. 13(3) elements that are still empty."""
        required = [
            (self.provider_identity, "Art. 13(3)(a) — provider identity"),
            (self.provider_contact, "Art. 13(3)(a) — provider contact details"),
            (self.intended_purpose, "Art. 13(3)(b)(i) — intended purpose"),
            (
                self.performance_metrics,
                "Art. 13(3)(b)(ii) — accuracy, robustness and cybersecurity levels",
            ),
            (
                self.accuracy_conditions,
                "Art. 13(3)(b)(ii) — circumstances that may impact them",
            ),
            (
                self.risk_circumstances,
                "Art. 13(3)(b)(iii) — circumstances leading to risks",
            ),
            (self.explainability, "Art. 13(3)(b)(iv) — means to explain the output"),
            (
                self.group_performance,
                "Art. 13(3)(b)(v) — performance on the groups it is used on",
            ),
            (
                self.input_specifications,
                "Art. 13(3)(b)(vi) — input-data specifications",
            ),
            (
                self.output_interpretation,
                "Art. 13(3)(b)(vii) — how to interpret and use the output",
            ),
            (self.predetermined_changes, "Art. 13(3)(c) — predetermined changes"),
            (
                self.human_oversight_measures,
                "Art. 13(3)(d) — human oversight measures",
            ),
            (self.resource_requirements, "Art. 13(3)(e) — compute and hardware needed"),
            (self.expected_lifetime, "Art. 13(3)(e) — expected lifetime"),
            (self.maintenance, "Art. 13(3)(e) — maintenance and updates"),
            (self.log_collection, "Art. 13(3)(f) — log collection and interpretation"),
        ]
        return [label for value, label in required if not str(value).strip()]

    @property
    def is_complete(self) -> bool:
        """Whether every Art. 13(3) element carries content."""
        return not self.missing_elements()

    def to_markdown(self) -> str:
        """Render the instructions for the deployer."""
        sections: list[tuple[str, str]] = [
            ("Provider", f"{self.provider_identity}\n\n{self.provider_contact}"),
            ("Authorised representative", self.authorised_representative or ""),
            ("Intended purpose", self.intended_purpose),
            ("Accuracy, robustness and cybersecurity", self.performance_metrics),
            ("Circumstances affecting performance", self.accuracy_conditions),
            ("Circumstances that may lead to risks", self.risk_circumstances),
            ("Explaining the output", self.explainability),
            ("Performance on specific groups", self.group_performance),
            ("Input data specifications", self.input_specifications),
            ("Interpreting and using the output", self.output_interpretation),
            ("Predetermined changes", self.predetermined_changes),
            ("Human oversight measures", self.human_oversight_measures),
            ("Computational and hardware resources", self.resource_requirements),
            ("Expected lifetime", self.expected_lifetime),
            ("Maintenance and updates", self.maintenance),
            ("Collecting and interpreting the logs", self.log_collection),
        ]
        lines = [
            f"# Instructions for use — {self.system_name} v{self.version}",
            "",
            "*EU AI Act Art. 13. Read this before assigning human oversight "
            "under Art. 26(2).*",
            "",
        ]
        for title, body in sections:
            content = (body or "").strip()
            if not content and title == "Authorised representative":
                continue  # optional: only required where one is appointed
            lines.extend([f"## {title}", "", content or "> **Not documented.**", ""])
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "system_id": self.system_id,
            "system_name": self.system_name,
            "version": self.version,
            "provider_identity": self.provider_identity,
            "provider_contact": self.provider_contact,
            "authorised_representative": self.authorised_representative,
            "intended_purpose": self.intended_purpose,
            "performance_metrics": self.performance_metrics,
            "accuracy_conditions": self.accuracy_conditions,
            "risk_circumstances": self.risk_circumstances,
            "explainability": self.explainability,
            "group_performance": self.group_performance,
            "input_specifications": self.input_specifications,
            "output_interpretation": self.output_interpretation,
            "predetermined_changes": self.predetermined_changes,
            "human_oversight_measures": self.human_oversight_measures,
            "resource_requirements": self.resource_requirements,
            "expected_lifetime": self.expected_lifetime,
            "maintenance": self.maintenance,
            "log_collection": self.log_collection,
            "details": self.details,
            "missing_elements": self.missing_elements(),
            "is_complete": self.is_complete,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "issued_at": _iso(self.issued_at),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InstructionsForUse:
        """Reconstruct the instructions from their :meth:`to_dict` payload."""
        return cls(
            system_id=data["system_id"],
            system_name=data["system_name"],
            version=data.get("version", "0.0.0"),
            provider_identity=data.get("provider_identity", ""),
            provider_contact=data.get("provider_contact", ""),
            authorised_representative=data.get("authorised_representative"),
            intended_purpose=data.get("intended_purpose", ""),
            performance_metrics=data.get("performance_metrics", ""),
            accuracy_conditions=data.get("accuracy_conditions", ""),
            risk_circumstances=data.get("risk_circumstances", ""),
            explainability=data.get("explainability", ""),
            group_performance=data.get("group_performance", ""),
            input_specifications=data.get("input_specifications", ""),
            output_interpretation=data.get("output_interpretation", ""),
            predetermined_changes=data.get("predetermined_changes", ""),
            human_oversight_measures=data.get("human_oversight_measures", ""),
            resource_requirements=data.get("resource_requirements", ""),
            expected_lifetime=data.get("expected_lifetime", ""),
            maintenance=data.get("maintenance", ""),
            log_collection=data.get("log_collection", ""),
            details=dict(data.get("details", {})),
            id=data["id"],
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            issued_at=_parse_dt(data.get("issued_at")),
        )


def draft_instructions(
    system: AiSystem,
    *,
    risk_file: RiskManagementSystem | None = None,
    monitoring_plan: PostMarketMonitoringPlan | None = None,
    provider_contact: str | None = None,
) -> InstructionsForUse:
    """Pre-fill Art. 13(3) elements from the records the framework already holds.

    Drafted: the provider identity (a), the intended purpose (b)(i), the risk
    circumstances (b)(iii) from the Art. 9 file, the metrics (b)(ii) from the
    Art. 72 plan, the oversight measures (d), and the log description (f).

    Left empty on purpose: everything that is genuinely deployer-facing prose —
    how to interpret the output, what the input must look like, what degrades
    accuracy. Inventing those would produce instructions that read complete and
    tell the deployer nothing true.
    """
    from core.config.audit import get_audit_config

    instructions = InstructionsForUse(
        system_id=system.id,
        system_name=system.name,
        version=system.version,
        provider_identity=system.provider_name or "",
        provider_contact=provider_contact or "",
        intended_purpose=system.intended_purpose,
    )

    if system.human_oversight_contacts:
        instructions.human_oversight_measures = (
            "Human oversight is assigned to: "
            + ", ".join(system.human_oversight_contacts)
            + ". The deployer must assign natural persons with the necessary "
            "competence, training and authority (Art. 26(2))."
        )

    if risk_file is not None:
        parts: list[str] = []
        if risk_file.foreseeable_misuse:
            parts.append(f"Reasonably foreseeable misuse: {risk_file.foreseeable_misuse}")
        for risk in risk_file.risks:
            harms = ", ".join(h.value for h in risk.harm_categories) or "unspecified"
            parts.append(f"- {risk.description} (risk to {harms})")
        instructions.risk_circumstances = "\n".join(parts)

    if monitoring_plan is not None and monitoring_plan.metrics:
        instructions.performance_metrics = "\n".join(
            f"- {m.name}"
            + (f": threshold {m.threshold} ({m.direction.value})" if m.threshold else "")
            for m in monitoring_plan.metrics
        )

    audit = get_audit_config()
    if audit.enabled:
        instructions.log_collection = (
            "The system records events automatically (Art. 12). Logs are retained "
            f"for {audit.retention_days} days"
            + (f" in {audit.db_path}" if audit.db_path else "")
            + ". The deployer must keep the logs it controls for at least six "
            "months (Art. 26(6)) and can verify their integrity through the "
            "audit chain verification endpoint."
        )

    instructions.updated_at = _utcnow()
    return instructions


__all__ = ["InstructionsForUse", "draft_instructions"]
