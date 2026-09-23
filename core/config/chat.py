"""Chat configuration (``CHAT_``).

RAG retrieval sizes, reranking and response caching for the chat engine.
Moved out of ``core.config.services`` (module size cap), which re-exports it.
"""

import logging

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class ChatConfig(BaseSettings):
    """
    Configuration for the Chat orchestration engine.

    Defines the logic for RAG (Retrieval-Augmented Generation),
    reranking, and response caching.
    """

    model_config = SettingsConfigDict(
        env_prefix="CHAT_",
        case_sensitive=False,
        extra="ignore",
    )

    long_term_memory_enabled: bool = Field(
        default=False,
        description=(
            "Give the chat Orchestrator an AgentMemory so runs recall past "
            "interactions and write new ones back. Off by default: it adds an "
            "embedding + store round-trip per request. Distinct from "
            "CHAT_MEMORY_ENABLED, which governs per-conversation history."
        ),
    )
    streaming_enabled: bool = Field(
        default=True, description="Enable streaming responses"
    )

    # Number of documents to pull in the first broad sweep from vector search.
    initial_search_k: int = Field(
        default=20, description="Initial number of documents to retrieve"
    )

    # Final number of best-match documents to feed into the LLM context.
    final_top_k: int = Field(
        default=5, description="Final number of documents after reranking"
    )

    # Limit on history turns sent to the LLM (to manage context window).
    max_history_length: int = Field(
        default=10,
        description="Deprecated, no effect: nothing reads it; conversation history is sized by CHAT_MEMORY_MAX_TURNS",
    )

    # If enabled, uses a secondary model to re-score documents for better precision.
    enable_reranking: bool = Field(
        default=True, description="Enable document reranking"
    )

    reranker_model: str = Field(
        default="cross-encoder/ms-marco-MiniLM-L-6-v2",
        description="Reranker model name",
    )

    # Max documents to pass to the reranker engine.
    rerank_max_candidates: int = Field(
        default=50, description="Maximum number of candidates to rerank"
    )

    # == Exact Match Caching ==
    enable_response_cache: bool = Field(
        default=True,
        description="Deprecated, no effect: nothing reads it (the live chat path has no answer cache)",
    )

    response_cache_ttl: int = Field(
        default=3600, description="Response cache TTL in seconds"
    )

    # External factory/plugin orchestration
    service_factory: str | None = Field(
        default=None,
        alias="CHAT_SERVICE_FACTORY",
        description="Import path to a custom chat service factory",
    )

    service_config_file: str | None = Field(
        default=None,
        alias="CHAT_SERVICE_CONFIG_FILE",
        description="Path to an external YAML/JSON chat config file",
    )


_chat_config: ChatConfig | None = None


def get_chat_config() -> ChatConfig:
    """Retrieve or initialize the global ChatConfig singleton."""
    global _chat_config
    if _chat_config is None:
        _chat_config = ChatConfig()
        logger.info(
            f"Initialized ChatConfig with streaming={_chat_config.streaming_enabled}"
        )
    return _chat_config


__all__ = ["ChatConfig", "get_chat_config"]
