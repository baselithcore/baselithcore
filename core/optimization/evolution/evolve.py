"""The evolution loop: select from the frontier, mutate, evaluate, archive.

Two design commitments from the GEPA/AlphaEvolve lineage:

* **Selection samples the Pareto frontier**, not the scalar best — the
  specialist that only wins one instance is exactly the lineage a scalar
  hill-climb would discard.
* **Holdout instances the search never optimizes against** are scored once
  at the end. A best candidate whose holdout mean falls below the seed's is
  *reported* as regressed (evaluator gaming); the landing decision belongs
  to the caller, mirroring the eval-gated posture of
  :mod:`core.optimization.tune_gate`.

Every archive-accepted child emits a ``SELF_MODIFY_PROPOSE`` audit event —
mutation is self-modification and stays on the audit trail.
"""

from __future__ import annotations

import random
from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from core.observability.logging import get_logger
from core.optimization.evolution.archive import CandidateArchive
from core.optimization.evolution.types import Candidate, EvolutionBudget
from core.skill_evolution.types import FitnessVector

logger = get_logger(__name__)

__all__ = ["Evaluator", "EvolutionEngine", "EvolutionReport", "Mutator"]

#: ``(content, instances) -> {instance_id: score in [0, 1]}`` — the caller
#: wires this to real evals or a sandbox; the engine only consumes scores.
Evaluator = Callable[[str, Sequence[str]], Awaitable[dict[str, float]]]

#: Failure notes fed to the mutator per selected parent.
_MAX_FAILURE_NOTES = 3


class Mutator(Protocol):
    """Contract the engine needs from a mutator (see ``ReflectiveMutator``)."""

    async def mutate(
        self, parent: Candidate, failures: list[str]
    ) -> str | None:  # pragma: no cover - protocol
        ...


class EvolutionReport(BaseModel):
    """Terminal result of one :meth:`EvolutionEngine.run`.

    Attributes:
        best: Best-overall candidate by scalarized fitness.
        holdout_scores: ``best`` scored on the holdout instances (empty
            when no holdouts were configured).
        holdout_regressed: True when ``best``'s holdout mean fell below
            the seed's — the evaluator-gaming signal. The run still
            reports its best; acting on the flag is the caller's job.
        generations_run: Mutation/selection rounds actually executed.
        evaluations_used: Total evaluator calls, terminal holdout audit
            included.
    """

    model_config = ConfigDict(frozen=True)

    best: Candidate
    holdout_scores: dict[str, float]
    holdout_regressed: bool
    generations_run: int
    evaluations_used: int


def _mean(scores: dict[str, float]) -> float:
    return sum(scores.values()) / len(scores) if scores else 0.0


def _fitness(scores: dict[str, float]) -> FitnessVector:
    """Quality-only fitness from training scores (clamped to [0, 1])."""
    return FitnessVector(quality=min(1.0, max(0.0, _mean(scores))))


def _worst_instances(candidate: Candidate) -> list[str]:
    """Failure notes for the candidate's lowest-scoring instances."""
    worst = sorted(candidate.instance_scores.items(), key=lambda kv: kv[1])
    return [
        f"instance {instance}: score {score:.3f}"
        for instance, score in worst[:_MAX_FAILURE_NOTES]
    ]


