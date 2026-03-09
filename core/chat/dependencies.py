"""
Chat Dependency Management.

Defines the configuration and dependency containers used by the ChatService,
including embedders, rerankers, and caches.
"""

from __future__ import annotations

from core.observability.logging import get_logger
from dataclasses import dataclass, fields
from typing import Any, Callable, Mapping, Optional, Union

from sentence_transformers import CrossEncoder, SentenceTransformer  # type: ignore[import-untyped]

from core.cache import TTLCache, RedisTTLCache, create_redis_client
from core.chat.history import ChatHistoryManager

# Domain-specific imports removed - now provided by plugins
# Domain-specific imports removed - now provided by plugins
from core.nlp import CachedEmbedder, get_embedder, get_reranker
from core.config import (
    get_app_config,
    get_chat_config,
    get_storage_config,
    get_vectorstore_config,
)

_app_config = get_app_config()
_chat_config = get_chat_config()
_storage_config = get_storage_config()
_vs_config = get_vectorstore_config()

CACHE_BACKEND = _storage_config.cache_backend
CACHE_REDIS_PREFIX = _storage_config.cache_redis_prefix
CACHE_REDIS_URL = _storage_config.cache_redis_url

CHAT_MEMORY_ENABLED = _app_config.chat_memory_enabled
CHAT_MEMORY_MAX_SESSIONS = _app_config.chat_memory_max_sessions
CHAT_MEMORY_MAX_TURNS = _app_config.chat_memory_max_turns
CHAT_MEMORY_TTL = _app_config.chat_memory_ttl
CHAT_MEMORY_SUMMARY_ENABLED = _app_config.chat_memory_summary_enabled
CHAT_MEMORY_SUMMARY_MAX_CHARS = _app_config.chat_memory_summary_max_chars
CHAT_MEMORY_SUMMARY_MAX_TURNS = _app_config.chat_memory_summary_max_turns

CHAT_RERANK_CACHE_ENABLED = _app_config.chat_rerank_cache_enabled
CHAT_RERANK_CACHE_MAXSIZE = _app_config.chat_rerank_cache_maxsize
CHAT_RERANK_CACHE_TTL = _app_config.chat_rerank_cache_ttl

CHAT_RESPONSE_CACHE_ENABLED = _app_config.chat_response_cache_enabled
CHAT_RESPONSE_CACHE_MAXSIZE = _app_config.chat_response_cache_maxsize
CHAT_RESPONSE_CACHE_TTL = _app_config.chat_response_cache_ttl

EMBEDDER_MODEL = _vs_config.embedding_model
RERANKER_MODEL = _chat_config.reranker_model

logger = get_logger(__name__)
_redis_client = None


@dataclass
class ChatDependencies:
    """Container for objects and configurations required by ChatService."""

    embedder: Union[SentenceTransformer, CachedEmbedder]
    reranker: CrossEncoder
    response_cache: Optional[Union[TTLCache, RedisTTLCache]]
    rerank_cache: Optional[Union[TTLCache, RedisTTLCache]]
    history_manager: ChatHistoryManager
    newline: str
    double_newline: str
    section_separator: str
    # Domain-specific dependencies removed - now provided by plugins
    # project_planner: Optional[ProjectPlanner]


def _get_redis_client() -> Any:
    global _redis_client
    if _redis_client is None:
        _redis_client = create_redis_client(CACHE_REDIS_URL)
    return _redis_client


def _build_cache(
    maxsize: int, ttl: float, *, namespace: str
) -> Union[TTLCache, RedisTTLCache]:
    if CACHE_BACKEND == "redis":
        client = _get_redis_client()
        prefix = f"{CACHE_REDIS_PREFIX}:{namespace}"
        return RedisTTLCache(client, prefix=prefix, default_ttl=ttl)
    return TTLCache(maxsize=maxsize, ttl=ttl)


