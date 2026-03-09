"""
Contract tests for service protocols.

Verifies that protocol definitions are correct and can be implemented.
"""

import pytest
from typing import Sequence, Any, Dict, Iterator, AsyncIterator

from core.interfaces import (
    VectorStoreProtocol,
    ChatServiceProtocol,
    LLMServiceProtocol,
    EmbedderProtocol,
    RerankerProtocol,
)


# Mock implementations for testing


class MockEmbedder:
    """Mock embedder implementation."""

    def encode(self, queries: Sequence[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in queries]


class MockReranker:
    """Mock reranker implementation."""

    def rerank(self, query: str, documents: Sequence[Any], top_k: int) -> Sequence[Any]:
        return documents[:top_k]


class MockVectorStore:
    """Mock vector store implementation."""

    def search(self, query_vector: Sequence[float], k: int, **kwargs) -> Sequence[Any]:
        return [{"id": i, "score": 0.9 - i * 0.1} for i in range(k)]

    def index(self, documents: Sequence[Any], **kwargs) -> None:
        pass

    def create_collection(self, collection_name: str, **kwargs) -> None:
        pass


class MockLLMService:
    """Mock LLM service implementation."""

    async def generate_response(
        self, prompt: str, model: str | None = None, json: bool = False
    ) -> str:
        return f"Response to: {prompt[:20]}..."

    async def generate_response_stream(
        self, prompt: str, model: str | None = None
    ) -> AsyncIterator[str]:
        yield "Response "
        yield "chunk "
        yield "by "
        yield "chunk"


class MockChatService:
    """Mock chat service implementation."""

    def handle_chat(self, request: Any) -> Dict[str, Any]:
        return {"answer": "Mock response", "sources": []}

    def handle_chat_stream(self, request: Any) -> Iterator[str]:
        yield "Mock "
        yield "streaming "
        yield "response"

    async def handle_chat_async(self, request: Any) -> Dict[str, Any]:
        return {"answer": "Mock async response", "sources": []}

    async def handle_chat_stream_async(self, request: Any) -> AsyncIterator[str]:
        yield "Mock "
        yield "async "
        yield "streaming "
        yield "response"


# Protocol compliance tests


class TestEmbedderProtocol:
    """Test that EmbedderProtocol can be implemented."""

    def test_mock_embedder_implements_protocol(self):
        """Test that MockEmbedder implements EmbedderProtocol."""
        embedder: EmbedderProtocol = MockEmbedder()

        queries = ["test query 1", "test query 2"]
        embeddings = embedder.encode(queries)

        assert len(list(embeddings)) == 2
        assert all(len(emb) == 3 for emb in embeddings)


class TestRerankerProtocol:
    """Test that RerankerProtocol can be implemented."""

    def test_mock_reranker_implements_protocol(self):
        """Test that MockReranker implements RerankerProtocol."""
        reranker: RerankerProtocol = MockReranker()

        documents = [{"id": i} for i in range(10)]
        reranked = reranker.rerank("query", documents, top_k=3)

        assert len(reranked) == 3


class TestVectorStoreProtocol:
    """Test that VectorStoreProtocol can be implemented."""

    def test_mock_vectorstore_implements_protocol(self):
        """Test that MockVectorStore implements VectorStoreProtocol."""
        store: VectorStoreProtocol = MockVectorStore()

        # Test search
        results = store.search([0.1, 0.2, 0.3], k=5)
        assert len(results) == 5

        # Test index
        store.index([{"text": "doc1"}, {"text": "doc2"}])

        # Test create_collection
        store.create_collection("test_collection")


class TestLLMServiceProtocol:
    """Test that LLMServiceProtocol can be implemented."""

    @pytest.mark.asyncio
    async def test_mock_llm_implements_protocol(self):
        """Test that MockLLMService implements LLMServiceProtocol."""
        llm: LLMServiceProtocol = MockLLMService()

        # Test generate_response
        response = await llm.generate_response("test prompt")
        assert "Response to:" in response

        # Test generate_response with json
        json_response = await llm.generate_response("test prompt", json=True)
        assert isinstance(json_response, str)

        # Test generate_response_stream
        chunks = []
        async for chunk in llm.generate_response_stream("test prompt"):
            chunks.append(chunk)
        assert len(chunks) == 4
        assert "".join(chunks) == "Response chunk by chunk"


class TestChatServiceProtocol:
    """Test that ChatServiceProtocol can be implemented."""

    def test_mock_chat_implements_protocol(self):
        """Test that MockChatService implements ChatServiceProtocol."""
        chat: ChatServiceProtocol = MockChatService()

        # Test handle_chat
        response = chat.handle_chat({"query": "test"})
        assert "answer" in response
        assert "sources" in response

        # Test handle_chat_stream
        chunks = list(chat.handle_chat_stream({"query": "test"}))
        assert len(chunks) == 3

    @pytest.mark.asyncio
    async def test_mock_chat_implements_async_protocol(self):
        """Test that MockChatService implements async methods."""
        chat: ChatServiceProtocol = MockChatService()

        # Test handle_chat_async
        response = await chat.handle_chat_async({"query": "test"})
        assert "answer" in response

        # Test handle_chat_stream_async
        chunks = []
        async for chunk in chat.handle_chat_stream_async({"query": "test"}):
            chunks.append(chunk)
        assert len(chunks) == 4


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
