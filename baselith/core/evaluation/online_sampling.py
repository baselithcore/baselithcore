"""Deterministic sampling of production flows for online evaluation.

A flow is sampled when a stable hash of its run id falls below the sample
rate, so the decision is reproducible: the same run is always in or out,
whichever replica sees it, and raising the rate only adds runs (a run sampled
at 5% is still sampled at 10%). The LLM judge uses the same position on the
unit interval, so judged runs are always a subset of sampled ones.

Runs without an id (an unaddressable run) get a random key: they are sampled
at the configured rate, but not reproducibly.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping
from dataclasses import dataclass

#: Divisor that maps the first 8 bytes of the digest onto ``[0, 1)``.
_SPAN = float(1 << 64)


def sample_position(key: str) -> float:
    """Map *key* to a stable position in ``[0, 1)``.

    Args:
        key: The sampling key (the run id).

    Returns:
        A float in ``[0, 1)``, identical for identical keys in every process.
    """
    digest = hashlib.blake2b(key.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") / _SPAN


@dataclass(frozen=True, slots=True)
class SampleDecision:
    """Whether one flow is evaluated, and how."""

    sampled: bool
    judged: bool
    position: float


class OnlineSampler:
    """Decide which flows are evaluated online.

    Args:
        rate: Default fraction of flows sampled for the deterministic checks.
        judge_rate: Fraction of all flows also LLM-judged (capped at the
            effective sample rate, so a judged flow is always sampled).
        intent_rates: Per-intent overrides of ``rate``.
    """

    def __init__(
        self,
        rate: float,
        judge_rate: float = 0.0,
        intent_rates: Mapping[str, float] | None = None,
    ) -> None:
        self.rate = _clamp(rate)
        self.judge_rate = _clamp(judge_rate)
        self.intent_rates = {k: _clamp(v) for k, v in (intent_rates or {}).items()}

    def rate_for(self, intent: str) -> float:
        """The sample rate applying to *intent*."""
        return self.intent_rates.get(intent, self.rate)

    def decide(self, intent: str, run_id: str | None) -> SampleDecision:
        """Decide whether the flow of *run_id* is sampled and judged.

        Args:
            intent: The flow's intent (selects a per-intent override).
            run_id: The run id; ``None`` falls back to a random key.

        Returns:
            The sampling decision.
        """
        position = sample_position(run_id or uuid.uuid4().hex)
        rate = self.rate_for(intent)
        sampled = position < rate
        judged = sampled and position < min(rate, self.judge_rate)
        return SampleDecision(sampled=sampled, judged=judged, position=position)


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, float(value)))


__all__ = ["OnlineSampler", "SampleDecision", "sample_position"]
