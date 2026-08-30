"""Bounded candidate archive with a per-instance Pareto frontier.

A scalar-only leaderboard collapses the population onto one gradient and
loses every specialist. This archive keeps GEPA's insight instead: a
candidate stays on the frontier if it is the strict best on *at least one*
evaluation instance, so diverse partial winners survive as mutation
material. The bound evicts dominated candidates only — frontier knowledge
is never silently dropped.
"""

from __future__ import annotations

from core.observability.logging import get_logger
from core.optimization.evolution.types import Candidate

logger = get_logger(__name__)

__all__ = ["CandidateArchive"]


class CandidateArchive:
    """In-memory candidate store with Pareto-aware bounded eviction.

    Args:
        max_candidates: Maximum candidates retained; ``None`` means
            unbounded. When full, adding evicts the worst *off-frontier*
            member (lowest scalarized fitness, unevaluated first). If every
            member is on the frontier the add is rejected and logged — the
            archive never trades away frontier knowledge silently.
    """

    def __init__(self, *, max_candidates: int | None = None) -> None:
        if max_candidates is not None and max_candidates < 1:
            raise ValueError("max_candidates must be >= 1")
        self._max_candidates = max_candidates
        self._candidates: dict[str, Candidate] = {}

    def __len__(self) -> int:
        return len(self._candidates)

    def add(self, candidate: Candidate) -> bool:
        """Add a candidate, evicting a dominated member if at capacity.

        Args:
            candidate: The candidate to store. Its ``id`` must be new.

        Returns:
            ``True`` if stored; ``False`` if rejected because the archive
            is full and every current member sits on the Pareto frontier.

        Raises:
            ValueError: If a candidate with the same id is already stored.
        """
        if candidate.id in self._candidates:
            raise ValueError(f"candidate {candidate.id!r} already archived")
        if (
            self._max_candidates is not None
            and len(self._candidates) >= self._max_candidates
        ):
            evicted = self._evict_worst_off_frontier()
            if evicted is None:
                logger.warning(
                    "candidate_archive_add_rejected id=%s size=%d "
                    "reason=all_members_on_frontier",
                    candidate.id,
                    len(self._candidates),
                )
                return False
            logger.info("candidate_archive_evicted id=%s for=%s", evicted, candidate.id)
        self._candidates[candidate.id] = candidate
        return True

    def get(self, candidate_id: str) -> Candidate | None:
        """Return the candidate with this id, or ``None``."""
        return self._candidates.get(candidate_id)

    def all(self) -> list[Candidate]:
        """All candidates in insertion order."""
        return list(self._candidates.values())

    def pareto_frontier(self) -> list[Candidate]:
        """Candidates holding the strict best score on >= 1 instance.

        Per instance, the slot goes to the highest score; among exact ties
        the earliest generation wins (then earliest insertion). Candidates
        with no ``instance_scores`` can hold no slot and are off-frontier.

        Returns:
            Frontier members in archive insertion order.
        """
        members = list(self._candidates.values())
        winner_ids: set[str] = set()
        instances = {i for c in members for i in c.instance_scores}
        for instance in instances:
            scored = [
                (idx, c)
                for idx, c in enumerate(members)
                if instance in c.instance_scores
            ]
            best_score = max(c.instance_scores[instance] for _, c in scored)
            winner = min(
                (
                    (idx, c)
                    for idx, c in scored
                    if c.instance_scores[instance] == best_score
                ),
                key=lambda pair: (pair[1].generation, pair[0]),
            )[1]
            winner_ids.add(winner.id)
        return [c for c in members if c.id in winner_ids]

    def best_overall(self) -> Candidate | None:
        """The evaluated candidate with the highest scalarized fitness.

        Returns:
            The best candidate by ``fitness.scalarize()`` (earliest
            insertion wins exact ties), or ``None`` if no candidate has a
            fitness yet.
        """
        best: Candidate | None = None
        best_score = float("-inf")
        for candidate in self._candidates.values():
            if candidate.fitness is None:
                continue
            score = candidate.fitness.scalarize()
            if score > best_score:
                best, best_score = candidate, score
        return best

    def _evict_worst_off_frontier(self) -> str | None:
        """Evict the worst dominated member; ``None`` if all are frontier."""
        frontier_ids = {c.id for c in self.pareto_frontier()}
        evictable = [
            (idx, c)
            for idx, c in enumerate(self._candidates.values())
            if c.id not in frontier_ids
        ]
        if not evictable:
            return None
        _, victim = min(
            evictable,
            key=lambda pair: (
                pair[1].fitness.scalarize()
                if pair[1].fitness is not None
                else float("-inf"),
                pair[0],
            ),
        )
        del self._candidates[victim.id]
        return victim.id
