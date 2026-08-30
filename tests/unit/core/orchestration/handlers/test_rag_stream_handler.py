"""Unit tests for the streaming RAG handler (token streaming for qa_docs)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest

from core.orchestration.handlers.rag_stream import StandardRagStreamHandler


class _FakeEmbedder:
    async def encode(self, query: str) -> list[float]:
        return [0.1, 0.2]


class _FakeVectorStore:
    def __init__(self, results: list[Any]) -> None:
        self._results = results
        self.search_kwargs: dict[str, Any] | None = None

    async def search(self, **kwargs: Any) -> list[Any]:
        self.search_kwargs = kwargs
        return self._results


class _FakeLLMService:
    def __init__(self, chunks: list[str]) -> None:
        self._chunks = chunks
        self.stream_calls = 0

    async def generate_response_stream(
        self, prompt: str, **kwargs: Any
    ) -> AsyncIterator[str]:
        self.stream_calls += 1
        for chunk in self._chunks:
            yield chunk


def _result(doc_id: str, content: str) -> Any:
    return SimpleNamespace(
        document=SimpleNamespace(
            id=doc_id, content=content, metadata={"source": doc_id}
        )
    )


def _config() -> Any:
    return SimpleNamespace(
        enable_reranking=False,
        final_top_k=6,
        initial_search_k=40,
        embedder_model="all-MiniLM-L6-v2",
    )


@pytest.mark.asyncio
async def test_streams_llm_chunks_when_documents_found():
    llm = _FakeLLMService(["Rome ", "is ", "the answer."])
    handler = StandardRagStreamHandler(
        vector_store=_FakeVectorStore([_result("d1", "Rome is the capital.")]),
        llm_service=llm,
        config=_config(),
        embedder=_FakeEmbedder(),
    )

    chunks = [c async for c in handler.handle("capital of Italy?", {})]

    assert chunks == ["Rome ", "is ", "the answer."]
    assert llm.stream_calls == 1


@pytest.mark.asyncio
async def test_yields_not_found_message_without_documents():
    llm = _FakeLLMService(["should not stream"])
    handler = StandardRagStreamHandler(
        vector_store=_FakeVectorStore([]),
        llm_service=llm,
        config=_config(),
        embedder=_FakeEmbedder(),
    )

    chunks = [c async for c in handler.handle("anything", {})]

    assert llm.stream_calls == 0
    assert "couldn't find" in "".join(chunks)


def test_orchestrator_registers_stream_handler_for_default_intent():
    from core.orchestration.orchestrator import Orchestrator

    orch = Orchestrator()
    assert orch.has_stream_handler("qa_docs") is True
