"""Tests for distilling STRATEGY patterns into the few-shot library."""

from __future__ import annotations

import pytest
from core.skill_evolution.distillation import (
    patterns_to_few_shot,
    sync_strategies_to_library,
)

from core.personas.few_shot import FewShotLibrary
from core.skill_evolution.store import InMemoryPatternStore
from core.skill_evolution.types import Pattern, PatternKind, PatternStatus

pytestmark = [pytest.mark.unit]

TASK = "fix_campaign"


def _pattern(
    *,
    fingerprint: str = "fp-1",
    kind: PatternKind = PatternKind.STRATEGY,
    status: PatternStatus = PatternStatus.PROMOTED,
    occurrences: int = 1,
    title: str = "Strategy: booking",
    summary: str = "Confirm availability before quoting a price.",
) -> Pattern:
    return Pattern(
        fingerprint=fingerprint,
        kind=kind,
        status=status,
        occurrences=occurrences,
        title=title,
        summary=summary,
    )


class TestFilters:
    def test_promoted_strategy_is_converted(self):
        library = FewShotLibrary()
        added = patterns_to_few_shot([_pattern()], library, task=TASK)
        assert added == 1
        assert len(library.select(TASK)) == 1

    def test_failure_mode_patterns_are_skipped(self):
        library = FewShotLibrary()
        added = patterns_to_few_shot(
            [_pattern(kind=PatternKind.FAILURE_MODE)], library, task=TASK
        )
        assert added == 0
        assert library.select(TASK) == []

    def test_candidate_below_min_occurrences_is_skipped(self):
        library = FewShotLibrary()
        added = patterns_to_few_shot(
            [_pattern(status=PatternStatus.CANDIDATE, occurrences=2)],
            library,
            task=TASK,
        )
        assert added == 0

    def test_candidate_at_min_occurrences_is_converted(self):
        library = FewShotLibrary()
        added = patterns_to_few_shot(
            [_pattern(status=PatternStatus.CANDIDATE, occurrences=3)],
            library,
            task=TASK,
        )
        assert added == 1

    def test_min_occurrences_is_tunable(self):
        library = FewShotLibrary()
        added = patterns_to_few_shot(
            [_pattern(status=PatternStatus.CANDIDATE, occurrences=3)],
            library,
            task=TASK,
            min_occurrences=5,
        )
        assert added == 0

    def test_retired_patterns_are_skipped(self):
        library = FewShotLibrary()
        added = patterns_to_few_shot(
            [_pattern(status=PatternStatus.RETIRED, occurrences=10)],
            library,
            task=TASK,
        )
        assert added == 0

    def test_blank_summary_is_skipped(self):
        library = FewShotLibrary()
        added = patterns_to_few_shot([_pattern(summary="   ")], library, task=TASK)
        assert added == 0


class TestIdempotency:
    def test_same_fingerprint_is_added_once(self):
        library = FewShotLibrary()
        pattern = _pattern()
        assert patterns_to_few_shot([pattern], library, task=TASK) == 1
        assert patterns_to_few_shot([pattern], library, task=TASK) == 0
        assert len(library.select(TASK)) == 1

    def test_dedup_is_per_task_bucket(self):
        library = FewShotLibrary()
        pattern = _pattern()
        assert patterns_to_few_shot([pattern], library, task=TASK) == 1
        assert patterns_to_few_shot([pattern], library, task="other_task") == 1

    def test_duplicate_fingerprints_within_one_batch_added_once(self):
        library = FewShotLibrary()
        added = patterns_to_few_shot(
            [_pattern(), _pattern(summary="Different wording.")],
            library,
            task=TASK,
        )
        assert added == 1


class TestExampleShape:
    def test_example_conforms_to_library_model(self):
        library = FewShotLibrary()
        pattern = _pattern()
        patterns_to_few_shot([pattern], library, task=TASK)

        [example] = library.select(TASK)
        assert example.input == pattern.title
        assert example.output == pattern.summary
        assert example.rationale
        assert f"pattern:{pattern.fingerprint}" in example.tags

    def test_examples_render_through_the_real_library(self):
        library = FewShotLibrary()
        patterns_to_few_shot([_pattern()], library, task=TASK)
        rendered = library.render(TASK)
        assert "Strategy: booking" in rendered
        assert "Confirm availability" in rendered


class TestSyncFromStore:
    async def test_sync_pulls_strategies_and_skips_failures(self):
        store = InMemoryPatternStore()
        await store.upsert(_pattern(fingerprint="fp-a", title="Strategy: a"))
        await store.upsert(
            _pattern(
                fingerprint="fp-b",
                kind=PatternKind.FAILURE_MODE,
                title="Failure: b",
            )
        )
        library = FewShotLibrary()

        added = await sync_strategies_to_library(store, library, task=TASK)

        assert added == 1
        [example] = library.select(TASK)
        assert example.input == "Strategy: a"

    async def test_sync_is_idempotent(self):
        store = InMemoryPatternStore()
        await store.upsert(_pattern())
        library = FewShotLibrary()

        assert await sync_strategies_to_library(store, library, task=TASK) == 1
        assert await sync_strategies_to_library(store, library, task=TASK) == 0
        assert len(library.select(TASK)) == 1

    async def test_sync_honors_min_occurrences_for_candidates(self):
        store = InMemoryPatternStore()
        await store.upsert(_pattern(status=PatternStatus.CANDIDATE, occurrences=1))
        library = FewShotLibrary()

        added = await sync_strategies_to_library(
            store, library, task=TASK, min_occurrences=2
        )
        assert added == 0
