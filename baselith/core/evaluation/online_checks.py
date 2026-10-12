"""Cheap deterministic checks run on every sampled production flow.

No LLM is involved: each check reads the ``FLOW_COMPLETED`` payload and costs
microseconds, so the checks run on all sampled traffic while the LLM judge
stays an opt-in rate on top. Each failed check multiplies the score by its
factor; the product is the flow's ``checks`` score.

========================  ======  ===========================================
Flag                      Factor  Fails when
========================  ======  ===========================================
``empty``                 0.0     the answer is blank
``guard_blocked``         0.0     moderation or groundedness replaced it
``refusal``               0.25    it opens with a refusal phrase
``too_short``             0.5     it is shorter than the configured minimum
``ungrounded``            0.5     the groundedness rail scored it below 0.5
========================  ======  ===========================================

Latency, spend and guard redaction counts are reported as signals, not scored:
a slow or redacted answer is not necessarily a worse one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from baselith.core.evaluation.protocols import QualityLevel, quality_for_score

#: Refusal openings, matched against the start of the answer only: a refusal
#: phrase deep inside a long answer is usually a quoted caveat, not a refusal.
_REFUSAL = re.compile(
    r"^\W*(?:(?:i'?m|i am)\s+(?:sorry|afraid|unable|not able)|"
    r"(?:sorry|unfortunately),?\s+(?:but\s+)?i\s+(?:can(?:no|')t|cannot|am unable)|"
    r"i\s+(?:can(?:no|')t|cannot|won'?t|am unable to|must decline)|"
    r"as an ai\b|that(?:'s| is) (?:outside|beyond) (?:my|the) scope)",
    re.IGNORECASE,
)
_REFUSAL_PREFIX = 200

_FACTORS: dict[str, float] = {
    "empty": 0.0,
    "guard_blocked": 0.0,
    "refusal": 0.25,
    "too_short": 0.5,
    "ungrounded": 0.5,
}
#: The flags a check can raise, in a stable order (a bounded metric label set).
CHECK_FLAGS: tuple[str, ...] = tuple(_FACTORS)


@dataclass(frozen=True, slots=True)
class OnlineSample:
    """One sampled flow, as queued for evaluation."""

    intent: str
    query: str
    response: str
    run_id: str | None = None
    tenant_id: str | None = None
    judged: bool = False
    context: dict[str, Any] = field(default_factory=dict)
    guardrails: dict[str, Any] = field(default_factory=dict)
    duration_ms: float | None = None
    cost_usd: float | None = None
    tokens: int | None = None


@dataclass(frozen=True, slots=True)
class CheckOutcome:
    """Result of the deterministic checks on one sample."""

    score: float
    quality: QualityLevel
    aspects: dict[str, float]
    flags: tuple[str, ...]
    signals: dict[str, float]


def _guard_verdicts(guardrails: dict[str, Any]) -> tuple[bool, float | None, int]:
    """``(blocked, groundedness score, redaction count)`` from guard metadata."""
    blocked = False
    grounded: float | None = None
    moderation = guardrails.get("moderation")
    if isinstance(moderation, dict) and moderation.get("blocked"):
        blocked = True
    groundedness = guardrails.get("groundedness")
    if isinstance(groundedness, dict):
        blocked = blocked or bool(groundedness.get("blocked"))
        raw = groundedness.get("score")
        if isinstance(raw, (int, float)):
            grounded = float(raw)
    redactions = guardrails.get("redactions")
    count = 0
    if isinstance(redactions, dict):
        count = sum(v for v in redactions.values() if isinstance(v, int))
    elif isinstance(redactions, (list, tuple)):
        count = len(redactions)
    return blocked, grounded, count


def run_online_checks(sample: OnlineSample, *, min_chars: int) -> CheckOutcome:
    """Score *sample* with the deterministic checks.

    Args:
        sample: The sampled flow.
        min_chars: Answers shorter than this (stripped) fail ``too_short``.

    Returns:
        The score, quality level, per-check aspects (1.0 pass / 0.0 fail),
        the failed flags and the unscored signals.
    """
    answer = sample.response.strip()
    blocked, grounded, redactions = _guard_verdicts(sample.guardrails)
    failed: dict[str, bool] = {
        "empty": not answer,
        "guard_blocked": blocked,
        "refusal": bool(answer) and bool(_REFUSAL.search(answer[:_REFUSAL_PREFIX])),
        "too_short": bool(answer) and len(answer) < min_chars,
        "ungrounded": grounded is not None and grounded < 0.5,
    }
    score = 1.0
    for flag, did_fail in failed.items():
        if did_fail:
            score *= _FACTORS[flag]
    aspects = {
        f"not_{flag}": 0.0 if did_fail else 1.0 for flag, did_fail in failed.items()
    }
    if grounded is not None:
        aspects["groundedness"] = grounded
    signals: dict[str, float] = {"answer_chars": float(len(answer))}
    if redactions:
        signals["redactions"] = float(redactions)
    if sample.duration_ms is not None:
        signals["duration_ms"] = float(sample.duration_ms)
    if sample.cost_usd is not None:
        signals["cost_usd"] = float(sample.cost_usd)
    if sample.tokens is not None:
        signals["tokens"] = float(sample.tokens)
    return CheckOutcome(
        score=score,
        quality=quality_for_score(score),
        aspects=aspects,
        flags=tuple(flag for flag, did_fail in failed.items() if did_fail),
        signals=signals,
    )


__all__ = ["CHECK_FLAGS", "CheckOutcome", "OnlineSample", "run_online_checks"]
