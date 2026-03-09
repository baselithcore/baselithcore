"""
High-Level Conversational Orchestration Service.

Acts as the primary interface for managing complex agent-human
dialogues. Coordinates between embedders, rerankers, and history
managers, providing a plugin-extensible framework for RAG workflows,
streaming responses, and state-aware interactions.
"""

from __future__ import annotations

from core.observability.logging import get_logger
from typing import Any, Optional

from core.services.chat import ChatService as CoreChatService, ChatServiceConfig
from core.services.chat.utils.history import ChatHistoryManager

from core.chat.dependencies import (
    ChatDependencies,
    ChatDependencyConfig,
    create_default_dependencies,
)

logger = get_logger(__name__)


class ChatService(CoreChatService):
    """
    Application-specific conversational engine.

    Bridges the core ChatService logic with framework-level dependency
    injection and plugin registries. Manages the lifecycle of RAG
    pipelines, history pruning, and response synthesis, ensuring
    high-quality, context-aware agent outputs.
    """

    # Class-level constants for backwards compatibility
    FINAL_TOP_K = 6
    INITIAL_SEARCH_K = 40

    def __init__(
        self,
        *,
        dependencies: Optional[ChatDependencies] = None,
        dependency_config: Optional[ChatDependencyConfig] = None,
        plugin_registry: Optional[Any] = None,
    ) -> None:
        """
        Initialize ChatService with application dependencies.

        Args:
            dependencies: Pre-built ChatDependencies (optional).
            dependency_config: Configuration for building dependencies (optional).
            plugin_registry: Plugin registry for extensibility.
        """
        # Build dependencies if not provided
        if dependencies is None:
            dependencies = create_default_dependencies(dependency_config)

        # Store dependencies for direct access (backwards compatibility)
        self.dependencies = dependencies

        # Build core config from dependency config
        dep_cfg = dependency_config or ChatDependencyConfig()
        core_config = ChatServiceConfig(
            initial_search_k=self.INITIAL_SEARCH_K,
            final_top_k=self.FINAL_TOP_K,
            streaming_enabled=True,  # Always True, handled by config
            embedder_model=dep_cfg.embedder_model,
            reranker_model=dep_cfg.reranker_model,
            history_enabled=dep_cfg.history_enabled,
            history_max_turns=dep_cfg.history_max_turns,
        )

        # Initialize core service with injected dependencies
        super().__init__(
            config=core_config,
            embedder=dependencies.embedder,
            reranker=dependencies.reranker,
            response_cache=dependencies.response_cache,
            rerank_cache=dependencies.rerank_cache,
            history_manager=dependencies.history_manager,
            plugin_registry=plugin_registry,
        )

        # Keep formatting attributes from dependencies (backwards compatibility)
        self.newline = dependencies.newline
        self.double_newline = dependencies.double_newline
        self.section_separator = dependencies.section_separator

    # Expose dependencies directly for backwards compatibility
    @property
    def embedder(self):
        """Get embedder from dependencies."""
        return self.dependencies.embedder

    @property
    def reranker(self):
        """Get reranker from dependencies."""
        return self.dependencies.reranker

    @property
    def history_manager(self) -> ChatHistoryManager:
        """Get history manager from dependencies."""
        return self.dependencies.history_manager


# Global chat service instance
# Note: plugin_registry will be injected after app startup
chat_service = ChatService()


def initialize_chat_service_with_plugins(plugin_registry: Any) -> None:
    """
    Initialize chat service with plugin registry after app startup.

    This is called from backend.py after plugins are loaded.

    Args:
        plugin_registry: The plugin registry to inject.
    """
    global chat_service
    chat_service = ChatService(plugin_registry=plugin_registry)


__all__ = ["ChatService", "chat_service", "initialize_chat_service_with_plugins"]
