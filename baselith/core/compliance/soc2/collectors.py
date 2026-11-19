"""Automatic evidence collectors.

A collector reads something the platform already knows and turns it into an
:class:`EvidenceDraft`: the audit trail's integrity check, the state of the
incident registers, the declared posture against the running configuration, the
third-party register. An operator links a control to a collector by name; each
collection then attaches a dated, hashed piece of evidence to every active
control that lists it.

The registry is a seam, not a closed list. Core ships collectors only for what
``baselith/core/`` itself owns; a plugin that owns other evidence (an access
review, a change log) registers its own with :func:`register_evidence_collector`
at ``initialize`` — core never imports a plugin.

**A collector reports what it found, not what would look good.** A broken audit
chain, an overdue incident milestone or a gap in the declared posture comes
back as an ``exception`` outcome, because evidence that every check passed,
collected from a system where one failed, is worse than none. A collector that
cannot run at all raises; the caller records that as an exception too rather
than skipping the control.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from baselith.core.compliance.soc2.types import EvidenceOutcome
from baselith.core.observability.logging import get_logger

logger = get_logger(__name__)


@dataclass
class EvidenceDraft:
    """What a collector found, before the service stamps and stores it."""

    title: str
    summary: str
    outcome: EvidenceOutcome = EvidenceOutcome.OPERATED
    details: dict[str, Any] = field(default_factory=dict)


Collector = Callable[[], Awaitable[EvidenceDraft]]


@dataclass(frozen=True)
class CollectorSpec:
    """A registered collector and what it can speak to."""

    name: str
    description: str
    #: The criteria the evidence is relevant to (a hint for mapping controls).
    criteria: tuple[str, ...]
    run: Collector

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "criteria": list(self.criteria),
        }


_REGISTRY: dict[str, CollectorSpec] = {}


def register_evidence_collector(
    name: str, run: Collector, *, description: str, criteria: tuple[str, ...] = ()
) -> None:
    """Register (or replace) a collector. Safe to call at every start."""
    _REGISTRY[name] = CollectorSpec(name, description, criteria, run)


def registered_collectors() -> dict[str, CollectorSpec]:
    """Every registered collector, by name."""
    return dict(_REGISTRY)


# -- built-in collectors ----------------------------------------------------


async def _audit_chain() -> EvidenceDraft:
    from baselith.core.observability.audit_setup import get_durable_audit_sink

    sink = get_durable_audit_sink()
    if sink is None:
        return EvidenceDraft(
            "Audit trail integrity check",
            "No durable audit trail is configured, so there is nothing to verify.",
            EvidenceOutcome.EXCEPTION,
            {"durable": False},
        )
    result = await asyncio.to_thread(sink.verify_chain)
    records = await asyncio.to_thread(sink.count)
    ok = bool(result.ok)
    return EvidenceDraft(
        "Audit trail integrity check",
        (
            f"Hash chain verified over {result.checked} records."
            if ok
            else f"Hash chain broken at record {result.broken_at}: {result.reason}."
        ),
        EvidenceOutcome.OPERATED if ok else EvidenceOutcome.EXCEPTION,
        {"durable": True, "records": records, **result.to_dict()},
    )


async def _incident_register() -> EvidenceDraft:
    from baselith.core.incidents import (
        get_ai_act_incident_service,
        get_breach_service,
        get_dora_incident_service,
        get_incident_service,
    )

    services: dict[str, tuple[Any, str]] = {
        "nis2": (get_incident_service(), "list_incidents"),
        "dora": (get_dora_incident_service(), "list_incidents"),
        "ai_act": (get_ai_act_incident_service(), "list_incidents"),
        "gdpr": (get_breach_service(), "list_breaches"),
    }
    regimes: dict[str, dict[str, int]] = {}
    overdue_total = 0
    for regime, (service, lister) in services.items():
        records = await getattr(service, lister)()
        overdue = len(await service.overdue_milestones())
        overdue_total += overdue
        regimes[regime] = {
            "total": len(records),
            "open": len([r for r in records if getattr(r, "closed_at", None) is None]),
            "overdue_milestones": overdue,
        }
    return EvidenceDraft(
        "Incident registers: state and overdue obligations",
        (
            "No incident obligation is overdue."
            if not overdue_total
            else f"{overdue_total} incident reporting obligation(s) are overdue."
        ),
        EvidenceOutcome.EXCEPTION if overdue_total else EvidenceOutcome.OPERATED,
        {"regimes": regimes},
    )


async def _declared_posture() -> EvidenceDraft:
    from baselith.core.compliance import evaluate_profile

    report = await asyncio.to_thread(evaluate_profile)
    if report.profile.value == "off":
        return EvidenceDraft(
            "Declared regulatory posture vs running configuration",
            "No compliance profile is declared, so the configuration is not "
            "checked against one.",
            EvidenceOutcome.INFORMATIONAL,
            report.to_dict(),
        )
    return EvidenceDraft(
        "Declared regulatory posture vs running configuration",
        (
            f"Profile '{report.profile.value}' is satisfied by the running configuration."
            if report.satisfied
            else f"Profile '{report.profile.value}' has {len(report.gaps)} unmet "
            "requirement(s)."
        ),
        EvidenceOutcome.OPERATED if report.satisfied else EvidenceOutcome.EXCEPTION,
        report.to_dict(),
    )


async def _vendor_register() -> EvidenceDraft:
    from baselith.core.thirdparty import get_register

    summary = await get_register().concentration_summary()
    flags = summary.get("concentration_flags", [])
    return EvidenceDraft(
        "ICT third-party register and concentration view",
        (
            f"{summary.get('providers', 0)} provider(s), "
            f"{summary.get('arrangements', 0)} arrangement(s), "
            f"{len(flags)} concentration flag(s)."
        ),
        EvidenceOutcome.INFORMATIONAL,
        {
            k: summary.get(k)
            for k in ("providers", "arrangements", "concentration_flags")
        },
    )


register_evidence_collector(
    "audit_chain",
    _audit_chain,
    description="Recomputes every link of the tamper-evident audit trail.",
    criteria=("CC7.2", "CC4.1"),
)
register_evidence_collector(
    "incident_register",
    _incident_register,
    description="State of the NIS2, DORA, AI Act and GDPR incident registers.",
    criteria=("CC7.3", "CC7.4", "CC7.5"),
)
register_evidence_collector(
    "declared_posture",
    _declared_posture,
    description="The declared compliance profile against the running configuration.",
    criteria=("CC4.1", "CC5.2"),
)
register_evidence_collector(
    "vendor_register",
    _vendor_register,
    description="The ICT third-party register and its concentration view.",
    criteria=("CC9.2",),
)


__all__ = [
    "Collector",
    "CollectorSpec",
    "EvidenceDraft",
    "registered_collectors",
    "register_evidence_collector",
]
