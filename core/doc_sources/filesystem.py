"""Backward-compatible shim for the Document Sources filesystem module."""

import sys

import plugins.document_sources.filesystem as _filesystem
from plugins.document_sources.filesystem import FilesystemDocumentSource

# Register self as the plugin module for runtime compatibility
sys.modules[__name__] = _filesystem

__all__ = ["FilesystemDocumentSource"]
