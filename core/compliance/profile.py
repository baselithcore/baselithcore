"""Compliance profiles — one switch that checks the whole posture.

The regulatory subsystems are individually opt-in, which is correct for a
framework: nobody should inherit NIS2 reporting because they installed a chat
library. The cost is that a deployment which *is* in scope has to get roughly a
dozen independent flags right, and a single one left off is invisible until an
auditor finds it.

A profile inverts that. It names a regulatory posture and states which settings
that posture requires; :func:`evaluate_profile` reports what is missing.

**It deliberately does not turn anything on.** Silently enabling durable audit
storage, retention sweeps or incident clocks because an env var named a profile
would change behaviour the operator never configured — including where data is
written and what gets deleted. The profile reports; the operator decides. In
strict mode a gap fails startup instead of warning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from core.observability.logging import get_logger

logger = get_logger(__name__)


class ComplianceProfile(str, Enum):
    """Named regulatory postures."""

    OFF = "off"
    GDPR = "gdpr"
    NIS2 = "nis2"
    DORA = "dora"
    AI_ACT_LIMITED_RISK = "ai-act-limited-risk"
    AI_ACT_HIGH_RISK = "ai-act-high-risk"
    FULL = "full"


@dataclass
class Requirement:
    """One setting a profile requires, and why."""

    setting: str
    why: str
    satisfied: bool = False
    actual: Any = None
    expected: str = "enabled"

    def to_dict(self) -> dict[str, Any]:
        return {
            "setting": self.setting,
            "why": self.why,
            "expected": self.expected,
            "actual": self.actual,
            "satisfied": self.satisfied,
        }


@dataclass
class ProfileReport:
    """The result of checking a deployment against a profile."""

    profile: ComplianceProfile
    requirements: list[Requirement] = field(default_factory=list)

    @property
    def gaps(self) -> list[Requirement]:
        """Requirements the deployment does not currently satisfy."""
        return [r for r in self.requirements if not r.satisfied]

    @property
    def satisfied(self) -> bool:
        """Whether every requirement of the profile is met."""
        return not self.gaps

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile.value,
            "satisfied": self.satisfied,
            "requirements": [r.to_dict() for r in self.requirements],
            "gaps": [r.setting for r in self.gaps],
        }


def _audit_requirements() -> list[Requirement]:
    from core.config.audit import MIN_RETENTION_DAYS, get_audit_config

    config = get_audit_config()
    return [
        Requirement(
            setting="AUDIT_ENABLED",
            why="AI Act Art. 12 / GDPR Art. 5(2): events must be recorded, "
            "not merely logged.",
            satisfied=config.enabled,
            actual=config.enabled,
        ),
        Requirement(
            setting="AUDIT_DB_PATH",
            why="Records must survive a restart to be evidence.",
            expected="a filesystem path",
            satisfied=bool(config.db_path),
            actual=config.db_path,
        ),
        Requirement(
            setting="AUDIT_RETENTION_DAYS",
            why=f"AI Act Art. 19 / Art. 26(6): keep the logs for at least "
            f"{MIN_RETENTION_DAYS} days.",
            expected=f">= {MIN_RETENTION_DAYS}",
            satisfied=config.retention_days == 0
            or config.retention_days >= MIN_RETENTION_DAYS,
            actual=config.retention_days,
        ),
    ]


def _privacy_requirements() -> list[Requirement]:
    from core.config.privacy import get_privacy_config

    config = get_privacy_config()
    return [
        Requirement(
            setting="PRIVACY_CONSENT_DB_PATH",
            why="GDPR Art. 7(1): consent must be demonstrable later, so the "
            "record chain has to outlive the process.",
            expected="a filesystem path",
            satisfied=bool(config.consent_db_path),
            actual=config.consent_db_path,
        ),
        Requirement(
            setting="PRIVACY_AUTOMATED_DECISIONS_DB_PATH",
            why="GDPR Art. 22(3): the register is the evidence that the "
            "safeguards exist and where subjects reach them.",
            expected="a filesystem path",
            satisfied=bool(config.automated_decisions_db_path),
            actual=config.automated_decisions_db_path,
        ),
        Requirement(
            setting="PRIVACY_ENABLED",
            why="GDPR Chapter III: data-subject rights must be servable.",
            satisfied=config.enabled,
            actual=config.enabled,
        ),
        Requirement(
            setting="PRIVACY_RETENTION_DAYS",
            why="GDPR Art. 5(1)(e): storage limitation must be enforced, "
            "not merely available.",
            expected="> 0",
            satisfied=config.retention_days > 0,
            actual=config.retention_days,
        ),
    ]


def _incident_requirements(*fields: tuple[str, str, str]) -> list[Requirement]:
    from core.config.incidents import get_incident_config

    config = get_incident_config()
    return [
        Requirement(
            setting=setting,
            why=why,
            satisfied=bool(getattr(config, attribute)),
            actual=getattr(config, attribute),
        )
        for attribute, setting, why in fields
    ]


def _transparency_requirements() -> list[Requirement]:
    from core.config.transparency import get_transparency_config

    config = get_transparency_config()
    return [
        Requirement(
            setting="TRANSPARENCY_ENABLED",
            why="AI Act Art. 50: disclose AI interaction and mark synthetic content.",
            satisfied=config.enabled,
            actual=config.enabled,
        )
    ]


#: The governance artefacts an AI Act deployment must be able to produce years
#: later, mapped to the setting that makes their store durable. An artefact kept
#: only in memory is not an artefact: the obligation is to *hold it at the
#: disposal* of authorities (Art. 18), not to have computed it once.
_ARTEFACT_STORES: tuple[tuple[str, str, str], ...] = (
    (
        "registry_db_path",
        "COMPLIANCE_REGISTRY_DB_PATH",
        "Art. 49 — the AI system inventory must outlive the process.",
    ),
    (
        "documents_db_path",
        "COMPLIANCE_DOCUMENTS_DB_PATH",
        "Art. 18 — Annex IV documentation is held at the authorities' disposal "
        "for 10 years.",
    ),
    (
        "risk_db_path",
        "COMPLIANCE_RISK_DB_PATH",
        "Art. 9(1) — the risk management file is reviewed and updated across "
        "the lifecycle.",
    ),
    (
        "instructions_db_path",
        "COMPLIANCE_INSTRUCTIONS_DB_PATH",
        "Art. 13 — the instructions issued to deployers must remain reproducible.",
    ),
    (
        "post_market_db_path",
        "COMPLIANCE_POST_MARKET_DB_PATH",
        "Art. 72 — the observation history is the evidence that monitoring was active.",
    ),
)


def _governance_requirements() -> list[Requirement]:
    from core.config.compliance import get_compliance_config

    config = get_compliance_config()
    requirements = [
        Requirement(
            setting="COMPLIANCE_ENABLED",
            why="AI Act Art. 6/11/49: obligations attach to registered systems; "
            "an unlisted system cannot be shown compliant.",
            satisfied=config.enabled,
            actual=config.enabled,
        )
    ]
    requirements.extend(
        Requirement(
            setting=setting,
            why=why,
            expected="a filesystem path",
            satisfied=bool(getattr(config, attribute, None)),
            actual=getattr(config, attribute, None),
        )
        for attribute, setting, why in _ARTEFACT_STORES
    )
    requirements.append(
        Requirement(
            setting="COMPLIANCE_POST_MARKET_SWEEP_ENABLED",
            why="Art. 9(1)/72(1)/GDPR Art. 35(11): the reviews are recurring; "
            "without the sweep an overdue artefact is never reported.",
            satisfied=config.post_market_sweep_enabled,
            actual=config.post_market_sweep_enabled,
        )
    )
    return requirements


def _dpia_requirements() -> list[Requirement]:
    from core.config.compliance import get_compliance_config

    config = get_compliance_config()
    return [
        Requirement(
            setting="COMPLIANCE_DPIA_DB_PATH",
            why="GDPR Art. 35/36: the assessment and its prior-consultation "
            "state must survive to be shown to the supervisory authority.",
            expected="a filesystem path",
            satisfied=bool(config.dpia_db_path),
            actual=config.dpia_db_path,
        )
    ]


_GDPR_INCIDENTS = (
    (
        "gdpr_enabled",
        "GDPR_BREACH_REPORTING_ENABLED",
        "GDPR Art. 33/34: the 72h clock.",
    ),
)
_NIS2_INCIDENTS = (
    ("enabled", "INCIDENT_REPORTING_ENABLED", "NIS2 Art. 23: the 24h/72h clock."),
)
_DORA_INCIDENTS = (
    ("dora_enabled", "DORA_INCIDENT_REPORTING_ENABLED", "DORA Art. 19: the 4h clock."),
)
_AI_ACT_INCIDENTS = (
    (
        "ai_act_enabled",
        "AI_ACT_INCIDENT_REPORTING_ENABLED",
        "AI Act Art. 73: the 2/10/15-day serious-incident clock.",
    ),
)


def requirements_for(profile: ComplianceProfile) -> list[Requirement]:
    """Return the requirements a profile imposes, evaluated against config."""
    if profile is ComplianceProfile.OFF:
        return []
    if profile is ComplianceProfile.GDPR:
        return [
            *_audit_requirements(),
            *_privacy_requirements(),
            *_dpia_requirements(),
            *_incident_requirements(*_GDPR_INCIDENTS),
        ]
    if profile is ComplianceProfile.NIS2:
        return [*_audit_requirements(), *_incident_requirements(*_NIS2_INCIDENTS)]
    if profile is ComplianceProfile.DORA:
        return [
            *_audit_requirements(),
            *_incident_requirements(*_NIS2_INCIDENTS, *_DORA_INCIDENTS),
        ]
    if profile is ComplianceProfile.AI_ACT_LIMITED_RISK:
        return [*_audit_requirements(), *_transparency_requirements()]
    if profile is ComplianceProfile.AI_ACT_HIGH_RISK:
        return [
            *_audit_requirements(),
            *_transparency_requirements(),
            *_governance_requirements(),
            *_incident_requirements(*_AI_ACT_INCIDENTS),
        ]
    # FULL
    return [
        *_audit_requirements(),
        *_privacy_requirements(),
        *_dpia_requirements(),
        *_transparency_requirements(),
        *_governance_requirements(),
        *_incident_requirements(
            *_NIS2_INCIDENTS, *_DORA_INCIDENTS, *_AI_ACT_INCIDENTS, *_GDPR_INCIDENTS
        ),
    ]


def evaluate_profile(profile: ComplianceProfile | str | None = None) -> ProfileReport:
    """Check the running configuration against a profile.

    ``profile`` defaults to ``BASELITH_COMPLIANCE_PROFILE``. An unknown name
    falls back to :attr:`ComplianceProfile.OFF` with a warning rather than
    raising — a typo in a profile name must not take the service down, but it
    must not silently read as "compliant" either.
    """
    import os

    raw = (
        profile
        if profile is not None
        else os.getenv("BASELITH_COMPLIANCE_PROFILE", ComplianceProfile.OFF.value)
    )
    if isinstance(raw, ComplianceProfile):
        resolved = raw
    else:
        try:
            resolved = ComplianceProfile(str(raw).strip().lower())
        except ValueError:
            logger.warning(
                "unknown_compliance_profile",
                extra={
                    "value": raw,
                    "known": [p.value for p in ComplianceProfile],
                },
            )
            resolved = ComplianceProfile.OFF
    return ProfileReport(profile=resolved, requirements=requirements_for(resolved))


class ComplianceProfileError(RuntimeError):
    """Raised in strict mode when the deployment does not satisfy its profile."""

    def __init__(self, report: ProfileReport) -> None:
        gaps = "; ".join(f"{r.setting} (expected {r.expected})" for r in report.gaps)
        super().__init__(
            f"Compliance profile '{report.profile.value}' is not satisfied: {gaps}"
        )
        self.report = report


def enforce_profile(profile: ComplianceProfile | str | None = None) -> ProfileReport:
    """Evaluate the profile and log — or raise, in strict mode — on any gap.

    Strict mode is ``BASELITH_COMPLIANCE_PROFILE_STRICT=true``. Use it in
    regulated deployments where starting up mis-configured is worse than not
    starting at all.
    """
    import os

    report = evaluate_profile(profile)
    if report.profile is ComplianceProfile.OFF or report.satisfied:
        if report.profile is not ComplianceProfile.OFF:
            logger.info(
                "compliance_profile_satisfied",
                extra={"profile": report.profile.value},
            )
        return report

    logger.warning(
        "compliance_profile_gaps",
        extra={
            "profile": report.profile.value,
            "gaps": [r.setting for r in report.gaps],
        },
    )
    for gap in report.gaps:
        logger.warning(
            "compliance_profile_gap | %s (expected %s, got %s) — %s",
            gap.setting,
            gap.expected,
            gap.actual,
            gap.why,
        )
    strict = os.getenv("BASELITH_COMPLIANCE_PROFILE_STRICT", "").lower() in (
        "1",
        "true",
        "yes",
    )
    if strict:
        raise ComplianceProfileError(report)
    return report


__all__ = [
    "ComplianceProfile",
    "ComplianceProfileError",
    "ProfileReport",
    "Requirement",
    "enforce_profile",
    "evaluate_profile",
    "requirements_for",
]
