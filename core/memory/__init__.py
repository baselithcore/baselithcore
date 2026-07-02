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
from .types import MemoryItem, MemoryType

# Alias for backward compatibility
MemoryEntry = MemoryItem

logger = get_logger(__name__)

# Global singleton
_agent_memory: AgentMemory | None = None


def get_memory() -> AgentMemory:
    """Get or create global AgentMemory instance."""
    global _agent_memory
    if _agent_memory is None:
        _agent_memory = AgentMemory()
    return _agent_memory


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
]
