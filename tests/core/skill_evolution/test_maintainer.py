"""Unit tests for WikiMaintainer distillation."""

from __future__ import annotations

from core.loops.lessons import Lesson
from core.skill_evolution.maintainer import WikiMaintainer
from core.skill_evolution.store import InMemoryPatternStore
from core.skill_evolution.types import PatternKind

FAILURE_EVENT = {
    "score": 0.1,
    "intent": "parse-report",
    "feedback": "ERROR: AssertionError in parser\nFAILED tests/test_x.py",
    "run_id": "run-1",
}


async def test_low_score_creates_failure_pattern() -> None:
    store = InMemoryPatternStore()
    maintainer = WikiMaintainer(store)
    pattern = await maintainer.distill_evaluation(FAILURE_EVENT)
    assert pattern is not None
    assert pattern.kind is PatternKind.FAILURE_MODE
    assert "parse-report" in pattern.title
    assert "AssertionError" in pattern.summary
    assert pattern.evidence[-1].run_id == "run-1"
    assert pattern.evidence[-1].score == 0.1


async def test_identical_failures_dedup() -> None:
    store = InMemoryPatternStore()
    maintainer = WikiMaintainer(store)
    await maintainer.distill_evaluation(FAILURE_EVENT)
    merged = await maintainer.distill_evaluation({**FAILURE_EVENT, "run_id": "run-2"})
    assert merged is not None
    assert merged.occurrences == 2
    assert len(await store.list_patterns()) == 1


async def test_high_score_creates_strategy_pattern() -> None:
    store = InMemoryPatternStore()
    maintainer = WikiMaintainer(store)
    pattern = await maintainer.distill_evaluation(
        {"score": 0.95, "intent": "summarize", "response": "Use the outline tool"}
    )
    assert pattern is not None
    assert pattern.kind is PatternKind.STRATEGY
    assert "summarize" in pattern.title


async def test_mid_score_is_ignored() -> None:
    maintainer = WikiMaintainer(InMemoryPatternStore())
    assert await maintainer.distill_evaluation({"score": 0.6}) is None


async def test_unscored_payload_is_skipped_not_fabricated() -> None:
    maintainer = WikiMaintainer(InMemoryPatternStore())
    # score=None (present) and score missing must both be skipped — a
    # score-less payload with feedback must never become a failure pattern.
    assert (
        await maintainer.distill_evaluation({"score": None, "feedback": "ERROR x"})
        is None
    )
    assert await maintainer.distill_evaluation({"feedback": "minor note"}) is None


async def test_integer_zero_run_id_is_preserved() -> None:
    store = InMemoryPatternStore()
    maintainer = WikiMaintainer(store)
    pattern = await maintainer.distill_evaluation({**FAILURE_EVENT, "run_id": 0})
    assert pattern is not None
    assert pattern.evidence[-1].run_id == "0"


async def test_empty_failure_evidence_is_ignored() -> None:
    maintainer = WikiMaintainer(InMemoryPatternStore())
    assert await maintainer.distill_evaluation({"score": 0.1, "feedback": ""}) is None


async def test_rca_summary_used_when_available() -> None:
    async def rca(_evidence: str) -> str:
        return "Root cause: parser assumes UTF-8"

    maintainer = WikiMaintainer(InMemoryPatternStore(), rca=rca)
    pattern = await maintainer.distill_evaluation(FAILURE_EVENT)
    assert pattern is not None
    assert pattern.summary == "Root cause: parser assumes UTF-8"


async def test_rca_failure_falls_back_to_compact_evidence() -> None:
    async def rca(_evidence: str) -> str:
        raise RuntimeError("llm down")

    maintainer = WikiMaintainer(InMemoryPatternStore(), rca=rca)
    pattern = await maintainer.distill_evaluation(FAILURE_EVENT)
    assert pattern is not None
    assert "AssertionError" in pattern.summary


async def test_persist_lessons_bridges_lesson_log() -> None:
    store = InMemoryPatternStore()
    maintainer = WikiMaintainer(store)
    lessons = [
        Lesson(attempt=1, fingerprint="fp-a", summary="ERROR timeout"),
        Lesson(attempt=2, fingerprint="fp-b", summary="ERROR schema"),
    ]
    patterns = await maintainer.persist_lessons(lessons, run_id="run-9")
    assert [p.fingerprint for p in patterns] == ["fp-a", "fp-b"]
    assert all(p.kind is PatternKind.FAILURE_MODE for p in patterns)
    assert len(await store.list_patterns()) == 2
    assert patterns[0].evidence[-1].run_id == "run-9"
