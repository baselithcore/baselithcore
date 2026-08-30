"""Synchronous groundedness rail on the outbound guard pipeline.

Pins ``BASELITH_OUTPUT_GROUNDEDNESS`` (``off`` default | ``annotate`` |
``block``): when a result carries retrieved source material (``sources`` /
``context``), the FaithfulnessEvaluator judges the response against it —
annotating a score, or in ``block`` mode replacing an ungrounded response and
emitting the guardrail block metric. Judge failures are strictly fail-open.
"""

from __future__ import annotations

from typing import Any

import pytest

from core.evaluation.protocols import EvaluationResult, QualityLevel
from core.orchestration import guard_groundedness
from core.orchestration.guard_pipeline import guard_output_async


class _StubJudge:
    """FaithfulnessEvaluator stand-in with a canned verdict."""

    def __init__(
        self,
        score: float = 0.9,
        should_refine: bool = False,
        raises: bool = False,
        fallback: bool = False,
    ) -> None:
        self._score = score
        self._should_refine = should_refine
        self._raises = raises
        self._fallback = fallback
        self.calls = 0
        self.contexts: list[dict[str, Any] | None] = []

    async def evaluate(
        self, response: str, query: str, context: dict[str, Any] | None = None
    ) -> EvaluationResult:
        self.calls += 1
        self.contexts.append(context)
        if self._raises:
            raise ConnectionError("judge LLM down")
        return EvaluationResult(
            score=self._score,
            quality=QualityLevel.GOOD,
            feedback="canned",
            should_refine=self._should_refine,
            metadata={"fallback": True} if self._fallback else {},
        )


def _install(monkeypatch: pytest.MonkeyPatch, judge: _StubJudge) -> None:
    monkeypatch.setattr(guard_groundedness, "_build_judge", lambda: judge)


def _blocks() -> float:
    from prometheus_client import REGISTRY

    labels = {"layer": "output_groundedness", "reason": "ungrounded"}
    return REGISTRY.get_sample_value("mas_guardrail_blocks_total", labels) or 0.0


def _sourced_result(response: str = "Paris is the capital.") -> dict[str, Any]:
    return {
        "response": response,
        "sources": [{"content": "Paris is the capital of France."}],
    }


class TestModes:
    async def test_off_by_default_judge_not_called(self, monkeypatch):
        monkeypatch.delenv("BASELITH_OUTPUT_GROUNDEDNESS", raising=False)
        judge = _StubJudge(score=0.1)
        _install(monkeypatch, judge)

        result = await guard_output_async(_sourced_result())

        assert result["response"] == "Paris is the capital."
        assert "groundedness" not in result.get("guardrails", {})
        assert judge.calls == 0

    async def test_annotate_writes_score_under_guardrails(self, monkeypatch):
        monkeypatch.setenv("BASELITH_OUTPUT_GROUNDEDNESS", "annotate")
        judge = _StubJudge(score=0.9, should_refine=False)
        _install(monkeypatch, judge)

        result = await guard_output_async(_sourced_result())

        assert result["guardrails"]["groundedness"] == {
            "score": 0.9,
            "should_refine": False,
        }
        assert result["response"] == "Paris is the capital."

    async def test_annotate_never_blocks_even_below_threshold(self, monkeypatch):
        monkeypatch.setenv("BASELITH_OUTPUT_GROUNDEDNESS", "annotate")
        judge = _StubJudge(score=0.2, should_refine=True)
        _install(monkeypatch, judge)

        before = _blocks()
        result = await guard_output_async(_sourced_result())

        assert result["response"] == "Paris is the capital."
        assert result["guardrails"]["groundedness"]["score"] == 0.2
        assert _blocks() == before

    async def test_block_replaces_ungrounded_response_and_emits_metric(
        self, monkeypatch
    ):
        monkeypatch.setenv("BASELITH_OUTPUT_GROUNDEDNESS", "block")
        judge = _StubJudge(score=0.3)
        _install(monkeypatch, judge)

        before = _blocks()
        result = await guard_output_async(_sourced_result())

        assert result["response"] != "Paris is the capital."
        assert "not" in result["response"].lower()  # refusal-to-assert message
        grounded = result["guardrails"]["groundedness"]
        assert grounded["score"] == 0.3
        assert grounded["blocked"] is True
        assert _blocks() == before + 1

    async def test_block_keeps_grounded_response(self, monkeypatch):
        monkeypatch.setenv("BASELITH_OUTPUT_GROUNDEDNESS", "block")
        judge = _StubJudge(score=0.95)
        _install(monkeypatch, judge)

        before = _blocks()
        result = await guard_output_async(_sourced_result())

        assert result["response"] == "Paris is the capital."
        assert result["guardrails"]["groundedness"]["score"] == 0.95
        assert _blocks() == before

    async def test_threshold_env_override(self, monkeypatch):
        monkeypatch.setenv("BASELITH_OUTPUT_GROUNDEDNESS", "block")
        monkeypatch.setenv("BASELITH_OUTPUT_GROUNDEDNESS_THRESHOLD", "0.9")
        judge = _StubJudge(score=0.7)  # above default 0.6, below override 0.9
        _install(monkeypatch, judge)

        result = await guard_output_async(_sourced_result())

        assert result["guardrails"]["groundedness"]["blocked"] is True
        assert result["response"] != "Paris is the capital."


