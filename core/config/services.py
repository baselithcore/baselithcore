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
from typing import Any, Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Re-exported for backwards compatibility: the multimodal schemas used to live
# here, and `core.config` still resolves them through this module.
from core.config.multimodal import (
    FineTuningConfig,
    VisionConfig,
    VoiceConfig,
    get_finetuning_config,
    get_vision_config,
    get_voice_config,
)

# NOTE: Using direct logging.getLogger() here instead of core.observability.logging.get_logger()
# This is intentional: config modules initialize during framework bootstrap, before the
# observability infrastructure is fully set up. Direct logging prevents circular dependencies.
logger = logging.getLogger(__name__)


class LLMConfig(BaseSettings):
    """
    Configuration for Large Language Model providers.

    Manages connection parameters, generation hyper-parameters (temperature),
    and semantic search-based response caching.
    """

    # ``env_ignore_empty`` matters for the credential fields below: they read
    # from an ``AliasChoices`` list, and a blank ``LLM_ANTHROPIC_API_KEY=`` line
    # would otherwise *win* the lookup and shadow the SDK-standard
    # ``ANTHROPIC_API_KEY`` that actually carries the key.
    model_config = SettingsConfigDict(
        env_prefix="LLM_",
        case_sensitive=False,
        extra="ignore",
        env_ignore_empty=True,
    )

    # The backend provider to route LLM requests to.
    provider: Literal["openai", "ollama", "huggingface", "anthropic", "gemini"] = Field(
        default="ollama",
        description="LLM provider (openai, ollama, huggingface, anthropic, or gemini)",
    )

    # The specific model family/version (e.g., 'gpt-4o', 'llama3.2', 'claude-3-opus').
    model: str = Field(default="llama3.2", description="Model name to use")

    # API credentials. If None, service might depend on local environment or local proxy.
    api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("LLM_API_KEY", "LLM_OPENAI_API_KEY"),
        description="API key for provider",
    )

    # Custom endpoint for self-hosted or proxied LLMs (like Ollama or vLLM).
    # This one belongs to the *default* ``provider`` above — see
    # ``core.services.llm.runtime.api_base_for`` for why that matters the
    # moment a per-plugin policy routes a call somewhere else.
    api_base: str | None = Field(
        default=None, description="Base URL for the default provider's API"
    )

    # == Per-provider endpoints (central LLM policy) ==
    # ``api_base`` above belongs to the default ``provider``. When a per-plugin
    # LLM policy routes a call to a *different* provider, handing it that URL
    # points it at the wrong server — an OpenAI-compatible gateway answering
    # nothing on Ollama's ``/api/chat``, which reads as a hang, not a
    # misconfiguration. Dedicated fields keep each provider's endpoint its own.
    # Deliberately NOT aliased to ``OLLAMA_HOST``: that variable is commonly
    # exported machine-wide, and folding it in here would let it outrank an
    # explicit ``LLM_API_BASE`` on an Ollama-default deployment. It stays the
    # last resort inside ``core.services.llm.runtime.api_base_for``.
    ollama_api_base: str | None = Field(
        default=None,
        validation_alias=AliasChoices("LLM_OLLAMA_API_BASE"),
        description="Dedicated Ollama endpoint (for policy-routed calls)",
    )

    # == Per-provider credentials (central LLM policy) ==
    # `api_key` above belongs to the *default* `provider`. When a per-plugin
    # LLM policy routes a call to a different provider, that provider's key is
    # read from these dedicated fields (each honours the SDK-standard env var).
    # Keys stay in configuration — a policy names a provider, never a secret.
    anthropic_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("LLM_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
        description="Dedicated Anthropic API key (for policy-routed calls)",
    )

    # == Anthropic serving backend ==
    # 'api' (default) talks to api.anthropic.com with the key above.
    # 'bedrock'/'vertex' use the Anthropic SDK's native cloud clients, which
    # authenticate through the cloud's own credential chain (AWS SigV4 /
    # Google ADC) — no Anthropic API key required.
    anthropic_backend: Literal["api", "bedrock", "vertex"] = Field(
        default="api",
        validation_alias=AliasChoices("LLM_ANTHROPIC_BACKEND"),
        description="Anthropic serving backend: api, bedrock, or vertex",
    )
    anthropic_aws_region: str | None = Field(
        default=None,
        validation_alias=AliasChoices("LLM_ANTHROPIC_AWS_REGION"),
        description="Bedrock region (falls back to the SDK's AWS_REGION)",
    )
    anthropic_vertex_project: str | None = Field(
        default=None,
        validation_alias=AliasChoices("LLM_ANTHROPIC_VERTEX_PROJECT"),
        description="Vertex project id (falls back to GOOGLE_CLOUD_PROJECT)",
    )
    anthropic_vertex_region: str | None = Field(
        default=None,
        validation_alias=AliasChoices("LLM_ANTHROPIC_VERTEX_REGION"),
        description="Vertex region (falls back to CLOUD_ML_REGION)",
    )

    openai_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENAI_API_KEY"),
        description="Dedicated OpenAI API key (for policy-routed calls)",
    )

    huggingface_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("LLM_HUGGINGFACE_API_KEY", "HF_TOKEN"),
        description="Dedicated HuggingFace API key (for policy-routed calls)",
    )

    gemini_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "LLM_GEMINI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"
        ),
        description="Dedicated Google Gemini API key (for policy-routed calls)",
    )

    # Controls randomness: 0.0 is deterministic, 1.0+ is creative.
    temperature: float = Field(
        default=0.7, ge=0.0, le=2.0, description="Temperature for generation"
    )

    # Optional cap on completion length.
    max_tokens: int | None = Field(
        default=None, description="Maximum tokens to generate"
    )

    # == Client timeouts ==
    # Hard per-request deadline at the SDK/HTTP layer. SDK defaults (600s for
    # Anthropic/OpenAI) let a hung upstream block a caller for ~10 minutes.
    request_timeout: float = Field(
        default=120.0,
        gt=0,
        description="Total per-request timeout (seconds) for provider SDK calls",
    )

    connect_timeout: float = Field(
        default=5.0,
        gt=0,
        description="TCP connect timeout (seconds) for provider SDK calls",
    )

    # == Native tool-calling / structured outputs ==
    # On by default: the structured path still guards on the provider's
    # ``supports_native_tools`` flag, so providers without a native API keep
    # using the prompt-coercion fallback. Set false to force coercion.
    enable_native_tools: bool = Field(
        default=True,
        description="Use providers' native tool-calling / structured-output APIs "
        "in LLMService.generate() (falls back to prompt coercion when off).",
    )

    # Per-process cap on simultaneously in-flight provider calls. Token
    # budgets and rate limits bound spend per request/minute, but nothing
    # bounded concurrency: a burst of requests opened that many provider
    # streams at once. 0 (default) keeps the historical unlimited behavior.
    max_concurrent_requests: int = Field(
        default=0,
        ge=0,
        description="Max concurrent LLM provider calls per process "
        "(0 = unlimited). Env: LLM_MAX_CONCURRENT_REQUESTS via the LLM_ "
        "prefix.",
    )

    # == Cross-provider fallback chain ==
    # Ordered "provider:model" pairs tried when the primary provider fails or
    # its circuit breaker is open. Budget/deadline errors never fall through.
    # Each entry needs its provider's dedicated credentials configured.
    fallback_chain: str = Field(
        default="",
        description="Comma-separated ordered 'provider:model' fallback entries "
        "(e.g. 'openai:gpt-4o-mini,ollama:llama3.2'). Empty disables fallback.",
    )

    # Per-stage latency bound for the chain above. Without one, every stage may
    # spend the full ``request_timeout``, so a chain ending at a slow local
    # model can hold one HTTP request open for minutes — long past the point a
    # reverse proxy (60s by default in nginx) has given up. With it, a stage
    # that overruns becomes a failed attempt and the chain moves on.
    fallback_stage_timeout: float | None = Field(
        default=None,
        gt=0,
        description="Per-stage timeout (seconds) for the fallback chain; "
        "unset means each stage may use the full request timeout.",
    )

    # Bounds the *whole* chain, which nothing else does when no per-stage
    # timeout is set: each stage may spend ``request_timeout`` across
    # ``retry_max_attempts`` plus backoff, so three stages run for many
    # minutes. Unset means ``request_timeout`` — a chain has no business
    # outliving its request. Stages that no longer fit are skipped.
    fallback_total_timeout: float | None = Field(
        default=None,
        gt=0,
        description="Wall-clock timeout (seconds) for the whole fallback "
        "chain; unset falls back to request_timeout.",
    )

    # == Cost-aware model routing ==
    # When enabled, callers may pass task_category to generate_response();
    # the router picks a model tier for that category. Explicit per-call
    # model= and policy-pinned models always win over routing.
    routing_enabled: bool = Field(
        default=False,
        description="Enable cost-aware model routing by task category.",
    )

    routing_policy: str = Field(
        default="",
        description="JSON object mapping task category to model id "
        '(e.g. \'{"planning": "gpt-4o", "classification": "gpt-4o-mini"}\'). '
        "Empty uses the built-in default policy.",
    )

    # == Extended thinking by task category ==
    # When enabled, calls that carry a task_category (and no explicit
    # effort/thinking_budget) get the default effort tier for that category
    # (core.services.llm.thinking.DEFAULT_EFFORT_BY_TASK_CATEGORY): hard
    # planning/reasoning gets a reasoning scratchpad, high-volume
    # classification stays off. Only providers with a thinking API honour it
    # (currently Anthropic); others ignore the hint.
    thinking_enabled: bool = Field(
        default=False,
        description="Derive an extended-thinking effort tier from task_category "
        "for providers that support it (off keeps previous behaviour).",
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
        if v not in ["openai", "ollama", "huggingface", "anthropic", "gemini"]:
            raise ValueError(f"Unsupported provider: {v}")
        return v

    @field_validator(
        "max_tokens",
        "fallback_stage_timeout",
        "api_key",
        "api_base",
        "ollama_api_base",
        "anthropic_api_key",
        "openai_api_key",
        "huggingface_api_key",
        "gemini_api_key",
        mode="before",
    )
    @classmethod
    def validate_empty_to_none(cls, v: Any) -> Any:
        """Treat a blank environment value as *unset* rather than an empty value.

        A ``.env`` line left as ``ANTHROPIC_API_KEY=`` (or one carrying only
        stray whitespace) must not read as a present credential: admin surfaces
        would advertise the provider as configured and the pin would only fail
        at the first provider call.
        """
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @model_validator(mode="after")
    def warn_trust_remote_code(self) -> "LLMConfig":
        if self.huggingface_trust_remote_code:
            logger.warning(
                "SECURITY: huggingface_trust_remote_code=True — "
                "this allows arbitrary code execution from remote HuggingFace model repositories."
            )
        return self


# VectorStoreConfig moved to core.config.vectorstore (module size cap);
# re-exported here so existing imports keep working.
from core.config.vectorstore import (  # noqa: E402
    VectorStoreConfig,
    get_vectorstore_config,
    get_vectorstore_config_no_lazy,
)


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


# --- Service Configuration Singletons ---
# These are the primary entry points for accessing settings across the core.

_llm_config: LLMConfig | None = None
_chat_config: ChatConfig | None = None


def get_llm_config() -> LLMConfig:
    """Retrieve or initialize the global LLMConfig singleton."""
    global _llm_config
    if _llm_config is None:
        _llm_config = LLMConfig()
        logger.info(
            f"Initialized LLMConfig with provider={_llm_config.provider}, model={_llm_config.model}"
        )
    return _llm_config


def get_chat_config() -> ChatConfig:
    """Retrieve or initialize the global ChatConfig singleton."""
    global _chat_config
    if _chat_config is None:
        _chat_config = ChatConfig()
        logger.info(
            f"Initialized ChatConfig with streaming={_chat_config.streaming_enabled}"
        )
    return _chat_config


__all__ = [
    "ChatConfig",
    "FineTuningConfig",
    "LLMConfig",
    "VectorStoreConfig",
    "VisionConfig",
    "VoiceConfig",
    "get_chat_config",
    "get_finetuning_config",
    "get_llm_config",
    "get_vectorstore_config",
    "get_vectorstore_config_no_lazy",
    "get_vision_config",
    "get_voice_config",
]
