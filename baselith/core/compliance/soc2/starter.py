"""Starter controls: one per built-in collector, as a place to begin.

These are **drafts of the organisation's own statements**, not a prescribed
control set: the operator names an owner, reads each description against what
the organisation actually does and edits it. What they provide is the mapping
from the evidence the platform can collect to the criteria it speaks to, so a
first engagement does not start from an empty page.
"""

from __future__ import annotations

from baselith.core.compliance.soc2.types import ControlFrequency, Soc2Control

_STARTERS: tuple[tuple[str, str, list[str], ControlFrequency, list[str]], ...] = (
    (
        "Audit trail integrity is verified",
        "The tamper-evident audit trail is verified on a schedule and any "
        "divergence is investigated.",
        ["CC7.2", "CC4.1"],
        ControlFrequency.DAILY,
        ["audit_chain"],
    ),
    (
        "Security incidents are tracked to their reporting obligations",
        "Security incidents and personal-data breaches are recorded, and the "
        "obligations they start are tracked until they are met.",
        ["CC7.3", "CC7.4", "CC7.5"],
        ControlFrequency.WEEKLY,
        ["incident_register"],
    ),
    (
        "Configuration is checked against the declared posture",
        "The running configuration is checked against the regulatory profile "
        "the organisation declares, and gaps are resolved.",
        ["CC4.1", "CC5.2"],
        ControlFrequency.WEEKLY,
        ["declared_posture"],
    ),
    (
        "Third-party providers are registered and reviewed",
        "ICT third-party providers and their arrangements are kept in a "
        "register, with concentration risk reviewed.",
        ["CC9.2"],
        ControlFrequency.QUARTERLY,
        ["vendor_register"],
    ),
)


def starter_controls(owner: str) -> list[Soc2Control]:
    """Fresh starter controls owned by ``owner`` (not stored)."""
    return [
        Soc2Control(
            title=title,
            description=description,
            owner=owner,
            criteria=list(criteria),
            frequency=frequency,
            collectors=list(collectors),
        )
        for title, description, criteria, frequency, collectors in _STARTERS
    ]


__all__ = ["starter_controls"]
