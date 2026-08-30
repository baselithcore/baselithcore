"""
VectorStore Service package.

Provides a modular, protocol-based vector store service with support for multiple providers.
"""

from core.services.vectorstore.chunking_hierarchical import (
    ChildChunk,
    ExpandedParent,
    HierarchicalChunker,
    InMemoryParentStore,
    ParentStore,
    StoredParent,
    expand_to_parents,
    parent_chunk_id,
)
from core.services.vectorstore.service import (
    VectorStoreService,
    get_vectorstore_service,
)
from core.services.vectorstore.splitters import (
    MarkdownHeaderSplitter,
    PythonCodeSplitter,
    RecursiveTextSplitter,
    SplitterProtocol,
    TextChunk,
    select_splitter,
)

__all__ = [
    "ChildChunk",
    "ExpandedParent",
    "HierarchicalChunker",
    "InMemoryParentStore",
    "MarkdownHeaderSplitter",
    "ParentStore",
    "PythonCodeSplitter",
    "RecursiveTextSplitter",
    "SplitterProtocol",
    "StoredParent",
    "TextChunk",
    "VectorStoreService",
    "expand_to_parents",
    "get_vectorstore_service",
    "parent_chunk_id",
    "select_splitter",
]
