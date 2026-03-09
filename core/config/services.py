"""
Service-level configuration for BaselithCore internal engines.

This module houses the configuration schemas for primary external-facing
services:
- LLM (Large Language Models): OpenAI, Ollama, Anthropic, etc.
- VectorStore: High-performance semantic search (Qdrant).
- Chat: Orchestration parameters for RAG and conversation history.
- Specialized: Vision, Voice (TTS/STT), and Fine-tuning.
"""

import logging
from typing import Optional, Literal

from pydantic import Field, field_validator, AliasChoices
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class LLMConfig(BaseSettings):
    """
    Configuration for Large Language Model providers.

    Manages connection parameters, generation hyper-parameters (temperature),
    and semantic search-based response caching.
    """

    model_config = SettingsConfigDict(
        env_prefix="LLM_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # The backend provider to route LLM requests to.
    provider: Literal["openai", "ollama", "huggingface", "anthropic"] = Field(
        default="ollama",
        description="LLM provider (openai, ollama, huggingface, or anthropic)",
    )

    # The specific model family/version (e.g., 'gpt-4o', 'llama3.2', 'claude-3-opus').
    model: str = Field(default="llama3.2", description="Model name to use")

    # API credentials. If None, service might depend on local environment or local proxy.
    api_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("LLM_API_KEY", "LLM_OPENAI_API_KEY"),
        description="API key for provider",
    )

    # Custom endpoint for self-hosted or proxied LLMs (like Ollama or vLLM).
    api_base: Optional[str] = Field(
        default=None, description="Base URL for API (for Ollama)"
    )

    # Controls randomness: 0.0 is deterministic, 1.0+ is creative.
    temperature: float = Field(
        default=0.7, ge=0.0, le=2.0, description="Temperature for generation"
    )

    # Optional cap on completion length.
    max_tokens: Optional[int] = Field(
        default=None, description="Maximum tokens to generate"
    )

    # == Semantic Caching ==
    # If enabled, uses a vector-based cache to reuse similar past responses.
    enable_cache: bool = Field(
        default=True, description="Enable semantic caching for LLM responses"
    )

    cache_ttl: int = Field(
        default=3600, description="Cache TTL in seconds (default 1 hour)"
    )

    cache_max_size: int = Field(
        default=1000, description="Maximum number of cached items"
    )

    # == HuggingFace specific settings ==
    # Use the local `transformers` library instead of remote API calls.
    huggingface_local: bool = Field(
        default=False,
        description="Use local transformers instead of HuggingFace Inference API",
    )

    huggingface_device: str = Field(
        default="auto",
        description="Device for local HuggingFace models (auto, cpu, cuda, mps)",
    )

    huggingface_dtype: str = Field(
        default="auto",
        description="Torch dtype for local models (auto, float16, bfloat16, float32)",
    )

    huggingface_trust_remote_code: bool = Field(
        default=False,
        description="Trust remote code when loading HuggingFace models",
    )

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, v: str) -> str:
        """Ensure the requested provider is supported by the framework."""
        if v not in ["openai", "ollama", "huggingface", "anthropic"]:
            raise ValueError(f"Unsupported provider: {v}")
        return v


class VectorStoreConfig(BaseSettings):
    """
    Configuration for semantic database and indexing.

    BaselithCore primarily uses Qdrant for high-performance vector operations.
    """

    model_config = SettingsConfigDict(
        env_prefix="VECTORSTORE_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    provider: Literal["qdrant"] = Field(
        default="qdrant", description="Vector store provider"
    )

    # The default logical container for vector embeddings.
    collection_name: str = Field(
        default="documents", description="Collection name for documents"
    )

    host: str = Field(
        default="localhost",
        validation_alias=AliasChoices("VECTORSTORE_HOST", "VECTORSTORE_QDRANT_HOST"),
        description="Vector store server host",
    )
    port: int = Field(default=6333, description="Vector store HTTP/REST port")
    grpc_port: int = Field(default=6334, description="Vector store gRPC port")

    # == Embedding Settings ==
    # Model used to convert text into numerical vectors.
    embedding_model: str = Field(
        default="sentence-transformers/all-MiniLM-L6-v2",
        description="Embedding model name",
    )

    # Dimension size of the vectors produced by the model.
    embedding_dim: int = Field(default=384, description="Embedding dimension")

    embedding_cache_ttl: int = Field(
        default=3600, alias="EMBEDDING_CACHE_TTL", description="Embedding cache TTL"
    )

    # == Search Settings ==
    # Number of documents to return by default in vector searches.
    search_limit: int = Field(
        default=10, description="Default number of search results"
    )

    # Qdrant deployment mode: 'server' for cluster/docker, 'local' for in-memory/disk.
    qdrant_mode: str = Field(default="server", alias="QDRANT_MODE")
    qdrant_path: Optional[str] = Field(default=None, alias="QDRANT_PATH")