class TestFailOpen:
    async def test_judge_exception_fails_open(self, monkeypatch):
        monkeypatch.setenv("BASELITH_OUTPUT_GROUNDEDNESS", "block")
        judge = _StubJudge(raises=True)
        _install(monkeypatch, judge)

        result = await guard_output_async(_sourced_result())

        assert result["response"] == "Paris is the capital."
        assert "groundedness" not in result.get("guardrails", {})

    async def test_judge_internal_fallback_fails_open(self, monkeypatch):
        # BaseLLMEvaluator swallows LLM outages into a score-0 fallback
        # verdict; treating that as a real zero would block on every outage.
        monkeypatch.setenv("BASELITH_OUTPUT_GROUNDEDNESS", "block")
        judge = _StubJudge(score=0.0, fallback=True)
        _install(monkeypatch, judge)

        result = await guard_output_async(_sourced_result())

        assert result["response"] == "Paris is the capital."
        assert "groundedness" not in result.get("guardrails", {})


class TestSourceMaterial:
    async def test_no_sources_passthrough_judge_not_called(self, monkeypatch):
        monkeypatch.setenv("BASELITH_OUTPUT_GROUNDEDNESS", "block")
        judge = _StubJudge(score=0.0)
        _install(monkeypatch, judge)

        result = await guard_output_async({"response": "A plain chat answer."})

        assert result["response"] == "A plain chat answer."
        assert judge.calls == 0

    async def test_empty_sources_passthrough(self, monkeypatch):
        monkeypatch.setenv("BASELITH_OUTPUT_GROUNDEDNESS", "block")
        judge = _StubJudge(score=0.0)
        _install(monkeypatch, judge)

        result = await guard_output_async({"response": "answer", "sources": []})

        assert result["response"] == "answer"
        assert judge.calls == 0

    async def test_context_key_counts_as_source_material(self, monkeypatch):
        monkeypatch.setenv("BASELITH_OUTPUT_GROUNDEDNESS", "annotate")
        judge = _StubJudge(score=0.8)
        _install(monkeypatch, judge)

        result = await guard_output_async(
            {"response": "answer", "context": "Retrieved passage about the topic."}
        )

        assert judge.calls == 1
        assert result["guardrails"]["groundedness"]["score"] == 0.8
        # The judge received the sources as its faithfulness context.
        assert (
            judge.contexts[0]["memory_context"] == "Retrieved passage about the topic."
        )

    async def test_sources_dicts_flattened_for_judge(self, monkeypatch):
        monkeypatch.setenv("BASELITH_OUTPUT_GROUNDEDNESS", "annotate")
        judge = _StubJudge(score=0.8)
        _install(monkeypatch, judge)

        await guard_output_async(_sourced_result())

        assert "Paris is the capital of France." in judge.contexts[0]["memory_context"]

    async def test_master_kill_switch_bypasses_groundedness(self, monkeypatch):
        monkeypatch.setenv("BASELITH_OUTPUT_GROUNDEDNESS", "block")
        monkeypatch.setenv("BASELITH_ORCHESTRATOR_GUARDRAILS", "off")
        judge = _StubJudge(score=0.0)
        _install(monkeypatch, judge)

        result = await guard_output_async(_sourced_result())

        assert result["response"] == "Paris is the capital."
        assert judge.calls == 0
