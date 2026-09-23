"""
Performance-First Lazy Initialization Subsystem.

Implements the deferred instantiation pattern for resource-intensive
core components. Ensures that heavy dependencies (DB connections,
LLM clients, Vector stores) are initialized only upon their first
functional call, significantly reducing system cold-start latency.
"""

import asyncio
from typing import Any

from core.observability.logging import get_logger, redact_url_credentials

logger = get_logger(__name__)


async def initialize_postgres() -> Any:
    """
    Lazy initialize the PostgreSQL connection using the database configuration.

    Initializes the database schema if necessary and returns the main storage
    interface for persistence.

    Runs inside :func:`core.db.connection.system_tenant_scope`: this is boot
    work with no request behind it, and under ``DB_RLS_ENABLED`` an unbound
    tenant is refused rather than degraded to ``default``.

    Returns:
        Any: The initialized core storage instance.
    """
    from core.db.connection import system_tenant_scope
    from core.storage import get_storage, init_db

    logger.info("🗄️ Lazy initializing Postgres connection...")
    with system_tenant_scope():
        await init_db()
        core_storage = await get_storage()
    logger.info("✅ Postgres initialized")
    return core_storage


async def initialize_vectorstore() -> Any:
    """
    Lazy initialize the configured vector store service (Qdrant or pgvector).

    Ensures the required collections are created before returning the service.

    Runs inside :func:`core.db.connection.system_tenant_scope`, exactly like
    :func:`initialize_postgres`. Collection setup is not always a remote call:
    the pgvector backend creates its extension, table and indexes through the
    *shared Postgres pool*, and under ``DB_RLS_ENABLED`` that checkout refuses
    to invent a tenant nobody bound. Unscoped, the resulting
    ``TenantContextError`` was rewrapped as ``VectorStoreError`` by
    ``VectorStoreService.create_collection`` and escaped the ``except
    ImportError`` in :mod:`core.api.lifespan`, so the app did not boot at all.
    The scope is a contextvar set, so the Qdrant path is unaffected.

    Returns:
        Any: The initialized VectorStore service instance.
    """
    from core.db.connection import system_tenant_scope
    from core.services.vectorstore import get_vectorstore_service

    logger.info("📦 Lazy initializing vectorstore...")
    vectorstore_service = get_vectorstore_service()
    with system_tenant_scope():
        await vectorstore_service.create_collection()
    logger.info("✅ Vectorstore initialized")
    return vectorstore_service


async def initialize_llm() -> Any:
    """
    Lazy initialize the Large Language Model (LLM) service.

    Loads the LLM configuration and initializes the configured provider
    (OpenAI, Anthropic, Ollama, etc.).

    Returns:
        Any: The global LLM service instance.
    """
    from core.services.llm.service import get_llm_service

    logger.info("🤖 Lazy initializing LLM service...")
    llm_service = get_llm_service()
    logger.info("✅ LLM service initialized")
    return llm_service


async def initialize_graph() -> Any:
    """
    Lazy initialize the Graph database connection (e.g., NetworkX or specialized DB).

    Pings the connection and logs its status.

    Returns:
        Any: The initialized GraphDB instance.
    """
    from core.config import get_storage_config
    from core.graph import graph_db

    storage_config = get_storage_config()

    logger.info("🕸️ Lazy initializing GraphDB...")
    graph_ok = graph_db.ping()
    if graph_ok:
        logger.info(
            f"✅ GraphDB connected to {redact_url_credentials(storage_config.graph_db_url)} "
            f"(graph={storage_config.graph_db_name})"
        )
    else:
        logger.warning(
            f"⚠️ GraphDB enabled but not reachable "
            f"({redact_url_credentials(storage_config.graph_db_url)}, graph={storage_config.graph_db_name})"
        )
    return graph_db


async def initialize_redis() -> Any:
    """
    Lazy initialize the Redis connection for caching and pub/sub.

    Returns:
        Any: The asynchronous Redis client instance.
    """
    import redis.asyncio as redis

    from core.config import get_storage_config

    storage_config = get_storage_config()

    logger.info(
        f"🔴 Lazy initializing Redis at "
        f"{redact_url_credentials(storage_config.cache_redis_url)}..."
    )
    redis_client = redis.from_url(
        storage_config.cache_redis_url, encoding="utf-8", decode_responses=True
    )
    # Test connection
    await redis_client.ping()
    logger.info("✅ Redis initialized")
    return redis_client


