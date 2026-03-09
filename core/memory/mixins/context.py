"""
Context Mixin for AgentMemory.

This mixin defines how raw memories are transformed into structured
strings for LLM consumption. It includes support for 'Context Folding',
a technique for compressing multiple memories into a single semantic unit.
"""

import json
from core.observability.logging import get_logger
from typing import Any, Dict, List, Optional

from core.memory.types import MemoryItem

logger = get_logger(__name__)


class ContextMixin:
    """
    Extends AgentMemory with context synthesis capabilities.

    Handles the formatting of working memory into prompt-ready strings,
    supporting both standard list-based output and advanced folding logic.
    """

    provider: Optional[Any]
    embedder: Optional[Any]
    context_folder: Optional[Any]
    similarity_threshold: float
    _working_memory: List[MemoryItem]
    _working_memory_limit: int
    _working_memory_embeddings: List[List[float]]

    def get_memory_stats(self) -> Dict[str, Any]:
        """
        Get current memory statistics.

        Returns:
            Dict with memory stats
        """
        return {
            "working_memory_size": len(self._working_memory),
            "working_memory_limit": self._working_memory_limit,
            "has_provider": self.provider is not None,
            "has_embedder": self.embedder is not None,
            "similarity_threshold": self.similarity_threshold,
        }

    def get_context(self, max_tokens: int = 2000) -> str:
        """
        Get formatted context from working memory for LLM prompts.

        Args:
            max_tokens: Approximate max characters to return

        Returns:
            Formatted context string
        """
        if not self._working_memory:
            return ""

        context_parts = ["## Current Context\n"]
        total_len = len(context_parts[0])

        # Sort by importance (metadata) if available, otherwise recency
        sorted_memories = sorted(
            self._working_memory,
            key=lambda e: (e.metadata.get("importance", 0.5), e.created_at),
            reverse=True,
        )

        for entry in sorted_memories:
            line = f"- {entry.content}\n"
            if total_len + len(line) > max_tokens:
                break
            context_parts.append(line)
            total_len += len(line)

        return "".join(context_parts)

    async def get_context_async(self, max_tokens: int = 2000) -> str:
        """
        Get formulated context (async), supporting Context Folding.
        """
        if self.context_folder:
            try:
                # Use folding on the working memory items
                return await self.context_folder.fold(self._working_memory)
            except Exception as e:
                logger.warning(f"Context folding failed: {e}")

        # Fallback to sync implementation
        return self.get_context(max_tokens)

    def to_json(self) -> str:
        """Serialize working memory to JSON."""
        return json.dumps([e.to_dict() for e in self._working_memory])

    def from_json(self, data: str) -> None:
        """Load working memory from JSON."""
        entries = json.loads(data)
        self._working_memory = []
        self._working_memory_embeddings = []
        for e in entries:
            # Assuming MemoryItem.from_dict or compatible dict
            self._working_memory.append(MemoryItem.from_dict(e))
            self._working_memory_embeddings.append([])  # Lost embeddings
