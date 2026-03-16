"""
Memory Manager Module.

This module acts as the central orchestrator for BaselithCore's
hierarchical memory system. It integrates multiple specialized
mixins to provide a unified 'AgentMemory' interface that handles
everything from raw storage to semantic context synthesis.
"""

from core.observability.logging import get_logger
from typing import TYPE_CHECKING, List, Optional


from .interfaces import MemoryProvider
from .types import MemoryItem

from .mixins.storage import StorageMixin
from .mixins.search import SearchMixin
from .mixins.optimization import OptimizationMixin
from .mixins.context import ContextMixin

if TYPE_CHECKING:
    from core.interfaces.services import EmbedderProtocol
    from core.memory.folding import ContextFolder

logger = get_logger(__name__)


class AgentMemory(StorageMixin, SearchMixin, OptimizationMixin, ContextMixin):
    """
    Unified manager for tiered agent memory.

    This class coordinates between volatile short-term (working) memory
    and persistent long-term storage providers. It leverages mixins
    to separate concerns like semantic search, content optimization,
    and prompt-ready context extraction.

    Patterns:
    - Layered Memory: Dynamic switching between episodic and semantic stores.
    - Proactive Compression: Folding and summarization via OptimizationMixin.
    """

    def __init__(
        self,
        provider: Optional[MemoryProvider] = None,
        embedder: Optional["EmbedderProtocol"] = None,
        similarity_threshold: float = 0.7,
        short_term_limit: int = 50,  # Legacy param support
        working_memory_limit: int = 10,  # Legacy param support
        context_folder: Optional["ContextFolder"] = None,
    ):
        """
        Configure the memory orchestration layer.

        Args:
            provider: The persistent storage implementation (e.g., PostgreSQL, Redis).
            embedder: A service for generating vector embeddings (e.g., OpenAI, Ollama).
            similarity_threshold: Minimum cosine similarity for semantic matches (0.0-1.0).
            short_term_limit: Maximum items retained in the rolling buffer.
            working_memory_limit: Size limit for active 'top-of-mind' context.
            context_folder: Service for hierarchical organization and folding.
        """
        self.provider = provider
        self.embedder = embedder
        self.similarity_threshold = similarity_threshold
        self.context_folder = context_folder
        # In-memory working memory (previously short_term_buffer)
        self._working_memory: List[MemoryItem] = []
        self._working_memory_embeddings: List[List[float]] = []
        self._working_memory_limit = working_memory_limit or 10
