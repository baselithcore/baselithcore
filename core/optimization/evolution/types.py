"""Domain types for population-based evolutionary search.

The evolution vertical (GEPA/AlphaEvolve-style) searches over *candidates* —
versioned text artifacts (prompts, skills, configs) scored per evaluation
instance. Per-instance scores are first-class because the Pareto frontier is
computed per instance, not on a collapsed scalar: a candidate that is the
best anywhere keeps its niche even when its average is mediocre.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from core.skill_evolution.types import FitnessVector


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Candidate(BaseModel):
    """One immutable member of the evolutionary population.

    Attributes:
        id: Stable hex identifier (also the audit resource on acceptance).
        content: The full text artifact under evolution.
        parent_id: Id of the candidate this one was mutated from, or
            ``None`` for a seed.
        generation: 0 for the seed; parent's generation + 1 for children.
        fitness: Aggregate multi-objective fitness (``None`` until
            evaluated). ``fitness.scalarize()`` drives ``best_overall``.
        instance_scores: Per-evaluation-instance scores in ``[0, 1]``,
            keyed by instance id — the substrate of the Pareto frontier.
        created_at: Creation timestamp (UTC).
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: uuid4().hex)
    content: str
    parent_id: str | None = None
    generation: int = Field(default=0, ge=0)
    fitness: FitnessVector | None = None
    instance_scores: dict[str, float] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)


class EvolutionBudget(BaseModel):
    """Hard resource bounds on one evolution run — all enforced.

    Attributes:
        max_generations: Cap on mutation/selection rounds.
        max_candidates: Cap on candidates ever created (seed included).
        max_evaluations: Cap on evaluator calls spent searching (seed and
            child training evaluations). The terminal holdout audit is the
            anti-gaming defense and always runs; it is reported in
            ``evaluations_used`` but never skipped to stay under this cap.
    """

    model_config = ConfigDict(frozen=True)

    max_generations: int = Field(ge=1)
    max_candidates: int = Field(ge=1)
    max_evaluations: int = Field(ge=1)


__all__ = ["Candidate", "EvolutionBudget"]
