"""``rag_only`` must pin the retrieval intent on both orchestrator paths.

``ChatRequest.rag_only`` was copied into the orchestrator context and then
ignored: the classifier could still route a "retrieval-only" request to the
reasoning, vision or swarm handlers.
"""

from __future__ import annotations

from typing import Any

from core.orchestration.mixins._context_assembly import rag_only_intent
from core.orchestration.orchestrator import Orchestrator


class _FlowHandler:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = 0

    async def handle(self, query: str, context: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        return {"response": self.response}


def _orchestrator(monkeypatch) -> tuple[Orchestrator, _FlowHandler, _FlowHandler, list]:
    orch = Orchestrator(default_intent="qa_docs")
    rag, other = _FlowHandler("rag answer"), _FlowHandler("other answer")
    orch.register_handler("qa_docs", rag)
    orch.register_handler("complex_reasoning", other)
    classified: list[str] = []

    async def fake_classify(query: str) -> str:
        classified.append(query)
        return "complex_reasoning"

    monkeypatch.setattr(orch, "classify_intent_async", fake_classify)
    return orch, rag, other, classified


async def test_rag_only_forces_default_intent_and_skips_classifier(monkeypatch):
    orch, rag, other, classified = _orchestrator(monkeypatch)

    result = await orch.process("why?", {"rag_only": True})

    assert result["intent"] == "qa_docs"
    assert rag.calls == 1
    assert other.calls == 0
    assert classified == []


async def test_without_rag_only_the_classifier_routes(monkeypatch):
    orch, rag, other, classified = _orchestrator(monkeypatch)

    result = await orch.process("why?", {"rag_only": False})

    assert result["intent"] == "complex_reasoning"
    assert other.calls == 1
    assert classified == ["why?"]


async def test_explicit_intent_wins_over_rag_only(monkeypatch):
    orch, rag, other, _ = _orchestrator(monkeypatch)

    result = await orch.process("why?", {"rag_only": True}, intent="complex_reasoning")

    assert result["intent"] == "complex_reasoning"
    assert rag.calls == 0


async def test_stream_path_honours_rag_only(monkeypatch):
    orch, rag, other, classified = _orchestrator(monkeypatch)
    orch._stream_handlers.pop("qa_docs", None)

    chunks = [c async for c in orch.process_stream("why?", {"rag_only": True})]

    assert "rag answer" in "".join(chunks)
    assert classified == []
    assert other.calls == 0


def test_helper_returns_none_when_unset():
    class _Owner:
        default_intent = "qa_docs"

    assert rag_only_intent(_Owner(), {}) is None
    assert rag_only_intent(_Owner(), {"rag_only": 1}) == "qa_docs"
