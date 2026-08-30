"""End-to-end tests for the evolution engine (fake evaluator, no LLM)."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from core.observability import audit as audit_module
from core.observability.audit import AuditEventType
from core.optimization.evolution import (
    Candidate,
    CandidateArchive,
    EvolutionBudget,
    EvolutionEngine,
)

pytestmark = [pytest.mark.unit]


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list = []

    async def write(self, event) -> None:
        self.events.append(event)


@pytest.fixture
def audit_sink(monkeypatch):
    sink = _RecordingSink()
    logger = audit_module.AuditLogger(sinks=[sink])
    monkeypatch.setattr(audit_module, "get_audit_logger", lambda: logger)
    return sink


class _SequenceMutator:
    """Fake mutator yielding a fixed sequence of children; records parents."""

    def __init__(self, children: Sequence[str]) -> None:
        self._children = list(children)
        self.parents: list[str] = []

    async def mutate(self, parent: Candidate, failures: list[str]) -> str | None:
        self.parents.append(parent.content)
        if not self._children:
            return None
        return self._children.pop(0)


class _TableEvaluator:
    """Deterministic per-instance scores from a content-keyed table."""

    def __init__(
        self,
        table: dict[str, dict[str, float]],
        default: float = 0.5,
    ) -> None:
        self._table = table
        self._default = default
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    async def __call__(
        self, content: str, instances: Sequence[str]
    ) -> dict[str, float]:
        self.calls.append((content, tuple(instances)))
        row = self._table.get(content, {})
        return {i: row.get(i, self._default) for i in instances}


class TestEvolutionRun:
    async def test_best_improves_across_generations(self, audit_sink):
        evaluator = _TableEvaluator(
            {
                "v0": {"i1": 0.5, "i2": 0.4, "h1": 0.5},
                "v1": {"i1": 0.7, "i2": 0.6, "h1": 0.6},
                "v2": {"i1": 0.9, "i2": 0.8, "h1": 0.7},
            }
        )
        engine = EvolutionEngine(
            CandidateArchive(),
            _SequenceMutator(["v1", "v2"]),
            evaluator,
            budget=EvolutionBudget(
                max_generations=2, max_candidates=10, max_evaluations=10
            ),
            holdout_instances=("h1",),
        )

        report = await engine.run("v0", ("i1", "i2", "h1"))

        assert report.best.content == "v2"
        assert report.best.fitness is not None
        assert report.best.fitness.quality == pytest.approx(0.85)
        assert report.generations_run == 2
        # seed + 2 children on training, then best + seed on holdout.
        assert report.evaluations_used == 5
        assert report.holdout_scores == {"h1": 0.7}
        assert report.holdout_regressed is False
        # Holdout instances are never part of the training evaluations.
        training_calls = [c for c in evaluator.calls if "h1" not in c[1]]
        assert all(set(c[1]) == {"i1", "i2"} for c in training_calls)
        assert len(training_calls) == 3

    async def test_max_evaluations_respected_exactly(self, audit_sink):
        evaluator = _TableEvaluator({"v0": {"i1": 0.5}, "v1": {"i1": 0.6}})
        engine = EvolutionEngine(
            CandidateArchive(),
            _SequenceMutator(["v1", "v2", "v3"]),
            evaluator,
            budget=EvolutionBudget(
                max_generations=5, max_candidates=10, max_evaluations=2
            ),
        )

        report = await engine.run("v0", ("i1",))

        assert report.evaluations_used == 2  # seed + one child, then stop
        assert report.generations_run == 1
        assert len(evaluator.calls) == 2

    async def test_max_candidates_stops_before_first_child(self, audit_sink):
        evaluator = _TableEvaluator({"v0": {"i1": 0.5}})
        mutator = _SequenceMutator(["v1"])
        engine = EvolutionEngine(
            CandidateArchive(),
            mutator,
            evaluator,
            budget=EvolutionBudget(
                max_generations=5, max_candidates=1, max_evaluations=10
            ),
        )

        report = await engine.run("v0", ("i1",))

        assert report.generations_run == 0
        assert report.evaluations_used == 1  # the seed only
        assert mutator.parents == []
        assert report.best.content == "v0"

    async def test_max_generations_respected(self, audit_sink):
        evaluator = _TableEvaluator({"v0": {"i1": 0.5}})
        engine = EvolutionEngine(
            CandidateArchive(),
            _SequenceMutator(["v1", "v2", "v3", "v4"]),
            evaluator,
            budget=EvolutionBudget(
                max_generations=3, max_candidates=100, max_evaluations=100
            ),
        )

        report = await engine.run("v0", ("i1",))

        assert report.generations_run == 3

    async def test_holdout_regression_flagged_on_overfit(self, audit_sink):
        # v1 overfits training i1 and collapses on the held-out h1.
        evaluator = _TableEvaluator(
            {
                "v0": {"i1": 0.5, "h1": 0.8},
                "v1": {"i1": 0.9, "h1": 0.4},
            }
        )
        engine = EvolutionEngine(
            CandidateArchive(),
            _SequenceMutator(["v1"]),
            evaluator,
            budget=EvolutionBudget(
                max_generations=1, max_candidates=10, max_evaluations=10
            ),
            holdout_instances=("h1",),
        )

        report = await engine.run("v0", ("i1", "h1"))

        assert report.best.content == "v1"  # search still reports its best
        assert report.holdout_scores == {"h1": 0.4}
        assert report.holdout_regressed is True  # ...flagged as gamed

    async def test_audit_propose_emitted_per_accepted_child(self, audit_sink):
        evaluator = _TableEvaluator(
            {
                "v0": {"i1": 0.5},
                "v1": {"i1": 0.6},
                "v2": {"i1": 0.7},
            }
        )
        archive = CandidateArchive()
        engine = EvolutionEngine(
            archive,
            _SequenceMutator(["v1", "v2"]),
            evaluator,
            budget=EvolutionBudget(
                max_generations=2, max_candidates=10, max_evaluations=10
            ),
        )

        await engine.run("v0", ("i1",))

        proposed = [
            e
            for e in audit_sink.events
            if e.event_type == AuditEventType.SELF_MODIFY_PROPOSE
        ]
        assert len(proposed) == 2
        child_ids = {c.id for c in archive.all() if c.parent_id is not None}
        assert {e.resource for e in proposed} == child_ids
        assert all(e.details["parent_id"] for e in proposed)

    async def test_rejected_mutation_consumes_generation_without_child(
        self, audit_sink
    ):
        evaluator = _TableEvaluator({"v0": {"i1": 0.5}})
        engine = EvolutionEngine(
            CandidateArchive(),
            _SequenceMutator([]),  # mutator always returns None
            evaluator,
            budget=EvolutionBudget(
                max_generations=2, max_candidates=10, max_evaluations=10
            ),
        )

        report = await engine.run("v0", ("i1",))

        assert report.generations_run == 2
        assert report.evaluations_used == 1  # seed only, no child evaluated
        assert report.best.content == "v0"

    async def test_seeded_selection_is_deterministic(self, audit_sink):
        # v0 and c1 split the frontier (each best on one instance), so from
        # generation 2 onward parent selection is a genuine random choice.
        table = {
            "v0": {"i1": 0.9, "i2": 0.1},
            "c1": {"i1": 0.1, "i2": 0.9},
        }

        async def _run() -> list[str]:
            mutator = _SequenceMutator(["c1", "c2", "c3", "c4", "c5"])
            engine = EvolutionEngine(
                CandidateArchive(),
                mutator,
                _TableEvaluator(table),
                budget=EvolutionBudget(
                    max_generations=5, max_candidates=100, max_evaluations=100
                ),
                rng_seed=42,
            )
            await engine.run("v0", ("i1", "i2"))
            return mutator.parents

        assert await _run() == await _run()

    async def test_archive_rejection_emits_no_audit(self, audit_sink):
        # A full all-frontier archive refuses the child: evaluated, but not
        # accepted, so no SELF_MODIFY_PROPOSE lands on the trail.
        evaluator = _TableEvaluator({"v0": {"i1": 0.9}, "v1": {"i1": 0.1}})
        engine = EvolutionEngine(
            CandidateArchive(max_candidates=1),
            _SequenceMutator(["v1"]),
            evaluator,
            budget=EvolutionBudget(
                max_generations=1, max_candidates=10, max_evaluations=10
            ),
        )

        report = await engine.run("v0", ("i1",))

        assert report.best.content == "v0"
        proposed = [
            e
            for e in audit_sink.events
            if e.event_type == AuditEventType.SELF_MODIFY_PROPOSE
        ]
        assert proposed == []

    async def test_no_training_instances_raises(self, audit_sink):
        evaluator = _TableEvaluator({})
        engine = EvolutionEngine(
            CandidateArchive(),
            _SequenceMutator([]),
            evaluator,
            budget=EvolutionBudget(
                max_generations=1, max_candidates=10, max_evaluations=10
            ),
            holdout_instances=("i1",),
        )
        with pytest.raises(ValueError):
            await engine.run("v0", ("i1",))

    async def test_best_equal_to_seed_skips_duplicate_holdout_eval(self, audit_sink):
        evaluator = _TableEvaluator({"v0": {"i1": 0.5, "h1": 0.6}})
        engine = EvolutionEngine(
            CandidateArchive(),
            _SequenceMutator([]),
            evaluator,
            budget=EvolutionBudget(
                max_generations=1, max_candidates=10, max_evaluations=10
            ),
            holdout_instances=("h1",),
        )

        report = await engine.run("v0", ("i1", "h1"))

        assert report.best.content == "v0"
        assert report.holdout_regressed is False
        assert report.evaluations_used == 2  # seed training + one holdout
