"""
Bootstrap module for lazy initialization of core services.

This module provides factory functions for on-demand service initialization.
"""

from core.bootstrap.lazy_init import (
    RESOURCE_FACTORIES,
    initialize_evaluation,
    initialize_evolution,
    initialize_graph,
    initialize_llm,
    initialize_memory,
    initialize_postgres,
    initialize_redis,
    initialize_vectorstore,
)

__all__ = [
    "RESOURCE_FACTORIES",
    "initialize_evaluation",
    "initialize_evolution",
    "initialize_graph",
    "initialize_llm",
    "initialize_memory",
    "initialize_postgres",
    "initialize_redis",
    "initialize_vectorstore",
]
