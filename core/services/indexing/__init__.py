"""
Document Indexing Service.

Provides modern, DI-based document indexing with:
- Document source abstraction
- Incremental indexing support
- Graph synchronization
- Metrics and observability
"""

from core.services.indexing.service import (
    IndexingService,
    get_indexing_service,
)

__all__ = [
    "IndexingService",
    "get_indexing_service",
]
