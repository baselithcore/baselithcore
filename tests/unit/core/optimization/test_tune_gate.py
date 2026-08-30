"""Tests for the eval gate on automated prompt tuning."""

from __future__ import annotations

import pytest
from core.optimization.tune_gate import (
    eval_gate_enabled,
    review_candidate,
)

from core.prompts.registry import PromptRegistry

pytestmark = [pytest.mark.unit]


class TestFlag:
    def test_default_off(self, monkeypatch):
        monkeypatch.delenv("BASELITH_OPTIMIZER_EVAL_GATE", raising=False)
        assert eval_gate_enabled() is False

    def test_enabled(self, monkeypatch):
        monkeypatch.setenv("BASELITH_OPTIMIZER_EVAL_GATE", "true")
        assert eval_gate_enabled() is True


class TestReviewCandidate:
    async def test_accept_registers_candidate_version(self, monkeypatch):
        registry = PromptRegistry()
        monkeypatch.setattr(
            "core.prompts.registry.get_prompt_registry", lambda: registry
        )

        async def evaluator(agent_id: str, prompt: str) -> float:
            return 0.95

        decision = await review_candidate(
            "researcher",
            "You are a rigorous researcher.",
            evaluator,
            threshold=0.9,
            register_as="agent:researcher",
        )
        assert decision.accepted is True
        assert decision.score == 0.95
        version = registry.get("agent:researcher", label="candidate")
        assert version.template == "You are a rigorous researcher."

    async def test_below_threshold_rejected(self):
        async def evaluator(agent_id: str, prompt: str) -> float:
            return 0.4

        decision = await review_candidate(
            "researcher", "weak prompt", evaluator, threshold=0.9
        )
        assert decision.accepted is False

    async def test_no_evaluator_fails_closed(self):
        decision = await review_candidate("researcher", "prompt", None)
        assert decision.accepted is False

    async def test_raising_evaluator_fails_closed(self):
        async def evaluator(agent_id: str, prompt: str) -> float:
            raise RuntimeError("eval suite unavailable")

        decision = await review_candidate("researcher", "prompt", evaluator)
        assert decision.accepted is False


class TestAutoTuneIntegration:
    async def test_gated_auto_tune_refuses_unvalidated_apply(self, monkeypatch):
        from core.learning.feedback import FeedbackCollector
        from core.optimization.optimizer import PromptOptimizer

        monkeypatch.setenv("BASELITH_OPTIMIZER_EVAL_GATE", "true")

        collector = FeedbackCollector()
        await collector.log_feedback("agent-x", "task-1", 0.2, "wrong answers")

        class _LLM:
            async def generate_response(self, prompt: str) -> str:
                return "New improved system prompt"

        optimizer = PromptOptimizer(collector)
        optimizer._llm_service = _LLM()

        applied_calls = []

        async def apply_fn(agent_id: str, new_prompt: str) -> bool:
            applied_calls.append(agent_id)
            return True

        result = await optimizer.auto_tune("agent-x", apply_fn, dry_run=False)
        # No tune evaluator configured + gate on = fail closed: no apply.
        assert result is not None
        assert result.applied is False
        assert applied_calls == []