class EvolutionEngine:
    """Budgeted population search over text candidates.

    Args:
        archive: Candidate store providing the Pareto frontier.
        mutator: Bounded-edit mutator (``ReflectiveMutator`` or any
            :class:`Mutator`).
        evaluator: Per-instance scorer; the engine never sees eval
            internals, only ``{instance: score}``.
        budget: Hard bounds on generations, candidates and evaluations.
        holdout_instances: Instances excluded from every training
            evaluation and scored only in the terminal anti-gaming audit.
        rng_seed: Seed for frontier selection, for deterministic tests.
    """

    def __init__(
        self,
        archive: CandidateArchive,
        mutator: Mutator,
        evaluator: Evaluator,
        *,
        budget: EvolutionBudget,
        holdout_instances: Sequence[str] = (),
        rng_seed: int | None = None,
    ) -> None:
        self._archive = archive
        self._mutator = mutator
        self._evaluator = evaluator
        self._budget = budget
        self._holdout = tuple(holdout_instances)
        self._rng = random.Random(rng_seed)

    async def run(self, seed_content: str, instances: Sequence[str]) -> EvolutionReport:
        """Evolve from ``seed_content`` until a budget bound is hit.

        Args:
            seed_content: Generation-0 candidate text.
            instances: All evaluation instance ids; holdout instances are
                subtracted to form the training set.

        Returns:
            The report — best candidate, holdout audit, budget usage.

        Raises:
            ValueError: If no training instances remain after removing
                the holdouts.
        """
        holdout_set = set(self._holdout)
        training = [i for i in instances if i not in holdout_set]
        if not training:
            raise ValueError("no training instances remain after holdout split")

        evaluations = 0
        seed_scores = await self._evaluator(seed_content, training)
        evaluations += 1
        seed = Candidate(
            content=seed_content,
            generation=0,
            fitness=_fitness(seed_scores),
            instance_scores=seed_scores,
        )
        self._archive.add(seed)
        candidates_created = 1

        generations = 0
        while generations < self._budget.max_generations:
            if candidates_created >= self._budget.max_candidates:
                break
            if evaluations >= self._budget.max_evaluations:
                break
            child = await self._step(training)
            generations += 1
            if child is not None:
                evaluations += 1
                candidates_created += 1

        best = self._archive.best_overall() or seed
        holdout_scores, regressed = await self._holdout_audit(best, seed)
        if holdout_scores:
            evaluations += 1 if best.id == seed.id else 2
        return EvolutionReport(
            best=best,
            holdout_scores=holdout_scores,
            holdout_regressed=regressed,
            generations_run=generations,
            evaluations_used=evaluations,
        )

    async def _step(self, training: Sequence[str]) -> Candidate | None:
        """Run one generation; return the evaluated child, if any."""
        frontier = self._archive.pareto_frontier()
        if not frontier:
            return None
        parent = self._rng.choice(frontier)
        child_content = await self._mutator.mutate(parent, _worst_instances(parent))
        if child_content is None:
            logger.info("evolution_generation_no_child parent=%s", parent.id)
            return None
        child_scores = await self._evaluator(child_content, training)
        child = Candidate(
            content=child_content,
            parent_id=parent.id,
            generation=parent.generation + 1,
            fitness=_fitness(child_scores),
            instance_scores=child_scores,
        )
        if self._archive.add(child):
            await _audit_propose(child)
        return child

    async def _holdout_audit(
        self, best: Candidate, seed: Candidate
    ) -> tuple[dict[str, float], bool]:
        """Score ``best`` (and the seed baseline) on the held-out instances."""
        if not self._holdout:
            return {}, False
        best_holdout = await self._evaluator(best.content, self._holdout)
        if best.id == seed.id:
            return best_holdout, False
        seed_holdout = await self._evaluator(seed.content, self._holdout)
        regressed = _mean(best_holdout) < _mean(seed_holdout)
        if regressed:
            logger.warning(
                "evolution_holdout_regression best=%s best_mean=%.3f "
                "seed_mean=%.3f — likely evaluator gaming; do not land "
                "without review",
                best.id,
                _mean(best_holdout),
                _mean(seed_holdout),
            )
        return best_holdout, regressed


async def _audit_propose(child: Candidate) -> None:
    """Record an accepted mutation on the audit trail (never raises)."""
    try:
        from core.observability.audit import AuditEventType, get_audit_logger

        await get_audit_logger().log(
            AuditEventType.SELF_MODIFY_PROPOSE,
            resource=child.id,
            action="evolution.mutate",
            details={
                "parent_id": child.parent_id,
                "generation": child.generation,
                "quality": (
                    child.fitness.quality if child.fitness is not None else None
                ),
            },
        )
    except Exception:  # pragma: no cover - observability only
        logger.debug("evolution_audit_failed", exc_info=True)
