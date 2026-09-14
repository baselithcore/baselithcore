"""
Chat Dependency Management.

Defines the configuration and dependency containers used by the ChatService,
including embedders, rerankers, and caches.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, fields
from typing import TYPE_CHECKING, Any

from core.observability.logging import get_logger

if TYPE_CHECKING:
    from sentence_transformers import (  # type: ignore[import-untyped]
        CrossEncoder,
        SentenceTransformer,
    )
else:
    CrossEncoder = Any
    SentenceTransformer = Any


from core.cache import RedisTTLCache, TTLCache, create_redis_client
from core.chat.history import ChatHistoryManager
from core.chat.precheck import PRECHECK_CACHE_NAMESPACE
from core.config import (
    get_app_config,
    get_chat_config,
    get_storage_config,
    get_vectorstore_config,
)
from core.context import get_tenant_or_default

# Domain-specific imports removed - now provided by plugins
# Domain-specific imports removed - now provided by plugins
from core.nlp import CachedEmbedder, get_embedder, get_reranker

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

# Pre-retrieval answer cache: opt-in, separate namespace, its own short TTL.
CHAT_RAG_PRECHECK_ENABLED = _app_config.chat_rag_precheck_enabled
CHAT_RAG_PRECHECK_MAXSIZE = _app_config.chat_rag_precheck_maxsize
CHAT_RAG_PRECHECK_TTL = _app_config.chat_rag_precheck_ttl

EMBEDDER_MODEL = _vs_config.embedding_model
RERANKER_MODEL = _chat_config.reranker_model

logger = get_logger(__name__)
_redis_client = None


@dataclass
class ChatDependencies:
    """Container for objects and configurations required by ChatService."""

    embedder: SentenceTransformer | CachedEmbedder
    reranker: CrossEncoder
    response_cache: TTLCache | RedisTTLCache | None
    precheck_cache: TTLCache | RedisTTLCache | None
    rerank_cache: TTLCache | RedisTTLCache | None
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


class TenantScopedRedisCache(RedisTTLCache[Any, Any]):
    """A Redis cache whose keyspace is the *calling* tenant's, not the process's.

    ``RedisTTLCache`` fixes its prefix at construction, but the chat caches are
    built once per process (at import of :func:`create_default_dependencies`)
    while the tenant is only known per request. One instance therefore served
    every tenant out of a single keyspace: ``clear()`` flushed all tenants at
    once, and any key that did not itself carry the tenant crossed the
    boundary on a plain ``get``.

    Keys are written under ``{base_prefix}:{tenant}:{namespace}`` with the
    tenant resolved on *every* operation, so reads, writes and ``clear()`` all
    stay inside the caller's keyspace. Out-of-request callers (background
    tasks, scripts, bootstrap) resolve to ``"default"`` via
    :func:`core.context.get_tenant_or_default` — the pre-tenant keyspace —
    rather than failing work that has no request to inherit a tenant from.

    Why ``get_tenant_or_default`` here when
    ``RetrievalContextMixin.check_cache`` *withholds* its key under strict
    isolation: the two guard different things. This class only decides **where**
    an entry is stored, and ``"default"`` is a real, separate bucket — an
    unbound caller lands in its own keyspace, never in a request tenant's. The
    key decides **what** may be served, and there a placeholder tenant would
    put unrelated callers in one bucket and let an answer cross between them.
    Storage therefore degrades (so background work keeps running) while the
    serve decision fails closed. This class is also the *only* keyspace for
    layers whose keys carry no tenant of their own — chat history, keyed on a
    client-supplied ``conversation_id`` — which is precisely why the segment
    lives in the prefix rather than being left to each caller's key.

    Note: introducing the tenant segment moves every key, so the first deploy
    starts from a cold cache. All three layers behind this class are
    TTL-bounded (answers, rerank scores, chat history), so the old entries
    expire on their own; the alternative — keeping one keyspace so the entries
    survive — is the cross-tenant reachability this class removes.
    """

    def __init__(
        self,
        client: Any,
        *,
        base_prefix: str,
        namespace: str,
        default_ttl: float | None = None,
    ) -> None:
        """Build a tenant-scoped view over one Redis client.

        Args:
            client: The async Redis client to issue commands on.
            base_prefix: Deployment-wide key prefix (``CACHE_REDIS_PREFIX``).
            namespace: Cache-layer segment, e.g. ``response`` or ``rerank``.
            default_ttl: Entry TTL in seconds; falls back to the cache config.
        """
        # The parent prefix is only a placeholder: every key path below
        # re-derives the prefix from the bound tenant.
        super().__init__(
            client,
            prefix=f"{base_prefix}:{namespace}",
            default_ttl=default_ttl,
        )
        self._base_prefix = base_prefix.rstrip(":")
        self._namespace_segment = namespace.strip(":")

    def _tenant_prefix(self) -> str:
        return (
            f"{self._base_prefix}:{get_tenant_or_default()}:{self._namespace_segment}"
        )

    @property
    def namespace(self) -> str:
        """Key prefix the *calling* tenant reads and writes under."""
        return self._tenant_prefix()

    def _serialize_key(self, key: Any) -> str:
        # Reuse the parent's digest (orjson + SHA-256) and only re-prefix it.
        # The digest is hex, so it never contains the separator.
        digest = super()._serialize_key(key).rsplit(":", 1)[-1]
        return f"{self._tenant_prefix()}:{digest}"

    async def clear(self) -> None:
        """Clear only the calling tenant's namespace.

        Deliberately narrower than the inherited ``clear()``: flushing one
        tenant's cache must not evict every other tenant's entries.
        """
        pattern = f"{self._tenant_prefix()}:*"
        cursor = 0
        while True:
            cursor, keys = await self._client.scan(
                cursor=cursor, match=pattern, count=500
            )
            if keys:
                await self._client.delete(*keys)
            if cursor == 0:
                break


class TenantScopedTTLCache(TTLCache[Any, Any]):
    """The in-process counterpart of :class:`TenantScopedRedisCache`.

    ``CACHE_BACKEND`` defaults to ``local``, so this — not the Redis class — is
    what most deployments actually run. A plain ``TTLCache`` is one dict for
    the whole process, and the layers built on it are not all keyed by content:
    chat history is keyed on ``conversation_id``, which arrives from the client
    (``core.services.chat.utils.history``). Guessing or replaying another
    tenant's conversation id therefore returned that conversation.

    Every key is namespaced ``(tenant, key)`` with the tenant resolved on each
    operation, so the two backends behave identically: reads, writes,
    ``delete``, ``clear`` and ``len()`` all see only the calling tenant's
    entries. Out-of-request callers resolve to ``"default"`` via
    :func:`core.context.get_tenant_or_default` — see
    :class:`TenantScopedRedisCache` for why that is right here.

    ``maxsize`` stays a single process-wide bound (one LRU, one memory
    ceiling), so a busy tenant can still *evict* another's entries. That is an
    availability trade-off, not a disclosure one: an evicted entry is a cache
    miss, never someone else's answer.
    """

    def _scoped(self, key: Any) -> tuple[str, Any]:
        return (get_tenant_or_default(), key)

    @staticmethod
    def _belongs(stored_key: Any, tenant: str) -> bool:
        """Whether a raw store key is this tenant's (defensive on shape)."""
        return (
            isinstance(stored_key, tuple)
            and len(stored_key) == 2
            and stored_key[0] == tenant
        )

    async def get(self, key: Any) -> Any | None:
        """Read the calling tenant's entry for ``key``."""
        return await super().get(self._scoped(key))

    async def set(self, key: Any, value: Any) -> None:
        """Write ``value`` under the calling tenant's ``key``."""
        await super().set(self._scoped(key), value)

    async def get_many(self, keys: Sequence[Any]) -> list[Any | None]:
        """Batch read, all within the calling tenant's namespace."""
        return await super().get_many([self._scoped(key) for key in keys])

    async def set_many(self, items: Sequence[tuple[Any, Any]]) -> None:
        """Batch write, all within the calling tenant's namespace."""
        await super().set_many([(self._scoped(key), value) for key, value in items])

    async def delete(self, key: Any) -> None:
        """Drop the calling tenant's entry for ``key``."""
        await super().delete(self._scoped(key))

    async def clear(self) -> None:
        """Clear only the calling tenant's entries.

        Deliberately narrower than the inherited ``clear()``: flushing one
        tenant's cache must not evict every other tenant's entries.
        """
        tenant = get_tenant_or_default()
        # The lock is not reentrant, so this walks the store directly rather
        # than delegating to the inherited (also-locking) helpers.
        with self._lock:
            for stored_key in [
                key for key in self._store if self._belongs(key, tenant)
            ]:
                self._store.pop(stored_key, None)

    def __len__(self) -> int:
        """Number of live entries **this tenant** can see."""
        tenant = get_tenant_or_default()
        with self._lock:
            if self._should_purge():
                self._purge_expired()
            return sum(1 for key in self._store if self._belongs(key, tenant))


