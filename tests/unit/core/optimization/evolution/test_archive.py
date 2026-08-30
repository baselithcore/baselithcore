"""Tests for the per-instance Pareto candidate archive."""

from __future__ import annotations

import pytest

from core.optimization.evolution import Candidate, CandidateArchive
from core.skill_evolution.types import FitnessVector

pytestmark = [pytest.mark.unit]


def _cand(
    cid: str,
    scores: dict[str, float],
    *,
    generation: int = 0,
    quality: float | None = None,
    content: str = "text",
) -> Candidate:
    fitness = None if quality is None else FitnessVector(quality=quality)
    return Candidate(
        id=cid,
        content=content,
        generation=generation,
        fitness=fitness,
        instance_scores=scores,
    )


class TestParetoFrontier:
    def test_best_on_one_instance_stays_on_frontier(self):
        archive = CandidateArchive()
        archive.add(_cand("a", {"i1": 0.9, "i2": 0.1}))
        archive.add(_cand("b", {"i1": 0.1, "i2": 0.9}))
        # c is dominated everywhere: best on zero instances.
        archive.add(_cand("c", {"i1": 0.5, "i2": 0.5}))

        frontier_ids = {c.id for c in archive.pareto_frontier()}
        assert frontier_ids == {"a", "b"}

    def test_dominated_everywhere_leaves_frontier(self):
        archive = CandidateArchive()
        archive.add(_cand("a", {"i1": 0.9, "i2": 0.9}))
        archive.add(_cand("b", {"i1": 0.5, "i2": 0.5}))

        frontier_ids = {c.id for c in archive.pareto_frontier()}
        assert frontier_ids == {"a"}

    def test_tie_goes_to_earliest_generation(self):
        archive = CandidateArchive()
        archive.add(_cand("late", {"i1": 0.9}, generation=3))
        archive.add(_cand("early", {"i1": 0.9}, generation=1))

        frontier_ids = {c.id for c in archive.pareto_frontier()}
        assert frontier_ids == {"early"}

    def test_candidate_without_instance_scores_is_off_frontier(self):
        archive = CandidateArchive()
        archive.add(_cand("scored", {"i1": 0.2}))
        archive.add(_cand("unscored", {}))

        frontier_ids = {c.id for c in archive.pareto_frontier()}
        assert frontier_ids == {"scored"}

    def test_empty_archive_has_empty_frontier(self):
        assert CandidateArchive().pareto_frontier() == []


class TestBestOverall:
    def test_best_overall_uses_scalarized_fitness(self):
        archive = CandidateArchive()
        archive.add(_cand("low", {"i1": 0.4}, quality=0.4))
        archive.add(_cand("high", {"i1": 0.9}, quality=0.9))
        archive.add(_cand("nofit", {"i1": 1.0}))  # no fitness: ignored

        best = archive.best_overall()
        assert best is not None
        assert best.id == "high"

    def test_best_overall_none_when_nothing_has_fitness(self):
        archive = CandidateArchive()
        archive.add(_cand("nofit", {"i1": 1.0}))
        assert archive.best_overall() is None


class TestBoundedArchive:
    def test_eviction_removes_worst_off_frontier_never_frontier(self):
        archive = CandidateArchive(max_candidates=3)
        archive.add(_cand("a", {"i1": 0.9, "i2": 0.1}, quality=0.5))
        archive.add(_cand("b", {"i1": 0.1, "i2": 0.9}, quality=0.5))
        # Dominated everywhere and worst scalar fitness: eviction target.
        archive.add(_cand("c", {"i1": 0.2, "i2": 0.2}, quality=0.2))

        assert archive.add(_cand("d", {"i1": 0.5, "i2": 0.5}, quality=0.5))

        ids = {c.id for c in archive.all()}
        assert ids == {"a", "b", "d"}
        assert len(archive.all()) == 3

    def test_add_rejected_when_all_members_are_frontier(self):
        archive = CandidateArchive(max_candidates=2)
        archive.add(_cand("a", {"i1": 0.9, "i2": 0.1}))
        archive.add(_cand("b", {"i1": 0.1, "i2": 0.9}))

        accepted = archive.add(_cand("c", {"i1": 0.5, "i2": 0.5}))

        assert accepted is False
        assert {c.id for c in archive.all()} == {"a", "b"}

    def test_add_and_get_roundtrip(self):
        archive = CandidateArchive()
        cand = _cand("a", {"i1": 0.5})
        assert archive.add(cand) is True
        assert archive.get("a") == cand
        assert archive.get("missing") is None

    def test_duplicate_id_rejected(self):
        archive = CandidateArchive()
        archive.add(_cand("a", {"i1": 0.5}))
        with pytest.raises(ValueError):
            archive.add(_cand("a", {"i1": 0.7}))

    def test_len_tracks_members(self):
        archive = CandidateArchive()
        assert len(archive) == 0
        archive.add(_cand("a", {"i1": 0.5}))
        assert len(archive) == 1

    def test_invalid_bound_rejected(self):
        with pytest.raises(ValueError):
            CandidateArchive(max_candidates=0)

    def test_eviction_prefers_unevaluated_candidate(self):
        archive = CandidateArchive(max_candidates=2)
        archive.add(_cand("a", {"i1": 0.9}, quality=0.9))
        # No fitness: treated as worse than any evaluated candidate.
        archive.add(_cand("b", {"i1": 0.1}))

        assert archive.add(_cand("c", {"i1": 0.5}, quality=0.5))
        assert {c.id for c in archive.all()} == {"a", "c"}
