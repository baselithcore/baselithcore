"""
Core Memory Module.

Provides memory management for agents with support for short-term,
long-term, and working memory patterns.
"""

from __future__ import annotations

from core.observability.logging import get_logger
from typing import Optional

from .interfaces import MemoryProvider, ContextProvider
from .types import MemoryType, MemoryItem
from .manager import AgentMemory

# New efficiency-focused modules
from .hierarchy import HierarchicalMemory, MemoryTier, HierarchyConfig, TierConfig
from .folding import ContextFolder, FoldingConfig
from .metrics import MemoryMetrics, MemoryMetricsCollector

# Alias for backward compatibility
MemoryEntry = MemoryItem

logger = get_logger(__name__)

# Global singleton
_agent_memory: Optional[AgentMemory] = None


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
]
