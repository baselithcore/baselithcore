"""Backward-compatible shim for the Document Sources plugin package."""

import sys

from core.utils.optional_import import optional_module

_document_sources = optional_module("plugins.document_sources")
if _document_sources is not None:
    from plugins.document_sources import DocumentSourceError, create_document_sources

    # Register self as the plugin module for runtime compatibility
    sys.modules[__name__] = _document_sources
else:

    class DocumentSourceError(Exception):
        """Raised when optional document source configuration is invalid."""

    def create_document_sources() -> list[tuple[str, object]]:
        return []

__all__ = ["DocumentSourceError", "create_document_sources"]