@dataclass
class ChatDependencyConfig:
    """Configuration options for initializing ChatDependencies."""

    embedder_model: str = EMBEDDER_MODEL
    reranker_model: str = RERANKER_MODEL
    response_cache_enabled: bool = CHAT_RESPONSE_CACHE_ENABLED
    response_cache_maxsize: int = CHAT_RESPONSE_CACHE_MAXSIZE
    response_cache_ttl: float = CHAT_RESPONSE_CACHE_TTL
    rerank_cache_enabled: bool = CHAT_RERANK_CACHE_ENABLED
    rerank_cache_maxsize: int = CHAT_RERANK_CACHE_MAXSIZE
    rerank_cache_ttl: float = CHAT_RERANK_CACHE_TTL
    history_enabled: bool = CHAT_MEMORY_ENABLED
    history_ttl: float = CHAT_MEMORY_TTL
    history_max_turns: int = CHAT_MEMORY_MAX_TURNS
    history_max_sessions: int = CHAT_MEMORY_MAX_SESSIONS
    summary_enabled: bool = CHAT_MEMORY_SUMMARY_ENABLED
    summary_max_turns: int = CHAT_MEMORY_SUMMARY_MAX_TURNS
    summary_max_chars: int = CHAT_MEMORY_SUMMARY_MAX_CHARS
    newline: str = "\n"
    double_newline: Optional[str] = None
    section_separator: Optional[str] = None
    # Domain-specific configuration removed - plugins provide their own
    # project_planner_factory: Optional[Callable[["ChatDependencyConfig"], Optional[ProjectPlanner]]] = None
    # test_case_generator_factory: Optional[Callable[["ChatDependencyConfig"], Optional[TestCaseGenerator]]] = None

    embedder_factory: Optional[Callable[[str], Any]] = None
    reranker_factory: Optional[Callable[[str], Any]] = None
    response_cache_factory: Optional[
        Callable[[int, float], Union[TTLCache, RedisTTLCache]]
    ] = None
    rerank_cache_factory: Optional[
        Callable[[int, float], Union[TTLCache, RedisTTLCache]]
    ] = None
    history_cache_factory: Optional[
        Callable[[int, float], Union[TTLCache, RedisTTLCache]]
    ] = None
    history_manager_factory: Optional[
        Callable[
            [Optional[Union[TTLCache, RedisTTLCache]], "ChatDependencyConfig"],
            ChatHistoryManager,
        ]
    ] = None

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any]) -> "ChatDependencyConfig":
        """
        Create a configuration from a dictionary.

        Args:
            mapping: A dictionary containing configuration keys and values.

        Returns:
            A new ChatDependencyConfig instance.
        """
        allowed = {field.name for field in fields(cls)}
        filtered = {key: value for key, value in mapping.items() if key in allowed}
        return cls(**filtered)

    def copy_with_overrides(self, **overrides: Any) -> "ChatDependencyConfig":
        """
        Create a copy of the configuration with specified overrides.

        Args:
            **overrides: Configuration fields to override.

        Returns:
            A new ChatDependencyConfig instance.
        """
        data = {field.name: getattr(self, field.name) for field in fields(self)}
        data.update({key: value for key, value in overrides.items() if key in data})
        return ChatDependencyConfig(**data)


def create_default_dependencies(
    config: Optional[ChatDependencyConfig] = None,
) -> ChatDependencies:
    """
    Bootstrap the default set of chat dependencies.

    Args:
        config: Optional configuration overrides.

    Returns:
        A populated ChatDependencies container.
    """
    cfg = config or ChatDependencyConfig()

    embedder_factory = cfg.embedder_factory or get_embedder
    embedder = embedder_factory(cfg.embedder_model)

    reranker_factory = cfg.reranker_factory or get_reranker
    reranker = reranker_factory(cfg.reranker_model)

    response_cache = None
    if cfg.response_cache_enabled:
        response_cache_factory = cfg.response_cache_factory or (
            lambda maxsize, ttl: _build_cache(maxsize, ttl, namespace="response")
        )
        response_cache = response_cache_factory(
            cfg.response_cache_maxsize, cfg.response_cache_ttl
        )

    rerank_cache = None
    if cfg.rerank_cache_enabled:
        rerank_cache_factory = cfg.rerank_cache_factory or (
            lambda maxsize, ttl: _build_cache(maxsize, ttl, namespace="rerank")
        )
        rerank_cache = rerank_cache_factory(
            cfg.rerank_cache_maxsize, cfg.rerank_cache_ttl
        )

    history_cache = None
    if cfg.history_enabled:
        history_cache_factory = cfg.history_cache_factory or (
            lambda maxsize, ttl: _build_cache(maxsize, ttl, namespace="history")
        )
        history_cache = history_cache_factory(cfg.history_max_sessions, cfg.history_ttl)

    if cfg.history_manager_factory is not None:
        history_manager = cfg.history_manager_factory(history_cache, cfg)
    else:
        history_manager = ChatHistoryManager(
            history_cache,
            max_turns=cfg.history_max_turns,
            summary_enabled=cfg.summary_enabled,
            summary_max_turns=cfg.summary_max_turns,
            summary_max_chars=cfg.summary_max_chars,
        )

    newline = cfg.newline
    double_newline = (
        cfg.double_newline if cfg.double_newline is not None else newline * 2
    )
    section_separator = (
        cfg.section_separator
        if cfg.section_separator is not None
        else f"{double_newline}---{double_newline}"
    )

    # Domain-specific dependencies removed - plugins handle their own initialization
    # Domain-specific dependencies removed - now provided by plugins

    return ChatDependencies(
        embedder=embedder,
        reranker=reranker,
        response_cache=response_cache,
        rerank_cache=rerank_cache,
        history_manager=history_manager,
        newline=newline,
        double_newline=double_newline,
        section_separator=section_separator,
    )


__all__ = [
    "ChatDependencies",
    "ChatDependencyConfig",
    "create_default_dependencies",
]
