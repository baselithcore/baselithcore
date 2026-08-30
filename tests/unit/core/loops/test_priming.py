"""Tests for lesson priming — BM25 retrieval of past patterns for a goal."""

from __future__ import annotations

import pytest
from core.loops.priming import MAX_PRIMER_CHARS, prime_lessons

from core.loops.flow_handler import LoopFlowHandler
from core.skill_evolution.store import InMemoryPatternStore
from core.skill_evolution.types import Pattern, PatternKind, PatternStatus

pytestmark = [pytest.mark.unit]


def _pattern(
    *,
    fingerprint: str,
    title: str,
    summary: str,
    kind: PatternKind = PatternKind.FAILURE_MODE,
    status: PatternStatus = PatternStatus.CANDIDATE,
) -> Pattern:
    return Pattern(
        fingerprint=fingerprint,
        kind=kind,
        title=title,
        summary=summary,
        status=status,
    )


async def _seeded_store() -> InMemoryPatternStore:
    store = InMemoryPatternStore()
    await store.upsert(
        _pattern(
            fingerprint="fp-flaky",
            title="Flaky checkout test",
            summary="The checkout integration test fails when retries are disabled.",
        )
    )
    await store.upsert(
        _pattern(
            fingerprint="fp-migrations",
            title="Migration ordering",
            summary="Alembic migration ordering broke the tenants table backfill.",
            kind=PatternKind.STRATEGY,
        )
    )
    return store


class TestRelevance:
    async def test_relevant_pattern_ranks_first(self):
        store = await _seeded_store()
        block = await prime_lessons("fix the flaky checkout test", store)
        assert "Lessons from past campaigns" in block
        assert block.index("Flaky checkout test") < len(block)
        first_bullet = block.splitlines()[1]
        assert "Flaky checkout test" in first_bullet

    async def test_irrelevant_goal_returns_empty_string(self):
        store = await _seeded_store()
        assert await prime_lessons("zzzz qqqq unrelatedwords", store) == ""

    async def test_empty_store_returns_empty_string(self):
        assert await prime_lessons("any goal at all", InMemoryPatternStore()) == ""

    async def test_kinds_filter_excludes_other_kinds(self):
        store = await _seeded_store()
        block = await prime_lessons(
            "alembic migration ordering tenants",
            store,
            kinds=(PatternKind.FAILURE_MODE,),
        )
        assert "Migration ordering" not in block

    async def test_retired_patterns_are_excluded(self):
        store = InMemoryPatternStore()
        await store.upsert(
            _pattern(
                fingerprint="fp-old",
                title="Retired checkout lesson",
                summary="Old checkout advice.",
                status=PatternStatus.RETIRED,
            )
        )
        assert await prime_lessons("checkout advice", store) == ""


class TestBounds:
    async def test_k_caps_the_bullet_count(self):
        store = InMemoryPatternStore()
        for i in range(6):
            await store.upsert(
                _pattern(
                    fingerprint=f"fp-{i}",
                    title=f"Checkout lesson {i}",
                    summary=f"Checkout detail number {i}.",
                )
            )
        block = await prime_lessons("checkout lesson", store, k=2)
        bullets = [line for line in block.splitlines() if line.startswith("- ")]
        assert len(bullets) == 2

    async def test_block_stays_within_char_bound(self):
        store = InMemoryPatternStore()
        for i in range(5):
            await store.upsert(
                _pattern(
                    fingerprint=f"fp-long-{i}",
                    title=f"Checkout failure mode {i} " + "verbose " * 20,
                    summary=("checkout retry backoff detail " * 40) + str(i),
                )
            )
        block = await prime_lessons("checkout retry backoff", store, k=5)
        assert block
        assert len(block) <= MAX_PRIMER_CHARS

    async def test_invalid_k_raises(self):
        with pytest.raises(ValueError):
            await prime_lessons("goal", InMemoryPatternStore(), k=0)


class TestFlowHandlerPriming:
    """LoopFlowHandler's opt-in seam: prepend the primer to the run goal."""

    @staticmethod
    def _capturing_handler(**kwargs) -> tuple[LoopFlowHandler, list[str]]:
        seen_goals: list[str] = []

        def act_factory(query, context):
            async def act(attempt_context):
                seen_goals.append(attempt_context.goal)

            return act

        def verify_factory(query, context):
            async def verify():
                return True, "verified"

            return verify

        return LoopFlowHandler(act_factory, verify_factory, **kwargs), seen_goals

    async def test_primed_block_reaches_the_actor_goal(self):
        store = await _seeded_store()
        handler, seen_goals = self._capturing_handler(
            max_attempts=1, pattern_store=store
        )

        result = await handler.handle("fix the flaky checkout test", {})

        assert not result.get("error")
        assert len(seen_goals) == 1
        assert seen_goals[0].startswith("Lessons from past campaigns")
        assert "Flaky checkout test" in seen_goals[0]
        assert seen_goals[0].endswith("fix the flaky checkout test")

    async def test_without_store_goal_is_untouched(self):
        handler, seen_goals = self._capturing_handler(max_attempts=1)
        await handler.handle("fix the flaky checkout test", {})
        assert seen_goals == ["fix the flaky checkout test"]

    async def test_irrelevant_lessons_leave_goal_untouched(self):
        store = await _seeded_store()
        handler, seen_goals = self._capturing_handler(
            max_attempts=1, pattern_store=store
        )
        await handler.handle("zzzz qqqq unrelatedwords", {})
        assert seen_goals == ["zzzz qqqq unrelatedwords"]

    async def test_priming_failure_is_fail_soft(self):
        class ExplodingStore:
            async def list_patterns(self, **kwargs):
                raise RuntimeError("store down")

        handler, seen_goals = self._capturing_handler(
            max_attempts=1, pattern_store=ExplodingStore()
        )
        result = await handler.handle("fix the flaky checkout test", {})
        assert not result.get("error")
        assert seen_goals == ["fix the flaky checkout test"]
