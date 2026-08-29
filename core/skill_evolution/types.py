"""Domain types for the skill-evolution loop.

The loop separates three layers: raw execution traces (already persisted by
checkpoints/run-events), a *wiki* of deduplicated patterns distilled from
outcomes, and executable declarative skills compiled from those patterns.
These models are the shared vocabulary of that loop.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

#: Hard cap on evidence entries kept per pattern (most recent win).
MAX_EVIDENCE: Final[int] = 20

#: Managed skill names must be safe path components (also enforced on disk).
SKILL_NAME_PATTERN: Final[str] = r"^[a-z0-9][a-z0-9-]{0,79}$"


class PatternKind(StrEnum):
    """What a wiki pattern documents."""

    FAILURE_MODE = "failure_mode"
    STRATEGY = "strategy"


class PatternStatus(StrEnum):
    """Lifecycle of a wiki pattern."""

    CANDIDATE = "candidate"
    PROMOTED = "promoted"
    RETIRED = "retired"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class EvidenceRef(BaseModel):
    """One observation backing a pattern (a run, an evaluation, a lesson)."""

    run_id: str | None = None
    score: float | None = None
    note: str = ""
    observed_at: datetime = Field(default_factory=_utcnow)


def _capped(evidence: list[EvidenceRef]) -> list[EvidenceRef]:
    """Single enforcement point for the evidence cap (newest win).

    Exists because ``model_copy(update=...)`` bypasses field validators, so
    the validator alone cannot protect the merge path.
    """
    return evidence[-MAX_EVIDENCE:]


class Pattern(BaseModel):
    """A deduplicated unit of accumulated knowledge.

    Identity for dedup purposes is the ``fingerprint`` (see
    :func:`core.loops.fingerprint.failure_fingerprint`): two outcomes that
    fail the same way merge into one pattern with a higher occurrence count
    instead of piling up as free-form strings.
    """

    id: str = Field(default_factory=lambda: uuid4().hex)
    fingerprint: str
    kind: PatternKind
    title: str
    summary: str
    evidence: list[EvidenceRef] = Field(default_factory=list)
    occurrences: int = 1
    status: PatternStatus = PatternStatus.CANDIDATE
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    @field_validator("evidence")
    @classmethod
    def _cap_evidence(cls, value: list[EvidenceRef]) -> list[EvidenceRef]:
        return _capped(value)

    def merge_observations(self, evidence: Iterable[EvidenceRef]) -> Pattern:
        """Return a copy recording one more observation of this pattern.

        One call is one observation (``occurrences + 1``) regardless of how
        many evidence entries it carries — matching the Postgres backend's
        ``ON CONFLICT`` merge, which appends all incoming evidence.

        Args:
            evidence: Evidence entries to append (the list stays capped at
                :data:`MAX_EVIDENCE`, newest kept).

        Returns:
            A new :class:`Pattern` with ``occurrences + 1`` and a refreshed
            ``updated_at``. ``id``/``title``/``summary`` are preserved.
        """
        return self.model_copy(
            update={
                "occurrences": self.occurrences + 1,
                "evidence": _capped([*self.evidence, *evidence]),
                "updated_at": _utcnow(),
            }
        )


class SkillProposal(BaseModel):
    """A generated declarative skill draft, pending the validation gate."""

    name: str = Field(pattern=SKILL_NAME_PATTERN)
    description: str = Field(min_length=1, max_length=200)
    body: str
    source_pattern_ids: list[str] = Field(default_factory=list)


class GateDecision(BaseModel):
    """Outcome of gating one skill version against a validation score."""

    skill_name: str
    accepted: bool
    score: float
    previous_best: float | None = None
    rolled_back: bool = False


class SkillImpact(BaseModel):
    """Aggregated activation/outcome statistics for one skill."""

    skill_name: str
    activations: int = 0
    outcomes: int = 0
    score_sum: float = 0.0

    @property
    def mean_score(self) -> float | None:
        """Mean outcome score, or None before any outcome is recorded."""
        if self.outcomes == 0:
            return None
        return self.score_sum / self.outcomes


__all__ = [
    "MAX_EVIDENCE",
    "SKILL_NAME_PATTERN",
    "EvidenceRef",
    "GateDecision",
    "Pattern",
    "PatternKind",
    "PatternStatus",
    "SkillImpact",
    "SkillProposal",
]