class ChatConfig(BaseSettings):
    """
    Configuration for the Chat orchestration engine.

    Defines the logic for RAG (Retrieval-Augmented Generation),
    reranking, and response caching.
    """

    model_config = SettingsConfigDict(
        env_prefix="CHAT_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # If True, streams the response tokens back to the client in real-time.
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
        default=10, description="Maximum conversation history length"
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
        default=True, description="Enable response exact-match caching"
    )

    response_cache_ttl: int = Field(
        default=3600, description="Response cache TTL in seconds"
    )

    # External factory/plugin orchestration
    service_factory: Optional[str] = Field(
        default=None,
        alias="CHAT_SERVICE_FACTORY",
        description="Import path to a custom chat service factory",
    )

    service_config_file: Optional[str] = Field(
        default=None,
        alias="CHAT_SERVICE_CONFIG_FILE",
        description="Path to an external YAML/JSON chat config file",
    )


class VisionConfig(BaseSettings):
    """
    Configuration for multimodal Vision services (OCR, Image analysis).
    """

    model_config = SettingsConfigDict(
        env_prefix="VISION_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    provider: Literal["openai", "anthropic", "google", "ollama"] = Field(
        default="openai", description="Default vision capabilities provider"
    )

    openai_api_key: Optional[str] = Field(default=None, alias="OPENAI_API_KEY")
    anthropic_api_key: Optional[str] = Field(default=None, alias="ANTHROPIC_API_KEY")
    google_api_key: Optional[str] = Field(default=None, alias="GOOGLE_API_KEY")
    ollama_url: str = Field(default="http://localhost:11434", alias="OLLAMA_HOST")


class VoiceConfig(BaseSettings):
    """
    Configuration for Voice/Audio synthesis and recognition.
    """

    model_config = SettingsConfigDict(
        env_prefix="VOICE_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    provider: Literal["openai", "elevenlabs", "google"] = Field(
        default="openai", description="Default voice synthesis provider"
    )

    openai_api_key: Optional[str] = Field(default=None, alias="OPENAI_API_KEY")
    elevenlabs_api_key: Optional[str] = Field(default=None, alias="ELEVENLABS_API_KEY")
    google_api_key: Optional[str] = Field(default=None, alias="GOOGLE_API_KEY")
    google_credentials_path: Optional[str] = Field(
        default=None, alias="GOOGLE_APPLICATION_CREDENTIALS"
    )

    # ElevenLabs specific voice tuning.
    elevenlabs_model_id: str = Field(
        default="eleven_multilingual_v2",
        description="ElevenLabs model ID for TTS",
    )
    elevenlabs_stability: float = Field(
        default=0.5, ge=0.0, le=1.0, description="ElevenLabs voice stability"
    )
    elevenlabs_similarity_boost: float = Field(
        default=0.75, ge=0.0, le=1.0, description="ElevenLabs similarity boost"
    )

    # Model used for caching voice samples by content similarity.
    embedding_model: str = Field(
        default="all-MiniLM-L6-v2",
        description="Sentence-transformer model for semantic voice cache",
    )


class FineTuningConfig(BaseSettings):
    """
    Configuration for model fine-tuning and adaptation pipelines.
    """

    model_config = SettingsConfigDict(
        env_prefix="FINETUNE_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    openai_api_key: Optional[str] = Field(default=None, alias="OPENAI_API_KEY")
    together_api_key: Optional[str] = Field(default=None, alias="TOGETHER_API_KEY")


# --- Service Configuration Singletons ---
# These are the primary entry points for accessing settings across the core.

_llm_config: Optional[LLMConfig] = None
_vectorstore_config: Optional[VectorStoreConfig] = None
_chat_config: Optional[ChatConfig] = None


def get_llm_config() -> LLMConfig:
    """Retrieve or initialize the global LLMConfig singleton."""
    global _llm_config
    if _llm_config is None:
        _llm_config = LLMConfig()
        logger.info(
            f"Initialized LLMConfig with provider={_llm_config.provider}, model={_llm_config.model}"
        )
    return _llm_config


def get_vectorstore_config() -> VectorStoreConfig:
    """Retrieve or initialize the global VectorStoreConfig singleton."""
    global _vectorstore_config
    if _vectorstore_config is None:
        _vectorstore_config = VectorStoreConfig()
        logger.info(
            f"Initialized VectorStoreConfig with collection={_vectorstore_config.collection_name}"
        )
    return _vectorstore_config


def get_vectorstore_config_no_lazy() -> VectorStoreConfig:
    """Non-logging version for bootstrap safety."""
    return get_vectorstore_config()


def get_chat_config() -> ChatConfig:
    """Retrieve or initialize the global ChatConfig singleton."""
    global _chat_config
    if _chat_config is None:
        _chat_config = ChatConfig()
        logger.info(
            f"Initialized ChatConfig with streaming={_chat_config.streaming_enabled}"
        )
    return _chat_config


_vision_config: Optional[VisionConfig] = None
_voice_config: Optional[VoiceConfig] = None
_finetuning_config: Optional[FineTuningConfig] = None


def get_vision_config() -> VisionConfig:
    """Retrieve or initialize the global Vision configuration."""
    global _vision_config
    if _vision_config is None:
        _vision_config = VisionConfig()
    return _vision_config


def get_voice_config() -> VoiceConfig:
    """Retrieve or initialize the global Voice configuration."""
    global _voice_config
    if _voice_config is None:
        _voice_config = VoiceConfig()
    return _voice_config


def get_finetuning_config() -> FineTuningConfig:
    """Retrieve or initialize the global Fine-tuning configuration."""
    global _finetuning_config
    if _finetuning_config is None:
        _finetuning_config = FineTuningConfig()
    return _finetuning_config
