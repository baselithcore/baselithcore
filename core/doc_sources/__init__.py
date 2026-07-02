"""Backward-compatible shim for the Document Sources plugin package."""

import sys

import plugins.document_sources as _document_sources
from plugins.document_sources import DocumentSourceError, create_document_sources

# Register self as the plugin module for runtime compatibility
sys.modules[__name__] = _document_sources

__all__ = ["DocumentSourceError", "create_document_sources"]
