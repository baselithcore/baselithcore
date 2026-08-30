"""Tests for the multi-model bake-off harness."""

from __future__ import annotations

import pytest

from core.evaluation.bake_off import run_bake_off
from core.evaluation.prompt_eval import EvalCase

pytestmark = [pytest.mark.unit]


class _LLM:
    """Mock chat LLM whose answer quality depends on the model name."""

    def __init__(self, model: str) -> None:
        self.model = model

    async def generate_response(self, prompt: str, **kwargs) -> str:
        if self.model == "strong-model":
            return "Paris is the capital of France."
        return "I am not sure."


def _cases() -> list[EvalCase]:
    return [
        EvalCase(
            name="capital",
            user_input="What is the capital of France?",
            expected_keywords=["Paris"],
        )
    ]


class TestRunBakeOff:
    async def test_matrix_ranks_models(self):
        result = await run_bake_off(
            system_prompt="You are a geography tutor.",
            cases=_cases(),
            models=["strong-model", "weak-model"],
            llm_factory=lambda model: _LLM(model),
        )
        by_model = {row.model: row for row in result.rows}
        assert by_model["strong-model"].report.pass_rate == 1.0
        assert by_model["weak-model"].report.pass_rate == 0.0
        assert result.best().model == "strong-model"

    async def test_cost_estimator_populates_column(self):
        result = await run_bake_off(
            system_prompt="s",
            cases=_cases(),
            models=["strong-model"],
            llm_factory=lambda model: _LLM(model),
            cost_estimator=lambda model, report: 0.042,
        )
        assert result.rows[0].cost_usd == 0.042
        assert "strong-model" in result.summary()
