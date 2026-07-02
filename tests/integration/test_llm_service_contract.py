"""
LLMService API-contract tests for the reasoning and meta subsystems.

These tests intentionally do NOT mock ``LLMService``: they run a real
``LLMService`` instance over a fake in-process provider. Permissive mocks
(e.g. exposing a non-existent ``generate_response_async`` or accepting
arbitrary kwargs) previously masked API mismatches that made every
reasoning/meta LLM call fail silently in production. Any signature drift
between callers and ``LLMService.generate_response`` fails loudly here.
"""

from types import SimpleNamespace

import pytest

from core.services.llm.service import LLMService


class FakeProvider:
    """Minimal in-process LLMProviderProtocol implementation.

    Records every call so tests can assert on the kwargs that actually
    reached the provider boundary.
    """

    def __init__(self):
        self.calls: list[dict] = []

    async def generate(
        self, prompt: str, model: str, json_mode: bool = False, **kwargs
    ) -> tuple[str, int]:
        self.calls.append(
            {"prompt": prompt, "model": model, "json_mode": json_mode, **kwargs}
        )
        lowered = prompt.lower()
        if "score the quality" in lowered or "evaluator" in lowered:
            return "0.8", 10
        if "generate" in lowered and "next steps" in lowered:
            return "1. Alpha step\n2. Beta step", 10
        return "PERSPECTIVE: Fine\nREASONING: Because\nCONFIDENCE: High", 10

    async def generate_stream(self, prompt: str, model: str, **kwargs):
        yield "chunk", 1

    async def close(self) -> None:
        return None


def _make_service() -> tuple[LLMService, FakeProvider]:
    config = SimpleNamespace(
        provider="ollama",
        api_key=None,
        api_base="http://localhost:11434",
        model="test-model",
        enable_cache=False,
        cache_max_size=16,
        cache_ttl=60,
    )
    service = LLMService(config=config, enable_cache=False)
    provider = FakeProvider()
    service.provider = provider
    return service, provider


@pytest.mark.asyncio
async def test_generate_response_accepts_sampling_params():
    """temperature/max_tokens must flow through to the provider boundary."""
    service, provider = _make_service()

    result = await service.generate_response(
        "hello", system_prompt="sys", temperature=0.3, max_tokens=42
    )

    assert result
    assert provider.calls[0]["temperature"] == 0.3
    assert provider.calls[0]["max_tokens"] == 42
    assert provider.calls[0]["system"] == "sys"


@pytest.mark.asyncio
async def test_tree_of_thoughts_reaches_the_llm():
    """ToT must produce real thoughts through the real service API."""
    from core.reasoning.tot.cache import get_thought_cache
    from core.reasoning.tot.engine import TreeOfThoughtsAsync

    get_thought_cache().clear()
    service, provider = _make_service()
    tot = TreeOfThoughtsAsync(llm_service=service)

    result = await tot.solve(
        problem="Plan a migration", strategy="mcts", iterations=2, max_steps=2, k=2
    )

    assert provider.calls, "ToT never reached the LLM provider"
    assert result["solution"] not in ("No solution found", "")
    assert result["steps"][-1] in ("Alpha step", "Beta step")
    get_thought_cache().clear()


@pytest.mark.asyncio
async def test_chain_of_thought_reaches_the_llm():
    """CoT must not degrade to the canned no-LLM fallback."""
    from core.reasoning.cot import ChainOfThought

    service, provider = _make_service()
    cot = ChainOfThought(llm_service=service)

    answer, steps = await cot.reason("Why is the sky blue?")

    assert provider.calls, "CoT never reached the LLM provider"
    assert answer != "Unable to reason without LLM service"


@pytest.mark.asyncio
async def test_persona_ensemble_reaches_the_llm():
    """Ensemble perspectives must be real generations, not error strings."""
    from core.meta.ensemble import PersonaEnsemble

    service, provider = _make_service()
    ensemble = PersonaEnsemble()
    ensemble._llm_service = service

    perspectives = await ensemble.generate_perspectives("Should we ship?")

    assert provider.calls, "Ensemble never reached the LLM provider"
    assert perspectives
    for perspective in perspectives:
        assert not perspective.content.startswith("[Error generating perspective")
    # Persona temperature diversity must reach the provider boundary.
    temperatures = {call.get("temperature") for call in provider.calls}
    assert len(temperatures) > 1


@pytest.mark.asyncio
async def test_internal_debate_reaches_the_llm():
    """Debate counterarguments/agreement analysis must use the real API."""
    from core.meta.debate import InternalDebate
    from core.meta.types import DebateRole, Perspective

    service, provider = _make_service()
    debate = InternalDebate(max_rounds=1)
    debate._llm_service = service

    perspectives = [
        Perspective(
            persona_name="advocate",
            role=DebateRole.ADVOCATE,
            content="We should ship now.",
            reasoning="Speed matters.",
            confidence=0.8,
        ),
        Perspective(
            persona_name="devils_advocate",
            role=DebateRole.CRITIC,
            content="Shipping now is risky.",
            reasoning="Quality matters.",
            confidence=0.7,
        ),
    ]

    result = await debate.run(perspectives, query="Should we ship?")

    assert provider.calls, "Debate never reached the LLM provider"
    assert result.rounds
    assert result.rounds[0].counterarguments
