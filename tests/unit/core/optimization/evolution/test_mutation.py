"""Tests for the bounded reflective mutator (edits enforced by code)."""

from __future__ import annotations

import pytest

from core.optimization.evolution import Candidate, ReflectiveMutator

pytestmark = [pytest.mark.unit]

PARENT_CONTENT = "line 1\nline 2\nline 3\nline 4\nline 5"


def _parent() -> Candidate:
    return Candidate(id="p1", content=PARENT_CONTENT, generation=0)


def _mutator_returning(text: str, *, max_changed_lines: int = 2) -> ReflectiveMutator:
    async def generate(prompt: str) -> str:
        return text

    return ReflectiveMutator(generate, max_changed_lines=max_changed_lines)


class TestBoundEnforcement:
    async def test_within_bound_edit_accepted(self):
        child = "line 1\nline 2 CHANGED\nline 3\nline 4\nline 5"
        mutator = _mutator_returning(child, max_changed_lines=2)
        result = await mutator.mutate(_parent(), ["failed on i1"])
        assert result == child

    async def test_over_bound_edit_rejected(self):
        child = "A\nB\nC\nline 4\nline 5"  # 3 changed lines, bound is 2
        mutator = _mutator_returning(child, max_changed_lines=2)
        assert await mutator.mutate(_parent(), []) is None

    async def test_insertions_count_as_changed_lines(self):
        child = PARENT_CONTENT + "\nnew 1\nnew 2\nnew 3"
        mutator = _mutator_returning(child, max_changed_lines=2)
        assert await mutator.mutate(_parent(), []) is None

    async def test_identical_output_rejected(self):
        mutator = _mutator_returning(PARENT_CONTENT)
        assert await mutator.mutate(_parent(), []) is None

    async def test_empty_output_rejected(self):
        mutator = _mutator_returning("   \n  ")
        assert await mutator.mutate(_parent(), []) is None

    async def test_full_rewrite_within_default_bound(self):
        # Default bound is 20 changed lines; a 5-line rewrite passes.
        child = "a\nb\nc\nd\ne"

        async def generate(prompt: str) -> str:
            return child

        mutator = ReflectiveMutator(generate)
        assert await mutator.mutate(_parent(), []) == child


class TestConstruction:
    def test_invalid_bound_rejected(self):
        async def generate(prompt: str) -> str:
            return ""

        with pytest.raises(ValueError):
            ReflectiveMutator(generate, max_changed_lines=0)


class TestPromptConstruction:
    async def test_prompt_carries_parent_failures_and_bound(self):
        prompts: list[str] = []

        async def generate(prompt: str) -> str:
            prompts.append(prompt)
            return "line 1\nline 2 CHANGED\nline 3\nline 4\nline 5"

        mutator = ReflectiveMutator(generate, max_changed_lines=7)
        await mutator.mutate(_parent(), ["instance i1: score 0.100"])

        assert len(prompts) == 1
        prompt = prompts[0]
        assert PARENT_CONTENT in prompt
        assert "instance i1: score 0.100" in prompt
        assert "7" in prompt  # the bound is stated in the instruction
