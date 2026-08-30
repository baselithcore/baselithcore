"""Tests for the rubric-graded loop verifier."""

from __future__ import annotations

import pytest

from core.loops.rubric import RubricJudge, rubric_verifier

pytestmark = [pytest.mark.unit]


class _LLM:
    """Mock LLM service returning a canned judge verdict."""

    def __init__(self, score: float) -> None:
        self._score = score
        self.prompts: list[str] = []

    async def generate_response(self, prompt: str, json: bool = False) -> str:
        self.prompts.append(prompt)
        import json as json_module

        return json_module.dumps(
            {"feedback": "graded", "score": self._score, "should_refine": False}
        )


class TestRubricJudge:
    async def test_prompt_carries_rubric_and_output(self):
        llm = _LLM(score=0.9)
        judge = RubricJudge(rubric="Answer must cite a source.", llm_service=llm)
        result = await judge.evaluate("the answer [1]", "the goal")
        assert result.score == 0.9
        assert "Answer must cite a source." in llm.prompts[0]
        assert "the answer [1]" in llm.prompts[0]


class TestRubricVerifier:
    async def test_passes_at_threshold(self):
        verifier = rubric_verifier(
            "clear and complete",
            lambda: "final output",
            goal="write the summary",
            threshold=0.8,
            llm_service=_LLM(score=0.85),
        )
        done, evidence = await verifier()
        assert done is True
        assert "0.85" in evidence

    async def test_fails_below_threshold(self):
        verifier = rubric_verifier(
            "clear and complete",
            lambda: "draft",
            goal="write the summary",
            threshold=0.8,
            llm_service=_LLM(score=0.4),
        )
        done, evidence = await verifier()
        assert done is False
        assert "graded" in evidence
