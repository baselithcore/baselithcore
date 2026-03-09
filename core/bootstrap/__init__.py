"""
Bootstrap module for lazy initialization of core services.

This module provides factory functions for on-demand service initialization.
"""

from core.bootstrap.lazy_init import (
    initialize_postgres,
    initialize_vectorstore,
    initialize_llm,
    initialize_graph,
    initialize_redis,
    initialize_memory,
    initialize_evaluation,
    initialize_evolution,
    RESOURCE_FACTORIES,
)

__all__ = [
    "initialize_postgres",
    "initialize_vectorstore",
    "initialize_llm",
    "initialize_graph",
    "initialize_redis",
    "initialize_memory",
    "initialize_evaluation",
    "initialize_evolution",
    "RESOURCE_FACTORIES",
]
