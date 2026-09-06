"""
Standard RAG Flow Handler.

Implements the default Question Answering logic over documents.
"""

from typing import TYPE_CHECKING, Any

from core.observability.logging import get_logger

if TYPE_CHECKING:
    pass

from core.config.services import get_chat_config
from core.orchestration.handlers import BaseFlowHandler
from core.services.llm import get_llm_service
from core.services.vectorstore import get_vectorstore_service

logger = get_logger(__name__)

# Shared with the streaming twin (rag_stream.StandardRagStreamHandler) so the
# two paths can never drift on prompt or fallback wording.
RAG_SYSTEM_PROMPT = (
    "You are an intelligent assistant that answers questions based ONLY on the provided context.\n"
    "If the answer is not in the context, state that clearly.\n"
    "Cite sources when possible."
)
RAG_NOT_FOUND_MESSAGE = (
    "I couldn't find relevant information in the documents to answer your question."
)


def build_rag_user_prompt(context_text: str, query: str) -> str:
    """Compose the user prompt from the retrieved context block and the query."""
    return f"Context:\n{context_text}\n\nQuestion: {query}\n\nAnswer:"


class StandardRagHandler(BaseFlowHandler):
    """
    Standard RAG handler for 'qa_docs' intent.
    Retrieves documents, reranks them (if enabled), and generates an answer.
    """

    def __init__(
        self,
        vector_store: Any | None = None,
        llm_service: Any | None = None,
        config: Any | None = None,
        embedder: Any | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """
        Initialize the standard RAG handler.

        Args:
            vector_store: Optional vector store service.
            llm_service: Optional LLM service.
            config: Optional chat configuration.
            embedder: Optional embedding service.
            *args, **kwargs: Passed to BaseFlowHandler.
        """
        super().__init__(*args, **kwargs)
        self._vector_store = vector_store
        self._llm_service = llm_service
        self._config = config
        self._embedder = embedder

    @property
    def vector_store(self) -> Any:
        """Lazy load the vector store service."""
        if self._vector_store is None:
            self._vector_store = get_vectorstore_service()
        return self._vector_store

    @property
    def llm_service(self) -> Any:
        """Lazy load the LLM service."""
        if self._llm_service is None:
            self._llm_service = get_llm_service()
        return self._llm_service

    @property
    def config(self) -> Any:
        """Lazy load chat configuration."""
        if self._config is None:
            self._config = get_chat_config()
        return self._config

    @property
    def embedder(self) -> Any | None:
        """Lazy load the embedder used for query encoding."""
        if self._embedder is None:
            try:
                from core.nlp import get_embedder

                model_name = getattr(self.config, "embedder_model", "all-MiniLM-L6-v2")
                self._embedder = get_embedder(model_name)
            except Exception as exc:
                logger.warning(
                    "Embedder initialization failed for StandardRagHandler: %s", exc
                )
                self._embedder = False
        return None if self._embedder is False else self._embedder

    async def retrieve(
        self, query: str, context: dict[str, Any]
    ) -> tuple[list[Any], str, list[str]]:
        """Embed the query, search the vector store and build the context block.

        Shared by the non-streaming :meth:`handle` and the streaming twin
        (``rag_stream.StandardRagStreamHandler``) so retrieval behavior can
        never drift between the two paths.

        Args:
            query: The user question.
            context: Execution context, optionally with ``kb_label``.

        Returns:
            ``(results, context_text, sources)`` — empty results mean no
            relevant documents were found.

        Raises:
            RuntimeError: When the embedder failed to initialize.
        """
        embedder = self.embedder
        if embedder is None:
            raise RuntimeError("Embedder not initialized")
        query_vector = await embedder.encode(query)
        if hasattr(query_vector, "tolist"):
            query_vector = query_vector.tolist()
        from typing import cast

        if not isinstance(query_vector, list):
            query_vector = list(query_vector)
        query_vector = cast(list[float], query_vector)

        rerank = getattr(self.config, "enable_reranking", False)
        kb_label = context.get("kb_label")

        results = await self.vector_store.search(
            query_vector=query_vector,
            query_text=query,
            rerank=rerank,
            k=self.config.final_top_k if rerank else self.config.initial_search_k,
            collection_name=kb_label,  # Filter by specific KB if label provided
        )
        if not results:
            return [], "", []

        context_text = "\n\n".join(
            [f"Source [{r.document.id}]: {r.document.content}" for r in results]
        )
        sources = [r.document.metadata.get("source", r.document.id) for r in results]
        return results, context_text, sources

    async def handle(self, query: str, context: dict[str, Any]) -> dict[str, Any]:
        """
        Process a user query using Retrieval-Augmented Generation.

        Retrieves relevant document fragments from the vector store,
        optionally reranks them, constructs a context-enriched prompt,
        and generates an answer using the LLM service.

        Args:
            query: The user input question or search query.
            context: Execution context, optionally containing 'kb_label'
                    to filter the search collection.

        Returns:
            Dict[str, Any]: A dictionary containing the 'response',
                           a list of unique 'sources', and 'metadata'.
        """
        try:
            if not self.embedder:
                return {"answer": "Error: Embedder not initialized.", "error": True}

            results, context_text, sources = await self.retrieve(query, context)

            if not results:
                return {
                    "response": RAG_NOT_FOUND_MESSAGE,
                    "sources": [],
                    "metadata": {"rag_retrieved": 0},
                }

            response = await self.llm_service.generate_response(
                prompt=build_rag_user_prompt(context_text, query),
                system_prompt=RAG_SYSTEM_PROMPT,
            )

            rerank = getattr(self.config, "enable_reranking", False)
            return {
                "response": response,
                "sources": list(set(sources)),
                "metadata": {"rag_retrieved": len(results), "rerank_used": rerank},
            }

        except Exception as e:
            logger.error(f"Error in RAG Handler: {e}")
            return {
                "response": "An error occurred while searching for information.",
                "error": True,
                "metadata": {"error": str(e)},
            }