def _build_cache(
    maxsize: int, ttl: float, *, namespace: str
) -> TTLCache | RedisTTLCache:
    """Build one chat cache layer, tenant-scoped on either backend.

    Args:
        maxsize: Entry cap for the in-process backend.
        ttl: Entry lifetime in seconds.
        namespace: Cache-layer segment, e.g. ``response`` or ``history``.

    Returns:
        A cache whose keyspace is the calling tenant's, whichever backend is
        configured — the two must not differ in isolation, only in storage.
    """
    if CACHE_BACKEND == "redis":
        client = _get_redis_client()
        return TenantScopedRedisCache(
            client,
            base_prefix=CACHE_REDIS_PREFIX,
            namespace=namespace,
            default_ttl=ttl,
        )
    return TenantScopedTTLCache(maxsize=maxsize, ttl=ttl)


@dataclass
class ChatDependencyConfig:
    """Configuration options for initializing ChatDependencies."""

    embedder_model: str = EMBEDDER_MODEL
    reranker_model: str = RERANKER_MODEL
    response_cache_enabled: bool = CHAT_RESPONSE_CACHE_ENABLED
    response_cache_maxsize: int = CHAT_RESPONSE_CACHE_MAXSIZE
    response_cache_ttl: float = CHAT_RESPONSE_CACHE_TTL
    precheck_cache_enabled: bool = CHAT_RAG_PRECHECK_ENABLED
    precheck_cache_maxsize: int = CHAT_RAG_PRECHECK_MAXSIZE
    precheck_cache_ttl: float = CHAT_RAG_PRECHECK_TTL
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
    double_newline: str | None = None
    section_separator: str | None = None
    # Domain-specific configuration removed - plugins provide their own
    # project_planner_factory: Optional[Callable[["ChatDependencyConfig"], Optional[ProjectPlanner]]] = None
    # test_case_generator_factory: Optional[Callable[["ChatDependencyConfig"], Optional[TestCaseGenerator]]] = None

    embedder_factory: Callable[[str], Any] | None = None
    reranker_factory: Callable[[str], Any] | None = None
    response_cache_factory: Callable[[int, float], TTLCache | RedisTTLCache] | None = (
        None
    )
    precheck_cache_factory: Callable[[int, float], TTLCache | RedisTTLCache] | None = (
        None
    )
    rerank_cache_factory: Callable[[int, float], TTLCache | RedisTTLCache] | None = None
    history_cache_factory: Callable[[int, float], TTLCache | RedisTTLCache] | None = (
        None
    )
    history_manager_factory: (
        Callable[
            [TTLCache | RedisTTLCache | None, ChatDependencyConfig], ChatHistoryManager
        ]
        | None
    ) = None

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any]) -> ChatDependencyConfig:
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

    def copy_with_overrides(self, **overrides: Any) -> ChatDependencyConfig:
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
    config: ChatDependencyConfig | None = None,
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

    # Deliberately a *distinct* cache object with its own namespace and TTL:
    # the pre-check layer accepts a staleness window the response cache does
    # not, so it must be flushable and expirable on its own terms.
    precheck_cache = None
    if cfg.precheck_cache_enabled:
        precheck_cache_factory = cfg.precheck_cache_factory or (
            lambda maxsize, ttl: _build_cache(
                maxsize, ttl, namespace=PRECHECK_CACHE_NAMESPACE
            )
        )
        precheck_cache = precheck_cache_factory(
            cfg.precheck_cache_maxsize, cfg.precheck_cache_ttl
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
        precheck_cache=precheck_cache,
        rerank_cache=rerank_cache,
        history_manager=history_manager,
        newline=newline,
        double_newline=double_newline,
        section_separator=section_separator,
    )


__all__ = [
    "ChatDependencies",
    "ChatDependencyConfig",
    "TenantScopedRedisCache",
    "TenantScopedTTLCache",
    "create_default_dependencies",
]
