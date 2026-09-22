"""
Core Memory Module.

Provides memory management for agents with support for short-term,
long-term, and working memory patterns.
"""

from __future__ import annotations

from typing import Optional

from core.observability.logging import get_logger

from .folding import ContextFolder, FoldingConfig

# New efficiency-focused modules
from .hierarchy import HierarchicalMemory, HierarchyConfig, MemoryTier, TierConfig
from .interfaces import ContextProvider, MemoryProvider
from .manager import AgentMemory
from .metrics import MemoryMetrics, MemoryMetricsCollector

# Supermemory intelligent memory layer
from .supermemory_provider import SupermemoryContextProvider, SupermemoryProvider

# Tenant scoping for the in-process tiers (working memory, STM/MTM/LTM)
from .tenant_state import TenantScopedState, current_memory_tenant
from .types import MemoryItem, MemoryType

# Alias for backward compatibility
MemoryEntry = MemoryItem

logger = get_logger(__name__)

# Global singleton
_agent_memory: AgentMemory | None = None


def get_memory() -> AgentMemory:
    """Get or create the process-wide :class:`AgentMemory`.

    Wired the same way as the lazy-registry ``memory`` resource: a persistent
    provider when ``MEMORY_PERSISTENCE_ENABLED`` allows one, and a summarizer
    so compaction can run. It used to be built bare — ``AgentMemory()`` — which
    made this singleton silently the degraded one: long-term memory was a
    bounded in-process deque that died with the worker, whatever the
    configuration said.

    Nothing heavy is constructed here. The embedder loads on the first query
    that needs it, so a synchronous call from inside an event loop does not
    stall it with a model load.

    Returns:
        The shared memory manager.
    """
    global _agent_memory
    if _agent_memory is None:
        from core.memory.providers import build_memory_provider

        _agent_memory = AgentMemory(
            provider=build_memory_provider("agent_memory"),
            llm_service=_default_llm_service(),
        )
    return _agent_memory


def _default_llm_service() -> object | None:
    """The summarizer compaction needs, or ``None`` when none can be built."""
    try:
        from core.services.llm import get_llm_service

        return get_llm_service()
    except Exception as exc:  # pragma: no cover - no provider configured
        logger.warning(f"No LLM service for memory compaction: {exc}")
        return None


__all__ = [
    # Core types
    "MemoryType",
    "MemoryItem",
    "MemoryEntry",
    "MemoryProvider",
    "ContextProvider",
    "AgentMemory",
    "get_memory",
    # Hierarchical Memory (NEW)
    "HierarchicalMemory",
    "MemoryTier",
    "HierarchyConfig",
    "TierConfig",
    # Context Folding (NEW)
    "ContextFolder",
    "FoldingConfig",
    # Metrics (NEW)
    "MemoryMetrics",
    "MemoryMetricsCollector",
    # Supermemory intelligent memory layer
    "SupermemoryProvider",
    "SupermemoryContextProvider",
    # Tenant scoping of the in-process tiers
    "TenantScopedState",
    "current_memory_tenant",
]
