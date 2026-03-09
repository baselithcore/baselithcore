"""
Protocol definitions for core services.

These protocols define the interfaces that services must implement,
enabling dependency injection and making the codebase more testable
and modular.
"""

from typing import Any, Protocol, Iterator, AsyncIterator, Sequence, Iterable
from core.models.domain import Document, SearchResult
from core.models.chat import ChatRequest, ChatResponse


class EmbedderProtocol(Protocol):
    """Protocol for text embedding services (synchronous)."""

    def encode(self, queries: Sequence[str]) -> Iterable[Sequence[float]]:
        """
        Encode text queries into vector embeddings.

        Args:
            queries: List of text strings to encode

        Returns:
            List of embedding vectors
        """
        ...


class AsyncEmbedderProtocol(Protocol):
    """Protocol for async text embedding services."""

    async def encode(self, queries: Sequence[str]) -> Iterable[Sequence[float]]:
        """
        Encode text queries into vector embeddings asynchronously.

        Args:
            queries: List of text strings to encode

        Returns:
            List of embedding vectors
        """
        ...


class DocumentRerankerProtocol(Protocol):
    """Protocol for document reranking services (semantic search)."""

    def rerank(
        self, query: str, documents: Sequence[Document], top_k: int
    ) -> Sequence[SearchResult]:
        """
        Rerank documents based on relevance to query.

        Args:
            query: Search query
            documents: List of documents to rerank
            top_k: Number of top documents to return

        Returns:
            Reranked list of documents with scores
        """
        ...


# Backward compatibility alias
RerankerProtocol = DocumentRerankerProtocol


class ScoreRerankerProtocol(Protocol):
    """Protocol for query-document pair scoring (cross-encoder style)."""

    def predict(self, pairs: Sequence[tuple[str, str]]) -> Any:
        """
        Predict relevance scores for query-document pairs.

        Args:
            pairs: List of (query, document) tuples

        Returns:
            Relevance scores (array-like, supporting .tolist() if needed)
        """
        ...


class VectorStoreProtocol(Protocol):
    """Protocol for vector store operations."""

    async def search(
        self, query_vector: Sequence[float], k: int, **kwargs
    ) -> Sequence[SearchResult]:
        """
        Search for similar vectors in the store.

        Args:
            query_vector: Query embedding vector
            k: Number of results to return
            **kwargs: Additional search parameters

        Returns:
            List of search results
        """
        ...

    async def index(self, documents: Sequence[Document], **kwargs) -> None:
        """
        Index documents into the vector store.

        Args:
            documents: Documents to index
            **kwargs: Additional indexing parameters
        """
        ...

    async def create_collection(self, collection_name: str, **kwargs) -> None:
        """
        Create a new collection in the vector store.

        Args:
            collection_name: Name of the collection
            **kwargs: Additional collection parameters
        """
        ...


class LLMServiceProtocol(Protocol):
    """Protocol for LLM services."""

    async def generate_response(
        self, prompt: str, model: str | None = None, json: bool = False
    ) -> str:
        """
        Generate a response from the LLM.

        Args:
            prompt: Input prompt
            model: Optional model override
            json: Whether to request JSON output

        Returns:
            Generated response text
        """
        ...

    async def generate_response_stream(
        self, prompt: str, model: str | None = None
    ) -> AsyncIterator[str]:
        """
        Generate a streaming response from the LLM.

        Args:
            prompt: Input prompt
            model: Optional model override

        Yields:
            Response chunks as they are generated
        """
        ...


class ChatServiceProtocol(Protocol):
    """Protocol for chat services."""

    def handle_chat(self, request: ChatRequest) -> ChatResponse:
        """
        Handle a synchronous chat request.

        Args:
            request: Chat request object

        Returns:
            Chat response object
        """
        ...

    def handle_chat_stream(self, request: ChatRequest) -> Iterator[str]:
        """
        Handle a streaming chat request.

        Args:
            request: Chat request object

        Yields:
            Response chunks as they are generated
        """
        ...

    async def handle_chat_async(self, request: ChatRequest) -> ChatResponse:
        """
        Handle an asynchronous chat request.

        Args:
            request: Chat request object

        Returns:
            Chat response object
        """
        ...

    async def handle_chat_stream_async(
        self, request: ChatRequest
    ) -> AsyncIterator[str]:
        """
        Handle an asynchronous streaming chat request.

        Args:
            request: Chat request object

        Yields:
            Response chunks as they are generated
        """
        ...
