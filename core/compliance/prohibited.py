"""Art. 5 prohibited AI practices (Regulation (EU) 2024/1689).

The Art. 5 bans have applied since **2 February 2025** and are the only part of
the AI Act with no compliance path: a prohibited practice is not permitted with
extra documentation, oversight, or consent. Penalties reach the highest tier
(Art. 99(3)).

This module makes the list explicit and enforceable at the point where a system
is registered, so a practice cannot be shipped merely because nobody named it.
It is a **declaration gate, not a detector**: it acts on what the operator
declares about a system's purpose, because whether a system performs, say,
social scoring is a question about its purpose and deployment context that no
runtime check can answer.

The declaration is auditable — every screening emits a structured audit record —
which is precisely what makes it worth having.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from core.observability.audit import AuditEventType, audit_emit
from core.observability.logging import get_logger

logger = get_logger(__name__)


class ProhibitedPractice(str, Enum):
    """The Art. 5(1) prohibited practices, in the order the article lists them."""

    #: (a) subliminal, purposefully manipulative or deceptive techniques that
    #: materially distort behaviour and cause significant harm.
    MANIPULATIVE_TECHNIQUES = "manipulative_techniques"
    #: (b) exploitation of vulnerabilities due to age, disability, or a specific
    #: social or economic situation.
    VULNERABILITY_EXPLOITATION = "vulnerability_exploitation"
    #: (c) social scoring leading to detrimental or disproportionate treatment.
    SOCIAL_SCORING = "social_scoring"
    #: (d) predicting criminal offences based solely on profiling or personality
    #: traits (excluding systems supporting a human assessment grounded in
    #: objective, verifiable facts).
    PREDICTIVE_POLICING = "predictive_policing"
    #: (e) untargeted scraping of facial images to build or expand facial
    #: recognition databases.
    UNTARGETED_FACIAL_SCRAPING = "untargeted_facial_scraping"
    #: (f) inferring emotions in the workplace or in education institutions
    #: (except for medical or safety reasons).
    EMOTION_INFERENCE_WORK_EDUCATION = "emotion_inference_work_education"
    #: (g) biometric categorisation to deduce race, political opinions, trade
    #: union membership, religious or philosophical beliefs, sex life or sexual
    #: orientation.
    BIOMETRIC_CATEGORISATION_SENSITIVE = "biometric_categorisation_sensitive"
    #: (h) real-time remote biometric identification in publicly accessible
    #: spaces for law enforcement purposes (outside the narrow derogations).
    REALTIME_REMOTE_BIOMETRIC_ID = "realtime_remote_biometric_id"


#: Human-readable rationale, quoted for the audit record and error message.
PRACTICE_DESCRIPTIONS: dict[ProhibitedPractice, str] = {
    ProhibitedPractice.MANIPULATIVE_TECHNIQUES: (
        "Art. 5(1)(a) — subliminal, manipulative or deceptive techniques that "
        "materially distort behaviour and cause significant harm."
    ),
    ProhibitedPractice.VULNERABILITY_EXPLOITATION: (
        "Art. 5(1)(b) — exploitation of vulnerabilities due to age, disability, "
        "or a specific social or economic situation."
    ),
    ProhibitedPractice.SOCIAL_SCORING: (
        "Art. 5(1)(c) — social scoring leading to detrimental or "
        "disproportionate treatment."
    ),
    ProhibitedPractice.PREDICTIVE_POLICING: (
        "Art. 5(1)(d) — predicting criminal offences based solely on profiling "
        "or personality traits."
    ),
    ProhibitedPractice.UNTARGETED_FACIAL_SCRAPING: (
        "Art. 5(1)(e) — untargeted scraping of facial images to build or expand "
        "facial recognition databases."
    ),
    ProhibitedPractice.EMOTION_INFERENCE_WORK_EDUCATION: (
        "Art. 5(1)(f) — inferring emotions in the workplace or in education "
        "institutions, outside medical or safety purposes."
    ),
    ProhibitedPractice.BIOMETRIC_CATEGORISATION_SENSITIVE: (
        "Art. 5(1)(g) — biometric categorisation to deduce race, political "
        "opinions, trade union membership, religious or philosophical beliefs, "
        "sex life or sexual orientation."
    ),
    ProhibitedPractice.REALTIME_REMOTE_BIOMETRIC_ID: (
        "Art. 5(1)(h) — real-time remote biometric identification in publicly "
        "accessible spaces for law enforcement purposes."
    ),
}


class ProhibitedPracticeError(ValueError):
    """Raised when a system declares a practice banned by Art. 5."""

    def __init__(self, practices: list[ProhibitedPractice]) -> None:
        detail = "; ".join(PRACTICE_DESCRIPTIONS[p] for p in practices)
        super().__init__(f"Prohibited AI practice declared: {detail}")
        self.practices = practices


@dataclass
class ProhibitionScreening:
    """Outcome of screening a system's declared practices against Art. 5."""

    system_name: str
    practices: list[ProhibitedPractice] = field(default_factory=list)
    exemption_rationale: str | None = None

    @property
    def is_prohibited(self) -> bool:
        """Whether any banned practice was declared without a claimed exemption.

        Art. 5 exemptions are narrow (medical/safety purposes for emotion
        inference, the law-enforcement derogations for real-time biometric ID)
        and are the operator's assertion to justify — recording one does not
        make it valid, it makes it *reviewable*.
        """
        return bool(self.practices) and not self.exemption_rationale

    def to_dict(self) -> dict[str, Any]:
        return {
            "system_name": self.system_name,
            "practices": [p.value for p in self.practices],
            "descriptions": [PRACTICE_DESCRIPTIONS[p] for p in self.practices],
            "exemption_rationale": self.exemption_rationale,
            "is_prohibited": self.is_prohibited,
        }


def screen_practices(
    system_name: str,
    practices: list[ProhibitedPractice] | None = None,
    *,
    exemption_rationale: str | None = None,
) -> ProhibitionScreening:
    """Screen declared practices against Art. 5 and audit the result.

    Returns the screening rather than raising, so a caller can decide between
    blocking and flagging. :func:`enforce_practices` is the raising variant.
    """
    screening = ProhibitionScreening(
        system_name=system_name,
        practices=list(practices or []),
        exemption_rationale=exemption_rationale,
    )
    if screening.practices:
        logger.warning(
            "AUDIT | COMPLIANCE | art5 screening | system=%s practices=%s prohibited=%s",
            system_name,
            [p.value for p in screening.practices],
            screening.is_prohibited,
        )
    audit_emit(
        AuditEventType.COMPLIANCE_ASSESSMENT,
        resource=system_name,
        action="art5_screening",
        success=not screening.is_prohibited,
        details=screening.to_dict(),
    )
    return screening


def enforce_practices(
    system_name: str,
    practices: list[ProhibitedPractice] | None = None,
    *,
    exemption_rationale: str | None = None,
) -> ProhibitionScreening:
    """Screen, and raise :class:`ProhibitedPracticeError` if a ban applies."""
    screening = screen_practices(
        system_name, practices, exemption_rationale=exemption_rationale
    )
    if screening.is_prohibited:
        raise ProhibitedPracticeError(screening.practices)
    return screening


__all__ = [
    "PRACTICE_DESCRIPTIONS",
    "ProhibitedPractice",
    "ProhibitedPracticeError",
    "ProhibitionScreening",
    "enforce_practices",
    "screen_practices",
]
