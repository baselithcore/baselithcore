"""
Performance-First Lazy Initialization Subsystem.

Implements the deferred instantiation pattern for resource-intensive
core components. Ensures that heavy dependencies (DB connections,
LLM clients, Vector stores) are initialized only upon their first
functional call, significantly reducing system cold-start latency.
"""

from core.observability.logging import get_logger
from typing import Any

logger = get_logger(__name__)


async def initialize_postgres() -> Any:
    """
    Lazy initialize the PostgreSQL connection using the database configuration.

    Initializes the database schema if necessary and returns the main storage
    interface for persistence.

    Returns:
        Any: The initialized core storage instance.
    """
    from core.storage import init_db, get_storage

    logger.info("🗄️ Lazy initializing Postgres connection...")
    await init_db()
    core_storage = await get_storage()
    logger.info("✅ Postgres initialized")
    return core_storage


async def initialize_vectorstore() -> Any:
    """
    Lazy initialize the vector store service (typically Qdrant).

    Ensures the required collections are created before returning the service.

    Returns:
        Any: The initialized VectorStore service instance.
    """
    from core.services.vectorstore import get_vectorstore_service

    logger.info("📦 Lazy initializing Qdrant vectorstore...")
    vectorstore_service = get_vectorstore_service()
    await vectorstore_service.create_collection()
    logger.info("✅ Qdrant vectorstore initialized")
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
    from core.graph import graph_db
    from core.config import get_storage_config

    storage_config = get_storage_config()

    logger.info("🕸️ Lazy initializing GraphDB...")
    graph_ok = graph_db.ping()
    if graph_ok:
        logger.info(
            f"✅ GraphDB connected to {storage_config.graph_db_url} "
            f"(graph={storage_config.graph_db_name})"
        )
    else:
        logger.warning(
            f"⚠️ GraphDB enabled but not reachable "
            f"({storage_config.graph_db_url}, graph={storage_config.graph_db_name})"
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

    logger.info(f"🔴 Lazy initializing Redis at {storage_config.cache_redis_url}...")
    redis_client = redis.from_url(
        storage_config.cache_redis_url, encoding="utf-8", decode_responses=True
    )
    # Test connection
    await redis_client.ping()
    logger.info("✅ Redis initialized")
    return redis_client


async def initialize_memory() -> Any:
    """
    Lazy initialize the core AgentMemory manager.

    This manager orchestrates hierarchy and persistence for agent experiences.

    Returns:
        Any: The global AgentMemory instance.
    """
    from core.memory.manager import AgentMemory

    logger.info("🧠 Lazy initializing AgentMemory...")
    memory_manager = AgentMemory()
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
    from core.learning.evolution import EvolutionService
    from core.di.lazy_registry import get_lazy_registry
    from core.memory.manager import AgentMemory

    logger.info("🧬 Lazy initializing Evolution Service...")

    # Get memory (will be lazy-initialized if not already)
    lazy_registry = get_lazy_registry()
    memory_manager = await lazy_registry.get_or_create(AgentMemory)

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
    from core.services.llm.service import get_llm_service
    from core.nlp.models import get_embedder

    logger.info("🧠 Lazy initializing HierarchicalMemory...")

    llm_service = get_llm_service()
    embedder = get_embedder()

    memory = HierarchicalMemory(llm_service=llm_service, embedder=embedder)
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