async def _memory_provider(collection: str) -> Any | None:
    """Build the memory backing store without stalling the event loop.

    The construction itself lives in :func:`core.memory.providers.build_memory_provider`
    so the lazy registry and the process-wide singleton cannot disagree about
    whether memories are persisted. It runs in a worker thread because the
    vector client performs a server compatibility check when it is built.

    Args:
        collection: Vector-store collection to keep this memory in.

    Returns:
        A provider, or ``None`` when persistence is off or unavailable.
    """
    from core.memory.providers import build_memory_provider

    return await asyncio.to_thread(build_memory_provider, collection)


async def initialize_memory() -> Any:
    """
    Lazy initialize the core AgentMemory manager.

    This manager orchestrates hierarchy and persistence for agent experiences.

    Returns:
        Any: The global AgentMemory instance.
    """
    from core.memory.manager import AgentMemory
    from core.services.llm.service import get_llm_service

    logger.info("🧠 Lazy initializing AgentMemory...")
    memory_manager = AgentMemory(
        provider=await _memory_provider("agent_memory"),
        # Compaction refuses to run without a summarizer rather than replacing
        # a batch of memories with a truncation of three of them.
        llm_service=get_llm_service(),
    )
    logger.info("✅ AgentMemory initialized")
    return memory_manager


async def initialize_evaluation() -> Any:
    """
    Lazy initialize the Evaluation service for benchmarking results.

    Returns:
        Any: The started EvaluationService instance.
    """
    from core.evaluation.service import EvaluationService

    logger.info("⚖️ Lazy initializing Evaluation Service...")
    evaluation_service = EvaluationService()
    evaluation_service.start()
    logger.info("✅ Evaluation Service initialized")
    return evaluation_service


async def initialize_evolution() -> Any:
    """
    Lazy initialize the Evolution service for continuous learning.

    Interdependently initializes the Memory system first if not already available.

    Returns:
        Any: The started EvolutionService instance.
    """
    from core.di.lazy_registry import get_lazy_registry
    from core.learning.evolution import EvolutionService

    logger.info("🧬 Lazy initializing Evolution Service...")

    # Factories are keyed by resource name (RESOURCE_FACTORIES), never by
    # class. Asking for ``AgentMemory`` raised KeyError on every boot, which
    # the lifespan logged and swallowed, so evolution never started.
    lazy_registry = get_lazy_registry()
    memory_manager: Any = await lazy_registry.get_or_create("memory")

    evolution_service = EvolutionService(memory_manager=memory_manager)
    evolution_service.start()
    logger.info("✅ Evolution Service initialized")
    return evolution_service


async def initialize_hierarchical_memory() -> Any:
    """
    Lazy initialize the Hierarchical Memory system with embedding support.

    Distinguishes between short-term context and long-term knowledge retrieval.

    Returns:
        Any: The HierarchicalMemory instance.
    """
    from core.memory.hierarchy import HierarchicalMemory
    from core.nlp.models import get_embedder
    from core.services.llm.service import get_llm_service

    logger.info("🧠 Lazy initializing HierarchicalMemory...")

    llm_service = get_llm_service()
    embedder = get_embedder()

    memory = HierarchicalMemory(
        llm_service=llm_service,
        embedder=embedder,
        provider=await _memory_provider("hierarchical_memory"),
    )
    logger.info("✅ HierarchicalMemory initialized")
    return memory


# Global mapping of resource names to their corresponding factory functions.
# This registry is used by `LazyRegistry` to instantiate services on-demand.
RESOURCE_FACTORIES = {
    "postgres": initialize_postgres,
    "vectorstore": initialize_vectorstore,
    "llm": initialize_llm,
    "graph": initialize_graph,
    "redis": initialize_redis,
    "memory": initialize_memory,
    "hierarchical_memory": initialize_hierarchical_memory,
    "evaluation": initialize_evaluation,
    "evolution": initialize_evolution,
}
