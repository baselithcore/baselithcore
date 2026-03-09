"""
Router Module.

Provides components for semantically routing user requests to the appropriate
agents based on tool and capability embeddings.
"""

from typing import Dict, List, Optional
from pydantic import BaseModel, Field
from core.interfaces.services import (
    LLMServiceProtocol,
    VectorStoreProtocol,
    AsyncEmbedderProtocol,
)
from core.config import RouterConfig


class RouteRequest(BaseModel):
    """
    Structured request for selecting an appropriate agent.

    Attributes:
        query: The user input text.
        thread_id: Tracking identifier for conversation state.
        context: Optional dictionary of additional metadata.
    """

    query: str
    thread_id: Optional[str] = None
    context: Optional[Dict] = None


class RouteResult(BaseModel):
    """
    Ranked outcome of a routing operation.

    Attributes:
        agent_id: Identifier of the suggested agent.
        confidence: Normalized score representing match quality.
        reasoning: Explanation for the model's selection.
        metadata: Additional payload information.
    """

    agent_id: str
    confidence: float
    reasoning: str
    metadata: Dict = Field(default_factory=dict)


class Router:
    """
    Router component responsible for selecting the appropriate agent(s)
    to handle a user request based on semantic similarity and/or LLM reasoning.
    """

    def __init__(
        self,
        config: RouterConfig,
        llm_service: LLMServiceProtocol,
        vector_store: VectorStoreProtocol,
        embedder: AsyncEmbedderProtocol,
    ):
        """
        Initialize the Router.

        Args:
            config: Configuration settings for routing thresholds and limits.
            llm_service: Protocol for LLM operations.
            vector_store: Protocol for vector storage operations.
            embedder: Protocol for async text embedding.
        """
        self.config = config
        self.llm_service = llm_service
        self.vector_store = vector_store
        self.embedder = embedder

    async def route(self, request: RouteRequest) -> List[RouteResult]:
        """
        Determine the optimal agent(s) to handle a user request.

        Currently implements semantic routing via vector similarity search.
        Designed to be extensible for hybrid routing (semantic + LLM).

        Args:
            request: The routing request containing query and context.

        Returns:
            List[RouteResult]: A ranked list of candidate agents with
                              confidence scores and reasoning.
        """
        # In the future, this could combine semantic search with LLM reasoning (hybrid routing)
        return await self._semantic_route(request)

    async def _semantic_route(self, request: RouteRequest) -> List[RouteResult]:
        """
        Execute semantic matching using the configured vector store.

        Generates an embedding for the query and retrieves the N most
        relevant tool/agent mappings. Aggregates scores to identify the
        top K unique agent candidates.

        Args:
            request: The routing request.

        Returns:
            List[RouteResult]: Ranked agent candidates.
        """
        # 1. Generate query vector
        vectors = await self.embedder.encode([request.query])
        query_vector = list(next(iter(vectors)))

        # 2. Retrieve candidates from vector store (Tool-to-Agent Retrieval)
        # We retrieve N items (retrieval_limit) to ensure we find enough unique agents (K)
        search_results = await self.vector_store.search(
            query_vector=query_vector,
            k=self.config.retrieval_limit,
            score_threshold=self.config.score_threshold,
        )

        candidates: Dict[str, float] = {}  # agent_id -> best_score

        for res in search_results:
            # Resolving the parent agent (Tool -> Agent)
            agent_id = res.document.metadata.get("agent_id")
            if not agent_id:
                continue

            # Keep the highest score for this agent
            if agent_id not in candidates:
                candidates[agent_id] = res.score
            else:
                candidates[agent_id] = max(candidates[agent_id], res.score)

        # Sort by score descending and take top K
        sorted_candidates = sorted(
            candidates.items(), key=lambda x: x[1], reverse=True
        )[: self.config.max_candidates]

        routes = []
        for agent_id, score in sorted_candidates:
            routes.append(
                RouteResult(
                    agent_id=agent_id,
                    confidence=score,
                    reasoning=f"Semantic match (score: {score:.2f})",
                    metadata={"source": "vector_store"},
                )
            )

        return routes
