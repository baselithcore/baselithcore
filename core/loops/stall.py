"""Stall detection for engineered loops.

A watchdog notices when an agent *dies*. A stall guard notices when it is
alive, busy, billing, and getting nowhere: the failure fingerprint has not
changed for N attempts, so the loop is redecorating rather than converging.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.loops.fingerprint import failure_fingerprint
from core.observability.logging import get_logger

logger = get_logger(__name__)

__all__ = ["StallGuard", "StallVerdict"]


@dataclass(frozen=True)
class StallVerdict:
    """Outcome of recording one failed attempt against a :class:`StallGuard`."""

    fingerprint: str
    #: How many *consecutive* attempts have produced this fingerprint,
    #: counting the one just recorded (1 on the first sighting).
    repeats: int
    #: True when *repeats* reached the guard's threshold: stop, persist
    #: state, escalate to a human.
    stalled: bool
    reason: str | None = None


@dataclass
class StallGuard:
    """Track failure fingerprints across attempts and flag no-progress loops.

    Args:
        threshold: Number of consecutive identical fingerprints that counts
            as a stall. Must be >= 2 — a single failure is never a stall.

    Example::

        guard = StallGuard(threshold=3)
        verdict = guard.record(pytest_output)
        if verdict.stalled:
            escalate(reason=verdict.reason)
    """

    threshold: int = 3
    _last: str | None = field(default=None, init=False, repr=False)
    _repeats: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.threshold < 2:
            raise ValueError("StallGuard threshold must be >= 2")

    @property
    def last_fingerprint(self) -> str | None:
        """Fingerprint of the most recently recorded failure, if any."""
        return self._last

    def record(self, evidence: str) -> StallVerdict:
        """Record one failed attempt and report whether the loop has stalled.

        Args:
            evidence: Raw verifier output for the failed attempt.

        Returns:
            The :class:`StallVerdict` for this attempt.
        """
        fingerprint = failure_fingerprint(evidence)
        if fingerprint == self._last:
            self._repeats += 1
        else:
            self._last = fingerprint
            self._repeats = 1

        stalled = self._repeats >= self.threshold
        reason = None
        if stalled:
            reason = (
                f"no progress: the same failure ({fingerprint}) repeated "
                f"{self._repeats} times"
            )
            logger.warning("Loop stalled — %s", reason)
        return StallVerdict(
            fingerprint=fingerprint, repeats=self._repeats, stalled=stalled, reason=reason
        )

    def reset(self) -> None:
        """Forget the recorded history (e.g. when the goal changes)."""
        self._last = None
        self._repeats = 0
