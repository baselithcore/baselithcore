"""
Document Sources.

Modular system for scanning and reading various sources of information
to be indexed into the knowledge base.
"""

from .models import DocumentSourceError
from .registry import create_document_sources

__all__ = ["DocumentSourceError", "create_document_sources"]
