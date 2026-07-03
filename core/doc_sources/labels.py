"""Backward-compatible shim for the Document Sources labels module."""

import sys

import plugins.document_sources.labels as _labels
from plugins.document_sources.labels import build_doc_label, build_kb_label

# Register self as the plugin module for runtime compatibility
sys.modules[__name__] = _labels

__all__ = ["build_doc_label", "build_kb_label"]
