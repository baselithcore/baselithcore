"""
VectorStore Service package.

Provides a modular, protocol-based vector store service with support for multiple providers.
"""

from core.services.vectorstore.service import (
    VectorStoreService,
    get_vectorstore_service,
)

__all__ = [
    "VectorStoreService",
    "get_vectorstore_service",
]
