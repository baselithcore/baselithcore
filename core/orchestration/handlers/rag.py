"""
Standard RAG Flow Handler.

Implements the default Question Answering logic over documents.
"""

from core.observability.logging import get_logger
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    pass

from core.orchestration.handlers import BaseFlowHandler
from core.services.vectorstore import get_vectorstore_service
from core.services.llm import get_llm_service
from core.config.services import get_chat_config

logger = get_logger(__name__)


class StandardRagHandler(BaseFlowHandler):
    """
    Standard RAG handler for 'qa_docs' intent.
    Retrieves documents, reranks them (if enabled), and generates an answer.
    """

    def __init__(
        self,
        vector_store: Optional[Any] = None,
        llm_service: Optional[Any] = None,
        config: Optional[Any] = None,
        embedder: Optional[Any] = None,
        *args,
        **kwargs,
    ):
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

        # Dependency Injection with fallbacks
        if vector_store:
            self.vector_store = vector_store
        else:
            self.vector_store = get_vectorstore_service()

        if llm_service:
            self.llm_service = llm_service
        else:
            self.llm_service = get_llm_service()

        if config:
            self.config = config
        else:
            self.config = get_chat_config()

        # Initialize embedder
        if embedder:
            self.embedder = embedder
        else:
            try:
                from core.nlp import get_embedder

                # Use configured model or default
                model_name = getattr(self.config, "embedder_model", "all-MiniLM-L6-v2")
                self.embedder = get_embedder(model_name)
            except ImportError:
                logger.warning("Embedder not found, StandardRagHandler might fail.")
                self.embedder = None

    async def handle(self, query: str, context: Dict[str, Any]) -> Dict[str, Any]:
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
            # 1. Retrieval
            if not self.embedder:
                return {"answer": "Error: Embedder not initialized.", "error": True}

            # Ensure we await the async encode
            query_vector = await self.embedder.encode(query)
            if hasattr(query_vector, "tolist"):
                query_vector = query_vector.tolist()
            from typing import cast

            if not isinstance(query_vector, list):
                query_vector = list(query_vector)
            query_vector = cast(List[float], query_vector)

            # Check enabled reranking
            rerank = getattr(self.config, "enable_reranking", False)

            kb_label = context.get("kb_label", None)

            results = await self.vector_store.search(
                query_vector=query_vector,
                query_text=query,
                rerank=rerank,
                k=self.config.final_top_k if rerank else self.config.initial_search_k,
                collection_name=kb_label,  # Filter by specific KB if label provided
            )

            # 2. Context Construction
            if not results:
                return {
                    "response": "I couldn't find relevant information in the documents to answer your question.",
                    "sources": [],
                    "metadata": {"rag_retrieved": 0},
                }

            context_text = "\n\n".join(
                [f"Source [{r.document.id}]: {r.document.content}" for r in results]
            )

            # 3. Generation
            # Basic RAG prompt
            system_prompt = (
                "You are an intelligent assistant that answers questions based ONLY on the provided context.\n"
                "If the answer is not in the context, state that clearly.\n"
                "Cite sources when possible."
            )

            user_prompt = f"Context:\n{context_text}\n\nQuestion: {query}\n\nAnswer:"

            # We assume LLMService has a method for chat or completion
            # Using generate_response (sync or async?)
            # LLMService methods are often sync wrapping async or mixed.
            # Best to use run_in_executor if blocking, or async method if available.
            if hasattr(self.llm_service, "generate_response_async"):
                response = await self.llm_service.generate_response_async(
                    prompt=user_prompt, system=system_prompt
                )
            else:
                # Fallback - LLMService.generate_response is now async
                full_prompt = f"{system_prompt}\n\n{user_prompt}"
                response = await self.llm_service.generate_response(prompt=full_prompt)

            # Extract sources
            sources = [
                r.document.metadata.get("source", r.document.id) for r in results
            ]

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
