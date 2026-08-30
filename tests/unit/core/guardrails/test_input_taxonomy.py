"""Input-guard taxonomy — LLM classification of inbound queries.

Pins the ``InputGuard.classify`` contract (strict-JSON LLM verdict, fail-open
on malformed output, ``out_of_scope`` decidable only under a configured
topical rail) and its opt-in wiring into the orchestrator guard pipeline
(``BASELITH_INPUT_GUARD_TAXONOMY``, threshold-gated blocking, Prometheus
block metrics). The synchronous regex path stays untouched.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from core.guardrails.config import GuardrailsConfig
from core.guardrails.input_guard import (
    InputClassification,
    InputGuard,
    InputValidationResult,
)


class _StubLLM:
    """LLM stub returning a canned payload (or raising it)."""

    def __init__(self, payload: str | Exception) -> None:
        self.payload = payload
        self.prompts: list[str] = []

    async def generate_response(self, prompt: str, **kwargs: Any) -> str:
        self.prompts.append(prompt)
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def _install_llm(monkeypatch: pytest.MonkeyPatch, stub: _StubLLM) -> None:
    monkeypatch.setattr("core.services.llm.get_llm_service", lambda: stub)


def _verdict(intent: str, confidence: float = 0.95, reason: str = "because") -> str:
    return json.dumps({"reason": reason, "intent": intent, "confidence": confidence})


class TestClassify:
    """InputGuard.classify — the LLM taxonomy verdict."""

    @pytest.mark.parametrize("intent", ["in_scope", "jailbreak", "harmful"])
    async def test_returns_each_intent(self, monkeypatch, intent):
        stub = _StubLLM(_verdict(intent, confidence=0.92))
        _install_llm(monkeypatch, stub)

        result = await InputGuard().classify("some user input")

        assert isinstance(result, InputClassification)
        assert result.intent == intent
        assert result.confidence == pytest.approx(0.92)
        assert result.reason == "because"

    async def test_malformed_json_fails_open(self, monkeypatch):
        stub = _StubLLM("not json at all {{{")
        _install_llm(monkeypatch, stub)

        result = await InputGuard().classify("hello")

        assert result.intent == "in_scope"
        assert result.confidence == 0.0

    async def test_llm_failure_fails_open(self, monkeypatch):
        stub = _StubLLM(ConnectionError("provider down"))
        _install_llm(monkeypatch, stub)

        result = await InputGuard().classify("hello")

        assert result.intent == "in_scope"
        assert result.confidence == 0.0

    async def test_unknown_intent_fails_open(self, monkeypatch):
        stub = _StubLLM(_verdict("existential_dread"))
        _install_llm(monkeypatch, stub)

        result = await InputGuard().classify("hello")

        assert result.intent == "in_scope"
        assert result.confidence == 0.0

    async def test_out_of_scope_without_allowed_topics_coerced_in_scope(
        self, monkeypatch
    ):
        stub = _StubLLM(_verdict("out_of_scope", confidence=0.99))
        _install_llm(monkeypatch, stub)

        guard = InputGuard()  # default config: allowed_topics=None
        result = await guard.classify("tell me about cooking")

        assert result.intent == "in_scope"

    async def test_out_of_scope_with_allowed_topics_returned(self, monkeypatch):
        stub = _StubLLM(_verdict("out_of_scope", confidence=0.9))
        _install_llm(monkeypatch, stub)

        guard = InputGuard(GuardrailsConfig(allowed_topics="Kubernetes operations"))
        result = await guard.classify("tell me about cooking")

        assert result.intent == "out_of_scope"
        assert result.confidence == pytest.approx(0.9)

    async def test_prompt_includes_allowed_topics_when_set(self, monkeypatch):
        stub = _StubLLM(_verdict("in_scope"))
        _install_llm(monkeypatch, stub)

        guard = InputGuard(GuardrailsConfig(allowed_topics="Kubernetes operations"))
        await guard.classify("how do I roll back a deployment?")

        assert len(stub.prompts) == 1
        assert "Kubernetes operations" in stub.prompts[0]

    async def test_prompt_omits_out_of_scope_without_allowed_topics(self, monkeypatch):
        stub = _StubLLM(_verdict("in_scope"))
        _install_llm(monkeypatch, stub)

        await InputGuard().classify("hello")

        assert "out_of_scope" not in stub.prompts[0]

    async def test_confidence_clamped_to_unit_interval(self, monkeypatch):
        stub = _StubLLM(_verdict("jailbreak", confidence=1.7))
        _install_llm(monkeypatch, stub)

        result = await InputGuard().classify("hello")

        assert result.confidence == 1.0

    def test_sync_regex_path_untouched(self):
        # The taxonomy is additive: validate() stays a pure-regex sync path.
        verdict = InputGuard().validate("what is the capital of France?")
        assert verdict.is_valid is True


class _StubTaxonomyGuard:
    """InputGuard stand-in for pipeline tests: regex passes, classify canned."""

    def __init__(
        self,
        classification: InputClassification,
        allowed_topics: str | None = None,
    ) -> None:
        self.config = GuardrailsConfig(allowed_topics=allowed_topics)
        self._classification = classification
        self.classify_calls = 0

    def validate(self, text: str) -> InputValidationResult:
        return InputValidationResult(is_valid=True, sanitized_input=text)

    async def classify(self, text: str) -> InputClassification:
        self.classify_calls += 1
        return self._classification


def _install_pipeline(
    monkeypatch: pytest.MonkeyPatch, guard: _StubTaxonomyGuard
) -> None:
    from core.guardrails import moderation as moderation_module
    from core.orchestration import guard_pipeline

    monkeypatch.setattr(guard_pipeline, "_guards", lambda: (guard, None))
    monkeypatch.setattr(moderation_module, "get_moderator", lambda: None)


def _blocks(reason: str) -> float:
    from prometheus_client import REGISTRY

    labels = {"layer": "input_taxonomy", "reason": reason}
    return REGISTRY.get_sample_value("mas_guardrail_blocks_total", labels) or 0.0


class TestPipelineWiring:
    """guard_input_async taxonomy rail behind BASELITH_INPUT_GUARD_TAXONOMY."""

    async def test_default_off_never_classifies(self, monkeypatch):
        from core.orchestration.guard_pipeline import guard_input_async

        monkeypatch.delenv("BASELITH_INPUT_GUARD_TAXONOMY", raising=False)
        guard = _StubTaxonomyGuard(
            InputClassification(intent="harmful", confidence=0.99, reason="bad")
        )
        _install_pipeline(monkeypatch, guard)

        assert await guard_input_async("anything") is None
        assert guard.classify_calls == 0

    @pytest.mark.parametrize("intent", ["jailbreak", "harmful"])
    async def test_blocks_and_emits_metric(self, monkeypatch, intent):
        from core.orchestration.guard_pipeline import guard_input_async

        monkeypatch.setenv("BASELITH_INPUT_GUARD_TAXONOMY", "1")
        guard = _StubTaxonomyGuard(
            InputClassification(intent=intent, confidence=0.95, reason="nope")
        )
        _install_pipeline(monkeypatch, guard)

        before = _blocks(intent)
        blocked = await guard_input_async("do the bad thing")

        assert blocked is not None
        assert blocked["error"] is True
        assert blocked["intent"] == "blocked_by_taxonomy"
        assert "guardrail" in blocked["response"].lower()
        assert _blocks(intent) == before + 1

    async def test_in_scope_passes(self, monkeypatch):
        from core.orchestration.guard_pipeline import guard_input_async

        monkeypatch.setenv("BASELITH_INPUT_GUARD_TAXONOMY", "1")
        guard = _StubTaxonomyGuard(
            InputClassification(intent="in_scope", confidence=0.99, reason="fine")
        )
        _install_pipeline(monkeypatch, guard)

        assert await guard_input_async("what is the capital of France?") is None
        assert guard.classify_calls == 1

    async def test_below_threshold_passes(self, monkeypatch):
        from core.orchestration.guard_pipeline import guard_input_async

        monkeypatch.setenv("BASELITH_INPUT_GUARD_TAXONOMY", "1")
        guard = _StubTaxonomyGuard(
            InputClassification(intent="harmful", confidence=0.5, reason="maybe")
        )
        _install_pipeline(monkeypatch, guard)

        assert await guard_input_async("borderline request") is None

    async def test_threshold_env_override(self, monkeypatch):
        from core.orchestration.guard_pipeline import guard_input_async

        monkeypatch.setenv("BASELITH_INPUT_GUARD_TAXONOMY", "1")
        monkeypatch.setenv("BASELITH_INPUT_GUARD_TAXONOMY_THRESHOLD", "0.4")
        guard = _StubTaxonomyGuard(
            InputClassification(intent="harmful", confidence=0.5, reason="maybe")
        )
        _install_pipeline(monkeypatch, guard)

        blocked = await guard_input_async("borderline request")
        assert blocked is not None
        assert blocked["intent"] == "blocked_by_taxonomy"

    async def test_out_of_scope_blocks_only_with_allowed_topics(self, monkeypatch):
        from core.orchestration.guard_pipeline import guard_input_async

        monkeypatch.setenv("BASELITH_INPUT_GUARD_TAXONOMY", "1")
        classification = InputClassification(
            intent="out_of_scope", confidence=0.95, reason="off topic"
        )

        # Without a topical rail: out_of_scope is not blockable.
        _install_pipeline(monkeypatch, _StubTaxonomyGuard(classification))
        assert await guard_input_async("tell me about cooking") is None

        # With one: it is.
        _install_pipeline(
            monkeypatch,
            _StubTaxonomyGuard(classification, allowed_topics="Kubernetes ops"),
        )
        blocked = await guard_input_async("tell me about cooking")
        assert blocked is not None
        assert blocked["intent"] == "blocked_by_taxonomy"

    async def test_master_kill_switch_bypasses_taxonomy(self, monkeypatch):
        from core.orchestration.guard_pipeline import guard_input_async

        monkeypatch.setenv("BASELITH_INPUT_GUARD_TAXONOMY", "1")
        monkeypatch.setenv("BASELITH_ORCHESTRATOR_GUARDRAILS", "off")
        guard = _StubTaxonomyGuard(
            InputClassification(intent="harmful", confidence=0.99, reason="bad")
        )
        _install_pipeline(monkeypatch, guard)

        assert await guard_input_async("anything") is None
        assert guard.classify_calls == 0
